"""
M&A Due Diligence analyzer.

Two modes:
  - LIVE  : Calls an OpenAI-compatible LLM with the system prompt + provenance-
            tagged extracted text, parses the response into a validated
            `DueDiligenceReport`.
  - MOCK  : When no API key is configured (or use_mock=True), returns a
            realistic stubbed report so the UI is demoable end-to-end.

This separation lets you develop the frontend / persistence layer without
spending API budget, and lets you swap LLM providers by changing base_url.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from config.settings import settings
from prompts.system_prompt import SYSTEM_PROMPT
from schemas.report_schema import (
    DueDiligenceReport,
    IntegrationComplexity,
    Recommendation,
)
from utils.models import EntityDocuments

logger = logging.getLogger(__name__)


class AnalyzerError(RuntimeError):
    """Raised when live-mode analysis fails in a way the UI should surface."""


# Regex to strip accidental markdown code fences some models emit even with
# response_format=json_object (e.g. GLM, some OpenRouter routes).
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?|\n?\s*```\s*$", re.IGNORECASE | re.DOTALL)


# =====================================================================
# Deal parameters passed in from the UI
# =====================================================================

class DealParams:
    """Lightweight container for deal context entered in the UI."""
    def __init__(
        self,
        deal_value_usd_b: Optional[float] = None,
        deal_type: str = "Hypothetical full acquisition",
        time_horizon_months: int = 36,
    ):
        self.deal_value_usd_b = deal_value_usd_b
        self.deal_type = deal_type
        self.time_horizon_months = time_horizon_months


# =====================================================================
# MOCK MODE — realistic stub for UI / integration development
# =====================================================================

def _mock_report(entity_a: EntityDocuments, entity_b: EntityDocuments, params: DealParams) -> DueDiligenceReport:
    """Return a plausible report so the full UI pipeline can be exercised offline."""
    name_a = entity_a.entity_name or "Entity A (Acquirer)"
    name_b = entity_b.entity_name or "Entity B (Target)"

    def cite(doc_idx: int, page: str):
        docs = entity_a.documents if doc_idx < 10 else entity_b.documents
        real_idx = doc_idx if doc_idx < 10 else doc_idx - 10
        if not docs:
            return {"source_document": f"{name_a}.pdf" if doc_idx < 10 else f"{name_b}.pdf", "location": page}
        return {"source_document": docs[min(real_idx, len(docs) - 1)].filename, "location": page}

    return DueDiligenceReport(
        deal={
            "entity_a_name": name_a,
            "entity_b_name": name_b,
            "deal_type": params.deal_type,
            "deal_value_usd_b": params.deal_value_usd_b,
            "analysis_date": datetime.now().strftime("%Y-%m-%d"),
            "time_horizon_months": params.time_horizon_months,
            "estimated_revenue_synergy_usd_b": 1.8,
            "estimated_cost_synergy_usd_b": 0.9,
            "target_synergy_npv_usd_b": 3.2,
            "estimated_integration_cost_usd_b": 0.6,
        },
        executive_summary={
            "strategic_rationale": (
                f"[MOCK] {name_a} gains access to next-generation technology and IP, while "
                f"{name_b} secures the capital and global scale required to reach profitability. "
                f"The combined entity would consolidate market position in a consolidating sector."
            ),
            "integration_complexity": IntegrationComplexity.HIGH,
            "complexity_justification": "[MOCK] Cross-border, cross-stack integration with divergent cultures.",
            "top_deal_breaker_risks": [
                {"claim": "[MOCK] Antitrust exposure in overlapping segment.", "sources": [cite(0, "p.1"), cite(10, "p.1")]},
            ],
            "recommendation": Recommendation.CONDITIONAL,
            "recommendation_rationale": "[MOCK] Proceed conditional on regulatory clearance.",
        },
        strategic_fit={
            "market_position_overlap": [{"claim": "[MOCK] Overlap in premium segment.", "sources": [cite(0, "p.1"), cite(10, "p.1")]}],
            "cross_selling_potential": [{"claim": "[MOCK] Target's SaaS upsell to acquirer base.", "sources": [cite(10, "p.1")]}],
            "revenue_synergies": [{"claim": "[MOCK] Co-developed platform unlocks new TAM.", "sources": [cite(0, "p.1"), cite(10, "p.1")]}],
        },
        operational_tech={
            "tech_stack_clash": [{"claim": "[MOCK] AWS vs Azure divergence.", "sources": [cite(10, "p.1")]}],
            "supply_chain_redundancies": [{"claim": "[MOCK] Shared Tier-1 supplier concentration.", "sources": [cite(0, "p.1"), cite(10, "p.1")]}],
            "cost_synergies": [{"claim": "[MOCK] ~15% SG&A consolidation achievable.", "sources": [cite(0, "p.1")]}],
        },
        cultural_risk={
            "leadership_style": [{"claim": "[MOCK] Agile startup vs process-driven incumbent.", "sources": [cite(10, "p.1")]}],
            "talent_retention_risk": [{"claim": "[MOCK] Software engineering org at highest risk.", "sources": [cite(10, "p.1")]}],
        },
        regulatory_esg={
            "antitrust_flags": [{"claim": "[MOCK] EU merger control filing likely required.", "sources": [cite(0, "p.1"), cite(10, "p.1")]}],
            "esg_csrd_gaps": [{"claim": "[MOCK] Target lacks CSRD-ready Scope 3 reporting.", "sources": [cite(10, "p.1")]}],
        },
        data_gaps=[
            "[MOCK] Detailed job descriptions not provided — tech stack inference is indicative only.",
        ],
    )


# =====================================================================
# LIVE MODE — real LLM call
# =====================================================================

def _build_user_message(entity_a: EntityDocuments, entity_b: EntityDocuments, params: DealParams) -> str:
    """Assemble the user prompt with provenance-tagged text from both entities."""
    cap = settings.max_chars_to_llm // 2  # split context budget across both entities
    return (
        "Perform the M&A due diligence analysis on the two entities below.\n\n"
        f"=== ENTITY A (ACQUIRER): {entity_a.entity_name} ===\n"
        f"Sources: {', '.join(entity_a.document_names)}\n"
        f"{entity_a.provenance_text(cap)}\n\n"
        f"=== ENTITY B (TARGET): {entity_b.entity_name} ===\n"
        f"Sources: {', '.join(entity_b.document_names)}\n"
        f"{entity_b.provenance_text(cap)}\n\n"
        f"=== DEAL CONTEXT ===\n"
        f"Deal type: {params.deal_type}\n"
        f"Deal value: ${params.deal_value_usd_b}B USD\n"
        f"Synergy capture horizon: {params.time_horizon_months} months\n\n"
        "Return ONLY the JSON report per the schema. No markdown fences, no commentary."
    )


def _parse_llm_response(raw: str, entity_a: EntityDocuments, entity_b: EntityDocuments,
                        params: DealParams) -> DueDiligenceReport:
    """
    Parse + validate an LLM JSON response into a DueDiligenceReport.

    Extracted as a pure function so it can be unit-tested without an API call.
    Strips accidental markdown fences, validates against the Pydantic schema,
    and injects the authoritative deal params from the UI.
    """
    if not raw or not raw.strip():
        raise AnalyzerError("LLM returned an empty response.")

    cleaned = _FENCE_RE.sub("", raw).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        snippet = cleaned[:200].replace("\n", " ")
        raise AnalyzerError(
            f"LLM response was not valid JSON: {e}. Snippet: '{snippet}...'"
        ) from e

    # Inject authoritative deal params from the UI.
    # Entity names + deal value/type/horizon are USER-ENTERED and must win over
    # anything the LLM emits (it shouldn't rename the entities the analyst typed).
    # Financial ESTIMATES (synergy NPV, etc.) are LLM-derived, so those stay
    # model-authored — we only touch them via setdefault when absent.
    data.setdefault("deal", {})
    data["deal"]["entity_a_name"] = entity_a.entity_name
    data["deal"]["entity_b_name"] = entity_b.entity_name
    data["deal"]["deal_type"] = params.deal_type
    data["deal"]["deal_value_usd_b"] = params.deal_value_usd_b
    data["deal"]["time_horizon_months"] = params.time_horizon_months
    data["deal"].setdefault("analysis_date", datetime.now().strftime("%Y-%m-%d"))

    try:
        return DueDiligenceReport.model_validate(data)
    except Exception as e:  # Pydantic ValidationError or similar
        raise AnalyzerError(f"LLM response failed schema validation: {e}") from e


def _live_report(entity_a: EntityDocuments, entity_b: EntityDocuments, params: DealParams) -> DueDiligenceReport:
    """Call the LLM and validate the JSON response against the Pydantic schema."""
    # Lazy import: only required for LIVE mode. Keeps MOCK mode (and tests) runnable
    # without the openai SDK installed.
    from openai import OpenAI

    if not settings.llm_api_key:
        raise AnalyzerError("Live mode selected but no LLM API key is configured.")

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url or None)

    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(entity_a, entity_b, params)},
            ],
            response_format={"type": "json_object"},  # forced JSON
            temperature=0.2,                          # low temp for analytical precision
        )
    except Exception as e:  # noqa: BLE001 - any SDK/network error -> AnalyzerError
        raise AnalyzerError(f"LLM API call failed: {e}") from e

    raw = response.choices[0].message.content
    logger.debug(f"LLM raw response: {raw[:500]}...")
    return _parse_llm_response(raw, entity_a, entity_b, params)


# =====================================================================
# PUBLIC ENTRYPOINT
# =====================================================================

def analyze_documents(
    entity_a: EntityDocuments,
    entity_b: EntityDocuments,
    params: Optional[DealParams] = None,
    use_mock: Optional[bool] = None,
) -> DueDiligenceReport:
    """
    Run the synergy analysis on two aggregated entity document sets.

    Args:
        entity_a: Aggregated acquirer documents.
        entity_b: Aggregated target documents.
        params:   Deal context (value, type, horizon). Defaults to $0/36mo.
        use_mock: Force mock mode. If None, auto-detect: mock when no API key.
    """
    if params is None:
        params = DealParams()
    if use_mock is None:
        use_mock = not settings.llm_api_key

    if use_mock:
        logger.info(f"Running in MOCK mode for {entity_a.entity_name} x {entity_b.entity_name}.")
        return _mock_report(entity_a, entity_b, params)

    logger.info(f"Running LIVE LLM call with model={settings.llm_model}.")
    return _live_report(entity_a, entity_b, params)
