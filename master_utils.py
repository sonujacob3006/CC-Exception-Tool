"""
Article Master utilities — Al Meera Cycle Count Exception Tool
Handles column detection, deduplication, joining the category hierarchy
(MC-L1 through MC-L5) onto the exception output, and the scope filter
(FMCG + NON FOOD only, no consignment).
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
    "Consignment": ["consignment", "consignment flag", "consignment item"],
}

# Canonical names we always use internally
HIER_COLS = [
    "MC-L1", "MC-L1 Desc",
    "MC-L2", "MC-L2 Desc",
    "MC-L3", "MC-L3 Desc",
    "MC-L4", "MC-L4 Desc",
    "MC-L5", "MC-L5 Desc",
]

# Agreed scope: only these MC-L1 divisions are cycle counted
DEFAULT_SCOPE_L1 = ["FMCG", "NON FOOD"]


def _normalise(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).lower().strip())


def norm_material(s: pd.Series) -> pd.Series:
    """
    Material codes as clean strings. 1000016, 1000016.0 and ' 1000016 ' all become
    '1000016', so files that store the code as int / float / text still join.
    """
    out = s.astype(str).str.strip()
    num = pd.to_numeric(s, errors="coerce").dropna()
    if len(num):
        out.loc[num.index] = num.astype("int64").astype(str)
    return out


def detect_columns(df: pd.DataFrame) -> dict[str, str]:
    """
    Return {canonical_name: actual_column_name} for every master column found.
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
    One row per Material with canonical column names:
    hierarchy columns + 'Consignment' ('Y' if ANY master row for the material is Y,
    else ''). Returns None if no Material column is found.
    """
    col_map = detect_columns(master_df)

    if "Material" not in col_map:
        return None

    work = master_df.copy()
    work["Material"] = norm_material(work[col_map["Material"]])

    cons = None
    if "Consignment" in col_map:
        is_y = work[col_map["Consignment"]].astype(str).str.strip().str.upper().eq("Y")
        cons = is_y.groupby(work["Material"]).max()

    lookup = work.drop_duplicates(subset=["Material"], keep="first").copy()

    rename = {v: k for k, v in col_map.items() if k != "Material" and v != k}
    lookup = lookup.rename(columns=rename)

    keep = ["Material"] + [c for c in HIER_COLS if c in lookup.columns]
    lookup = lookup[keep].copy()

    if cons is not None:
        lookup["Consignment"] = lookup["Material"].map(cons).fillna(False).map(
            {True: "Y", False: ""}
        )
    return lookup


def enrich(exceptions_df: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """
    Left-join hierarchy columns from lookup onto exceptions_df on Material.
    Unmatched rows get empty strings.
    """
    exc = exceptions_df.copy()
    exc["Material"] = norm_material(exc["Material"])

    hier_cols_present = [c for c in HIER_COLS if c in lookup.columns]
    merge_cols = ["Material"] + hier_cols_present

    # never duplicate hierarchy columns that are already on the frame
    exc = exc.drop(columns=[c for c in hier_cols_present if c in exc.columns])
    enriched = exc.merge(lookup[merge_cols], on="Material", how="left")

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


def l1_options(lookup: pd.DataFrame) -> list[str]:
    """All MC-L1 Desc values in the master, sorted."""
    if lookup is None or "MC-L1 Desc" not in lookup.columns:
        return []
    return sorted(v for v in lookup["MC-L1 Desc"].dropna().astype(str).str.strip().unique() if v)


def apply_scope(
    inp: pd.DataFrame,
    lookup: pd.DataFrame,
    l1_include: list[str],
    exclude_consignment: bool = True,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """
    Keep only SKUs that are in scope. Each excluded SKU gets exactly ONE reason,
    checked in this order:
        1. Not found in Article Master
        2. Category (MC-L1) not in scope   -> reason names the category
        3. Consignment = Y

    Returns (in_scope_input, audit_dict, excluded_df).
    The input keeps its original columns; Material is normalised.
    """
    df = inp.copy()
    mat_col = next((c for c in df.columns if _normalise(c) in ("material", "matnr", "mat")), None)
    if mat_col is None:
        raise ValueError("Transaction data has no Material column.")
    df[mat_col] = norm_material(df[mat_col])

    l1_set = {v.strip().upper() for v in l1_include}

    meta_cols = ["Material"] + [c for c in ["MC-L1 Desc", "Consignment"] if c in lookup.columns]
    j = df[[mat_col]].rename(columns={mat_col: "Material"}).merge(
        lookup[meta_cols], on="Material", how="left"
    )
    j.index = df.index

    has_cons = "Consignment" in lookup.columns
    l1 = j["MC-L1 Desc"].fillna("").astype(str).str.strip().str.upper() if "MC-L1 Desc" in j else pd.Series("", index=df.index)
    unmatched = j["MC-L1 Desc"].isna() if "MC-L1 Desc" in j else pd.Series(True, index=df.index)
    out_of_cat = ~unmatched & ~l1.isin(l1_set)
    consign = (
        (~unmatched & ~out_of_cat & j["Consignment"].fillna("").eq("Y"))
        if (has_cons and exclude_consignment) else pd.Series(False, index=df.index)
    )

    reason = pd.Series("", index=df.index)
    reason[unmatched] = "Not in Article Master"
    reason[out_of_cat] = "Category: " + j.loc[out_of_cat, "MC-L1 Desc"].astype(str)
    reason[consign] = "Consignment"

    keep = reason.eq("")
    audit = {
        "total": int(len(df)),
        "unmatched": int(unmatched.sum()),
        "by_category": j.loc[out_of_cat, "MC-L1 Desc"].value_counts().to_dict(),
        "consignment": int(consign.sum()),
        "consignment_checked": bool(has_cons and exclude_consignment),
        "eligible": int(keep.sum()),
        "l1_include": list(l1_include),
    }

    desc_col = next((c for c in df.columns if _normalise(c) in ("materialdesc", "materialdescription", "description")), None)
    excluded = pd.DataFrame({
        "Material": df.loc[~keep, mat_col].values,
        "Material_Desc": df.loc[~keep, desc_col].values if desc_col else "",
        "Exclusion_Reason": reason[~keep].values,
    })
    return df[keep].reset_index(drop=True), audit, excluded
