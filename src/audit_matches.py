"""Audit the brand -> alternative matching for missed matches.

The matching keys on RxNorm (D-011, D-025), so this audit checks it against
sources that don't depend on RxNorm text, and lists every disagreement with
the dollars involved and the likely reason:

  A  biosimilars   each dispensed biosimilar vs. its FDA Purple Book reference
                   product: is the reference dispensed, and in the same group?
  B  generics      each dispensed A-rated generic (FDA Orange Book) vs. its
                   reference listed drug (the brand NDA with the same
                   ingredient, dosage form and strength): in the same group?
  C  near misses   brand and alternative with the same clinical drug (or the
                   same biologic reference and presentation) but grouped
                   apart because their MDRP billing units differ
  D  unmatchable   dispensed NDCs with no group at all (no RxNorm clinical
                   drug, or no product type), which can never get an
                   alternative
  E  exclusions    groups dropped from savings because brand and alternative
                   report units on different bases (D-022)

Inputs: data/processed/equivalence_ndc_{year}.parquet, the national SDUD
fact table, the Orange Book and Purple Book downloads (src.reference_data),
outputs/savings_excluded_unit_mismatch_{year}.csv.
Output: outputs/audit/match_audit_{year}.csv, one row per finding.

Usage:
    python -m src.audit_matches --year 2025
"""
from __future__ import annotations

import argparse
import sys

import duckdb
import pandas as pd

from src import config, equivalence
from src.explore import _ob

OUT_DIR = config.OUTPUTS / "audit"
BRAND_ROLES = {"brand", "reference"}
ALT_ROLES = {"generic", "authorized_generic", "biosimilar", "interchangeable"}


def load(year: int) -> pd.DataFrame:
    """Every NDC with its matching fields and national 2025 prescriptions and spending."""
    eq = pd.read_parquet(equivalence.output_path(year))
    use = duckdb.sql(f"""
        SELECT ndc, sum(number_of_prescriptions) AS rx, sum(medicaid_amount_reimbursed)::DOUBLE AS amt
        FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc""").df()
    df = eq.merge(use, on="ndc", how="left").fillna({"rx": 0, "amt": 0})
    df["dispensed"] = df["rx"] > 0
    df["name"] = df["proprietary_name"].fillna(df["nonproprietary_name"]).fillna("")
    return df


def finding(check, reason, action, brand, alt, brand_key, alt_key, brand_amt, alt_amt, note=""):
    return dict(check=check, reason=reason, action=action, brand=brand, alternative=alt, brand_key=brand_key,
                alternative_key=alt_key, brand_spending=round(brand_amt), alternative_spending=round(alt_amt),
                note=note)


def names(d: pd.DataFrame) -> str:
    return ", ".join(sorted(set(d["name"].str.strip()) - {""}))[:120]


def check_biosimilars(df: pd.DataFrame) -> list:
    """A: every dispensed biosimilar group should contain its Purple Book reference."""
    out = []
    by_biosimilar, ref_blas = equivalence.purple_book_references()
    d = df[df["dispensed"]]
    bios = d[d["role"].isin(["biosimilar", "interchangeable"])]
    for key, g in bios.groupby("primary_key", dropna=False):
        if pd.notna(key) and (d[d["primary_key"] == key]["role"] == "reference").any():
            continue
        refs = by_biosimilar.get(g["application_no"].iloc[0], {})
        ref_name = refs.get(str(g["proprietary_name"].iloc[0] or "").upper()) or next(iter(set(refs.values())), None)
        ref_ndcs = d[d["application_no"].isin(ref_blas.get(ref_name, [])) & (d["role"] == "reference")]
        pres = equivalence.presentation(str(g["clinical_name"].iloc[0] or ""))
        same_pres = ref_ndcs[ref_ndcs["clinical_name"].fillna("").map(equivalence.presentation) == pres]
        if pd.isna(key):
            reason, action = "biosimilar has no RxNorm clinical drug", "fix: map the NDC in RxNorm overrides"
        elif ref_name is None:
            reason, action = "no Purple Book reference for this BLA", "check the Purple Book entry"
        elif ref_ndcs.empty:
            reason, action = "reference product not dispensed in Medicaid", "none (no brand spending to switch)"
        elif not same_pres.empty:
            reason, action = "billing unit differs from the reference's", "review: unit conversion (see C)"
        else:
            reason, action = "reference not dispensed in this presentation", "none (different device or strength)"
        out.append(finding("A biosimilars", reason, action, ref_name or "", names(g), names(same_pres) and
                           same_pres["primary_key"].iloc[0] or "", key, same_pres["amt"].sum(), g["amt"].sum(),
                           f"reference presentations dispensed: {', '.join(sorted(set(ref_ndcs['clinical_name'].dropna().map(equivalence.presentation))))[:200]}"))
    return out


def check_generics(df: pd.DataFrame) -> list:
    """B: every dispensed A-rated generic should share a group with its reference listed drug."""
    ob = _ob("products.txt")
    ob["form"] = (ob["Ingredient"].str.upper().str.strip() + "|" + ob["DF;Route"].str.upper().str.strip() + "|"
                  + ob["Strength"].str.upper().str.replace(" ", "", regex=False))
    rld = ob[(ob["Appl_Type"] == "N") & (ob["RLD"] == "Yes")].groupby("form")["appl_no"].agg(set)
    anda = ob[(ob["Appl_Type"] == "A") & ob["TE_Code"].str.upper().str.startswith("A")]
    anda_rlds = anda.assign(rlds=anda["form"].map(rld)).dropna(subset=["rlds"]).groupby("appl_no")["rlds"].agg(
        lambda s: set().union(*s))

    d = df[df["dispensed"]]
    brands = d[d["role"] == "brand"]
    gens = d[d["product_type"].eq("Generic") & d["application_no"].isin(anda_rlds.index)].copy()
    gens["rlds"] = gens["application_no"].map(anda_rlds)
    out = []
    for (key, appl), g in gens.groupby(["primary_key", "application_no"], dropna=False):
        b = brands[brands["application_no"].isin(g["rlds"].iloc[0])]
        if b.empty:
            continue                                  # brand not dispensed: nothing to switch from
        if pd.notna(key) and (b["primary_key"] == key).any():
            continue                                  # matched
        rx = g["clinical_rxcui"].iloc[0]
        same_drug = b[b["clinical_rxcui"] == rx] if pd.notna(rx) else b.iloc[0:0]
        role = g["role"].iloc[0]
        brand_amt = same_drug["amt"].sum()
        if pd.isna(key):
            reason, action = "generic has no RxNorm clinical drug", "fix: map the NDC in RxNorm overrides"
        elif not same_drug.empty:
            # The brand may already be matched to other generics in its own billing unit.
            matched = d[d["primary_key"].isin(same_drug["primary_key"]) & d["role"].isin(ALT_ROLES)]
            reason, action = (("billing unit differs; brand matched to other generics", "none (price uses the others)")
                              if not matched.empty else
                              ("billing unit differs; brand has no other match", "review: unit conversion (see C)"))
        elif role == "generic_not_a_rated":
            reason, action = "MDRP has no A rating, Orange Book does", "review: accept Orange Book A rating"
            brand_amt = b["amt"].sum()
        else:
            # Usually a strength the brand didn't sell; where several brands share an Orange Book
            # strength (albuterol inhalers, metformin ER), the generic is rated against another one.
            reason, action = "brand not dispensed at this strength and form", "none (verify a sample)"
        out.append(finding("B generics", reason, action, names(b), names(g),
                           same_drug["primary_key"].iloc[0] if not same_drug.empty else "", key,
                           brand_amt, g["amt"].sum(),
                           f"ANDA {appl}; generic: {g['clinical_name'].iloc[0]}; brand forms dispensed: "
                           f"{'; '.join(sorted(set(b['clinical_name'].dropna())))[:200]}"))
    return out


def check_near_misses(df: pd.DataFrame) -> list:
    """C: same drug, split only by billing unit."""
    d = df[df["dispensed"] & df["primary_key"].notna()].copy()
    d["drug"] = d["primary_key"].str.rsplit("|", n=1).str[0]   # the key without its unit
    out = []
    for drug, g in d.groupby("drug"):
        if g["primary_key"].nunique() < 2:
            continue
        b, a = g[g["role"].isin(BRAND_ROLES)], g[g["role"].isin(ALT_ROLES)]
        for bk, bg in b.groupby("primary_key"):
            alts = a[a["primary_key"] != bk]
            if alts.empty or (a["primary_key"] == bk).any() or bg["amt"].sum() < 10_000:
                continue                              # nothing elsewhere, already matched here, or no real brand spending
            out.append(finding("C near misses", "same drug, different billing unit",
                               "review: unit conversion", names(bg), names(alts), bk,
                               ", ".join(sorted(alts["primary_key"].unique())), bg["amt"].sum(), alts["amt"].sum(),
                               f"units: brand {bk.rsplit('|', 1)[1]}, alternative "
                               f"{', '.join(sorted({k.rsplit('|', 1)[1] for k in alts['primary_key']}))}"))
    return out


def check_unmatchable(df: pd.DataFrame) -> list:
    """D: dispensed NDCs that never enter a group, by product type, with the largest products."""
    d = df[df["dispensed"] & df["primary_key"].isna()]
    out = []
    for pt_name, g in d.groupby(d["product_type"].fillna("unclassified")):
        why = "no product type role" if (g["role"] == "none").all() else "no RxNorm clinical drug"
        top = g.groupby("name")["amt"].sum().sort_values(ascending=False).head(8)
        action = ("none (OTC / unclassified, no switch target)" if pt_name.startswith("OTC")
                  else "review the largest: map in RxNorm overrides")
        out.append(finding("D unmatchable", why, action, pt_name, "", "", "", g["amt"].sum(), 0,
                           f"{len(g):,} NDCs; largest: " + "; ".join(f"{n} ${v / 1e6:,.1f}M" for n, v in top.items())))
    return out


def check_exclusions(year: int) -> list:
    """E: groups dropped from savings because units are on different bases (D-022)."""
    path = config.OUTPUTS / f"savings_excluded_unit_mismatch_{year}.csv"
    if not path.exists():
        return []
    ex = pd.read_csv(path)
    ex = ex[ex["match_level"] == "primary"] if "match_level" in ex else ex
    return [finding("E exclusions", "excluded: units per prescription differ (D-022)", "none (by design)",
                    "", "", r.get("group_key", ""), "", r.get("brand_spending", 0) or 0, 0, str(r.get("group_name", "")))
            for r in ex.to_dict("records")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)
    df = load(args.year)
    rows = (check_biosimilars(df) + check_generics(df) + check_near_misses(df) + check_unmatchable(df)
            + check_exclusions(args.year))
    out = pd.DataFrame(rows).sort_values(["check", "brand_spending"], ascending=[True, False])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"match_audit_{args.year}.csv"
    out.to_csv(path, index=False)
    summary = out.groupby(["check", "reason", "action"]).agg(
        findings=("reason", "size"), brand_spending=("brand_spending", "sum"),
        alternative_spending=("alternative_spending", "sum")).reset_index()
    for c in ("brand_spending", "alternative_spending"):
        summary[c] = (summary[c] / 1e6).round(1)
    print(f"  {path.name}: {len(out):,} findings ($M below)")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
