"""
DOCX export engine — converts a DueDiligenceReport into a top-tier consulting memo.

Output structure (mirrors the Streamlit report):
  1. Cover page       — deal title, deal params, date
  2. Table of Contents — Word TOC field (updates on open via "Update Field")
  3. Executive Summary — rationale, complexity, top risks, recommendation
  4. Metric Cards table — Deal Value / Synergy NPV / Revenue / Cost
  5. Sections 2–5      — each claim with inline citation lines
  6. Data Gaps         — explicit list

Citation policy: inline italic grey lines under each claim (e.g.
"Source: 10K.pdf — p.47, Note 9"). This is how real consulting memos render
sources and avoids python-docx's lack of a native footnote API.

All styling (navy headers, shaded cells, serif fonts) is applied via direct
XML/property manipulation so the output is deterministic across Word versions
— we don't rely on built-in table styles that vary by install.
"""
from __future__ import annotations

import io
from typing import Iterable

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Cm, Pt, RGBColor

from schemas.report_schema import Claim, DueDiligenceReport

# ---- Brand palette (matches the "financial terminal" web UI) ----
NAVY = RGBColor(0x1A, 0x3A, 0x5C)
SLATE = RGBColor(0x4A, 0x55, 0x68)
MUTED = RGBColor(0x8B, 0x95, 0xA7)
INK = RGBColor(0x14, 0x18, 0x22)
NAVY_HEX = "1A3A5C"
LIGHT_BG_HEX = "F4F2EC"

SERIF = "Georgia"
SANS = "Calibri"


# =====================================================================
# Low-level XML helpers (python-docx doesn't expose these directly)
# =====================================================================

def _set_cell_shading(cell, hex_fill: str) -> None:
    """Shade a table cell via raw XML — reliable across Word versions."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _add_toc(document: Document) -> None:
    """
    Insert a Word Table of Contents field. Page numbers populate when the user
    opens the doc and accepts Word's "Update fields" prompt (standard behavior
    for TOC fields in any generated .docx).
    """
    para = document.add_paragraph()
    run = para.add_run()
    fld_begin = OxmlElement("w:fldChar"); fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar"); fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "Right-click and select 'Update Field' to populate the Table of Contents."
    fld_end = OxmlElement("w:fldChar"); fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin); run._r.append(instr); run._r.append(fld_sep)
    run._r.append(placeholder); run._r.append(fld_end)


def _page_break(document: Document) -> None:
    document.add_page_break()


def _style_run(run, *, font: str = SANS, size: int = 11, bold: bool = False,
               italic: bool = False, color: RGBColor = INK) -> None:
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    # Ensure font applies to East-Asian + complex-script text too
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), font)
    rfonts.set(qn("w:hAnsi"), font)


# =====================================================================
# Section builders
# =====================================================================

def _build_cover(document: Document, report: DueDiligenceReport) -> None:
    d = report.deal
    # Vertical spacing to push title into the upper-middle
    for _ in range(6):
        document.add_paragraph()

    p = document.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _style_run(p.add_run("PRE-MERGER INTELLIGENCE MEMO"), font=SERIF, size=12, bold=True, color=MUTED)

    p = document.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _style_run(p.add_run(f"{d.entity_a_name}  ×  {d.entity_b_name}"),
               font=SERIF, size=30, bold=True, color=NAVY)

    p = document.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _style_run(p.add_run("Due Diligence & Synergy Intelligence"), font=SERIF, size=16, color=SLATE)

    document.add_paragraph()
    # Cover meta strip
    meta = [
        ("Deal Type", d.deal_type),
        ("Deal Value", _fmt_b(d.deal_value_usd_b)),
        ("Analysis Date", d.analysis_date),
        ("Synergy Horizon", f"{d.time_horizon_months or 36} months"),
    ]
    for label, value in meta:
        p = document.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _style_run(p.add_run(f"{label}:  "), font=SANS, size=11, bold=True, color=SLATE)
        _style_run(p.add_run(str(value)), font=SANS, size=11, color=INK)
    _page_break(document)


def _build_toc(document: Document) -> None:
    p = document.add_paragraph()
    _style_run(p.add_run("Table of Contents"), font=SERIF, size=20, bold=True, color=NAVY)
    document.add_paragraph()
    _add_toc(document)
    _page_break(document)


def _build_metric_table(document: Document, report: DueDiligenceReport) -> None:
    d = report.deal
    rows = [
        ("Estimated Deal Value", _fmt_b(d.deal_value_usd_b)),
        ("Target Synergy NPV (Horizon)", _fmt_b(d.target_synergy_npv_usd_b)),
        ("Revenue Synergy (Run-rate)", _fmt_b(d.estimated_revenue_synergy_usd_b)),
        ("Cost Synergy (Run-rate)", _fmt_b(d.estimated_cost_synergy_usd_b)),
        ("Estimated Integration Cost", _fmt_b(d.estimated_integration_cost_usd_b)),
    ]
    table = document.add_table(rows=len(rows) + 1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header
    for i, label in enumerate(["Metric", "Value (USD)"]):
        cell = table.rows[0].cells[i]
        _set_cell_shading(cell, NAVY_HEX)
        p = cell.paragraphs[0]
        _style_run(p.add_run(label), font=SANS, size=10, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

    # Body — alternating shading for readability
    for r_idx, (label, value) in enumerate(rows, start=1):
        fill = LIGHT_BG_HEX if r_idx % 2 == 0 else "FFFFFF"
        for c_idx, text in enumerate([label, value]):
            cell = table.rows[r_idx].cells[c_idx]
            _set_cell_shading(cell, fill)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            _style_run(p.add_run(text), font=SANS, size=11,
                       bold=(c_idx == 0), color=(NAVY if c_idx == 0 else INK))
    # Column widths
    for row in table.rows:
        row.cells[0].width = Cm(8)
        row.cells[1].width = Cm(6)
    document.add_paragraph()


def _add_section_heading(document: Document, text: str) -> None:
    p = document.add_paragraph()
    _style_run(p.add_run(text), font=SERIF, size=16, bold=True, color=NAVY)
    # Bottom border on the heading for a clean rule
    p_pr = p._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "4"); bottom.set(qn("w:color"), NAVY_HEX)
    p_bdr.append(bottom); p_pr.append(p_bdr)


def _add_claims(document: Document, label: str, claims: Iterable[Claim]) -> None:
    p = document.add_paragraph()
    _style_run(p.add_run(label), font=SANS, size=12, bold=True, color=SLATE)
    for claim in claims:
        cp = document.add_paragraph(style="List Bullet")
        _style_run(cp.add_run(claim.claim), font=SANS, size=11, color=INK)
        # Inline citations rendered as italic muted lines beneath the claim
        for src in claim.sources:
            cite = document.add_paragraph()
            cite.paragraph_format.left_indent = Cm(1.0)
            _style_run(cite.add_run(f"Source: {src.source_document} — {src.location}"),
                       font=SANS, size=9, italic=True, color=MUTED)


def _build_executive_summary(document: Document, report: DueDiligenceReport) -> None:
    es = report.executive_summary
    _add_section_heading(document, "1. Executive Summary")

    p = document.add_paragraph()
    _style_run(p.add_run("Strategic Rationale.  "), font=SANS, size=11, bold=True, color=NAVY)
    _style_run(p.add_run(es.strategic_rationale), font=SANS, size=11, color=INK)

    # Complexity + recommendation as a 2-col mini-table
    t = document.add_table(rows=2, cols=2)
    for c_idx, (label, value) in enumerate([
        ("Integration Complexity", es.integration_complexity.value),
        ("Recommendation", es.recommendation.value),
    ]):
        _set_cell_shading(t.rows[0].cells[c_idx], LIGHT_BG_HEX)
        _style_run(t.rows[0].cells[c_idx].paragraphs[0].add_run(label),
                   font=SANS, size=10, bold=True, color=SLATE)
        _style_run(t.rows[1].cells[c_idx].paragraphs[0].add_run(value),
                   font=SERIF, size=13, bold=True, color=NAVY)
    document.add_paragraph()

    _style_run(document.add_paragraph().add_run(es.complexity_justification),
               font=SANS, size=10, italic=True, color=SLATE)

    _add_claims(document, "Top Deal-Breaker Risks", es.top_deal_breaker_risks)

    p = document.add_paragraph()
    _style_run(p.add_run("Recommendation Rationale.  "), font=SANS, size=11, bold=True, color=NAVY)
    _style_run(p.add_run(es.recommendation_rationale), font=SANS, size=11, color=INK)


# =====================================================================
# Public API
# =====================================================================

def _fmt_b(value, suffix: str = "B") -> str:
    if value is None:
        return "—"
    return f"${value:.1f}{suffix}"


def report_to_docx_bytes(report: DueDiligenceReport) -> bytes:
    """
    Render a DueDiligenceReport to a fully formatted .docx as a byte string.
    Suitable for Streamlit st.download_button(data=...).
    """
    doc = Document()

    # Default style (body)
    style = doc.styles["Normal"]
    style.font.name = SANS
    style.font.size = Pt(11)
    style.font.color.rgb = INK

    _build_cover(doc, report)
    _build_toc(doc)
    _build_executive_summary(doc, report)
    _build_metric_table(doc, report)

    sections = [
        ("2. Strategic Fit & Commercial Synergies", "strategic_fit"),
        ("3. Operational & Technology", "operational_tech"),
        ("4. Cultural & Human Capital", "cultural_risk"),
        ("5. Regulatory, ESG & Macro", "regulatory_esg"),
    ]
    for title, attr in sections:
        _add_section_heading(document=doc, text=title)
        section = getattr(report, attr)
        for field_name in type(section).model_fields:
            label = field_name.replace("_", " ").title()
            _add_claims(doc, label, getattr(section, field_name))

    if report.data_gaps:
        _add_section_heading(doc, "Data Gaps")
        for g in report.data_gaps:
            p = doc.add_paragraph(style="List Bullet")
            _style_run(p.add_run(g), font=SANS, size=10, italic=True, color=MUTED)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def report_to_docx_file(report: DueDiligenceReport, path: str) -> None:
    """Convenience: write the .docx directly to disk."""
    with open(path, "wb") as f:
        f.write(report_to_docx_bytes(report))
