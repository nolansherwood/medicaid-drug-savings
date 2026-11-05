"""Tests for the NDC -> ingredient -> ATC crosswalk.

Rule tests use a fake RxNav client (no network). Data tests check the built
crosswalk and are skipped until src.crosswalk has been run.
"""
import json

import duckdb
import pytest

from src import config, crosswalk


def cls(rxcui, tty, code, name="x"):
    return {"minConcept": {"rxcui": rxcui, "name": name, "tty": tty},
            "rxclassMinConceptItem": {"classId": code, "className": "", "classType": "ATC1-4"}}


class FakeRxNav:
    """Canned responses shaped like the real endpoints."""

    def __init__(self, related=None, history=None, classes=None):
        self._related = related or {}
        self._history = history or {}
        self._classes = classes or {}

    def related_ingredients(self, rxcui):
        return self._related.get(rxcui, {})

    def history_status(self, rxcui):
        return self._history.get(rxcui, {})

    def classes(self, rxcui, source):
        return self._classes.get((rxcui, source), [])


# -- step 1 ------------------------------------------------------------------

def test_product_rxcui_active():
    assert crosswalk.resolve_product_rxcui({"rxcui": "1551300"}) == ("1551300", "ndcstatus")


def test_product_rxcui_obsolete_uses_latest_history():
    status = {"rxcui": "", "ndcHistory": [
        {"activeRxcui": "111", "originalRxcui": "111", "endDate": "201801"},
        {"activeRxcui": "222", "originalRxcui": "111", "endDate": "202305"},
    ]}
    assert crosswalk.resolve_product_rxcui(status) == ("222", "ndc_history_active")


def test_product_rxcui_unknown():
    assert crosswalk.resolve_product_rxcui({"status": "UNKNOWN", "rxcui": ""}) == (None, None)


# -- step 2 ------------------------------------------------------------------

def test_single_ingredient_maps_to_in():
    ing = crosswalk.ingredient_from_related({"IN": [{"rxcui": "83367", "name": "atorvastatin"}], "MIN": []})
    assert (ing["ingredient_rxcui"], ing["ingredient_tty"]) == ("83367", "IN")


def test_combination_maps_to_min():
    ing = crosswalk.ingredient_from_related({
        "IN": [{"rxcui": "52175", "name": "losartan"}, {"rxcui": "5487", "name": "hydrochlorothiazide"}],
        "MIN": [{"rxcui": "214619", "name": "hydrochlorothiazide / losartan"}]})
    assert ing["ingredient_rxcui"] == "214619"
    assert ing["ingredient_tty"] == "MIN"
    assert ing["component_in_rxcuis"] == ["52175", "5487"]


def test_copack_without_min_gets_synthetic_ingredient():
    ing = crosswalk.ingredient_from_related({
        "IN": [{"rxcui": "2", "name": "rilpivirine"}, {"rxcui": "1", "name": "cabotegravir"}]})
    assert ing["ingredient_rxcui"] == "1+2"
    assert ing["ingredient_name"] == "cabotegravir / rilpivirine"
    assert ing["ingredient_tty"] == "MULTI_IN"


def test_retired_rxcui_follows_active_remap():
    rx = FakeRxNav(
        related={"900": {"IN": [{"rxcui": "4850", "name": "glucose"}]}},
        history={"237648": {"derivedConcepts": {"remappedConcept": [
            {"remappedRxCui": "800", "remappedActive": "NO"},
            {"remappedRxCui": "900", "remappedActive": "YES"}]}}})
    ing = crosswalk.resolve_ingredient(rx, "237648")
    assert ing["ingredient_name"] == "glucose"
    assert ing["atc_rxcui"] == "900"
    assert ing["ingredient_source"] == "history_remap"


# -- steps 3/4 ---------------------------------------------------------------

def test_atc4_codes_filters_to_exact_concept():
    rows = [cls("83367", "IN", "C10AA"), cls("1422085", "MIN", "C10BA"), cls("83367", "IN", "C10")]
    assert crosswalk.atc4_codes(rows, only_concept="83367") == {"C10AA"}
    assert crosswalk.atc4_codes(rows) == {"C10AA", "C10BA"}


def test_min_fallback_prefers_combination_code():
    rx = FakeRxNav(classes={
        ("52175", "ATC"): [cls("52175", "IN", "C09CA"), cls("214619", "MIN", "C09DA")],
        ("5487", "ATC"): [cls("5487", "IN", "C03AA")]})
    ing = {"ingredient_tty": "MIN", "ingredient_rxcui": "214619", "component_in_rxcuis": ["52175", "5487"]}
    assert crosswalk.ingredient_level_classes(rx, ing) == {"C09DA"}


def test_choose_primary_by_volume_then_spend_then_code():
    assert crosswalk.choose_primary({"A07EA": (10, 99), "H02AB": (500, 1)}) == "H02AB"
    assert crosswalk.choose_primary({"B01AC": (5, 10), "N02BA": (5, 20)}) == "N02BA"
    assert crosswalk.choose_primary({"B01AC": (5, 10), "N02BA": (5, 10)}) == "B01AC"


# -- data tests --------------------------------------------------------------

needs_crosswalk = pytest.mark.skipif(not crosswalk.CROSSWALK_PATH.exists(),
                                     reason="crosswalk not built yet")


@pytest.fixture(scope="module")
def xw():
    return duckdb.sql(f"SELECT * FROM read_parquet('{crosswalk.CROSSWALK_PATH}')").df()


@needs_crosswalk
def test_crosswalk_covers_every_sdud_ndc(xw):
    n = duckdb.sql(f"SELECT count(DISTINCT ndc) FROM read_parquet('{config.NATIONAL_FACT_GLOB}')").fetchone()[0]
    assert len(xw) == n
    assert xw["ndc"].is_unique
    assert xw["ndc"].str.fullmatch(r"\d{11}").all()


@needs_crosswalk
def test_one_primary_class_per_ingredient(xw):
    mapped = xw[xw["atc4"].notna()]
    assert (mapped.groupby("ingredient_rxcui")["atc4"].nunique() == 1).all()


@needs_crosswalk
def test_atc_levels_consistent(xw):
    m = xw[xw["atc4"].notna()]
    assert (m["atc2"] == m["atc4"].str[:3]).all()
    assert m["atc2_name"].notna().all()


@needs_crosswalk
def test_spending_coverage_above_95pct():
    rep = json.loads(crosswalk.COVERAGE_PATH.read_text())
    for year, r in rep["by_year"].items():
        assert r["share_spending_mapped_to_atc"] >= 0.95, year


@needs_crosswalk
@pytest.mark.parametrize("name,expected_atc2", [
    ("atorvastatin", "C10"), ("amlodipine", "C08"), ("dulaglutide", "A10"),
    ("semaglutide", "A10"), ("hydrochlorothiazide / losartan", "C09")])
def test_known_drugs_land_in_expected_category(xw, name, expected_atc2):
    rows = xw[xw["ingredient_name"] == name]
    assert len(rows), f"{name} not found"
    assert set(rows["atc2"]) == {expected_atc2}
