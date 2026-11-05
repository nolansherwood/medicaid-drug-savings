"""Rankings: category summary and the top 25 drugs by prescriptions and by
spending, nationally or for one state. Clicking a bar opens the products that
sell that ingredient, with a link to Explore Alternatives."""
import pandas as pd
import streamlit as st

from lib import charts, data, tooltips, ui

ALL = "All product types"
CATEGORY_NOTES = {
    ALL: "Every drug, all categories combined. Hover a bar to see how its use splits across brand, generic and "
         "biosimilar versions.",
    "Brand, single source": "Brand-name drugs (FDA NDA) with no generic of the same strength and form dispensed to "
                            "Medicaid in 2025. Some have FDA-approved generics that are not yet on the market.",
    "Brand, generic competition": "Brand-name drugs (FDA NDA) where a generic of the same strength and form was "
                                  "dispensed to Medicaid in 2025. This is based on what was dispensed, not the "
                                  "category manufacturers report to CMS, which is often out of date.",
    "Authorized generic": "The brand manufacturer’s own drug, sold under a generic label.",
    "Generic": "Generic copies approved by FDA ANDA.",
    "Reference biologic": "Original biologics (FDA BLA 351(a)), including insulins and other products moved to "
                          "biologic status in 2020.",
    "Biosimilar": "Biosimilars (FDA 351(k)) not designated interchangeable.",
    "Interchangeable biosimilar": "Biosimilars FDA has designated interchangeable, so pharmacies may substitute "
                                  "them where state law allows.",
    "OTC / unapproved / unclassified": "Over-the-counter monograph products, unapproved drugs, and products with "
                                       "no FDA or CMS record.",
}

st.session_state.setdefault("geo", data.NATIONAL)
st.session_state.setdefault("rank_cat", ALL)
st.session_state.setdefault("bar_n", 0)          # bumps to clear a chart's click selection


def _set_geo():
    st.session_state.geo = st.session_state.rank_geo


def _set_cat_pill():
    st.session_state.rank_cat = st.session_state.rank_cat_pills or ALL


def _set_cat_table(key, cats):
    rows = st.session_state[key].selection.rows
    if rows:
        st.session_state.rank_cat = cats[rows[0]]
        st.session_state.rank_cat_pills = st.session_state.rank_cat


def _bar_click(key, ingredients, classes):
    pts = st.session_state[key].selection.get("points", [])
    if pts:
        i = pts[0]["point_index"]
        st.session_state.open_ingredient = (ingredients[i], classes[i])
        st.session_state.bar_n += 1


geo = st.session_state.geo
geo_name = data.STATE_NAMES.get(geo, geo)
national = geo == data.NATIONAL

# ---- header --------------------------------------------------------------------
ui.page_header("Rankings", f"The most-used and highest-spending drugs in Medicaid, {data.YEAR}, "
                           f"{'nationally' if national else 'in ' + geo_name}, by drug category.",
               lambda: ui.state_picker("rank_geo", geo, _set_geo))
if not national:
    st.caption(
        "State figures come from that state’s own rows. CMS hides any row with fewer than 11 prescriptions, so "
        "rare, high-cost drugs are undercounted at the state level, and a state’s numbers do not add up to its "
        "full Medicaid total."
    )

# ---- KPI tiles and the prescriptions vs. spending card ----------------------------
cs = data.category_summary()
rows = cs[cs.state == geo]
rx_total, spend_total = rows.prescriptions.sum(), rows.medicaid_amount.sum()
mix = pd.DataFrame([
    {"group": name, "types": " · ".join(types),
     "prescriptions": rows[rows.product_type.isin(types)].prescriptions.sum(),
     "spending": rows[rows.product_type.isin(types)].medicaid_amount.sum()}
    for name, types in data.MIX_GROUPS
])
mix["rx_share"], mix["spend_share"] = mix.prescriptions / rx_total, mix.spending / spend_total
brand_rx, brand_spend = mix.head(2).rx_share.sum(), mix.head(2).spend_share.sum()

# Totals stacked on the left; the wide card on the right shows where they go.
totals_col, mix_col = st.container(key="kpi-row").columns([1, 3], gap="medium")
with totals_col:
    with st.container(border=True, key="card-kpi-payments"):
        ui.kpi("payments", "#4C8DF6", "Drug spending", ui.usd_compact(spend_total), "Before rebates")
    with st.container(border=True, key="card-kpi-prescriptions"):
        ui.kpi("prescriptions", "#F59E5B", "Prescriptions", f"{rx_total / 1e6:,.1f}M", "Paid claims")
with mix_col, st.container(border=True, key="card-kpi-mix"):
    ui.card_title("compare_arrows", "Prescriptions vs. spending", "#F0A37F")
    st.markdown(
        f'<div class="mr-mix-head">Brand-name drugs and biologics were <b>{brand_rx:.1%}</b> of prescriptions '
        f"but <b>{brand_spend:.1%}</b> of spending.</div>"
        + '<div class="mr-legend">' + "".join(
            f'<span><i style="background:{c}"></i>{name}</span>'
            for (name, _), c in zip(data.MIX_GROUPS, charts.MIX_COLORS)) + "</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(charts.mix_bars(mix), key=f"mix_{geo}", config={"displayModeBar": False})

# ---- category table --------------------------------------------------------------
with st.container(border=True, key="card-categories"):
    st.markdown('<div class="mr-section">Drug categories</div>', unsafe_allow_html=True)
    st.caption(
        "Categories follow the FDA approval pathway (brand NDA, generic ANDA, biologic BLA, biosimilar 351(k)). "
        f"Select a row to rank its drugs below. {'National' if national else geo_name} totals, all of {data.YEAR}."
    )
    tbl = rows[["product_type", "prescriptions", "share_prescriptions", "medicaid_amount",
                "share_spending", "cost_per_prescription"]].reset_index(drop=True)
    tbl["medicaid_amount"] = tbl.medicaid_amount.map(ui.usd_compact)   # 8 rows: "$55.2B" reads better
    cats = list(tbl.product_type)
    if st.session_state.rank_cat not in cats:      # e.g. a category this state has no rows for
        st.session_state.rank_cat = ALL
        st.session_state.pop("rank_cat_pills", None)
    key = f"cat_table_{geo}_{st.session_state.rank_cat}"
    st.dataframe(
        tbl, key=key, hide_index=True, width="stretch", on_select=lambda: _set_cat_table(key, cats),
        selection_mode="single-row",
        column_config={
            "product_type": "Category",
            "prescriptions": st.column_config.NumberColumn("Prescriptions", format="localized"),
            "share_prescriptions": st.column_config.ProgressColumn(
                "Share of Rx", format="percent", min_value=0, max_value=1.0),
            "medicaid_amount": "Medicaid spending",
            "share_spending": st.column_config.ProgressColumn(
                "Share of spending", format="percent", min_value=0, max_value=1.0),
            "cost_per_prescription": st.column_config.NumberColumn("Cost per Rx", format="dollar"),
        },
    )

# ---- top 25 charts ---------------------------------------------------------------
cat = st.session_state.rank_cat
where = "" if national else f" in {geo_name}"
with st.container(border=True, key="card-rankings"):
    st.markdown(
        f'<div class="mr-section">Top 25 drugs{where}{", all categories" if cat == ALL else ": " + cat}</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"{CATEGORY_NOTES.get(cat, '')} Drugs are ranked by active ingredient, summing every package of that "
        "ingredient within the category. Click a drug to see the products that sell it."
    )
    st.session_state.setdefault("rank_cat_pills", cat)
    st.pills("Category", [ALL] + cats, key="rank_cat_pills", on_change=_set_cat_pill,
             format_func=lambda c: "All drugs" if c == ALL else c, label_visibility="collapsed")

    rk = data.rankings()
    rk = rk[(rk.state == geo) & (rk.category == cat)]
    left, right = st.columns(2, gap="large")
    for col, measure, heading, note in (
        (left, "volume", "By prescriptions", "Paid prescriptions. Each fill or refill counts once."),
        (right, "spending", "By Medicaid spending",
         "Amount reimbursed to pharmacies, before rebates."),
    ):
        with col:
            st.markdown(f'<div class="mr-subsection">{heading}</div>', unsafe_allow_html=True)
            st.caption(note)
            r = rk[rk.measure == measure].sort_values("rank")
            if r.empty:
                st.info("No drugs with a mapped ingredient in this category.")
                continue
            k = f"bars_{measure}_{st.session_state.bar_n}"
            st.plotly_chart(
                charts.rank_bars(r, measure, cat == ALL), key=k, selection_mode="points",
                on_select=lambda k=k, ing=list(r.ingredient), cl=list(r.therapeutic_class): _bar_click(k, ing, cl),
                config={"displayModeBar": False},
            )

# Last on the page, so its (hidden) frame adds no gap above the header.
tooltips.install()

with st.expander("How to read these numbers"):
    st.markdown(
        "- Spending is before manufacturer rebates, so it overstates Medicaid's net cost, especially for brand drugs.\n"
        "- Reimbursement includes pharmacy dispensing fees.\n"
        "- “Generic competition” means a generic of the same strength and form was dispensed to Medicaid in 2025 "
        "(national data). Manufacturers’ own CMS categories often disagree, so they are not used. A brand can appear "
        "in both brand categories when only some strengths have generics.\n"
        "- Prescription counts are not adjusted for days supply: a 90-day fill counts the same as a 30-day fill.\n"
        "- Source: CMS State Drug Utilization Data 2025 (national totals), FDA NDC Directory, Purple Book and "
        "Medicaid Drug Rebate Program product data; ingredients from NLM RxNorm."
    )


# ---- products dialog -------------------------------------------------------------
@st.dialog("Products", width="large")
def products_dialog(ingredient: str, therapeutic_class):
    st.subheader(ingredient)
    cls = therapeutic_class.capitalize() if isinstance(therapeutic_class, str) else ""
    st.caption(" · ".join(x for x in (cls, f"{'National' if national else geo_name}, {data.YEAR}") if x))

    p = data.products(geo)
    p = p[p.ingredient == ingredient].sort_values("medicaid_amount", ascending=False).reset_index(drop=True)
    st.markdown(
        f"{len(p)} product{'s' if len(p) != 1 else ''} · "
        f"{p.prescriptions.sum():,.0f} prescriptions · ${p.medicaid_amount.sum():,.0f} Medicaid spending"
    )
    ndcs = data.product_ndcs()
    ndc_lists = [ndcs.get((r.product, r.labeler, r.product_type), []) if pd.notna(r.product_type) else []
                 for r in p.itertuples()]
    # Figures right after the name, so they are easy to sort by and stay visible on a narrow screen.
    view = pd.DataFrame({
        "Product": p["product"],
        "Prescriptions": p.prescriptions,
        "Medicaid spending ($)": p.medicaid_amount.round(),
        "Cost per Rx": p.cost_per_prescription,
        "Manufacturer": p.labeler.fillna(""),
        "NDCs": [(l[0] + (f" +{len(l) - 1} more" if len(l) > 1 else "")) if l else "" for l in ndc_lists],
        "Category": p.product_type.fillna(""),
        "Form": p.forms.fillna(""),
        "Cost per unit": [("—" if pd.isna(r.cost_per_unit) else f"${r.cost_per_unit:,.4f}".rstrip("0").rstrip(".")
                           + f" / {str(r.unit_type).lower()}") for r in p.itertuples()],
    })
    # Bold the rows in the category being viewed, as the old popup did.
    styled = view.style.apply(
        lambda row: ["font-weight: 700" if cat != ALL and row["Category"] == cat else ""] * len(row), axis=1)
    sel = st.dataframe(
        styled, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={
            "Prescriptions": st.column_config.NumberColumn(format="localized"),
            "Medicaid spending ($)": st.column_config.NumberColumn(format="localized"),
            "Cost per Rx": st.column_config.NumberColumn(format="dollar"),
        },
    )
    rows_sel = sel.selection.rows
    if not rows_sel:
        st.caption("Select a product to see its FDA-approved use, all its NDCs and a link to its alternatives.")
    else:
        r = p.iloc[rows_sel[0]]
        rec = data.indications().get((r["product"], r.labeler))
        with st.container(border=True):
            st.markdown(f"**{r['product']}** · {r.labeler}")
            if rec and isinstance(rec.get("indication"), str):
                eff = str(rec.get("effective_time") or "")
                date = f"{eff[:4]}-{eff[4:6]}-{eff[6:8]}" if len(eff) == 8 else eff
                st.markdown(f"**FDA-approved use.** {rec['indication']}")
                st.caption("FDA label" + (f": {rec['label_brand_name']}" if isinstance(rec.get('label_brand_name'), str)
                                          else "") + (f" · revised {date}" if date else ""))
            lst = ndc_lists[rows_sel[0]]
            if lst:
                st.caption(f"NDCs ({len(lst)}): " + ", ".join(lst))
            pid = data.explore_pid(r["product"], r.labeler, r.product_type)
            if pid is not None and st.button(f"Explore alternatives to {r['product']}", type="primary"):
                st.session_state.explore_pid = pid
                st.switch_page("views/explore.py")
    st.caption(
        "Each product combines all strengths and package sizes sold under that name by that manufacturer; NDCs are "
        "listed nationally, highest Medicaid spending first. Cost per unit is per billing unit (tablet, capsule, mL, "
        "each), so it is only comparable between products of the same strength and form. Bold rows are in the "
        "category you are viewing. Spending is before manufacturer rebates."
    )


if "open_ingredient" in st.session_state:
    products_dialog(*st.session_state.pop("open_ingredient"))
