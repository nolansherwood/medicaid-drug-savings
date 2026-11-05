"""Shared paths and constants for the pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
CACHE = DATA / "cache"
OUTPUTS = ROOT / "outputs"

# SDUD national-total pseudo-state. Never sum these rows with state rows.
NATIONAL_STATE = "XX"

# Processed table locations. One Parquet file per year so a year can be
# re-ingested without touching the others; read all years with the glob.
def state_fact_path(year: int) -> Path:
    return PROCESSED / f"sdud_state_{year}.parquet"


def national_fact_path(year: int) -> Path:
    return PROCESSED / f"sdud_national_{year}.parquet"


def residual_path(year: int) -> Path:
    return PROCESSED / f"sdud_suppression_residual_{year}.parquet"


def validation_path(year: int) -> Path:
    return PROCESSED / f"sdud_validation_{year}.json"


STATE_FACT_GLOB = str(PROCESSED / "sdud_state_*.parquet")
NATIONAL_FACT_GLOB = str(PROCESSED / "sdud_national_*.parquet")
RESIDUAL_GLOB = str(PROCESSED / "sdud_suppression_residual_*.parquet")
