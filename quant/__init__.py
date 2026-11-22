"""
Institutional Quantitative M&A Valuation & Financial Due Diligence Suite (`quant`).

Exports:
  - Quality of Earnings (QoE), NWC Peg / CFDF Bridge & Customer Cohort Analytics (`quant.qoe_nwc`)
  - 5-Year Accretion/Dilution Merger Model, PE LBO Engine & Section 338(h)(10) Tax Step-Up (`quant.merger_lbo`)
  - Wall Street Valuation Football Field & Correlated Monte Carlo Synergy Simulation (`quant.comps_montecarlo`)
"""
from __future__ import annotations

from quant.comps_montecarlo import (
    FootballFieldResult,
    MonteCarloConfig,
    MonteCarloResult,
    ValuationRange,
    compute_football_field,
    run_monte_carlo_synergies,
)
from quant.merger_lbo import (
    LBOInputs,
    LBOResult,
    LBOYearRow,
    MergerInputs,
    MergerModelResult,
    MergerYearRow,
    TaxStepUpResult,
    compute_lbo_model,
    compute_merger_model,
    compute_tax_step_up,
    default_lbo_inputs_for_report,
    default_merger_inputs_for_report,
)
from quant.qoe_nwc import (
    CohortAnalysisResult,
    CustomerRecord,
    DebtLikeItem,
    NWCMonth,
    NWCResult,
    QoEAdjustment,
    QoEDefaultInputs,
    QoEResult,
    analyze_customer_cohorts,
    compute_nwc_peg,
    compute_qoe_bridge,
    default_qoe_for_report,
)

__all__ = [
    # QoE, NWC & Customer Cohorts
    "QoEAdjustment",
    "QoEResult",
    "QoEDefaultInputs",
    "compute_qoe_bridge",
    "NWCMonth",
    "DebtLikeItem",
    "NWCResult",
    "compute_nwc_peg",
    "CustomerRecord",
    "CohortAnalysisResult",
    "analyze_customer_cohorts",
    "default_qoe_for_report",
    # Merger Model, LBO & Tax Step-Up
    "MergerInputs",
    "MergerYearRow",
    "MergerModelResult",
    "compute_merger_model",
    "LBOInputs",
    "LBOYearRow",
    "LBOResult",
    "compute_lbo_model",
    "TaxStepUpResult",
    "compute_tax_step_up",
    "default_merger_inputs_for_report",
    "default_lbo_inputs_for_report",
    # Valuation Football Field & Correlated Monte Carlo
    "ValuationRange",
    "FootballFieldResult",
    "compute_football_field",
    "MonteCarloConfig",
    "MonteCarloResult",
    "run_monte_carlo_synergies",
]
