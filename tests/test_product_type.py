"""Tests for product type classification.

Unit tests use synthetic rows. Hand-checked cases (CLAUDE.md validation list)
use real NDCs from 2025 SDUD and are skipped until src.product_type has run.
Each hand-checked expectation was verified against the FDA NDC Directory,
Purple Book (Sept 2026) and MDRP on 2026-10-21.
"""
import duckdb
import pandas as pd
import pytest

from src import product_type as pt
from src.reference_data import ndc10_to_11_sql


# -- NDC 10 -> 11 digit conversion -------------------------------------------

@pytest.mark.parametrize("ndc10,expected", [
    ("0002-1433-80", "00002143380"),   # 4-4-2: pad labeler
    ("12345-678-90", "12345067890"),   # 5-3-2: pad product
    ("12345-6789-0", "12345678900"),   # 5-4-1: pad package
    ("50090-0876-0", "50090087600"),
    ("0019-N068-B0", None),            # letters: not a valid NDC
    ("1234-567-89", None),             # 4-3-2: not a valid layout
    ("12345678901", None),             # unhyphenated: layout unknown
])
def test_ndc10_to_11(ndc10, expected):
    got = duckdb.execute(f"SELECT {ndc10_to_11_sql('c')} FROM (SELECT ?::VARCHAR AS c)", [ndc10]).fetchone()[0]
    assert got == expected


# -- name flags --------------------------------------------------------------

@pytest.mark.parametrize("prop,nonprop,expected", [
    ("Atorvastatin Calcium", "atorvastatin calcium", True),
    ("Metformin Hydrochloride ER", "metformin hydrochloride", True),
    ("Adalimumab", "adalimumab-adaz", True),          # unbranded Hyrimoz
    ("Insulin Glargine", "insulin glargine-yfgn", True),
    ("Wixela Inhub", "fluticasone propionate and salmeterol", False),
    ("Humira", "adalimumab", False),
    (None, "adalimumab", True),
    ("Humira", None, None),
])
def test_is_unbranded(prop, nonprop, expected):
    assert pt.is_unbranded(prop, nonprop) == expected


# -- classify_row on synthetic rows -------------------------------------------

BASE = {
    "ndc": "00000000001", "sdud_product_name": "X", "fda_marketing_category": None,
    "fda_application_no": None, "mdrp_cod_status": None, "mdrp_application_no": None,
    "mdrp_drug_category": None, "proprietary_name": None, "nonproprietary_name": None,
    "substance_name": None, "mdrp_product_name": None,
}
PB = {
    "125057": {"bla_type": "351(a)", "any_interchangeable": False, "all_interchangeable": None},
    "761071": {"bla_type": "351(k)", "any_interchangeable": True, "all_interchangeable": True},
    "761999": {"bla_type": "351(k)", "any_interchangeable": False, "all_interchangeable": False},
    "761285": {"bla_type": "351(k)", "any_interchangeable": True, "all_interchangeable": False},
    "021081": {"bla_type": "351(a)", "any_interchangeable": False, "all_interchangeable": None},
}
PATTERNS = ["insulin", "somatropin"]


def run(**kw):
    out, issues = pt.classify_row({**BASE, **kw}, PB, set(), PATTERNS, {})
    return out, issues


@pytest.mark.parametrize("cat,expected,flag", [
    ("S", pt.BRAND_SS, False), ("I", pt.BRAND_GC, False), ("N", pt.BRAND_GC, True)])
def test_nda_falls_back_to_mdrp_category_without_market_evidence(cat, expected, flag):
    out, _ = run(fda_marketing_category="NDA", fda_application_no="020702", mdrp_drug_category=cat)
    assert (out["modality"], out["product_type"], out["nda_non_innovator"]) == (pt.SM, expected, flag)
    assert out["competition_source"] == "mdrp_category"


@pytest.mark.parametrize("cat,on_market,expected,disagree", [
    ("I", False, pt.BRAND_SS, True),    # Invega Sustenna: reported I, no generic dispensed
    ("S", True, pt.BRAND_GC, True),     # Vyvanse: reported S, generics dispensed
    ("S", False, pt.BRAND_SS, False),   # Eliquis: S, approved generics not marketed
    ("I", True, pt.BRAND_GC, False),
    (None, False, pt.BRAND_SS, False),  # missing MDRP: market evidence decides
])
def test_nda_split_by_market_evidence(cat, on_market, expected, disagree):
    out, issues = run(fda_marketing_category="NDA", fda_application_no="020702",
                      mdrp_drug_category=cat, generic_on_market=on_market)
    assert out["product_type"] == expected
    assert out["competition_source"] == "medicaid_dispensing"
    assert any(i["check"] == "mdrp_category_vs_market" for i in issues) == disagree


def test_nda_without_mdrp_category_is_single_source_and_flagged():
    out, issues = run(fda_marketing_category="NDA", fda_application_no="020702")
    assert out["product_type"] == pt.BRAND_SS and out["mdrp_category_missing"]
    assert any(i["check"] == "nda_without_mdrp_category" for i in issues)


def test_authorized_generic_and_generic():
    assert run(fda_marketing_category="NDA AUTHORIZED GENERIC")[0]["product_type"] == pt.AUTH_GENERIC
    out, _ = run(fda_marketing_category="ANDA", proprietary_name="Wixela Inhub",
                 nonproprietary_name="fluticasone propionate and salmeterol")
    assert out["product_type"] == pt.GENERIC and out["branded_generic"] is True


@pytest.mark.parametrize("app,expected,partial", [
    ("125057", pt.REF_BIO, False), ("761071", pt.INTERCHANGEABLE, False),
    ("761999", pt.BIOSIMILAR, False), ("761285", pt.INTERCHANGEABLE, True),
    ("999999", pt.REF_BIO, False)])          # not in Purple Book (D-006)
def test_biologics_via_purple_book(app, expected, partial):
    out, _ = run(fda_marketing_category="BLA", fda_application_no=app)
    assert (out["modality"], out["product_type"], out["interchangeable_partial"]) == (pt.BIO, expected, partial)


def test_transitioned_biologic_listed_as_nda_is_biologic():
    out, issues = run(fda_marketing_category="NDA", fda_application_no="021081",
                      substance_name="INSULIN GLARGINE", mdrp_drug_category="S")
    assert out["modality"] == pt.BIO and out["product_type"] == pt.REF_BIO
    assert out["transitioned_biologic"]
    assert any(i["check"].startswith("transitioned_listed_as") for i in issues)


def test_fda_wins_over_mdrp_and_disagreement_logged():
    out, issues = run(fda_marketing_category="NDA AUTHORIZED GENERIC", mdrp_cod_status="03")
    assert out["product_type"] == pt.AUTH_GENERIC
    assert [i["check"] for i in issues] == ["pathway_fda_vs_mdrp"]


def test_mdrp_fallback_when_no_fda_record():
    out, _ = run(mdrp_cod_status="01", mdrp_drug_category="N")
    assert out["product_type"] == pt.GENERIC and out["pathway_source"] == "mdrp"


def test_other_bucket():
    assert run(fda_marketing_category="OTC MONOGRAPH DRUG")[0]["product_type"] == pt.OTHER_TYPE
    out, _ = run()
    assert out["product_type"] == pt.OTHER_TYPE and out["other_reason"] == "no FDA or MDRP record"


# -- hand-checked real NDCs (2025) ---------------------------------------------

YEAR = 2025
OUT_PATH = pt.output_paths(YEAR)[0]
needs_output = pytest.mark.skipif(not OUT_PATH.exists(), reason="product_type not run for 2025")

HAND_CHECKED = [
    # ndc, description, modality, product type, extra flag expectations
    ("00071015723", "Lipitor (Pfizer)", pt.SM, pt.BRAND_GC, {}),
    ("60505257908", "atorvastatin calcium (Apotex ANDA)", pt.SM, pt.GENERIC, {"branded_generic": False}),
    ("66993058597", "fluticasone/salmeterol authorized generic (Prasco)", pt.SM, pt.AUTH_GENERIC, {}),
    ("00378932132", "Wixela Inhub (branded generic)", pt.SM, pt.GENERIC, {"branded_generic": True}),
    ("00074055402", "Humira", pt.BIO, pt.REF_BIO, {"unbranded_biologic": False}),
    # Hyrimoz is listed as interchangeable in the Sept 2026 Purple Book.
    ("61314047320", "Hyrimoz", pt.BIO, pt.INTERCHANGEABLE, {"unbranded_biologic": False}),
    ("61314032720", "unbranded adalimumab-adaz (Sandoz)", pt.BIO, pt.INTERCHANGEABLE, {"unbranded_biologic": True}),
    ("00088222033", "Lantus (transitioned)", pt.BIO, pt.REF_BIO, {"transitioned_biologic": True}),
    ("83257001233", "Semglee", pt.BIO, pt.INTERCHANGEABLE, {"unbranded_biologic": False}),
    ("83257001532", "insulin glargine-yfgn (unbranded Semglee)", pt.BIO, pt.INTERCHANGEABLE,
     {"unbranded_biologic": True}),
    ("00013264681", "Genotropin (somatropin, transitioned)", pt.BIO, pt.REF_BIO, {"transitioned_biologic": True}),
    # D-019: generic competition = a matched generic was dispensed in Medicaid in 2025.
    ("50458056401", "Invega Sustenna (MDRP says I; no generic exists)", pt.SM, pt.BRAND_SS, {}),
    ("00003089421", "Eliquis (generics approved, not marketed)", pt.SM, pt.BRAND_SS, {}),
    ("59417010310", "Vyvanse (MDRP says S; generics dispensed)", pt.SM, pt.BRAND_GC, {}),
]


@pytest.fixture(scope="module")
def classified():
    return pd.read_parquet(OUT_PATH).set_index("ndc")


@needs_output
@pytest.mark.parametrize("ndc,desc,modality,ptype,flags", HAND_CHECKED, ids=[c[1] for c in HAND_CHECKED])
def test_hand_checked(classified, ndc, desc, modality, ptype, flags):
    r = classified.loc[ndc]
    assert (r["modality"], r["product_type"]) == (modality, ptype)
    for k, v in flags.items():
        assert bool(r[k]) == v, k


@needs_output
def test_every_sdud_ndc_classified_once(classified):
    n = duckdb.sql(f"SELECT count(DISTINCT ndc) FROM read_parquet('{pt.config.national_fact_path(YEAR)}')").fetchone()[0]
    assert len(classified) == n and classified.index.is_unique
    assert set(zip(classified["modality"], classified["product_type"])) <= set(pt.PRODUCT_TYPES)


@needs_output
def test_unclassified_bucket_small(classified):
    share = (classified.loc[classified["product_type"] == pt.OTHER_TYPE, "medicaid_amount"].sum()
             / classified["medicaid_amount"].sum())
    assert share < 0.05
