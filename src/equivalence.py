"""Group NDCs into equivalence groups for switching analysis (D-011, D-012).

Each NDC gets a role and two group keys:

  role         brand | generic | generic_not_a_rated | authorized_generic |
               reference | biosimilar | interchangeable | none
  primary_key  defensible match (D-011 / D-012)
                 small molecules: same RxNorm clinical drug (SCD, or GPCK for
                 packs: ingredient + strength + dose form) and same MDRP unit
                 type
                 biologics: same clinical drug name once the biosimilar
                 suffix is removed ("adalimumab-adaz" -> "adalimumab"), so
                 concentration, volume and device must all match; same unit
  loose_key    proof of concept, needs vetting: same ingredient and dose form
               across strengths (numbers removed from the clinical drug name)

A generic is a switch target only with an FDA "A" therapeutic equivalence
code in the MDRP file (AB, AA, AN, AO, AP, AT, A1, A2, ...). Authorized
generics are the brand's own product and always qualify.

Usage:
    python -m src.equivalence --year 2025
"""
from __future__ import annotations

import argparse
import re
import sys

import duckdb
import pandas as pd

from src import config, product_type as pt
from src.crosswalk import CROSSWALK_PATH, parallel_map
from src.rebates import MIN_BRAND, MIN_BRAND_CLOTTING_PEDIATRIC, MIN_GENERIC
from src.rxnav import RxNav


def output_path(year: int):
    return config.PROCESSED / f"equivalence_ndc_{year}.parquet"


ROLE_BY_TYPE = {
    pt.BRAND_SS: "brand", pt.BRAND_GC: "brand", pt.AUTH_GENERIC: "authorized_generic",
    pt.GENERIC: "generic", pt.REF_BIO: "reference", pt.BIOSIMILAR: "biosimilar",
    pt.INTERCHANGEABLE: "interchangeable",
}

# Biosimilar proper names end in a 4-letter FDA suffix (adalimumab-adaz,
# insulin glargine-yfgn, ustekinumab-ttwe). RxNorm keeps it in the clinical
# drug name, so it is removed to compare presentations with the reference.
_SUFFIX = re.compile(r"\b([a-z][a-z]+)-[a-z]{4}\b")
_NUMERIC = re.compile(r"^[\d.,/]+$|^\d")
_UNIT = re.compile(r"^(MG|ML|MCG|UNT|MEQ|MMOL|GM|G|L|%|HR|ACTUAT|IU|MIU|BAU|PNU|AU|CELLS|SQ-HDM)(/[A-Z0-9.\-]+)*$")


def strip_biosimilar_suffix(name: str) -> str:
    return _SUFFIX.sub(r"\1", name)


def form_key(clinical_name: str) -> str:
    """Ingredient + dose form, with strengths and volumes removed (loose match)."""
    out = []
    for tok in strip_biosimilar_suffix(clinical_name).replace("{", " ").replace("}", " ").replace("(", " ").replace(")", " ").split():
        if _NUMERIC.match(tok) or _UNIT.match(tok):
            continue
        out.append(tok)
    return " ".join(out)


def is_a_rated(te_code: str | None) -> bool:
    return bool(te_code) and te_code.upper().startswith("A")


def minimum_rebate_rate(mdrp_category, clotting, pediatric, role) -> float:
    """Statutory floor by MDRP category (D-016); role decides when MDRP is missing."""
    known = lambda v: v is not None and not pd.isna(v)  # noqa: E731  (MDRP fields may be missing)
    cat = mdrp_category if known(mdrp_category) else ("N" if role in ("generic", "generic_not_a_rated") else "S")
    if cat == "N":
        return MIN_GENERIC
    special = (known(clotting) and bool(clotting)) or (known(pediatric) and bool(pediatric))
    return MIN_BRAND_CLOTTING_PEDIATRIC if special else MIN_BRAND


def build(year: int) -> pd.DataFrame:
    pt_path = pt.output_paths(year)[0]
    df = duckdb.sql(f"""
        SELECT p.ndc, p.modality, p.product_type, p.proprietary_name, p.nonproprietary_name,
               p.labeler_name, p.mdrp_drug_category, p.mdrp_unit_type, p.mdrp_te_code,
               p.mdrp_clotting_factor, p.mdrp_pediatric, p.purple_book_bla_type,
               c.product_rxcui, c.ingredient_rxcui, c.ingredient_name, c.atc2_name
        FROM read_parquet('{pt_path}') p
        LEFT JOIN read_parquet('{CROSSWALK_PATH}') c USING (ndc)
    """).df()

    rx = RxNav()
    products = sorted(df["product_rxcui"].dropna().unique())
    clinical = parallel_map(rx.clinical_concept, products, "clinical drug")
    for col in ("clinical_rxcui", "clinical_name", "clinical_tty", "product_tty"):
        df[col] = df["product_rxcui"].map(lambda p: clinical[p][col] if isinstance(p, str) else None)

    role = df["product_type"].map(ROLE_BY_TYPE).fillna("none")
    role = role.where(~((role == "generic") & ~df["mdrp_te_code"].map(is_a_rated)), "generic_not_a_rated")
    df["role"] = role

    unit = df["mdrp_unit_type"].fillna("UNK")
    is_bio = df["modality"] == pt.BIO
    has = df["clinical_name"].notna() & (df["role"] != "none")
    bio_name = df["clinical_name"].fillna("").map(strip_biosimilar_suffix)
    df["primary_key"] = None
    df.loc[has & ~is_bio, "primary_key"] = "scd:" + df["clinical_rxcui"] + "|" + unit
    df.loc[has & is_bio, "primary_key"] = "bio:" + bio_name + "|" + unit
    df["group_name"] = bio_name.where(is_bio, df["clinical_name"])
    df["loose_key"] = None
    df.loc[has, "loose_key"] = ("form:" + df["clinical_name"].fillna("").map(form_key) + "|" + unit)[has]
    df["loose_group_name"] = df["clinical_name"].fillna("").map(form_key).where(has)

    df["rebate_min_rate"] = [minimum_rebate_rate(c, cf, pe, r) for c, cf, pe, r in
                             zip(df["mdrp_drug_category"], df["mdrp_clotting_factor"], df["mdrp_pediatric"], df["role"])]
    print(f"  {has.sum():,} of {len(df):,} NDCs grouped; roles: {df['role'].value_counts().to_dict()}")
    print(f"  RxNav calls: {rx.n_network:,} network, {rx.n_cached:,} cached")
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)
    df = build(args.year)
    df.to_parquet(output_path(args.year), index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
