"""Explore Alternatives: pick a product to see the generics, authorized generics
or biosimilars sold at the same strength and form (RxNorm clinical drug + billing
unit), what each cost Medicaid per unit, and, where none was dispensed, the
reason from FDA records (D-021). A port of the old dashboard's Explore tab."""
import html
import math

import pandas as pd
import streamlit as st

from lib import data, ui

ROLE_LABEL = {
    "brand": "Brand", "reference": "Reference biologic", "generic": "Generic (A-rated)",
    "generic_not_a_rated": "Generic (not A-rated)", "authorized_generic": "Authorized generic",
    "biosimilar": "Biosimilar", "interchangeable": "Interchangeable biosimilar",
}
ALT_FOR = {"brand": ("generic", "authorized_generic"), "reference": ("interchangeable", "biosimilar")}
# Rebate rates for the "after avg. rebates" comparison, as in the savings estimates (D-016):
# brands get the calibrated national average, alternatives their statutory minimum.
_us = data.rebate_rates().set_index("state").loc[data.NATIONAL]
AVG_REBATE = {"brand": float(_us.brand_rate_used), "reference": float(_us.brand_rate_used),
              "generic": float(_us.generic_rate_used), "generic_not_a_rated": float(_us.generic_rate_used),
              "authorized_generic": 0.231, "biosimilar": 0.231, "interchangeable": 0.231}
CARD_LIMIT = 12       # strengths/forms shown before "Show N more"
ROW_LIMIT = 15        # alternatives shown per strength/form
BEFORE, AFTER = "vs. this, before rebates", "vs. this, after avg. rebates (est.)"

st.session_state.setdefault("geo", data.NATIONAL)


def _set_geo():
    st.session_state.geo = st.session_state.ex_geo


def _pick(pid):
    st.session_state.explore_pid = pid
    st.session_state.ex_show_all = False
    st.session_state.explore_query = ""


def _query():
    st.session_state.explore_query = st.session_state.ex_query


geo = st.session_state.geo
national = geo == data.NATIONAL
geo_name = data.STATE_NAMES.get(geo, geo)
here = "nationally" if national else f"in {geo_name}"


# ---- formatting ----------------------------------------------------------------------
def esc(text: str) -> str:
    """Streamlit markdown reads text between two "$" as math; escape them (not needed in HTML blocks)."""
    return str(text).replace("$", "\\$")


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def usd2(v):
    return "—" if v is None else f"${v:,.2f}"


def unit_fmt(v, unit):
    if v is None:
        return "—"
    return f"${v:,.4f}".rstrip("0").rstrip(".") + f" / {str(unit or 'unit').lower()}"


def fmt_date(s):
    d = pd.to_datetime(s, errors="coerce") if isinstance(s, str) and s else None
    return "" if d is None or pd.isna(d) else d.strftime("%b %-d, %Y")


def ndc_text(ndcs, shown=3):
    lst = str(ndcs or "").split()
    if not lst:
        return "—"
    return ", ".join(lst[:shown]) + (f" +{len(lst) - shown} more" if len(lst) > shown else "")


# ---- state figures -------------------------------------------------------------------
sf = None if national else data.explore_state_figures(geo)


def figures(m: dict) -> dict:
    """One group member's figures in the selected geography (national price as fallback)."""
    nat_unit, nat_rx = num(m["cost_per_unit"]), num(m["cost_per_prescription"])
    if national:
        return dict(rx=num(m["prescriptions"]) or 0, amt=num(m["medicaid_amount"]) or 0, unit=nat_unit,
                    per_rx=nat_rx, unit_national=False, here=True)
    r = sf.get("g", {}).get(int(m["gid"]))
    if not r or not (num(r["rx"]) or 0) > 0:
        return dict(rx=0, amt=0, unit=nat_unit, per_rx=nat_rx, unit_national=True, here=False)
    units, rx, amt = num(r["units"]) or 0, num(r["rx"]), num(r["amt"]) or 0
    return dict(rx=rx, amt=amt, unit=amt / units if units > 0 else nat_unit, per_rx=amt / rx if rx > 0 else nat_rx,
                unit_national=not units > 0, here=True)


def reason(st_row: dict) -> list:
    """Why no alternative was dispensed, from the Orange Book / Purple Book fields."""
    s = st_row["status"]
    if s == "only_non_equivalent":
        lines = ['Generics of this strength and form were dispensed, but none is rated by the FDA as therapeutically '
                 'equivalent ("A"-rated), so none is an automatic substitute.']
    elif s == "other_strengths":
        lines = [f"No generic or biosimilar of this exact strength and form was dispensed in Medicaid in {data.YEAR}. "
                 "Equivalents exist for other strengths or forms of the same ingredient."]
    elif s == "approved_not_marketed":
        first = fmt_date(st_row.get("first_generic_approval"))
        lines = [f"The FDA has approved a generic for this form{f' (first approval {first})' if first else ''}, but "
                 f"none was dispensed in Medicaid in {data.YEAR}. Generic launches are often delayed by patents or "
                 "patent settlements."]
    elif s == "no_generic_approved":
        lines = ["No generic has been approved by the FDA for this form."]
    elif s == "biosimilar_not_dispensed":
        lic = st_row.get("biosimilars_licensed")
        lines = [f"The FDA has licensed biosimilars for this product{': ' + lic if isinstance(lic, str) else ''}. None "
                 f"matching this exact presentation was dispensed in Medicaid in {data.YEAR}."]
    elif s == "no_biosimilar_licensed":
        lines = ["No biosimilar has been licensed by the FDA for this product."]
    else:
        lines = ["This product's FDA application is not in the Orange Book, so the reason can't be determined."]
    exp = fmt_date(st_row.get("latest_patent_expiry"))
    if exp:
        n = num(st_row.get("n_patents"))
        asof = fmt_date(st_row.get("as_of"))
        count = (f" ({int(n):,} unexpired patent{'' if n == 1 else 's'}{' as of ' + asof if asof else ''})"
                 if n else "")
        lines.append(f"Listed patents expire as late as {exp}{count}. Listed patents can be challenged or settled, so "
                     "this is not a guaranteed launch date.")
    if isinstance(st_row.get("exclusivities"), str):
        asof = fmt_date(st_row.get("as_of"))
        lines.append(f"FDA exclusivity{' (unexpired as of ' + asof + ')' if asof else ''}: {st_row['exclusivities']}.")
    if fmt_date(st_row.get("ref_exclusivity")):
        lines.append(f"Reference-product exclusivity until {fmt_date(st_row['ref_exclusivity'])}.")
    if fmt_date(st_row.get("orphan_exclusivity")):
        lines.append(f"Orphan-drug exclusivity until {fmt_date(st_row['orphan_exclusivity'])}.")
    return lines


def callout(text: str, kind: str = "") -> None:
    st.markdown(f'<div class="mr-callout {kind}">{html.escape(text)}</div>', unsafe_allow_html=True)


# ---- one card per strength/form ------------------------------------------------------
def group_card(i: int, members: list, me: dict, st_row) -> None:
    f = figures(me)
    is_brand = me["role"] in ALT_FOR
    others = [m for m in members
              if not (m["product"] == me["product"] and m["labeler"] == me["labeler"] and m["role"] == me["role"])]
    if is_brand:
        others = [m for m in others if m["role"] in ALT_FOR[me["role"]]]

    # Collapsed to the presentation line plus a short marker; the details open on demand.
    if is_brand and st_row and st_row["status"] != "alternatives":
        marker = ":orange[No biosimilar dispensed]" if me["role"] == "reference" else ":orange[No generic dispensed]"
    elif not others:
        marker = ":gray[No alternatives]"
    else:
        n = len(others)
        noun = ("alternative" if is_brand else "other product") + ("" if n == 1 else "s")
        marker = f":green[{n} {noun}]" if is_brand else f":gray[{n} {noun}]"
    with st.expander(f"{esc(me['presentation'])}   {marker}"):
        if f["here"]:
            st.caption(esc(f"{me['product']} {here}: {unit_fmt(f['unit'], me['unit_type'])}"
                           f"{' (national)' if f['unit_national'] else ''} · {usd2(f['per_rx'])} per Rx · "
                           f"{f['rx']:,.0f} prescriptions · {ui.usd_compact(f['amt'])} spent"))
        else:
            st.caption(esc(f"{me['product']} was not dispensed {here} at this strength in {data.YEAR} (or in fewer "
                           f"than 11 prescriptions). National price: {unit_fmt(f['unit'], me['unit_type'])}."))
        st.caption(f"{esc(me['product'])} NDCs: {ndc_text(me['ndcs'], 6)}")

        if is_brand and st_row and st_row["status"] != "alternatives":
            title = "No biosimilar alternative dispensed" if me["role"] == "reference" else \
                "No generic alternative dispensed"
            body = "".join(f"<p>{html.escape(line)}</p>" for line in reason(st_row))
            st.markdown(f'<div class="mr-none"><strong>{title}</strong>{body}</div>', unsafe_allow_html=True)
            return
        if not others:
            st.caption("No alternative of this presentation was dispensed." if is_brand else
                       f"No other product of this strength and form was dispensed in Medicaid in {data.YEAR}.")
            return

        rows = sorted(((m, figures(m)) for m in others),
                      key=lambda x: (not x[1]["here"], x[1]["unit"] if x[1]["unit"] is not None else math.inf))
        if is_brand:
            if st_row and st_row.get("unit_mismatch") is True:
                callout('Medicaid units for this brand and its alternatives are reported on different bases (for '
                        'example per package versus per tablet), so per-unit prices and the "less per unit" comparison '
                        "are not reliable here, and no savings are estimated.")
            if not national and not any(o["here"] for _, o in rows):
                callout(f"Alternatives were dispensed nationally but not in {geo_name} in {data.YEAR} (or each in "
                        "fewer than 11 prescriptions). National prices are shown.")
            # Savings: the selected geography's estimate; a state without one falls back to national.
            sv = st_row if national else (sf.get("s", {}).get(int(st_row["sid"])) if st_row else None)
            if sv is not None:
                g, mn, av = ((num(sv.get("gross_savings")), num(sv.get("min_rebate_savings")),
                              num(sv.get("avg_rebate_savings"))) if national else
                             (num(sv.get("gross")), num(sv.get("min_rebate")), num(sv.get("avg_rebate"))))
            else:
                g = mn = av = None
            if g is not None:
                st.markdown(esc(f"If every prescription {here} moved to the alternatives: **{ui.usd_compact(g)}** gross "
                                f"· {ui.usd_compact(mn)} after minimum rebates · {ui.usd_compact(av)} after average "
                                "rebates."))
            elif not national and st_row and num(st_row.get("gross_savings")) is not None:
                st.markdown(esc(f"No {geo_name} estimate (too little volume there). National estimate: "
                                f"{ui.usd_compact(num(st_row['gross_savings']))} gross · "
                                f"{ui.usd_compact(num(st_row['avg_rebate_savings']))} after average rebates."))
            flags = []
            reverse = (st_row.get("savings_reverse_after_rebates") is True) if national else \
                (sv is not None and num(sv.get("reverse")) == 1)
            if reverse:
                flags.append("After average rebates the brand may cost Medicaid less than the alternatives.")
            if national and st_row and isinstance(st_row.get("brand_preferred_states"), str):
                flags.append(f"On the brand-preferred list in {st_row['brand_preferred_states'].replace(',', ', ')} "
                             "(the state found the brand cheaper after rebates).")
            if not national and sv is not None and num(sv.get("preferred_here")) == 1:
                flags.append(f"On {geo_name}'s brand-preferred list: the state found the brand cheaper after rebates.")
            if flags:
                callout(" ".join(flags), "mr-callout-warn")

        def compare(m, o, rebates=False):
            if not (f["unit"] and f["unit"] > 0) or o["unit"] is None:
                return "—"
            mine, theirs = f["unit"], o["unit"]
            if rebates:
                mine, theirs = mine * (1 - AVG_REBATE[me["role"]]), theirs * (1 - AVG_REBATE[m["role"]])
            d = (theirs - mine) / mine
            return f"{-d:.0%} less per unit" if d < 0 else f"{d:.0%} more per unit" if d > 0 else "same price"

        shown = rows[:ROW_LIMIT]
        # Comparison columns first, so a narrow screen cuts off the less important ones.
        view = pd.DataFrame({
            "Product": [m["product"] for m, _ in shown],
            BEFORE: [compare(m, o) for m, o in shown],
            AFTER: [compare(m, o, rebates=True) for m, o in shown],
            "Cost per unit": [unit_fmt(o["unit"], m["unit_type"]) + (" (national)" if o["unit_national"] and not national
                                                                    else "") for m, o in shown],
            "Cost per Rx": [usd2(o["per_rx"]) for _, o in shown],
            ("Prescriptions" if national else f"{geo} prescriptions"):
                [f"{o['rx']:,.0f}" if o["here"] else "not dispensed here" for _, o in shown],
            "Type": [ROLE_LABEL.get(m["role"], m["product_type"]) for m, _ in shown],
            "Manufacturer": [m["labeler"] for m, _ in shown],
            "NDCs": [ndc_text(m["ndcs"], 1) for m, _ in shown],
        })
        styled = view.style.apply(
            lambda col: [f"color: {ui.good()}" if v.endswith("less per unit") else
                         f"color: {ui.bad()}" if v.endswith("more per unit") else "" for v in col]
            if col.name in (BEFORE, AFTER) else [""] * len(col), axis=0)
        st.dataframe(styled, hide_index=True, width="stretch", height=35 * (len(view) + 1) + 3, column_config={
            BEFORE: st.column_config.TextColumn(
                help=f"Price per unit compared with {me['product']}, using what Medicaid paid before rebates"),
            AFTER: st.column_config.TextColumn(
                help=f"Estimated: brands and reference biologics less {AVG_REBATE['brand']:.1%} (the national average, "
                     f"see the Rebates page), generics less {AVG_REBATE['generic']:.0%}, authorized generics and "
                     "biosimilars less 23.1%. Real rebates are confidential and vary by drug."),
        })
        if len(rows) > ROW_LIMIT:
            st.caption(f"{len(rows) - ROW_LIMIT} more products not shown.")


# ---- page ---------------------------------------------------------------------------
ui.page_header(
    "Explore Alternatives",
    "Pick a product to see the generics, authorized generics or biosimilars sold at the same strength and form, "
    "and what each cost Medicaid per unit. Where none exists, the page says why, using FDA records.",
    lambda: ui.state_picker("ex_geo", geo, _set_geo),
)

idx = data.explore_index()
current = st.session_state.get("explore_pid")
st.session_state.ex_query = st.session_state.get("explore_query", "")
st.text_input("Find a product", key="ex_query", on_change=_query,
              placeholder="Brand or generic name, e.g. Eliquis, Humira, atorvastatin")
query = st.session_state.ex_query.strip()
if len(query) >= 2:
    hits = data.search_products(query)
    with st.container(key="ex-results"):
        if hits.empty:
            st.caption("No product matches that name.")
        for r in hits.itertuples():
            meta = f"{r.labeler} · {r.product_type if isinstance(r.product_type, str) else 'unclassified'} · " \
                   f"{r.ingredient if isinstance(r.ingredient, str) else ''} · {ui.usd_compact(r.medicaid_amount)} national"
            st.button(f"**{esc(r.product)}**  \n{esc(meta)}", key=f"hit-{r.pid}", type="tertiary", width="stretch",
                      on_click=_pick, args=(int(r.pid),))
st.caption(f"National figures, {data.YEAR}." if national else
           f"Prescriptions, spending and prices for {geo_name}, {data.YEAR} (national price where a product was not "
           "dispensed in the state). Reasons come from national FDA records.")

if current is None or current not in set(idx.pid):
    st.info('Search for a product above, or select a product in a Rankings popup and choose "Explore alternatives".',
            icon=":material/search:")
else:
    prod = idx[idx.pid == current].iloc[0]
    product, labeler = prod["product"], prod.labeler
    grp = data.explore_groups()
    status = data.explore_status()
    mine = grp[(grp["product"] == product) & (grp.labeler == labeler)].to_dict("records")
    mine.sort(key=lambda m: (-figures(m)["amt"], -(num(m["medicaid_amount"]) or 0)))
    keys = {m["group_key"] for m in mine}
    by_group = {k: part.to_dict("records") for k, part in grp[grp.group_key.isin(keys)].groupby("group_key")}

    # ---- product header ----
    with st.container(border=True, key="card-ex-head"):
        st.subheader(product)
        st.caption(" · ".join(str(x) for x in (labeler, prod.product_type, prod.ingredient,
                                               "National" if national else geo_name) if isinstance(x, str)))
        if national:
            rx, amt = num(prod.prescriptions) or 0, num(prod.medicaid_amount) or 0
        else:
            sp = sf.get("p", {}).get(int(prod.pid))
            rx, amt = (num(sp["rx"]) or 0, num(sp["amt"]) or 0) if sp else (0, 0)
        suffix = "" if national else f" ({geo})"
        facts = [(f"Prescriptions{suffix}", f"{rx:,.0f}"), (f"Medicaid spending{suffix}", f"${amt:,.0f}"),
                 (f"Cost per Rx{suffix}", usd2(amt / rx) if rx > 0 else "—"),
                 ("Strengths and forms", f"{int(prod.presentations):,}")]
        for col, (k, v) in zip(st.columns(4), facts):
            col.markdown(f'<div class="mr-fact"><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>',
                         unsafe_allow_html=True)
        rec = data.indications().get((product, labeler))
        if rec and isinstance(rec.get("indication"), str):
            st.markdown(f'<div class="mr-ex-use">{html.escape("FDA-approved use: " + rec["indication"])}</div>',
                        unsafe_allow_html=True)

        brand_rows = [m for m in mine if m["role"] in ALT_FOR]
        where = "" if national else f" in {geo_name}"
        if brand_rows:
            def alt_here(m):
                return (status.get(m["group_key"], {}).get("status") == "alternatives" and
                        any(o["role"] in ALT_FOR[m["role"]] and figures(o)["here"] and figures(o)["rx"] > 0
                            for o in by_group.get(m["group_key"], [])))
            with_alt = [m for m in brand_rows if alt_here(m)]
            spend = sum(figures(m)["amt"] for m in brand_rows)
            spend_alt = sum(figures(m)["amt"] for m in with_alt)
            n = len(brand_rows)
            if with_alt:
                verdict = (f"A cheaper equivalent was dispensed{where} for {len(with_alt)} of {n} strength/form"
                           f"{'' if n == 1 else 's'}"
                           + (f", covering {spend_alt / spend:.0%} of this product's spending{where}" if spend else "")
                           + ".")
            else:
                verdict = (f"No generic or biosimilar alternative was dispensed{where} in {data.YEAR} for any "
                           f"strength or form of {product}. The details are below.")
        elif mine:
            verdict = ("This product is itself a generic, authorized generic or biosimilar. Below are the other "
                       "products sold at the same strength and form, cheapest first.")
        else:
            verdict = ("This product could not be matched to a strength and form (no RxNorm clinical drug), so "
                       "alternatives cannot be listed.")
        callout(verdict)

    # ---- one card per strength/form ----
    show_all = st.session_state.get("ex_show_all", False)
    limit = len(mine) if show_all else CARD_LIMIT
    for i, me in enumerate(mine[:limit]):
        group_card(i, by_group.get(me["group_key"], []), me, status.get(me["group_key"]))
    if len(mine) > limit:
        st.button(f"Show {len(mine) - limit} more strengths and forms",
                  on_click=lambda: st.session_state.update(ex_show_all=True))

with st.expander("How alternatives and reasons are found"):
    st.markdown(esc(
        "- An alternative has the same ingredient, strength and dose form (RxNorm clinical drug) and the same billing "
        'unit. Generics must carry an FDA "A" therapeutic-equivalence rating; authorized generics always qualify. '
        "Biosimilars must match concentration, volume and device.\n"
        f"- Prices are national averages of what Medicaid paid per unit in {data.YEAR}, before rebates. Savings "
        "estimates come from the Savings Opportunity Overview.\n"
        f"- The after-rebates comparison is an estimate: brands and reference biologics get the national average "
        f"rebate ({AVG_REBATE['brand']:.1%}), generics {AVG_REBATE['generic']:.0%}, authorized generics and "
        "biosimilars 23.1%. A brand can come out cheaper than its generic after rebates; that is why some states "
        "put brands on preferred lists. See the Rebates page for how the average is estimated.\n"
        "- When no alternative was dispensed, the reason comes from the FDA Orange Book (generic approvals, listed "
        "patents, exclusivities) or the Purple Book (licensed biosimilars, exclusivities). Listed patents can be "
        "challenged, settled or ruled invalid, so their dates are not guaranteed launch dates."
    ))
