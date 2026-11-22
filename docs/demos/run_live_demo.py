#!/usr/bin/env python3
"""
1-Command Enterprise M&A Due Diligence Copilot — End-to-End Demo Script.

Demonstrates the full 10-pillar pipeline offline (zero API spend) in ~2 seconds:
  1. Loads Volkswagen AG × Rivian Automotive ($17.5B EV) institutional case study
  2. Runs 6-Agent Specialist Diligence + Adversarial Red-Team Critic + Numerical Fact-Check
  3. Executes Deterministic Synergy DCF + 10,000-Run Monte Carlo + Valuation Football Field
  4. Runs 3-Statement Accretion/Dilution Merger Model + PE LBO/IRR + QoE Normalized EBITDA
  5. Runs Legal CoC Scanner + ABA/CMS SPA Benchmark + Multi-Jurisdiction Antitrust/CFIUS
  6. Seals a Tamper-Evident SHA-256 WORM Audit Pack ZIP (PPTX + DOCX + Excel + HTML + Jira CSV)

Usage:
    python docs/demos/run_live_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.orchestrator import evaluate_report_quality, run_multi_agent_diligence
from core.demo_cases import DEMO_CASES
from core.financial_model import ValuationAssumptions, compute_synergy_dcf
from legal import assess_antitrust_and_fdi, benchmark_spa_terms, scan_change_of_control
from quant import (
    MonteCarloConfig,
    compute_football_field,
    compute_lbo_model,
    compute_merger_model,
    compute_qoe_bridge,
    default_lbo_inputs_for_report,
    default_merger_inputs_for_report,
    default_qoe_for_report,
    run_monte_carlo_synergies,
)
from utils.audit_pack import build_audit_pack_zip
from utils.pptx_exporter import BrandingConfig


def main() -> None:
    print("=" * 78)
    print("  M&A DUE DILIGENCE COPILOT — ENTERPRISE v2.0 END-TO-END DEMO")
    print("=" * 78)

    report = DEMO_CASES["vw_rivian"]["builder"]()
    d = report.deal
    print(f"\n[1] Transaction: {d.entity_a_name} × {d.entity_b_name} (${d.deal_value_usd_b:.1f}B EV)")
    print(f"    Recommendation: {report.executive_summary.recommendation.value} | Conviction: {d.overall_conviction_score}/100")

    scorecard = evaluate_report_quality(report)
    ma = run_multi_agent_diligence(report)
    print(f"\n[2] Multi-Agent & Red-Team Audit:")
    print(f"    Institutional Grade : {scorecard.institutional_grade} ({scorecard.overall_score:.0f}/100) | Grounding: {report.grounding_ratio()*100:.0f}%")
    print(f"    Specialists Run     : {len(ma.specialist_findings)} domain agents")
    print(f"    Red-Team Bear NPV   : ${ma.red_team.haircut_synergy_npv_usd_b:.2f}B ({ma.red_team.implied_npv_downside_pct:+.1f}% vs Base ${ma.red_team.base_synergy_npv_usd_b:.2f}B)")

    dcf = compute_synergy_dcf(report)
    mc = run_monte_carlo_synergies(report, ValuationAssumptions.from_report(report), MonteCarloConfig(n_simulations=10000, seed=42))
    lbo = compute_lbo_model(default_lbo_inputs_for_report(report), compute_sensitivity=False)
    ff = compute_football_field(report, dcf, lbo_result=lbo)
    merger = compute_merger_model(default_merger_inputs_for_report(report))
    qoe_in = default_qoe_for_report(report)
    qoe = compute_qoe_bridge(qoe_in.reported_ebitda_m, qoe_in.revenue_m, qoe_in.adjustments, qoe_in.headline_ev_m)

    print(f"\n[3] Deterministic Quant Valuation Suite:")
    print(f"    Synergy DCF NPV     : ${dcf.total_synergy_npv_b:.2f}B ({dcf.value_creation_pct_of_deal:.1f}% of EV) | Payback: {dcf.payback_years:.2f} yrs")
    print(f"    Monte Carlo (10k)   : P10=${mc.p10_b:.2f}B | Median=${mc.p50_b:.2f}B | P90=${mc.p90_b:.2f}B | 95% VaR=${mc.var_95_b:.2f}B")
    print(f"    Football Field Mid  : ${ff.consensus_mid_b:.2f}B | Walkaway EV: ${ff.synergy_adjusted_walkaway_ev_b:.2f}B")
    print(f"    3-Stmt Merger EPS   : Y1 {merger.year1_accretion_pct:+.2f}% | Y3 {merger.year3_accretion_pct:+.2f}% | Goodwill: ${merger.goodwill_created_m/1000:.2f}B")
    print(f"    PE LBO Returns      : Sponsor IRR {lbo.sponsor_irr_pct:.1f}% | MOIC {lbo.moic_x:.2f}x")
    print(f"    QoE Adj. EBITDA     : ${qoe.adjusted_ebitda_m:,.0f}M (Scrutiny Score: {qoe.scrutiny_score}/100)")

    coc = scan_change_of_control([], report=report)
    spa = benchmark_spa_terms((d.deal_value_usd_b or 17.5) * 1000.0)
    anti = assess_antitrust_and_fdi(320.0, 5.0, 22.0, d.industry_sector or "Automotive", target_country="US", acquirer_country="DE")
    print(f"\n[4] Legal, CoC & Antitrust Screening:")
    print(f"    CoC Flags           : {len(coc)} clause(s) | Est. Revenue at Risk: ${sum(f.revenue_at_risk_usd_m or 0 for f in coc):,.0f}M")
    print(f"    SPA Buyer Score     : {spa.buyer_protection_score}/100 ({spa.overall_stance})")
    print(f"    Antitrust & CFIUS   : {anti.mandatory_filings_count} mandatory filings | Phase II Prob: {anti.phase_2_probability_pct:.0f}% | Est: {anti.estimated_clearance_months} mo.")

    pack_bytes = build_audit_pack_zip(
        report,
        dcf_result=dcf,
        branding=BrandingConfig(firm_name="M&A Due Diligence Copilot Advisory"),
        watermark_user="IC_Demo_Runner",
    )
    out_zip = ROOT / "data" / "outputs" / "Demo_Audit_Pack_VW_Rivian.zip"
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    out_zip.write_bytes(pack_bytes)
    print(f"\n[5] Sealed 8-File SHA-256 Boardroom Audit Pack -> {out_zip} ({len(pack_bytes):,} bytes)")
    print("=" * 78)


if __name__ == "__main__":
    main()
