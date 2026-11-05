"""Plotly figures, colored for the current light or dark mode (ui.THEMES)."""
import math

import pandas as pd
import plotly.graph_objects as go

from lib import data, tooltips, ui



def _c() -> dict:
    """Colors for the current light/dark mode (ui.THEMES): card, text, muted, line and so on."""
    return ui.colors()


# Sequential blue (dataviz reference ramp, steps 650 -> 100), low to high on the
# dark surface: low values sit near the background and high values read brightest.
# The light mode reverses it, so high values are darkest (state_map()).
BLUE_RAMP = ["#104281", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"]

# Map metrics: key -> (menu label, group, legend/hover title, value format, note).
METRICS = {
    "generic_rx_share": ("Generic share of Rx", "Utilization", "Generic share of prescriptions", "pct",
                         "Share of paid prescriptions filled with a generic (ANDA) product."),
    "biosimilar_share": ("Biosimilar uptake", "Utilization", "Biosimilar share of biologic spending", "pct",
                         "Biosimilar and interchangeable-biosimilar spending as a share of all biologic "
                         "spending (reference biologics, biosimilars and interchangeable biosimilars)."),
    "gross_savings": ("Savings opportunity", "Savings", "Savings before rebates", "usd",
                      "Gross savings if matched brands had been filled with their generic or biosimilar."),
    "avg_rebate_savings": ("Savings after rebates", "Savings", "Savings after average rebates", "usd",
                           "Savings after brand rebates at an estimated 61.9% (calibrated to MACPAC FY 2024)."),
    "savings_share": ("Savings vs. drug spending", "Savings", "Savings before rebates as a share of drug spending",
                      "pct", "Gross savings divided by the state's total Medicaid drug spending."),
    "brand_spend_share": ("Brand share of spending", "Spending", "Brand and reference biologic share of spending",
                          "pct", "Brand single source, brand with generic competition and reference biologics."),
    "spending": ("Total drug spending", "Spending", "Medicaid drug spending", "usd",
                 "Medicaid amount reimbursed to pharmacies, before manufacturer rebates."),
    "cost_per_rx": ("Cost per prescription", "Spending", "Spending per prescription", "usd2",
                    "Total Medicaid spending divided by paid prescriptions, all product types."),
}


def fmt(v: float, kind: str) -> str:
    if pd.isna(v):
        return "—"
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "usd2":
        return f"${v:,.2f}"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:,.1f}{suffix}"
    return f"${v:,.0f}"


def _money_ticks(lo: float, hi: float, n: int = 5) -> tuple:
    """Round tick values across lo..hi, labeled $500M / $1.5B rather than Plotly's SI "$1.5G"."""
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    vals = [math.ceil(lo / step) * step + i * step for i in range(n + 2)]
    vals = [v for v in vals if v <= hi + 1e-9]
    text = []
    for v in vals:
        div, suffix = next(((d, x) for d, x in ((1e9, "B"), (1e6, "M"), (1e3, "K")) if v >= d), (1, ""))
        text.append(f"${v / div:,.2f}".rstrip("0").rstrip(".") + suffix)
    return vals, text


def state_map(metric: str, selected: str) -> go.Figure:
    """Choropleth of one metric across states; the selected state is outlined."""
    _, _, title, kind, _ = METRICS[metric]
    df = data.state_metrics()
    df = df[df.geo != data.NATIONAL].copy()
    df["name"] = df.geo.map(data.STATE_NAMES)
    df["label"] = df[metric].map(lambda v: fmt(v, kind))

    if kind == "pct":
        ticks = dict(tickformat=".0%")
    else:
        vals, text = _money_ticks(df[metric].min(), df[metric].max())
        ticks = dict(tickvals=vals, ticktext=text)
    ramp = BLUE_RAMP if ui.mode() == "dark" else BLUE_RAMP[::-1]
    fig = go.Figure(
        go.Choropleth(
            locations=df.geo, z=df[metric], locationmode="USA-states",
            colorscale=[[i / (len(ramp) - 1), c] for i, c in enumerate(ramp)],
            marker_line_color=_c()['card'], marker_line_width=1,
            customdata=df[["name", "label"]],
            hovertemplate="<b>%{customdata[0]}</b><br>" + title + ": %{customdata[1]}<extra></extra>",
            colorbar=dict(
                orientation="h", thickness=8, len=0.5, x=0.5, y=-0.02, xanchor="center", yanchor="top",
                outlinewidth=0, **ticks, tickfont=dict(color=_c()['muted'], size=11),
            ),
        )
    )
    if selected != data.NATIONAL and selected in set(df.geo):
        fig.add_trace(go.Choropleth(
            locations=[selected], z=[0], locationmode="USA-states", showscale=False,
            colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
            marker_line_color=_c()['text'], marker_line_width=2.5, hoverinfo="skip",
        ))
    fig.update_geos(scope="usa", bgcolor="rgba(0,0,0,0)", showlakes=False, showland=False, showframe=False)
    fig.update_layout(
        height=420, margin=dict(l=0, r=0, t=0, b=40), dragmode=False,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color=_c()['text']),
        hoverlabel=dict(bgcolor=_c()['raised'], bordercolor=_c()['tip_line'], font=dict(color=_c()['text'], size=13)),
    )
    return fig


# Categorical slots 1 and 2 (dataviz reference palette, dark steps): prescriptions
# are always blue and spending always orange, on every page.
RX_COLOR = "#3987e5"
SPEND_COLOR = "#d95926"


# Mix card: brand-name groups in orange, their alternatives in blue (the
# color-blind-safe axis), lighter steps within each family; other in gray.
MIX_COLORS = ["#d95926", "#f0a37f", "#3987e5", "#86b6ef", "#5A5D66"]
MIX_TEXT = ["#FFFFFF", "#17181C", "#FFFFFF", "#17181C", "#FFFFFF"]


def mix_bars(mix: pd.DataFrame) -> go.Figure:
    """Two 100% bars, prescriptions over spending, split into data.MIX_GROUPS.

    mix has one row per group: group, types, prescriptions, spending, rx_share,
    spend_share. A white bracket joins the brand-name part of the two bars, so the
    jump from share of prescriptions to share of spending is the first thing seen.
    """
    fig = go.Figure()
    w = 0.42                                   # bar thickness; bars sit at y=1 (Rx) and y=0 (spending)
    for i, r in enumerate(mix.itertuples()):
        tip = tooltips.spec(r.group, r.types, [
            ("Share of prescriptions", f"{r.rx_share:.1%}"), ("Share of spending", f"{r.spend_share:.1%}"),
            ("Prescriptions", f"{r.prescriptions:,.0f}"), ("Medicaid spending", f"${r.spending:,.0f}"),
        ])
        fig.add_trace(go.Bar(
            name=r.group, orientation="h", y=[1, 0], x=[r.rx_share, r.spend_share], width=w,
            marker=dict(color=MIX_COLORS[i], line=dict(color=_c()['card'], width=2)),
            text=[f"{v:.0%}" if v >= 0.06 else "" for v in (r.rx_share, r.spend_share)],
            textposition="inside", insidetextanchor="middle", textfont=dict(color=MIX_TEXT[i], size=12),
            customdata=[tip, tip], hoverinfo="none",
        ))
    brand = mix.head(2)
    rx, sp = brand.rx_share.sum(), brand.spend_share.sum()
    top, bottom = 1 - w / 2, w / 2
    # Muted gray bracket under the brand-name part of each bar, joined midpoint to midpoint
    # by a right-angled connector.
    g, t = 0.08, 0.02                         # bracket gap from the bar, tick length
    a, b = top - g, bottom + g
    fig.add_shape(type="path", line=dict(color=_c()['muted'], width=1.5), path=(
        f"M 0 {top - t} L 0 {a} L {rx} {a} L {rx} {top - t} "
        f"M {rx / 2} {a} L {rx / 2} {(a + b) / 2} L {sp / 2} {(a + b) / 2} L {sp / 2} {b} "
        f"M 0 {bottom + t} L 0 {b} L {sp} {b} L {sp} {bottom + t}"))
    # Segments under 1% are too thin to see (biosimilars are 0.1% of prescriptions),
    # so they get a callout: above the prescriptions bar, below the spending bar.
    for field, edge, label_y in (("rx_share", 1 + w / 2, 1.5), ("spend_share", -w / 2, -0.5)):
        ends = mix[field].cumsum()
        tiny = [(r.group, getattr(r, field), end) for r, end in zip(mix.itertuples(), ends) if 0 < getattr(r, field) < 0.01]
        if tiny:
            x = tiny[0][2] - tiny[0][1] / 2
            tick_end = label_y - 0.12 if label_y > 1 else label_y + 0.12
            fig.add_shape(type="line", x0=x, x1=x, y0=edge, y1=tick_end, line=dict(color=_c()['muted'], width=1))
            fig.add_annotation(x=x, y=label_y, xanchor="right", showarrow=False, font=dict(color=_c()['muted'], size=11.5),
                               text=" · ".join(f"{g} {v:.1%}" for g, v, _ in tiny))
    for y, label in ((1, "Prescriptions"), (0, "Spending")):
        fig.add_annotation(x=0, xref="paper", xanchor="left", y=y, showarrow=False, text=label,
                           font=dict(color=_c()['text'], size=12.5))
    fig.update_xaxes(visible=False, range=[0, 1], domain=[0.14, 1])
    fig.update_yaxes(visible=False, range=[-0.6, 1.6])
    fig.update_layout(
        # The legend is HTML above the chart (rankings.py), so it wraps cleanly.
        barmode="stack", height=190, margin=dict(l=0, r=0, t=4, b=0), dragmode=False, showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, sans-serif", color=_c()['text']),
    )
    return fig


BRAND_COLOR, ALT_COLOR = MIX_COLORS[0], MIX_COLORS[2]


def breakeven_dots(df: pd.DataFrame, avg_rate: float, min_rate: float) -> go.Figure:
    """Each brand's break-even rebate (the rebate at which it would cost the same as its
    alternatives after their own rebates), against the average and minimum brand rates.

    df: brand, breakeven, gross_savings, rank. Right of the average line, the switch
    still saves after average rebates; left of it, the brand may already be cheaper.
    """
    df = df.sort_values("rank")
    saves = df.breakeven > avg_rate
    tips = [tooltips.spec(r.brand, f"#{r.rank} by savings", [
        ("Break-even brand rebate", f"{r.breakeven:.1%}"), ("Average brand rebate (est.)", f"{avg_rate:.1%}"),
        ("Gross savings", _short(r.gross_savings, True))],
        "Switching saves even after average rebates." if s else
        "After average rebates, the brand may cost Medicaid less.") for r, s in zip(df.itertuples(), saves)]
    fig = go.Figure(go.Scatter(
        x=df.breakeven, y=df.brand, mode="markers",
        marker=dict(size=11, color=[ALT_COLOR if s else BRAND_COLOR for s in saves], line=dict(color=_c()['card'], width=2)),
        customdata=tips, hoverinfo="none", cliponaxis=False,
    ))
    for x, label in ((min_rate, f"Minimum {min_rate:.1%}"), (avg_rate, f"Average {avg_rate:.1%}")):
        fig.add_vline(x=x, line=dict(color=_c()['muted'], width=1, dash="dot"))
        fig.add_annotation(x=x, y=1, yref="paper", yanchor="bottom", showarrow=False, text=label,
                           font=dict(color=_c()['muted'], size=11.5))
    fig.update_xaxes(range=[0, 1], tickformat=".0%", gridcolor=_c()['line'], zeroline=False,
                     tickfont=dict(color=_c()['muted'], size=11))
    fig.update_yaxes(autorange="reversed", showgrid=True, gridcolor=_c()['card_alt'], tickfont=dict(color=_c()['text'], size=12),
                     ticks="", ticklabelstandoff=8)
    fig.update_layout(
        height=24 * len(df) + 60, margin=dict(l=0, r=12, t=26, b=10), dragmode=False, showlegend=False,
        hovermode="closest", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color=_c()['text']),
    )
    return fig


def net_price_bars(prices: pd.DataFrame) -> go.Figure:
    """Brand vs. alternative price per unit in each scenario (grouped bars).

    prices: scenario, brand, alt (dollars per billing unit), in display order.
    """
    fig = go.Figure()
    for col, name, color in (("brand", "Brand", BRAND_COLOR), ("alt", "Alternative", ALT_COLOR)):
        tips = [tooltips.spec(f"{name}: {r.scenario.lower()}", "", [("Per unit", f"${getattr(r, col):,.4f}"),
                ("Brand vs. alternative", f"{r.brand / r.alt:,.1f}×" if r.alt else "—")]) for r in prices.itertuples()]
        fig.add_trace(go.Bar(
            name=name, x=prices.scenario, y=prices[col], marker=dict(color=color, cornerradius=4),
            text=[f"${v:,.2f}" if v >= 1 else f"${v:,.4f}" for v in prices[col]], textposition="outside",
            textfont=dict(color=_c()['muted'], size=12), cliponaxis=False, customdata=tips, hoverinfo="none",
        ))
    fig.update_yaxes(visible=False, range=[0, prices[["brand", "alt"]].max().max() * 1.18])
    fig.update_xaxes(tickfont=dict(color=_c()['text'], size=12.5), ticks="", showline=False)
    fig.update_layout(
        barmode="group", bargap=0.35, bargroupgap=0.08, height=300, margin=dict(l=0, r=0, t=10, b=10),
        dragmode=False, showlegend=False, hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, sans-serif", color=_c()['text']),
    )
    return fig


def _short(v: float, usd: bool) -> str:
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{'$' if usd else ''}{v / div:,.1f}{suffix}"
    return f"{'$' if usd else ''}{v:,.0f}"


def _clip(name: str, n: int = 34) -> str:
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


def rank_bars(rows: pd.DataFrame, measure: str, all_categories: bool) -> go.Figure:
    """Top-N ranked horizontal bars for one measure ('volume' or 'spending')."""
    volume = measure == "volume"
    field = "prescriptions" if volume else "medicaid_amount"
    share = "share_of_category_prescriptions" if volume else "share_of_category_spending"
    split = "product_type_split_prescriptions" if volume else "product_type_split_spending"
    share_label = ("Share of all Rx" if volume else "Share of all spending") if all_categories else \
        ("Share of category Rx" if volume else "Share of category spending")

    rows = rows.sort_values("rank")
    ylab = [f"{r.rank}. {_clip(r.ingredient)}" for r in rows.itertuples()]

    # Tooltips are HTML (lib/tooltips.py), laid out like the original dashboard's.
    def tip(r) -> str:
        split_text = getattr(r, split)
        return tooltips.spec(
            f"{r.rank}. {r.ingredient}",
            r.therapeutic_class.capitalize() if isinstance(r.therapeutic_class, str) else "",
            [("Prescriptions", f"{r.prescriptions:,.0f}"),
             ("Medicaid spending", f"${r.medicaid_amount:,.0f}"),
             ("Cost per Rx", "—" if pd.isna(r.cost_per_prescription) else f"${r.cost_per_prescription:,.2f}"),
             (share_label, f"{getattr(r, share):.1%}")],
            ("By category: " + split_text.replace("; ", " · ")) if all_categories and isinstance(split_text, str) else "",
        )

    hover = [tip(r) for r in rows.itertuples()]
    fig = go.Figure(go.Bar(
        x=rows[field], y=ylab, orientation="h",
        marker=dict(color=RX_COLOR if volume else SPEND_COLOR, cornerradius=4),
        text=[_short(v, not volume) for v in rows[field]], textposition="outside", cliponaxis=False,
        textfont=dict(color=_c()['muted'], size=12),
        customdata=hover, hoverinfo="none",
    ))
    # Plotly right-aligns tick labels, so the names are left-aligned annotations
    # over the left 44% of the plot. The plot spans the full width, with that
    # left part as a negative stretch of the x axis, so hovering anywhere on a
    # row (name or bar) hits that row: hovermode "y" with no distance limit.
    x_max = rows[field].max() * 1.24
    x_min = -x_max * 0.44 / 0.56
    for label, r in zip(ylab, rows.itertuples()):
        fig.add_annotation(
            x=0, xref="paper", xanchor="left", y=label, yref="y", showarrow=False, align="left",
            text=f"<span style='color:{_c()['muted']}'>{r.rank}.</span> {_clip(r.ingredient, 26)}",
            font=dict(color=_c()['text'], size=12.5),
        )
    fig.update_yaxes(autorange="reversed", showgrid=False, ticks="", showticklabels=False)
    fig.update_xaxes(visible=False, range=[x_min, x_max])
    fig.update_layout(
        height=26 * len(rows) + 30, margin=dict(l=0, r=16, t=4, b=4), bargap=0.38,
        hovermode="y", hoverdistance=-1,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", dragmode=False, showlegend=False,
        font=dict(family="Inter, sans-serif", color=_c()['text']),
        hoverlabel=dict(bgcolor=_c()['raised'], bordercolor=_c()['tip_line'], font=dict(color=_c()['text'], size=13), align="left"),
    )
    return fig
