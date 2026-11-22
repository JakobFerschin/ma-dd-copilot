"""
M&A Due Diligence Copilot — Enterprise Investment Committee Platform (v2.0).

Run UI:   streamlit run app.py
Run API:  uvicorn api.main:app --host 0.0.0.0 --port 8000

6 Integrated Institutional Workspaces:
  1. Workspace & VDR       — Flagship Cases, Multi-Format/ZIP/Excel/PPTX Ingestion, Live SEC EDGAR, Sanctions Screener, VDR Completeness
  2. IC Memo & Red-Team    — MECE Memo, Golden Evaluation Grade, Multi-Agent + Red-Team Critic, Analyst Sign-Off, 7-Format Exports (PPTX/DOCX/XLSX/HTML/Flash/Jira/Audit-ZIP)
  3. Quant Valuation Suite — Synergy DCF, 10k Monte Carlo, Football Field, 3-Statement Accretion/Dilution, PE LBO/IRR, QoE EBITDA Bridge, NWC Peg, Customer Cohorts
  4. Legal & Regulatory    — CoC/Anti-Assignment Scanner, SPA Benchmark, Antitrust (HSR/EC/CMA/Bundeskartellamt/SAMR) & CFIUS/FDI, IP/Cyber/CSRD Disclosure Schedules
  5. IC Workflow & Copilot — Hybrid-RAG IC Q&A Copilot, Lifecycle Stage Gate, VDR Q&A Tracker, Digital IC Voting, Clean Team Workbench, SHA-256 WORM Audit Log
  6. Deal Room & Studio    — Supabase + Local Deal Vault, Side-by-Side Benchmarking, White-Label Branding Studio, PII/MNPI Firewall, Enterprise VDR Connectors
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st

from agents.orchestrator import evaluate_report_quality, run_multi_agent_diligence
from api.audit import AuditLogger
from api.auth import Role
from config.settings import UPLOAD_DIR, Settings, load_runtime_settings
from core.analyzer import AnalyzerError, DealParams, analyze_documents
from core.demo_cases import DEMO_CASES
from core.financial_model import (
    SynergyDCFResult,
    ValuationAssumptions,
    compute_synergy_dcf,
)
from core.qa_copilot import SUGGESTED_IC_PROMPTS, answer_ic_question
from db import local_vault, supabase_client
from rag.engine import HybridRAGIndex, detect_cross_document_contradictions
from schemas.report_schema import DueDiligenceReport
from ui.charts import (
    deal_comparison_chart,
    pillar_radar_chart,
    risk_matrix_chart,
)
from ui.legal_workflow_views import (
    render_enterprise_studio_panel,
    render_ic_workflow_view,
    render_legal_diligence_view,
)
from ui.quant_view import render_quant_valuation_suite
from ui.styles import (
    INSTITUTIONAL_CSS,
    claim_html,
    grounding_summary_html,
    kpi_strip_html,
    masthead_html,
    reco_badge_html,
    section_header_html,
)
from utils.audit_pack import (
    build_audit_pack_zip,
    build_localized_executive_brief,
    pmi_roadmap_to_jira_csv,
    report_to_red_flag_flash_html,
)
from utils.export_engine import (
    report_to_docx_bytes,
    report_to_excel_bytes,
    report_to_html_bytes,
)
from utils.models import EntityDocuments
from utils.pdf_extractor import sanitize_filename
from utils.pptx_exporter import BrandingConfig, report_to_pptx_bytes
from vdr.connectors import fetch_sec_edgar_filings, screen_sanctions_and_entity
from vdr.ingestion import assess_vdr_completeness, ingest_vdr_paths
from workflow import CLAIM_STATUSES, EnterpriseWorkflowStore, compute_claim_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

NAV_VIEWS = [
    "Workspace & VDR",
    "IC Memo & Red-Team",
    "Quant Valuation Suite",
    "Legal & Regulatory",
    "IC Workflow & Copilot",
    "Deal Room & Studio",
]


# =====================================================================
# Session State & Singletons
# =====================================================================

@st.cache_resource
def _get_workflow_store() -> EnterpriseWorkflowStore:
    return EnterpriseWorkflowStore()


@st.cache_resource
def _get_audit_logger() -> AuditLogger:
    return AuditLogger()


def _init_session_state() -> None:
    if "runtime_config" not in st.session_state:
        st.session_state["runtime_config"] = load_runtime_settings()
    if "active_view" not in st.session_state:
        st.session_state["active_view"] = NAV_VIEWS[0]
    if "active_role" not in st.session_state:
        st.session_state["active_role"] = Role.MD.value
    if "active_user" not in st.session_state:
        st.session_state["active_user"] = "Managing Director (IC Chair)"
    if "qa_history" not in st.session_state:
        st.session_state["qa_history"] = []
    if "source_entities" not in st.session_state:
        st.session_state["source_entities"] = []
    if "ingestion_diagnostics" not in st.session_state:
        st.session_state["ingestion_diagnostics"] = []
    if "vdr_completeness" not in st.session_state:
        st.session_state["vdr_completeness"] = None
    if "excel_anomalies" not in st.session_state:
        st.session_state["excel_anomalies"] = []
    if "edgar_staged" not in st.session_state:
        st.session_state["edgar_staged"] = {}


def _get_config() -> Settings:
    return st.session_state["runtime_config"]


def _deal_key(report: DueDiligenceReport) -> str:
    d = report.deal
    slug = f"{d.entity_a_name}_{d.entity_b_name}".lower()
    return "".join(ch if ch.isalnum() else "_" for ch in slug)[:48]


def _get_branding_config() -> BrandingConfig:
    raw = _get_workflow_store().get_branding("default")
    return BrandingConfig(
        firm_name=raw.get("firm_name") or "M&A Due Diligence Copilot Advisory",
        primary_hex=(raw.get("primary_hex") or "#0B192C").lstrip("#"),
        accent_hex=(raw.get("accent_hex") or "#B8935A").lstrip("#"),
        disclaimer=raw.get("legal_disclaimer") or "STRICTLY PRIVATE & CONFIDENTIAL — Investment Committee Use Only.",
        watermark_text=f"CONFIDENTIAL · {st.session_state.get('active_user', 'IC Member')}",
    )


def _set_active_report(
    report: DueDiligenceReport,
    entities: Optional[list[EntityDocuments]] = None,
    jump_to_memo: bool = True,
) -> None:
    """Activate a report across all workspaces, sync DCF defaults, and audit the event."""
    st.session_state["last_report"] = report
    st.session_state["source_entities"] = entities or []
    st.session_state["qa_history"] = []
    st.session_state["multi_agent_review"] = None
    st.session_state["dcf_wacc"] = float(report.deal.wacc_pct)
    st.session_state["dcf_tax"] = float(report.deal.tax_rate_pct)
    st.session_state["dcf_g"] = float(report.deal.terminal_growth_pct)
    st.session_state["dcf_realization"] = 100.0
    st.session_state["dcf_overrun"] = 0.0
    st.session_state["dcf_prob_weight"] = True
    st.session_state["dcf_term_method"] = "multiple"
    st.session_state["dcf_term_mult"] = 4.5
    _get_audit_logger().log_event(
        "VIEW",
        st.session_state.get("active_user", "Analyst"),
        st.session_state.get("active_role", "MD"),
        deal_id=_deal_key(report),
        metadata={"acquirer": report.deal.entity_a_name, "target": report.deal.entity_b_name},
    )
    if jump_to_memo:
        st.session_state["active_view"] = "IC Memo & Red-Team"


def _fmt_b(val: Optional[float]) -> str:
    return "—" if val is None else f"${val:.2f}B"


def _safe_slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in (name or "Entity"))[:20]


def _humanize(field_name: str) -> str:
    return field_name.replace("_", " ").title()


def _get_live_dcf_for_report(report: DueDiligenceReport) -> SynergyDCFResult:
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


# =====================================================================
# Industrial VDR Ingestion Pipeline (PDF, DOCX, XLSX, PPTX, ZIP, TXT, CSV)
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
    edgar_a: Optional[EntityDocuments] = None,
    edgar_b: Optional[EntityDocuments] = None,
) -> None:
    cfg = _get_config()
    store = _get_workflow_store()
    audit_logger = _get_audit_logger()

    progress = st.progress(0, text=f"Ingesting VDR package for {name_a} (OCR/Excel/PPTX/ZIP + SHA-256 Dedup)...")
    paths_a = _save_uploads_to_disk(files_a or [], "A")
    ing_a = ingest_vdr_paths(paths_a, entity_name=name_a, max_pages_per_doc=cfg.max_pages_per_pdf) if paths_a else None
    docs_a = list(ing_a.entity_documents.documents) if ing_a else []
    if edgar_a:
        docs_a.extend(edgar_a.documents)
    entity_a = EntityDocuments(entity_name=name_a, documents=docs_a)

    progress.progress(35, text=f"Ingesting VDR package for {name_b} (Taxonomy & Anomaly Scan)...")
    paths_b = _save_uploads_to_disk(files_b or [], "B")
    ing_b = ingest_vdr_paths(paths_b, entity_name=name_b, max_pages_per_doc=cfg.max_pages_per_pdf) if paths_b else None
    docs_b = list(ing_b.entity_documents.documents) if ing_b else []
    if edgar_b:
        docs_b.extend(edgar_b.documents)
    entity_b = EntityDocuments(entity_name=name_b, documents=docs_b)

    if not entity_a.documents or not entity_b.documents:
        progress.empty()
        st.error("Provide at least one VDR document or SEC EDGAR filing for both Acquirer and Target.")
        return

    # Collect VDR diagnostics, Excel anomalies & sector completeness
    diagnostics = []
    all_records = []
    anomalies = []
    for side, ing, ent in (("Acquirer", ing_a, entity_a), ("Target", ing_b, entity_b)):
        if ing:
            all_records.extend(ing.records)
            anomalies.extend(ing.excel_anomalies)
            for rec in ing.records:
                diagnostics.append({
                    "Side": side,
                    "Entity": ent.entity_name,
                    "Filename": rec.document.filename,
                    "Category": rec.classification.category,
                    "Type": rec.document.file_type.upper(),
                    "Pages": rec.document.num_pages,
                    "Words": rec.document.word_count,
                    "Tables": len(rec.document.tables),
                    "SHA-256": rec.sha256[:12] + "…",
                })
        elif ent.documents:
            for doc in ent.documents:
                diagnostics.append({
                    "Side": side,
                    "Entity": ent.entity_name,
                    "Filename": doc.filename,
                    "Category": "Financials (SEC EDGAR)",
                    "Type": doc.file_type.upper(),
                    "Pages": doc.num_pages,
                    "Words": doc.word_count,
                    "Tables": len(doc.tables),
                    "SHA-256": "EDGAR",
                })

    st.session_state["ingestion_diagnostics"] = diagnostics
    st.session_state["excel_anomalies"] = anomalies
    if all_records:
        st.session_state["vdr_completeness"] = assess_vdr_completeness(all_records, sector=params.industry_sector or "General")

    progress.progress(68, text="Running MECE Synergy & Risk Intelligence Engine...")
    try:
        report = analyze_documents(entity_a, entity_b, params=params, config=cfg)
    except AnalyzerError as e:
        progress.empty()
        st.error(f"Live LLM analysis failed: {e}")
        st.info("Verify API key / Base URL / Model in the sidebar — or leave API key empty for instant Demo/MOCK mode.")
        return

    total_pages = entity_a.total_pages + entity_b.total_pages
    deal_id = _deal_key(report)
    store.log_usage("default", deal_id, "ANALYZE", pages_processed=total_pages, estimated_tokens=min(cfg.max_chars_to_llm // 4, 35000), actor=st.session_state["active_user"])
    audit_logger.log_event("ANALYZE", st.session_state["active_user"], st.session_state["active_role"], deal_id=deal_id, metadata={"pages": total_pages, "mode": "LIVE" if cfg.is_live_llm else "MOCK"})

    progress.progress(100, text="Investment Committee Intelligence Memo ready.")
    _set_active_report(report, entities=[entity_a, entity_b], jump_to_memo=True)
    st.rerun()


# =====================================================================
# Sidebar — Institutional Configuration & RBAC Role Rail
# =====================================================================

def _render_sidebar() -> None:
    cfg = _get_config()
    with st.sidebar:
        st.markdown(
            '<div style="font-family:\'Playfair Display\',Georgia,serif;font-size:1.25rem;'
            'font-weight:700;color:#FFFFFF;margin-bottom:0.2rem;">Advisory Terminal</div>'
            '<div style="font-size:0.72rem;color:#B8935A;letter-spacing:0.12em;text-transform:uppercase;'
            'margin-bottom:1rem;">Enterprise v2.0 · Zero-Retention</div>',
            unsafe_allow_html=True,
        )

        st.markdown("**Active Principal & RBAC Role**")
        role_options = [r.value for r in Role]
        cur_role = st.session_state.get("active_role", Role.MD.value)
        picked_role = st.selectbox("Session Role (Chinese Wall / Clean Team)", role_options, index=role_options.index(cur_role) if cur_role in role_options else 0)
        picked_user = st.text_input("Principal Name", value=st.session_state.get("active_user", "Managing Director (IC Chair)"))
        st.session_state["active_role"] = picked_role
        st.session_state["active_user"] = picked_user

        st.divider()
        st.markdown("**LLM Provider (OpenAI / ZDR Private Link)**")
        api_key = st.text_input("API Key", value=cfg.llm_api_key, type="password", help="OpenAI, Azure OpenAI ZDR, OpenRouter, or local vLLM.")
        base_url = st.text_input("Base URL (Optional)", value=cfg.llm_base_url, placeholder="https://api.openai.com/v1")
        model = st.text_input("Model", value=cfg.llm_model)

        st.divider()
        st.markdown("**Cloud Persistence (Supabase)**")
        sb_url = st.text_input("Supabase URL", value=cfg.supabase_url, placeholder="https://xxxx.supabase.co")
        sb_key = st.text_input("Supabase Key", value=cfg.supabase_key, type="password")

        if st.button("Apply Session Settings", width="stretch"):
            updated = cfg.clone()
            updated.llm_api_key = api_key.strip()
            updated.llm_base_url = base_url.strip()
            updated.llm_model = model.strip() or "gpt-4o"
            updated.supabase_url = sb_url.strip()
            updated.supabase_key = sb_key.strip()
            st.session_state["runtime_config"] = updated
            supabase_client.reset_client()
            st.success("Session configuration updated.")
            st.rerun()

        st.divider()
        llm_status = f"LIVE · {cfg.llm_model}" if cfg.is_live_llm else "DEMO / MOCK MODE"
        db_status = "Supabase + Local Vault" if cfg.is_supabase_configured else "Local Vault + SQLite"
        st.markdown(
            f"<div style='font-family:JetBrains Mono,monospace;font-size:0.71rem;line-height:1.75;'>"
            f"<div>ROLE: <strong style='color:#B8935A;'>{picked_role}</strong></div>"
            f"<div>ENGINE: <strong>{llm_status}</strong></div>"
            f"<div>STORE: <strong>{db_status}</strong></div>"
            f"<div>REST API: <strong>FastAPI v2.0</strong></div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# =====================================================================
# VIEW 1: Workspace, Flagship Cases, Industrial VDR & Live SEC EDGAR
# =====================================================================

def _render_workspace_view() -> None:
    st.markdown(section_header_html("Flagship Institutional Case Studies", "INSTANT 1-CLICK IC SHOWCASE"), unsafe_allow_html=True)
    st.caption(
        "Launch complete Wall Street / HBS-grade pre-merger diligence memos with live Synergy DCF, 10,000-run Monte Carlo, "
        "3-Statement Merger Model, PE LBO, Legal CoC/Antitrust, Red-Team Critique, and 12-Slide PPTX Pitchbook exports."
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
            if st.button("Load IC Memo →", key=f"load_case_{case_id}", width="stretch"):
                _set_active_report(meta["builder"](), entities=[], jump_to_memo=True)
                st.rerun()

    st.markdown("<div style='height:0.65rem;'></div>", unsafe_allow_html=True)

    # ---- Live SEC EDGAR Auto-Ingestion & Sanctions Screener ----
    with st.expander("Live SEC EDGAR Auto-Ingestion & Entity Sanctions Screener (Public Filings by Ticker)", expanded=False):
        ec1, ec2, ec3 = st.columns([1.4, 1.4, 1.2])
        with ec1:
            tkr_a = st.text_input("Acquirer Ticker / CIK (e.g. MSFT, LLY)", key="edgar_tkr_a")
            if st.button("Fetch Acquirer 10-K/10-Q from SEC EDGAR", width="stretch") and tkr_a.strip():
                with st.spinner(f"Querying SEC EDGAR for {tkr_a.strip()}..."):
                    res_a = fetch_sec_edgar_filings(tkr_a.strip(), form_types=("10-K", "10-Q"), limit=2)
                if res_a.status == "ok" and res_a.entity_documents:
                    st.session_state["edgar_staged"]["A"] = res_a
                    st.success(f"Loaded {res_a.company_name} (CIK {res_a.cik}): {len(res_a.documents)} filing(s) staged.")
                else:
                    st.warning(f"SEC EDGAR fetch status: {res_a.status} — {res_a.error or 'Unavailable'}")
        with ec2:
            tkr_b = st.text_input("Target Ticker / CIK (e.g. RIVN, VRTX)", key="edgar_tkr_b")
            if st.button("Fetch Target 10-K/10-Q from SEC EDGAR", width="stretch") and tkr_b.strip():
                with st.spinner(f"Querying SEC EDGAR for {tkr_b.strip()}..."):
                    res_b = fetch_sec_edgar_filings(tkr_b.strip(), form_types=("10-K", "10-Q"), limit=2)
                if res_b.status == "ok" and res_b.entity_documents:
                    st.session_state["edgar_staged"]["B"] = res_b
                    st.success(f"Loaded {res_b.company_name} (CIK {res_b.cik}): {len(res_b.documents)} filing(s) staged.")
                else:
                    st.warning(f"SEC EDGAR fetch status: {res_b.status} — {res_b.error or 'Unavailable'}")
        with ec3:
            scr_name = st.text_input("Quick OFAC / Sanctions Entity Check", placeholder="e.g. Target Holdings Ltd.")
            scr_cty = st.text_input("Jurisdiction (ISO)", value="US")
            if st.button("Screen Entity", width="stretch") and scr_name.strip():
                scr = screen_sanctions_and_entity(scr_name.strip(), country=scr_cty.strip())
                st.info(f"**{scr.normalized_name}** · Risk: `{scr.risk_level}` ({scr.risk_score}/100) · {scr.clearance_recommendation}")

    # ---- Industrial Multi-Format VDR Ingestion ----
    with st.container(border=True):
        st.markdown(section_header_html("New Transaction Analysis", "INDUSTRIAL VDR INGESTION (PDF · DOCX · XLSX · PPTX · ZIP · CSV)"), unsafe_allow_html=True)

        staged_a = st.session_state["edgar_staged"].get("A")
        staged_b = st.session_state["edgar_staged"].get("B")

        r1c1, r1c2, r1c3 = st.columns([2, 2, 1.6])
        with r1c1:
            name_a = st.text_input("Acquirer (Entity A)", value=staged_a.company_name if staged_a else "", placeholder="e.g. Volkswagen AG")
        with r1c2:
            name_b = st.text_input("Target (Entity B)", value=staged_b.company_name if staged_b else "", placeholder="e.g. Rivian Automotive")
        with r1c3:
            sector = st.selectbox("Industry Sector (VDR Checklist)", ["General", "SaaS", "Automotive/Industrial", "Healthcare/Biopharma"])

        r2c1, r2c2, r2c3, r2c4 = st.columns([1.6, 1.1, 1.1, 1.4])
        with r2c1:
            deal_type = st.selectbox(
                "Transaction Structure",
                [
                    "Strategic All-Cash Majority Acquisition",
                    "Cash & Stock Merger",
                    "Transformational Full Acquisition",
                    "Private Equity Leveraged Buyout (LBO)",
                    "Carve-Out / Divestiture Acquisition",
                ],
            )
        with r2c2:
            deal_value = st.number_input("Deal Value ($B)", min_value=0.0, value=17.5, step=0.5)
        with r2c3:
            wacc_input = st.number_input("Baseline WACC (%)", min_value=3.0, max_value=25.0, value=9.0, step=0.25)
        with r2c4:
            horizon = st.select_slider("Synergy Horizon (Months)", options=[12, 24, 36, 48, 60], value=36)

        st.caption(
            "Supports entire **VDR ZIP archives**, native **Excel models** (automatic formula/hardcode anomaly scan), "
            "**PowerPoint CIMs**, **PDFs** (page-aligned Markdown tables), **DOCX**, and **TXT/CSV** transcripts."
        )

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**Acquirer VDR — {name_a or 'Entity A'}**")
            files_a = st.file_uploader(
                "Acquirer documents",
                type=["pdf", "docx", "xlsx", "pptx", "zip", "txt", "md", "csv"],
                accept_multiple_files=True,
                key="upload_a",
                label_visibility="collapsed",
            )
            if staged_a:
                st.caption(f"SEC EDGAR staged: {len(staged_a.documents)} filing(s) for {staged_a.company_name}")
        with col_b:
            st.markdown(f"**Target VDR — {name_b or 'Entity B'}**")
            files_b = st.file_uploader(
                "Target documents",
                type=["pdf", "docx", "xlsx", "pptx", "zip", "txt", "md", "csv"],
                accept_multiple_files=True,
                key="upload_b",
                label_visibility="collapsed",
            )
            if staged_b:
                st.caption(f"SEC EDGAR staged: {len(staged_b.documents)} filing(s) for {staged_b.company_name}")

        has_a = bool(files_a or staged_a)
        has_b = bool(files_b or staged_b)
        run_enabled = bool(name_a.strip() and name_b.strip() and has_a and has_b)
        if st.button("Run Institutional Pre-Merger Intelligence Analysis", disabled=not run_enabled, type="primary", width="stretch"):
            params = DealParams(
                deal_value_usd_b=deal_value,
                deal_type=deal_type,
                time_horizon_months=horizon,
                industry_sector=sector,
                wacc_pct=wacc_input,
            )
            _run_analysis(
                name_a.strip(),
                files_a,
                name_b.strip(),
                files_b,
                params,
                edgar_a=staged_a.entity_documents if staged_a else None,
                edgar_b=staged_b.entity_documents if staged_b else None,
            )

    # ---- Latest VDR Diagnostics, Completeness & Excel Formula Anomalies ----
    if st.session_state.get("ingestion_diagnostics"):
        with st.expander("VDR Ingestion Diagnostics, Taxonomy & Excel Formula Anomalies", expanded=False):
            st.dataframe(pd.DataFrame(st.session_state["ingestion_diagnostics"]), width="stretch", hide_index=True)
            comp = st.session_state.get("vdr_completeness")
            if comp:
                st.info(
                    f"**VDR Completeness ({comp.sector}):** {comp.coverage_pct:.0f}% overall · "
                    f"Critical Items: {comp.critical_present}/{comp.critical_total} · Readiness Grade: `{comp.readiness_grade}`"
                )
            anoms = st.session_state.get("excel_anomalies") or []
            if anoms:
                st.warning(f"Detected {len(anoms)} Excel formula/hardcode anomaly alert(s)!")
                st.dataframe(pd.DataFrame([a.model_dump() if hasattr(a, "model_dump") else vars(a) for a in anoms]), width="stretch", hide_index=True)


# =====================================================================
# VIEW 2: Intelligence Memo, Multi-Agent Red-Team, Sign-Off & 7-Format Exports
# =====================================================================

def _render_memo_view() -> None:
    report: Optional[DueDiligenceReport] = st.session_state.get("last_report")
    if report is None:
        st.info("No active transaction memo. Load a Flagship Case Study or ingest a VDR in **Workspace & VDR**.")
        return

    d = report.deal
    es = report.executive_summary
    dcf = _get_live_dcf_for_report(report)
    store = _get_workflow_store()
    audit_logger = _get_audit_logger()
    deal_id = _deal_key(report)
    entities = st.session_state.get("source_entities", [])
    scorecard = evaluate_report_quality(report, entities=entities)

    tickers = f"  ·  {d.acquirer_ticker or 'PRIVATE'} × {d.target_ticker or 'PRIVATE'}" if (d.acquirer_ticker or d.target_ticker) else ""
    st.markdown(
        f'<h2 style="margin-bottom:0.1rem;">{d.entity_a_name} <span style="color:#B8935A;">×</span> {d.entity_b_name}</h2>'
        f'<div style="color:#475569;font-size:0.86rem;margin-bottom:0.85rem;">'
        f'{d.deal_type}  ·  {d.industry_sector or "Strategic M&A"}{tickers}  ·  {d.analysis_date}  ·  '
        f'Evaluation Grade: <strong style="color:#0B192C;">{scorecard.institutional_grade} ({scorecard.overall_score:.0f}/100)</strong></div>',
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
            ("IC Conviction / Grade", conv_val, f"Audit Grade: {scorecard.institutional_grade} · {es.integration_complexity.value}", False),
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
                claim_html(risk.claim, risk.severity.value if risk.severity else "Critical", risk.quantified_impact_usd_m, risk.sources),
                unsafe_allow_html=True,
            )

    # ---- Multi-Agent Specialist Review, Adversarial Red-Team & Numerical Fact-Checker ----
    with st.container(border=True):
        st.markdown(section_header_html("Multi-Agent Specialist Review, Red-Team Critic & Fact-Checker", "ADVERSARIAL ZERO-HALLUCINATION LAYER"), unsafe_allow_html=True)
        ma_review = st.session_state.get("multi_agent_review")
        if ma_review is None:
            if st.button("Run 6-Agent Specialist Review + Adversarial Red-Team Critique + Numerical Fact-Check", type="primary"):
                with st.spinner("Running 6 specialist DD agents, Red-Team Skeptic, and deterministic fact-checker..."):
                    ma_review = run_multi_agent_diligence(report, entities=entities, config=_get_config())
                    st.session_state["multi_agent_review"] = ma_review
                st.rerun()
        else:
            rt = ma_review.red_team
            fc = ma_review.fact_check
            st.markdown(
                f"**Consensus:** `{ma_review.consensus_recommendation}` ({ma_review.consensus_conviction_score}/100)  ·  "
                f"**Red-Team Bear NPV Haircut:** `${rt.haircut_synergy_npv_usd_b:.2f}B` ({rt.implied_npv_downside_pct:+.1f}% vs Base `${rt.base_synergy_npv_usd_b:.2f}B`)  ·  "
                f"**Fact-Checker:** `{fc.verification_rate_pct:.0f}%` verified ({fc.verified_count + fc.close_match_count}/{fc.total_figures_checked} figures)"
            )
            t_rt, t_sp, t_fc, t_cd = st.tabs(["Adversarial Red-Team Bear Case", "6 Specialist Agents", "Numerical Fact-Checker", "Cross-Doc Contradictions"])
            with t_rt:
                st.warning(f"**Bear-Case Counter-Thesis ({rt.overall_skepticism_level} Skepticism):** {rt.bear_case_thesis}")
                c_tr, c_sq = st.columns(2)
                with c_tr:
                    st.markdown("**Hidden Execution Traps**")
                    for trap in rt.hidden_execution_traps:
                        st.markdown(f"- {trap}")
                with c_sq:
                    st.markdown("**IC Stress-Test Questions**")
                    for sq in rt.stress_questions:
                        st.markdown(f"- {sq}")
            with t_sp:
                sp_df = pd.DataFrame([
                    {"Agent": s.agent_name, "Domain": s.domain, "Stance": s.stance, "Confidence": f"{s.confidence_score}/100", "Headline Takeaway": s.headline}
                    for s in ma_review.specialist_findings
                ])
                st.dataframe(sp_df, width="stretch", hide_index=True)
            with t_fc:
                st.caption(fc.summary)
                if fc.items:
                    fc_df = pd.DataFrame([
                        {
                            "Status": it.status,
                            "Claim Figure": it.extracted_figure,
                            "Matched Value": it.matched_value_display or "—",
                            "Source": it.matched_source or "—",
                            "Note": it.note,
                        }
                        for it in fc.items[:15]
                    ])
                    st.dataframe(fc_df, width="stretch", hide_index=True)
            with t_cd:
                contras = detect_cross_document_contradictions(entities, report=report)
                if contras:
                    st.dataframe(pd.DataFrame([c.model_dump() if hasattr(c, "model_dump") else vars(c) for c in contras]), width="stretch", hide_index=True)
                else:
                    st.success("Zero cross-document numerical contradictions detected across active filings and memo.")

    # ---- Filter Bar + Analyst Sign-Off Mode for Sections 2–5 ----
    f1, f2, f3 = st.columns([2.2, 1.2, 1.0])
    with f1:
        search_q = st.text_input("Filter MECE findings by keyword or source", placeholder="e.g. antitrust, AWS, battery, CSRD...")
    with f2:
        sev_filter = st.multiselect("Severity Filter", ["Critical", "High", "Medium", "Low"], default=[])
    with f3:
        st.markdown("<div style='height:1.65rem;'></div>", unsafe_allow_html=True)
        signoff_mode = st.toggle("Analyst Sign-Off Mode", value=False)

    sections = [
        ("2. Strategic Fit & Commercial Synergies", "COMMERCIAL DILIGENCE", "strategic_fit"),
        ("3. Operational & Technology", "TECH & OPERATIONS", "operational_tech"),
        ("4. Cultural & Human Capital", "ORGANIZATION & TALENT", "cultural_risk"),
        ("5. Regulatory, ESG & Macro", "REGULATORY & COMPLIANCE", "regulatory_esg"),
    ]
    q_lower = search_q.strip().lower()
    existing_reviews = {r["claim_hash"]: r for r in store.list_claim_reviews(deal_id)}

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
                    for idx_c, c in enumerate(claims):
                        chash = compute_claim_hash(c.claim)
                        rev = existing_reviews.get(chash)
                        badge_suffix = f"  [{rev['status'].upper()}]" if rev else ""
                        st.markdown(
                            claim_html(c.claim + badge_suffix, c.severity.value if c.severity else None, c.quantified_impact_usd_m, c.sources),
                            unsafe_allow_html=True,
                        )
                        if signoff_mode:
                            sc1, sc2 = st.columns([1.4, 2.6])
                            cur_st = rev["status"] if rev and rev["status"] in CLAIM_STATUSES else "AI Draft"
                            picked_st = sc1.selectbox("Verification Status", CLAIM_STATUSES, index=CLAIM_STATUSES.index(cur_st), key=f"st_{attr}_{lbl}_{idx_c}", label_visibility="collapsed")
                            if sc2.button("Save Sign-Off", key=f"save_st_{attr}_{lbl}_{idx_c}"):
                                store.set_claim_review(deal_id, chash, picked_st, reviewer=st.session_state["active_user"], claim_text=c.claim)
                                audit_logger.log_event("CLAIM_VERIFY", st.session_state["active_user"], st.session_state["active_role"], deal_id=deal_id, metadata={"claim_hash": chash, "status": picked_st})
                                st.rerun()

    # ---- Risk Register & PMI Roadmap ----
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

    if report.integration_roadmap:
        with st.container(border=True):
            st.markdown(section_header_html("7. Post-Merger Integration (PMI) Roadmap", "DAY-1 TO MONTH-36 EXECUTION"), unsafe_allow_html=True)
            pmi_df = pd.DataFrame([
                {"Phase": m.phase, "Workstream": m.workstream, "Objective": m.objective, "Owner": m.owner, "Target KPI": m.kpi}
                for m in report.integration_roadmap
            ])
            st.dataframe(pmi_df, width="stretch", hide_index=True)

    # ---- Localized Executive Brief Preview ----
    with st.expander("Multi-Language Boardroom Executive Brief (EN / DE / FR / ES)", expanded=False):
        lang = st.selectbox("Target Language", ["DE", "EN", "FR", "ES"], index=0)
        st.markdown(build_localized_executive_brief(report, language=lang, dcf_result=dcf))

    # ---- 7-Format Institutional Boardroom Export Suite ----
    with st.container(border=True):
        st.markdown(section_header_html("Boardroom Publishing & Regulatory Audit Suite", "WHITE-LABEL · 7-FORMAT DELIVERABLES"), unsafe_allow_html=True)
        branding = _get_branding_config()
        safe_a, safe_b = _safe_slug(d.entity_a_name), _safe_slug(d.entity_b_name)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        row1 = st.columns(4)
        with row1[0]:
            st.download_button(
                "12-Slide Pitchbook (PPTX)",
                data=report_to_pptx_bytes(report, dcf, branding=branding),
                file_name=f"IC_Pitchbook_{safe_a}_{safe_b}_{ts}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                width="stretch",
                type="primary",
            )
        with row1[1]:
            st.download_button(
                "IC Memo (DOCX)",
                data=report_to_docx_bytes(report, dcf),
                file_name=f"IC_Memo_{safe_a}_{safe_b}_{ts}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                width="stretch",
            )
        with row1[2]:
            st.download_button(
                "DCF Model (Excel)",
                data=report_to_excel_bytes(report, dcf),
                file_name=f"Synergy_DCF_{safe_a}_{safe_b}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        with row1[3]:
            st.download_button(
                "Audit Pack (SHA-256 ZIP)",
                data=build_audit_pack_zip(
                    report,
                    dcf_result=dcf,
                    audit_entries=audit_logger.query_events(deal_id=deal_id, limit=50),
                    branding=branding,
                    watermark_user=st.session_state["active_user"],
                ),
                file_name=f"Audit_Pack_{safe_a}_{safe_b}_{ts}.zip",
                mime="application/zip",
                width="stretch",
            )

        row2 = st.columns(4)
        with row2[0]:
            st.download_button(
                "Board Brief (HTML)",
                data=report_to_html_bytes(report, dcf),
                file_name=f"Board_Brief_{safe_a}_{safe_b}_{ts}.html",
                mime="text/html",
                width="stretch",
            )
        with row2[1]:
            st.download_button(
                "Red-Flag Flash (HTML)",
                data=report_to_red_flag_flash_html(report, dcf, branding=branding),
                file_name=f"Red_Flag_Flash_{safe_a}_{safe_b}_{ts}.html",
                mime="text/html",
                width="stretch",
            )
        with row2[2]:
            st.download_button(
                "PMI Tasks (Jira CSV)",
                data=pmi_roadmap_to_jira_csv(report),
                file_name=f"PMI_Jira_{safe_a}_{safe_b}_{ts}.csv",
                mime="text/csv",
                width="stretch",
            )
        with row2[3]:
            if st.button("Save to Deal Room", width="stretch"):
                rid, backend = local_vault.save_to_deal_room(report, config=_get_config())
                audit_logger.log_event("EXPORT", st.session_state["active_user"], st.session_state["active_role"], deal_id=deal_id, metadata={"saved_id": rid, "backend": backend})
                st.success(f"Saved to {'Supabase Cloud' if backend == 'supabase' else 'Local Deal Vault'} (`{rid}`)")


# =====================================================================
# VIEW 5: Combined IC Workflow & Hybrid-RAG IC Q&A Copilot
# =====================================================================

def _render_workflow_and_copilot_view() -> None:
    report: Optional[DueDiligenceReport] = st.session_state.get("last_report")
    if report is None:
        st.info("Load a case study or run a VDR analysis first to open the IC Workflow & Q&A Copilot.")
        return

    mode_sub = st.radio(
        "Workspace Mode",
        ["IC Q&A Copilot (Hybrid RAG)", "Deal Lifecycle, VDR Q&A, IC Voting & Clean Team"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if mode_sub == "Deal Lifecycle, VDR Q&A, IC Voting & Clean Team":
        render_ic_workflow_view(
            report=report,
            store=_get_workflow_store(),
            audit_logger=_get_audit_logger(),
            active_role=st.session_state["active_role"],
            active_user=st.session_state["active_user"],
        )
        return

    # ---- Hybrid-RAG IC Q&A Copilot ----
    d = report.deal
    cfg = _get_config()
    dcf = _get_live_dcf_for_report(report)
    entities = st.session_state.get("source_entities", [])

    st.markdown(
        f'<h2>Investment Committee Q&amp;A Copilot — {d.entity_a_name} <span style="color:#B8935A;">×</span> {d.entity_b_name}</h2>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"{'LIVE LLM (' + cfg.llm_model + ')' if cfg.is_live_llm else 'Grounded Offline Synthesis'} · "
        f"Hybrid BM25 + TF-IDF Vector RAG over {sum(len(e.documents) for e in entities)} uploaded VDR filing(s)."
    )

    # Optional direct Hybrid RAG Search across VDR documents
    if entities:
        with st.expander("Direct Hybrid RAG Semantic + BM25 Search across VDR Filings", expanded=False):
            rag_q = st.text_input("Search VDR chunks", placeholder="e.g. change of control Amazon EDV warranty reserve...")
            if rag_q.strip():
                idx = HybridRAGIndex()
                idx.index_entities(entities)
                hits = idx.search(rag_q.strip(), top_k=5)
                for h in hits:
                    st.markdown(f"**`{h.chunk.filename} | p.{h.chunk.page_num}`** *(score {h.score:.3f})*")
                    st.caption(h.chunk.text[:450] + ("…" if len(h.chunk.text) > 450 else ""))

    with st.container(border=True):
        st.markdown("**Suggested Investment Committee Inquiries**")
        p_cols = st.columns(3)
        for idx, prompt_text in enumerate(SUGGESTED_IC_PROMPTS):
            if p_cols[idx % 3].button(prompt_text, key=f"ic_sug_{idx}", width="stretch"):
                st.session_state["qa_history"].append({"role": "user", "content": prompt_text})
                ans = answer_ic_question(prompt_text, report=report, history=st.session_state["qa_history"][:-1], dcf_result=dcf, source_entities=entities, config=cfg)
                st.session_state["qa_history"].append({"role": "assistant", "content": ans})
                st.rerun()

    for msg in st.session_state["qa_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_q = st.chat_input("Ask the Investment Committee Copilot...")
    if user_q:
        st.session_state["qa_history"].append({"role": "user", "content": user_q})
        with st.spinner("Synthesizing grounded IC response..."):
            ans = answer_ic_question(user_q, report=report, history=st.session_state["qa_history"][:-1], dcf_result=dcf, source_entities=entities, config=cfg)
        st.session_state["qa_history"].append({"role": "assistant", "content": ans})
        st.rerun()

    if st.session_state["qa_history"] and st.button("Clear Conversation"):
        st.session_state["qa_history"] = []
        st.rerun()


# =====================================================================
# VIEW 6: Deal Room, Benchmarking & Enterprise Branding/Security Studio
# =====================================================================

def _render_deal_room_and_studio_view() -> None:
    cfg = _get_config()
    st.markdown(section_header_html("Deal Room & Transaction Archive", "SUPABASE + LOCAL VAULT · ZERO N+1"), unsafe_allow_html=True)

    deals = local_vault.list_deal_room(limit=40, config=cfg)
    if not deals:
        st.info("Your Deal Room is currently empty. Seed the 3 Flagship Case Studies or save any active analysis.")
        if st.button("Seed Deal Room with 3 Flagship Case Studies", type="primary"):
            for meta in DEMO_CASES.values():
                local_vault.save_to_deal_room(meta["builder"](), config=cfg)
            st.rerun()
    else:
        f1, f2 = st.columns([2.2, 1.3])
        with f1:
            q = st.text_input("Search Deal Room", placeholder="Filter by acquirer, target, or sector...")
        with f2:
            rec_filter = st.multiselect("Recommendation", ["Go", "Conditional Go", "No-Go"], default=[])

        filtered = [
            it for it in deals
            if (not q.strip() or q.strip().lower() in f"{it.get('entity_a', '')} {it.get('entity_b', '')} {it.get('industry_sector', '')}".lower())
            and (not rec_filter or it.get("recommendation") in rec_filter)
        ]
        st.caption(f"Showing {len(filtered)} of {len(deals)} archived transaction(s).")

        for deal in filtered:
            deal_id = deal["id"]
            storage = deal.get("storage", "local")
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([2.6, 1.15, 1.45, 0.9, 0.8])
                with c1:
                    st.markdown(f"**{deal.get('entity_a', '?')}** × **{deal.get('entity_b', '?')}**")
                    ts_label = str(deal.get("created_at", "—"))[:16].replace("T", " ")
                    st.caption(f"{deal.get('industry_sector') or deal.get('deal_type') or ''}  ·  {ts_label}  ·  `{storage.upper()}`")
                with c2:
                    st.markdown(
                        f"<span style='font-family:JetBrains Mono,monospace;font-size:0.8rem;'>"
                        f"EV: <strong>{_fmt_b(deal.get('deal_value_usd_b'))}</strong><br/>"
                        f"NPV: <strong>{_fmt_b(deal.get('target_synergy_npv_usd_b'))}</strong></span>",
                        unsafe_allow_html=True,
                    )
                with c3:
                    rec_val = deal.get("recommendation")
                    if rec_val:
                        st.markdown(reco_badge_html(rec_val), unsafe_allow_html=True)
                with c4:
                    if st.button("Open", key=f"open_{deal_id}", width="stretch", type="primary"):
                        loaded = local_vault.load_from_deal_room(deal_id, storage=storage, config=cfg)
                        if loaded:
                            _set_active_report(loaded, entities=[], jump_to_memo=True)
                            st.rerun()
                with c5:
                    if st.button("Delete", key=f"del_{deal_id}", width="stretch"):
                        if local_vault.delete_from_deal_room(deal_id, storage=storage, config=cfg):
                            st.rerun()

        if len(deals) >= 2:
            with st.container(border=True):
                st.markdown(section_header_html("Side-by-Side Transaction Benchmarking", "PORTFOLIO IC COMPARISON"), unsafe_allow_html=True)
                options_map = {
                    f"{d.get('entity_a', '?')} × {d.get('entity_b', '?')} ({str(d.get('created_at', ''))[:10]}) [{d['id'][-6:]}]": d
                    for d in deals
                }
                selected_labels = st.multiselect(
                    "Select 2 to 4 transactions",
                    list(options_map.keys()),
                    default=list(options_map.keys())[: min(3, len(options_map))],
                    max_selections=4,
                )
                if len(selected_labels) >= 2:
                    loaded_reports = [
                        rep for lbl in selected_labels
                        if (rep := local_vault.load_from_deal_room(options_map[lbl]["id"], storage=options_map[lbl].get("storage", "auto"), config=cfg)) is not None
                    ]
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
                                "Grounding": f"{r.grounding_ratio()*100:.0f}%",
                            })
                        st.dataframe(pd.DataFrame(comp_rows), width="stretch", hide_index=True)

    st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)
    render_enterprise_studio_panel(_get_workflow_store(), _get_audit_logger())


# =====================================================================
# MAIN APPLICATION ENTRYPOINT
# =====================================================================

st.set_page_config(
    page_title="M&A Due Diligence Copilot — Enterprise Investment Committee Platform",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(INSTITUTIONAL_CSS, unsafe_allow_html=True)

_init_session_state()
_render_sidebar()

cfg = _get_config()
mode_badge = f"LIVE ZDR · {cfg.llm_model}" if cfg.is_live_llm else f"ENTERPRISE DEMO · {st.session_state['active_role']}"
st.markdown(masthead_html(mode_badge), unsafe_allow_html=True)

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
    selected_view = st.radio("Navigation", NAV_VIEWS, index=NAV_VIEWS.index(current_view), horizontal=True, label_visibility="collapsed")

if selected_view != st.session_state["active_view"]:
    st.session_state["active_view"] = selected_view
    st.rerun()

if selected_view == "Workspace & VDR":
    _render_workspace_view()
elif selected_view == "IC Memo & Red-Team":
    _render_memo_view()
elif selected_view == "Quant Valuation Suite":
    rep = st.session_state.get("last_report")
    if rep is None:
        st.info("Load a Flagship Case Study or run a VDR analysis first to open the Quant Valuation Suite.")
    else:
        render_quant_valuation_suite(rep)
elif selected_view == "Legal & Regulatory":
    rep = st.session_state.get("last_report")
    if rep is None:
        st.info("Load a Flagship Case Study or run a VDR analysis first to open Legal & Regulatory Due Diligence.")
    else:
        render_legal_diligence_view(rep, st.session_state.get("source_entities", []))
elif selected_view == "IC Workflow & Copilot":
    _render_workflow_and_copilot_view()
elif selected_view == "Deal Room & Studio":
    _render_deal_room_and_studio_view()
