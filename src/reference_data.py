"""Download and normalize the FDA and CMS reference files used for product type.

Sources (saved under data/raw/reference/, parsed to data/processed/reference/):
  - FDA NDC Directory: current (ndctext.zip) and excluded/discontinued
    (ndc_excluded.zip) product + package files. Marketing category and
    application number per NDC package.
  - FDA Purple Book: full product list; 351(a) vs 351(k), interchangeability.
  - FDA Orange Book (products.txt) and Drugs@FDA (Applications.txt): the set of
    real NDA/ANDA/BLA application numbers, used for validation only.
  - CMS Medicaid Drug Rebate Program (MDRP) product file: drug category
    S / I / N, COD status and FDA application number per NDC per quarter.

Usage:
    python -m src.reference_data [--force-download]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone

import duckdb
import pandas as pd
import requests

from src import config

REF_RAW = config.RAW / "reference"
REF_OUT = config.PROCESSED / "reference"

FDA_NDC_URL = "https://www.accessdata.fda.gov/cder/ndctext.zip"
FDA_NDC_EXCLUDED_URL = "https://www.accessdata.fda.gov/cder/ndc_excluded.zip"
ORANGE_BOOK_URL = "https://www.fda.gov/media/76860/download"
DRUGS_AT_FDA_URL = "https://www.fda.gov/media/89850/download"
PURPLE_BOOK_PAGE = "https://purplebooksearch.fda.gov/downloads"
MDRP_TITLE = "Drug Products in the Medicaid Drug Rebate Program"
CATALOG_URL = "https://data.medicaid.gov/api/1/metastore/schemas/dataset/items"

HEADERS = {"User-Agent": "Mozilla/5.0 (medicaid-drug-savings research pipeline)"}

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}

# FDA 10-digit NDC (hyphenated 4-4-2, 5-3-2 or 5-4-1) -> 11-digit 5-4-2.
# Padding each segment to 5/4/2 adds the leading zero to whichever segment is
# short, which is exactly the standard conversion. Inputs that are not one
# of the three layouts return NULL (and are counted in validation).
NDC10_TO_11_SQL = """
CASE WHEN regexp_full_match({col}, '\\d{{4}}-\\d{{4}}-\\d{{2}}|\\d{{5}}-\\d{{3}}-\\d{{2}}|\\d{{5}}-\\d{{4}}-\\d{{1}}')
     THEN lpad(split_part({col}, '-', 1), 5, '0')
       || lpad(split_part({col}, '-', 2), 4, '0')
       || lpad(split_part({col}, '-', 3), 2, '0')
END"""


def ndc10_to_11_sql(col: str) -> str:
    return NDC10_TO_11_SQL.format(col=col)


# MDRP COD status -> approval pathway. CMS does not ship the code list with
# the file; this mapping was inferred by cross-tabulating COD status against
# the FDA NDC Directory marketing category for the same NDCs (Oct 2026):
# 01 ~ ANDA, 02 ~ BLA, 03 ~ NDA, 04 ~ NDA authorized generic, 09/10 ~ OTC
# monograph, other codes ~ unapproved drugs (DESI, pre-1938, shortage, other).
# See docs/decisions.md D-005.
MDRP_COD_PATHWAY = {"01": "ANDA", "02": "BLA", "03": "NDA", "04": "NDA_AG",
                    "09": "OTC", "10": "OTC"}


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def _download(url: str, dest, force: bool) -> None:
    if dest.exists() and not force:
        print(f"  using existing {dest.name}")
        return
    print(f"  downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers=HEADERS, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def latest_purple_book_url() -> str:
    """Most recent monthly Purple Book CSV linked from the downloads page."""
    html = requests.get(PURPLE_BOOK_PAGE, headers=HEADERS, timeout=60).text
    links = re.findall(
        r'href="(https://[^"]*/PurpleBook/(\d{4})/purplebook-search-(\w+)-data-download\.csv)"', html)
    if not links:
        raise RuntimeError("No Purple Book CSV links found on downloads page")
    return max(links, key=lambda t: (int(t[1]), MONTHS.get(t[2].lower(), 0)))[0]


def mdrp_url() -> str:
    catalog = requests.get(CATALOG_URL, timeout=120).json()
    ds = [d for d in catalog if d.get("title", "").strip() == MDRP_TITLE]
    if len(ds) != 1:
        raise RuntimeError(f"Expected one catalog entry titled {MDRP_TITLE!r}")
    return ds[0]["distribution"][0]["downloadURL"]


def download_all(force: bool = False) -> dict:
    REF_RAW.mkdir(parents=True, exist_ok=True)
    meta_path = REF_RAW / "reference_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() and not force else {}

    sources = {
        "ndctext.zip": FDA_NDC_URL,
        "ndc_excluded.zip": FDA_NDC_EXCLUDED_URL,
        "orange_book.zip": ORANGE_BOOK_URL,
        "drugsatfda.zip": DRUGS_AT_FDA_URL,
    }
    if force or not (REF_RAW / "purplebook.csv").exists():
        sources["purplebook.csv"] = latest_purple_book_url()
    if force or not (REF_RAW / "mdrp_products.csv").exists():
        sources["mdrp_products.csv"] = mdrp_url()

    for name, url in sources.items():
        if force or not (REF_RAW / name).exists():
            _download(url, REF_RAW / name, force=True)
            meta[name] = {"url": url, "downloaded_at": datetime.now(timezone.utc).isoformat()}
        else:
            meta.setdefault(name, {"url": url, "downloaded_at": "unknown (pre-existing file)"})
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


# --------------------------------------------------------------------------
# Parse
# --------------------------------------------------------------------------

def _read_zip_tsv(zip_name: str, member: str) -> pd.DataFrame:
    with zipfile.ZipFile(REF_RAW / zip_name) as z:
        with z.open(member) as f:
            return pd.read_csv(f, sep="\t", dtype=str, encoding="latin1",
                               quoting=3, keep_default_na=False)  # quoting=3: none


def build_fda_ndc(con: duckdb.DuckDBPyConnection) -> None:
    """One row per 11-digit package NDC from the FDA NDC Directory.

    Current listings win over excluded/discontinued ones; within a source the
    most recently started listing wins.
    """
    frames = []
    for zip_name, prod, pack, source in (
        ("ndctext.zip", "product.txt", "package.txt", "current"),
        ("ndc_excluded.zip", "Products_excluded.txt", "Packages_excluded.txt", "excluded"),
    ):
        products = _read_zip_tsv(zip_name, prod)
        packages = _read_zip_tsv(zip_name, pack)[["PRODUCTID", "NDCPACKAGECODE", "STARTMARKETINGDATE",
                                                  "ENDMARKETINGDATE"]]
        packages = packages.rename(columns={"STARTMARKETINGDATE": "PKG_START", "ENDMARKETINGDATE": "PKG_END"})
        df = packages.merge(products, on="PRODUCTID", how="left")
        df["listing_source"] = source
        frames.append(df)
    con.register("fda_raw", pd.concat(frames, ignore_index=True))

    con.execute(f"""
        CREATE OR REPLACE TABLE fda_ndc AS
        WITH t AS (
            SELECT
                {ndc10_to_11_sql('NDCPACKAGECODE')} AS ndc,
                NDCPACKAGECODE AS ndc10_package,
                PRODUCTNDC AS product_ndc,
                -- PRODUCTID = '<product ndc>_<SPL document id>': the exact FDA label
                -- the labeler filed (joins to openFDA label `id`, src.indications).
                NULLIF(split_part(PRODUCTID, '_', 2), '') AS spl_id,
                listing_source,
                NULLIF(NDC_EXCLUDE_FLAG, '') AS exclude_flag,
                NULLIF(PRODUCTTYPENAME, '') AS fda_product_type,
                NULLIF(trim(PROPRIETARYNAME), '') AS proprietary_name,
                NULLIF(trim(PROPRIETARYNAMESUFFIX), '') AS proprietary_name_suffix,
                NULLIF(trim(NONPROPRIETARYNAME), '') AS nonproprietary_name,
                NULLIF(trim(SUBSTANCENAME), '') AS substance_name,
                NULLIF(DOSAGEFORMNAME, '') AS dosage_form,
                NULLIF(ROUTENAME, '') AS route,
                NULLIF(MARKETINGCATEGORYNAME, '') AS marketing_category,
                NULLIF(APPLICATIONNUMBER, '') AS application_number_raw,
                regexp_extract(APPLICATIONNUMBER, '^([A-Z]+)', 1) AS application_prefix,
                NULLIF(regexp_extract(APPLICATIONNUMBER, '(\\d+)$', 1), '') AS application_digits,
                NULLIF(LABELERNAME, '') AS labeler_name,
                NULLIF(coalesce(NULLIF(PKG_START, ''), STARTMARKETINGDATE), '') AS start_marketing_date,
                NULLIF(coalesce(NULLIF(PKG_END, ''), ENDMARKETINGDATE), '') AS end_marketing_date
            FROM fda_raw
        )
        SELECT * EXCLUDE (rn) FROM (
            SELECT *,
                   -- Application numbers compared as 6-digit strings.
                   row_number() OVER (PARTITION BY ndc ORDER BY listing_source = 'current' DESC,
                                      start_marketing_date DESC NULLS LAST) AS rn
            FROM t WHERE ndc IS NOT NULL
        ) WHERE rn = 1
    """)
    con.execute("""
        CREATE OR REPLACE TABLE fda_ndc AS
        SELECT *, lpad(ltrim(application_digits, '0'), 6, '0') AS application_no FROM fda_ndc
    """)
    bad, = con.execute(f"""SELECT count(*) FROM fda_raw
                          WHERE {ndc10_to_11_sql('NDCPACKAGECODE')} IS NULL""").fetchone()
    print(f"  FDA NDC Directory: {con.execute('SELECT count(*) FROM fda_ndc').fetchone()[0]:,} "
          f"package NDCs ({bad:,} malformed package codes skipped)")
    con.execute(f"COPY fda_ndc TO '{REF_OUT / 'fda_ndc.parquet'}' (FORMAT parquet)")

    # Product-level fallback. Some SDUD NDCs are "inner" unit codes (the pen
    # inside a carton, e.g. 00169-4130-01 inside carton 00169-4130-13) that the
    # FDA package file never lists. The first 9 digits of an 11-digit NDC are
    # labeler (5) + product (4), so they still match the FDA product listing.
    con.execute(f"""
        CREATE OR REPLACE TABLE fda_product AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT lpad(split_part(product_ndc, '-', 1), 5, '0') || lpad(split_part(product_ndc, '-', 2), 4, '0')
                       AS product9,
                   * EXCLUDE (ndc, ndc10_package, start_marketing_date, end_marketing_date),
                   row_number() OVER (PARTITION BY lpad(split_part(product_ndc, '-', 1), 5, '0')
                                                   || lpad(split_part(product_ndc, '-', 2), 4, '0')
                                      ORDER BY listing_source = 'current' DESC,
                                               start_marketing_date DESC NULLS LAST) AS rn
            FROM fda_ndc WHERE regexp_full_match(product_ndc, '\\d{{4,5}}-\\d{{3,4}}'))
        WHERE rn = 1
    """)
    print(f"  FDA products (9-digit fallback): {con.execute('SELECT count(*) FROM fda_product').fetchone()[0]:,}")
    con.execute(f"COPY fda_product TO '{REF_OUT / 'fda_product.parquet'}' (FORMAT parquet)")


def build_purple_book(con: duckdb.DuckDBPyConnection) -> None:
    """Full Purple Book product list (the file's second section)."""
    lines = (REF_RAW / "purplebook.csv").read_text(encoding="utf-8", errors="replace").splitlines()
    # The file starts with a "changes this month" section; the full product
    # list follows the LAST header row.
    header_rows = [i for i, l in enumerate(lines) if l.startswith("N/R/U,")]
    if not header_rows:
        raise RuntimeError("Purple Book header row not found")
    pb = pd.read_csv(io.StringIO("\n".join(lines[header_rows[-1]:])), dtype=str, keep_default_na=False)
    pb = pb.apply(lambda s: s.str.strip())
    pb = pb[pb["BLA Number"].str.fullmatch(r"\d+")]
    pb = pd.DataFrame({
        "bla_no": pb["BLA Number"].str.zfill(6),
        "proprietary_name": pb["Proprietary Name"].replace("N/A", ""),
        "proper_name": pb["Proper Name"],
        "license_type": pb["License Type"],
        "marketing_status": pb["Marketing Status"],
        "ref_product_proper_name": pb["Ref. Product Proper Name"],
        "ref_product_proprietary_name": pb["Ref. Product Proprietary Name"],
        "applicant": pb["Applicant"],
    })
    con.register("pb_raw", pb)
    # One row per BLA. A few 351(k) BLAs list some products as interchangeable
    # and others not (different strengths/presentations); NDCs cannot be tied
    # to Purple Book products, so such BLAs are marked partial (D-004).
    con.execute("""
        CREATE OR REPLACE TABLE purple_book AS
        SELECT bla_no,
               CASE WHEN bool_or(license_type LIKE '351(k)%') THEN '351(k)' ELSE '351(a)' END AS bla_type,
               bool_or(license_type = '351(k) Interchangeable') AS any_interchangeable,
               bool_and(license_type = '351(k) Interchangeable') FILTER (WHERE license_type LIKE '351(k)%')
                   AS all_interchangeable,
               string_agg(DISTINCT NULLIF(proprietary_name, ''), '; ') AS proprietary_names,
               string_agg(DISTINCT proper_name, '; ') AS proper_names,
               string_agg(DISTINCT NULLIF(ref_product_proprietary_name, ''), '; ') AS ref_product_names,
               count(DISTINCT license_type) AS n_license_types
        FROM pb_raw GROUP BY bla_no
    """)
    mixed_ak, = con.execute("""SELECT count(*) FROM (SELECT bla_no FROM pb_raw GROUP BY 1
                               HAVING bool_or(license_type = '351(a)') AND bool_or(license_type LIKE '351(k)%'))""").fetchone()
    if mixed_ak:
        raise RuntimeError(f"{mixed_ak} BLAs listed as both 351(a) and 351(k); rules need review")
    print(f"  Purple Book: {con.execute('SELECT count(*) FROM purple_book').fetchone()[0]:,} BLAs")
    con.execute(f"COPY purple_book TO '{REF_OUT / 'purple_book.parquet'}' (FORMAT parquet)")


def build_fda_applications(con: duckdb.DuckDBPyConnection) -> None:
    """Set of real FDA application numbers (Drugs@FDA + Orange Book)."""
    apps = _read_zip_tsv("drugsatfda.zip", "Applications.txt")
    ob = pd.read_csv(zipfile.ZipFile(REF_RAW / "orange_book.zip").open("products.txt"),
                     sep="~", dtype=str, encoding="latin1", keep_default_na=False)
    df = pd.concat([
        pd.DataFrame({"appl_type": apps["ApplType"].str.strip(), "appl_no": apps["ApplNo"].str.strip(),
                      "source": "drugs@fda"}),
        pd.DataFrame({"appl_type": ob["Appl_Type"].map({"N": "NDA", "A": "ANDA"}),
                      "appl_no": ob["Appl_No"].str.strip(), "source": "orange_book"}),
    ])
    df["appl_no"] = df["appl_no"].str.lstrip("0").str.zfill(6)
    df = df.groupby(["appl_type", "appl_no"], as_index=False)["source"].agg(lambda s: ",".join(sorted(set(s))))
    con.register("apps_df", df)
    con.execute("CREATE OR REPLACE TABLE fda_applications AS SELECT * FROM apps_df")
    print(f"  FDA applications: {len(df):,} ({df['appl_type'].value_counts().to_dict()})")
    con.execute(f"COPY fda_applications TO '{REF_OUT / 'fda_applications.parquet'}' (FORMAT parquet)")


def build_mdrp(con: duckdb.DuckDBPyConnection) -> None:
    """MDRP product records, one row per NDC per quarter (kept for as-of lookups)."""
    path = REF_RAW / "mdrp_products.csv"
    con.execute(f"""
        CREATE OR REPLACE TABLE mdrp AS
        SELECT
            lpad(regexp_replace(ndc, '[^0-9]', '', 'g'), 11, '0') AS ndc,
            CAST(_year AS SMALLINT) AS year,
            CAST(_quarter AS TINYINT) AS quarter,
            NULLIF(trim(drug_category), '') AS mdrp_drug_category,
            -- COD status appears both zero-padded ('03') and not ('3').
            lpad(NULLIF(trim(cod_status), ''), 2, '0') AS mdrp_cod_status,
            NULLIF(lpad(ltrim(regexp_replace(fda_application_number, '[^0-9]', '', 'g'), '0'), 6, '0'), '000000')
                AS mdrp_application_no,
            NULLIF(trim(fda_product_name), '') AS mdrp_product_name,
            NULLIF(trim(labeler_name), '') AS mdrp_labeler_name,
            NULLIF(trim(termination_date), '') AS mdrp_termination_date,
            -- Used by phase 2: unit comparability, substitutability, rebate floor.
            NULLIF(upper(trim(unit_type)), '') AS mdrp_unit_type,
            NULLIF(upper(trim(fda_therapeutic_equivalence_code)), '') AS mdrp_te_code,
            upper(trim(clotting_factor_indicator)) = 'Y' AS mdrp_clotting_factor,
            upper(trim(pediatric_indicator)) = 'Y' AS mdrp_pediatric
        FROM read_csv('{path}', all_varchar=true, header=true, quote='"', normalize_names=true)
    """)
    n, k = con.execute("SELECT count(*), count(DISTINCT ndc) FROM mdrp").fetchone()
    print(f"  MDRP: {n:,} NDC-quarter records, {k:,} NDCs")
    con.execute(f"COPY mdrp TO '{REF_OUT / 'mdrp.parquet'}' (FORMAT parquet)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args(argv)

    print("[1/2] download reference files")
    download_all(force=args.force_download)
    REF_OUT.mkdir(parents=True, exist_ok=True)

    print("[2/2] parse")
    con = duckdb.connect()
    build_fda_ndc(con)
    build_purple_book(con)
    build_fda_applications(con)
    build_mdrp(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
