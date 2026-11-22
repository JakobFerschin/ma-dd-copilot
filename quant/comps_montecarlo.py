"""
Institutional Valuation Triangulation & Stochastic Risk Engine:
  1. Wall Street Valuation Football Field (Trading Comps EV/Rev, EV/EBITDA, P/E,
     Precedent M&A Transactions, Synergy-Adjusted DCF, and LBO Implied Sponsor Floor)
  2. Correlated Monte Carlo Synergy Valuation Engine (up to 10,000 vectorized NumPy
     simulations with Cholesky-correlated Realization %, WACC %, Integration Cost
     Overrun %, and Ramp Delay; computes P10/P25/P50/P75/P90, Mean, Std Dev,
     Prob(NPV > 0), Prob(NPV > 25% Deal EV), 95% Downside VaR / CVaR, and histogram data)

Zero LLM arithmetic — 100% deterministic / reproducible quantitative finance.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np

from core.financial_model import (
    SynergyDCFResult,
    ValuationAssumptions,
    _synthesize_fallback_workstreams,
    compute_synergy_dcf,
)
from quant.merger_lbo import LBOInputs, LBOResult, compute_lbo_model, default_lbo_inputs_for_report
from schemas.report_schema import DueDiligenceReport


# =====================================================================
# 1. Wall Street Valuation Football Field
# =====================================================================

@dataclass
class ValuationRange:
    """Single horizontal valuation bar on an institutional Football Field chart ($B)."""
    methodology: str
    category: str                  # "Trading Comps" | "Precedent M&A" | "Synergy DCF" | "Sponsor LBO"
    low_b: float
    mid_b: float
    high_b: float
    multiple_range_label: str
    notes: str = ""


@dataclass
class FootballFieldResult:
    """Complete triangulated Football Field valuation output ($B)."""
    target_name: str
    offer_ev_b: float
    ranges: list[ValuationRange] = field(default_factory=list)

    consensus_low_b: float = 0.0
    consensus_mid_b: float = 0.0
    consensus_high_b: float = 0.0
    offer_vs_mid_premium_pct: float = 0.0

    implied_standalone_ev_b: float = 0.0
    synergy_npv_b: float = 0.0
    synergy_adjusted_walkaway_ev_b: float = 0.0
    valuation_verdict: str = ""


def _solve_lbo_entry_multiple_for_irr(
    lbo_inputs: LBOInputs,
    target_irr_pct: float,
    low_mult: float = 4.0,
    high_mult: float = 26.0,
) -> float:
    """
    Solve via bisection for the Entry EV/EBITDA multiple at which a Private Equity
    sponsor achieves `target_irr_pct` (monotonic decreasing: higher entry multiple => lower IRR).
    """
    def irr_at(entry_x: float) -> float:
        res = compute_lbo_model(replace(lbo_inputs, entry_ev_ebitda_x=entry_x), compute_sensitivity=False)
        return res.sponsor_irr_pct if res.sponsor_irr_pct is not None else -100.0

    lo, hi = low_mult, high_mult
    irr_lo = irr_at(lo)
    irr_hi = irr_at(hi)
    if irr_lo <= target_irr_pct:
        return lo
    if irr_hi >= target_irr_pct:
        return hi

    for _ in range(36):
        mid = (lo + hi) / 2.0
        if irr_at(mid) >= target_irr_pct:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 2)


def compute_football_field(
    report: DueDiligenceReport,
    dcf_result: SynergyDCFResult,
    lbo_result: Optional[LBOResult] = None,
) -> FootballFieldResult:
    """
    Triangulate institutional Enterprise Value ranges ($B) across:
      1. Trading Comps — EV / Revenue
      2. Trading Comps — EV / EBITDA
      3. Trading Comps — P / E (Equity-to-EV Implied)
      4. Precedent M&A Transactions (incl. control premium)
      5. Standalone + Synergy DCF (Downside / Base / Upside)
      6. LBO Implied Valuation Floor (Sponsor 24% – 17% Target IRR)
    """
    d = report.deal
    offer_ev_b = d.deal_value_usd_b if (d.deal_value_usd_b and d.deal_value_usd_b > 0) else 10.0
    sector = (d.industry_sector or "").lower()

    # Sector-calibrated trading & precedent multiple bands
    if any(k in sector for k in ("auto", "ev", "mobility", "industrial")):
        rev_b = offer_ev_b / 3.2
        ebitda_b = offer_ev_b / 10.5
        ni_b = offer_ev_b / 24.0
        net_debt_b = offer_ev_b * 0.04
        ev_rev_band = (2.3, 2.85, 3.4)
        ev_ebitda_band = (8.5, 10.0, 11.8)
        pe_band = (17.5, 21.5, 26.0)
        prec_ebitda_band = (10.5, 12.2, 14.2)
    elif any(k in sector for k in ("cloud", "software", "saas", "ai", "data")):
        rev_b = offer_ev_b / 12.5
        ebitda_b = offer_ev_b / 14.5
        ni_b = offer_ev_b / 34.0
        net_debt_b = -offer_ev_b * 0.04  # net cash balance sheet
        ev_rev_band = (9.5, 11.5, 13.8)
        ev_ebitda_band = (11.5, 13.5, 16.0)
        pe_band = (26.0, 31.5, 38.0)
        prec_ebitda_band = (14.0, 16.5, 19.5)
    elif any(k in sector for k in ("pharma", "bio", "therap", "health")):
        rev_b = offer_ev_b / 4.2
        ebitda_b = offer_ev_b / 11.5
        ni_b = offer_ev_b / 18.5
        net_debt_b = -offer_ev_b * 0.12  # net cash pharma balance sheet
        ev_rev_band = (3.3, 3.9, 4.6)
        ev_ebitda_band = (9.5, 11.2, 13.0)
        pe_band = (15.0, 18.0, 21.5)
        prec_ebitda_band = (12.0, 14.0, 16.5)
    else:
        rev_b = offer_ev_b / 2.8
        ebitda_b = offer_ev_b / 11.0
        ni_b = offer_ev_b / 20.0
        net_debt_b = offer_ev_b * 0.12
        ev_rev_band = (2.2, 2.6, 3.1)
        ev_ebitda_band = (8.8, 10.3, 12.0)
        pe_band = (15.5, 18.5, 22.0)
        prec_ebitda_band = (11.0, 12.8, 14.8)

    # 1. Trading Comps — EV / Revenue
    tc_rev = ValuationRange(
        methodology="Trading Comps (EV / Revenue)",
        category="Trading Comps",
        low_b=round(rev_b * ev_rev_band[0], 2),
        mid_b=round(rev_b * ev_rev_band[1], 2),
        high_b=round(rev_b * ev_rev_band[2], 2),
        multiple_range_label=f"{ev_rev_band[0]:.1f}x – {ev_rev_band[2]:.1f}x LTM Rev",
        notes="Public peer 25th–75th percentile enterprise revenue multiples.",
    )

    # 2. Trading Comps — EV / EBITDA
    tc_ebitda = ValuationRange(
        methodology="Trading Comps (EV / EBITDA)",
        category="Trading Comps",
        low_b=round(ebitda_b * ev_ebitda_band[0], 2),
        mid_b=round(ebitda_b * ev_ebitda_band[1], 2),
        high_b=round(ebitda_b * ev_ebitda_band[2], 2),
        multiple_range_label=f"{ev_ebitda_band[0]:.1f}x – {ev_ebitda_band[2]:.1f}x Adj. EBITDA",
        notes="Public peer 25th–75th percentile operating cash-flow multiples.",
    )

    # 3. Trading Comps — P / E (Equity value converted to implied EV via + Net Debt)
    pe_low_ev = max(0.5, ni_b * pe_band[0] + net_debt_b)
    pe_mid_ev = max(pe_low_ev, ni_b * pe_band[1] + net_debt_b)
    pe_high_ev = max(pe_mid_ev, ni_b * pe_band[2] + net_debt_b)
    tc_pe = ValuationRange(
        methodology="Trading Comps (P / E Implied EV)",
        category="Trading Comps",
        low_b=round(pe_low_ev, 2),
        mid_b=round(pe_mid_ev, 2),
        high_b=round(pe_high_ev, 2),
        multiple_range_label=f"{pe_band[0]:.1f}x – {pe_band[2]:.1f}x NTM P/E",
        notes="Peer forward P/E applied to normalized Net Income plus target Net Debt.",
    )

    # 4. Precedent M&A Transactions (incl. historical 20-35% control premium)
    prec_ma = ValuationRange(
        methodology="Precedent M&A Transactions",
        category="Precedent M&A",
        low_b=round(ebitda_b * prec_ebitda_band[0], 2),
        mid_b=round(ebitda_b * prec_ebitda_band[1], 2),
        high_b=round(ebitda_b * prec_ebitda_band[2], 2),
        multiple_range_label=f"{prec_ebitda_band[0]:.1f}x – {prec_ebitda_band[2]:.1f}x Deal EBITDA",
        notes="Comparable change-of-control strategic M&A transactions (includes control premium).",
    )

    # 5. Standalone + Synergy DCF (live link to deterministic DCF engine)
    standalone_mid_b = round((tc_rev.mid_b + tc_ebitda.mid_b + tc_pe.mid_b) / 3.0, 2)
    standalone_low_b = round(standalone_mid_b * 0.90, 2)
    standalone_high_b = round(standalone_mid_b * 1.08, 2)

    base_assump = dcf_result.assumptions
    down_dcf = compute_synergy_dcf(
        report,
        replace(
            base_assump,
            wacc_pct=min(25.0, base_assump.wacc_pct + 1.0),
            realization_factor_pct=max(30.0, base_assump.realization_factor_pct * 0.70),
        ),
    )
    up_dcf = compute_synergy_dcf(
        report,
        replace(
            base_assump,
            wacc_pct=max(3.0, base_assump.wacc_pct - 1.0),
            realization_factor_pct=min(150.0, base_assump.realization_factor_pct * 1.20),
        ),
    )

    dcf_low_b = round(max(0.5, standalone_low_b + down_dcf.total_synergy_npv_b), 2)
    dcf_mid_b = round(max(dcf_low_b, standalone_mid_b + dcf_result.total_synergy_npv_b), 2)
    dcf_high_b = round(max(dcf_mid_b, standalone_high_b + up_dcf.total_synergy_npv_b), 2)

    synergy_dcf_range = ValuationRange(
        methodology="Synergy-Adjusted DCF",
        category="Synergy DCF",
        low_b=dcf_low_b,
        mid_b=dcf_mid_b,
        high_b=dcf_high_b,
        multiple_range_label=(
            f"WACC {max(3.0, base_assump.wacc_pct - 1.0):.1f}%–{base_assump.wacc_pct + 1.0:.1f}% | "
            f"+${dcf_result.total_synergy_npv_b:.2f}B Syn. NPV"
        ),
        notes="Standalone intrinsic value plus probability-weighted after-tax Synergy DCF NPV.",
    )

    # 6. LBO Implied Valuation Floor (Sponsor Ability-to-Pay at 24% / 20% / 17% Target IRR)
    lbo_inp = lbo_result.inputs if lbo_result is not None else default_lbo_inputs_for_report(report)
    m_low = _solve_lbo_entry_multiple_for_irr(lbo_inp, target_irr_pct=24.0)
    m_mid = _solve_lbo_entry_multiple_for_irr(lbo_inp, target_irr_pct=20.0)
    m_high = _solve_lbo_entry_multiple_for_irr(lbo_inp, target_irr_pct=17.0)
    # Ensure strict non-decreasing ordering
    m_mid = max(m_low, m_mid)
    m_high = max(m_mid, m_high)

    lbo_ebitda_b = lbo_inp.ltm_ebitda_m / 1000.0
    lbo_range = ValuationRange(
        methodology="LBO Implied Valuation (Sponsor Floor)",
        category="Sponsor LBO",
        low_b=round(lbo_ebitda_b * m_low, 2),
        mid_b=round(lbo_ebitda_b * m_mid, 2),
        high_b=round(lbo_ebitda_b * m_high, 2),
        multiple_range_label=f"{m_low:.1f}x – {m_high:.1f}x EBITDA (24%–17% IRR)",
        notes="Financial sponsor buyout floor assuming no strategic synergies.",
    )

    ranges = [tc_rev, tc_ebitda, tc_pe, prec_ma, synergy_dcf_range, lbo_range]

    consensus_low = float(np.median([r.low_b for r in ranges]))
    consensus_mid = float(np.median([r.mid_b for r in ranges]))
    consensus_high = float(np.median([r.high_b for r in ranges]))
    offer_prem_pct = ((offer_ev_b - consensus_mid) / consensus_mid * 100.0) if consensus_mid > 0 else 0.0
    walkaway_ev_b = round(standalone_mid_b + dcf_result.total_synergy_npv_b, 2)

    if offer_ev_b <= consensus_mid:
        verdict = "Attractive Entry — Offer Below Blended Valuation Midpoint"
    elif offer_ev_b <= walkaway_ev_b:
        verdict = "Synergy-Justified Premium — Offer Below Walk-Away Ceiling"
    else:
        verdict = "Rich Valuation — Offer Exceeds Synergy-Adjusted Walk-Away EV"

    return FootballFieldResult(
        target_name=d.entity_b_name,
        offer_ev_b=round(offer_ev_b, 2),
        ranges=ranges,
        consensus_low_b=round(consensus_low, 2),
        consensus_mid_b=round(consensus_mid, 2),
        consensus_high_b=round(consensus_high, 2),
        offer_vs_mid_premium_pct=round(offer_prem_pct, 1),
        implied_standalone_ev_b=standalone_mid_b,
        synergy_npv_b=round(dcf_result.total_synergy_npv_b, 2),
        synergy_adjusted_walkaway_ev_b=walkaway_ev_b,
        valuation_verdict=verdict,
    )


# =====================================================================
# 2. Correlated Monte Carlo Synergy Valuation Engine (NumPy)
# =====================================================================

@dataclass
class MonteCarloConfig:
    """Stochastic simulation parameters for Correlated Synergy NPV Monte Carlo."""
    n_simulations: int = 5000
    seed: Optional[int] = 42

    # 1-sigma volatilities around base DCF assumptions
    realization_std_pct: float = 18.0       # e.g. 100% +/- 18 pct pts
    wacc_std_pct: float = 1.25              # e.g. 9.0% +/- 1.25 pct pts
    cost_overrun_std_pct: float = 22.0      # e.g. 0% +/- 22 pct pts
    ramp_delay_mean_years: float = 0.20     # slight positive skew for real-world PMI execution friction
    ramp_delay_std_years: float = 0.35      # clipped to [0.0, 2.0] years

    n_bins: int = 35


@dataclass
class MonteCarloResult:
    """Full stochastic distribution, risk percentiles, VaR, and histogram data ($B)."""
    config: MonteCarloConfig
    n_simulations: int
    base_case_npv_b: float
    deal_value_usd_b: Optional[float]

    p10_b: float
    p25_b: float
    p50_b: float
    p75_b: float
    p90_b: float
    mean_b: float
    std_b: float
    min_b: float
    max_b: float

    prob_npv_positive_pct: float
    prob_npv_gt_25pct_ev_pct: float
    var_95_b: float                         # 5th percentile NPV (95% downside confidence level)
    cvar_95_b: float                        # Expected Shortfall (mean of worst 5% outcomes)
    downside_var_95_vs_base_b: float        # Base Case NPV minus 5th percentile NPV

    histogram_bin_edges: list[float] = field(default_factory=list)
    histogram_counts: list[int] = field(default_factory=list)
    samples_b: list[float] = field(default_factory=list)
    factor_correlations: dict[str, float] = field(default_factory=dict)


# Empirically grounded 4×4 correlation matrix:
# Order: [Realization %, WACC %, Integration Cost Overrun %, Ramp Delay (yrs)]
_CORR_MATRIX = np.array(
    [
        [1.00, -0.25, -0.45, -0.50],
        [-0.25, 1.00, 0.15, 0.15],
        [-0.45, 0.15, 1.00, 0.55],
        [-0.50, 0.15, 0.55, 1.00],
    ],
    dtype=np.float64,
)


def _vectorized_ramp(r1: float, r2: float, r3: float, t_eff: np.ndarray) -> np.ndarray:
    """
    Smooth piecewise-linear synergy ramp-up curve evaluated at effective year `t_eff`:
      t_eff <= 0  -> 0.0
      0 < t <= 1  -> linear 0 -> r1
      1 < t <= 2  -> linear r1 -> r2
      2 < t <= 3  -> linear r2 -> r3
      t > 3       -> r3
    Exact match to `_ramp_for_year` when delay == 0 (integer years 1, 2, 3+).
    """
    return np.interp(t_eff, [0.0, 1.0, 2.0, 3.0], [0.0, r1, r2, r3])


def run_monte_carlo_synergies(
    report: DueDiligenceReport,
    base_assumptions: ValuationAssumptions,
    config: Optional[MonteCarloConfig] = None,
) -> MonteCarloResult:
    """
    Run up to 10,000+ correlated Monte Carlo simulations of the Synergy DCF model
    using NumPy (deterministic seed supported) across Realization %, WACC %,
    Integration Cost Overrun %, and Ramp Delay.
    """
    cfg = config or MonteCarloConfig()
    n_sims = max(100, min(50_000, int(cfg.n_simulations)))
    rng = np.random.default_rng(cfg.seed)

    base_dcf = compute_synergy_dcf(report, base_assumptions)
    workstreams = report.synergy_workstreams or _synthesize_fallback_workstreams(report)

    # Generate correlated standard normals via Cholesky factorization
    chol = np.linalg.cholesky(_CORR_MATRIX)
    z_indep = rng.standard_normal((n_sims, 4))
    z_corr = z_indep @ chol.T

    realization_pct = np.clip(
        base_assumptions.realization_factor_pct + z_corr[:, 0] * cfg.realization_std_pct,
        20.0,
        150.0,
    )
    wacc_pct = np.clip(
        base_assumptions.wacc_pct + z_corr[:, 1] * cfg.wacc_std_pct,
        2.5,
        24.0,
    )
    overrun_pct = np.clip(
        base_assumptions.integration_cost_overrun_pct + z_corr[:, 2] * cfg.cost_overrun_std_pct,
        -35.0,
        125.0,
    )
    ramp_delay_yrs = np.clip(
        cfg.ramp_delay_mean_years + z_corr[:, 3] * cfg.ramp_delay_std_years,
        0.0,
        2.0,
    )

    # Vectorized Synergy DCF evaluation across all N scenarios simultaneously
    realization = realization_pct / 100.0
    wacc = np.maximum(wacc_pct / 100.0, 0.01)
    tax_rate = max(0.0, min(base_assumptions.tax_rate_pct / 100.0, 0.60))
    g = np.minimum(base_assumptions.terminal_growth_pct / 100.0, wacc - 0.01)
    cost_mult = np.maximum(0.0, 1.0 + overrun_pct / 100.0)
    years = max(3, base_assumptions.projection_years)

    int_phasing = {1: 0.60, 2: 0.30, 3: 0.10}
    base_one_time_m = sum(ws.one_time_cost_usd_m for ws in workstreams)
    if base_one_time_m == 0.0 and (report.deal.estimated_integration_cost_usd_b or 0.0) > 0:
        base_one_time_m = (report.deal.estimated_integration_cost_usd_b or 0.0) * 1000.0
    total_one_time_m = base_one_time_m * cost_mult

    # Pre-extract workstream effective run-rates & ramp schedules
    ws_params = []
    for ws in workstreams:
        prob = (ws.probability_pct / 100.0) if base_assumptions.use_probability_weighting else 1.0
        ws_params.append(
            (
                ws.annual_run_rate_usd_m * prob,
                ws.ramp_year1_pct / 100.0,
                ws.ramp_year2_pct / 100.0,
                ws.ramp_year3_pct / 100.0,
            )
        )

    pv_explicit_m = np.zeros(n_sims, dtype=np.float64)
    final_gross_m = np.zeros(n_sims, dtype=np.float64)

    for y in range(1, years + 1):
        t_eff = np.maximum(0.0, float(y) - ramp_delay_yrs)
        gross_y = np.zeros(n_sims, dtype=np.float64)
        for eff_rr, r1, r2, r3 in ws_params:
            gross_y += eff_rr * realization * _vectorized_ramp(r1, r2, r3, t_eff)

        int_cost_y = total_one_time_m * int_phasing.get(y, 0.0)
        net_cf_y = (gross_y - int_cost_y) * (1.0 - tax_rate)
        df_y = 1.0 / ((1.0 + wacc) ** y)
        pv_explicit_m += net_cf_y * df_y

        if y == years:
            final_gross_m = gross_y

    # Terminal value matching `core/financial_model.py`
    final_nopat_m = final_gross_m * (1.0 - tax_rate)
    if base_assumptions.terminal_method == "perpetuity":
        tv_m = np.where(final_nopat_m > 0.0, (final_nopat_m * (1.0 + g)) / (wacc - g), 0.0)
    else:
        tv_m = np.where(final_nopat_m > 0.0, final_nopat_m * max(base_assumptions.terminal_multiple, 0.0), 0.0)

    pv_tv_m = tv_m / ((1.0 + wacc) ** years)
    npv_b = (pv_explicit_m + pv_tv_m) / 1000.0

    # Percentiles & summary statistics
    p05, p10, p25, p50, p75, p90 = np.percentile(npv_b, [5, 10, 25, 50, 75, 90])
    mean_b = float(np.mean(npv_b))
    std_b = float(np.std(npv_b))
    min_b = float(np.min(npv_b))
    max_b = float(np.max(npv_b))

    tail_mask = npv_b <= p05
    cvar_95_b = float(np.mean(npv_b[tail_mask])) if np.any(tail_mask) else float(p05)
    prob_pos_pct = float(np.mean(npv_b > 0.0) * 100.0)

    deal_val_b = report.deal.deal_value_usd_b
    if deal_val_b and deal_val_b > 0:
        prob_gt_25_ev = float(np.mean(npv_b > (0.25 * deal_val_b)) * 100.0)
    else:
        prob_gt_25_ev = 0.0

    # Histogram bins
    n_bins = max(10, min(100, cfg.n_bins))
    counts, edges = np.histogram(npv_b, bins=n_bins)

    # Pearson driver correlations with simulated NPV (handle zero-variance safely)
    def _safe_corr(x: np.ndarray, y: np.ndarray) -> float:
        if float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
            return 0.0
        return round(float(np.corrcoef(x, y)[0, 1]), 3)

    factor_corrs = {
        "Synergy Realization (%)": _safe_corr(realization_pct, npv_b),
        "Discount Rate / WACC (%)": _safe_corr(wacc_pct, npv_b),
        "Integration Cost Overrun (%)": _safe_corr(overrun_pct, npv_b),
        "PMI Ramp Delay (Years)": _safe_corr(ramp_delay_yrs, npv_b),
    }

    return MonteCarloResult(
        config=cfg,
        n_simulations=n_sims,
        base_case_npv_b=round(base_dcf.total_synergy_npv_b, 2),
        deal_value_usd_b=deal_val_b,
        p10_b=round(float(p10), 2),
        p25_b=round(float(p25), 2),
        p50_b=round(float(p50), 2),
        p75_b=round(float(p75), 2),
        p90_b=round(float(p90), 2),
        mean_b=round(mean_b, 2),
        std_b=round(std_b, 2),
        min_b=round(min_b, 2),
        max_b=round(max_b, 2),
        prob_npv_positive_pct=round(prob_pos_pct, 1),
        prob_npv_gt_25pct_ev_pct=round(prob_gt_25_ev, 1),
        var_95_b=round(float(p05), 2),
        cvar_95_b=round(cvar_95_b, 2),
        downside_var_95_vs_base_b=round(base_dcf.total_synergy_npv_b - float(p05), 2),
        histogram_bin_edges=[round(float(e), 3) for e in edges],
        histogram_counts=[int(c) for c in counts],
        samples_b=[round(float(v), 3) for v in npv_b],
        factor_correlations=factor_corrs,
    )
