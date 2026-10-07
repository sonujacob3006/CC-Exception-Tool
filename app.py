"""
Al Meera — Cycle Count Exception Tool
Streamlit app — Phase 1  (with category drill-down)
"""

import io
from datetime import date

import pandas as pd
import streamlit as st

from engine import apply_rules, flag_siblings, build_priority_list
from master_utils import build_lookup, enrich, available_levels, HIER_COLS
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
    "daily_df":     None,   # top-N daily count list
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

    daily_cap = st.number_input("Daily Cap per L2 Department", min_value=50,
                                max_value=2000, value=500, step=50,
                                help="Top N exceptions per MC-L2 department. "
                                     "Falls back to a global cap if no Article Master is loaded.")

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

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def _apply_l2_cap(full_df: pd.DataFrame, cap: int,
                  l2_col: str = "MC-L2 Desc") -> pd.DataFrame:
    """
    Return top `cap` rows per MC-L2 department, then re-sort by Priority_Score.
    Falls back to a global head(cap) when the L2 column is absent
    (i.e. no Article Master was loaded).
    """
    if l2_col not in full_df.columns:
        return full_df.head(cap).reset_index(drop=True)

    _UNCAT = "__UNCATEGORISED__"
    tmp = full_df.copy()
    tmp["_l2_grp"] = tmp[l2_col].replace("", _UNCAT).fillna(_UNCAT)

    groups = [grp.head(cap) for _, grp in tmp.groupby("_l2_grp", sort=False)]
    daily = pd.concat(groups, ignore_index=True).drop(columns=["_l2_grp"])
    daily = daily.sort_values("Priority_Score", ascending=False).reset_index(drop=True)
    daily["Daily_Rank"] = range(1, len(daily) + 1)
    return daily


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
        st.caption("Required for category drill-down (L1–L5 filters). Use the July 2026 master or upload a newer one.")
        master_file = st.file_uploader("Article Master (Excel)", type=["xlsx","xls"], key="mf")
        if master_file:
            with st.spinner("Reading Article Master…"):
                try:
                    am = pd.read_excel(master_file)
                    lookup = build_lookup(am)
                    if lookup is None:
                        st.error("Could not find a Material column in this file. Check column names.")
                    else:
                        st.session_state.master_df = am
                        st.session_state.master_lookup = lookup
                        hier_found = [c for c in HIER_COLS if c in lookup.columns]
                        st.success(f"✅ {len(lookup):,} unique materials · Hierarchy: {', '.join(hier_found)}")
                        with st.expander("Preview"):
                            st.dataframe(lookup.head())
                except Exception as e:
                    st.error(f"Error: {e}")

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
            with st.spinner("Reading…"):
                try:
                    inp = pd.read_csv(input_file) if input_file.name.endswith(".csv") \
                          else pd.read_excel(input_file)
                    st.session_state.input_df = inp
                    st.success(f"✅ {len(inp):,} rows")
                    with st.expander("Preview"):
                        st.dataframe(inp.head())
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()
    if st.session_state.input_df is None:
        st.info("Upload transaction data above to enable Run.")

    if st.button("🚀 Run Exception Engine", disabled=st.session_state.input_df is None,
                 type="primary", use_container_width=True):
        with st.spinner("Running rules R1–R11 and building priority list…"):
            try:
                inp = st.session_state.input_df.copy()

                # 1. Rules
                flagged = apply_rules(inp)

                # 2. Siblings
                flagged_only = flagged[flagged["Rule_Fired"] != ""]
                sibling_result = flag_siblings(flagged_only, flagged)

                # 3. Full ranked list — NO cap
                history_dict = st.session_state.history.as_days_dict(run_date)
                full = build_priority_list(flagged_only, sibling_result,
                                           history=history_dict, daily_cap=None)

                # 4. Enrich with Article Master category hierarchy
                if st.session_state.master_lookup is not None:
                    full = enrich(full, st.session_state.master_lookup)

                st.session_state.full_df = full

                # 5. Daily list = top N per L2 department (global cap fallback)
                st.session_state.daily_df = _apply_l2_cap(full, int(daily_cap))

                # 6. Update rotation history
                st.session_state.history.record_flagged(
                    full["Material"].astype(str).tolist(), run_date
                )
                st.session_state.history.save()

                n_exc   = len(full[full["Source"] == "Exception"])
                n_sibs  = len(full[full["Severity"] == "LINKED SIBLING"])
                n_high  = len(full[full["Severity"] == "HIGH"])
                n_med   = len(full[full["Severity"] == "MEDIUM"])
                n_daily = len(st.session_state.daily_df)

                has_master = st.session_state.master_lookup is not None
                cap_msg = (f"top {int(daily_cap)} per L2 dept" if has_master
                           else f"top {int(daily_cap)} global (no Article Master)")
                st.success(
                    f"✅  {n_exc:,} exceptions + {n_sibs:,} siblings = "
                    f"{len(full):,} total  |  HIGH {n_high} · MEDIUM {n_med}  |  "
                    f"Daily list ({cap_msg}): {n_daily} SKUs"
                )
                cat_msg = (" Category drill-down enabled (shows ALL exceptions per category)."
                           if has_master
                           else " Upload Article Master to enable category drill-down.")
                st.info(f"Switch to **Daily List** or **Category Drill-Down** tabs.{cat_msg}")

            except Exception as e:
                st.error(f"Engine error: {e}")
                st.exception(e)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — DAILY LIST (top N)
# ══════════════════════════════════════════════════════════════════════════════
with tab_exc:
    if st.session_state.daily_df is None:
        st.info("Run the engine first.")
    else:
        df = st.session_state.daily_df

        # Describe the cap mode
        has_l2 = "MC-L2 Desc" in df.columns and df["MC-L2 Desc"].replace("","<NA>").dropna().shape[0] > 0
        if has_l2:
            n_l2_depts = df["MC-L2 Desc"].replace("","__UNCAT__").nunique()
            st.caption(
                f"📌 Capped at **{int(daily_cap)} per L2 department** "
                f"({n_l2_depts} departments in list). "
                f"Total all exceptions: {len(st.session_state.full_df):,}. "
                f"Use **Category Drill-Down** tab to see all exceptions within any department."
            )
        else:
            st.caption(
                f"📌 Global cap of **{int(daily_cap)}** — upload Article Master to switch to per-L2 capping."
            )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("In Daily List", len(df))
        c2.metric("HIGH",   len(df[df["Severity"]=="HIGH"]))
        c3.metric("MEDIUM", len(df[df["Severity"]=="MEDIUM"]))
        c4.metric("Siblings", len(df[df["Severity"]=="LINKED SIBLING"]))
        c5.metric("All exceptions", len(st.session_state.full_df))

        # L2 breakdown table
        if has_l2:
            with st.expander("Breakdown by L2 Department"):
                l2_grp = (
                    df.groupby("MC-L2 Desc")
                    .agg(
                        SKUs=("Material","count"),
                        HIGH=("Severity", lambda s: (s=="HIGH").sum()),
                        MEDIUM=("Severity", lambda s: (s=="MEDIUM").sum()),
                        Siblings=("Severity", lambda s: (s=="LINKED SIBLING").sum()),
                    )
                    .sort_values("SKUs", ascending=False)
                    .reset_index()
                )
                st.dataframe(l2_grp, use_container_width=True, hide_index=True)

        st.divider()

        # Quick filters
        f1, f2, f3 = st.columns([2,2,3])
        sev_f = f1.multiselect("Severity", ["HIGH","MEDIUM","LINKED SIBLING"],
                               default=["HIGH","MEDIUM","LINKED SIBLING"])
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

        def highlight_sev(row):
            c = {"HIGH":"background-color:#FFC0C0","MEDIUM":"background-color:#FFE0C0",
                 "LINKED SIBLING":"background-color:#FFF0A0"}
            return [c.get(row.get("Severity",""),"")] * len(row)

        disp = [c for c in ["Daily_Rank","Material","Material_Desc","Severity","Rule_Fired",
                             "Priority_Score","Opening_SOH","Closing_SOH","Sales30",
                             "Receipts10","DoC"] if c in view.columns]
        st.dataframe(view[disp].style.apply(highlight_sev, axis=1),
                     use_container_width=True, height=520)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CATEGORY DRILL-DOWN
# ══════════════════════════════════════════════════════════════════════════════
with tab_drill:
    if st.session_state.full_df is None:
        st.info("Run the engine first.")
    else:
        full = st.session_state.full_df
        levels = available_levels(full)

        if not levels:
            st.warning(
                "No category hierarchy found in the exception output. "
                "Upload an Article Master before running the engine to enable drill-down."
            )
        else:
            st.markdown("### 🔍 Drill down by category — L1 → L2 → L3 → L4 → L5")
            st.caption(
                "Each level filters the next. Showing **all** exceptions in the selected "
                "category (daily cap does not apply here)."
            )

            # ── Cascading selectors ───────────────────────────────────────
            drill_cols = st.columns(min(len(levels), 5))
            selections = {}
            filtered = full.copy()

            level_labels = {
                "MC-L1 Desc": "L1 — Division",
                "MC-L2 Desc": "L2 — Department",
                "MC-L3 Desc": "L3 — Section",
                "MC-L4 Desc": "L4 — Family",
                "MC-L5 Desc": "L5 — Sub-Family",
            }

            for i, level_col in enumerate(levels):
                opts = sorted(
                    [v for v in filtered[level_col].unique() if v and v != ""]
                )
                label = level_labels.get(level_col, level_col)
                # "All" = don't filter this level
                choices = ["— All —"] + opts
                with drill_cols[min(i, len(drill_cols)-1)]:
                    st.markdown(f'<p class="drill-label">{label}</p>', unsafe_allow_html=True)
                    sel = st.selectbox(" ", choices, key=f"drill_{level_col}",
                                       label_visibility="collapsed")
                selections[level_col] = sel
                if sel != "— All —":
                    filtered = filtered[filtered[level_col] == sel]

            # ── Category path breadcrumb ───────────────────────────────────
            path_parts = [v for v in selections.values() if v != "— All —"]
            if path_parts:
                path_str = " › ".join(path_parts)
                st.markdown(f'<div class="cat-path">📂 {path_str}</div>',
                            unsafe_allow_html=True)

            # ── Metrics for this category ──────────────────────────────────
            if len(filtered) == 0:
                st.warning("No exceptions found in this category combination.")
            else:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("SKUs in view", len(filtered))
                m2.metric("HIGH",   len(filtered[filtered["Severity"]=="HIGH"]))
                m3.metric("MEDIUM", len(filtered[filtered["Severity"]=="MEDIUM"]))
                m4.metric("Siblings", len(filtered[filtered["Severity"]=="LINKED SIBLING"]))

                # ── Severity filter inside drill-down ─────────────────────
                sev_drill = st.multiselect(
                    "Filter severity within this category",
                    ["HIGH","MEDIUM","LINKED SIBLING"],
                    default=["HIGH","MEDIUM","LINKED SIBLING"],
                    key="sev_drill"
                )
                if sev_drill:
                    filtered = filtered[filtered["Severity"].isin(sev_drill)]

                st.caption(f"Sorted by Priority Score (highest first) — {len(filtered):,} rows")

                def highlight_sev(row):
                    c = {"HIGH":"background-color:#FFC0C0",
                         "MEDIUM":"background-color:#FFE0C0",
                         "LINKED SIBLING":"background-color:#FFF0A0"}
                    return [c.get(row.get("Severity",""),"")] * len(row)

                # Show hierarchy columns + exception columns
                hier_in_df = [c for c in HIER_COLS if c in filtered.columns and
                              filtered[c].replace("","<NA>").dropna().shape[0] > 0]
                exc_cols = [c for c in ["Daily_Rank","Material","Material_Desc","Severity",
                                        "Rule_Fired","Priority_Score","Opening_SOH",
                                        "Closing_SOH","Sales30","Receipts10","DoC"]
                            if c in filtered.columns]
                # Combine: hierarchy first then exception data
                show_cols = hier_in_df + [c for c in exc_cols if c not in hier_in_df]

                st.dataframe(
                    filtered[show_cols].style.apply(highlight_sev, axis=1),
                    use_container_width=True,
                    height=500,
                )

                # ── Quick download for this category slice ────────────────
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

        # Category breakdown if hierarchy is available
        levels = available_levels(full)
        if levels:
            st.divider()
            st.subheader("Exceptions by Category")
            lvl_sel = st.selectbox("Group by", levels,
                                   format_func=lambda x: x.replace(" Desc",""))
            grp = (full[full["Severity"].isin(["HIGH","MEDIUM"])]
                   .groupby(lvl_sel)
                   .agg(Total=("Material","count"),
                        HIGH=("Severity", lambda s: (s=="HIGH").sum()),
                        MEDIUM=("Severity", lambda s: (s=="MEDIUM").sum()))
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
    if st.session_state.daily_df is None:
        st.info("Run the engine first.")
    else:
        daily = st.session_state.daily_df
        full  = st.session_state.full_df

        st.subheader("📥 Download Daily Count List (Excel)")
        has_l2_dl = ("MC-L2 Desc" in daily.columns and
                     daily["MC-L2 Desc"].replace("","<NA>").dropna().shape[0] > 0)
        cap_desc = f"top {int(daily_cap)} per L2 dept" if has_l2_dl else f"top {int(daily_cap)} global"
        st.caption(f"Colour-coded workbook ({cap_desc}, {len(daily)} SKUs): "
                   f"AM Summary · Top Priority · HIGH · MEDIUM · Linked Siblings")

        stats = {
            "total_input": len(st.session_state.input_df) if st.session_state.input_df is not None else "—",
            "flagged":  len(full[full["Source"]=="Exception"]) if "Source" in full.columns else "—",
            "siblings": len(full[full["Severity"]=="LINKED SIBLING"]),
            "high":     len(full[full["Severity"]=="HIGH"]),
            "medium":   len(full[full["Severity"]=="MEDIUM"]),
            "cap":      int(daily_cap),
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
                fname = f"CC_Exceptions_{run_date.strftime('%Y%m%d')}.xlsx"
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
st.caption(f"Al Meera Consumer Goods · Cycle Count Exception Tool · v1.2 · {date.today().strftime('%b %Y')}")
