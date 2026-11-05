"""Tests for phase 2: equivalence groups, rebate rates, savings math, brand-preferred lists."""
import pandas as pd
import pytest

from src import equivalence as eqv
from src import rebates, savings


# -- equivalence keys ----------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("0.4 ML adalimumab-adaz 100 MG/ML Auto-Injector", "0.4 ML adalimumab 100 MG/ML Auto-Injector"),
    ("3 ML insulin glargine-yfgn 100 UNT/ML Pen Injector", "3 ML insulin glargine 100 UNT/ML Pen Injector"),
    ("0.8 ML adalimumab 50 MG/ML Auto-Injector", "0.8 ML adalimumab 50 MG/ML Auto-Injector"),
    ("hydrochlorothiazide 12.5 MG / losartan potassium 50 MG Oral Tablet",
     "hydrochlorothiazide 12.5 MG / losartan potassium 50 MG Oral Tablet"),
])
def test_strip_biosimilar_suffix(name, expected):
    assert eqv.strip_biosimilar_suffix(name) == expected


def test_humira_and_hyrimoz_same_presentation_share_a_key():
    assert eqv.strip_biosimilar_suffix("0.4 ML adalimumab-adaz 100 MG/ML Auto-Injector") == \
        eqv.strip_biosimilar_suffix("0.4 ML adalimumab 100 MG/ML Auto-Injector")


@pytest.mark.parametrize("a,b,same", [
    ("atorvastatin 10 MG Oral Tablet", "atorvastatin 80 MG Oral Tablet", True),      # strengths collapse
    ("atorvastatin 10 MG Oral Tablet", "atorvastatin 10 MG Oral Capsule", False),    # forms do not
    ("24 HR paliperidone 6 MG Extended Release Oral Tablet",
     "1.5 ML paliperidone palmitate 156 MG/ML Prefilled Syringe", False),             # Invega Sustenna vs tablet
])
def test_form_key(a, b, same):
    assert (eqv.form_key(a) == eqv.form_key(b)) == same


@pytest.mark.parametrize("code,ok", [("AB", True), ("AB2", True), ("AP", True), ("A1", True),
                                     ("BX", False), ("BC", False), ("NR", False), (None, False)])
def test_a_rated(code, ok):
    assert eqv.is_a_rated(code) == ok


@pytest.mark.parametrize("cat,clot,ped,role,rate", [
    ("S", False, False, "brand", 0.231), ("I", False, False, "authorized_generic", 0.231),
    ("N", False, False, "generic", 0.13), ("S", True, False, "reference", 0.171),
    ("I", False, True, "brand", 0.171), (None, False, False, "generic", 0.13), (None, False, False, "brand", 0.231)])
def test_minimum_rebate_rate(cat, clot, ped, role, rate):
    assert eqv.minimum_rebate_rate(cat, clot, ped, role) == pytest.approx(rate)


# -- brand-preferred matching ----------------------------------------------------

def test_bog_exact_matching_ignores_device_words_only():
    bog = pd.DataFrame({"state": ["SC", "NY", "NH", "SC"],
                        "brand_name": ["Advair HFA", "Spiriva Handihaler", "Cleocin", "Lantus"]})
    bog["key"] = bog["brand_name"].map(savings.norm_brand)
    assert savings.bog_states_for(["ADVAIR HFA"], bog) == ["SC"]
    assert savings.bog_states_for(["Lantus Solostar"], bog) == ["SC"]      # device word ignored
    assert savings.bog_states_for(["Advair Diskus"], bog) == []            # different product
    assert savings.bog_states_for(["Spiriva Respimat"], bog) == []         # listed only as HandiHaler
    assert savings.bog_states_for(["Cleocin T", "Cleocin Phosphate"], bog) == []   # not the listed product


def test_bog_list_file_is_well_formed():
    bog = pd.read_csv(savings.BOG_PATH, dtype=str, keep_default_na=False)
    assert set(bog.columns) >= {"state", "brand_name", "effective_date", "source_url"}
    assert bog["state"].str.fullmatch(r"[A-Z]{2}").all()
    assert bog["source_url"].str.startswith("https://").all()
    assert not bog.duplicated(["state", "brand_name", "qualifier"]).any()


# -- savings math on a synthetic group --------------------------------------------

def _toy():
    eq = pd.DataFrame({
        "ndc": ["B1", "G1", "G2", "X1"],
        "primary_key": ["k", "k", "k", "k"], "loose_key": ["k", "k", "k", "k"],
        "role": ["brand", "generic", "generic", "generic_not_a_rated"],
        "rebate_min_rate": [0.231, 0.13, 0.13, 0.13],
        "mdrp_drug_category": ["I", "N", "N", "N"],
        "proprietary_name": ["Brandex", "drug", "drug", "drug"],
    })
    m = pd.DataFrame({
        "geo": ["US"] * 4, "ndc": ["B1", "G1", "G2", "X1"],
        "units": [100.0, 600.0, 400.0, 50.0], "rx": [10, 60, 40, 5],
        "amt": [1000.0, 600.0, 800.0, 5.0],   # brand $10/unit; generics $1 and $2/unit; B-rated $0.10
    })
    return eq, m


def test_savings_math():
    eq, m = _toy()
    r = savings.compute(2025, "primary_key", "generic", eq, m, brand_rate=0.6).iloc[0]
    # Benchmark is the unit-weighted average of A-rated generics only: (600 + 800) / 1000 = $1.40.
    assert r["alt_cost_per_unit"] == pytest.approx(1.4)
    assert r["alt_lowest_cost_per_unit"] == pytest.approx(1.0)
    assert r["gross_savings"] == pytest.approx(1000 - 100 * 1.4)
    assert r["gross_savings_best_case"] == pytest.approx(1000 - 100 * 1.0)
    assert r["min_rebate_savings"] == pytest.approx(1000 * (1 - 0.231) - 140 * (1 - 0.13))
    assert r["avg_rebate_savings"] == pytest.approx(1000 * (1 - 0.6) - 140 * (1 - 0.13))
    assert r["alt_share_of_units"] == pytest.approx(1000 / 1100)


def test_savings_can_reverse_after_rebates():
    eq, m = _toy()
    m.loc[m["ndc"] == "B1", "amt"] = 200.0    # brand $2/unit, generic $1.40
    r = savings.compute(2025, "primary_key", "generic", eq, m, brand_rate=0.6).iloc[0]
    assert r["gross_savings"] > 0
    assert r["avg_rebate_savings"] < 0         # 200*0.4 = 80 < 140*0.87 = 121.8


def test_state_falls_back_to_national_benchmark():
    eq, m = _toy()
    state = pd.DataFrame({"geo": ["SC", "SC"], "ndc": ["B1", "G1"], "units": [10.0, 5.0], "rx": [1, 1],
                          "amt": [100.0, 5.0]})
    r = savings.compute(2025, "primary_key", "generic", eq, pd.concat([m, state]), brand_rate=0.6)
    sc = r[r["geo"] == "SC"].iloc[0]
    assert sc["benchmark_source"].startswith("national")
    assert sc["alt_cost_per_unit"] == pytest.approx(1.4)


# -- rebate calibration --------------------------------------------------------------

def test_rebate_rates_file():
    if not rebates.RATES_PATH.exists():
        pytest.skip("rebate rates not built")
    r = rebates.load_rates().set_index("state")
    assert 0.5 < r.loc["US", "brand_rate_used"] < 0.75
    assert r["brand_rate_used"].between(rebates.MIN_BRAND, 1.0).all()
