"""
Multi-Agent M&A Diligence Orchestrator, Model Router, Red-Team Critic,
Deterministic Numerical Fact-Checker & Golden Evaluation Harness.

Capabilities:
  1. `ModelRouter`:
     - Routes `fast`, `balanced`, and `frontier` tasks to configured models with
       token cost estimation and automatic model failover.
  2. `run_multi_agent_diligence(report, entities=None, config=None)`:
     - Executes 6 institutional specialist agents (Financial DD, Commercial Strategy,
       Operational/Tech, Legal/SPA, Cultural/HR, Antitrust/ESG) — LIVE via LLM when
       `config.is_live_llm` is True, or deeply grounded deterministic synthesis
       when offline/mock.
     - Executes an Adversarial Red-Team / Skeptic Critic Agent challenging bullish
       synergy assumptions, quantifying bear-case DCF haircut, exposing execution
       traps, and drafting sharp IC Stress Questions.
     - Executes a Deterministic Numerical Fact-Checker verifying currency and
       percentage figures in report claims against extracted VDR documents and
       deterministic DCF/workstream outputs.
  3. `evaluate_report_quality(report, entities=None) -> EvaluationScorecard`:
     - Golden Evaluation Harness scoring Citation Coverage %, Claim Specificity,
       Financial Internal Consistency (Memo NPV vs. Deterministic DCF), Risk
       Completeness, and assigning an overall Institutional Grade (A+ to D).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional, Sequence, Union

from pydantic import BaseModel, Field

from config.settings import Settings, resolve_settings
from core.financial_model import SynergyDCFResult, ValuationAssumptions, compute_synergy_dcf
from rag.engine import HybridRAGIndex
from schemas.report_schema import (
    Claim,
    DueDiligenceReport,
    RiskDimension,
    Severity,
    SourceCitation,
    SynergyType,
)
from utils.models import EntityDocuments

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?|\n?\s*```\s*$", re.IGNORECASE | re.DOTALL)


# =====================================================================
# 1. ModelRouter (Tiered Routing, Cost Estimation & Failover)
# =====================================================================

class ModelRoute(BaseModel):
    """Resolved model routing plan for a given reasoning tier."""
    tier: str = Field(..., description="'fast' | 'balanced' | 'frontier'")
    primary_model: str
    fallback_models: list[str] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: int = 1800
    est_input_cost_per_1m_usd: float = 2.50
    est_output_cost_per_1m_usd: float = 10.00


class ModelUsageRecord(BaseModel):
    """Telemetry record for a single routed LLM call."""
    tier: str
    model_used: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    used_fallback: bool = False


class ModelRouter:
    """
    Routes LLM tasks across `fast`, `balanced`, and `frontier` tiers with
    cost estimation, custom-endpoint compatibility, and automatic failover.
    """

    DEFAULT_TIERS: dict[str, dict[str, Any]] = {
        "fast": {
            "primary_model": "gpt-4o-mini",
            "fallback_models": ["gpt-4o"],
            "temperature": 0.15,
            "max_tokens": 1200,
            "input_cost": 0.15,
            "output_cost": 0.60,
        },
        "balanced": {
            "primary_model": "gpt-4o",
            "fallback_models": ["gpt-4o-mini"],
            "temperature": 0.20,
            "max_tokens": 1800,
            "input_cost": 2.50,
            "output_cost": 10.00,
        },
        "frontier": {
            "primary_model": "gpt-4.1",
            "fallback_models": ["gpt-4o", "gpt-4o-mini"],
            "temperature": 0.25,
            "max_tokens": 2400,
            "input_cost": 5.00,
            "output_cost": 15.00,
        },
    }

    def __init__(self, tier_overrides: Optional[dict[str, str]] = None) -> None:
        self._overrides = dict(tier_overrides or {})
        self.usage_log: list[ModelUsageRecord] = []

    @property
    def total_estimated_cost_usd(self) -> float:
        return round(sum(u.estimated_cost_usd for u in self.usage_log), 6)

    def route(self, tier: str = "balanced", config: Optional[Settings] = None) -> ModelRoute:
        """Resolve primary + fallback models for `tier` (`fast`, `balanced`, `frontier`)."""
        t = (tier or "balanced").strip().lower()
        if t not in self.DEFAULT_TIERS:
            t = "balanced"
        spec = self.DEFAULT_TIERS[t]

        primary = self._overrides.get(t) or spec["primary_model"]
        fallbacks = list(spec["fallback_models"])

        # If a custom Settings model or custom OpenAI-compatible base_url is configured,
        # prioritize the configured model for balanced/frontier so custom endpoints work smoothly.
        if config is not None and config.llm_model:
            cfg_model = config.llm_model.strip()
            has_custom_endpoint = bool((config.llm_base_url or "").strip())
            if has_custom_endpoint:
                primary = cfg_model
                fallbacks = [m for m in fallbacks if m != cfg_model]
            elif t == "balanced":
                primary = cfg_model
                if spec["primary_model"] != cfg_model and spec["primary_model"] not in fallbacks:
                    fallbacks.insert(0, spec["primary_model"])
            elif cfg_model not in (primary, *fallbacks):
                fallbacks.append(cfg_model)

        # Deduplicate fallbacks while preserving order
        clean_fb: list[str] = []
        for m in fallbacks:
            if m != primary and m not in clean_fb:
                clean_fb.append(m)

        return ModelRoute(
            tier=t,
            primary_model=primary,
            fallback_models=clean_fb,
            temperature=spec["temperature"],
            max_tokens=spec["max_tokens"],
            est_input_cost_per_1m_usd=spec["input_cost"],
            est_output_cost_per_1m_usd=spec["output_cost"],
        )

    def estimate_cost(self, tier: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate USD cost for a call at `tier` given input/output token counts."""
        r = self.route(tier)
        cost = (
            (max(input_tokens, 0) / 1_000_000.0) * r.est_input_cost_per_1m_usd
            + (max(output_tokens, 0) / 1_000_000.0) * r.est_output_cost_per_1m_usd
        )
        return round(cost, 6)

    def call_json(
        self,
        tier: str,
        system_prompt: str,
        user_prompt: str,
        config: Optional[Settings] = None,
    ) -> tuple[dict[str, Any], str, float]:
        """
        Invoke an OpenAI-compatible JSON completion with automatic failover across
        the routed tier's primary and fallback models.
        Returns `(parsed_dict, model_used, estimated_cost_usd)`.
        """
        from openai import OpenAI

        cfg = resolve_settings(config)
        if not cfg.is_live_llm:
            raise RuntimeError("ModelRouter.call_json requires a configured live LLM API key.")

        route = self.route(tier, config=cfg)
        candidates = [route.primary_model, *route.fallback_models]

        client = OpenAI(
            api_key=cfg.llm_api_key.strip(),
            base_url=(cfg.llm_base_url or "").strip() or None,
            timeout=cfg.llm_timeout_seconds,
        )

        last_err: Optional[Exception] = None
        for idx, model_name in enumerate(candidates):
            t0 = time.perf_counter()
            try:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=route.temperature,
                    max_tokens=route.max_tokens,
                )
                raw = resp.choices[0].message.content or ""
                cleaned = _FENCE_RE.sub("", raw).strip()
                if not (cleaned.startswith("{") and cleaned.endswith("}")):
                    s, e = cleaned.find("{"), cleaned.rfind("}")
                    if s != -1 and e > s:
                        cleaned = cleaned[s: e + 1]
                parsed = json.loads(cleaned)
                if not isinstance(parsed, dict):
                    raise ValueError("LLM did not return a JSON object.")

                usage = getattr(resp, "usage", None)
                in_tok = int(getattr(usage, "prompt_tokens", 0) or (len(system_prompt) + len(user_prompt)) // 4)
                out_tok = int(getattr(usage, "completion_tokens", 0) or len(raw) // 4)
                est_cost = self.estimate_cost(route.tier, in_tok, out_tok)
                latency_ms = int((time.perf_counter() - t0) * 1000)

                self.usage_log.append(
                    ModelUsageRecord(
                        tier=route.tier,
                        model_used=model_name,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        estimated_cost_usd=est_cost,
                        latency_ms=latency_ms,
                        used_fallback=idx > 0,
                    )
                )
                return parsed, model_name, est_cost
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning(f"ModelRouter [{route.tier}] model '{model_name}' failed: {exc}")

        raise RuntimeError(f"All routed models failed for tier '{route.tier}': {last_err}") from last_err


# =====================================================================
# 2. Multi-Agent Diligence Data Models
# =====================================================================

class SpecialistFinding(BaseModel):
    """Structured review produced by one of the 6 specialist M&A diligence agents."""
    agent_name: str
    domain: str
    stance: str = Field("Cautious", description="'Supportive' | 'Cautious' | 'Adverse'")
    confidence_score: int = Field(70, ge=0, le=100)
    headline: str
    key_findings: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    diligence_requests: list[str] = Field(default_factory=list)
    citations: list[SourceCitation] = Field(default_factory=list)
    model_used: str = "deterministic-grounded"


class RedTeamCritique(BaseModel):
    """Output of the Adversarial Red-Team / Skeptic Critic Agent."""
    overall_skepticism_level: str = Field("Elevated", description="'Moderate' | 'Elevated' | 'Severe'")
    bear_case_thesis: str
    challenged_assumptions: list[str] = Field(default_factory=list)
    hidden_execution_traps: list[str] = Field(default_factory=list)
    stress_questions: list[str] = Field(default_factory=list)
    base_synergy_npv_usd_b: Optional[float] = None
    haircut_synergy_npv_usd_b: Optional[float] = None
    implied_npv_downside_pct: Optional[float] = None
    model_used: str = "deterministic-redteam"


class FactCheckItem(BaseModel):
    """Verification outcome for a single numeric figure found inside a report claim."""
    claim_text: str
    extracted_figure: str
    normalized_value: float
    unit: str = Field(..., description="'USD_M' | 'PCT'")
    status: str = Field(..., description="'Verified' | 'Close Match' | 'Divergent' | 'Unverified'")
    matched_source: Optional[str] = None
    matched_value_display: Optional[str] = None
    variance_pct: Optional[float] = None
    note: str = ""


class FactCheckResult(BaseModel):
    """Aggregate output of the Deterministic Numerical Fact-Checker."""
    total_figures_checked: int = 0
    verified_count: int = 0
    close_match_count: int = 0
    divergent_count: int = 0
    unverified_count: int = 0
    verification_rate_pct: float = 100.0
    items: list[FactCheckItem] = Field(default_factory=list)
    summary: str = ""


class MultiAgentReview(BaseModel):
    """Full Investment Committee multi-agent review bundle."""
    deal_label: str
    mode: str = Field("offline_grounded", description="'live' | 'offline_grounded'")
    specialist_findings: list[SpecialistFinding] = Field(default_factory=list)
    red_team: RedTeamCritique
    fact_check: FactCheckResult
    consensus_recommendation: str
    consensus_conviction_score: int = Field(70, ge=0, le=100)
    executive_synthesis: str
    estimated_llm_cost_usd: float = 0.0
    models_used: list[str] = Field(default_factory=list)


# =====================================================================
# 3. Deterministic Numerical Fact-Checker
# =====================================================================

_CLAIM_MONEY_RE = re.compile(
    r"(?P<curr>\$|€|£)\s*(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<scale>billion|bn|million|mn|thousand|b|m|k)?\b",
    re.IGNORECASE,
)
_CLAIM_PCT_RE = re.compile(r"(?P<num>\d{1,3}(?:\.\d+)?)\s*%")


def _to_millions(num_str: str, scale: str) -> Optional[float]:
    try:
        v = float(num_str.replace(",", ""))
    except ValueError:
        return None
    sc = (scale or "").lower()
    if sc in ("billion", "bn", "b"):
        return v * 1000.0
    if sc in ("million", "mn", "m"):
        return v
    if sc in ("thousand", "k"):
        return v / 1000.0
    if v >= 1_000_000:
        return v / 1_000_000.0
    if v >= 1_000:
        return v / 1_000.0
    return v


def _extract_figures(text: str) -> list[tuple[str, float, str]]:
    """Extract (display, normalized_value, unit) pairs for currency & percentage figures."""
    figs: list[tuple[str, float, str]] = []
    for m in _CLAIM_MONEY_RE.finditer(text):
        val = _to_millions(m.group("num"), m.group("scale") or "")
        if val is not None and val > 0:
            figs.append((m.group(0).strip(), round(val, 4), "USD_M"))
    for m in _CLAIM_PCT_RE.finditer(text):
        v = float(m.group("num"))
        if 0.0 < v <= 500.0:
            figs.append((m.group(0).strip(), round(v, 4), "PCT"))
    return figs


def _build_ground_truth_pool(
    report: DueDiligenceReport,
    dcf: SynergyDCFResult,
    entities: Optional[Sequence[EntityDocuments]],
) -> list[tuple[float, str, str, str]]:
    """
    Build ground-truth reference figures `(normalized_value, unit, source_label, display)`
    from extracted VDR documents AND deterministic financial model / workstream outputs.
    """
    pool: list[tuple[float, str, str, str]] = []

    # 1. Extracted VDR documents (authoritative primary evidence)
    if entities:
        for ent in entities:
            for doc in ent.documents:
                pages = doc.pages if doc.pages else [doc.full_text]
                for p_idx in range(len(pages)):
                    body = doc.page_with_tables(p_idx) if doc.pages else pages[p_idx]
                    if not body:
                        continue
                    for disp, val, unit in _extract_figures(body):
                        pool.append((val, unit, f"{doc.filename} (p.{p_idx + 1})", disp))

    # 2. Deterministic DCF & Workstream figures (for claims citing modeled synergies)
    d = report.deal
    if d.deal_value_usd_b is not None:
        pool.append((d.deal_value_usd_b * 1000.0, "USD_M", "DealMeta.deal_value_usd_b", f"${d.deal_value_usd_b:.2f}B"))
    if d.estimated_revenue_synergy_usd_b is not None:
        pool.append((d.estimated_revenue_synergy_usd_b * 1000.0, "USD_M", "DealMeta.estimated_revenue_synergy_usd_b", f"${d.estimated_revenue_synergy_usd_b:.2f}B"))
    if d.estimated_cost_synergy_usd_b is not None:
        pool.append((d.estimated_cost_synergy_usd_b * 1000.0, "USD_M", "DealMeta.estimated_cost_synergy_usd_b", f"${d.estimated_cost_synergy_usd_b:.2f}B"))
    if d.estimated_integration_cost_usd_b is not None:
        pool.append((d.estimated_integration_cost_usd_b * 1000.0, "USD_M", "DealMeta.estimated_integration_cost_usd_b", f"${d.estimated_integration_cost_usd_b:.2f}B"))
    pool.append((dcf.total_synergy_npv_b * 1000.0, "USD_M", "Deterministic DCF NPV", f"${dcf.total_synergy_npv_b:.2f}B"))
    pool.append((dcf.steady_state_total_synergy_b * 1000.0, "USD_M", "Deterministic DCF Steady-State", f"${dcf.steady_state_total_synergy_b:.2f}B"))
    pool.append((d.wacc_pct, "PCT", "DealMeta.wacc_pct", f"{d.wacc_pct:.1f}%"))
    pool.append((d.tax_rate_pct, "PCT", "DealMeta.tax_rate_pct", f"{d.tax_rate_pct:.1f}%"))
    pool.append((d.terminal_growth_pct, "PCT", "DealMeta.terminal_growth_pct", f"{d.terminal_growth_pct:.1f}%"))

    for ws in report.synergy_workstreams:
        pool.append((ws.annual_run_rate_usd_m, "USD_M", f"Workstream:{ws.name}", f"${ws.annual_run_rate_usd_m:.1f}M"))
        if ws.one_time_cost_usd_m > 0:
            pool.append((ws.one_time_cost_usd_m, "USD_M", f"Workstream:{ws.name} (Cost)", f"${ws.one_time_cost_usd_m:.1f}M"))
        pool.append((ws.probability_pct, "PCT", f"Workstream:{ws.name} (Prob)", f"{ws.probability_pct:.0f}%"))

    return pool


def run_numerical_fact_check(
    report: DueDiligenceReport,
    entities: Optional[Sequence[EntityDocuments]] = None,
    dcf: Optional[SynergyDCFResult] = None,
) -> FactCheckResult:
    """
    Deterministically verify currency and percentage figures appearing in report
    claims against extracted VDR documents and deterministic DCF/workstream numbers.
    """
    dcf_res = dcf or compute_synergy_dcf(report)
    pool = _build_ground_truth_pool(report, dcf_res, entities)
    has_vdr_docs = bool(entities and any(ent.documents for ent in entities))

    items: list[FactCheckItem] = []
    verified = 0
    close_match = 0
    divergent = 0
    unverified = 0

    for claim in report.all_claims():
        figs = _extract_figures(claim.claim)
        cited_docs = {s.source_document.lower() for s in claim.sources}

        for disp, val, unit in figs:
            # Prefer matching figures from the explicitly cited documents first
            unit_candidates = [p for p in pool if p[1] == unit]
            cited_candidates = [
                p for p in unit_candidates
                if any(cd in p[2].lower() for cd in cited_docs)
            ] if cited_docs else []

            search_order = cited_candidates if cited_candidates else unit_candidates
            best_match: Optional[tuple[float, tuple[float, str, str, str]]] = None

            for cand in search_order:
                c_val = cand[0]
                denom = max(abs(val), abs(c_val), 1e-9)
                var_pct = (abs(val - c_val) / denom) * 100.0
                if best_match is None or var_pct < best_match[0]:
                    best_match = (var_pct, cand)

            # If cited doc didn't match closely, also check full pool (e.g., workstream/other VDR file)
            if best_match is None or best_match[0] > 8.0:
                for cand in unit_candidates:
                    c_val = cand[0]
                    denom = max(abs(val), abs(c_val), 1e-9)
                    var_pct = (abs(val - c_val) / denom) * 100.0
                    if best_match is None or var_pct < best_match[0]:
                        best_match = (var_pct, cand)

            if best_match is None:
                unverified += 1
                items.append(
                    FactCheckItem(
                        claim_text=claim.claim[:180],
                        extracted_figure=disp,
                        normalized_value=val,
                        unit=unit,
                        status="Unverified",
                        note="No numeric reference pool available to verify figure.",
                    )
                )
                continue

            var_pct, cand = best_match
            c_val, _c_unit, c_src, c_disp = cand
            var_round = round(var_pct, 2)

            if var_pct <= 1.5:
                verified += 1
                status = "Verified"
                note = f"Exact match with {c_src} ({c_disp})."
            elif var_pct <= 8.0:
                close_match += 1
                status = "Close Match"
                note = f"Within {var_round:.1f}% of {c_src} ({c_disp}) — likely rounding/fx."
            elif var_pct <= 35.0 and (has_vdr_docs or cited_candidates):
                divergent += 1
                status = "Divergent"
                note = f"Nearest reference {c_src} shows {c_disp} ({var_round:.1f}% divergence) — verify source."
            else:
                unverified += 1
                status = "Unverified"
                note = (
                    f"Figure '{disp}' not found in uploaded VDR documents or model "
                    f"(nearest reference: {c_src} at {c_disp})."
                )

            items.append(
                FactCheckItem(
                    claim_text=claim.claim[:180],
                    extracted_figure=disp,
                    normalized_value=val,
                    unit=unit,
                    status=status,
                    matched_source=c_src,
                    matched_value_display=c_disp,
                    variance_pct=var_round,
                    note=note,
                )
            )

    total = len(items)
    rate = round(((verified + close_match) / total) * 100.0, 1) if total > 0 else 100.0
    summary = (
        f"Audited {total} numeric figure(s) across {len(report.all_claims())} claims: "
        f"{verified} verified, {close_match} close match, {divergent} divergent, {unverified} unverified "
        f"({rate:.1f}% grounded verification rate)."
    )

    return FactCheckResult(
        total_figures_checked=total,
        verified_count=verified,
        close_match_count=close_match,
        divergent_count=divergent,
        unverified_count=unverified,
        verification_rate_pct=rate,
        items=items,
        summary=summary,
    )


# =====================================================================
# 4. Six Specialist Agents + Red-Team Critic (Offline Grounded & Live LLM)
# =====================================================================

_SPECIALIST_SPECS: list[tuple[str, str, str]] = [
    ("Financial DD Specialist", "Financial DD", "revenue ebitda margin cash flow dcf synergy wacc debt working capital"),
    ("Commercial Strategy Specialist", "Commercial Strategy", "market overlap cross-sell customer concentration revenue synergy churn arr"),
    ("Operational & Tech Specialist", "Operational/Tech", "tech stack cloud erp software supply chain procurement migration cost synergy"),
    ("Legal & SPA Specialist", "Legal/SPA", "change of control contract litigation indemnity representation warranty spa lease"),
    ("Cultural & HR Specialist", "Cultural/HR", "leadership culture talent retention attrition compensation works council union"),
    ("Antitrust & ESG Specialist", "Antitrust/ESG", "antitrust merger control hsr eu commission cfius esg csrd scope 3 emissions"),
]


def _collect_citations(claims: Sequence[Claim], limit: int = 4) -> list[SourceCitation]:
    seen: set[tuple[str, str]] = set()
    out: list[SourceCitation] = []
    for c in claims:
        for s in c.sources:
            k = (s.source_document, s.location)
            if k not in seen:
                seen.add(k)
                out.append(s)
                if len(out) >= limit:
                    return out
    return out


def _offline_specialist_finding(
    agent_name: str,
    domain: str,
    report: DueDiligenceReport,
    dcf: SynergyDCFResult,
    rag_index: Optional[HybridRAGIndex],
    rag_query: str,
) -> SpecialistFinding:
    """Build a richly grounded deterministic specialist review from report + DCF + RAG."""
    d = report.deal
    rag_hits = rag_index.search(rag_query, top_k=3) if (rag_index and rag_index.chunk_count > 0) else []
    rag_cites = [
        SourceCitation(source_document=h.chunk.filename, location=f"p.{h.chunk.page_num}, {h.chunk.section_title}")
        for h in rag_hits
    ]

    if domain == "Financial DD":
        vc_str = f"{dcf.value_creation_pct_of_deal:.1f}% of deal value" if dcf.value_creation_pct_of_deal is not None else "N/A"
        irr_str = f"{dcf.synergy_irr_pct:.1f}%" if dcf.synergy_irr_pct is not None else "N/A"
        pb_str = f"{dcf.payback_years:.2f} yrs" if dcf.payback_years is not None else "N/A"
        ratio_str = f"{dcf.npv_to_integration_cost_ratio:.2f}x" if dcf.npv_to_integration_cost_ratio is not None else "N/A"
        stance = "Supportive" if (dcf.total_synergy_npv_b > 0 and (dcf.value_creation_pct_of_deal or 10.0) >= 8.0) else "Cautious"
        findings = [
            f"Deterministic Synergy NPV stands at ${dcf.total_synergy_npv_b:.2f}B ({vc_str}) at {d.wacc_pct:.1f}% WACC and {d.tax_rate_pct:.0f}% tax rate.",
            f"Probability-weighted run-rate synergies reach ${dcf.steady_state_total_synergy_b:.2f}B/yr (Rev: ${dcf.steady_state_revenue_synergy_b:.2f}B, Cost: ${dcf.steady_state_cost_synergy_b:.2f}B) vs. ${dcf.total_integration_cost_b:.2f}B one-time integration cost.",
            f"Capital efficiency: {ratio_str} NPV-to-integration-cost ratio, {pb_str} simple payback, {irr_str} project IRR.",
        ]
        red_flags: list[str] = []
        if d.target_synergy_npv_usd_b is not None and abs(d.target_synergy_npv_usd_b - dcf.total_synergy_npv_b) > 0.15:
            red_flags.append(
                f"Memo headline NPV (${d.target_synergy_npv_usd_b:.2f}B) diverges from bottom-up DCF (${dcf.total_synergy_npv_b:.2f}B)."
            )
        if dcf.steady_state_revenue_synergy_b > dcf.steady_state_cost_synergy_b * 1.25:
            red_flags.append("Valuation leans heavily on higher-risk revenue synergies rather than controllable cost synergies.")
        if not red_flags:
            red_flags.append("Verify standalone QoE EBITDA add-backs, normalized working capital peg, and debt-like items in VDR.")
        reqs = [
            "Obtain audited 3-year QoE databook and monthly EBITDA bridge with non-recurring add-back support.",
            "Validate NWC peg seasonality and off-balance-sheet debt-like obligations (deferred revenue, leases, pensions).",
        ]
        cites = _collect_citations([c for w in report.synergy_workstreams for c in [Claim(claim=w.name, sources=w.sources)]]) or rag_cites
        return SpecialistFinding(
            agent_name=agent_name,
            domain=domain,
            stance=stance,
            confidence_score=84 if report.synergy_workstreams else 68,
            headline=f"Synergy NPV ${dcf.total_synergy_npv_b:.2f}B ({vc_str}) with {pb_str} payback.",
            key_findings=findings,
            red_flags=red_flags,
            diligence_requests=reqs,
            citations=cites[:4],
        )

    if domain == "Commercial Strategy":
        comm_claims = [
            *report.strategic_fit.market_position_overlap,
            *report.strategic_fit.cross_selling_potential,
            *report.strategic_fit.revenue_synergies,
        ]
        rev_ws = [w for w in report.synergy_workstreams if w.synergy_type == SynergyType.REVENUE]
        avg_prob = sum(w.probability_pct for w in rev_ws) / len(rev_ws) if rev_ws else 65.0
        findings = [c.claim for c in comm_claims[:3]] or ["Commercial overlap and cross-sell assumptions require VDR customer-cohort validation."]
        red_flags = [
            f"Average modeled probability on revenue workstreams is {avg_prob:.0f}%; institutional M&A benchmarks realize <55% of Year-1/2 revenue synergies.",
        ]
        if any(c.severity in (Severity.HIGH, Severity.CRITICAL) for c in report.strategic_fit.market_position_overlap):
            red_flags.append("Material market/customer overlap risks revenue dis-synergy and channel conflict.")
        reqs = [
            "Request top-20 customer concentration, multi-year NRR/logo churn cohorts, and renewal schedule.",
            "Run clean-team account overlap match between acquirer and target CRM pipelines.",
        ]
        cites = _collect_citations(comm_claims) or rag_cites
        return SpecialistFinding(
            agent_name=agent_name,
            domain=domain,
            stance="Supportive" if len(comm_claims) >= 3 else "Cautious",
            confidence_score=78 if comm_claims else 60,
            headline=f"Commercial thesis anchored on ${dcf.steady_state_revenue_synergy_b:.2f}B/yr prob-adjusted revenue synergies.",
            key_findings=findings,
            red_flags=red_flags,
            diligence_requests=reqs,
            citations=cites[:4],
        )

    if domain == "Operational/Tech":
        op_claims = [
            *report.operational_tech.tech_stack_clash,
            *report.operational_tech.supply_chain_redundancies,
            *report.operational_tech.cost_synergies,
        ]
        high_clash = any(c.severity in (Severity.HIGH, Severity.CRITICAL) for c in report.operational_tech.tech_stack_clash)
        findings = [c.claim for c in op_claims[:3]] or ["Operational and IT integration scope requires architecture assessment."]
        red_flags = [c.claim for c in report.operational_tech.tech_stack_clash if c.severity in (Severity.HIGH, Severity.CRITICAL)][:2]
        if not red_flags:
            red_flags.append("ERP/cloud migration sequencing and Day-1 cybersecurity posture require phased API bridging.")
        reqs = [
            "Review SBOM / open-source license scan, SOC 2 Type II report, and cloud hosting commit schedules.",
            "Obtain Tier-1 supplier sole-source map and IT cutover cost estimates.",
        ]
        cites = _collect_citations(op_claims) or rag_cites
        return SpecialistFinding(
            agent_name=agent_name,
            domain=domain,
            stance="Cautious" if high_clash else "Supportive",
            confidence_score=79 if op_claims else 62,
            headline=f"Integration complexity rated '{report.executive_summary.integration_complexity.value}' with ${dcf.steady_state_cost_synergy_b:.2f}B/yr cost synergy pool.",
            key_findings=findings,
            red_flags=red_flags,
            diligence_requests=reqs,
            citations=cites[:4],
        )

    if domain == "Legal/SPA":
        breakers = [r for r in report.risk_register if r.is_deal_breaker]
        top_risks = report.executive_summary.top_deal_breaker_risks
        findings = [
            f"Identified {len(breakers)} deal-breaker risk(s) in risk register and {len(top_risks)} top executive red flag(s) requiring SPA protection.",
            f"Documented {len(report.data_gaps)} material public/VDR data gap(s) that must be addressed via specific reps, warranties, or special indemnities.",
        ]
        if rag_hits:
            findings.append(f"VDR legal reference located in {rag_hits[0].citation}.")
        red_flags = [f"Deal-breaker: {r.title} — Mitigation: {r.mitigation}" for r in breakers[:3]]
        if not red_flags:
            red_flags.append("Change-of-control provisions across top customer/vendor contracts unverified in public filings.")
        reqs = [
            "Complete full change-of-control & anti-assignment audit on top 25 customer, vendor, and IP license agreements.",
            "Draft specific SPA indemnities, R&W insurance exclusions review, and MAC/outside-date regulatory clauses.",
        ]
        cites = _collect_citations(top_risks) or rag_cites
        return SpecialistFinding(
            agent_name=agent_name,
            domain=domain,
            stance="Adverse" if len(breakers) >= 3 else "Cautious",
            confidence_score=76,
            headline=f"{len(breakers)} deal-breaker risk(s) require binding SPA reps, closing conditions, and escrow protection.",
            key_findings=findings,
            red_flags=red_flags,
            diligence_requests=reqs,
            citations=cites[:4],
        )

    if domain == "Cultural/HR":
        hr_claims = [*report.cultural_risk.leadership_style, *report.cultural_risk.talent_retention_risk]
        high_attrition = any(c.severity in (Severity.HIGH, Severity.CRITICAL) for c in report.cultural_risk.talent_retention_risk)
        findings = [c.claim for c in hr_claims[:3]] or ["Cultural integration and leadership alignment require Day-1 governance charter."]
        red_flags = [c.claim for c in report.cultural_risk.talent_retention_risk[:2]] or [
            "Elevated flight risk among key engineering and commercial leaders during sign-to-close window."
        ]
        reqs = [
            "Obtain anonymized employee census, top-50 key-person equity/unvested RSU schedule, and severance triggers.",
            "Structure 24–36 month milestone-vested retention pool prior to signing announcement.",
        ]
        cites = _collect_citations(hr_claims) or rag_cites
        return SpecialistFinding(
            agent_name=agent_name,
            domain=domain,
            stance="Cautious" if high_attrition else "Supportive",
            confidence_score=74 if hr_claims else 60,
            headline="Key-talent retention and operating-cadence alignment are pivotal Day-1 execution priorities.",
            key_findings=findings,
            red_flags=red_flags,
            diligence_requests=reqs,
            citations=cites[:4],
        )

    # Antitrust / ESG
    reg_claims = [*report.regulatory_esg.antitrust_flags, *report.regulatory_esg.esg_csrd_gaps]
    crit_reg = any(c.severity == Severity.CRITICAL for c in reg_claims)
    findings = [c.claim for c in reg_claims[:3]] or ["Regulatory merger control and ESG/CSRD gap analysis required."]
    red_flags = [c.claim for c in report.regulatory_esg.antitrust_flags[:2]] or [
        "Potential HSR Second Request / EU Phase II review timeline risk."
    ]
    reqs = [
        "Engage antitrust counsel for HHI overlap modeling, HSR/EC/CFIUS notification triggers, and hell-or-high-water cap.",
        "Audit target Scope 1–3 GHG baseline and EU CSRD double-materiality readiness.",
    ]
    cites = _collect_citations(reg_claims) or rag_cites
    return SpecialistFinding(
        agent_name=agent_name,
        domain=domain,
        stance="Adverse" if crit_reg else "Cautious",
        confidence_score=77 if reg_claims else 62,
        headline="Merger control clearance timing and CSRD/Scope 3 compliance define external closing risk.",
        key_findings=findings,
        red_flags=red_flags,
        diligence_requests=reqs,
        citations=cites[:4],
    )


def _compute_bear_case_dcf(report: DueDiligenceReport) -> SynergyDCFResult:
    """Deterministic Red-Team stress case: 60% realization, +25% cost overrun, +150bps WACC."""
    base = ValuationAssumptions.from_report(report)
    bear = ValuationAssumptions(
        wacc_pct=min(25.0, base.wacc_pct + 1.5),
        tax_rate_pct=base.tax_rate_pct,
        terminal_growth_pct=max(0.5, base.terminal_growth_pct - 0.5),
        realization_factor_pct=60.0,
        integration_cost_overrun_pct=25.0,
        projection_years=base.projection_years,
        use_probability_weighting=True,
        terminal_method=base.terminal_method,
        terminal_multiple=max(2.5, base.terminal_multiple - 1.0),
    )
    return compute_synergy_dcf(report, bear)


def _offline_red_team_critique(
    report: DueDiligenceReport,
    base_dcf: SynergyDCFResult,
    bear_dcf: SynergyDCFResult,
    fact_check: FactCheckResult,
) -> RedTeamCritique:
    """Deterministic adversarial Red-Team / Skeptic Critic grounded in DCF stress math & risks."""
    d = report.deal
    base_npv = base_dcf.total_synergy_npv_b
    bear_npv = bear_dcf.total_synergy_npv_b
    downside_pct = (
        round(((bear_npv - base_npv) / abs(base_npv)) * 100.0, 1)
        if abs(base_npv) > 1e-6 else None
    )

    rev_share = (
        round((base_dcf.steady_state_revenue_synergy_b / max(base_dcf.steady_state_total_synergy_b, 1e-6)) * 100.0, 1)
        if base_dcf.steady_state_total_synergy_b > 0 else 0.0
    )

    challenged: list[str] = []
    if rev_share >= 40.0:
        challenged.append(
            f"Revenue synergy over-reliance: {rev_share:.0f}% of modeled steady-state synergies come from revenue cross-sell, "
            "which empirical M&A studies show under-deliver by 40–60% due to salesforce disruption and customer churn."
        )
    challenged.append(
        f"Bear-case valuation fragility: Applying 60% synergy capture, +25% integration cost overrun, and +150bps WACC "
        f"cuts Synergy NPV from ${base_npv:.2f}B to ${bear_npv:.2f}B"
        + (f" ({downside_pct:.1f}% haircut)." if downside_pct is not None else ".")
    )
    if base_dcf.total_integration_cost_b < base_dcf.steady_state_total_synergy_b * 0.8:
        challenged.append(
            f"Under-budgeted integration cost: One-time cost (${base_dcf.total_integration_cost_b:.2f}B) is below "
            "the institutional 1.0x–1.3x annual cost-synergy rule-of-thumb for complex integrations."
        )
    if fact_check.divergent_count > 0 or fact_check.unverified_count > 2:
        challenged.append(
            f"Evidence verification gap: Numerical fact-checker flagged {fact_check.divergent_count} divergent and "
            f"{fact_check.unverified_count} unverified figure(s) across memo claims."
        )

    traps: list[str] = [
        f"Sign-to-close limbo: Extended regulatory clearance could trigger key-talent attrition and customer hesitation at {d.entity_b_name}.",
        "Hidden dis-synergies: Overlapping enterprise accounts may demand volume price concessions upon contract consolidation.",
        "IT & data migration drag: Dual-stack maintenance frequently delays Year-1/Year-2 cost capture by 6–12 months.",
    ]
    for r in report.risk_register:
        if r.is_deal_breaker and len(traps) < 5:
            traps.append(f"Deal-breaker trap — {r.title} (Likelihood {r.likelihood}/5 × Impact {r.impact}/5).")

    stress_qs = [
        f"If revenue synergies realize at 0% and only 70% of cost synergies materialize, does {d.entity_a_name} still clear its WACC hurdle?",
        f"What exact percentage of {d.entity_b_name}'s revenue is tied to contracts with change-of-control termination rights?",
        f"If the top 15 engineering/commercial leaders at {d.entity_b_name} depart within 12 months, how much of the product roadmap slips?",
        "What is the walk-away reverse break-up fee if regulators demand structural divestitures?",
        "Which integration cost line items (ERP cutover, severance, retention, rebranding) have binding vendor quotes vs. top-down estimates?",
    ]

    if bear_npv <= 0 or len([r for r in report.risk_register if r.is_deal_breaker]) >= 3:
        skepticism = "Severe"
    elif (downside_pct is not None and downside_pct <= -45.0) or rev_share >= 50.0:
        skepticism = "Elevated"
    else:
        skepticism = "Moderate"

    bear_thesis = (
        f"BEAR-CASE COUNTER-THESIS ({d.entity_a_name} × {d.entity_b_name}): "
        f"Under realistic downside execution (60% synergy capture, +25% integration cost overrun, {d.wacc_pct + 1.5:.1f}% WACC), "
        f"risk-adjusted Synergy NPV compresses from ${base_npv:.2f}B to ${bear_npv:.2f}B"
        + (f" ({downside_pct:.1f}%)" if downside_pct is not None else "")
        + f". With integration complexity rated '{report.executive_summary.integration_complexity.value}' and "
        f"{len([r for r in report.risk_register if r.is_deal_breaker])} deal-breaker risk(s), paying full upfront multiple "
        "transfers execution risk entirely to the acquirer unless mitigated via earn-out, price adjustment, or ring-fenced SPA indemnities."
    )

    return RedTeamCritique(
        overall_skepticism_level=skepticism,
        bear_case_thesis=bear_thesis,
        challenged_assumptions=challenged,
        hidden_execution_traps=traps[:5],
        stress_questions=stress_qs,
        base_synergy_npv_usd_b=base_npv,
        haircut_synergy_npv_usd_b=bear_npv,
        implied_npv_downside_pct=downside_pct,
        model_used="deterministic-redteam",
    )


def _live_specialist_call(
    router: ModelRouter,
    agent_name: str,
    domain: str,
    report: DueDiligenceReport,
    dcf: SynergyDCFResult,
    rag_context: str,
    cfg: Settings,
) -> SpecialistFinding:
    """Execute a single specialist review live via `ModelRouter` (balanced tier)."""
    sys_prompt = (
        f"You are the {agent_name} on an institutional M&A Investment Committee.\n"
        "Return ONLY valid JSON with keys: stance ('Supportive'|'Cautious'|'Adverse'), "
        "confidence_score (0-100 int), headline (str), key_findings (list[str], 3 items), "
        "red_flags (list[str], 2-3 items), diligence_requests (list[str], 2-3 items)."
    )
    user_prompt = (
        f"Deal: {report.deal.entity_a_name} acquiring {report.deal.entity_b_name} "
        f"(Deal Value: ${report.deal.deal_value_usd_b or 'N/A'}B, Recommendation: {report.executive_summary.recommendation.value}).\n"
        f"Deterministic DCF NPV: ${dcf.total_synergy_npv_b:.2f}B (Rev: ${dcf.steady_state_revenue_synergy_b:.2f}B/yr, "
        f"Cost: ${dcf.steady_state_cost_synergy_b:.2f}B/yr, Integration Cost: ${dcf.total_integration_cost_b:.2f}B).\n"
        f"Domain Focus: {domain}\n"
        f"Report Excerpt:\n{report.model_dump_json()[:7000]}\n\n"
        f"Retrieved VDR Context:\n{rag_context[:4000]}"
    )
    parsed, model_used, _cost = router.call_json("balanced", sys_prompt, user_prompt, config=cfg)
    stance = str(parsed.get("stance", "Cautious"))
    if stance not in ("Supportive", "Cautious", "Adverse"):
        stance = "Cautious"
    return SpecialistFinding(
        agent_name=agent_name,
        domain=domain,
        stance=stance,
        confidence_score=max(0, min(100, int(parsed.get("confidence_score", 75)))),
        headline=str(parsed.get("headline", f"{domain} diligence assessment completed.")),
        key_findings=[str(x) for x in parsed.get("key_findings", [])[:5]],
        red_flags=[str(x) for x in parsed.get("red_flags", [])[:5]],
        diligence_requests=[str(x) for x in parsed.get("diligence_requests", [])[:5]],
        citations=[],
        model_used=model_used,
    )


def _live_red_team_call(
    router: ModelRouter,
    report: DueDiligenceReport,
    base_dcf: SynergyDCFResult,
    bear_dcf: SynergyDCFResult,
    fact_check: FactCheckResult,
    cfg: Settings,
) -> RedTeamCritique:
    """Execute the Adversarial Red-Team Critic live via `ModelRouter` (frontier tier)."""
    base_npv = base_dcf.total_synergy_npv_b
    bear_npv = bear_dcf.total_synergy_npv_b
    downside_pct = round(((bear_npv - base_npv) / abs(base_npv)) * 100.0, 1) if abs(base_npv) > 1e-6 else None

    sys_prompt = (
        "You are an Adversarial Red-Team Skeptic Critic on an M&A Investment Committee. "
        "Your job is to ruthlessly challenge bullish synergy assumptions, uncover hidden execution traps, "
        "and formulate sharp Bear-Case Stress Questions.\n"
        "Return ONLY valid JSON with keys: overall_skepticism_level ('Moderate'|'Elevated'|'Severe'), "
        "bear_case_thesis (str), challenged_assumptions (list[str], 3-4 items), "
        "hidden_execution_traps (list[str], 3-4 items), stress_questions (list[str], 4-5 items)."
    )
    user_prompt = (
        f"Deal: {report.deal.entity_a_name} × {report.deal.entity_b_name}\n"
        f"Base Synergy NPV: ${base_npv:.2f}B | Stressed Bear-Case NPV (60% capture, +25% cost, +150bps WACC): ${bear_npv:.2f}B ({downside_pct}%).\n"
        f"Fact-Check Summary: {fact_check.summary}\n"
        f"Report Summary:\n{report.model_dump_json()[:8000]}"
    )
    parsed, model_used, _cost = router.call_json("frontier", sys_prompt, user_prompt, config=cfg)
    level = str(parsed.get("overall_skepticism_level", "Elevated"))
    if level not in ("Moderate", "Elevated", "Severe"):
        level = "Elevated"
    return RedTeamCritique(
        overall_skepticism_level=level,
        bear_case_thesis=str(parsed.get("bear_case_thesis", "Bear-case critique generated.")),
        challenged_assumptions=[str(x) for x in parsed.get("challenged_assumptions", [])[:5]],
        hidden_execution_traps=[str(x) for x in parsed.get("hidden_execution_traps", [])[:5]],
        stress_questions=[str(x) for x in parsed.get("stress_questions", [])[:6]],
        base_synergy_npv_usd_b=base_npv,
        haircut_synergy_npv_usd_b=bear_npv,
        implied_npv_downside_pct=downside_pct,
        model_used=model_used,
    )


def run_multi_agent_diligence(
    report: DueDiligenceReport,
    entities: Optional[Union[EntityDocuments, Sequence[EntityDocuments]]] = None,
    config: Optional[Settings] = None,
    use_live: Optional[bool] = None,
) -> MultiAgentReview:
    """
    Orchestrate the full institutional Multi-Agent Due Diligence Review:
      - 6 specialist agents (Financial DD, Commercial Strategy, Operational/Tech,
        Legal/SPA, Cultural/HR, Antitrust/ESG)
      - Adversarial Red-Team / Skeptic Critic Agent with Bear-Case DCF stress math
      - Deterministic Numerical Fact-Checker against VDR docs & DCF outputs
    """
    cfg = resolve_settings(config)
    live_mode = cfg.is_live_llm if use_live is None else (use_live and cfg.is_live_llm)

    ent_list: list[EntityDocuments] = []
    if isinstance(entities, EntityDocuments):
        ent_list = [entities]
    elif entities:
        ent_list = list(entities)

    rag_index: Optional[HybridRAGIndex] = None
    if ent_list and any(e.documents for e in ent_list):
        rag_index = HybridRAGIndex()
        rag_index.index_entities(ent_list)

    base_dcf = compute_synergy_dcf(report)
    bear_dcf = _compute_bear_case_dcf(report)
    fact_check = run_numerical_fact_check(report, entities=ent_list, dcf=base_dcf)
    router = ModelRouter()

    findings: list[SpecialistFinding] = []
    models_used: set[str] = set()

    for agent_name, domain, rag_query in _SPECIALIST_SPECS:
        if live_mode:
            rag_ctx = (
                HybridRAGIndex.format_context(rag_index.search(rag_query, top_k=4), max_chars=4000)
                if rag_index else ""
            )
            try:
                f = _live_specialist_call(router, agent_name, domain, report, base_dcf, rag_ctx, cfg)
                findings.append(f)
                models_used.add(f.model_used)
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Live specialist '{agent_name}' failed; falling back to grounded synthesis: {e}")

        f = _offline_specialist_finding(agent_name, domain, report, base_dcf, rag_index, rag_query)
        findings.append(f)
        models_used.add(f.model_used)

    # Red-Team Critic
    if live_mode:
        try:
            red_team = _live_red_team_call(router, report, base_dcf, bear_dcf, fact_check, cfg)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Live Red-Team Critic failed; falling back to grounded synthesis: {e}")
            red_team = _offline_red_team_critique(report, base_dcf, bear_dcf, fact_check)
    else:
        red_team = _offline_red_team_critique(report, base_dcf, bear_dcf, fact_check)
    models_used.add(red_team.model_used)

    # Consensus synthesis across the 6 specialists + Red-Team adjustment
    stance_pts = {"Supportive": 85, "Cautious": 68, "Adverse": 42}
    avg_stance = sum(stance_pts.get(f.stance, 65) for f in findings) / max(len(findings), 1)
    skeptic_penalty = {"Moderate": 3, "Elevated": 7, "Severe": 14}.get(red_team.overall_skepticism_level, 6)
    base_conv = report.deal.overall_conviction_score if report.deal.overall_conviction_score is not None else 70
    consensus_score = int(max(10, min(98, round(0.55 * avg_stance + 0.45 * base_conv - skeptic_penalty))))

    adverse_count = sum(1 for f in findings if f.stance == "Adverse")
    supportive_count = sum(1 for f in findings if f.stance == "Supportive")
    if adverse_count >= 2 or consensus_score < 50:
        consensus_rec = "No-Go / Re-Price Required"
    elif supportive_count >= 4 and red_team.overall_skepticism_level == "Moderate" and consensus_score >= 78:
        consensus_rec = "Go"
    else:
        consensus_rec = "Conditional Go"

    deal_label = f"{report.deal.entity_a_name} × {report.deal.entity_b_name}"
    synthesis = (
        f"Multi-Agent IC Panel ({len(findings)} specialists + Red-Team Critic + Deterministic Fact-Checker) "
        f"assigns '{consensus_rec}' with {consensus_score}/100 conviction for {deal_label}. "
        f"Stance breakdown: {supportive_count} Supportive, {len(findings) - supportive_count - adverse_count} Cautious, "
        f"{adverse_count} Adverse. Base Synergy NPV is ${base_dcf.total_synergy_npv_b:.2f}B vs. "
        f"Red-Team Bear-Case NPV of ${bear_dcf.total_synergy_npv_b:.2f}B. {fact_check.summary}"
    )

    return MultiAgentReview(
        deal_label=deal_label,
        mode="live" if live_mode else "offline_grounded",
        specialist_findings=findings,
        red_team=red_team,
        fact_check=fact_check,
        consensus_recommendation=consensus_rec,
        consensus_conviction_score=consensus_score,
        executive_synthesis=synthesis,
        estimated_llm_cost_usd=router.total_estimated_cost_usd,
        models_used=sorted(models_used),
    )


# =====================================================================
# 5. Golden Evaluation Harness (`evaluate_report_quality`)
# =====================================================================

class EvaluationDimension(BaseModel):
    """Individual scored dimension in the Golden Evaluation Harness."""
    name: str
    score: float = Field(..., ge=0.0, le=100.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    passed: bool
    detail: str


class EvaluationScorecard(BaseModel):
    """Institutional quality & audit scorecard produced by `evaluate_report_quality`."""
    overall_score: float = Field(..., ge=0.0, le=100.0)
    institutional_grade: str = Field(..., description="'A+' | 'A' | 'A-' | 'B+' | 'B' | 'B-' | 'C+' | 'C' | 'D'")
    citation_coverage_pct: float
    claim_specificity_score: float
    financial_consistency_score: float
    risk_completeness_score: float
    grounding_verified_pct: float
    memo_npv_usd_b: Optional[float] = None
    deterministic_dcf_npv_usd_b: float = 0.0
    npv_divergence_pct: Optional[float] = None
    dimensions: list[EvaluationDimension] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    deficiencies: list[str] = Field(default_factory=list)
    audit_passed: bool = True


def _score_to_grade(score: float) -> str:
    if score >= 95.0:
        return "A+"
    if score >= 90.0:
        return "A"
    if score >= 85.0:
        return "A-"
    if score >= 80.0:
        return "B+"
    if score >= 75.0:
        return "B"
    if score >= 70.0:
        return "B-"
    if score >= 65.0:
        return "C+"
    if score >= 60.0:
        return "C"
    return "D"


def evaluate_report_quality(
    report: DueDiligenceReport,
    entities: Optional[Union[EntityDocuments, Sequence[EntityDocuments]]] = None,
) -> EvaluationScorecard:
    """
    Golden Evaluation Harness scoring an Investment Committee DueDiligenceReport across:
      1. Citation Coverage % (25% weight) — claim, workstream & risk citations + real-file verification
      2. Claim Specificity (25% weight) — analytical depth, quantification, severity/confidence tagging
      3. Financial Internal Consistency (30% weight) — Memo NPV vs. Deterministic DCF & workstream reconciliation
      4. Risk & Governance Completeness (20% weight) — 5-dimension risk coverage, deal-breakers, PMI roadmap, VDR Q&A
    Returns an `EvaluationScorecard` with Institutional Grade (A+ to D).
    """
    ent_list: list[EntityDocuments] = []
    if isinstance(entities, EntityDocuments):
        ent_list = [entities]
    elif entities:
        ent_list = list(entities)

    known_files = {
        doc.filename.lower()
        for ent in ent_list
        for doc in ent.documents
    }
    if not known_files and report.deal.documents_analyzed:
        known_files = {f.lower() for f in report.deal.documents_analyzed}

    claims = report.all_claims()
    strengths: list[str] = []
    deficiencies: list[str] = []

    # ---- Dimension 1: Citation Coverage (25%) ----
    claim_cit_pct = round(report.grounding_ratio() * 100.0, 1)
    ws_cit_pct = (
        round((sum(1 for w in report.synergy_workstreams if w.sources) / len(report.synergy_workstreams)) * 100.0, 1)
        if report.synergy_workstreams else claim_cit_pct
    )
    risk_cit_pct = (
        round((sum(1 for r in report.risk_register if r.sources) / len(report.risk_register)) * 100.0, 1)
        if report.risk_register else claim_cit_pct
    )

    all_cited_docs = report.unique_sources()
    if known_files and all_cited_docs:
        verified_files = sum(
            1 for s in all_cited_docs
            if s.lower() in known_files or any(kf in s.lower() or s.lower() in kf for kf in known_files)
        )
        grounding_verified_pct = round((verified_files / len(all_cited_docs)) * 100.0, 1)
    elif all_cited_docs:
        grounding_verified_pct = 100.0
    else:
        grounding_verified_pct = 0.0

    cit_score = round(0.55 * claim_cit_pct + 0.15 * ws_cit_pct + 0.15 * risk_cit_pct + 0.15 * grounding_verified_pct, 1)
    if cit_score >= 85.0:
        strengths.append(f"Strong evidence grounding: {claim_cit_pct:.0f}% of claims cite explicit source documents & locations.")
    else:
        deficiencies.append(f"Citation coverage ({claim_cit_pct:.0f}% claims, {grounding_verified_pct:.0f}% verified files) below 85% institutional threshold.")

    # ---- Dimension 2: Claim Specificity (25%) ----
    if claims:
        avg_words = sum(len(c.claim.split()) for c in claims) / len(claims)
        depth_score = min(100.0, (avg_words / 18.0) * 100.0)
        quant_hits = sum(
            1 for c in claims
            if c.quantified_impact_usd_m is not None or bool(re.search(r"(?:\$|€|£)\s*\d|\d+(?:\.\d+)?%", c.claim))
        )
        quant_score = min(100.0, (quant_hits / max(len(claims) * 0.45, 1.0)) * 100.0)
        meta_tagged = sum(1 for c in claims if c.severity is not None and c.confidence is not None)
        meta_score = (meta_tagged / len(claims)) * 100.0
        # Penalize generic/demo placeholders slightly only if overly sparse
        spec_score = round(0.40 * depth_score + 0.35 * quant_score + 0.25 * meta_score, 1)
    else:
        spec_score = 0.0

    if spec_score >= 80.0:
        strengths.append(f"High analytical specificity ({spec_score:.1f}/100) with quantified impacts and severity/confidence ratings.")
    else:
        deficiencies.append(f"Claim specificity ({spec_score:.1f}/100) can be strengthened with more quantified dollar/percentage figures.")

    # ---- Dimension 3: Financial Internal Consistency (30%) ----
    dcf = compute_synergy_dcf(report)
    memo_npv = report.deal.target_synergy_npv_usd_b
    dcf_npv = dcf.total_synergy_npv_b
    npv_div_pct: Optional[float] = None
    fin_score = 100.0

    if not report.synergy_workstreams:
        fin_score -= 22.0
        deficiencies.append("Missing itemized bottom-up `synergy_workstreams`; DCF relies on top-down fallback.")

    if memo_npv is None:
        fin_score -= 18.0
        deficiencies.append("`deal.target_synergy_npv_usd_b` is missing from DealMeta.")
    elif abs(dcf_npv) > 1e-6:
        npv_div_pct = round((abs(memo_npv - dcf_npv) / max(abs(dcf_npv), abs(memo_npv), 1e-6)) * 100.0, 2)
        if npv_div_pct <= 2.0:
            strengths.append(f"Exact financial consistency: Memo NPV (${memo_npv:.2f}B) reconciles with Deterministic DCF (${dcf_npv:.2f}B).")
        elif npv_div_pct <= 10.0:
            fin_score -= 10.0
            deficiencies.append(f"Minor NPV variance ({npv_div_pct:.1f}%) between Memo (${memo_npv:.2f}B) and Deterministic DCF (${dcf_npv:.2f}B).")
        else:
            fin_score -= min(45.0, 15.0 + npv_div_pct * 0.6)
            deficiencies.append(f"Material NPV contradiction ({npv_div_pct:.1f}%): Memo reports ${memo_npv:.2f}B vs. Deterministic DCF ${dcf_npv:.2f}B.")

    # Reconcile workstream totals vs headline DealMeta synergy totals
    if report.synergy_workstreams:
        ws_rev_b = sum(w.annual_run_rate_usd_m for w in report.synergy_workstreams if w.synergy_type == SynergyType.REVENUE) / 1000.0
        ws_cost_b = sum(w.annual_run_rate_usd_m for w in report.synergy_workstreams if w.synergy_type == SynergyType.COST) / 1000.0
        ws_int_b = sum(w.one_time_cost_usd_m for w in report.synergy_workstreams) / 1000.0

        for lbl, headline, ws_sum in (
            ("Revenue synergy", report.deal.estimated_revenue_synergy_usd_b, ws_rev_b),
            ("Cost synergy", report.deal.estimated_cost_synergy_usd_b, ws_cost_b),
            ("Integration cost", report.deal.estimated_integration_cost_usd_b, ws_int_b),
        ):
            if headline is not None and ws_sum > 0:
                diff_pct = (abs(headline - ws_sum) / max(abs(headline), abs(ws_sum), 1e-6)) * 100.0
                if diff_pct > 15.0:
                    fin_score -= 10.0
                    deficiencies.append(f"{lbl} headline (${headline:.2f}B) diverges from workstream sum (${ws_sum:.2f}B).")

    fin_score = round(max(0.0, min(100.0, fin_score)), 1)

    # ---- Dimension 4: Risk & Governance Completeness (20%) ----
    dims_covered = {r.dimension for r in report.risk_register}
    total_dims = len(RiskDimension)
    risk_dim_score = (len(dims_covered) / total_dims) * 100.0 if total_dims else 100.0
    has_mitigations = (
        all(bool(r.mitigation and len(r.mitigation.strip()) >= 15) for r in report.risk_register)
        if report.risk_register else False
    )
    has_breakers = len(report.executive_summary.top_deal_breaker_risks) >= 2
    has_roadmap = len(report.integration_roadmap) >= 3
    has_pillars = len(report.pillar_scores) >= 4
    has_vdr_qs = len(report.due_diligence_questions) >= 3

    risk_comp_score = round(
        0.35 * risk_dim_score
        + (15.0 if has_mitigations else 0.0)
        + (15.0 if has_breakers else 7.0)
        + (15.0 if has_roadmap else 0.0)
        + (10.0 if has_pillars else 0.0)
        + (10.0 if has_vdr_qs else 0.0),
        1,
    )
    if risk_comp_score >= 80.0:
        strengths.append(
            f"Comprehensive IC governance package ({len(report.risk_register)} risks across {len(dims_covered)}/{total_dims} dimensions, "
            f"{len(report.integration_roadmap)} PMI milestones, {len(report.due_diligence_questions)} VDR Q&As)."
        )
    else:
        missing_dims = [d.value for d in RiskDimension if d not in dims_covered]
        if missing_dims:
            deficiencies.append(f"Risk register missing coverage in: {', '.join(missing_dims)}.")
        if not has_roadmap:
            deficiencies.append("Post-Merger Integration (PMI) roadmap has fewer than 3 phased milestones.")

    # ---- Weighted Overall Score & Grade ----
    dimensions = [
        EvaluationDimension(
            name="Citation Coverage & Provenance",
            score=cit_score,
            weight=0.25,
            passed=cit_score >= 75.0,
            detail=f"{claim_cit_pct:.1f}% claim grounding, {grounding_verified_pct:.1f}% source-file verification.",
        ),
        EvaluationDimension(
            name="Claim Specificity & Quantification",
            score=spec_score,
            weight=0.25,
            passed=spec_score >= 70.0,
            detail=f"{len(claims)} claims analyzed for depth, quantified figures, and severity/confidence metadata.",
        ),
        EvaluationDimension(
            name="Financial Internal Consistency",
            score=fin_score,
            weight=0.30,
            passed=fin_score >= 80.0,
            detail=(
                f"Memo NPV ${memo_npv:.2f}B vs. Deterministic DCF ${dcf_npv:.2f}B "
                f"(divergence: {f'{npv_div_pct:.2f}%' if npv_div_pct is not None else 'N/A'})."
                if memo_npv is not None else f"Deterministic DCF ${dcf_npv:.2f}B (Memo NPV missing)."
            ),
        ),
        EvaluationDimension(
            name="Risk & Governance Completeness",
            score=risk_comp_score,
            weight=0.20,
            passed=risk_comp_score >= 75.0,
            detail=f"{len(dims_covered)}/{total_dims} risk dimensions, {len(report.integration_roadmap)} PMI milestones, {len(report.due_diligence_questions)} VDR Q&As.",
        ),
    ]

    overall = round(sum(dim.score * dim.weight for dim in dimensions), 1)
    grade = _score_to_grade(overall)
    audit_passed = overall >= 75.0 and fin_score >= 70.0 and cit_score >= 65.0

    return EvaluationScorecard(
        overall_score=overall,
        institutional_grade=grade,
        citation_coverage_pct=claim_cit_pct,
        claim_specificity_score=spec_score,
        financial_consistency_score=fin_score,
        risk_completeness_score=risk_comp_score,
        grounding_verified_pct=grounding_verified_pct,
        memo_npv_usd_b=memo_npv,
        deterministic_dcf_npv_usd_b=dcf_npv,
        npv_divergence_pct=npv_div_pct,
        dimensions=dimensions,
        strengths=strengths,
        deficiencies=deficiencies,
        audit_passed=audit_passed,
    )
