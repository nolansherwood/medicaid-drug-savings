"""Write the dashboard's data files: rankings and category totals, national and per state.

Runs src.rankings for the nation (state "US", from the national XX rows) and
for every state in the SDUD state table, and writes two CSVs to
outputs/dashboard/:

  rankings_{year}.csv          top N per category x measure, plus "All product types"
  category_summary_{year}.csv  prescriptions and spending per product type
  savings_top_{year}.csv       top switching opportunities (src.savings)
  savings_summary_{year}.csv   savings totals per geography x match level x tier
  savings_brands_{year}.csv    top brands by savings, all strengths and forms added up
  savings_brand_groups_{year}.csv  the strength/form rows behind each of those brands
  rebate_rates_{year}.csv      MACPAC FY 2024 rebates by state and the implied brand rate (D-016)

Each row carries `state`. State figures come from that state's own rows and
undercount small-volume drugs, because CMS suppresses rows with fewer than 11
prescriptions (see CLAUDE.md, Suppression).

Usage:
    python -m src.export_dashboard --year 2025 [--top 25]
"""
from __future__ import annotations

import argparse
import sys

import duckdb
import pandas as pd

from src import config, rankings

OUT_DIR = config.OUTPUTS / "dashboard"
NATIONAL = "US"

RANKING_COLUMNS = {
    "ingredient_name": "ingredient", "atc2_name": "therapeutic_class",
}


def states(year: int) -> list[str]:
    rows = duckdb.sql(f"SELECT DISTINCT state FROM read_parquet('{config.state_fact_path(year)}') ORDER BY 1")
    return [r[0] for r in rows.fetchall()]


def one(year: int, state: str | None, top_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    t = rankings.run(year, state, top_n)
    code = state or NATIONAL
    r = pd.concat([t["top_overall"], t["top_by_product_type"]], ignore_index=True)
    r = r.rename(columns=RANKING_COLUMNS).assign(state=code, category=r["product_type"])
    r = r[["state", "category", "measure", "rank", "ingredient", "therapeutic_class", "prescriptions",
           "medicaid_amount", "cost_per_prescription", "share_of_category_prescriptions",
           "share_of_category_spending", "product_type_split_prescriptions", "product_type_split_spending"]]
    s = t["category_summary"].assign(state=code)
    s = s[["state", "modality", "product_type", "ingredients", "prescriptions", "share_prescriptions",
           "medicaid_amount", "share_spending", "cost_per_prescription"]]
    return r, s


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    """Round for a compact file; keep counts as integers."""
    df = df.copy()
    df["prescriptions"] = df["prescriptions"].astype("int64")
    for c in df.columns:
        if c.startswith("share"):
            df[c] = df[c].round(5)
        elif c in ("medicaid_amount", "cost_per_prescription"):
            df[c] = df[c].round(2)
    return df


SAVINGS_TOP_N = {"primary": 25}
# The cross-strength "proof of concept" match (D-011) is unvetted, so it stays in
# outputs/savings_{year}.csv for analysis but is not exported to the public
# dashboard files (decision 2026-10-23).
PUBLIC_MATCH_LEVELS = ("primary",)
SAVINGS_COLUMNS = ["geo", "match_level", "tier", "group_key", "group_name", "ingredient", "brand_names",
                   "alternative_names", "therapeutic_class", "brand_prescriptions", "brand_spending",
                   "alt_share_of_units", "brand_cost_per_unit", "alt_cost_per_unit", "benchmark_source",
                   "gross_savings", "gross_savings_best_case", "min_rebate_savings", "avg_rebate_savings",
                   "brand_preferred_here", "brand_preferred_states", "savings_reverse_after_rebates",
                   "brand_min_rebate_rate", "brand_avg_rebate_rate", "alt_min_rebate_rate", "alt_avg_rebate_rate"]


SAVINGS_BRAND_TOP_N = 25


def brand_key(df: pd.DataFrame) -> pd.Series:
    """One brand = first listed brand name (case-insensitive) + ingredient, so every
    strength and form of Humira adds up, even when the names listed differ by
    strength ("STELARA" vs "STELARA; ustekinumab")."""
    first = df["brand_names"].fillna(df["ingredient"]).str.split(";").str[0].str.strip().str.lower()
    return first + "|" + df["ingredient"].fillna("")


def brand_rollup(pos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Top brands per geography x match level x tier view, and their member rows.

    pos: savings rows with gross_savings > 0 (one per geo x match level x tier x
    group). Tier view "all" keeps each group's best tier first (as savings_summary
    does), so a reference biologic's strength is not counted in both biosimilar
    tiers. Sums are plain sums over the brand's strengths and forms, so a brand's
    after-rebate savings can be negative when its switches cost more after rebates.
    """
    best = (pos.sort_values("gross_savings", ascending=False)
               .drop_duplicates(["geo", "match_level", "group_key"]).assign(tier_view="all"))
    rows = pd.concat([best, pos.assign(tier_view=pos["tier"])], ignore_index=True)
    rows["brand_key"] = brand_key(rows)
    rows = rows.sort_values("gross_savings", ascending=False)
    keys = ["geo", "match_level", "tier_view", "brand_key"]
    g = rows.groupby(keys, sort=False)
    brands = g.agg(
        brand=("brand_names", lambda s: s.fillna("").iloc[0].split(";")[0].strip()),
        ingredient=("ingredient", "first"),
        tiers=("tier", lambda s: ",".join(sorted(set(s)))),
        groups=("group_key", "size"),
        brand_prescriptions=("brand_prescriptions", "sum"),
        brand_spending=("brand_spending", "sum"),
        gross_savings=("gross_savings", "sum"),
        min_rebate_savings=("min_rebate_savings", "sum"),
        avg_rebate_savings=("avg_rebate_savings", "sum"),
        groups_reverse_after_rebates=("savings_reverse_after_rebates", "sum"),
        groups_brand_preferred_here=("brand_preferred_here", "sum"),
        brand_preferred_states=("brand_preferred_states",
                                lambda s: ",".join(sorted({x for v in s.dropna() for x in str(v).split(",") if x}))),
    ).reset_index()
    # Share already on the alternative, weighted by each strength's brand prescriptions.
    w = rows.assign(_w=rows["alt_share_of_units"] * rows["brand_prescriptions"]).groupby(keys, sort=False)["_w"].sum()
    brands["alt_share_of_units"] = (w.values / brands["brand_prescriptions"].where(brands["brand_prescriptions"] > 0)).round(4)
    brands["brand"] = brands["brand"].where(brands["brand"] != "", brands["ingredient"])
    brands = (brands.sort_values("gross_savings", ascending=False)
                    .groupby(["geo", "match_level", "tier_view"], group_keys=False).head(SAVINGS_BRAND_TOP_N))
    brands["rank"] = brands.groupby(["geo", "match_level", "tier_view"]).cumcount() + 1
    members = rows.merge(brands[keys], on=keys)
    members = members[keys + [c for c in SAVINGS_COLUMNS if c not in keys]]
    return brands, members


SAVINGS_ALT_ROLES = {"generic": ("generic", "authorized_generic"),
                     "interchangeable_biosimilar": ("interchangeable",),
                     "biosimilar": ("biosimilar",)}


def export_savings_ndcs(year: int, top: pd.DataFrame) -> None:
    """Brand and alternative NDCs for each opportunity row on the savings page.

    One row per (match_level, tier, group_key) appearing in savings_top, so the
    lists are stored once instead of once per state. NDCs dispensed nationally
    (national Medicaid spending > 0), 11 digits, highest spending first. Group key is
    equivalence.primary_key for match_level "primary", loose_key otherwise.
    Writes savings_ndcs_{year}.csv.
    """
    from src import equivalence
    eq = duckdb.sql(f"""
        WITH d AS (
            SELECT ndc, sum(medicaid_amount_reimbursed)::DOUBLE AS amt
            FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc)
        SELECT e.primary_key, e.loose_key, e.role, {NDC_FMT} AS ndc_fmt, d.amt
        FROM read_parquet('{equivalence.output_path(year)}') e JOIN d USING (ndc)
        WHERE d.amt > 0
    """).df()
    rows = []
    keys = top[["match_level", "tier", "group_key"]].drop_duplicates()
    for level, tier, key in keys.itertuples(index=False):
        col = "primary_key" if level == "primary" else "loose_key"
        g = eq[eq[col] == key].sort_values("amt", ascending=False)
        brand_role = "brand" if tier == "generic" else "reference"
        b = g[g["role"] == brand_role]["ndc_fmt"].tolist()
        a = g[g["role"].isin(SAVINGS_ALT_ROLES[tier])]["ndc_fmt"].tolist()
        rows.append({"match_level": level, "tier": tier, "group_key": key,
                     "n_brand_ndcs": len(b), "brand_ndcs": " ".join(b),
                     "n_alt_ndcs": len(a), "alt_ndcs": " ".join(a)})
    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / f"savings_ndcs_{year}.csv", index=False)
    print(f"  savings_ndcs_{year}.csv: {len(out):,} rows, {int(out['n_brand_ndcs'].sum() + out['n_alt_ndcs'].sum()):,} NDCs")


def export_savings(year: int) -> None:
    """Top opportunities per geography x match level x tier, and totals (savings page)."""
    from src import savings
    s = pd.read_parquet(savings.output_paths(year)[1])
    s = s[s["match_level"].isin(PUBLIC_MATCH_LEVELS)]
    pos = s[s["gross_savings"] > 0]
    top = (pos.sort_values("gross_savings", ascending=False)
              .groupby(["geo", "match_level", "tier"], group_keys=False)
              .apply(lambda g: g.head(SAVINGS_TOP_N[g.name[1]])))
    top = top[SAVINGS_COLUMNS].copy()
    top["key"] = top["geo"] + "|" + top["match_level"] + "|" + top["tier"] + "|" + top["group_key"]
    for c in ["brand_spending", "gross_savings", "gross_savings_best_case", "min_rebate_savings",
              "avg_rebate_savings"]:
        top[c] = top[c].round(0)
    for c in ["brand_cost_per_unit", "alt_cost_per_unit"]:
        top[c] = top[c].round(4)
    top["alt_share_of_units"] = top["alt_share_of_units"].round(4)
    top.to_csv(OUT_DIR / f"savings_top_{year}.csv", index=False)

    # The page ranks brands: every strength and form of a brand added up.
    brands, members = brand_rollup(pos)
    for df in (brands, members):
        for c in ["brand_spending", "gross_savings", "min_rebate_savings", "avg_rebate_savings"]:
            df[c] = df[c].round(0)
    for c in ["brand_cost_per_unit", "alt_cost_per_unit"]:
        members[c] = members[c].round(4)
    members["alt_share_of_units"] = members["alt_share_of_units"].round(4)
    brands.to_csv(OUT_DIR / f"savings_brands_{year}.csv", index=False)
    members.to_csv(OUT_DIR / f"savings_brand_groups_{year}.csv", index=False)
    print(f"  savings_brands_{year}.csv: {len(brands):,} rows; savings_brand_groups_{year}.csv: {len(members):,} rows")
    export_savings_ndcs(year, pd.concat([top, members], ignore_index=True))

    # Totals: only positive savings count as an opportunity in each scenario.
    def totals(g):
        return pd.Series({
            "groups": int((g["gross_savings"] > 0).sum()),
            "brand_spending": g.loc[g["gross_savings"] > 0, "brand_spending"].sum(),
            "gross_savings": g["gross_savings"].clip(lower=0).sum(),
            "min_rebate_savings": g["min_rebate_savings"].clip(lower=0).sum(),
            "avg_rebate_savings": g["avg_rebate_savings"].clip(lower=0).sum(),
            "groups_reverse_after_rebates": int(g["savings_reverse_after_rebates"].sum()),
            "groups_brand_preferred_here": int((g["brand_preferred_here"] & (g["gross_savings"] > 0)).sum()),
            "gross_savings_brand_preferred_here": g.loc[g["brand_preferred_here"], "gross_savings"].clip(lower=0).sum(),
        })
    by_tier = s.groupby(["geo", "match_level", "tier"]).apply(totals).reset_index()
    # A reference biologic can appear in both biosimilar tiers; the tiers are
    # alternatives, not additive, so "all" keeps each group's best switch.
    best = s.sort_values("gross_savings", ascending=False).drop_duplicates(["geo", "match_level", "group_key"])
    all_tiers = best.groupby(["geo", "match_level"]).apply(totals).reset_index().assign(tier="all")
    summ = pd.concat([all_tiers, by_tier], ignore_index=True)
    for c in summ.columns:
        if c not in ("geo", "match_level", "tier"):
            summ[c] = summ[c].round(0)
    summ.to_csv(OUT_DIR / f"savings_summary_{year}.csv", index=False)
    print(f"  savings_top_{year}.csv: {len(top):,} rows; savings_summary_{year}.csv: {len(summ):,} rows")


def export_rebates(year: int) -> None:
    """MACPAC rebate calibration (src.rebates) for the Rebates page, unchanged."""
    from src import rebates
    rates = rebates.load_rates()
    rates.to_csv(OUT_DIR / f"rebate_rates_{year}.csv", index=False)
    print(f"  rebate_rates_{year}.csv: {len(rates):,} rows")


# How a popup "product" is named; shared by the product and indication exports.
# Product name: FDA proprietary name, else the CMS rebate-file name, else SDUD's.
# Last resort for products with no name in any source (e.g. some supplements):
# label by the NDC's labeler-product code so each stays separate and traceable.
# Every query using this joins on `ndc`.
PRODUCT_EXPR = ("coalesce(p.proprietary_name, p.mdrp_product_name, NULLIF(trim(p.sdud_product_name), ''), "
                "'Unnamed product, NDC ' || left(ndc, 5) || '-' || substr(ndc, 6, 4))")
# Labeler: FDA (package or product match), else the CMS rebate-file labeler.
LABELER_EXPR = "coalesce(p.labeler_name, p.mdrp_labeler_name, 'Unknown labeler')"
INDICATION_MAX_CHARS = 600


def shorten(text: str, limit: int = INDICATION_MAX_CHARS) -> str:
    """Cut at the last sentence end before `limit` characters (else at a word)."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:end + 1] if end > limit * 0.4 else cut[:cut.rfind(" ")]) + " …"


# NDCs are shown as 11 plain digits, the billing format SDUD uses (user preference,
# 2026-10-23). A hyphenated 5-4-2 form would be:
#   left(ndc, 5) || '-' || substr(ndc, 6, 4) || '-' || right(ndc, 2)
NDC_FMT = "ndc"


def export_product_ndcs(year: int) -> None:
    """The NDCs behind each popup product (name + labeler + product type).

    One row per product, `ndcs` = its 11-digit NDCs, space-separated,
    highest national Medicaid spending first. National SDUD rows only, so a
    state view lists the product's NDCs nationally (the popup notes this).
    Writes product_ndcs_{year}.csv.
    """
    from src.product_type import output_paths as pt_paths
    df = duckdb.sql(f"""
        WITH d AS (
            SELECT ndc, coalesce(sum(medicaid_amount_reimbursed), 0)::DOUBLE AS amt
            FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc)
        SELECT {PRODUCT_EXPR} AS product, {LABELER_EXPR} AS labeler, p.product_type,
               count(*) AS n_ndcs,
               string_agg({NDC_FMT}, ' ' ORDER BY d.amt DESC, ndc) AS ndcs
        FROM d JOIN read_parquet('{pt_paths(year)[0]}') p USING (ndc)
        GROUP BY ALL
    """).df()
    used = pd.concat([pd.read_csv(OUT_DIR / f"products_{g}_{year}.csv", usecols=["product", "labeler", "product_type"])
                      for g in ("us", "states")]).dropna(subset=["product_type"]).drop_duplicates()
    df = df.merge(used, on=["product", "labeler", "product_type"])
    df.to_csv(OUT_DIR / f"product_ndcs_{year}.csv", index=False)
    print(f"  product_ndcs_{year}.csv: {len(df):,} products, {int(df['n_ndcs'].sum()):,} NDCs")


def export_indications(year: int) -> None:
    """FDA-approved indication per popup product (hover text on the product name).

    Joins each popup product (name + labeler) to the FDA label of its 9-digit
    product code with the most national Medicaid spending:
      left(sdud.ndc, 9) = label_indications.product9   (src.indications)
    Writes indications_{year}.csv: product, labeler, indication, label_brand_name,
    set_id (DailyMed), effective_time.
    """
    from src.indications import OUT_PATH as LABELS
    from src.product_type import output_paths as pt_paths
    df = duckdb.sql(f"""
        WITH d AS (
            SELECT ndc, sum(medicaid_amount_reimbursed)::DOUBLE AS amt
            FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc
            UNION ALL
            SELECT ndc, sum(medicaid_amount_reimbursed)::DOUBLE
            FROM read_parquet('{config.state_fact_path(year)}') GROUP BY ndc),
        prod AS (
            SELECT {PRODUCT_EXPR} AS product, {LABELER_EXPR} AS labeler, left(d.ndc, 9) AS product9,
                   sum(d.amt) AS amt
            FROM d JOIN read_parquet('{pt_paths(year)[0]}') p USING (ndc)
            GROUP BY ALL)
        SELECT product, labeler, l.indication, l.label_brand_name, l.set_id, l.effective_time
        FROM prod JOIN read_parquet('{LABELS}') l USING (product9)
        QUALIFY row_number() OVER (PARTITION BY product, labeler ORDER BY amt DESC NULLS LAST) = 1
    """).df()
    used = pd.concat([pd.read_csv(OUT_DIR / f"products_{g}_{year}.csv", usecols=["product", "labeler"])
                      for g in ("us", "states")]).drop_duplicates()
    df = df.merge(used, on=["product", "labeler"])
    df["indication"] = df["indication"].map(shorten)
    df.to_csv(OUT_DIR / f"indications_{year}.csv", index=False)
    print(f"  indications_{year}.csv: {len(df):,} products with an FDA label "
          f"(of {len(used):,} popup products)")


def export_products(year: int) -> None:
    """Products behind each ranked ingredient (the ingredient popup on the rankings page).

    One row per geography x ingredient x product, where a product is a
    proprietary name + labeler + product type (all strengths and package sizes
    of that product combined). Only ingredients that appear in that
    geography's rankings are included. Every product is kept, nationally and
    for each state (D-024).

    Writes products_us_{year}.csv and products_states_{year}.csv.
    """
    from src.crosswalk import CROSSWALK_PATH
    from src.product_type import output_paths as pt_paths
    df = duckdb.sql(f"""
        WITH d AS (
            SELECT 'US' AS geo, ndc, sum(number_of_prescriptions) AS rx,
                   sum(medicaid_amount_reimbursed)::DOUBLE AS amt, sum(units_reimbursed)::DOUBLE AS units
            FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc
            UNION ALL
            SELECT state, ndc, sum(number_of_prescriptions), sum(medicaid_amount_reimbursed)::DOUBLE,
                   sum(units_reimbursed)::DOUBLE
            FROM read_parquet('{config.state_fact_path(year)}') GROUP BY state, ndc)
        SELECT d.geo, c.ingredient_name AS ingredient,
               {PRODUCT_EXPR} AS product,
               {LABELER_EXPR} AS labeler,
               p.product_type,
               string_agg(DISTINCT lower(p.dosage_form), '; ') AS forms,
               string_agg(DISTINCT p.mdrp_unit_type, '/') AS unit_type,
               count(DISTINCT d.ndc) AS ndcs,
               sum(d.rx) AS prescriptions, sum(d.amt) AS medicaid_amount, sum(d.units) AS units
        FROM d
        JOIN read_parquet('{CROSSWALK_PATH}') c USING (ndc)
        JOIN read_parquet('{pt_paths(year)[0]}') p USING (ndc)
        WHERE c.ingredient_name IS NOT NULL AND d.rx > 0
        GROUP BY ALL
    """).df()
    ranked = pd.read_csv(OUT_DIR / f"rankings_{year}.csv", usecols=["state", "ingredient"]).drop_duplicates()
    df = df.merge(ranked.rename(columns={"state": "geo"}), on=["geo", "ingredient"])
    df = df.sort_values(["geo", "ingredient", "medicaid_amount"], ascending=[True, True, False])

    us = df[df["geo"] == NATIONAL]
    states = df[df["geo"] != NATIONAL]
    for name, part in (("products_us", us), ("products_states", states)):
        part = part.copy()
        part["cost_per_prescription"] = (part["medicaid_amount"] / part["prescriptions"]).round(2)
        # Cost per unit only when the product has one billing unit type.
        one_unit = part["unit_type"].fillna("").str.fullmatch(r"[A-Z]+") & (part["units"].fillna(0) > 0)
        part["cost_per_unit"] = (part["medicaid_amount"] / part["units"]).where(one_unit).round(4)
        part["medicaid_amount"] = part["medicaid_amount"].round(0)
        part["prescriptions"] = part["prescriptions"].astype("int64")
        # Rows are identified on the page by geo + ingredient + product + labeler +
        # product_type, so no separate key column is written (keeps the file small).
        part = part.drop(columns=["units"])
        part.to_csv(OUT_DIR / f"{name}_{year}.csv", index=False)
        print(f"  {name}_{year}.csv: {len(part):,} rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--top", type=int, default=rankings.DEFAULT_TOP_N)
    parser.add_argument("--savings-only", action="store_true", help="only rewrite the savings page files")
    parser.add_argument("--products-only", action="store_true", help="only rewrite the ingredient popup files")
    args = parser.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.products_only:
        export_products(args.year)
        export_indications(args.year)
        export_product_ndcs(args.year)
        return 0
    export_savings(args.year)
    export_rebates(args.year)
    if args.savings_only:
        return 0

    parts = [one(args.year, None, args.top)] + [one(args.year, s, args.top) for s in states(args.year)]
    r = tidy(pd.concat([p[0] for p in parts], ignore_index=True))
    s = tidy(pd.concat([p[1] for p in parts], ignore_index=True))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    r.to_csv(OUT_DIR / f"rankings_{args.year}.csv", index=False)
    s.to_csv(OUT_DIR / f"category_summary_{args.year}.csv", index=False)
    print(f"  rankings_{args.year}.csv: {len(r):,} rows, {r['state'].nunique()} geographies")
    print(f"  category_summary_{args.year}.csv: {len(s):,} rows")
    export_products(args.year)   # depends on the rankings file written above
    export_indications(args.year)
    export_product_ndcs(args.year)
    return 0


if __name__ == "__main__":
    sys.exit(main())
