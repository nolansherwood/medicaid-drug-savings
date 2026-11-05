"""Rebates: the public rebate totals behind the savings estimates (MACPAC FY 2024,
D-016) and how rebates change the price comparison between a brand and its
alternatives. MACPAC's state rows are not shown: a single state's year is
distorted by rebate timing (see the caveat on the page)."""
import pandas as pd
import streamlit as st

from lib import charts, data, tooltips, ui

st.session_state.setdefault("geo", data.NATIONAL)


def _set_geo():
    st.session_state.geo = st.session_state.reb_geo


geo = st.session_state.geo
geo_name = data.STATE_NAMES.get(geo, geo)
national = geo == data.NATIONAL

ui.page_header(
    "Rebates",
    "Manufacturers pay Medicaid rebates after a prescription is filled, and the amount for each drug is "
    "confidential. This page shows the public totals the savings estimates are built on (MACPAC, fiscal year "
    "2024) and how rebates change the price gap between a brand and its generic or biosimilar.",
    lambda: ui.state_picker("reb_geo", geo, _set_geo),
)

rates = data.rebate_rates()
us = rates[rates.state == data.NATIONAL].iloc[0]
AVG, MIN_BRAND, MIN_GENERIC = float(us.brand_rate_used), 0.231, float(us.generic_rate_used)

# ---- KPI tiles: the national calibration (the rate used in every state) ----------------
kpis = [
    ("payments", "#4C8DF6", "Gross drug spending", ui.usd_compact(us.gross_millions * 1e6),
     "FY 2024, national, before rebates"),
    ("account_balance", "#C084FC", "Rebates collected", ui.usd_compact(us.rebates_millions * 1e6),
     f"{us.rebate_share_of_gross:.0%} of gross spending"),
    ("workspace_premium", "#D95926", "Brand rebate (est.)", f"{AVG:.1%}",
     "National rate used in every state"),
    ("verified", "#3987E5", "Generic rebate", f"{MIN_GENERIC:.0%}", "Statutory minimum, assumed"),
]
for col, (icon, color, label, value, sub) in zip(st.container(key="kpi-row").columns(4, gap="medium"), kpis):
    with col, st.container(border=True, key=f"card-kpi-reb-{icon}"):
        ui.kpi(icon, color, label, value, sub)

# ---- how the brand rate is estimated -------------------------------------------------
gross, rebates = us.gross_millions * 1e6, us.rebates_millions * 1e6
brand_gross, generic_gross = gross * us.brand_share_of_gross, gross * us.generic_share_of_gross
generic_rebates = generic_gross * MIN_GENERIC
with st.container(border=True, key="card-reb-calc"):
    st.markdown('<div class="mr-section">How the average brand rebate is estimated</div>', unsafe_allow_html=True)
    st.caption(
        "MACPAC publishes each state's total rebates and the brand and generic shares of spending, but not rebates "
        "per drug. Generics are assumed to get the statutory minimum (13%); whatever is left is credited to brands."
    )
    steps = [
        ("Gross Medicaid drug spending", ui.usd_compact(gross), "MACPAC FY 2024, all states"),
        ("Rebates collected", ui.usd_compact(rebates), f"{us.rebate_share_of_gross:.1%} of gross"),
        ("Generic spending × 13%", f"− {ui.usd_compact(generic_rebates)}",
         f"{ui.usd_compact(generic_gross)} of generic spending ({us.generic_share_of_gross:.1%} of gross)"),
        ("Left for brands", ui.usd_compact(rebates - generic_rebates), ""),
        ("÷ brand spending", ui.usd_compact(brand_gross), f"{us.brand_share_of_gross:.1%} of gross"),
        ("Average brand rebate", f"{AVG:.1%}", "used for the “after average rebates” estimates"),
    ]
    st.markdown(
        '<div class="mr-steps">' + "".join(
            f'<div class="mr-step{" mr-step-total" if i == len(steps) - 1 else ""}"><span>{a}</span>'
            f"<b>{b}</b><em>{c}</em></div>" for i, (a, b, c) in enumerate(steps)) + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="mr-callout mr-callout-warn">MACPAC counts rebates in the year they are received, not the year '
        "the prescriptions were filled. Rebates arrive a quarter or more after the claims, and manufacturers' price "
        "revisions bring payments for earlier years, so FY 2024 rebates include money for earlier prescriptions. "
        "Nationally these timing effects largely even out, which is why one national rate is used for every state. "
        "A single state's year can be far off: some states collected more in rebates than they spent.</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Rebate rates are a share of the manufacturer's price (AMP), but they are applied here to what Medicaid paid "
        "pharmacies, which also includes dispensing fees, so rebates are somewhat overstated. Confidential state "
        "supplemental rebates and inflation rebates on generics are not modeled."
    )

# ---- break-even rebate across the top brands ------------------------------------------
tooltips.install()
brands = data.savings_brands()
brands = brands[(brands.geo == geo) & (brands.match_level == "primary") & (brands.tier_view == "all")]
members = data.savings_brand_groups()
members = members[(members.geo == geo) & (members.match_level == "primary") & (members.tier_view == "all")]
# Break-even: the brand rebate at which the brand costs the same as its alternatives
# after their own (average-scenario) rebates, over all the brand's strengths.
m = members.assign(alt_net=(members.brand_spending - members.gross_savings) * (1 - members.alt_avg_rebate_rate))
be = m.groupby("brand_key").agg(alt_net=("alt_net", "sum"), spend=("brand_spending", "sum"))
brands = brands.assign(breakeven=brands.brand_key.map(1 - be.alt_net / be.spend))

with st.container(border=True, key="card-reb-breakeven"):
    st.markdown(f'<div class="mr-section">Break-even rebate, top 25 brands by savings'
                f'{"" if national else " in " + geo_name}</div>', unsafe_allow_html=True)
    st.caption(
        "The rebate a brand would need for Medicaid to pay the same as for its generic or biosimilar after that "
        "alternative's own rebate. If a brand's real rebate is above its break-even point, the brand is the cheaper "
        "option, which is why some states put brands on preferred lists."
    )
    if brands.empty:
        st.info("No matched opportunities for this selection.")
    else:
        n_above = int((brands.breakeven > AVG).sum())
        st.markdown(
            '<div class="mr-legend">'
            f'<span><i style="background:{charts.ALT_COLOR}"></i>Break-even above the average: switching saves '
            f"({n_above})</span>"
            f'<span><i style="background:{charts.BRAND_COLOR}"></i>Below the average: the brand may be cheaper '
            f"({len(brands) - n_above})</span></div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(charts.breakeven_dots(brands, AVG, MIN_BRAND), key=f"be_{geo}",
                        config={"displayModeBar": False})

# ---- price comparison for one brand ----------------------------------------------------
with st.container(border=True, key="card-reb-prices"):
    st.markdown('<div class="mr-section">Price comparison before and after rebates</div>', unsafe_allow_html=True)
    if brands.empty:
        st.info("No matched opportunities for this selection.")
    else:
        names = brands.sort_values("rank").brand_key.tolist()
        label = dict(zip(brands.brand_key, brands.brand))
        if st.session_state.get("reb_brand") not in names:
            st.session_state.reb_brand = names[0]
        pick_b, pick_s = st.columns(2, gap="medium")
        with pick_b:
            st.selectbox("Brand", names, format_func=label.get, key="reb_brand")
        rows = members[members.brand_key == st.session_state.reb_brand].sort_values("gross_savings", ascending=False)
        groups = rows.group_key.tolist()
        group_label = dict(zip(rows.group_key, rows.group_name))
        if st.session_state.get("reb_group") not in groups:
            st.session_state.reb_group = groups[0]
        with pick_s:
            st.selectbox("Strength and form", groups, format_func=group_label.get, key="reb_group")
        r = rows[rows.group_key == st.session_state.reb_group].iloc[0]

        prices = pd.DataFrame({
            "scenario": ["Before rebates", "After minimum rebates", "After average rebates"],
            "brand": [r.brand_cost_per_unit, r.brand_cost_per_unit * (1 - r.brand_min_rebate_rate),
                      r.brand_cost_per_unit * (1 - r.brand_avg_rebate_rate)],
            "alt": [r.alt_cost_per_unit, r.alt_cost_per_unit * (1 - r.alt_min_rebate_rate),
                    r.alt_cost_per_unit * (1 - r.alt_avg_rebate_rate)],
        })
        breakeven = 1 - r.alt_cost_per_unit * (1 - r.alt_avg_rebate_rate) / r.brand_cost_per_unit
        alt_names = r.alternative_names if isinstance(r.alternative_names, str) else "the alternatives"
        net_b, net_a = prices.brand.iloc[2], prices.alt.iloc[2]
        verdict = (f"After average rebates the brand still costs {net_b / net_a:,.1f}× the alternative per unit."
                   if net_b > net_a else
                   f"After average rebates the brand costs {1 - net_b / net_a:.0%} less per unit than the alternative.")
        st.markdown(
            '<div class="mr-legend">'
            f'<span><i style="background:{charts.BRAND_COLOR}"></i>{label[r.brand_key]}</span>'
            f'<span><i style="background:{charts.ALT_COLOR}"></i>{alt_names}</span></div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(charts.net_price_bars(prices), key=f"np_{geo}_{r.group_key}",
                        config={"displayModeBar": False})
        st.markdown(f'<div class="mr-callout">{verdict} Break-even brand rebate for this strength: '
                    f"<b>{breakeven:.1%}</b> (average used: {AVG:.1%}).</div>", unsafe_allow_html=True)
        st.caption(
            f"Price per billing unit (tablet, mL, pen and so on), {'national' if national else geo_name}, "
            f"{data.YEAR}. Rebates used: brand {r.brand_min_rebate_rate:.1%} minimum and "
            f"{r.brand_avg_rebate_rate:.1%} average; alternative {r.alt_min_rebate_rate:.1%} in both. Price "
            f"benchmark: {r.benchmark_source}."
        )
