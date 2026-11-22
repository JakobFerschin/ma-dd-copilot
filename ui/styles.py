"""
Bespoke Institutional Editorial / Private-Bank CSS & HTML component builders.

Design language: Goldman Sachs Marquee × McKinsey Global Institute × Financial Times.
  - Canvas: Warm Alabaster (#F6F5F0)
  - Elevated Surfaces: Crisp White (#FFFFFF) with hairline warm borders (#E2DFD7)
  - Institutional Primary: Deep Obsidian Navy (#0B192C)
  - Heritage Accent: Muted Antique Gold (#B8935A)
  - Typography: Playfair Display / Georgia serif headers + Inter sans + JetBrains Mono metrics
"""
from __future__ import annotations

import html
from typing import Optional

from schemas.report_schema import DueDiligenceReport

INSTITUTIONAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&family=Playfair+Display:wght@600;700&display=swap');

:root {
    --bg-canvas: #F6F5F0;
    --bg-surface: #FFFFFF;
    --navy-950: #0B192C;
    --navy-800: #162B47;
    --navy-700: #1E3A5F;
    --gold-500: #B8935A;
    --gold-100: #F5EFE4;
    --ink-900: #141822;
    --ink-600: #475569;
    --ink-400: #788496;
    --border-hairline: #E2DFD7;
    --forest: #14532D;
    --forest-bg: #ECFDF5;
    --crimson: #7F1D1D;
    --crimson-bg: #FEF2F2;
    --amber: #78350F;
    --amber-bg: #FFFBEB;
}

/* ---- Global Canvas & Typography ---- */
.stApp {
    background-color: var(--bg-canvas);
    color: var(--ink-900);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.block-container {
    padding-top: 1.6rem !important;
    padding-bottom: 3rem !important;
    max-width: 1340px !important;
}

h1, h2, h3, .serif-heading {
    font-family: 'Playfair Display', 'Georgia', 'Times New Roman', serif !important;
    color: var(--navy-950);
    letter-spacing: -0.015em;
}

/* ---- Clean Chrome ---- */
#MainMenu, footer, [data-testid="stAppDeployButton"], [data-testid="stToolbar"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
}
header[data-testid="stHeader"] {
    background: transparent !important;
}

/* ---- Sidebar: Obsidian Institutional Rail ---- */
section[data-testid="stSidebar"] {
    background: #0B192C !important;
    border-right: 1px solid #1C314E;
}
section[data-testid="stSidebar"] * {
    color: #E2E8F0 !important;
}
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea {
    background-color: #13253F !important;
    color: #F8FAFC !important;
    border: 1px solid #264066 !important;
    border-radius: 6px !important;
}
section[data-testid="stSidebar"] input::placeholder {
    color: #7E92AE !important;
    opacity: 1 !important;
}
section[data-testid="stSidebar"] [data-testid="stTextInput"] button {
    background-color: #13253F !important;
    border: 1px solid #264066 !important;
    color: #94A3B8 !important;
}
section[data-testid="stSidebar"] .stButton > button,
section[data-testid="stSidebar"] .stButton > button p,
section[data-testid="stSidebar"] .stButton > button span {
    background: #B8935A !important;
    color: #0B192C !important;
    font-weight: 700 !important;
    border: none !important;
    border-radius: 5px !important;
    letter-spacing: 0.03em;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: #C9A56B !important;
}
section[data-testid="stSidebar"] hr {
    border-color: #1F3555 !important;
}

/* ---- Executive Masthead ---- */
.ic-masthead {
    background: var(--navy-950);
    border-bottom: 3px solid var(--gold-500);
    border-radius: 8px;
    padding: 1.55rem 2rem;
    margin-bottom: 1.25rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: 0 6px 24px rgba(11, 25, 44, 0.14);
}
.ic-masthead-eyebrow {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--gold-500);
    margin-bottom: 0.3rem;
}
.ic-masthead-title {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1.95rem;
    font-weight: 700;
    color: #FFFFFF;
    margin: 0;
    line-height: 1.15;
}
.ic-masthead-sub {
    color: #B6C2D2;
    font-size: 0.88rem;
    margin-top: 0.35rem;
}
.ic-status-chip {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.42rem 0.85rem;
    border-radius: 4px;
    border: 1px solid rgba(184, 147, 90, 0.5);
    color: #F5EFE4;
    background: rgba(184, 147, 90, 0.14);
    white-space: nowrap;
}

/* ---- Institutional Navigation Bar (st.segmented_control) ---- */
div[data-testid="stSegmentedControl"] {
    background: #FFFFFF;
    padding: 0.32rem;
    border-radius: 8px;
    border: 1px solid var(--border-hairline);
    box-shadow: 0 1px 3px rgba(11, 25, 44, 0.04);
    margin-bottom: 1rem;
}
div[data-testid="stSegmentedControl"] button {
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.84rem !important;
    color: var(--ink-600) !important;
    border-radius: 6px !important;
    border: none !important;
    padding: 0.45rem 1.05rem !important;
}
div[data-testid="stSegmentedControl"] button[aria-pressed="true"],
div[data-testid="stSegmentedControl"] button[data-testid="stBaseButton-segmented_controlActive"] {
    background: var(--navy-950) !important;
    color: #FFFFFF !important;
}
div[data-testid="stSegmentedControl"] button[aria-pressed="true"] p,
div[data-testid="stSegmentedControl"] button[data-testid="stBaseButton-segmented_controlActive"] p {
    color: #FFFFFF !important;
}

/* ---- KPI Terminal Cards ---- */
.kpi-strip {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 0.85rem;
    margin: 0.75rem 0 1.25rem;
}
@media (max-width: 1024px) {
    .kpi-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.kpi-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-hairline);
    border-top: 3px solid var(--navy-950);
    border-radius: 6px;
    padding: 1rem 1.15rem;
    box-shadow: 0 2px 8px rgba(11, 25, 44, 0.04);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(11, 25, 44, 0.08);
}
.kpi-card.accent {
    background: var(--navy-950);
    border-color: var(--navy-800);
    border-top: 3px solid var(--gold-500);
}
.kpi-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-400);
}
.kpi-card.accent .kpi-label { color: #9FB0C7; }
.kpi-value {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1.72rem;
    font-weight: 700;
    color: var(--navy-950);
    font-variant-numeric: tabular-nums;
    margin-top: 0.25rem;
    line-height: 1.15;
}
.kpi-card.accent .kpi-value { color: #FFFFFF; }
.kpi-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: var(--ink-600);
    margin-top: 0.3rem;
}
.kpi-card.accent .kpi-sub { color: var(--gold-500); }

/* ---- Streamlit Native Metric Cards Polish ---- */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid var(--border-hairline);
    border-top: 3px solid var(--navy-950);
    border-radius: 6px;
    padding: 0.9rem 1.1rem !important;
    box-shadow: 0 1px 4px rgba(11, 25, 44, 0.04);
}
div[data-testid="stMetric"] label {
    color: var(--ink-400) !important;
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    font-weight: 700;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    color: var(--navy-950) !important;
    font-family: 'Playfair Display', Georgia, serif !important;
    font-size: 1.55rem !important;
    font-weight: 700;
}

/* ---- Elevated Section Containers ---- */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--bg-surface);
    border: 1px solid var(--border-hairline) !important;
    border-radius: 7px !important;
    box-shadow: 0 1px 4px rgba(11, 25, 44, 0.04);
}

/* ---- Badges & Tags ---- */
.reco-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.4rem 0.85rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    white-space: nowrap;
}
.reco-go   { background: var(--forest-bg); color: var(--forest); border: 1px solid #A7F3D0; }
.reco-nogo { background: var(--crimson-bg); color: var(--crimson); border: 1px solid #FECACA; }
.reco-cond { background: var(--amber-bg); color: var(--amber); border: 1px solid #FDE68A; }

.sev-badge {
    display: inline-block;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.67rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0.14rem 0.48rem;
    border-radius: 3px;
    margin-right: 0.45rem;
    vertical-align: middle;
}
.sev-critical { background: #FEE2E2; color: #991B1B; }
.sev-high     { background: #FEF3C7; color: #92400E; }
.sev-medium   { background: #E0E7FF; color: #1E3A8A; }
.sev-low      { background: #DCFCE7; color: #166534; }

.impact-pill {
    display: inline-block;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--navy-700);
    background: #F1F5F9;
    border: 1px solid #CBD5E1;
    border-radius: 3px;
    padding: 0.1rem 0.45rem;
    margin-left: 0.4rem;
}

.cite-line {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.73rem;
    color: var(--ink-400);
    margin: 0.2rem 0 0.6rem 1.25rem;
}
.claim-block {
    padding: 0.55rem 0.75rem;
    border-left: 2px solid var(--border-hairline);
    margin-bottom: 0.55rem;
    background: #FCFBF9;
    border-radius: 0 4px 4px 0;
}
.claim-block:hover {
    border-left-color: var(--gold-500);
}

.section-rule {
    border-bottom: 1.5px solid var(--gold-500);
    padding-bottom: 0.35rem;
    margin-bottom: 0.85rem;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
}
.section-rule h3 {
    margin: 0 !important;
    font-size: 1.22rem !important;
}
.section-kicker {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: var(--ink-400);
    text-transform: uppercase;
    letter-spacing: 0.08em;
}

/* ---- Primary Buttons ---- */
.stButton > button[kind="primary"] {
    background: var(--navy-950) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--navy-950) !important;
    border-radius: 6px !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em;
    padding: 0.55rem 1.2rem !important;
}
.stButton > button[kind="primary"]:hover {
    background: var(--navy-700) !important;
    border-color: var(--navy-700) !important;
}
.stDownloadButton > button {
    border-radius: 6px !important;
    font-weight: 600 !important;
}

/* ---- File Uploader ---- */
div[data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed #B5B0A4 !important;
    border-radius: 6px !important;
    background: #FCFBF8 !important;
}

/* ---- Case Study Selector Cards ---- */
.case-card {
    background: #FFFFFF;
    border: 1px solid var(--border-hairline);
    border-left: 3px solid var(--gold-500);
    border-radius: 6px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.5rem;
}
.case-title {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1.02rem;
    font-weight: 700;
    color: var(--navy-950);
}
.case-sub {
    font-size: 0.78rem;
    color: var(--ink-600);
    margin-top: 0.2rem;
}
</style>
"""


def masthead_html(mode_label: str, subtitle: str = "Institutional Pre-Merger Intelligence, Synergy DCF & Investment Committee Platform") -> str:
    return (
        '<div class="ic-masthead">'
        '  <div>'
        '    <div class="ic-masthead-eyebrow">MERGERS &amp; ACQUISITIONS ADVISORY  ·  INVESTMENT COMMITTEE SUITE</div>'
        '    <div class="ic-masthead-title">M&amp;A Due Diligence Copilot</div>'
        f'    <div class="ic-masthead-sub">{html.escape(subtitle)}</div>'
        '  </div>'
        f'  <div class="ic-status-chip">{html.escape(mode_label)}</div>'
        '</div>'
    )


def reco_badge_html(value: str) -> str:
    cls = {"Go": "reco-go", "No-Go": "reco-nogo", "Conditional Go": "reco-cond"}.get(value, "reco-cond")
    return f'<span class="reco-pill {cls}">■&nbsp;&nbsp;{html.escape(value)}</span>'


def severity_badge_html(sev: Optional[str]) -> str:
    if not sev:
        return ""
    cls = {
        "Critical": "sev-critical",
        "High": "sev-high",
        "Medium": "sev-medium",
        "Low": "sev-low",
    }.get(sev, "sev-medium")
    return f'<span class="sev-badge {cls}">{html.escape(sev)}</span>'


def section_header_html(title: str, kicker: str = "") -> str:
    k_html = f'<span class="section-kicker">{html.escape(kicker)}</span>' if kicker else ""
    return f'<div class="section-rule"><h3>{html.escape(title)}</h3>{k_html}</div>'


def kpi_strip_html(cards: list[tuple[str, str, str, bool]]) -> str:
    """Render a 5-card institutional KPI strip. Tuple: (label, value, sub, is_accent)."""
    items = []
    for lbl, val, sub, is_accent in cards:
        acc_cls = " accent" if is_accent else ""
        items.append(
            f'<div class="kpi-card{acc_cls}">'
            f'  <div class="kpi-label">{html.escape(lbl)}</div>'
            f'  <div class="kpi-value">{html.escape(val)}</div>'
            f'  <div class="kpi-sub">{html.escape(sub)}</div>'
            f'</div>'
        )
    return f'<div class="kpi-strip">{"".join(items)}</div>'


def claim_html(claim_text: str, severity: Optional[str], impact_m: Optional[float], sources: list) -> str:
    sev = severity_badge_html(severity)
    imp = f'<span class="impact-pill">${impact_m:,.0f}M/yr</span>' if impact_m is not None else ""
    cites = "".join(
        f'<div class="cite-line">↳ {html.escape(s.source_document)}  ·  {html.escape(s.location)}</div>'
        for s in sources
    )
    return (
        f'<div class="claim-block">'
        f'  <div>{sev}{html.escape(claim_text)}{imp}</div>'
        f'  {cites}'
        f'</div>'
    )


def grounding_summary_html(report: DueDiligenceReport) -> str:
    ratio = report.grounding_ratio() * 100.0
    cites = report.citation_count()
    docs = len(report.unique_sources())
    mode_tag = "DEMO / MOCK DATA" if report.deal.is_mock else "LIVE VDR GROUNDED"
    return (
        f'<div style="display:flex;gap:1.5rem;align-items:center;font-family:\'JetBrains Mono\',monospace;'
        f'font-size:0.74rem;color:#475569;padding:0.55rem 0.9rem;background:#FFFFFF;'
        f'border:1px solid #E2DFD7;border-radius:6px;margin-bottom:0.9rem;flex-wrap:wrap;">'
        f'  <span><strong style="color:#0B192C;">AUDIT MODE:</strong> {mode_tag}</span>'
        f'  <span><strong style="color:#0B192C;">GROUNDING COVERAGE:</strong> {ratio:.0f}%</span>'
        f'  <span><strong style="color:#0B192C;">CITATIONS:</strong> {cites}</span>'
        f'  <span><strong style="color:#0B192C;">FILINGS CITED:</strong> {docs}</span>'
        f'  <span><strong style="color:#0B192C;">OPEN DATA GAPS:</strong> {len(report.data_gaps)}</span>'
        f'</div>'
    )
