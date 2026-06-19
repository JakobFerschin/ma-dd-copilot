"""
M&A Due Diligence Copilot — Streamlit frontend (Boutique Edition).

Run:  streamlit run app.py

Boutique UI/UX:
  - Custom CSS injection: strips default Streamlit chrome, applies a tailored
    "financial terminal" aesthetic with serif report headers + clean sans data.
  - Tabbed layout: "New Analysis" (workflow) and "Deal Room" (history).
  - Multi-document upload per entity (10-K + ESG + Earnings Call, etc.).
  - High-end metric cards: Deal Value, Synergy NPV, Revenue/Cost Synergies.
  - Deal params (names, value, horizon) flow into the report.

Modes: auto-falls back to MOCK mode if no API key is set.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import streamlit as st

from config import settings as settings_module
from config.settings import settings, UPLOAD_DIR
from core.analyzer import AnalyzerError, DealParams, analyze_documents
from db.supabase_client import fetch_recent_deals, fetch_report, save_report
from schemas.report_schema import DueDiligenceReport
from utils.export_engine import report_to_docx_bytes
from utils.pdf_extractor import extract_entity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# =====================================================================
# CUSTOM CSS — "Boutique Financial Terminal" aesthetic
# =====================================================================

BOUTIQUE_CSS = """
<style>
/* ---- Typography hierarchy ---- */
.css-1v0mbdj, .css-1jrf438, [data-testid="stMarkdownContainer"] {
    font-family: 'Inter', 'Segoe UI', 'Helvetica Neue', sans-serif;
}
/* Serif headers for official-memo gravitas */
h1, h2, h3, .report-header {
    font-family: 'Georgia', 'Times New Roman', serif !important;
    letter-spacing: -0.01em;
}

/* ---- Strip default Streamlit chrome ---- */
#MainMenu, footer, header[data-testid="stHeader"] [data-testid="stToolbar"] {
    display: none !important;
}
.css-1adrfps e1f1d6gn0 { padding-top: 1rem; }

/* ---- Main app background + padding ---- */
.stApp {
    background-color: #fafaf7;
}
.block-container {
    padding-top: 2.5rem;
    max-width: 1280px;
}

/* ---- Metric cards: financial terminal style ---- */
div[data-testid="stMetric"] {
    background: linear-gradient(135deg, #0e1117 0%, #1a1f2e 100%);
    border: 1px solid #2a3142;
    border-radius: 8px;
    padding: 1.2rem 1.4rem !important;
    box-shadow: 0 4px 14px rgba(0,0,0,0.18);
    transition: transform 0.15s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
}
div[data-testid="stMetric"] label {
    color: #8b95a7 !important;
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    color: #f0f3f8 !important;
    font-family: 'Georgia', serif !important;
    font-size: 1.85rem !important;
    font-weight: 700;
}

/* ---- Section containers (report body) ---- */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: #ffffff;
    border: 1px solid #e3e1d8 !important;
    border-left: 3px solid #1a3a5c !important;
    border-radius: 4px !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

/* ---- Recommendation badge ---- */
.reco-badge {
    display: inline-block;
    padding: 0.4rem 1.1rem;
    border-radius: 999px;
    font-weight: 700;
    font-size: 0.95rem;
    letter-spacing: 0.03em;
}
.reco-go      { background: #1b4332; color: #d8f3dc; }
.reco-nogo    { background: #6a040f; color: #ffe5e9; }
.reco-cond    { background: #7f4f24; color: #fefae0; }

/* ---- Deal Room table ---- */
.deal-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.85rem 1rem;
    border-bottom: 1px solid #e3e1d8;
    transition: background 0.1s;
}
.deal-row:hover { background: #f0eee5; }

/* ---- File uploader polish ---- */
div[data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed #8b95a7 !important;
    border-radius: 6px !important;
    background: #ffffff !important;
}
</style>
"""


# =====================================================================
# Helpers (defined first so call sites always resolve cleanly)
# =====================================================================

def _humanize(field_name: str) -> str:
    """market_position_overlap -> Market Position Overlap"""
    return field_name.replace("_", " ").title()


def _fmt_b(value, suffix: str = "B") -> str:
    """Format a USD-billion value for metric cards; em-dash if None."""
    if value is None:
        return "—"
    return f"${value:.1f}{suffix}"


def _reco_badge_html(value: str) -> str:
    cls = {"Go": "reco-go", "No-Go": "reco-nogo", "Conditional Go": "reco-cond"}[value]
    icon = {"Go": "●", "No-Go": "●", "Conditional Go": "●"}[value]
    return f'<span class="reco-badge {cls}">{icon}&nbsp;&nbsp;{value.upper()}</span>'


def _save_uploads_to_disk(files, entity_tag: str) -> list[str]:
    """Persist uploaded files to UPLOAD_DIR and return their paths."""
    paths: list[str] = []
    for f in files:
        path = UPLOAD_DIR / f"{entity_tag}_{f.name}"
        path.write_bytes(f.getvalue())
        paths.append(str(path))
    return paths


def _run_analysis(name_a: str, files_a, name_b: str, files_b, params: DealParams) -> None:
    """Extract both entity doc-sets, run the analyzer, store report in session state."""
    progress = st.progress(0, text=f"Extracting {name_a} documents...")
    paths_a = _save_uploads_to_disk(files_a, "A")
    entity_a = extract_entity(name_a, paths_a, max_pages_per_doc=settings.max_pages_per_pdf)

    progress.progress(40, text=f"Extracting {name_b} documents...")
    paths_b = _save_uploads_to_disk(files_b, "B")
    entity_b = extract_entity(name_b, paths_b, max_pages_per_doc=settings.max_pages_per_pdf)

    progress.progress(70, text="Analyzing synergies & risks...")
    try:
        report = analyze_documents(entity_a, entity_b, params=params)
    except AnalyzerError as e:
        progress.empty()
        st.error(f"Live analysis failed: {e}")
        st.info("Tip: verify your API key and base URL in the sidebar, or clear the key to run in MOCK mode.")
        return

    progress.progress(100, text="Done.")
    st.session_state["last_report"] = report
    st.session_state["active_tab"] = 0  # jump to report view


def _render_metric_cards(report: DueDiligenceReport) -> None:
    """Top financial-terminal strip: the headline numbers."""
    d = report.deal
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Deal Value", _fmt_b(d.deal_value_usd_b))
    c2.metric("Synergy NPV (Horizon)", _fmt_b(d.target_synergy_npv_usd_b))
    c3.metric("Revenue Synergy (Run-rate)", _fmt_b(d.estimated_revenue_synergy_usd_b))
    c4.metric("Cost Synergy (Run-rate)", _fmt_b(d.estimated_cost_synergy_usd_b))


def _render_report(report: DueDiligenceReport) -> None:
    """Render the validated report across the 5-section MECE structure."""
    d = report.deal
    st.markdown(
        f'<h2 class="report-header">📄 {d.entity_a_name} <span style="color:#8b95a7;font-size:0.7em;">×</span> '
        f'{d.entity_b_name}</h2>',
        unsafe_allow_html=True,
    )
    st.caption(f"{d.deal_type}  ·  {d.analysis_date}  ·  {d.time_horizon_months or 36}-month horizon")

    _render_metric_cards(report)

    # ----- Section 1: Executive Summary -----
    es = report.executive_summary
    with st.container(border=True):
        st.markdown('<h3 class="report-header">1. Executive Summary</h3>', unsafe_allow_html=True)
        st.markdown(f"**Strategic Rationale.** {es.strategic_rationale}")
        c1, c2 = st.columns([1, 3])
        c1.metric("Integration Complexity", es.integration_complexity.value)
        c2.markdown(f"*{es.complexity_justification}*")
        st.markdown("**Top Deal-Breaker Risks.**")
        for risk in es.top_deal_breaker_risks:
            st.markdown(f"- {risk.claim}")
        st.markdown(_reco_badge_html(es.recommendation.value), unsafe_allow_html=True)
        st.caption(es.recommendation_rationale)

    # ----- Sections 2-5 (generic renderer driven by model fields) -----
    sections = [
        ("2. Strategic Fit & Commercial Synergies", "strategic_fit"),
        ("3. Operational & Technology", "operational_tech"),
        ("4. Cultural & Human Capital", "cultural_risk"),
        ("5. Regulatory, ESG & Macro", "regulatory_esg"),
    ]
    for title, attr in sections:
        with st.container(border=True):
            st.markdown(f'<h3 class="report-header">{title}</h3>', unsafe_allow_html=True)
            section = getattr(report, attr)
            for field_name in type(section).model_fields:
                st.markdown(f"**{_humanize(field_name)}**")
                for claim in getattr(section, field_name):
                    st.markdown(f"- {claim.claim}")
                    for src in claim.sources:
                        st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ *{src.source_document} — {src.location}*")

    # ----- Data gaps -----
    if report.data_gaps:
        with st.expander("⚠️ Data Gaps (insufficient public data)", expanded=False):
            for g in report.data_gaps:
                st.markdown(f"- {g}")

    # ----- Export + persist -----
    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    safe_a = "".join(ch if ch.isalnum() else "_" for ch in d.entity_a_name)[:20]
    safe_b = "".join(ch if ch.isalnum() else "_" for ch in d.entity_b_name)[:20]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with c1:
        st.download_button(
            "📄 Download Deal Memo (DOCX)",
            data=report_to_docx_bytes(report),
            file_name=f"DD_Memo_{safe_a}_{safe_b}_{ts}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
            type="primary",
        )
    with c2:
        st.download_button(
            "⬇️ Download JSON",
            data=json.dumps(report.model_dump(mode="json"), indent=2),
            file_name=f"dd_report_{safe_a}_{safe_b}_{ts}.json",
            mime="application/json",
            use_container_width=True,
        )
    with c3:
        if st.button("💾 Save to Deal Room", use_container_width=True):
            rid = save_report(report)
            if rid:
                st.success(f"Saved. Row id: `{rid}`")
            else:
                st.warning("Persistence skipped (Supabase not configured).")


def _render_deal_room() -> None:
    """Deal Room tab: list past deals + open one into the renderer."""
    st.markdown('<h2 class="report-header">🗂️ Deal Room</h2>', unsafe_allow_html=True)
    st.caption("Previously analyzed transactions stored in Supabase.")

    deals = fetch_recent_deals(limit=20)

    if not deals:
        st.info(
            "No saved deals found. This is expected if Supabase isn't configured "
            "or no analysis has been persisted yet. Run an analysis and click "
            "**Save to Deal Room** to populate this view."
        )
        return

    st.markdown(f"*{len(deals)} deal(s) on record.*")
    for deal in deals:
        with st.container(border=True):
            cols = st.columns([3, 1, 1])
            with cols[0]:
                st.markdown(
                    f"**{deal.get('entity_a', '?')}** × **{deal.get('entity_b', '?')}**"
                )
                st.caption(f"Analyzed: {deal.get('created_at', '—')[:16].replace('T', ' ')}")
            with cols[1]:
                if st.button("Open", key=f"open_{deal['id']}", use_container_width=True):
                    report = fetch_report(deal["id"])
                    if report:
                        st.session_state["last_report"] = report
                        st.session_state["active_tab"] = 0
                        st.rerun()
                    else:
                        st.error("Could not load this report.")
            with cols[2]:
                # Export any past deal to DOCX without leaving the Deal Room.
                report = fetch_report(deal["id"])
                if report:
                    safe_a = "".join(ch if ch.isalnum() else "_" for ch in (deal.get("entity_a") or "A"))[:20]
                    safe_b = "".join(ch if ch.isalnum() else "_" for ch in (deal.get("entity_b") or "B"))[:20]
                    st.download_button(
                        "📄 DOCX",
                        data=report_to_docx_bytes(report),
                        file_name=f"DD_Memo_{safe_a}_{safe_b}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key=f"dl_{deal['id']}",
                    )


# =====================================================================
# PAGE CONFIG + CSS
# =====================================================================
st.set_page_config(
    page_title="M&A Due Diligence Copilot",
    page_icon="🤝",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(BOUTIQUE_CSS, unsafe_allow_html=True)


# =====================================================================
# SIDEBAR — configuration
# =====================================================================
with st.sidebar:
    st.markdown('<h3 class="report-header">⚙️ Configuration</h3>', unsafe_allow_html=True)

    st.markdown("**LLM**")
    api_key = st.text_input("API Key", value=settings.llm_api_key, type="password",
                            help="OpenAI-compatible key. Leave empty to run in MOCK mode.")
    base_url = st.text_input("Base URL (optional)", value=settings.llm_base_url,
                             placeholder="https://api.openai.com/v1  (OpenRouter / Azure / local)")
    model = st.text_input("Model", value=settings.llm_model)

    st.divider()
    st.markdown("**Supabase**")
    sb_url = st.text_input("Supabase URL", value=settings.supabase_url)
    sb_key = st.text_input("Supabase Key", value=settings.supabase_key, type="password")

    if st.button("Apply settings", use_container_width=True):
        settings_module.settings.llm_api_key = api_key
        settings_module.settings.llm_base_url = base_url
        settings_module.settings.llm_model = model
        settings_module.settings.supabase_url = sb_url
        settings_module.settings.supabase_key = sb_key
        st.success("Settings applied for this session.")
        st.rerun()

    st.divider()
    mode = "🟢 LIVE (LLM)" if (api_key or settings.llm_api_key) else "🟡 MOCK (no key)"
    st.caption(f"Active mode: **{mode}**")


# =====================================================================
# HEADER
# =====================================================================
st.markdown(
    '<h1 class="report-header">M&amp;A Due Diligence Copilot</h1>'
    '<p style="color:#5a6478;margin-top:-0.5rem;">Boutique Pre-Merger Intelligence &amp; Synergy Analysis</p>',
    unsafe_allow_html=True,
)

# =====================================================================
# TABS — New Analysis | Deal Room
# =====================================================================
tab_analysis, tab_dealroom = st.tabs(["🆕 New Analysis", "🗂️ Deal Room"])

with tab_analysis:
    # ---- Deal parameters ----
    with st.container(border=True):
        st.markdown('<h3 class="report-header">Deal Parameters</h3>', unsafe_allow_html=True)
        p1, p2, p3 = st.columns([2, 2, 1])
        with p1:
            name_a = st.text_input("Acquirer (Entity A)", value="", placeholder="e.g. Volkswagen AG")
        with p2:
            name_b = st.text_input("Target (Entity B)", value="", placeholder="e.g. Rivian Automotive")
        p3_a, p3_b = st.columns(2)
        with p3_a:
            deal_value = st.number_input("Deal Value ($B)", min_value=0.0, value=17.5, step=0.5)
        with p3_b:
            horizon = st.select_slider(
                "Horizon (mo.)", options=[12, 24, 36, 48, 60], value=36
            )

    # ---- Multi-document uploaders ----
    st.markdown('<h3 class="report-header">Source Documents</h3>', unsafe_allow_html=True)
    st.caption("Upload multiple documents per entity — the 10-K, ESG report, and Earnings Call transcript are analyzed together.")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**🔵 Acquirer — {name_a or 'Entity A'}**")
        files_a = st.file_uploader(
            "Acquirer documents",
            type=["pdf"],
            accept_multiple_files=True,
            key="upload_a",
            label_visibility="collapsed",
        )
        if files_a:
            st.caption(f"📄 {len(files_a)} file(s): {', '.join(f.name for f in files_a)}")
    with col_b:
        st.markdown(f"**🟠 Target — {name_b or 'Entity B'}**")
        files_b = st.file_uploader(
            "Target documents",
            type=["pdf"],
            accept_multiple_files=True,
            key="upload_b",
            label_visibility="collapsed",
        )
        if files_b:
            st.caption(f"📄 {len(files_b)} file(s): {', '.join(f.name for f in files_b)}")

    run_enabled = bool(name_a and name_b and files_a and files_b)
    if st.button("🚀 Run Synergy Analysis", disabled=not run_enabled, type="primary", use_container_width=True):
        params = DealParams(deal_value_usd_b=deal_value, time_horizon_months=horizon)
        _run_analysis(name_a, files_a, name_b, files_b, params)

    # ---- Render latest report inline (below the upload form) ----
    if "last_report" in st.session_state:
        st.markdown("---")
        _render_report(st.session_state["last_report"])

with tab_dealroom:
    _render_deal_room()
