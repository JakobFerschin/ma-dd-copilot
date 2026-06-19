"""
Pydantic schemas defining the MECE 5-section Due Diligence report structure.
The LLM is instructed to return JSON conforming to `DueDiligenceReport`.
This file is the single source of truth for what a "valid" report looks like.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------- Enums for constrained fields ----------

class IntegrationComplexity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class Recommendation(str, Enum):
    GO = "Go"
    NO_GO = "No-Go"
    CONDITIONAL = "Conditional Go"


class Severity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


# ---------- Reusable building blocks ----------

class SourceCitation(BaseModel):
    """Every claim must tie back to a specific document + location."""
    source_document: str = Field(..., description="e.g. 'Entity_A_10K_2024.pdf'")
    location: str = Field(..., description="e.g. 'p.47, Note 9 — Leases' or 'Earnings Call Q3, p.12'")


class Claim(BaseModel):
    """A grounded assertion: claim text + the sources backing it."""
    claim: str
    sources: list[SourceCitation] = Field(default_factory=list)


# ---------- Section 1: Executive Summary ----------

class ExecutiveSummary(BaseModel):
    strategic_rationale: str = Field(..., description="3-sentence summary of why this deal does/doesn't make sense.")
    integration_complexity: IntegrationComplexity
    complexity_justification: str
    top_deal_breaker_risks: list[Claim] = Field(..., min_length=1, max_length=3,
                                                description="Top 1-3 red flags from the data.")
    recommendation: Recommendation
    recommendation_rationale: str = Field(..., description="Definitive stance grounded in available data.")


# ---------- Section 2: Strategic Fit & Commercial Synergies ----------

class StrategicFit(BaseModel):
    market_position_overlap: list[Claim]
    cross_selling_potential: list[Claim]
    revenue_synergies: list[Claim]


# ---------- Section 3: Operational & Technology ----------

class OperationalTech(BaseModel):
    tech_stack_clash: list[Claim]
    supply_chain_redundancies: list[Claim]
    cost_synergies: list[Claim]


# ---------- Section 4: Cultural & Human Capital ----------

class CulturalRisk(BaseModel):
    leadership_style: list[Claim]
    talent_retention_risk: list[Claim]


# ---------- Section 5: Regulatory, ESG & Macro ----------

class RegulatoryESG(BaseModel):
    antitrust_flags: list[Claim]
    esg_csrd_gaps: list[Claim]


# ---------- Top-level report ----------

class DealMeta(BaseModel):
    entity_a_name: str
    entity_b_name: str
    deal_type: str = Field("Hypothetical full acquisition", description="e.g. 'All-cash majority takeover'")
    deal_value_usd_b: Optional[float] = Field(None, description="Approx deal value in USD billions")
    analysis_date: str
    time_horizon_months: Optional[int] = None

    # --- Financial synergy estimates (drive the metric cards) ---
    # All optional: the LLM populates what it can ground; blanks render as "—".
    estimated_revenue_synergy_usd_b: Optional[float] = Field(
        None, description="Estimated annual run-rate revenue synergy at horizon (USD B)."
    )
    estimated_cost_synergy_usd_b: Optional[float] = Field(
        None, description="Estimated annual run-rate cost synergy at horizon (USD B)."
    )
    target_synergy_npv_usd_b: Optional[float] = Field(
        None, description="Net Present Value of total synergy capture over the horizon (USD B)."
    )
    estimated_integration_cost_usd_b: Optional[float] = Field(
        None, description="One-time integration cost (USD B)."
    )


class DueDiligenceReport(BaseModel):
    """The complete MECE M&A Due Diligence & Synergy Intelligence Memo."""
    deal: DealMeta
    executive_summary: ExecutiveSummary
    strategic_fit: StrategicFit
    operational_tech: OperationalTech
    cultural_risk: CulturalRisk
    regulatory_esg: RegulatoryESG
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Sections where public data was insufficient — each mapped to 'Insufficient public data for definitive assessment.'",
    )
