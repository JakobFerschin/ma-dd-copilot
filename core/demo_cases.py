"""
Flagship Institutional M&A Case Studies (Instant 1-Click Showcase).

Each case study is built to Harvard Business School / Goldman Sachs Investment
Committee standards: complete 5-section MECE intelligence, bottom-up Synergy
Workstreams feeding the DCF engine, Risk Register Matrix, 100-Day PMI Roadmap,
MECE Pillar Scores, Valuation Commentary, and Virtual Data Room (VDR) Q&A list.
"""
from __future__ import annotations

from datetime import datetime

from schemas.report_schema import (
    Claim,
    Confidence,
    CulturalRisk,
    DealMeta,
    DueDiligenceReport,
    ExecutiveSummary,
    IntegrationComplexity,
    IntegrationMilestone,
    OperationalTech,
    PillarScore,
    Recommendation,
    RegulatoryESG,
    RiskDimension,
    RiskItem,
    Severity,
    SourceCitation,
    StrategicFit,
    SynergyType,
    SynergyWorkstream,
)


def _c(doc: str, loc: str) -> SourceCitation:
    return SourceCitation(source_document=doc, location=loc)


def build_vw_rivian_case() -> DueDiligenceReport:
    """Case 1: Volkswagen AG × Rivian Automotive ($17.5B EV Software & Platform Acquisition)."""
    return DueDiligenceReport(
        deal=DealMeta(
            entity_a_name="Volkswagen AG",
            entity_b_name="Rivian Automotive, Inc.",
            acquirer_ticker="VOW3.DE",
            target_ticker="RIVN",
            industry_sector="Automotive & Software-Defined Vehicles (SDV)",
            deal_type="Strategic All-Cash Majority Acquisition",
            deal_value_usd_b=17.5,
            analysis_date=datetime.now().strftime("%Y-%m-%d"),
            time_horizon_months=36,
            overall_conviction_score=74,
            estimated_revenue_synergy_usd_b=1.45,
            estimated_cost_synergy_usd_b=1.18,
            target_synergy_npv_usd_b=7.84,
            estimated_integration_cost_usd_b=1.35,
            wacc_pct=9.2,
            tax_rate_pct=26.0,
            terminal_growth_pct=2.0,
            is_mock=True,
            documents_analyzed=[
                "VW_Annual_Report_2024.pdf",
                "VW_ESG_CSRD_Report_2024.pdf",
                "VW_Q4_Earnings_Call.txt",
                "Rivian_10K_FY2024.pdf",
                "Rivian_Q4_Earnings_Transcript.txt",
            ],
        ),
        executive_summary=ExecutiveSummary(
            strategic_rationale=(
                "Acquiring Rivian solves Volkswagen Group's multi-billion-euro CARIAD software bottleneck in a single stroke "
                "by securing a production-validated zonal E/E architecture and vertically integrated vehicle OS. "
                "Simultaneously, Rivian gains VW's global Tier-1 procurement scale, battery gigafactory capacity, and balance-sheet "
                "durability needed to cross into sustained gross-margin profitability on its R2/R3 platforms. "
                "At $17.5B EV, risk-adjusted run-rate synergies of ~$2.0B+ can generate ~$7.8B in after-tax Synergy NPV, "
                "justifying the control premium if and only if Rivian's Silicon Valley engineering org is ring-fenced."
            ),
            integration_complexity=IntegrationComplexity.HIGH,
            complexity_justification=(
                "Cross-border transatlantic integration combining a German Mitbestimmung-governed industrial incumbent "
                "(~680k employees, legacy multi-brand ECU sprawl) with an agile California-based software-first EV challenger "
                "poses severe governance, architectural migration, and engineering retention risks."
            ),
            top_deal_breaker_risks=[
                Claim(
                    claim="Engineering Attrition & Ring-Fencing Failure: Imposing Wolfsburg/CARIAD governance onto Rivian's ~2,800 core E/E and software engineers could trigger >25% attrition within 12 months, destroying the core IP thesis.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.24, Item 1A — Human Capital Risks"), _c("VW_Q4_Earnings_Call.txt", "p.8, CARIAD Restructuring Q&A")],
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=950.0,
                ),
                Claim(
                    claim="Amazon Commercial Delivery Van (EDV) Exclusivity & Change-of-Control: Rivian's 100,000-unit EDV contract contains equity/commercial covenants that may require Amazon waiver or renegotiation upon full control transfer.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.19, Item 1A — Customer Concentration"), _c("Rivian_10K_FY2024.pdf", "p.88, Note 14 — Related Party Transactions")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=620.0,
                ),
                Claim(
                    claim="CFIUS & Transatlantic Antitrust Scrutiny: Foreign control over US domestic EV manufacturing, charging infrastructure, and DOE/IRA-linked supply chains triggers mandatory CFIUS review and EU/DOJ Phase II scrutiny.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.31, Regulatory & IRA Compliance"), _c("VW_Annual_Report_2024.pdf", "p.112, Risk Report — Geopolitical")],
                    severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    quantified_impact_usd_m=480.0,
                ),
            ],
            recommendation=Recommendation.CONDITIONAL,
            recommendation_rationale=(
                "Proceed to binding offer strictly conditional on: (1) formal Amazon EDV consent and commercial continuity agreement, "
                "(2) binding 36-month $450M retention equity/cash pool for Rivian's top 400 E/E & OS architects under an autonomous "
                "US Software Subsidiary governance charter, and (3) pre-filing CFIUS mitigation framework."
            ),
        ),
        strategic_fit=StrategicFit(
            market_position_overlap=[
                Claim(
                    claim="Minimal cannibalization across North American and European segments: Rivian's core R1S/R1T ($70k–$105k) and upcoming R2 ($45k) target US adventure/SUV buyers where VW ID.4 and Scout have limited installed base.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.6, Item 1 — Vehicle Lineup"), _c("VW_Annual_Report_2024.pdf", "p.44, North America Brand Deliveries")],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                ),
                Claim(
                    claim="Direct portfolio overlap with VW's Scout Motors relaunch in South Carolina ($2B capex) requires immediate platform convergence onto Rivian's R2 zonal chassis to avoid $600M+ redundant tooling.",
                    sources=[_c("VW_Annual_Report_2024.pdf", "p.58, North America Strategy — Scout Motors"), _c("Rivian_Q4_Earnings_Transcript.txt", "p.5, R2 Architecture Briefing")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=410.0,
                ),
            ],
            cross_selling_potential=[
                Claim(
                    claim="European distribution unlock: Leveraging VW Group's 4,200+ European dealer/service points enables R2/R3 EU rollout 24 months ahead of Rivian's standalone capex-constrained timeline.",
                    sources=[_c("VW_Annual_Report_2024.pdf", "p.61, Distribution & After-Sales Network"), _c("Rivian_Q4_Earnings_Transcript.txt", "p.11, International Expansion Q&A")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    quantified_impact_usd_m=680.0,
                ),
                Claim(
                    claim="Recurring Software & Autonomy Subscription (ARPU): Deploying Rivian's Connect+ and ADAS stack across Audi, Porsche, and VW SSP vehicles creates high-margin (>65% GM) recurring software revenue.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.12, Software & Services Ecosystem"), _c("VW_Q4_Earnings_Call.txt", "p.6, SSP Platform Software Roadmap")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    quantified_impact_usd_m=520.0,
                ),
            ],
            revenue_synergies=[
                Claim(
                    claim="Accelerated SSP / Audi / Porsche EV launches: Avoiding an additional 18-month CARIAD software delay preserves an estimated $750M+ annual incremental EV contribution margin across VW Group premium brands.",
                    sources=[_c("VW_Q4_Earnings_Call.txt", "p.4, SSP Launch Cadence"), _c("Rivian_Q4_Earnings_Transcript.txt", "p.7, Zonal Platform Licensing")],
                    severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    quantified_impact_usd_m=750.0,
                ),
            ],
        ),
        operational_tech=OperationalTech(
            tech_stack_clash=[
                Claim(
                    claim="Zonal vs. Legacy Distributed ECU Architecture: Rivian reduced ECU count from 17 to 7 in Gen-2 with 1.6 miles less wiring harness; VW's MEB/PPE fleet still runs 40–70 distributed ECUs across heterogeneous Tier-1 middleware.",
                    sources=[_c("Rivian_Q4_Earnings_Transcript.txt", "p.3, Gen-2 Zonal E/E Cost Reduction"), _c("VW_Annual_Report_2024.pdf", "p.79, R&D & E/E Architecture")],
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=480.0,
                ),
                Claim(
                    claim="Cloud & Telemetry Divergence: Rivian's fleet telemetry and OTA pipeline runs natively on AWS; VW Group's Digital Production Platform and CARIAD cloud rely heavily on Microsoft Azure and SAP S/4HANA.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.15, Cloud & Connected Vehicle Ops"), _c("VW_Annual_Report_2024.pdf", "p.83, IT Infrastructure & Cloud")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                ),
            ],
            supply_chain_redundancies=[
                Claim(
                    claim="Battery Cell & Power Electronics Consolidation: Merging Rivian's ~15 GWh cell demand into VW PowerCo's 200+ GWh global cell contracts unlocks 11%–14% BOM savings on LFP/NMC packs and SiC inverters.",
                    sources=[_c("VW_Annual_Report_2024.pdf", "p.66, PowerCo Battery Strategy"), _c("Rivian_10K_FY2024.pdf", "p.49, Cost of Revenues — Battery BOM")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=540.0,
                ),
            ],
            cost_synergies=[
                Claim(
                    claim="CARIAD R&D De-Duplication: Retiring overlapping legacy OS/middleware tracks within CARIAD (FY24 operating loss ~$2.4B) yields $420M–$550M in annual R&D run-rate avoidance by Month 30.",
                    sources=[_c("VW_Annual_Report_2024.pdf", "p.134, Segment Reporting — CARIAD"), _c("VW_Q4_Earnings_Call.txt", "p.9, R&D Capitalization")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=490.0,
                ),
                Claim(
                    claim="Shared US Manufacturing Footprint: Co-producing R2 and Scout variants across Normal (IL) and Blythewood (SC) optimizes plant utilization and defers $350M+ in Georgia greenfield capex.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.41, Manufacturing & Georgia Capex Pause"), _c("VW_Annual_Report_2024.pdf", "p.59, US Capex Commitments")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    quantified_impact_usd_m=240.0,
                ),
            ],
        ),
        cultural_risk=CulturalRisk(
            leadership_style=[
                Claim(
                    claim="Governance Velocity Clash: Rivian operates with flat Silicon Valley sprint cycles and weekly OTA pushes, whereas VW requires Supervisory Board and Works Council (Betriebsrat) alignment for structural R&D shifts.",
                    sources=[_c("VW_Annual_Report_2024.pdf", "p.18, Corporate Governance & Co-Determination"), _c("Rivian_10K_FY2024.pdf", "p.14, Culture & Product Development")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                ),
            ],
            talent_retention_risk=[
                Claim(
                    claim="High Flight Risk Among Principal E/E Architects: Competing offers from Tesla, Apple alumni startups, and Waymo make Palo Alto/Irvine engineering retention the #1 execution determinant.",
                    sources=[_c("Rivian_10K_FY2024.pdf", "p.24, Item 1A — Key Personnel Dependence")],
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=450.0,
                ),
            ],
        ),
        regulatory_esg=RegulatoryESG(
            antitrust_flags=[
                Claim(
                    claim="HSR & EU Merger Control Clearance Achievable with Remedies: Combined US EV market share remains <14% (well below Tesla's ~48%), making structural divestitures unlikely, though CFIUS data-privacy conditions are probable.",
                    sources=[_c("VW_Annual_Report_2024.pdf", "p.45, Regional Market Shares"), _c("Rivian_10K_FY2024.pdf", "p.28, Competition & Regulatory")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                ),
            ],
            esg_csrd_gaps=[
                Claim(
                    claim="CSRD / Scope 3 Audit Readiness Gap: VW is subject to full EU CSRD double-materiality assurance starting FY25; Rivian's current disclosures lack audited Scope 3 Cat. 1 (purchased goods) upstream mineral traceability.",
                    sources=[_c("VW_ESG_CSRD_Report_2024.pdf", "p.14, CSRD Compliance Roadmap"), _c("Rivian_10K_FY2024.pdf", "p.33, Environmental & Supply Chain")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                ),
            ],
        ),
        data_gaps=[
            "Non-public Amazon EDV Master Supply Agreement change-of-control clauses and pricing schedules require VDR legal review.",
            "Line-by-line CARIAD vs. Rivian OS software license/IP encumbrances (third-party AUTOSAR / QNX contracts) are not disclosed in public filings.",
            "Granular employee stock option (RSU) vesting cliffs for Rivian's top 200 engineers are unavailable outside the VDR compensation folder.",
        ],
        synergy_workstreams=[
            SynergyWorkstream(
                name="Zonal E/E & SSP Platform Revenue Acceleration",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=750.0,
                one_time_cost_usd_m=320.0,
                probability_pct=70.0,
                ramp_year1_pct=10.0,
                ramp_year2_pct=45.0,
                ramp_year3_pct=100.0,
                owner="Head of Group Software & SSP",
                rationale="Eliminates 18-month software launch slip across Audi/VW SSP models.",
                sources=[_c("VW_Q4_Earnings_Call.txt", "p.4"), _c("Rivian_Q4_Earnings_Transcript.txt", "p.7")],
            ),
            SynergyWorkstream(
                name="R2 / R3 European Dealer Channel Rollout",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=460.0,
                one_time_cost_usd_m=180.0,
                probability_pct=75.0,
                ramp_year1_pct=15.0,
                ramp_year2_pct=55.0,
                ramp_year3_pct=100.0,
                owner="VP Europe Commercial",
                rationale="Deploys Rivian R2/R3 through VW Group's existing European showroom & service footprint.",
                sources=[_c("VW_Annual_Report_2024.pdf", "p.61")],
            ),
            SynergyWorkstream(
                name="Connected Software & ADAS Subscription ARPU",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=240.0,
                one_time_cost_usd_m=90.0,
                probability_pct=65.0,
                ramp_year1_pct=15.0,
                ramp_year2_pct=50.0,
                ramp_year3_pct=100.0,
                owner="Chief Digital Officer",
                rationale="Cross-sells Rivian Connect+ telemetry and ADAS tiers across VW Group EV fleet.",
                sources=[_c("Rivian_10K_FY2024.pdf", "p.12")],
            ),
            SynergyWorkstream(
                name="PowerCo Battery Cell & SiC Inverter Procurement",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=540.0,
                one_time_cost_usd_m=160.0,
                probability_pct=88.0,
                ramp_year1_pct=35.0,
                ramp_year2_pct=75.0,
                ramp_year3_pct=100.0,
                owner="Chief Procurement Officer / PowerCo",
                rationale="12% BOM reduction on Rivian R1/R2 packs via VW global volume tiers.",
                sources=[_c("VW_Annual_Report_2024.pdf", "p.66"), _c("Rivian_10K_FY2024.pdf", "p.49")],
            ),
            SynergyWorkstream(
                name="CARIAD Middleware & R&D De-Duplication",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=490.0,
                one_time_cost_usd_m=440.0,
                probability_pct=80.0,
                ramp_year1_pct=20.0,
                ramp_year2_pct=65.0,
                ramp_year3_pct=100.0,
                owner="Group CTO & IMO Lead",
                rationale="Sunsets redundant legacy middleware layers while ring-fencing Rivian OS core.",
                sources=[_c("VW_Annual_Report_2024.pdf", "p.134")],
            ),
            SynergyWorkstream(
                name="SG&A, Logistics & Indirect Spend Consolidation",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=150.0,
                one_time_cost_usd_m=160.0,
                probability_pct=90.0,
                ramp_year1_pct=40.0,
                ramp_year2_pct=85.0,
                ramp_year3_pct=100.0,
                owner="Group CFO",
                rationale="Consolidates North American freight, insurance, audit, and public-company overhead.",
                sources=[_c("Rivian_10K_FY2024.pdf", "p.51")],
            ),
        ],
        risk_register=[
            RiskItem(
                title="Silicon Valley E/E Engineering Attrition",
                dimension=RiskDimension.CULTURAL,
                severity=Severity.CRITICAL,
                likelihood=4,
                impact=5,
                is_deal_breaker=True,
                mitigation="Establish legally autonomous US Software Co. with $450M 36-month retention grants and separate comp bands.",
                sources=[_c("Rivian_10K_FY2024.pdf", "p.24")],
            ),
            RiskItem(
                title="Amazon EDV Contract Change-of-Control",
                dimension=RiskDimension.STRATEGIC,
                severity=Severity.HIGH,
                likelihood=3,
                impact=5,
                is_deal_breaker=True,
                mitigation="Secure signed Amazon consent letter and volume reaffirmation as a signing condition precedent.",
                sources=[_c("Rivian_10K_FY2024.pdf", "p.19")],
            ),
            RiskItem(
                title="CFIUS & Connected-Vehicle Data Restrictions",
                dimension=RiskDimension.REGULATORY_ESG,
                severity=Severity.HIGH,
                likelihood=3,
                impact=4,
                is_deal_breaker=True,
                mitigation="Pre-file voluntary CFIUS notice with US-domiciled telemetry data enclave (AWS US-Gov/Commercial).",
                sources=[_c("Rivian_10K_FY2024.pdf", "p.31")],
            ),
            RiskItem(
                title="Legacy ECU-to-Zonal Porting Delays on SSP",
                dimension=RiskDimension.OPERATIONAL_TECH,
                severity=Severity.HIGH,
                likelihood=4,
                impact=4,
                is_deal_breaker=False,
                mitigation="Freeze scope on Gen-2 zonal reference board; run joint 90-day hardware-in-the-loop (HIL) validation before SOP.",
                sources=[_c("VW_Annual_Report_2024.pdf", "p.79")],
            ),
            RiskItem(
                title="Scout Motors vs. Rivian R2 Brand Cannibalization",
                dimension=RiskDimension.STRATEGIC,
                severity=Severity.MEDIUM,
                likelihood=3,
                impact=3,
                is_deal_breaker=False,
                mitigation="Converge Scout and R2 onto shared skateboard/zonal platform while differentiating rugged vs. premium lifestyle positioning.",
                sources=[_c("VW_Annual_Report_2024.pdf", "p.58")],
            ),
            RiskItem(
                title="EU CSRD Scope 3 Assurance Non-Compliance",
                dimension=RiskDimension.REGULATORY_ESG,
                severity=Severity.MEDIUM,
                likelihood=4,
                impact=2,
                is_deal_breaker=False,
                mitigation="Onboard Rivian suppliers into VW's Catena-X battery passport and Scope 3 audit pipeline within 180 days.",
                sources=[_c("VW_ESG_CSRD_Report_2024.pdf", "p.14")],
            ),
        ],
        integration_roadmap=[
            IntegrationMilestone(
                phase="Pre-Close / Signing",
                workstream="Legal & Regulatory",
                objective="Execute Amazon EDV consent waiver, HSR/EU merger filings, and voluntary CFIUS mitigation draft.",
                owner="General Counsel & M&A Lead",
                kpi="100% regulatory clearance & signed Amazon consent prior to close",
            ),
            IntegrationMilestone(
                phase="Day 1",
                workstream="People & Culture",
                objective="Announce Autonomous US Software Subsidiary charter and issue retention grants to top 400 E/E & OS engineers.",
                owner="CHRO & Rivian CTO",
                kpi=">95% retention agreement sign-off within 14 days of closing",
            ),
            IntegrationMilestone(
                phase="First 100 Days",
                workstream="Procurement & Supply Chain",
                objective="Migrate Rivian battery cell, SiC inverter, and aluminum contracts onto VW PowerCo master pricing agreements.",
                owner="Chief Procurement Officer",
                kpi="$180M+ annualized BOM savings contracted by Day 100",
            ),
            IntegrationMilestone(
                phase="First 100 Days",
                workstream="Tech & Engineering",
                objective="Complete Zonal E/E reference architecture freeze for VW SSP and Scout convergence; sunset duplicate CARIAD layers.",
                owner="Joint CTO Council",
                kpi="Signed joint SOP architecture blueprint + HIL bench live",
            ),
            IntegrationMilestone(
                phase="Months 4–12",
                workstream="Commercial & Go-to-Market",
                objective="Certify R2 homologation for EU market and onboard top 150 VW European flagship hubs for Rivian sales/service.",
                owner="VP Europe Commercial",
                kpi="EU homologation filed; 150 service bays operational",
            ),
            IntegrationMilestone(
                phase="Months 13–36",
                workstream="Manufacturing & Financial",
                objective="Scale Normal/Blythewood shared platform output and capture full $1.18B cost & $1.45B revenue synergy run-rate.",
                owner="IMO Lead & Group CFO",
                kpi=">=85% of risk-adjusted synergy run-rate verified in P&L",
            ),
        ],
        pillar_scores=[
            PillarScore(pillar="Strategic Fit", score=86, summary="Solves VW's existential software bottleneck with minimal product overlap."),
            PillarScore(pillar="Operational & Tech", score=72, summary="High procurement upside offset by complex legacy-to-zonal migration."),
            PillarScore(pillar="Cultural Readiness", score=54, summary="Acute governance velocity clash; requires strict ring-fencing."),
            PillarScore(pillar="Regulatory & ESG", score=74, summary="Manageable antitrust profile; CFIUS & CSRD Scope 3 need structured plans."),
            PillarScore(pillar="Financial Value Creation", score=81, summary="Strong ~45% Synergy NPV / Deal Value ratio under base-case WACC."),
        ],
        valuation_commentary=(
            "At an illustrative $17.5B enterprise value (~3.1x FY26E revenue), the transaction requires approximately $5.2B "
            "in net synergy value creation to clear a 30% control premium threshold. Our bottom-up, probability-weighted DCF "
            "delivers ~$7.8B in after-tax Synergy NPV at 9.2% WACC (~45% of deal value), leaving a ~$2.6B margin of safety "
            "even under an 80% realization downside case—provided CARIAD restructuring charges stay capped below $1.5B."
        ),
        due_diligence_questions=[
            "Provide the full Amazon EDV Master Supply Agreement, including change-of-control clauses, exclusivity carve-outs, and minimum volume commitments.",
            "Break down Gen-2 R1 BOM cost reductions by subsystem (zonal controllers, harness, LFP/NMC pack, drive unit) vs. projected R2 BOM targets.",
            "Share the retention cliff schedule (unvested RSUs and cash bonuses) for the top 250 software, ADAS, and E/E hardware engineers.",
            "Detail all third-party IP licenses embedded in Rivian's vehicle OS and cloud stack (AWS commitments, Linux/QNX, NVIDIA/Mobileye ADAS agreements).",
            "Quantify total remaining capex required to bring Normal (IL) R2 expansion to 215k unit capacity vs. restarting the Georgia facility.",
            "Provide current Scope 1, 2, and 3 emissions inventories and upstream battery mineral chain-of-custody documentation for EU CSRD gap sizing.",
            "Clarify warranty reserve assumptions and historical field failure rates for Gen-1 vs. Gen-2 zonal hardware modules.",
        ],
    )


def build_msft_databricks_case() -> DueDiligenceReport:
    """Case 2: Microsoft Corp. × Databricks Inc. ($52.0B Enterprise AI & Data Lakehouse Consolidation)."""
    return DueDiligenceReport(
        deal=DealMeta(
            entity_a_name="Microsoft Corporation",
            entity_b_name="Databricks, Inc.",
            acquirer_ticker="MSFT",
            target_ticker="PRIVATE",
            industry_sector="Enterprise Cloud Infrastructure & AI Data Platforms",
            deal_type="Strategic Cash & Stock Acquisition",
            deal_value_usd_b=52.0,
            analysis_date=datetime.now().strftime("%Y-%m-%d"),
            time_horizon_months=36,
            overall_conviction_score=79,
            estimated_revenue_synergy_usd_b=3.10,
            estimated_cost_synergy_usd_b=1.05,
            target_synergy_npv_usd_b=15.90,
            estimated_integration_cost_usd_b=2.10,
            wacc_pct=8.5,
            tax_rate_pct=21.0,
            terminal_growth_pct=3.0,
            is_mock=True,
            documents_analyzed=[
                "Microsoft_10K_FY2024.pdf",
                "Microsoft_Q2_FY25_Transcript.txt",
                "Databricks_SeriesJ_Investor_Deck.pdf",
                "Databricks_SOC2_ESG_Summary.pdf",
            ],
        ),
        executive_summary=ExecutiveSummary(
            strategic_rationale=(
                "Acquiring Databricks cements Microsoft Azure as the undisputed enterprise AI & data control plane, unifying "
                "Databricks' industry-leading Lakehouse/Mosaic AI stack (~$3.0B+ ARR, >55% YoY growth) with Microsoft Fabric "
                "and Azure OpenAI workloads. The combined platform unlocks massive Azure cloud pull-through across Fortune 500 "
                "enterprises while neutralizing Snowflake and AWS Bedrock's primary data-gravity wedge. "
                "However, intense FTC/EU/CMA vertical-foreclosure scrutiny over AWS and GCP multi-cloud commitments makes "
                "binding multi-cloud neutrality remedies mandatory for closing."
            ),
            integration_complexity=IntegrationComplexity.CRITICAL,
            complexity_justification=(
                "Severe regulatory clearance path across US/EU/UK plus delicate product rationalization between Microsoft Fabric "
                "(OneLake) and Databricks Unity Catalog, alongside preserving Databricks' >45% AWS/GCP multi-cloud ARR base."
            ),
            top_deal_breaker_risks=[
                Claim(
                    claim="Antitrust Vertical Foreclosure Challenge (FTC / EC / CMA): Competitors (AWS, Google Cloud, Snowflake) will aggressively lobby regulators arguing Microsoft will degrade Databricks on AWS/GCP.",
                    sources=[_c("Microsoft_10K_FY2024.pdf", "p.27, Item 1A — Regulatory & Antitrust"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.14, Multi-Cloud ARR Mix")],
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=1800.0,
                ),
                Claim(
                    claim="Multi-Cloud Customer Churn: ~45% of Databricks ARR runs on AWS and GCP; perceived Azure lock-in could drive enterprises to Snowflake Iceberg or native AWS SageMaker.",
                    sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.14, Multi-Cloud ARR Mix")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=1100.0,
                ),
                Claim(
                    claim="Microsoft Fabric vs. Databricks Product Overlap: Conflicting sales incentives between Fabric capacity units and Databricks DBUs risk field-sales friction and roadmap paralysis.",
                    sources=[_c("Microsoft_Q2_FY25_Transcript.txt", "p.6, Intelligent Cloud & Fabric Growth"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.9, Unity Catalog & Delta Lake")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=640.0,
                ),
            ],
            recommendation=Recommendation.CONDITIONAL,
            recommendation_rationale=(
                "Proceed conditionally with a proactive 10-year legally binding Multi-Cloud Parity Commitment (mirroring the "
                "Activision/Call of Duty behavioral remedy playbook), an independent Databricks business unit inside Azure "
                "(LinkedIn/GitHub governance model), and unified Delta/Iceberg open-table interoperability with Microsoft Fabric."
            ),
        ),
        strategic_fit=StrategicFit(
            market_position_overlap=[
                Claim(
                    claim="Overlapping Data Engineering & BI Layer: Microsoft Fabric OneLake and Databricks Lakehouse compete directly for enterprise data engineering budgets, though Databricks dominates high-end ML/AI & Spark workloads.",
                    sources=[_c("Microsoft_Q2_FY25_Transcript.txt", "p.6"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.8")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                ),
            ],
            cross_selling_potential=[
                Claim(
                    claim="Microsoft Enterprise Agreement (EA) Distribution Machine: Bundling Databricks Mosaic AI and Unity Catalog into Microsoft's 35,000+ global enterprise accounts accelerates net-new enterprise penetration by 2.5x.",
                    sources=[_c("Microsoft_10K_FY2024.pdf", "p.11, Enterprise Commercial Channel"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.16, Fortune 500 Penetration")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=1450.0,
                ),
            ],
            revenue_synergies=[
                Claim(
                    claim="Azure Compute & AI Pull-Through: Every $1 of Azure Databricks DBU consumption pulls ~$1.80 of underlying Azure GPU/storage/networking spend, driving $1.65B+ incremental Azure IaaS revenue by Year 3.",
                    sources=[_c("Microsoft_Q2_FY25_Transcript.txt", "p.5, Azure AI Consumption"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.12, Net Dollar Retention 140%+")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=1650.0,
                ),
            ],
        ),
        operational_tech=OperationalTech(
            tech_stack_clash=[
                Claim(
                    claim="Metadata & Governance Convergence: Unifying Databricks Unity Catalog with Microsoft Purview and Fabric OneLake requires clean Apache Iceberg / Delta UniForm bridging without breaking existing enterprise pipelines.",
                    sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.10, Delta UniForm"), _c("Microsoft_10K_FY2024.pdf", "p.9, Intelligent Cloud")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                ),
            ],
            supply_chain_redundancies=[
                Claim(
                    claim="GPU Cluster & Datacenter Capacity Sharing: Moving Databricks' internal Mosaic AI foundation-model training clusters onto Microsoft's owned Azure AI supercomputing infrastructure reduces third-party GPU hosting costs by ~35%.",
                    sources=[_c("Microsoft_10K_FY2024.pdf", "p.42, AI Datacenter Capex"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.19, Gross Margin Bridge")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=420.0,
                ),
            ],
            cost_synergies=[
                Claim(
                    claim="Go-to-Market, Cloud Hosting & G&A Leverage: Eliminating duplicate cloud resale margins on Azure Databricks, consolidating global G&A, and unifying enterprise security/compliance yields $1.05B in run-rate savings.",
                    sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.20, OpEx Breakdown"), _c("Microsoft_10K_FY2024.pdf", "p.45, Operating Margins")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=630.0,
                ),
            ],
        ),
        cultural_risk=CulturalRisk(
            leadership_style=[
                Claim(
                    claim="Open-Source Founder DNA vs. Hyperscaler Governance: Databricks' UC Berkeley / Apache Spark open-source ethos thrives on rapid engineering autonomy; GitHub-style subsidiary independence is proven within Microsoft.",
                    sources=[_c("Microsoft_10K_FY2024.pdf", "p.14, GitHub & LinkedIn Governance"), _c("Databricks_SeriesJ_Investor_Deck.pdf", "p.4, Leadership & OSS")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                ),
            ],
            talent_retention_risk=[
                Claim(
                    claim="AI Research & Systems Engineering Competition: Top Mosaic AI and Spark distributed-systems researchers face fierce poaching from OpenAI, Anthropic, and Meta; multi-year MSFT equity packages are essential.",
                    sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.22, Engineering Headcount")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                ),
            ],
        ),
        regulatory_esg=RegulatoryESG(
            antitrust_flags=[
                Claim(
                    claim="Extended 14–18 Month Global Merger Clearance: Mandatory HSR Second Request, EC Phase II, and UK CMA review expected; reverse break-fee exposure ($2.5B–$3.0B) must be factored into deal structuring.",
                    sources=[_c("Microsoft_10K_FY2024.pdf", "p.27, Antitrust Proceedings")],
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                ),
            ],
            esg_csrd_gaps=[
                Claim(
                    claim="AI Datacenter Carbon Footprint Accounting: Integrating Databricks' GPU training workloads requires alignment with Microsoft's 2030 Carbon Negative Scope 2/3 energy-matching standards.",
                    sources=[_c("Databricks_SOC2_ESG_Summary.pdf", "p.6, Emissions & Cloud Energy"), _c("Microsoft_10K_FY2024.pdf", "p.16, Sustainability Commitments")],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                ),
            ],
        ),
        data_gaps=[
            "Audited standalone GAAP financials and exact AWS vs. Azure vs. GCP gross margin split for Databricks are private and require VDR financial folder access.",
            "Exact terms of the existing Azure Databricks first-party revenue-share contract and termination provisions are non-public.",
        ],
        synergy_workstreams=[
            SynergyWorkstream(
                name="Azure Compute & AI Workload Pull-Through",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=1650.0,
                one_time_cost_usd_m=450.0,
                probability_pct=80.0,
                ramp_year1_pct=25.0,
                ramp_year2_pct=65.0,
                ramp_year3_pct=100.0,
                owner="EVP Cloud + AI",
                rationale="Native Azure Databricks + Azure OpenAI bundles accelerate enterprise workload migration.",
                sources=[_c("Microsoft_Q2_FY25_Transcript.txt", "p.5")],
            ),
            SynergyWorkstream(
                name="Microsoft EA Global Cross-Sell & Copilot Integration",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=1450.0,
                one_time_cost_usd_m=520.0,
                probability_pct=75.0,
                ramp_year1_pct=20.0,
                ramp_year2_pct=60.0,
                ramp_year3_pct=100.0,
                owner="Chief Commercial Officer",
                rationale="Expands Databricks penetration across Microsoft's 35k+ enterprise EA install base.",
                sources=[_c("Microsoft_10K_FY2024.pdf", "p.11")],
            ),
            SynergyWorkstream(
                name="Internal AI Infrastructure & GPU Hosting Savings",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=420.0,
                one_time_cost_usd_m=280.0,
                probability_pct=90.0,
                ramp_year1_pct=40.0,
                ramp_year2_pct=85.0,
                ramp_year3_pct=100.0,
                owner="Azure Infrastructure VP",
                rationale="Consolidates Mosaic AI training and internal dev clusters onto owned Azure capacity.",
                sources=[_c("Microsoft_10K_FY2024.pdf", "p.42")],
            ),
            SynergyWorkstream(
                name="Sales Ops, Channel Margin & G&A Consolidation",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=630.0,
                one_time_cost_usd_m=850.0,
                probability_pct=85.0,
                ramp_year1_pct=30.0,
                ramp_year2_pct=75.0,
                ramp_year3_pct=100.0,
                owner="Corporate Finance & IMO",
                rationale="Eliminates duplicate enterprise selling overhead and public-readiness G&A.",
                sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.20")],
            ),
        ],
        risk_register=[
            RiskItem(
                title="FTC / EC / CMA Vertical Foreclosure Block",
                dimension=RiskDimension.REGULATORY_ESG,
                severity=Severity.CRITICAL,
                likelihood=4,
                impact=5,
                is_deal_breaker=True,
                mitigation="Offer binding 10-year AWS/GCP feature & pricing parity commitments upfront.",
                sources=[_c("Microsoft_10K_FY2024.pdf", "p.27")],
            ),
            RiskItem(
                title="AWS / GCP Multi-Cloud Customer Defection",
                dimension=RiskDimension.STRATEGIC,
                severity=Severity.HIGH,
                likelihood=3,
                impact=4,
                is_deal_breaker=True,
                mitigation="Preserve standalone Databricks brand, open-source governance, and AWS/GCP engineering teams.",
                sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.14")],
            ),
            RiskItem(
                title="Fabric vs. Databricks Field-Sales Cannibalization",
                dimension=RiskDimension.OPERATIONAL_TECH,
                severity=Severity.HIGH,
                likelihood=4,
                impact=3,
                is_deal_breaker=False,
                mitigation="Harmonize quota retirement across Fabric and Databricks SKUs from Day 1.",
                sources=[_c("Microsoft_Q2_FY25_Transcript.txt", "p.6")],
            ),
            RiskItem(
                title="Mosaic AI Research Talent Attrition",
                dimension=RiskDimension.CULTURAL,
                severity=Severity.HIGH,
                likelihood=3,
                impact=4,
                is_deal_breaker=False,
                mitigation="Deploy 4-year MSFT equity retention pool + GitHub-style autonomous engineering charter.",
                sources=[_c("Databricks_SeriesJ_Investor_Deck.pdf", "p.22")],
            ),
        ],
        integration_roadmap=[
            IntegrationMilestone(
                phase="Pre-Close / Signing",
                workstream="Regulatory",
                objective="Publish binding 10-year Multi-Cloud Parity Pledge for AWS and GCP customers.",
                owner="President & Chief Legal Officer",
                kpi="Regulatory clearance achieved within 16 months",
            ),
            IntegrationMilestone(
                phase="Day 1",
                workstream="Commercial",
                objective="Align Microsoft EA field-sales compensation so Fabric and Databricks carry equal quota credit.",
                owner="Chief Commercial Officer",
                kpi="Zero field-sales channel conflict; unified enterprise SKU sheet live",
            ),
            IntegrationMilestone(
                phase="First 100 Days",
                workstream="Tech & Engineering",
                objective="Ship bidirectional Unity Catalog ↔ Microsoft Purview & OneLake Delta UniForm bridge.",
                owner="Databricks CTO & Fabric VP",
                kpi="GA release of unified metadata governance bridge",
            ),
            IntegrationMilestone(
                phase="Months 4–12",
                workstream="Infrastructure",
                objective="Transition internal Mosaic AI foundation-model training clusters to owned Azure AI datacenters.",
                owner="Azure Infrastructure VP",
                kpi=">=30% reduction in internal GPU compute unit cost",
            ),
        ],
        pillar_scores=[
            PillarScore(pillar="Strategic Fit", score=94, summary="Category-defining enterprise AI data gravity moat."),
            PillarScore(pillar="Operational & Tech", score=79, summary="Strong existing Azure Databricks foundation; Fabric overlap manageable."),
            PillarScore(pillar="Cultural Readiness", score=76, summary="Proven GitHub/LinkedIn autonomous subsidiary playbook reduces friction."),
            PillarScore(pillar="Regulatory & ESG", score=48, summary="Primary execution bottleneck: global antitrust scrutiny requires upfront remedies."),
            PillarScore(pillar="Financial Value Creation", score=86, summary="~$15.9B Synergy NPV (~31% of EV) plus massive strategic option value."),
        ],
        valuation_commentary=(
            "At $52.0B EV (~17x NTM ARR), valuation is rich on standalone multiples but highly compelling on an all-in "
            "platform basis: probability-weighted Synergy NPV of ~$15.9B reduces the effective acquisition multiple to "
            "~12x NTM ARR for a >55% YoY growth asset with >140% net dollar retention. Accretive to non-GAAP EPS by Year 3."
        ),
        due_diligence_questions=[
            "Provide audited standalone ARR, gross margin, and net dollar retention segmented by cloud provider (Azure vs. AWS vs. GCP).",
            "Detail customer concentration and contract renewal terms for top 50 AWS-native enterprise accounts.",
            "Quantify Mosaic AI standalone ARR, inference gross margins, and committed third-party GPU lease obligations.",
            "Map exact architectural overlaps between Unity Catalog and Microsoft Purview / Fabric OneLake.",
            "Assess reverse termination fee sizing and outside-date mechanics for an 18-month FTC/EC/CMA review cycle.",
        ],
    )


def build_lilly_vertex_case() -> DueDiligenceReport:
    """Case 3: Eli Lilly × Vertex Pharmaceuticals ($44.0B Biopharma Pipeline & Gene-Editing Expansion)."""
    return DueDiligenceReport(
        deal=DealMeta(
            entity_a_name="Eli Lilly and Company",
            entity_b_name="Vertex Pharmaceuticals Inc.",
            acquirer_ticker="LLY",
            target_ticker="VRTX",
            industry_sector="Biopharmaceuticals & Precision Therapeutics",
            deal_type="Transformational All-Cash Strategic Acquisition",
            deal_value_usd_b=44.0,
            analysis_date=datetime.now().strftime("%Y-%m-%d"),
            time_horizon_months=36,
            overall_conviction_score=85,
            estimated_revenue_synergy_usd_b=2.15,
            estimated_cost_synergy_usd_b=1.35,
            target_synergy_npv_usd_b=14.60,
            estimated_integration_cost_usd_b=1.40,
            wacc_pct=8.2,
            tax_rate_pct=19.0,
            terminal_growth_pct=2.5,
            is_mock=True,
            documents_analyzed=[
                "EliLilly_10K_FY2024.pdf",
                "EliLilly_ESG_Report_2024.pdf",
                "Vertex_10K_FY2024.pdf",
                "Vertex_Q4_Earnings_Call.txt",
            ],
        ),
        executive_summary=ExecutiveSummary(
            strategic_rationale=(
                "Combining Eli Lilly's unmatched global cardiometabolic commercial engine with Vertex's durable Cystic Fibrosis "
                "cash-cow franchise (~$10.5B revenue, >45% operating margin) and blockbuster non-opioid pain launch (suzetrigine / "
                "Journavx) creates a diversified, patent-protected biopharma powerhouse through the late 2030s. "
                "Eli Lilly's global primary-care and hospital salesforce immediately multiplies commercial reach for Vertex's "
                "non-opioid acute/chronic pain franchise while funding Vertex's CRISPR/cell-therapy diabetes pipeline (VX-880) "
                "with zero therapeutic overlap."
            ),
            integration_complexity=IntegrationComplexity.MEDIUM,
            complexity_justification=(
                "High therapeutic complementarity and zero commercial antitrust overlap keep integration execution clean; "
                "primary complexity lies in preserving Vertex's Boston R&D discovery autonomy and scaling gene-editing manufacturing."
            ),
            top_deal_breaker_risks=[
                Claim(
                    claim="Non-Opioid Pain Commercial Payer Formulary Uptake: Achieving peak $4B+ sales on suzetrigine requires aggressive PBM contracting against $5 generic opioids.",
                    sources=[_c("Vertex_Q4_Earnings_Call.txt", "p.4, Pain Franchise Launch & Payer Access")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=850.0,
                ),
                Claim(
                    claim="Boston R&D Discovery Attrition: Centralizing Vertex's serial-innovation Boston labs into Indianapolis corporate R&D hierarchies risks losing key medicinal chemistry and gene-editing PIs.",
                    sources=[_c("Vertex_10K_FY2024.pdf", "p.29, Item 1A — R&D Talent"), _c("EliLilly_10K_FY2024.pdf", "p.18, Global R&D Structure")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=520.0,
                ),
            ],
            recommendation=Recommendation.GO,
            recommendation_rationale=(
                "Strong Go. Immediate EPS accretion from Vertex's high-margin CF cash flows, clean FTC/EC antitrust clearance "
                "(zero overlapping commercial indications), and ~$14.6B in Synergy NPV (~33% of EV) driven by Lilly's "
                "unmatched global primary-care pain distribution."
            ),
        ),
        strategic_fit=StrategicFit(
            market_position_overlap=[
                Claim(
                    claim="Zero Commercial Overlap, Perfect Portfolio Hedge: Vertex's rare-disease CF monopoly and non-opioid pain assets diversify Lilly beyond GLP-1 concentration (Mounjaro/Zepbound >45% of FY24 sales).",
                    sources=[_c("EliLilly_10K_FY2024.pdf", "p.52, Product Revenue Breakdown"), _c("Vertex_10K_FY2024.pdf", "p.8, CF & Pipeline Overview")],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                ),
            ],
            cross_selling_potential=[
                Claim(
                    claim="Global Pain Franchise Commercial Acceleration: Plugging Vertex's non-opioid pain drug into Lilly's 12,000+ global commercial reps unlocks EU/Japan/US primary-care volume 3 years ahead of Vertex standalone.",
                    sources=[_c("EliLilly_10K_FY2024.pdf", "p.14, Global Commercial Infrastructure"), _c("Vertex_Q4_Earnings_Call.txt", "p.6, Pain Commercial Footprint")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=1550.0,
                ),
            ],
            revenue_synergies=[
                Claim(
                    claim="Type-1 Diabetes Curative Synergy: Pairing Vertex's VX-880 islet cell therapy with Lilly's global endocrinology & diabetes KOL network accelerates clinical enrollment and specialty center adoption.",
                    sources=[_c("Vertex_10K_FY2024.pdf", "p.15, T1D Cell Therapy Program"), _c("EliLilly_10K_FY2024.pdf", "p.10, Diabetes Leadership")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    quantified_impact_usd_m=600.0,
                ),
            ],
        ),
        operational_tech=OperationalTech(
            tech_stack_clash=[
                Claim(
                    claim="Low Enterprise IT Friction: Both organizations operate standardized Veeva Vault RIM/CTMS clinical platforms and SAP ERP cores, enabling rapid Phase-1 system harmonization.",
                    sources=[_c("EliLilly_10K_FY2024.pdf", "p.22, Digital & Clinical Systems"), _c("Vertex_10K_FY2024.pdf", "p.34, Operations")],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                ),
            ],
            supply_chain_redundancies=[
                Claim(
                    claim="Biologics & Fill-Finish Manufacturing Scale: Utilizing Lilly's $18B+ newly built sterile manufacturing network de-risks Vertex's commercial scaling for Casgevy and next-gen cell therapies.",
                    sources=[_c("EliLilly_10K_FY2024.pdf", "p.48, Manufacturing Capex"), _c("Vertex_10K_FY2024.pdf", "p.25, Manufacturing Risks")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=420.0,
                ),
            ],
            cost_synergies=[
                Claim(
                    claim="SG&A, Medical Affairs & Duplicate Commercial Build Avoidance: Avoiding Vertex's planned $600M/yr standalone global pain salesforce build plus corporate G&A consolidation yields $1.35B annual run-rate savings.",
                    sources=[_c("Vertex_10K_FY2024.pdf", "p.64, SG&A Operating Expenses"), _c("EliLilly_10K_FY2024.pdf", "p.58, Operating Leverage")],
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    quantified_impact_usd_m=930.0,
                ),
            ],
        ),
        cultural_risk=CulturalRisk(
            leadership_style=[
                Claim(
                    claim="High Scientific Culture Alignment: Both companies are R&D-led, science-first organizations with industry-leading R&D-to-sales investment ratios (~25%+).",
                    sources=[_c("EliLilly_10K_FY2024.pdf", "p.55, R&D Expense"), _c("Vertex_10K_FY2024.pdf", "p.63, R&D Expense")],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                ),
            ],
            talent_retention_risk=[
                Claim(
                    claim="Boston Innovation Hub Preservation: Retaining Vertex's Seaport Boston discovery campus as Lilly's autonomous Rare Disease & Genetic Therapies Center of Excellence minimizes PI turnover.",
                    sources=[_c("Vertex_10K_FY2024.pdf", "p.29, Human Capital")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                ),
            ],
        ),
        regulatory_esg=RegulatoryESG(
            antitrust_flags=[
                Claim(
                    claim="Clean Antitrust Profile: Minimal commercial overlap across CF, acute pain, and GLP-1/oncology; only early-stage Type-1 diabetes pipeline review expected under HSR.",
                    sources=[_c("EliLilly_10K_FY2024.pdf", "p.28, Competition"), _c("Vertex_10K_FY2024.pdf", "p.21, Pipeline")],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                ),
            ],
            esg_csrd_gaps=[
                Claim(
                    claim="Drug Pricing & Access Scrutiny: High list price of Casgevy ($2.2M) and Trikafta ($300k+/yr) requires alignment with Lilly's value-based outcomes contracting and patient-access ESG framework.",
                    sources=[_c("EliLilly_ESG_Report_2024.pdf", "p.19, Patient Access & Pricing"), _c("Vertex_10K_FY2024.pdf", "p.31, Pricing & Reimbursement")],
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                ),
            ],
        ),
        data_gaps=[
            "Confidential PBM rebate tiers and hospital formulary contracts for suzetrigine launch require VDR commercial access review.",
            "Royalty and milestone sharing agreements with CRISPR Therapeutics on Casgevy change-of-control mechanics need legal verification.",
        ],
        synergy_workstreams=[
            SynergyWorkstream(
                name="Global Non-Opioid Pain Commercial Expansion",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=1550.0,
                one_time_cost_usd_m=380.0,
                probability_pct=82.0,
                ramp_year1_pct=25.0,
                ramp_year2_pct=65.0,
                ramp_year3_pct=100.0,
                owner="EVP Global Commercial",
                rationale="Leverages Lilly's 12,000+ global primary-care reps to accelerate Journavx adoption.",
                sources=[_c("EliLilly_10K_FY2024.pdf", "p.14")],
            ),
            SynergyWorkstream(
                name="T1D Cell Therapy & Endocrinology Acceleration",
                synergy_type=SynergyType.REVENUE,
                annual_run_rate_usd_m=600.0,
                one_time_cost_usd_m=220.0,
                probability_pct=65.0,
                ramp_year1_pct=10.0,
                ramp_year2_pct=40.0,
                ramp_year3_pct=100.0,
                owner="Head of Diabetes & Endocrinology",
                rationale="Accelerates VX-880 specialist adoption via Lilly's global diabetes KOL network.",
                sources=[_c("Vertex_10K_FY2024.pdf", "p.15")],
            ),
            SynergyWorkstream(
                name="Commercial Infrastructure & SG&A Avoidance",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=930.0,
                one_time_cost_usd_m=520.0,
                probability_pct=90.0,
                ramp_year1_pct=45.0,
                ramp_year2_pct=85.0,
                ramp_year3_pct=100.0,
                owner="Group CFO & Commercial Ops",
                rationale="Avoids Vertex standalone global pain salesforce build + consolidates G&A.",
                sources=[_c("Vertex_10K_FY2024.pdf", "p.64")],
            ),
            SynergyWorkstream(
                name="Sterile Manufacturing & Clinical Trial Procurement",
                synergy_type=SynergyType.COST,
                annual_run_rate_usd_m=420.0,
                one_time_cost_usd_m=280.0,
                probability_pct=85.0,
                ramp_year1_pct=30.0,
                ramp_year2_pct=75.0,
                ramp_year3_pct=100.0,
                owner="EVP Manufacturing & CRO Sourcing",
                rationale="Consolidates CRO spend and shifts commercial fill-finish onto Lilly facilities.",
                sources=[_c("EliLilly_10K_FY2024.pdf", "p.48")],
            ),
        ],
        risk_register=[
            RiskItem(
                title="Non-Opioid Pain PBM Formulary Resistance",
                dimension=RiskDimension.STRATEGIC,
                severity=Severity.HIGH,
                likelihood=3,
                impact=4,
                is_deal_breaker=False,
                mitigation="Leverage Lilly's multi-product PBM contracting power (GLP-1 portfolio) to secure Tier-2 preferred access.",
                sources=[_c("Vertex_Q4_Earnings_Call.txt", "p.4")],
            ),
            RiskItem(
                title="Boston Discovery R&D Principal Investigator Attrition",
                dimension=RiskDimension.CULTURAL,
                severity=Severity.HIGH,
                likelihood=3,
                impact=4,
                is_deal_breaker=False,
                mitigation="Designate Boston Seaport as Lilly's autonomous Rare Disease & Genetic Therapies Hub.",
                sources=[_c("Vertex_10K_FY2024.pdf", "p.29")],
            ),
            RiskItem(
                title="CRISPR Therapeutics Co-Development Governance",
                dimension=RiskDimension.REGULATORY_ESG,
                severity=Severity.MEDIUM,
                likelihood=2,
                impact=3,
                is_deal_breaker=False,
                mitigation="Complete pre-signing legal audit of Casgevy collaboration agreement.",
                sources=[_c("Vertex_10K_FY2024.pdf", "p.21")],
            ),
            RiskItem(
                title="Type-1 Diabetes Early Pipeline Antitrust Inquiry",
                dimension=RiskDimension.REGULATORY_ESG,
                severity=Severity.LOW,
                likelihood=2,
                impact=3,
                is_deal_breaker=False,
                mitigation="Demonstrate distinct curative mechanism (islet cell replacement vs. exogenous insulin).",
                sources=[_c("EliLilly_10K_FY2024.pdf", "p.28")],
            ),
        ],
        integration_roadmap=[
            IntegrationMilestone(
                phase="Day 1",
                workstream="Commercial & People",
                objective="Confirm Boston R&D autonomy charter and mobilize Lilly hospital/pain salesforce training.",
                owner="CEO & Chief Scientific Officer",
                kpi=">96% R&D leadership retention; 4,000 reps certified on pain franchise",
            ),
            IntegrationMilestone(
                phase="First 100 Days",
                workstream="Market Access",
                objective="Integrate suzetrigine into Lilly's national PBM and hospital IDN contracting bundles.",
                owner="VP Market Access",
                kpi=">70% commercial covered lives with unrestricted Tier-2 access",
            ),
            IntegrationMilestone(
                phase="Months 4–12",
                workstream="Operations & Finance",
                objective="Consolidate CRO procurement, corporate G&A, and European regulatory/medical affairs hubs.",
                owner="CFO & IMO Lead",
                kpi="$750M+ cost synergies locked in run-rate P&L by Month 12",
            ),
        ],
        pillar_scores=[
            PillarScore(pillar="Strategic Fit", score=92, summary="De-risks GLP-1 concentration with high-margin CF and blockbuster pain assets."),
            PillarScore(pillar="Operational & Tech", score=88, summary="Compatible Veeva/SAP stack and high commercial leverage."),
            PillarScore(pillar="Cultural Readiness", score=84, summary="Shared science-first R&D ethos with clear Boston hub ring-fencing."),
            PillarScore(pillar="Regulatory & ESG", score=86, summary="Clean antitrust profile; manageable drug-pricing access governance."),
            PillarScore(pillar="Financial Value Creation", score=89, summary="Immediately EPS-accretive with ~$14.6B Synergy NPV."),
        ],
        valuation_commentary=(
            "At $44.0B EV, Vertex's $10.5B high-margin CF annuity underpins ~75% of the purchase price before attributing "
            "value to suzetrigine or VX-880. Risk-adjusted Synergy NPV of ~$14.6B (33% of deal value) makes this one of "
            "the highest-conviction, lowest-antitrust-risk strategic acquisitions available in large-cap biopharma."
        ),
        due_diligence_questions=[
            "Share current US hospital formulary win rates and gross-to-net pricing assumptions for suzetrigine.",
            "Confirm patent cliff protection and next-gen triple-combination (vanza triple) IP expiry dates across US and EU.",
            "Review CRISPR Therapeutics collaboration agreement change-of-control and royalty split schedules.",
            "Provide Phase 1/2/3 clinical durability and immunosuppression safety data for VX-880 and VX-264.",
            "Detail commercial manufacturing capacity bottlenecks for Casgevy viral vector and cell-processing suites.",
        ],
    )


def _with_synced_dcf(report: DueDiligenceReport) -> DueDiligenceReport:
    """Sync DealMeta.target_synergy_npv_usd_b with the deterministic DCF engine."""
    from core.financial_model import compute_synergy_dcf

    dcf = compute_synergy_dcf(report)
    report.deal.target_synergy_npv_usd_b = dcf.total_synergy_npv_b
    return report


DEMO_CASES = {
    "vw_rivian": {
        "label": "Volkswagen AG × Rivian Automotive",
        "subtitle": "$17.5B EV Zonal Software & Platform Acquisition · Automotive / SDV",
        "recommendation": "Conditional Go",
        "builder": lambda: _with_synced_dcf(build_vw_rivian_case()),
    },
    "msft_databricks": {
        "label": "Microsoft Corp. × Databricks Inc.",
        "subtitle": "$52.0B Enterprise AI & Data Lakehouse Consolidation · Cloud / AI",
        "recommendation": "Conditional Go",
        "builder": lambda: _with_synced_dcf(build_msft_databricks_case()),
    },
    "lilly_vertex": {
        "label": "Eli Lilly × Vertex Pharmaceuticals",
        "subtitle": "$44.0B Biopharma Pipeline & Non-Opioid Pain Expansion · Healthcare",
        "recommendation": "Go",
        "builder": lambda: _with_synced_dcf(build_lilly_vertex_case()),
    },
}
