# Al Meera — Cycle Count Exception Tool

> **Streamlit app** for daily inventory exception detection across FMCG and NON FOOD categories.

## What it does

- Applies **10 exception rules (R1–R11)** to daily SAP transaction data
- **Priority-ranks** flagged SKUs using: Severity × Sales Velocity × Recency Weight
- Identifies **linked siblings** (same product family as a negative-SOH item)
- Caps output at a configurable **daily count limit** (default 500)
- Exports a **formatted Excel workbook** — colour-coded by severity

## Exception Rules

| Rule | Condition | Severity |
|------|-----------|----------|
| R1  | Closing SOH < 0 | HIGH |
| R2  | Closing ≤ 0 AND Sales > 0 | HIGH |
| R3  | Sales ≥ 30 AND DoC > 60 days | MEDIUM |
| R4  | Sales > 0 AND 0 < DoC ≤ 3 days | HIGH |
| R5  | Closing > 50, no sales, no receipts | MEDIUM |
| R6  | 0 < Closing < 6, no sales, no receipts | HIGH |
| R7  | Receipts > 0 AND Sales = 0 | MEDIUM |
| R9  | Opening < 0 AND Closing > 0, no receipts | MEDIUM |
| R10 | Receipts > 0 AND 0 < DoC ≤ 5 days | HIGH |
| R11 | Opening < 0 AND Closing < 0 | HIGH |

**DoC** = Closing SOH ÷ (Sales30 ÷ 30)

## Input format

One row per Material. Required columns:

| Column | Notes |
|--------|-------|
| Material | SAP material number |
| Material_Desc | Product description (for sibling matching) |
| Opening SOH | Opening stock (30-day period) |
| Closing SOH | Closing stock (current) |
| Sales Qty (30D) | Units sold in last 30 days |
| Receipts (10D) | Units received in last 10 days |

Download the input template from the app's Upload tab.

## Priority Score formula

```
Priority = Severity_Weight × (Sales30 / 30) × Recency_Weight

Severity_Weight:  HIGH=3, MEDIUM=2, LINKED SIBLING=1
Recency_Weight:   1.0 + 0.1 × days_since_last_flagged   (capped at 3.0)
```

SKUs not seen in a long time are boosted so they don't stay invisible.

## Calendar

Valid count days exclude:
- Friday, Saturday
- 2nd and 4th Thursday of each month
- Qatar National Day (18 Dec 2026)

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploying to Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub account
4. Select this repo, branch `main`, file `app.py`
5. Click **Deploy**

No API key or secrets needed for Phase 1.

## File structure

```
cc_app/
├── app.py              # Streamlit UI
├── engine.py           # Exception rules + sibling pairing + priority scoring
├── scheduler.py        # Calendar logic + rotation history
├── excel_export.py     # Excel workbook builder
├── requirements.txt
└── .streamlit/
    └── config.toml     # Theme (Al Meera brand colours)
```

---
*Al Meera Consumer Goods Co. Q.P.S.C. — Inventory Control*
