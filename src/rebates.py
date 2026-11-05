"""Rebate assumptions for savings estimates (D-016).

Per-NDC Medicaid rebates are confidential, so savings use two public anchors:

1. Statutory minimum rebate (Social Security Act 1927(c)), applied by the
   NDC's MDRP drug category:
     S / I (brands, incl. authorized generics and biologics): 23.1% of AMP
       (17.1% for clotting factors and pediatric-only drugs, MDRP flags)
     N (generics): 13% of AMP
   Inflation rebates come on top; since 2024 there is no cap at 100% of AMP.

2. Average rebate calibrated to MACPAC MACStats (Feb 2026) FY 2024
   Exhibit 28 (gross spending and rebates by state) and Exhibit 26 (brand vs
   generic share of gross). Generics are assumed to receive the statutory 13%;
   the brand rate is what is left of the state's total rebates:
       brand_rate = (rebates - 0.13 * generic_gross) / brand_gross
   MACPAC's "brand" = MDRP categories S and I; "generic" = N.

Both are applied to Medicaid reimbursement, not AMP (AMP is not public).
Reimbursement includes dispensing fees and is above AMP, so applying a % of
AMP to reimbursement overstates the rebate somewhat; documented in D-016.

Usage:
    python -m src.rebates     # parses the MACPAC PDFs, writes rebate_rates_fy2024.csv
"""
from __future__ import annotations

import re
import sys

import pandas as pd

from src import config

MACPAC_DIR = config.RAW / "reference" / "macpac"
RATES_PATH = config.PROCESSED / "reference" / "rebate_rates_fy2024.csv"

MIN_BRAND = 0.231
MIN_BRAND_CLOTTING_PEDIATRIC = 0.171
MIN_GENERIC = 0.13
ASSUMED_GENERIC_AVERAGE = MIN_GENERIC

STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Puerto Rico": "PR", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Total": "US",
}
# MACPAC footnotes flag these states' FY 2024 data as anomalous.
ANOMALOUS = {"KY": "MACPAC: anomalous Q3 FY 2024 spending",
             "VA": "MACPAC: atypical generic share, possible payment data anomaly"}

_NUM = r"(-?\$?-?[\d,]+\.\d|–)"


def _num(tok: str) -> float | None:
    if tok == "–":
        return None
    return float(tok.replace("$", "").replace(",", ""))


def _rows(pdf_name: str, n_values: int) -> dict[str, list]:
    import pypdf
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(MACPAC_DIR / pdf_name).pages)
    pat = re.compile(rf"^([A-Z][A-Za-z .]+?)\d*\s+" + r"\s+".join([_NUM] * n_values) + r"%?\s*$")
    out = {}
    for line in text.splitlines():
        line = line.replace("%", "").strip()
        m = pat.match(line)
        if m and m.group(1).strip() in STATE_CODES:
            out[STATE_CODES[m.group(1).strip()]] = [_num(t) for t in m.groups()[1:]]
    return out


def build_rates() -> pd.DataFrame:
    gross_rebates = _rows("exhibit28_fy2024.pdf", 6)   # gross tot/ffs/mc, rebates tot/ffs/mc
    brand_generic = _rows("exhibit26_fy2024.pdf", 12)  # total, brand%, generic%, unknown%, x3
    rows = []
    for code, g in gross_rebates.items():
        bg = brand_generic.get(code)
        if bg is None:
            continue
        gross, rebates = g[0], -g[3]
        brand_share, generic_share = bg[1] / 100, bg[2] / 100
        brand_gross, generic_gross = gross * brand_share, gross * generic_share
        raw = (rebates - ASSUMED_GENERIC_AVERAGE * generic_gross) / brand_gross if brand_gross else None
        used = None if raw is None else min(max(raw, MIN_BRAND), 1.0)
        rows.append({
            "state": code, "gross_millions": gross, "rebates_millions": rebates,
            "rebate_share_of_gross": rebates / gross if gross else None,
            "brand_share_of_gross": brand_share, "generic_share_of_gross": generic_share,
            "brand_rate_implied": raw, "brand_rate_used": used,
            "generic_rate_used": ASSUMED_GENERIC_AVERAGE,
            "note": "; ".join(filter(None, [
                ANOMALOUS.get(code),
                "implied brand rate clamped to [23.1%, 100%]" if raw is not None and raw != used else None,
            ])) or None,
        })
    df = pd.DataFrame(rows).sort_values("state").reset_index(drop=True)
    missing = sorted(set(STATE_CODES.values()) - set(df["state"]))
    if missing:
        print(f"  warning: no MACPAC row parsed for {missing}")
    return df


def load_rates() -> pd.DataFrame:
    return pd.read_csv(RATES_PATH, keep_default_na=False, na_values=[""])


def main() -> int:
    df = build_rates()
    RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RATES_PATH, index=False)
    pd.set_option("display.width", 200)
    print(df[["state", "gross_millions", "rebate_share_of_gross", "brand_share_of_gross",
              "brand_rate_implied", "brand_rate_used", "note"]].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
