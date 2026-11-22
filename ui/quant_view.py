"""
Institutional Quantitative Valuation Suite UI (`ui/quant_view.py`).

Unifies 6 Wall Street / Private Equity deterministic financial engines under clean
sub-tabs:
  1. Synergy DCF & Sensitivity (Live Stress-Testing + Bridge + Phasing + 2D Heatmap)
  2. Monte Carlo & Football Field (10,000-Run Correlated Simulation + Implied EV Ranges)
  3. 3-Statement Merger Model (Accretion / Dilution, PPA, Goodwill, Breakeven Synergies)
  4. Private Equity LBO & Tax Step-Up (Sources & Uses, Debt Sweep, IRR/MOIC, 338(h)(10))
  5. Quality of Earnings (QoE) & NWC Peg (EBITDA Bridge, Scrutiny Score, Debt-Like Items)
  6. Customer Cohorts & Unit Economics (NDR, GRR, HHI, Top-20 Concentration Pareto)
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.financial_model import (
    ValuationAssumptions,
    compute_sensitivity_matrix,
    compute_synergy_dcf,
)
from quant import (
    LBOInputs,
    MergerInputs,
    MonteCarloConfig,
    analyze_customer_cohorts,
    compute_football_field,
    compute_lbo_model,
    compute_merger_model,
    compute_nwc_peg,
    compute_qoe_bridge,
    compute_tax_step_up,
    default_lbo_inputs_for_report,
    default_merger_inputs_for_report,
    default_qoe_for_report,
    run_monte_carlo_synergies,
)
from schemas.report_schema import DueDiligenceReport
from ui.charts import (
    accretion_dilution_chart,
    cashflow_phasing_chart,
    football_field_chart,
    lbo_debt_paydown_chart,
    monte_carlo_histogram_chart,
    qoe_bridge_chart,
    sensitivity_heatmap_chart,
    synergy_bridge_chart,
    workstream_breakdown_chart,
)
from ui.styles import kpi_strip_html, section_header_html
from utils.export_engine import report_to_excel_bytes


def _fmt_b(val: float | None) -> str:
    return "—" if val is None else f"${val:.2f}B"


def _safe_slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in (name or "Entity"))[:20]


def render_quant_valuation_suite(report: DueDiligenceReport) -> None:
    d = report.deal
    st.markdown(
        f'<h2>Institutional Quant &amp; Valuation Suite — {d.entity_a_name} <span style="color:#B8935A;">×</span> {d.entity_b_name}</h2>',
        unsafe_allow_html=True,
    )
    st.caption(
        "100% deterministic financial engineering (zero LLM arithmetic). Stress-test Synergy DCF, "
        "10,000-run Monte Carlo distributions, 3-Statement Accretion/Dilution, PE LBO/IRR, QoE EBITDA Bridges, and Football Field ranges live."
    )

    tabs = st.tabs([
        "Synergy DCF & Sensitivity",
        "Monte Carlo & Football Field",
        "3-Statement Merger Model",
        "PE LBO & Tax Step-Up",
        "QoE & NWC Peg",
        "Customer Cohorts",
    ])

    # =====================================================================
    # SUB-TAB 1: Synergy DCF & Sensitivity
    # =====================================================================
    with tabs[0]:
        with st.container(border=True):
            st.markdown(section_header_html("Valuation Assumptions & Stress-Test Controls", "LIVE DCF PARAMETERS"), unsafe_allow_html=True)

            p1, p2, p3, p4 = st.columns([1, 1, 1, 1.8])
            if p1.button("Bear / Downside Case", width="stretch"):
                st.session_state["dcf_wacc"] = min(20.0, float(d.wacc_pct) + 1.5)
                st.session_state["dcf_realization"] = 75.0
                st.session_state["dcf_overrun"] = 25.0
                st.rerun()
            if p2.button("Base Case (Reset)", width="stretch"):
                st.session_state["dcf_wacc"] = float(d.wacc_pct)
                st.session_state["dcf_tax"] = float(d.tax_rate_pct)
                st.session_state["dcf_g"] = float(d.terminal_growth_pct)
                st.session_state["dcf_realization"] = 100.0
                st.session_state["dcf_overrun"] = 0.0
                st.session_state["dcf_prob_weight"] = True
                st.session_state["dcf_term_method"] = "multiple"
                st.session_state["dcf_term_mult"] = 4.5
                st.rerun()
            if p3.button("Bull / Upside Case", width="stretch"):
                st.session_state["dcf_wacc"] = max(5.0, float(d.wacc_pct) - 0.75)
                st.session_state["dcf_realization"] = 120.0
                st.session_state["dcf_overrun"] = -10.0
                st.rerun()
            with p4:
                term_choice = st.selectbox(
                    "Terminal Valuation Basis",
                    ["Conservative Fade Multiple (IB Standard)", "Gordon Growth Perpetuity"],
                    index=0 if st.session_state.get("dcf_term_method", "multiple") == "multiple" else 1,
                    label_visibility="collapsed",
                )
                term_method = "multiple" if term_choice.startswith("Conservative") else "perpetuity"
                st.session_state["dcf_term_method"] = term_method

            s1, s2, s3 = st.columns(3)
            with s1:
                wacc = st.slider("Discount Rate / WACC (%)", 5.0, 18.0, float(st.session_state.get("dcf_wacc", d.wacc_pct)), 0.25, key="dcf_wacc")
                tax = st.slider("Effective Corporate Tax Rate (%)", 0.0, 45.0, float(st.session_state.get("dcf_tax", d.tax_rate_pct)), 1.0, key="dcf_tax")
            with s2:
                if term_method == "multiple":
                    term_mult = st.slider("Terminal Synergy Fade Multiple (x NOPAT)", 1.0, 10.0, float(st.session_state.get("dcf_term_mult", 4.5)), 0.5, key="dcf_term_mult")
                    term_g = float(st.session_state.get("dcf_g", d.terminal_growth_pct))
                else:
                    term_g = st.slider("Terminal Growth Rate g (%)", 0.0, 5.0, float(st.session_state.get("dcf_g", d.terminal_growth_pct)), 0.25, key="dcf_g")
                    term_mult = float(st.session_state.get("dcf_term_mult", 4.5))
                real_pct = st.slider("Global Synergy Realization (%)", 40.0, 150.0, float(st.session_state.get("dcf_realization", 100.0)), 5.0, key="dcf_realization")
            with s3:
                overrun = st.slider("Integration Cost Overrun (%)", -25.0, 100.0, float(st.session_state.get("dcf_overrun", 0.0)), 5.0, key="dcf_overrun")
                prob_w = st.toggle("Apply Workstream Probability Weighting", value=bool(st.session_state.get("dcf_prob_weight", True)), key="dcf_prob_weight")

        assump = ValuationAssumptions(
            wacc_pct=wacc,
            tax_rate_pct=tax,
            terminal_growth_pct=term_g,
            realization_factor_pct=real_pct,
            integration_cost_overrun_pct=overrun,
            use_probability_weighting=prob_w,
            terminal_method=term_method,
            terminal_multiple=term_mult,
            projection_years=max(3, min(7, round((d.time_horizon_months or 36) / 12) + 2)),
        )
        dcf = compute_synergy_dcf(report, assump)

        st.markdown(
            kpi_strip_html([
                ("Stressed Synergy NPV", _fmt_b(dcf.total_synergy_npv_b), f"Explicit {_fmt_b(dcf.pv_explicit_period_b)} + TV {_fmt_b(dcf.pv_terminal_value_b)}", True),
                ("Value Creation / EV", f"{dcf.value_creation_pct_of_deal:.1f}%" if dcf.value_creation_pct_of_deal is not None else "—", f"Deal EV: {_fmt_b(d.deal_value_usd_b)}", False),
                ("Run-Rate Synergies", _fmt_b(dcf.steady_state_total_synergy_b), f"Rev {_fmt_b(dcf.steady_state_revenue_synergy_b)} · Cost {_fmt_b(dcf.steady_state_cost_synergy_b)}", False),
                ("Simple Payback", f"{dcf.payback_years:.2f} yrs" if dcf.payback_years else "—", f"Discounted: {dcf.discounted_payback_years:.2f}y" if dcf.discounted_payback_years else "—", False),
                ("Synergy Project IRR", f"{dcf.synergy_irr_pct:.1f}%" if dcf.synergy_irr_pct is not None else "—", f"NPV / 1x Cost: {dcf.npv_to_integration_cost_ratio:.1f}x" if dcf.npv_to_integration_cost_ratio else "—", False),
            ]),
            unsafe_allow_html=True,
        )

        ch1, ch2 = st.columns(2)
        with ch1:
            with st.container(border=True):
                st.plotly_chart(synergy_bridge_chart(dcf), width="stretch", config={"displayModeBar": False})
        with ch2:
            with st.container(border=True):
                st.plotly_chart(cashflow_phasing_chart(dcf), width="stretch", config={"displayModeBar": False})

        wacc_vals, real_vals, grid = compute_sensitivity_matrix(report, assump)
        ch3, ch4 = st.columns(2)
        with ch3:
            with st.container(border=True):
                st.plotly_chart(sensitivity_heatmap_chart(wacc_vals, real_vals, grid), width="stretch", config={"displayModeBar": False})
        with ch4:
            with st.container(border=True):
                if report.synergy_workstreams:
                    st.plotly_chart(workstream_breakdown_chart(report), width="stretch", config={"displayModeBar": False})
                else:
                    st.info("No itemized bottom-up workstreams in this report.")

        with st.container(border=True):
            st.markdown(section_header_html("Annual Synergy Cash Flow Schedule ($M)", "EXPLICIT DCF PROJECTION"), unsafe_allow_html=True)
            cf_df = pd.DataFrame([
                {
                    "Year": f"Year {r.year}",
                    "Rev. Synergies ($M)": r.revenue_synergy_m,
                    "Cost Synergies ($M)": r.cost_synergy_m + r.capital_synergy_m,
                    "1x Costs ($M)": -r.integration_cost_m,
                    "Pre-Tax EBT ($M)": r.ebt_impact_m,
                    "Tax ($M)": -r.tax_impact_m,
                    "Net Cash Flow ($M)": r.net_cash_flow_m,
                    "Discount Factor": r.discount_factor,
                    "PV Cash Flow ($M)": r.pv_cash_flow_m,
                }
                for r in dcf.annual_rows
            ])
            st.dataframe(cf_df, width="stretch", hide_index=True)

            safe_a, safe_b = _safe_slug(d.entity_a_name), _safe_slug(d.entity_b_name)
            st.download_button(
                "Export Stressed DCF Workbook (Live Excel Formulas .xlsx)",
                data=report_to_excel_bytes(report, dcf),
                file_name=f"Synergy_DCF_Stressed_{safe_a}_{safe_b}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )

    # =====================================================================
    # SUB-TAB 2: Monte Carlo (10,000 Runs) & Valuation Football Field
    # =====================================================================
    with tabs[1]:
        with st.container(border=True):
            st.markdown(section_header_html("Stochastic Monte Carlo Synergy Simulation", "CHOLESKY-CORRELATED RISK ENGINE"), unsafe_allow_html=True)
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                n_sim = st.select_slider("Simulation Runs", options=[1000, 2500, 5000, 10000], value=10000)
            with mc2:
                real_std = st.slider("Realization Volatility (Std Dev %)", 5.0, 35.0, 15.0, 2.5)
            with mc3:
                wacc_std = st.slider("WACC Volatility (Std Dev %)", 0.25, 2.5, 1.0, 0.25)

        mc_res = run_monte_carlo_synergies(
            report,
            assump,
            MonteCarloConfig(n_simulations=n_sim, seed=42, realization_std_pct=real_std, wacc_std_pct=wacc_std),
        )
        lbo_base = compute_lbo_model(default_lbo_inputs_for_report(report), compute_sensitivity=False)
        ff = compute_football_field(report, dcf, lbo_result=lbo_base)

        st.markdown(
            kpi_strip_html([
                ("Monte Carlo Median (P50)", _fmt_b(mc_res.p50_b), f"Mean: {_fmt_b(mc_res.mean_b)} · Std: ±${mc_res.std_b:.2f}B", True),
                ("Downside P10 / VaR 95%", _fmt_b(mc_res.p10_b), f"95% VaR: {_fmt_b(mc_res.var_95_b)}", False),
                ("Upside P90", _fmt_b(mc_res.p90_b), f"Prob(NPV > 0): {mc_res.prob_npv_positive_pct:.1f}%", False),
                ("Football Field Midpoint", _fmt_b(ff.consensus_mid_b), f"Range: {_fmt_b(ff.consensus_low_b)}–{_fmt_b(ff.consensus_high_b)}", False),
                ("Walkaway Synergy EV", _fmt_b(ff.synergy_adjusted_walkaway_ev_b), f"Offer vs Mid: {ff.offer_vs_mid_premium_pct:+.1f}%", False),
            ]),
            unsafe_allow_html=True,
        )

        col_mc, col_ff = st.columns(2)
        with col_mc:
            with st.container(border=True):
                st.plotly_chart(
                    monte_carlo_histogram_chart(mc_res.samples_b, mc_res.p10_b, mc_res.p50_b, mc_res.p90_b),
                    width="stretch",
                    config={"displayModeBar": False},
                )
        with col_ff:
            with st.container(border=True):
                st.plotly_chart(
                    football_field_chart(ff.ranges, deal_value_b=d.deal_value_usd_b),
                    width="stretch",
                    config={"displayModeBar": False},
                )

        with st.container(border=True):
            st.markdown(f"**Valuation Triangulation Verdict:** {ff.valuation_verdict}")
            ff_df = pd.DataFrame([
                {
                    "Methodology": r.methodology,
                    "Category": r.category,
                    "Low ($B)": r.low_b,
                    "Midpoint ($B)": r.mid_b,
                    "High ($B)": r.high_b,
                    "Implied Multiple": r.multiple_range_label,
                    "Notes": r.notes,
                }
                for r in ff.ranges
            ])
            st.dataframe(ff_df, width="stretch", hide_index=True)

    # =====================================================================
    # SUB-TAB 3: 3-Statement Accretion / Dilution Merger Model
    # =====================================================================
    with tabs[2]:
        base_m = default_merger_inputs_for_report(report)
        with st.container(border=True):
            st.markdown(section_header_html("Transaction Financing Mix & PPA Controls", "3-STATEMENT MERGER MODEL"), unsafe_allow_html=True)
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                cash_pct = st.slider("Cash Financing (%)", 0, 100, int(base_m.cash_pct), 5)
            with m2:
                max_debt = max(0, 100 - cash_pct)
                debt_pct = st.slider("New Debt Financing (%)", 0, max_debt, min(int(base_m.debt_pct), max_debt), 5)
            with m3:
                stock_pct = max(0, 100 - cash_pct - debt_pct)
                st.metric("New Stock Issued (%)", f"{stock_pct}%")
            with m4:
                debt_rate = st.slider("New Debt Interest Rate (%)", 3.0, 12.0, float(base_m.new_debt_interest_rate_pct), 0.25)

        merger_in = MergerInputs(
            acquirer_name=base_m.acquirer_name,
            target_name=base_m.target_name,
            acquirer_share_price=base_m.acquirer_share_price,
            acquirer_diluted_shares_m=base_m.acquirer_diluted_shares_m,
            acquirer_net_income_y1_m=base_m.acquirer_net_income_y1_m,
            acquirer_ni_growth_pct=base_m.acquirer_ni_growth_pct,
            purchase_equity_value_m=base_m.purchase_equity_value_m,
            target_net_income_y1_m=base_m.target_net_income_y1_m,
            target_ni_growth_pct=base_m.target_ni_growth_pct,
            target_book_value_m=base_m.target_book_value_m,
            cash_pct=float(cash_pct),
            debt_pct=float(debt_pct),
            stock_pct=float(stock_pct),
            new_debt_interest_rate_pct=debt_rate,
            steady_state_synergies_m=dcf.steady_state_total_synergy_b * 1000.0,
            one_time_integration_cost_m=dcf.total_integration_cost_b * 1000.0,
            tax_rate_pct=assump.tax_rate_pct,
        )
        m_res = compute_merger_model(merger_in)

        st.markdown(
            kpi_strip_html([
                ("Year 1 EPS Impact", f"{m_res.year1_accretion_pct:+.2f}%", f"{m_res.year1_accretion_usd:+.2f} $/sh ({'Accretive' if m_res.is_year1_accretive else 'Dilutive'})", True),
                ("Year 3 EPS Impact", f"{m_res.year3_accretion_pct:+.2f}%", f"Year 5: {m_res.year5_accretion_pct:+.2f}%", False),
                ("Y1 Breakeven Synergies", f"${m_res.breakeven_pre_tax_synergies_y1_m:,.0f}M", f"Y1 Cushion: ${m_res.y1_synergy_cushion_m:+,.0f}M", False),
                ("Goodwill Created (PPA)", f"${m_res.goodwill_created_m/1000:.2f}B", f"Intangibles: ${m_res.identifiable_intangibles_m/1000:.2f}B", False),
                ("Pro-Forma Ownership", f"{m_res.acquirer_ownership_pct:.1f}%", f"Target Rollover: {m_res.target_ownership_pct:.1f}%", False),
            ]),
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            st.plotly_chart(accretion_dilution_chart(m_res.years), width="stretch", config={"displayModeBar": False})
            m_df = pd.DataFrame([
                {
                    "Year": f"Year {r.year}",
                    "Acq. NI ($M)": round(r.acquirer_standalone_ni_m, 1),
                    "Target NI ($M)": round(r.target_standalone_ni_m, 1),
                    "Synergies ($M)": round(r.pre_tax_synergies_m, 1),
                    "New Debt Int. ($M)": -round(r.interest_on_new_debt_m, 1),
                    "Intang. Amort ($M)": -round(r.intangible_amortization_m, 1),
                    "Pro-Forma NI ($M)": round(r.pro_forma_net_income_m, 1),
                    "Standalone EPS ($)": round(r.standalone_eps, 2),
                    "Pro-Forma EPS ($)": round(r.pro_forma_eps, 2),
                    "Accretion / (Dilution)": f"{r.eps_accretion_dilution_pct:+.2f}%",
                }
                for r in m_res.years
            ])
            st.dataframe(m_df, width="stretch", hide_index=True)

    # =====================================================================
    # SUB-TAB 4: Private Equity LBO & Section 338(h)(10) Tax Step-Up
    # =====================================================================
    with tabs[3]:
        base_lbo = default_lbo_inputs_for_report(report)
        with st.container(border=True):
            st.markdown(section_header_html("Private Equity LBO Capital Structure & Exit Controls", "SPONSOR RETURNS ENGINE"), unsafe_allow_html=True)
            l1, l2, l3, l4 = st.columns(4)
            with l1:
                entry_mult = st.slider("Entry EV / EBITDA (x)", 6.0, 22.0, float(base_lbo.entry_ev_ebitda_x), 0.5)
            with l2:
                exit_mult = st.slider("Exit EV / EBITDA (x)", 6.0, 22.0, float(base_lbo.exit_ev_ebitda_x), 0.5)
            with l3:
                sen_x = st.slider("Senior Debt (x EBITDA)", 1.0, 6.0, float(base_lbo.senior_debt_ebitda_x), 0.25)
            with l4:
                ebitda_cagr = st.slider("EBITDA CAGR (%)", 2.0, 25.0, float(base_lbo.ebitda_cagr_pct), 0.5)

        lbo_in = LBOInputs(
            company_name=base_lbo.company_name,
            ltm_ebitda_m=base_lbo.ltm_ebitda_m,
            ltm_revenue_m=base_lbo.ltm_revenue_m,
            entry_ev_ebitda_x=entry_mult,
            exit_ev_ebitda_x=exit_mult,
            hold_years=5,
            ebitda_cagr_pct=ebitda_cagr,
            senior_debt_ebitda_x=sen_x,
            mezz_debt_ebitda_x=base_lbo.mezz_debt_ebitda_x,
            tax_rate_pct=assump.tax_rate_pct,
        )
        lbo = compute_lbo_model(lbo_in, compute_sensitivity=True)
        tax_step = compute_tax_step_up(
            purchase_price_m=(d.deal_value_usd_b or 15.0) * 1000.0,
            tax_basis_m=(d.deal_value_usd_b or 15.0) * 350.0,
            tax_rate_pct=assump.tax_rate_pct,
            wacc_pct=assump.wacc_pct,
        )

        st.markdown(
            kpi_strip_html([
                ("Sponsor IRR (5-Year)", f"{lbo.sponsor_irr_pct:.1f}%" if lbo.sponsor_irr_pct is not None else "—", f"MOIC: {lbo.moic_x:.2f}x", True),
                ("Sponsor Equity Check", f"${lbo.sponsor_equity_m/1000:.2f}B", f"Debt: {lbo.debt_pct_of_sources:.0f}% · Equity: {lbo.equity_pct_of_sources:.0f}%", False),
                ("Exit Equity Value", f"${lbo.exit_equity_m/1000:.2f}B", f"Exit EV: ${lbo.exit_ev_m/1000:.2f}B", False),
                ("Total Debt Paydown", f"${lbo.total_debt_paydown_m/1000:.2f}B", f"Exit Leverage: {lbo.years[-1].net_debt_to_ebitda_x:.1f}x", False),
                ("338(h)(10) Tax Step-Up PV", f"${tax_step.pv_tax_shield_m/1000:.2f}B", f"{tax_step.pv_as_pct_of_purchase_price:.1f}% of EV Shield", False),
            ]),
            unsafe_allow_html=True,
        )

        lc1, lc2 = st.columns([1.25, 1.0])
        with lc1:
            with st.container(border=True):
                st.plotly_chart(lbo_debt_paydown_chart(lbo.years), width="stretch", config={"displayModeBar": False})
        with lc2:
            with st.container(border=True):
                st.markdown(section_header_html("Entry × Exit Multiple IRR Grid (%)", "LBO SENSITIVITY"), unsafe_allow_html=True)
                sens_df = pd.DataFrame(
                    [[f"{v:.1f}%" if v is not None else "—" for v in row] for row in lbo.irr_sensitivity_grid],
                    index=[f"Entry {x:.1f}x" for x in lbo.entry_multiple_axis],
                    columns=[f"Exit {x:.1f}x" for x in lbo.exit_multiple_axis],
                )
                st.dataframe(sens_df, width="stretch")
                st.caption(tax_step.structuring_note)

    # =====================================================================
    # SUB-TAB 5: Quality of Earnings (QoE) & Net Working Capital Peg
    # =====================================================================
    with tabs[4]:
        qoe_in = default_qoe_for_report(report)
        qoe = compute_qoe_bridge(qoe_in.reported_ebitda_m, qoe_in.revenue_m, qoe_in.adjustments, qoe_in.headline_ev_m)
        nwc = compute_nwc_peg(qoe_in.monthly_nwc, qoe_in.target_close_nwc_m, qoe_in.debt_like_items, qoe_in.cash_m, qoe_in.headline_ev_m)

        st.markdown(
            kpi_strip_html([
                ("Normalized Adj. EBITDA", f"${qoe.adjusted_ebitda_m:,.0f}M", f"Reported: ${qoe.reported_ebitda_m:,.0f}M ({qoe.margin_delta_bps:+.0f} bps)", True),
                ("QoE Scrutiny Score", f"{qoe.scrutiny_score}/100", f"Rating: {qoe.scrutiny_rating}", False),
                ("Implied EV / Adj. EBITDA", f"{qoe.implied_ev_adjusted_ebitda_x:.1f}x" if qoe.implied_ev_adjusted_ebitda_x else "—", f"Add-backs: {qoe.addback_pct_of_reported_ebitda:.1f}%", False),
                ("LTM NWC Peg", f"${nwc.ltm_avg_nwc_peg_m:,.0f}M", f"Closing Adj.: ${nwc.nwc_adjustment_m:+,.0f}M", False),
                ("Debt-Like Equity Bridge", f"${nwc.implied_equity_value_m/1000:.2f}B", f"Debt-Like Items: ${nwc.total_debt_like_m:,.0f}M", False),
            ]),
            unsafe_allow_html=True,
        )

        qc1, qc2 = st.columns([1.25, 1.0])
        with qc1:
            with st.container(border=True):
                st.plotly_chart(qoe_bridge_chart(qoe.reported_ebitda_m, qoe.adjustments, qoe.adjusted_ebitda_m), width="stretch", config={"displayModeBar": False})
        with qc2:
            with st.container(border=True):
                st.markdown(section_header_html("Cash-Free / Debt-Free Equity Bridge", "NWC & DEBT-LIKE ITEMS"), unsafe_allow_html=True)
                dl_df = pd.DataFrame([
                    {"Debt-Like Item": item.name, "Category": item.category, "Amount ($M)": item.amount_m, "Rationale": item.rationale}
                    for item in nwc.debt_like_items
                ])
                st.dataframe(dl_df, width="stretch", hide_index=True)
                if qoe.flags:
                    for fl in qoe.flags:
                        st.warning(fl)

    # =====================================================================
    # SUB-TAB 6: Customer Cohorts & Unit Economics
    # =====================================================================
    with tabs[5]:
        qoe_in = default_qoe_for_report(report)
        coh = analyze_customer_cohorts(qoe_in.customers)
        st.markdown(
            kpi_strip_html([
                ("Net Dollar Retention (NDR)", f"{coh.ndr_pct:.1f}%", f"Gross Retention (GRR): {coh.grr_pct:.1f}%", True),
                ("Top-5 Concentration", f"{coh.top5_concentration_pct:.1f}%", f"Top-1: {coh.top1_concentration_pct:.1f}%", False),
                ("Top-10 Concentration", f"{coh.top10_concentration_pct:.1f}%", f"Top-20: {coh.top20_concentration_pct:.1f}%", False),
                ("HHI Concentration Index", f"{coh.hhi_index:,.0f}", f"Tier: {coh.hhi_tier}", False),
                ("Logo Retention", f"{coh.logo_retention_pct:.1f}%", f"{coh.customer_count} Enterprise Accounts", False),
            ]),
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            st.markdown(section_header_html("Top Enterprise Customer Concentration Pareto", "COMMERCIAL UNIT ECONOMICS"), unsafe_allow_html=True)
            if coh.red_flags:
                for rf in coh.red_flags:
                    st.warning(rf)
            cust_df = pd.DataFrame([
                {
                    "Rank": c["rank"],
                    "Customer": c["name"],
                    "Segment": c["segment"],
                    "Prior Rev ($M)": c["prior_rev_m"],
                    "Current Rev ($M)": c["current_rev_m"],
                    "Rev Share (%)": c["share_pct"],
                    "YoY Growth (%)": c["yoy_growth_pct"],
                    "Contract Term (mo.)": c["contract_term_months"],
                }
                for c in coh.ranked_customers[:15]
            ])
            st.dataframe(cust_df, width="stretch", hide_index=True)
