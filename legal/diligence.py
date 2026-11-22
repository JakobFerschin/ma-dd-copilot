"""
Institutional Legal Due Diligence, SPA Benchmarking, Antitrust/FDI & IP/Cyber/ESG Engine.

All calculations and legal heuristics are deterministic, transparent, and auditable
so M&A counsel and Investment Committees can stress-test deal risk without LLM arithmetic
or hallucinated statutory thresholds.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

from schemas.report_schema import DueDiligenceReport
from utils.models import EntityDocuments, ExtractedDocument


# =====================================================================
# 1. Material Contract & Change-of-Control (CoC) Scanner
# =====================================================================

@dataclass
class CoCFinding:
    """A single material contract / VDR clause risk finding."""
    clause_type: str                     # Change-of-Control | Anti-Assignment | Exclusivity | Most-Favored-Nation (MFN) | Termination-for-Convenience
    severity: str                        # Critical | High | Medium | Low
    source_document: str
    location: str
    counterparty_or_context: str
    excerpt: str
    revenue_at_risk_usd_m: float
    consent_required: bool
    required_action: str
    is_synthesized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CLAUSE_RULES: tuple[dict[str, Any], ...] = (
    {
        "clause_type": "Change-of-Control",
        "severity": "Critical",
        "consent_required": True,
        "default_rev_share": 0.045,
        "pattern": re.compile(
            r"(?:change[\s\-]+of[\s\-]+control|control\s+transfer|deemed\s+assignment|"
            r"acquisition\s+of\s+(?:more\s+than\s+)?50%|merger\s+or\s+consolidation|"
            r"transfer\s+of\s+substantially\s+all\s+assets)",
            re.IGNORECASE,
        ),
        "action": (
            "Request written Change-of-Control waiver prior to Closing; "
            "add counterparty consent as a closing Condition Precedent (CP) in the SPA."
        ),
    },
    {
        "clause_type": "Anti-Assignment",
        "severity": "High",
        "consent_required": True,
        "default_rev_share": 0.030,
        "pattern": re.compile(
            r"(?:anti[\s\-]+assignment|(?:may|shall)\s+not\s+assign|"
            r"assignment\s+by\s+operation\s+of\s+law|"
            r"(?:without|unless)\s+(?:the\s+)?prior\s+written\s+consent)",
            re.IGNORECASE,
        ),
        "action": (
            "Confirm whether acquisition structure (reverse triangular merger vs. asset deal) "
            "triggers assignment-by-operation-of-law; obtain advance written consent."
        ),
    },
    {
        "clause_type": "Exclusivity",
        "severity": "High",
        "consent_required": True,
        "default_rev_share": 0.035,
        "pattern": re.compile(
            r"(?:exclusiv(?:e|ity)\s+(?:supply|distribution|partner|commercial|rights?|arrangement|covenant)|"
            r"sole\s+(?:supplier|provider|distributor)|non[\s\-]?compete\s+covenant|"
            r"right\s+of\s+first\s+refusal|minimum\s+purchase\s+commitment)",
            re.IGNORECASE,
        ),
        "action": (
            "Audit territorial & product scope of exclusivity to ensure acquirer's existing "
            "business lines are not inadvertently bound ('affiliate contagion' risk)."
        ),
    },
    {
        "clause_type": "Most-Favored-Nation (MFN)",
        "severity": "Medium",
        "consent_required": False,
        "default_rev_share": 0.020,
        "pattern": re.compile(
            r"(?:most[\s\-]+favored[\s\-]+(?:nation|customer|pricing)|"
            r"\bMFN\b|best[\s\-]+price\s+(?:guarantee|clause)|price\s+parity\s+clause)",
            re.IGNORECASE,
        ),
        "action": (
            "Quantify cross-entity pricing spillover under MFN clause; ring-fence target "
            "contracting entity post-close to prevent acquirer catalog repricing."
        ),
    },
    {
        "clause_type": "Termination-for-Convenience",
        "severity": "Medium",
        "consent_required": False,
        "default_rev_share": 0.025,
        "pattern": re.compile(
            r"(?:terminat(?:e|ion)\s+for\s+convenience|terminat(?:e|ion)\s+without\s+cause|"
            r"terminat(?:e|ion)\s+at\s+will|cancel\s+upon\s+\d+\s+days['’]?\s+(?:prior\s+)?notice)",
            re.IGNORECASE,
        ),
        "action": (
            "Verify customer churn exposure and renewal lock-in; engage top-10 accounts "
            "during confirmatory diligence to secure post-signing continuity letters."
        ),
    },
)

_MONEY_RE = re.compile(
    r"(?:\$|USD\s*|€|EUR\s*)\s*(\d{1,4}(?:[,\d]*)(?:\.\d+)?)\s*(billion|bn|b\b|million|mn|m\b)?",
    re.IGNORECASE,
)


def _extract_nearby_usd_m(snippet: str, fallback_usd_m: float) -> float:
    """Extract a realistic USD-millions dollar figure from a clause excerpt, or use fallback."""
    for match in _MONEY_RE.finditer(snippet):
        raw_num = match.group(1).replace(",", "")
        unit = (match.group(2) or "").lower()
        try:
            val = float(raw_num)
        except ValueError:
            continue
        if val <= 0:
            continue
        if unit.startswith("b"):
            usd_m = val * 1000.0
        elif unit.startswith("m"):
            usd_m = val
        elif val >= 1_000_000:
            usd_m = val / 1_000_000.0
        else:
            continue
        if 1.0 <= usd_m <= 50_000.0:
            return round(usd_m, 1)
    return round(max(5.0, fallback_usd_m), 1)


def _iter_source_pages(entities: Any) -> list[tuple[str, str, str]]:
    """
    Normalize heterogeneous inputs into `(source_document, location, page_text)` tuples.
    Supports EntityDocuments, ExtractedDocument, lists/tuples thereof, dicts, or raw strings.
    """
    if not entities:
        return []

    items: Sequence[Any]
    if isinstance(entities, (EntityDocuments, ExtractedDocument, dict, str)):
        items = [entities]
    elif isinstance(entities, Iterable):
        items = list(entities)
    else:
        return []

    pages: list[tuple[str, str, str]] = []
    for idx, item in enumerate(items):
        if isinstance(item, EntityDocuments):
            for doc in item.documents:
                if doc.pages:
                    for p_idx, p_txt in enumerate(doc.pages):
                        if p_txt and p_txt.strip():
                            pages.append((doc.filename, f"p.{p_idx + 1}", p_txt))
                elif doc.full_text.strip():
                    pages.append((doc.filename, "Full Text", doc.full_text))
        elif isinstance(item, ExtractedDocument):
            if item.pages:
                for p_idx, p_txt in enumerate(item.pages):
                    if p_txt and p_txt.strip():
                        pages.append((item.filename, f"p.{p_idx + 1}", p_txt))
            elif item.full_text.strip():
                pages.append((item.filename, "Full Text", item.full_text))
        elif isinstance(item, dict):
            doc_name = str(item.get("filename") or item.get("source_document") or f"VDR_Contract_{idx + 1}.txt")
            loc = str(item.get("location") or "Section 1")
            txt = str(item.get("text") or item.get("content") or "")
            if txt.strip():
                pages.append((doc_name, loc, txt))
        elif isinstance(item, str) and item.strip():
            pages.append((f"Uploaded_Contract_{idx + 1}.txt", "Clause Extract", item))
    return pages


def _infer_counterparty_context(snippet: str, entity_fallback: str) -> str:
    """Pull a concise clause context label from surrounding text."""
    clean = re.sub(r"\s+", " ", snippet).strip()
    m = re.search(
        r"(?:with|between|by)\s+([A-Z][A-Za-z0-9&\-\s]{2,40}?)(?:\s+(?:Inc|Corp|LLC|AG|Ltd|GmbH|Co)\b|\s*[,\.\(\;])",
        clean,
    )
    if m:
        return m.group(1).strip()
    return entity_fallback


def scan_change_of_control(
    entities: Any,
    report: Optional[DueDiligenceReport] = None,
) -> list[CoCFinding]:
    """
    Scan uploaded contract / VDR documents for Change-of-Control, Anti-Assignment,
    Exclusivity, Most-Favored-Nation (MFN), and Termination-for-Convenience clauses.

    When no raw contract text is provided (or no matches are found) and a
    `DueDiligenceReport` is supplied, synthesizes grounded baseline flags from the
    report's claims, risk register, and VDR Q&A so Legal DD is immediately actionable.
    """
    deal_val_m = (
        (report.deal.deal_value_usd_b * 1000.0)
        if (report and report.deal.deal_value_usd_b)
        else 1500.0
    )
    target_name = report.deal.entity_b_name if report else "Target Entity"
    findings: list[CoCFinding] = []
    seen_keys: set[tuple[str, str, str]] = set()

    # 1. Direct scan over uploaded VDR / contract pages
    for doc_name, location, text in _iter_source_pages(entities):
        for rule in _CLAUSE_RULES:
            for match in rule["pattern"].finditer(text):
                start = max(0, match.start() - 120)
                end = min(len(text), match.end() + 160)
                excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
                key = (rule["clause_type"], doc_name, location)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                fallback_rev = deal_val_m * rule["default_rev_share"]
                rev_at_risk = _extract_nearby_usd_m(excerpt, fallback_rev)
                context = _infer_counterparty_context(excerpt, f"{target_name} — Material Agreement")

                findings.append(
                    CoCFinding(
                        clause_type=rule["clause_type"],
                        severity=rule["severity"],
                        source_document=doc_name,
                        location=location,
                        counterparty_or_context=context,
                        excerpt=f"...{excerpt}...",
                        revenue_at_risk_usd_m=rev_at_risk,
                        consent_required=rule["consent_required"],
                        required_action=rule["action"],
                        is_synthesized=False,
                    )
                )

    # 2. Synthesize grounded baseline flags from report when no raw contracts triggered matches
    if not findings and report is not None:
        for claim in report.all_claims():
            for rule in _CLAUSE_RULES:
                if rule["pattern"].search(claim.claim):
                    src_doc = claim.sources[0].source_document if claim.sources else f"{target_name}_Filing.pdf"
                    src_loc = claim.sources[0].location if claim.sources else "Report Risk Analysis"
                    key = (rule["clause_type"], src_doc, src_loc)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    rev_at_risk = (
                        round(claim.quantified_impact_usd_m, 1)
                        if claim.quantified_impact_usd_m
                        else _extract_nearby_usd_m(claim.claim, deal_val_m * rule["default_rev_share"])
                    )
                    findings.append(
                        CoCFinding(
                            clause_type=rule["clause_type"],
                            severity=(claim.severity.value if claim.severity else rule["severity"]),
                            source_document=src_doc,
                            location=src_loc,
                            counterparty_or_context=_infer_counterparty_context(
                                claim.claim, f"{target_name} — Key Commercial Contract"
                            ),
                            excerpt=claim.claim[:280],
                            revenue_at_risk_usd_m=rev_at_risk,
                            consent_required=rule["consent_required"],
                            required_action=rule["action"],
                            is_synthesized=True,
                        )
                    )

        # Ensure baseline institutional contract-review checklist if report text had no explicit clause regex hits
        existing_types = {f.clause_type for f in findings}
        baseline_templates = [
            (
                "Change-of-Control",
                "High",
                True,
                0.04,
                f"{target_name} Top-10 Enterprise Customer & OEM Agreements",
                (
                    f"No raw contracts uploaded; baseline Legal DD requires Change-of-Control audit "
                    f"across {target_name}'s top customer, JV, and IP license agreements."
                ),
                "Request full VDR Material Contracts folder (>1% revenue threshold) and verify CoC consent triggers.",
            ),
            (
                "Anti-Assignment",
                "Medium",
                True,
                0.025,
                f"{target_name} Cloud, SaaS & Core Technology Vendor Licenses",
                (
                    f"Standard enterprise software, cloud hosting, and patent cross-licenses typically "
                    f"contain anti-assignment clauses triggered by control transfers."
                ),
                "Review Schedule 3.4 consents list and pre-clear critical IT/IP licenses prior to signing.",
            ),
            (
                "Most-Favored-Nation (MFN)",
                "Medium",
                False,
                0.015,
                f"{target_name} Strategic Enterprise Pricing Schedules",
                (
                    f"Screen major B2B / distribution agreements for MFN and price-parity covenants that "
                    f"could spill over into {report.deal.entity_a_name}'s standalone catalog post-close."
                ),
                "Require seller disclosure of all MFN / price-protection clauses in Disclosure Schedules.",
            ),
        ]
        default_doc = (
            report.deal.documents_analyzed[0]
            if report.deal.documents_analyzed
            else f"{target_name}_VDR_Baseline.pdf"
        )
        for ctype, sev, consent, share, ctx, excerpt, action in baseline_templates:
            if ctype not in existing_types:
                findings.append(
                    CoCFinding(
                        clause_type=ctype,
                        severity=sev,
                        source_document=default_doc,
                        location="Synthesized Baseline Legal DD",
                        counterparty_or_context=ctx,
                        excerpt=excerpt,
                        revenue_at_risk_usd_m=round(max(10.0, deal_val_m * share), 1),
                        consent_required=consent,
                        required_action=action,
                        is_synthesized=True,
                    )
                )

    severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 9), -f.revenue_at_risk_usd_m))
    return findings


# =====================================================================
# 2. SPA / APA Institutional Market Benchmarking (ABA / CMS Standards)
# =====================================================================

@dataclass
class SPABenchmarkItem:
    """Comparison of a single SPA/APA economic or legal protection term vs. market median."""
    term: str
    proposed_value: str
    market_median: str
    market_range: str
    status: str                  # Buyer-Friendly | Market | Buyer-Unfriendly | Watch
    gap_analysis: str
    recommendation: str
    benchmark_source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SPABenchmarkResult:
    """Complete SPA / APA market benchmark assessment."""
    deal_value_usd_m: float
    deal_size_tier: str
    buyer_protection_score: int  # 0-100
    overall_stance: str          # Buyer-Friendly | Balanced / Market | Seller-Friendly — Renegotiate
    estimated_rwi_cost_usd_m: float
    items: list[SPABenchmarkItem] = field(default_factory=list)
    buyer_unfriendly_gaps: list[str] = field(default_factory=list)
    recommended_markup_points: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deal_value_usd_m": self.deal_value_usd_m,
            "deal_size_tier": self.deal_size_tier,
            "buyer_protection_score": self.buyer_protection_score,
            "overall_stance": self.overall_stance,
            "estimated_rwi_cost_usd_m": self.estimated_rwi_cost_usd_m,
            "items": [i.to_dict() for i in self.items],
            "buyer_unfriendly_gaps": list(self.buyer_unfriendly_gaps),
            "recommended_markup_points": list(self.recommended_markup_points),
        }


def benchmark_spa_terms(
    deal_value_usd_m: float,
    liability_cap_pct: float = 10.0,
    basket_pct: float = 0.75,
    escrow_pct: float = 8.0,
    survival_months: int = 18,
    has_rw_insurance: bool = True,
    has_mac_carveouts: bool = True,
) -> SPABenchmarkResult:
    """
    Benchmark proposed SPA/APA risk-allocation terms against ABA Private Target
    M&A Deal Points Study & CMS European M&A Study institutional medians.
    """
    ev_m = max(1.0, float(deal_value_usd_m))
    if ev_m < 100.0:
        tier = "Lower Middle Market (<$100M)"
        non_rwi_cap_median = 15.0
        non_rwi_escrow_median = 10.0
    elif ev_m <= 1000.0:
        tier = "Middle Market ($100M–$1B)"
        non_rwi_cap_median = 10.0
        non_rwi_escrow_median = 8.0
    else:
        tier = "Large Cap (>$1B)"
        non_rwi_cap_median = 10.0
        non_rwi_escrow_median = 7.5

    items: list[SPABenchmarkItem] = []
    gaps: list[str] = []
    markups: list[str] = []
    score = 78  # institutional market baseline

    # 1. General Indemnity / Liability Cap
    if has_rw_insurance:
        cap_median_str = "10.0% policy limit (0.5%–1.0% seller retention)"
        cap_range_str = "10.0% RWI limit / 0.5%–1.0% seller cap"
        if liability_cap_pct < 8.0:
            cap_status = "Buyer-Unfriendly"
            score -= 14
            cap_gap = (
                f"Proposed {liability_cap_pct:.1f}% effective coverage cap falls below the "
                "standard 10% RWI policy limit for institutional transactions."
            )
            cap_rec = "Increase combined RWI policy limit + seller retention to at least 10% of EV; carve out Fraud & Fundamental Reps to 100% EV."
            gaps.append(f"Liability Cap ({liability_cap_pct:.1f}% EV) below 10% RWI market standard.")
            markups.append(cap_rec)
        elif liability_cap_pct >= 12.0:
            cap_status = "Buyer-Friendly"
            score += 6
            cap_gap = f"Proposed {liability_cap_pct:.1f}% cap exceeds 10% market norm, giving buyer strong tail-risk coverage."
            cap_rec = "Maintain current cap; confirm seller retention split (50/50 of 1.0% retention) aligns with RWI underwriting."
        else:
            cap_status = "Market"
            cap_gap = f"Proposed {liability_cap_pct:.1f}% total coverage aligns with institutional RWI deals (10% policy limit)."
            cap_rec = "Confirm Fundamental Reps, Tax Indemnity, and Known Specific Indemnities sit outside the general cap."
    else:
        cap_median_str = f"{non_rwi_cap_median:.1f}% of EV"
        cap_range_str = "10.0%–15.0% of EV (non-RWI)"
        if liability_cap_pct < non_rwi_cap_median * 0.8:
            cap_status = "Buyer-Unfriendly"
            score -= 18
            cap_gap = (
                f"Without R&W Insurance, a {liability_cap_pct:.1f}% liability cap is materially below "
                f"the ABA/CMS non-RWI median of {non_rwi_cap_median:.1f}%."
            )
            cap_rec = f"Raise general indemnity cap to >={non_rwi_cap_median:.1f}% of EV or procure a 10% R&W Insurance policy."
            gaps.append(f"Uninsured Liability Cap ({liability_cap_pct:.1f}% EV) materially below {non_rwi_cap_median:.1f}% median.")
            markups.append(cap_rec)
        elif liability_cap_pct > non_rwi_cap_median * 1.25:
            cap_status = "Buyer-Friendly"
            score += 8
            cap_gap = f"Proposed {liability_cap_pct:.1f}% cap provides above-market buyer recourse in a non-RWI deal."
            cap_rec = "Preserve cap while ensuring Fundamental Reps & Fraud remain capped at 100% of Purchase Price."
        else:
            cap_status = "Market"
            cap_gap = f"Proposed {liability_cap_pct:.1f}% cap tracks the ABA/CMS non-RWI median ({non_rwi_cap_median:.1f}%)."
            cap_rec = "Ensure special indemnities (litigation, tax, environmental) are uncapped or carry a separate pool."

    items.append(
        SPABenchmarkItem(
            term="General Liability Cap (% EV)",
            proposed_value=f"{liability_cap_pct:.2f}% (${ev_m * liability_cap_pct / 100:.1f}M)",
            market_median=cap_median_str,
            market_range=cap_range_str,
            status=cap_status,
            gap_analysis=cap_gap,
            recommendation=cap_rec,
            benchmark_source="ABA Private Target M&A Study & CMS European M&A Study",
        )
    )

    # 2. Indemnity Basket / Deductible
    basket_median = 0.50 if has_rw_insurance else 0.75
    if basket_pct > 1.0:
        b_status = "Buyer-Unfriendly"
        score -= 12
        b_gap = f"Proposed {basket_pct:.2f}% basket exceeds the 0.50%–0.75% market range, shifting first-dollar loss onto buyer."
        b_rec = f"Reduce basket to <={basket_median:.2f}% of EV and negotiate a tipping basket (or drop-down retention to 0.5% after 12 months)."
        gaps.append(f"Indemnity Basket ({basket_pct:.2f}% EV) above market ceiling (<=0.75%–1.0%).")
        markups.append(b_rec)
    elif basket_pct <= 0.50:
        b_status = "Buyer-Friendly"
        score += 5
        b_gap = f"Proposed {basket_pct:.2f}% basket is tight and buyer-protective (<=0.50% of EV)."
        b_rec = "Confirm basket does not apply to Fundamental Reps, Fraud, or Pre-Closing Taxes."
    else:
        b_status = "Market"
        b_gap = f"Proposed {basket_pct:.2f}% basket sits squarely within ABA/CMS institutional norms (0.50%–0.75%)."
        b_rec = "Negotiate a 12-month retention drop-down to 0.50% if using RWI."

    items.append(
        SPABenchmarkItem(
            term="Indemnity Basket / Retention (% EV)",
            proposed_value=f"{basket_pct:.2f}% (${ev_m * basket_pct / 100:.1f}M)",
            market_median=f"{basket_median:.2f}% of EV",
            market_range="0.50%–0.75% of EV",
            status=b_status,
            gap_analysis=b_gap,
            recommendation=b_rec,
            benchmark_source="ABA Deal Points Study (Deductible vs. Tipping Basket)",
        )
    )

    # 3. Escrow / Holdback
    if has_rw_insurance:
        esc_median = 1.0
        esc_range = "0.5%–1.5% (RWI retention split) + Special Escrow"
        if escrow_pct < 0.5:
            e_status = "Buyer-Unfriendly"
            score -= 8
            e_gap = f"Escrow of {escrow_pct:.2f}% leaves buyer exposed on seller's 50% share of RWI retention and working-capital true-up."
            e_rec = "Establish at least a 0.5% indemnity escrow plus a separate Working Capital Adjustment escrow."
            gaps.append(f"Escrow ({escrow_pct:.2f}% EV) insufficient to backstop RWI retention split.")
            markups.append(e_rec)
        elif escrow_pct >= 5.0:
            e_status = "Buyer-Friendly"
            score += 5
            e_gap = f"Escrow of {escrow_pct:.2f}% provides substantial cash collateral alongside RWI (covers excluded items / special indemnities)."
            e_rec = "Maintain dedicated special-indemnity escrow for RWI policy exclusions (known risks, pension, cyber)."
        else:
            e_status = "Market"
            e_gap = f"Escrow of {escrow_pct:.2f}% aligns with RWI-backed deals."
            e_rec = "Keep working-capital adjustment escrow segregated from indemnity escrow."
    else:
        esc_median = non_rwi_escrow_median
        esc_range = "7.5%–10.0% of EV (non-RWI)"
        if escrow_pct < 5.0:
            e_status = "Buyer-Unfriendly"
            score -= 14
            e_gap = f"Without RWI, {escrow_pct:.2f}% escrow creates collection/credit risk against seller post-closing."
            e_rec = f"Increase cash escrow to {non_rwi_escrow_median:.1f}% of EV or require parent guarantee / letter of credit."
            gaps.append(f"Non-RWI Escrow ({escrow_pct:.2f}% EV) creates post-close collection risk (median {non_rwi_escrow_median:.1f}%).")
            markups.append(e_rec)
        elif escrow_pct >= non_rwi_escrow_median:
            e_status = "Buyer-Friendly" if escrow_pct > non_rwi_escrow_median + 1.5 else "Market"
            if e_status == "Buyer-Friendly":
                score += 5
            e_gap = f"Escrow of {escrow_pct:.2f}% adequately collateralizes general indemnity claims."
            e_rec = "Tie escrow release schedule to completion of first full post-closing audit cycle (18 months)."
        else:
            e_status = "Watch"
            score -= 4
            e_gap = f"Escrow of {escrow_pct:.2f}% is slightly below the {non_rwi_escrow_median:.1f}% non-RWI median."
            e_rec = "Negotiate staggered release (50% at 12m, 50% at 18m) with pending-claims holdback."

    items.append(
        SPABenchmarkItem(
            term="Indemnity Escrow / Holdback (% EV)",
            proposed_value=f"{escrow_pct:.2f}% (${ev_m * escrow_pct / 100:.1f}M)",
            market_median=f"{esc_median:.1f}% of EV",
            market_range=esc_range,
            status=e_status,
            gap_analysis=e_gap,
            recommendation=e_rec,
            benchmark_source="ABA Private Target Study & SRS Acquiom Claims Study",
        )
    )

    # 4. General Reps Survival Period
    if survival_months < 12:
        s_status = "Buyer-Unfriendly"
        score -= 14
        s_gap = f"Survival of {survival_months} months expires before buyer completes one full post-closing financial audit cycle."
        s_rec = "Extend general reps survival to >=18 months (or >=36 months via RWI policy) and Fundamental/Tax reps to statute of limitations + 60 days."
        gaps.append(f"General Reps Survival ({survival_months} months) shorter than 1 full audit cycle (<12m).")
        markups.append(s_rec)
    elif survival_months >= 24:
        s_status = "Buyer-Friendly"
        score += 6
        s_gap = f"Survival of {survival_months} months exceeds ABA US median (12–18m) and matches top-quartile European CMS practice."
        s_rec = "Lock in 24-month survival and align RWI policy period (3 years general / 6 years fundamental & tax)."
    else:
        s_status = "Market"
        s_gap = f"Survival of {survival_months} months aligns with ABA (12–18m) & CMS European (18–24m) medians."
        s_rec = "Ensure Fundamental Reps, Tax, and Environmental/IP survive for 60–72 months or applicable statute of limitations."

    items.append(
        SPABenchmarkItem(
            term="General Reps Survival Period",
            proposed_value=f"{survival_months} months",
            market_median="18 months (US: 12–18m / EU: 18–24m)",
            market_range="12–24 months",
            status=s_status,
            gap_analysis=s_gap,
            recommendation=s_rec,
            benchmark_source="ABA Private Target Study & CMS European M&A Study",
        )
    )

    # 5. R&W / W&I Insurance
    rwi_limit_m = ev_m * 0.10
    rwi_cost_m = round(rwi_limit_m * 0.030, 2) if has_rw_insurance else 0.0
    if has_rw_insurance:
        rwi_status = "Buyer-Friendly"
        score += 6
        rwi_gap = (
            f"R&W Insurance active: transfers general rep breach risk to A-rated insurer "
            f"(~${rwi_limit_m:.1f}M limit; est. all-in premium + underwriting fee ~${rwi_cost_m:.2f}M)."
        )
        rwi_rec = "Review insurer exclusions carefully (known diligence issues, cyber/privacy sub-limits, transfer pricing) and mirror with seller special indemnities."
    else:
        rwi_status = "Watch"
        score -= 4
        rwi_gap = "No R&W Insurance proposed; buyer relies purely on seller creditworthiness and cash escrow."
        rwi_rec = "Price a buyer-side RWI / W&I policy (approx. 2.8%–3.2% of limit) to strengthen bid competitiveness and extend survival to 3–6 years."

    items.append(
        SPABenchmarkItem(
            term="R&W / W&I Insurance Structure",
            proposed_value="Included" if has_rw_insurance else "Not Included",
            market_median="Included (>75% of PE & Large-Cap deals)",
            market_range="10% EV limit; 0.75%–1.0% retention",
            status=rwi_status,
            gap_analysis=rwi_gap,
            recommendation=rwi_rec,
            benchmark_source="Aon / Marsh M&A Transaction Solutions Benchmark",
        )
    )

    # 6. Material Adverse Change (MAC / MAE) Clause
    if has_mac_carveouts:
        mac_status = "Market"
        mac_gap = (
            "Standard systemic MAC carve-outs (macro, industry, GAAP changes) are present; "
            "must verify 'disproportionate impact' exception protects buyer."
        )
        mac_rec = (
            "Insert explicit 'disproportionate adverse effect' exception to all industry/macro carve-outs "
            "and exclude seller-specific regulatory/cyber shocks from carve-outs."
        )
    else:
        mac_status = "Buyer-Friendly"
        score += 7
        mac_gap = "No broad seller MAC carve-outs — gives buyer stronger walk-away rights between Signing and Closing."
        mac_rec = "Expect seller pushback to add standard macro carve-outs; trade for tight disproportionate-impact language."

    items.append(
        SPABenchmarkItem(
            term="MAC / MAE Carve-Outs",
            proposed_value="Standard Carve-Outs Included" if has_mac_carveouts else "Clean Buyer MAC (No Carve-Outs)",
            market_median="Systemic carve-outs + Disproportionate Impact qualifier",
            market_range="95%+ of ABA deals include macro carve-outs",
            status=mac_status,
            gap_analysis=mac_gap,
            recommendation=mac_rec,
            benchmark_source="ABA Private Target M&A Study (MAC Definition)",
        )
    )

    score = max(0, min(100, score))
    if score >= 80 and not gaps:
        stance = "Buyer-Friendly"
    elif score >= 62 and len(gaps) <= 1:
        stance = "Balanced / Market"
    else:
        stance = "Seller-Friendly — Renegotiate"

    if not markups:
        markups.append(
            "Verify Sandbagging (pro-sandbagging preferred in NY/DE), Fraud carve-out, and "
            "Locked-Box vs. Closing Accounts leakage covenants before final SPA execution."
        )

    return SPABenchmarkResult(
        deal_value_usd_m=round(ev_m, 2),
        deal_size_tier=tier,
        buyer_protection_score=score,
        overall_stance=stance,
        estimated_rwi_cost_usd_m=rwi_cost_m,
        items=items,
        buyer_unfriendly_gaps=gaps,
        recommended_markup_points=markups,
    )


# =====================================================================
# 3. Antitrust Merger Control & FDI National-Security Screening
# =====================================================================

@dataclass
class AntitrustJurisdictionCheck:
    """Mandatory filing & risk assessment for a single merger control authority."""
    jurisdiction: str            # US HSR | EU Commission | UK CMA | German Bundeskartellamt | China SAMR
    authority: str
    filing_mandatory: bool
    trigger_rationale: str
    review_risk: str             # Low | Moderate | High | Critical
    estimated_months: float
    suspensory: bool             # Gun-jumping bar prior to clearance

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AntitrustAssessment:
    """Multi-jurisdictional merger control + CFIUS / AWG FDI screening result."""
    acquirer_rev_b: float
    target_rev_b: float
    combined_market_share_pct: float
    sector: str
    target_country: str
    acquirer_country: str
    overall_regulatory_risk: str         # Low | Moderate | High | Critical
    phase_2_probability_pct: float
    estimated_clearance_months: float
    clearance_timeline_range: str
    mandatory_filings_count: int
    jurisdictions: list[AntitrustJurisdictionCheck] = field(default_factory=list)
    fdi_triggers: list[dict[str, Any]] = field(default_factory=list)
    remedy_scenarios: list[str] = field(default_factory=list)
    recommended_reverse_break_fee_pct: float = 4.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "acquirer_rev_b": self.acquirer_rev_b,
            "target_rev_b": self.target_rev_b,
            "combined_market_share_pct": self.combined_market_share_pct,
            "sector": self.sector,
            "target_country": self.target_country,
            "acquirer_country": self.acquirer_country,
            "overall_regulatory_risk": self.overall_regulatory_risk,
            "phase_2_probability_pct": self.phase_2_probability_pct,
            "estimated_clearance_months": self.estimated_clearance_months,
            "clearance_timeline_range": self.clearance_timeline_range,
            "mandatory_filings_count": self.mandatory_filings_count,
            "jurisdictions": [j.to_dict() for j in self.jurisdictions],
            "fdi_triggers": list(self.fdi_triggers),
            "remedy_scenarios": list(self.remedy_scenarios),
            "recommended_reverse_break_fee_pct": self.recommended_reverse_break_fee_pct,
        }


_SENSITIVE_ANTITRUST_SECTORS = (
    "ai", "cloud", "software", "tech", "semiconductor", "pharma", "biopharma",
    "healthcare", "telecom", "defense", "aerospace", "energy", "automotive", "fintech",
)
_SENSITIVE_FDI_SECTORS = (
    "ai", "artificial intelligence", "semiconductor", "chip", "defense", "aerospace",
    "cyber", "telecom", "cloud", "data", "biotech", "biopharma", "pharma",
    "energy", "grid", "infrastructure", "automotive", "autonomous", "ev", "software",
)
_EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}


def assess_antitrust_and_fdi(
    acquirer_rev_b: float,
    target_rev_b: float,
    combined_market_share_pct: float,
    sector: str,
    target_country: str = "US",
    acquirer_country: str = "DE",
) -> AntitrustAssessment:
    """
    Evaluate mandatory merger control thresholds across US HSR, EU Commission,
    UK CMA, German Bundeskartellamt, and China SAMR, estimate Phase II probability
    and clearance timeline, and evaluate CFIUS / German AWG FDI screening triggers.
    """
    acq_b = max(0.0, float(acquirer_rev_b))
    tgt_b = max(0.0, float(target_rev_b))
    comb_b = acq_b + tgt_b
    share = max(0.0, min(100.0, float(combined_market_share_pct)))
    sec_lower = (sector or "").lower()
    t_cc = (target_country or "US").strip().upper()
    a_cc = (acquirer_country or "DE").strip().upper()

    is_sensitive_antitrust = any(k in sec_lower for k in _SENSITIVE_ANTITRUST_SECTORS)
    is_sensitive_fdi = any(k in sec_lower for k in _SENSITIVE_FDI_SECTORS)

    # Proxy deal enterprise value from target revenue (~3.0x sales conservative baseline)
    est_deal_val_b = max(tgt_b * 3.0, tgt_b)

    # ---- 1. US HSR (FTC / DOJ) ----
    # 2025 HSR size-of-transaction threshold ~$126.4M (~$0.126B)
    us_nexus = (t_cc == "US" or a_cc == "US" or comb_b >= 2.0)
    hsr_mandatory = us_nexus and (est_deal_val_b >= 0.126) and (tgt_b >= 0.025 or est_deal_val_b >= 0.505)
    if not hsr_mandatory:
        hsr_risk, hsr_months = "Low", 0.0
        hsr_reason = "Below US HSR size-of-transaction ($126.4M) or insufficient US commerce nexus."
    elif share >= 30.0 or (share >= 20.0 and is_sensitive_antitrust):
        hsr_risk, hsr_months = "Critical" if share >= 35.0 else "High", 11.0 if share >= 35.0 else 8.5
        hsr_reason = (
            f"Exceeds HSR $126.4M threshold (est. EV ~${est_deal_val_b:.2f}B); combined share {share:.1f}% "
            f"in '{sector}' triggers 2023 FTC/DOJ Merger Guidelines structural presumption (>=30%) / Second Request risk."
        )
    else:
        hsr_risk, hsr_months = "Moderate" if is_sensitive_antitrust else "Low", 3.0 if is_sensitive_antitrust else 1.5
        hsr_reason = f"Mandatory HSR filing (est. EV ~${est_deal_val_b:.2f}B > $126.4M); 30-day statutory waiting period expected."

    # ---- 2. EU Commission (EUMR) ----
    # Primary: combined worldwide > €5B (~$5.4B) & each EU turnover > €250M (proxy >=$0.30B for multinational or EU party)
    eu_party_nexus = (t_cc in _EU_COUNTRIES or a_cc in _EU_COUNTRIES)
    eu_mandatory = (comb_b >= 5.4 and tgt_b >= 0.30 and (eu_party_nexus or comb_b >= 10.0)) or (
        comb_b >= 2.7 and tgt_b >= 0.12 and eu_party_nexus
    )
    if not eu_mandatory:
        eu_risk, eu_months = "Low", 0.0
        eu_reason = "Does not meet EUMR Art. 1(2) (€5B WW / €250M EU) or Art. 1(3) turnover thresholds."
    elif share >= 25.0 or (share >= 18.0 and is_sensitive_antitrust):
        eu_risk, eu_months = "Critical" if share >= 35.0 else "High", 10.5 if share >= 35.0 else 7.5
        eu_reason = (
            f"Triggers EUMR one-stop-shop filing (combined ${comb_b:.1f}B); {share:.1f}% combined share "
            "creates horizontal/vertical overlap concerns under DG COMP Phase II scrutiny."
        )
    else:
        eu_risk, eu_months = "Moderate" if comb_b >= 15.0 else "Low", 3.0
        eu_reason = f"Mandatory EUMR notification (combined ${comb_b:.1f}B); simplified/Phase I clearance probable (25–35 working days)."

    # ---- 3. UK CMA (Enterprise Act / DMCC Act) ----
    # Voluntary regime, aggressive call-in: target UK rev >£70M (~$0.09B) OR >=25% share of supply OR DMCC hybrid (£350M UK + 33% share)
    uk_nexus = (t_cc == "UK" or a_cc == "UK" or comb_b >= 5.0)
    cma_triggered = uk_nexus and (tgt_b >= 0.50 or t_cc == "UK" or share >= 25.0)
    if not cma_triggered:
        cma_risk, cma_months = "Low", 0.0
        cma_reason = "No material UK turnover (>£70M) or >=25% Share-of-Supply trigger identified."
    elif share >= 25.0 or (is_sensitive_antitrust and comb_b >= 10.0):
        cma_risk, cma_months = "High", 9.0 if share >= 30.0 else 6.0
        cma_reason = (
            f"UK CMA call-in likely under Share-of-Supply ({share:.1f}%) / DMCC hybrid threshold; "
            "CMA actively asserts jurisdiction in global tech/biopharma/industrial mergers."
        )
    else:
        cma_risk, cma_months = "Moderate", 3.5
        cma_reason = "Voluntary briefing paper advisable to preempt CMA Initial Enforcement Order (IEO)."

    # ---- 4. German Bundeskartellamt (GWB §35) ----
    # Combined WW >€500M (~$0.54B), one party DE >€50M, other >€17.5M (or >€400M transaction value).
    # Preempted by EU one-stop-shop when EUMR applies!
    de_nexus = (t_cc == "DE" or a_cc == "DE" or comb_b >= 3.0)
    de_threshold_met = de_nexus and comb_b >= 0.54 and (tgt_b >= 0.05 or est_deal_val_b >= 0.43)
    de_mandatory = de_threshold_met and not eu_mandatory
    if eu_mandatory and de_threshold_met:
        de_risk, de_months = "Low", 0.0
        de_reason = (
            "GWB §35 thresholds exceeded, but preempted by EU Commission 'one-stop-shop' "
            "(subject to Art. 9 referral-back request for German-specific markets)."
        )
    elif not de_mandatory:
        de_risk, de_months = "Low", 0.0
        de_reason = "Below German GWB §35 turnover (€500M WW / €50M + €17.5M DE) and €400M deal-value thresholds."
    elif share >= 25.0:
        de_risk, de_months = "High", 5.5
        de_reason = f"Mandatory GWB §35 filing; {share:.1f}% combined share raises Phase II (Hauptprüfverfahren, 4–5m) risk."
    else:
        de_risk, de_months = "Low", 1.5
        de_reason = "Mandatory GWB §35 filing; Phase I clearance within 1 month expected."

    # ---- 5. China SAMR (Anti-Monopoly Law 2024 Thresholds) ----
    # Combined WW >RMB 12B (~$1.65B) & two parties >RMB 800M (~$0.11B) in China
    china_nexus = (t_cc == "CN" or a_cc == "CN" or (acq_b >= 3.0 and tgt_b >= 0.8))
    samr_mandatory = china_nexus and comb_b >= 1.65 and tgt_b >= 0.15
    if not samr_mandatory:
        samr_risk, samr_months = "Low", 0.0
        samr_reason = "Below SAMR 2024 thresholds (RMB 12B worldwide / RMB 800M China turnover for two parties)."
    elif share >= 25.0 or (is_sensitive_antitrust and comb_b >= 15.0):
        samr_risk, samr_months = "High", 10.0 if share >= 30.0 else 7.0
        samr_reason = (
            f"Mandatory SAMR filing (combined ${comb_b:.1f}B); geopolitical & supply-chain sensitivity in '{sector}' "
            "may extend statutory review via pull-and-refile."
        )
    else:
        samr_risk, samr_months = "Moderate", 4.0
        samr_reason = "Mandatory SAMR filing; eligible for Simplified Procedure (approx. 3–4 months)."

    jurisdictions = [
        AntitrustJurisdictionCheck(
            jurisdiction="US HSR",
            authority="FTC / DOJ Antitrust Division",
            filing_mandatory=hsr_mandatory,
            trigger_rationale=hsr_reason,
            review_risk=hsr_risk,
            estimated_months=hsr_months,
            suspensory=True,
        ),
        AntitrustJurisdictionCheck(
            jurisdiction="EU Commission",
            authority="European Commission (DG COMP)",
            filing_mandatory=eu_mandatory,
            trigger_rationale=eu_reason,
            review_risk=eu_risk,
            estimated_months=eu_months,
            suspensory=True,
        ),
        AntitrustJurisdictionCheck(
            jurisdiction="UK CMA",
            authority="Competition and Markets Authority",
            filing_mandatory=cma_triggered,
            trigger_rationale=cma_reason,
            review_risk=cma_risk,
            estimated_months=cma_months,
            suspensory=False,
        ),
        AntitrustJurisdictionCheck(
            jurisdiction="German Bundeskartellamt",
            authority="Bundeskartellamt (BKartA)",
            filing_mandatory=de_mandatory,
            trigger_rationale=de_reason,
            review_risk=de_risk,
            estimated_months=de_months,
            suspensory=True,
        ),
        AntitrustJurisdictionCheck(
            jurisdiction="China SAMR",
            authority="State Administration for Market Regulation",
            filing_mandatory=samr_mandatory,
            trigger_rationale=samr_reason,
            review_risk=samr_risk,
            estimated_months=samr_months,
            suspensory=True,
        ),
    ]

    # ---- 6. FDI / National Security Screening (CFIUS, German AWG/AWV, UK NSIA) ----
    fdi_triggers: list[dict[str, Any]] = []
    if t_cc == "US" and a_cc != "US":
        cfius_mandatory = is_sensitive_fdi
        cfius_risk = "High" if (is_sensitive_fdi and a_cc not in {"UK", "CA", "AU", "NZ"}) else (
            "Moderate" if is_sensitive_fdi else "Low"
        )
        if a_cc in {"CN", "RU"} and is_sensitive_fdi:
            cfius_risk = "Critical"
        fdi_triggers.append(
            {
                "regime": "US CFIUS (FIRRMA / 31 C.F.R. Part 800)",
                "triggered": True,
                "mandatory_declaration": cfius_mandatory,
                "risk_level": cfius_risk,
                "estimated_months": 5.0 if cfius_risk in {"High", "Critical"} else 2.5,
                "rationale": (
                    f"Foreign acquisition ({a_cc} acquirer -> US target) in '{sector}'. "
                    + (
                        "Likely constitutes a TID U.S. Business (Critical Technology / Infrastructure / Sensitive Personal Data), requiring mandatory CFIUS filing or full Joint Voluntary Notice."
                        if cfius_mandatory
                        else "Voluntary CFIUS notice recommended for safe-harbor certainty."
                    )
                ),
            }
        )

    if t_cc == "DE" and a_cc not in _EU_COUNTRIES:
        awg_mandatory = is_sensitive_fdi
        awg_risk = "High" if is_sensitive_fdi else "Moderate"
        fdi_triggers.append(
            {
                "regime": "German Foreign Trade Act (AWG / AWV — BMWK)",
                "triggered": True,
                "mandatory_declaration": awg_mandatory,
                "risk_level": awg_risk,
                "estimated_months": 4.5 if awg_mandatory else 2.0,
                "rationale": (
                    f"Non-EU acquirer ({a_cc}) acquiring German target in '{sector}'. "
                    "Triggers mandatory cross-sectoral BMWK screening (>=10% voting rights for critical infrastructure / >=20% for AI, semiconductors, autonomous vehicles, biotech)."
                ),
            }
        )
    elif t_cc in _EU_COUNTRIES and a_cc not in _EU_COUNTRIES:
        fdi_triggers.append(
            {
                "regime": f"EU FDI Screening Regulation (2019/452) — {t_cc} National Authority",
                "triggered": is_sensitive_fdi,
                "mandatory_declaration": is_sensitive_fdi,
                "risk_level": "Moderate" if is_sensitive_fdi else "Low",
                "estimated_months": 3.5 if is_sensitive_fdi else 1.5,
                "rationale": (
                    f"Non-EU acquirer ({a_cc}) acquiring EU target ({t_cc}) triggers EU Member State "
                    "FDI notification & European Commission cooperation mechanism."
                ),
            }
        )

    if t_cc == "UK" and is_sensitive_fdi:
        fdi_triggers.append(
            {
                "regime": "UK National Security and Investment Act (NSIA 2021)",
                "triggered": True,
                "mandatory_declaration": True,
                "risk_level": "Moderate" if a_cc in {"US", "DE", "FR"} else "High",
                "estimated_months": 3.0,
                "rationale": f"UK target operates in one of the 17 NSIA mandatory-notification sectors ('{sector}').",
            }
        )

    # ---- 7. Phase II Probability & Timeline Synthesis ----
    phase_2_prob = 5.0
    if share >= 40.0:
        phase_2_prob += 55.0
    elif share >= 30.0:
        phase_2_prob += 38.0
    elif share >= 20.0:
        phase_2_prob += 20.0
    elif share >= 12.0:
        phase_2_prob += 8.0

    if is_sensitive_antitrust:
        phase_2_prob += 12.0
    mandatory_count = sum(1 for j in jurisdictions if j.filing_mandatory)
    if mandatory_count >= 3:
        phase_2_prob += 8.0
    if comb_b >= 30.0:
        phase_2_prob += 7.0
    phase_2_prob = round(min(92.0, phase_2_prob), 1)

    max_antitrust_m = max((j.estimated_months for j in jurisdictions), default=1.5)
    max_fdi_m = max((f["estimated_months"] for f in fdi_triggers if f["triggered"]), default=0.0)
    est_months = round(max(1.5, max_antitrust_m, max_fdi_m), 1)
    low_m = max(1, int(round(est_months * 0.75)))
    high_m = max(low_m + 2, int(round(est_months * 1.35)))
    timeline_str = f"{low_m}–{high_m} months"

    if phase_2_prob >= 55.0 or any(j.review_risk == "Critical" for j in jurisdictions) or any(
        f["risk_level"] == "Critical" for f in fdi_triggers
    ):
        overall_risk = "Critical"
        rbf_pct = 6.5
    elif phase_2_prob >= 30.0 or any(j.review_risk == "High" for j in jurisdictions) or any(
        f["risk_level"] == "High" for f in fdi_triggers
    ):
        overall_risk = "High"
        rbf_pct = 5.0
    elif mandatory_count >= 2 or phase_2_prob >= 15.0:
        overall_risk = "Moderate"
        rbf_pct = 3.5
    else:
        overall_risk = "Low"
        rbf_pct = 2.5

    remedies: list[str] = []
    if phase_2_prob >= 35.0:
        remedies.append(
            "Prepare 'Fix-It-First' structural divestiture package for overlapping product lines "
            "and cap SPA divestiture commitment ('burdensome condition' / hell-or-high-water limit)."
        )
        remedies.append(
            f"Negotiate SPA Outside Date of >= {high_m + 3} months with two automatic 3-month regulatory extensions."
        )
    if is_sensitive_antitrust:
        remedies.append(
            "Offer FRAND / interoperability behavioral commitments to address vertical foreclosure concerns."
        )
    if any(f["triggered"] for f in fdi_triggers):
        remedies.append(
            "Pre-align National Security Agreement (NSA) mitigation options (US data localization, "
            "proxy board / security officer, source-code ring-fencing) for CFIUS / AWG."
        )
    if not remedies:
        remedies.append(
            "Standard regulatory cooperation covenant ('reasonable best efforts') with 6-month Outside Date."
        )

    return AntitrustAssessment(
        acquirer_rev_b=round(acq_b, 2),
        target_rev_b=round(tgt_b, 2),
        combined_market_share_pct=round(share, 1),
        sector=sector,
        target_country=t_cc,
        acquirer_country=a_cc,
        overall_regulatory_risk=overall_risk,
        phase_2_probability_pct=phase_2_prob,
        estimated_clearance_months=est_months,
        clearance_timeline_range=timeline_str,
        mandatory_filings_count=mandatory_count,
        jurisdictions=jurisdictions,
        fdi_triggers=fdi_triggers,
        remedy_scenarios=remedies,
        recommended_reverse_break_fee_pct=rbf_pct,
    )


# =====================================================================
# 4. IP / Open-Source (GPL), Cyber (SOC2/GDPR) & EU CSRD / CBAM / LkSG
# =====================================================================

@dataclass
class IPCyberESGDiligence:
    """Integrated Tech-IP, Cybersecurity & Regulatory ESG Diligence + SPA Schedules & CPs."""
    ip_risk_score: int                           # 0-100 (higher = higher risk)
    ip_rating: str                               # Low | Moderate | High | Critical
    ip_findings: list[str] = field(default_factory=list)
    oss_copyleft_flags: list[str] = field(default_factory=list)

    cyber_risk_score: int = 35                   # 0-100 (higher = higher risk)
    cyber_rating: str = "Moderate"
    cyber_findings: list[str] = field(default_factory=list)

    esg_risk_score: int = 40                     # 0-100 (higher = higher risk)
    esg_readiness_score: int = 60                # 0-100 (higher = better prepared)
    esg_rating: str = "Moderate"
    esg_findings: list[str] = field(default_factory=list)
    csrd_cbam_lksg_flags: list[str] = field(default_factory=list)

    overall_risk_score: int = 40
    disclosure_schedule_items: list[dict[str, Any]] = field(default_factory=list)
    conditions_precedent: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _score_to_rating(risk_score: int) -> str:
    if risk_score >= 75:
        return "Critical"
    if risk_score >= 55:
        return "High"
    if risk_score >= 30:
        return "Moderate"
    return "Low"


def assess_ip_cyber_esg(
    report: DueDiligenceReport,
    entities: Any = None,
) -> IPCyberESGDiligence:
    """
    Score Open-Source / GPL / IP Chain-of-Title risk, Cyber / SOC 2 / GDPR risk,
    and EU CSRD / CBAM / LkSG readiness, generating concrete Disclosure Schedule
    Items and SPA Conditions Precedent (CPs).
    """
    target = report.deal.entity_b_name
    acquirer = report.deal.entity_a_name
    sector = (report.deal.industry_sector or "").lower()

    # Aggregate analytical corpus from report + any uploaded documents
    corpus_parts = [
        report.executive_summary.strategic_rationale,
        report.executive_summary.complexity_justification,
        report.deal.industry_sector or "",
    ]
    corpus_parts.extend(c.claim for c in report.all_claims())
    corpus_parts.extend(r.title + " " + r.mitigation for r in report.risk_register)
    corpus_parts.extend(report.due_diligence_questions)
    for _, _, p_txt in _iter_source_pages(entities):
        corpus_parts.append(p_txt[:15_000])
    corpus = "\n".join(corpus_parts).lower()

    # ---- A. IP / Open-Source (GPL/AGPL) & Chain-of-Title ----
    ip_score = 32
    ip_findings: list[str] = []
    oss_flags: list[str] = []

    is_software_heavy = any(
        k in corpus or k in sector
        for k in ("software", "open-source", "open source", "zonal", "lakehouse", "spark", "ai", "cloud", "os", "architecture")
    )
    if is_software_heavy:
        ip_score += 18
        ip_findings.append(
            f"Software/IP-intensive target ({target}): Black Duck / FOSSA source-code scan mandatory to validate OSS license hygiene."
        )

    if any(k in corpus for k in ("gpl", "agpl", "lgpl", "copyleft", "open-source", "open source", "apache")):
        ip_score += 16
        oss_flags.append(
            "Copyleft / Open-Source Exposure: Detect GPL/AGPL viral propagation risk in commercialized or embedded software layers."
        )
    if any(k in corpus for k in ("patent", "litigation", "infringement", "crispr", "royalty", "chain of title", "ip")):
        ip_score += 14
        ip_findings.append(
            "Patent & Licensing Chain-of-Title: Verify inventor assignment agreements, third-party royalty stacks, and freedom-to-operate (FTO)."
        )
    if any(k in corpus for k in ("attrition", "engineer", "key-person", "retention")):
        ip_score += 8
        ip_findings.append(
            "Trade Secret & Inventor Retention: High engineering attrition risk necessitates PIIA (Proprietary Information & Inventions Agreement) audit."
        )
    if not ip_findings:
        ip_findings.append(
            f"Baseline IP Chain-of-Title audit required for {target}'s registered patents, trademarks, and contractor IP assignments."
        )
    ip_score = min(95, ip_score)

    # ---- B. Cybersecurity / SOC 2 / ISO 27001 / GDPR ----
    cyber_score = 30
    cyber_findings: list[str] = []
    if any(k in corpus for k in ("cloud", "saas", "data", "ai", "connected", "telematics", "customer data", "api")):
        cyber_score += 18
        cyber_findings.append(
            "Cloud & Data Perimeter Exposure: Require current SOC 2 Type II, ISO 27001 certificate, and external penetration-test report (<6 months old)."
        )
    if any(k in corpus for k in ("gdpr", "privacy", "personal data", "pii", "eu", "europe", "hipaa", "clinical")):
        cyber_score += 16
        cyber_findings.append(
            "Data Privacy & Cross-Border Transfers (GDPR / HIPAA / EU AI Act): Audit Standard Contractual Clauses (SCCs), DPA register, and AI training-data provenance."
        )
    if any(k in corpus for k in ("breach", "ransomware", "cyber", "security", "vulnerability", "zero-day")):
        cyber_score += 18
        cyber_findings.append(
            "Historical Incident / Vulnerability Signal: Inspect 36-month security incident log, regulatory breach notifications, and cyber insurance coverage."
        )
    if any(k in corpus for k in ("automotive", "vehicle", "sdv", "ecu")):
        cyber_score += 10
        cyber_findings.append(
            "Connected-Vehicle Cybersecurity (UN R155 / ISO 21434): Validate CSMS certification and OTA update signing infrastructure."
        )
    if not cyber_findings:
        cyber_findings.append(
            "Baseline cyber hygiene review: verify MFA coverage, EDR deployment, privileged access controls, and SOC 2 Type II attestation."
        )
    cyber_score = min(95, cyber_score)

    # ---- C. ESG / EU CSRD / CBAM / LkSG Readiness ----
    esg_score = 28
    esg_findings: list[str] = []
    csrd_flags: list[str] = []

    if report.regulatory_esg.esg_csrd_gaps:
        esg_score += min(28, len(report.regulatory_esg.esg_csrd_gaps) * 10)
        for claim in report.regulatory_esg.esg_csrd_gaps[:3]:
            esg_findings.append(claim.claim[:220])

    if any(k in corpus for k in ("csrd", "scope 3", "scope 1", "esrs", "double materiality", "emissions")):
        esg_score += 12
        csrd_flags.append(
            "EU CSRD / ESRS Reporting Gap: Target must integrate into acquirer's double-materiality & Scope 1–3 assurance perimeter."
        )
    if any(k in corpus for k in ("battery", "steel", "aluminum", "manufacturing", "supply chain", "cbam", "carbon", "gigafactory")):
        esg_score += 12
        csrd_flags.append(
            "EU CBAM (Carbon Border Adjustment Mechanism): Embedded carbon pricing exposure across imported steel, aluminum, and battery precursors."
        )
    if any(k in corpus for k in ("lksg", "supply chain", "cobalt", "lithium", "supplier", "human rights", "german", "eu")):
        esg_score += 10
        csrd_flags.append(
            "German LkSG / EU CSDDD Supply-Chain Due Diligence: Tier-1/Tier-N human-rights and environmental grievance audit mandatory prior to consolidation."
        )
    if not esg_findings:
        esg_findings.append(
            f"Validate {target}'s GHG emissions inventory (Scope 1–3) and EU Taxonomy alignment ahead of post-close consolidated CSRD reporting."
        )
    esg_score = min(95, esg_score)
    esg_readiness = max(5, 100 - esg_score)

    # ---- D. Concrete Disclosure Schedule Items ----
    disclosure_items: list[dict[str, Any]] = [
        {
            "schedule_ref": "Schedule 3.12(a) — Registered IP & Open-Source Software",
            "category": "IP & Open-Source",
            "title": "Complete OSS Bill of Materials (SBOM) & Copyleft License Register",
            "required_disclosure": (
                f"List all proprietary software, patents, and third-party/OSS components (flagging GPL, AGPL, LGPL, "
                f"and proprietary modifications) deployed in {target}'s production products."
            ),
            "materiality": _score_to_rating(ip_score),
        },
        {
            "schedule_ref": "Schedule 3.12(b) — IP Chain-of-Title & Inbound Licenses",
            "category": "IP & Open-Source",
            "title": "Employee/Contractor PIIAs & Material Inbound IP/Royalty Agreements",
            "required_disclosure": (
                "Confirm 100% executed invention-assignment agreements for all current/former R&D staff and disclose "
                "all inbound patent/collaboration licenses with CoC or royalty step-up clauses."
            ),
            "materiality": _score_to_rating(ip_score),
        },
        {
            "schedule_ref": "Schedule 3.15 — Data Privacy, Cybersecurity & Security Incidents",
            "category": "Cyber & Privacy",
            "title": "36-Month Breach Log, SOC 2 Type II Exceptions & GDPR/AI Data Lineage",
            "required_disclosure": (
                "Disclose any unauthorized data access, ransomware event, critical pen-test findings, "
                "data-protection authority inquiries, and cross-border data transfer mechanisms."
            ),
            "materiality": _score_to_rating(cyber_score),
        },
        {
            "schedule_ref": "Schedule 3.19 — Environmental, ESG & Supply-Chain Compliance",
            "category": "ESG / CSRD / LkSG",
            "title": "Scope 1–3 GHG Baseline, CBAM Goods & Tier-1 Supplier Audit Findings",
            "required_disclosure": (
                "Provide audited environmental permits, Scope 1–3 emissions methodology, CBAM-covered import volumes, "
                "and any LkSG / forced-labor / conflict-minerals remediation notices."
            ),
            "materiality": _score_to_rating(esg_score),
        },
    ]

    # ---- E. Concrete SPA Conditions Precedent (CPs) ----
    cps: list[dict[str, Any]] = [
        {
            "cp_id": "CP-IP-01",
            "category": "IP & Open-Source",
            "condition": (
                f"Completion of clean third-party Open-Source Software audit (Black Duck / FOSSA) with remediation "
                f"or escrow ring-fencing of any Critical GPL/AGPL copyleft contamination in {target}'s core code."
            ),
            "responsible_party": f"Seller / {target} CTO",
            "deadline_trigger": "Prior to Closing (or Signing for clean-room confirmation)",
        },
        {
            "cp_id": "CP-CYB-01",
            "category": "Cybersecurity & GDPR",
            "condition": (
                "Delivery of unmodified SOC 2 Type II / ISO 27001 report, zero unresolved Critical/High CVEs "
                "from independent pen-test, and validated GDPR/SCC data-transfer compliance."
            ),
            "responsible_party": f"Seller / {target} CISO",
            "deadline_trigger": "Prior to Closing",
        },
        {
            "cp_id": "CP-ESG-01",
            "category": "ESG / CSRD / LkSG",
            "condition": (
                f"Delivery of CSRD-ready Scope 1–3 emissions data pack and LkSG supplier risk self-assessment "
                f"enabling {acquirer}'s Day-1 consolidated sustainability reporting."
            ),
            "responsible_party": f"Seller / {target} Sustainability Lead",
            "deadline_trigger": "Prior to Closing (Day-1 Readiness)",
        },
    ]
    if ip_score >= 60:
        cps.append(
            {
                "cp_id": "CP-IP-02",
                "category": "IP Retention",
                "condition": (
                    f"Execution of confirmatory IP assignments and key-engineer retention agreements covering >=90% "
                    f"of {target}'s core technical architects."
                ),
                "responsible_party": "Buyer & Seller HR/Legal",
                "deadline_trigger": "Concurrent with Signing / Effective at Closing",
            }
        )

    overall = int(round(ip_score * 0.36 + cyber_score * 0.34 + esg_score * 0.30))
    summary = (
        f"Composite Tech-IP / Cyber / ESG Risk Index: {overall}/100 ({_score_to_rating(overall)}). "
        f"IP/OSS Risk {ip_score}/100 ({_score_to_rating(ip_score)}), "
        f"Cyber/GDPR Risk {cyber_score}/100 ({_score_to_rating(cyber_score)}), "
        f"ESG Readiness {esg_readiness}/100 (Risk {esg_score}/100). "
        f"Generated {len(disclosure_items)} Disclosure Schedules and {len(cps)} SPA Conditions Precedent."
    )

    return IPCyberESGDiligence(
        ip_risk_score=ip_score,
        ip_rating=_score_to_rating(ip_score),
        ip_findings=ip_findings,
        oss_copyleft_flags=oss_flags,
        cyber_risk_score=cyber_score,
        cyber_rating=_score_to_rating(cyber_score),
        cyber_findings=cyber_findings,
        esg_risk_score=esg_score,
        esg_readiness_score=esg_readiness,
        esg_rating=_score_to_rating(esg_score),
        esg_findings=esg_findings,
        csrd_cbam_lksg_flags=csrd_flags,
        overall_risk_score=overall,
        disclosure_schedule_items=disclosure_items,
        conditions_precedent=cps,
        summary=summary,
    )
