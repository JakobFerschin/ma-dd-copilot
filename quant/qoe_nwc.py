"""
Institutional Financial Due Diligence (FDD) Engine:
  1. Quality of Earnings (QoE) & Normalized Adjusted EBITDA Bridge
  2. Net Working Capital (NWC) LTM Peg, Seasonality & Cash-Free/Debt-Free Equity Bridge
  3. Customer Cohort Concentration, HHI & Revenue Retention (NDR / GRR)

Zero LLM arithmetic — 100% deterministic Python calculations built to Big-4 Transaction
Advisory Services (TAS) and Investment Banking IC standards.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from schemas.report_schema import DueDiligenceReport


# =====================================================================
# 1. Quality of Earnings (QoE) & Adjusted EBITDA Bridge
# =====================================================================

@dataclass
class QoEAdjustment:
    """Single FDD adjustment line item bridging Reported EBITDA to Normalized Adj. EBITDA."""
    label: str
    amount_m: float
    category: str = "Non-Recurring"      # "Non-Recurring" | "Owner/SBC" | "Run-Rate" | "Accounting Policy" | "Diligence Haircut"
    credibility: str = "High"            # "High" | "Medium" | "Low"
    rationale: str = ""


@dataclass
class QoEResult:
    """Deterministic Quality of Earnings (QoE) audit and valuation multiple impact."""
    reported_ebitda_m: float
    revenue_m: float
    adjustments: list[QoEAdjustment] = field(default_factory=list)

    total_positive_addbacks_m: float = 0.0
    total_negative_deductions_m: float = 0.0
    net_adjustments_m: float = 0.0
    adjusted_ebitda_m: float = 0.0

    reported_ebitda_margin_pct: float = 0.0
    adjusted_ebitda_margin_pct: float = 0.0
    margin_delta_bps: float = 0.0

    addback_pct_of_reported_ebitda: float = 0.0
    net_adjustment_pct_of_reported: float = 0.0
    low_credibility_addback_pct: float = 0.0

    scrutiny_score: int = 100            # 0 (Severe Red Flag) .. 100 (Pristine Earnings Quality)
    scrutiny_rating: str = "Clean"       # "Clean" | "Moderate Scrutiny" | "Elevated / Aggressive" | "Red Flag"
    aggressive_addback_flag: bool = False
    flags: list[str] = field(default_factory=list)

    headline_ev_m: Optional[float] = None
    implied_ev_reported_ebitda_x: Optional[float] = None
    implied_ev_adjusted_ebitda_x: Optional[float] = None
    multiple_delta_x: Optional[float] = None

    bridge_steps: list[tuple[str, float]] = field(default_factory=list)


def compute_qoe_bridge(
    reported_ebitda_m: float,
    revenue_m: float,
    adjustments: list[QoEAdjustment],
    headline_ev_m: Optional[float] = None,
) -> QoEResult:
    """
    Compute the Normalized Adjusted EBITDA Bridge, EBITDA margins before/after,
    Big-4 add-back scrutiny score (flagging aggressive add-backs > 15% of EBITDA),
    and implied EV / Adjusted EBITDA valuation multiple.
    """
    pos_addbacks = sum(a.amount_m for a in adjustments if a.amount_m > 0)
    neg_deductions = sum(a.amount_m for a in adjustments if a.amount_m < 0)
    net_adj = pos_addbacks + neg_deductions
    adj_ebitda = reported_ebitda_m + net_adj

    rep_margin = (reported_ebitda_m / revenue_m * 100.0) if revenue_m > 0 else 0.0
    adj_margin = (adj_ebitda / revenue_m * 100.0) if revenue_m > 0 else 0.0
    margin_delta_bps = (adj_margin - rep_margin) * 100.0

    # Express add-back intensity against absolute EBITDA base (or adjusted EBITDA if reported <= 0)
    if reported_ebitda_m > 0:
        denom = reported_ebitda_m
    elif adj_ebitda > 0:
        denom = adj_ebitda
    else:
        denom = max(abs(reported_ebitda_m), 1.0)

    addback_pct = (pos_addbacks / denom) * 100.0 if pos_addbacks > 0 else 0.0
    net_adj_pct = (net_adj / denom) * 100.0

    low_cred_pos = sum(
        a.amount_m
        for a in adjustments
        if a.amount_m > 0 and a.credibility.strip().lower() in ("low", "aggressive")
    )
    med_cred_pos = sum(
        a.amount_m
        for a in adjustments
        if a.amount_m > 0 and a.credibility.strip().lower() == "medium"
    )
    run_rate_pos = sum(
        a.amount_m
        for a in adjustments
        if a.amount_m > 0 and "run-rate" in a.category.strip().lower()
    )
    low_cred_share = (low_cred_pos / pos_addbacks * 100.0) if pos_addbacks > 0 else 0.0

    # Scrutiny scoring (100 = pristine; penalize aggressive add-backs > 15% and low-quality items)
    flags: list[str] = []
    penalty = 0.0

    aggressive_flag = addback_pct > 15.0
    if aggressive_flag:
        flags.append(
            f"Aggressive Add-Backs: Gross positive add-backs represent {addback_pct:.1f}% of EBITDA "
            f"(exceeds institutional 15.0% FDD threshold)."
        )
        # Progressive penalty above 15% threshold
        penalty += min(45.0, 15.0 + (addback_pct - 15.0) * 1.1)
    elif addback_pct > 8.0:
        penalty += (addback_pct - 8.0) * 1.2

    if low_cred_pos > 0:
        low_pct_ebitda = (low_cred_pos / denom) * 100.0
        penalty += min(25.0, low_pct_ebitda * 1.5)
        flags.append(
            f"Low-Credibility Add-Backs: ${low_cred_pos:,.1f}M ({low_cred_share:.1f}% of positive add-backs) "
            f"carry low auditability and should be haircut in buyer's valuation."
        )

    if med_cred_pos > 0:
        penalty += min(10.0, (med_cred_pos / denom) * 100.0 * 0.4)

    if run_rate_pos > 0 and (run_rate_pos / denom * 100.0) > 5.0:
        penalty += 8.0
        flags.append(
            f"Pro-Forma Run-Rate Normalization: ${run_rate_pos:,.1f}M relies on forward-looking "
            f"run-rate assumptions rather than realized historical cost savings."
        )

    if reported_ebitda_m <= 0 and adj_ebitda > 0:
        penalty += 20.0
        flags.append(
            "Negative-to-Positive EBITDA Bridge: Reported GAAP EBITDA is negative; positive Adjusted "
            "EBITDA relies entirely on management add-backs."
        )

    if neg_deductions < 0:
        flags.append(
            f"Buyer Diligence Haircuts Identified: ${abs(neg_deductions):,.1f}M of negative QoE "
            f"deductions (out-of-period / non-recurring revenue or understated run-rate costs)."
        )

    scrutiny_score = int(round(max(0.0, min(100.0, 100.0 - penalty))))
    if scrutiny_score >= 80:
        scrutiny_rating = "Clean"
    elif scrutiny_score >= 65:
        scrutiny_rating = "Moderate Scrutiny"
    elif scrutiny_score >= 45:
        scrutiny_rating = "Elevated / Aggressive"
    else:
        scrutiny_rating = "Red Flag"

    ev_rep_x: Optional[float] = None
    ev_adj_x: Optional[float] = None
    mult_delta_x: Optional[float] = None
    if headline_ev_m is not None and headline_ev_m > 0:
        if reported_ebitda_m > 0:
            ev_rep_x = round(headline_ev_m / reported_ebitda_m, 2)
        if adj_ebitda > 0:
            ev_adj_x = round(headline_ev_m / adj_ebitda, 2)
        if ev_rep_x is not None and ev_adj_x is not None:
            mult_delta_x = round(ev_adj_x - ev_rep_x, 2)

    bridge_steps: list[tuple[str, float]] = [("Reported EBITDA", round(reported_ebitda_m, 2))]
    for adj in adjustments:
        bridge_steps.append((adj.label, round(adj.amount_m, 2)))
    bridge_steps.append(("Normalized Adj. EBITDA", round(adj_ebitda, 2)))

    return QoEResult(
        reported_ebitda_m=round(reported_ebitda_m, 2),
        revenue_m=round(revenue_m, 2),
        adjustments=list(adjustments),
        total_positive_addbacks_m=round(pos_addbacks, 2),
        total_negative_deductions_m=round(neg_deductions, 2),
        net_adjustments_m=round(net_adj, 2),
        adjusted_ebitda_m=round(adj_ebitda, 2),
        reported_ebitda_margin_pct=round(rep_margin, 2),
        adjusted_ebitda_margin_pct=round(adj_margin, 2),
        margin_delta_bps=round(margin_delta_bps, 1),
        addback_pct_of_reported_ebitda=round(addback_pct, 1),
        net_adjustment_pct_of_reported=round(net_adj_pct, 1),
        low_credibility_addback_pct=round(low_cred_share, 1),
        scrutiny_score=scrutiny_score,
        scrutiny_rating=scrutiny_rating,
        aggressive_addback_flag=aggressive_flag,
        flags=flags,
        headline_ev_m=round(headline_ev_m, 2) if headline_ev_m is not None else None,
        implied_ev_reported_ebitda_x=ev_rep_x,
        implied_ev_adjusted_ebitda_x=ev_adj_x,
        multiple_delta_x=mult_delta_x,
        bridge_steps=bridge_steps,
    )


# =====================================================================
# 2. Net Working Capital (NWC) Peg & Cash-Free / Debt-Free Bridge
# =====================================================================

@dataclass
class NWCMonth:
    """Monthly working capital snapshot for LTM Peg & Seasonality analysis ($M)."""
    month: str
    ar_m: float = 0.0
    inventory_m: float = 0.0
    other_current_assets_m: float = 0.0
    ap_m: float = 0.0
    accrued_liabilities_m: float = 0.0
    deferred_revenue_m: float = 0.0
    nwc_m: Optional[float] = None

    def __post_init__(self) -> None:
        if self.nwc_m is None:
            self.nwc_m = round(
                (self.ar_m + self.inventory_m + self.other_current_assets_m)
                - (self.ap_m + self.accrued_liabilities_m + self.deferred_revenue_m),
                2,
            )


@dataclass
class DebtLikeItem:
    """Indebtedness or debt-like liability deducted in the EV-to-Equity purchase price bridge ($M)."""
    name: str
    amount_m: float
    category: str = "Financial Debt"     # "Financial Debt" | "Pension / Benefit" | "Tax Liability" | "Lease / Off-BS" | "Transaction Expenses"
    rationale: str = ""


@dataclass
class NWCResult:
    """LTM Working Capital Peg, Seasonality Band, and Cash-Free/Debt-Free Equity Value Bridge."""
    monthly_nwc: list[NWCMonth] = field(default_factory=list)
    ltm_avg_nwc_peg_m: float = 0.0
    nwc_min_m: float = 0.0
    nwc_max_m: float = 0.0
    seasonality_band_m: float = 0.0
    nwc_std_m: float = 0.0
    peak_month: str = ""
    trough_month: str = ""

    target_close_nwc_m: float = 0.0
    nwc_adjustment_m: float = 0.0        # Close NWC - Peg (positive = buyer pays up; negative = price reduction)

    headline_ev_m: float = 0.0
    cash_m: float = 0.0
    debt_like_items: list[DebtLikeItem] = field(default_factory=list)
    total_debt_like_m: float = 0.0
    net_debt_m: float = 0.0
    implied_equity_value_m: float = 0.0
    ev_to_equity_delta_m: float = 0.0

    equity_bridge_steps: list[tuple[str, float]] = field(default_factory=list)


def compute_nwc_peg(
    monthly_nwc: list[NWCMonth],
    target_close_nwc_m: float,
    debt_like_items: list[DebtLikeItem],
    cash_m: float,
    headline_ev_m: float,
) -> NWCResult:
    """
    Compute the LTM Average Net Working Capital Peg, min/max seasonality corridor,
    SPA closing NWC purchase price adjustment, and Cash-Free / Debt-Free Equity Value Bridge.
    """
    if monthly_nwc:
        vals = [float(m.nwc_m if m.nwc_m is not None else 0.0) for m in monthly_nwc]
        peg = sum(vals) / len(vals)
        min_idx = min(range(len(vals)), key=lambda i: vals[i])
        max_idx = max(range(len(vals)), key=lambda i: vals[i])
        nwc_min = vals[min_idx]
        nwc_max = vals[max_idx]
        peak_month = monthly_nwc[max_idx].month
        trough_month = monthly_nwc[min_idx].month
        variance = sum((v - peg) ** 2 for v in vals) / len(vals)
        nwc_std = math.sqrt(variance)
    else:
        peg = 0.0
        nwc_min = 0.0
        nwc_max = 0.0
        peak_month = ""
        trough_month = ""
        nwc_std = 0.0

    seasonality_band = nwc_max - nwc_min
    nwc_adj = target_close_nwc_m - peg

    total_debt_like = sum(item.amount_m for item in debt_like_items)
    net_debt = total_debt_like - cash_m
    implied_equity = headline_ev_m + cash_m - total_debt_like + nwc_adj
    delta_m = implied_equity - headline_ev_m

    bridge_steps: list[tuple[str, float]] = [
        ("Headline Enterprise Value", round(headline_ev_m, 2)),
        ("(+) Cash & Marketable Securities", round(cash_m, 2)),
        ("(-) Debt & Debt-Like Items", round(-total_debt_like, 2)),
        ("(+/-) NWC Closing Adjustment vs. Peg", round(nwc_adj, 2)),
        ("Implied Equity Purchase Price", round(implied_equity, 2)),
    ]

    return NWCResult(
        monthly_nwc=list(monthly_nwc),
        ltm_avg_nwc_peg_m=round(peg, 2),
        nwc_min_m=round(nwc_min, 2),
        nwc_max_m=round(nwc_max, 2),
        seasonality_band_m=round(seasonality_band, 2),
        nwc_std_m=round(nwc_std, 2),
        peak_month=peak_month,
        trough_month=trough_month,
        target_close_nwc_m=round(target_close_nwc_m, 2),
        nwc_adjustment_m=round(nwc_adj, 2),
        headline_ev_m=round(headline_ev_m, 2),
        cash_m=round(cash_m, 2),
        debt_like_items=list(debt_like_items),
        total_debt_like_m=round(total_debt_like, 2),
        net_debt_m=round(net_debt, 2),
        implied_equity_value_m=round(implied_equity, 2),
        ev_to_equity_delta_m=round(delta_m, 2),
        equity_bridge_steps=bridge_steps,
    )


# =====================================================================
# 3. Customer Cohort Concentration, HHI & Retention (NDR / GRR)
# =====================================================================

@dataclass
class CustomerRecord:
    """Single customer account with prior-year and current-year revenue ($M)."""
    name: str
    current_rev_m: float
    prior_rev_m: float
    segment: str = "Enterprise"
    contract_term_months: int = 12


@dataclass
class CohortAnalysisResult:
    """Commercial Due Diligence (CDD) customer concentration & revenue durability analytics."""
    customer_count: int = 0
    total_current_rev_m: float = 0.0
    total_prior_rev_m: float = 0.0
    revenue_growth_pct: float = 0.0

    top1_concentration_pct: float = 0.0
    top5_concentration_pct: float = 0.0
    top10_concentration_pct: float = 0.0
    top20_concentration_pct: float = 0.0

    hhi_index: float = 0.0               # 0..10,000 standard DOJ/FTC scale
    hhi_tier: str = "Unconcentrated"     # "Unconcentrated" | "Moderately Concentrated" | "Highly Concentrated"

    ndr_pct: float = 0.0                 # Net Dollar Retention (%)
    grr_pct: float = 0.0                 # Gross Dollar Retention (%, capped at 100%)
    logo_retention_pct: float = 0.0

    new_logo_rev_m: float = 0.0
    expansion_rev_m: float = 0.0
    contraction_churn_rev_m: float = 0.0

    red_flags: list[str] = field(default_factory=list)
    ranked_customers: list[dict[str, object]] = field(default_factory=list)


def analyze_customer_cohorts(customers: list[CustomerRecord]) -> CohortAnalysisResult:
    """
    Analyze customer revenue concentration (Top-5 / Top-10 / Top-20 %, HHI),
    Net Dollar Retention (NDR), Gross Dollar Retention (GRR), and commercial red flags.
    """
    if not customers:
        return CohortAnalysisResult()

    sorted_cust = sorted(customers, key=lambda c: c.current_rev_m, reverse=True)
    total_curr = sum(max(0.0, c.current_rev_m) for c in sorted_cust)
    total_prior = sum(max(0.0, c.prior_rev_m) for c in sorted_cust)
    rev_growth = ((total_curr - total_prior) / total_prior * 100.0) if total_prior > 0 else 0.0

    def _top_n_pct(n: int) -> float:
        if total_curr <= 0:
            return 0.0
        return sum(max(0.0, c.current_rev_m) for c in sorted_cust[:n]) / total_curr * 100.0

    top1_pct = _top_n_pct(1)
    top5_pct = _top_n_pct(5)
    top10_pct = _top_n_pct(10)
    top20_pct = _top_n_pct(20)

    # HHI on 0 - 10,000 scale using revenue shares (%)
    hhi = 0.0
    if total_curr > 0:
        for c in sorted_cust:
            share_pct = (max(0.0, c.current_rev_m) / total_curr) * 100.0
            hhi += share_pct ** 2

    if hhi > 2500.0:
        hhi_tier = "Highly Concentrated"
    elif hhi >= 1500.0:
        hhi_tier = "Moderately Concentrated"
    else:
        hhi_tier = "Unconcentrated"

    # Cohort retention metrics on customers active in prior period (prior_rev_m > 0)
    existing = [c for c in sorted_cust if c.prior_rev_m > 0]
    existing_prior_total = sum(c.prior_rev_m for c in existing)
    existing_curr_total = sum(max(0.0, c.current_rev_m) for c in existing)
    retained_capped_total = sum(min(max(0.0, c.current_rev_m), c.prior_rev_m) for c in existing)

    ndr_pct = (existing_curr_total / existing_prior_total * 100.0) if existing_prior_total > 0 else 0.0
    grr_pct = (retained_capped_total / existing_prior_total * 100.0) if existing_prior_total > 0 else 0.0
    retained_logos = sum(1 for c in existing if c.current_rev_m > 0)
    logo_ret_pct = (retained_logos / len(existing) * 100.0) if existing else 0.0

    new_logo_rev = sum(max(0.0, c.current_rev_m) for c in sorted_cust if c.prior_rev_m <= 0 and c.current_rev_m > 0)
    expansion_rev = sum(max(0.0, c.current_rev_m - c.prior_rev_m) for c in existing)
    contraction_churn_rev = sum(max(0.0, c.prior_rev_m - max(0.0, c.current_rev_m)) for c in existing)

    red_flags: list[str] = []
    if top1_pct >= 15.0:
        red_flags.append(
            f"Single-Customer Concentration Risk: Top account ({sorted_cust[0].name}) represents "
            f"{top1_pct:.1f}% of total revenue (>15% threshold); verify change-of-control clauses."
        )
    if top5_pct >= 40.0:
        red_flags.append(
            f"Top-5 Concentration Alert: Top 5 customers account for {top5_pct:.1f}% of revenue (>40% threshold)."
        )
    if top10_pct >= 60.0:
        red_flags.append(
            f"Top-10 Concentration Alert: Top 10 customers account for {top10_pct:.1f}% of revenue (>60% threshold)."
        )
    if hhi >= 1500.0:
        red_flags.append(
            f"Elevated Customer HHI ({hhi:,.0f} — {hhi_tier}): Revenue base exhibits structural concentration."
        )
    if existing_prior_total > 0 and grr_pct < 85.0:
        red_flags.append(
            f"Elevated Gross Churn: GRR of {grr_pct:.1f}% falls below the 85% institutional durability benchmark."
        )
    if existing_prior_total > 0 and ndr_pct < 100.0:
        red_flags.append(
            f"Net Cohort Contraction: NDR of {ndr_pct:.1f}% indicates existing customer base is shrinking YoY."
        )

    ranked_customers: list[dict[str, object]] = []
    for idx, c in enumerate(sorted_cust, start=1):
        share = (max(0.0, c.current_rev_m) / total_curr * 100.0) if total_curr > 0 else 0.0
        yoy = ((c.current_rev_m - c.prior_rev_m) / c.prior_rev_m * 100.0) if c.prior_rev_m > 0 else None
        ranked_customers.append(
            {
                "rank": idx,
                "name": c.name,
                "segment": c.segment,
                "prior_rev_m": round(c.prior_rev_m, 2),
                "current_rev_m": round(c.current_rev_m, 2),
                "share_pct": round(share, 2),
                "yoy_growth_pct": round(yoy, 1) if yoy is not None else None,
                "contract_term_months": c.contract_term_months,
            }
        )

    return CohortAnalysisResult(
        customer_count=len(sorted_cust),
        total_current_rev_m=round(total_curr, 2),
        total_prior_rev_m=round(total_prior, 2),
        revenue_growth_pct=round(rev_growth, 1),
        top1_concentration_pct=round(top1_pct, 1),
        top5_concentration_pct=round(top5_pct, 1),
        top10_concentration_pct=round(top10_pct, 1),
        top20_concentration_pct=round(top20_pct, 1),
        hhi_index=round(hhi, 1),
        hhi_tier=hhi_tier,
        ndr_pct=round(ndr_pct, 1),
        grr_pct=round(grr_pct, 1),
        logo_retention_pct=round(logo_ret_pct, 1),
        new_logo_rev_m=round(new_logo_rev, 2),
        expansion_rev_m=round(expansion_rev, 2),
        contraction_churn_rev_m=round(contraction_churn_rev, 2),
        red_flags=red_flags,
        ranked_customers=ranked_customers,
    )


# =====================================================================
# 4. Report-Aware Baseline Generator for Any DueDiligenceReport
# =====================================================================

@dataclass
class QoEDefaultInputs:
    """Complete baseline FDD inputs derived deterministically from a DueDiligenceReport."""
    target_name: str
    headline_ev_m: float
    revenue_m: float
    reported_ebitda_m: float
    adjustments: list[QoEAdjustment]
    monthly_nwc: list[NWCMonth]
    target_close_nwc_m: float
    debt_like_items: list[DebtLikeItem]
    cash_m: float
    customers: list[CustomerRecord]


def default_qoe_for_report(report: DueDiligenceReport) -> QoEDefaultInputs:
    """
    Build realistic, sector-calibrated baseline QoE, NWC, Debt-Like, and Customer Cohort
    inputs from any `DueDiligenceReport` so the FDD workbench loads populated out-of-the-box.
    """
    d = report.deal
    target = d.entity_b_name
    sector = (d.industry_sector or "").lower()
    ev_m = (d.deal_value_usd_b or 10.0) * 1000.0

    # Sector-calibrated revenue, EBITDA margin, NWC intensity, and customer profiles
    if any(k in sector for k in ("auto", "ev", "mobility", "vehicle", "industrial")):
        ev_rev_mult = 3.2
        ebitda_margin = 0.145
        nwc_pct_rev = 0.095
        cash_pct_ev = 0.24
        debt_pct_ev = 0.26
        is_concentrated = True
        cohort_profile = "auto"
    elif any(k in sector for k in ("cloud", "software", "saas", "ai", "data", "tech")):
        ev_rev_mult = 12.5
        ebitda_margin = 0.220
        nwc_pct_rev = -0.065  # Negative NWC driven by upfront annual deferred revenue
        cash_pct_ev = 0.08
        debt_pct_ev = 0.04
        is_concentrated = False
        cohort_profile = "saas"
    elif any(k in sector for k in ("pharma", "bio", "health", "therap", "med")):
        ev_rev_mult = 4.2
        ebitda_margin = 0.410
        nwc_pct_rev = 0.135
        cash_pct_ev = 0.19
        debt_pct_ev = 0.06
        is_concentrated = True
        cohort_profile = "pharma"
    else:
        ev_rev_mult = 2.8
        ebitda_margin = 0.215
        nwc_pct_rev = 0.110
        cash_pct_ev = 0.10
        debt_pct_ev = 0.22
        is_concentrated = False
        cohort_profile = "general"

    revenue_m = round(ev_m / ev_rev_mult, 1)
    reported_ebitda_m = round(revenue_m * ebitda_margin, 1)
    base_scale = max(reported_ebitda_m, revenue_m * 0.10)

    # Realistic QoE adjustments (mix of High/Medium/Low credibility + buyer haircut)
    adjustments = [
        QoEAdjustment(
            label="Non-Recurring Restructuring & Severance",
            amount_m=round(base_scale * 0.055, 1),
            category="Non-Recurring",
            credibility="High",
            rationale="Documented one-time facility consolidation and severance charges verified against GL.",
        ),
        QoEAdjustment(
            label="Stock-Based Compensation (Executive & Transaction)",
            amount_m=round(base_scale * 0.065, 1),
            category="Owner/SBC",
            credibility="Medium",
            rationale="Management proposes full SBC add-back; buyers scrutinize recurring engineering dilution.",
        ),
        QoEAdjustment(
            label="M&A Advisory, Legal & VDR Transaction Costs",
            amount_m=round(base_scale * 0.028, 1),
            category="Non-Recurring",
            credibility="High",
            rationale="Third-party legal, audit, and banker fees unrelated to ongoing operations.",
        ),
        QoEAdjustment(
            label="Pro-Forma Supplier Re-Pricing Run-Rate",
            amount_m=round(base_scale * 0.042, 1),
            category="Run-Rate",
            credibility="Low",
            rationale="Management annualized Q4 vendor rebate terms; limited full-year realization proof.",
        ),
        QoEAdjustment(
            label="FDD Haircut: Capitalized R&D / Out-of-Period Revenue",
            amount_m=round(-base_scale * 0.032, 1),
            category="Diligence Haircut",
            credibility="High",
            rationale="Buyer FDD adjustment reversing aggressive software capitalization and Q4 pull-forward.",
        ),
    ]

    # 12-month NWC seasonality curve
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    seasonal_factors = [0.94, 0.96, 1.02, 1.00, 0.98, 1.04, 1.01, 0.99, 1.06, 1.03, 0.97, 1.00]
    base_nwc = revenue_m * nwc_pct_rev

    monthly_nwc: list[NWCMonth] = []
    for mo, sf in zip(months, seasonal_factors):
        if nwc_pct_rev >= 0:
            ar = round(revenue_m * 0.145 * sf, 1)
            inv = round(revenue_m * (0.090 if cohort_profile in ("auto", "pharma") else 0.055) * sf, 1)
            oca = round(revenue_m * 0.025, 1)
            ap = round(revenue_m * 0.085 * (2.0 - sf) * 0.5 + revenue_m * 0.042, 1)
            accr = round(revenue_m * 0.045, 1)
            def_rev = round(revenue_m * 0.015, 1)
            nwc_val = round(base_nwc * sf, 1)
            # Reconcile AP so component sum equals nwc_val cleanly
            ap = round((ar + inv + oca) - accr - def_rev - nwc_val, 1)
        else:
            ar = round(revenue_m * 0.165 * sf, 1)
            inv = 0.0
            oca = round(revenue_m * 0.025, 1)
            ap = round(revenue_m * 0.045, 1)
            accr = round(revenue_m * 0.055, 1)
            nwc_val = round(base_nwc * sf, 1)
            def_rev = round((ar + inv + oca) - ap - accr - nwc_val, 1)

        monthly_nwc.append(
            NWCMonth(
                month=mo,
                ar_m=ar,
                inventory_m=inv,
                other_current_assets_m=oca,
                ap_m=ap,
                accrued_liabilities_m=accr,
                deferred_revenue_m=def_rev,
                nwc_m=nwc_val,
            )
        )

    avg_peg = sum(m.nwc_m or 0.0 for m in monthly_nwc) / len(monthly_nwc)
    # Slight shortfall at close (-2.5% of magnitude) to illustrate SPA purchase price adjustment
    target_close_nwc_m = round(avg_peg - abs(avg_peg) * 0.045, 1)

    cash_m = round(ev_m * cash_pct_ev, 1)
    fin_debt = round(ev_m * debt_pct_ev, 1)
    debt_like_items = [
        DebtLikeItem(
            name="Senior Notes & Term Loan Facilities",
            amount_m=fin_debt,
            category="Financial Debt",
            rationale="Principal balance plus accrued interest at closing.",
        ),
        DebtLikeItem(
            name="Capitalized Operating / Finance Lease Obligations",
            amount_m=round(ev_m * 0.022, 1),
            category="Lease / Off-BS",
            rationale="ASC 842 / IFRS 16 manufacturing & data-center finance leases treated as debt-like.",
        ),
        DebtLikeItem(
            name="Unfunded Pension & Deferred Compensation",
            amount_m=round(ev_m * 0.011, 1),
            category="Pension / Benefit",
            rationale="Tax-effected underfunded benefit obligations and change-of-control retention bonuses.",
        ),
        DebtLikeItem(
            name="Uncertain Tax Positions & Accrued Income Taxes",
            amount_m=round(ev_m * 0.008, 1),
            category="Tax Liability",
            rationale="FIN 48 / pre-closing federal & state tax exposures identified in tax DD.",
        ),
        DebtLikeItem(
            name="Unpaid Seller Transaction Expenses",
            amount_m=round(ev_m * 0.007, 1),
            category="Transaction Expenses",
            rationale="Seller M&A advisory fees and D&O tail insurance payable at close.",
        ),
    ]

    # Build a 22-account customer cohort matching sector concentration & retention dynamics
    customers = _build_sector_customer_cohort(revenue_m, cohort_profile, is_concentrated)

    return QoEDefaultInputs(
        target_name=target,
        headline_ev_m=round(ev_m, 1),
        revenue_m=revenue_m,
        reported_ebitda_m=reported_ebitda_m,
        adjustments=adjustments,
        monthly_nwc=monthly_nwc,
        target_close_nwc_m=target_close_nwc_m,
        debt_like_items=debt_like_items,
        cash_m=cash_m,
        customers=customers,
    )


def _build_sector_customer_cohort(
    total_revenue_m: float,
    profile: str,
    concentrated: bool,
) -> list[CustomerRecord]:
    """Generate a deterministic 22-customer cohort calibrated to industry dynamics."""
    if profile == "auto":
        names = [
            ("Amazon Logistics (EDV Fleet)", "Commercial Fleet", 0.215, 1.12, 60),
            ("Direct Consumer — West Coast", "D2C Retail", 0.140, 1.09, 12),
            ("Direct Consumer — Northeast", "D2C Retail", 0.115, 1.07, 12),
            ("Direct Consumer — South / Midwest", "D2C Retail", 0.095, 1.04, 12),
            ("Enterprise Fleet Leasing Co.", "Commercial Fleet", 0.068, 1.18, 36),
            ("AT&T Commercial Pilot Fleet", "Commercial Fleet", 0.048, 1.25, 36),
            ("National Utility Services", "Commercial Fleet", 0.036, 0.94, 24),
            ("Canada D2C Direct", "D2C Retail", 0.034, 1.10, 12),
        ]
    elif profile == "saas":
        names = [
            ("Global Tier-1 Investment Bank", "Strategic Enterprise", 0.068, 1.34, 36),
            ("Fortune 10 Retail Conglomerate", "Strategic Enterprise", 0.056, 1.28, 36),
            ("Hyperscale Telecom Carrier", "Strategic Enterprise", 0.049, 1.22, 36),
            ("Global Life Sciences Leader", "Strategic Enterprise", 0.044, 1.31, 24),
            ("Multinational CPG Group", "Strategic Enterprise", 0.039, 1.18, 24),
            ("Tier-1 European Insurer", "Enterprise", 0.035, 1.26, 24),
            ("US Federal Defense Agency", "Public Sector", 0.032, 1.15, 36),
            ("Global Semiconductor Fab", "Enterprise", 0.030, 1.40, 24),
        ]
    elif profile == "pharma":
        names = [
            ("McKesson Specialty Distribution", "Wholesaler", 0.245, 1.08, 36),
            ("Cencora (AmerisourceBergen)", "Wholesaler", 0.210, 1.06, 36),
            ("Cardinal Health Specialty", "Wholesaler", 0.165, 1.04, 36),
            ("NHS England / EU Tender", "Payer / Gov", 0.072, 1.11, 24),
            ("German GKV Statutory Consortium", "Payer / Gov", 0.054, 1.05, 24),
            ("CVS Caremark Specialty", "PBM / Specialty", 0.045, 1.09, 24),
            ("OptumRx Specialty Network", "PBM / Specialty", 0.038, 0.96, 24),
            ("France CEPS Hospital Direct", "Payer / Gov", 0.028, 1.03, 24),
        ]
    else:
        names = [
            ("Apex Global Holdings", "Key Account", 0.110, 1.09, 36),
            ("Meridian Industrial Group", "Key Account", 0.088, 1.05, 24),
            ("NorthStar Logistics", "Key Account", 0.072, 1.12, 24),
            ("Continental Retail Partners", "Enterprise", 0.060, 0.93, 24),
            ("Vanguard Manufacturing", "Enterprise", 0.052, 1.07, 24),
            ("Summit Energy Corp", "Enterprise", 0.046, 1.02, 12),
            ("Pacific Healthcare Systems", "Enterprise", 0.042, 1.14, 24),
            ("Atlantic Financial Services", "Enterprise", 0.038, 0.97, 12),
        ]

    used_share = sum(item[2] for item in names)
    remaining_share = max(0.05, 1.0 - used_share)

    # Add 14 mid-market / long-tail accounts (including 2 new logos and 1 churned account)
    tail_weights = [14, 12, 11, 10, 9, 8.5, 8, 7.5, 7, 6.5, 6, 5.5, 5, 4]
    w_sum = sum(tail_weights)

    records: list[CustomerRecord] = []
    for name, seg, share, growth_mult, term in names:
        curr = round(total_revenue_m * share, 2)
        prior = round(curr / growth_mult, 2)
        records.append(
            CustomerRecord(
                name=name,
                current_rev_m=curr,
                prior_rev_m=prior,
                segment=seg,
                contract_term_months=term,
            )
        )

    for idx, w in enumerate(tail_weights, start=1):
        share = remaining_share * (w / w_sum)
        curr = round(total_revenue_m * share, 2)
        if idx in (12, 13):
            # New logo won during current year
            prior = 0.0
        elif idx == 14:
            # Churned account (had prior rev, dropped to 0)
            prior = round(curr * 1.1, 2)
            curr = 0.0
        else:
            mult = 1.16 if profile == "saas" else (1.03 if not concentrated else 0.98)
            if idx in (5, 9):
                mult = 0.88  # modest contraction
            prior = round(curr / mult, 2)

        records.append(
            CustomerRecord(
                name=f"Mid-Market Account #{idx:02d}",
                current_rev_m=curr,
                prior_rev_m=prior,
                segment="Mid-Market",
                contract_term_months=12,
            )
        )

    return records
