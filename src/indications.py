"""FDA-approved indications for each product, from the openFDA drug label bulk files.

The label's "INDICATIONS AND USAGE" section is the FDA-approved statement of
what a drug is for. It is the clearest way to tell apart products that share an
ingredient (Ozempic: type 2 diabetes; Wegovy: chronic weight management).

Source: openFDA /drug/label bulk download (all SPL labels, ~270k records), saved
in data/raw/reference/openfda_label/. Two joins to SDUD, in order (D-020):
  1. Label document id: the FDA NDC Directory PRODUCTID ends with the SPL
     document id of the label the labeler filed (fda_ndc.spl_id), which equals
     the openFDA label `id`. Exact, and works when openFDA left its harmonized
     `openfda` block empty (it does for many current labels: Ozempic, Wegovy,
     Eliquis).
  2. openfda.product_ndc ("0169-4130") on the 9-digit labeler + product code,
     left(ndc, 9) = product9, most recently effective label first.
  3. NDCs printed on the label's package panel ("NDC 0169-4130-13"), parsed
     to the same 9-digit code. Needed because openFDA keeps only the newest
     version of each label while the NDC Directory can point to an older
     version id, and openFDA's `openfda` block is empty for many labels.

Output: data/processed/reference/label_indications.parquet, one row per product9:
  product9, set_id, effective_time, label_brand_name, label_generic_name,
  indication (cleaned full section text), indication_source

Usage:
    python -m src.indications
"""
from __future__ import annotations

import glob
import re
import sys
import zipfile

import duckdb
import ijson
import pandas as pd

from src import config

LABEL_DIR = config.RAW / "reference" / "openfda_label"
OUT_PATH = config.PROCESSED / "reference" / "label_indications.parquet"


def product9(product_ndc: str) -> str | None:
    """'0169-4130' -> '001694130' (labeler padded to 5, product to 4)."""
    m = re.fullmatch(r"(\d{4,5})-(\d{3,4})", product_ndc or "")
    return m.group(1).zfill(5) + m.group(2).zfill(4) if m else None


_HEADING = re.compile(r"^\s*(\d+(\.\d+)?\s*)?(INDICATIONS?\s*(AND|&)\s*USAGES?|INDICATIONS?|USES?|PURPOSES?)\s*:?\s*",
                      re.IGNORECASE)
_XREF = re.compile(r"\(\s*\d+(\.\d+)*(\s*,\s*\d+(\.\d+)*)*\s*\)")   # "( 1.1 )", "(1.2, 14)"


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = _HEADING.sub("", text)
    text = _XREF.sub("", text)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()


def dedupe_restatements(text: str) -> str:
    """Drop sentences/bullets whose words nearly all appeared earlier in the text.

    Many labels repeat the indication (a summary line, then the full list), and
    openFDA joins them into one string.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.;])\s+|\s*•\s*", text) if p and p.strip()]
    seen: set[str] = set()
    kept = []
    for part in parts:
        words = set(re.findall(r"[a-z0-9]+", part.lower())) - {"and", "or", "the", "of", "in", "for", "to", "a", "with", "is", "are", "as"}
        if words and len(words & seen) / len(words) >= 0.8:
            continue
        kept.append(part)
        seen |= words
    while kept and kept[-1].endswith(":"):     # dangling restated header, e.g. "X is a GLP-1 agonist indicated:"
        kept.pop()
    return " ".join(kept)


_PRINTED_NDC = re.compile(r"NDC[\s#:No.]*?(\d{4,5})\s*-\s*(\d{3,4})\s*-\s*(\d{1,2})\b", re.IGNORECASE)


def printed_product9s(rec: dict) -> set[str]:
    """9-digit product codes of NDCs printed on the package label panel."""
    text = " ".join(rec.get("package_label_principal_display_panel", []))
    out = set()
    for lab, prod, _ in _PRINTED_NDC.findall(text):
        if len(lab) + len(prod) in (8, 9):         # 4-4, 5-3, 5-4 layouts
            out.add(lab.zfill(5) + prod.zfill(4))
    return out


def needed_products() -> tuple[set[str], dict[str, set[str]]]:
    """SDUD 9-digit product codes, and the FDA label document ids filed for each."""
    rows = duckdb.sql(f"SELECT DISTINCT left(ndc, 9) FROM read_parquet('{config.NATIONAL_FACT_GLOB}')").fetchall()
    needed = {r[0] for r in rows}
    ref = config.PROCESSED / "reference" / "fda_ndc.parquet"
    ids = duckdb.sql(f"""SELECT DISTINCT left(ndc, 9) AS p9, spl_id FROM read_parquet('{ref}')
                         WHERE spl_id IS NOT NULL""").fetchall()
    by_id: dict[str, set[str]] = {}
    for p9, spl in ids:
        if p9 in needed:
            by_id.setdefault(spl, set()).add(p9)
    return needed, by_id


def scan(needed: set[str], by_id: dict[str, set[str]]) -> pd.DataFrame:
    rows = []
    parts = sorted(glob.glob(str(LABEL_DIR / "drug-label-*.json.zip")))
    if not parts:
        raise SystemExit(f"No label files in {LABEL_DIR}; download them first (see module docstring).")
    for path in parts:
        n_before = len(rows)
        with zipfile.ZipFile(path) as z:
            with z.open(z.namelist()[0]) as f:
                for rec in ijson.items(f, "results.item"):
                    of = rec.get("openfda") or {}
                    by_doc = by_id.get(rec.get("id"), set())
                    by_ndc = {product9(p) for p in of.get("product_ndc", [])} & needed
                    by_text = printed_product9s(rec) & needed
                    if not (by_doc or by_ndc or by_text):
                        continue
                    if rec.get("indications_and_usage"):
                        text, source = " ".join(rec["indications_and_usage"]), "indications_and_usage"
                    elif rec.get("purpose"):
                        text, source = " ".join(rec["purpose"]), "purpose (OTC label)"
                    else:
                        continue
                    text = dedupe_restatements(clean(text))
                    brand = "; ".join(of.get("brand_name", [])[:2]) or brand_from_elements(rec)
                    matches = ([(k, "label_id") for k in by_doc] + [(k, "openfda_ndc") for k in by_ndc - by_doc]
                               + [(k, "printed_ndc") for k in by_text - by_doc - by_ndc])
                    for k, how in matches:
                        rows.append({
                            "product9": k, "match": how, "set_id": rec.get("set_id"),
                            "effective_time": rec.get("effective_time"),
                            "label_brand_name": brand,
                            "label_generic_name": "; ".join(of.get("generic_name", [])[:2]),
                            "indication": text, "indication_source": source,
                        })
        print(f"  {path.rsplit('/', 1)[-1]}: {len(rows) - n_before:,} matching label rows", flush=True)
    df = pd.DataFrame(rows)
    # Prefer the label the labeler filed (exact document id), then the newest.
    df["rank"] = df["match"].map({"label_id": 0, "openfda_ndc": 1, "printed_ndc": 2})
    return (df.sort_values(["product9", "rank", "effective_time"], ascending=[True, True, False])
              .drop_duplicates("product9").drop(columns="rank").reset_index(drop=True))


def brand_from_elements(rec: dict) -> str:
    """Brand name when openFDA's block is empty: first word(s) of spl_product_data_elements."""
    el = " ".join(rec.get("spl_product_data_elements", []))
    return el.split(" ")[0].title() if el else ""


def main() -> int:
    needed, by_id = needed_products()
    df = scan(needed, by_id)
    df.to_parquet(OUT_PATH, index=False)
    print(f"  labels found for {len(df):,} of {len(needed):,} SDUD products (9-digit codes); "
          f"by match: {df['match'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
