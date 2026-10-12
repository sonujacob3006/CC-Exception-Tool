"""
Al Meera — Cycle Count Exception Tool
Streamlit app — Phase 1  (with category drill-down)
"""

import io
from datetime import date

import pandas as pd
import streamlit as st

from engine import apply_rules, flag_siblings, flag_promo_parents, build_priority_list
from master_utils import (
    build_lookup, enrich, available_levels, HIER_COLS,
    norm_material, apply_scope, l1_options, DEFAULT_SCOPE_L1,
)
from scheduler import RotationHistory, is_valid_count_day, build_calendar
from excel_export import build_workbook

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Al Meera — Cycle Count",
    page_icon="🏪",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {
        background: linear-gradient(90deg, #1F3864, #2E75B6);
        color: white; padding: 1rem 1.5rem;
        border-radius: 8px; margin-bottom: 1rem;
    }
    .main-header h1 { color: white; margin: 0; font-size: 1.5rem; }
    .main-header p  { color: #BDD7EE; margin: 0.2rem 0 0; font-size: 0.85rem; }
    .drill-label { font-size: 0.78rem; color: #555; font-weight: 600;
                   text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2px; }
    .cat-path { background:#EEF4FF; border-left:3px solid #2E75B6;
                padding:6px 12px; border-radius:4px; font-size:0.85rem;
                color:#1F3864; margin-bottom:8px; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>🏪 Al Meera — Cycle Count Exception Tool</h1>
    <p>Rules R1–R11 · Priority-ranked · Category drill-down L1 → L5</p>
</div>
""", unsafe_allow_html=True)

# ─── SESSION STATE ────────────────────────────────────────────────────────────
defaults = {
    "master_df":    None,   # raw Article Master upload
    "master_lookup": None,  # deduplicated lookup table
    "input_df":     None,   # transaction data
    "full_df":      None,   # ALL exceptions ranked (no cap) + category cols
    "daily_df":     None,   # top-N count list for the selected L2 (set by Daily List tab)
    "daily_label":  "",     # category path of the daily list, e.g. "FMCG › GROCERY"
    "scope_audit":  None,   # eligibility audit from the last run
    "scope_excluded": None, # SKUs excluded by scope, with reason
    "history":      RotationHistory(),
    "run_date":     date.today(),
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    run_date = st.date_input("Count Date", value=st.session_state.run_date)
    st.session_state.run_date = run_date

    if not is_valid_count_day(run_date):
        st.warning("⚠️ Not a valid count day (Fri / Sat / blocked Thursday / holiday)")
    else:
        st.success("✅ Valid count day")

    daily_cap = st.number_input("Daily list cap (SKUs per L2)", min_value=50,
                                max_value=2000, value=500, step=50,
                                help="The Daily List shows one L2 department at a time: "
                                     "its top N SKUs by priority. Raise this to see more.")

    st.divider()
    st.subheader("🎯 Count Scope")
    scope_l1_opts = l1_options(st.session_state.master_lookup)
    scope_default = [o for o in scope_l1_opts if o.upper() in DEFAULT_SCOPE_L1]
    if scope_l1_opts:
        scope_l1 = st.multiselect(
            "Categories counted (MC-L1)", scope_l1_opts, default=scope_default,
            help="Agreed scope is FMCG + NON FOOD. Fresh Food, Consumable Items and "
                 "Passive Products are left out.")
    else:
        scope_l1 = list(DEFAULT_SCOPE_L1)
        st.caption("Load the Article Master to set scope. Default: FMCG + NON FOOD.")
    scope_excl_cons = st.checkbox("Exclude consignment items", value=True)
    st.caption("Stock location: sellable only (the input file must already hold "
               "Good location 1000 stock).")

    st.divider()
    st.subheader("📅 Calendar")
    preview_end = st.date_input("Check until", value=date(2026, 12, 31))
    if st.button("Count working days"):
        working = build_calendar(run_date, preview_end)
        st.info(f"**{len(working)} valid days** between {run_date} and {preview_end}")

    st.divider()
    st.subheader("🔄 Rotation History")
    if st.button("Clear history", type="secondary"):
        st.session_state.history.clear()
        st.success("Cleared.")
    hist_df = st.session_state.history.to_dataframe(run_date)
    st.caption(f"{len(hist_df)} materials tracked" if not hist_df.empty else "No history yet")

    st.divider()
    st.markdown("**Rules**")
    rules_ref = {"R1":"Closing<0 → HIGH","R2":"Closing≤0 & Sales>0 → HIGH",
                 "R3":"Sales≥30 & DoC>60 → MED","R4":"Sales>0 & DoC≤3 → HIGH",
                 "R5":"Closing>50, zero mvt → MED","R6":"0<Closing<6, zero mvt → HIGH",
                 "R7":"Receipts>0 & Sales=0 → MED","R9":"Opening<0, Closing>0 → MED",
                 "R10":"Receipts>0 & DoC≤5 → HIGH","R11":"Both SOH<0 → HIGH"}
    for c, d in rules_ref.items():
        st.markdown(f"**{c}** {d}")
    st.divider()
    st.markdown("**🟣 Promo Parent**")
    st.caption("Promo/assorted pack whose brand matches a negative-SOH component — "
               "count to confirm if an open box explains the negative stock.")

# ─── HELPERS ─────────────────────────────────────────────────────────────────
ALL = "— All —"
LEVEL_LABELS = {
    "MC-L1 Desc": "L1 — Division",
    "MC-L2 Desc": "L2 — Department",
    "MC-L3 Desc": "L3 — Section",
    "MC-L4 Desc": "L4 — Family",
    "MC-L5 Desc": "L5 — Sub-Family",
}


def cascade_filters(df: pd.DataFrame, prefix: str, required: tuple = ()):
    """
    L1 -> L5 drop-downs where each level only offers values that exist under the
    levels already picked, and the SKUs returned are only those under the full
    picked path (L1 FMCG => only FMCG's L2/L3/L4/L5 and SKUs; L2 GROCERY => only
    GROCERY's L3/L4/L5 and SKUs).

    Each widget key includes the path picked above it, so changing a parent level
    gives the children fresh widgets (no stale choice left over from another branch).
    `required` levels have no "All" option (the first value is selected).
    Returns (filtered_df, {level_col: choice}, [picked values in order]).
    """
    levels = [c for c in LEVEL_LABELS if c in df.columns
              and df[c].replace("", pd.NA).dropna().shape[0] > 0]
    out = df
    selections: dict = {}
    path: list = []
    cols = st.columns(len(levels)) if levels else []

    for i, lvl in enumerate(levels):
        counts = out[lvl].replace("", pd.NA).dropna().value_counts()
        opts = counts.index.tolist() if lvl in required else sorted(counts.index.tolist())
        choices = opts if lvl in required else [ALL] + opts
        with cols[i]:
            if not choices:
                st.selectbox(LEVEL_LABELS[lvl], ["(none)"], disabled=True,
                             key=f"{prefix}|{lvl}|empty|" + "|".join(path))
                selections[lvl] = ALL
                continue
            sel = st.selectbox(
                LEVEL_LABELS[lvl], choices,
                format_func=lambda v, _c=counts: v if v == ALL else f"{v} ({int(_c.get(v, 0)):,})",
                key=f"{prefix}|{lvl}|" + "|".join(path),
            )
        selections[lvl] = sel
        if sel != ALL:
            out = out[out[lvl] == sel]
            path.append(str(sel))
    return out, selections, path


def sev_style(row):
    c = {"HIGH": "background-color:#FFC0C0", "MEDIUM": "background-color:#FFE0C0",
         "PROMO PARENT": "background-color:#E5C8F0",
         "LINKED SIBLING": "background-color:#FFF0A0"}
    return [c.get(row.get("Severity", ""), "")] * len(row)


# ─── TABS ─────────────────────────────────────────────────────────────────────
tab_upload, tab_exc, tab_drill, tab_analysis, tab_dl = st.tabs(
    ["📤 Upload", "⚠️ Daily List", "🔍 Category Drill-Down", "📊 Analysis", "📥 Download"]
)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — UPLOAD
# ══════════════════════════════════════════════════════════════════════════════
with tab_upload:
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("📋 Article Master")
        st.caption("Required: sets the count scope (FMCG + NON FOOD, no consignment) and the L1–L5 filters. "
                   "Use the July 2026 master or upload a newer one.")
        master_file = st.file_uploader("Article Master (Excel)", type=["xlsx","xls"], key="mf")
        if master_file:
            fid = (master_file.name, master_file.size)
            parsed_now = False
            if st.session_state.get("master_fid") != fid:
                with st.spinner("Reading Article Master… (about 30 seconds for the full file)"):
                    try:
                        am = pd.read_excel(master_file)
                        lookup = build_lookup(am)
                        if lookup is None:
                            st.error("Could not find a Material column in this file. Check column names.")
                        else:
                            st.session_state.master_lookup = lookup
                            st.session_state.master_fid = fid
                            parsed_now = True
                    except Exception as e:
                        st.error(f"Error: {e}")
            if parsed_now:
                st.rerun()   # refresh the sidebar scope options
            lookup = st.session_state.master_lookup
            if lookup is not None and st.session_state.get("master_fid") == fid:
                hier_found = [c for c in HIER_COLS if c in lookup.columns]
                st.success(f"✅ {len(lookup):,} unique materials · Hierarchy: {', '.join(hier_found)}")
                if "Consignment" in lookup.columns:
                    st.caption(f"Consignment flag found: {int((lookup['Consignment'] == 'Y').sum()):,} consignment materials.")
                else:
                    st.warning("This master has no 'Consignment' column — consignment items "
                               "cannot be excluded. Use the July 2026 master or newer.")
                with st.expander("Preview"):
                    st.dataframe(lookup.head())

    with col_r:
        st.subheader("📦 Transaction Data")
        st.caption("One row per Material. Columns: Material, Material_Desc, Opening SOH, Closing SOH, Sales Qty (30D), Receipts (10D)")

        # template download
        sample = pd.DataFrame({
            "Material": ["100001","100002","100003"],
            "Material_Desc": ["VASELINE LIP ORIG 20G","VASELINE LIP SHEER 20G","DETTOL SURFACE CLEANER 500ML"],
            "Opening SOH": [50, 30, 120],
            "Closing SOH": [-3, 10, 160],
            "Sales Qty (30D)": [30, 0, 0],
            "Receipts (10D)": [0, 0, 80],
        })
        buf = io.BytesIO()
        sample.to_excel(buf, index=False)
        st.download_button("⬇️ Input Template", buf.getvalue(),
                           "CC_Input_Template.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        input_file = st.file_uploader("Transaction Data (Excel or CSV)",
                                      type=["xlsx","xls","csv"], key="if")
        if input_file:
            ifid = (input_file.name, input_file.size)
            if st.session_state.get("input_fid") != ifid:
                with st.spinner("Reading…"):
                    try:
                        inp = pd.read_csv(input_file) if input_file.name.endswith(".csv") \
                              else pd.read_excel(input_file)
                        st.session_state.input_df = inp
                        st.session_state.input_fid = ifid
                    except Exception as e:
                        st.error(f"Error: {e}")
            if st.session_state.input_df is not None and st.session_state.get("input_fid") == ifid:
                st.success(f"✅ {len(st.session_state.input_df):,} rows")
                with st.expander("Preview"):
                    st.dataframe(st.session_state.input_df.head())

    st.divider()
    inp_ready = st.session_state.input_df is not None
    master_ready = st.session_state.master_lookup is not None
    if not inp_ready:
        st.info("Upload transaction data above to enable Run.")
    elif not master_ready:
        st.warning("Load the Article Master first. It applies the count scope "
                   "(FMCG + NON FOOD, no consignment). Without it, Fresh Food, "
                   "Consumable and Passive items would be counted.")

    if st.button("🚀 Run Exception Engine", disabled=not (inp_ready and master_ready),
                 type="primary", use_container_width=True):
        with st.spinner("Applying scope, running rules R1–R11 and building priority list…"):
            try:
                if not scope_l1:
                    raise ValueError("Pick at least one category under Count Scope in the sidebar.")

                # 0. Scope: FMCG + NON FOOD, not consignment, found in Article Master
                inp, audit, excluded = apply_scope(
                    st.session_state.input_df, st.session_state.master_lookup,
                    scope_l1, scope_excl_cons,
                )
                st.session_state.scope_audit = audit
                st.session_state.scope_excluded = excluded
                if inp.empty:
                    raise ValueError("No SKUs left in scope. Check the Count Scope settings.")

                # 1. Rules
                flagged = apply_rules(inp)

                # 2. Siblings
                flagged_only = flagged[flagged["Rule_Fired"] != ""]
                sibling_result = flag_siblings(flagged_only, flagged)

                # 3. Promo parents
                promo_result = flag_promo_parents(flagged_only, flagged)

                # 4. Full ranked list — NO cap
                history_dict = st.session_state.history.as_days_dict(run_date)
                full = build_priority_list(
                    flagged_only, sibling_result,
                    promo_df=promo_result,
                    history=history_dict, daily_cap=None
                )

                # 5. Enrich with Article Master category hierarchy
                full = enrich(full, st.session_state.master_lookup)

                st.session_state.full_df = full
                st.session_state.daily_df = None   # built per L2 in the Daily List tab

                # 6. Update rotation history
                st.session_state.history.record_flagged(
                    full["Material"].astype(str).tolist(), run_date
                )
                st.session_state.history.save()

                n_exc   = len(full[full["Source"] == "Exception"])
                n_sibs  = len(full[full["Severity"] == "LINKED SIBLING"])
                n_promo = len(full[full["Severity"] == "PROMO PARENT"])
                n_high  = len(full[full["Severity"] == "HIGH"])
                n_med   = len(full[full["Severity"] == "MEDIUM"])

                promo_msg = f" · 🟣 Promo Parents {n_promo}" if n_promo else ""
                st.success(
                    f"✅  {audit['eligible']:,} SKUs in scope → {n_exc:,} exceptions "
                    f"+ {n_sibs:,} siblings{promo_msg} = {len(full):,} total  |  "
                    f"HIGH {n_high:,} · MEDIUM {n_med:,}"
                )
                st.info("Open **Daily List** and pick an L2 department "
                        f"(shows its top {int(daily_cap)}), or **Category Drill-Down** "
                        "for every exception in a category.")

            except Exception as e:
                st.error(f"Engine error: {e}")
                st.exception(e)

    # ── Scope audit (what went in, what was left out and why) ────────────────
    audit = st.session_state.scope_audit
    if audit:
        with st.expander("🧾 Scope audit — what was counted and what was left out", expanded=True):
            rows = [("SKUs in the uploaded file", audit["total"])]
            rows.append(("Left out: not found in Article Master", audit["unmatched"]))
            for cat, n in sorted(audit["by_category"].items(), key=lambda kv: -kv[1]):
                rows.append((f"Left out: category {cat}", n))
            if audit["consignment_checked"]:
                rows.append(("Left out: consignment", audit["consignment"]))
            rows.append(("In scope (sent to the rules)", audit["eligible"]))
            st.dataframe(pd.DataFrame(rows, columns=["Step", "SKUs"]),
                         use_container_width=True, hide_index=True)
            st.caption(f"Categories counted: {', '.join(audit['l1_include'])}. "
                       "Each left-out SKU is shown once, under the first reason that applies.")
            if not audit["consignment_checked"]:
                st.warning("Consignment was not excluded (setting off, or no Consignment column in the master).")
            ex = st.session_state.scope_excluded
            if ex is not None and len(ex):
                st.download_button("⬇️ Download left-out SKUs (CSV)",
                                   ex.to_csv(index=False).encode(),
                                   f"CC_LeftOut_{run_date.strftime('%Y%m%d')}.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — DAILY LIST (one L2 department at a time, top N)
# ══════════════════════════════════════════════════════════════════════════════
with tab_exc:
    if st.session_state.full_df is None:
        st.info("Run the engine first.")
    else:
        full_all = st.session_state.full_df
        cap = int(daily_cap)

        st.markdown("### ⚠️ Daily count list")
        st.caption(
            f"Pick an L2 department: the list shows its top **{cap}** SKUs by priority "
            "(change the cap in the sidebar). L1 narrows the L2 choices; L3–L5 narrow the list further."
        )
        sl, _sel, _path = cascade_filters(full_all, "dl", required=("MC-L2 Desc",))
        sl = sl.sort_values("Priority_Score", ascending=False)
        n_slice = len(sl)
        label = " › ".join(_path) if _path else "selection"

        if n_slice == 0:
            st.session_state.daily_df = None
            st.session_state.daily_label = ""
            st.info("No exceptions in this selection.")
        else:
            df = sl.head(cap).copy().reset_index(drop=True)
            df["Daily_Rank"] = range(1, len(df) + 1)
            st.session_state.daily_df = df
            st.session_state.daily_label = label

            if n_slice > cap:
                st.caption(f"📌 Showing the top **{len(df):,}** of {n_slice:,} exceptions in **{label}**. "
                           f"Raise the cap in the sidebar to see more. "
                           f"Whole store: {len(full_all):,} exceptions.")
            else:
                st.caption(f"📌 Showing all **{n_slice:,}** exceptions in **{label}**. "
                           f"Whole store: {len(full_all):,} exceptions.")

            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("In this list", len(df))
            c2.metric("HIGH",   len(df[df["Severity"]=="HIGH"]))
            c3.metric("MEDIUM", len(df[df["Severity"]=="MEDIUM"]))
            c4.metric("🟣 Promo Parents", len(df[df["Severity"]=="PROMO PARENT"]))
            c5.metric("Siblings", len(df[df["Severity"]=="LINKED SIBLING"]))
            c6.metric("In selection (all)", n_slice)

            st.divider()

            # Quick filters
            f1, f2, f3 = st.columns([2,2,3])
            sev_f = f1.multiselect("Severity", ["HIGH","MEDIUM","PROMO PARENT","LINKED SIBLING"],
                                   default=["HIGH","MEDIUM","PROMO PARENT","LINKED SIBLING"])
            rule_f = f2.text_input("Rule (e.g. R1)")
            desc_f = f3.text_input("Search description")

            view = df.copy()
            if sev_f:
                view = view[view["Severity"].isin(sev_f)]
            if rule_f.strip():
                view = view[view["Rule_Fired"].str.contains(rule_f.strip(), case=False, na=False)]
            if desc_f.strip():
                view = view[view["Material_Desc"].str.contains(desc_f.strip(), case=False, na=False)]

            st.caption(f"Showing {len(view):,} of {len(df):,}")

            disp = [c for c in ["Daily_Rank","Material","Material_Desc","MC-L3 Desc","MC-L5 Desc",
                                 "Severity","Rule_Fired","Promo_Parent_Of","Priority_Score",
                                 "Opening_SOH","Closing_SOH","Sales30","Receipts10","DoC"]
                    if c in view.columns]
            st.dataframe(view[disp].style.apply(sev_style, axis=1),
                         use_container_width=True, height=520)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CATEGORY DRILL-DOWN (every exception, no cap)
# ══════════════════════════════════════════════════════════════════════════════
with tab_drill:
    if st.session_state.full_df is None:
        st.info("Run the engine first.")
    else:
        full = st.session_state.full_df

        if not available_levels(full):
            st.warning(
                "No category hierarchy found in the exception output. "
                "Load the Article Master and run the engine again."
            )
        else:
            st.markdown("### 🔍 Drill down by category — L1 → L2 → L3 → L4 → L5")
            st.caption(
                "Each level only offers what sits under the levels above it, and the list "
                "shows only SKUs under the full path. Shows **all** exceptions "
                "(the daily cap does not apply here)."
            )

            filtered, _dsel, path_parts = cascade_filters(full, "dd")

            if path_parts:
                st.markdown(f'<div class="cat-path">📂 {" › ".join(path_parts)}</div>',
                            unsafe_allow_html=True)

            if len(filtered) == 0:
                st.warning("No exceptions found in this category combination.")
            else:
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("SKUs in view", len(filtered))
                m2.metric("HIGH",   len(filtered[filtered["Severity"]=="HIGH"]))
                m3.metric("MEDIUM", len(filtered[filtered["Severity"]=="MEDIUM"]))
                m4.metric("🟣 Promo Parents", len(filtered[filtered["Severity"]=="PROMO PARENT"]))
                m5.metric("Siblings", len(filtered[filtered["Severity"]=="LINKED SIBLING"]))

                sev_drill = st.multiselect(
                    "Filter severity within this category",
                    ["HIGH","MEDIUM","PROMO PARENT","LINKED SIBLING"],
                    default=["HIGH","MEDIUM","PROMO PARENT","LINKED SIBLING"],
                    key="sev_drill"
                )
                if sev_drill:
                    filtered = filtered[filtered["Severity"].isin(sev_drill)]

                st.caption(f"Sorted by Priority Score (highest first) — {len(filtered):,} rows")

                hier_in_df = [c for c in HIER_COLS if c in filtered.columns and
                              filtered[c].replace("","<NA>").dropna().shape[0] > 0]
                exc_cols = [c for c in ["Daily_Rank","Material","Material_Desc","Severity",
                                        "Rule_Fired","Promo_Parent_Of","Priority_Score","Opening_SOH",
                                        "Closing_SOH","Sales30","Receipts10","DoC"]
                            if c in filtered.columns]
                show_cols = hier_in_df + [c for c in exc_cols if c not in hier_in_df]

                st.dataframe(
                    filtered[show_cols].style.apply(sev_style, axis=1),
                    use_container_width=True,
                    height=500,
                )

                csv_bytes = filtered[show_cols].to_csv(index=False).encode()
                fname_parts = [v.replace(" ","_")[:20] for v in path_parts] or ["All"]
                fname = f"CC_Drill_{'_'.join(fname_parts)}_{run_date.strftime('%Y%m%d')}.csv"
                st.download_button(
                    f"⬇️ Download this category slice ({len(filtered)} rows)",
                    csv_bytes, fname, "text/csv"
                )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab_analysis:
    if st.session_state.full_df is None:
        st.info("Run the engine first.")
    else:
        full = st.session_state.full_df

        c_a, c_b = st.columns(2)
        with c_a:
            st.subheader("Severity Breakdown (full exception universe)")
            sc = full["Severity"].value_counts().reset_index()
            sc.columns = ["Severity","Count"]
            st.dataframe(sc, use_container_width=True, hide_index=True)

        with c_b:
            st.subheader("Rules Fired (top 10)")
            from collections import Counter
            rc: Counter = Counter()
            for rs in full["Rule_Fired"].dropna():
                for r in str(rs).split(","):
                    r = r.strip()
                    if r and r != "SIBLING":
                        rc[r] += 1
            rd = pd.DataFrame(rc.most_common(10), columns=["Rule","Count"])
            st.dataframe(rd, use_container_width=True, hide_index=True)

        # Promo parent list
        promo_full = full[full["Severity"] == "PROMO PARENT"]
        if not promo_full.empty:
            st.divider()
            st.subheader(f"🟣 Promo Parents ({len(promo_full)} SKUs)")
            st.caption(
                "These promo / assorted packs share a brand with a negative-SOH component. "
                "Count them to confirm whether opening the pack explains the negative stock."
            )
            promo_show_cols = [c for c in [
                "Daily_Rank","Material","Material_Desc","Promo_Parent_Of",
                "Priority_Score","Opening_SOH","Closing_SOH","Sales30"
            ] if c in promo_full.columns]
            st.dataframe(
                promo_full[promo_show_cols].style.apply(
                    lambda row: ["background-color:#E5C8F0"] * len(row), axis=1
                ),
                use_container_width=True, hide_index=True
            )

        # Category breakdown if hierarchy is available
        levels = available_levels(full)
        if levels:
            st.divider()
            st.subheader("Exceptions by Category")
            lvl_sel = st.selectbox("Group by", levels,
                                   format_func=lambda x: x.replace(" Desc",""))
            grp = (full[full["Severity"].isin(["HIGH","MEDIUM","PROMO PARENT"])]
                   .groupby(lvl_sel)
                   .agg(Total=("Material","count"),
                        HIGH=("Severity", lambda s: (s=="HIGH").sum()),
                        MEDIUM=("Severity", lambda s: (s=="MEDIUM").sum()),
                        Promo=("Severity", lambda s: (s=="PROMO PARENT").sum()))
                   .sort_values("Total", ascending=False)
                   .reset_index())
            st.dataframe(grp, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Top 20 by Priority Score")
        t20_cols = [c for c in ["Daily_Rank","Material","Material_Desc","Severity",
                                 "Rule_Fired","Priority_Score","Closing_SOH","Sales30"]
                    if c in full.columns]
        st.dataframe(full.head(20)[t20_cols], use_container_width=True, hide_index=True)

        # Rotation history
        hist_df = st.session_state.history.to_dataframe(run_date)
        if not hist_df.empty:
            st.divider()
            st.subheader("Rotation History (top 50 longest unseen)")
            st.dataframe(hist_df.sort_values("Days_Since", ascending=False).head(50),
                         use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════
with tab_dl:
    if st.session_state.full_df is None:
        st.info("Run the engine first.")
    elif st.session_state.daily_df is None:
        st.info("Nothing in the Daily List selection. Pick a department with exceptions in the Daily List tab.")
    else:
        daily = st.session_state.daily_df
        full  = st.session_state.full_df
        list_label = st.session_state.daily_label or "selection"

        st.subheader("📥 Download Daily Count List (Excel)")
        st.caption(f"Colour-coded workbook for **{list_label}** (top {int(daily_cap)}, "
                   f"{len(daily):,} SKUs): AM Summary · Top Priority · HIGH · MEDIUM · "
                   f"Linked Siblings · Promo Parents. Pick another department in the "
                   f"Daily List tab to download that one.")

        audit_dl = st.session_state.scope_audit or {}
        stats = {
            "total_input": audit_dl.get("eligible", "—"),
            "flagged":  len(full[full["Source"]=="Exception"]) if "Source" in full.columns else "—",
            "siblings": len(full[full["Severity"]=="LINKED SIBLING"]),
            "high":     len(full[full["Severity"]=="HIGH"]),
            "medium":   len(full[full["Severity"]=="MEDIUM"]),
            "cap":      int(daily_cap),
            "list_label": list_label,
        }

        hist_df = st.session_state.history.to_dataframe(run_date)

        with st.spinner("Building Excel…"):
            try:
                xlsx = build_workbook(
                    priority_df=daily,
                    summary_stats=stats,
                    history_df=hist_df if not hist_df.empty else None,
                    run_date=run_date,
                )
                safe_lbl = "".join(ch if ch.isalnum() else "_" for ch in list_label)[:40]
                fname = f"CC_Exceptions_{safe_lbl}_{run_date.strftime('%Y%m%d')}.xlsx"
                st.download_button("⬇️ Download Excel (Daily List)", xlsx, fname,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   type="primary", use_container_width=True)
                st.success(f"Ready: {fname}  ({len(daily)} SKUs)")
            except Exception as e:
                st.error(f"Excel error: {e}")
                st.exception(e)

        st.divider()
        st.subheader("📥 Download Full Exception Universe (CSV)")
        st.caption(f"All {len(full):,} exceptions + siblings — no daily cap, with category columns if available")
        full_csv = full.to_csv(index=False).encode()
        st.download_button("⬇️ Download Full Exception CSV", full_csv,
                           f"CC_Full_Exceptions_{run_date.strftime('%Y%m%d')}.csv",
                           "text/csv", use_container_width=True)

        st.divider()
        st.subheader("📥 Download CSV (Daily List only)")
        csv = daily.to_csv(index=False).encode()
        st.download_button("⬇️ Download Daily List CSV", csv,
                           f"CC_DailyList_{run_date.strftime('%Y%m%d')}.csv", "text/csv")

st.markdown("---")
st.caption(f"Al Meera Consumer Goods · Cycle Count Exception Tool · v1.4 · {date.today().strftime('%b %Y')}")
