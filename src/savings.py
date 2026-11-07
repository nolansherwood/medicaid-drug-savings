"""Estimate savings from switching brands to generics and reference biologics to biosimilars.

For every equivalence group (src.equivalence) and geography (US national
rows, and each state's own rows):

  from side  brand NDCs (small molecules) or reference biologics
  to side    A-rated generics + authorized generics (tier "generic"),
             interchangeable biosimilars (tier "interchangeable_biosimilar"),
             other biosimilars (tier "biosimilar")                     (D-013)

Benchmark (D-015): volume-weighted average Medicaid paid per unit of the to
side in the same group and geography. A state with too little alternative
volume (fewer than MIN_ALT_UNITS units and fewer than MIN_ALT_NDCS NDCs) uses
the national benchmark, flagged. The lowest-cost alternative NDC with real
volume gives a best case.

Savings, three ways (D-016):
  gross              brand paid - brand units x benchmark
  minimum rebates    each side net of its statutory floor
  average rebates    brand side net of the calibrated national brand rate
                     (MACPAC FY 2024); generics 13%; authorized generics and
                     biosimilars at the 23.1% floor (no evidence of large
                     inflation rebates on new or low-priced products, so
                     their net cost is not understated)

Brand-preferred flags (D-017) come from src/brand_preferred_lists.csv.

Usage:
    python -m src.savings --year 2025
"""
from __future__ import annotations

import argparse
import re
import sys

import duckdb
import numpy as np
import pandas as pd

from src import config, equivalence
from src.rebates import MIN_BRAND, MIN_GENERIC, load_rates

MIN_ALT_UNITS = 1000
MIN_ALT_NDCS = 2
LOWEST_MIN_SHARE = 0.01     # an NDC needs >= 1% of alternative units to set the best case
BOG_PATH = config.ROOT / "src" / "brand_preferred_lists.csv"

TIERS = {
    "generic": ("brand", ("generic", "authorized_generic")),
    "interchangeable_biosimilar": ("reference", ("interchangeable",)),
    "biosimilar": ("reference", ("biosimilar",)),
}


def output_paths(year: int):
    return config.OUTPUTS / f"savings_{year}.csv", config.OUTPUTS / f"savings_{year}.parquet"


def average_rate(role: str, mdrp_category: str | None, brand_rate: float) -> float:
    if role in ("brand", "reference"):
        return brand_rate
    if role == "generic":
        return MIN_GENERIC
    return MIN_BRAND  # authorized generics, biosimilars


# Device / packaging words that do not change which product a brand name means
# ("Lantus SoloStar" is Lantus). Anything else must match exactly (D-017 rev.).
DEVICE_WORDS = {"solostar", "kwikpen", "flextouch", "flexpen", "penfill", "pen", "pens", "vial", "vials",
                "autoinjector", "auto", "injector", "prefilled", "syringe", "cartridge"}


def norm_brand(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in DEVICE_WORDS)


def load_bog() -> pd.DataFrame:
    bog = pd.read_csv(BOG_PATH, dtype=str, keep_default_na=False)
    bog["key"] = bog["brand_name"].map(norm_brand)
    return bog


def bog_states_for(names: list[str], bog: pd.DataFrame) -> list[str]:
    """States whose list names one of these brands: exact brand-name match, ignoring
    device words. A prefix match flagged different products (Cleocin T for
    "Cleocin", Spiriva Respimat for "Spiriva", Lotemax SM for "Lotemax")."""
    normed = {norm_brand(n) for n in names if n}
    hits = bog[bog["key"].isin(normed)]
    return sorted(hits["state"].unique())


def ndc_measures(year: int) -> pd.DataFrame:
    """Units, prescriptions and paid amount per geography x NDC (US = national rows)."""
    return duckdb.sql(f"""
        SELECT 'US' AS geo, ndc, sum(units_reimbursed)::DOUBLE AS units,
               sum(number_of_prescriptions) AS rx, sum(medicaid_amount_reimbursed)::DOUBLE AS amt
        FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc
        UNION ALL
        SELECT state, ndc, sum(units_reimbursed)::DOUBLE, sum(number_of_prescriptions),
               sum(medicaid_amount_reimbursed)::DOUBLE
        FROM read_parquet('{config.state_fact_path(year)}') GROUP BY state, ndc
    """).df()


def _side(df: pd.DataFrame, key: str) -> pd.DataFrame:
    g = df.groupby(["geo", key])
    out = g.agg(units=("units", "sum"), reported_units=("reported_units", "sum"), rx=("rx", "sum"),
                amt=("amt", "sum"), ndcs=("ndc", "nunique"),
                min_net=("min_net_amt", "sum"), avg_net=("avg_net_amt", "sum"))
    return out.reset_index()


def compute(year: int, key: str, tier: str, eq: pd.DataFrame, m: pd.DataFrame, brand_rate: float) -> pd.DataFrame:
    from_role, to_roles = TIERS[tier]
    if "reported_units" not in m:                 # units not converted (D-027): reported = units
        m = m.assign(reported_units=m["units"])
    d = m.merge(eq[["ndc", key, "role", "rebate_min_rate", "mdrp_drug_category", "proprietary_name"]], on="ndc")
    d = d[d[key].notna() & (d["units"] > 0) & (d["amt"] > 0)]
    d["min_net_amt"] = d["amt"] * (1 - d["rebate_min_rate"])
    d["avg_net_amt"] = d["amt"] * (1 - np.array([average_rate(r, c, brand_rate)
                                                  for r, c in zip(d["role"], d["mdrp_drug_category"])]))
    frm = d[d["role"] == from_role]
    to = d[d["role"].isin(to_roles)]
    if frm.empty or to.empty:
        return pd.DataFrame()

    f = _side(frm, key)
    t = _side(to, key)
    # Lowest-cost alternative with real volume, per geo x group.
    to_ndc = to.assign(cpu=to["amt"] / to["units"])
    to_ndc = to_ndc.merge(t[["geo", key, "units"]].rename(columns={"units": "grp_units"}), on=["geo", key])
    lowest = (to_ndc[to_ndc["units"] >= LOWEST_MIN_SHARE * to_ndc["grp_units"]]
              .groupby(["geo", key])["cpu"].min().rename("to_cpu_lowest").reset_index())
    t = t.merge(lowest, on=["geo", key], how="left")
    t["to_cpu"] = t["amt"] / t["units"]
    t["to_min_rate"] = 1 - t["min_net"] / t["amt"]
    t["to_avg_rate"] = 1 - t["avg_net"] / t["amt"]
    t["sufficient"] = (t["reported_units"] >= MIN_ALT_UNITS) | (t["ndcs"] >= MIN_ALT_NDCS)

    tcols = ["units", "ndcs", "to_cpu", "to_cpu_lowest", "to_min_rate", "to_avg_rate", "sufficient"]
    local = t[["geo", key] + tcols]
    national = t[t["geo"] == "US"][[key] + tcols].add_prefix("us_").rename(columns={f"us_{key}": key})
    out = f.merge(local, on=["geo", key], how="left", suffixes=("", "_to")).merge(national, on=key, how="left")

    use_local = out["sufficient"].eq(True)
    use_us = ~use_local & out["us_sufficient"].eq(True)
    out = out[use_local | use_us].copy()
    use_local = use_local[out.index]
    pick = lambda a, b: np.where(use_local, out[a], out[b])  # noqa: E731
    out["benchmark_source"] = np.where(use_local, "same geography", "national (state alternative volume too low)")
    out["alt_cpu"] = pick("to_cpu", "us_to_cpu")
    out["alt_cpu_lowest"] = pick("to_cpu_lowest", "us_to_cpu_lowest")
    out["alt_min_rate"] = pick("to_min_rate", "us_to_min_rate")
    out["alt_avg_rate"] = pick("to_avg_rate", "us_to_avg_rate")

    switched = out["units"] * out["alt_cpu"]
    res = pd.DataFrame({
        "geo": out["geo"], "match_level": "primary" if key == "primary_key" else "proof_of_concept",
        "tier": tier, "group_key": out[key],
        "brand_prescriptions": out["rx"], "brand_units": out["units"], "brand_spending": out["amt"],
        "brand_cost_per_unit": out["amt"] / out["units"],
        "alt_units": out["units_to"].fillna(0), "alt_ndcs": out["ndcs_to"].fillna(0).astype(int),
        "alt_share_of_units": out["units_to"].fillna(0) / (out["units"] + out["units_to"].fillna(0)),
        "alt_cost_per_unit": out["alt_cpu"], "alt_lowest_cost_per_unit": out["alt_cpu_lowest"],
        "benchmark_source": out["benchmark_source"],
        "gross_savings": out["amt"] - switched,
        "gross_savings_best_case": out["amt"] - out["units"] * out["alt_cpu_lowest"],
        "min_rebate_savings": out["min_net"] - switched * (1 - out["alt_min_rate"]),
        "avg_rebate_savings": out["avg_net"] - switched * (1 - out["alt_avg_rate"]),
        # Effective rebate rates behind the two scenarios (spending-weighted over
        # the group's NDCs), for the Rebates page's net-price comparison.
        "brand_min_rebate_rate": 1 - out["min_net"] / out["amt"],
        "brand_avg_rebate_rate": 1 - out["avg_net"] / out["amt"],
        "alt_min_rebate_rate": out["alt_min_rate"], "alt_avg_rebate_rate": out["alt_avg_rate"],
    })
    return res


def describe_groups(eq: pd.DataFrame, key: str, name_col: str) -> pd.DataFrame:
    def brands(s):
        names = s.dropna().map(lambda n: n.strip()).unique()
        return "; ".join(sorted(names, key=str.lower)[:4])
    b = eq[eq["role"].isin(["brand", "reference"])].groupby(key).agg(brand_names=("proprietary_name", brands))
    a = eq[eq["role"].isin(["generic", "authorized_generic", "biosimilar", "interchangeable"])].groupby(key).agg(
        alternative_names=("proprietary_name", brands))
    g = eq.groupby(key).agg(group_name=(name_col, "first"), ingredient=("ingredient_name", "first"),
                            therapeutic_class=("atc2_name", "first"), modality=("modality", "first"))
    return g.join(b).join(a).reset_index().rename(columns={key: "group_key"})


UNIT_MISMATCH_RATIO = 10


def units_per_rx(eq: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """National units per prescription for each group's brand side and each tier's alternatives."""
    nat = m[m["geo"] == "US"].merge(eq[["ndc", "primary_key", "loose_key", "role"]], on="ndc")
    out = []
    for key, level in (("primary_key", "primary"), ("loose_key", "proof_of_concept")):
        for tier, (from_role, to_roles) in TIERS.items():
            for side, roles in (("brand", (from_role,)), ("alt", to_roles)):
                g = nat[nat["role"].isin(roles) & nat[key].notna()].groupby(key)[["units", "rx"]].sum()
                g = (g["units"] / g["rx"].where(g["rx"] > 0)).rename(f"{side}_upr")
                out.append(g.reset_index().rename(columns={key: "group_key"}).assign(match_level=level, tier=tier, side=side))
    df = pd.concat(out).pivot_table(index=["match_level", "tier", "group_key"], columns="side",
                                    values=[c for c in ("brand_upr", "alt_upr")], aggfunc="first")
    df.columns = [c[0] for c in df.columns]
    return df.reset_index()


def drop_unit_mismatches(res: pd.DataFrame, eq: pd.DataFrame, m: pd.DataFrame, year: int) -> pd.DataFrame:
    """Drop opportunities whose brand and alternatives report units on different bases (D-022).

    SDUD units are reported by states; a few NDCs are reported per package
    (exactly 1 unit per prescription) while their equivalents are per tablet or
    mL, so cost per unit is not comparable. Flagged when the national units per
    prescription of the brand side and the alternative side differ by more than
    UNIT_MISMATCH_RATIO times. Excluded rows are written to
    outputs/savings_excluded_unit_mismatch_{year}.csv.
    """
    upr = units_per_rx(eq, m)
    upr["ratio"] = upr["alt_upr"] / upr["brand_upr"]
    bad = upr[(upr["ratio"] > UNIT_MISMATCH_RATIO) | (upr["ratio"] < 1 / UNIT_MISMATCH_RATIO)]
    keys = set(zip(bad["match_level"], bad["tier"], bad["group_key"]))
    hit = [k in keys for k in zip(res["match_level"], res["tier"], res["group_key"])]
    excluded = res[hit].merge(bad, on=["match_level", "tier", "group_key"], how="left")
    excluded.to_csv(config.OUTPUTS / f"savings_excluded_unit_mismatch_{year}.csv", index=False)
    us = excluded[(excluded["geo"] == "US") & (excluded["match_level"] == "primary")]
    print(f"  unit-mismatch exclusions: {len(bad):,} groups; national primary gross ${us['gross_savings'].clip(lower=0).sum()/1e6:,.1f}M")
    return res[[not h for h in hit]].reset_index(drop=True)


def run(year: int) -> pd.DataFrame:
    eq = pd.read_parquet(equivalence.output_path(year))
    m = ndc_measures(year)
    # Units on the group's common basis (D-027): e.g. per-pen units x 0.4 mL per pen. The
    # minimum-volume test (MIN_ALT_UNITS) keeps using the units as reported, so converting
    # doesn't change which benchmarks count as having enough volume.
    m["reported_units"] = m["units"]
    m["units"] = m["units"] * m["ndc"].map(eq.set_index("ndc")["unit_factor"]).fillna(1.0)
    rates = load_rates()
    brand_rate = float(rates.loc[rates["state"] == "US", "brand_rate_used"].iloc[0])

    parts = [compute(year, key, tier, eq, m, brand_rate)
             for key in ("primary_key", "loose_key") for tier in TIERS]
    res = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    res = drop_unit_mismatches(res, eq, m, year)
    names = pd.concat([describe_groups(eq, "primary_key", "group_name"),
                       describe_groups(eq, "loose_key", "loose_group_name")], ignore_index=True)
    res = res.merge(names.drop_duplicates("group_key"), on="group_key", how="left")

    bog = load_bog()
    # Match on the FDA's full product name: proprietary name + suffix
    # ("Spiriva" + "HandiHaler", "Advair" + "Diskus", "Xigduo" + "XR"), which is
    # how the state lists name products.
    from src.product_type import output_paths as pt_paths
    full = pd.read_parquet(pt_paths(year)[0], columns=["ndc", "proprietary_name", "proprietary_name_suffix"])
    full["full_name"] = (full["proprietary_name"].fillna("") + " " + full["proprietary_name_suffix"].fillna("")).str.strip()
    brands = eq[eq["role"].isin(["brand", "reference"])].merge(full[["ndc", "full_name"]], on="ndc", how="left")
    brand_names = brands.groupby("primary_key")["full_name"].apply(list)
    loose_names = brands.groupby("loose_key")["full_name"].apply(list)
    all_names = pd.concat([brand_names, loose_names])
    states_by_group = {k: bog_states_for(v, bog) for k, v in all_names.items()}
    res["brand_preferred_states"] = res["group_key"].map(lambda k: ",".join(states_by_group.get(k, [])))
    res["brand_preferred_here"] = [
        (g in s.split(",")) if g != "US" else False
        for g, s in zip(res["geo"], res["brand_preferred_states"])]
    res["savings_reverse_after_rebates"] = (res["gross_savings"] > 0) & (res["avg_rebate_savings"] < 0)
    res["avg_brand_rebate_rate_used"] = brand_rate
    return res.sort_values(["geo", "match_level", "gross_savings"], ascending=[True, True, False]).reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)
    res = run(args.year)
    csv_path, pq_path = output_paths(args.year)
    res.to_csv(csv_path, index=False)
    res.to_parquet(pq_path, index=False)
    us = res[(res["geo"] == "US")]
    summary = us.groupby(["match_level", "tier"]).agg(
        groups=("group_key", "size"), brand_spending=("brand_spending", "sum"),
        gross=("gross_savings", lambda s: s.clip(lower=0).sum()),
        min_rebate=("min_rebate_savings", lambda s: s.clip(lower=0).sum()),
        avg_rebate=("avg_rebate_savings", lambda s: s.clip(lower=0).sum()),
        reversals=("savings_reverse_after_rebates", "sum"))
    pd.set_option("display.width", 200)
    print((summary.assign(**{c: summary[c] / 1e9 for c in ["brand_spending", "gross", "min_rebate", "avg_rebate"]})
           .round(3)).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
