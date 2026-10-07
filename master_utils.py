"""
Article Master utilities — Al Meera Cycle Count Exception Tool
Handles column detection, deduplication, and joining category hierarchy
(MC-L1 through MC-L5) onto the exception output DataFrame.
"""

import pandas as pd
import re

# Possible column name variants in Al Meera Article Master exports
_COL_VARIANTS = {
    "Material": ["material", "matnr", "mat", "material_no", "material number"],
    "MC-L1":    ["mc-l1", "mc_l1", "mcl1", "mc l1", "level1", "l1"],
    "MC-L1 Desc": ["mc-l1 desc", "mc_l1_desc", "mcl1desc", "mc-l1desc",
                   "mc l1 desc", "l1 desc", "l1desc", "level1 desc"],
    "MC-L2":    ["mc-l2", "mc_l2", "mcl2", "mc l2", "level2", "l2"],
    "MC-L2 Desc": ["mc-l2 desc", "mc_l2_desc", "mcl2desc", "mc-l2desc",
                   "mc l2 desc", "l2 desc", "l2desc", "level2 desc"],
    "MC-L3":    ["mc-l3", "mc_l3", "mcl3", "mc l3", "level3", "l3"],
    "MC-L3 Desc": ["mc-l3 desc", "mc_l3_desc", "mcl3desc", "mc-l3desc",
                   "mc l3 desc", "l3 desc", "l3desc", "level3 desc"],
    "MC-L4":    ["mc-l4", "mc_l4", "mcl4", "mc l4", "level4", "l4"],
    "MC-L4 Desc": ["mc-l4 desc", "mc_l4_desc", "mcl4desc", "mc-l4desc",
                   "mc l4 desc", "l4 desc", "l4desc", "level4 desc"],
    "MC-L5":    ["mc-l5", "mc_l5", "mcl5", "mc l5", "level5", "l5"],
    "MC-L5 Desc": ["mc-l5 desc", "mc_l5_desc", "mcl5desc", "mc-l5desc",
                   "mc l5 desc", "l5 desc", "l5desc", "level5 desc"],
}

# Canonical names we always use internally
HIER_COLS = [
    "MC-L1", "MC-L1 Desc",
    "MC-L2", "MC-L2 Desc",
    "MC-L3", "MC-L3 Desc",
    "MC-L4", "MC-L4 Desc",
    "MC-L5", "MC-L5 Desc",
]


def _normalise(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", col.lower().strip())


def detect_columns(df: pd.DataFrame) -> dict[str, str]:
    """
    Return {canonical_name: actual_column_name} for every hierarchy column found.
    """
    actual_cols_norm = {_normalise(c): c for c in df.columns}
    mapping = {}
    for canonical, variants in _COL_VARIANTS.items():
        for v in variants:
            v_norm = _normalise(v)
            if v_norm in actual_cols_norm:
                mapping[canonical] = actual_cols_norm[v_norm]
                break
    return mapping


def build_lookup(master_df: pd.DataFrame) -> pd.DataFrame | None:
    """
    Deduplicate master on Material (first occurrence) and return a clean lookup
    table with canonical column names.  Returns None if Material column not found.
    """
    col_map = detect_columns(master_df)

    if "Material" not in col_map:
        return None

    mat_col = col_map["Material"]
    lookup = master_df.drop_duplicates(subset=[mat_col], keep="first").copy()

    # rename to canonical
    rename = {v: k for k, v in col_map.items() if v != k}
    lookup = lookup.rename(columns=rename)

    # keep only canonical columns that exist
    keep = ["Material"] + [c for c in HIER_COLS if c in lookup.columns]
    lookup = lookup[keep].copy()
    lookup["Material"] = lookup["Material"].astype(str).str.strip()
    return lookup


def enrich(exceptions_df: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """
    Left-join hierarchy columns from lookup onto exceptions_df on Material.
    Returns enriched DataFrame (new columns added; unmatched rows get empty strings).
    """
    exc = exceptions_df.copy()
    exc["Material"] = exc["Material"].astype(str).str.strip()

    hier_cols_present = [c for c in HIER_COLS if c in lookup.columns]
    merge_cols = ["Material"] + hier_cols_present

    enriched = exc.merge(lookup[merge_cols], on="Material", how="left")

    # fill missing hierarchy with empty string (not NaN) for cleaner UI
    for col in hier_cols_present:
        enriched[col] = enriched[col].fillna("").astype(str).str.strip()

    return enriched


def available_levels(df: pd.DataFrame) -> list[str]:
    """Return which MC-L* Desc columns are present and non-empty."""
    levels = []
    for col in ["MC-L1 Desc", "MC-L2 Desc", "MC-L3 Desc", "MC-L4 Desc", "MC-L5 Desc"]:
        if col in df.columns and df[col].replace("", pd.NA).dropna().shape[0] > 0:
            levels.append(col)
    return levels
