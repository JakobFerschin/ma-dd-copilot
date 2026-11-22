"""
Institutional Corporate Finance & Private Equity Modeling Engine:
  1. 5-Year M&A Accretion / Dilution Merger Model (Cash / Debt / Stock Mix, PPA Goodwill &
     Intangibles Amortization, Synergy Ramp, Pro-Forma EPS, and Year-1 Breakeven Synergies)
  2. Private Equity Leveraged Buyout (LBO) Model (Sources & Uses, Senior + Mezzanine Debt
     Tranches, Mandatory Amortization + Excess Cash Sweep, Credit Ratios, Bisection IRR,
     MOIC, Value Creation Attribution, and 2D Entry × Exit Multiple IRR Sensitivity Grid)
  3. Section 338(h)(10) / Asset Deal Tax Step-Up PV vs. Share Deal Structuring Engine

Zero LLM arithmetic — 100% deterministic Python financial modeling.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from schemas.report_schema import DueDiligenceReport


# =====================================================================
# Shared Helper: Bisection IRR Solver
# =====================================================================

def _bisection_irr_pct(cash_flows: list[float]) -> Optional[float]:
    """
    Compute annual IRR (%) via robust bisection on [CF_0, CF_1, ..., CF_n].
    Handles negative IRRs (down to -99%) and high positive IRRs (up to +500%).
    Returns -100.0 for complete equity wipeout (CF_0 < 0 and all subsequent CFs <= 0).
    """
    if len(cash_flows) < 2:
        return None
    if cash_flows[0] < 0 and all(cf <= 0 for cf in cash_flows[1:]):
        return -100.0
    has_neg = any(cf < 0 for cf in cash_flows)
    has_pos = any(cf > 0 for cf in cash_flows)
    if not (has_neg and has_pos):
        return None

    def npv_at(rate: float) -> float:
        return sum(cf / ((1.0 + rate) ** t) for t, cf in enumerate(cash_flows))

    low, high = -0.99, 5.0
    f_low, f_high = npv_at(low), npv_at(high)
    if f_low * f_high > 0:
        # Expand upper bound for extreme multi-bagger LBOs
        high = 20.0
        f_high = npv_at(high)
        if f_low * f_high > 0:
            return None

    for _ in range(100):
        mid = (low + high) / 2.0
        f_mid = npv_at(mid)
        if abs(f_mid) < 1e-8 or (high - low) < 1e-9:
            return round(mid * 100.0, 2)
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return round(((low + high) / 2.0) * 100.0, 2)


# =====================================================================
# 1. 5-Year M&A Accretion / Dilution Merger Model
# =====================================================================

@dataclass
class MergerInputs:
    """Complete inputs for an institutional 5-year M&A Accretion / Dilution Merger Model."""
    acquirer_name: str
    target_name: str

    # Acquirer standalone profile
    acquirer_share_price: float          # USD per share
    acquirer_diluted_shares_m: float     # Millions of diluted shares
    acquirer_net_income_y1_m: float      # Year-1 standalone Net Income ($M)
    acquirer_ni_growth_pct: float = 6.0  # Annual standalone NI growth (%)

    # Target standalone & offer profile
    purchase_equity_value_m: float = 10000.0   # Offer Equity Value ($M)
    target_net_income_y1_m: float = 400.0      # Year-1 standalone Net Income ($M)
    target_ni_growth_pct: float = 10.0         # Annual standalone NI growth (%)
    target_book_value_m: float = 2500.0        # Target net tangible book equity ($M) for PPA

    # Consideration & Financing Mix (normalized to 100% if needed)
    cash_pct: float = 40.0
    debt_pct: float = 40.0
    stock_pct: float = 20.0
    new_debt_interest_rate_pct: float = 6.25
    foregone_cash_yield_pct: float = 4.25
    transaction_fees_pct: float = 1.75         # Advisory + financing fees as % of equity purchase price
    annual_debt_paydown_pct: float = 10.0      # Annual principal repayment % of initial new debt

    # Purchase Price Allocation (PPA)
    intangible_writeup_pct_of_excess: float = 30.0  # % of (Purchase Price - Book Value) allocated to amortizable intangibles
    intangible_amort_years: int = 10

    # Synergies & Integration Costs
    steady_state_synergies_m: float = 500.0
    synergy_ramp_pcts: tuple[float, ...] = (25.0, 65.0, 100.0, 100.0, 100.0)
    one_time_integration_cost_m: float = 300.0
    include_one_time_costs_in_eps: bool = False  # False = Wall Street Adjusted EPS; True = GAAP EPS

    # Tax & Horizon
    tax_rate_pct: float = 25.0
    projection_years: int = 5


@dataclass
class MergerYearRow:
    """Single projection year in the Pro-Forma Accretion / Dilution schedule."""
    year: int
    acquirer_standalone_ni_m: float
    target_standalone_ni_m: float
    pre_tax_synergies_m: float
    interest_on_new_debt_m: float
    foregone_interest_on_cash_m: float
    intangible_amortization_m: float
    one_time_integration_cost_m: float
    pre_tax_deal_adjustments_m: float
    tax_impact_on_adjustments_m: float
    after_tax_deal_adjustments_m: float
    pro_forma_net_income_m: float
    standalone_shares_m: float
    new_shares_issued_m: float
    pro_forma_shares_m: float
    standalone_eps: float
    pro_forma_eps: float
    eps_accretion_dilution_usd: float
    eps_accretion_dilution_pct: float
    ending_new_debt_m: float


@dataclass
class MergerModelResult:
    """Complete 5-year Merger Model output including PPA, Ownership, EPS Accretion & Breakeven."""
    inputs: MergerInputs

    # Financing & Ownership
    cash_used_m: float
    new_debt_raised_m: float
    stock_issued_m: float
    transaction_fees_m: float
    total_funding_required_m: float
    new_shares_issued_m: float
    pro_forma_shares_m: float
    acquirer_ownership_pct: float
    target_ownership_pct: float

    # Purchase Price Allocation (PPA)
    excess_purchase_price_m: float
    identifiable_intangibles_m: float
    annual_intangible_amort_m: float
    goodwill_created_m: float

    # Multi-year EPS schedule
    years: list[MergerYearRow] = field(default_factory=list)

    # Headline Investment Committee metrics
    year1_accretion_usd: float = 0.0
    year1_accretion_pct: float = 0.0
    year3_accretion_usd: float = 0.0
    year3_accretion_pct: float = 0.0
    year5_accretion_usd: float = 0.0
    year5_accretion_pct: float = 0.0
    is_year1_accretive: bool = False
    breakeven_pre_tax_synergies_y1_m: float = 0.0
    y1_synergy_cushion_m: float = 0.0


def compute_merger_model(inputs: MergerInputs) -> MergerModelResult:
    """
    Execute the deterministic 5-year M&A Accretion / Dilution Merger Model.
    Computes Financing Sources, PPA Goodwill & Intangibles, Pro-Forma Net Income,
    Standalone vs. Pro-Forma EPS, Accretion/Dilution ($ and %), and exact analytical
    Year-1 Breakeven Pre-Tax Synergies for EPS neutrality.
    """
    # Normalize financing mix cleanly to 100%
    raw_mix = max(0.0, inputs.cash_pct) + max(0.0, inputs.debt_pct) + max(0.0, inputs.stock_pct)
    if raw_mix <= 0:
        w_cash, w_debt, w_stock = 1.0, 0.0, 0.0
    else:
        w_cash = max(0.0, inputs.cash_pct) / raw_mix
        w_debt = max(0.0, inputs.debt_pct) / raw_mix
        w_stock = max(0.0, inputs.stock_pct) / raw_mix

    purchase_px = max(0.0, inputs.purchase_equity_value_m)
    tx_fees = purchase_px * max(0.0, inputs.transaction_fees_pct) / 100.0
    # Stock consideration covers equity purchase price share; cash/debt also absorb transaction fees
    stock_issued = purchase_px * w_stock
    non_stock_total = (purchase_px - stock_issued) + tx_fees
    if (w_cash + w_debt) > 0:
        cash_used = non_stock_total * (w_cash / (w_cash + w_debt))
        new_debt_raised = non_stock_total * (w_debt / (w_cash + w_debt))
    else:
        # 100% stock deal: fees paid from cash on hand
        cash_used = tx_fees
        new_debt_raised = 0.0
    total_funding = cash_used + new_debt_raised + stock_issued

    share_price = max(inputs.acquirer_share_price, 1e-4)
    standalone_shares = max(inputs.acquirer_diluted_shares_m, 1e-4)
    new_shares = stock_issued / share_price
    pf_shares = standalone_shares + new_shares
    acq_own_pct = (standalone_shares / pf_shares) * 100.0
    tgt_own_pct = (new_shares / pf_shares) * 100.0

    # Purchase Price Allocation (PPA)
    excess_pp = max(0.0, purchase_px - max(0.0, inputs.target_book_value_m))
    writeup_frac = max(0.0, min(100.0, inputs.intangible_writeup_pct_of_excess)) / 100.0
    intangibles = excess_pp * writeup_frac
    amort_years = max(1, inputs.intangible_amort_years)
    annual_amort = intangibles / amort_years
    goodwill = excess_pp - intangibles

    tax_rate = max(0.0, min(0.60, inputs.tax_rate_pct / 100.0))
    debt_rate = max(0.0, inputs.new_debt_interest_rate_pct / 100.0)
    cash_yield = max(0.0, inputs.foregone_cash_yield_pct / 100.0)
    acq_g = inputs.acquirer_ni_growth_pct / 100.0
    tgt_g = inputs.target_ni_growth_pct / 100.0
    paydown_frac = max(0.0, min(100.0, inputs.annual_debt_paydown_pct)) / 100.0
    n_years = max(1, inputs.projection_years)

    # Integration cost phasing: 60% Y1, 30% Y2, 10% Y3
    int_phasing = {1: 0.60, 2: 0.30, 3: 0.10}
    ramp_tuple = inputs.synergy_ramp_pcts or (100.0,)

    rows: list[MergerYearRow] = []
    beg_debt = new_debt_raised
    y1_interest = 0.0
    y1_foregone = 0.0
    y1_amort = 0.0
    y1_one_time = 0.0

    for y in range(1, n_years + 1):
        acq_ni = inputs.acquirer_net_income_y1_m * ((1.0 + acq_g) ** (y - 1))
        tgt_ni = inputs.target_net_income_y1_m * ((1.0 + tgt_g) ** (y - 1))

        ramp_pct = ramp_tuple[y - 1] if (y - 1) < len(ramp_tuple) else ramp_tuple[-1]
        synergies = max(0.0, inputs.steady_state_synergies_m) * (max(0.0, ramp_pct) / 100.0)

        interest_exp = beg_debt * debt_rate
        foregone_int = cash_used * cash_yield
        amort_y = annual_amort if y <= amort_years else 0.0
        one_time_y = max(0.0, inputs.one_time_integration_cost_m) * int_phasing.get(y, 0.0)

        if y == 1:
            y1_interest = interest_exp
            y1_foregone = foregone_int
            y1_amort = amort_y
            y1_one_time = one_time_y

        included_one_time = one_time_y if inputs.include_one_time_costs_in_eps else 0.0
        pre_tax_adj = synergies - interest_exp - foregone_int - amort_y - included_one_time
        tax_impact = pre_tax_adj * tax_rate
        after_tax_adj = pre_tax_adj - tax_impact

        pf_ni = acq_ni + tgt_ni + after_tax_adj
        sa_eps = acq_ni / standalone_shares
        pf_eps = pf_ni / pf_shares
        accr_usd = pf_eps - sa_eps
        accr_pct = (accr_usd / abs(sa_eps) * 100.0) if abs(sa_eps) > 1e-6 else 0.0

        # Scheduled de-leveraging of new acquisition debt
        repayment = min(beg_debt, new_debt_raised * paydown_frac)
        end_debt = max(0.0, beg_debt - repayment)

        rows.append(
            MergerYearRow(
                year=y,
                acquirer_standalone_ni_m=round(acq_ni, 2),
                target_standalone_ni_m=round(tgt_ni, 2),
                pre_tax_synergies_m=round(synergies, 2),
                interest_on_new_debt_m=round(interest_exp, 2),
                foregone_interest_on_cash_m=round(foregone_int, 2),
                intangible_amortization_m=round(amort_y, 2),
                one_time_integration_cost_m=round(one_time_y, 2),
                pre_tax_deal_adjustments_m=round(pre_tax_adj, 2),
                tax_impact_on_adjustments_m=round(tax_impact, 2),
                after_tax_deal_adjustments_m=round(after_tax_adj, 2),
                pro_forma_net_income_m=round(pf_ni, 2),
                standalone_shares_m=round(standalone_shares, 2),
                new_shares_issued_m=round(new_shares, 2),
                pro_forma_shares_m=round(pf_shares, 2),
                standalone_eps=round(sa_eps, 3),
                pro_forma_eps=round(pf_eps, 3),
                eps_accretion_dilution_usd=round(accr_usd, 3),
                eps_accretion_dilution_pct=round(accr_pct, 2),
                ending_new_debt_m=round(end_debt, 2),
            )
        )
        beg_debt = end_debt

    # Exact closed-form analytical Breakeven Pre-Tax Synergies for Year-1 EPS neutrality:
    # Need: [NI_A + NI_T + (Syn_BE - Int - Foregone - Amort - OneTime_inc)*(1 - t)] / S_pf = NI_A / S_0
    # => (Syn_BE - Costs)*(1 - t) = NI_A * (new_shares / standalone_shares) - NI_T
    one_time_be = y1_one_time if inputs.include_one_time_costs_in_eps else 0.0
    after_tax_gap = inputs.acquirer_net_income_y1_m * (new_shares / standalone_shares) - inputs.target_net_income_y1_m
    raw_breakeven = (y1_interest + y1_foregone + y1_amort + one_time_be) + (after_tax_gap / max(1.0 - tax_rate, 1e-4))
    breakeven_y1 = max(0.0, raw_breakeven)
    cushion_y1 = max(0.0, -raw_breakeven)

    y1 = rows[0]
    y3 = rows[min(2, len(rows) - 1)]
    y5 = rows[-1]

    return MergerModelResult(
        inputs=inputs,
        cash_used_m=round(cash_used, 2),
        new_debt_raised_m=round(new_debt_raised, 2),
        stock_issued_m=round(stock_issued, 2),
        transaction_fees_m=round(tx_fees, 2),
        total_funding_required_m=round(total_funding, 2),
        new_shares_issued_m=round(new_shares, 2),
        pro_forma_shares_m=round(pf_shares, 2),
        acquirer_ownership_pct=round(acq_own_pct, 2),
        target_ownership_pct=round(tgt_own_pct, 2),
        excess_purchase_price_m=round(excess_pp, 2),
        identifiable_intangibles_m=round(intangibles, 2),
        annual_intangible_amort_m=round(annual_amort, 2),
        goodwill_created_m=round(goodwill, 2),
        years=rows,
        year1_accretion_usd=y1.eps_accretion_dilution_usd,
        year1_accretion_pct=y1.eps_accretion_dilution_pct,
        year3_accretion_usd=y3.eps_accretion_dilution_usd,
        year3_accretion_pct=y3.eps_accretion_dilution_pct,
        year5_accretion_usd=y5.eps_accretion_dilution_usd,
        year5_accretion_pct=y5.eps_accretion_dilution_pct,
        is_year1_accretive=y1.eps_accretion_dilution_usd >= 0.0,
        breakeven_pre_tax_synergies_y1_m=round(breakeven_y1, 2),
        y1_synergy_cushion_m=round(cushion_y1, 2),
    )


# =====================================================================
# 2. Private Equity Leveraged Buyout (LBO) Model
# =====================================================================

@dataclass
class LBOInputs:
    """Institutional Private Equity LBO model assumptions (Senior + Mezzanine Debt)."""
    company_name: str
    ltm_ebitda_m: float
    ltm_revenue_m: float = 0.0

    # Valuation & Holding Period
    entry_ev_ebitda_x: float = 11.0
    exit_ev_ebitda_x: float = 11.0
    hold_years: int = 5

    # Operating Projection
    ebitda_cagr_pct: float = 8.5
    da_pct_of_ebitda: float = 20.0
    capex_pct_of_ebitda: float = 18.0
    nwc_change_pct_of_ebitda_delta: float = 15.0
    tax_rate_pct: float = 25.0

    # Debt Capital Structure
    senior_debt_ebitda_x: float = 4.0
    mezz_debt_ebitda_x: float = 1.5
    senior_interest_rate_pct: float = 7.25
    mezz_interest_rate_pct: float = 11.50
    mezz_pik_pct: float = 0.0                # % of mezz interest paid-in-kind (0 = 100% cash pay)
    senior_mandatory_amort_pct: float = 5.0  # Annual mandatory principal amortization (% of initial senior)
    cash_sweep_pct: float = 75.0             # % of excess FCF swept to optional debt prepayment
    sweep_mezz_after_senior: bool = True

    # Transaction & Financing Fees
    transaction_fees_pct: float = 2.0        # % of Entry EV
    financing_fees_pct: float = 2.5          # % of Total Initial Debt
    minimum_cash_m: float = 0.0


@dataclass
class LBOYearRow:
    """Annual operating, cash flow, debt waterfall, and credit metric snapshot for LBO."""
    year: int
    ebitda_m: float
    da_m: float
    ebit_m: float
    senior_interest_m: float
    mezz_cash_interest_m: float
    mezz_pik_interest_m: float
    total_interest_m: float
    ebt_m: float
    taxes_m: float
    net_income_m: float
    capex_m: float
    nwc_change_m: float
    cfads_m: float
    mandatory_amort_m: float
    optional_cash_sweep_m: float
    total_debt_paydown_m: float
    ending_senior_debt_m: float
    ending_mezz_debt_m: float
    ending_total_debt_m: float
    ending_cash_m: float
    ending_net_debt_m: float
    total_debt_to_ebitda_x: float
    net_debt_to_ebitda_x: float
    interest_coverage_x: float


@dataclass
class LBOResult:
    """Complete LBO returns, Sources & Uses, debt schedule, attribution, and 2D IRR sensitivity."""
    inputs: LBOInputs

    # Sources & Uses
    purchase_ev_m: float
    transaction_fees_m: float
    financing_fees_m: float
    total_uses_m: float
    initial_senior_debt_m: float
    initial_mezz_debt_m: float
    total_initial_debt_m: float
    sponsor_equity_m: float
    debt_pct_of_sources: float
    equity_pct_of_sources: float
    sources_table: dict[str, float] = field(default_factory=dict)
    uses_table: dict[str, float] = field(default_factory=dict)

    # Annual Schedule
    years: list[LBOYearRow] = field(default_factory=list)

    # Exit & Sponsor Returns
    exit_ebitda_m: float = 0.0
    exit_ev_m: float = 0.0
    exit_total_debt_m: float = 0.0
    exit_cash_m: float = 0.0
    exit_net_debt_m: float = 0.0
    exit_equity_m: float = 0.0
    total_debt_paydown_m: float = 0.0

    sponsor_irr_pct: Optional[float] = None
    moic_x: float = 0.0

    # PE Value Creation Attribution ($M)
    value_creation_ebitda_growth_m: float = 0.0
    value_creation_multiple_expansion_m: float = 0.0
    value_creation_deleveraging_m: float = 0.0

    # 2D Entry Multiple × Exit Multiple IRR Sensitivity Grid
    entry_multiple_axis: list[float] = field(default_factory=list)
    exit_multiple_axis: list[float] = field(default_factory=list)
    irr_sensitivity_grid: list[list[Optional[float]]] = field(default_factory=list)
    moic_sensitivity_grid: list[list[float]] = field(default_factory=list)


def compute_lbo_model(
    inputs: LBOInputs,
    compute_sensitivity: bool = True,
) -> LBOResult:
    """
    Run the institutional Private Equity LBO model:
    Sources & Uses, Senior + Mezzanine Debt waterfall with mandatory amortization & excess
    cash sweep, annual credit metrics (Debt/EBITDA, Interest Coverage), Exit EV & Equity,
    Bisection Sponsor IRR (%), MOIC (x), and 2D Entry × Exit Multiple IRR sensitivity grid.
    """
    ltm_ebitda = max(inputs.ltm_ebitda_m, 1.0)
    entry_mult = max(inputs.entry_ev_ebitda_x, 1.0)
    exit_mult = max(inputs.exit_ev_ebitda_x, 1.0)
    hold_years = max(1, min(10, inputs.hold_years))

    purchase_ev = ltm_ebitda * entry_mult
    tx_fees = purchase_ev * max(0.0, inputs.transaction_fees_pct) / 100.0
    min_cash = max(0.0, inputs.minimum_cash_m)

    init_senior = ltm_ebitda * max(0.0, inputs.senior_debt_ebitda_x)
    init_mezz = ltm_ebitda * max(0.0, inputs.mezz_debt_ebitda_x)
    total_init_debt = init_senior + init_mezz

    # Guard against over-leveraging beyond 90% of purchase EV so sponsor equity stays positive
    max_debt = purchase_ev * 0.90
    if total_init_debt > max_debt and total_init_debt > 0:
        scale = max_debt / total_init_debt
        init_senior *= scale
        init_mezz *= scale
        total_init_debt = init_senior + init_mezz

    fin_fees = total_init_debt * max(0.0, inputs.financing_fees_pct) / 100.0
    total_uses = purchase_ev + tx_fees + fin_fees + min_cash
    sponsor_equity = max(1.0, total_uses - total_init_debt)
    # Reconcile total_sources == total_uses
    total_sources = init_senior + init_mezz + sponsor_equity

    sources_table = {
        "Senior Secured Debt": round(init_senior, 2),
        "Mezzanine / Subordinated Debt": round(init_mezz, 2),
        "Sponsor Common Equity": round(sponsor_equity, 2),
        "Total Sources": round(total_sources, 2),
    }
    uses_table = {
        "Purchase Enterprise Value": round(purchase_ev, 2),
        "Transaction Advisory Fees": round(tx_fees, 2),
        "Debt Financing Fees": round(fin_fees, 2),
        "Balance Sheet Minimum Cash": round(min_cash, 2),
        "Total Uses": round(total_uses, 2),
    }

    g_ebitda = inputs.ebitda_cagr_pct / 100.0
    da_pct = max(0.0, inputs.da_pct_of_ebitda) / 100.0
    capex_pct = max(0.0, inputs.capex_pct_of_ebitda) / 100.0
    nwc_delta_pct = inputs.nwc_change_pct_of_ebitda_delta / 100.0
    tax_rate = max(0.0, min(0.60, inputs.tax_rate_pct / 100.0))

    sen_rate = max(0.0, inputs.senior_interest_rate_pct) / 100.0
    mezz_rate = max(0.0, inputs.mezz_interest_rate_pct) / 100.0
    pik_frac = max(0.0, min(100.0, inputs.mezz_pik_pct)) / 100.0
    mand_amort_frac = max(0.0, inputs.senior_mandatory_amort_pct) / 100.0
    sweep_frac = max(0.0, min(100.0, inputs.cash_sweep_pct)) / 100.0

    sen_bal = init_senior
    mezz_bal = init_mezz
    cash_bal = min_cash
    prev_ebitda = ltm_ebitda

    rows: list[LBOYearRow] = []
    for y in range(1, hold_years + 1):
        ebitda_y = ltm_ebitda * ((1.0 + g_ebitda) ** y)
        da_y = ebitda_y * da_pct
        ebit_y = ebitda_y - da_y

        sen_int = sen_bal * sen_rate
        mezz_tot_int = mezz_bal * mezz_rate
        mezz_pik = mezz_tot_int * pik_frac
        mezz_cash_int = mezz_tot_int - mezz_pik
        tot_int = sen_int + mezz_tot_int

        ebt_y = ebit_y - tot_int
        taxes_y = max(0.0, ebt_y * tax_rate)
        net_inc_y = ebt_y - taxes_y

        capex_y = ebitda_y * capex_pct
        nwc_chg_y = max(0.0, ebitda_y - prev_ebitda) * nwc_delta_pct

        # Cash Flow Available for Debt Service (add back non-cash D&A and PIK interest)
        cfads_y = net_inc_y + da_y + mezz_pik - capex_y - nwc_chg_y

        # Accrue PIK interest onto mezzanine principal
        mezz_bal += mezz_pik

        # Mandatory Senior Debt Amortization
        scheduled_mand = min(sen_bal, init_senior * mand_amort_frac)
        if cfads_y >= scheduled_mand:
            mand_paid = scheduled_mand
            excess_cf = cfads_y - mand_paid
        elif cfads_y > 0:
            mand_paid = cfads_y
            excess_cf = 0.0
        else:
            # Deficit draws from cash first, then revolver/senior debt if cash exhausted
            mand_paid = 0.0
            excess_cf = 0.0
            deficit = abs(cfads_y)
            if cash_bal >= deficit:
                cash_bal -= deficit
            else:
                shortfall = deficit - cash_bal
                cash_bal = 0.0
                sen_bal += shortfall

        sen_bal = max(0.0, sen_bal - mand_paid)

        # Optional Excess Cash Sweep (Senior first, then Mezzanine if enabled)
        sweep_available = max(0.0, excess_cf * sweep_frac)
        sen_sweep = min(sen_bal, sweep_available)
        sen_bal -= sen_sweep
        rem_sweep = sweep_available - sen_sweep

        mezz_sweep = 0.0
        if inputs.sweep_mezz_after_senior and rem_sweep > 0 and mezz_bal > 0:
            mezz_sweep = min(mezz_bal, rem_sweep)
            mezz_bal -= mezz_sweep

        opt_sweep = sen_sweep + mezz_sweep
        tot_paydown = mand_paid + opt_sweep
        cash_bal += max(0.0, excess_cf - opt_sweep)

        end_tot_debt = sen_bal + mezz_bal
        end_net_debt = end_tot_debt - cash_bal
        debt_ebitda = end_tot_debt / ebitda_y if ebitda_y > 0 else 0.0
        net_debt_ebitda = end_net_debt / ebitda_y if ebitda_y > 0 else 0.0
        int_cov = ebitda_y / tot_int if tot_int > 1e-6 else 99.9

        rows.append(
            LBOYearRow(
                year=y,
                ebitda_m=round(ebitda_y, 2),
                da_m=round(da_y, 2),
                ebit_m=round(ebit_y, 2),
                senior_interest_m=round(sen_int, 2),
                mezz_cash_interest_m=round(mezz_cash_int, 2),
                mezz_pik_interest_m=round(mezz_pik, 2),
                total_interest_m=round(tot_int, 2),
                ebt_m=round(ebt_y, 2),
                taxes_m=round(taxes_y, 2),
                net_income_m=round(net_inc_y, 2),
                capex_m=round(capex_y, 2),
                nwc_change_m=round(nwc_chg_y, 2),
                cfads_m=round(cfads_y, 2),
                mandatory_amort_m=round(mand_paid, 2),
                optional_cash_sweep_m=round(opt_sweep, 2),
                total_debt_paydown_m=round(tot_paydown, 2),
                ending_senior_debt_m=round(sen_bal, 2),
                ending_mezz_debt_m=round(mezz_bal, 2),
                ending_total_debt_m=round(end_tot_debt, 2),
                ending_cash_m=round(cash_bal, 2),
                ending_net_debt_m=round(end_net_debt, 2),
                total_debt_to_ebitda_x=round(debt_ebitda, 2),
                net_debt_to_ebitda_x=round(net_debt_ebitda, 2),
                interest_coverage_x=round(min(int_cov, 99.9), 2),
            )
        )
        prev_ebitda = ebitda_y

    exit_row = rows[-1]
    exit_ebitda = exit_row.ebitda_m
    exit_ev = exit_ebitda * exit_mult
    exit_net_debt = exit_row.ending_net_debt_m
    exit_equity = max(0.0, exit_ev - exit_net_debt)
    moic = exit_equity / sponsor_equity

    # Sponsor cash flow series for bisection IRR
    irr_flows = [-sponsor_equity] + [0.0] * (hold_years - 1) + [exit_equity]
    irr_pct = _bisection_irr_pct(irr_flows)

    # PE Equity Value Creation Attribution Bridge:
    # Exit Equity - Sponsor Equity = EBITDA Growth + Multiple Expansion + De-leveraging (net of fees)
    vc_ebitda = (exit_ebitda - ltm_ebitda) * entry_mult
    vc_multiple = (exit_mult - entry_mult) * exit_ebitda
    vc_deleverage = (exit_equity - sponsor_equity) - vc_ebitda - vc_multiple

    # 2D Entry Multiple × Exit Multiple IRR Sensitivity Grid (5×5 centered on base multiples)
    entry_axis: list[float] = []
    exit_axis: list[float] = []
    irr_grid: list[list[Optional[float]]] = []
    moic_grid: list[list[float]] = []

    if compute_sensitivity:
        deltas = (-2.0, -1.0, 0.0, 1.0, 2.0)
        entry_axis = [round(max(3.0, entry_mult + d), 1) for d in deltas]
        exit_axis = [round(max(3.0, exit_mult + d), 1) for d in deltas]
        for em in entry_axis:
            irr_row: list[Optional[float]] = []
            moic_row: list[float] = []
            for xm in exit_axis:
                stressed = replace(inputs, entry_ev_ebitda_x=em, exit_ev_ebitda_x=xm)
                res = compute_lbo_model(stressed, compute_sensitivity=False)
                irr_row.append(res.sponsor_irr_pct)
                moic_row.append(res.moic_x)
            irr_grid.append(irr_row)
            moic_grid.append(moic_row)

    return LBOResult(
        inputs=inputs,
        purchase_ev_m=round(purchase_ev, 2),
        transaction_fees_m=round(tx_fees, 2),
        financing_fees_m=round(fin_fees, 2),
        total_uses_m=round(total_uses, 2),
        initial_senior_debt_m=round(init_senior, 2),
        initial_mezz_debt_m=round(init_mezz, 2),
        total_initial_debt_m=round(total_init_debt, 2),
        sponsor_equity_m=round(sponsor_equity, 2),
        debt_pct_of_sources=round(total_init_debt / total_sources * 100.0, 1),
        equity_pct_of_sources=round(sponsor_equity / total_sources * 100.0, 1),
        sources_table=sources_table,
        uses_table=uses_table,
        years=rows,
        exit_ebitda_m=round(exit_ebitda, 2),
        exit_ev_m=round(exit_ev, 2),
        exit_total_debt_m=round(exit_row.ending_total_debt_m, 2),
        exit_cash_m=round(exit_row.ending_cash_m, 2),
        exit_net_debt_m=round(exit_net_debt, 2),
        exit_equity_m=round(exit_equity, 2),
        total_debt_paydown_m=round(total_init_debt - exit_row.ending_total_debt_m, 2),
        sponsor_irr_pct=irr_pct,
        moic_x=round(moic, 2),
        value_creation_ebitda_growth_m=round(vc_ebitda, 2),
        value_creation_multiple_expansion_m=round(vc_multiple, 2),
        value_creation_deleveraging_m=round(vc_deleverage, 2),
        entry_multiple_axis=entry_axis,
        exit_multiple_axis=exit_axis,
        irr_sensitivity_grid=irr_grid,
        moic_sensitivity_grid=moic_grid,
    )


# =====================================================================
# 3. Section 338(h)(10) / Asset Deal Tax Step-Up PV vs. Share Deal
# =====================================================================

@dataclass
class TaxStepUpResult:
    """Section 338(h)(10) / Asset Purchase Tax Basis Step-Up valuation vs. Share Deal."""
    purchase_price_m: float
    tax_basis_m: float
    step_up_basis_m: float
    tax_rate_pct: float
    amort_years: int
    wacc_pct: float

    annual_amortization_m: float
    annual_tax_shield_m: float
    undiscounted_tax_shield_m: float
    pv_tax_shield_m: float

    share_deal_effective_price_m: float
    asset_deal_effective_price_m: float
    pv_as_pct_of_purchase_price: float
    pv_as_pct_of_step_up: float

    annual_schedule: list[dict[str, float]] = field(default_factory=list)
    structuring_note: str = ""


def compute_tax_step_up(
    purchase_price_m: float,
    tax_basis_m: float,
    tax_rate_pct: float = 25.0,
    amort_years: int = 15,
    wacc_pct: float = 9.0,
) -> TaxStepUpResult:
    """
    Model the Present Value of a Section 338(h)(10) / Asset Purchase tax basis step-up
    (IRC Section 197 straight-line amortization, typically 15 years) compared against a
    traditional Share Deal with carryover tax basis.
    """
    px = max(0.0, purchase_price_m)
    basis = max(0.0, tax_basis_m)
    step_up = max(0.0, px - basis)
    n_years = max(1, amort_years)
    t = max(0.0, min(0.60, tax_rate_pct / 100.0))
    r = max(0.005, wacc_pct / 100.0)

    annual_amort = step_up / n_years
    annual_shield = annual_amort * t
    undisc_shield = annual_shield * n_years

    schedule: list[dict[str, float]] = []
    pv_shield = 0.0
    for yr in range(1, n_years + 1):
        df = 1.0 / ((1.0 + r) ** yr)
        pv_yr = annual_shield * df
        pv_shield += pv_yr
        schedule.append(
            {
                "year": float(yr),
                "amortization_m": round(annual_amort, 2),
                "tax_shield_m": round(annual_shield, 2),
                "discount_factor": round(df, 4),
                "pv_tax_shield_m": round(pv_yr, 2),
            }
        )

    asset_eff_px = px - pv_shield
    pct_of_px = (pv_shield / px * 100.0) if px > 0 else 0.0
    pct_of_step = (pv_shield / step_up * 100.0) if step_up > 0 else 0.0

    if step_up <= 0:
        note = (
            "Purchase price does not exceed target's existing tax basis; a Section 338(h)(10) "
            "election creates no incremental amortizable tax shield."
        )
    else:
        note = (
            f"A Section 338(h)(10) / Asset Deal generates ${pv_shield:,.1f}M in PV tax shields "
            f"({pct_of_px:.1f}% effective purchase price reduction over {n_years} years at {wacc_pct:.1f}% WACC). "
            f"Evaluate seller's inside-vs-outside basis corporate tax leakage to negotiate a split of the shield."
        )

    return TaxStepUpResult(
        purchase_price_m=round(px, 2),
        tax_basis_m=round(basis, 2),
        step_up_basis_m=round(step_up, 2),
        tax_rate_pct=round(tax_rate_pct, 2),
        amort_years=n_years,
        wacc_pct=round(wacc_pct, 2),
        annual_amortization_m=round(annual_amort, 2),
        annual_tax_shield_m=round(annual_shield, 2),
        undiscounted_tax_shield_m=round(undisc_shield, 2),
        pv_tax_shield_m=round(pv_shield, 2),
        share_deal_effective_price_m=round(px, 2),
        asset_deal_effective_price_m=round(asset_eff_px, 2),
        pv_as_pct_of_purchase_price=round(pct_of_px, 2),
        pv_as_pct_of_step_up=round(pct_of_step, 2),
        annual_schedule=schedule,
        structuring_note=note,
    )


# =====================================================================
# 4. Report-Aware Default Input Builders
# =====================================================================

def default_merger_inputs_for_report(report: DueDiligenceReport) -> MergerInputs:
    """Build realistic, sector-calibrated Merger Model defaults from any `DueDiligenceReport`."""
    d = report.deal
    deal_val_m = (d.deal_value_usd_b or 12.0) * 1000.0
    sector = (d.industry_sector or "").lower()

    # Derive steady-state synergies and one-time integration costs directly from the report
    if report.synergy_workstreams:
        syn_m = sum(
            w.annual_run_rate_usd_m * (w.probability_pct / 100.0)
            for w in report.synergy_workstreams
        )
        int_cost_m = sum(w.one_time_cost_usd_m for w in report.synergy_workstreams)
    else:
        syn_m = ((d.estimated_revenue_synergy_usd_b or 0.0) + (d.estimated_cost_synergy_usd_b or 0.0)) * 1000.0
        if syn_m <= 0:
            syn_m = deal_val_m * 0.06
        int_cost_m = (d.estimated_integration_cost_usd_b or (deal_val_m / 1000.0 * 0.05)) * 1000.0

    # Calibrate acquirer & target standalone profiles by sector / flagship deal
    if "volkswagen" in d.entity_a_name.lower() or any(k in sector for k in ("auto", "ev", "mobility")):
        acq_price = 105.0
        acq_shares = 501.0
        acq_ni = 13800.0
        acq_g = 4.5
        tgt_ni = 280.0
        tgt_g = 22.0
        tgt_book = round(deal_val_m * 0.36, 1)
        cash_pct, debt_pct, stock_pct = 55.0, 45.0, 0.0
        debt_rate = 5.6
    elif "microsoft" in d.entity_a_name.lower() or any(k in sector for k in ("cloud", "software", "ai", "data")):
        acq_price = 425.0
        acq_shares = 7435.0
        acq_ni = 92500.0
        acq_g = 12.0
        tgt_ni = 650.0
        tgt_g = 28.0
        tgt_book = round(deal_val_m * 0.14, 1)
        cash_pct, debt_pct, stock_pct = 50.0, 20.0, 30.0
        debt_rate = 4.9
    elif "lilly" in d.entity_a_name.lower() or any(k in sector for k in ("pharma", "bio", "therap")):
        acq_price = 780.0
        acq_shares = 900.0
        acq_ni = 14200.0
        acq_g = 16.0
        tgt_ni = 3600.0
        tgt_g = 9.5
        tgt_book = round(deal_val_m * 0.32, 1)
        cash_pct, debt_pct, stock_pct = 25.0, 45.0, 30.0
        debt_rate = 5.4
    else:
        acq_mcap = deal_val_m * 4.5
        acq_price = 120.0
        acq_shares = round(acq_mcap / acq_price, 1)
        acq_ni = round(acq_mcap / 18.0, 1)
        acq_g = 7.0
        tgt_ni = round(deal_val_m / 22.0, 1)
        tgt_g = 11.0
        tgt_book = round(deal_val_m * 0.30, 1)
        cash_pct, debt_pct, stock_pct = 40.0, 35.0, 25.0
        debt_rate = 6.2

    return MergerInputs(
        acquirer_name=d.entity_a_name,
        target_name=d.entity_b_name,
        acquirer_share_price=acq_price,
        acquirer_diluted_shares_m=acq_shares,
        acquirer_net_income_y1_m=acq_ni,
        acquirer_ni_growth_pct=acq_g,
        purchase_equity_value_m=round(deal_val_m, 1),
        target_net_income_y1_m=tgt_ni,
        target_ni_growth_pct=tgt_g,
        target_book_value_m=tgt_book,
        cash_pct=cash_pct,
        debt_pct=debt_pct,
        stock_pct=stock_pct,
        new_debt_interest_rate_pct=debt_rate,
        foregone_cash_yield_pct=4.25,
        transaction_fees_pct=1.75,
        annual_debt_paydown_pct=10.0,
        intangible_writeup_pct_of_excess=30.0,
        intangible_amort_years=10,
        steady_state_synergies_m=round(syn_m, 1),
        synergy_ramp_pcts=(25.0, 65.0, 100.0, 100.0, 100.0),
        one_time_integration_cost_m=round(int_cost_m, 1),
        include_one_time_costs_in_eps=False,
        tax_rate_pct=d.tax_rate_pct,
        projection_years=5,
    )


def default_lbo_inputs_for_report(report: DueDiligenceReport) -> LBOInputs:
    """Build realistic Private Equity sponsor LBO assumptions calibrated to any `DueDiligenceReport`."""
    d = report.deal
    deal_val_m = (d.deal_value_usd_b or 12.0) * 1000.0
    sector = (d.industry_sector or "").lower()

    if any(k in sector for k in ("auto", "ev", "mobility", "industrial")):
        entry_x = 10.5
        exit_x = 11.0
        ebitda_cagr = 10.5
        senior_x, mezz_x = 3.5, 1.25
        da_pct, capex_pct = 26.0, 24.0
        rev_mult = 3.2
    elif any(k in sector for k in ("cloud", "software", "saas", "ai", "data")):
        entry_x = 14.5
        exit_x = 15.0
        ebitda_cagr = 15.0
        senior_x, mezz_x = 4.5, 1.75
        da_pct, capex_pct = 12.0, 10.0
        rev_mult = 12.5
    elif any(k in sector for k in ("pharma", "bio", "therap", "health")):
        entry_x = 11.5
        exit_x = 12.0
        ebitda_cagr = 8.5
        senior_x, mezz_x = 4.25, 1.50
        da_pct, capex_pct = 16.0, 13.0
        rev_mult = 4.2
    else:
        entry_x = 11.0
        exit_x = 11.5
        ebitda_cagr = 9.0
        senior_x, mezz_x = 4.0, 1.5
        da_pct, capex_pct = 20.0, 18.0
        rev_mult = 2.8

    ltm_ebitda_m = round(deal_val_m / entry_x, 1)
    ltm_rev_m = round(deal_val_m / rev_mult, 1)

    return LBOInputs(
        company_name=d.entity_b_name,
        ltm_ebitda_m=ltm_ebitda_m,
        ltm_revenue_m=ltm_rev_m,
        entry_ev_ebitda_x=entry_x,
        exit_ev_ebitda_x=exit_x,
        hold_years=5,
        ebitda_cagr_pct=ebitda_cagr,
        da_pct_of_ebitda=da_pct,
        capex_pct_of_ebitda=capex_pct,
        nwc_change_pct_of_ebitda_delta=15.0,
        tax_rate_pct=d.tax_rate_pct,
        senior_debt_ebitda_x=senior_x,
        mezz_debt_ebitda_x=mezz_x,
        senior_interest_rate_pct=7.25,
        mezz_interest_rate_pct=11.50,
        mezz_pik_pct=0.0,
        senior_mandatory_amort_pct=5.0,
        cash_sweep_pct=75.0,
        sweep_mezz_after_senior=True,
        transaction_fees_pct=2.0,
        financing_fees_pct=2.5,
        minimum_cash_m=round(ltm_ebitda_m * 0.15, 1),
    )
