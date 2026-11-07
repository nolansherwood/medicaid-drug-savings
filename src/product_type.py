"""Classify every SDUD NDC by product type (the project's primary category).

Classification is by FDA approval pathway, never by product name (names are
only used for the branded_generic / unbranded_biologic flags). The hierarchy
and the reasoning behind every rule are in docs/decisions.md; rule IDs (D-xxx)
are cited inline.

Order of precedence for each NDC:
  1. src/product_type_overrides.csv (manual, documented)
  2. Transitioned biologics (src/transitioned_biologics.csv) -> Biologic
  3. Purple Book (for any biologic): 351(a) / 351(k) / interchangeable
  4. FDA NDC Directory marketing category
  5. MDRP: S/I/N splits NDA brands; COD status is the pathway fallback when
     the FDA directory has no record
  6. Otherwise "OTC / unapproved / unclassified"

Inputs: SDUD national tables, data/processed/reference/* (src.reference_data).
Outputs:
  data/processed/product_type_ndc_{year}.parquet
  outputs/product_type_disagreements_{year}.csv
  outputs/product_type_coverage_{year}.json

Usage:
    python -m src.product_type --year 2025
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile

import duckdb
import pandas as pd

from src import config
from src.reference_data import MDRP_COD_PATHWAY, REF_OUT, REF_RAW

OVERRIDES_PATH = config.ROOT / "src" / "product_type_overrides.csv"
TRANSITIONED_PATH = config.ROOT / "src" / "transitioned_biologics.csv"

# Product type labels (CLAUDE.md taxonomy).
SM, BIO, OTHER = "Small molecule", "Biologic", "Other"
BRAND_SS = "Brand, single source"
BRAND_GC = "Brand, generic competition"
AUTH_GENERIC = "Authorized generic"
GENERIC = "Generic"
REF_BIO = "Reference biologic"
BIOSIMILAR = "Biosimilar"
INTERCHANGEABLE = "Interchangeable biosimilar"
OTHER_TYPE = "OTC / unapproved / unclassified"

PRODUCT_TYPES = [(SM, BRAND_SS), (SM, BRAND_GC), (SM, AUTH_GENERIC), (SM, GENERIC),
                 (BIO, REF_BIO), (BIO, BIOSIMILAR), (BIO, INTERCHANGEABLE), (OTHER, OTHER_TYPE)]

# FDA NDC Directory marketing category -> pathway.
FDA_CATEGORY_PATHWAY = {
    "NDA": "NDA",
    "NDA AUTHORIZED GENERIC": "NDA_AG",
    "ANDA": "ANDA",
    "BLA": "BLA",
    "OTC MONOGRAPH DRUG": "OTC",
    "OTC MONOGRAPH FINAL": "OTC",
    "OTC MONOGRAPH NOT FINAL": "OTC",
}  # everything else (UNAPPROVED *, EMERGENCY USE AUTHORIZATION) -> UNAPPROVED


def output_paths(year: int):
    return (config.PROCESSED / f"product_type_ndc_{year}.parquet",
            config.OUTPUTS / f"product_type_disagreements_{year}.csv",
            config.OUTPUTS / f"product_type_coverage_{year}.json")


# --------------------------------------------------------------------------
# Name-based flags
# --------------------------------------------------------------------------

# Words that do not make a proprietary name a brand: salts, dosage forms,
# release types, packaging. "Metformin Hydrochloride ER" is unbranded.
NON_BRAND_WORDS = {
    "and", "with", "in", "of", "the", "usp", "nf",
    "hcl", "hydrochloride", "sodium", "potassium", "calcium", "magnesium", "besylate",
    "maleate", "mesylate", "succinate", "tartrate", "citrate", "acetate", "sulfate",
    "phosphate", "fumarate", "bromide", "chloride", "monohydrate", "dihydrate",
    "er", "xr", "sr", "xl", "la", "cr", "dr", "ec", "odt", "ir", "hct", "hctz",
    "extended", "release", "delayed", "immediate", "chewable", "disintegrating", "orally",
    "tablet", "tablets", "capsule", "capsules", "oral", "solution", "suspension", "syrup",
    "injection", "injectable", "cream", "ointment", "gel", "lotion", "patch", "spray",
    "inhaler", "inhalation", "nasal", "ophthalmic", "otic", "topical", "rectal", "vaginal",
    "kit", "pack", "pak", "pen", "vial", "syringe", "prefilled", "autoinjector", "auto",
    "injector", "single", "dose", "multi", "units", "unit", "mg", "ml", "mcg",
}


def _tokens(name: str | None) -> set[str]:
    if not name:
        return set()
    return {t for t in re.findall(r"[a-z]+", name.lower())}


def is_unbranded(proprietary: str | None, nonproprietary: str | None) -> bool | None:
    """True if the proprietary name is just the nonproprietary name (+ form words).

    Handles unbranded biologics listed as e.g. proprietary "Adalimumab" with
    nonproprietary "adalimumab-adaz": every proprietary word appears in the
    nonproprietary name. None when names are missing.
    """
    if not nonproprietary:
        return None
    brand_words = _tokens(proprietary) - NON_BRAND_WORDS
    return brand_words <= _tokens(nonproprietary)


# --------------------------------------------------------------------------
# Load inputs
# --------------------------------------------------------------------------

def load_inputs(con: duckdb.DuckDBPyConnection, year: int) -> pd.DataFrame:
    """One row per SDUD NDC with FDA, MDRP (as of the year) and Purple Book fields."""
    nat = config.national_fact_path(year)
    if not nat.exists():
        raise SystemExit(f"No SDUD national table for {year}; run src.ingest_sdud first.")
    # MDRP record as of the analysis year: the latest quarter at or before Q4
    # of that year; if none, the earliest later record (D-005).
    return con.execute(f"""
        WITH s AS (
            SELECT ndc, mode(product_name) AS sdud_product_name,
                   coalesce(sum(number_of_prescriptions), 0) AS prescriptions,
                   coalesce(sum(medicaid_amount_reimbursed), 0)::DOUBLE AS medicaid_amount
            FROM read_parquet('{nat}') GROUP BY ndc),
        m AS (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY ndc
                    ORDER BY (year * 10 + quarter) <= {year * 10 + 4} DESC,
                             CASE WHEN (year * 10 + quarter) <= {year * 10 + 4}
                                  THEN -(year * 10 + quarter) ELSE (year * 10 + quarter) END) AS rn
                FROM read_parquet('{REF_OUT / "mdrp.parquet"}'))
            WHERE rn = 1)
        SELECT s.*,
               -- FDA fields: the package listing (f) when the full NDC matches,
               -- else the product listing (fp) matched on the first 9 digits (D-018).
               CASE WHEN f.ndc IS NOT NULL THEN 'package' WHEN fp.product9 IS NOT NULL THEN 'product' END
                   AS fda_match_level,
               coalesce(f.listing_source, fp.listing_source) AS fda_listing_source,
               coalesce(f.marketing_category, fp.marketing_category) AS fda_marketing_category,
               coalesce(f.application_prefix, fp.application_prefix) AS fda_application_prefix,
               coalesce(f.application_no, fp.application_no) AS fda_application_no,
               coalesce(f.proprietary_name, fp.proprietary_name) AS proprietary_name,
               coalesce(f.proprietary_name_suffix, fp.proprietary_name_suffix) AS proprietary_name_suffix,
               coalesce(f.nonproprietary_name, fp.nonproprietary_name) AS nonproprietary_name,
               coalesce(f.substance_name, fp.substance_name) AS substance_name,
               coalesce(f.labeler_name, fp.labeler_name) AS labeler_name,
               coalesce(f.dosage_form, fp.dosage_form) AS dosage_form,
               coalesce(f.route, fp.route) AS route,
               m.year AS mdrp_record_year, m.quarter AS mdrp_record_quarter,
               m.mdrp_drug_category, m.mdrp_cod_status, m.mdrp_application_no, m.mdrp_product_name,
               m.mdrp_labeler_name,
               m.mdrp_unit_type, m.mdrp_te_code, m.mdrp_clotting_factor, m.mdrp_pediatric
        FROM s
        LEFT JOIN read_parquet('{REF_OUT / "fda_ndc.parquet"}') f USING (ndc)
        LEFT JOIN read_parquet('{REF_OUT / "fda_product.parquet"}') fp
               ON f.ndc IS NULL AND left(s.ndc, 9) = fp.product9
        LEFT JOIN m USING (ndc)
        ORDER BY ndc
    """).df()


def load_purple_book(con) -> dict[str, dict]:
    pb = con.execute(f"SELECT * FROM read_parquet('{REF_OUT / 'purple_book.parquet'}')").df()
    return {r["bla_no"]: r for r in pb.to_dict("records")}


def load_valid_applications(con) -> set[tuple[str, str]]:
    rows = con.execute(f"SELECT appl_type, appl_no FROM read_parquet('{REF_OUT / 'fda_applications.parquet'}')")
    return {(t, n) for t, n in rows.fetchall()}


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def fda_pathway(marketing_category: str | None) -> str | None:
    if not marketing_category:
        return None
    return FDA_CATEGORY_PATHWAY.get(marketing_category, "UNAPPROVED")


def mdrp_pathway(cod_status: str | None) -> str | None:
    if not cod_status:
        return None
    return MDRP_COD_PATHWAY.get(cod_status, "UNAPPROVED")


def matches_transitioned(names: list[str | None], patterns: list[str]) -> bool:
    text = " ".join(n.lower() for n in names if n)
    return any(re.search(rf"\b{re.escape(p)}\b", text) for p in patterns)


def classify_row(r: dict, pb: dict, valid_apps: set, patterns: list[str], overrides: dict) -> tuple[dict, list[dict]]:
    """Return (classification fields, list of disagreement records) for one NDC."""
    issues = []

    def log(check, detail, resolution):
        issues.append({"ndc": r["ndc"], "check": check, "detail": detail, "resolution": resolution})

    p_fda = fda_pathway(r["fda_marketing_category"])
    p_mdrp = mdrp_pathway(r["mdrp_cod_status"])
    pathway = p_fda or p_mdrp
    pathway_source = "fda_ndc_directory" if p_fda else ("mdrp" if p_mdrp else None)
    if p_fda and p_mdrp and p_fda != p_mdrp:
        log("pathway_fda_vs_mdrp", f"FDA {p_fda} vs MDRP {p_mdrp}", "FDA directory wins (precedence)")

    app_no = r["fda_application_no"] if p_fda else r["mdrp_application_no"]
    if r["fda_application_no"] and r["mdrp_application_no"] and r["fda_application_no"] != r["mdrp_application_no"] \
            and p_fda in ("NDA", "NDA_AG", "ANDA", "BLA"):
        log("application_no_fda_vs_mdrp", f"FDA {r['fda_application_no']} vs MDRP {r['mdrp_application_no']}",
            "FDA directory number used")

    out = {
        "pathway": pathway, "pathway_source": pathway_source, "application_no": app_no,
        "transitioned_biologic": False, "purple_book_match": None, "purple_book_bla_type": None,
        "interchangeable_partial": False, "nda_non_innovator": False, "mdrp_category_missing": False,
        "branded_generic": None, "unbranded_biologic": None, "application_validated": None,
        "other_reason": None, "classification_source": None, "competition_source": None,
    }

    # 1. Manual override.
    if r["ndc"] in overrides:
        o = overrides[r["ndc"]]
        out.update(modality=o["modality"], product_type=o["product_type"], classification_source="override")
        return out, issues

    # 2. Transitioned biologics: biologic whatever the listing says.
    names = [r["substance_name"], r["nonproprietary_name"], r["proprietary_name"], r["mdrp_product_name"],
             r["sdud_product_name"]]
    is_transitioned = pathway in ("NDA", "NDA_AG", "ANDA", "BLA") and matches_transitioned(names, patterns)
    out["transitioned_biologic"] = is_transitioned
    if is_transitioned and pathway != "BLA":
        log("transitioned_listed_as_" + pathway, f"{r['nonproprietary_name'] or r['mdrp_product_name']}",
            "classified as biologic (transitioned 2020)")

    # Validate NDA/ANDA/BLA numbers against Drugs@FDA + Orange Book.
    appl_type = {"NDA": "NDA", "NDA_AG": "NDA", "ANDA": "ANDA", "BLA": "BLA"}.get(pathway)
    if appl_type and app_no:
        ok = (appl_type, app_no) in valid_apps or (appl_type == "BLA" and app_no in pb) \
            or (is_transitioned and app_no in pb)
        out["application_validated"] = ok
        if not ok:
            log("application_not_in_fda_approvals", f"{appl_type} {app_no}", "flag only; classification unchanged")

    # 3. Biologics: Purple Book decides reference vs biosimilar.
    if pathway == "BLA" or is_transitioned:
        out["modality"] = BIO
        out["unbranded_biologic"] = is_unbranded(r["proprietary_name"], r["nonproprietary_name"])
        rec = pb.get(app_no) if app_no else None
        out["purple_book_match"] = rec is not None
        if rec is None:
            # Every 351(k) biosimilar is listed in the Purple Book, so an
            # unlisted BLA is treated as a reference product (D-006).
            out["product_type"] = REF_BIO
            out["classification_source"] = "bla_not_in_purple_book"
            log("bla_not_in_purple_book", f"BLA {app_no}", "classified Reference biologic (D-006)")
        else:
            out["purple_book_bla_type"] = rec["bla_type"]
            out["classification_source"] = "purple_book"
            if rec["bla_type"] == "351(a)":
                out["product_type"] = REF_BIO
            elif rec["any_interchangeable"]:
                out["product_type"] = INTERCHANGEABLE
                out["interchangeable_partial"] = not rec["all_interchangeable"]  # D-004
            else:
                out["product_type"] = BIOSIMILAR
        return out, issues

    cat = r["mdrp_drug_category"]
    # 4/5. Small molecules by pathway. NDA brands are split into single source
    # vs generic competition by market evidence (D-019): was an A-rated generic
    # or authorized generic of the same strength and form (RxNorm SCD + unit)
    # dispensed in Medicaid this year? The manufacturer-reported MDRP category
    # is only the fallback when the product's SCD is unknown, and is logged
    # wherever it disagrees with the market.
    if pathway == "NDA":
        out["modality"] = SM
        out["classification_source"] = pathway_source
        out["nda_non_innovator"] = cat == "N"
        evidence = r.get("generic_on_market")
        if evidence is not None and evidence == evidence:          # not None / NaN
            out["product_type"] = BRAND_GC if evidence else BRAND_SS
            out["competition_source"] = "medicaid_dispensing"
            mdrp_says = {"S": False, "I": True, "N": True}.get(cat)
            if mdrp_says is not None and mdrp_says != bool(evidence):
                log("mdrp_category_vs_market",
                    f"MDRP {cat}; matched generic dispensed in Medicaid: {'yes' if evidence else 'no'}",
                    f"{out['product_type']} (market evidence, D-019)")
        elif cat in ("I", "N"):
            out["product_type"] = BRAND_GC
            out["competition_source"] = "mdrp_category"
        else:
            out["product_type"] = BRAND_SS          # D-007 when MDRP is missing too
            out["competition_source"] = "mdrp_category" if cat == "S" else "none"
            if cat is None or cat != cat:
                out["mdrp_category_missing"] = True
                log("nda_without_mdrp_category", r["proprietary_name"] or "", "Brand, single source (D-007)")
        return out, issues

    if pathway == "NDA_AG":
        out.update(modality=SM, product_type=AUTH_GENERIC, classification_source=pathway_source)
        return out, issues

    if pathway == "ANDA":
        out.update(modality=SM, product_type=GENERIC, classification_source=pathway_source)
        unbranded = is_unbranded(r["proprietary_name"], r["nonproprietary_name"])
        out["branded_generic"] = None if unbranded is None else not unbranded
        if cat in ("S", "I"):
            log("anda_with_mdrp_innovator_category", f"MDRP {cat}", "Generic (FDA pathway wins)")
        return out, issues

    # 6. Everything else.
    out.update(modality=OTHER, product_type=OTHER_TYPE, classification_source=pathway_source or "no_source")
    out["other_reason"] = {"OTC": "OTC monograph", "UNAPPROVED": "unapproved drug",
                           None: "no FDA or MDRP record"}[pathway]
    if pathway == "UNAPPROVED" and r["fda_marketing_category"]:
        out["other_reason"] = r["fda_marketing_category"].lower()
    return out, issues


# ---------------------------------------------------------------------------
# Fallbacks for NDCs the MDRP file doesn't rate or give a unit (D-026)
# ---------------------------------------------------------------------------

def orange_book_a_rated() -> set:
    """ANDA numbers (6 digits) whose every Orange Book product has an "A" TE code.
    Used when MDRP has no TE code or "NR" (not rated); an explicit MDRP B code is kept."""
    with zipfile.ZipFile(REF_RAW / "orange_book.zip") as z:
        ob = pd.read_csv(z.open("products.txt"), sep="~", dtype=str, encoding="latin1", keep_default_na=False)
    anda = ob[ob["Appl_Type"] == "A"].assign(appl=ob["Appl_No"].str.lstrip("0").str.zfill(6),
                                             a=ob["TE_Code"].str.upper().str.startswith("A"))
    rated = anda.groupby("appl")["a"].all()
    return set(rated[rated].index)


def a_rated(te_code: pd.Series, application_no: pd.Series, ob_a: set) -> pd.Series:
    """MDRP TE code starting "A"; when MDRP has none or "NR", the Orange Book's rating (D-026)."""
    te = te_code.fillna("").str.upper().str.strip()
    return te.str.startswith("A") | (te.isin(["", "NR"]) & application_no.isin(ob_a))


def fill_units(unit: pd.Series, clinical_rxcui: pd.Series) -> pd.Series:
    """MDRP unit type; when MDRP has none, the most common unit of the other NDCs with the same
    RxNorm clinical drug (D-026). Still unknown: "UNK"."""
    known = pd.DataFrame({"c": clinical_rxcui, "u": unit}).dropna()
    mode = known.groupby("c")["u"].agg(lambda s: s.value_counts().index[0])
    return unit.fillna(clinical_rxcui.map(mode)).fillna("UNK")


_VOLUME = re.compile(r"^(\d*\.?\d+) ML ")
COUNT_UNITS = ("TAB", "CAP")


def common_units(unit: pd.Series, clinical_name: pd.Series, clinical_rxcui: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Put MDRP units on one basis per clinical drug (D-027). Returns (unit_type, unit_factor):
    reported units x unit_factor are in unit_type.
    - EA with a stated volume ("0.4 ML adalimumab 100 MG/ML Auto-Injector", "50 ML cefazolin ...
      Injection") is converted to ML at that volume. Packs ("{3 (0.8 ML ...) } Pack") are not:
      their volume is per item.
    - EA for a clinical drug whose other NDCs are billed per TAB or CAP counts as one of those."""
    name = clinical_name.fillna("")
    vol = name.str.extract(_VOLUME)[0].astype(float)
    factor = pd.Series(1.0, index=unit.index)
    by_volume = (unit == "EA") & vol.notna()
    unit = unit.where(~by_volume, "ML")
    factor[by_volume] = vol[by_volume]
    counted = pd.DataFrame({"c": clinical_rxcui, "u": unit})
    counted = counted[counted["u"].isin(COUNT_UNITS)].groupby("c")["u"].agg(lambda s: s.value_counts().index[0])
    as_count = (unit == "EA") & clinical_rxcui.map(counted).notna()
    unit = unit.where(~as_count, clinical_rxcui.map(counted))
    return unit, factor


def add_market_evidence(df: pd.DataFrame) -> pd.DataFrame:
    """Add clinical_rxcui and generic_on_market for the generic-competition rule (D-019).

    generic_on_market is True when some NDC with the same RxNorm SCD/GPCK and
    MDRP unit type is an A-rated generic (ANDA + MDRP TE code starting "A", or
    the Orange Book's A rating when MDRP has none, D-026) or an authorized
    generic, and had prescriptions in this year's national SDUD. A missing
    MDRP unit is taken from the other NDCs of the same clinical drug, and units
    are put on one basis per clinical drug as in the savings groups (D-027).
    None when the NDC's own SCD is unknown.
    """
    from src.crosswalk import CROSSWALK_PATH, parallel_map
    from src.rxnav import RxNav

    xw = duckdb.sql(f"SELECT ndc, product_rxcui FROM read_parquet('{CROSSWALK_PATH}')").df()
    df = df.merge(xw, on="ndc", how="left")
    rx = RxNav()
    products = sorted(df["product_rxcui"].dropna().unique())
    clinical = parallel_map(rx.clinical_concept, products, "clinical drug")
    df["clinical_rxcui"] = df["product_rxcui"].map(lambda p: clinical[p]["clinical_rxcui"] if isinstance(p, str) else None)
    clinical_name = df["product_rxcui"].map(lambda p: clinical[p]["clinical_name"] if isinstance(p, str) else None)

    pre = [fda_pathway(m) or mdrp_pathway(c) for m, c in zip(df["fda_marketing_category"], df["mdrp_cod_status"])]
    rated = a_rated(df["mdrp_te_code"], df["fda_application_no"].fillna(df["mdrp_application_no"]),
                    orange_book_a_rated())
    is_alt = (((pd.Series(pre, index=df.index) == "ANDA") & rated) | (pd.Series(pre, index=df.index) == "NDA_AG"))
    unit, _ = common_units(fill_units(df["mdrp_unit_type"], df["clinical_rxcui"]), clinical_name, df["clinical_rxcui"])
    on_market = df[is_alt & (df["prescriptions"] > 0) & df["clinical_rxcui"].notna()]
    keys = set(zip(on_market["clinical_rxcui"], unit[on_market.index]))
    df["generic_on_market"] = [
        ((c, u) in keys) if isinstance(c, str) else None for c, u in zip(df["clinical_rxcui"], unit)]
    df["generic_on_market"] = df["generic_on_market"].astype(object)
    return df


def classify(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect()
    df = load_inputs(con, year)
    pb = load_purple_book(con)
    valid = load_valid_applications(con)
    patterns = pd.read_csv(TRANSITIONED_PATH, dtype=str)["pattern"].str.lower().tolist()
    ov = pd.read_csv(OVERRIDES_PATH, dtype=str, keep_default_na=False)
    bad = ov[~ov["ndc"].str.fullmatch(r"\d{11}") |
             ~ov.apply(lambda o: (o["modality"], o["product_type"]) in PRODUCT_TYPES, axis=1)]
    if len(bad):
        raise ValueError(f"Invalid rows in {OVERRIDES_PATH.name}:\n{bad}")
    overrides = {o["ndc"]: o for o in ov.to_dict("records")}
    df = add_market_evidence(df)

    results, issues = [], []
    for r in df.to_dict("records"):
        out, iss = classify_row(r, pb, valid, patterns, overrides)
        results.append(out)
        issues.extend(iss)
    out = pd.concat([df, pd.DataFrame(results)], axis=1)

    issue_df = pd.DataFrame(issues, columns=["ndc", "check", "detail", "resolution"])
    issue_df = issue_df.merge(out[["ndc", "sdud_product_name", "proprietary_name", "labeler_name",
                                   "modality", "product_type", "prescriptions", "medicaid_amount"]],
                              on="ndc", how="left").sort_values("medicaid_amount", ascending=False)
    return out, issue_df


def coverage(df: pd.DataFrame, issues: pd.DataFrame, year: int) -> dict:
    tot_rx, tot_amt = df["prescriptions"].sum(), df["medicaid_amount"].sum()
    g = (df.groupby(["modality", "product_type"])
           .agg(ndcs=("ndc", "size"), prescriptions=("prescriptions", "sum"), medicaid_amount=("medicaid_amount", "sum"))
           .reset_index())
    g["share_prescriptions"] = (g["prescriptions"] / tot_rx).round(4)
    g["share_spending"] = (g["medicaid_amount"] / tot_amt).round(4)
    unclassified = df[df["product_type"] == OTHER_TYPE]

    def flag_size(col):
        sub = df[df[col] == True]  # noqa: E712
        return {"ndcs": int(len(sub)), "prescriptions": float(sub["prescriptions"].sum()),
                "medicaid_amount": float(sub["medicaid_amount"].sum()),
                "share_spending": round(float(sub["medicaid_amount"].sum() / tot_amt), 4)}

    return {
        "year": year,
        "basis": "SDUD national (XX) rows",
        "by_product_type": g.to_dict("records"),
        "share_spending_classified_excluding_other": round(1 - unclassified["medicaid_amount"].sum() / tot_amt, 4),
        "other_bucket_by_reason": (unclassified.groupby("other_reason")["medicaid_amount"].sum()
                                   .div(tot_amt).round(4).to_dict()),
        "flags": {c: flag_size(c) for c in ["nda_non_innovator", "mdrp_category_missing", "transitioned_biologic",
                                            "interchangeable_partial", "branded_generic", "unbranded_biologic"]},
        "pathway_source": df["pathway_source"].fillna("none").value_counts().to_dict(),
        "disagreements_by_check": issues.groupby("check").agg(
            ndcs=("ndc", "size"), medicaid_amount=("medicaid_amount", "sum")).reset_index().to_dict("records"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)

    out_path, issues_path, cov_path = output_paths(args.year)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)

    df, issues = classify(args.year)
    df.to_parquet(out_path, index=False)
    issues.to_csv(issues_path, index=False)
    rep = coverage(df, issues, args.year)
    cov_path.write_text(json.dumps(rep, indent=2, default=float))

    print(pd.DataFrame(rep["by_product_type"]).to_string(index=False))
    print(json.dumps({k: rep[k] for k in ("share_spending_classified_excluding_other",
                                          "other_bucket_by_reason", "flags", "pathway_source",
                                          "disagreements_by_check")}, indent=2, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
