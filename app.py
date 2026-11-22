"""
M&A Due Diligence Copilot — Institutional Investment Committee Suite.

Run:  streamlit run app.py

Architecture:
  - Session-isolated RuntimeConfig (never mutates process-wide `settings` across users).
  - Programmatic top navigation across 5 integrated institutional workspaces:
      1. Workspace & Ingestion  (Flagship Case Studies + Multi-Format VDR Upload)
      2. Intelligence Memo      (MECE 5-Section Memo, Radar, Risk Matrix, PMI, 4-Format Exports)
      3. Synergy DCF Model      (Live Interactive Stress-Testing, Bridge, Phasing, 2D Sensitivity)
      4. IC Q&A Copilot         (Grounded Interactive Deal Interrogation — Live + Offline)
      5. Deal Room              (Supabase + Local Vault, Lazy Loading, Side-by-Side Comparison)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st

from config.settings import UPLOAD_DIR, Settings, load_runtime_settings
from core.analyzer import AnalyzerError, DealParams, analyze_documents
from core.demo_cases import DEMO_CASES
from core.financial_model import (
    SynergyDCFResult,
    ValuationAssumptions,
    compute_sensitivity_matrix,
    compute_synergy_dcf,
)
from core.qa_copilot import SUGGESTED_IC_PROMPTS, answer_ic_question
from db import local_vault, supabase_client
from schemas.report_schema import DueDiligenceReport
from ui.charts import (
    cashflow_phasing_chart,
    deal_comparison_chart,
    pillar_radar_chart,
    risk_matrix_chart,
    sensitivity_heatmap_chart,
    synergy_bridge_chart,
    workstream_breakdown_chart,
)
from ui.styles import (
    INSTITUTIONAL_CSS,
    claim_html,
    grounding_summary_html,
    kpi_strip_html,
    masthead_html,
    reco_badge_html,
    section_header_html,
)
from utils.export_engine import (
    report_to_docx_bytes,
    report_to_excel_bytes,
    report_to_html_bytes,
)
from utils.models import EntityDocuments
from utils.pdf_extractor import extract_entity, sanitize_filename

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

NAV_VIEWS = [
    "Workspace & Ingestion",
    "Intelligence Memo",
    "Synergy DCF Model",
    "IC Q&A Copilot",
    "Deal Room",
]


# =====================================================================
# Session State Initialization
# =====================================================================

def _init_session_state() -> None:
    if "runtime_config" not in st.session_state:
        st.session_state["runtime_config"] = load_runtime_settings()
    if "active_view" not in st.session_state:
        st.session_state["active_view"] = NAV_VIEWS[0]
    if "qa_history" not in st.session_state:
        st.session_state["qa_history"] = []
    if "source_entities" not in st.session_state:
        st.session_state["source_entities"] = []
    if "ingestion_diagnostics" not in st.session_state:
        st.session_state["ingestion_diagnostics"] = []


def _get_config() -> Settings:
    return st.session_state["runtime_config"]


def _set_active_report(
    report: DueDiligenceReport,
    entities: Optional[list[EntityDocuments]] = None,
    jump_to_memo: bool = True,
) -> None:
    """Activate a report across all tabs, reset DCF overrides & Q&A thread."""
    st.session_state["last_report"] = report
    st.session_state["source_entities"] = entities or []
    st.session_state["qa_history"] = []
    st.session_state["dcf_preset_seed"] = f"{report.deal.entity_a_name}_{report.deal.entity_b_name}_{datetime.now().timestamp()}"
    # Reset interactive DCF sliders to match the newly loaded report's baseline
    st.session_state["dcf_wacc"] = float(report.deal.wacc_pct)
    st.session_state["dcf_tax"] = float(report.deal.tax_rate_pct)
    st.session_state["dcf_g"] = float(report.deal.terminal_growth_pct)
    st.session_state["dcf_realization"] = 100.0
    st.session_state["dcf_overrun"] = 0.0
    st.session_state["dcf_prob_weight"] = True
    if jump_to_memo:
        st.session_state["active_view"] = "Intelligence Memo"


def _fmt_b(val: Optional[float]) -> str:
    return "—" if val is None else f"${val:.2f}B"


def _safe_slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in (name or "Entity"))[:20]


def _humanize(field_name: str) -> str:
    return field_name.replace("_", " ").title()


# =====================================================================
# Ingestion & Execution Pipeline
# =====================================================================

def _save_uploads_to_disk(files, entity_tag: str) -> list[str]:
    paths: list[str] = []
    for f in files:
        safe_name = sanitize_filename(f.name)
        path = UPLOAD_DIR / f"{entity_tag}_{safe_name}"
        path.write_bytes(f.getvalue())
        paths.append(str(path))
    return paths


def _run_analysis(
    name_a: str,
    files_a,
    name_b: str,
    files_b,
    params: DealParams,
) -> None:
    cfg = _get_config()
    progress = st.progress(0, text=f"Ingesting VDR documents for {name_a}...")
    paths_a = _save_uploads_to_disk(files_a, "A")
    entity_a = extract_entity(name_a, paths_a, max_pages_per_doc=cfg.max_pages_per_pdf)

    progress.progress(35, text=f"Ingesting VDR documents for {name_b}...")
    paths_b = _save_uploads_to_disk(files_b, "B")
    entity_b = extract_entity(name_b, paths_b, max_pages_per_doc=cfg.max_pages_per_pdf)

    if not entity_a.documents or not entity_b.documents:
        progress.empty()
        st.error("Document extraction failed for one or both entities. Please verify the uploaded files are valid, non-corrupt documents.")
        return

    diagnostics = []
    for label, ent in (("Acquirer", entity_a), ("Target", entity_b)):
        for doc in ent.documents:
            diagnostics.append({
                "Side": label,
                "Entity": ent.entity_name,
                "Filename": doc.filename,
                "Type": doc.file_type.upper(),
                "Pages": doc.num_pages,
                "Words": doc.word_count,
                "Tables": len(doc.tables),
                "Notes": "; ".join(doc.warnings) if doc.warnings else "OK",
            })
    st.session_state["ingestion_diagnostics"] = diagnostics

    progress.progress(68, text="Running MECE Synergy & Risk Intelligence Engine...")
    try:
        report = analyze_documents(entity_a, entity_b, params=params, config=cfg)
    except AnalyzerError as e:
        progress.empty()
        st.error(f"Live LLM analysis failed: {e}")
        st.info("Verify your API key, Base URL, and Model in the sidebar — or clear the API key to run in instant Demo/MOCK mode.")
        return

    progress.progress(100, text="Investment Committee Memo complete.")
    _set_active_report(report, entities=[entity_a, entity_b], jump_to_memo=True)
    st.rerun()


# =====================================================================
# Sidebar — Institutional Configuration Rail
# =====================================================================

def _render_sidebar() -> None:
    cfg = _get_config()
    with st.sidebar:
        st.markdown(
            '<div style="font-family:\'Playfair Display\',Georgia,serif;font-size:1.25rem;'
            'font-weight:700;color:#FFFFFF;margin-bottom:0.2rem;">Advisory Terminal</div>'
            '<div style="font-size:0.74rem;color:#B8935A;letter-spacing:0.1em;text-transform:uppercase;'
            'margin-bottom:1rem;">Session Configuration</div>',
            unsafe_allow_html=True,
        )

        st.markdown("**LLM Provider (OpenAI-Compatible)**")
        api_key = st.text_input(
            "API Key",
            value=cfg.llm_api_key,
            type="password",
            help="Supports OpenAI, Azure OpenAI, OpenRouter, or local vLLM/Ollama. Leave empty for instant Demo/MOCK mode.",
        )
        base_url = st.text_input(
            "Base URL (Optional)",
            value=cfg.llm_base_url,
            placeholder="https://api.openai.com/v1",
        )
        model = st.text_input("Model", value=cfg.llm_model)

        st.divider()
        st.markdown("**Cloud Persistence (Supabase)**")
        sb_url = st.text_input("Supabase URL", value=cfg.supabase_url, placeholder="https://xxxx.supabase.co")
        sb_key = st.text_input("Supabase Key", value=cfg.supabase_key, type="password")
        st.caption("Local Deal Vault is always active as automatic fallback.")

        if st.button("Apply Session Settings", width="stretch"):
            updated = cfg.clone()
            updated.llm_api_key = api_key.strip()
            updated.llm_base_url = base_url.strip()
            updated.llm_model = model.strip() or "gpt-4o"
            updated.supabase_url = sb_url.strip()
            updated.supabase_key = sb_key.strip()
            st.session_state["runtime_config"] = updated
            # Fix legacy bug: reset cached Supabase client whenever credentials change
            supabase_client.reset_client()
            st.success("Session configuration updated.")
            st.rerun()

        st.divider()
        llm_status = f"LIVE · {cfg.llm_model}" if cfg.is_live_llm else "DEMO / MOCK MODE"
        db_status = "Supabase Cloud + Local Vault" if cfg.is_supabase_configured else "Local Deal Vault"
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.72rem;line-height:1.7;'>"
            f"<div>ENGINE: <strong style='color:#B8935A;'>{llm_status}</strong></div>"
            f"<div>STORAGE: <strong>{db_status}</strong></div>"
            f"<div>BUDGET: <strong>{cfg.max_chars_to_llm:,} chars</strong></div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# =====================================================================
# VIEW 1: Workspace & VDR Document Ingestion
# =====================================================================

def _render_workspace_view() -> None:
    # ---- Flagship Institutional Case Studies (1-Click Showcase) ----
    st.markdown(section_header_html("Flagship Institutional Case Studies", "INSTANT 1-CLICK IC SHOWCASE"), unsafe_allow_html=True)
    st.caption(
        "Explore full Harvard Business School / Investment Banking caliber pre-merger memos — complete with bottom-up "
        "Synergy DCF models, interactive sensitivity heatmaps, risk matrices, PMI roadmaps, and multi-format exports."
    )
    case_cols = st.columns(3)
    for col, (case_id, meta) in zip(case_cols, DEMO_CASES.items()):
        with col:
            st.markdown(
                f'<div class="case-card">'
                f'  <div class="case-title">{meta["label"]}</div>'
                f'  <div class="case-sub">{meta["subtitle"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.button(f"Load IC Memo →", key=f"load_case_{case_id}", width="stretch"):
                _set_active_report(meta["builder"](), entities=[], jump_to_memo=True)
                st.rerun()

    st.markdown("<div style='height:0.65rem;'></div>", unsafe_allow_html=True)

    # ---- Custom Transaction Parameters ----
    with st.container(border=True):
        st.markdown(section_header_html("New Transaction Analysis", "VIRTUAL DATA ROOM INGESTION"), unsafe_allow_html=True)

        r1c1, r1c2, r1c3 = st.columns([2, 2, 1.6])
        with r1c1:
            name_a = st.text_input("Acquirer (Entity A)", value="", placeholder="e.g. Volkswagen AG")
        with r1c2:
            name_b = st.text_input("Target (Entity B)", value="", placeholder="e.g. Rivian Automotive")
        with r1c3:
            sector = st.text_input("Industry Sector (Optional)", value="", placeholder="e.g. Enterprise AI / Cloud")

        r2c1, r2c2, r2c3, r2c4 = st.columns([1.6, 1.1, 1.1, 1.4])
        with r2c1:
            deal_type = st.selectbox(
                "Transaction Structure",
                [
                    "Strategic All-Cash Majority Acquisition",
                    "Cash & Stock Merger",
                    "Transformational Full Acquisition",
                    "Carve-Out / Divestiture Acquisition",
                    "Merger of Equals",
                ],
            )
        with r2c2:
            deal_value = st.number_input("Deal Value ($B)", min_value=0.0, value=17.5, step=0.5)
        with r2c3:
            wacc_input = st.number_input("Baseline WACC (%)", min_value=3.0, max_value=25.0, value=9.0, step=0.25)
        with r2c4:
            horizon = st.select_slider("Synergy Horizon (Months)", options=[12, 24, 36, 48, 60], value=36)

        st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
        st.caption(
            "Upload multiple VDR filings per entity (10-K, Annual Report, ESG/CSRD Report, Earnings Transcripts, Financial Extracts). "
            "Supported formats: **PDF, DOCX, TXT, MD, CSV**. Tables are preserved as page-cited Markdown and budgeted proportionally across files."
        )

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**Acquirer VDR — {name_a or 'Entity A'}**")
            files_a = st.file_uploader(
                "Acquirer documents",
                type=["pdf", "docx", "txt", "md", "csv"],
                accept_multiple_files=True,
                key="upload_a",
                label_visibility="collapsed",
            )
            if files_a:
                st.caption(f"{len(files_a)} filing(s) staged: {', '.join(f.name for f in files_a)}")
        with col_b:
            st.markdown(f"**Target VDR — {name_b or 'Entity B'}**")
            files_b = st.file_uploader(
                "Target documents",
                type=["pdf", "docx", "txt", "md", "csv"],
                accept_multiple_files=True,
                key="upload_b",
                label_visibility="collapsed",
            )
            if files_b:
                st.caption(f"{len(files_b)} filing(s) staged: {', '.join(f.name for f in files_b)}")

        run_enabled = bool(name_a.strip() and name_b.strip() and files_a and files_b)
        st.markdown("<div style='height:0.35rem;'></div>", unsafe_allow_html=True)
        if st.button(
            "Run Pre-Merger Synergy & Risk Analysis",
            disabled=not run_enabled,
            type="primary",
            width="stretch",
        ):
            params = DealParams(
                deal_value_usd_b=deal_value,
                deal_type=deal_type,
                time_horizon_months=horizon,
                industry_sector=sector.strip() or None,
                wacc_pct=wacc_input,
            )
            _run_analysis(name_a.strip(), files_a, name_b.strip(), files_b, params)

    # Show ingestion diagnostics from the latest run if available
    if st.session_state.get("ingestion_diagnostics"):
        with st.expander("Latest VDR Ingestion Diagnostics", expanded=False):
            st.dataframe(
                pd.DataFrame(st.session_state["ingestion_diagnostics"]),
                width="stretch",
                hide_index=True,
            )


# =====================================================================
# VIEW 2: Intelligence Memo (Executive Summary, MECE 2-5, Risk, PMI, Exports)
# =====================================================================

def _get_live_dcf_for_report(report: DueDiligenceReport) -> SynergyDCFResult:
    """Compute DCF using current session slider assumptions if set, else report defaults."""
    assump = ValuationAssumptions(
        wacc_pct=float(st.session_state.get("dcf_wacc", report.deal.wacc_pct)),
        tax_rate_pct=float(st.session_state.get("dcf_tax", report.deal.tax_rate_pct)),
        terminal_growth_pct=float(st.session_state.get("dcf_g", report.deal.terminal_growth_pct)),
        realization_factor_pct=float(st.session_state.get("dcf_realization", 100.0)),
        integration_cost_overrun_pct=float(st.session_state.get("dcf_overrun", 0.0)),
        use_probability_weighting=bool(st.session_state.get("dcf_prob_weight", True)),
        terminal_method=str(st.session_state.get("dcf_term_method", "multiple")),
        terminal_multiple=float(st.session_state.get("dcf_term_mult", 4.5)),
        projection_years=max(3, min(7, round((report.deal.time_horizon_months or 36) / 12) + 2)),
    )
    return compute_synergy_dcf(report, assump)


def _render_memo_view() -> None:
    report: Optional[DueDiligenceReport] = st.session_state.get("last_report")
    if report is None:
        st.info("No active transaction memo. Load a Flagship Case Study or run a new analysis from **Workspace & Ingestion**.")
        return

    d = report.deal
    es = report.executive_summary
    dcf = _get_live_dcf_for_report(report)

    # ---- Transaction Title & Grounding Audit Bar ----
    tickers = ""
    if d.acquirer_ticker or d.target_ticker:
        tickers = f"  ·  {d.acquirer_ticker or 'PRIVATE'} × {d.target_ticker or 'PRIVATE'}"
    st.markdown(
        f'<h2 style="margin-bottom:0.1rem;">{d.entity_a_name} <span style="color:#B8935A;">×</span> {d.entity_b_name}</h2>'
        f'<div style="color:#475569;font-size:0.86rem;margin-bottom:0.85rem;">'
        f'{d.deal_type}  ·  {d.industry_sector or "Strategic M&A"}{tickers}  ·  {d.analysis_date}  ·  {d.time_horizon_months or 36}-Month Horizon</div>',
        unsafe_allow_html=True,
    )

    st.markdown(grounding_summary_html(report), unsafe_allow_html=True)

    # ---- 5-Card Institutional KPI Strip ----
    vc_sub = f"{dcf.value_creation_pct_of_deal:.1f}% of Deal EV" if dcf.value_creation_pct_of_deal is not None else "DCF After-Tax"
    rr_sub = f"Rev {_fmt_b(dcf.steady_state_revenue_synergy_b)} · Cost {_fmt_b(dcf.steady_state_cost_synergy_b)}"
    payback_sub = f"Payback {dcf.payback_years:.1f}y · IRR {dcf.synergy_irr_pct:.0f}%" if (dcf.payback_years and dcf.synergy_irr_pct) else "1x Implementation"
    conv_val = f"{d.overall_conviction_score}/100" if d.overall_conviction_score is not None else "—"

    st.markdown(
        kpi_strip_html([
            ("Transaction Value (EV)", _fmt_b(d.deal_value_usd_b), f"{d.time_horizon_months or 36}m Horizon · WACC {dcf.assumptions.wacc_pct:.1f}%", False),
            ("Synergy NPV (DCF)", _fmt_b(dcf.total_synergy_npv_b), vc_sub, True),
            ("Risk-Adj Run-Rate", _fmt_b(dcf.steady_state_total_synergy_b), rr_sub, False),
            ("Integration Cost", _fmt_b(dcf.total_integration_cost_b), payback_sub, False),
            ("IC Conviction Index", conv_val, f"Complexity: {es.integration_complexity.value}", False),
        ]),
        unsafe_allow_html=True,
    )

    # ---- Section 1: Executive Summary + MECE Pillar Radar ----
    with st.container(border=True):
        st.markdown(section_header_html("1. Executive Summary & Investment Committee Stance", "BOARDROOM SYNTHESIS"), unsafe_allow_html=True)

        col_es, col_radar = st.columns([1.65, 1.0]) if report.pillar_scores else (st.container(), None)
        with col_es:
            st.markdown(
                f"{reco_badge_html(es.recommendation.value)} &nbsp;&nbsp; "
                f"<span style='font-family:JetBrains Mono,monospace;font-size:0.78rem;color:#475569;'>"
                f"INTEGRATION COMPLEXITY: <strong style='color:#0B192C;'>{es.integration_complexity.value.upper()}</strong></span>",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height:0.55rem;'></div>", unsafe_allow_html=True)
            st.markdown(f"**Strategic Rationale.** {es.strategic_rationale}")
            st.markdown(f"**Recommendation Rationale.** {es.recommendation_rationale}")
            st.caption(f"*Complexity Assessment:* {es.complexity_justification}")
            if report.valuation_commentary:
                st.markdown(f"**Valuation & Accretion Commentary.** {report.valuation_commentary}")

        if col_radar is not None and report.pillar_scores:
            with col_radar:
                st.plotly_chart(pillar_radar_chart(report), width="stretch", config={"displayModeBar": False})

        st.markdown("**Top Deal-Breaker Risks**")
        for risk in es.top_deal_breaker_risks:
            st.markdown(
                claim_html(
                    risk.claim,
                    risk.severity.value if risk.severity else "Critical",
                    risk.quantified_impact_usd_m,
                    risk.sources,
                ),
                unsafe_allow_html=True,
            )

    # ---- Filter Bar for Sections 2–5 ----
    f1, f2 = st.columns([2.5, 1.2])
    with f1:
        search_q = st.text_input("Filter MECE findings by keyword or source document", placeholder="e.g. antitrust, AWS, battery, CSRD, p.24...")
    with f2:
        sev_filter = st.multiselect("Filter by Severity", ["Critical", "High", "Medium", "Low"], default=[])

    # ---- Sections 2–5 ----
    sections = [
        ("2. Strategic Fit & Commercial Synergies", "COMMERCIAL DILIGENCE", "strategic_fit"),
        ("3. Operational & Technology", "TECH & OPERATIONS", "operational_tech"),
        ("4. Cultural & Human Capital", "ORGANIZATION & TALENT", "cultural_risk"),
        ("5. Regulatory, ESG & Macro", "REGULATORY & COMPLIANCE", "regulatory_esg"),
    ]
    q_lower = search_q.strip().lower()
    for title, kicker, attr in sections:
        section = getattr(report, attr)
        rendered_blocks: list[tuple[str, list]] = []
        for field_name in type(section).model_fields:
            claims = getattr(section, field_name)
            filtered = []
            for c in claims:
                if sev_filter and (not c.severity or c.severity.value not in sev_filter):
                    continue
                if q_lower:
                    haystack = (c.claim + " " + " ".join(f"{s.source_document} {s.location}" for s in c.sources)).lower()
                    if q_lower not in haystack:
                        continue
                filtered.append(c)
            if filtered:
                rendered_blocks.append((_humanize(field_name), filtered))

        if rendered_blocks:
            with st.container(border=True):
                st.markdown(section_header_html(title, kicker), unsafe_allow_html=True)
                for lbl, claims in rendered_blocks:
                    st.markdown(f"**{lbl}**")
                    for c in claims:
                        st.markdown(
                            claim_html(
                                c.claim,
                                c.severity.value if c.severity else None,
                                c.quantified_impact_usd_m,
                                c.sources,
                            ),
                            unsafe_allow_html=True,
                        )

    # ---- Risk Register Matrix ----
    if report.risk_register:
        with st.container(border=True):
            st.markdown(section_header_html("6. Investment Committee Risk Register", "LIKELIHOOD × IMPACT MATRIX"), unsafe_allow_html=True)
            rc1, rc2 = st.columns([1.05, 1.35])
            with rc1:
                st.plotly_chart(risk_matrix_chart(report), width="stretch", config={"displayModeBar": False})
            with rc2:
                risk_df = pd.DataFrame([
                    {
                        "Risk": ("★ " if r.is_deal_breaker else "") + r.title,
                        "Dimension": r.dimension.value,
                        "Severity": r.severity.value,
                        "Score": f"{r.likelihood}×{r.impact} ({r.likelihood * r.impact})",
                        "Mitigation Plan": r.mitigation,
                    }
                    for r in report.risk_register
                ])
                st.dataframe(risk_df, width="stretch", hide_index=True, height=370)

    # ---- Post-Merger Integration Roadmap ----
    if report.integration_roadmap:
        with st.container(border=True):
            st.markdown(section_header_html("7. Post-Merger Integration (PMI) Roadmap", "DAY-1 TO MONTH-36 EXECUTION"), unsafe_allow_html=True)
            pmi_df = pd.DataFrame([
                {
                    "Phase": m.phase,
                    "Workstream": m.workstream,
                    "Objective": m.objective,
                    "Accountable Owner": m.owner,
                    "Target KPI": m.kpi,
                }
                for m in report.integration_roadmap
            ])
            st.dataframe(pmi_df, width="stretch", hide_index=True)

    # ---- VDR Management Q&A & Data Gaps ----
    c_vdr, c_gap = st.columns(2)
    with c_vdr:
        with st.expander(f"Priority VDR & Management Follow-Up Questions ({len(report.due_diligence_questions)})", expanded=True):
            if report.due_diligence_questions:
                for idx, q in enumerate(report.due_diligence_questions, 1):
                    st.markdown(f"**{idx}.** {q}")
            else:
                st.caption("No VDR follow-up questions generated.")
    with c_gap:
        with st.expander(f"Public Data Gaps Requiring Phase-2 Verification ({len(report.data_gaps)})", expanded=True):
            if report.data_gaps:
                for g in report.data_gaps:
                    st.markdown(f"- {g}")
            else:
                st.caption("No public data gaps flagged.")

    # ---- Multi-Format Institutional Export Suite & Deal Room Persistence ----
    st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(section_header_html("Deliverables & Deal Room Export Suite", "BOARDROOM PACKAGE"), unsafe_allow_html=True)
        safe_a = _safe_slug(d.entity_a_name)
        safe_b = _safe_slug(d.entity_b_name)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        e1, e2, e3, e4, e5 = st.columns(5)
        with e1:
            st.download_button(
                "IC Memo (DOCX)",
                data=report_to_docx_bytes(report, dcf),
                file_name=f"IC_Memo_{safe_a}_{safe_b}_{ts}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                width="stretch",
                type="primary",
            )
        with e2:
            st.download_button(
                "DCF Model (Excel)",
                data=report_to_excel_bytes(report, dcf),
                file_name=f"Synergy_DCF_{safe_a}_{safe_b}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        with e3:
            st.download_button(
                "Board Brief (HTML)",
                data=report_to_html_bytes(report, dcf),
                file_name=f"Board_Brief_{safe_a}_{safe_b}_{ts}.html",
                mime="text/html",
                width="stretch",
            )
        with e4:
            st.download_button(
                "Structured JSON",
                data=json.dumps(report.model_dump(mode="json"), indent=2),
                file_name=f"dd_report_{safe_a}_{safe_b}_{ts}.json",
                mime="application/json",
                width="stretch",
            )
        with e5:
            if st.button("Save to Deal Room", width="stretch"):
                rid, backend = local_vault.save_to_deal_room(report, config=_get_config())
                label = "Supabase Cloud" if backend == "supabase" else "Local Deal Vault"
                st.success(f"Saved to {label} (`{rid}`)")


# =====================================================================
# VIEW 3: Interactive Synergy Valuation & DCF Stress-Testing
# =====================================================================

def _render_financial_model_view() -> None:
    report: Optional[DueDiligenceReport] = st.session_state.get("last_report")
    if report is None:
        st.info("Load a case study or run an analysis first to open the deterministic Synergy DCF Valuation Engine.")
        return

    d = report.deal
    st.markdown(
        f'<h2>Synergy DCF &amp; Sensitivity Engine — {d.entity_a_name} <span style="color:#B8935A;">×</span> {d.entity_b_name}</h2>',
        unsafe_allow_html=True,
    )
    st.caption(
        "100% deterministic Python financial engine (zero LLM math). Adjust WACC, tax rate, realization haircuts, "
        "and cost overruns below to stress-test value creation live for the Investment Committee."
    )

    # ---- Scenario Preset Buttons ----
    with st.container(border=True):
        st.markdown(section_header_html("Valuation Assumptions & Stress-Test Controls", "LIVE DCF PARAMETERS"), unsafe_allow_html=True)

        p1, p2, p3, p4 = st.columns([1, 1, 1, 1.8])
        if p1.button("Bear / Downside Case", width="stretch"):
            st.session_state["dcf_wacc"] = min(20.0, float(d.wacc_pct) + 1.5)
            st.session_state["dcf_realization"] = 75.0
            st.session_state["dcf_overrun"] = 25.0
            st.rerun()
        if p2.button("Base Case (Reset)", width="stretch"):
            st.session_state["dcf_wacc"] = float(d.wacc_pct)
            st.session_state["dcf_tax"] = float(d.tax_rate_pct)
            st.session_state["dcf_g"] = float(d.terminal_growth_pct)
            st.session_state["dcf_realization"] = 100.0
            st.session_state["dcf_overrun"] = 0.0
            st.session_state["dcf_prob_weight"] = True
            st.session_state["dcf_term_method"] = "multiple"
            st.session_state["dcf_term_mult"] = 4.5
            st.rerun()
        if p3.button("Bull / Upside Case", width="stretch"):
            st.session_state["dcf_wacc"] = max(5.0, float(d.wacc_pct) - 0.75)
            st.session_state["dcf_realization"] = 120.0
            st.session_state["dcf_overrun"] = -10.0
            st.rerun()
        with p4:
            term_choice = st.selectbox(
                "Terminal Valuation Basis",
                ["Conservative Fade Multiple (IB Standard)", "Gordon Growth Perpetuity"],
                index=0 if st.session_state.get("dcf_term_method", "multiple") == "multiple" else 1,
                label_visibility="collapsed",
            )
            term_method = "multiple" if term_choice.startswith("Conservative") else "perpetuity"
            st.session_state["dcf_term_method"] = term_method

        s1, s2, s3 = st.columns(3)
        with s1:
            wacc = st.slider("Discount Rate / WACC (%)", 5.0, 18.0, float(st.session_state.get("dcf_wacc", d.wacc_pct)), 0.25, key="dcf_wacc")
            tax = st.slider("Effective Corporate Tax Rate (%)", 0.0, 45.0, float(st.session_state.get("dcf_tax", d.tax_rate_pct)), 1.0, key="dcf_tax")
        with s2:
            if term_method == "multiple":
                term_mult = st.slider("Terminal Synergy Fade Multiple (x NOPAT)", 1.0, 10.0, float(st.session_state.get("dcf_term_mult", 4.5)), 0.5, key="dcf_term_mult")
                term_g = float(st.session_state.get("dcf_g", d.terminal_growth_pct))
            else:
                term_g = st.slider("Terminal Growth Rate g (%)", 0.0, 5.0, float(st.session_state.get("dcf_g", d.terminal_growth_pct)), 0.25, key="dcf_g")
                term_mult = float(st.session_state.get("dcf_term_mult", 4.5))
            real_pct = st.slider("Global Synergy Realization (%)", 40.0, 150.0, float(st.session_state.get("dcf_realization", 100.0)), 5.0, key="dcf_realization")
        with s3:
            overrun = st.slider("Integration Cost Overrun (%)", -25.0, 100.0, float(st.session_state.get("dcf_overrun", 0.0)), 5.0, key="dcf_overrun")
            prob_w = st.toggle("Apply Workstream Probability Weighting", value=bool(st.session_state.get("dcf_prob_weight", True)), key="dcf_prob_weight")

    assump = ValuationAssumptions(
        wacc_pct=wacc,
        tax_rate_pct=tax,
        terminal_growth_pct=term_g,
        realization_factor_pct=real_pct,
        integration_cost_overrun_pct=overrun,
        use_probability_weighting=prob_w,
        terminal_method=term_method,
        terminal_multiple=term_mult,
        projection_years=max(3, min(7, round((d.time_horizon_months or 36) / 12) + 2)),
    )
    dcf = compute_synergy_dcf(report, assump)

    # ---- Stressed Valuation KPI Strip ----
    st.markdown(
        kpi_strip_html([
            ("Stressed Synergy NPV", _fmt_b(dcf.total_synergy_npv_b), f"Explicit {_fmt_b(dcf.pv_explicit_period_b)} + TV {_fmt_b(dcf.pv_terminal_value_b)}", True),
            ("Value Creation / EV", f"{dcf.value_creation_pct_of_deal:.1f}%" if dcf.value_creation_pct_of_deal is not None else "—", f"Deal EV: {_fmt_b(d.deal_value_usd_b)}", False),
            ("Run-Rate Synergies", _fmt_b(dcf.steady_state_total_synergy_b), f"Rev {_fmt_b(dcf.steady_state_revenue_synergy_b)} · Cost {_fmt_b(dcf.steady_state_cost_synergy_b)}", False),
            ("Simple Payback", f"{dcf.payback_years:.2f} yrs" if dcf.payback_years else "—", f"Discounted: {dcf.discounted_payback_years:.2f}y" if dcf.discounted_payback_years else "—", False),
            ("Synergy Project IRR", f"{dcf.synergy_irr_pct:.1f}%" if dcf.synergy_irr_pct is not None else "—", f"NPV / 1x Cost: {dcf.npv_to_integration_cost_ratio:.1f}x" if dcf.npv_to_integration_cost_ratio else "—", False),
        ]),
        unsafe_allow_html=True,
    )

    # ---- Charts Row 1: Value Bridge & Annual Phasing ----
    ch1, ch2 = st.columns(2)
    with ch1:
        with st.container(border=True):
            st.plotly_chart(synergy_bridge_chart(dcf), width="stretch", config={"displayModeBar": False})
    with ch2:
        with st.container(border=True):
            st.plotly_chart(cashflow_phasing_chart(dcf), width="stretch", config={"displayModeBar": False})

    # ---- Charts Row 2: 2D Sensitivity Heatmap & Workstream Breakdown ----
    wacc_vals, real_vals, grid = compute_sensitivity_matrix(report, assump)
    ch3, ch4 = st.columns(2)
    with ch3:
        with st.container(border=True):
            st.plotly_chart(sensitivity_heatmap_chart(wacc_vals, real_vals, grid), width="stretch", config={"displayModeBar": False})
    with ch4:
        with st.container(border=True):
            if report.synergy_workstreams:
                st.plotly_chart(workstream_breakdown_chart(report), width="stretch", config={"displayModeBar": False})
            else:
                st.info("No itemized bottom-up workstreams in this report (using top-down DealMeta estimates).")

    # ---- Annual Cash Flow Schedule ----
    with st.container(border=True):
        st.markdown(section_header_html("Annual Synergy Cash Flow Schedule ($M)", "EXPLICIT DCF PROJECTION"), unsafe_allow_html=True)
        cf_df = pd.DataFrame([
            {
                "Year": f"Year {r.year}",
                "Rev. Synergies ($M)": r.revenue_synergy_m,
                "Cost Synergies ($M)": r.cost_synergy_m + r.capital_synergy_m,
                "1x Costs ($M)": -r.integration_cost_m,
                "Pre-Tax EBT ($M)": r.ebt_impact_m,
                "Tax ($M)": -r.tax_impact_m,
                "Net Cash Flow ($M)": r.net_cash_flow_m,
                "Discount Factor": r.discount_factor,
                "PV Cash Flow ($M)": r.pv_cash_flow_m,
            }
            for r in dcf.annual_rows
        ])
        st.dataframe(cf_df, width="stretch", hide_index=True)

        safe_a, safe_b = _safe_slug(d.entity_a_name), _safe_slug(d.entity_b_name)
        st.download_button(
            "Export Stressed DCF Workbook (Live Excel Formulas .xlsx)",
            data=report_to_excel_bytes(report, dcf),
            file_name=f"Synergy_DCF_Stressed_{safe_a}_{safe_b}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

    # ---- Bottom-Up Workstreams Table ----
    if report.synergy_workstreams:
        with st.container(border=True):
            st.markdown(section_header_html("Bottom-Up Synergy Workstreams", "LEVER-BY-LEVER SCHEDULE"), unsafe_allow_html=True)
            ws_df = pd.DataFrame([
                {
                    "Workstream": w.name,
                    "Type": w.synergy_type.value,
                    "Gross Run-Rate ($M)": w.annual_run_rate_usd_m,
                    "Prob. (%)": w.probability_pct,
                    "Risk-Adj ($M)": round(w.annual_run_rate_usd_m * w.probability_pct / 100.0, 1),
                    "1x Cost ($M)": w.one_time_cost_usd_m,
                    "Ramp Y1/Y2/Y3": f"{w.ramp_year1_pct:.0f}% / {w.ramp_year2_pct:.0f}% / {w.ramp_year3_pct:.0f}%",
                    "Owner": w.owner,
                }
                for w in report.synergy_workstreams
            ])
            st.dataframe(ws_df, width="stretch", hide_index=True)


# =====================================================================
# VIEW 4: Investment Committee Q&A Copilot
# =====================================================================

def _render_qa_copilot_view() -> None:
    report: Optional[DueDiligenceReport] = st.session_state.get("last_report")
    if report is None:
        st.info("Load a case study or run an analysis first to interrogate the transaction with the IC Q&A Copilot.")
        return

    d = report.deal
    cfg = _get_config()
    dcf = _get_live_dcf_for_report(report)

    st.markdown(
        f'<h2>Investment Committee Q&amp;A Copilot — {d.entity_a_name} <span style="color:#B8935A;">×</span> {d.entity_b_name}</h2>',
        unsafe_allow_html=True,
    )
    mode_note = (
        f"Connected to LIVE LLM (`{cfg.llm_model}`) with full memo + VDR retrieval."
        if cfg.is_live_llm
        else "Running in Grounded Offline Synthesis mode (enter an API key in the sidebar for open-ended live LLM interrogation)."
    )
    st.caption(mode_note)

    # ---- Suggested 1-Click IC Prompts ----
    with st.container(border=True):
        st.markdown("**Suggested Investment Committee Inquiries**")
        p_cols = st.columns(3)
        for idx, prompt_text in enumerate(SUGGESTED_IC_PROMPTS):
            if p_cols[idx % 3].button(prompt_text, key=f"ic_sug_{idx}", width="stretch"):
                st.session_state["qa_history"].append({"role": "user", "content": prompt_text})
                ans = answer_ic_question(
                    prompt_text,
                    report=report,
                    history=st.session_state["qa_history"][:-1],
                    dcf_result=dcf,
                    source_entities=st.session_state.get("source_entities", []),
                    config=cfg,
                )
                st.session_state["qa_history"].append({"role": "assistant", "content": ans})
                st.rerun()

    # ---- Conversation Thread ----
    for msg in st.session_state["qa_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_q = st.chat_input("Ask the Investment Committee Copilot (e.g., 'Challenge the synergy valuation' or 'Draft CFO VDR questions')...")
    if user_q:
        st.session_state["qa_history"].append({"role": "user", "content": user_q})
        with st.spinner("Synthesizing grounded IC response..."):
            ans = answer_ic_question(
                user_q,
                report=report,
                history=st.session_state["qa_history"][:-1],
                dcf_result=dcf,
                source_entities=st.session_state.get("source_entities", []),
                config=cfg,
            )
        st.session_state["qa_history"].append({"role": "assistant", "content": ans})
        st.rerun()

    if st.session_state["qa_history"]:
        if st.button("Clear Conversation"):
            st.session_state["qa_history"] = []
            st.rerun()


# =====================================================================
# VIEW 5: Deal Room & Side-by-Side Comparison (Lazy Loaded — Zero N+1!)
# =====================================================================

def _render_deal_room_view() -> None:
    cfg = _get_config()
    st.markdown(section_header_html("Deal Room & Transaction Archive", "SUPABASE + LOCAL VAULT"), unsafe_allow_html=True)
    st.caption(
        "Previously analyzed transactions persisted in Supabase Cloud or Local Deal Vault. "
        "Reports are loaded lazily on demand (zero N+1 queries)."
    )

    deals = local_vault.list_deal_room(limit=40, config=cfg)

    if not deals:
        st.info(
            "Your Deal Room is currently empty. Load any Flagship Case Study or run an analysis, "
            "then click **Save to Deal Room** to archive and compare transactions side by side."
        )
        # Offer one-click seed of all 3 flagship cases into the Local Vault for instant comparison demo
        if st.button("Seed Deal Room with 3 Flagship Case Studies (for Side-by-Side Comparison)", type="primary"):
            for meta in DEMO_CASES.values():
                local_vault.save_to_deal_room(meta["builder"](), config=cfg)
            st.rerun()
        return

    # ---- Search & Filter Bar ----
    f1, f2 = st.columns([2.2, 1.3])
    with f1:
        q = st.text_input("Search Deal Room", placeholder="Filter by acquirer, target, or sector...")
    with f2:
        rec_filter = st.multiselect("Recommendation", ["Go", "Conditional Go", "No-Go"], default=[])

    filtered = []
    for item in deals:
        if q.strip():
            hay = f"{item.get('entity_a', '')} {item.get('entity_b', '')} {item.get('industry_sector', '')}".lower()
            if q.strip().lower() not in hay:
                continue
        if rec_filter and item.get("recommendation") not in rec_filter:
            continue
        filtered.append(item)

    st.caption(f"Showing {len(filtered)} of {len(deals)} archived transaction(s).")

    # ---- Lazy Transaction Rows ----
    for deal in filtered:
        deal_id = deal["id"]
        storage = deal.get("storage", "local")
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([2.6, 1.15, 1.45, 0.9, 0.8])
            with c1:
                st.markdown(f"**{deal.get('entity_a', '?')}** × **{deal.get('entity_b', '?')}**")
                ts_label = str(deal.get("created_at", "—"))[:16].replace("T", " ")
                sector_lbl = deal.get("industry_sector") or deal.get("deal_type") or ""
                st.caption(f"{sector_lbl}  ·  {ts_label}  ·  `{storage.upper()}`")
            with c2:
                ev_str = _fmt_b(deal.get("deal_value_usd_b"))
                npv_str = _fmt_b(deal.get("target_synergy_npv_usd_b"))
                st.markdown(f"<span style='font-family:JetBrains Mono,monospace;font-size:0.8rem;'>EV: <strong>{ev_str}</strong><br/>NPV: <strong>{npv_str}</strong></span>", unsafe_allow_html=True)
            with c3:
                rec_val = deal.get("recommendation")
                if rec_val:
                    st.markdown(reco_badge_html(rec_val), unsafe_allow_html=True)
                else:
                    st.caption("Archived")
            with c4:
                if st.button("Open", key=f"open_{deal_id}", width="stretch", type="primary"):
                    loaded = local_vault.load_from_deal_room(deal_id, storage=storage, config=cfg)
                    if loaded:
                        _set_active_report(loaded, entities=[], jump_to_memo=True)
                        st.rerun()
                    else:
                        st.error("Failed to load archived report.")
            with c5:
                if st.button("Delete", key=f"del_{deal_id}", width="stretch"):
                    if local_vault.delete_from_deal_room(deal_id, storage=storage, config=cfg):
                        st.rerun()

    # ---- Side-by-Side Deal Comparison ----
    if len(deals) >= 2:
        st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(section_header_html("Side-by-Side Transaction Comparison", "PORTFOLIO IC BENCHMARK"), unsafe_allow_html=True)
            options_map = {
                f"{d.get('entity_a', '?')} × {d.get('entity_b', '?')} ({str(d.get('created_at', ''))[:10]}) [{d['id'][-6:]}]": d
                for d in deals
            }
            selected_labels = st.multiselect(
                "Select 2 to 4 transactions to benchmark",
                list(options_map.keys()),
                default=list(options_map.keys())[: min(3, len(options_map))],
                max_selections=4,
            )
            if len(selected_labels) >= 2:
                loaded_reports: list[DueDiligenceReport] = []
                for lbl in selected_labels:
                    meta = options_map[lbl]
                    rep = local_vault.load_from_deal_room(meta["id"], storage=meta.get("storage", "auto"), config=cfg)
                    if rep:
                        loaded_reports.append(rep)

                if len(loaded_reports) >= 2:
                    st.plotly_chart(deal_comparison_chart(loaded_reports), width="stretch", config={"displayModeBar": False})
                    comp_rows = []
                    for r in loaded_reports:
                        r_dcf = compute_synergy_dcf(r)
                        comp_rows.append({
                            "Transaction": f"{r.deal.entity_a_name} × {r.deal.entity_b_name}",
                            "Recommendation": r.executive_summary.recommendation.value,
                            "Deal Value ($B)": r.deal.deal_value_usd_b,
                            "Synergy NPV ($B)": r_dcf.total_synergy_npv_b,
                            "Value Creation (%)": f"{r_dcf.value_creation_pct_of_deal:.1f}%" if r_dcf.value_creation_pct_of_deal is not None else "—",
                            "Complexity": r.executive_summary.integration_complexity.value,
                            "Conviction": f"{r.deal.overall_conviction_score}/100" if r.deal.overall_conviction_score is not None else "—",
                            "Deal-Breaker Risks": len(r.executive_summary.top_deal_breaker_risks),
                            "Grounding": f"{r.grounding_ratio()*100:.0f}%",
                        })
                    st.dataframe(pd.DataFrame(comp_rows), width="stretch", hide_index=True)


# =====================================================================
# MAIN APPLICATION ENTRYPOINT
# =====================================================================

st.set_page_config(
    page_title="M&A Due Diligence Copilot — Investment Committee Suite",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(INSTITUTIONAL_CSS, unsafe_allow_html=True)

_init_session_state()
_render_sidebar()

cfg = _get_config()
mode_badge = f"LIVE ENGINE · {cfg.llm_model}" if cfg.is_live_llm else "DEMO / MOCK MODE"
st.markdown(masthead_html(mode_badge), unsafe_allow_html=True)

# Programmatic Top Navigation Bar (supports jumping directly to Intelligence Memo)
current_view = st.session_state["active_view"] if st.session_state["active_view"] in NAV_VIEWS else NAV_VIEWS[0]
if hasattr(st, "segmented_control"):
    picked = st.segmented_control(
        "Navigation",
        options=NAV_VIEWS,
        default=current_view,
        selection_mode="single",
        label_visibility="collapsed",
        key=f"nav_seg_{current_view}",
    )
    selected_view = picked or current_view
else:
    selected_view = st.radio(
        "Navigation",
        NAV_VIEWS,
        index=NAV_VIEWS.index(current_view),
        horizontal=True,
        label_visibility="collapsed",
    )

if selected_view != st.session_state["active_view"]:
    st.session_state["active_view"] = selected_view
    st.rerun()

if selected_view == "Workspace & Ingestion":
    _render_workspace_view()
elif selected_view == "Intelligence Memo":
    _render_memo_view()
elif selected_view == "Synergy DCF Model":
    _render_financial_model_view()
elif selected_view == "IC Q&A Copilot":
    _render_qa_copilot_view()
elif selected_view == "Deal Room":
    _render_deal_room_view()
