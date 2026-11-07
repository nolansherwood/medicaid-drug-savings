"""Tests for the Streamlit app (app/).

Two kinds:
- Data helpers (app/lib/data.py): the figures the pages show match the pipeline's
  dashboard files, and the old dashboard's 2025 figures are pinned so a change
  in the app or the data is noticed.
- Page runs (Streamlit AppTest): every page renders without an error, nationally
  and for a state, and the links between pages work.

They read outputs/dashboard/*_2025.csv, so they are skipped until
python -m src.export_dashboard and python -m src.explore have run.
"""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))   # the app imports its helpers as "lib"

from lib import data, ui  # noqa: E402
from src import explore  # noqa: E402

pytestmark = pytest.mark.skipif(not (data.DASH_DIR / "explore_status_2025.csv").exists(),
                                reason="dashboard data not built")
HOME = str(ROOT / "app" / "Home.py")


def close(a, b, rel=1e-9):
    return math.isclose(float(a), float(b), rel_tol=rel)


# ---- data helpers ------------------------------------------------------------------
def test_hero_matches_savings_summary():
    s = data.savings_summary()
    row = s[(s.geo == "US") & (s.tier == "all") & (s.match_level == "primary")].iloc[0]
    assert close(data.savings_for("US", "all").gross_savings, row.gross_savings)


@pytest.mark.parametrize("geo,tier,shown", [
    # Figures shown on the old dashboard (2025 data): gross, after minimum rebates, after average rebates.
    ("US", "all", ("$9.1B", "$6.8B", "$2.1B")),
    ("US", "generic", ("$5.0B", "$3.6B", "$918.9M")),
    ("NY", "generic", ("$522.7M", "$385.5M", "$106.7M")),
])
def test_savings_figures_match_old_dashboard(geo, tier, shown):
    row = data.savings_for(geo, tier)
    got = tuple(ui.usd_compact(v) for v in (row.gross_savings, row.min_rebate_savings, row.avg_rebate_savings))
    assert got == shown


def test_rankings_figures_match_old_dashboard():
    cs = data.category_summary()
    us = cs[cs.state == "US"]
    assert abs(us.medicaid_amount.sum() - 111.9e9) < 0.06e9
    assert round(us[us.product_type == "Generic"].share_prescriptions.iloc[0], 3) == 0.802
    rk = data.rankings()
    top = rk[(rk.state == "US") & (rk.category == "All product types") & (rk.measure == "spending")
             & (rk["rank"] == 1)].iloc[0]
    assert top.ingredient == "semaglutide" and abs(top.medicaid_amount - 6.5e9) < 0.06e9


def test_state_metrics_cover_every_state():
    m = data.state_metrics()
    assert m.geo.nunique() == len(m) == 53                         # 50 states, DC, Puerto Rico, national
    assert set(m.geo) - {data.NATIONAL} <= set(data.STATE_NAMES)
    us = m[m.geo == data.NATIONAL].iloc[0]
    assert close(us.gross_savings, data.savings_for("US").gross_savings)
    assert 0 < us.generic_rx_share < 1 and 0 < us.brand_spend_share < 1


def test_states_lists_national_first():
    codes = data.states()
    assert codes[0] == data.NATIONAL and len(codes) == 53


def test_search_matches_names_exactly():
    # The old selectbox's fuzzy match turned "Eliquis" into Yervoy and "Humira" into fluoxetine.
    for query in ("Eliquis", "Humira", "atorvastatin"):
        hits = data.search_products(query)
        assert not hits.empty
        names = hits["product"].fillna("").str.lower() + " " + hits.ingredient.fillna("").str.lower()
        assert names.str.contains(query.lower()).all()
    assert data.search_products("Eliquis")["product"].iloc[0].lower() == "eliquis"
    assert data.search_products("zzzz-no-such-drug").empty


def test_explore_state_files_follow_pipeline_rule():
    codes = sorted(set(data.category_summary().state) - {data.NATIONAL})
    buckets = explore.state_buckets(codes)
    for geo in (codes[0], "NY", codes[-1]):
        figs = data.explore_state_figures(geo)
        assert figs, geo
        on_disk = pd.read_csv(data.DASH_DIR / f"explore_states_{buckets[geo]}_2025.csv", usecols=["state"])
        assert (on_disk.state == geo).any()


# ---- pages ---------------------------------------------------------------------------
@pytest.fixture
def app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(HOME, default_timeout=120)


def page(at, path, **state):
    for k, v in state.items():
        at.session_state[k] = v
    at.switch_page(path).run()
    assert not at.exception, [e.message for e in at.exception]
    return at


@pytest.mark.parametrize("geo", ["US", "TX"])
@pytest.mark.parametrize("path,title", [("views/home.py", "Overview"), ("views/rankings.py", "Rankings"),
                                        ("views/savings.py", "Savings Opportunity"), ("views/rebates.py", "Rebates"),
                                        ("views/explore.py", "Explore Alternatives"),
                                        ("views/categories.py", "Drug Categories")])
def test_pages_render(app, path, title, geo):
    at = page(app.run(), path, geo=geo)
    assert [t.value for t in at.title] == [title]


def test_home_hero_follows_region(app):
    at = app.run()
    hero = lambda: next(m.value for m in at.markdown if "mr-hero" in m.value)  # noqa: E731
    assert "$9.1 billion" in hero() and "nationally" in hero()
    at.selectbox(key="home_geo").select("TX").run()
    assert "in Texas" in hero() and at.session_state["geo"] == "TX"


def test_home_search_opens_explore(app):
    at = app.run()
    at.text_input(key="home_search").input("humira").run()
    assert not at.exception
    assert [t.value for t in at.title] == ["Explore Alternatives"]
    assert at.session_state["explore_query"] == "humira"


@pytest.mark.parametrize("tier", ["all", "generic", "interchangeable_biosimilar", "biosimilar"])
def test_savings_tiers_render(app, tier):
    page(app.run(), "views/savings.py", sav_tier=tier)


def test_explore_humira_strengths(app):
    idx = data.explore_index()
    pid = int(idx[(idx["product"] == "Humira")].sort_values("medicaid_amount").iloc[-1].pid)
    at = page(app.run(), "views/explore.py", explore_pid=pid)
    labels = [e.label for e in at.expander]
    strengths = [lbl for lbl in labels if "adalimumab" in lbl]
    assert len(strengths) == 9
    assert sum("alternative" in lbl for lbl in strengths) == 6          # "6 of 9 strength/forms" in the verdict
    assert sum("No biosimilar dispensed" in lbl for lbl in strengths) == 3


def test_explore_eliquis_shows_reason(app):
    pid = int(data.search_products("Eliquis").iloc[0].pid)
    at = page(app.run(), "views/explore.py", explore_pid=pid)
    strengths = [e.label for e in at.expander if "apixaban" in e.label]
    assert strengths and all("No generic dispensed" in lbl for lbl in strengths)
    assert any("2031" in m.value for m in at.markdown)                 # latest patent expiry, Aug 24, 2031
    assert any("unexpired patents as of " in m.value for m in at.markdown)   # counted on the build date


def test_mix_groups_cover_every_product_type():
    types = [t for _, group in data.MIX_GROUPS for t in group]
    assert sorted(types) == sorted(data.category_summary().product_type.unique())   # each once, none missing


def test_rankings_mix_card_headline(app):
    at = page(app.run(), "views/rankings.py", geo="US")
    head = next(m.value for m in at.markdown if "mr-mix-head" in m.value)
    assert "<b>12.8%</b> of prescriptions" in head and "<b>87.2%</b> of spending" in head


def test_savings_brands_add_up_their_strengths():
    brands, members = data.savings_brands(), data.savings_brand_groups()
    keys = ["geo", "match_level", "tier_view", "brand_key"]
    assert not brands.duplicated(keys).any()                            # one row per brand
    sums = members.groupby(keys).gross_savings.sum()
    merged = brands.set_index(keys).gross_savings
    assert ((sums.reindex(merged.index) - merged).abs() <= 2).all()     # rounded to whole dollars
    us = brands[(brands.geo == "US") & (brands.tier_view == "all")]
    assert len(us) == 25 and us.brand.str.lower().duplicated().sum() == 0
    humira = us[us.brand == "Humira"].iloc[0]
    assert humira["rank"] == 1 and humira.groups >= 4
    # "all" keeps one tier per strength, so no strength is counted twice.
    m = members[(members.geo == "US") & (members.tier_view == "all")]
    assert not m.duplicated("group_key").any()


def test_rebate_calibration_matches_d016():
    us = data.rebate_rates().set_index("state").loc["US"]
    implied = (us.rebates_millions - 0.13 * us.gross_millions * us.generic_share_of_gross) / \
        (us.gross_millions * us.brand_share_of_gross)
    assert round(implied, 3) == round(us.brand_rate_used, 3) == 0.619


def test_rebates_page_price_comparison(app):
    at = page(app.run(), "views/rebates.py", geo="US")
    assert at.selectbox(key="reb_brand").value.startswith("humira|")
    callout = next(m.value for m in at.markdown if "Break-even brand rebate for this strength" in m.value)
    assert "61.9%" in callout
    at.selectbox(key="reb_brand").set_value("farxiga|dapagliflozin").run()   # brand cheaper after avg. rebates
    assert not at.exception
    assert any("less per unit than the alternative" in m.value for m in at.markdown)


def test_explore_after_rebates_column(app):
    # Generic dapagliflozin is cheaper than Farxiga before rebates and dearer after the average brand rebate.
    pid = int(data.search_products("Farxiga").iloc[0].pid)
    at = page(app.run(), "views/explore.py", explore_pid=pid)
    df = at.dataframe[0].value
    row = getattr(df, "data", df).iloc[0]
    assert row["vs. this, before rebates"].endswith("less per unit")
    assert row["vs. this, after avg. rebates (est.)"].endswith("more per unit")


def test_state_product_lists_are_complete(app):
    # D-024: no "N other products" row; Texas methylphenidate lists all 42 products.
    p = data.products("TX")
    assert not p["product"].str.contains("other products", na=False).any() and p.product_type.notna().all()
    assert (p.ingredient == "methylphenidate").sum() == 42
    at = page(app.run(), "views/rankings.py", geo="TX", open_ingredient=("methylphenidate", None))
    df = at.dataframe[-1].value
    df = getattr(df, "data", df)
    assert len(df) == 42 and list(df.columns[:4]) == ["Product", "Prescriptions", "Medicaid spending ($)", "Cost per Rx"]


@pytest.mark.parametrize("path", ["views/home.py", "views/rankings.py", "views/savings.py", "views/rebates.py",
                                  "views/explore.py", "views/categories.py"])
@pytest.mark.parametrize("dark,card", [(False, "#FFFFFF"), (True, "#1F2025")])
def test_pages_render_in_both_modes(app, path, dark, card):
    # Light is the default; the side-menu toggle sets theme_dark.
    at = page(app.run(), path, **({"theme_dark": True} if dark else {}))
    style = next(h.proto.body for h in at.get("html") if "--mr-card:" in h.proto.body)
    assert f"--mr-card: {card}" in style
    sync = next(h.proto.body for h in at.get("html") if "mr-theme-sync" in h.proto.body)
    assert f'data-mode="{"dark" if dark else "light"}"' in sync


@pytest.mark.parametrize("dark", [False, True])
def test_state_map_high_values_stand_out(dark, monkeypatch):
    # High values are the color farthest from the background: darkest in light mode, lightest in dark mode.
    from lib import charts
    monkeypatch.setattr(ui, "mode", lambda: "dark" if dark else "light")
    scale = charts.state_map("generic_rx_share", "US").data[0].colorscale
    assert scale[-1][1].lower() == (charts.BLUE_RAMP[-1] if dark else charts.BLUE_RAMP[0]).lower()


def test_biosimilars_match_their_purple_book_reference():
    # D-025: Kirsty (insulin aspart-xjhz) matches NovoLog ("insulin aspart, human" in RxNorm);
    # Semglee's reference is Lantus, so it is not an alternative to Basaglar (its own 351(a) product).
    g = pd.read_csv(data.DASH_DIR / "explore_groups_2025.csv")
    kirsty = set(g[g["product"].str.upper() == "KIRSTY"].group_key)
    assert kirsty and all((g[g.group_key == k]["product"].str.upper() == "NOVOLOG").any() for k in kirsty)
    basaglar = set(g[g["product"].str.upper() == "BASAGLAR"].group_key)
    assert not g[g.group_key.isin(basaglar) & g.role.isin(["biosimilar", "interchangeable"])].shape[0]
