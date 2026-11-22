"""
Deterministic Institutional Synergy DCF & Valuation Engine.

Zero LLM arithmetic — every dollar of Synergy NPV, cash flow phasing, tax impact,
terminal value, payback period, IRR, and WACC × Realization sensitivity is computed
deterministically in Python so Investment Committees can stress-test assumptions
live without hallucination risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from schemas.report_schema import DueDiligenceReport, SynergyType, SynergyWorkstream


@dataclass
class ValuationAssumptions:
    """Interactive DCF parameters controllable from the Investment Committee UI."""
    wacc_pct: float = 9.0
    tax_rate_pct: float = 25.0
    terminal_growth_pct: float = 2.0
    realization_factor_pct: float = 100.0        # global multiplier on workstream capture (e.g. 80% downside)
    integration_cost_overrun_pct: float = 0.0    # e.g. +20% integration cost stress
    projection_years: int = 5
    use_probability_weighting: bool = True
    terminal_method: str = "multiple"            # "multiple" (conservative 4.5x synergy fade) | "perpetuity" (Gordon Growth)
    terminal_multiple: float = 4.5               # steady-state after-tax synergy fade multiple (Years 6+)

    @classmethod
    def from_report(cls, report: DueDiligenceReport) -> "ValuationAssumptions":
        d = report.deal
        horizon_years = max(3, min(7, round((d.time_horizon_months or 36) / 12) + 2))
        return cls(
            wacc_pct=d.wacc_pct,
            tax_rate_pct=d.tax_rate_pct,
            terminal_growth_pct=d.terminal_growth_pct,
            projection_years=horizon_years,
        )


@dataclass
class AnnualCashFlowRow:
    year: int
    revenue_synergy_m: float
    cost_synergy_m: float
    capital_synergy_m: float
    gross_synergy_m: float
    integration_cost_m: float
    ebt_impact_m: float
    tax_impact_m: float
    net_cash_flow_m: float
    discount_factor: float
    pv_cash_flow_m: float
    cumulative_cf_m: float
    cumulative_pv_m: float


@dataclass
class SynergyDCFResult:
    assumptions: ValuationAssumptions
    deal_value_usd_b: Optional[float]
    annual_rows: list[AnnualCashFlowRow] = field(default_factory=list)

    # Headline outputs (USD billions)
    steady_state_revenue_synergy_b: float = 0.0
    steady_state_cost_synergy_b: float = 0.0
    steady_state_total_synergy_b: float = 0.0
    total_integration_cost_b: float = 0.0

    pv_explicit_period_b: float = 0.0
    terminal_value_b: float = 0.0
    pv_terminal_value_b: float = 0.0
    total_synergy_npv_b: float = 0.0

    # Bridge components (PV after-tax, USD billions)
    pv_revenue_synergies_b: float = 0.0
    pv_cost_synergies_b: float = 0.0
    pv_capital_synergies_b: float = 0.0
    pv_integration_costs_b: float = 0.0

    # Return & capital efficiency metrics
    value_creation_pct_of_deal: Optional[float] = None
    payback_years: Optional[float] = None
    discounted_payback_years: Optional[float] = None
    synergy_irr_pct: Optional[float] = None
    npv_to_integration_cost_ratio: Optional[float] = None


def _synthesize_fallback_workstreams(report: DueDiligenceReport) -> list[SynergyWorkstream]:
    """
    If a legacy or minimal report lacks itemized `synergy_workstreams`, build
    clean top-down workstreams from the DealMeta headline numbers so the DCF
    engine always works seamlessly.
    """
    d = report.deal
    items: list[SynergyWorkstream] = []
    rev_m = (d.estimated_revenue_synergy_usd_b or 0.0) * 1000.0
    cost_m = (d.estimated_cost_synergy_usd_b or 0.0) * 1000.0
    int_m = (d.estimated_integration_cost_usd_b or 0.0) * 1000.0

    if rev_m > 0:
        items.append(
            SynergyWorkstream(
                name="Commercial & Cross-Selling Revenue Synergies",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=rev_m,
                one_time_cost_usd_m=round(int_m * 0.35, 1),
                probability_pct=70.0,
                ramp_year1_pct=20.0,
                ramp_year2_pct=60.0,
                ramp_year3_pct=100.0,
                owner="Chief Commercial Officer",
                rationale="Derived from headline run-rate revenue synergy estimate.",
            )
        )
    if cost_m > 0:
        items.append(
            SynergyWorkstream(
                name="Operational, Procurement & SG&A Cost Synergies",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=cost_m,
                one_time_cost_usd_m=round(int_m * 0.65, 1),
                probability_pct=85.0,
                ramp_year1_pct=35.0,
                ramp_year2_pct=75.0,
                ramp_year3_pct=100.0,
                owner="Chief Operating Officer",
                rationale="Derived from headline run-rate cost synergy estimate.",
            )
        )
    return items


def _ramp_for_year(ws: SynergyWorkstream, year: int) -> float:
    """Return ramp-up fraction (0.0..1.0) for a given projection year (1-indexed)."""
    if year == 1:
        return ws.ramp_year1_pct / 100.0
    if year == 2:
        return ws.ramp_year2_pct / 100.0
    return ws.ramp_year3_pct / 100.0


def _compute_irr(cash_flows: list[float]) -> Optional[float]:
    """
    Compute IRR via robust bisection on annual cash flows [CF_0, CF_1, ..., CF_n].
    Returns IRR as percentage (e.g. 28.4) or None if no sign change exists.
    """
    if len(cash_flows) < 2:
        return None
    has_neg = any(cf < 0 for cf in cash_flows)
    has_pos = any(cf > 0 for cf in cash_flows)
    if not (has_neg and has_pos):
        return None

    def npv_at(rate: float) -> float:
        return sum(cf / ((1.0 + rate) ** t) for t, cf in enumerate(cash_flows))

    low, high = -0.85, 5.0
    f_low, f_high = npv_at(low), npv_at(high)
    if f_low * f_high > 0:
        return None

    for _ in range(80):
        mid = (low + high) / 2.0
        f_mid = npv_at(mid)
        if abs(f_mid) < 1e-6:
            return round(mid * 100.0, 1)
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return round(((low + high) / 2.0) * 100.0, 1)


def compute_synergy_dcf(
    report: DueDiligenceReport,
    assumptions: Optional[ValuationAssumptions] = None,
) -> SynergyDCFResult:
    """
    Run the full Synergy DCF model for a DueDiligenceReport under given assumptions.
    All internal calculations run in USD millions, converted to USD billions for summaries.
    """
    if assumptions is None:
        assumptions = ValuationAssumptions.from_report(report)

    workstreams = report.synergy_workstreams or _synthesize_fallback_workstreams(report)
    wacc = max(assumptions.wacc_pct / 100.0, 0.01)
    tax_rate = max(0.0, min(assumptions.tax_rate_pct / 100.0, 0.60))
    # Cap terminal growth strictly below WACC to guarantee convergent Gordon Growth
    g = min(assumptions.terminal_growth_pct / 100.0, wacc - 0.01)
    realization = max(0.0, assumptions.realization_factor_pct / 100.0)
    cost_mult = max(0.0, 1.0 + assumptions.integration_cost_overrun_pct / 100.0)
    years = max(3, assumptions.projection_years)

    # Integration cost phasing: 60% Year 1, 30% Year 2, 10% Year 3
    int_phasing = {1: 0.60, 2: 0.30, 3: 0.10}

    total_one_time_cost_m = sum(ws.one_time_cost_usd_m for ws in workstreams) * cost_mult
    if total_one_time_cost_m == 0.0 and (report.deal.estimated_integration_cost_usd_b or 0.0) > 0:
        total_one_time_cost_m = (report.deal.estimated_integration_cost_usd_b or 0.0) * 1000.0 * cost_mult

    rows: list[AnnualCashFlowRow] = []
    cum_cf = 0.0
    cum_pv = 0.0
    pv_rev_after_tax_m = 0.0
    pv_cost_after_tax_m = 0.0
    pv_cap_after_tax_m = 0.0
    pv_int_after_tax_m = 0.0

    # For IRR, treat 40% of Year-1 integration cost as Day-0/Closing outlay so IRR is realistic
    day0_outlay_m = total_one_time_cost_m * 0.35
    rem_cost_m = total_one_time_cost_m - day0_outlay_m
    irr_series: list[float] = [-day0_outlay_m * (1.0 - tax_rate)] if day0_outlay_m > 0 else [0.0]

    steady_rev_m = 0.0
    steady_cost_m = 0.0
    steady_cap_m = 0.0

    for ws in workstreams:
        prob = (ws.probability_pct / 100.0) if assumptions.use_probability_weighting else 1.0
        eff = ws.annual_run_rate_usd_m * prob * realization
        if ws.synergy_type == SynergyType.REVENUE:
            steady_rev_m += eff
        elif ws.synergy_type == SynergyType.COST:
            steady_cost_m += eff
        else:
            steady_cap_m += eff

    payback_years: Optional[float] = None
    discounted_payback_years: Optional[float] = None

    for y in range(1, years + 1):
        rev_y = 0.0
        cost_y = 0.0
        cap_y = 0.0
        for ws in workstreams:
            prob = (ws.probability_pct / 100.0) if assumptions.use_probability_weighting else 1.0
            capture = ws.annual_run_rate_usd_m * prob * realization * _ramp_for_year(ws, y)
            if ws.synergy_type == SynergyType.REVENUE:
                rev_y += capture
            elif ws.synergy_type == SynergyType.COST:
                cost_y += capture
            else:
                cap_y += capture

        gross_y = rev_y + cost_y + cap_y
        int_cost_y = total_one_time_cost_m * int_phasing.get(y, 0.0)
        ebt_y = gross_y - int_cost_y
        tax_y = ebt_y * tax_rate
        net_cf_y = ebt_y - tax_y
        df = 1.0 / ((1.0 + wacc) ** y)
        pv_y = net_cf_y * df

        prev_cum_cf = cum_cf
        prev_cum_pv = cum_pv
        cum_cf += net_cf_y
        cum_pv += pv_y

        # Track payback crossing (simple & discounted)
        if payback_years is None and prev_cum_cf < 0 <= cum_cf and net_cf_y > 0:
            payback_years = round((y - 1) + abs(prev_cum_cf) / net_cf_y, 2)
        if discounted_payback_years is None and prev_cum_pv < 0 <= cum_pv and pv_y > 0:
            discounted_payback_years = round((y - 1) + abs(prev_cum_pv) / pv_y, 2)

        pv_rev_after_tax_m += rev_y * (1.0 - tax_rate) * df
        pv_cost_after_tax_m += cost_y * (1.0 - tax_rate) * df
        pv_cap_after_tax_m += cap_y * (1.0 - tax_rate) * df
        pv_int_after_tax_m += int_cost_y * (1.0 - tax_rate) * df

        # IRR series adjusts remaining integration cost after day-0 outlay
        irr_int_y = rem_cost_m * int_phasing.get(y, 0.0)
        irr_series.append((gross_y - irr_int_y) * (1.0 - tax_rate))

        rows.append(
            AnnualCashFlowRow(
                year=y,
                revenue_synergy_m=round(rev_y, 1),
                cost_synergy_m=round(cost_y, 1),
                capital_synergy_m=round(cap_y, 1),
                gross_synergy_m=round(gross_y, 1),
                integration_cost_m=round(int_cost_y, 1),
                ebt_impact_m=round(ebt_y, 1),
                tax_impact_m=round(tax_y, 1),
                net_cash_flow_m=round(net_cf_y, 1),
                discount_factor=round(df, 4),
                pv_cash_flow_m=round(pv_y, 1),
                cumulative_cf_m=round(cum_cf, 1),
                cumulative_pv_m=round(cum_pv, 1),
            )
        )

    # If Year 1 is already cash-flow positive, payback is immediate (< 1 yr)
    if payback_years is None and rows and rows[0].cumulative_cf_m > 0 and total_one_time_cost_m > 0:
        first_net = max(rows[0].gross_synergy_m * (1.0 - tax_rate), 1e-6)
        payback_years = round(min(1.0, (total_one_time_cost_m * (1.0 - tax_rate)) / first_net), 2)
    if discounted_payback_years is None and rows and rows[0].cumulative_pv_m > 0 and total_one_time_cost_m > 0:
        discounted_payback_years = payback_years

    # Terminal value of steady-state after-tax synergies:
    # Institutional M&A default ("multiple") uses a conservative 4.5x steady-state NOPAT fade
    # multiple (reflecting competitive erosion post-Year 5); "perpetuity" uses Gordon Growth.
    final_gross_m = rows[-1].gross_synergy_m if rows else 0.0
    final_nopat_m = final_gross_m * (1.0 - tax_rate)
    if final_nopat_m > 0:
        if assumptions.terminal_method == "perpetuity":
            terminal_value_m = (final_nopat_m * (1.0 + g)) / (wacc - g)
        else:
            terminal_value_m = final_nopat_m * max(assumptions.terminal_multiple, 0.0)
    else:
        terminal_value_m = 0.0
    final_df = 1.0 / ((1.0 + wacc) ** years)
    pv_terminal_m = terminal_value_m * final_df

    # Allocate PV of terminal value proportionally across synergy types for the valuation bridge
    if final_gross_m > 0:
        last_row = rows[-1]
        pv_rev_after_tax_m += pv_terminal_m * (last_row.revenue_synergy_m / final_gross_m)
        pv_cost_after_tax_m += pv_terminal_m * (last_row.cost_synergy_m / final_gross_m)
        pv_cap_after_tax_m += pv_terminal_m * (last_row.capital_synergy_m / final_gross_m)

    pv_explicit_m = sum(r.pv_cash_flow_m for r in rows)
    total_npv_m = pv_explicit_m + pv_terminal_m

    # Add terminal value to final IRR cash flow for realistic synergy project IRR
    if len(irr_series) > 1 and terminal_value_m > 0:
        irr_series[-1] += terminal_value_m * 0.5  # conservative 50% TV credit in IRR

    deal_val_b = report.deal.deal_value_usd_b
    total_npv_b = round(total_npv_m / 1000.0, 2)
    total_int_b = round(total_one_time_cost_m / 1000.0, 2)
    vc_pct = round((total_npv_b / deal_val_b) * 100.0, 1) if deal_val_b and deal_val_b > 0 else None
    npv_cost_ratio = round(total_npv_b / total_int_b, 2) if total_int_b > 0 else None

    return SynergyDCFResult(
        assumptions=assumptions,
        deal_value_usd_b=deal_val_b,
        annual_rows=rows,
        steady_state_revenue_synergy_b=round(steady_rev_m / 1000.0, 2),
        steady_state_cost_synergy_b=round(steady_cost_m / 1000.0, 2),
        steady_state_total_synergy_b=round((steady_rev_m + steady_cost_m + steady_cap_m) / 1000.0, 2),
        total_integration_cost_b=total_int_b,
        pv_explicit_period_b=round(pv_explicit_m / 1000.0, 2),
        terminal_value_b=round(terminal_value_m / 1000.0, 2),
        pv_terminal_value_b=round(pv_terminal_m / 1000.0, 2),
        total_synergy_npv_b=total_npv_b,
        pv_revenue_synergies_b=round(pv_rev_after_tax_m / 1000.0, 2),
        pv_cost_synergies_b=round(pv_cost_after_tax_m / 1000.0, 2),
        pv_capital_synergies_b=round(pv_cap_after_tax_m / 1000.0, 2),
        pv_integration_costs_b=round(pv_int_after_tax_m / 1000.0, 2),
        value_creation_pct_of_deal=vc_pct,
        payback_years=payback_years,
        discounted_payback_years=discounted_payback_years,
        synergy_irr_pct=_compute_irr(irr_series),
        npv_to_integration_cost_ratio=npv_cost_ratio,
    )


def compute_sensitivity_matrix(
    report: DueDiligenceReport,
    base: ValuationAssumptions,
    wacc_steps: tuple[float, ...] = (-2.0, -1.0, 0.0, 1.0, 2.0),
    realization_levels: tuple[float, ...] = (60.0, 80.0, 100.0, 115.0, 130.0),
) -> tuple[list[float], list[float], list[list[float]]]:
    """
    Build a 2D Investment Banking sensitivity table:
      Rows    = WACC values (%)
      Columns = Synergy Realization Rate (%)
      Cells   = Total Synergy NPV ($B)
    """
    wacc_values = [round(max(2.0, base.wacc_pct + delta), 2) for delta in wacc_steps]
    real_values = [round(r, 1) for r in realization_levels]
    grid: list[list[float]] = []

    for w in wacc_values:
        row: list[float] = []
        for r in real_values:
            stressed = ValuationAssumptions(
                wacc_pct=w,
                tax_rate_pct=base.tax_rate_pct,
                terminal_growth_pct=min(base.terminal_growth_pct, w - 1.0),
                realization_factor_pct=r,
                integration_cost_overrun_pct=base.integration_cost_overrun_pct,
                projection_years=base.projection_years,
                use_probability_weighting=base.use_probability_weighting,
                terminal_method=base.terminal_method,
                terminal_multiple=base.terminal_multiple,
            )
            res = compute_synergy_dcf(report, stressed)
            row.append(res.total_synergy_npv_b)
        grid.append(row)

    return wacc_values, real_values, grid
