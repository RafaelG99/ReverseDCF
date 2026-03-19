# Reverse DCF

Implied growth extraction + scenario analysis for equity screening.

## Setup

```bash
pip install -r requirements.txt
```

## Workflow

```
Bloomberg Terminal                    Streamlit App
┌──────────────────────┐             ┌────────────────────┐
│  templates/           │   upload    │  app.py            │
│  reverse_dcf.xlsx    │  ────────►  │                    │
│                      │             │  Scenario fan       │
│  1. Change ticker    │             │  ROIC gate          │
│  2. BDH loads        │             │  TV decomposition   │
│  3. Copy → HC block  │             │  Sensitivity        │
│  4. Save             │             │  All adjustable     │
└──────────────────────┘             └────────────────────┘
```

### Bloomberg

1. Open `templates/reverse_dcf.xlsx` on Bloomberg Terminal
2. Change ticker in `Fundamentals!B2`
3. Set start/end year in `T1`/`T2` (default 2019-2024)
4. Each BDH cell loads one value (no spill)
5. Copy BBG block → Paste Values into HC block
6. Same for Current (col B → col C) and Macro (col C → col D)
7. Save to `data/` folder

### Analysis

```bash
# Interactive dashboard
streamlit run app.py

# CLI
python reverse_dcf_engine.py data/reverse_dcf.xlsx

# Batch screening
python screener.py data/ --sort Base_Upside --export results.xlsx
```

## Excel Structure

**Fundamentals** (transposed: fields as columns, years as rows)
- Row 1: Field headers | Row 2: Ticker | Row 3: BBG fields
- Rows 6-11: BBG BDH (each cell = own formula, dynamic year from col A)
- Row 12: LTM (BDP) | Row 13: FY1 Estimates (BDP BEST_*)
- Rows 17-22: Hard Copy (paste values) | Row 23: HC LTM | Row 24: HC FY1
- Rows 28-33: Derived metrics (margins, ROIC, FCFF)
- Row 36+: Summary stats (CAGRs, medians)

**Current**: BDP live (col B) | Hard Copy (col C)

**Macro**: Risk-free rates (BDP + HC), GDP growth, ERP

**WACC**: Linked to HC sheets | Manual override | Active switch

## Engine

Given a stock price, solves for implied revenue growth (FCFF-based, 2-stage DCF), then:
- Scenario Fan (Bull/Base/Bear)
- ROIC Gate (value creation check)
- TV Decomposition (explicit vs terminal %)
- Plausibility Checks (implied vs historical)
- Sensitivity Table (WACC × Terminal Growth)

## Files

```
reverse-dcf/
├── reverse_dcf_engine.py   # Core DCF engine
├── app.py                  # Streamlit dashboard
├── screener.py             # Batch screening
├── requirements.txt
├── .gitignore
├── README.md
├── templates/
│   └── reverse_dcf.xlsx    # Bloomberg template (no data)
└── data/                   # .gitignored
    └── .gitkeep
```
