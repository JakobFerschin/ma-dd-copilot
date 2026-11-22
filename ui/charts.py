"""
Institutional Plotly Visualizations for M&A Investment Committee Presentations.

All figures adhere strictly to the Obsidian Navy / Antique Gold / Slate / Forest / Crimson
private-bank palette with transparent/crisp-white surfaces and tabular figures.
"""
from __future__ import annotations

import plotly.graph_objects as go

from core.financial_model import SynergyDCFResult
from schemas.report_schema import DueDiligenceReport, RiskDimension

NAVY = "#0B192C"
NAVY_MID = "#1E3A5F"
GOLD = "#B8935A"
FOREST = "#1B6B46"
CRIMSON = "#991B1B"
SLATE = "#64748B"
GRID = "#E8E5DD"

_BASE_LAYOUT = dict(
    font=dict(family="Inter, -apple-system, sans-serif", color="#141822", size=12),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#FFFFFF",
    margin=dict(l=44, r=24, t=44, b=40),
)


def synergy_bridge_chart(dcf: SynergyDCFResult) -> go.Figure:
    """After-Tax Synergy Value Creation Waterfall Bridge ($B)."""
    labels = [
        "PV Revenue<br>Synergies",
        "PV Cost & Cap.<br>Synergies",
        "PV Integration<br>Costs",
        "Net Synergy<br>NPV",
    ]
    cost_cap = round(dcf.pv_cost_synergies_b + dcf.pv_capital_synergies_b, 2)
    neg_int = -round(dcf.pv_integration_costs_b, 2)
    values = [dcf.pv_revenue_synergies_b, cost_cap, neg_int, dcf.total_synergy_npv_b]
    measures = ["relative", "relative", "relative", "total"]
    text = [
        f"+${dcf.pv_revenue_synergies_b:.2f}B",
        f"+${cost_cap:.2f}B",
        f"-${dcf.pv_integration_costs_b:.2f}B",
        f"${dcf.total_synergy_npv_b:.2f}B",
    ]

    fig = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=measures,
            text=text,
            textposition="outside",
            textfont=dict(size=12, color=NAVY),
            increasing=dict(marker=dict(color=FOREST)),
            decreasing=dict(marker=dict(color=CRIMSON)),
            totals=dict(marker=dict(color=NAVY)),
            connector=dict(line=dict(color="#CBD5E1", width=1, dash="dot")),
        )
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="After-Tax Synergy Value Creation Bridge ($B)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        yaxis=dict(title="USD Billions ($B)", gridcolor=GRID, zerolinecolor="#94A3B8"),
        xaxis=dict(showgrid=False),
        showlegend=False,
        height=360,
    )
    return fig


def cashflow_phasing_chart(dcf: SynergyDCFResult) -> go.Figure:
    """Annual Synergy Capture Ramp-Up vs. One-Time Costs + Cumulative PV Line ($M)."""
    years = [f"Year {r.year}" for r in dcf.annual_rows]
    rev = [r.revenue_synergy_m for r in dcf.annual_rows]
    cost = [r.cost_synergy_m + r.capital_synergy_m for r in dcf.annual_rows]
    integ = [-r.integration_cost_m for r in dcf.annual_rows]
    cum_pv = [r.cumulative_pv_m for r in dcf.annual_rows]

    fig = go.Figure()
    fig.add_bar(name="Revenue Synergies ($M)", x=years, y=rev, marker_color=NAVY_MID)
    fig.add_bar(name="Cost & Capital Synergies ($M)", x=years, y=cost, marker_color=GOLD)
    fig.add_bar(name="One-Time Integration Costs ($M)", x=years, y=integ, marker_color=CRIMSON)
    fig.add_trace(
        go.Scatter(
            name="Cumulative PV ($M)",
            x=years,
            y=cum_pv,
            mode="lines+markers",
            line=dict(color=FOREST, width=2.8),
            marker=dict(size=8, color=FOREST, line=dict(color="#FFFFFF", width=1.5)),
            yaxis="y2",
        )
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        barmode="relative",
        title=dict(text="Annual Synergy Ramp-Up & Cumulative PV ($M)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        yaxis=dict(title="Annual Pre-Tax Impact ($M)", gridcolor=GRID, zerolinecolor="#94A3B8"),
        yaxis2=dict(title="Cumulative PV ($M)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.28, xanchor="center", x=0.5, font=dict(size=11)),
        height=380,
    )
    return fig


def sensitivity_heatmap_chart(
    wacc_vals: list[float],
    real_vals: list[float],
    grid: list[list[float]],
) -> go.Figure:
    """Goldman Sachs-style 2D WACC × Realization Rate Synergy NPV Sensitivity Heatmap ($B)."""
    x_labels = [f"{r:.0f}%" for r in real_vals]
    y_labels = [f"{w:.1f}%" for w in wacc_vals]
    text_grid = [[f"${v:.2f}B" for v in row] for row in grid]

    fig = go.Figure(
        go.Heatmap(
            z=grid,
            x=x_labels,
            y=y_labels,
            text=text_grid,
            texttemplate="%{text}",
            textfont=dict(size=12, family="JetBrains Mono, monospace"),
            colorscale=[
                [0.0, "#F5EFE4"],
                [0.45, "#C7D6E8"],
                [0.8, "#1E3A5F"],
                [1.0, "#0B192C"],
            ],
            colorbar=dict(title="NPV ($B)", thickness=12),
            hovertemplate="WACC: %{y}<br>Realization: %{x}<br><b>Synergy NPV: $%{z:.2f}B</b><extra></extra>",
        )
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="DCF Sensitivity Matrix: Synergy NPV ($B)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        xaxis=dict(title="Synergy Realization Factor (%)", side="top"),
        yaxis=dict(title="Discount Rate (WACC %)", autorange="reversed"),
        height=370,
    )
    return fig


def risk_matrix_chart(report: DueDiligenceReport) -> go.Figure:
    """Interactive Likelihood (1–5) × Impact (1–5) Investment Committee Risk Matrix."""
    dim_colors = {
        RiskDimension.STRATEGIC: NAVY,
        RiskDimension.OPERATIONAL_TECH: NAVY_MID,
        RiskDimension.CULTURAL: GOLD,
        RiskDimension.REGULATORY_ESG: CRIMSON,
        RiskDimension.FINANCIAL: FOREST,
    }

    fig = go.Figure()
    # Subtle quadrant shading
    fig.add_shape(type="rect", x0=0.5, y0=0.5, x1=3.0, y1=3.0, fillcolor="rgba(20,83,45,0.05)", line_width=0)
    fig.add_shape(type="rect", x0=3.0, y0=3.0, x1=5.5, y1=5.5, fillcolor="rgba(153,27,27,0.08)", line_width=0)

    # Slight deterministic jitter for identical (L, I) pairs so markers never obscure each other
    seen_coords: dict[tuple[int, int], int] = {}
    for risk in report.risk_register:
        key = (risk.likelihood, risk.impact)
        count = seen_coords.get(key, 0)
        seen_coords[key] = count + 1
        dx = (count % 3 - 1) * 0.14 if count else 0.0
        dy = (count // 3) * 0.14 if count else 0.0

        fig.add_trace(
            go.Scatter(
                x=[risk.likelihood + dx],
                y=[risk.impact + dy],
                mode="markers+text",
                name=risk.dimension.value,
                text=[risk.title[:22] + ("…" if len(risk.title) > 22 else "")],
                textposition="top center",
                textfont=dict(size=10, color=NAVY),
                marker=dict(
                    size=18 if risk.is_deal_breaker else 14,
                    symbol="diamond" if risk.is_deal_breaker else "circle",
                    color=dim_colors.get(risk.dimension, SLATE),
                    line=dict(color="#FFFFFF", width=1.8),
                ),
                hovertemplate=(
                    f"<b>{risk.title}</b><br>"
                    f"Dimension: {risk.dimension.value}<br>"
                    f"Severity: {risk.severity.value} | Score: {risk.likelihood * risk.impact}/25<br>"
                    f"Deal-Breaker: {'YES' if risk.is_deal_breaker else 'No'}<br>"
                    f"Mitigation: {risk.mitigation}<extra></extra>"
                ),
                showlegend=False,
            )
        )

    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="Risk Matrix (◆ = Deal-Breaker Risk)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        xaxis=dict(title="Likelihood (1 = Rare → 5 = Almost Certain)", range=[0.5, 5.5], dtick=1, gridcolor=GRID),
        yaxis=dict(title="Impact (1 = Minor → 5 = Catastrophic)", range=[0.5, 5.5], dtick=1, gridcolor=GRID),
        height=390,
    )
    return fig


def pillar_radar_chart(report: DueDiligenceReport) -> go.Figure:
    """5-Pillar MECE Institutional Readiness & Conviction Radar Chart."""
    scores = report.pillar_scores
    if not scores:
        return go.Figure()

    def _wrap(label: str) -> str:
        words = label.split()
        if len(words) <= 2:
            return label
        mid = len(words) // 2
        return " ".join(words[:mid]) + "<br>" + " ".join(words[mid:])

    wrapped = [_wrap(p.pillar) for p in scores]
    theta = wrapped + [wrapped[0]]
    r = [p.score for p in scores] + [scores[0].score]

    fig = go.Figure(
        go.Scatterpolar(
            r=r,
            theta=theta,
            fill="toself",
            fillcolor="rgba(184, 147, 90, 0.22)",
            line=dict(color=NAVY, width=2.4),
            marker=dict(size=7, color=GOLD, line=dict(color=NAVY, width=1.5)),
            hovertemplate="<b>%{theta}</b>: %{r}/100<extra></extra>",
        )
    )
    fig.update_layout(
        font=dict(family="Inter, -apple-system, sans-serif", color="#141822", size=11),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FFFFFF",
        margin=dict(l=64, r=64, t=48, b=38),
        title=dict(text="MECE Conviction Profile", font=dict(family="Georgia, serif", size=14, color=NAVY), x=0.5),
        polar=dict(
            bgcolor="#FFFFFF",
            radialaxis=dict(visible=True, range=[0, 100], tickfont=dict(size=9, color=SLATE), gridcolor=GRID),
            angularaxis=dict(tickfont=dict(size=10, color=NAVY)),
        ),
        showlegend=False,
        height=355,
    )
    return fig


def workstream_breakdown_chart(report: DueDiligenceReport) -> go.Figure:
    """Horizontal bar comparison: Gross vs. Probability-Adjusted Annual Run-Rate ($M)."""
    ws_list = sorted(report.synergy_workstreams, key=lambda w: w.annual_run_rate_usd_m)
    names = [w.name[:34] + ("…" if len(w.name) > 34 else "") for w in ws_list]
    gross = [w.annual_run_rate_usd_m for w in ws_list]
    adj = [round(w.annual_run_rate_usd_m * w.probability_pct / 100.0, 1) for w in ws_list]

    fig = go.Figure()
    fig.add_bar(name="Gross Run-Rate ($M)", y=names, x=gross, orientation="h", marker_color="#CBD5E1")
    fig.add_bar(name="Risk-Adjusted Run-Rate ($M)", y=names, x=adj, orientation="h", marker_color=NAVY)
    fig.update_layout(
        **_BASE_LAYOUT,
        barmode="overlay",
        title=dict(text="Synergy Levers: Gross vs. Probability-Adjusted ($M/yr)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        xaxis=dict(title="Annual Pre-Tax Run-Rate ($M)", gridcolor=GRID),
        yaxis=dict(showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.24, xanchor="center", x=0.5),
        height=max(320, 52 * len(ws_list) + 120),
    )
    return fig


def deal_comparison_chart(reports: list[DueDiligenceReport]) -> go.Figure:
    """Side-by-Side Comparison of Deal Value vs. Synergy NPV across selected Deal Room deals."""
    from core.financial_model import compute_synergy_dcf

    labels = [f"{r.deal.entity_a_name[:14]} × {r.deal.entity_b_name[:14]}" for r in reports]
    deal_vals = [r.deal.deal_value_usd_b or 0.0 for r in reports]
    npvs = [compute_synergy_dcf(r).total_synergy_npv_b for r in reports]

    fig = go.Figure()
    fig.add_bar(name="Deal Value ($B)", x=labels, y=deal_vals, marker_color=NAVY)
    fig.add_bar(name="Synergy NPV ($B)", x=labels, y=npvs, marker_color=GOLD)
    fig.update_layout(
        **_BASE_LAYOUT,
        barmode="group",
        title=dict(text="Deal Comparison: Transaction EV vs. Synergy NPV ($B)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        yaxis=dict(title="USD Billions ($B)", gridcolor=GRID),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22, xanchor="center", x=0.5),
        height=350,
    )
    return fig


def monte_carlo_histogram_chart(
    samples: list[float],
    p10: float,
    p50: float,
    p90: float,
) -> go.Figure:
    """Stochastic Monte Carlo Synergy NPV Distribution ($B) with P10 / P50 / P90 markers."""
    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=samples,
            nbinsx=45,
            marker=dict(color=NAVY_MID, line=dict(color="#FFFFFF", width=0.5)),
            opacity=0.88,
            name="Simulated NPV",
            hovertemplate="Synergy NPV: $%{x:.2f}B<br>Count: %{y}<extra></extra>",
        )
    )
    for val, label, color in (
        (p10, f"P10: ${p10:.2f}B", CRIMSON),
        (p50, f"Median (P50): ${p50:.2f}B", GOLD),
        (p90, f"P90: ${p90:.2f}B", FOREST),
    ):
        fig.add_vline(
            x=val,
            line_width=2.2,
            line_dash="dash",
            line_color=color,
            annotation_text=label,
            annotation_position="top",
            annotation_font=dict(size=10, color=color, family="JetBrains Mono, monospace"),
        )
    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="Monte Carlo Synergy NPV Distribution ($B)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        xaxis=dict(title="Simulated Synergy NPV ($B)", gridcolor=GRID),
        yaxis=dict(title="Simulation Frequency", gridcolor=GRID),
        showlegend=False,
        height=370,
    )
    return fig


def football_field_chart(ranges: list, deal_value_b: float | None = None) -> go.Figure:
    """Investment Banking 'Football Field' Implied Enterprise Value Valuation Chart ($B)."""
    fig = go.Figure()
    labels = [r.methodology for r in ranges]
    lows = [r.low_b for r in ranges]
    highs = [r.high_b for r in ranges]
    mids = [r.mid_b for r in ranges]
    spans = [max(h - l, 0.05) for l, h in zip(lows, highs)]

    fig.add_trace(
        go.Bar(
            y=labels,
            x=spans,
            base=lows,
            orientation="h",
            marker=dict(color=NAVY_MID, line=dict(color=NAVY, width=1.2)),
            text=[f"${l:.1f}B – ${h:.1f}B" for l, h in zip(lows, highs)],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(color="#FFFFFF", size=11, family="JetBrains Mono, monospace"),
            hovertemplate="<b>%{y}</b><br>Range: %{text}<extra></extra>",
            name="Valuation Range",
        )
    )
    fig.add_trace(
        go.Scatter(
            y=labels,
            x=mids,
            mode="markers",
            marker=dict(size=9, color=GOLD, symbol="diamond", line=dict(color=NAVY, width=1.2)),
            name="Midpoint",
            hovertemplate="<b>%{y} Midpoint</b>: $%{x:.2f}B<extra></extra>",
        )
    )
    if deal_value_b is not None and deal_value_b > 0:
        fig.add_vline(
            x=deal_value_b,
            line_width=2.2,
            line_dash="dot",
            line_color=CRIMSON,
            annotation_text=f"Deal EV: ${deal_value_b:.1f}B",
            annotation_position="top right",
            annotation_font=dict(size=11, color=CRIMSON, family="JetBrains Mono, monospace"),
        )
    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="Valuation Football Field — Implied Enterprise Value ($B)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        xaxis=dict(title="Implied Enterprise Value ($B)", gridcolor=GRID),
        yaxis=dict(autorange="reversed", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22, xanchor="center", x=0.5),
        height=370,
    )
    return fig


def qoe_bridge_chart(reported_ebitda_m: float, adjustments: list, adjusted_ebitda_m: float) -> go.Figure:
    """Quality of Earnings (QoE) Normalized Adjusted EBITDA Waterfall Bridge ($M)."""
    labels = ["Reported<br>EBITDA"] + [a.label[:20] for a in adjustments] + ["Normalized<br>Adj. EBITDA"]
    values = [reported_ebitda_m] + [a.amount_m for a in adjustments] + [adjusted_ebitda_m]
    measures = ["absolute"] + ["relative"] * len(adjustments) + ["total"]
    text = [f"${reported_ebitda_m:,.0f}M"] + [f"{'+' if a.amount_m >= 0 else ''}${a.amount_m:,.0f}M" for a in adjustments] + [f"${adjusted_ebitda_m:,.0f}M"]

    fig = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=measures,
            text=text,
            textposition="outside",
            textfont=dict(size=11, color=NAVY),
            increasing=dict(marker=dict(color=FOREST)),
            decreasing=dict(marker=dict(color=CRIMSON)),
            totals=dict(marker=dict(color=NAVY)),
            connector=dict(line=dict(color="#CBD5E1", width=1, dash="dot")),
        )
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="Quality of Earnings (QoE) — Adjusted EBITDA Bridge ($M)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        yaxis=dict(title="USD Millions ($M)", gridcolor=GRID),
        showlegend=False,
        height=360,
    )
    return fig


def accretion_dilution_chart(rows: list) -> go.Figure:
    """5-Year Standalone vs. Pro-Forma EPS Accretion / Dilution Chart."""
    years = [f"Year {r.year}" for r in rows]
    standalone = [r.standalone_eps for r in rows]
    proforma = [r.pro_forma_eps for r in rows]
    pct = [r.eps_accretion_dilution_pct for r in rows]

    fig = go.Figure()
    fig.add_bar(name="Standalone EPS ($)", x=years, y=standalone, marker_color="#CBD5E1")
    fig.add_bar(name="Pro-Forma EPS ($)", x=years, y=proforma, marker_color=NAVY)
    fig.add_trace(
        go.Scatter(
            name="Accretion / (Dilution) %",
            x=years,
            y=pct,
            mode="lines+markers+text",
            text=[f"{v:+.1f}%" for v in pct],
            textposition="top center",
            line=dict(color=GOLD, width=2.6),
            marker=dict(size=8, color=GOLD, line=dict(color=NAVY, width=1.4)),
            yaxis="y2",
        )
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        barmode="group",
        title=dict(text="3-Statement Merger Model: EPS Accretion / (Dilution)", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        yaxis=dict(title="EPS ($ / Share)", gridcolor=GRID),
        yaxis2=dict(title="Accretion / Dilution (%)", overlaying="y", side="right", showgrid=False, zeroline=True, zerolinecolor=CRIMSON),
        legend=dict(orientation="h", yanchor="bottom", y=-0.26, xanchor="center", x=0.5),
        height=370,
    )
    return fig


def lbo_debt_paydown_chart(rows: list) -> go.Figure:
    """Private Equity LBO Deleveraging & Implied Sponsor Equity Value Creation ($M)."""
    years = [f"Year {r.year}" for r in rows]
    total_debt = [r.ending_total_debt_m for r in rows]
    leverage = [r.net_debt_to_ebitda_x for r in rows]

    fig = go.Figure()
    fig.add_bar(name="Ending Total Debt ($M)", x=years, y=total_debt, marker_color=NAVY_MID)
    fig.add_trace(
        go.Scatter(
            name="Net Debt / EBITDA (x)",
            x=years,
            y=leverage,
            mode="lines+markers+text",
            text=[f"{v:.1f}x" for v in leverage],
            textposition="top center",
            line=dict(color=GOLD, width=2.6),
            marker=dict(size=8, color=GOLD, line=dict(color=NAVY, width=1.4)),
            yaxis="y2",
        )
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        title=dict(text="LBO Debt Paydown Schedule & Deleveraging Profile", font=dict(family="Georgia, serif", size=15, color=NAVY)),
        yaxis=dict(title="Debt Balance ($M)", gridcolor=GRID),
        yaxis2=dict(title="Leverage (Net Debt / EBITDA)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.26, xanchor="center", x=0.5),
        height=365,
    )
    return fig

