"""Overview: three cards and a state map. The region picked in the region
card (or by clicking the map) drives the hero number and the map outline."""
import streamlit as st

from lib import charts, data, tooltips, ui

ui.page_header("Overview", f"Medicaid prescription drug spending, {data.YEAR}, and where a generic or biosimilar "
                           "could cost less.")

st.session_state.setdefault("geo", data.NATIONAL)
st.session_state.setdefault("map_metric", "generic_rx_share")


def _set_geo():
    st.session_state.geo = st.session_state.home_geo


def _map_click():
    # A click on a state selects it; the region picker follows.
    pts = st.session_state.home_map.selection.get("points", [])
    if pts and pts[0].get("location"):
        st.session_state.geo = pts[0]["location"]


def _go_explore():
    # Callbacks can't change page, so flag it and switch below. Explore runs the search.
    q = st.session_state.home_search.strip()
    if q:
        st.session_state.explore_query = q
        st.session_state.go_explore = True
        st.session_state.home_search = ""


if st.session_state.pop("go_explore", False):
    st.switch_page("views/explore.py")

geo = st.session_state.geo
geo_name = data.STATE_NAMES.get(geo, geo)
sav = data.savings_for(geo)

# Equal widths; ui.py makes the three cards equal height too.
hero, region, search = st.container(key="home-cards").columns(3, gap="medium")

with hero:
    with st.container(border=True, key="card-hero"):
        ui.card_title("savings", "Savings opportunity", "#4ADE80")
        where = "nationally" if geo == data.NATIONAL else f"in {'the ' if geo == 'DC' else ''}{geo_name}"
        st.markdown(
            f'<div class="mr-hero"><span class="mr-hero-pre">Up to</span>{ui.usd_words(sav.gross_savings)}</div>'
            f'<div class="mr-hero-sub">in savings opportunities before rebates {where}</div>',
            unsafe_allow_html=True,
        )
        st.page_link("views/savings.py", label="View savings ›")

with region:
    with st.container(border=True, key="card-region"):
        ui.card_title("map", "Explore by region", "#4C8DF6")
        st.markdown(
            '<div class="mr-note">Top drugs by prescriptions and by spending, nationally or for one state. '
            "You can also click a state on the map.</div>",
            unsafe_allow_html=True,
        )
        # The picker follows the shared geo (set by it or by a map click) through Session State only;
        # passing index= as well makes Streamlit warn when both are set.
        st.session_state.home_geo = geo
        st.selectbox(
            "Region",
            data.states(),
            format_func=lambda c: data.STATE_NAMES.get(c, c),
            key="home_geo",
            label_visibility="collapsed",
            on_change=_set_geo,
        )
        st.page_link("views/rankings.py", label="View rankings ›")

with search:
    with st.container(border=True, key="card-search"):
        ui.card_title("search", "Search for alternatives", "#F59E5B")
        st.markdown(
            '<div class="mr-note">Generics, authorized generics or biosimilars at the same strength and form, '
            "and what each cost Medicaid.</div>",
            unsafe_allow_html=True,
        )
        st.text_input(
            "Find a product",
            placeholder="Brand or generic name, then Enter",
            key="home_search",
            label_visibility="collapsed",
            on_change=_go_explore,
        )

# ---- state map ---------------------------------------------------------------
metric = st.session_state.map_metric
label, _, title, kind, note = charts.METRICS[metric]

with st.container(border=True, key="card-map"):
    menu, view = st.columns([1.1, 3.3], gap="large")
    with menu:
        ui.card_title("public", "State map", "#4C8DF6")
        group = None
        for key, (item, g, *_rest) in charts.METRICS.items():
            if g != group:
                st.markdown(f'<div class="mr-menu-head">{g}</div>', unsafe_allow_html=True)
                group = g
            st.button(
                item, key=f"metric-{key}", width="stretch",
                type="primary" if key == metric else "tertiary",
                on_click=lambda k=key: st.session_state.update(map_metric=k),
            )
    with view:
        metrics = data.state_metrics()
        nat = metrics.set_index("geo").loc[data.NATIONAL, metric]
        st.markdown(
            f'<div class="mr-map-title">{title}, {data.YEAR}</div>'
            f'<div class="mr-note">{note} National: <b>{charts.fmt(nat, kind)}</b>.</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            charts.state_map(metric, geo),
            key="home_map",
            on_select=_map_click,
            selection_mode="points",
            config={"displayModeBar": False, "scrollZoom": False},
        )
        st.caption(
            "State figures come from each state's own rows. CMS hides rows with fewer than 11 prescriptions, "
            "so rare, high-cost drugs are undercounted at the state level. Puerto Rico is in the table only."
        )
        with st.expander("Table view"):
            tbl = metrics[metrics.geo != data.NATIONAL].sort_values(metric, ascending=False)
            fmt = {"pct": "percent", "usd": "compact", "usd2": "dollar"}[kind]
            col = title + (" ($)" if kind == "usd" else "")
            st.dataframe(
                tbl.assign(State=tbl.geo.map(data.STATE_NAMES))[["State", metric]].rename(columns={metric: col}),
                column_config={col: st.column_config.NumberColumn(format=fmt)},
                hide_index=True, width="stretch", height=320,
            )

# Also stops the map from being dragged (lib/tooltips.py).
tooltips.install()
