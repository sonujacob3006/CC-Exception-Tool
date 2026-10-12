"""
Excel Export — Al Meera Cycle Count Exception Tool
Produces a formatted, multi-sheet Excel workbook for download.
"""

import io
from datetime import date
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

# ─── BRAND COLOURS (Al Meera) ─────────────────────────────────────────────────
CLR_RED     = "FFC00000"   # HIGH
CLR_ORANGE  = "FFFF6600"   # MEDIUM
CLR_YELLOW  = "FFFFC000"   # LINKED SIBLING
CLR_PURPLE  = "FF7030A0"   # PROMO PARENT
CLR_HEADER  = "FF1F3864"   # Dark navy header
CLR_WHITE   = "FFFFFFFF"
CLR_LIGHT   = "FFD9E1F2"   # Light blue row alt
CLR_GREEN   = "FF70AD47"   # Positive/OK indicator

THIN = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _header_row(ws, columns: list[str], row: int = 1):
    fill = PatternFill("solid", fgColor=CLR_HEADER)
    font = Font(bold=True, color=CLR_WHITE, size=10)
    for col_idx, col_name in enumerate(columns, 1):
        cell = ws.cell(row=row, column=col_idx, value=col_name)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER


def _auto_width(ws, min_width=10, max_width=40):
    for col in ws.columns:
        max_len = max(
            (len(str(cell.value)) if cell.value else 0 for cell in col),
            default=0,
        )
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(
            max(max_len + 2, min_width), max_width
        )


def _severity_fill(severity: str) -> PatternFill | None:
    m = {
        "HIGH": PatternFill("solid", fgColor=CLR_RED),
        "MEDIUM": PatternFill("solid", fgColor=CLR_ORANGE),
        "LINKED SIBLING": PatternFill("solid", fgColor=CLR_YELLOW),
        "PROMO PARENT": PatternFill("solid", fgColor=CLR_PURPLE),
    }
    return m.get(severity)


def _write_df_to_sheet(ws, df: pd.DataFrame, sev_col: str | None = None, start_row: int = 2):
    for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=False), start_row):
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=False)
            if sev_col:
                sev_val = row[df.columns.get_loc(sev_col)]
                fill = _severity_fill(str(sev_val))
                if fill:
                    cell.fill = fill


# ─── MAIN BUILD ───────────────────────────────────────────────────────────────
def build_workbook(
    priority_df: pd.DataFrame,
    summary_stats: dict,
    history_df: pd.DataFrame | None = None,
    run_date: date | None = None,
) -> bytes:
    """
    Build and return an Excel workbook as bytes.

    priority_df   — output of engine.build_priority_list()
    summary_stats — dict with keys: total_input, flagged, siblings, high, medium, run_date
    history_df    — rotation history DataFrame (optional)
    """
    run_date = run_date or date.today()
    wb = Workbook()

    # ── Sheet 1: AM Summary ────────────────────────────────────────────────
    ws_sum = wb.active
    ws_sum.title = "AM Summary"
    ws_sum.sheet_view.showGridLines = False

    title_font = Font(bold=True, size=14, color=CLR_HEADER)
    ws_sum["B2"] = "Al Meera — Cycle Count Exception Report"
    ws_sum["B2"].font = title_font
    ws_sum["B3"] = f"Run Date: {run_date.strftime('%d %b %Y')}"
    ws_sum["B3"].font = Font(italic=True, size=10)

    stats = [
        ("List covers",              summary_stats.get("list_label", "Whole store")),
        ("SKUs in scope (whole store)", summary_stats.get("total_input", "—")),
        ("Exception SKUs, whole store (R1-R11)", summary_stats.get("flagged", "—")),
        ("Linked Siblings, whole store", summary_stats.get("siblings", "—")),
        ("HIGH, whole store",           summary_stats.get("high", "—")),
        ("MEDIUM, whole store",         summary_stats.get("medium", "—")),
        ("Daily cap applied (SKUs)",   summary_stats.get("cap", 500)),
        ("SKUs in This Output",       len(priority_df)),
    ]
    ws_sum["B5"] = "Metric"
    ws_sum["C5"] = "Value"
    ws_sum["B5"].font = Font(bold=True, color=CLR_WHITE)
    ws_sum["C5"].font = Font(bold=True, color=CLR_WHITE)
    ws_sum["B5"].fill = PatternFill("solid", fgColor=CLR_HEADER)
    ws_sum["C5"].fill = PatternFill("solid", fgColor=CLR_HEADER)

    for i, (label, val) in enumerate(stats, 6):
        ws_sum.cell(row=i, column=2, value=label)
        ws_sum.cell(row=i, column=3, value=val)
        if i % 2 == 0:
            for c in [2, 3]:
                ws_sum.cell(row=i, column=c).fill = PatternFill("solid", fgColor=CLR_LIGHT)

    ws_sum["B15"] = "Severity Legend"
    ws_sum["B15"].font = Font(bold=True)
    legend = [
        ("HIGH",           CLR_RED,    "Negative / critical stock anomaly — count today"),
        ("MEDIUM",         CLR_ORANGE, "Slow-mover / receipt anomaly — count within 3 days"),
        ("PROMO PARENT",   CLR_PURPLE, "Promo/assorted pack linked to a negative component — check and adjust"),
        ("LINKED SIBLING", CLR_YELLOW, "Same-family SKU with a negative sibling — verify count"),
    ]
    for i, (label, clr, note) in enumerate(legend, 16):
        ws_sum.cell(row=i, column=2, value=label).fill = PatternFill("solid", fgColor=clr)
        ws_sum.cell(row=i, column=2).font = Font(bold=True)
        ws_sum.cell(row=i, column=3, value=note)

    for col in ["B", "C", "D"]:
        ws_sum.column_dimensions[col].width = 38 if col == "C" else (40 if col == "B" else 20)

    # ── Sheet 2: Top SKUs (Priority List) ─────────────────────────────────
    ws_top = wb.create_sheet("Top Priority SKUs")
    ws_top.sheet_view.showGridLines = False
    ws_top.freeze_panes = "A2"

    display_cols = [
        "Daily_Rank", "Material", "Material_Desc", "Severity", "Rule_Fired",
        "Priority_Score", "Opening_SOH", "Closing_SOH", "Sales30", "Receipts10",
        "DoC", "Source",
    ]
    # keep only columns that actually exist
    display_cols = [c for c in display_cols if c in priority_df.columns]
    out_df = priority_df[display_cols].copy()

    header_labels = {
        "Daily_Rank": "Rank",
        "Material": "Material",
        "Material_Desc": "Description",
        "Severity": "Severity",
        "Rule_Fired": "Rule(s)",
        "Priority_Score": "Priority Score",
        "Opening_SOH": "Opening SOH",
        "Closing_SOH": "Closing SOH",
        "Sales30": "Sales 30D",
        "Receipts10": "Receipts 10D",
        "DoC": "Days of Cover",
        "Source": "Source",
    }
    pretty_cols = [header_labels.get(c, c) for c in display_cols]
    _header_row(ws_top, pretty_cols)

    sev_idx = display_cols.index("Severity") if "Severity" in display_cols else None
    for r_idx, row_vals in enumerate(out_df.itertuples(index=False), 2):
        row_list = list(row_vals)
        for c_idx, val in enumerate(row_list, 1):
            # round floats
            if isinstance(val, float):
                val = round(val, 2)
            cell = ws_top.cell(row=r_idx, column=c_idx, value=val)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center")
            if sev_idx is not None:
                fill = _severity_fill(str(row_list[sev_idx]))
                if fill:
                    cell.fill = fill

    _auto_width(ws_top)

    # ── Sheet 3: HIGH ──────────────────────────────────────────────────────
    ws_high = wb.create_sheet("HIGH Priority")
    ws_high.sheet_view.showGridLines = False
    ws_high.freeze_panes = "A2"
    high_df = priority_df[priority_df["Severity"] == "HIGH"][display_cols].copy()
    _header_row(ws_high, pretty_cols)
    _write_df_to_sheet(ws_high, high_df, sev_col="Severity" if "Severity" in display_cols else None)
    _auto_width(ws_high)

    # ── Sheet 4: MEDIUM ────────────────────────────────────────────────────
    ws_med = wb.create_sheet("MEDIUM Priority")
    ws_med.sheet_view.showGridLines = False
    ws_med.freeze_panes = "A2"
    med_df = priority_df[priority_df["Severity"] == "MEDIUM"][display_cols].copy()
    _header_row(ws_med, pretty_cols)
    _write_df_to_sheet(ws_med, med_df, sev_col="Severity" if "Severity" in display_cols else None)
    _auto_width(ws_med)

    # ── Sheet 5: Linked Siblings ───────────────────────────────────────────
    ws_sib = wb.create_sheet("Linked Siblings")
    ws_sib.sheet_view.showGridLines = False
    ws_sib.freeze_panes = "A2"
    sib_df = priority_df[priority_df["Severity"] == "LINKED SIBLING"][display_cols].copy()
    _header_row(ws_sib, pretty_cols)
    _write_df_to_sheet(ws_sib, sib_df, sev_col="Severity" if "Severity" in display_cols else None)
    _auto_width(ws_sib)

    # ── Sheet 6: Promo Parents ─────────────────────────────────────────────
    promo_df_sheet = priority_df[priority_df["Severity"] == "PROMO PARENT"].copy()
    if not promo_df_sheet.empty:
        ws_promo = wb.create_sheet("Promo Parents")
        ws_promo.sheet_view.showGridLines = False
        ws_promo.freeze_panes = "A2"
        # Add Promo_Parent_Of column if present
        promo_extra = [c for c in ["Promo_Parent_Of"] if c in priority_df.columns]
        promo_display = display_cols + promo_extra
        promo_display = [c for c in promo_display if c in promo_df_sheet.columns]
        promo_pretty = [header_labels.get(c, c.replace("_", " ")) for c in promo_display]
        _header_row(ws_promo, promo_pretty)
        _write_df_to_sheet(
            ws_promo, promo_df_sheet[promo_display],
            sev_col="Severity" if "Severity" in promo_display else None
        )
        _auto_width(ws_promo)

    # ── Sheet 7: Rotation History (optional) ──────────────────────────────
    if history_df is not None and not history_df.empty:
        ws_hist = wb.create_sheet("Rotation History")
        ws_hist.sheet_view.showGridLines = False
        ws_hist.freeze_panes = "A2"
        _header_row(ws_hist, list(history_df.columns))
        _write_df_to_sheet(ws_hist, history_df)
        _auto_width(ws_hist)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
