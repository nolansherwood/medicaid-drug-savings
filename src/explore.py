"""Data for the Explore Alternatives page: every product's equivalents, prices, and why none exist.

For each presentation group (src.equivalence primary_key: same RxNorm SCD and
unit type for small molecules; same presentation without biosimilar suffix for
biologics) this lists every product dispensed in Medicaid nationally, with its
cost per unit and per prescription. For each brand or reference-biologic
presentation it records a status (D-021):

  alternatives               an equivalent generic / authorized generic /
                             biosimilar was dispensed in Medicaid this year
  only_non_equivalent        generics were dispensed, but none FDA A-rated
  other_strengths            no equivalent in this strength/form, but one exists
                             for another strength or form (loose_key)
  approved_not_marketed      FDA approved an A-rated generic (Orange Book ANDA
                             for the same ingredient + dosage form + route), but
                             none was dispensed (often delayed by patents or
                             settlements)
  no_generic_approved        no ANDA in the Orange Book for that form; listed
                             patents and exclusivities are shown
  biosimilar_not_dispensed   Purple Book lists a biosimilar referencing this
                             product, but none was dispensed for this presentation
  no_biosimilar_licensed     no biosimilar in the Purple Book; the reference
                             product's exclusivity date is shown if listed
  unknown                    the brand's application is not in the Orange Book

Listed patents do not prove generics are blocked (patents can be challenged,
settled or found invalid); the page words them as "listed patents expire as
late as ...".

Outputs (outputs/dashboard/):
  explore_index_{year}.csv    one row per product (name + labeler), national totals; `pid`
  explore_groups_{year}.csv   one row per presentation group x product, national; `gid`
  explore_status_{year}.csv   one row per brand/reference presentation group; `sid`
  explore_states_{n}_{year}.csv   state figures for the rows above, in STATE_FILES files
      (13 states each, alphabetical by code) so the page loads only the file
      holding the selected state. Columns: state, kind (p = product, g = group
      member, s = savings), id (pid / gid / sid), rx, amt, units, gross, min_rebate,
      avg_rebate, reverse, preferred_here. State rows exclude suppressed cells
      (fewer than 11 prescriptions).

Usage:
    python -m src.explore --year 2025
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from datetime import date

import duckdb
import pandas as pd

from src import config, equivalence, savings
from src.export_dashboard import LABELER_EXPR, NDC_FMT, OUT_DIR, PRODUCT_EXPR
from src.product_type import output_paths as pt_paths
from src.reference_data import REF_RAW

TODAY = date.today()
STATE_FILES = 4


def state_buckets(states: list[str]) -> dict[str, int]:
    """State code -> file number (1..STATE_FILES). The page uses the same rule."""
    codes = sorted(states)
    size = -(-len(codes) // STATE_FILES)
    return {c: i // size + 1 for i, c in enumerate(codes)}
ALT_ROLES = {"brand": ("generic", "authorized_generic"),
             "reference": ("biosimilar", "interchangeable")}

# Orange Book exclusivity codes (prefix match), in plain words.
EXCLUSIVITY = [
    ("NCE", "new chemical entity"), ("ODE", "orphan drug"), ("NPP", "new patient population"),
    ("NP", "new product"), ("PED", "pediatric extension"), ("GAIN", "antibiotic (QIDP) extension"),
    ("CGT", "competitive generic therapy"), ("RTO", "prescription-to-OTC switch"), ("NS", "new strength"),
    ("NC", "new combination"), ("NDF", "new dosage form"), ("NR", "new route"), ("I-", "new indication"),
    ("D-", "new dosing regimen"), ("M-", "other new clinical use"), ("PC", "patent challenge (180-day generic)"),
]


def exclusivity_name(code: str) -> str:
    for prefix, name in EXCLUSIVITY:
        if code.startswith(prefix):
            return name
    return code


def _ob(member: str) -> pd.DataFrame:
    with zipfile.ZipFile(REF_RAW / "orange_book.zip") as z:
        df = pd.read_csv(z.open(member), sep="~", dtype=str, encoding="latin1", keep_default_na=False)
    df["appl_no"] = df["Appl_No"].str.lstrip("0").str.zfill(6)
    return df


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.replace("Approved Prior to Jan 1, 1982", "Jan 1, 1982"), errors="coerce")


def orange_book() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per NDA: forms, generic approvals for those forms, patents, exclusivities."""
    prod = _ob("products.txt")
    prod["form_key"] = prod["Ingredient"].str.upper().str.strip() + "|" + prod["DF;Route"].str.upper().str.strip()
    prod["approved"] = _date(prod["Approval_Date"])
    ndas = prod[prod["Appl_Type"] == "N"][["appl_no", "form_key"]].drop_duplicates()
    andas = prod[(prod["Appl_Type"] == "A") & prod["TE_Code"].str.upper().str.startswith("A")]
    first_generic = andas.groupby("form_key").agg(first_generic_approval=("approved", "min"),
                                                  n_generic_approvals=("appl_no", "nunique")).reset_index()
    nda = ndas.merge(first_generic, on="form_key", how="left").groupby("appl_no").agg(
        first_generic_approval=("first_generic_approval", "min"),
        n_generic_approvals=("n_generic_approvals", "sum")).reset_index()

    pat = _ob("patent.txt")
    pat = pat[(pat["Appl_Type"] == "N") & (pat["Delist_Flag"].str.upper() != "Y")]
    pat["expires"] = _date(pat["Patent_Expire_Date_Text"])
    pat = pat[pat["expires"].dt.date >= TODAY]
    pats = pat.groupby("appl_no").agg(latest_patent_expiry=("expires", "max"),
                                      n_patents=("Patent_No", "nunique")).reset_index()

    exc = _ob("exclusivity.txt")
    exc = exc[exc["Appl_Type"] == "N"].copy()
    exc["expires"] = _date(exc["Exclusivity_Date"])
    exc = exc[exc["expires"].dt.date >= TODAY]
    exc["label"] = exc["Exclusivity_Code"].map(exclusivity_name)
    exc = exc.sort_values("expires").drop_duplicates(["appl_no", "label"], keep="last")
    excs = exc.groupby("appl_no").apply(
        lambda g: "; ".join(f"{l} until {d:%b %-d, %Y}" for l, d in zip(g["label"], g["expires"]))
    ).rename("exclusivities").reset_index()
    return nda.merge(pats, on="appl_no", how="outer").merge(excs, on="appl_no", how="outer"), prod


def purple_book() -> pd.DataFrame:
    """Per reference BLA: biosimilars licensed against it, and its exclusivity dates."""
    lines = (REF_RAW / "purplebook.csv").read_text(encoding="utf-8", errors="replace").splitlines()
    head = [i for i, l in enumerate(lines) if l.startswith("N/R/U,")][-1]
    pb = pd.read_csv(io.StringIO("\n".join(lines[head:])), dtype=str, keep_default_na=False)
    pb = pb[pb["BLA Number"].str.fullmatch(r"\d+")].copy()
    pb["bla_no"] = pb["BLA Number"].str.zfill(6)
    pb["approved"] = pd.to_datetime(pb["Approval Date"], errors="coerce")
    ref = pb[pb["License Type"] == "351(a)"].copy()
    # A 351(a) product's own dates: its reference-product exclusivity ("Exclusivity
    # Expiration Date") and orphan exclusivity. Only dates not yet passed are kept.
    def future(col: str) -> pd.Series:
        d = pd.to_datetime(ref[col], errors="coerce")
        return d.where(d >= pd.Timestamp(TODAY))
    ref["ref_exclusivity"] = future("Exclusivity Expiration Date")
    ref["orphan_exclusivity"] = future("Orphan Exclusivity Exp. Date")
    refs = ref.groupby("bla_no").agg(
        ref_names=("Proprietary Name", lambda s: {n.strip().upper() for n in s if n.strip()}),
        ref_exclusivity=("ref_exclusivity", "max"), orphan_exclusivity=("orphan_exclusivity", "max")).reset_index()
    bios = pb[pb["License Type"].str.startswith("351(k)")]
    rows = []
    for r in refs.itertuples():
        match = bios[bios["Ref. Product Proprietary Name"].str.upper().str.strip().isin(r.ref_names)]
        first = match.sort_values("approved").drop_duplicates("Proprietary Name")
        names = [f"{n} ({d:%b %Y})" if pd.notna(d) else n
                 for n, d in zip(first["Proprietary Name"], first["approved"])]
        rows.append({"bla_no": r.bla_no, "biosimilars_licensed": "; ".join(names), "n_biosimilars": len(names),
                     "ref_exclusivity": r.ref_exclusivity, "orphan_exclusivity": r.orphan_exclusivity})
    return pd.DataFrame(rows)


def build(year: int) -> dict[str, pd.DataFrame]:
    eq_path = equivalence.output_path(year)
    members = duckdb.sql(f"""
        WITH s AS (
            SELECT ndc, sum(units_reimbursed)::DOUBLE AS units, sum(number_of_prescriptions) AS rx,
                   sum(medicaid_amount_reimbursed)::DOUBLE AS amt
            FROM read_parquet('{config.national_fact_path(year)}') GROUP BY ndc)
        SELECT {PRODUCT_EXPR} AS product, {LABELER_EXPR} AS labeler, p.product_type, p.modality,
               e.role, e.primary_key, e.loose_key, e.group_name, e.mdrp_unit_type AS unit_type,
               e.ingredient_name AS ingredient, p.application_no,
               sum(s.units) AS units, sum(s.rx) AS rx, sum(s.amt) AS amt,
               -- NDCs (11 digits) in this row, highest Medicaid spending first.
               string_agg({NDC_FMT}, ' ' ORDER BY s.amt DESC, ndc) AS ndcs
        FROM s
        JOIN read_parquet('{pt_paths(year)[0]}') p USING (ndc)
        JOIN read_parquet('{eq_path}') e USING (ndc)
        WHERE s.rx > 0
        GROUP BY ALL
    """).df()

    # --- index: one row per product -------------------------------------------
    index = (members.groupby(["product", "labeler"])
             .agg(ingredient=("ingredient", "first"), product_type=("product_type", "first"),
                  modality=("modality", "first"), prescriptions=("rx", "sum"), medicaid_amount=("amt", "sum"),
                  presentations=("primary_key", "nunique"))
             .reset_index())

    # --- groups: presentation x product, national prices --------------------------
    grouped = members[members["primary_key"].notna()]
    groups = (grouped.groupby(["primary_key", "product", "labeler", "product_type", "role"], dropna=False)
              .agg(presentation=("group_name", "first"), unit_type=("unit_type", "first"),
                   units=("units", "sum"), prescriptions=("rx", "sum"), medicaid_amount=("amt", "sum"),
                   ndcs=("ndcs", lambda s: " ".join(dict.fromkeys(" ".join(s).split()))))
              .reset_index().rename(columns={"primary_key": "group_key"}))
    groups["cost_per_unit"] = (groups["medicaid_amount"] / groups["units"]).where(groups["units"] > 0)
    groups["cost_per_prescription"] = groups["medicaid_amount"] / groups["prescriptions"]

    # --- status: one row per brand/reference presentation ---------------------------
    ob, _ = orange_book()
    pb = purple_book()
    loose_alt = set(grouped.loc[grouped["role"].isin(["generic", "authorized_generic", "biosimilar",
                                                      "interchangeable"]) & (grouped["units"] > 0), "loose_key"])
    rows = []
    for key, g in grouped.groupby("primary_key"):
        brands = g[g["role"].isin(["brand", "reference"])]
        if brands.empty:
            continue
        role = "reference" if (brands["role"] == "reference").any() else "brand"
        alts = g[g["role"].isin(ALT_ROLES[role]) & (g["units"] > 0)]
        app = brands.sort_values("amt", ascending=False)["application_no"].dropna()
        app = app.iloc[0] if len(app) else None
        r = {"group_key": key, "role": role, "application_no": app, "presentation": g["group_name"].iloc[0]}
        if len(alts):
            r["status"] = "alternatives"
        elif role == "brand" and (g["role"] == "generic_not_a_rated").any():
            r["status"] = "only_non_equivalent"
        elif brands["loose_key"].isin(loose_alt).any():
            r["status"] = "other_strengths"
        elif role == "brand":
            o = ob[ob["appl_no"] == app]
            if o.empty:
                r["status"] = "unknown"
            else:
                o = o.iloc[0]
                r["status"] = "approved_not_marketed" if pd.notna(o["first_generic_approval"]) else "no_generic_approved"
                r["first_generic_approval"] = o["first_generic_approval"]
                r["latest_patent_expiry"] = o["latest_patent_expiry"]
                r["n_patents"] = o["n_patents"]
                r["exclusivities"] = o["exclusivities"]
        else:
            b = pb[pb["bla_no"] == app]
            b = b.iloc[0] if len(b) else None
            licensed = b is not None and b["n_biosimilars"] > 0
            r["status"] = "biosimilar_not_dispensed" if licensed else "no_biosimilar_licensed"
            if b is not None:
                r["biosimilars_licensed"] = b["biosimilars_licensed"]
                r["ref_exclusivity"] = b["ref_exclusivity"]
                r["orphan_exclusivity"] = b["orphan_exclusivity"]
        rows.append(r)
    status = pd.DataFrame(rows)

    # Patent / exclusivity context also for brands that do have alternatives
    # in other forms (useful on the page), and savings estimates (US, best tier).
    s = pd.read_parquet(savings.output_paths(year)[1])
    s = s[(s["geo"] == "US") & (s["match_level"] == "primary")].sort_values("gross_savings", ascending=False)
    s = s.drop_duplicates("group_key")[["group_key", "tier", "alt_cost_per_unit", "gross_savings",
                                        "min_rebate_savings", "avg_rebate_savings", "brand_preferred_states",
                                        "savings_reverse_after_rebates"]]
    status = status.merge(s, on="group_key", how="left")
    # Presentations excluded from savings because brand and alternatives report
    # units on different bases (D-022): the page says per-unit prices aren't comparable.
    ex = pd.read_csv(config.OUTPUTS / f"savings_excluded_unit_mismatch_{year}.csv", usecols=["match_level", "group_key"])
    status["unit_mismatch"] = status["group_key"].isin(set(ex.loc[ex["match_level"] == "primary", "group_key"]))
    for c in ("first_generic_approval", "latest_patent_expiry", "ref_exclusivity", "orphan_exclusivity"):
        if c in status:
            status[c] = pd.to_datetime(status[c]).dt.strftime("%Y-%m-%d")
    index = index.sort_values("medicaid_amount", ascending=False).reset_index(drop=True)
    index.insert(0, "pid", index.index)
    groups = groups.reset_index(drop=True)
    groups.insert(0, "gid", groups.index)
    status = status.reset_index(drop=True)
    status.insert(0, "sid", status.index)
    return {"index": index, "groups": groups, "status": status, "states": state_rows(year, index, groups, status)}


def state_rows(year: int, index: pd.DataFrame, groups: pd.DataFrame, status: pd.DataFrame) -> pd.DataFrame:
    """Each state's prescriptions, spending and units for the national rows (by id)."""
    eq_path = equivalence.output_path(year)
    st = duckdb.sql(f"""
        WITH s AS (
            SELECT state, ndc, sum(units_reimbursed)::DOUBLE AS units, sum(number_of_prescriptions) AS rx,
                   sum(medicaid_amount_reimbursed)::DOUBLE AS amt
            FROM read_parquet('{config.state_fact_path(year)}') GROUP BY state, ndc)
        SELECT s.state, {PRODUCT_EXPR} AS product, {LABELER_EXPR} AS labeler, p.product_type,
               e.role, e.primary_key AS group_key, s.units, s.rx, s.amt
        FROM s
        JOIN read_parquet('{pt_paths(year)[0]}') p USING (ndc)
        LEFT JOIN read_parquet('{eq_path}') e USING (ndc)
        WHERE s.rx > 0
    """).df()
    prod = (st.groupby(["state", "product", "labeler"]).agg(rx=("rx", "sum"), amt=("amt", "sum")).reset_index()
              .merge(index[["pid", "product", "labeler"]], on=["product", "labeler"]))
    prod = prod.assign(kind="p", id=prod["pid"])
    keys = ["group_key", "product", "labeler", "product_type", "role"]
    mem = (st[st["group_key"].notna()].groupby(["state"] + keys, dropna=False)
             .agg(rx=("rx", "sum"), amt=("amt", "sum"), units=("units", "sum")).reset_index()
             .merge(groups[["gid"] + keys], on=keys))
    mem = mem.assign(kind="g", id=mem["gid"])
    sv = pd.read_parquet(savings.output_paths(year)[1])
    sv = sv[(sv["geo"] != "US") & (sv["match_level"] == "primary")].sort_values("gross_savings", ascending=False)
    sv = (sv.drop_duplicates(["geo", "group_key"])
            .rename(columns={"geo": "state", "min_rebate_savings": "min_rebate", "avg_rebate_savings": "avg_rebate",
                             "gross_savings": "gross", "savings_reverse_after_rebates": "reverse",
                             "brand_preferred_here": "preferred_here"})
            .merge(status[["sid", "group_key"]], on="group_key"))
    sv = sv.assign(kind="s", id=sv["sid"])
    cols = ["state", "kind", "id", "rx", "amt", "units", "gross", "min_rebate", "avg_rebate", "reverse", "preferred_here"]
    out = pd.concat([prod, mem, sv], ignore_index=True).reindex(columns=cols)
    out["rx"] = out["rx"].astype("Int64")
    for c in ("amt", "gross", "min_rebate", "avg_rebate"):
        out[c] = out[c].round(0).astype("Int64")
    out["units"] = out["units"].round(1)
    for c in ("reverse", "preferred_here"):
        out[c] = out[c].map({True: 1, False: 0}).astype("Int64")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)
    t = build(args.year)
    t["index"]["medicaid_amount"] = t["index"]["medicaid_amount"].round(0)
    g = t["groups"]
    for c in ("units", "medicaid_amount"):
        g[c] = g[c].round(2 if c == "units" else 0)
    g["cost_per_unit"] = g["cost_per_unit"].round(4)
    g["cost_per_prescription"] = g["cost_per_prescription"].round(2)
    st = t["status"]
    for c in ("alt_cost_per_unit",):
        st[c] = st[c].round(4)
    for c in ("gross_savings", "min_rebate_savings", "avg_rebate_savings"):
        st[c] = st[c].round(0)
    # Patents and exclusivities are kept only if unexpired on the build date, so the page says "as of".
    st["as_of"] = TODAY.isoformat()
    states = t.pop("states")
    for name, df in t.items():
        df.to_csv(OUT_DIR / f"explore_{name}_{args.year}.csv", index=False)
        print(f"  explore_{name}_{args.year}.csv: {len(df):,} rows")
    bucket = state_buckets(states["state"].unique().tolist())
    for n in range(1, STATE_FILES + 1):
        part = states[states["state"].map(bucket) == n].sort_values(["state", "kind", "id"])
        path = OUT_DIR / f"explore_states_{n}_{args.year}.csv"
        part.to_csv(path, index=False)
        print(f"  {path.name}: {len(part):,} rows, states {', '.join(sorted(part['state'].unique()))}")
    print(t["status"]["status"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
