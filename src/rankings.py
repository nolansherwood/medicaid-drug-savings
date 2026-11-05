"""Rank drugs (ingredients) within each product type, by volume and by spending.

Joins the SDUD fact table to the RxNav crosswalk (ingredient, ATC) and the
product type table (src.product_type), then writes to outputs/:

  category_summary_{tag}.csv/.parquet        prescriptions and spending per product type
  top_by_product_type_{tag}.csv/.parquet     top N ingredients per product type, both measures
  top_overall_{tag}.csv/.parquet             top N ingredients across all types, with product type split
  ingredient_product_type_split_{tag}.csv    every ingredient x product type (x unbranded flag)

{tag} is the year, plus the state for a state run (e.g. 2025_CA).

Ranking unit: the ingredient within a product type, summing all of its NDCs
of that type (brand Lipitor and generic atorvastatin rank in different
categories). Shares are of the whole category, including NDCs that have no
ingredient; those NDCs are not ranked and are counted in the summary.

National rankings use the national (XX) rows. State rankings use that
state's rows, which undercount small drugs because of suppression.

Caveats carried into every output: spending is before manufacturer rebates;
it includes dispensing fees; prescriptions are not adjusted for days supply.

Usage:
    python -m src.rankings --year 2025 [--state CA] [--top 25]
"""
from __future__ import annotations

import argparse
import sys

import duckdb
import pandas as pd

from src import config
from src.crosswalk import CROSSWALK_PATH
from src.product_type import PRODUCT_TYPES, output_paths as product_type_paths

DEFAULT_TOP_N = 25
ALL_TYPES = "All product types"


def drug_rows(con: duckdb.DuckDBPyConnection, year: int, state: str | None) -> None:
    """Create view `drug` : one row per NDC with measures, ingredient and product type."""
    if state is None:
        fact, where = config.national_fact_path(year), ""
    else:
        fact, where = config.state_fact_path(year), f"WHERE state = '{state}'"
    pt_path = product_type_paths(year)[0]
    con.execute(f"""
        CREATE OR REPLACE VIEW drug AS
        WITH f AS (
            SELECT ndc,
                   coalesce(sum(number_of_prescriptions), 0) AS prescriptions,
                   coalesce(sum(medicaid_amount_reimbursed), 0)::DOUBLE AS medicaid_amount
            FROM read_parquet('{fact}') {where}
            GROUP BY ndc)
        SELECT f.*, c.ingredient_rxcui, c.ingredient_name, c.atc2, c.atc2_name,
               p.modality, p.product_type, coalesce(p.unbranded_biologic, false) AS unbranded_biologic
        FROM f
        LEFT JOIN read_parquet('{CROSSWALK_PATH}') c USING (ndc)
        LEFT JOIN read_parquet('{pt_path}') p USING (ndc)
    """)
    missing, = con.execute("SELECT count(*) FROM drug WHERE product_type IS NULL").fetchone()
    if missing:
        raise RuntimeError(f"{missing} NDCs have no product type; rerun src.product_type --year {year}")


def category_summary(con) -> pd.DataFrame:
    df = con.execute("""
        SELECT modality, product_type,
               sum(prescriptions) AS prescriptions,
               sum(medicaid_amount) AS medicaid_amount,
               count(DISTINCT ingredient_rxcui) AS ingredients,
               sum(prescriptions) FILTER (WHERE ingredient_rxcui IS NULL) AS prescriptions_unranked,
               sum(medicaid_amount) FILTER (WHERE ingredient_rxcui IS NULL) AS medicaid_amount_unranked
        FROM drug GROUP BY ALL
    """).df()
    df["share_prescriptions"] = df["prescriptions"] / df["prescriptions"].sum()
    df["share_spending"] = df["medicaid_amount"] / df["medicaid_amount"].sum()
    df["cost_per_prescription"] = df["medicaid_amount"] / df["prescriptions"].where(df["prescriptions"] > 0)
    order = {pt: i for i, (_, pt) in enumerate(PRODUCT_TYPES)}
    return df.sort_values("product_type", key=lambda s: s.map(order)).reset_index(drop=True)


def _rank(df: pd.DataFrame, group_cols: list[str], top_n: int) -> pd.DataFrame:
    """Top N per group by each measure; long format with a `measure` column."""
    out = []
    for measure, sort_cols in (("volume", ["prescriptions", "medicaid_amount"]),
                               ("spending", ["medicaid_amount", "prescriptions"])):
        d = df.sort_values(group_cols + sort_cols + ["ingredient_name"],
                           ascending=[True] * len(group_cols) + [False, False, True])
        d = d.assign(rank=d.groupby(group_cols).cumcount() + 1)
        out.append(d[d["rank"] <= top_n].assign(measure=measure))
    return pd.concat(out, ignore_index=True)


def top_by_product_type(con, top_n: int) -> pd.DataFrame:
    df = con.execute("""
        WITH cat AS (SELECT product_type, sum(prescriptions) AS cat_rx, sum(medicaid_amount) AS cat_amt
                     FROM drug GROUP BY 1)
        SELECT d.modality, d.product_type, d.ingredient_rxcui, any_value(d.ingredient_name) AS ingredient_name,
               any_value(d.atc2) AS atc2, any_value(d.atc2_name) AS atc2_name,
               sum(d.prescriptions) AS prescriptions, sum(d.medicaid_amount) AS medicaid_amount,
               count(*) AS ndcs,
               sum(d.prescriptions) / any_value(cat.cat_rx) AS share_of_category_prescriptions,
               sum(d.medicaid_amount) / any_value(cat.cat_amt) AS share_of_category_spending
        FROM drug d JOIN cat USING (product_type)
        WHERE d.ingredient_rxcui IS NOT NULL
        GROUP BY d.modality, d.product_type, d.ingredient_rxcui
    """).df()
    df["cost_per_prescription"] = df["medicaid_amount"] / df["prescriptions"].where(df["prescriptions"] > 0)
    return _rank(df, ["product_type"], top_n)


def ingredient_split(con) -> pd.DataFrame:
    """Every ingredient x product type x unbranded flag, with shares within the ingredient."""
    df = con.execute("""
        SELECT ingredient_rxcui, any_value(ingredient_name) AS ingredient_name, modality, product_type,
               unbranded_biologic,
               sum(prescriptions) AS prescriptions, sum(medicaid_amount) AS medicaid_amount
        FROM drug WHERE ingredient_rxcui IS NOT NULL
        GROUP BY ingredient_rxcui, modality, product_type, unbranded_biologic
    """).df()
    tot = df.groupby("ingredient_rxcui")[["prescriptions", "medicaid_amount"]].transform("sum")
    df["share_of_ingredient_prescriptions"] = df["prescriptions"] / tot["prescriptions"].where(tot["prescriptions"] > 0)
    df["share_of_ingredient_spending"] = df["medicaid_amount"] / tot["medicaid_amount"].where(tot["medicaid_amount"] > 0)
    return df.sort_values(["ingredient_name", "medicaid_amount"], ascending=[True, False]).reset_index(drop=True)


def top_overall(con, split: pd.DataFrame, top_n: int) -> pd.DataFrame:
    df = con.execute("""
        SELECT ingredient_rxcui, any_value(ingredient_name) AS ingredient_name,
               any_value(atc2) AS atc2, any_value(atc2_name) AS atc2_name,
               sum(prescriptions) AS prescriptions, sum(medicaid_amount) AS medicaid_amount, count(*) AS ndcs
        FROM drug WHERE ingredient_rxcui IS NOT NULL GROUP BY ingredient_rxcui
    """).df()
    tot_rx, tot_amt = con.execute("SELECT sum(prescriptions), sum(medicaid_amount) FROM drug").fetchone()
    df["share_of_category_prescriptions"] = df["prescriptions"] / tot_rx
    df["share_of_category_spending"] = df["medicaid_amount"] / tot_amt
    df["cost_per_prescription"] = df["medicaid_amount"] / df["prescriptions"].where(df["prescriptions"] > 0)

    # Product type split as readable text, largest share first, per measure.
    by_type = split.groupby(["ingredient_rxcui", "product_type"], as_index=False)[
        ["share_of_ingredient_prescriptions", "share_of_ingredient_spending"]].sum()

    def describe(col):
        kept = by_type[by_type[col] >= 0.005].sort_values(col, ascending=False)
        labels = kept["product_type"] + " " + kept[col].map("{:.0%}".format)
        return (labels.groupby(kept["ingredient_rxcui"]).agg("; ".join)
                .rename(col.replace("share_of_ingredient", "product_type_split")))

    df = df.join(describe("share_of_ingredient_prescriptions"), on="ingredient_rxcui")
    df = df.join(describe("share_of_ingredient_spending"), on="ingredient_rxcui")
    df["modality"], df["product_type"] = "All", ALL_TYPES
    return _rank(df, ["product_type"], top_n)


def run(year: int, state: str | None = None, top_n: int = DEFAULT_TOP_N) -> dict[str, pd.DataFrame]:
    con = duckdb.connect()
    drug_rows(con, year, state)
    split = ingredient_split(con)
    return {
        "category_summary": category_summary(con),
        "top_by_product_type": top_by_product_type(con, top_n),
        "top_overall": top_overall(con, split, top_n),
        "ingredient_product_type_split": split,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--state", help="two-letter state code; omit for national")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args(argv)

    tag = f"{args.year}" + (f"_{args.state}" if args.state else "")
    tables = run(args.year, args.state, args.top)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(config.OUTPUTS / f"{name}_{tag}.csv", index=False)
        if name != "ingredient_product_type_split":
            df.to_parquet(config.OUTPUTS / f"{name}_{tag}.parquet", index=False)
        print(f"  {name}_{tag}: {len(df):,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
