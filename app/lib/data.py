"""Cached loaders for the dashboard tables in outputs/dashboard/.

The app only reads what src.export_dashboard and src.explore write; it does
no aggregation of its own, so every figure matches the pipeline outputs.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
DASH_DIR = ROOT / "outputs" / "dashboard"
YEAR = 2025
NATIONAL = "US"

STATE_NAMES = {
    "US": "United States (national)", "AK": "Alaska", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DC": "District of Columbia", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "IA": "Iowa", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "MA": "Massachusetts",
    "MD": "Maryland", "ME": "Maine", "MI": "Michigan", "MN": "Minnesota", "MO": "Missouri",
    "MS": "Mississippi", "MT": "Montana", "NC": "North Carolina", "ND": "North Dakota", "NE": "Nebraska",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NV": "Nevada", "NY": "New York",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VA": "Virginia", "VT": "Vermont", "WA": "Washington", "WI": "Wisconsin",
    "WV": "West Virginia", "WY": "Wyoming",
}


def _read(name: str, **kw) -> pd.DataFrame:
    # NDC columns must stay strings (leading zeros), so callers pass dtype.
    return pd.read_csv(DASH_DIR / f"{name}_{YEAR}.csv", **kw)


@st.cache_data
def savings_summary() -> pd.DataFrame:
    return _read("savings_summary")


@st.cache_data
def explore_index() -> pd.DataFrame:
    return _read("explore_index")


def national_savings(tier: str = "all") -> pd.Series:
    """The national primary-match savings row for one switch tier."""
    s = savings_summary()
    row = s[(s.geo == NATIONAL) & (s.match_level == "primary") & (s.tier == tier)]
    return row.iloc[0]


def states() -> list:
    """State codes in the data, national first, then by state name."""
    codes = set(savings_summary().geo) - {NATIONAL}
    return [NATIONAL] + sorted(codes, key=lambda c: STATE_NAMES.get(c, c))


@st.cache_data
def category_summary() -> pd.DataFrame:
    return _read("category_summary")


BRAND_TYPES = ["Brand, single source", "Brand, generic competition", "Reference biologic"]
BIOLOGIC_TYPES = ["Reference biologic", "Biosimilar", "Interchangeable biosimilar"]
BIOSIMILAR_TYPES = ["Biosimilar", "Interchangeable biosimilar"]

# Rankings' "Prescriptions vs. spending" card: every product type in exactly one
# group, so each bar adds up to 100%. The first two are the brand-name side.
MIX_GROUPS = [
    ("Brand drugs", ["Brand, single source", "Brand, generic competition"]),
    ("Reference biologics", ["Reference biologic"]),
    ("Generics", ["Generic", "Authorized generic"]),
    ("Biosimilars", BIOSIMILAR_TYPES),
    ("OTC and other", ["OTC / unapproved / unclassified"]),
]


@st.cache_data
def state_metrics() -> pd.DataFrame:
    """One row per geography (states plus US) with the map's metrics.

    Everything is a ratio or sum of category_summary and savings_summary rows,
    using the same definitions as the dashboard text: "generic share" is the
    Generic (ANDA) product type only, and "brand" is brand single source + brand
    generic competition + reference biologic (the Rankings lede's brand share).
    """
    cs = category_summary()

    def spend(types):
        return cs[cs.product_type.isin(types)].groupby("state").medicaid_amount.sum()

    tot = cs.groupby("state")[["prescriptions", "medicaid_amount"]].sum()
    out = pd.DataFrame(index=tot.index)
    out["spending"] = tot.medicaid_amount
    out["cost_per_rx"] = tot.medicaid_amount / tot.prescriptions
    out["generic_rx_share"] = cs[cs.product_type == "Generic"].set_index("state").share_prescriptions
    out["brand_spend_share"] = spend(BRAND_TYPES) / tot.medicaid_amount
    out["biosimilar_share"] = spend(BIOSIMILAR_TYPES) / spend(BIOLOGIC_TYPES)

    s = savings_summary()
    s = s[(s.match_level == "primary") & (s.tier == "all")].set_index("geo")
    out["gross_savings"] = s.gross_savings
    out["avg_rebate_savings"] = s.avg_rebate_savings
    out["savings_share"] = s.gross_savings / tot.medicaid_amount
    out.index.name = "geo"
    return out.reset_index()


def savings_for(geo: str, tier: str = "all") -> pd.Series:
    s = savings_summary()
    row = s[(s.geo == geo) & (s.match_level == "primary") & (s.tier == tier)]
    return row.iloc[0]


@st.cache_data
def rankings() -> pd.DataFrame:
    return _read("rankings")


@st.cache_data
def products(geo: str) -> pd.DataFrame:
    """Products selling each ingredient in one geography (national or a state file)."""
    df = _read("products_us" if geo == NATIONAL else "products_states")
    return df[df.geo == geo]


@st.cache_data
def product_ndcs() -> dict:
    """(product, labeler, product_type) -> NDC strings, highest national spending first."""
    df = _read("product_ndcs", dtype={"ndcs": str})
    return {(r.product, r.labeler, r.product_type): r.ndcs.split() for r in df.itertuples()}


@st.cache_data
def indications() -> dict:
    """(product, labeler) -> FDA label record (indication text, label name, revision date)."""
    df = _read("indications", dtype={"effective_time": str})
    return {(r["product"], r["labeler"]): r for r in df.to_dict("records")}


@st.cache_data
def explore_pid(product: str, labeler: str, product_type: str):
    """The Explore Alternatives id for a product, or None if Explore doesn't list it."""
    idx = explore_index()
    hit = idx[(idx["product"] == product) & (idx.labeler == labeler)]
    same = hit[hit.product_type == product_type]
    hit = same if len(same) else hit
    return int(hit.pid.iloc[0]) if len(hit) else None


@st.cache_data
def savings_brands() -> pd.DataFrame:
    """Top 25 brands per geography x tier view, every strength and form added up."""
    return _read("savings_brands")


@st.cache_data
def savings_brand_groups() -> pd.DataFrame:
    """The strength/form rows behind each brand in savings_brands."""
    return _read("savings_brand_groups")


@st.cache_data
def rebate_rates() -> pd.DataFrame:
    """MACPAC FY 2024 gross spending and rebates by state ("US" = national), with the
    implied brand rebate rate (src.rebates, D-016). Savings use the national rate."""
    return _read("rebate_rates", keep_default_na=False, na_values=[""])


@st.cache_data
def savings_ndcs() -> dict:
    """(match_level, tier, group_key) -> (brand NDCs, alternative NDCs), national, highest spending first."""
    df = _read("savings_ndcs", dtype={"brand_ndcs": str, "alt_ndcs": str})
    return {(r.match_level, r.tier, r.group_key): (str(r.brand_ndcs or "").split(), str(r.alt_ndcs or "").split())
            for r in df.fillna("").itertuples()}


@st.cache_data
def explore_pid_by_name(product: str, ingredient: str):
    """Explore id for a brand name (no labeler known): its highest-spending listing."""
    idx = explore_index()
    hit = idx[(idx["product"].str.lower() == product.lower()) & (idx.ingredient == ingredient)]
    if hit.empty:
        hit = idx[idx["product"].str.lower() == product.lower()]
    return int(hit.sort_values("medicaid_amount", ascending=False).pid.iloc[0]) if len(hit) else None


# ---- Explore Alternatives ------------------------------------------------------------
EXPLORE_STATE_FILES = 4          # must match src/explore.py STATE_FILES


@st.cache_data
def explore_groups() -> pd.DataFrame:
    """One row per product x presentation (RxNorm clinical drug + billing unit), national."""
    return _read("explore_groups", dtype={"ndcs": str})


@st.cache_data
def explore_status() -> dict:
    """group_key -> the brand/reference product's FDA status and national savings for that presentation."""
    df = _read("explore_status", dtype={"first_generic_approval": str, "latest_patent_expiry": str,
                                        "ref_exclusivity": str, "orphan_exclusivity": str,
                                        "as_of": str})
    return {r["group_key"]: r for r in df.to_dict("records")}


@st.cache_data
def explore_state_figures(geo: str) -> dict:
    """One state's figures, keyed by kind: p (product, by pid), g (group member, by gid), s (savings, by sid).

    States are split across EXPLORE_STATE_FILES files, alphabetically, the same
    rule as src.explore.state_buckets.
    """
    codes = sorted(set(category_summary().state) - {NATIONAL})
    size = -(-len(codes) // EXPLORE_STATE_FILES)
    df = _read(f"explore_states_{codes.index(geo) // size + 1}")
    df = df[df.state == geo]
    return {k: {int(r["id"]): r for r in part.to_dict("records")} for k, part in df.groupby("kind")}


def search_products(query: str, limit: int = 12) -> pd.DataFrame:
    """Products whose name or ingredient contains the query (case-insensitive), highest
    national spending first: the old Explore search."""
    idx = explore_index()
    q = query.strip().lower()
    hit = idx["product"].fillna("").str.lower().str.contains(q, regex=False) | \
        idx.ingredient.fillna("").str.lower().str.contains(q, regex=False)
    return idx[hit].sort_values("medicaid_amount", ascending=False).head(limit)
