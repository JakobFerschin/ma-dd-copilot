"""
Enterprise Audit Pack, Executive Flash Report, Jira PMI Exporter & Multi-Language Briefs.

Provides four institutional publishing & governance utilities:
  1. `report_to_red_flag_flash_html(report, dcf_result=None, branding=None)` —
     60-Second 1-Page Executive Red-Flag Flash Report (Deal-Breakers, Quantified Risk
     Exposure, Immediate Conditions Precedent).
  2. `pmi_roadmap_to_jira_csv(report)` —
     Jira / Asana / Smartsheet-importable CSV of Post-Merger Integration milestones.
  3. `build_localized_executive_brief(report, language="DE", dcf_result=None)` —
     Localized Investment Committee executive brief in EN, DE, FR, or ES using
     authentic M&A banking terminology.
  4. `build_audit_pack_zip(report, dcf_result=None, audit_entries=None, branding=None, watermark_user="IC_Member")` —
     Tamper-evident (revisionssicheres) Enterprise Audit & Regulatory Pack (.zip)
     bundling DOCX, PPTX, XLSX, HTML Brief, Red-Flag Flash, Jira CSV, Canonical JSON,
     and a SHA-256 Integrity Certificate & Audit Trail manifest.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import html
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from core.financial_model import SynergyDCFResult, compute_synergy_dcf
from schemas.report_schema import DueDiligenceReport
from utils.export_engine import (
    report_to_docx_bytes,
    report_to_excel_bytes,
    report_to_html_bytes,
)
from utils.pptx_exporter import (
    DEFAULT_ACCENT_HEX,
    DEFAULT_PRIMARY_HEX,
    BrandingConfig,
    _clean_hex,
    report_to_pptx_bytes,
)


def _fmt_b(val: Optional[float]) -> str:
    if val is None:
        return "—"
    return f"${val:.2f}B"


def _fmt_m(val: Optional[float]) -> str:
    if val is None:
        return "—"
    return f"${val:,.0f}M"


def _infer_jira_priority(phase: str) -> str:
    p = (phase or "").lower()
    if any(k in p for k in ("pre-close", "signing", "day 1", "day-1", "closing")):
        return "Highest"
    if any(k in p for k in ("100", "first 90", "30 day", "immediate")):
        return "High"
    if any(k in p for k in ("month 4", "months 4", "year 1", "q2", "q3", "q4")):
        return "Medium"
    return "Low"


def _extract_conditions_precedent(report: DueDiligenceReport) -> list[str]:
    """Assemble actionable Conditions Precedent from recommendation, deal-breakers, and Pre-Close milestones."""
    cps: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        norm = " ".join(text.split()).strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            cps.append(norm)

    for r in report.risk_register:
        if r.is_deal_breaker:
            _add(f"[{r.title}] {r.mitigation}")

    for m in report.integration_roadmap:
        p = m.phase.lower()
        if any(k in p for k in ("pre-close", "signing", "day 1", "day-1")):
            _add(f"[{m.phase} · {m.workstream}] {m.objective} (KPI: {m.kpi})")

    if not cps and report.executive_summary.recommendation_rationale:
        _add(report.executive_summary.recommendation_rationale)

    return cps[:6]


def _compute_quantified_risk_exposure_m(report: DueDiligenceReport) -> float:
    """Sum quantified annual risk exposure ($M) across top deal-breakers (and critical/high risk claims if none)."""
    db_impacts = [
        c.quantified_impact_usd_m
        for c in report.executive_summary.top_deal_breaker_risks
        if c.quantified_impact_usd_m is not None and c.quantified_impact_usd_m > 0
    ]
    if db_impacts:
        return round(sum(db_impacts), 1)
    fallback = [
        c.quantified_impact_usd_m
        for c in report.all_claims()
        if c.quantified_impact_usd_m is not None
        and c.quantified_impact_usd_m > 0
        and c.severity is not None
        and c.severity.value in ("Critical", "High")
    ]
    return round(sum(fallback), 1)


# =====================================================================
# 1. 60-Second 1-Page Executive Red-Flag Flash Report (.html)
# =====================================================================

def report_to_red_flag_flash_html(
    report: DueDiligenceReport,
    dcf_result: Optional[SynergyDCFResult] = None,
    branding: Optional[BrandingConfig] = None,
) -> bytes:
    """
    Generate a 60-Second, 1-Page Print-Ready Executive Red-Flag Flash Report (.html)
    highlighting Deal-Breakers, Quantified Risk Exposure, and Immediate Conditions Precedent.
    """
    dcf = dcf_result or compute_synergy_dcf(report)
    brand = branding or BrandingConfig()
    primary = _clean_hex(brand.primary_hex, DEFAULT_PRIMARY_HEX)
    accent = _clean_hex(brand.accent_hex, DEFAULT_ACCENT_HEX)
    esc = html.escape
    d = report.deal
    es = report.executive_summary

    deal_breaker_risks = [r for r in report.risk_register if r.is_deal_breaker]
    db_count = max(len(deal_breaker_risks), len(es.top_deal_breaker_risks))
    exposure_m = _compute_quantified_risk_exposure_m(report)
    exposure_b = exposure_m / 1000.0
    exposure_pct_ev = (
        f"{(exposure_b / d.deal_value_usd_b) * 100.0:.1f}% of EV"
        if d.deal_value_usd_b and d.deal_value_usd_b > 0 and exposure_m > 0
        else "Unquantified in public filings"
    )
    exposure_pct_syn = (
        f"{(exposure_b / dcf.steady_state_total_synergy_b) * 100.0:.0f}% of Run-Rate Synergies"
        if dcf.steady_state_total_synergy_b > 0 and exposure_m > 0
        else "Requires VDR sizing"
    )

    reco_badge_colors = {
        "Go": ("#14532D", "#ECFDF5"),
        "No-Go": ("#7F1D1D", "#FEF2F2"),
        "Conditional Go": ("#78350F", "#FFFBEB"),
    }
    reco_fg, reco_bg = reco_badge_colors.get(es.recommendation.value, (f"#{primary}", "#F6F5F0"))

    # Deal-Breaker Claims rows
    db_claim_items = []
    for c in es.top_deal_breaker_risks:
        sev = c.severity.value.upper() if c.severity else "HIGH"
        sev_cls = "sev-crit" if sev == "CRITICAL" else "sev-high"
        imp = f'<span class="imp">{_fmt_m(c.quantified_impact_usd_m)}/yr at risk</span>' if c.quantified_impact_usd_m else ""
        cites = " · ".join(f"{s.source_document} ({s.location})" for s in c.sources[:2])
        cite_html = f'<div class="cite">Source: {esc(cites)}</div>' if cites else ""
        db_claim_items.append(
            f'<li><span class="pill {sev_cls}">{esc(sev)}</span> '
            f'<strong>{esc(c.claim)}</strong> {imp}{cite_html}</li>'
        )

    # Risk Register Deal-Breaker / High Matrix rows
    top_reg = sorted(report.risk_register, key=lambda r: (not r.is_deal_breaker, -(r.likelihood * r.impact)))[:5]
    reg_rows = "".join(
        f"<tr>"
        f"<td><strong>{'★ ' if r.is_deal_breaker else ''}{esc(r.title)}</strong></td>"
        f"<td>{esc(r.dimension.value)}</td>"
        f"<td class='center'><span class='score {'score-hi' if r.likelihood * r.impact >= 15 else 'score-mid'}'>{r.likelihood}×{r.impact} ({r.likelihood * r.impact})</span></td>"
        f"<td>{esc(r.mitigation)}</td>"
        f"</tr>"
        for r in top_reg
    )

    conditions = _extract_conditions_precedent(report)
    cp_html = "".join(f"<li>{esc(cp)}</li>" for cp in conditions)

    gaps_html = "".join(f"<li>{esc(g)}</li>" for g in report.data_gaps[:4]) or "<li>No critical public data gaps flagged.</li>"
    wm_banner = (
        f'<div class="wm">CONFIDENTIAL WATERMARK: {esc(brand.watermark_text)}</div>'
        if brand.watermark_text
        else ""
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>60-Second Red-Flag Flash — {esc(d.entity_a_name)} × {esc(d.entity_b_name)}</title>
<style>
  @page {{ size: A4; margin: 10mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #F6F5F0; color: #141822; margin: 0; padding: 18px; font-size: 11.5px; line-height: 1.4; }}
  .sheet {{ max-width: 960px; margin: 0 auto; background: #FFFFFF; border: 1px solid #DCD8CE; border-top: 5px solid #7F1D1D; padding: 18px 22px; }}
  .hdr {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #{accent}; padding-bottom: 10px; margin-bottom: 12px; }}
  .eyebrow {{ color: #7F1D1D; font-size: 9.5px; font-weight: 800; letter-spacing: 0.14em; text-transform: uppercase; }}
  h1 {{ font-family: Georgia, serif; font-size: 21px; color: #{primary}; margin: 3px 0 2px; }}
  .sub {{ color: #475569; font-size: 11px; }}
  .reco-box {{ text-align: right; }}
  .reco {{ display: inline-block; padding: 5px 12px; border-radius: 4px; font-weight: 800; font-size: 11px; letter-spacing: 0.08em; color: {reco_fg}; background: {reco_bg}; border: 1px solid {reco_fg}55; }}
  .wm {{ margin-top: 4px; font-size: 9px; color: #7F1D1D; font-weight: 700; letter-spacing: 0.06em; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }}
  .kpi {{ background: #FAF9F6; border: 1px solid #E2DFD7; border-left: 3px solid #{primary}; padding: 8px 10px; }}
  .kpi.alert {{ border-left-color: #7F1D1D; background: #FEF2F2; }}
  .kpi-l {{ font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: #64748B; font-weight: 700; }}
  .kpi-v {{ font-family: Georgia, serif; font-size: 17px; font-weight: 700; color: #{primary}; margin: 2px 0; }}
  .kpi.alert .kpi-v {{ color: #7F1D1D; }}
  .kpi-s {{ font-size: 9.5px; color: #475569; }}
  h2 {{ font-family: Georgia, serif; font-size: 13px; color: #{primary}; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid #DCD8CE; padding-bottom: 3px; margin: 12px 0 7px; }}
  ul, ol {{ margin: 4px 0 8px; padding-left: 18px; }}
  li {{ margin-bottom: 5px; }}
  .pill {{ display: inline-block; font-size: 8.5px; font-weight: 800; padding: 1px 5px; border-radius: 3px; margin-right: 4px; }}
  .sev-crit {{ background: #7F1D1D; color: #FFFFFF; }}
  .sev-high {{ background: #78350F; color: #FFFFFF; }}
  .imp {{ display: inline-block; background: #FEF3C7; color: #78350F; font-weight: 700; font-size: 10px; padding: 0 5px; border-radius: 3px; margin-left: 4px; }}
  .cite {{ font-size: 9.5px; color: #64748B; font-style: italic; margin-top: 1px; }}
  .two-col {{ display: grid; grid-template-columns: 1.25fr 1fr; gap: 14px; }}
  .box {{ background: #FAF9F6; border: 1px solid #E2DFD7; padding: 10px 12px; border-radius: 4px; }}
  .box.navy {{ background: #{primary}; color: #FFFFFF; border-color: #{accent}; }}
  .box.navy h2 {{ color: #{accent}; border-bottom-color: #1A3A5C; margin-top: 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 10.5px; margin-top: 4px; }}
  th {{ background: #{primary}; color: #FFFFFF; text-align: left; padding: 5px 7px; font-size: 9.5px; text-transform: uppercase; }}
  td {{ padding: 5px 7px; border-bottom: 1px solid #E2DFD7; vertical-align: top; }}
  .center {{ text-align: center; white-space: nowrap; }}
  .score {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-weight: 700; font-size: 10px; }}
  .score-hi {{ background: #FEE2E2; color: #7F1D1D; }}
  .score-mid {{ background: #FEF3C7; color: #78350F; }}
  .foot {{ margin-top: 12px; padding-top: 8px; border-top: 1px solid #DCD8CE; font-size: 9px; color: #64748B; display: flex; justify-content: space-between; }}
  @media print {{ body {{ background: #FFFFFF; padding: 0; }} .sheet {{ border: none; padding: 0; }} }}
</style>
</head>
<body>
<div class="sheet">
  <div class="hdr">
    <div>
      <div class="eyebrow">60-Second Executive Red-Flag Flash Report · {esc(brand.firm_name)}</div>
      <h1>{esc(d.entity_a_name)} × {esc(d.entity_b_name)}</h1>
      <div class="sub">{esc(d.deal_type)} · {esc(d.industry_sector or 'Strategic M&A')} · Analysis Date: {esc(d.analysis_date)} · Grounding: {report.grounding_ratio() * 100:.0f}% ({report.citation_count()} Citations)</div>
    </div>
    <div class="reco-box">
      <div class="reco">{esc(es.recommendation.value.upper())}</div>
      <div class="sub" style="margin-top:4px;">Complexity: <strong>{esc(es.integration_complexity.value)}</strong> · Conviction: <strong>{d.overall_conviction_score if d.overall_conviction_score is not None else '—'}/100</strong></div>
      {wm_banner}
    </div>
  </div>

  <div class="kpi-row">
    <div class="kpi alert">
      <div class="kpi-l">Deal-Breaker Flags</div>
      <div class="kpi-v">{db_count}</div>
      <div class="kpi-s">Mandatory IC gating items</div>
    </div>
    <div class="kpi alert">
      <div class="kpi-l">Quantified Risk Exposure</div>
      <div class="kpi-v">{_fmt_m(exposure_m) if exposure_m > 0 else 'TBD (VDR)'}</div>
      <div class="kpi-s">{esc(exposure_pct_ev)} · {esc(exposure_pct_syn)}</div>
    </div>
    <div class="kpi">
      <div class="kpi-l">Deal EV / Synergy NPV</div>
      <div class="kpi-v">{_fmt_b(d.deal_value_usd_b)} / {_fmt_b(dcf.total_synergy_npv_b)}</div>
      <div class="kpi-s">Value Creation: {f'{dcf.value_creation_pct_of_deal:.1f}% EV' if dcf.value_creation_pct_of_deal is not None else 'N/A'}</div>
    </div>
    <div class="kpi">
      <div class="kpi-l">1x Integration Spend</div>
      <div class="kpi-v">{_fmt_b(dcf.total_integration_cost_b)}</div>
      <div class="kpi-s">Payback: {f'{dcf.payback_years:.1f} yrs' if dcf.payback_years else '—'} · IRR: {f'{dcf.synergy_irr_pct:.1f}%' if dcf.synergy_irr_pct is not None else '—'}</div>
    </div>
  </div>

  <h2>1. Top Deal-Breaker Risks & Red Flags</h2>
  <ul>{''.join(db_claim_items)}</ul>

  {f'<h2>2. Priority Risk Heatmap & Mitigations</h2><table><thead><tr><th style="width:26%">Risk (★ Deal-Breaker)</th><th style="width:16%">Dimension</th><th style="width:12%" class="center">L × I</th><th>Mitigation Strategy</th></tr></thead><tbody>{reg_rows}</tbody></table>' if reg_rows else ''}

  <div class="two-col" style="margin-top:12px;">
    <div class="box navy">
      <h2>3. Immediate Conditions Precedent (Binding Offer Gates)</h2>
      <ol>{cp_html}</ol>
    </div>
    <div class="box">
      <h2 style="margin-top:0;">4. Critical Public Data Gaps (VDR Phase-2)</h2>
      <ul>{gaps_html}</ul>
    </div>
  </div>

  <div class="foot">
    <span>{esc(brand.disclaimer)}</span>
    <span>Generated UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}</span>
  </div>
</div>
</body>
</html>"""
    return doc.encode("utf-8")


# =====================================================================
# 2. PMI Roadmap to Jira / Asana / Smartsheet CSV
# =====================================================================

def pmi_roadmap_to_jira_csv(report: DueDiligenceReport) -> bytes:
    """
    Export the Post-Merger Integration (PMI) Roadmap directly as a
    Jira / Asana / Smartsheet-importable CSV.

    Columns: Summary,Issue Type,Phase,Workstream,Assignee,KPI,Priority
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Summary", "Issue Type", "Phase", "Workstream", "Assignee", "KPI", "Priority"])

    for m in report.integration_roadmap:
        summary = f"[{m.phase}] {m.workstream}: {m.objective}"
        writer.writerow([
            summary,
            "Task",
            m.phase,
            m.workstream,
            m.owner,
            m.kpi,
            _infer_jira_priority(m.phase),
        ])

    return buf.getvalue().encode("utf-8")


# =====================================================================
# 3. Localized Executive Brief (EN / DE / FR / ES)
# =====================================================================

_LOCALIZED_LEXICON: dict[str, dict[str, Any]] = {
    "EN": {
        "banner": "INVESTMENT COMMITTEE EXECUTIVE BRIEF — STRICTLY CONFIDENTIAL",
        "subtitle": "Pre-Merger Intelligence & Deterministic Synergy Valuation Summary",
        "meta_sector": "Industry Sector",
        "meta_type": "Transaction Structure",
        "meta_date": "Analysis Date",
        "meta_horizon": "Synergy Capture Horizon",
        "months": "Months",
        "sec_financials": "1. Headline Valuation & Synergy Metrics",
        "col_metric": "Metric",
        "col_value": "Assessment",
        "m_ev": "Enterprise Value (EV)",
        "m_npv": "Deterministic Synergy NPV (After-Tax)",
        "m_vc": "Value Creation (% of EV)",
        "m_rev": "Risk-Adjusted Revenue Synergies (Run-Rate)",
        "m_cost": "Risk-Adjusted Cost Synergies (Run-Rate)",
        "m_int": "One-Time Integration Costs",
        "m_pb_irr": "Simple Payback / Synergy IRR",
        "m_wacc": "Valuation Basis (WACC / Tax)",
        "sec_reco": "2. Investment Committee Recommendation",
        "lbl_stance": "Recommendation",
        "lbl_complexity": "Integration Complexity",
        "lbl_conviction": "Conviction Index",
        "lbl_rationale": "Strategic Rationale",
        "lbl_stance_detail": "Recommendation Rationale",
        "sec_risks": "3. Key Deal-Breaker Risks & Red Flags",
        "sec_cp": "4. Immediate Conditions Precedent",
        "sec_grounding": "5. Evidence Grounding & Data Gaps",
        "grounding_tpl": "{citations} source citations across {sources} filing(s) ({ratio:.0f}% claim grounding ratio); {gaps} public data gap(s) flagged for Phase-2 VDR.",
        "reco_map": {"Go": "GO — Proceed to Execution", "Conditional Go": "CONDITIONAL GO — Subject to Conditions Precedent", "No-Go": "NO-GO — Decline Transaction"},
        "comp_map": {"Low": "Low", "Medium": "Medium", "High": "High", "Critical": "Critical"},
        "yrs": "yrs",
    },
    "DE": {
        "banner": "INVESTITIONSAUSSCHUSS-KURZBERICHT (EXECUTIVE BRIEF) — STRENG VERTRAULICH",
        "subtitle": "Pre-Merger Due Diligence & Deterministische Synergiebewertung (DCF)",
        "meta_sector": "Branche / Sektor",
        "meta_type": "Transaktionsstruktur",
        "meta_date": "Analysedatum",
        "meta_horizon": "Synergie-Realisierungshorizont",
        "months": "Monate",
        "sec_financials": "1. Zentrale Bewertungs- & Synergiekennzahlen",
        "col_metric": "Kennzahl",
        "col_value": "Bewertung",
        "m_ev": "Transaktionswert (Enterprise Value)",
        "m_npv": "Deterministischer Synergie-Kapitalwert (NPV nach Steuern)",
        "m_vc": "Wertschöpfung (% des Transaktionswerts)",
        "m_rev": "Risikoadjustierte Umsatzsynergien (Run-Rate)",
        "m_cost": "Risikoadjustierte Kostensynergien (Run-Rate)",
        "m_int": "Einmalige Integrationskosten (Implementation Costs)",
        "m_pb_irr": "Amortisationsdauer (Payback) / Projekt-IRR",
        "m_wacc": "Bewertungsbasis (WACC / Steuersatz)",
        "sec_reco": "2. Empfehlung des Investment Committee",
        "lbl_stance": "Votum",
        "lbl_complexity": "Integrationskomplexität",
        "lbl_conviction": "Conviction-Index",
        "lbl_rationale": "Strategische Transaktionslogik",
        "lbl_stance_detail": "Begründung & Auflagen",
        "sec_risks": "3. Wesentliche Deal-Breaker-Risiken (Red Flags)",
        "sec_cp": "4. Aufschiebende Bedingungen (Conditions Precedent)",
        "sec_grounding": "5. Quellenabdeckung (Audit Trail) & Datenlücken",
        "grounding_tpl": "{citations} Quellenbelege aus {sources} Dokument(en) ({ratio:.0f}% Belegquote / Grounding Ratio); {gaps} öffentliche Datenlücke(n) für Phase-2-VDR markiert.",
        "reco_map": {"Go": "FREIGABE (Go) — Transaktion fortführen", "Conditional Go": "BEDINGTE FREIGABE (Conditional Go) — Unter Auflagen", "No-Go": "ABLEHNUNG (No-Go) — Transaktion verwerfen"},
        "comp_map": {"Low": "Niedrig", "Medium": "Mittel", "High": "Hoch", "Critical": "Kritisch"},
        "yrs": "Jahre",
    },
    "FR": {
        "banner": "NOTE DE SYNTHÈSE — COMITÉ D'INVESTISSEMENT — STRICTEMENT CONFIDENTIEL",
        "subtitle": "Due Diligence Pré-Fusion & Valorisation Déterministe des Synergies (DCF)",
        "meta_sector": "Secteur d'Activité",
        "meta_type": "Structure de l'Opération",
        "meta_date": "Date d'Analyse",
        "meta_horizon": "Horizon de Capture des Synergies",
        "months": "Mois",
        "sec_financials": "1. Indicateurs Clés de Valorisation & Synergies",
        "col_metric": "Indicateur",
        "col_value": "Évaluation",
        "m_ev": "Valeur d'Entreprise (VE)",
        "m_npv": "VAN Déterministe des Synergies (Après Impôts)",
        "m_vc": "Création de Valeur (% de la VE)",
        "m_rev": "Synergies de Revenus Ajustées du Risque (Run-Rate)",
        "m_cost": "Synergies de Coûts Ajustées du Risque (Run-Rate)",
        "m_int": "Coûts d'Intégration Non Récurrents",
        "m_pb_irr": "Délai de Récupération (Payback) / TRI Projet",
        "m_wacc": "Hypothèses DCF (CMPC / WACC & Taux d'IS)",
        "sec_reco": "2. Recommandation du Comité d'Investissement",
        "lbl_stance": "Décision Recommandée",
        "lbl_complexity": "Complexité d'Intégration",
        "lbl_conviction": "Indice de Conviction",
        "lbl_rationale": "Logique Stratégique",
        "lbl_stance_detail": "Position & Conditions",
        "sec_risks": "3. Risques Rédhibitoires (Deal-Breakers)",
        "sec_cp": "4. Conditions Suspensives Immédiates",
        "sec_grounding": "5. Traçabilité Documentaire & Lacunes (VDR)",
        "grounding_tpl": "{citations} citations sourcées issues de {sources} document(s) (taux d'ancrage : {ratio:.0f}%); {gaps} lacune(s) de données publiques à vérifier en Data Room Phase 2.",
        "reco_map": {"Go": "FEU VERT (Go) — Poursuivre l'acquisition", "Conditional Go": "FEU VERT SOUS CONDITIONS (Conditional Go)", "No-Go": "REJET (No-Go) — Abandonner l'opération"},
        "comp_map": {"Low": "Faible", "Medium": "Modérée", "High": "Élevée", "Critical": "Critique"},
        "yrs": "ans",
    },
    "ES": {
        "banner": "RESUMEN EJECUTIVO — COMITÉ DE INVERSIONES — ESTRICTAMENTE CONFIDENCIAL",
        "subtitle": "Due Diligence Pre-Fusión y Valoración Determinista de Sinergias (DCF)",
        "meta_sector": "Sector Industrial",
        "meta_type": "Estructura de la Operación",
        "meta_date": "Fecha de Análisis",
        "meta_horizon": "Horizonte de Captura de Sinergias",
        "months": "Meses",
        "sec_financials": "1. Métricas Clave de Valoración y Sinergias",
        "col_metric": "Métrica",
        "col_value": "Evaluación",
        "m_ev": "Valor de Empresa (EV)",
        "m_npv": "VAN Determinista de Sinergias (Después de Impuestos)",
        "m_vc": "Creación de Valor (% del EV)",
        "m_rev": "Sinergias de Ingresos Ajustadas por Riesgo (Run-Rate)",
        "m_cost": "Sinergias de Costes Ajustadas por Riesgo (Run-Rate)",
        "m_int": "Costes Únicos de Integración",
        "m_pb_irr": "Plazo de Recuperación (Payback) / TIR de Sinergias",
        "m_wacc": "Base de Valoración (WACC / Tasa Fiscal)",
        "sec_reco": "2. Recomendación del Comité de Inversiones",
        "lbl_stance": "Dictamen",
        "lbl_complexity": "Complejidad de Integración",
        "lbl_conviction": "Índice de Convicción",
        "lbl_rationale": "Fundamento Estratégico",
        "lbl_stance_detail": "Justificación y Condiciones",
        "sec_risks": "3. Riesgos Críticos (Deal-Breakers / Red Flags)",
        "sec_cp": "4. Condiciones Precedentes Inmediatas",
        "sec_grounding": "5. Trazabilidad Documental y Brechas de Datos",
        "grounding_tpl": "{citations} citas documentales en {sources} documento(s) ({ratio:.0f}% de respaldo verificable); {gaps} brecha(s) de información pública señaladas para revisión en VDR Fase 2.",
        "reco_map": {"Go": "APROBACIÓN (Go) — Proceder con la operación", "Conditional Go": "APROBACIÓN CONDICIONADA (Conditional Go)", "No-Go": "RECHAZO (No-Go) — Descartar la operación"},
        "comp_map": {"Low": "Baja", "Medium": "Media", "High": "Alta", "Critical": "Crítica"},
        "yrs": "años",
    },
}


def build_localized_executive_brief(
    report: DueDiligenceReport,
    language: str = "DE",
    dcf_result: Optional[SynergyDCFResult] = None,
) -> str:
    """
    Generate an executive brief header & structured Markdown summary localized for
    EN, DE, FR, or ES using authentic M&A banking terminology.
    """
    lang = (language or "EN").strip().upper()
    lex = _LOCALIZED_LEXICON.get(lang, _LOCALIZED_LEXICON["EN"])
    dcf = dcf_result or compute_synergy_dcf(report)
    d = report.deal
    es = report.executive_summary

    reco_txt = lex["reco_map"].get(es.recommendation.value, es.recommendation.value)
    comp_txt = lex["comp_map"].get(es.integration_complexity.value, es.integration_complexity.value)
    vc_str = f"{dcf.value_creation_pct_of_deal:.1f}%" if dcf.value_creation_pct_of_deal is not None else "—"
    pb_str = f"{dcf.payback_years:.2f} {lex['yrs']}" if dcf.payback_years is not None else "—"
    irr_str = f"{dcf.synergy_irr_pct:.1f}%" if dcf.synergy_irr_pct is not None else "—"
    conv_str = f"{d.overall_conviction_score}/100" if d.overall_conviction_score is not None else "—"

    lines = [
        f"> **{lex['banner']}**",
        "",
        f"# {d.entity_a_name} × {d.entity_b_name}",
        f"*{lex['subtitle']}*",
        "",
        f"- **{lex['meta_sector']}:** {d.industry_sector or 'Strategic M&A'}",
        f"- **{lex['meta_type']}:** {d.deal_type}",
        f"- **{lex['meta_date']}:** {d.analysis_date}",
        f"- **{lex['meta_horizon']}:** {d.time_horizon_months or 36} {lex['months']}",
        "",
        f"## {lex['sec_financials']}",
        "",
        f"| {lex['col_metric']} | {lex['col_value']} |",
        "|---|---:|",
        f"| {lex['m_ev']} | {_fmt_b(d.deal_value_usd_b)} |",
        f"| {lex['m_npv']} | **{_fmt_b(dcf.total_synergy_npv_b)}** |",
        f"| {lex['m_vc']} | {vc_str} |",
        f"| {lex['m_rev']} | {_fmt_b(dcf.steady_state_revenue_synergy_b)} |",
        f"| {lex['m_cost']} | {_fmt_b(dcf.steady_state_cost_synergy_b)} |",
        f"| {lex['m_int']} | {_fmt_b(dcf.total_integration_cost_b)} |",
        f"| {lex['m_pb_irr']} | {pb_str} / {irr_str} |",
        f"| {lex['m_wacc']} | {dcf.assumptions.wacc_pct:.1f}% / {dcf.assumptions.tax_rate_pct:.0f}% |",
        "",
        f"## {lex['sec_reco']}",
        "",
        f"- **{lex['lbl_stance']}:** {reco_txt}",
        f"- **{lex['lbl_complexity']}:** {comp_txt}",
        f"- **{lex['lbl_conviction']}:** {conv_str}",
        "",
        f"**{lex['lbl_rationale']}:** {es.strategic_rationale}",
        "",
        f"**{lex['lbl_stance_detail']}:** {es.recommendation_rationale}",
        "",
        f"## {lex['sec_risks']}",
        "",
    ]

    for c in es.top_deal_breaker_risks:
        sev = f"[{c.severity.value.upper()}] " if c.severity else ""
        imp = f" ({_fmt_m(c.quantified_impact_usd_m)}/yr)" if c.quantified_impact_usd_m else ""
        src = (
            " — *" + "; ".join(f"{s.source_document} ({s.location})" for s in c.sources[:2]) + "*"
            if c.sources
            else ""
        )
        lines.append(f"- {sev}{c.claim}{imp}{src}")

    lines.extend(["", f"## {lex['sec_cp']}", ""])
    for idx, cp in enumerate(_extract_conditions_precedent(report), start=1):
        lines.append(f"{idx}. {cp}")

    lines.extend([
        "",
        f"## {lex['sec_grounding']}",
        "",
        lex["grounding_tpl"].format(
            citations=report.citation_count(),
            sources=len(report.unique_sources()),
            ratio=report.grounding_ratio() * 100.0,
            gaps=len(report.data_gaps),
        ),
        "",
    ])
    return "\n".join(lines)


# =====================================================================
# 4. Tamper-Evident Enterprise Audit & Regulatory Pack (.zip)
# =====================================================================

def _serialize_audit_entries(audit_entries: Optional[Iterable[Any]]) -> list[Any]:
    if not audit_entries:
        return []
    out: list[Any] = []
    for entry in audit_entries:
        if isinstance(entry, dict):
            out.append(entry)
        elif hasattr(entry, "model_dump"):
            out.append(entry.model_dump(mode="json"))
        elif dataclasses.is_dataclass(entry):
            out.append(dataclasses.asdict(entry))
        else:
            out.append({"event": str(entry)})
    return out


def build_audit_pack_zip(
    report: DueDiligenceReport,
    dcf_result: Optional[SynergyDCFResult] = None,
    audit_entries: Optional[Iterable[Any]] = None,
    branding: Optional[BrandingConfig] = None,
    watermark_user: str = "IC_Member",
) -> bytes:
    """
    Generate a tamper-evident (revisionssicheres) Enterprise Audit & Regulatory Pack (.zip)
    containing all 8 institutional deliverables plus SHA-256 checksums and integrity metadata.
    """
    dcf = dcf_result or compute_synergy_dcf(report)
    generated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    user_label = (watermark_user or "IC_Member").strip()

    base_brand = branding or BrandingConfig()
    effective_watermark = base_brand.watermark_text or f"Issued to {user_label} · {generated_utc[:10]}"
    effective_brand = BrandingConfig(
        firm_name=base_brand.firm_name,
        primary_hex=base_brand.primary_hex,
        accent_hex=base_brand.accent_hex,
        disclaimer=base_brand.disclaimer,
        watermark_text=effective_watermark,
    )

    artifacts: list[tuple[str, bytes]] = [
        ("01_IC_Memo.docx", report_to_docx_bytes(report, dcf_result=dcf)),
        ("02_IC_Pitchbook.pptx", report_to_pptx_bytes(report, dcf_result=dcf, branding=effective_brand)),
        ("03_Synergy_DCF_Model.xlsx", report_to_excel_bytes(report, dcf_result=dcf)),
        ("04_Boardroom_Brief.html", report_to_html_bytes(report, dcf_result=dcf)),
        ("05_Red_Flag_Flash_Report.html", report_to_red_flag_flash_html(report, dcf_result=dcf, branding=effective_brand)),
        ("06_PMI_Jira_Import.csv", pmi_roadmap_to_jira_csv(report)),
        (
            "07_Report_Schema_Payload.json",
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False).encode("utf-8"),
        ),
    ]

    file_manifest: list[dict[str, Any]] = []
    root_hasher = hashlib.sha256()
    for fname, payload in artifacts:
        digest = hashlib.sha256(payload).hexdigest()
        file_manifest.append({
            "filename": fname,
            "sha256": digest,
            "size_bytes": len(payload),
        })
        root_hasher.update(f"{fname}:{digest}\n".encode("utf-8"))

    serialized_trail = _serialize_audit_entries(audit_entries)
    serialized_trail.append({
        "timestamp_utc": generated_utc,
        "action": "AUDIT_PACK_SEALED",
        "actor": user_label,
        "details": f"Sealed 7 institutional deliverables with SHA-256 manifest root for {report.deal.entity_a_name} × {report.deal.entity_b_name}.",
    })

    d = report.deal
    certificate = {
        "certificate_version": "1.0",
        "generated_at_utc": generated_utc,
        "watermark_user": user_label,
        "watermark_stamp": effective_watermark,
        "firm_name": effective_brand.firm_name,
        "compliance_alignment": [
            "SOC 2 Type II (CC6 Logical Access & CC7 System Operations)",
            "ISO/IEC 27001:2022 Annex A.8.15 Logging & Integrity",
            "EU AI Act Article 12 Automatic Record-Keeping",
            "GoBD Tamper-Evident Archival Checksums (SHA-256)",
        ],
        "deal_metadata": {
            "entity_a_name": d.entity_a_name,
            "entity_b_name": d.entity_b_name,
            "deal_type": d.deal_type,
            "deal_value_usd_b": d.deal_value_usd_b,
            "analysis_date": d.analysis_date,
            "recommendation": report.executive_summary.recommendation.value,
            "integration_complexity": report.executive_summary.integration_complexity.value,
            "deterministic_synergy_npv_usd_b": dcf.total_synergy_npv_b,
            "wacc_pct": dcf.assumptions.wacc_pct,
        },
        "evidence_integrity": {
            "citation_count": report.citation_count(),
            "unique_sources_count": len(report.unique_sources()),
            "grounding_ratio": round(report.grounding_ratio(), 4),
            "unique_sources": report.unique_sources(),
            "data_gaps_count": len(report.data_gaps),
        },
        "manifest_root_sha256": root_hasher.hexdigest(),
        "files": file_manifest,
        "audit_trail": serialized_trail,
    }

    cert_bytes = json.dumps(certificate, indent=2, ensure_ascii=False).encode("utf-8")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname, payload in artifacts:
            zf.writestr(fname, payload)
        zf.writestr("08_Audit_Trail_And_Integrity_Certificate.json", cert_bytes)

    return zip_buf.getvalue()
