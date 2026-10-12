"""
Cycle Count Exception Engine — Al Meera Consumer Goods
Rules R1-R11 (R8 dropped as mathematically invalid)
Priority scoring: severity × sales_velocity × recency_weight
Promo parent detection: brand-word + promo-keyword matching
"""

import pandas as pd
import numpy as np
import re


# ─── CONSTANTS ────────────────────────────────────────────────────────────────
SEVERITY = {"HIGH": 3, "MEDIUM": 2, "PROMO PARENT": 2, "LINKED SIBLING": 1}

SPECIAL_CHARS = re.compile(r"[!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]")
STRIP_LEADING_AL = re.compile(r"^AL\s+", re.IGNORECASE)

# ─── PROMO DETECTION CONSTANTS ────────────────────────────────────────────────
# Phrases that, when found in a description, identify a promo / assorted pack.
# Checked as substrings against the fully cleaned (uppercase) description.
PROMO_PHRASES = [
    "PROMO", "PROMOTION", "PROMOTIONAL",
    "ASSORTED", "ASST",
    "BUNDLE",
    "OFFER",
    "GIFT SET", "GIFT PACK", "GIFTSET",
    "COMBO",
    "VARIETY",
    "MULTIPACK", "MULTI PACK",
    "VALUE PACK",
    "MIXED PACK",
]

# Words too generic to serve as a brand identifier.
_BRAND_STOPWORDS = frozenset([
    "THE", "NEW", "MINI", "SUPER", "FRESH", "PURE", "EXTRA", "ULTRA",
    "ORIGINAL", "CLASSIC", "PREMIUM", "NATURAL", "LITE", "LIGHT",
    "BIG", "LARGE", "SMALL", "MEDIUM", "SPECIAL", "SELECTED",
    "MIXED", "MIX", "ASSORTED", "BUNDLE", "PROMO", "OFFER",
    "VARIETY", "COMBO", "VALUE", "MULTI",
])


# ─── KEY3 HELPERS ─────────────────────────────────────────────────────────────
def clean_desc(desc: str) -> str:
    """Clean description for matching: strip leading AL, replace special chars, normalise spaces."""
    if not isinstance(desc, str):
        return ""
    d = STRIP_LEADING_AL.sub("", desc.strip())
    d = SPECIAL_CHARS.sub(" ", d)
    d = re.sub(r"\s+", " ", d).strip()
    return d.upper()


def make_key3(desc: str) -> str:
    """Return first 3 words of cleaned description."""
    words = clean_desc(desc).split()
    return " ".join(words[:3]) if len(words) >= 3 else " ".join(words)


def _extract_brand(desc: str) -> str:
    """
    Return the first meaningful (non-stopword, ≥3 chars) word from the cleaned
    description.  This is used as the brand identifier for promo-parent matching.
    Falls back to the raw first word if every word is a stopword.
    """
    words = clean_desc(desc).split()
    for w in words:
        if w not in _BRAND_STOPWORDS and len(w) >= 3:
            return w
    return words[0] if words else ""


def _has_promo_phrase(desc: str) -> bool:
    """Return True if the cleaned description contains any promo phrase."""
    cleaned = clean_desc(desc)
    return any(phrase in cleaned for phrase in PROMO_PHRASES)


# ─── RULE ENGINE ──────────────────────────────────────────────────────────────
def apply_rules(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply R1-R11 to df. Returns df with new columns:
        Rule_Fired  — comma-separated rule codes that fired
        Severity    — HIGH / MEDIUM (highest if multiple)
        DoC         — Days of Cover
    Input columns required (case-insensitive match attempted):
        Material, Material_Desc, Opening_SOH, Closing_SOH, Sales30, Receipts10
    """
    # normalise column names
    col_map = {
        "material": "Material",
        "material_desc": "Material_Desc",
        "material desc": "Material_Desc",
        "material description": "Material_Desc",
        "description": "Material_Desc",
        "opening soh": "Opening_SOH",
        "opening_soh": "Opening_SOH",
        "opening": "Opening_SOH",
        "closing soh": "Closing_SOH",
        "closing_soh": "Closing_SOH",
        "closing": "Closing_SOH",
        "sales qty (30d)": "Sales30",
        "sales30": "Sales30",
        "sales_30": "Sales30",
        "sales qty": "Sales30",
        "sales": "Sales30",
        "receipts (10d)": "Receipts10",
        "receipts10": "Receipts10",
        "receipts_10": "Receipts10",
        "receipts": "Receipts10",
    }
    df = df.copy()
    df.columns = [col_map.get(c.lower().strip(), c) for c in df.columns]

    required = ["Material", "Opening_SOH", "Closing_SOH", "Sales30", "Receipts10"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Got: {list(df.columns)}")

    if "Material_Desc" not in df.columns:
        df["Material_Desc"] = df["Material"].astype(str)

    # coerce numerics
    for col in ["Opening_SOH", "Closing_SOH", "Sales30", "Receipts10"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # DoC = Closing / (Sales30/30); infinity / nan → 9999 (zero-sales case handled per rule)
    df["DoC"] = np.where(
        df["Sales30"] > 0,
        df["Closing_SOH"] / (df["Sales30"] / 30),
        np.nan,
    )

    # individual rule flags
    r1 = df["Closing_SOH"] < 0
    r2 = (df["Closing_SOH"] <= 0) & (df["Sales30"] > 0)
    r3 = (df["Sales30"] >= 30) & (df["DoC"] > 60)
    r4 = (df["Sales30"] > 0) & (df["DoC"] > 0) & (df["DoC"] <= 3)
    r5 = (
        (df["Closing_SOH"] > 50)
        & (df["Sales30"] == 0)
        & (df["Opening_SOH"] > 0)
        & (df["Receipts10"] == 0)
    )
    r6 = (
        (df["Closing_SOH"] > 0)
        & (df["Closing_SOH"] < 6)
        & (df["Sales30"] == 0)
        & (df["Receipts10"] == 0)
    )
    r7 = (df["Receipts10"] > 0) & (df["Sales30"] == 0)
    r9 = (df["Opening_SOH"] < 0) & (df["Closing_SOH"] > 0) & (df["Receipts10"] == 0)
    r10 = (df["Receipts10"] > 0) & (df["DoC"] > 0) & (df["DoC"] <= 5)
    r11 = (df["Opening_SOH"] < 0) & (df["Closing_SOH"] < 0)

    rule_map = {
        "R1": (r1, "HIGH"),
        "R2": (r2, "HIGH"),
        "R3": (r3, "MEDIUM"),
        "R4": (r4, "HIGH"),
        "R5": (r5, "MEDIUM"),
        "R6": (r6, "HIGH"),
        "R7": (r7, "MEDIUM"),
        "R9": (r9, "MEDIUM"),
        "R10": (r10, "HIGH"),
        "R11": (r11, "HIGH"),
    }

    fired_rules = [pd.Series([""] * len(df), index=df.index)]
    severity_num = pd.Series([0] * len(df), index=df.index)

    for code, (mask, sev) in rule_map.items():
        fired_rules.append(mask.map({True: code, False: ""}))
        severity_num = severity_num.where(~mask, other=np.maximum(severity_num, SEVERITY[sev]))

    def join_rules(row):
        codes = [v for v in row if v]
        return ", ".join(codes) if codes else ""

    rules_df = pd.concat(fired_rules[1:], axis=1)
    rules_df.columns = list(rule_map.keys())
    df["Rule_Fired"] = rules_df.apply(join_rules, axis=1)

    sev_label = {0: "", 1: "MEDIUM", 2: "MEDIUM", 3: "HIGH"}
    df["Severity"] = severity_num.map(sev_label)

    return df


# ─── SIBLING PAIRING ──────────────────────────────────────────────────────────
def flag_siblings(flagged_df: pd.DataFrame, full_df: pd.DataFrame) -> pd.DataFrame:
    """
    For every row in flagged_df where R1, R2, or R11 fired (negative-SOH triggers),
    find all rows in full_df sharing the same Key3 and mark them LINKED SIBLING.

    Returns full_df with added columns:
        Key3, Is_Negative_Trigger, Sibling_Of, Sibling_Flag
    """
    full_df = full_df.copy()
    full_df["Key3"] = full_df["Material_Desc"].apply(make_key3)

    neg_rules = {"R1", "R2", "R11"}

    def has_neg(rule_str):
        if not isinstance(rule_str, str):
            return False
        fired = {r.strip() for r in rule_str.split(",")}
        return bool(fired & neg_rules)

    flagged_df = flagged_df.copy()
    flagged_df["Key3"] = flagged_df["Material_Desc"].apply(make_key3)

    neg_triggers = flagged_df[flagged_df["Rule_Fired"].apply(has_neg)][["Material", "Key3"]].copy()

    # build map key3 → list of negative-trigger materials
    key3_to_neg = (
        neg_triggers.groupby("Key3")["Material"]
        .apply(list)
        .to_dict()
    )

    full_df["Is_Negative_Trigger"] = full_df["Material"].isin(neg_triggers["Material"])

    def sibling_of(row):
        if row["Is_Negative_Trigger"]:
            return ""
        k3 = row["Key3"]
        if k3 in key3_to_neg:
            parents = key3_to_neg[k3]
            # don't flag if the row is already in flagged_df (it has its own rule)
            if row["Material"] not in flagged_df["Material"].values:
                return ", ".join(str(m) for m in parents)
        return ""

    full_df["Sibling_Of"] = full_df.apply(sibling_of, axis=1)
    full_df["Sibling_Flag"] = full_df["Sibling_Of"].apply(
        lambda x: "LINKED SIBLING" if x else ""
    )

    return full_df


# ─── PROMO PARENT DETECTION ───────────────────────────────────────────────────
def flag_promo_parents(flagged_df: pd.DataFrame, full_df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect promo / assorted pack SKUs whose stock may be inflated because a component
    SKU went negative (open box / broken promo).

    Logic:
    1. Find all R1/R2/R11 negative triggers in flagged_df.
    2. Extract the brand word (first meaningful word) from each trigger's description.
    3. Scan full_df for SKUs that share that brand word AND contain a promo keyword
       phrase in their description, but are NOT already flagged exceptions.
    4. Mark those SKUs as PROMO PARENT and record which negative material triggered
       the match.

    Returns full_df with added columns:
        Promo_Parent_Of — comma-separated negative trigger materials
        Promo_Flag      — "PROMO PARENT" if matched, else ""
    """
    full_df = full_df.copy()

    neg_rules = {"R1", "R2", "R11"}

    def has_neg(rule_str):
        if not isinstance(rule_str, str):
            return False
        fired = {r.strip() for r in rule_str.split(",")}
        return bool(fired & neg_rules)

    flagged_copy = flagged_df.copy()
    neg_triggers = flagged_copy[flagged_copy["Rule_Fired"].apply(has_neg)].copy()

    if neg_triggers.empty:
        full_df["Promo_Parent_Of"] = ""
        full_df["Promo_Flag"] = ""
        return full_df

    # Build brand → list of negative trigger materials
    neg_triggers["_Brand"] = neg_triggers["Material_Desc"].apply(_extract_brand)
    brand_to_negs: dict[str, list] = {}
    for _, row in neg_triggers.iterrows():
        brand = row["_Brand"]
        if brand and len(brand) >= 3:
            brand_to_negs.setdefault(brand, []).append(str(row["Material"]))

    if not brand_to_negs:
        full_df["Promo_Parent_Of"] = ""
        full_df["Promo_Flag"] = ""
        return full_df

    # Materials already flagged as exceptions — don't double-flag them
    flagged_mats = set(flagged_copy["Material"].astype(str).tolist())

    # Compute brand word for every row in full_df
    full_df["_BrandWord"] = full_df["Material_Desc"].apply(_extract_brand)

    def check_promo(row) -> str:
        mat = str(row.get("Material", ""))
        if mat in flagged_mats:
            return ""
        brand = row.get("_BrandWord", "")
        if not brand or brand not in brand_to_negs:
            return ""
        if not _has_promo_phrase(str(row.get("Material_Desc", ""))):
            return ""
        # Filter out self-reference
        neg_mats = [m for m in brand_to_negs[brand] if m != mat]
        if not neg_mats:
            return ""
        return ", ".join(neg_mats)

    full_df["Promo_Parent_Of"] = full_df.apply(check_promo, axis=1)
    full_df["Promo_Flag"] = full_df["Promo_Parent_Of"].apply(
        lambda x: "PROMO PARENT" if x else ""
    )
    full_df = full_df.drop(columns=["_BrandWord"])

    return full_df


# ─── PRIORITY SCORING ─────────────────────────────────────────────────────────
def priority_score(row: pd.Series, history: dict | None = None) -> float:
    """
    Priority = severity_weight × sales_velocity × recency_weight
    severity_weight: HIGH=3, MEDIUM=2, PROMO PARENT=2, SIBLING=1
    sales_velocity:  daily sales rate = Sales30/30, floored at 0.01 to avoid zero
    recency_weight:  1 + 0.1 × days_since_last_flagged (capped at 3.0)
                     if no history → 1.0
    """
    sev = SEVERITY.get(
        row.get("Severity") or row.get("Sibling_Flag") or row.get("Promo_Flag") or "", 0
    )
    if sev == 0:
        return 0.0

    sales30 = float(row.get("Sales30", 0) or 0)
    velocity = max(sales30 / 30, 0.01)

    days_since = 0
    if history and str(row.get("Material")) in history:
        days_since = history[str(row["Material"])]
    recency = min(1.0 + 0.1 * days_since, 3.0)

    return round(sev * velocity * recency, 4)


def build_priority_list(
    flagged_df: pd.DataFrame,
    sibling_df: pd.DataFrame,
    promo_df: pd.DataFrame | None = None,
    history: dict | None = None,
    daily_cap: int | None = None,
) -> pd.DataFrame:
    """
    Combine flagged SKUs + siblings + promo parents, compute priority scores, sort descending.
    Returns the FULL ranked list.  Caller applies daily_cap for the daily count list.
    daily_cap=None (default) returns all exceptions — used for category drill-down.
    Pass daily_cap=500 (or any int) to get the capped daily list directly.
    """
    # flagged rows
    out = flagged_df[flagged_df["Rule_Fired"] != ""].copy()
    out["Source"] = "Exception"

    # sibling rows (only those not already flagged)
    sibs = sibling_df[
        (sibling_df["Sibling_Flag"] == "LINKED SIBLING")
        & (~sibling_df["Material"].isin(out["Material"]))
    ].copy()
    sibs["Severity"] = "LINKED SIBLING"
    sibs["Rule_Fired"] = "SIBLING"
    sibs["Source"] = "Linked Sibling"

    already_in = set(out["Material"].astype(str)).union(set(sibs["Material"].astype(str)))

    # promo parent rows (only those not already exception or sibling)
    promo_rows = pd.DataFrame()
    if promo_df is not None:
        promo_rows = promo_df[
            (promo_df["Promo_Flag"] == "PROMO PARENT")
            & (~promo_df["Material"].astype(str).isin(already_in))
        ].copy()
        promo_rows["Severity"] = "PROMO PARENT"
        promo_rows["Rule_Fired"] = "PROMO PARENT"
        promo_rows["Source"] = "Promo Parent"

    parts = [out, sibs]
    if not promo_rows.empty:
        parts.append(promo_rows)
    combined = pd.concat(parts, ignore_index=True)

    combined["Priority_Score"] = combined.apply(
        lambda r: priority_score(r, history), axis=1
    )

    combined = combined.sort_values("Priority_Score", ascending=False).reset_index(drop=True)
    combined["Daily_Rank"] = range(1, len(combined) + 1)

    if daily_cap is not None:
        return combined.head(daily_cap).reset_index(drop=True)
    return combined
