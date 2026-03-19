"""
Reverse DCF Engine
==================
FCFF-based 2-stage Reverse DCF with:
- Implied revenue growth extraction (Newton-Raphson solver)
- Scenario fan: Bull / Base / Bear
- ROIC gate: flags value-destructive growth
- Terminal Value decomposition
- Implied vs. Historical plausibility check

Usage:
    from reverse_dcf_engine import ReverseDCF
    model = ReverseDCF.from_excel("rdcf_data_NESN_SW.xlsx")
    results = model.run()
    model.print_report(results)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class DCFParams:
    """All parameters for the Reverse DCF model."""
    # Projection
    projection_years: int = 5
    terminal_growth: float = 0.02  # nominal GDP proxy

    # WACC
    risk_free: float = 0.025
    erp: float = 0.05
    beta: float = 1.0
    cost_of_debt_pretax: float = 0.03
    tax_rate: float = 0.20
    debt_weight: float = 0.20
    equity_weight: float = 0.80
    wacc_override: Optional[float] = None

    # Scenario offsets (applied to implied base growth)
    bull_growth_add: float = 0.03    # +3pp
    bear_growth_add: float = -0.03   # -3pp

    # Terminal margin fade: if set, TV uses this margin instead of projection margin
    # Reflects reversion to long-run sustainable margin (sector median or lower)
    terminal_ebit_margin: Optional[float] = None  # None = same as projection

    # Margins: if None, use last historical
    ebit_margin_override: Optional[float] = None
    da_pct_revenue_override: Optional[float] = None
    capex_pct_revenue_override: Optional[float] = None
    nwc_pct_revenue_override: Optional[float] = None

    @property
    def cost_of_equity(self) -> float:
        return self.risk_free + self.beta * self.erp

    @property
    def cost_of_debt_aftertax(self) -> float:
        return self.cost_of_debt_pretax * (1 - self.tax_rate)

    @property
    def wacc(self) -> float:
        if self.wacc_override is not None:
            return self.wacc_override
        return (self.equity_weight * self.cost_of_equity +
                self.debt_weight * self.cost_of_debt_aftertax)


@dataclass
class HistoricalProfile:
    """Derived historical metrics for plausibility checks."""
    revenue_cagr_3y: float = 0.0
    revenue_cagr_5y: float = 0.0
    max_revenue_growth: float = 0.0
    min_revenue_growth: float = 0.0
    median_ebit_margin: float = 0.0
    median_roic: float = 0.0
    avg_reinvestment_rate: float = 0.0
    years_available: int = 0


class ReverseDCF:
    """
    Reverse DCF: given market price, solve for implied revenue growth rate.
    Uses FCFF discounted at WACC → Enterprise Value.
    """

    def __init__(
        self,
        historical: pd.DataFrame,
        current: dict,
        params: DCFParams = None,
        ticker: str = "",
    ):
        self.hist = historical.copy()
        self.current = current
        self.params = params or DCFParams()
        self.ticker = ticker
        self._prepare_data()

    @classmethod
    def from_excel(cls, path: str, params: DCFParams = None) -> "ReverseDCF":
        """Load from Excel. Supports:
        - Single file (reverse_dcf.xlsx): HC in cols I-N, Current HC in col C
        - Standalone hard_copy: HC in cols B-G
        - Legacy bbg_data_loader: years as index
        """
        xl = pd.ExcelFile(path)
        sheets = xl.sheet_names

        if "Fundamentals" in sheets and "Current" in sheets:
            test = pd.read_excel(xl, "Fundamentals", header=2, nrows=2)
            cols = list(test.columns)

            # Single-file: has BBG + HC blocks (stacked or side-by-side)
            # Check for "HARD COPY" section header anywhere in the sheet
            full_scan = pd.read_excel(xl, "Fundamentals", header=None, nrows=50)
            has_hc_section = full_scan.iloc[:, 0].astype(str).str.contains("HARD COPY", case=False, na=False).any()
            has_item1 = "Item.1" in cols

            if has_hc_section or has_item1:
                return cls._load_single_file(xl, path, params)
            elif "Item" in cols:
                return cls._load_hard_copy(xl, path, params)
            else:
                return cls._load_legacy_template(xl, path, params)
        elif "Fundamentals" in sheets and "Current_Snapshot" in sheets:
            return cls._load_master_template(xl, path, params)
        else:
            raise ValueError(f"Unrecognized Excel format. Sheets: {sheets}")

    @classmethod
    def _load_single_file(cls, xl, path, params):
        """Load from single reverse_dcf.xlsx — transposed layout.
        Fields as columns, years as rows. HC block found by 'HARD COPY' marker.
        """
        raw = pd.read_excel(xl, "Fundamentals", header=None)

        # Find HC block
        hc_header_row = None
        for i in range(len(raw)):
            val = str(raw.iloc[i, 0]) if pd.notna(raw.iloc[i, 0]) else ""
            if "HARD COPY" in val.upper():
                hc_header_row = i + 1  # next row is column headers
                break

        if hc_header_row is None:
            raise ValueError("Cannot find 'HARD COPY' section in Fundamentals sheet")

        # Read HC block from the header row
        hc = pd.read_excel(xl, "Fundamentals", header=hc_header_row)

        # Drop unnamed columns and empty rows
        hc = hc[[c for c in hc.columns if not str(c).startswith("Unnamed")]]
        first_col = hc.columns[0]  # "Year" or "Date"
        hc = hc.dropna(subset=[first_col])

        # Keep only numeric year rows BEFORE any section marker (LTM, FY1, DERIVED)
        stop_labels = ["LTM", "FY1", "DERIVED", "SUMMARY"]
        valid_rows = []
        for idx, row in hc.iterrows():
            label = str(row[first_col]).strip().upper()
            if any(s in label for s in stop_labels):
                break
            if label.isdigit():
                valid_rows.append(idx)
        hc_years = hc.loc[valid_rows].copy()

        # Also grab LTM and FY1 rows
        ltm_mask = hc[first_col].astype(str).str.upper() == "LTM"
        est_mask = hc[first_col].astype(str).str.contains("FY1|EST", case=False, na=False)
        hc_ltm = hc[ltm_mask].copy()
        hc_est = hc[est_mask].copy()

        # Build historical DataFrame: index=years, columns=fields
        hc_years = hc_years.set_index(first_col)
        hc_years.index = pd.to_datetime([str(int(float(y))) for y in hc_years.index], format="%Y")
        hc_years.index.name = "Date"

        # Map column names to engine names
        col_map = {
            "Revenue": "Revenue", "EBIT": "EBIT", "EBITDA": "EBITDA",
            "D&A": "DA", "Tax Expense": "Tax_Expense", "Interest Exp": "Interest_Expense",
            "Net Income": "Net_Income", "Total Debt": "Total_Debt",
            "Cash & Equiv": "Cash", "Minority Int": "Minority_Interest",
            "Shares Out": "Shares_Outstanding", "Book Equity": "Book_Equity",
            "Total Assets": "Total_Assets", "CapEx": "CapEx", "CFO": "CFO",
            "Chg in NWC": "Change_NWC",
        }
        hist = hc_years.rename(columns=col_map)
        valid = [v for v in col_map.values() if v in hist.columns]
        hist = hist[valid]
        hist = hist.apply(pd.to_numeric, errors="coerce")

        # Current: HC in col C (Hard Copy), header at row 3 (header=2)
        curr_raw = pd.read_excel(xl, "Current", header=2)
        curr_raw = curr_raw.dropna(subset=["Field"])
        hc_col = "Hard Copy" if "Hard Copy" in curr_raw.columns else curr_raw.columns[2] if len(curr_raw.columns) > 2 else None
        current = dict(zip(curr_raw["Field"], curr_raw[hc_col])) if hc_col else {}

        curr_map = {
            "Price": "Price", "Market Cap": "Market_Cap",
            "Shares Out": "Shares_Out_Current", "EV": "EV",
            "Beta Raw": "Beta_Raw", "Beta Adj": "Beta_Adj",
            "BBG WACC": "BBG_WACC", "ROIC": "ROIC",
            "Div Yield": "Div_Yield", "D/E": "Debt_to_Equity",
            "Op Margin": "Operating_Margin",
            "Cons Rev FY1": "Consensus_Revenue_FY1",
            "Cons EPS FY1": "Consensus_EPS_FY1",
            "Net Debt": "Net_Debt", "Minority Int": "Minority_Interest",
            "EV calc": "EV_Calc",
        }
        current = {curr_map.get(k, k): v for k, v in current.items() if k in curr_map}

        # WACC
        if "WACC" in xl.sheet_names:
            params = cls._parse_wacc_sheet_single(xl, params)

        ticker = Path(path).stem.replace("reverse_dcf_", "").replace("reverse_dcf", "TICKER")
        return cls(hist, current, params, ticker)

        # Read HC block: header row at hc_start, data below
        hc_block = pd.read_excel(xl, "Fundamentals", header=hc_start, nrows=20)
        hc_block = hc_block.dropna(subset=["Item"])
        hc_block = hc_block.set_index("Item")
        hc_block = hc_block.dropna(how="all")

        # Filter to input rows only
        input_labels = [
            "Revenue", "EBIT", "EBITDA", "D&A", "Tax Expense", "Interest Expense",
            "Net Income", "Total Debt", "Cash & Equivalents", "Minority Interest",
            "Shares Outstanding", "Book Equity", "Total Assets", "CapEx", "CFO",
            "Change in NWC",
        ]
        hc_block = hc_block.loc[hc_block.index.isin(input_labels)]

        # Transpose: rows=years, columns=items
        hist = hc_block.T.copy()
        # Column names are years (int or float)
        hist.index = pd.to_datetime([str(int(float(y))) for y in hist.index], format="%Y")
        hist.index.name = "Date"

        col_map = {
            "Revenue": "Revenue", "EBIT": "EBIT", "EBITDA": "EBITDA",
            "D&A": "DA", "Tax Expense": "Tax_Expense", "Net Income": "Net_Income",
            "Interest Expense": "Interest_Expense", "Total Debt": "Total_Debt",
            "Cash & Equivalents": "Cash", "Minority Interest": "Minority_Interest",
            "Shares Outstanding": "Shares_Outstanding", "Book Equity": "Book_Equity",
            "Total Assets": "Total_Assets", "CapEx": "CapEx", "CFO": "CFO",
            "Change in NWC": "Change_NWC",
        }
        hist = hist.rename(columns=col_map)
        valid = [v for v in col_map.values() if v in hist.columns]
        hist = hist[valid]
        hist = hist.apply(pd.to_numeric, errors="coerce")

        # Current: HC in column C, header at row 3 (header=2)
        curr_raw = pd.read_excel(xl, "Current", header=2)
        curr_raw = curr_raw.dropna(subset=["Field"])
        hc_col = "Hard Copy" if "Hard Copy" in curr_raw.columns else curr_raw.columns[2] if len(curr_raw.columns) > 2 else None
        current = dict(zip(curr_raw["Field"], curr_raw[hc_col])) if hc_col else {}

        curr_map = {
            "Price": "Price", "Market Cap": "Market_Cap",
            "Shares Out (current)": "Shares_Out_Current",
            "Enterprise Value": "EV",
            "Beta (Raw)": "Beta_Raw", "Beta (Adjusted)": "Beta_Adj",
            "BBG WACC": "BBG_WACC", "ROIC": "ROIC",
            "Dividend Yield": "Div_Yield", "D/E Ratio": "Debt_to_Equity",
            "Operating Margin": "Operating_Margin",
            "Consensus Revenue FY1": "Consensus_Revenue_FY1",
            "Consensus EPS FY1": "Consensus_EPS_FY1",
            "Net Debt": "Net_Debt", "Minority Interest": "Minority_Interest",
        }
        current = {curr_map.get(k, k): v for k, v in current.items() if k in curr_map}

        # WACC
        if "WACC" in xl.sheet_names:
            params = cls._parse_wacc_sheet_single(xl, params)

        ticker = Path(path).stem.replace("reverse_dcf_", "").replace("reverse_dcf", "TICKER")
        return cls(hist, current, params, ticker)

    @classmethod
    def _load_single_file_v3(cls, xl, path, params):
        """Fallback: load side-by-side format (v3 with Item.1 and HC columns)."""
        raw = pd.read_excel(xl, "Fundamentals", header=2)
        all_cols = list(raw.columns)

        # Find HC columns: "Item.1" and year columns ending with "(HC)"
        hc_item_col = "Item.1"
        hc_year_cols = [c for c in all_cols if str(c).endswith("(HC)") and c != hc_item_col]

        if hc_item_col not in all_cols:
            raise ValueError("Cannot find HC block (Item.1 column) in Fundamentals sheet")

        hc_block = raw[[hc_item_col] + hc_year_cols].copy()
        hc_block = hc_block.rename(columns={hc_item_col: "Item"})
        # Clean year column names: "2019 (HC)" → "2019"
        hc_block.columns = [str(c).replace(" (HC)", "") if "(HC)" in str(c) else c for c in hc_block.columns]
        hc_block = hc_block.set_index("Item")
        hc_block = hc_block.dropna(how="all")
        hc_block = hc_block.loc[hc_block.index.notna()]

        # Filter to input rows only
        input_labels = [
            "Revenue", "EBIT", "EBITDA", "D&A", "Tax Expense", "Interest Expense",
            "Net Income", "Total Debt", "Cash & Equivalents", "Minority Interest",
            "Shares Outstanding", "Book Equity", "Total Assets", "CapEx", "CFO",
            "Change in NWC",
        ]
        hc_block = hc_block.loc[hc_block.index.isin(input_labels)]

        # Transpose
        hist = hc_block.T.copy()
        hist.index = pd.to_datetime([str(int(float(y))) for y in hist.index], format="%Y")
        hist.index.name = "Date"

        col_map = {
            "Revenue": "Revenue", "EBIT": "EBIT", "EBITDA": "EBITDA",
            "D&A": "DA", "Tax Expense": "Tax_Expense", "Net Income": "Net_Income",
            "Interest Expense": "Interest_Expense", "Total Debt": "Total_Debt",
            "Cash & Equivalents": "Cash", "Minority Interest": "Minority_Interest",
            "Shares Outstanding": "Shares_Outstanding", "Book Equity": "Book_Equity",
            "Total Assets": "Total_Assets", "CapEx": "CapEx", "CFO": "CFO",
            "Change in NWC": "Change_NWC",
        }
        hist = hist.rename(columns=col_map)
        valid = [v for v in col_map.values() if v in hist.columns]
        hist = hist[valid]
        hist = hist.apply(pd.to_numeric, errors="coerce")

        # Current: HC values in column C (index 2), header row 3 (header=2)
        curr_raw = pd.read_excel(xl, "Current", header=2)
        curr_raw = curr_raw.dropna(subset=["Field"])
        # "Hard Copy" column has the pasted values
        hc_col = "Hard Copy" if "Hard Copy" in curr_raw.columns else curr_raw.columns[2] if len(curr_raw.columns) > 2 else None
        if hc_col:
            current = dict(zip(curr_raw["Field"], curr_raw[hc_col]))
        else:
            current = dict(zip(curr_raw["Field"], curr_raw.iloc[:, 1]))

        curr_map = {
            "Price": "Price", "Market Cap": "Market_Cap",
            "Shares Out (current)": "Shares_Out_Current",
            "Enterprise Value": "EV",
            "Beta (Raw)": "Beta_Raw", "Beta (Adjusted)": "Beta_Adj",
            "BBG WACC": "BBG_WACC", "ROIC": "ROIC",
            "Dividend Yield": "Div_Yield", "D/E Ratio": "Debt_to_Equity",
            "Operating Margin": "Operating_Margin",
            "Consensus Revenue FY1": "Consensus_Revenue_FY1",
            "Consensus EPS FY1": "Consensus_EPS_FY1",
            "Net Debt": "Net_Debt",
            "Minority Interest": "Minority_Interest",
        }
        current = {curr_map.get(k, k): v for k, v in current.items() if k in curr_map}

        # WACC from WACC sheet
        if "WACC" in xl.sheet_names:
            params = cls._parse_wacc_sheet_single(xl, params)

        ticker = Path(path).stem.replace("reverse_dcf_", "").replace("reverse_dcf", "TICKER")
        return cls(hist, current, params, ticker)

    @classmethod
    def _parse_wacc_sheet_single(cls, xl, params):
        """Parse WACC sheet — auto-detects column names."""
        # Try header=2 first (current layout), fallback to header=3
        for h in [2, 3]:
            wacc_df = pd.read_excel(xl, "WACC", header=h)
            param_col = next((c for c in wacc_df.columns if str(c).lower().startswith("param")), None)
            if param_col:
                break
        if param_col is None:
            return params or DCFParams()

        wacc_df = wacc_df.dropna(subset=[param_col])
        if params is None:
            params = DCFParams()

        param_map = {
            "Rf": "risk_free", "Risk-Free Rate": "risk_free",
            "ERP": "erp", "Equity Risk Premium": "erp",
            "Beta": "beta",
            "Pre-Tax CoD": "cost_of_debt_pretax", "Pre-Tax Cost of Debt": "cost_of_debt_pretax",
            "Tax Rate": "tax_rate",
            "Eq Weight": "equity_weight", "Equity Weight": "equity_weight",
            "Tg": "terminal_growth", "Terminal Growth": "terminal_growth",
            "T Margin": "terminal_ebit_margin", "Terminal Margin": "terminal_ebit_margin",
            "Terminal EBIT Margin": "terminal_ebit_margin",
            "Proj Yrs": "projection_years", "Projection Years": "projection_years",
            "Bull": "bull_growth_add", "Bull Offset": "bull_growth_add",
            "Bear": "bear_growth_add", "Bear Offset": "bear_growth_add",
        }

        # Auto-detect value columns
        cols_lower = {c: c.lower() for c in wacc_df.columns}
        used_col = next((c for c, cl in cols_lower.items() if cl in ["used", "used value"]), None)
        manual_col = next((c for c, cl in cols_lower.items() if cl in ["manual", "manual override"]), None)
        linked_col = next((c for c, cl in cols_lower.items() if cl in ["linked", "linked value"]), None)
        active_col = next((c for c, cl in cols_lower.items() if cl == "active"), None)

        def _to_float(val):
            if pd.isna(val): return None
            try: return float(val)
            except (ValueError, TypeError): return None

        for _, row in wacc_df.iterrows():
            name = str(row.get(param_col, "")).strip()
            attr = param_map.get(name)
            if not attr:
                continue

            active = str(row.get(active_col, "")).strip() if active_col else ""
            used = _to_float(row.get(used_col)) if used_col else None
            linked = _to_float(row.get(linked_col)) if linked_col else None
            manual = _to_float(row.get(manual_col)) if manual_col else None

            # Smart resolution:
            # 1. If Active=Manual → use Manual Override
            # 2. If Active=Linked → use Linked if it looks real, else fallback to Manual
            # 3. If Active=Calc → use Used Value (formula result)
            # 4. Fallback chain: Used Value → Manual → Linked
            val = None
            if active == "Manual":
                val = manual
            elif active == "Linked":
                # Linked values of 0.0 when Manual has a real value likely means
                # the HC source is empty → prefer Manual
                if linked is not None and linked != 0.0:
                    val = linked
                elif manual is not None:
                    val = manual
                else:
                    val = linked
            elif active == "Calc":
                if used is not None and used != 0.0:
                    val = used
                # Calc fields (CoE, CoD_at, WACC) are computed by engine, skip
                else:
                    continue
            else:
                # Unknown active → fallback chain
                val = used if used is not None else manual if manual is not None else linked

            if val is not None:
                if attr == "projection_years":
                    setattr(params, attr, int(val))
                else:
                    setattr(params, attr, val)

        params.debt_weight = 1 - params.equity_weight
        return params

    @classmethod
    def _load_hard_copy(cls, xl, path, params):
        """Load from standalone rdcf_hard_copy.xlsx (Items in col A, years in B-G)."""
        hist_raw = pd.read_excel(xl, "Fundamentals", header=2)
        hist_raw = hist_raw.set_index("Item")
        hist_raw = hist_raw.dropna(how="all")
        hist_raw = hist_raw.loc[hist_raw.index.notna()]

        # Filter to input rows only (exclude derived metrics)
        input_labels = [
            "Revenue", "EBIT", "EBITDA", "D&A", "Tax Expense", "Interest Expense",
            "Net Income", "Total Debt", "Cash & Equivalents", "Minority Interest",
            "Shares Outstanding", "Book Equity", "Total Assets", "CapEx", "CFO",
            "Change in NWC",
        ]
        hist_raw = hist_raw.loc[hist_raw.index.isin(input_labels)]

        # Transpose: rows=years, columns=items
        hist = hist_raw.T.copy()
        # Column names might be int/float (2019.0) — convert safely
        hist.index = pd.to_datetime([str(int(float(y))) for y in hist.index], format="%Y")
        hist.index.name = "Date"

        col_map = {
            "Revenue": "Revenue", "EBIT": "EBIT", "EBITDA": "EBITDA",
            "D&A": "DA", "Tax Expense": "Tax_Expense", "Net Income": "Net_Income",
            "Interest Expense": "Interest_Expense", "Total Debt": "Total_Debt",
            "Cash & Equivalents": "Cash", "Minority Interest": "Minority_Interest",
            "Shares Outstanding": "Shares_Outstanding", "Book Equity": "Book_Equity",
            "Total Assets": "Total_Assets", "CapEx": "CapEx", "CFO": "CFO",
            "Change in NWC": "Change_NWC",
        }
        hist = hist.rename(columns=col_map)
        valid_cols = [v for v in col_map.values() if v in hist.columns]
        hist = hist[valid_cols]
        hist = hist.apply(pd.to_numeric, errors="coerce")

        # Current snapshot
        curr_raw = pd.read_excel(xl, "Current", header=2)
        curr_raw = curr_raw.dropna(subset=["Field"])
        current = dict(zip(curr_raw["Field"], curr_raw["Value"]))
        curr_map = {
            "Price": "Price", "Market Cap": "Market_Cap",
            "Shares Out (current)": "Shares_Out_Current",
            "Enterprise Value": "EV",
            "Beta (Raw)": "Beta_Raw", "Beta (Adjusted)": "Beta_Adj",
            "BBG WACC": "BBG_WACC", "ROIC": "ROIC",
            "Dividend Yield": "Div_Yield", "D/E Ratio": "Debt_to_Equity",
            "Operating Margin": "Operating_Margin",
            "Consensus Revenue FY1": "Consensus_Revenue_FY1",
            "Consensus EPS FY1": "Consensus_EPS_FY1",
        }
        current = {curr_map.get(k, k): v for k, v in current.items() if k in curr_map}

        # Also pull derived values from Current sheet (Net Debt, Minority)
        for _, row in curr_raw.iterrows():
            field = row.get("Field", "")
            val = row.get("Value")
            if "Net Debt" in str(field) and pd.notna(val):
                current["Net_Debt"] = val
            if "Minority" in str(field) and "derived" not in str(field).lower() and pd.notna(val):
                current["Minority_Interest"] = val

        # WACC from analysis file if available in same directory
        # (Python engine reads hard_copy; WACC comes from params or analysis file)
        if params is None:
            params = DCFParams()

        ticker = Path(path).stem.replace("rdcf_hard_copy_", "").replace("rdcf_hard_copy", "TICKER")
        return cls(hist, current, params, ticker)

    @classmethod
    def _load_legacy_template(cls, xl, path, params):
        """Load from old bbg_data_loader format."""
        hist = pd.read_excel(xl, "Fundamentals", index_col=0)
        hist.index = pd.to_datetime(hist.index, format="%Y")
        hist = hist.sort_index()

        curr_df = pd.read_excel(xl, "Current", index_col=0)
        current = curr_df["Value"].to_dict()

        if "WACC_Inputs" in xl.sheet_names:
            params = cls._parse_wacc_sheet(xl, "WACC_Inputs", params, legacy=True)

        ticker = Path(path).stem.replace("rdcf_data_", "")
        return cls(hist, current, params, ticker)

    @classmethod
    def _load_master_template(cls, xl, path, params):
        """Load from new master template format."""
        # Fundamentals: skip title rows, header at row 4 (0-indexed: header=3)
        hist_raw = pd.read_excel(xl, "Fundamentals", header=3)
        # First column is "Item", rest are years
        hist_raw = hist_raw.set_index("Item")
        hist_raw = hist_raw.dropna(how="all")  # drop spacer rows
        hist_raw = hist_raw.loc[hist_raw.index.notna()]

        # Transpose: rows=years, columns=items
        hist = hist_raw.T.copy()
        hist.index = pd.to_datetime(hist.index.astype(str), format="%Y")
        hist.index.name = "Date"

        # Map master template names to engine names
        col_map = {
            "Revenue": "Revenue", "EBIT": "EBIT", "EBITDA": "EBITDA",
            "D&A": "DA", "Tax Expense": "Tax_Expense", "Net Income": "Net_Income",
            "Interest Expense": "Interest_Expense", "Total Debt": "Total_Debt",
            "Cash & Equivalents": "Cash", "Minority Interest": "Minority_Interest",
            "Shares Outstanding (mm)": "Shares_Outstanding", "Book Equity": "Book_Equity",
            "Total Assets": "Total_Assets", "CapEx": "CapEx", "CFO": "CFO",
            "Change in NWC": "Change_NWC",
        }
        hist = hist.rename(columns=col_map)
        # Keep only mapped columns that exist
        valid_cols = [v for v in col_map.values() if v in hist.columns]
        hist = hist[valid_cols]
        hist = hist.apply(pd.to_numeric, errors="coerce")

        # Current Snapshot
        curr_raw = pd.read_excel(xl, "Current_Snapshot", header=3)
        curr_raw = curr_raw.dropna(subset=["Field"])
        current = dict(zip(curr_raw["Field"], curr_raw["Value"]))
        # Map field names
        curr_map = {
            "Price": "Price", "Market Cap (mm)": "Market_Cap",
            "Shares Outstanding (mm)": "Shares_Out_Current",
            "Enterprise Value (mm)": "EV",
            "Total Debt (mm)": "Total_Debt", "Cash & Equiv (mm)": "Cash",
            "Minority Interest (mm)": "Minority_Interest",
            "Net Debt (mm)": "Net_Debt",
            "Beta (Raw)": "Beta_Raw", "Beta (Adjusted)": "Beta_Adj",
            "BBG WACC (%)": "BBG_WACC", "ROIC (%)": "ROIC",
            "Div Yield (%)": "Div_Yield", "D/E Ratio": "Debt_to_Equity",
            "Operating Margin (%)": "Operating_Margin",
            "Consensus Revenue FY1 (mm)": "Consensus_Revenue_FY1",
            "Consensus EPS FY1": "Consensus_EPS_FY1",
        }
        current = {curr_map.get(k, k): v for k, v in current.items() if k in curr_map}

        # WACC
        if "WACC_Calculator" in xl.sheet_names:
            params = cls._parse_wacc_calculator(xl, params)
        elif "WACC_Inputs" in xl.sheet_names:
            params = cls._parse_wacc_sheet(xl, "WACC_Inputs", params, legacy=True)

        ticker = Path(path).stem.replace("rdcf_data_", "").replace("rdcf_master_template", "TEMPLATE")
        return cls(hist, current, params, ticker)

    @classmethod
    def _parse_wacc_sheet(cls, xl, sheet_name, params, legacy=False):
        """Parse WACC_Inputs sheet (legacy format)."""
        wacc_df = pd.read_excel(xl, sheet_name)
        wacc_dict = dict(zip(wacc_df["Parameter"], wacc_df["Manual_Override"]))
        active_dict = dict(zip(wacc_df["Parameter"], wacc_df["Active"]))
        bbg_dict = dict(zip(wacc_df["Parameter"], wacc_df["BBG_Default"]))

        if params is None:
            params = DCFParams()

        def _get(key, fallback):
            if active_dict.get(key) == "Manual" and pd.notna(wacc_dict.get(key)):
                return float(wacc_dict[key])
            elif pd.notna(bbg_dict.get(key)):
                return float(bbg_dict[key])
            return fallback

        params.risk_free = _get("Risk_Free_Rate", params.risk_free)
        params.erp = _get("Equity_Risk_Premium", params.erp)
        params.beta = _get("Beta", params.beta)
        params.cost_of_debt_pretax = _get("Pre_Tax_Cost_of_Debt", params.cost_of_debt_pretax)
        params.tax_rate = _get("Tax_Rate", params.tax_rate)
        params.debt_weight = _get("Debt_Weight", params.debt_weight)
        params.equity_weight = _get("Equity_Weight", params.equity_weight)
        params.terminal_growth = _get("Terminal_Growth_Rate", params.terminal_growth)

        wacc_val = _get("WACC", None)
        if wacc_val is not None:
            params.wacc_override = wacc_val

        return params

    @classmethod
    def _parse_wacc_calculator(cls, xl, params):
        """Parse WACC_Calculator sheet (master template format)."""
        wacc_df = pd.read_excel(xl, "WACC_Calculator", header=3)
        wacc_df = wacc_df.dropna(subset=["Parameter"])

        if params is None:
            params = DCFParams()

        param_map = {
            "Risk-Free Rate": "risk_free",
            "Equity Risk Premium": "erp",
            "Beta": "beta",
            "Pre-Tax Cost of Debt": "cost_of_debt_pretax",
            "Tax Rate (effective)": "tax_rate",
            "Equity Weight (E/(D+E))": "equity_weight",
            "Debt Weight (D/(D+E))": "debt_weight",
            "Terminal Growth Rate": "terminal_growth",
            "Terminal EBIT Margin": "terminal_ebit_margin",
        }

        for _, row in wacc_df.iterrows():
            name = row.get("Parameter", "")
            attr = param_map.get(name)
            if not attr:
                continue

            active = row.get("Active", "")
            manual = row.get("Manual Override")
            bbg = row.get("BBG Default")
            used = row.get("Used Value")

            # Priority: Used Value (already calculated) > Manual > BBG > default
            val = None
            if pd.notna(used) and isinstance(used, (int, float)):
                val = float(used)
            elif active == "Manual" and pd.notna(manual):
                val = float(manual)
            elif pd.notna(bbg):
                val = float(bbg)

            if val is not None:
                setattr(params, attr, val)

        return params

    def _prepare_data(self):
        """Derive key ratios from historical data with NaN handling."""
        h = self.hist
        self._validation_warnings = []

        def _safe_last(series, name, fallback=None, required=False):
            """Get last non-NaN value from a series with validation."""
            clean = series.dropna() if isinstance(series, pd.Series) else pd.Series(dtype=float)
            if len(clean) == 0:
                if required:
                    self._validation_warnings.append(f"CRITICAL: {name} has no data — model unreliable")
                else:
                    self._validation_warnings.append(f"WARNING: {name} missing — using fallback {fallback}")
                return fallback
            nan_count = series.isna().sum()
            if nan_count > 0:
                self._validation_warnings.append(
                    f"INFO: {name} has {nan_count}/{len(series)} NaN values — using last available"
                )
            return clean.iloc[-1]

        # Latest year as base
        self.base_revenue = _safe_last(
            h.get("Revenue", pd.Series(dtype=float)), "Revenue", required=True) or 1
        self.base_ebit = _safe_last(
            h.get("EBIT", pd.Series(dtype=float)), "EBIT", required=True) or 0

        # EBIT fallback: EBITDA - D&A
        if self.base_ebit == 0 and "EBITDA" in h and "DA" in h:
            ebitda = _safe_last(h["EBITDA"], "EBITDA", 0)
            da = _safe_last(h["DA"], "D&A", 0)
            if ebitda and da:
                self.base_ebit = ebitda - da
                self._validation_warnings.append("INFO: EBIT derived from EBITDA - D&A")

        self.base_da = _safe_last(
            h.get("DA", pd.Series(dtype=float)), "D&A",
            fallback=self.base_revenue * 0.03)

        # Ratios from last available year
        self.ebit_margin = (self.params.ebit_margin_override
                           or (self.base_ebit / self.base_revenue if self.base_revenue else 0.15))

        self.da_pct = (self.params.da_pct_revenue_override
                      or (self.base_da / self.base_revenue if self.base_revenue else 0.03))

        capex = _safe_last(
            h.get("CapEx", pd.Series(dtype=float)), "CapEx",
            fallback=self.base_revenue * -0.05)
        self.capex_pct = (self.params.capex_pct_revenue_override
                         or (abs(capex) / self.base_revenue if self.base_revenue else 0.05))

        nwc = _safe_last(
            h.get("Change_NWC", pd.Series(dtype=float)), "Change in NWC", fallback=0)
        self.nwc_pct = (self.params.nwc_pct_revenue_override
                       or (nwc / self.base_revenue if self.base_revenue else 0.01))

        # Tax rate from data if not overridden
        if "Tax_Expense" in h and "EBIT" in h:
            tax_exp = h["Tax_Expense"].dropna()
            ebit_vals = h["EBIT"].dropna()
            if len(tax_exp) > 0 and len(ebit_vals) > 0:
                eff_tax = (tax_exp.iloc[-1] / ebit_vals.iloc[-1]) if ebit_vals.iloc[-1] != 0 else 0.20
                if 0 < eff_tax < 0.50:
                    self.params.tax_rate = eff_tax
                elif eff_tax >= 0.50 or eff_tax <= 0:
                    self._validation_warnings.append(
                        f"WARNING: Effective tax rate ({eff_tax:.1%}) out of bounds — using default {self.params.tax_rate:.1%}")

        # Enterprise value from market data
        price = self._safe_numeric(self.current.get("Price"), 0)
        shares = (self._safe_numeric(self.current.get("Shares_Out_Current"))
                 or self._safe_numeric(self.current.get("Shares_Outstanding"), 0))
        market_cap = self._safe_numeric(self.current.get("Market_Cap")) or (price * shares if shares else 0)

        # ── AUTO UNIT NORMALIZATION ──────────────────────────────────────────
        # Bloomberg CUR_MKT_CAP is in full currency units (e.g. 200'000'000'000 CHF)
        # But Revenue/EBIT etc from BDH are in millions
        # Detect mismatch: if Market Cap is >1000x Revenue, it's in full units
        if self.base_revenue and self.base_revenue > 0 and market_cap > 0:
            ratio = market_cap / self.base_revenue
            if ratio > 5000:  # Market Cap likely in full units, Revenue in millions
                market_cap = market_cap / 1e6
                self._validation_warnings.append(
                    f"INFO: Market Cap normalized from {market_cap*1e6:,.0f} to {market_cap:,.0f} (÷1M to match Revenue units)")

        # Same check for EV from Current sheet
        ev_calc = self._safe_numeric(self.current.get("EV_Calc"))
        if ev_calc and self.base_revenue and ev_calc / self.base_revenue > 5000:
            ev_calc = ev_calc / 1e6

        # ROIC: Bloomberg gives as percentage (10.2 = 10.2%), engine needs decimal (0.102)
        roic_val = self._safe_numeric(self.current.get("ROIC"))
        if roic_val and roic_val > 1:  # Clearly a percentage, not decimal
            self.current["ROIC"] = roic_val / 100

        total_debt = self._safe_numeric(self.current.get("Total_Debt"), 0)
        cash = self._safe_numeric(self.current.get("Cash"), 0)

        # Prefer Net Debt from Fundamentals HC (already in correct units)
        # Current sheet's Net Debt is a formula linking to Fundamentals, so should be fine
        net_debt_direct = self._safe_numeric(self.current.get("Net_Debt"))
        if net_debt_direct is not None:
            net_debt = net_debt_direct
        elif total_debt > 0:
            net_debt = total_debt - cash
        else:
            # Fallback: compute from last year of historical data
            h = self.hist
            td = h.get("Total_Debt", pd.Series(dtype=float)).dropna()
            ca = h.get("Cash", pd.Series(dtype=float)).dropna()
            net_debt = (td.iloc[-1] if len(td) else 0) - (ca.iloc[-1] if len(ca) else 0)

        minority = self._safe_numeric(self.current.get("Minority_Interest"), 0)

        # If Market Cap was normalized but shares weren't, recompute shares
        if shares > 0 and price > 0:
            implied_mcap = price * shares
            if abs(implied_mcap - market_cap) / max(market_cap, 1) > 0.5:
                # Shares * Price doesn't match Market Cap — Market Cap was normalized
                # This is expected, just note it
                pass

        self.market_ev = market_cap + net_debt + minority
        self.market_cap = market_cap
        self.net_debt = net_debt
        self.minority = minority
        self.shares = shares
        self.price = price

        if self.market_ev <= 0:
            self._validation_warnings.append("CRITICAL: Enterprise Value <= 0 — check Market Cap and Net Debt inputs")

    @staticmethod
    def _safe_numeric(val, default=None):
        """Convert to float, handling None, NaN, strings."""
        if val is None:
            return default
        try:
            f = float(val)
            return f if not np.isnan(f) else default
        except (ValueError, TypeError):
            return default

    def _compute_historical_profile(self) -> HistoricalProfile:
        """Compute historical benchmarks for plausibility."""
        h = self.hist
        rev = h["Revenue"].dropna()
        prof = HistoricalProfile()
        prof.years_available = len(rev)

        if len(rev) >= 2:
            yoy = rev.pct_change().dropna()
            prof.max_revenue_growth = yoy.max()
            prof.min_revenue_growth = yoy.min()

        if len(rev) >= 4:
            prof.revenue_cagr_3y = (rev.iloc[-1] / rev.iloc[-4]) ** (1/3) - 1
        if len(rev) >= 6:
            prof.revenue_cagr_5y = (rev.iloc[-1] / rev.iloc[-6]) ** (1/5) - 1

        if "EBIT" in h:
            margins = (h["EBIT"] / h["Revenue"]).dropna()
            if len(margins) > 0:
                prof.median_ebit_margin = margins.median()

        roic_bbg = self.current.get("ROIC")
        if roic_bbg and pd.notna(roic_bbg):
            prof.median_roic = roic_bbg / 100 if roic_bbg > 1 else roic_bbg

        return prof

    def _fcff_from_revenue(self, revenue: float, margin_override: float = None) -> float:
        """Compute FCFF from a given revenue level."""
        margin = margin_override if margin_override is not None else self.ebit_margin
        ebit = revenue * margin
        nopat = ebit * (1 - self.params.tax_rate)
        da = revenue * self.da_pct
        capex = revenue * self.capex_pct
        nwc_change = revenue * abs(self.nwc_pct)
        fcff = nopat + da - capex - nwc_change
        return fcff

    def _ev_from_growth(self, g: float) -> float:
        """Compute enterprise value for a given constant revenue growth rate."""
        wacc = self.params.wacc
        tg = self.params.terminal_growth
        n = self.params.projection_years

        if wacc <= tg:
            return np.inf

        pv_explicit = 0.0
        rev = self.base_revenue
        for t in range(1, n + 1):
            rev *= (1 + g)
            fcff = self._fcff_from_revenue(rev)
            pv_explicit += fcff / (1 + wacc) ** t

        # Terminal value uses faded margin if specified
        terminal_margin = self.params.terminal_ebit_margin
        terminal_fcff = self._fcff_from_revenue(rev * (1 + tg), terminal_margin)
        tv = terminal_fcff / (wacc - tg)
        pv_tv = tv / (1 + wacc) ** n

        return pv_explicit + pv_tv

    def solve_implied_growth(self, tol: float = 1e-6, max_iter: int = 200) -> float:
        """Solve for implied revenue growth rate using bisection."""
        target_ev = self.market_ev

        if target_ev <= 0:
            return np.nan

        lo, hi = -0.30, 0.80
        for _ in range(max_iter):
            mid = (lo + hi) / 2
            ev_mid = self._ev_from_growth(mid)
            if abs(ev_mid - target_ev) / max(target_ev, 1) < tol:
                return mid
            if ev_mid < target_ev:
                lo = mid
            else:
                hi = mid

        return (lo + hi) / 2

    def scenario_analysis(self, implied_g: float) -> dict:
        """Compute Bull / Base / Bear scenario fan."""
        scenarios = {}
        for label, offset in [("Bear", self.params.bear_growth_add),
                               ("Base", 0.0),
                               ("Bull", self.params.bull_growth_add)]:
            g = implied_g + offset
            ev = self._ev_from_growth(g)
            equity_val = ev - self.net_debt - self.minority
            fair_price = equity_val / self.shares if self.shares else 0
            upside = (fair_price / self.price - 1) if self.price else 0

            scenarios[label] = {
                "growth_rate": g,
                "enterprise_value": ev,
                "equity_value": equity_val,
                "fair_price": fair_price,
                "upside_downside": upside,
            }
        return scenarios

    def tv_decomposition(self, implied_g: float) -> dict:
        """Decompose EV into explicit-period value vs terminal value."""
        wacc = self.params.wacc
        tg = self.params.terminal_growth
        n = self.params.projection_years

        pv_explicit = 0.0
        rev = self.base_revenue
        for t in range(1, n + 1):
            rev *= (1 + implied_g)
            fcff = self._fcff_from_revenue(rev)
            pv_explicit += fcff / (1 + wacc) ** t

        terminal_margin = self.params.terminal_ebit_margin
        terminal_fcff = self._fcff_from_revenue(rev * (1 + tg), terminal_margin)
        tv = terminal_fcff / (wacc - tg)
        pv_tv = tv / (1 + wacc) ** n
        total = pv_explicit + pv_tv

        return {
            "pv_explicit": pv_explicit,
            "pv_terminal": pv_tv,
            "total_ev": total,
            "tv_pct": pv_tv / total if total else 0,
            "explicit_pct": pv_explicit / total if total else 0,
            "terminal_margin_used": terminal_margin or self.ebit_margin,
        }

    def roic_gate(self, implied_g: float, hist_profile: HistoricalProfile) -> dict:
        """Check if implied growth creates or destroys value."""
        roic = hist_profile.median_roic
        wacc = self.params.wacc

        # Implied reinvestment rate = g / ROIC
        reinvest_rate = implied_g / roic if roic > 0 else np.nan
        value_creating = roic > wacc

        return {
            "roic": roic,
            "wacc": wacc,
            "spread": roic - wacc,
            "implied_reinvestment_rate": reinvest_rate,
            "value_creating": value_creating,
            "verdict": (
                f"ROIC ({roic:.1%}) {'>' if value_creating else '<'} WACC ({wacc:.1%}) → "
                f"Growth {'CREATES' if value_creating else 'DESTROYS'} value"
            ),
        }

    def plausibility_check(self, implied_g: float, hist_profile: HistoricalProfile) -> list:
        """Compare implied growth against historical track record."""
        checks = []

        # vs. 5Y CAGR
        if hist_profile.revenue_cagr_5y != 0:
            ratio = implied_g / hist_profile.revenue_cagr_5y if hist_profile.revenue_cagr_5y else np.inf
            flag = "🟢" if 0.5 < ratio < 1.5 else "🟡" if 0.3 < ratio < 2.0 else "🔴"
            checks.append({
                "check": "Implied vs 5Y CAGR",
                "implied": f"{implied_g:.1%}",
                "historical": f"{hist_profile.revenue_cagr_5y:.1%}",
                "ratio": f"{ratio:.1f}x",
                "flag": flag,
            })

        # vs. 3Y CAGR
        if hist_profile.revenue_cagr_3y != 0:
            ratio = implied_g / hist_profile.revenue_cagr_3y if hist_profile.revenue_cagr_3y else np.inf
            flag = "🟢" if 0.5 < ratio < 1.5 else "🟡" if 0.3 < ratio < 2.0 else "🔴"
            checks.append({
                "check": "Implied vs 3Y CAGR",
                "implied": f"{implied_g:.1%}",
                "historical": f"{hist_profile.revenue_cagr_3y:.1%}",
                "ratio": f"{ratio:.1f}x",
                "flag": flag,
            })

        # vs. historical max
        if hist_profile.max_revenue_growth != 0:
            exceeded = implied_g > hist_profile.max_revenue_growth
            flag = "🔴" if exceeded else "🟢"
            checks.append({
                "check": "Implied vs Historical Max Growth",
                "implied": f"{implied_g:.1%}",
                "historical": f"{hist_profile.max_revenue_growth:.1%}",
                "ratio": "EXCEEDS" if exceeded else "Within range",
                "flag": flag,
            })

        # EBIT margin check
        if hist_profile.median_ebit_margin != 0:
            margin_diff = self.ebit_margin - hist_profile.median_ebit_margin
            flag = "🟢" if abs(margin_diff) < 0.03 else "🟡" if abs(margin_diff) < 0.06 else "🔴"
            checks.append({
                "check": "Assumed vs Median EBIT Margin",
                "implied": f"{self.ebit_margin:.1%}",
                "historical": f"{hist_profile.median_ebit_margin:.1%}",
                "ratio": f"{margin_diff:+.1%}pp delta",
                "flag": flag,
            })

        return checks

    def run(self) -> dict:
        """Full Reverse DCF analysis."""
        hist_profile = self._compute_historical_profile()
        implied_g = self.solve_implied_growth()
        scenarios = self.scenario_analysis(implied_g)
        tv_decomp = self.tv_decomposition(implied_g)
        roic = self.roic_gate(implied_g, hist_profile)
        plausibility = self.plausibility_check(implied_g, hist_profile)

        return {
            "ticker": self.ticker,
            "price": self.price,
            "market_ev": self.market_ev,
            "market_cap": self.market_cap,
            "implied_growth": implied_g,
            "wacc": self.params.wacc,
            "terminal_growth": self.params.terminal_growth,
            "terminal_ebit_margin": self.params.terminal_ebit_margin,
            "ebit_margin_used": self.ebit_margin,
            "scenarios": scenarios,
            "tv_decomposition": tv_decomp,
            "roic_gate": roic,
            "plausibility": plausibility,
            "historical_profile": hist_profile,
            "validation_warnings": getattr(self, "_validation_warnings", []),
        }

    def print_report(self, results: dict = None):
        """Pretty-print the full analysis."""
        if results is None:
            results = self.run()

        r = results
        print("=" * 70)
        print(f"  REVERSE DCF REPORT: {r['ticker']}")
        print("=" * 70)

        print(f"\n  Price:          {r['price']:>12,.2f}")
        print(f"  Market Cap:     {r['market_cap']:>12,.0f}")
        print(f"  Enterprise Val: {r['market_ev']:>12,.0f}")
        print(f"  WACC:           {r['wacc']:>12.2%}")
        print(f"  Terminal g:     {r['terminal_growth']:>12.2%}")
        print(f"  EBIT Margin:    {r['ebit_margin_used']:>12.2%}")

        print(f"\n{'─' * 70}")
        print(f"  IMPLIED REVENUE GROWTH: {r['implied_growth']:.2%}")
        print(f"{'─' * 70}")
        print(f"  → Market prices in {r['implied_growth']:.1%} annual revenue growth")
        print(f"    over the next {self.params.projection_years} years.")

        # ROIC Gate
        rg = r["roic_gate"]
        print(f"\n  ROIC GATE:")
        print(f"  {rg['verdict']}")
        print(f"  Implied reinvestment rate: {rg['implied_reinvestment_rate']:.1%}")

        # TV Decomposition
        tv = r["tv_decomposition"]
        print(f"\n  TERMINAL VALUE DECOMPOSITION:")
        bar_len = 40
        expl_bar = "█" * int(tv["explicit_pct"] * bar_len)
        tv_bar = "░" * int(tv["tv_pct"] * bar_len)
        print(f"  [{expl_bar}{tv_bar}]")
        print(f"  Explicit period: {tv['explicit_pct']:.0%}  |  Terminal value: {tv['tv_pct']:.0%}")

        # Scenario Fan
        print(f"\n  SCENARIO FAN:")
        print(f"  {'Scenario':<8} {'Growth':>8} {'Fair Price':>12} {'Upside':>10}")
        print(f"  {'─' * 42}")
        for label in ["Bull", "Base", "Bear"]:
            s = r["scenarios"][label]
            print(f"  {label:<8} {s['growth_rate']:>8.1%} {s['fair_price']:>12,.2f} {s['upside_downside']:>+10.1%}")

        # Plausibility
        print(f"\n  PLAUSIBILITY CHECKS:")
        for c in r["plausibility"]:
            print(f"  {c['flag']} {c['check']}: implied {c['implied']} vs hist {c['historical']} ({c['ratio']})")

        # Validation warnings
        warnings = r.get("validation_warnings", [])
        if warnings:
            print(f"\n  DATA VALIDATION:")
            for w in warnings:
                print(f"  ⚠ {w}")

        print(f"\n{'=' * 70}\n")

    def to_dataframe(self, results: dict = None) -> pd.DataFrame:
        """Export results as a tidy DataFrame for further processing."""
        if results is None:
            results = self.run()

        rows = [
            ("Implied_Revenue_Growth", results["implied_growth"]),
            ("WACC", results["wacc"]),
            ("Terminal_Growth", results["terminal_growth"]),
            ("EBIT_Margin", results["ebit_margin_used"]),
            ("Market_EV", results["market_ev"]),
            ("TV_Pct_of_EV", results["tv_decomposition"]["tv_pct"]),
            ("ROIC", results["roic_gate"]["roic"]),
            ("ROIC_WACC_Spread", results["roic_gate"]["spread"]),
            ("Value_Creating", results["roic_gate"]["value_creating"]),
        ]
        for label in ["Bull", "Base", "Bear"]:
            s = results["scenarios"][label]
            rows.append((f"{label}_Growth", s["growth_rate"]))
            rows.append((f"{label}_Fair_Price", s["fair_price"]))
            rows.append((f"{label}_Upside", s["upside_downside"]))

        return pd.DataFrame(rows, columns=["Metric", "Value"])


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python reverse_dcf_engine.py <data_file.xlsx> [--wacc 0.08] [--tg 0.02]")
        sys.exit(1)

    path = sys.argv[1]
    params = DCFParams()

    # Simple CLI overrides
    args = sys.argv[2:]
    for i in range(0, len(args) - 1, 2):
        if args[i] == "--wacc":
            params.wacc_override = float(args[i+1])
        elif args[i] == "--tg":
            params.terminal_growth = float(args[i+1])
        elif args[i] == "--years":
            params.projection_years = int(args[i+1])

    model = ReverseDCF.from_excel(path, params)
    results = model.run()
    model.print_report(results)
