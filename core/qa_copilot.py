"""
Investment Committee (IC) Interactive Q&A Copilot.

Allows partners and deal teams to interrogate any generated or loaded
`DueDiligenceReport` (and optional extracted VDR documents) in natural language:
  - LIVE mode: calls the configured OpenAI-compatible LLM with the structured
    report JSON, deterministic DCF outputs, and top keyword-matched VDR pages.
  - OFFLINE / MOCK mode: performs grounded deterministic synthesis over the
    report's claims, citations, synergy workstreams, DCF math, risk register,
    PMI roadmap, and VDR Q&A list so the copilot is genuinely useful even
    without an API key.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from config.settings import Settings, resolve_settings
from core.financial_model import SynergyDCFResult, compute_synergy_dcf
from prompts.system_prompt import QA_SYSTEM_PROMPT
from schemas.report_schema import DueDiligenceReport
from utils.models import EntityDocuments

logger = logging.getLogger(__name__)


SUGGESTED_IC_PROMPTS = [
    "Summarize the core Investment Committee thesis and top 3 deal-breakers.",
    "Stress-test the synergy valuation — what drives NPV and where is the downside?",
    "What are the biggest antitrust, regulatory, and ESG hurdles to closing?",
    "Evaluate tech stack clash and talent retention risks — how should we ring-fence?",
    "Draft the priority Virtual Data Room (VDR) follow-up questions for target management.",
    "Walk through the Day-1 and First-100-Days Post-Merger Integration (PMI) priorities.",
]


def _extract_relevant_vdr_snippets(
    question: str,
    entities: list[EntityDocuments],
    max_chars: int = 12000,
) -> str:
    """Rank uploaded VDR pages by lexical overlap with the IC question."""
    tokens = {w for w in re.findall(r"[a-zA-Z0-9]{3,}", question.lower())}
    if not tokens or not entities:
        return ""

    scored: list[tuple[int, str]] = []
    for ent in entities:
        for doc in ent.documents:
            for idx in range(len(doc.pages)):
                body = doc.page_with_tables(idx)
                if not body.strip():
                    continue
                lower = body.lower()
                hits = sum(1 for t in tokens if t in lower)
                if hits > 0:
                    scored.append((hits, f"[{ent.entity_name} | {doc.filename} | p.{idx + 1}]\n{body[:2500]}"))

    scored.sort(key=lambda item: item[0], reverse=True)
    parts: list[str] = []
    used = 0
    for _hits, snippet in scored[:6]:
        if used + len(snippet) + 2 > max_chars:
            break
        parts.append(snippet)
        used += len(snippet) + 2
    return "\n\n".join(parts)


def _format_citations(sources) -> str:
    if not sources:
        return ""
    refs = ", ".join(f"`{s.source_document}` ({s.location})" for s in sources)
    return f" — *Sources: {refs}*"


def _offline_answer(
    question: str,
    report: DueDiligenceReport,
    dcf: SynergyDCFResult,
) -> str:
    """Deterministic grounded answer engine for offline / MOCK mode."""
    q = question.lower()
    d = report.deal
    es = report.executive_summary
    header = f"**IC Copilot Analysis — {d.entity_a_name} × {d.entity_b_name}**\n\n"

    # 1. Valuation / DCF / Synergy math
    if any(k in q for k in ("dcf", "npv", "valuat", "synerg", "wacc", "irr", "payback", "downside", "stress", "financial", "money", "bewert")):
        lines = [
            header,
            f"### Deterministic Synergy Valuation Snapshot",
            f"- **Deal Value:** {f'${d.deal_value_usd_b:.1f}B' if d.deal_value_usd_b else 'N/A'} | **Recommendation:** `{es.recommendation.value}`",
            f"- **Risk-Adjusted Steady-State Synergies:** `${dcf.steady_state_total_synergy_b:.2f}B/yr` (Revenue: `${dcf.steady_state_revenue_synergy_b:.2f}B`, Cost: `${dcf.steady_state_cost_synergy_b:.2f}B`)",
            f"- **One-Time Integration Costs:** `${dcf.total_integration_cost_b:.2f}B`",
            f"- **Total Synergy NPV (@ {dcf.assumptions.wacc_pct:.1f}% WACC, {dcf.assumptions.tax_rate_pct:.0f}% Tax):** **`${dcf.total_synergy_npv_b:.2f}B`**"
            + (f" ({dcf.value_creation_pct_of_deal:.1f}% of Deal Value)" if dcf.value_creation_pct_of_deal is not None else ""),
            f"- **Payback / IRR:** {f'{dcf.payback_years:.2f} yrs' if dcf.payback_years else 'N/A'} simple payback | {f'{dcf.synergy_irr_pct:.1f}%' if dcf.synergy_irr_pct else 'N/A'} project IRR",
        ]
        if report.valuation_commentary:
            lines.append(f"\n**IC Valuation Commentary:** {report.valuation_commentary}")
        if report.synergy_workstreams:
            lines.append("\n**Top Value Drivers (Bottom-Up Workstreams):**")
            top_ws = sorted(
                report.synergy_workstreams,
                key=lambda w: w.annual_run_rate_usd_m * (w.probability_pct / 100.0),
                reverse=True,
            )[:4]
            for ws in top_ws:
                adj = ws.annual_run_rate_usd_m * (ws.probability_pct / 100.0)
                lines.append(
                    f"- **{ws.name}** ({ws.synergy_type.value}): `${ws.annual_run_rate_usd_m:.0f}M` gross → `${adj:.0f}M` prob-adjusted ({ws.probability_pct:.0f}% prob., owner: {ws.owner}){_format_citations(ws.sources)}"
                )
        return "\n".join(lines)

    # 2. Regulatory / Antitrust / ESG
    if any(k in q for k in ("antitrust", "regulat", "cfius", "ftc", "esg", "csrd", "legal", "kartell", "closing", "hurdle")):
        lines = [header, "### Regulatory, Antitrust & ESG Assessment"]
        for c in report.regulatory_esg.antitrust_flags:
            lines.append(f"- **Antitrust:** {c.claim}{_format_citations(c.sources)}")
        for c in report.regulatory_esg.esg_csrd_gaps:
            lines.append(f"- **ESG / CSRD:** {c.claim}{_format_citations(c.sources)}")
        reg_risks = [r for r in report.risk_register if r.dimension.value == "Regulatory & ESG"]
        if reg_risks:
            lines.append("\n**Recommended Mitigations:**")
            for r in reg_risks:
                lines.append(f"- **{r.title}** (`{r.severity.value}`, L{r.likelihood}×I{r.impact}): {r.mitigation}")
        return "\n".join(lines)

    # 3. Tech / Cultural / Talent / Ring-fence
    if any(k in q for k in ("tech", "stack", "cultur", "talent", "attrition", "retain", "ring", "engineer", "people", "software")):
        lines = [header, "### Technology Architecture & Human Capital Assessment"]
        for c in report.operational_tech.tech_stack_clash:
            lines.append(f"- **Tech Stack:** {c.claim}{_format_citations(c.sources)}")
        for c in report.cultural_risk.leadership_style:
            lines.append(f"- **Leadership & Governance:** {c.claim}{_format_citations(c.sources)}")
        for c in report.cultural_risk.talent_retention_risk:
            lines.append(f"- **Talent Retention:** {c.claim}{_format_citations(c.sources)}")
        rel_risks = [r for r in report.risk_register if r.dimension.value in ("Operational & Tech", "Cultural & Talent")]
        if rel_risks:
            lines.append("\n**Actionable Mitigations:**")
            for r in rel_risks:
                lines.append(f"- **{r.title}:** {r.mitigation}")
        return "\n".join(lines)

    # 4. VDR / Questions / Data Gaps
    if any(k in q for k in ("vdr", "question", "cfo", "management", "gap", "missing", "fragen", "diligence")):
        lines = [header, "### Priority VDR & Management Follow-Up Questions"]
        if report.due_diligence_questions:
            for idx, q_item in enumerate(report.due_diligence_questions, 1):
                lines.append(f"{idx}. {q_item}")
        if report.data_gaps:
            lines.append("\n**Identified Public Data Gaps:**")
            for g in report.data_gaps:
                lines.append(f"- {g}")
        return "\n".join(lines)

    # 5. PMI / Integration / Day 1 / 100 Days
    if any(k in q for k in ("pmi", "day 1", "day-1", "100", "roadmap", "integrat", "plan", "milestone")):
        lines = [
            header,
            f"### Post-Merger Integration Roadmap (Complexity: `{es.integration_complexity.value}`)",
            f"*{es.complexity_justification}*\n",
        ]
        for m in report.integration_roadmap:
            lines.append(f"- **{m.phase} · {m.workstream}:** {m.objective} *(Owner: {m.owner} | KPI: {m.kpi})*")
        return "\n".join(lines)

    # 6. General / Executive Thesis / Rebuttal / Keyword search across all claims
    q_words = {w for w in re.findall(r"[a-zA-Z0-9]{4,}", q)}
    matched_claims = []
    for c in report.all_claims():
        overlap = sum(1 for w in q_words if w in c.claim.lower())
        if overlap > 0:
            matched_claims.append((overlap, c))
    matched_claims.sort(key=lambda x: x[0], reverse=True)

    lines = [
        header,
        f"### Investment Committee Synthesis — `{es.recommendation.value}`",
        f"{es.strategic_rationale}\n",
        f"**Stance:** {es.recommendation_rationale}\n",
        "**Top Deal-Breaker Risks:**",
    ]
    for risk in es.top_deal_breaker_risks:
        lines.append(f"- {risk.claim}{_format_citations(risk.sources)}")

    if matched_claims:
        lines.append("\n**Most Relevant Grounded Findings for Your Query:**")
        for _score, c in matched_claims[:4]:
            lines.append(f"- {c.claim}{_format_citations(c.sources)}")

    lines.append(
        "\n*Tip: Enter an OpenAI-compatible API key in the sidebar for unrestricted live LLM Q&A across full VDR documents.*"
    )
    return "\n".join(lines)


def answer_ic_question(
    question: str,
    report: DueDiligenceReport,
    history: Optional[list[dict[str, str]]] = None,
    dcf_result: Optional[SynergyDCFResult] = None,
    source_entities: Optional[list[EntityDocuments]] = None,
    config: Optional[Settings] = None,
) -> str:
    """
    Answer an Investment Committee question grounded in the report, DCF model,
    and optional VDR source documents. Uses LIVE LLM when configured, otherwise
    runs the deterministic grounded offline engine.
    """
    if not question or not question.strip():
        return "Please enter a due diligence or Investment Committee question."

    cfg = resolve_settings(config)
    dcf = dcf_result or compute_synergy_dcf(report)

    if not cfg.is_live_llm:
        return _offline_answer(question, report, dcf)

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=cfg.llm_api_key.strip(),
            base_url=(cfg.llm_base_url or "").strip() or None,
            timeout=cfg.llm_timeout_seconds,
        )

        dcf_summary = {
            "wacc_pct": dcf.assumptions.wacc_pct,
            "tax_rate_pct": dcf.assumptions.tax_rate_pct,
            "realization_factor_pct": dcf.assumptions.realization_factor_pct,
            "steady_state_revenue_synergy_b": dcf.steady_state_revenue_synergy_b,
            "steady_state_cost_synergy_b": dcf.steady_state_cost_synergy_b,
            "total_integration_cost_b": dcf.total_integration_cost_b,
            "total_synergy_npv_b": dcf.total_synergy_npv_b,
            "value_creation_pct_of_deal": dcf.value_creation_pct_of_deal,
            "payback_years": dcf.payback_years,
            "synergy_irr_pct": dcf.synergy_irr_pct,
        }
        vdr_snippets = _extract_relevant_vdr_snippets(question, source_entities or [])

        context_block = (
            f"=== DUE DILIGENCE MEMO JSON ===\n"
            f"{json.dumps(report.model_dump(mode='json'), indent=2)[:24000]}\n\n"
            f"=== DETERMINISTIC DCF OUTPUTS ===\n"
            f"{json.dumps(dcf_summary, indent=2)}\n"
        )
        if vdr_snippets:
            context_block += f"\n=== RELEVANT VDR SOURCE EXCERPTS ===\n{vdr_snippets}\n"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": QA_SYSTEM_PROMPT},
            {"role": "system", "content": context_block},
        ]
        for msg in (history or [])[-6:]:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": question})

        response = client.chat.completions.create(
            model=cfg.llm_model,
            messages=messages,
            temperature=0.25,
        )
        return (response.choices[0].message.content or "").strip() or _offline_answer(question, report, dcf)
    except Exception as e:  # noqa: BLE001 - graceful fallback with transparent note
        logger.warning(f"Live IC Q&A call failed, falling back to grounded offline synthesis: {e}")
        return (
            f"> *Live LLM Q&A temporarily unavailable ({e}). Showing grounded synthesis from the memo:*\n\n"
            + _offline_answer(question, report, dcf)
        )
