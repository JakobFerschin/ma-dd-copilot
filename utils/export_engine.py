"""
Institutional Multi-Format Export Engine.

Generates four boardroom-ready deliverables from any validated `DueDiligenceReport`:
  1. `report_to_docx_bytes(report, dcf=None)`  — Investment Committee Memo (.docx)
     with Cover Page, Running Header/Footer + Page Numbers, Word TOC, Executive
     Recommendation Callout, Financial Summary, Bottom-Up Workstreams, 5 MECE
     Sections with inline citations, Risk Register, PMI Roadmap, and VDR Q&A.
  2. `report_to_excel_bytes(report, dcf=None)` — Live-Formula Financial Model (.xlsx)
     formatted in Wall Street color conventions (Blue inputs, Black formulas,
     Navy headers) across 4 worksheets: Executive Summary, Synergy DCF Model,
     Synergy Workstreams, and Risk Register & PMI.
  3. `report_to_html_bytes(report, dcf=None)`  — Print-to-PDF-ready Boardroom Brief (.html).
  4. `report_to_docx_file(report, path)`       — Convenience disk writer.
"""
from __future__ import annotations

import html
import io
from typing import Iterable, Optional

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from core.financial_model import (
    SynergyDCFResult,
    compute_sensitivity_matrix,
    compute_synergy_dcf,
)
from schemas.report_schema import Claim, DueDiligenceReport

# ---- Institutional Palette (matches Private Bank / Financial Terminal UI) ----
NAVY = RGBColor(0x0B, 0x19, 0x2C)
NAVY_MID = RGBColor(0x1A, 0x3A, 0x5C)
GOLD = RGBColor(0xB8, 0x93, 0x5A)
SLATE = RGBColor(0x47, 0x55, 0x69)
MUTED = RGBColor(0x78, 0x84, 0x96)
INK = RGBColor(0x14, 0x18, 0x22)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FOREST = RGBColor(0x14, 0x53, 0x2D)
CRIMSON = RGBColor(0x7F, 0x1D, 0x1D)
AMBER = RGBColor(0x78, 0x35, 0x0F)

NAVY_HEX = "0B192C"
NAVY_MID_HEX = "1A3A5C"
GOLD_HEX = "B8935A"
LIGHT_BG_HEX = "F6F5F0"
BORDER_HEX = "DCD8CE"
RECO_BG = {"Go": "ECFDF5", "No-Go": "FEF2F2", "Conditional Go": "FFFBEB"}
RECO_FG = {"Go": FOREST, "No-Go": CRIMSON, "Conditional Go": AMBER}

SERIF = "Georgia"
SANS = "Calibri"


def _fmt_b(value: Optional[float], suffix: str = "B") -> str:
    if value is None:
        return "—"
    return f"${value:.2f}{suffix}"


def _fmt_m(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}M"


# =====================================================================
# Low-level python-docx XML helpers
# =====================================================================

def _set_cell_shading(cell, hex_fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _set_cell_margins(cell, top: int = 80, bottom: int = 80, left: int = 120, right: int = 120) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = OxmlElement("w:tcMar")
    for side, val in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        tc_mar.append(node)
    tc_pr.append(tc_mar)


def _set_table_borders(table, color: str = BORDER_HEX) -> None:
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "bottom", "insideH"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), color)
        borders.append(b)
    for side in ("left", "right", "insideV"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "none")
        borders.append(b)
    tbl_pr.append(borders)


def _add_field(paragraph, instr_text: str, placeholder: str = "", font_size: int = 9, color: RGBColor = MUTED) -> None:
    run = paragraph.add_run()
    _style_run(run, font=SANS, size=font_size, color=color)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instr_text
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    txt = OxmlElement("w:t")
    txt.text = placeholder
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_sep)
    run._r.append(txt)
    run._r.append(fld_end)


def _add_toc(document: Document) -> None:
    para = document.add_paragraph()
    _add_field(
        para,
        'TOC \\o "1-2" \\h \\z \\u',
        placeholder="Right-click and select 'Update Field' to populate the Table of Contents.",
        font_size=10,
        color=SLATE,
    )


def _style_run(
    run,
    *,
    font: str = SANS,
    size: int = 10,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor = INK,
) -> None:
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), font)
    rfonts.set(qn("w:hAnsi"), font)


def _configure_headers_footers(document: Document, report: DueDiligenceReport) -> None:
    d = report.deal
    section = document.sections[0]
    section.different_first_page_header_footer = True
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.1)
    section.right_margin = Cm(2.1)

    # Running Header (pages 2+)
    hdr = section.header
    hp = hdr.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _style_run(
        hp.add_run(f"CONFIDENTIAL  |  {d.entity_a_name} × {d.entity_b_name}  —  PRE-MERGER INTELLIGENCE MEMO"),
        font=SANS, size=8, color=MUTED,
    )

    # Running Footer (pages 2+)
    ftr = section.footer
    fp = ftr.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _style_run(fp.add_run(f"M&A Due Diligence Copilot  ·  {d.analysis_date}   |   Page "), font=SANS, size=8, color=MUTED)
    _add_field(fp, "PAGE", "1", font_size=8, color=MUTED)
    _style_run(fp.add_run(" of "), font=SANS, size=8, color=MUTED)
    _add_field(fp, "NUMPAGES", "1", font_size=8, color=MUTED)


def _add_section_heading(document: Document, text: str) -> None:
    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = True
    _style_run(p.add_run(text), font=SERIF, size=15, bold=True, color=NAVY)
    p_pr = p._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), GOLD_HEX)
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _build_styled_table(
    document: Document,
    headers: list[str],
    rows: list[list[str]],
    col_widths_cm: Optional[list[float]] = None,
) -> None:
    table = document.add_table(rows=len(rows) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)

    for c_idx, h_text in enumerate(headers):
        cell = table.rows[0].cells[c_idx]
        _set_cell_shading(cell, NAVY_HEX)
        _set_cell_margins(cell, top=90, bottom=90, left=100, right=100)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _style_run(cell.paragraphs[0].add_run(h_text), font=SANS, size=9, bold=True, color=WHITE)

    for r_idx, row_vals in enumerate(rows, start=1):
        fill = LIGHT_BG_HEX if r_idx % 2 == 0 else "FFFFFF"
        for c_idx, val in enumerate(row_vals):
            cell = table.rows[r_idx].cells[c_idx]
            _set_cell_shading(cell, fill)
            _set_cell_margins(cell, top=70, bottom=70, left=100, right=100)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _style_run(
                cell.paragraphs[0].add_run(str(val)),
                font=SANS,
                size=9,
                bold=(c_idx == 0),
                color=(NAVY if c_idx == 0 else INK),
            )

    if col_widths_cm:
        for row in table.rows:
            for c_idx, width in enumerate(col_widths_cm):
                row.cells[c_idx].width = Cm(width)

    document.add_paragraph()


def _build_cover(document: Document, report: DueDiligenceReport, dcf: SynergyDCFResult) -> None:
    d = report.deal
    es = report.executive_summary

    # Top institutional banner bar
    banner = document.add_table(rows=1, cols=1)
    banner.alignment = WD_TABLE_ALIGNMENT.CENTER
    bcell = banner.rows[0].cells[0]
    _set_cell_shading(bcell, NAVY_HEX)
    _set_cell_margins(bcell, top=180, bottom=180, left=220, right=220)
    bp = bcell.paragraphs[0]
    _style_run(bp.add_run("INVESTMENT COMMITTEE  ·  STRICTLY PRIVATE & CONFIDENTIAL"),
               font=SANS, size=9, bold=True, color=GOLD)

    for _ in range(3):
        document.add_paragraph()

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _style_run(p.add_run("PRE-MERGER INTELLIGENCE MEMO"), font=SANS, size=11, bold=True, color=GOLD)

    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(6)
    _style_run(p.add_run(f"{d.entity_a_name}  ×  {d.entity_b_name}"),
               font=SERIF, size=26, bold=True, color=NAVY)

    p = document.add_paragraph()
    subtitle = d.industry_sector or "Due Diligence & Synergy Intelligence"
    _style_run(p.add_run(subtitle), font=SERIF, size=14, italic=True, color=SLATE)

    document.add_paragraph()

    # Executive Deal Meta Grid
    meta_rows = [
        ["Transaction Type", d.deal_type],
        ["Transaction Value (EV)", _fmt_b(d.deal_value_usd_b)],
        ["Deterministic Synergy NPV", _fmt_b(dcf.total_synergy_npv_b)],
        ["IC Recommendation", es.recommendation.value.upper()],
        ["Integration Complexity", es.integration_complexity.value],
        ["Synergy Capture Horizon", f"{d.time_horizon_months or 36} Months"],
        ["Analysis Date", d.analysis_date],
        ["Source Grounding", f"{report.citation_count()} Citations across {len(report.unique_sources())} Filing(s)"],
    ]
    _build_styled_table(document, ["Transaction Parameter", "Institutional Assessment"], meta_rows, [6.5, 10.0])

    p_foot = document.add_paragraph()
    p_foot.paragraph_format.space_before = Pt(24)
    _style_run(
        p_foot.add_run(
            "Prepared by M&A Due Diligence Copilot. All analytical claims are grounded in source filings "
            "or explicitly flagged as public data gaps for Phase-2 Virtual Data Room verification."
        ),
        font=SANS, size=8, italic=True, color=MUTED,
    )
    document.add_page_break()


def _build_toc(document: Document) -> None:
    p = document.add_paragraph()
    _style_run(p.add_run("Table of Contents"), font=SERIF, size=18, bold=True, color=NAVY)
    document.add_paragraph()
    _add_toc(document)
    document.add_page_break()


def _add_claims(document: Document, label: str, claims: Iterable[Claim]) -> None:
    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    _style_run(p.add_run(label), font=SANS, size=11, bold=True, color=NAVY_MID)

    for claim in claims:
        cp = document.add_paragraph(style="List Bullet")
        cp.paragraph_format.space_after = Pt(2)
        if claim.severity:
            _style_run(cp.add_run(f"[{claim.severity.value.upper()}] "), font=SANS, size=9, bold=True, color=GOLD)
        _style_run(cp.add_run(claim.claim), font=SANS, size=10, color=INK)
        if claim.quantified_impact_usd_m is not None:
            _style_run(
                cp.add_run(f"  (Est. Impact: {_fmt_m(claim.quantified_impact_usd_m)}/yr)"),
                font=SANS, size=9, bold=True, color=NAVY_MID,
            )
        for src in claim.sources:
            cite = document.add_paragraph()
            cite.paragraph_format.left_indent = Cm(0.9)
            cite.paragraph_format.space_after = Pt(2)
            _style_run(
                cite.add_run(f"Source: {src.source_document} — {src.location}"),
                font=SANS, size=8, italic=True, color=MUTED,
            )


def _build_executive_summary(document: Document, report: DueDiligenceReport, dcf: SynergyDCFResult) -> None:
    es = report.executive_summary
    d = report.deal
    _add_section_heading(document, "1. Executive Summary")

    # Recommendation Callout Box
    box = document.add_table(rows=1, cols=3)
    box.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(box)
    reco_val = es.recommendation.value
    cells_spec = [
        ("IC RECOMMENDATION", reco_val.upper(), RECO_BG.get(reco_val, LIGHT_BG_HEX), RECO_FG.get(reco_val, NAVY)),
        ("INTEGRATION COMPLEXITY", es.integration_complexity.value.upper(), LIGHT_BG_HEX, NAVY),
        ("CONVICTION INDEX", f"{d.overall_conviction_score}/100" if d.overall_conviction_score is not None else "—", LIGHT_BG_HEX, NAVY),
    ]
    for idx, (lbl, val, bg, fg) in enumerate(cells_spec):
        cell = box.rows[0].cells[idx]
        _set_cell_shading(cell, bg)
        _set_cell_margins(cell, top=100, bottom=100, left=120, right=120)
        p0 = cell.paragraphs[0]
        _style_run(p0.add_run(lbl + "\n"), font=SANS, size=8, bold=True, color=SLATE)
        _style_run(p0.add_run(val), font=SERIF, size=13, bold=True, color=fg)
    document.add_paragraph()

    p = document.add_paragraph()
    _style_run(p.add_run("Strategic Rationale.  "), font=SANS, size=10, bold=True, color=NAVY)
    _style_run(p.add_run(es.strategic_rationale), font=SANS, size=10, color=INK)

    p = document.add_paragraph()
    _style_run(p.add_run("Recommendation Stance.  "), font=SANS, size=10, bold=True, color=NAVY)
    _style_run(p.add_run(es.recommendation_rationale), font=SANS, size=10, color=INK)

    p = document.add_paragraph()
    _style_run(p.add_run("Integration Complexity Assessment.  "), font=SANS, size=10, bold=True, color=NAVY)
    _style_run(p.add_run(es.complexity_justification), font=SANS, size=10, italic=True, color=SLATE)

    if report.valuation_commentary:
        p = document.add_paragraph()
        _style_run(p.add_run("Valuation & Accretion Commentary.  "), font=SANS, size=10, bold=True, color=NAVY)
        _style_run(p.add_run(report.valuation_commentary), font=SANS, size=10, color=INK)

    _add_claims(document, "Top Deal-Breaker Risks", es.top_deal_breaker_risks)

    # Financial Summary Table
    fin_rows = [
        ["Transaction Enterprise Value", _fmt_b(d.deal_value_usd_b)],
        ["Deterministic Synergy NPV (After-Tax)", _fmt_b(dcf.total_synergy_npv_b)],
        ["Value Creation (% of Deal Value)", f"{dcf.value_creation_pct_of_deal:.1f}%" if dcf.value_creation_pct_of_deal is not None else "—"],
        ["Risk-Adjusted Revenue Synergy (Run-Rate)", _fmt_b(dcf.steady_state_revenue_synergy_b)],
        ["Risk-Adjusted Cost Synergy (Run-Rate)", _fmt_b(dcf.steady_state_cost_synergy_b)],
        ["One-Time Integration Costs", _fmt_b(dcf.total_integration_cost_b)],
        ["Simple Payback / Project IRR", f"{dcf.payback_years:.2f} yrs  |  {f'{dcf.synergy_irr_pct:.1f}%' if dcf.synergy_irr_pct is not None else '—'}" if dcf.payback_years else "—"],
        ["DCF Baseline Assumptions", f"WACC {dcf.assumptions.wacc_pct:.1f}%  ·  Tax {dcf.assumptions.tax_rate_pct:.0f}%  ·  Terminal g {dcf.assumptions.terminal_growth_pct:.1f}%"],
    ]
    _build_styled_table(document, ["Financial Valuation Metric", "Output"], fin_rows, [9.5, 7.0])


def report_to_docx_bytes(
    report: DueDiligenceReport,
    dcf_result: Optional[SynergyDCFResult] = None,
) -> bytes:
    """
    Render a DueDiligenceReport into an institutional Investment Committee .docx byte stream.
    """
    dcf = dcf_result or compute_synergy_dcf(report)
    doc = Document()
    _configure_headers_footers(doc, report)

    style = doc.styles["Normal"]
    style.font.name = SANS
    style.font.size = Pt(10)
    style.font.color.rgb = INK

    _build_cover(doc, report, dcf)
    _build_toc(doc)
    _build_executive_summary(doc, report, dcf)

    # Sections 2–5
    sections = [
        ("2. Strategic Fit & Commercial Synergies", "strategic_fit"),
        ("3. Operational & Technology", "operational_tech"),
        ("4. Cultural & Human Capital", "cultural_risk"),
        ("5. Regulatory, ESG & Macro", "regulatory_esg"),
    ]
    for title, attr in sections:
        _add_section_heading(doc, title)
        section = getattr(report, attr)
        for field_name in type(section).model_fields:
            label = field_name.replace("_", " ").title()
            _add_claims(doc, label, getattr(section, field_name))

    # Section 6: Bottom-Up Synergy Workstreams
    if report.synergy_workstreams:
        _add_section_heading(doc, "6. Bottom-Up Synergy Workstreams")
        ws_rows = [
            [
                ws.name,
                ws.synergy_type.value,
                _fmt_m(ws.annual_run_rate_usd_m),
                f"{ws.probability_pct:.0f}%",
                _fmt_m(ws.annual_run_rate_usd_m * ws.probability_pct / 100.0),
                _fmt_m(ws.one_time_cost_usd_m),
                ws.owner,
            ]
            for ws in report.synergy_workstreams
        ]
        _build_styled_table(
            doc,
            ["Workstream", "Type", "Gross ($M)", "Prob.", "Risk-Adj ($M)", "1x Cost", "Owner"],
            ws_rows,
            [4.5, 1.8, 2.0, 1.4, 2.1, 1.9, 2.8],
        )

    # Section 7: Risk Register Matrix
    if report.risk_register:
        _add_section_heading(doc, "7. Investment Committee Risk Register")
        r_rows = [
            [
                ("★ " if r.is_deal_breaker else "") + r.title,
                r.dimension.value,
                r.severity.value,
                f"{r.likelihood}×{r.impact} ({r.likelihood * r.impact})",
                r.mitigation,
            ]
            for r in report.risk_register
        ]
        _build_styled_table(
            doc,
            ["Risk (★ = Deal-Breaker)", "Dimension", "Severity", "L×I", "Mitigation Plan"],
            r_rows,
            [3.8, 2.6, 1.8, 1.6, 6.7],
        )

    # Section 8: PMI Integration Roadmap
    if report.integration_roadmap:
        _add_section_heading(doc, "8. Post-Merger Integration (PMI) Roadmap")
        pmi_rows = [
            [m.phase, m.workstream, m.objective, m.owner, m.kpi]
            for m in report.integration_roadmap
        ]
        _build_styled_table(
            doc,
            ["Phase", "Workstream", "Objective", "Owner", "Target KPI"],
            pmi_rows,
            [2.4, 2.5, 5.2, 2.6, 3.8],
        )

    # Section 9: VDR Questions & Data Gaps
    if report.due_diligence_questions:
        _add_section_heading(doc, "9. Priority VDR & Management Q&A")
        for idx, q in enumerate(report.due_diligence_questions, 1):
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(3)
            _style_run(p.add_run(f"{idx}.  "), font=SANS, size=10, bold=True, color=NAVY)
            _style_run(p.add_run(q), font=SANS, size=10, color=INK)

    if report.data_gaps:
        _add_section_heading(doc, "10. Public Data Gaps")
        for g in report.data_gaps:
            p = doc.add_paragraph(style="List Bullet")
            _style_run(p.add_run(g), font=SANS, size=9, italic=True, color=SLATE)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def report_to_docx_file(report: DueDiligenceReport, path: str) -> None:
    """Convenience: write the .docx directly to disk."""
    with open(path, "wb") as f:
        f.write(report_to_docx_bytes(report))


# =====================================================================
# EXCEL FINANCIAL MODEL EXPORT (.xlsx with LIVE FORMULAS)
# =====================================================================

def report_to_excel_bytes(
    report: DueDiligenceReport,
    dcf_result: Optional[SynergyDCFResult] = None,
) -> bytes:
    """
    Export a Wall Street-formatted multi-sheet Excel Synergy Valuation Workbook (.xlsx)
    with LIVE Excel formulas (Blue = hardcoded inputs, Black = formulas, Navy headers).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    dcf = dcf_result or compute_synergy_dcf(report)
    wb = Workbook()

    # Wall Street styles
    navy_fill = PatternFill("solid", fgColor=NAVY_HEX)
    sub_fill = PatternFill("solid", fgColor="E9E6DD")
    zebra_fill = PatternFill("solid", fgColor=LIGHT_BG_HEX)
    total_fill = PatternFill("solid", fgColor="EFECE3")

    title_font = Font(name="Georgia", size=14, bold=True, color=NAVY_HEX)
    hdr_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    sec_font = Font(name="Calibri", size=10, bold=True, color=NAVY_HEX)
    input_font = Font(name="Calibri", size=10, bold=False, color="0000FF")   # IB blue inputs
    formula_font = Font(name="Calibri", size=10, bold=False, color="141822") # IB black formulas
    bold_formula = Font(name="Calibri", size=10, bold=True, color="0B192C")
    muted_font = Font(name="Calibri", size=9, italic=True, color="64748B")

    thin_side = Side(style="thin", color=BORDER_HEX)
    double_bottom = Side(style="double", color=NAVY_HEX)
    cell_border = Border(bottom=thin_side)
    total_border = Border(top=thin_side, bottom=double_bottom)

    d = report.deal
    es = report.executive_summary

    # ------------------------------------------------------------------
    # SHEET 1: Executive Summary
    # ------------------------------------------------------------------
    ws_exec = wb.active
    ws_exec.title = "Executive Summary"
    ws_exec.views.sheetView[0].showGridLines = True

    ws_exec["A1"] = f"M&A Synergy Intelligence — {d.entity_a_name} × {d.entity_b_name}"
    ws_exec["A1"].font = title_font
    ws_exec["A2"] = f"{d.deal_type}  |  {d.industry_sector or 'Strategic M&A'}  |  Analysis Date: {d.analysis_date}"
    ws_exec["A2"].font = muted_font

    summary_rows = [
        ("Acquirer (Entity A)", d.entity_a_name),
        ("Target (Entity B)", d.entity_b_name),
        ("Transaction Value ($B)", d.deal_value_usd_b or 0.0),
        ("IC Recommendation", es.recommendation.value),
        ("Integration Complexity", es.integration_complexity.value),
        ("Conviction Score (0-100)", d.overall_conviction_score or 0),
        ("Total Synergy NPV ($B)", "='Synergy DCF Model'!B24"),
        ("Value Creation (% of Deal Value)", "=IF(B6>0, B10/B6, 0)"),
        ("Steady-State Revenue Synergies ($B/yr)", dcf.steady_state_revenue_synergy_b),
        ("Steady-State Cost Synergies ($B/yr)", dcf.steady_state_cost_synergy_b),
        ("One-Time Integration Costs ($B)", dcf.total_integration_cost_b),
        ("Source Citations / Grounding Ratio", f"{report.citation_count()} citations ({report.grounding_ratio()*100:.0f}%)"),
    ]

    ws_exec["A4"] = "Transaction Summary"
    ws_exec["B4"] = "Value / Reference"
    for col in ("A4", "B4"):
        ws_exec[col].fill = navy_fill
        ws_exec[col].font = hdr_font

    for idx, (lbl, val) in enumerate(summary_rows, start=5):
        ws_exec[f"A{idx}"] = lbl
        ws_exec[f"A{idx}"].font = sec_font
        ws_exec[f"B{idx}"] = val
        is_formula = isinstance(val, str) and val.startswith("=")
        ws_exec[f"B{idx}"].font = bold_formula if is_formula else formula_font
        if "($B" in lbl:
            ws_exec[f"B{idx}"].number_format = "$#,##0.00"
        elif "(% of" in lbl:
            ws_exec[f"B{idx}"].number_format = "0.0%"
        ws_exec[f"A{idx}"].border = cell_border
        ws_exec[f"B{idx}"].border = cell_border

    ws_exec["A19"] = "Strategic Rationale"
    ws_exec["A19"].font = sec_font
    ws_exec["B19"] = es.strategic_rationale
    ws_exec["B19"].alignment = Alignment(wrap_text=True)

    ws_exec["A20"] = "Recommendation Rationale"
    ws_exec["A20"].font = sec_font
    ws_exec["B20"] = es.recommendation_rationale
    ws_exec["B20"].alignment = Alignment(wrap_text=True)

    ws_exec.column_dimensions["A"].width = 38
    ws_exec.column_dimensions["B"].width = 62

    # ------------------------------------------------------------------
    # SHEET 2: Synergy DCF Model (LIVE FORMULAS)
    # ------------------------------------------------------------------
    ws_dcf = wb.create_sheet("Synergy DCF Model")
    ws_dcf["A1"] = f"Synergy DCF Valuation Model ($ Millions) — {d.entity_a_name} × {d.entity_b_name}"
    ws_dcf["A1"].font = title_font
    ws_dcf["A2"] = "Color Convention: Blue = Hardcoded Assumption Inputs  |  Black = Live Formulas"
    ws_dcf["A2"].font = muted_font

    # Assumptions block (rows 4-8)
    ws_dcf["A4"] = "DCF Assumptions"
    ws_dcf["B4"] = "Input"
    for c in ("A4", "B4"):
        ws_dcf[c].fill = navy_fill
        ws_dcf[c].font = hdr_font

    assump_items = [
        ("WACC (Discount Rate)", dcf.assumptions.wacc_pct / 100.0, "0.0%"),
        ("Effective Corporate Tax Rate", dcf.assumptions.tax_rate_pct / 100.0, "0.0%"),
        ("Terminal Growth Rate (g)", dcf.assumptions.terminal_growth_pct / 100.0, "0.0%"),
        ("Global Realization Factor", dcf.assumptions.realization_factor_pct / 100.0, "0.0%"),
    ]
    for offset, (lbl, val, num_fmt) in enumerate(assump_items, start=5):
        ws_dcf[f"A{offset}"] = lbl
        ws_dcf[f"A{offset}"].font = sec_font
        ws_dcf[f"B{offset}"] = val
        ws_dcf[f"B{offset}"].font = input_font
        ws_dcf[f"B{offset}"].number_format = num_fmt

    # Projection Table (Row 10 headers, Rows 11-19 line items)
    num_years = len(dcf.annual_rows)
    ws_dcf["A10"] = "Annual Cash Flow Projection ($M)"
    ws_dcf["A10"].fill = navy_fill
    ws_dcf["A10"].font = hdr_font
    for y_idx in range(1, num_years + 1):
        col_let = get_column_letter(y_idx + 1)
        ws_dcf[f"{col_let}10"] = f"Year {y_idx}"
        ws_dcf[f"{col_let}10"].fill = navy_fill
        ws_dcf[f"{col_let}10"].font = hdr_font
        ws_dcf[f"{col_let}10"].alignment = Alignment(horizontal="right")

    total_col = get_column_letter(num_years + 2)
    ws_dcf[f"{total_col}10"] = "Total Explicit"
    ws_dcf[f"{total_col}10"].fill = navy_fill
    ws_dcf[f"{total_col}10"].font = hdr_font
    ws_dcf[f"{total_col}10"].alignment = Alignment(horizontal="right")

    row_labels = [
        (11, "Revenue Synergies (Risk-Adjusted)", "input"),
        (12, "Cost & Capital Synergies (Risk-Adjusted)", "input"),
        (13, "Gross Annual Synergies", "formula"),
        (14, "Less: One-Time Integration Costs", "input"),
        (15, "Pre-Tax Synergy Cash Flow (EBT)", "formula"),
        (16, "Less: Taxes", "formula"),
        (17, "After-Tax Net Synergy Cash Flow", "bold_formula"),
        (18, "Discount Factor", "formula"),
        (19, "Present Value of Annual Cash Flow", "bold_formula"),
    ]
    for r_num, lbl, style_type in row_labels:
        ws_dcf[f"A{r_num}"] = lbl
        ws_dcf[f"A{r_num}"].font = bold_formula if style_type == "bold_formula" else sec_font

    for y_idx, r in enumerate(dcf.annual_rows, start=1):
        col = get_column_letter(y_idx + 1)
        ws_dcf[f"{col}11"] = r.revenue_synergy_m
        ws_dcf[f"{col}11"].font = input_font
        ws_dcf[f"{col}12"] = round(r.cost_synergy_m + r.capital_synergy_m, 1)
        ws_dcf[f"{col}12"].font = input_font
        ws_dcf[f"{col}13"] = f"={col}11+{col}12"
        ws_dcf[f"{col}13"].font = formula_font
        ws_dcf[f"{col}14"] = r.integration_cost_m
        ws_dcf[f"{col}14"].font = input_font
        ws_dcf[f"{col}15"] = f"={col}13-{col}14"
        ws_dcf[f"{col}15"].font = formula_font
        ws_dcf[f"{col}16"] = f"={col}15*$B$6"
        ws_dcf[f"{col}16"].font = formula_font
        ws_dcf[f"{col}17"] = f"={col}15-{col}16"
        ws_dcf[f"{col}17"].font = bold_formula
        ws_dcf[f"{col}18"] = f"=1/((1+$B$5)^{y_idx})"
        ws_dcf[f"{col}18"].font = formula_font
        ws_dcf[f"{col}19"] = f"={col}17*{col}18"
        ws_dcf[f"{col}19"].font = bold_formula

        for r_num in (11, 12, 13, 14, 15, 16, 17, 19):
            ws_dcf[f"{col}{r_num}"].number_format = "$#,##0.0;($#,##0.0);\"-\""
        ws_dcf[f"{col}18"].number_format = "0.0000"

    last_y_col = get_column_letter(num_years + 1)
    for r_num in (11, 12, 13, 14, 15, 16, 17, 19):
        ws_dcf[f"{total_col}{r_num}"] = f"=SUM(B{r_num}:{last_y_col}{r_num})"
        ws_dcf[f"{total_col}{r_num}"].font = bold_formula if r_num in (17, 19) else formula_font
        ws_dcf[f"{total_col}{r_num}"].number_format = "$#,##0.0;($#,##0.0);\"-\""

    # Valuation Summary Bridge (Rows 21-24)
    ws_dcf["A21"] = "PV of Explicit Period Cash Flows ($M)"
    ws_dcf["B21"] = f"={total_col}19"
    if dcf.assumptions.terminal_method == "perpetuity":
        ws_dcf["A22"] = "Undiscounted Terminal Value — Gordon Growth ($M)"
        ws_dcf["B22"] = f"=({last_y_col}13*(1-$B$6)*(1+$B$7))/($B$5-$B$7)"
    else:
        ws_dcf["A22"] = f"Undiscounted Terminal Value — {dcf.assumptions.terminal_multiple:.1f}x NOPAT Fade ($M)"
        ws_dcf["B22"] = f"={last_y_col}13*(1-$B$6)*{dcf.assumptions.terminal_multiple:.2f}"
    ws_dcf["A23"] = "PV of Terminal Value ($M)"
    ws_dcf["B23"] = f"=B22*{last_y_col}18"
    ws_dcf["A24"] = "TOTAL SYNERGY NPV ($ Billions)"
    ws_dcf["B24"] = "=(B21+B23)/1000"

    for r_num in (21, 22, 23, 24):
        ws_dcf[f"A{r_num}"].font = bold_formula if r_num == 24 else sec_font
        ws_dcf[f"B{r_num}"].font = bold_formula if r_num == 24 else formula_font
        ws_dcf[f"B{r_num}"].number_format = "$#,##0.00" if r_num == 24 else "$#,##0.0"
        if r_num == 24:
            ws_dcf[f"A{r_num}"].fill = total_fill
            ws_dcf[f"B{r_num}"].fill = total_fill
            ws_dcf[f"A{r_num}"].border = total_border
            ws_dcf[f"B{r_num}"].border = total_border

    # 2D WACC x Realization Sensitivity Grid (Rows 27-33)
    wacc_vals, real_vals, grid = compute_sensitivity_matrix(report, dcf.assumptions)
    ws_dcf["A27"] = "Sensitivity: Synergy NPV ($B) — WACC (Rows) vs. Realization % (Cols)"
    ws_dcf["A27"].font = sec_font
    ws_dcf["A28"] = "WACC \\ Realization"
    ws_dcf["A28"].fill = navy_fill
    ws_dcf["A28"].font = hdr_font
    for c_idx, r_val in enumerate(real_vals, start=2):
        col = get_column_letter(c_idx)
        ws_dcf[f"{col}28"] = f"{r_val:.0f}%"
        ws_dcf[f"{col}28"].fill = navy_fill
        ws_dcf[f"{col}28"].font = hdr_font
        ws_dcf[f"{col}28"].alignment = Alignment(horizontal="right")

    for r_offset, (w_val, g_row) in enumerate(zip(wacc_vals, grid), start=29):
        ws_dcf[f"A{r_offset}"] = f"{w_val:.1f}%"
        ws_dcf[f"A{r_offset}"].fill = sub_fill
        ws_dcf[f"A{r_offset}"].font = sec_font
        for c_idx, cell_val in enumerate(g_row, start=2):
            col = get_column_letter(c_idx)
            ws_dcf[f"{col}{r_offset}"] = cell_val
            ws_dcf[f"{col}{r_offset}"].number_format = "$#,##0.00"
            ws_dcf[f"{col}{r_offset}"].font = bold_formula if (r_offset == 31 and c_idx == 4) else formula_font

    ws_dcf.column_dimensions["A"].width = 44
    for i in range(2, num_years + 3):
        ws_dcf.column_dimensions[get_column_letter(i)].width = 16

    # ------------------------------------------------------------------
    # SHEET 3: Synergy Workstreams
    # ------------------------------------------------------------------
    ws_work = wb.create_sheet("Synergy Workstreams")
    ws_work["A1"] = "Bottom-Up Synergy Workstreams"
    ws_work["A1"].font = title_font

    w_headers = ["Workstream", "Type", "Gross Run-Rate ($M)", "Probability (%)",
                 "Risk-Adj Run-Rate ($M)", "1x Cost ($M)", "Y1 Ramp", "Y2 Ramp", "Y3 Ramp", "Owner"]
    for c_idx, h in enumerate(w_headers, start=1):
        cell = ws_work.cell(row=3, column=c_idx, value=h)
        cell.fill = navy_fill
        cell.font = hdr_font

    for r_idx, w in enumerate(report.synergy_workstreams, start=4):
        ws_work.cell(row=r_idx, column=1, value=w.name).font = sec_font
        ws_work.cell(row=r_idx, column=2, value=w.synergy_type.value).font = formula_font
        c3 = ws_work.cell(row=r_idx, column=3, value=w.annual_run_rate_usd_m)
        c3.font = input_font; c3.number_format = "$#,##0.0"
        c4 = ws_work.cell(row=r_idx, column=4, value=w.probability_pct / 100.0)
        c4.font = input_font; c4.number_format = "0%"
        c5 = ws_work.cell(row=r_idx, column=5, value=f"=C{r_idx}*D{r_idx}")
        c5.font = bold_formula; c5.number_format = "$#,##0.0"
        c6 = ws_work.cell(row=r_idx, column=6, value=w.one_time_cost_usd_m)
        c6.font = input_font; c6.number_format = "$#,##0.0"
        for c_i, ramp in ((7, w.ramp_year1_pct), (8, w.ramp_year2_pct), (9, w.ramp_year3_pct)):
            rc = ws_work.cell(row=r_idx, column=c_i, value=ramp / 100.0)
            rc.font = input_font; rc.number_format = "0%"
        ws_work.cell(row=r_idx, column=10, value=w.owner).font = formula_font
        if r_idx % 2 == 1:
            for ci in range(1, 11):
                ws_work.cell(row=r_idx, column=ci).fill = zebra_fill

    if report.synergy_workstreams:
        tot_r = len(report.synergy_workstreams) + 4
        ws_work.cell(row=tot_r, column=1, value="TOTAL").font = bold_formula
        for col_i in (3, 5, 6):
            let = get_column_letter(col_i)
            tc = ws_work.cell(row=tot_r, column=col_i, value=f"=SUM({let}4:{let}{tot_r-1})")
            tc.font = bold_formula; tc.number_format = "$#,##0.0"
        for ci in range(1, 11):
            ws_work.cell(row=tot_r, column=ci).fill = total_fill
            ws_work.cell(row=tot_r, column=ci).border = total_border

    for idx, width in enumerate([38, 12, 18, 14, 20, 14, 10, 10, 10, 26], start=1):
        ws_work.column_dimensions[get_column_letter(idx)].width = width

    # ------------------------------------------------------------------
    # SHEET 4: Risk Register & PMI Roadmap
    # ------------------------------------------------------------------
    ws_risk = wb.create_sheet("Risk Register & PMI")
    ws_risk["A1"] = "Investment Committee Risk Register"
    ws_risk["A1"].font = title_font

    r_headers = ["Risk Title", "Dimension", "Severity", "Likelihood (1-5)", "Impact (1-5)", "Score", "Deal-Breaker", "Mitigation"]
    for c_idx, h in enumerate(r_headers, start=1):
        cell = ws_risk.cell(row=3, column=c_idx, value=h)
        cell.fill = navy_fill
        cell.font = hdr_font

    for r_idx, r in enumerate(report.risk_register, start=4):
        ws_risk.cell(row=r_idx, column=1, value=r.title).font = sec_font
        ws_risk.cell(row=r_idx, column=2, value=r.dimension.value).font = formula_font
        ws_risk.cell(row=r_idx, column=3, value=r.severity.value).font = formula_font
        ws_risk.cell(row=r_idx, column=4, value=r.likelihood).font = input_font
        ws_risk.cell(row=r_idx, column=5, value=r.impact).font = input_font
        ws_risk.cell(row=r_idx, column=6, value=f"=D{r_idx}*E{r_idx}").font = bold_formula
        ws_risk.cell(row=r_idx, column=7, value="YES" if r.is_deal_breaker else "No").font = bold_formula if r.is_deal_breaker else formula_font
        m_cell = ws_risk.cell(row=r_idx, column=8, value=r.mitigation)
        m_cell.font = formula_font
        m_cell.alignment = Alignment(wrap_text=True)

    pmi_start = len(report.risk_register) + 7
    ws_risk.cell(row=pmi_start, column=1, value="Post-Merger Integration (PMI) Roadmap").font = title_font
    p_headers = ["Phase", "Workstream", "Objective", "Owner", "Target KPI"]
    for c_idx, h in enumerate(p_headers, start=1):
        cell = ws_risk.cell(row=pmi_start + 2, column=c_idx, value=h)
        cell.fill = navy_fill
        cell.font = hdr_font

    for offset, m in enumerate(report.integration_roadmap, start=pmi_start + 3):
        ws_risk.cell(row=offset, column=1, value=m.phase).font = sec_font
        ws_risk.cell(row=offset, column=2, value=m.workstream).font = formula_font
        c_obj = ws_risk.cell(row=offset, column=3, value=m.objective)
        c_obj.font = formula_font; c_obj.alignment = Alignment(wrap_text=True)
        ws_risk.cell(row=offset, column=4, value=m.owner).font = formula_font
        c_kpi = ws_risk.cell(row=offset, column=5, value=m.kpi)
        c_kpi.font = formula_font; c_kpi.alignment = Alignment(wrap_text=True)

    for idx, width in enumerate([32, 20, 14, 15, 13, 10, 14, 52], start=1):
        ws_risk.column_dimensions[get_column_letter(idx)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =====================================================================
# PRINT-READY HTML BOARDROOM BRIEF (.html)
# =====================================================================

def report_to_html_bytes(
    report: DueDiligenceReport,
    dcf_result: Optional[SynergyDCFResult] = None,
) -> bytes:
    """Render a standalone, print-to-PDF-ready Institutional Investment Committee HTML Brief."""
    dcf = dcf_result or compute_synergy_dcf(report)
    d = report.deal
    es = report.executive_summary
    esc = html.escape

    reco_colors = {
        "Go": ("#064E3B", "#ECFDF5"),
        "No-Go": ("#7F1D1D", "#FEF2F2"),
        "Conditional Go": ("#78350F", "#FFFBEB"),
    }
    fg, bg = reco_colors.get(es.recommendation.value, ("#0B192C", "#F6F5F0"))

    def render_claims_html(claims: Iterable[Claim]) -> str:
        out = []
        for c in claims:
            sev = f'<span class="tag">{esc(c.severity.value)}</span> ' if c.severity else ""
            imp = f' <span class="impact">({_fmt_m(c.quantified_impact_usd_m)}/yr)</span>' if c.quantified_impact_usd_m else ""
            cites = "".join(
                f'<div class="cite">↳ {esc(s.source_document)} — {esc(s.location)}</div>'
                for s in c.sources
            )
            out.append(f"<li>{sev}{esc(c.claim)}{imp}{cites}</li>")
        return f"<ul>{''.join(out)}</ul>"

    sections_html = []
    for title, attr in (
        ("2. Strategic Fit & Commercial Synergies", "strategic_fit"),
        ("3. Operational & Technology", "operational_tech"),
        ("4. Cultural & Human Capital", "cultural_risk"),
        ("5. Regulatory, ESG & Macro", "regulatory_esg"),
    ):
        sec = getattr(report, attr)
        blocks = []
        for fname in type(sec).model_fields:
            lbl = fname.replace("_", " ").title()
            blocks.append(f"<h4>{esc(lbl)}</h4>{render_claims_html(getattr(sec, fname))}")
        sections_html.append(f'<section class="card"><h2>{esc(title)}</h2>{"".join(blocks)}</section>')

    ws_rows = "".join(
        f"<tr><td><strong>{esc(w.name)}</strong></td><td>{esc(w.synergy_type.value)}</td>"
        f"<td class='num'>{_fmt_m(w.annual_run_rate_usd_m)}</td><td class='num'>{w.probability_pct:.0f}%</td>"
        f"<td class='num'>{_fmt_m(w.annual_run_rate_usd_m * w.probability_pct / 100.0)}</td>"
        f"<td class='num'>{_fmt_m(w.one_time_cost_usd_m)}</td><td>{esc(w.owner)}</td></tr>"
        for w in report.synergy_workstreams
    )
    risk_rows = "".join(
        f"<tr><td><strong>{'★ ' if r.is_deal_breaker else ''}{esc(r.title)}</strong></td>"
        f"<td>{esc(r.dimension.value)}</td><td>{esc(r.severity.value)}</td>"
        f"<td class='num'>{r.likelihood}×{r.impact}</td><td>{esc(r.mitigation)}</td></tr>"
        for r in report.risk_register
    )
    pmi_rows = "".join(
        f"<tr><td><strong>{esc(m.phase)}</strong></td><td>{esc(m.workstream)}</td>"
        f"<td>{esc(m.objective)}</td><td>{esc(m.owner)}</td><td>{esc(m.kpi)}</td></tr>"
        for m in report.integration_roadmap
    )

    doc_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>IC Memo — {esc(d.entity_a_name)} × {esc(d.entity_b_name)}</title>
<style>
  body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #F6F5F0; color: #141822; margin: 0; padding: 36px; line-height: 1.55; }}
  .wrap {{ max-width: 1020px; margin: 0 auto; }}
  .masthead {{ background: #0B192C; color: #FFFFFF; padding: 32px 36px; border-bottom: 4px solid #B8935A; border-radius: 6px; }}
  .eyebrow {{ color: #B8935A; font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; font-weight: 700; }}
  h1 {{ font-family: Georgia, serif; margin: 8px 0 6px; font-size: 30px; font-weight: 700; }}
  h2 {{ font-family: Georgia, serif; color: #0B192C; font-size: 19px; border-bottom: 1.5px solid #B8935A; padding-bottom: 6px; margin-top: 0; }}
  h4 {{ color: #1A3A5C; margin: 14px 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; }}
  .meta {{ color: #CBD5E1; font-size: 13px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 20px 0; }}
  .kpi {{ background: #FFFFFF; border: 1px solid #E2DFD7; border-top: 3px solid #0B192C; border-radius: 5px; padding: 14px 16px; }}
  .kpi-lbl {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #64748B; font-weight: 600; }}
  .kpi-val {{ font-family: Georgia, serif; font-size: 24px; font-weight: 700; color: #0B192C; margin-top: 4px; }}
  .card {{ background: #FFFFFF; border: 1px solid #E2DFD7; border-radius: 6px; padding: 24px 28px; margin-bottom: 18px; page-break-inside: avoid; }}
  .reco {{ display: inline-block; padding: 6px 14px; border-radius: 4px; font-weight: 700; font-size: 12px; letter-spacing: 0.08em; color: {fg}; background: {bg}; border: 1px solid {fg}33; }}
  .tag {{ display: inline-block; font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 1px 6px; border-radius: 3px; background: #EFECE3; color: #0B192C; margin-right: 4px; }}
  .impact {{ font-weight: 600; color: #1A3A5C; font-size: 12px; }}
  .cite {{ font-size: 11.5px; color: #64748B; font-style: italic; margin-top: 2px; }}
  ul {{ padding-left: 18px; margin: 6px 0 10px; }}
  li {{ margin-bottom: 8px; font-size: 13.5px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 10px; }}
  th {{ background: #0B192C; color: #FFFFFF; text-align: left; padding: 9px 10px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #E2DFD7; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #FAF9F6; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  @media print {{ body {{ background: #FFFFFF; padding: 0; }} .card {{ box-shadow: none; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead">
    <div class="eyebrow">Investment Committee · Pre-Merger Intelligence Memo · Strictly Confidential</div>
    <h1>{esc(d.entity_a_name)} × {esc(d.entity_b_name)}</h1>
    <div class="meta">{esc(d.deal_type)} · {esc(d.industry_sector or 'Strategic M&A')} · {esc(d.analysis_date)} · {d.time_horizon_months or 36}-Month Horizon</div>
  </div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-lbl">Deal Value (EV)</div><div class="kpi-val">{_fmt_b(d.deal_value_usd_b)}</div></div>
    <div class="kpi"><div class="kpi-lbl">Synergy NPV (DCF)</div><div class="kpi-val">{_fmt_b(dcf.total_synergy_npv_b)}</div></div>
    <div class="kpi"><div class="kpi-lbl">Run-Rate Synergies</div><div class="kpi-val">{_fmt_b(dcf.steady_state_total_synergy_b)}</div></div>
    <div class="kpi"><div class="kpi-lbl">Conviction Index</div><div class="kpi-val">{f'{d.overall_conviction_score}/100' if d.overall_conviction_score is not None else '—'}</div></div>
  </div>

  <section class="card">
    <h2>1. Executive Summary</h2>
    <p><span class="reco">{esc(es.recommendation.value.upper())}</span> &nbsp; <strong>Integration Complexity:</strong> {esc(es.integration_complexity.value)}</p>
    <p><strong>Strategic Rationale.</strong> {esc(es.strategic_rationale)}</p>
    <p><strong>Recommendation Stance.</strong> {esc(es.recommendation_rationale)}</p>
    {f'<p><strong>Valuation Commentary.</strong> {esc(report.valuation_commentary)}</p>' if report.valuation_commentary else ''}
    <h4>Top Deal-Breaker Risks</h4>
    {render_claims_html(es.top_deal_breaker_risks)}
  </section>

  {''.join(sections_html)}

  {f'<section class="card"><h2>6. Bottom-Up Synergy Workstreams</h2><table><thead><tr><th>Workstream</th><th>Type</th><th class="num">Gross</th><th class="num">Prob.</th><th class="num">Risk-Adj</th><th class="num">1x Cost</th><th>Owner</th></tr></thead><tbody>{ws_rows}</tbody></table></section>' if ws_rows else ''}

  {f'<section class="card"><h2>7. Risk Register Matrix</h2><table><thead><tr><th>Risk (★ Deal-Breaker)</th><th>Dimension</th><th>Severity</th><th class="num">L×I</th><th>Mitigation</th></tr></thead><tbody>{risk_rows}</tbody></table></section>' if risk_rows else ''}

  {f'<section class="card"><h2>8. Post-Merger Integration (PMI) Roadmap</h2><table><thead><tr><th>Phase</th><th>Workstream</th><th>Objective</th><th>Owner</th><th>Target KPI</th></tr></thead><tbody>{pmi_rows}</tbody></table></section>' if pmi_rows else ''}
</div>
</body>
</html>"""
    return doc_html.encode("utf-8")
