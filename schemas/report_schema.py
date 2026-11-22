"""
Pydantic schemas defining the institutional MECE 5-section Due Diligence report structure.

The LLM is instructed to return JSON conforming to `DueDiligenceReport`.
This file is the single source of truth for what a "valid" Investment Committee memo looks like.
All new institutional fields (workstreams, risk register, PMI roadmap, pillar scores, VDR Q&A)
have clean defaults so legacy JSON reports remain 100% backwards-compatible.
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


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class SynergyType(str, Enum):
    REVENUE = "Revenue"
    COST = "Cost"
    CAPITAL = "Capital"


class RiskDimension(str, Enum):
    STRATEGIC = "Strategic"
    OPERATIONAL_TECH = "Operational & Tech"
    CULTURAL = "Cultural & Talent"
    REGULATORY_ESG = "Regulatory & ESG"
    FINANCIAL = "Financial"


# ---------- Reusable building blocks ----------

class SourceCitation(BaseModel):
    """Every claim must tie back to a specific document + location."""
    source_document: str = Field(..., description="e.g. 'Entity_A_10K_2024.pdf'")
    location: str = Field(..., description="e.g. 'p.47, Note 9 — Leases' or 'Earnings Call Q3, p.12'")


class Claim(BaseModel):
    """A grounded assertion: claim text + the sources backing it + optional analytical metadata."""
    claim: str
    sources: list[SourceCitation] = Field(default_factory=list)
    severity: Optional[Severity] = Field(None, description="Analytical materiality / severity rating.")
    confidence: Optional[Confidence] = Field(None, description="Evidence confidence level.")
    quantified_impact_usd_m: Optional[float] = Field(
        None, description="Optional quantified annual impact in USD millions."
    )


# ---------- Structured Institutional Extensions ----------

class SynergyWorkstream(BaseModel):
    """Granular synergy lever feeding the deterministic DCF Synergy Valuation Model."""
    name: str = Field(..., description="Concise lever name, e.g. 'E/E Architecture & Software Consolidation'")
    synergy_type: SynergyType
    annual_run_rate_usd_m: float = Field(..., ge=0.0, description="Steady-state annual pre-tax synergy ($M).")
    one_time_cost_usd_m: float = Field(0.0, ge=0.0, description="One-time implementation cost ($M).")
    probability_pct: float = Field(75.0, ge=0.0, le=100.0, description="Risk-adjusted realization probability (%).")
    ramp_year1_pct: float = Field(25.0, ge=0.0, le=100.0, description="Year 1 capture % of run-rate.")
    ramp_year2_pct: float = Field(65.0, ge=0.0, le=100.0, description="Year 2 capture % of run-rate.")
    ramp_year3_pct: float = Field(100.0, ge=0.0, le=100.0, description="Year 3 capture % of run-rate.")
    owner: str = Field("Integration Management Office (IMO)", description="Accountable executive owner.")
    rationale: str = Field("", description="Concise consulting rationale and key assumption.")
    sources: list[SourceCitation] = Field(default_factory=list)


class RiskItem(BaseModel):
    """Structured entry for the Investment Committee Risk Heatmap Matrix."""
    title: str
    dimension: RiskDimension
    severity: Severity
    likelihood: int = Field(3, ge=1, le=5, description="Likelihood score 1 (Rare) to 5 (Almost Certain).")
    impact: int = Field(3, ge=1, le=5, description="Impact score 1 (Minor) to 5 (Catastrophic).")
    is_deal_breaker: bool = False
    mitigation: str = Field(..., description="Actionable pre-signing or PMI mitigation strategy.")
    sources: list[SourceCitation] = Field(default_factory=list)


class IntegrationMilestone(BaseModel):
    """Post-Merger Integration (PMI) execution roadmap milestone."""
    phase: str = Field(..., description="e.g. 'Pre-Close / Signing', 'Day 1', 'First 100 Days', 'Months 4–12', 'Months 13–36'")
    workstream: str = Field(..., description="e.g. 'Commercial', 'Tech & Engineering', 'People & Culture', 'Regulatory'")
    objective: str
    owner: str
    kpi: str = Field(..., description="Measurable target KPI.")


class PillarScore(BaseModel):
    """0-100 institutional attractiveness / readiness score per MECE pillar."""
    pillar: str
    score: int = Field(..., ge=0, le=100)
    summary: str = ""


# ---------- Section 1: Executive Summary ----------

class ExecutiveSummary(BaseModel):
    strategic_rationale: str = Field(..., description="3-sentence summary of why this deal does/doesn't make sense.")
    integration_complexity: IntegrationComplexity
    complexity_justification: str
    top_deal_breaker_risks: list[Claim] = Field(..., min_length=1, max_length=5,
                                                description="Top 1-5 red flags from the data.")
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

    # --- Optional market / sector metadata ---
    acquirer_ticker: Optional[str] = None
    target_ticker: Optional[str] = None
    industry_sector: Optional[str] = None
    overall_conviction_score: Optional[int] = Field(
        None, ge=0, le=100, description="Overall IC conviction index (0-100)."
    )

    # --- Financial synergy estimates (drive the metric cards & DCF baseline) ---
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

    # --- Valuation assumptions ---
    wacc_pct: float = Field(9.0, ge=1.0, le=30.0, description="Discount rate / WACC (%).")
    tax_rate_pct: float = Field(25.0, ge=0.0, le=60.0, description="Effective corporate tax rate (%).")
    terminal_growth_pct: float = Field(2.0, ge=0.0, le=8.0, description="Perpetual synergy growth rate (%).")

    # --- Provenance & run metadata ---
    is_mock: bool = False
    documents_analyzed: list[str] = Field(default_factory=list)


class DueDiligenceReport(BaseModel):
    """The complete institutional MECE M&A Due Diligence & Synergy Intelligence Memo."""
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

    # --- Institutional extensions (optional / defaulted for backwards compatibility) ---
    synergy_workstreams: list[SynergyWorkstream] = Field(default_factory=list)
    risk_register: list[RiskItem] = Field(default_factory=list)
    integration_roadmap: list[IntegrationMilestone] = Field(default_factory=list)
    pillar_scores: list[PillarScore] = Field(default_factory=list)
    valuation_commentary: Optional[str] = None
    due_diligence_questions: list[str] = Field(
        default_factory=list,
        description="High-priority Virtual Data Room (VDR) & Management Q&A follow-up questions.",
    )

    # ---------- Analytical audit helpers ----------

    def all_claims(self) -> list[Claim]:
        """Flatten all Claim objects across Executive Summary and Sections 2–5."""
        claims: list[Claim] = list(self.executive_summary.top_deal_breaker_risks)
        for section in (self.strategic_fit, self.operational_tech, self.cultural_risk, self.regulatory_esg):
            for field_name in type(section).model_fields:
                claims.extend(getattr(section, field_name))
        return claims

    def citation_count(self) -> int:
        """Total number of source citations across claims, workstreams, and risks."""
        total = sum(len(c.sources) for c in self.all_claims())
        total += sum(len(w.sources) for w in self.synergy_workstreams)
        total += sum(len(r.sources) for r in self.risk_register)
        return total

    def unique_sources(self) -> list[str]:
        """Deduplicated ordered list of all cited source documents."""
        seen: list[str] = []
        for c in self.all_claims():
            for s in c.sources:
                if s.source_document not in seen:
                    seen.append(s.source_document)
        for w in self.synergy_workstreams:
            for s in w.sources:
                if s.source_document not in seen:
                    seen.append(s.source_document)
        for r in self.risk_register:
            for s in r.sources:
                if s.source_document not in seen:
                    seen.append(s.source_document)
        return seen

    def grounding_ratio(self) -> float:
        """Fraction of claims (0.0 to 1.0) backed by at least one explicit source citation."""
        claims = self.all_claims()
        if not claims:
            return 0.0
        grounded = sum(1 for c in claims if len(c.sources) > 0)
        return grounded / len(claims)
