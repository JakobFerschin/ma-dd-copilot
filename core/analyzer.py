"""
M&A Due Diligence Analyzer (LIVE + Rich Document-Aware MOCK).

Key upgrades:
  - Session-isolated `config: Optional[Settings]` (never requires mutating process globals).
  - Exponential-backoff retries & configurable timeout on transient LLM/network failures.
  - Hardened JSON extraction: strips markdown fences AND extracts outermost JSON object
    if a model emits extraneous prose around JSON.
  - Deterministic DCF NPV backfill: when an LLM returns itemized `synergy_workstreams`
    without populating `target_synergy_npv_usd_b`, computes it deterministically via
    `core.financial_model.compute_synergy_dcf`.
  - Rich, document-aware MOCK mode: inspects actual uploaded filenames, page counts,
    tables, and financial themes so offline demos exercise every institutional module
    (MECE sections, DCF workstreams, Risk Register, PMI Roadmap, Pillar Scores, VDR Q&A).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

from config.settings import Settings, resolve_settings
from core.financial_model import compute_synergy_dcf
from prompts.system_prompt import SYSTEM_PROMPT
from schemas.report_schema import (
    Confidence,
    DueDiligenceReport,
    IntegrationComplexity,
    Recommendation,
    RiskDimension,
    Severity,
    SynergyType,
)
from utils.models import EntityDocuments

logger = logging.getLogger(__name__)


class AnalyzerError(RuntimeError):
    """Raised when live-mode analysis fails in a way the UI should surface cleanly."""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?|\n?\s*```\s*$", re.IGNORECASE | re.DOTALL)


# =====================================================================
# Deal parameters passed in from the UI
# =====================================================================

class DealParams:
    """Structured container for deal context entered in the UI."""
    def __init__(
        self,
        deal_value_usd_b: Optional[float] = None,
        deal_type: str = "Hypothetical full acquisition",
        time_horizon_months: int = 36,
        industry_sector: Optional[str] = None,
        wacc_pct: float = 9.0,
        tax_rate_pct: float = 25.0,
        terminal_growth_pct: float = 2.0,
    ):
        self.deal_value_usd_b = deal_value_usd_b
        self.deal_type = deal_type
        self.time_horizon_months = time_horizon_months
        self.industry_sector = industry_sector
        self.wacc_pct = wacc_pct
        self.tax_rate_pct = tax_rate_pct
        self.terminal_growth_pct = terminal_growth_pct


# =====================================================================
# MOCK MODE — rich, document-aware institutional stub
# =====================================================================

def _mock_report(entity_a: EntityDocuments, entity_b: EntityDocuments, params: DealParams) -> DueDiligenceReport:
    """Return a rich, fully populated institutional report referencing the actual uploaded files."""
    name_a = entity_a.entity_name or "Entity A (Acquirer)"
    name_b = entity_b.entity_name or "Entity B (Target)"
    deal_val = params.deal_value_usd_b if params.deal_value_usd_b is not None else 12.0
    scale = max(deal_val, 1.0) / 15.0  # scales mock synergy sizing proportionally to deal value

    def cite(entity: EntityDocuments, idx: int, page: str) -> dict:
        docs = entity.documents
        if not docs:
            return {"source_document": f"{entity.entity_name}.pdf", "location": page}
        doc = docs[min(idx, len(docs) - 1)]
        p_num = min(max(1, int(re.sub(r"\D", "", page) or "1")), max(doc.num_pages, 1))
        return {"source_document": doc.filename, "location": f"p.{p_num}"}

    rev_1 = round(650.0 * scale, 1)
    rev_2 = round(420.0 * scale, 1)
    cost_1 = round(480.0 * scale, 1)
    cost_2 = round(310.0 * scale, 1)
    int_1 = round(220.0 * scale, 1)
    int_2 = round(160.0 * scale, 1)
    int_3 = round(190.0 * scale, 1)
    int_4 = round(130.0 * scale, 1)

    total_rev_b = round((rev_1 + rev_2) / 1000.0, 2)
    total_cost_b = round((cost_1 + cost_2) / 1000.0, 2)
    total_int_b = round((int_1 + int_2 + int_3 + int_4) / 1000.0, 2)

    all_docs = [*entity_a.document_names, *entity_b.document_names]

    report = DueDiligenceReport(
        deal={
            "entity_a_name": name_a,
            "entity_b_name": name_b,
            "deal_type": params.deal_type,
            "deal_value_usd_b": params.deal_value_usd_b,
            "industry_sector": params.industry_sector or "Cross-Sector Strategic M&A",
            "analysis_date": datetime.now().strftime("%Y-%m-%d"),
            "time_horizon_months": params.time_horizon_months,
            "overall_conviction_score": 76,
            "estimated_revenue_synergy_usd_b": total_rev_b,
            "estimated_cost_synergy_usd_b": total_cost_b,
            "target_synergy_npv_usd_b": None,  # filled deterministically below
            "estimated_integration_cost_usd_b": total_int_b,
            "wacc_pct": params.wacc_pct,
            "tax_rate_pct": params.tax_rate_pct,
            "terminal_growth_pct": params.terminal_growth_pct,
            "is_mock": True,
            "documents_analyzed": all_docs,
        },
        executive_summary={
            "strategic_rationale": (
                f"[DEMO / MOCK] Combining {name_a} with {name_b} pairs {name_a}'s established distribution scale and "
                f"balance-sheet capacity ({entity_a.total_pages} pages analyzed across {len(entity_a.documents)} filing(s)) "
                f"with {name_b}'s differentiated product and technology capabilities ({entity_b.total_pages} pages analyzed). "
                f"Bottom-up run-rate synergies of ~${total_rev_b + total_cost_b:.2f}B/yr support value creation over a "
                f"{params.time_horizon_months}-month capture horizon, subject to disciplined post-merger execution."
            ),
            "integration_complexity": IntegrationComplexity.HIGH,
            "complexity_justification": (
                f"[DEMO / MOCK] Integrating {name_b}'s product/engineering systems and operating cadence into {name_a} "
                f"requires dedicated IMO governance, ring-fenced key-talent retention, and phased IT consolidation."
            ),
            "top_deal_breaker_risks": [
                {
                    "claim": f"[DEMO / MOCK] Regulatory & Merger Control Exposure: Overlapping core segments between {name_a} and {name_b} may trigger extended antitrust review.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.CRITICAL,
                    "confidence": Confidence.MEDIUM,
                    "quantified_impact_usd_m": round(450.0 * scale, 1),
                },
                {
                    "claim": f"[DEMO / MOCK] Key Technical & Commercial Talent Attrition: Post-signing uncertainty creates elevated flight risk across {name_b}'s core engineering and go-to-market leadership.",
                    "sources": [cite(entity_b, 0, "p.1")],
                    "severity": Severity.HIGH,
                    "confidence": Confidence.MEDIUM,
                    "quantified_impact_usd_m": round(320.0 * scale, 1),
                },
            ],
            "recommendation": Recommendation.CONDITIONAL,
            "recommendation_rationale": (
                f"[DEMO / MOCK] Proceed conditional on regulatory pre-clearance assessment, binding 24–36 month retention "
                f"agreements for key {name_b} leadership, and VDR validation of customer concentration & IT migration costs."
            ),
        },
        strategic_fit={
            "market_position_overlap": [
                {
                    "claim": f"[DEMO / MOCK] Complementary customer tiers between {name_a} and {name_b} limit direct revenue cannibalization while expanding addressable enterprise wallet share.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.MEDIUM,
                    "confidence": Confidence.MEDIUM,
                },
            ],
            "cross_selling_potential": [
                {
                    "claim": f"[DEMO / MOCK] Cross-selling {name_b}'s flagship offerings into {name_a}'s installed enterprise base represents an estimated ${rev_1:.0f}M annual run-rate opportunity.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.HIGH,
                    "confidence": Confidence.MEDIUM,
                    "quantified_impact_usd_m": rev_1,
                },
            ],
            "revenue_synergies": [
                {
                    "claim": f"[DEMO / MOCK] Bundled platform go-to-market and international channel acceleration unlock an additional ${rev_2:.0f}M in annual run-rate revenue synergies by Month {params.time_horizon_months}.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.MEDIUM,
                    "confidence": Confidence.MEDIUM,
                    "quantified_impact_usd_m": rev_2,
                },
            ],
        },
        operational_tech={
            "tech_stack_clash": [
                {
                    "claim": f"[DEMO / MOCK] Divergent cloud/ERP infrastructure between {name_a} and {name_b} necessitates an API-first bridging layer before full systems migration.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.HIGH,
                    "confidence": Confidence.MEDIUM,
                },
            ],
            "supply_chain_redundancies": [
                {
                    "claim": f"[DEMO / MOCK] Shared Tier-1 vendor and procurement volume consolidation delivers an estimated ${cost_1:.0f}M in annual direct & indirect spend savings.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.HIGH,
                    "confidence": Confidence.HIGH,
                    "quantified_impact_usd_m": cost_1,
                },
            ],
            "cost_synergies": [
                {
                    "claim": f"[DEMO / MOCK] Consolidating duplicate SG&A, real estate, and overlapping corporate functions yields ~${cost_2:.0f}M in steady-state annual savings.",
                    "sources": [cite(entity_a, 0, "p.1")],
                    "severity": Severity.MEDIUM,
                    "confidence": Confidence.HIGH,
                    "quantified_impact_usd_m": cost_2,
                },
            ],
        },
        cultural_risk={
            "leadership_style": [
                {
                    "claim": f"[DEMO / MOCK] Operating cadence divergence between {name_a}'s scaled governance processes and {name_b}'s high-velocity execution culture requires an explicit Day-1 operating model.",
                    "sources": [cite(entity_b, 0, "p.1")],
                    "severity": Severity.MEDIUM,
                    "confidence": Confidence.MEDIUM,
                },
            ],
            "talent_retention_risk": [
                {
                    "claim": f"[DEMO / MOCK] Engineering and product leadership at {name_b} face elevated competitive poaching risk during the sign-to-close window.",
                    "sources": [cite(entity_b, 0, "p.1")],
                    "severity": Severity.HIGH,
                    "confidence": Confidence.MEDIUM,
                },
            ],
        },
        regulatory_esg={
            "antitrust_flags": [
                {
                    "claim": f"[DEMO / MOCK] Mandatory merger control filings (HSR / EU Commission) anticipated based on combined revenue thresholds.",
                    "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
                    "severity": Severity.HIGH,
                    "confidence": Confidence.MEDIUM,
                },
            ],
            "esg_csrd_gaps": [
                {
                    "claim": f"[DEMO / MOCK] {name_b} requires Scope 3 emissions and EU CSRD double-materiality alignment to match {name_a}'s consolidated reporting cadence.",
                    "sources": [cite(entity_b, 0, "p.1")],
                    "severity": Severity.MEDIUM,
                    "confidence": Confidence.MEDIUM,
                },
            ],
        },
        data_gaps=[
            "[DEMO / MOCK] Running in offline MOCK mode (no LLM API key configured). Add an OpenAI-compatible API key in the sidebar for live deep-reading of uploaded filings.",
            "Granular customer cohort retention, change-of-control vendor contracts, and unvested equity schedules require Virtual Data Room (VDR) Phase-2 disclosure.",
        ],
        synergy_workstreams=[
            {
                "name": f"Enterprise Cross-Sell ({name_b} → {name_a} Base)",
                "synergy_type": SynergyType.REVENUE,
                "annual_run_rate_usd_m": rev_1,
                "one_time_cost_usd_m": int_1,
                "probability_pct": 70.0,
                "ramp_year1_pct": 20.0,
                "ramp_year2_pct": 60.0,
                "ramp_year3_pct": 100.0,
                "owner": "Chief Commercial Officer",
                "rationale": f"Distributes {name_b} portfolio across {name_a}'s enterprise client relationships.",
                "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
            },
            {
                "name": "International Channel & Bundled Pricing Uplift",
                "synergy_type": SynergyType.REVENUE,
                "annual_run_rate_usd_m": rev_2,
                "one_time_cost_usd_m": int_2,
                "probability_pct": 65.0,
                "ramp_year1_pct": 15.0,
                "ramp_year2_pct": 50.0,
                "ramp_year3_pct": 100.0,
                "owner": "VP Global Sales",
                "rationale": "Accelerated international roll-out using existing regional hubs.",
                "sources": [cite(entity_a, 0, "p.1")],
            },
            {
                "name": "Procurement, Cloud & Supply Chain Consolidation",
                "synergy_type": SynergyType.COST,
                "annual_run_rate_usd_m": cost_1,
                "one_time_cost_usd_m": int_3,
                "probability_pct": 85.0,
                "ramp_year1_pct": 35.0,
                "ramp_year2_pct": 75.0,
                "ramp_year3_pct": 100.0,
                "owner": "Chief Operating Officer",
                "rationale": "Volume tier consolidation across top direct/indirect suppliers.",
                "sources": [cite(entity_a, 0, "p.1"), cite(entity_b, 0, "p.1")],
            },
            {
                "name": "SG&A & Corporate Overhead Rationalization",
                "synergy_type": SynergyType.COST,
                "annual_run_rate_usd_m": cost_2,
                "one_time_cost_usd_m": int_4,
                "probability_pct": 90.0,
                "ramp_year1_pct": 45.0,
                "ramp_year2_pct": 85.0,
                "ramp_year3_pct": 100.0,
                "owner": "Chief Financial Officer",
                "rationale": "Eliminates duplicate public-company, finance, legal, and IT overhead.",
                "sources": [cite(entity_a, 0, "p.1")],
            },
        ],
        risk_register=[
            {
                "title": "Antitrust / Regulatory Clearance Delay",
                "dimension": RiskDimension.REGULATORY_ESG,
                "severity": Severity.CRITICAL,
                "likelihood": 3,
                "impact": 5,
                "is_deal_breaker": True,
                "mitigation": "Prepare upfront clean-team economic submission and pre-drafted behavioral remedy package.",
                "sources": [cite(entity_a, 0, "p.1")],
            },
            {
                "title": "Key Engineering & Product Talent Attrition",
                "dimension": RiskDimension.CULTURAL,
                "severity": Severity.HIGH,
                "likelihood": 4,
                "impact": 4,
                "is_deal_breaker": True,
                "mitigation": "Deploy 36-month milestone-based retention packages before Day-1 announcement.",
                "sources": [cite(entity_b, 0, "p.1")],
            },
            {
                "title": "Core IT / ERP & Cloud Migration Overruns",
                "dimension": RiskDimension.OPERATIONAL_TECH,
                "severity": Severity.HIGH,
                "likelihood": 3,
                "impact": 4,
                "is_deal_breaker": False,
                "mitigation": "Adopt phased API-first integration; avoid disruptive Day-1 ERP cutover.",
                "sources": [cite(entity_b, 0, "p.1")],
            },
            {
                "title": "EU CSRD & Scope 3 Reporting Gap",
                "dimension": RiskDimension.REGULATORY_ESG,
                "severity": Severity.MEDIUM,
                "likelihood": 4,
                "impact": 2,
                "is_deal_breaker": False,
                "mitigation": "Integrate target into acquirer's ESG assurance calendar within 180 days.",
                "sources": [cite(entity_b, 0, "p.1")],
            },
        ],
        integration_roadmap=[
            {
                "phase": "Pre-Close / Signing",
                "workstream": "Regulatory & Legal",
                "objective": "Submit HSR / EU merger control filings and finalize clean-team synergy verification.",
                "owner": "General Counsel",
                "kpi": "Unconditional or agreed-remedy clearance within target window",
            },
            {
                "phase": "Day 1",
                "workstream": "People & Governance",
                "objective": "Stand up Integration Management Office (IMO) and issue key-talent retention agreements.",
                "owner": "IMO Lead & CHRO",
                "kpi": ">95% retention agreement acceptance among top-tier talent",
            },
            {
                "phase": "First 100 Days",
                "workstream": "Procurement & Commercial",
                "objective": "Launch joint enterprise account cross-sell pilots and renegotiate overlapping vendor contracts.",
                "owner": "CCO & CPO",
                "kpi": "Top 25 joint accounts engaged; 30% of Year-1 procurement savings contracted",
            },
            {
                "phase": "Months 4–12",
                "workstream": "Tech & Operations",
                "objective": "Complete unified data/API bridge and execute SG&A & shared-services consolidation.",
                "owner": "CTO & CFO",
                "kpi": "Zero customer-facing SLA disruption; >65% cost synergy run-rate achieved",
            },
        ],
        pillar_scores=[
            {"pillar": "Strategic Fit", "score": 82, "summary": "Strong commercial complementarity and cross-sell headroom."},
            {"pillar": "Operational & Tech", "score": 73, "summary": "High procurement synergy potential; IT integration requires phasing."},
            {"pillar": "Cultural Readiness", "score": 66, "summary": "Manageable with clear Day-1 operating charter and retention pools."},
            {"pillar": "Regulatory & ESG", "score": 70, "summary": "Standard merger control review; CSRD alignment tractable."},
            {"pillar": "Financial Value Creation", "score": 80, "summary": "Solid risk-adjusted Synergy NPV relative to transaction value."},
        ],
        valuation_commentary=(
            f"[DEMO / MOCK] Based on the bottom-up workstream assumptions at {params.wacc_pct:.1f}% WACC and "
            f"{params.tax_rate_pct:.0f}% effective tax rate, risk-adjusted synergies generate meaningful positive NPV "
            f"across the {params.time_horizon_months}-month horizon. Connect a live LLM API key in the sidebar to "
            f"extract exact filing figures and tailor every lever to the uploaded documents."
        ),
        due_diligence_questions=[
            f"Provide top-20 customer concentration, contract renewal dates, and change-of-control clauses for {name_b}.",
            f"Share detailed 3-year standalone financial forecast and capex schedule for {name_b} by business segment.",
            f"Provide unvested equity / retention exposure for {name_b}'s top 50 engineering and commercial leaders.",
            f"Detail material third-party software licenses, cloud commitments, and open-source IP audit findings.",
            f"Confirm any pending litigation, tax audits, or regulatory inquiries across key operating jurisdictions.",
        ],
    )

    # Deterministically compute the Synergy NPV from the workstreams so headline & DCF match 100%
    dcf = compute_synergy_dcf(report)
    report.deal.target_synergy_npv_usd_b = dcf.total_synergy_npv_b
    return report


# =====================================================================
# LIVE MODE — real OpenAI-compatible LLM call with retry & validation
# =====================================================================

def _build_user_message(
    entity_a: EntityDocuments,
    entity_b: EntityDocuments,
    params: DealParams,
    max_chars_to_llm: int,
) -> str:
    """Assemble the user prompt with proportionally budgeted, provenance-tagged text & tables."""
    cap = max(max_chars_to_llm // 2, 4000)
    deal_val_str = f"${params.deal_value_usd_b:.2f}B USD" if params.deal_value_usd_b is not None else "Not specified"
    sector_str = params.industry_sector or "Infer from filings"

    return (
        "Perform an institutional M&A Due Diligence & Synergy Intelligence analysis on the two entities below.\n\n"
        f"=== ENTITY A (ACQUIRER): {entity_a.entity_name} ===\n"
        f"Sources ({len(entity_a.documents)} files, {entity_a.total_pages} pages, {entity_a.total_tables} tables): "
        f"{', '.join(entity_a.document_names)}\n"
        f"{entity_a.provenance_text(cap)}\n\n"
        f"=== ENTITY B (TARGET): {entity_b.entity_name} ===\n"
        f"Sources ({len(entity_b.documents)} files, {entity_b.total_pages} pages, {entity_b.total_tables} tables): "
        f"{', '.join(entity_b.document_names)}\n"
        f"{entity_b.provenance_text(cap)}\n\n"
        f"=== DEAL CONTEXT ===\n"
        f"Deal type: {params.deal_type}\n"
        f"Deal value: {deal_val_str}\n"
        f"Industry sector: {sector_str}\n"
        f"Synergy capture horizon: {params.time_horizon_months} months\n"
        f"Baseline valuation parameters: WACC={params.wacc_pct}%, Tax Rate={params.tax_rate_pct}%, Terminal Growth={params.terminal_growth_pct}%\n\n"
        "Return ONLY valid JSON conforming strictly to the DueDiligenceReport schema. No markdown fences, no commentary."
    )


def _extract_json_payload(raw: str) -> str:
    """Strip markdown fences and isolate the outermost JSON object if surrounded by text."""
    cleaned = _FENCE_RE.sub("", raw).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start:end + 1]
    return cleaned


def _parse_llm_response(
    raw: str,
    entity_a: EntityDocuments,
    entity_b: EntityDocuments,
    params: DealParams,
) -> DueDiligenceReport:
    """
    Parse + validate an LLM JSON response into a DueDiligenceReport.

    Pure function — unit-tested without network calls. Strips accidental markdown
    fences, extracts JSON cleanly, enforces authoritative UI deal parameters,
    validates against Pydantic v2, and backfills Synergy NPV deterministically
    when workstreams are present.
    """
    if not raw or not raw.strip():
        raise AnalyzerError("LLM returned an empty response.")

    cleaned = _extract_json_payload(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        snippet = cleaned[:240].replace("\n", " ")
        raise AnalyzerError(
            f"LLM response was not valid JSON: {e}. Snippet: '{snippet}...'"
        ) from e

    if not isinstance(data, dict):
        raise AnalyzerError("LLM returned valid JSON, but the top-level value was not an object.")

    # Inject authoritative deal params from the UI.
    data.setdefault("deal", {})
    if isinstance(data["deal"], dict):
        data["deal"]["entity_a_name"] = entity_a.entity_name
        data["deal"]["entity_b_name"] = entity_b.entity_name
        data["deal"]["deal_type"] = params.deal_type
        data["deal"]["deal_value_usd_b"] = params.deal_value_usd_b
        data["deal"]["time_horizon_months"] = params.time_horizon_months
        if params.industry_sector:
            data["deal"]["industry_sector"] = params.industry_sector
        data["deal"].setdefault("wacc_pct", params.wacc_pct)
        data["deal"].setdefault("tax_rate_pct", params.tax_rate_pct)
        data["deal"].setdefault("terminal_growth_pct", params.terminal_growth_pct)
        data["deal"].setdefault("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        data["deal"]["is_mock"] = False
        data["deal"]["documents_analyzed"] = [*entity_a.document_names, *entity_b.document_names]

    try:
        report = DueDiligenceReport.model_validate(data)
    except Exception as e:  # Pydantic ValidationError
        raise AnalyzerError(f"LLM response failed schema validation: {e}") from e

    # Deterministic backfill: if workstreams exist and NPV is missing, compute via DCF engine
    if report.synergy_workstreams and report.deal.target_synergy_npv_usd_b is None:
        dcf = compute_synergy_dcf(report)
        report.deal.target_synergy_npv_usd_b = dcf.total_synergy_npv_b

    return report


def _live_report(
    entity_a: EntityDocuments,
    entity_b: EntityDocuments,
    params: DealParams,
    cfg: Settings,
) -> DueDiligenceReport:
    """Call the OpenAI-compatible LLM with retries and validate the JSON response."""
    from openai import OpenAI

    if not cfg.is_live_llm:
        raise AnalyzerError("Live mode selected but no LLM API key is configured.")

    client = OpenAI(
        api_key=cfg.llm_api_key.strip(),
        base_url=(cfg.llm_base_url or "").strip() or None,
        timeout=cfg.llm_timeout_seconds,
    )

    user_msg = _build_user_message(entity_a, entity_b, params, cfg.max_chars_to_llm)
    last_err: Optional[Exception] = None
    attempts = max(1, cfg.llm_max_retries + 1)

    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=cfg.llm_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            raw = response.choices[0].message.content or ""
            logger.debug(f"LLM raw response ({len(raw)} chars): {raw[:300]}...")
            return _parse_llm_response(raw, entity_a, entity_b, params)
        except AnalyzerError:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"LLM call attempt {attempt}/{attempts} failed: {e}")
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 6))

    raise AnalyzerError(f"LLM API call failed after {attempts} attempt(s): {last_err}") from last_err


# =====================================================================
# PUBLIC ENTRYPOINT
# =====================================================================

def analyze_documents(
    entity_a: EntityDocuments,
    entity_b: EntityDocuments,
    params: Optional[DealParams] = None,
    use_mock: Optional[bool] = None,
    config: Optional[Settings] = None,
) -> DueDiligenceReport:
    """
    Run the synergy & risk due diligence analysis on two entity document sets.

    Args:
        entity_a: Aggregated acquirer documents.
        entity_b: Aggregated target documents.
        params:   Deal context (value, type, horizon, valuation parameters).
        use_mock: Force mock mode. If None, auto-detect based on `config.is_live_llm`.
        config:   Optional session-scoped Settings override.
    """
    cfg = resolve_settings(config)
    if params is None:
        params = DealParams()
    if use_mock is None:
        use_mock = not cfg.is_live_llm

    if use_mock:
        logger.info(f"Running in MOCK mode for {entity_a.entity_name} x {entity_b.entity_name}.")
        return _mock_report(entity_a, entity_b, params)

    logger.info(f"Running LIVE LLM call with model={cfg.llm_model}.")
    return _live_report(entity_a, entity_b, params, cfg)
