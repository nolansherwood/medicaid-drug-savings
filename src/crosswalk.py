"""Build the NDC -> RxNorm ingredient -> ATC therapeutic class crosswalk.

Covers every NDC in the ingested SDUD national tables (all years found in
data/processed/). Steps, each backed by the RxNav response cache:

  1. NDC -> product RxCUI      ndcstatus (current RxCUI, else NDC history)
  2. product -> ingredient     related IN/MIN; retired RxCUIs follow historystatus remaps
  3. product -> ATC classes    RxClass relaSource=ATCPROD (product level)
  4. ingredient -> one primary ATC class (rules below)
  5. apply src/crosswalk_overrides.csv, write tables and coverage report

Ingredient (the ranking unit)
  - Single-ingredient products map to the IN concept.
  - Multi-ingredient products map to the MIN concept (e.g. "hydrochlorothiazide /
    losartan"). Co-packs without a MIN (e.g. Cabenuva) get a synthetic
    ingredient: sorted component IN RxCUIs joined with "+", tty "MULTI_IN".

ATC class: why product-level ATCPROD rather than ingredient-level ATC
  Ingredient-level ATC lookups on a MIN return each component's single-agent
  class (losartan/HCTZ -> C09CA and C03AA), never the combination code. ATCPROD
  on the product returns the combination code (C09DA "ARBs and diuretics"),
  which the project rules require, and also distinguishes route (prednisone
  tablets -> H02AB, enemas -> A07EA). Ingredient-level ATC is the fallback
  when no product of an ingredient has an ATCPROD class.

Combination products with several ATCPROD classes
  Keep only classes that are not a single-agent class of any component (e.g.
  Cabenuva: J05AG, J05AJ, J05AR -> J05AR). If that leaves nothing, keep all.

PRIMARY CLASS RULE (ingredients with more than one candidate ATC class)
  Each NDC's national prescriptions (all ingested years) are credited to its
  product classes, split evenly if a product has several. The ingredient's
  primary class is the one with the most prescriptions; ties go to the most
  Medicaid spending, then the alphabetically first ATC code. Ingredients with
  no ATCPROD class use their ingredient-level ATC class if there is exactly
  one; if there are several they are left unclassified (atc_source
  "ambiguous_needs_override") for a manual override (D-010). Every ingredient
  with more than one candidate is listed in
  outputs/crosswalk_multi_atc_ingredients.csv.

Overrides (src/crosswalk_overrides.csv; columns ndc, ingredient_rxcui,
ingredient_name, atc4, note)
  - Row with ndc set: sets that NDC's ingredient (if given) and/or its product
    ATC class (if atc4 given) before the primary-class rule runs.
  - Row with ndc blank and ingredient_rxcui set: forces that ingredient's
    primary ATC class to atc4.

Usage:
    python -m src.crosswalk
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import json
import re
import sys
from collections import defaultdict

import duckdb
import pandas as pd

from src import config
from src.rxnav import RxNav

OVERRIDES_PATH = config.ROOT / "src" / "crosswalk_overrides.csv"
CROSSWALK_PATH = config.PROCESSED / "crosswalk_ndc.parquet"
INGREDIENT_PATH = config.PROCESSED / "crosswalk_ingredient.parquet"
MULTI_ATC_PATH = config.OUTPUTS / "crosswalk_multi_atc_ingredients.csv"
UNMAPPED_PATH = config.OUTPUTS / "crosswalk_unmapped_ndcs.csv"
COVERAGE_PATH = config.OUTPUTS / "crosswalk_coverage.json"

N_WORKERS = 12


def parallel_map(fn, items, label):
    """Run fn over items with a thread pool, printing progress."""
    items = list(items)
    out = {}
    with cf.ThreadPoolExecutor(N_WORKERS) as ex:
        futures = {ex.submit(fn, it): it for it in items}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            out[futures[fut]] = fut.result()
            if i % 2000 == 0 or i == len(items):
                print(f"    {label}: {i:,}/{len(items):,}", flush=True)
    return out


# --------------------------------------------------------------------------
# Step 0: NDC universe with national volume weights
# --------------------------------------------------------------------------

def load_ndcs() -> pd.DataFrame:
    if not glob.glob(config.NATIONAL_FACT_GLOB):
        raise SystemExit("No SDUD national tables found; run src.ingest_sdud first.")
    return duckdb.sql(f"""
        SELECT ndc,
               mode(product_name) AS sdud_product_name,
               coalesce(sum(number_of_prescriptions), 0) AS weight_rx,
               coalesce(sum(medicaid_amount_reimbursed), 0)::DOUBLE AS weight_amt
        FROM read_parquet('{config.NATIONAL_FACT_GLOB}')
        GROUP BY ndc ORDER BY ndc
    """).df()


# --------------------------------------------------------------------------
# Step 1: NDC -> product RxCUI
# --------------------------------------------------------------------------

def resolve_product_rxcui(status: dict) -> tuple[str | None, str | None]:
    """Pick the product RxCUI for an NDC from its ndcstatus response.

    Active NDCs carry their RxCUI directly. Obsolete NDCs have an empty rxcui
    field; use the most recent history entry's activeRxcui (the concept the
    NDC was last mapped to), falling back to its original RxCUI.
    """
    if status.get("rxcui"):
        return status["rxcui"], "ndcstatus"
    history = sorted(status.get("ndcHistory") or [], key=lambda h: h.get("endDate") or "", reverse=True)
    for h in history:
        if h.get("activeRxcui"):
            return h["activeRxcui"], "ndc_history_active"
    for h in history:
        if h.get("originalRxcui"):
            return h["originalRxcui"], "ndc_history_original"
    return None, None


# --------------------------------------------------------------------------
# Step 2: product RxCUI -> ingredient
# --------------------------------------------------------------------------

def ingredient_from_related(related: dict[str, list[dict]]) -> dict | None:
    mins = related.get("MIN") or []
    ins = related.get("IN") or []
    component_ins = sorted(c["rxcui"] for c in ins)
    if len(mins) == 1:
        m = mins[0]
        return {"ingredient_rxcui": m["rxcui"], "ingredient_name": m["name"],
                "ingredient_tty": "MIN", "component_in_rxcuis": component_ins}
    if len(ins) == 1 and not mins:
        i = ins[0]
        return {"ingredient_rxcui": i["rxcui"], "ingredient_name": i["name"],
                "ingredient_tty": "IN", "component_in_rxcuis": component_ins}
    if ins or mins:
        # Co-packs / kits: several INs (or several MINs) without a single MIN.
        parts = sorted(ins, key=lambda c: c["name"].lower())
        return {"ingredient_rxcui": "+".join(component_ins),
                "ingredient_name": " / ".join(c["name"] for c in parts),
                "ingredient_tty": "MULTI_IN", "component_in_rxcuis": component_ins}
    return None


def resolve_ingredient(rx: RxNav, product_rxcui: str) -> dict:
    """Ingredient for a product RxCUI, following remaps for retired concepts.

    Returns the ingredient fields plus atc_rxcui: the product concept to use
    for ATCPROD (the remapped concept when the original is retired).
    """
    ing = ingredient_from_related(rx.related_ingredients(product_rxcui))
    if ing:
        return {**ing, "atc_rxcui": product_rxcui, "ingredient_source": "related"}

    hist = rx.history_status(product_rxcui)
    remapped = ((hist.get("derivedConcepts") or {}).get("remappedConcept")) or []
    # Prefer remapped concepts that are still active.
    remapped = sorted(remapped, key=lambda r: r.get("remappedActive") != "YES")
    for r in remapped:
        target = r.get("remappedRxCui")
        if not target:
            continue
        ing = ingredient_from_related(rx.related_ingredients(target))
        if ing:
            return {**ing, "atc_rxcui": target, "ingredient_source": "history_remap"}
    return {"ingredient_rxcui": None, "ingredient_name": None, "ingredient_tty": None,
            "component_in_rxcuis": [], "atc_rxcui": product_rxcui, "ingredient_source": None}


# --------------------------------------------------------------------------
# Step 3/4: ATC classes
# --------------------------------------------------------------------------

def atc4_codes(rows: list[dict], only_concept: str | None = None) -> set[str]:
    """Level-4 ATC codes from RxClass rows, optionally only for one concept.

    RxClass byRxcui on an ingredient also returns classes of combinations that
    contain it (atorvastatin -> "atorvastatin / ezetimibe" C10BA), so filter
    to the exact concept asked about when only_concept is given.
    """
    out = set()
    for r in rows:
        if only_concept and r["minConcept"]["rxcui"] != only_concept:
            continue
        code = r["rxclassMinConceptItem"]["classId"]
        if len(code) == 5:
            out.add(code)
    return out


def single_agent_classes(rx: RxNav, component_rxcuis: list[str]) -> set[str]:
    out = set()
    for c in component_rxcuis:
        out |= atc4_codes(rx.classes(c, "ATC"), only_concept=c)
    return out


def ingredient_level_classes(rx: RxNav, ing: dict) -> set[str]:
    """Fallback ATC classes from ingredient-level RxClass (source ATC)."""
    tty, rxcui, comps = ing["ingredient_tty"], ing["ingredient_rxcui"], ing["component_in_rxcuis"]
    if tty == "IN":
        return atc4_codes(rx.classes(rxcui, "ATC"), only_concept=rxcui)
    # Combinations: a component's ATC response sometimes lists the MIN itself
    # with its combination code; use that if present, else component classes.
    combo = set()
    if tty == "MIN":
        for c in comps:
            combo |= atc4_codes(rx.classes(c, "ATC"), only_concept=rxcui)
    return combo or single_agent_classes(rx, comps)


def choose_primary(candidates: dict[str, tuple[float, float]]) -> str:
    """Primary class: most prescriptions, then most spending, then ATC code."""
    return sorted(candidates.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[0][0]


# --------------------------------------------------------------------------
# Overrides
# --------------------------------------------------------------------------

def load_overrides() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not OVERRIDES_PATH.exists():
        empty = pd.DataFrame(columns=["ndc", "ingredient_rxcui", "ingredient_name", "atc4", "note"])
        return empty, empty
    ov = pd.read_csv(OVERRIDES_PATH, dtype=str, comment="#").fillna("")
    ov = ov.apply(lambda s: s.str.strip())
    bad = ov[(ov["ndc"] != "") & ~ov["ndc"].str.fullmatch(r"\d{11}")]
    if len(bad):
        raise ValueError(f"Overrides with malformed NDCs (need 11 digits):\n{bad}")
    return ov[ov["ndc"] != ""], ov[(ov["ndc"] == "") & (ov["ingredient_rxcui"] != "")]


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build() -> pd.DataFrame:
    rx = RxNav()
    ndcs = load_ndcs()
    print(f"  {len(ndcs):,} distinct NDCs across ingested years")

    print("[1/4] NDC -> product RxCUI")
    statuses = parallel_map(rx.ndc_status, ndcs["ndc"], "ndcstatus")
    ndcs["ndc_status"] = ndcs["ndc"].map(lambda n: statuses[n].get("status"))
    ndcs["rxnorm_product_name"] = ndcs["ndc"].map(lambda n: statuses[n].get("conceptName") or None)
    resolved = ndcs["ndc"].map(lambda n: resolve_product_rxcui(statuses[n]))
    ndcs["product_rxcui"] = resolved.map(lambda t: t[0])
    ndcs["product_rxcui_source"] = resolved.map(lambda t: t[1])

    print("[2/4] product -> ingredient")
    products = sorted(ndcs["product_rxcui"].dropna().unique())
    ingredients = parallel_map(lambda p: resolve_ingredient(rx, p), products, "ingredient")
    ing_cols = ["ingredient_rxcui", "ingredient_name", "ingredient_tty",
                "component_in_rxcuis", "atc_rxcui", "ingredient_source"]
    for col in ing_cols:
        ndcs[col] = ndcs["product_rxcui"].map(lambda p: ingredients[p][col] if p else None)

    print("[3/4] product -> ATC (ATCPROD)")
    atc_products = sorted(ndcs["atc_rxcui"].dropna().unique())
    prod_classes = parallel_map(lambda p: atc4_codes(rx.classes(p, "ATCPROD")), atc_products, "ATCPROD")

    # Combination products with several classes: keep the combination code(s).
    combo_ndcs = ndcs[ndcs["ingredient_tty"].isin(["MIN", "MULTI_IN"])]
    needs_resolution = {
        (r.atc_rxcui, tuple(r.component_in_rxcuis))
        for r in combo_ndcs.itertuples() if len(prod_classes.get(r.atc_rxcui, ())) > 1
    }
    singles = parallel_map(lambda k: single_agent_classes(rx, list(k[1])), needs_resolution, "combo check")

    def product_classes(row) -> set[str]:
        classes = set(prod_classes.get(row.atc_rxcui, set())) if row.atc_rxcui else set()
        key = (row.atc_rxcui, tuple(row.component_in_rxcuis or ()))
        if key in singles:
            combo_only = classes - singles[key]
            classes = combo_only or classes
        return classes

    ndcs["product_atc4"] = [product_classes(r) for r in ndcs.itertuples()]

    # NDC-level overrides (before the primary-class rule).
    ndc_ov, ing_ov = load_overrides()
    ndcs["overridden"] = False
    for o in ndc_ov.itertuples():
        idx = ndcs.index[ndcs["ndc"] == o.ndc]
        if len(idx) == 0:
            print(f"  warning: override NDC {o.ndc} not in SDUD; ignored")
            continue
        i = idx[0]
        if o.ingredient_rxcui:
            ndcs.at[i, "ingredient_rxcui"] = o.ingredient_rxcui
            ndcs.at[i, "ingredient_name"] = o.ingredient_name or o.ingredient_rxcui
            ndcs.at[i, "ingredient_tty"] = "IN" if "+" not in o.ingredient_rxcui else "MULTI_IN"
            ndcs.at[i, "ingredient_source"] = "override"
        if o.atc4:
            ndcs.at[i, "product_atc4"] = {o.atc4}
        ndcs.at[i, "overridden"] = True

    print("[4/4] primary ATC class per ingredient")
    names = rx.atc_class_names()
    ing_rows = []
    multi_rows = []
    mapped = ndcs[ndcs["ingredient_rxcui"].notna()]
    forced = dict(zip(ing_ov["ingredient_rxcui"], ing_ov["atc4"]))

    for ing_id, grp in mapped.groupby("ingredient_rxcui", sort=True):
        first = grp.iloc[0]
        # Credit each NDC's volume to its product classes, split evenly.
        cand: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for r in grp.itertuples():
            k = len(r.product_atc4)
            for code in r.product_atc4:
                cand[code][0] += r.weight_rx / k
                cand[code][1] += r.weight_amt / k
        source = "atcprod_volume"
        if not cand:
            fallback = ingredient_level_classes(rx, {
                "ingredient_tty": first.ingredient_tty, "ingredient_rxcui": ing_id,
                "component_in_rxcuis": first.component_in_rxcuis or []})
            cand = {c: [0.0, 0.0] for c in fallback}
            source = "ingredient_atc"
        candidates = {c: tuple(v) for c, v in cand.items()}

        if ing_id in forced:
            primary, source = forced[ing_id], "override"
        elif source == "ingredient_atc" and len(candidates) > 1:
            # No product-level class and several ingredient-level ones: there is
            # no volume to break the tie, and for combinations the candidates
            # are component classes, never the combination code (e.g. Trelegy
            # -> fluticasone's D07AC skin corticosteroid). Leave unclassified
            # for a manual override rather than guess (D-010).
            primary, source = None, "ambiguous_needs_override"
        elif candidates:
            primary = choose_primary(candidates)
        else:
            primary, source = None, None

        ing_rows.append({"ingredient_rxcui": ing_id, "atc4": primary, "atc_source": source,
                         "n_atc_candidates": len(candidates)})
        if len(candidates) > 1:
            total_rx = sum(v[0] for v in candidates.values()) or 1.0
            multi_rows.append({
                "ingredient_rxcui": ing_id,
                "ingredient_name": first.ingredient_name,
                "primary_atc4": primary,
                "primary_atc4_name": names.get(primary),
                "rule": source,
                "candidates": "; ".join(
                    f"{c} {names.get(c, '')} ({v[0] / total_rx:.1%} of rx)"
                    for c, v in sorted(candidates.items(), key=lambda kv: -kv[1][0])),
                "prescriptions": grp["weight_rx"].sum(),
                "medicaid_amount": grp["weight_amt"].sum(),
            })

    ing_df = pd.DataFrame(ing_rows)
    out = ndcs.merge(ing_df, on="ingredient_rxcui", how="left")

    # ATC hierarchy from the level-4 code: level 1 = 1 char, 2 = 3, 3 = 4.
    for level, n in ((1, 1), (2, 3), (3, 4), (4, 5)):
        code = out["atc4"].str[:n]
        out[f"atc{level}"] = code
        out[f"atc{level}_name"] = code.map(names)

    out["map_status"] = "mapped"
    out.loc[out["atc4"].isna(), "map_status"] = "no_atc"
    out.loc[out["ingredient_rxcui"].isna(), "map_status"] = "no_ingredient"
    out.loc[out["product_rxcui"].isna(), "map_status"] = "no_rxcui"
    out["product_atc4_codes"] = out["product_atc4"].map(lambda s: ",".join(sorted(s)) or None)
    out["component_in_rxcuis"] = out["component_in_rxcuis"].map(
        lambda c: ",".join(c) if isinstance(c, list) and c else None)

    cols = ["ndc", "sdud_product_name", "ndc_status", "product_rxcui", "product_rxcui_source",
            "rxnorm_product_name", "ingredient_rxcui", "ingredient_name", "ingredient_tty",
            "component_in_rxcuis", "ingredient_source", "product_atc4_codes",
            "atc1", "atc1_name", "atc2", "atc2_name", "atc3", "atc3_name", "atc4", "atc4_name",
            "atc_source", "map_status", "overridden"]
    out = out[cols].sort_values("ndc").reset_index(drop=True)

    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    out.to_parquet(CROSSWALK_PATH, index=False)
    ing_table = (out[out["ingredient_rxcui"].notna()]
                 .drop_duplicates("ingredient_rxcui")
                 [["ingredient_rxcui", "ingredient_name", "ingredient_tty", "component_in_rxcuis",
                   "atc1", "atc1_name", "atc2", "atc2_name", "atc3", "atc3_name", "atc4", "atc4_name",
                   "atc_source"]]
                 .merge(ing_df[["ingredient_rxcui", "n_atc_candidates"]], on="ingredient_rxcui"))
    ing_table.to_parquet(INGREDIENT_PATH, index=False)
    (pd.DataFrame(multi_rows).sort_values("prescriptions", ascending=False)
       .to_csv(MULTI_ATC_PATH, index=False))

    print(f"  RxNav calls: {rx.n_network:,} network, {rx.n_cached:,} cached")
    return out


# --------------------------------------------------------------------------
# Coverage report
# --------------------------------------------------------------------------

def coverage_report() -> dict:
    con = duckdb.connect()
    years = sorted(int(re.search(r"(\d{4})", p).group(1)) for p in glob.glob(config.NATIONAL_FACT_GLOB))
    report = {}
    for year in years:
        row = con.execute(f"""
            WITH f AS (
                SELECT n.number_of_prescriptions AS rx, n.medicaid_amount_reimbursed AS amt,
                       c.ingredient_rxcui IS NOT NULL AS has_ing, c.atc2 IS NOT NULL AS has_atc
                FROM read_parquet('{config.national_fact_path(year)}') n
                LEFT JOIN read_parquet('{CROSSWALK_PATH}') c USING (ndc))
            SELECT sum(rx), sum(rx) FILTER (WHERE has_ing), sum(rx) FILTER (WHERE has_atc),
                   sum(amt), sum(amt) FILTER (WHERE has_ing), sum(amt) FILTER (WHERE has_atc)
            FROM f""").fetchone()
        rx_all, rx_ing, rx_atc, amt_all, amt_ing, amt_atc = (float(x or 0) for x in row)
        report[str(year)] = {
            "basis": "national (XX) rows",
            "prescriptions_total": rx_all,
            "share_prescriptions_mapped_to_ingredient": round(rx_ing / rx_all, 4),
            "share_prescriptions_mapped_to_atc": round(rx_atc / rx_all, 4),
            "medicaid_amount_total": amt_all,
            "share_spending_mapped_to_ingredient": round(amt_ing / amt_all, 4),
            "share_spending_mapped_to_atc": round(amt_atc / amt_all, 4),
            "meets_95pct_spending_target": amt_atc / amt_all >= 0.95,
        }

    status = con.execute(f"""SELECT map_status, count(*) FROM read_parquet('{CROSSWALK_PATH}')
                             GROUP BY 1 ORDER BY 1""").fetchall()
    sources = con.execute(f"""SELECT coalesce(atc_source, 'none'), count(*) FROM read_parquet('{CROSSWALK_PATH}')
                              GROUP BY 1 ORDER BY 1""").fetchall()
    full = {"by_year": report, "ndcs_by_map_status": dict(status), "ndcs_by_atc_source": dict(sources)}
    COVERAGE_PATH.write_text(json.dumps(full, indent=2))

    # Unmapped NDCs ranked by spending: the worklist for crosswalk_overrides.csv.
    con.execute(f"""
        COPY (
            SELECT c.ndc, c.sdud_product_name, c.ndc_status, c.product_rxcui, c.rxnorm_product_name,
                   c.ingredient_name, c.map_status,
                   sum(n.number_of_prescriptions) AS prescriptions,
                   sum(n.medicaid_amount_reimbursed) AS medicaid_amount
            FROM read_parquet('{CROSSWALK_PATH}') c
            JOIN read_parquet('{config.NATIONAL_FACT_GLOB}') n USING (ndc)
            WHERE c.map_status <> 'mapped'
            GROUP BY ALL ORDER BY medicaid_amount DESC NULLS LAST
        ) TO '{UNMAPPED_PATH}' (HEADER, DELIMITER ',')""")
    return full


def main() -> int:
    build()
    rep = coverage_report()
    print(json.dumps(rep, indent=2))
    ok = all(y["meets_95pct_spending_target"] for y in rep["by_year"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
