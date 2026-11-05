"""Savings Opportunity Overview: estimated savings if matched brands had been
filled with their generic or biosimilar, nationally or for one state, by switch
type. Figures come straight from savings_summary and savings_top."""
import pandas as pd
import streamlit as st

from lib import data, ui


def esc(text: str) -> str:
    """Escape "$" so Streamlit markdown doesn't read it as math."""
    return str(text).replace("$", "\\$")

TIER_LABEL = {
    "all": "All switches",
    "generic": "Brand to generic",
    "interchangeable_biosimilar": "To interchangeable biosimilar",
    "biosimilar": "To biosimilar (new prescription)",
}
TIER_SHORT = {"generic": "Generic", "interchangeable_biosimilar": "Interch. biosim.",
              "biosimilar": "Biosimilar"}

st.session_state.setdefault("geo", data.NATIONAL)
st.session_state.setdefault("sav_tier", "all")


def _set_geo():
    st.session_state.geo = st.session_state.sav_geo


def _set_tier():
    st.session_state.sav_tier = st.session_state.sav_tier_pills or "all"


geo = st.session_state.geo
geo_name = data.STATE_NAMES.get(geo, geo)
national = geo == data.NATIONAL
where = "nationally" if national else f"in {'the ' if geo == 'DC' else ''}{geo_name}"

# ---- header ----------------------------------------------------------------------
ui.page_header(
    "Savings Opportunity",
    f"Estimated {data.YEAR} savings if prescriptions for brand-name drugs and reference biologics had been "
    f"filled with their matched generic or biosimilar, {where}. Matches require the same ingredient, strength "
    "and form, and an FDA substitutability rating for generics.",
    lambda: ui.state_picker("sav_geo", geo, _set_geo),
)

st.session_state.setdefault("sav_tier_pills", st.session_state.sav_tier)
st.pills("Switch type", list(TIER_LABEL), format_func=TIER_LABEL.get, key="sav_tier_pills",
         on_change=_set_tier, label_visibility="collapsed")
tier = st.session_state.sav_tier

# ---- KPI tiles -------------------------------------------------------------------
row = data.savings_for(geo, tier)
kpis = [
    ("payments", "#4C8DF6", "Brand spending", ui.usd_compact(row.brand_spending),
     "Matched to an alternative"),
    ("savings", "#4ADE80", "Gross savings", ui.usd_compact(row.gross_savings), "Upper bound, before rebates"),
    ("percent", "#F59E5B", "After min. rebates", ui.usd_compact(row.min_rebate_savings),
     "Statutory floor rebates"),
    ("account_balance", "#C084FC", "After avg. rebates", ui.usd_compact(row.avg_rebate_savings),
     "Brand rebates at 61.9%"),
]
for col, (icon, color, label, value, sub) in zip(st.container(key="kpi-row").columns(4, gap="medium"), kpis):
    with col, st.container(border=True, key=f"card-kpi-{icon}"):
        ui.kpi(icon, color, label, value, sub)
        if icon == "account_balance":
            st.page_link("views/rebates.py", label="How rebates are estimated ›")

# The old callout: how many matches, how many reverse after rebates, brand-preferred lists.
n, rev = int(row.groups), int(row.groups_reverse_after_rebates)
# Counted per strength and form (a brand with three strengths counts three times).
parts = [f"{n:,} brand strengths and forms have a cheaper matched alternative."]
if rev > 0:
    parts.append(f"{rev:,} of them would cost Medicaid more after average rebates, so the brand may already be the "
                 "better buy.")
if not national and row.groups_brand_preferred_here > 0:
    parts.append(f"{int(row.groups_brand_preferred_here):,} are on {geo_name}'s brand-preferred list "
                 f"({ui.usd_compact(row.gross_savings_brand_preferred_here)} of the gross savings): the state has "
                 "already judged the brand cheaper after rebates.")
st.markdown(f'<div class="mr-callout">{" ".join(parts)}</div>', unsafe_allow_html=True)

# ---- top 25 brands ----------------------------------------------------------------
# One row per brand: every strength and form with a matched alternative, added up
# (src.export_dashboard.brand_rollup). The strengths are in the row detail.
top = data.savings_brands()
top = top[(top.geo == geo) & (top.match_level == "primary") & (top.tier_view == tier)]
top = top.sort_values("rank").reset_index(drop=True)
members = data.savings_brand_groups()
members = members[(members.geo == geo) & (members.match_level == "primary") & (members.tier_view == tier)]
ndcs = data.savings_ndcs()


def _unit(v: float) -> str:
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def _states(v) -> str:
    return v.replace(",", ", ") if isinstance(v, str) and v else ""


def _preferred(r) -> str:
    if r.groups_brand_preferred_here:
        return geo
    return _states(r.brand_preferred_states) if national else ""


def _costs_more(r) -> str:
    n = int(r.groups_reverse_after_rebates or 0)
    return "" if not n else "All" if n == r.groups else f"{n} of {r.groups}"


def _switch(tiers: str) -> str:
    return " / ".join(TIER_SHORT[t] for t in tiers.split(","))


with st.container(border=True, key="card-savings-top"):
    st.markdown(
        f'<div class="mr-section">Top 25 brands by savings{"" if national else " in " + geo_name}: '
        f"{TIER_LABEL[tier].lower()}</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Ranked by gross savings, adding up every strength and form of the brand that has a matched alternative. "
        '"On alt." is how much of what was dispensed at these strengths and forms was already the '
        'generic or biosimilar rather than the brand (by units). "Brand preferred in" lists the states whose '
        'brand-preferred list includes the brand (the state found it cheaper after rebates), and "Costs more" counts the strengths where switching would cost '
        "more after average rebates. Select a row to see each strength and form."
    )
    if top.empty:
        st.info("No matched opportunities for this selection.")
    else:
        view = pd.DataFrame({
            "Brand": top.brand,
            "Strengths": top.groups,
            "Spending": top.brand_spending.map(ui.usd_compact),
            "Switch to": top.tiers.map(_switch),
            "On alt.": top.alt_share_of_units,
            "Gross savings ($)": top.gross_savings,
            "After min.": top.min_rebate_savings.map(ui.usd_compact),
            "After avg.": top.avg_rebate_savings.map(ui.usd_compact),
            "Brand preferred in": [_preferred(r) for r in top.itertuples()],
            "Costs more": [_costs_more(r) for r in top.itertuples()],
        })
        sel = st.dataframe(
            view.style.map(ui.gain_loss_style, subset=["After min.", "After avg."])
            .map(lambda _: f"color: {ui.good()}", subset=["Gross savings ($)"]), hide_index=True, width="stretch", height=35 * (len(view) + 1) + 3,
            on_select="rerun", selection_mode="single-row", key=f"sav_table_{geo}_{tier}",
            column_config={
                "Strengths": st.column_config.NumberColumn(width="small"),
                "Gross savings ($)": st.column_config.ProgressColumn(
                    width=125, format="compact", min_value=0, max_value=float(view["Gross savings ($)"].max())),
                "Switch to": st.column_config.TextColumn(width=112),
                "On alt.": st.column_config.NumberColumn(
                    format="percent", help="Of all units dispensed at these strengths and forms (brand plus "
                                           "alternatives), the share that were the generic or biosimilar"),
                "Brand preferred in": st.column_config.TextColumn(
                    width=175,  # fits all six listed states
                    help="State brand-preferred lists that include this brand (the state found it cheaper after rebates)"),
                "Costs more": st.column_config.TextColumn(
                    width=85,
                    help="Strengths where switching would cost Medicaid more after average rebates"),
            },
        )
        if sel.selection.rows:
            r = top.iloc[sel.selection.rows[0]]
            rows = members[members.brand_key == r.brand_key].sort_values("gross_savings", ascending=False)
            with st.container(border=True):
                st.markdown(f"**{r.brand}** ({r.ingredient}): {len(rows)} strength"
                            f"{'' if len(rows) == 1 else 's'} and forms with a cheaper matched alternative")
                # Figures first, so a narrow screen cuts off the long name lists.
                detail = pd.DataFrame({
                    "Strength and form": rows.group_name,
                    "Gross savings": rows.gross_savings.map(ui.usd_compact),
                    "After min. rebates": rows.min_rebate_savings.map(ui.usd_compact),
                    "After avg. rebates": rows.avg_rebate_savings.map(ui.usd_compact),
                    "Brand per unit": "$" + rows.brand_cost_per_unit.map(_unit),
                    "Alt. per unit": "$" + rows.alt_cost_per_unit.map(_unit),
                    "Switch to": rows.alternative_names.where(rows.alternative_names.notna(), rows.tier.map(TIER_SHORT)),
                    "Price benchmark": rows.benchmark_source,
                }).reset_index(drop=True)
                st.dataframe(
                    detail.style.map(ui.gain_loss_style,
                                     subset=["Gross savings", "After min. rebates", "After avg. rebates"]),
                    hide_index=True, width="stretch", height=35 * (len(detail) + 1) + 3,
                    column_config={"Strength and form": st.column_config.TextColumn(width="large")},
                )
                with st.expander("NDCs for each strength and form"):
                    for m in rows.itertuples():
                        b, a = ndcs.get(("primary", m.tier, m.group_key), ([], []))
                        st.caption(f"**{esc(m.group_name)}**  \nBrand ({len(b)}): {', '.join(b) or '—'}  \n"
                                   f"Alternative ({len(a)}): {', '.join(a) or '—'}")
                pid = data.explore_pid_by_name(r.brand, r.ingredient)
                if pid is not None and st.button(f"Explore alternatives to {r.brand}", type="primary"):
                    st.session_state.explore_pid = pid
                    st.switch_page("views/explore.py")

with st.expander("How the savings are estimated"):
    st.markdown(
        "- Brands match generics with the same RxNorm clinical drug (ingredient, strength and dose form) and an FDA "
        '"A" therapeutic-equivalence rating; authorized generics always match. Reference biologics match biosimilars '
        "with the same concentration, volume and device.\n"
        "- The alternative's price is the volume-weighted average Medicaid paid per unit for the matched products in "
        "the same state (national when a state has too little volume).\n"
        "- Minimum rebates use the statutory floor (23.1% of price for brands, 17.1% for clotting factors and "
        "pediatric drugs, 13% for generics). Average rebates use 61.9% for brands, calibrated to MACPAC's FY 2024 "
        "totals ($58.4B rebates on $106.4B gross); authorized generics and biosimilars stay at 23.1%. Rebates are "
        "applied to Medicaid paid amounts, and confidential state supplemental rebates are not included.\n"
        "- The totals at the top count only the strengths and forms where switching saves money in that scenario. "
        "The brand table adds up every strength of a brand, so its after-rebates figures can be negative "
        "(switching that brand would cost Medicaid more).\n"
        "- Brand-preferred lists are compiled for SC, IA, MD, NH, OH and NY (2026 lists). Other states may have "
        "lists that are not yet included.\n"
        "- Interchangeable biosimilars can be substituted by the pharmacy where state law allows. Other biosimilars "
        "need a new prescription."
    )
