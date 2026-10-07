"""
Cycle Count Exception Engine — Al Meera Consumer Goods
Rules R1-R11 (R8 dropped as mathematically invalid)
Priority scoring: severity × sales_velocity × recency_weight
"""

import pandas as pd
import numpy as np
import re


# ─── CONSTANTS ────────────────────────────────────────────────────────────────
SEVERITY = {"HIGH": 3, "MEDIUM": 2, "LINKED SIBLING": 1}

SPECIAL_CHARS = re.compile(r"[!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]")
STRIP_LEADING_AL = re.compile(r"^AL\s+", re.IGNORECASE)


# ─── KEY3 HELPERS ─────────────────────────────────────────────────────────────
def clean_desc(desc: str) -> str:
    """Clean description for Key3 pairing: strip leading AL, replace special chars with space, normalise spaces."""
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
    neg_trigger_keys = set(neg_triggers["Key3"].unique())

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


# ─── PRIORITY SCORING ─────────────────────────────────────────────────────────
def priority_score(row: pd.Series, history: dict | None = None) -> float:
    """
    Priority = severity_weight × sales_velocity × recency_weight
    severity_weight: HIGH=3, MEDIUM=2, SIBLING=1
    sales_velocity:  daily sales rate = Sales30/30, floored at 0.01 to avoid zero
    recency_weight:  1 + 0.1 × days_since_last_flagged (capped at 3.0)
                     if no history → 1.0
    """
    sev = SEVERITY.get(row.get("Severity") or row.get("Sibling_Flag") or "", 0)
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
    history: dict | None = None,
    daily_cap: int | None = None,
) -> pd.DataFrame:
    """
    Combine flagged SKUs + siblings, compute priority scores, sort descending.
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

    combined = pd.concat([out, sibs], ignore_index=True)

    combined["Priority_Score"] = combined.apply(
        lambda r: priority_score(r, history), axis=1
    )

    combined = combined.sort_values("Priority_Score", ascending=False).reset_index(drop=True)
    combined["Daily_Rank"] = range(1, len(combined) + 1)

    if daily_cap is not None:
        return combined.head(daily_cap).reset_index(drop=True)
    return combined
