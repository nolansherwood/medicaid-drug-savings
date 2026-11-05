"""Ingest one year of Medicaid State Drug Utilization Data (SDUD).

Steps:
  1. Look up the year's dataset in the data.medicaid.gov catalog.
  2. Download the CSV to data/raw/ (skipped if already present).
  3. Load with DuckDB using explicit types and write three Parquet tables:
       - state fact table   (grain: state x year x quarter x ndc x utilization_type)
       - national fact table (state = "XX" rows only, same grain without state)
       - suppression residual (national minus sum of states, per ndc/period)
  4. Run validation checks and save a JSON report next to the tables.

Usage:
    python -m src.ingest_sdud --year 2025 [--force-download]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import duckdb
import requests

from src import config

CATALOG_URL = "https://data.medicaid.gov/api/1/metastore/schemas/dataset/items"
DATASTORE_COUNT_URL = (
    "https://data.medicaid.gov/api/1/datastore/query/{dataset_id}/0"
    "?limit=1&count=true&results=false&schema=false&keys=false"
)

# Source CSV header -> our snake_case name. Mapped explicitly (rather than with
# DuckDB's normalize_names) so reserved words like "year" are not renamed, and
# so a change in the source header fails loudly instead of silently.
SOURCE_COLUMNS = {
    "Utilization Type": "utilization_type",
    "State": "state",
    "NDC": "ndc",
    "Labeler Code": "labeler_code",
    "Product Code": "product_code",
    "Package Size": "package_size",
    "Year": "year",
    "Quarter": "quarter",
    "Suppression Used": "suppression_used",
    "Product Name": "product_name",
    "Units Reimbursed": "units_reimbursed",
    "Number of Prescriptions": "number_of_prescriptions",
    "Total Amount Reimbursed": "total_amount_reimbursed",
    "Medicaid Amount Reimbursed": "medicaid_amount_reimbursed",
    "Non Medicaid Amount Reimbursed": "non_medicaid_amount_reimbursed",
}

MEASURES = [
    "units_reimbursed",
    "number_of_prescriptions",
    "total_amount_reimbursed",
    "medicaid_amount_reimbursed",
    "non_medicaid_amount_reimbursed",
]


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def find_dataset(year: int) -> dict:
    """Return catalog metadata (id, download URL, modified date) for one year."""
    resp = requests.get(CATALOG_URL, timeout=120)
    resp.raise_for_status()
    title = f"State Drug Utilization Data {year}"
    matches = [d for d in resp.json() if d.get("title", "").strip() == title]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one catalog entry titled {title!r}, found {len(matches)}")
    ds = matches[0]
    return {
        "dataset_id": ds["identifier"],
        "title": ds["title"],
        "modified": ds.get("modified"),
        "download_url": ds["distribution"][0]["downloadURL"],
    }


def fetch_source_row_count(dataset_id: str) -> int | None:
    """Row count CMS reports for the dataset (used to validate the download)."""
    try:
        resp = requests.get(DATASTORE_COUNT_URL.format(dataset_id=dataset_id), timeout=120)
        resp.raise_for_status()
        return int(resp.json()["count"])
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"  warning: could not fetch source row count: {exc}")
        return None


def download(year: int, force: bool = False) -> dict:
    """Download the year's CSV to data/raw/ and write a metadata sidecar."""
    csv_path = config.RAW / f"sdud_{year}.csv"
    meta_path = config.RAW / f"sdud_{year}_meta.json"

    if csv_path.exists() and meta_path.exists() and not force:
        print(f"  using existing {csv_path.name}")
        return json.loads(meta_path.read_text())

    meta = find_dataset(year)
    if csv_path.exists() and not force:
        # CSV downloaded by hand or by an interrupted run: just record metadata.
        print(f"  using existing {csv_path.name}; recording catalog metadata")
    else:
        print(f"  downloading {meta['download_url']}")
        tmp = csv_path.with_suffix(".csv.part")
        with requests.get(meta["download_url"], stream=True, timeout=600) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        tmp.rename(csv_path)

    meta["source_row_count"] = fetch_source_row_count(meta["dataset_id"])
    meta["downloaded_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


# --------------------------------------------------------------------------
# Load and transform
# --------------------------------------------------------------------------

def read_raw_sql(csv_path: str) -> str:
    """SQL that reads the raw CSV with every column as text.

    Everything is read as VARCHAR first so NDCs and codes keep leading zeros,
    then cast explicitly in clean_sql(). Quote must be set explicitly: product
    names such as "ZEMPLAR, 5" contain commas and DuckDB's sniffer misses it.
    """
    columns = ", ".join(f"'{src}': 'VARCHAR'" for src in SOURCE_COLUMNS)
    return (
        f"read_csv('{csv_path}', header=true, delim=',', quote='\"', escape='\"', "
        f"columns={{{columns}}})"
    )


def clean_sql(csv_path: str) -> str:
    """SQL producing typed, normalized rows from the raw CSV."""
    renamed = ", ".join(f'"{src}" AS {dst}' for src, dst in SOURCE_COLUMNS.items())
    return f"""
    WITH raw AS (SELECT {renamed} FROM {read_raw_sql(csv_path)})
    SELECT
        trim(state)                                   AS state,
        CAST(year AS SMALLINT)                        AS year,
        CAST(quarter AS TINYINT)                      AS quarter,
        trim(utilization_type)                        AS utilization_type,
        -- NDC: strip any non-digits, left-pad to 11 so leading zeros survive.
        lpad(regexp_replace(ndc, '[^0-9]', '', 'g'), 11, '0') AS ndc,
        labeler_code,
        product_code,
        package_size,
        trim(product_name)                            AS product_name,
        lower(trim(suppression_used)) = 'true'        AS suppression_used,
        -- Suppressed rows have blank measures; they stay NULL (not zero).
        CAST(units_reimbursed AS DECIMAL(18,3))       AS units_reimbursed,
        CAST(number_of_prescriptions AS BIGINT)       AS number_of_prescriptions,
        CAST(total_amount_reimbursed AS DECIMAL(18,2))        AS total_amount_reimbursed,
        CAST(medicaid_amount_reimbursed AS DECIMAL(18,2))     AS medicaid_amount_reimbursed,
        CAST(non_medicaid_amount_reimbursed AS DECIMAL(18,2)) AS non_medicaid_amount_reimbursed
    FROM raw
    """


def build_tables(con: duckdb.DuckDBPyConnection, year: int, csv_path: str) -> None:
    """Load the CSV and write the state, national and residual Parquet files."""
    con.execute(f"CREATE OR REPLACE TABLE sdud AS {clean_sql(csv_path)}")

    years = [r[0] for r in con.execute("SELECT DISTINCT year FROM sdud").fetchall()]
    if years != [year]:
        raise RuntimeError(f"File for {year} contains years {years}")

    order = "ORDER BY quarter, ndc, utilization_type"
    nat = config.NATIONAL_STATE

    con.execute(f"""
        COPY (SELECT * FROM sdud WHERE state <> '{nat}' {order}, state)
        TO '{config.state_fact_path(year)}' (FORMAT parquet, COMPRESSION zstd)
    """)
    # National rows keep the same columns (including state = 'XX') so the two
    # tables share a schema, but they live in a separate file and must never
    # be unioned into state totals.
    con.execute(f"""
        COPY (SELECT * FROM sdud WHERE state = '{nat}' {order})
        TO '{config.national_fact_path(year)}' (FORMAT parquet, COMPRESSION zstd)
    """)

    # Suppression residual: national total minus sum of reported state values,
    # per year x quarter x ndc x utilization_type. Suppressed state rows are
    # NULL and drop out of the sums, so the residual is (mostly) the volume
    # hidden by suppression. When the national row itself is suppressed its
    # measures are NULL and so is the residual.
    resid_cols = ",\n".join(
        f"n.{m} AS national_{m}, s.{m} AS states_{m}, "
        f"n.{m} - coalesce(s.{m}, 0) AS residual_{m}"
        for m in MEASURES
    )
    states_sums = ", ".join(f"sum({m}) AS {m}" for m in MEASURES)
    con.execute(f"""
        COPY (
            WITH s AS (
                SELECT year, quarter, ndc, utilization_type,
                       count(*) AS n_state_rows,
                       count(*) FILTER (WHERE suppression_used) AS n_state_rows_suppressed,
                       {states_sums}
                FROM sdud WHERE state <> '{nat}'
                GROUP BY ALL
            ),
            n AS (SELECT * FROM sdud WHERE state = '{nat}')
            SELECT
                coalesce(n.year, s.year)                         AS year,
                coalesce(n.quarter, s.quarter)                   AS quarter,
                coalesce(n.ndc, s.ndc)                           AS ndc,
                coalesce(n.utilization_type, s.utilization_type) AS utilization_type,
                n.suppression_used                               AS national_suppressed,
                coalesce(s.n_state_rows, 0)                      AS n_state_rows,
                coalesce(s.n_state_rows_suppressed, 0)           AS n_state_rows_suppressed,
                {resid_cols}
            FROM n FULL JOIN s USING (year, quarter, ndc, utilization_type)
            {order}
        ) TO '{config.residual_path(year)}' (FORMAT parquet, COMPRESSION zstd)
    """)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def count_csv_data_lines(csv_path: str) -> int:
    """Physical data lines in the CSV (excluding header and blank lines)."""
    n = 0
    with open(csv_path, "rb") as f:
        next(f)  # header
        for line in f:
            if line.strip():
                n += 1
    return n


def validate(con: duckdb.DuckDBPyConnection, year: int, csv_path: str, meta: dict) -> dict:
    """Run ingest checks; return a report dict with a pass/fail per check."""
    st = f"read_parquet('{config.state_fact_path(year)}')"
    na = f"read_parquet('{config.national_fact_path(year)}')"
    rs = f"read_parquet('{config.residual_path(year)}')"
    q = lambda sql: con.execute(sql).fetchone()  # noqa: E731

    checks: dict[str, dict] = {}

    def check(name: str, passed: bool, **detail) -> None:
        checks[name] = {"passed": bool(passed), **detail}

    # 1. Row counts match the source.
    n_state, = q(f"SELECT count(*) FROM {st}")
    n_nat, = q(f"SELECT count(*) FROM {na}")
    n_lines = count_csv_data_lines(csv_path)
    source = meta.get("source_row_count")
    check("row_count_matches_csv", n_state + n_nat == n_lines,
          state_rows=n_state, national_rows=n_nat, csv_data_lines=n_lines)
    check("row_count_matches_cms_api", source is None or n_state + n_nat == source,
          cms_reported_rows=source, skipped=source is None)

    # 2. NDC formatting.
    bad_ndc, = q(f"""SELECT count(*) FROM (SELECT ndc FROM {st} UNION ALL SELECT ndc FROM {na})
                    WHERE NOT regexp_full_match(ndc, '[0-9]{{11}}')""")
    check("ndc_is_11_digit_string", bad_ndc == 0, bad_rows=bad_ndc)
    mismatch, = q(f"SELECT count(*) FROM {st} WHERE ndc <> labeler_code || product_code || package_size")
    check("ndc_equals_labeler_product_package", mismatch == 0, mismatched_rows=mismatch)

    # 3. Grain is unique and tables are separated correctly.
    dup, = q(f"""SELECT count(*) FROM (SELECT 1 FROM {st}
                 GROUP BY state, year, quarter, ndc, utilization_type HAVING count(*) > 1)""")
    check("state_grain_unique", dup == 0, duplicate_keys=dup)
    dup_n, = q(f"""SELECT count(*) FROM (SELECT 1 FROM {na}
                   GROUP BY year, quarter, ndc, utilization_type HAVING count(*) > 1)""")
    check("national_grain_unique", dup_n == 0, duplicate_keys=dup_n)
    xx_in_state, = q(f"SELECT count(*) FROM {st} WHERE state = '{config.NATIONAL_STATE}'")
    check("no_national_rows_in_state_table", xx_in_state == 0, rows=xx_in_state)

    # 4. Suppression flag is consistent with blank measures.
    bad_supp, = q(f"""SELECT count(*) FROM {st}
                      WHERE (suppression_used AND number_of_prescriptions IS NOT NULL)
                         OR (NOT suppression_used AND number_of_prescriptions IS NULL)""")
    check("suppression_flag_matches_null_measures", bad_supp == 0, inconsistent_rows=bad_supp)
    n_supp, = q(f"SELECT count(*) FROM {st} WHERE suppression_used")
    check("suppressed_rows_retained", n_supp > 0, suppressed_state_rows=n_supp)

    # 5. Quarter and utilization-type coverage.
    quarters = [r[0] for r in con.execute(f"SELECT DISTINCT quarter FROM {st} ORDER BY 1").fetchall()]
    check("all_four_quarters_present", quarters == [1, 2, 3, 4], quarters=quarters)
    util = [r[0] for r in con.execute(f"SELECT DISTINCT utilization_type FROM {st} ORDER BY 1").fetchall()]
    check("utilization_types_expected", set(util) <= {"FFSU", "MCOU"}, utilization_types=util)

    # 6. States + residual reconcile to national, and residual is never negative.
    #    Every national key must appear in the residual table and vice versa.
    orphan_state, = q(f"SELECT count(*) FROM {rs} WHERE national_suppressed IS NULL")
    check("every_state_key_has_national_row", orphan_state == 0, state_only_keys=orphan_state)
    neg, = q(f"""SELECT count(*) FROM {rs}
                 WHERE residual_number_of_prescriptions < 0 OR residual_medicaid_amount_reimbursed < 0""")
    check("residual_non_negative", neg == 0, negative_residual_keys=neg)
    nat_rx, st_rx, res_rx, nat_amt, st_amt, res_amt = q(f"""
        SELECT sum(national_number_of_prescriptions), sum(states_number_of_prescriptions),
               sum(residual_number_of_prescriptions),
               sum(national_medicaid_amount_reimbursed), sum(states_medicaid_amount_reimbursed),
               sum(residual_medicaid_amount_reimbursed)
        FROM {rs} WHERE NOT national_suppressed""")
    nat_rx_direct, nat_amt_direct = q(f"""SELECT sum(number_of_prescriptions),
                                          sum(medicaid_amount_reimbursed) FROM {na}""")
    check("states_plus_residual_equals_national",
          st_rx + res_rx == nat_rx and st_amt + res_amt == nat_amt
          and nat_rx == nat_rx_direct and nat_amt == nat_amt_direct,
          national_prescriptions=int(nat_rx), state_prescriptions=int(st_rx),
          residual_prescriptions=int(res_rx),
          national_medicaid_amount=float(nat_amt), state_medicaid_amount=float(st_amt),
          residual_medicaid_amount=float(res_amt),
          state_share_of_national_prescriptions=round(st_rx / nat_rx, 4),
          state_share_of_national_spending=round(float(st_amt / nat_amt), 4))

    # 7. Medicaid + non-Medicaid = total (informational tolerance of 1 cent).
    bad_total, = q(f"""SELECT count(*) FROM {st}
                       WHERE abs(total_amount_reimbursed - medicaid_amount_reimbursed
                                 - non_medicaid_amount_reimbursed) > 0.01""")
    check("medicaid_plus_non_medicaid_equals_total", bad_total == 0, mismatched_rows=bad_total)

    # Informational: NDCs carrying the largest suppression residual (spending),
    # to show what the state-level undercount consists of.
    top_resid = con.execute(f"""
        SELECT r.ndc, any_value(n.product_name) AS product_name,
               sum(residual_number_of_prescriptions) AS residual_rx,
               sum(residual_medicaid_amount_reimbursed) AS residual_amount
        FROM {rs} r LEFT JOIN {na} n USING (year, quarter, ndc, utilization_type)
        GROUP BY r.ndc ORDER BY residual_amount DESC NULLS LAST LIMIT 15""").fetchall()

    report = {
        "year": year,
        "source": meta,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "all_passed": all(c["passed"] for c in checks.values()),
        "checks": checks,
        "top_residual_ndcs": [
            {"ndc": r[0], "product_name": r[1], "residual_prescriptions": int(r[2] or 0),
             "residual_medicaid_amount": float(r[3] or 0)}
            for r in top_resid
        ],
    }
    config.validation_path(year).write_text(json.dumps(report, indent=2, default=str))
    return report


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args(argv)

    for d in (config.RAW, config.PROCESSED):
        d.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] download SDUD {args.year}")
    meta = download(args.year, force=args.force_download)
    csv_path = str(config.RAW / f"sdud_{args.year}.csv")

    con = duckdb.connect()
    print("[2/3] build Parquet tables")
    build_tables(con, args.year, csv_path)

    print("[3/3] validate")
    report = validate(con, args.year, csv_path, meta)
    for name, c in report["checks"].items():
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {name}")
    print(f"  report: {config.validation_path(args.year)}")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
