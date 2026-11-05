"""Tests for the SDUD ingest step.

Unit tests run on a tiny synthetic CSV. Data tests run against the processed
Parquet tables and are skipped for any year that has not been ingested.
"""
import glob
import json
import re

import duckdb
import pytest

from src import config, ingest_sdud

HEADER = ",".join(ingest_sdud.SOURCE_COLUMNS)


@pytest.fixture
def tiny_csv(tmp_path):
    rows = [
        # Leading-zero NDC, quoted product name containing a comma.
        'FFSU,AK,00074165801,00074,1658,01,2025,4,false,"ZEMPLAR, 5",00000000012.200,000000020,000000000053.61,0000000053.61,0000000000.00',
        # Suppressed row: blank measures.
        "MCOU,AK,00074165801,00074,1658,01,2025,4,true,ZEMPLAR ,,,,,",
        "FFSU,XX,00074165801,00074,1658,01,2025,4,false,ZEMPLAR ,00000000030.000,000000025,000000000070.00,0000000070.00,0000000000.00",
    ]
    p = tmp_path / "tiny.csv"
    p.write_text(HEADER + "\r\n" + "\r\n".join(rows) + "\r\n")
    return str(p)


def test_clean_sql_types_and_ndc(tiny_csv):
    con = duckdb.connect()
    df = con.execute(ingest_sdud.clean_sql(tiny_csv)).df()
    assert len(df) == 3
    assert (df["ndc"] == "00074165801").all()
    assert df.loc[0, "product_name"] == "ZEMPLAR, 5"
    assert df.loc[0, "number_of_prescriptions"] == 20
    assert float(df.loc[0, "medicaid_amount_reimbursed"]) == pytest.approx(53.61)
    # Suppressed row keeps NULL measures (not zero) and a True flag.
    assert bool(df.loc[1, "suppression_used"]) is True
    assert df[ingest_sdud.MEASURES].iloc[1].isna().all()


def test_ndc_normalization_pads_and_strips():
    con = duckdb.connect()
    expr = "lpad(regexp_replace(?, '[^0-9]', '', 'g'), 11, '0')"
    assert con.execute(f"SELECT {expr}", ["2143380"]).fetchone()[0] == "00002143380"
    assert con.execute(f"SELECT {expr}", ["00002-1433-80"]).fetchone()[0] == "00002143380"


# --------------------------------------------------------------------------
# Data tests on processed tables
# --------------------------------------------------------------------------

INGESTED_YEARS = sorted(
    int(re.search(r"(\d{4})", p).group(1))
    for p in glob.glob(str(config.PROCESSED / "sdud_validation_*.json"))
)

needs_data = pytest.mark.skipif(not INGESTED_YEARS, reason="no SDUD year ingested yet")


@pytest.fixture(scope="module")
def con():
    return duckdb.connect()


@needs_data
@pytest.mark.parametrize("year", INGESTED_YEARS)
def test_validation_report_all_passed(year):
    report = json.loads(config.validation_path(year).read_text())
    failed = [k for k, v in report["checks"].items() if not v["passed"]]
    assert not failed, f"failed checks: {failed}"


@needs_data
@pytest.mark.parametrize("year", INGESTED_YEARS)
def test_row_counts_match_source(con, year):
    meta = json.loads((config.RAW / f"sdud_{year}_meta.json").read_text())
    n = con.execute(f"""
        SELECT (SELECT count(*) FROM read_parquet('{config.state_fact_path(year)}'))
             + (SELECT count(*) FROM read_parquet('{config.national_fact_path(year)}'))
    """).fetchone()[0]
    assert n == meta["source_row_count"]


@needs_data
@pytest.mark.parametrize("year", INGESTED_YEARS)
def test_ndc_is_11_char_string(con, year):
    for path in (config.state_fact_path(year), config.national_fact_path(year)):
        typ = con.execute(f"SELECT typeof(ndc) FROM read_parquet('{path}') LIMIT 1").fetchone()[0]
        assert typ == "VARCHAR"
        bad = con.execute(f"""SELECT count(*) FROM read_parquet('{path}')
                              WHERE NOT regexp_full_match(ndc, '[0-9]{{11}}')""").fetchone()[0]
        assert bad == 0


@needs_data
@pytest.mark.parametrize("year", INGESTED_YEARS)
def test_national_rows_separated(con, year):
    st = config.state_fact_path(year)
    na = config.national_fact_path(year)
    assert con.execute(f"SELECT count(*) FROM read_parquet('{st}') WHERE state = 'XX'").fetchone()[0] == 0
    assert con.execute(f"SELECT count(*) FROM read_parquet('{na}') WHERE state <> 'XX'").fetchone()[0] == 0


@needs_data
@pytest.mark.parametrize("year", INGESTED_YEARS)
def test_states_plus_residual_reconcile_per_key(con, year):
    """For every unsuppressed national key: sum(states) + residual == national."""
    bad = con.execute(f"""
        SELECT count(*) FROM read_parquet('{config.residual_path(year)}')
        WHERE NOT national_suppressed AND (
            coalesce(states_number_of_prescriptions, 0) + residual_number_of_prescriptions
                <> national_number_of_prescriptions
            OR coalesce(states_medicaid_amount_reimbursed, 0) + residual_medicaid_amount_reimbursed
                <> national_medicaid_amount_reimbursed
            OR residual_number_of_prescriptions < 0)
    """).fetchone()[0]
    assert bad == 0


@needs_data
@pytest.mark.parametrize("year", INGESTED_YEARS)
def test_suppressed_rows_kept(con, year):
    n = con.execute(f"""SELECT count(*) FROM read_parquet('{config.state_fact_path(year)}')
                        WHERE suppression_used AND number_of_prescriptions IS NULL""").fetchone()[0]
    assert n > 0
