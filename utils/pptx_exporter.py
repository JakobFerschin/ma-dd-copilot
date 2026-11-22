"""
Institutional 12-Slide Investment Committee PowerPoint Pitchbook Exporter (.pptx).

Generates a native, fully editable 16:9 widescreen boardroom deck using `python-pptx`
in an Obsidian Navy + Antique Gold + Alabaster palette with Georgia/Calibri typography,
native editable PowerPoint charts, Wall Street color-shaded 2D sensitivity matrix,
bottom-up synergy schedules, risk register, PMI roadmap, and VDR Q&A.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from core.financial_model import (
    SynergyDCFResult,
    _synthesize_fallback_workstreams,
    compute_sensitivity_matrix,
    compute_synergy_dcf,
)
from schemas.report_schema import Claim, DueDiligenceReport, RiskItem

# ---- Default Institutional Palette ----
DEFAULT_PRIMARY_HEX = "0B192C"  # Obsidian Navy
DEFAULT_ACCENT_HEX = "B8935A"   # Antique Gold
NAVY_MID_HEX = "1A3A5C"
NAVY_CARD_HEX = "132743"
ALABASTER_HEX = "F6F5F0"
CARD_WHITE_HEX = "FFFFFF"
BORDER_HEX = "DCD8CE"
INK_HEX = "141822"
SLATE_HEX = "475569"
MUTED_HEX = "788496"
LIGHT_SLATE_HEX = "CBD5E1"

FOREST_HEX = "14532D"
FOREST_BG_HEX = "ECFDF5"
CRIMSON_HEX = "7F1D1D"
CRIMSON_BG_HEX = "FEF2F2"
AMBER_HEX = "78350F"
AMBER_BG_HEX = "FFFBEB"
GOLD_SOFT_HEX = "FDF4DC"

SERIF_FONT = "Georgia"
SANS_FONT = "Calibri"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
TOTAL_SLIDES = 12


@dataclass
class BrandingConfig:
    """White-label branding & confidentiality controls for institutional deliverables."""
    firm_name: str = "M&A Due Diligence Copilot"
    primary_hex: str = DEFAULT_PRIMARY_HEX
    accent_hex: str = DEFAULT_ACCENT_HEX
    disclaimer: str = (
        "STRICTLY PRIVATE & CONFIDENTIAL — Prepared exclusively for Investment Committee review. "
        "All analytical assertions are grounded in cited source filings or explicitly flagged as public data gaps."
    )
    watermark_text: Optional[str] = None


# =====================================================================
# Color, Formatting & XML Styling Helpers
# =====================================================================

def _clean_hex(hex_str: str, fallback: str = DEFAULT_PRIMARY_HEX) -> str:
    cleaned = (hex_str or "").strip().lstrip("#").upper()
    if re.fullmatch(r"[0-9A-F]{6}", cleaned):
        return cleaned
    return fallback


def _rgb(hex_str: str, fallback: str = DEFAULT_PRIMARY_HEX) -> RGBColor:
    c = _clean_hex(hex_str, fallback)
    return RGBColor(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _fmt_b(val: Optional[float], signed: bool = False) -> str:
    if val is None:
        return "—"
    if signed and val < 0:
        return f"-${abs(val):.2f}B"
    return f"${val:.2f}B"


def _fmt_m(val: Optional[float], signed: bool = False) -> str:
    if val is None:
        return "—"
    if signed and val < 0:
        return f"(${abs(val):,.0f}M)"
    return f"${val:,.0f}M"


def _clip(text: str, max_chars: int) -> str:
    txt = " ".join((text or "").split())
    if len(txt) <= max_chars:
        return txt
    cut = txt[: max_chars - 1].rsplit(" ", 1)[0]
    return (cut if len(cut) > max_chars // 2 else txt[: max_chars - 1]).rstrip(".,;:") + "…"


def _set_slide_bg(slide, hex_color: str) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(hex_color)


def _add_rect(
    slide,
    left,
    top,
    width,
    height,
    *,
    fill_hex: str,
    border_hex: Optional[str] = None,
    border_pt: float = 0.75,
):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill_hex)
    if border_hex:
        shape.line.color.rgb = _rgb(border_hex)
        shape.line.width = Pt(border_pt)
    else:
        shape.line.fill.background()
    return shape


def _add_textbox(
    slide,
    left,
    top,
    width,
    height,
    text: str = "",
    *,
    font_name: str = SANS_FONT,
    font_size: float = 10,
    bold: bool = False,
    italic: bool = False,
    color_hex: str = INK_HEX,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    valign: MSO_ANCHOR = MSO_ANCHOR.TOP,
    margin_lr: float = 0.04,
    margin_tb: float = 0.02,
):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = valign
    tf.margin_left = Inches(margin_lr)
    tf.margin_right = Inches(margin_lr)
    tf.margin_top = Inches(margin_tb)
    tf.margin_bottom = Inches(margin_tb)
    if text:
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.name = font_name
        run.font.size = Pt(font_size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = _rgb(color_hex)
    return tb


def _add_run(
    para,
    text: str,
    *,
    font_name: str = SANS_FONT,
    font_size: float = 9.5,
    bold: bool = False,
    italic: bool = False,
    color_hex: str = INK_HEX,
):
    run = para.add_run()
    run.text = text
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = _rgb(color_hex)
    return run


def _set_cell_border(cell, *, color_hex: str = BORDER_HEX, width_emu: int = 6350, sides=("lnB",)) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    for side in ("lnL", "lnR", "lnT", "lnB"):
        existing = tc_pr.find(qn(f"a:{side}"))
        if existing is not None:
            tc_pr.remove(existing)
        ln = OxmlElement(f"a:{side}")
        if side in sides:
            ln.set("w", str(width_emu))
            ln.set("cap", "flat")
            ln.set("cmpd", "sng")
            ln.set("algn", "ctr")
            solid = OxmlElement("a:solidFill")
            srgb = OxmlElement("a:srgbClr")
            srgb.set("val", _clean_hex(color_hex))
            solid.append(srgb)
            ln.append(solid)
        else:
            no_fill = OxmlElement("a:noFill")
            ln.append(no_fill)
        tc_pr.append(ln)


def _style_cell(
    cell,
    text: str,
    *,
    fill_hex: str = CARD_WHITE_HEX,
    font_name: str = SANS_FONT,
    font_size: float = 8.5,
    bold: bool = False,
    italic: bool = False,
    color_hex: str = INK_HEX,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    border_hex: str = BORDER_HEX,
    border_sides=("lnB",),
    border_width_emu: int = 6350,
) -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = _rgb(fill_hex)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = Inches(0.08)
    cell.margin_right = Inches(0.08)
    cell.margin_top = Inches(0.03)
    cell.margin_bottom = Inches(0.03)
    _set_cell_border(cell, color_hex=border_hex, width_emu=border_width_emu, sides=border_sides)

    tf = cell.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = ""
    p.alignment = align
    run = p.add_run()
    run.text = str(text)
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = _rgb(color_hex)


def _add_slide_chrome(
    slide,
    *,
    eyebrow: str,
    title: str,
    slide_num: int,
    report: DueDiligenceReport,
    branding: BrandingConfig,
) -> None:
    """Apply consistent Alabaster background, Navy/Gold header, footer, slide number, and optional watermark."""
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    d = report.deal
    deal_short = _clip(f"{d.entity_a_name} × {d.entity_b_name}", 48)

    _set_slide_bg(slide, ALABASTER_HEX)

    # Top slim accent bar
    _add_rect(slide, Inches(0), Inches(0), SLIDE_W, Inches(0.08), fill_hex=primary)

    # Eyebrow & Title
    _add_textbox(
        slide, Inches(0.55), Inches(0.18), Inches(8.8), Inches(0.24),
        eyebrow.upper(), font_name=SANS_FONT, font_size=8.5, bold=True, color_hex=accent,
    )
    _add_textbox(
        slide, Inches(0.55), Inches(0.40), Inches(9.5), Inches(0.46),
        title, font_name=SERIF_FONT, font_size=20, bold=True, color_hex=primary,
    )

    # Top-right deal badge
    _add_textbox(
        slide, Inches(9.55), Inches(0.22), Inches(3.23), Inches(0.26),
        deal_short, font_name=SANS_FONT, font_size=8.5, bold=True, color_hex=SLATE_HEX, align=PP_ALIGN.RIGHT,
    )
    if branding.watermark_text:
        _add_textbox(
            slide, Inches(9.55), Inches(0.48), Inches(3.23), Inches(0.24),
            f"WATERMARK: {_clip(branding.watermark_text, 28)}",
            font_name=SANS_FONT, font_size=7.5, italic=True, color_hex=CRIMSON_HEX, align=PP_ALIGN.RIGHT,
        )

    # Gold rule under title
    _add_rect(slide, Inches(0.55), Inches(0.92), Inches(12.23), Inches(0.025), fill_hex=accent)

    # Footer divider + text + slide number
    _add_rect(slide, Inches(0.55), Inches(6.98), Inches(12.23), Inches(0.015), fill_hex=BORDER_HEX)
    wm_suffix = f"  |  RESTRICTED: {branding.watermark_text}" if branding.watermark_text else ""
    footer_text = f"{branding.firm_name}  ·  {deal_short}  ·  CONFIDENTIAL  ·  {d.analysis_date}{wm_suffix}"
    _add_textbox(
        slide, Inches(0.55), Inches(7.04), Inches(10.6), Inches(0.28),
        _clip(footer_text, 130), font_name=SANS_FONT, font_size=7.5, color_hex=MUTED_HEX,
    )
    _add_textbox(
        slide, Inches(11.45), Inches(7.04), Inches(1.33), Inches(0.28),
        f"Slide {slide_num} / {TOTAL_SLIDES}",
        font_name=SANS_FONT, font_size=8, bold=True, color_hex=primary, align=PP_ALIGN.RIGHT,
    )


def _add_kpi_card(
    slide,
    left,
    top,
    width,
    height,
    label: str,
    value: str,
    subtext: str = "",
    *,
    top_bar_hex: str = DEFAULT_PRIMARY_HEX,
    bg_hex: str = CARD_WHITE_HEX,
    value_hex: str = DEFAULT_PRIMARY_HEX,
    value_size: float = 20,
) -> None:
    _add_rect(slide, left, top, width, height, fill_hex=bg_hex, border_hex=BORDER_HEX, border_pt=0.75)
    _add_rect(slide, left, top, width, Inches(0.06), fill_hex=top_bar_hex)
    _add_textbox(
        slide, left + Inches(0.12), top + Inches(0.11), width - Inches(0.24), Inches(0.24),
        label.upper(), font_name=SANS_FONT, font_size=7.5, bold=True, color_hex=SLATE_HEX,
    )
    _add_textbox(
        slide, left + Inches(0.12), top + Inches(0.34), width - Inches(0.24), Inches(0.42),
        value, font_name=SERIF_FONT, font_size=value_size, bold=True, color_hex=value_hex,
    )
    if subtext:
        _add_textbox(
            slide, left + Inches(0.12), top + height - Inches(0.28), width - Inches(0.24), Inches(0.24),
            _clip(subtext, 44), font_name=SANS_FONT, font_size=7.5, color_hex=MUTED_HEX,
        )


def _render_claims_into_box(
    slide,
    left,
    top,
    width,
    height,
    claims: Iterable[Claim],
    *,
    max_items: int = 3,
    max_chars_per_claim: int = 165,
    font_size: float = 8.8,
    accent_hex: str = DEFAULT_ACCENT_HEX,
) -> None:
    tb = _add_textbox(slide, left, top, width, height, "", margin_lr=0.08, margin_tb=0.06)
    tf = tb.text_frame
    claim_list = list(claims)[:max_items]
    if not claim_list:
        p = tf.paragraphs[0]
        _add_run(
            p,
            "Insufficient public data for definitive assessment — flagged for Phase-2 VDR review.",
            font_size=font_size, italic=True, color_hex=MUTED_HEX,
        )
        return

    for idx, c in enumerate(claim_list):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.space_before = Pt(2 if idx == 0 else 6)
        p.space_after = Pt(1)
        _add_run(p, "▪ ", font_size=font_size, bold=True, color_hex=accent_hex)
        if c.severity:
            sev_color = CRIMSON_HEX if c.severity.value == "Critical" else (
                AMBER_HEX if c.severity.value == "High" else NAVY_MID_HEX
            )
            _add_run(p, f"[{c.severity.value.upper()}] ", font_size=font_size - 0.8, bold=True, color_hex=sev_color)
        _add_run(p, _clip(c.claim, max_chars_per_claim), font_size=font_size, color_hex=INK_HEX)
        if c.quantified_impact_usd_m is not None:
            _add_run(
                p,
                f" ({_fmt_m(c.quantified_impact_usd_m)}/yr)",
                font_size=font_size - 0.5, bold=True, color_hex=NAVY_MID_HEX,
            )
        if c.sources:
            cite_p = tf.add_paragraph()
            cite_p.space_before = Pt(0)
            cite_p.space_after = Pt(2)
            src_str = "; ".join(f"{s.source_document} ({s.location})" for s in c.sources[:2])
            _add_run(
                cite_p,
                f"   ↳ {_clip(src_str, 88)}",
                font_size=7.4, italic=True, color_hex=MUTED_HEX,
            )


def _pillar_score_lookup(report: DueDiligenceReport, keyword: str) -> Optional[int]:
    kw = keyword.lower()
    for ps in report.pillar_scores:
        if kw in ps.pillar.lower():
            return ps.score
    return None


# =====================================================================
# Individual Slide Builders (Slides 1 to 12)
# =====================================================================

def _build_slide_01_cover(prs: Presentation, report: DueDiligenceReport, dcf: SynergyDCFResult, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    d = report.deal
    es = report.executive_summary

    _set_slide_bg(slide, primary)
    # Left gold vertical architectural bar
    _add_rect(slide, Inches(0), Inches(0), Inches(0.28), SLIDE_H, fill_hex=accent)
    # Subtle top-right geometric accent strip
    _add_rect(slide, Inches(0.75), Inches(0.50), Inches(11.95), Inches(0.42), fill_hex=NAVY_CARD_HEX, border_hex=accent, border_pt=0.75)
    banner_txt = "STRICTLY PRIVATE & CONFIDENTIAL  ·  INVESTMENT COMMITTEE PITCHBOOK"
    if branding.watermark_text:
        banner_txt += f"  ·  {branding.watermark_text.upper()}"
    _add_textbox(
        slide, Inches(0.92), Inches(0.58), Inches(11.6), Inches(0.28),
        _clip(banner_txt, 110), font_name=SANS_FONT, font_size=9.5, bold=True, color_hex=accent,
    )

    # Firm Name eyebrow
    _add_textbox(
        slide, Inches(0.75), Inches(1.42), Inches(11.5), Inches(0.36),
        branding.firm_name.upper(), font_name=SANS_FONT, font_size=12, bold=True, color_hex=accent,
    )

    # Main Deal Title
    title_str = f"{d.entity_a_name}  ×  {d.entity_b_name}"
    title_size = 34 if len(title_str) <= 44 else 28
    _add_textbox(
        slide, Inches(0.75), Inches(1.85), Inches(11.8), Inches(1.35),
        title_str, font_name=SERIF_FONT, font_size=title_size, bold=True, color_hex=CARD_WHITE_HEX,
    )

    # Subtitle (Sector & Deal Type)
    subtitle = f"{d.deal_type}  |  {d.industry_sector or 'Strategic M&A Due Diligence & Synergy Valuation'}"
    _add_textbox(
        slide, Inches(0.75), Inches(3.25), Inches(11.6), Inches(0.60),
        _clip(subtitle, 120), font_name=SERIF_FONT, font_size=14.5, italic=True, color_hex=LIGHT_SLATE_HEX,
    )

    # Gold separator bar
    _add_rect(slide, Inches(0.75), Inches(4.02), Inches(3.8), Inches(0.05), fill_hex=accent)

    # 4 Cover KPI Cards
    cards = [
        ("TRANSACTION VALUE (EV)", _fmt_b(d.deal_value_usd_b), d.deal_type[:32]),
        ("SYNERGY NPV (DCF)", _fmt_b(dcf.total_synergy_npv_b), f"WACC {dcf.assumptions.wacc_pct:.1f}% · After-Tax"),
        ("IC RECOMMENDATION", es.recommendation.value.upper(), f"Complexity: {es.integration_complexity.value}"),
        ("ANALYSIS DATE & GROUNDING", d.analysis_date, f"{report.citation_count()} Citations · {len(report.unique_sources())} Filings"),
    ]
    card_w = Inches(2.82)
    gap = Inches(0.22)
    start_x = Inches(0.75)
    for idx, (lbl, val, sub) in enumerate(cards):
        cx = start_x + idx * (card_w + gap)
        _add_rect(slide, cx, Inches(4.45), card_w, Inches(1.42), fill_hex=NAVY_CARD_HEX, border_hex=NAVY_MID_HEX, border_pt=1.0)
        _add_rect(slide, cx, Inches(4.45), card_w, Inches(0.05), fill_hex=accent)
        _add_textbox(
            slide, cx + Inches(0.14), Inches(4.58), card_w - Inches(0.28), Inches(0.24),
            lbl, font_name=SANS_FONT, font_size=7.5, bold=True, color_hex=accent,
        )
        val_sz = 20 if len(val) <= 10 else 15.5
        _add_textbox(
            slide, cx + Inches(0.14), Inches(4.86), card_w - Inches(0.28), Inches(0.46),
            val, font_name=SERIF_FONT, font_size=val_sz, bold=True, color_hex=CARD_WHITE_HEX,
        )
        _add_textbox(
            slide, cx + Inches(0.14), Inches(5.45), card_w - Inches(0.28), Inches(0.28),
            _clip(sub, 38), font_name=SANS_FONT, font_size=8, color_hex=LIGHT_SLATE_HEX,
        )

    # Disclaimer footer
    _add_rect(slide, Inches(0.75), Inches(6.28), Inches(11.95), Inches(0.015), fill_hex=NAVY_MID_HEX)
    _add_textbox(
        slide, Inches(0.75), Inches(6.40), Inches(11.95), Inches(0.75),
        _clip(branding.disclaimer, 260), font_name=SANS_FONT, font_size=8, italic=True, color_hex="94A3B8",
    )


def _build_slide_02_exec_summary(prs: Presentation, report: DueDiligenceReport, dcf: SynergyDCFResult, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    d = report.deal
    es = report.executive_summary

    _add_slide_chrome(
        slide, eyebrow="Section 01 · Executive Summary",
        title="Executive Summary & Investment Committee Recommendation",
        slide_num=2, report=report, branding=branding,
    )

    reco = es.recommendation.value
    reco_styles = {
        "Go": (FOREST_BG_HEX, FOREST_HEX, FOREST_HEX),
        "No-Go": (CRIMSON_BG_HEX, CRIMSON_HEX, CRIMSON_HEX),
        "Conditional Go": (AMBER_BG_HEX, AMBER_HEX, AMBER_HEX),
    }
    r_bg, r_fg, r_bar = reco_styles.get(reco, (CARD_WHITE_HEX, primary, accent))

    # Top 3 Status Cards
    _add_kpi_card(
        slide, Inches(0.55), Inches(1.08), Inches(4.15), Inches(1.04),
        "IC Recommendation", reco.upper(),
        f"Deal EV {_fmt_b(d.deal_value_usd_b)} · Synergy NPV {_fmt_b(dcf.total_synergy_npv_b)}",
        top_bar_hex=r_bar, bg_hex=r_bg, value_hex=r_fg, value_size=19,
    )
    conv_str = f"{d.overall_conviction_score} / 100" if d.overall_conviction_score is not None else "N/A"
    _add_kpi_card(
        slide, Inches(4.90), Inches(1.08), Inches(3.84), Inches(1.04),
        "IC Conviction Index", conv_str,
        f"Evidence Grounding: {report.grounding_ratio() * 100:.0f}% ({report.citation_count()} Citations)",
        top_bar_hex=accent, bg_hex=CARD_WHITE_HEX, value_hex=primary, value_size=19,
    )
    comp_hex = CRIMSON_HEX if es.integration_complexity.value in ("High", "Critical") else primary
    _add_kpi_card(
        slide, Inches(8.94), Inches(1.08), Inches(3.84), Inches(1.04),
        "Integration Complexity", es.integration_complexity.value.upper(),
        f"Capture Horizon: {d.time_horizon_months or 36} Months",
        top_bar_hex=comp_hex, bg_hex=CARD_WHITE_HEX, value_hex=comp_hex, value_size=19,
    )

    # Left Narrative Card (Strategic Rationale, Recommendation Stance, Complexity)
    _add_rect(slide, Inches(0.55), Inches(2.28), Inches(6.35), Inches(4.55), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(0.55), Inches(2.28), Inches(6.35), Inches(0.05), fill_hex=primary)

    tb = _add_textbox(slide, Inches(0.68), Inches(2.40), Inches(6.10), Inches(4.35), "", margin_lr=0.05, margin_tb=0.04)
    tf = tb.text_frame
    blocks = [
        ("STRATEGIC RATIONALE", _clip(es.strategic_rationale, 430), False, INK_HEX),
        ("RECOMMENDATION STANCE & CONDITIONS", _clip(es.recommendation_rationale, 360), False, INK_HEX),
        ("INTEGRATION COMPLEXITY ASSESSMENT", _clip(es.complexity_justification, 250), True, SLATE_HEX),
    ]
    for idx, (hdr, body, is_it, col) in enumerate(blocks):
        p_hdr = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p_hdr.space_before = Pt(0 if idx == 0 else 9)
        p_hdr.space_after = Pt(2)
        _add_run(p_hdr, hdr, font_size=8.5, bold=True, color_hex=primary)
        p_b = tf.add_paragraph()
        p_b.space_after = Pt(2)
        _add_run(p_b, body, font_size=9.0, italic=is_it, color_hex=col)

    # Right Deal-Breakers Card
    _add_rect(slide, Inches(7.10), Inches(2.28), Inches(5.68), Inches(4.55), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(7.10), Inches(2.28), Inches(5.68), Inches(0.05), fill_hex=CRIMSON_HEX)
    _add_textbox(
        slide, Inches(7.25), Inches(2.40), Inches(5.38), Inches(0.28),
        "TOP DEAL-BREAKER RISKS & RED FLAGS",
        font_name=SANS_FONT, font_size=9, bold=True, color_hex=CRIMSON_HEX,
    )
    _render_claims_into_box(
        slide, Inches(7.20), Inches(2.70), Inches(5.48), Inches(4.05),
        es.top_deal_breaker_risks, max_items=4, max_chars_per_claim=160, font_size=8.6, accent_hex=CRIMSON_HEX,
    )


def _build_slide_03_financial_bridge(prs: Presentation, report: DueDiligenceReport, dcf: SynergyDCFResult, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    d = report.deal

    _add_slide_chrome(
        slide, eyebrow="Section 02 · Financial Valuation Dashboard",
        title="Headline Financial Dashboard & Synergy Value Bridge",
        slide_num=3, report=report, branding=branding,
    )

    # 5 KPI Cards across top
    vc_str = f"{dcf.value_creation_pct_of_deal:.1f}%" if dcf.value_creation_pct_of_deal is not None else "—"
    pb_str = f"{dcf.payback_years:.1f} yrs" if dcf.payback_years is not None else "—"
    irr_str = f"{dcf.synergy_irr_pct:.1f}% IRR" if dcf.synergy_irr_pct is not None else "IRR N/A"

    kpis = [
        ("Deal Value (EV)", _fmt_b(d.deal_value_usd_b), d.industry_sector[:28] if d.industry_sector else "Enterprise Value", primary),
        ("Total Synergy NPV", _fmt_b(dcf.total_synergy_npv_b), f"PV Expl. {_fmt_b(dcf.pv_explicit_period_b)} + TV", accent),
        ("Value Creation % EV", vc_str, "After-Tax Synergy Uplift", FOREST_HEX if (dcf.value_creation_pct_of_deal or 0) >= 15 else primary),
        ("Run-Rate Synergies", _fmt_b(dcf.steady_state_total_synergy_b), f"Rev {_fmt_b(dcf.steady_state_revenue_synergy_b)} | Cost {_fmt_b(dcf.steady_state_cost_synergy_b)}", NAVY_MID_HEX),
        ("Payback & Return", pb_str, irr_str, primary),
    ]
    kw = Inches(2.31)
    gap = Inches(0.17)
    for idx, (lbl, val, sub, bar_c) in enumerate(kpis):
        _add_kpi_card(
            slide, Inches(0.55) + idx * (kw + gap), Inches(1.08), kw, Inches(1.08),
            lbl, val, sub, top_bar_hex=bar_c, value_size=18.5,
        )

    # Left: Native Editable PowerPoint Column Chart — Synergy Value Bridge ($B PV)
    _add_rect(slide, Inches(0.55), Inches(2.34), Inches(7.70), Inches(4.50), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_textbox(
        slide, Inches(0.72), Inches(2.44), Inches(7.35), Inches(0.30),
        "SYNERGY VALUE CREATION BRIDGE — PRESENT VALUE AFTER-TAX ($ BILLIONS)",
        font_name=SANS_FONT, font_size=9, bold=True, color_hex=primary,
    )

    pv_cost_cap = round(dcf.pv_cost_synergies_b + dcf.pv_capital_synergies_b, 2)
    pv_int_neg = round(-abs(dcf.pv_integration_costs_b), 2)

    chart_data = CategoryChartData()
    chart_data.categories = [
        "PV Revenue\nSynergies",
        "PV Cost & Cap\nSynergies",
        "PV Integration\nCosts",
        "Net Synergy\nNPV",
    ]
    chart_data.add_series(
        "Present Value ($B)",
        (dcf.pv_revenue_synergies_b, pv_cost_cap, pv_int_neg, dcf.total_synergy_npv_b),
    )

    chart_shape = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(0.70), Inches(2.78), Inches(7.40), Inches(3.92),
        chart_data,
    )
    chart = chart_shape.chart
    chart.has_legend = False
    chart.has_title = False

    plot = chart.plots[0]
    plot.gap_width = 75
    plot.has_data_labels = True
    data_labels = plot.data_labels
    data_labels.number_format = '$#,##0.00"B";($#,##0.00"B")'
    data_labels.font.name = SANS_FONT
    data_labels.font.size = Pt(9.5)
    data_labels.font.bold = True
    data_labels.font.color.rgb = _rgb(primary)
    data_labels.position = XL_LABEL_POSITION.OUTSIDE_END

    series = plot.series[0]
    bar_colors = [primary, NAVY_MID_HEX, CRIMSON_HEX, accent]
    for p_idx, hex_c in enumerate(bar_colors):
        pt = series.points[p_idx]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = _rgb(hex_c)
        pt.format.line.fill.background()

    val_axis = chart.value_axis
    val_axis.has_major_gridlines = True
    val_axis.major_gridlines.format.line.color.rgb = _rgb("E5E2DA")
    val_axis.tick_labels.font.name = SANS_FONT
    val_axis.tick_labels.font.size = Pt(8.5)
    val_axis.tick_labels.font.color.rgb = _rgb(SLATE_HEX)
    val_axis.tick_labels.number_format = '$#,##0.0"B"'

    cat_axis = chart.category_axis
    cat_axis.tick_labels.font.name = SANS_FONT
    cat_axis.tick_labels.font.size = Pt(8.5)
    cat_axis.tick_labels.font.bold = True
    cat_axis.tick_labels.font.color.rgb = _rgb(INK_HEX)

    # Right: Valuation Commentary & Efficiency Metrics Card
    _add_rect(slide, Inches(8.45), Inches(2.34), Inches(4.33), Inches(4.50), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(8.45), Inches(2.34), Inches(4.33), Inches(0.05), fill_hex=accent)
    _add_textbox(
        slide, Inches(8.62), Inches(2.46), Inches(4.00), Inches(0.28),
        "VALUATION & CAPITAL EFFICIENCY",
        font_name=SANS_FONT, font_size=9, bold=True, color_hex=primary,
    )

    ratio_str = f"{dcf.npv_to_integration_cost_ratio:.2f}x" if dcf.npv_to_integration_cost_ratio is not None else "—"
    term_lbl = (
        f"{dcf.assumptions.terminal_multiple:.1f}x NOPAT Fade"
        if dcf.assumptions.terminal_method != "perpetuity"
        else f"Gordon Growth ({dcf.assumptions.terminal_growth_pct:.1f}%)"
    )
    metrics_rows = [
        ("PV Explicit Period", _fmt_b(dcf.pv_explicit_period_b)),
        ("PV Terminal Value", _fmt_b(dcf.pv_terminal_value_b)),
        ("Total 1x Integration Cost", _fmt_b(dcf.total_integration_cost_b)),
        ("NPV / Integration Cost", ratio_str),
        ("WACC / Tax Rate", f"{dcf.assumptions.wacc_pct:.1f}% / {dcf.assumptions.tax_rate_pct:.0f}%"),
        ("Terminal Methodology", term_lbl),
    ]
    tbl_shape = slide.shapes.add_table(len(metrics_rows), 2, Inches(8.60), Inches(2.80), Inches(4.03), Inches(1.88))
    tbl = tbl_shape.table
    tbl.columns[0].width = Inches(2.25)
    tbl.columns[1].width = Inches(1.78)
    for r_idx, (k, v) in enumerate(metrics_rows):
        bg = ALABASTER_HEX if r_idx % 2 == 0 else CARD_WHITE_HEX
        _style_cell(tbl.cell(r_idx, 0), k, fill_hex=bg, font_size=8.2, bold=True, color_hex=SLATE_HEX)
        _style_cell(tbl.cell(r_idx, 1), v, fill_hex=bg, font_size=8.5, bold=True, color_hex=primary, align=PP_ALIGN.RIGHT)

    _add_textbox(
        slide, Inches(8.62), Inches(4.80), Inches(4.00), Inches(0.24),
        "INVESTMENT COMMITTEE COMMENTARY",
        font_name=SANS_FONT, font_size=8.5, bold=True, color_hex=accent,
    )
    commentary = report.valuation_commentary or (
        f"Bottom-up deterministic DCF generates {_fmt_b(dcf.total_synergy_npv_b)} in after-tax Synergy NPV "
        f"at {dcf.assumptions.wacc_pct:.1f}% WACC against {_fmt_b(dcf.total_integration_cost_b)} in one-time integration spend."
    )
    _add_textbox(
        slide, Inches(8.62), Inches(5.06), Inches(4.00), Inches(1.68),
        _clip(commentary, 360), font_name=SANS_FONT, font_size=8.5, color_hex=INK_HEX,
    )


def _build_slide_04_dcf_schedule(prs: Presentation, report: DueDiligenceReport, dcf: SynergyDCFResult, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)

    _add_slide_chrome(
        slide, eyebrow="Section 03 · Deterministic Synergy DCF",
        title=f"{len(dcf.annual_rows)}-Year Synergy Cash Flow Ramp-Up & DCF Schedule",
        slide_num=4, report=report, branding=branding,
    )

    rows_data = dcf.annual_rows
    n_years = len(rows_data)
    n_cols = n_years + 2  # Label + Years + Total

    line_specs = [
        ("Revenue Synergies (Risk-Adjusted)", lambda r: _fmt_m(r.revenue_synergy_m), sum(r.revenue_synergy_m for r in rows_data), False),
        ("Cost & Capital Synergies", lambda r: _fmt_m(r.cost_synergy_m + r.capital_synergy_m), sum(r.cost_synergy_m + r.capital_synergy_m for r in rows_data), False),
        ("Gross Annual Synergies", lambda r: _fmt_m(r.gross_synergy_m), sum(r.gross_synergy_m for r in rows_data), True),
        ("Less: One-Time Integration Costs", lambda r: f"({_fmt_m(r.integration_cost_m)})" if r.integration_cost_m > 0 else "—", -sum(r.integration_cost_m for r in rows_data), False),
        ("Pre-Tax Synergy Cash Flow (EBT)", lambda r: _fmt_m(r.ebt_impact_m, signed=True), sum(r.ebt_impact_m for r in rows_data), False),
        (f"Less: Taxes ({dcf.assumptions.tax_rate_pct:.0f}%)", lambda r: _fmt_m(-r.tax_impact_m, signed=True), -sum(r.tax_impact_m for r in rows_data), False),
        ("After-Tax Net Synergy Cash Flow", lambda r: _fmt_m(r.net_cash_flow_m, signed=True), sum(r.net_cash_flow_m for r in rows_data), True),
        ("Discount Factor", lambda r: f"{r.discount_factor:.4f}", None, False),
        ("Present Value of Annual Cash Flow", lambda r: _fmt_m(r.pv_cash_flow_m, signed=True), sum(r.pv_cash_flow_m for r in rows_data), True),
        ("Cumulative Present Value ($M)", lambda r: _fmt_m(r.cumulative_pv_m, signed=True), None, False),
    ]

    tbl_shape = slide.shapes.add_table(len(line_specs) + 1, n_cols, Inches(0.55), Inches(1.10), Inches(12.23), Inches(4.32))
    tbl = tbl_shape.table

    label_w = Inches(3.75)
    rem_w = (Inches(12.23) - label_w) / (n_years + 1)
    tbl.columns[0].width = label_w
    for ci in range(1, n_cols):
        tbl.columns[ci].width = int(rem_w)

    # Header row
    _style_cell(tbl.cell(0, 0), "Synergy DCF Line Item ($ Millions)", fill_hex=primary, font_size=8.5, bold=True, color_hex=CARD_WHITE_HEX)
    for yi, r in enumerate(rows_data, start=1):
        _style_cell(tbl.cell(0, yi), f"Year {r.year}", fill_hex=primary, font_size=8.5, bold=True, color_hex=CARD_WHITE_HEX, align=PP_ALIGN.RIGHT)
    _style_cell(tbl.cell(0, n_cols - 1), "Total Explicit", fill_hex=accent, font_size=8.5, bold=True, color_hex=CARD_WHITE_HEX, align=PP_ALIGN.RIGHT)

    for ri, (lbl, fn, total_val, is_emph) in enumerate(line_specs, start=1):
        bg = "EFECE3" if is_emph else (CARD_WHITE_HEX if ri % 2 == 1 else ALABASTER_HEX)
        txt_c = primary if is_emph else INK_HEX
        _style_cell(tbl.cell(ri, 0), lbl, fill_hex=bg, font_size=8.3, bold=is_emph, color_hex=txt_c)
        for yi, r in enumerate(rows_data, start=1):
            _style_cell(tbl.cell(ri, yi), fn(r), fill_hex=bg, font_size=8.3, bold=is_emph, color_hex=txt_c, align=PP_ALIGN.RIGHT)
        tot_str = "—" if total_val is None else _fmt_m(total_val, signed=True)
        _style_cell(tbl.cell(ri, n_cols - 1), tot_str, fill_hex=bg, font_size=8.3, bold=True, color_hex=primary, align=PP_ALIGN.RIGHT)

    # Bottom 2 Cards: Assumptions + Terminal Value Bridge
    _add_rect(slide, Inches(0.55), Inches(5.60), Inches(6.00), Inches(1.22), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(0.55), Inches(5.60), Inches(6.00), Inches(0.05), fill_hex=primary)
    _add_textbox(slide, Inches(0.70), Inches(5.70), Inches(5.70), Inches(0.24), "KEY MODEL ASSUMPTIONS", font_size=8.5, bold=True, color_hex=primary)
    a = dcf.assumptions
    term_desc = f"{a.terminal_multiple:.1f}x NOPAT Fade Multiple" if a.terminal_method != "perpetuity" else f"Gordon Growth g={a.terminal_growth_pct:.1f}%"
    assump_txt = (
        f"• Discount Rate (WACC): {a.wacc_pct:.1f}%   |   Corporate Tax Rate: {a.tax_rate_pct:.0f}%   |   Realization Factor: {a.realization_factor_pct:.0f}%\n"
        f"• Terminal Value Method: {term_desc}   |   Probability Weighting: {'Enabled' if a.use_probability_weighting else 'Gross (100%)'}\n"
        f"• Integration Cost Phasing: 60% Year 1 · 30% Year 2 · 10% Year 3 (Cost Stress: {a.integration_cost_overrun_pct:+.0f}%)"
    )
    _add_textbox(slide, Inches(0.70), Inches(5.96), Inches(5.70), Inches(0.80), assump_txt, font_size=8.2, color_hex=SLATE_HEX)

    _add_rect(slide, Inches(6.75), Inches(5.60), Inches(6.03), Inches(1.22), fill_hex=primary, border_hex=accent, border_pt=1.0)
    _add_textbox(slide, Inches(6.92), Inches(5.70), Inches(5.70), Inches(0.24), "DETERMINISTIC VALUATION OUTPUT", font_size=8.5, bold=True, color_hex=accent)
    out_txt = (
        f"PV Explicit Cash Flows: {_fmt_b(dcf.pv_explicit_period_b)}    +    PV Terminal Value: {_fmt_b(dcf.pv_terminal_value_b)}\n"
        f"TOTAL SYNERGY NPV: {_fmt_b(dcf.total_synergy_npv_b)}   ({f'{dcf.value_creation_pct_of_deal:.1f}% of Deal EV' if dcf.value_creation_pct_of_deal is not None else 'EV N/A'})\n"
        f"Simple Payback: {f'{dcf.payback_years:.2f} yrs' if dcf.payback_years else '—'}   |   Discounted Payback: {f'{dcf.discounted_payback_years:.2f} yrs' if dcf.discounted_payback_years else '—'}   |   IRR: {f'{dcf.synergy_irr_pct:.1f}%' if dcf.synergy_irr_pct is not None else '—'}"
    )
    _add_textbox(slide, Inches(6.92), Inches(5.96), Inches(5.70), Inches(0.80), out_txt, font_size=8.6, bold=True, color_hex=CARD_WHITE_HEX)


def _build_slide_05_sensitivity(prs: Presentation, report: DueDiligenceReport, dcf: SynergyDCFResult, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)

    _add_slide_chrome(
        slide, eyebrow="Section 04 · Valuation Stress-Testing",
        title="2D Sensitivity Matrix — WACC × Synergy Realization Rate ($B NPV)",
        slide_num=5, report=report, branding=branding,
    )

    wacc_vals, real_vals, grid = compute_sensitivity_matrix(report, dcf.assumptions)
    flat_vals = [v for row in grid for v in row]
    min_v = min(flat_vals) if flat_vals else 0.0
    max_v = max(flat_vals) if flat_vals else 1.0
    span = max(max_v - min_v, 0.01)

    n_rows = len(wacc_vals) + 1
    n_cols = len(real_vals) + 1
    tbl_shape = slide.shapes.add_table(n_rows, n_cols, Inches(0.55), Inches(1.16), Inches(8.15), Inches(4.52))
    tbl = tbl_shape.table
    tbl.columns[0].width = Inches(1.65)
    col_w = int((Inches(8.15) - Inches(1.65)) / len(real_vals))
    for ci in range(1, n_cols):
        tbl.columns[ci].width = col_w

    _style_cell(tbl.cell(0, 0), "WACC \\ Realization", fill_hex=primary, font_size=8.8, bold=True, color_hex=accent, align=PP_ALIGN.CENTER)
    for ci, r_val in enumerate(real_vals, start=1):
        lbl = f"{r_val:.0f}%" + (" (Base)" if abs(r_val - 100.0) < 0.1 else "")
        _style_cell(tbl.cell(0, ci), lbl, fill_hex=primary, font_size=8.8, bold=True, color_hex=CARD_WHITE_HEX, align=PP_ALIGN.CENTER)

    base_r = len(wacc_vals) // 2
    base_c = min(range(len(real_vals)), key=lambda i: abs(real_vals[i] - 100.0))

    for ri, (w_val, g_row) in enumerate(zip(wacc_vals, grid), start=1):
        is_base_row = (ri - 1) == base_r
        row_lbl = f"{w_val:.1f}%" + (" ★" if is_base_row else "")
        _style_cell(tbl.cell(ri, 0), row_lbl, fill_hex="E9E6DD", font_size=9, bold=True, color_hex=primary, align=PP_ALIGN.CENTER)
        for ci, cell_val in enumerate(g_row, start=1):
            is_base = is_base_row and ((ci - 1) == base_c)
            norm = (cell_val - min_v) / span
            if is_base:
                bg, fg, bdr_c, bdr_sides, bdr_w = GOLD_SOFT_HEX, primary, accent, ("lnL", "lnR", "lnT", "lnB"), 19050
            elif cell_val < 0:
                bg, fg, bdr_c, bdr_sides, bdr_w = "FEE2E2", CRIMSON_HEX, BORDER_HEX, ("lnB",), 6350
            elif norm < 0.25:
                bg, fg, bdr_c, bdr_sides, bdr_w = "FEF3C7", AMBER_HEX, BORDER_HEX, ("lnB",), 6350
            elif norm < 0.55:
                bg, fg, bdr_c, bdr_sides, bdr_w = "F8FAFC", INK_HEX, BORDER_HEX, ("lnB",), 6350
            elif norm < 0.80:
                bg, fg, bdr_c, bdr_sides, bdr_w = "ECFDF5", FOREST_HEX, BORDER_HEX, ("lnB",), 6350
            else:
                bg, fg, bdr_c, bdr_sides, bdr_w = "D1FAE5", FOREST_HEX, BORDER_HEX, ("lnB",), 6350

            txt = f"{_fmt_b(cell_val, signed=True)}" + ("\n[BASE]" if is_base else "")
            _style_cell(
                tbl.cell(ri, ci), txt, fill_hex=bg, font_name=SERIF_FONT if is_base else SANS_FONT,
                font_size=9.5 if is_base else 9.2, bold=True if is_base else False, color_hex=fg,
                align=PP_ALIGN.CENTER, border_hex=bdr_c, border_sides=bdr_sides, border_width_emu=bdr_w,
            )

    # Right Takeaways Card
    downside_v = grid[-1][0] if grid else 0.0
    base_v = grid[base_r][base_c] if grid else dcf.total_synergy_npv_b
    upside_v = grid[0][-1] if grid else 0.0
    pos_count = sum(1 for v in flat_vals if v > 0)

    _add_rect(slide, Inches(8.92), Inches(1.16), Inches(3.86), Inches(4.52), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(8.92), Inches(1.16), Inches(3.86), Inches(0.05), fill_hex=accent)
    _add_textbox(slide, Inches(9.10), Inches(1.28), Inches(3.50), Inches(0.28), "STRESS-TEST SCENARIO RANGE", font_size=9, bold=True, color_hex=primary)

    scenarios = [
        ("DOWNSIDE CASE", _fmt_b(downside_v, signed=True), f"WACC {wacc_vals[-1]:.1f}% · {real_vals[0]:.0f}% Capture", CRIMSON_BG_HEX, CRIMSON_HEX),
        ("BASE CASE (★)", _fmt_b(base_v, signed=True), f"WACC {wacc_vals[base_r]:.1f}% · {real_vals[base_c]:.0f}% Capture", GOLD_SOFT_HEX, primary),
        ("UPSIDE CASE", _fmt_b(upside_v, signed=True), f"WACC {wacc_vals[0]:.1f}% · {real_vals[-1]:.0f}% Capture", FOREST_BG_HEX, FOREST_HEX),
    ]
    for s_idx, (s_lbl, s_val, s_sub, s_bg, s_fg) in enumerate(scenarios):
        sy = Inches(1.66) + s_idx * Inches(0.95)
        _add_rect(slide, Inches(9.10), sy, Inches(3.50), Inches(0.84), fill_hex=s_bg, border_hex=BORDER_HEX)
        _add_textbox(slide, Inches(9.22), sy + Inches(0.07), Inches(1.85), Inches(0.22), s_lbl, font_size=7.8, bold=True, color_hex=SLATE_HEX)
        _add_textbox(slide, Inches(9.22), sy + Inches(0.30), Inches(1.85), Inches(0.42), s_val, font_name=SERIF_FONT, font_size=16, bold=True, color_hex=s_fg)
        _add_textbox(slide, Inches(11.00), sy + Inches(0.24), Inches(1.52), Inches(0.45), s_sub, font_size=7.6, color_hex=SLATE_HEX, align=PP_ALIGN.RIGHT)

    resilience = (
        f"• Valuation Resilience: {pos_count} of {len(flat_vals)} stressed scenarios yield positive Synergy NPV.\n"
        f"• Total Scenario Spread: {_fmt_b(upside_v - downside_v)} between severe downside and upside capture.\n"
        f"• Sensitivity Driver: Realization discipline drives larger NPV variance than ±200bps WACC shifts."
    )
    _add_textbox(slide, Inches(9.10), Inches(4.56), Inches(3.50), Inches(1.04), resilience, font_size=8.0, color_hex=INK_HEX)

    # Bottom Legend Strip
    _add_rect(slide, Inches(0.55), Inches(5.86), Inches(12.23), Inches(0.92), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_textbox(
        slide, Inches(0.72), Inches(5.96), Inches(11.90), Inches(0.72),
        "WALL STREET HEATMAP LEGEND:   Deep Emerald = Top-Quartile NPV Upside   |   Mint = Accretive Base Band   |   "
        "Gold Highlight [BASE] = Committee Base Case   |   Amber = Margin-Compressed Downside   |   Crimson = Value-Destructive (< $0B NPV)\n"
        "Note: All 25 cells recomputed deterministically via the Python Synergy DCF engine holding tax rate and integration cost phasing constant.",
        font_size=8.0, color_hex=SLATE_HEX,
    )


def _build_slide_06_workstreams(prs: Presentation, report: DueDiligenceReport, dcf: SynergyDCFResult, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)

    _add_slide_chrome(
        slide, eyebrow="Section 05 · Bottom-Up Synergy Architecture",
        title="Bottom-Up Synergy Workstreams & Accountability Matrix",
        slide_num=6, report=report, branding=branding,
    )

    workstreams = list(report.synergy_workstreams) or _synthesize_fallback_workstreams(report)
    max_display = 7
    shown = workstreams[:max_display]

    headers = ["Synergy Workstream", "Type", "Gross ($M)", "Prob.", "Risk-Adj ($M)", "1x Cost ($M)", "Ramp Y1/Y2/Y3", "Accountable Owner"]
    col_widths = [Inches(3.25), Inches(0.95), Inches(1.12), Inches(0.76), Inches(1.22), Inches(1.12), Inches(1.36), Inches(2.45)]

    n_rows = max(len(shown), 1) + 2  # header + rows + total
    tbl_shape = slide.shapes.add_table(n_rows, len(headers), Inches(0.55), Inches(1.12), Inches(12.23), Inches(4.78))
    tbl = tbl_shape.table
    for ci, w in enumerate(col_widths):
        tbl.columns[ci].width = w

    for ci, h in enumerate(headers):
        al = PP_ALIGN.RIGHT if ci in (2, 3, 4, 5) else (PP_ALIGN.CENTER if ci in (1, 6) else PP_ALIGN.LEFT)
        _style_cell(tbl.cell(0, ci), h, fill_hex=primary, font_size=8.4, bold=True, color_hex=CARD_WHITE_HEX, align=al)

    tot_gross = sum(w.annual_run_rate_usd_m for w in workstreams)
    tot_risk_adj = sum(w.annual_run_rate_usd_m * (w.probability_pct / 100.0) for w in workstreams)
    tot_cost = sum(w.one_time_cost_usd_m for w in workstreams)
    avg_prob = (tot_risk_adj / tot_gross * 100.0) if tot_gross > 0 else 0.0

    if not shown:
        for ci in range(len(headers)):
            _style_cell(tbl.cell(1, ci), "No workstreams populated" if ci == 0 else "—", fill_hex=CARD_WHITE_HEX, font_size=8.5, italic=True, color_hex=MUTED_HEX)
    else:
        for ri, ws in enumerate(shown, start=1):
            bg = CARD_WHITE_HEX if ri % 2 == 1 else ALABASTER_HEX
            ra = ws.annual_run_rate_usd_m * (ws.probability_pct / 100.0)
            type_c = FOREST_HEX if ws.synergy_type.value == "Revenue" else NAVY_MID_HEX
            vals = [
                _clip(ws.name, 46),
                ws.synergy_type.value,
                _fmt_m(ws.annual_run_rate_usd_m),
                f"{ws.probability_pct:.0f}%",
                _fmt_m(ra),
                _fmt_m(ws.one_time_cost_usd_m),
                f"{ws.ramp_year1_pct:.0f}% / {ws.ramp_year2_pct:.0f}% / {ws.ramp_year3_pct:.0f}%",
                _clip(ws.owner, 30),
            ]
            for ci, v in enumerate(vals):
                al = PP_ALIGN.RIGHT if ci in (2, 3, 4, 5) else (PP_ALIGN.CENTER if ci in (1, 6) else PP_ALIGN.LEFT)
                _style_cell(
                    tbl.cell(ri, ci), v, fill_hex=bg, font_size=8.3,
                    bold=(ci in (0, 4)), color_hex=(type_c if ci == 1 else (primary if ci in (0, 4) else INK_HEX)), align=al,
                )

    # Total row
    tot_ri = n_rows - 1
    tot_vals = [
        f"TOTAL PORTFOLIO ({len(workstreams)} Workstreams)",
        "—",
        _fmt_m(tot_gross),
        f"{avg_prob:.0f}%",
        _fmt_m(tot_risk_adj),
        _fmt_m(tot_cost),
        "3-Yr Phased",
        "IMO / Executive Sponsor",
    ]
    for ci, v in enumerate(tot_vals):
        al = PP_ALIGN.RIGHT if ci in (2, 3, 4, 5) else (PP_ALIGN.CENTER if ci in (1, 6) else PP_ALIGN.LEFT)
        _style_cell(tbl.cell(tot_ri, ci), v, fill_hex="EFECE3", font_size=8.5, bold=True, color_hex=primary, align=al, border_hex=primary, border_sides=("lnT", "lnB"), border_width_emu=12700)

    # Bottom Summary Strip
    rev_share = (dcf.steady_state_revenue_synergy_b / max(dcf.steady_state_total_synergy_b, 1e-6)) * 100.0 if dcf.steady_state_total_synergy_b > 0 else 0.0
    cost_share = max(0.0, 100.0 - rev_share) if dcf.steady_state_total_synergy_b > 0 else 0.0
    _add_rect(slide, Inches(0.55), Inches(6.05), Inches(12.23), Inches(0.78), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(0.55), Inches(6.05), Inches(0.08), Inches(0.78), fill_hex=accent)
    _add_textbox(
        slide, Inches(0.78), Inches(6.14), Inches(11.85), Inches(0.60),
        f"PORTFOLIO COMPOSITION:  Risk-adjusted run-rate synergies total {_fmt_m(tot_risk_adj)}/yr (weighted realization probability {avg_prob:.0f}%), "
        f"split {rev_share:.0f}% Revenue vs. {cost_share:.0f}% Cost & Capital. One-time implementation outlay of {_fmt_m(tot_cost)} implies a "
        f"{(tot_risk_adj / tot_cost):.2f}x annual run-rate payback multiple." if tot_cost > 0 else
        f"PORTFOLIO COMPOSITION:  Risk-adjusted run-rate synergies total {_fmt_m(tot_risk_adj)}/yr (weighted probability {avg_prob:.0f}%).",
        font_size=8.5, color_hex=INK_HEX,
    )


def _build_three_pillar_columns(
    slide,
    columns_spec: list[tuple[str, Iterable[Claim], str]],
    *,
    primary: str,
    accent: str,
) -> None:
    col_w = Inches(3.92)
    gap = Inches(0.235)
    top = Inches(1.12)
    height = Inches(5.68)
    for idx, (heading, claims, cap_hex) in enumerate(columns_spec):
        cx = Inches(0.55) + idx * (col_w + gap)
        _add_rect(slide, cx, top, col_w, height, fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
        _add_rect(slide, cx, top, col_w, Inches(0.42), fill_hex=cap_hex)
        _add_textbox(
            slide, cx + Inches(0.12), top + Inches(0.08), col_w - Inches(0.24), Inches(0.28),
            heading.upper(), font_name=SANS_FONT, font_size=8.8, bold=True, color_hex=CARD_WHITE_HEX,
        )
        _render_claims_into_box(
            slide, cx + Inches(0.08), top + Inches(0.50), col_w - Inches(0.16), height - Inches(0.58),
            claims, max_items=4, max_chars_per_claim=175, font_size=8.6, accent_hex=accent,
        )


def _build_slide_07_strategic_fit(prs: Presentation, report: DueDiligenceReport, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    score = _pillar_score_lookup(report, "strategic")
    score_tag = f"  ·  Pillar Score: {score}/100" if score is not None else ""

    _add_slide_chrome(
        slide, eyebrow=f"Section 06 · Pillar 2 Due Diligence{score_tag}",
        title="Pillar 2 — Strategic Fit & Commercial Synergies",
        slide_num=7, report=report, branding=branding,
    )
    sf = report.strategic_fit
    _build_three_pillar_columns(
        slide,
        [
            ("Market Position & Overlap", sf.market_position_overlap, primary),
            ("Cross-Selling Potential", sf.cross_selling_potential, NAVY_MID_HEX),
            ("Commercial Revenue Synergies", sf.revenue_synergies, accent),
        ],
        primary=primary,
        accent=accent,
    )


def _build_slide_08_operational_tech(prs: Presentation, report: DueDiligenceReport, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    score = _pillar_score_lookup(report, "operational")
    score_tag = f"  ·  Pillar Score: {score}/100" if score is not None else ""

    _add_slide_chrome(
        slide, eyebrow=f"Section 07 · Pillar 3 Due Diligence{score_tag}",
        title="Pillar 3 — Operational & Technology Due Diligence",
        slide_num=8, report=report, branding=branding,
    )
    ot = report.operational_tech
    _build_three_pillar_columns(
        slide,
        [
            ("Tech Stack & Architecture Clash", ot.tech_stack_clash, primary),
            ("Supply Chain & Footprint Redundancies", ot.supply_chain_redundancies, NAVY_MID_HEX),
            ("Operational Cost Synergies", ot.cost_synergies, accent),
        ],
        primary=primary,
        accent=accent,
    )


def _build_slide_09_cultural_reg(prs: Presentation, report: DueDiligenceReport, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)
    c_score = _pillar_score_lookup(report, "cultur")
    r_score = _pillar_score_lookup(report, "regulat")

    _add_slide_chrome(
        slide, eyebrow="Section 08 · Pillars 4 & 5 Due Diligence",
        title="Pillars 4 & 5 — Cultural / Talent & Regulatory / ESG Highlights",
        slide_num=9, report=report, branding=branding,
    )

    quads = [
        (
            Inches(0.55), Inches(1.12),
            f"PILLAR 4A · LEADERSHIP STYLE & GOVERNANCE{f'  ({c_score}/100)' if c_score is not None else ''}",
            report.cultural_risk.leadership_style, primary,
        ),
        (
            Inches(0.55), Inches(4.02),
            "PILLAR 4B · KEY TALENT RETENTION RISK",
            report.cultural_risk.talent_retention_risk, CRIMSON_HEX,
        ),
        (
            Inches(6.80), Inches(1.12),
            f"PILLAR 5A · ANTITRUST & MERGER CONTROL{f'  ({r_score}/100)' if r_score is not None else ''}",
            report.regulatory_esg.antitrust_flags, NAVY_MID_HEX,
        ),
        (
            Inches(6.80), Inches(4.02),
            "PILLAR 5B · ESG & EU CSRD COMPLIANCE GAPS",
            report.regulatory_esg.esg_csrd_gaps, accent,
        ),
    ]
    bw, bh = Inches(5.98), Inches(2.74)
    for bx, by, hdr, claims, cap_hex in quads:
        _add_rect(slide, bx, by, bw, bh, fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
        _add_rect(slide, bx, by, bw, Inches(0.38), fill_hex=cap_hex)
        _add_textbox(
            slide, bx + Inches(0.12), by + Inches(0.07), bw - Inches(0.24), Inches(0.24),
            hdr, font_name=SANS_FONT, font_size=8.6, bold=True, color_hex=CARD_WHITE_HEX,
        )
        _render_claims_into_box(
            slide, bx + Inches(0.08), by + Inches(0.42), bw - Inches(0.16), bh - Inches(0.48),
            claims, max_items=3, max_chars_per_claim=170, font_size=8.5, accent_hex=cap_hex,
        )


def _build_slide_10_risk_register(prs: Presentation, report: DueDiligenceReport, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)

    _add_slide_chrome(
        slide, eyebrow="Section 09 · Risk Governance & Mitigation",
        title="Investment Committee Risk Register Matrix (Likelihood × Impact)",
        slide_num=10, report=report, branding=branding,
    )

    risks: list[RiskItem] = sorted(
        report.risk_register,
        key=lambda r: (not r.is_deal_breaker, -(r.likelihood * r.impact)),
    )
    db_count = sum(1 for r in risks if r.is_deal_breaker)
    crit_high = sum(1 for r in risks if r.severity.value in ("Critical", "High"))
    avg_score = (sum(r.likelihood * r.impact for r in risks) / len(risks)) if risks else 0.0

    # Summary pill bar
    _add_rect(slide, Inches(0.55), Inches(1.08), Inches(12.23), Inches(0.48), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    summary_str = (
        f"TOTAL TRACKED RISKS: {len(risks)}    |    "
        f"★ DEAL-BREAKER FLAGS: {db_count}    |    "
        f"CRITICAL / HIGH SEVERITY: {crit_high}    |    "
        f"AVERAGE L×I SCORE: {avg_score:.1f} / 25.0"
    )
    _add_textbox(
        slide, Inches(0.72), Inches(1.18), Inches(11.90), Inches(0.28),
        summary_str, font_name=SANS_FONT, font_size=9.0, bold=True, color_hex=CRIMSON_HEX if db_count > 0 else primary,
    )

    shown = risks[:7]
    headers = ["Risk Title (★ = Deal-Breaker)", "Dimension", "Severity", "L × I", "Score", "Actionable Pre-Signing / PMI Mitigation"]
    widths = [Inches(3.05), Inches(1.55), Inches(0.98), Inches(0.78), Inches(0.72), Inches(5.15)]

    n_rows = max(len(shown), 1) + 1
    tbl_shape = slide.shapes.add_table(n_rows, len(headers), Inches(0.55), Inches(1.68), Inches(12.23), Inches(5.12))
    tbl = tbl_shape.table
    for ci, w in enumerate(widths):
        tbl.columns[ci].width = w

    for ci, h in enumerate(headers):
        al = PP_ALIGN.CENTER if ci in (2, 3, 4) else PP_ALIGN.LEFT
        _style_cell(tbl.cell(0, ci), h, fill_hex=primary, font_size=8.4, bold=True, color_hex=CARD_WHITE_HEX, align=al)

    if not shown:
        fallback_txt = "; ".join(_clip(c.claim, 90) for c in report.executive_summary.top_deal_breaker_risks[:2])
        _style_cell(tbl.cell(1, 0), "See Executive Deal-Breakers", fill_hex=CARD_WHITE_HEX, font_size=8.5, bold=True, color_hex=primary)
        for ci in range(1, 5):
            _style_cell(tbl.cell(1, ci), "—", fill_hex=CARD_WHITE_HEX, font_size=8.5, align=PP_ALIGN.CENTER)
        _style_cell(tbl.cell(1, 5), _clip(fallback_txt, 200), fill_hex=CARD_WHITE_HEX, font_size=8.2, color_hex=INK_HEX)
    else:
        for ri, r in enumerate(shown, start=1):
            bg = CARD_WHITE_HEX if ri % 2 == 1 else ALABASTER_HEX
            score = r.likelihood * r.impact
            score_bg = "FEE2E2" if score >= 15 else ("FEF3C7" if score >= 9 else FOREST_BG_HEX)
            score_fg = CRIMSON_HEX if score >= 15 else (AMBER_HEX if score >= 9 else FOREST_HEX)
            sev_fg = CRIMSON_HEX if r.severity.value == "Critical" else (AMBER_HEX if r.severity.value == "High" else primary)
            title_txt = ("★ " if r.is_deal_breaker else "") + _clip(r.title, 44)
            _style_cell(tbl.cell(ri, 0), title_txt, fill_hex=(CRIMSON_BG_HEX if r.is_deal_breaker else bg), font_size=8.3, bold=True, color_hex=(CRIMSON_HEX if r.is_deal_breaker else primary))
            _style_cell(tbl.cell(ri, 1), r.dimension.value, fill_hex=bg, font_size=8.1, color_hex=SLATE_HEX)
            _style_cell(tbl.cell(ri, 2), r.severity.value.upper(), fill_hex=bg, font_size=8.0, bold=True, color_hex=sev_fg, align=PP_ALIGN.CENTER)
            _style_cell(tbl.cell(ri, 3), f"{r.likelihood} × {r.impact}", fill_hex=bg, font_size=8.2, color_hex=INK_HEX, align=PP_ALIGN.CENTER)
            _style_cell(tbl.cell(ri, 4), str(score), fill_hex=score_bg, font_size=8.8, bold=True, color_hex=score_fg, align=PP_ALIGN.CENTER)
            _style_cell(tbl.cell(ri, 5), _clip(r.mitigation, 145), fill_hex=bg, font_size=8.1, color_hex=INK_HEX)


def _build_slide_11_pmi_roadmap(prs: Presentation, report: DueDiligenceReport, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)

    _add_slide_chrome(
        slide, eyebrow="Section 10 · Post-Merger Integration Execution",
        title="Post-Merger Integration (PMI) Roadmap — Day 1 to Month 36",
        slide_num=11, report=report, branding=branding,
    )

    # Visual 5-Stage PMI Timeline Ribbon
    stages = [
        ("01 · PRE-CLOSE", "Approvals & Waivers", primary),
        ("02 · DAY 1", "Control & Retention", NAVY_MID_HEX),
        ("03 · FIRST 100 DAYS", "Quick-Win Synergies", "264D73"),
        ("04 · MONTHS 4–12", "Scale & Platform", accent),
        ("05 · MONTHS 13–36", "Full Run-Rate Capture", FOREST_HEX),
    ]
    sw = Inches(2.33)
    sgap = Inches(0.145)
    for idx, (st_title, st_sub, st_hex) in enumerate(stages):
        sx = Inches(0.55) + idx * (sw + sgap)
        _add_rect(slide, sx, Inches(1.08), sw, Inches(0.60), fill_hex=st_hex)
        _add_textbox(slide, sx + Inches(0.10), Inches(1.13), sw - Inches(0.20), Inches(0.24), st_title, font_size=8.2, bold=True, color_hex=CARD_WHITE_HEX)
        _add_textbox(slide, sx + Inches(0.10), Inches(1.37), sw - Inches(0.20), Inches(0.24), st_sub, font_size=7.6, color_hex="E2E8F0")

    # PMI Roadmap Table
    milestones = report.integration_roadmap[:7]
    headers = ["Phase", "Workstream", "Strategic Objective & Execution Deliverable", "Accountable Owner", "Measurable Target KPI"]
    widths = [Inches(1.75), Inches(1.85), Inches(4.28), Inches(1.85), Inches(2.50)]

    n_rows = max(len(milestones), 1) + 1
    tbl_shape = slide.shapes.add_table(n_rows, len(headers), Inches(0.55), Inches(1.84), Inches(12.23), Inches(4.96))
    tbl = tbl_shape.table
    for ci, w in enumerate(widths):
        tbl.columns[ci].width = w

    for ci, h in enumerate(headers):
        _style_cell(tbl.cell(0, ci), h, fill_hex=primary, font_size=8.4, bold=True, color_hex=CARD_WHITE_HEX)

    if not milestones:
        _style_cell(tbl.cell(1, 0), "TBD", fill_hex=CARD_WHITE_HEX, font_size=8.5, bold=True, color_hex=MUTED_HEX)
        _style_cell(tbl.cell(1, 1), "IMO Setup", fill_hex=CARD_WHITE_HEX, font_size=8.5, color_hex=MUTED_HEX)
        _style_cell(tbl.cell(1, 2), "Detailed PMI milestones to be finalized during Phase-2 Clean Team planning.", fill_hex=CARD_WHITE_HEX, font_size=8.5, italic=True, color_hex=MUTED_HEX)
        _style_cell(tbl.cell(1, 3), "IMO Lead", fill_hex=CARD_WHITE_HEX, font_size=8.5, color_hex=MUTED_HEX)
        _style_cell(tbl.cell(1, 4), "100-Day Plan Signed Off", fill_hex=CARD_WHITE_HEX, font_size=8.5, color_hex=MUTED_HEX)
    else:
        for ri, m in enumerate(milestones, start=1):
            bg = CARD_WHITE_HEX if ri % 2 == 1 else ALABASTER_HEX
            phase_low = m.phase.lower()
            p_bg = CRIMSON_BG_HEX if ("pre-close" in phase_low or "day 1" in phase_low) else (GOLD_SOFT_HEX if "100" in phase_low else "EBF3FA")
            p_fg = CRIMSON_HEX if ("pre-close" in phase_low or "day 1" in phase_low) else (AMBER_HEX if "100" in phase_low else primary)
            _style_cell(tbl.cell(ri, 0), _clip(m.phase, 22), fill_hex=p_bg, font_size=8.2, bold=True, color_hex=p_fg)
            _style_cell(tbl.cell(ri, 1), _clip(m.workstream, 26), fill_hex=bg, font_size=8.2, bold=True, color_hex=primary)
            _style_cell(tbl.cell(ri, 2), _clip(m.objective, 135), fill_hex=bg, font_size=8.1, color_hex=INK_HEX)
            _style_cell(tbl.cell(ri, 3), _clip(m.owner, 26), fill_hex=bg, font_size=8.1, color_hex=SLATE_HEX)
            _style_cell(tbl.cell(ri, 4), _clip(m.kpi, 75), fill_hex=bg, font_size=8.1, bold=True, color_hex=FOREST_HEX)


def _build_slide_12_vdr_next_steps(prs: Presentation, report: DueDiligenceReport, branding: BrandingConfig) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    primary = _clean_hex(branding.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(branding.accent_hex, DEFAULT_ACCENT_HEX)

    _add_slide_chrome(
        slide, eyebrow="Section 11 · Phase-2 Confirmatory Diligence",
        title="Priority VDR Management Q&A, Data Gaps & IC Next Steps",
        slide_num=12, report=report, branding=branding,
    )

    # Left Card: Priority VDR Management Q&A
    _add_rect(slide, Inches(0.55), Inches(1.12), Inches(6.72), Inches(5.68), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(0.55), Inches(1.12), Inches(6.72), Inches(0.40), fill_hex=primary)
    _add_textbox(
        slide, Inches(0.70), Inches(1.20), Inches(6.40), Inches(0.24),
        "PRIORITY VIRTUAL DATA ROOM (VDR) & MANAGEMENT Q&A",
        font_size=8.8, bold=True, color_hex=CARD_WHITE_HEX,
    )

    tb_q = _add_textbox(slide, Inches(0.68), Inches(1.60), Inches(6.45), Inches(5.10), "", margin_lr=0.06, margin_tb=0.04)
    tf_q = tb_q.text_frame
    questions = report.due_diligence_questions[:7]
    if not questions:
        _add_run(tf_q.paragraphs[0], "No follow-up VDR questions recorded; confirm full financial & legal folder completeness.", font_size=9, italic=True, color_hex=MUTED_HEX)
    else:
        for idx, q in enumerate(questions, start=1):
            p = tf_q.paragraphs[0] if idx == 1 else tf_q.add_paragraph()
            p.space_before = Pt(2 if idx == 1 else 6)
            p.space_after = Pt(2)
            _add_run(p, f"Q{idx:02d}  ", font_size=8.8, bold=True, color_hex=accent)
            _add_run(p, _clip(q, 148), font_size=8.7, color_hex=INK_HEX)

    # Top-Right Card: Public Data Gaps
    _add_rect(slide, Inches(7.48), Inches(1.12), Inches(5.30), Inches(2.74), fill_hex=CARD_WHITE_HEX, border_hex=BORDER_HEX)
    _add_rect(slide, Inches(7.48), Inches(1.12), Inches(5.30), Inches(0.38), fill_hex=AMBER_HEX)
    _add_textbox(
        slide, Inches(7.62), Inches(1.19), Inches(5.00), Inches(0.24),
        "PUBLIC DATA GAPS (CONFIRMATORY VDR REQUIRED)",
        font_size=8.6, bold=True, color_hex=CARD_WHITE_HEX,
    )
    tb_g = _add_textbox(slide, Inches(7.60), Inches(1.56), Inches(5.05), Inches(2.22), "", margin_lr=0.05, margin_tb=0.04)
    tf_g = tb_g.text_frame
    gaps = report.data_gaps[:4]
    if not gaps:
        _add_run(tf_g.paragraphs[0], "No material public data gaps flagged in Phase-1 screening.", font_size=8.8, italic=True, color_hex=MUTED_HEX)
    else:
        for idx, g in enumerate(gaps):
            p = tf_g.paragraphs[0] if idx == 0 else tf_g.add_paragraph()
            p.space_before = Pt(2 if idx == 0 else 5)
            _add_run(p, "▲ ", font_size=8.5, bold=True, color_hex=AMBER_HEX)
            _add_run(p, _clip(g, 130), font_size=8.4, color_hex=INK_HEX)

    # Bottom-Right Card: Immediate IC Next Steps & Conditions Precedent
    _add_rect(slide, Inches(7.48), Inches(4.04), Inches(5.30), Inches(2.76), fill_hex=primary, border_hex=accent, border_pt=1.0)
    _add_textbox(
        slide, Inches(7.64), Inches(4.14), Inches(4.98), Inches(0.26),
        "IMMEDIATE IC NEXT STEPS & CONDITIONS PRECEDENT",
        font_size=8.8, bold=True, color_hex=accent,
    )

    next_steps: list[str] = [f"Stance ({report.executive_summary.recommendation.value}): {_clip(report.executive_summary.recommendation_rationale, 155)}"]
    for r in report.risk_register:
        if r.is_deal_breaker and len(next_steps) < 4:
            next_steps.append(f"CP ({_clip(r.title, 30)}): {_clip(r.mitigation, 105)}")
    if len(next_steps) < 3:
        next_steps.append("Convene Clean Team & request Phase-2 VDR financial/legal disclosures before binding bid.")

    tb_n = _add_textbox(slide, Inches(7.62), Inches(4.44), Inches(5.02), Inches(2.24), "", margin_lr=0.05, margin_tb=0.04)
    tf_n = tb_n.text_frame
    for idx, step in enumerate(next_steps[:4]):
        p = tf_n.paragraphs[0] if idx == 0 else tf_n.add_paragraph()
        p.space_before = Pt(2 if idx == 0 else 5)
        _add_run(p, f"{idx + 1}. ", font_size=8.4, bold=True, color_hex=accent)
        _add_run(p, step, font_size=8.2, color_hex=CARD_WHITE_HEX)


# =====================================================================
# Public Entrypoint
# =====================================================================

def report_to_pptx_bytes(
    report: DueDiligenceReport,
    dcf_result: Optional[SynergyDCFResult] = None,
    branding: Optional[BrandingConfig] = None,
) -> bytes:
    """
    Generate a polished, native, editable 12-Slide Investment Committee PowerPoint
    Pitchbook (.pptx) as bytes from any validated `DueDiligenceReport`.
    """
    dcf = dcf_result or compute_synergy_dcf(report)
    brand = branding or BrandingConfig()

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    _build_slide_01_cover(prs, report, dcf, brand)
    _build_slide_02_exec_summary(prs, report, dcf, brand)
    _build_slide_03_financial_bridge(prs, report, dcf, brand)
    _build_slide_04_dcf_schedule(prs, report, dcf, brand)
    _build_slide_05_sensitivity(prs, report, dcf, brand)
    _build_slide_06_workstreams(prs, report, dcf, brand)
    _build_slide_07_strategic_fit(prs, report, brand)
    _build_slide_08_operational_tech(prs, report, brand)
    _build_slide_09_cultural_reg(prs, report, brand)
    _build_slide_10_risk_register(prs, report, brand)
    _build_slide_11_pmi_roadmap(prs, report, brand)
    _build_slide_12_vdr_next_steps(prs, report, brand)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
