"""Tests for the Explore Alternatives data (src.explore)."""
import pandas as pd
import pytest

from src import config, explore


@pytest.mark.parametrize("code,name", [
    ("NCE", "new chemical entity"), ("ODE-123", "orphan drug"), ("I-890", "new indication"),
    ("NPP", "new patient population"), ("NP", "new product"), ("PED", "pediatric extension"), ("ZZZ", "ZZZ"),
])
def test_exclusivity_name(code, name):
    assert explore.exclusivity_name(code) == name


OUT = config.OUTPUTS / "dashboard"
needs = pytest.mark.skipif(not (OUT / "explore_status_2025.csv").exists(), reason="explore not built")


@pytest.fixture(scope="module")
def tables():
    return (pd.read_csv(OUT / "explore_groups_2025.csv"), pd.read_csv(OUT / "explore_status_2025.csv"))


def status_of(tables, product):
    g, st = tables
    key = (g[(g["product"].str.upper() == product.upper()) & g["role"].isin(["brand", "reference"])]
           .sort_values("medicaid_amount", ascending=False)["group_key"].iloc[0])
    return st[st["group_key"] == key].iloc[0]


@needs
@pytest.mark.parametrize("product,status", [
    ("Humira", "alternatives"),                       # interchangeable biosimilars dispensed
    ("Vyvanse", "alternatives"),                      # generic lisdexamfetamine dispensed
    ("ELIQUIS", "approved_not_marketed"),             # generics approved 2020, held off the market
    ("Ozempic", "no_generic_approved"),
    ("INVEGA SUSTENNA", "no_generic_approved"),
    ("Dupixent", "no_biosimilar_licensed"),
])
def test_known_statuses(tables, product, status):
    assert status_of(tables, product)["status"] == status


@needs
def test_reasons_carry_dates(tables):
    assert pd.notna(status_of(tables, "ELIQUIS")["first_generic_approval"])
    assert pd.notna(status_of(tables, "Ozempic")["latest_patent_expiry"])
    assert pd.notna(status_of(tables, "Dupixent")["orphan_exclusivity"])
