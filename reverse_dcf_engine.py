"""
CORE DCF Engine v3 — 4-Stage Reverse DCF + All Adjustments
Improvements over v2:
  - Mid-cycle margin normalization
  - NWC modeling via DSO/DPI/DPO
  - IFRS 16 aware (lease in net debt + D&A split)
  - Scenario-weighted Expected Value
  - Implied multiples on Forward DCF
  - Margin of Safety (20% discount)
  - Shares dilution detection in Return Decomp
"""
import pandas as pd, numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class DCFConfig:
    wacc: float = 0.08
    terminal_growth: float = 0.015
    fade_growth: float = 0.025
    consensus_years: int = 2
    implied_years: int = 8
    fade_years: int = 10
    use_midcycle_margin: bool = True  # If True, base FCFF uses mid-cycle margin instead of current

@dataclass
class CScore:
    cfo_ni: int = 0; dso: int = 0; dsi: int = 0
    depr_intensity: int = 0; asset_growth: int = 0
    total: int = 0; details: Dict[str, str] = field(default_factory=dict)

@dataclass
class QualityProfile:
    roic_median: float = 0; roic_trend: str = ""; margin_stability: float = 0
    revenue_volatility: float = 0; fcf_conversion: float = 0; payout_avg: float = 0
    debt_ebitda: float = 0; c_score: CScore = field(default_factory=CScore); grade: str = ""

class CoreDCF:
    def __init__(self, hist, current, config=None, ticker="", ltm_data=None):
        self.hist = hist.copy(); self.current = current
        self.config = config or DCFConfig(); self.ticker = ticker
        self.ltm_data = ltm_data or {}; self._warnings = []; self.peers = []; self._prepare()

    # ══ EXCEL LOADER ══════════════════════════════════════════════════════════
    @classmethod
    def from_excel(cls, path):
        xl = pd.ExcelFile(path)
        raw = pd.read_excel(xl, "Fundamentals", header=None)
        ticker_raw = raw.iloc[1, 1] if len(raw) > 1 and pd.notna(raw.iloc[1, 1]) else ""
        ticker = str(ticker_raw).replace(" Equity", "").replace(" Index", "").strip()

        hc_header_row = None
        for i in range(len(raw)):
            if "HARD COPY" in str(raw.iloc[i, 0]).upper(): hc_header_row = i + 1; break
        if hc_header_row is None: raise ValueError("No HARD COPY section")

        hc = pd.read_excel(xl, "Fundamentals", header=hc_header_row)
        hc = hc[[c for c in hc.columns if not str(c).startswith("Unnamed")]]
        first_col = hc.columns[0]

        valid_rows = []
        for idx, row in hc.iterrows():
            label = str(row[first_col]).strip().upper()
            if any(s in label for s in ["LTM","FY1","DERIVED","SUMMARY"]): break
            if label.replace(".","").isdigit(): valid_rows.append(idx)
        hc_years = hc.loc[valid_rows].copy()

        ltm_mask = hc[first_col].astype(str).str.upper() == "LTM"
        hc_ltm = hc[ltm_mask]

        hc_years = hc_years.set_index(first_col)
        hc_years.index = pd.to_datetime([str(int(float(y))) for y in hc_years.index], format="%Y")
        hc_years.index.name = "Date"

        col_map = {"Revenue":"Revenue","Gross Profit":"Gross_Profit","EBIT":"EBIT","EBITDA":"EBITDA",
            "D&A":"DA","Tax Expense":"Tax_Expense","Interest Exp":"Interest_Expense",
            "Net Income":"Net_Income","SBC":"SBC","Diluted EPS":"Diluted_EPS","DPS":"DPS",
            "Total Debt":"Total_Debt","Lease Liab":"Lease_Liab","Cash & Equiv":"Cash",
            "Minority Int":"Minority_Interest","Shares Out":"Shares_Outstanding",
            "Dil Shares":"Diluted_Shares","Book Equity":"Book_Equity","Total Assets":"Total_Assets",
            "CapEx":"CapEx","CFO":"CFO","Chg in NWC":"Change_NWC",
            "Accts Recv":"Accounts_Receivable","Inventory":"Inventory",
            "Curr Assets":"Current_Assets","Price YE":"Price_YE"}
        hist = hc_years.rename(columns=col_map)
        valid = [v for v in col_map.values() if v in hist.columns]
        hist = hist[valid].apply(pd.to_numeric, errors="coerce")

        ltm_data = {}
        if not hc_ltm.empty:
            lr = hc_ltm.iloc[0]
            for o, m in col_map.items():
                v = lr.get(o)
                if pd.notna(v):
                    try: ltm_data[m] = float(v)
                    except: pass

        curr_raw = pd.read_excel(xl, "Current", header=2).dropna(subset=["Field"])
        hc_col = "Hard Copy" if "Hard Copy" in curr_raw.columns else curr_raw.columns[2]
        current = dict(zip(curr_raw["Field"], curr_raw[hc_col]))
        for _, row in curr_raw.iterrows():
            f = str(row.get("Field","")); v = row.get(hc_col)
            if "Net Debt" in f and pd.notna(v): current["Net_Debt"] = v

        config = DCFConfig()
        if "WACC" in xl.sheet_names:
            wdf = pd.read_excel(xl, "WACC", header=2)
            for _, row in wdf.iterrows():
                p = str(row.get("Parameter","")).strip(); v = row.get("Value")
                if pd.notna(v):
                    try:
                        fv = float(v)
                        if "BBG WACC" in p and 0.01 < fv < 0.25: config.wacc = fv
                        elif "Manual Override" in p and 0.01 < fv < 0.25: config.wacc = fv
                        elif "Terminal Growth" in p: config.terminal_growth = fv
                        elif "Fade Growth" in p: config.fade_growth = fv
                    except: pass

        bbg_w = cls._safe_num(current.get("BBG WACC"))
        if bbg_w and bbg_w > 1: bbg_w /= 100
        if bbg_w and 0.01 < bbg_w < 0.25 and config.wacc == 0.08: config.wacc = bbg_w

        # Peers
        peers_data = []
        if "Peers" in xl.sheet_names:
            pr = pd.read_excel(xl, "Peers", header=None)
            hdr_count = 0
            hc_hdr = 2
            for i in range(len(pr)):
                if str(pr.iloc[i, 0]).strip() == "Ticker" and str(pr.iloc[i, 1]).strip() == "Name":
                    hdr_count += 1
                    if hdr_count == 2: hc_hdr = i; break
            for j in range(1, 8):
                ri = hc_hdr + j
                if ri >= len(pr): break
                tkr = pr.iloc[ri, 0]
                if pd.isna(tkr) or "Peer Avg" in str(tkr): continue
                name = pr.iloc[ri, 1] if pd.notna(pr.iloc[ri, 1]) else ""
                row_data = {"ticker": str(tkr).replace(" Equity","").strip(), "name": str(name)}
                cols = ["P/E","EV/EBITDA","P/Sales","FCF","Div Yld","ROIC","Gross Mrg","EBIT Mrg"]
                for ci, cn in enumerate(cols):
                    v = pr.iloc[ri, 2+ci] if 2+ci < len(pr.columns) else None
                    try: row_data[cn] = float(v) if pd.notna(v) else None
                    except: row_data[cn] = None
                peers_data.append(row_data)

        obj = cls(hist, current, config, ticker, ltm_data)
        obj.peers = peers_data
        return obj

    # ══ DATA PREPARATION ══════════════════════════════════════════════════════
    def _prepare(self):
        h = self.hist; self._warnings = []
        fy_rev = self._last(h,"Revenue") or 1; fy_ebit = self._last(h,"EBIT") or 0
        ltm_rev = self.ltm_data.get("Revenue")
        if ltm_rev and fy_rev > 0 and 0.5 < ltm_rev/fy_rev < 1.5:
            self.base_revenue = ltm_rev; self.base_ebit = self.ltm_data.get("EBIT", fy_ebit)
            self._warnings.append(f"INFO: Using LTM Revenue ({ltm_rev:,.0f})")
        else:
            self.base_revenue = fy_rev; self.base_ebit = fy_ebit

        # Current margin
        self.ebit_margin = self.base_ebit / self.base_revenue if self.base_revenue else 0.15

        # Mid-cycle margin: trimmed mean of last 7Y (20% trim top/bottom)
        # — avoids structural breaks (e.g. divestments) and one-off restructuring years.
        # User override via Current sheet field "Clean Margin" takes precedence.
        clean_override = self._safe_num(self.current.get("Clean Margin"))
        if clean_override is not None and clean_override > 1: clean_override /= 100

        if "EBIT" in h and "Revenue" in h:
            margins_full = (h["EBIT"] / h["Revenue"]).replace([np.inf,-np.inf],np.nan).dropna()
            self.margin_min = float(margins_full.min()) if len(margins_full) >= 3 else self.ebit_margin
            self.margin_max = float(margins_full.max()) if len(margins_full) >= 3 else self.ebit_margin

            if clean_override is not None and 0.01 < clean_override < 0.60:
                self.mid_cycle_margin = clean_override
                self._warnings.append(f"INFO: Mid-Cycle Margin overridden by user ({clean_override:.1%})")
            elif len(margins_full) >= 5:
                # Trimmed mean of last 7Y
                recent = margins_full.iloc[-7:] if len(margins_full) >= 7 else margins_full
                lo, hi = np.percentile(recent, [20, 80])
                trimmed = recent[(recent >= lo) & (recent <= hi)]
                self.mid_cycle_margin = float(trimmed.mean()) if len(trimmed) > 0 else float(recent.median())
            else:
                self.mid_cycle_margin = float(margins_full.median())
        else:
            self.mid_cycle_margin = self.ebit_margin; self.margin_min = self.ebit_margin; self.margin_max = self.ebit_margin

        self.da_pct = self._ratio(h,"DA","Revenue",0.03)
        self.capex_pct = self._ratio(h,"CapEx","Revenue",0.05,absolute=True)
        self.sbc_pct = self._ratio(h,"SBC","Revenue",0.0,absolute=True)

        # NWC: DSO/DPI based modeling
        self.dso = self.dpi = self.dpo = 0.0; self.nwc_pct = 0.0
        if "Accounts_Receivable" in h and "Revenue" in h:
            dso_s = (h["Accounts_Receivable"] / h["Revenue"] * 365).dropna()
            if len(dso_s) >= 2: self.dso = float(dso_s.median())
        if "Inventory" in h and "Revenue" in h:
            dpi_s = (h["Inventory"] / h["Revenue"] * 365).dropna()
            if len(dpi_s) >= 2: self.dpi = float(dpi_s.median())
        # NWC as % of revenue from DSO + DPI
        self.nwc_pct = (self.dso + self.dpi) / 365 if (self.dso + self.dpi) > 0 else 0.0
        # But for FCFF we need change in NWC, not level — approximate as incremental
        # For growing company: delta NWC = nwc_pct * delta_revenue ≈ nwc_pct * growth * revenue
        # We'll use this in the DCF projection, not in base FCFF
        self.nwc_change_pct = 0.0  # base year: no change
        nwc_s = h.get("Change_NWC", pd.Series(dtype=float)).dropna()
        rev_s = h.get("Revenue", pd.Series(dtype=float)).dropna()
        if len(nwc_s) > 2 and len(rev_s) >= len(nwc_s):
            nwc_r = nwc_s / rev_s.iloc[:len(nwc_s)].values
            if nwc_r.std() > 0.05 or (nwc_r.max() > 0 and nwc_r.min() < 0):
                self.nwc_change_pct = 0.0
            else:
                self.nwc_change_pct = float(nwc_r.median())

        # Tax: median
        tax_rates = []
        if "Tax_Expense" in h and "EBIT" in h:
            for i in range(len(h)):
                t=h["Tax_Expense"].iloc[i]; e=h["EBIT"].iloc[i]
                if pd.notna(t) and pd.notna(e) and e!=0:
                    r=t/e
                    if 0<r<0.50: tax_rates.append(r)
        self.tax_rate = float(np.median(tax_rates)) if tax_rates else 0.20

        # FCFF: use mid-cycle margin by default (toggleable via DCFConfig)
        if self.config.use_midcycle_margin:
            self.base_fcff = self._compute_fcff(self.base_revenue, margin_override=self.mid_cycle_margin)
        else:
            self.base_fcff = self._compute_fcff(self.base_revenue)
        # Prefer BBG Current Shares Out (more recent — accounts for buybacks/issuances)
        # over historical last-year shares.
        shares_bbg = self._safe_num(self.current.get("Shares Out"))
        shares_hist = self._last(h,"Shares_Outstanding")
        if shares_bbg and shares_hist:
            # Use BBG current; warn if differs by >5% (indicates recent corporate action)
            shares = shares_bbg
            diff_pct = (shares_bbg - shares_hist) / shares_hist if shares_hist else 0
            if abs(diff_pct) > 0.05:
                self._warnings.append(
                    f"INFO: Using BBG Current Shares Out ({shares_bbg:,.0f}) "
                    f"vs Historical ({shares_hist:,.0f}) — "
                    f"{diff_pct:+.1%} change suggests recent rights issue, buyback, or split.")
        else:
            shares = shares_bbg or shares_hist or 1
        self.shares = shares; self.base_fcff_per_share = self.base_fcff/shares if shares else 0

        # ── Currency detection & normalization ────────────────────────────────
        # UK-listed stocks (".LN" / "LN") quote price in PENCE (GBp = 1/100 GBP)
        # but Market Cap/EV in GBP. Some report Revenue/EBITDA in USD (e.g. Glencore,
        # BP, Shell). We normalize price to a "base currency" so MCap = Sh × Price.
        self.price = self._safe_num(self.current.get("Price")) or 0
        mcap = self._safe_num(self.current.get("Market Cap")) or 0
        if self.base_revenue>0 and mcap>0 and mcap/self.base_revenue>5000:
            mcap /= 1e6; self._warnings.append("INFO: Market Cap normalized (÷1M)")

        # Detect pence-quoting: if Sh × Price differs from MCap by ~100x, price is in pence
        # IMPORTANT: Use BBG current Shares Out (not historical) since post-issuance share counts
        # may have changed materially. National Grid did 2024 rights issue → 25% more shares.
        self._is_pence_quoted = False
        shares_bbg = self._safe_num(self.current.get("Shares Out"))
        shares_for_check = shares_bbg if shares_bbg else shares  # fallback to hist
        if self.price > 0 and shares_for_check > 0 and mcap > 0:
            implied_mcap = self.price * shares_for_check
            ratio = implied_mcap / mcap if mcap else 1
            if 70 < ratio < 130:  # ~100x off → pence quotation (loosened from 80-120)
                self.price = self.price / 100
                self._is_pence_quoted = True
                self._warnings.append(
                    f"INFO: Price normalized from pence (GBp) to GBP "
                    f"(was {self._safe_num(self.current.get('Price')):.1f}p, now {self.price:.2f})")
            elif 0.7 < ratio < 1.3:
                # Already in major currency (GBP/EUR/USD/CHF) — no normalization needed
                pass
            else:
                # Suspicious but no clear pattern — warn
                self._warnings.append(
                    f"NOTE: Sh × Price / MCap = {ratio:.2f}x — unusual ratio. "
                    f"Verify BBG Shares Out ({shares_for_check:,.0f}) and Price ({self.price:.2f}) "
                    f"are in matching units.")

        # Detect Revenue/MCap currency mismatch (e.g. Glencore: USD revenue, GBP MCap)
        # We do NOT auto-FX-convert anymore — instead we warn loudly and offer
        # manual override via Excel field "FX Rate" in Current sheet.
        self._currency_warning = False
        self._fx_applied = None
        fcf_per_share_bbg = self._safe_num(self.current.get("FCF/Share TTM"))

        # Optional: user can specify an FX rate in Current sheet to convert MCap/EV
        # to fundamentals currency (e.g. for Glencore: FX Rate = 1.27 to convert GBP→USD)
        fx_rate = self._safe_num(self.current.get("FX Rate"))

        if fx_rate and 0.1 < fx_rate < 10:
            # User-specified FX → apply to MCap, EV, Price
            mcap = mcap * fx_rate
            self._fx_applied = fx_rate
            self.price_local = self.price
            self.price = self.price * fx_rate
            self._warnings.append(
                f"INFO: User-specified FX Rate {fx_rate} applied to Market Cap, EV, and Price.")
        elif self._is_pence_quoted and self.base_revenue > 0:
            # Auto-detection only as a warning, not auto-fix
            ev_to_rev_gbp = mcap / self.base_revenue
            if ev_to_rev_gbp < 0.5:
                self._warnings.append(
                    f"⚠ CURRENCY MISMATCH: This UK-listed stock likely reports Fundamentals "
                    f"in USD, but MCap/EV are in GBP. EV/Revenue = {ev_to_rev_gbp:.2f}x is suspicious. "
                    f"FIX: Add 'FX Rate' field in Current sheet (e.g. 1.27 for USD/GBP) "
                    f"OR convert all Fundamentals to GBP. Current results NOT RELIABLE.")
                self._currency_warning = True
        debt = self._last(h,"Total_Debt") or 0; lease = self._last(h,"Lease_Liab") or 0
        cash = self._last(h,"Cash") or 0; mi = self._last(h,"Minority_Interest") or 0
        self.market_cap = mcap; self.net_debt = debt+lease-cash; self.minority = mi
        self.lease_liab = lease

        # EV: prefer Bloomberg-provided EV (which includes pension, lease, adjustments)
        # over computed (MCap + NetDebt + Minority). The computed version misses
        # adjustments like pension liabilities and is therefore a vereinfachte Schätzung.
        bbg_ev = self._safe_num(self.current.get("EV"))
        # Same scale-detection as MCap: BBG sometimes reports in absolute units
        if bbg_ev and self.base_revenue > 0 and bbg_ev / self.base_revenue > 5000:
            bbg_ev /= 1e6
        # If FX was applied to MCap, also apply to BBG-EV
        if self._fx_applied and bbg_ev:
            bbg_ev = bbg_ev * self._fx_applied
        # Sanity: BBG-EV should be at least as big as MCap and within 30% of computed
        computed_ev = mcap + self.net_debt + mi
        if bbg_ev and bbg_ev > mcap * 0.9 and abs(bbg_ev/computed_ev - 1) < 0.30:
            self.market_ev = bbg_ev
            self._ev_source = "BBG"
            self._ev_adjustment = bbg_ev - computed_ev
            if abs(self._ev_adjustment) > mcap * 0.01:
                self._warnings.append(
                    f"INFO: BBG EV ({bbg_ev:,.0f}) used; differs from computed "
                    f"({computed_ev:,.0f}) by {self._ev_adjustment:+,.0f} "
                    f"(likely pension/other adjustments)")
        else:
            self.market_ev = computed_ev
            self._ev_source = "computed"
            self._ev_adjustment = 0

        # Consensus
        cons_fy1 = self._safe_num(self.current.get("Cons Rev FY1"))
        cons_fy2 = self._safe_num(self.current.get("Cons Rev FY2"))
        if cons_fy1 and self.base_revenue > 0 and cons_fy1 / self.base_revenue < 0.5:
            self._warnings.append(f"WARNING: Cons Rev FY1 ({cons_fy1:,.0f}) looks like partial year — using 0%")
            cons_fy1 = None
        self.consensus_growth_fy1 = cons_fy1/self.base_revenue-1 if cons_fy1 and self.base_revenue>0 else 0.0
        self.consensus_growth_fy2 = cons_fy2/cons_fy1-1 if cons_fy2 and cons_fy1 and cons_fy1>0 else self.consensus_growth_fy1

        bbg_w = self._safe_num(self.current.get("BBG WACC"))
        if bbg_w and bbg_w>1: bbg_w /= 100
        self.bbg_wacc = bbg_w
        roic = self._safe_num(self.current.get("ROIC"))
        if roic and roic>1: roic /= 100
        self.current_roic = roic or 0

    # ══ FCFF ══════════════════════════════════════════════════════════════════
    def _compute_fcff(self, revenue, margin_override=None):
        m = margin_override if margin_override is not None else self.ebit_margin
        nopat = revenue * m * (1 - self.tax_rate)
        return nopat + revenue*self.da_pct - revenue*self.capex_pct - revenue*abs(self.nwc_change_pct) - revenue*self.sbc_pct

    # ══ 4-STAGE DCF ══════════════════════════════════════════════════════════
    def _ev_from_fcf_growth(self, ig):
        w=self.config.wacc; tg=self.config.terminal_growth
        n1=self.config.consensus_years; n2=self.config.implied_years; n3=self.config.fade_years
        if w<=tg: return np.inf
        fcff=self.base_fcff; pv=0.0; yr=0
        for i in range(n1):
            yr+=1; g=[self.consensus_growth_fy1,self.consensus_growth_fy2][min(i,1)]
            fcff*=(1+g); pv+=fcff/(1+w)**yr
        for i in range(n2):
            yr+=1; fcff*=(1+ig); pv+=fcff/(1+w)**yr
        for i in range(n3):
            yr+=1; fp=(i+1)/n3; g=ig*(1-fp)+tg*fp; fcff*=(1+g); pv+=fcff/(1+w)**yr
        tv_fcff=fcff*(1+tg); tv=tv_fcff/(w-tg); pv_tv=tv/(1+w)**yr
        return pv+pv_tv

    def solve_implied_growth(self, tol=1e-6, max_iter=200):
        target=self.market_ev
        if target<=0: return 0.0
        # Sanity check: if base FCFF is negative, the standard Reverse DCF doesn't work.
        # Common for Utilities/Infrastructure during heavy capex phase (e.g. National Grid).
        # In this case, mark a warning and return a flag value.
        if self.base_fcff <= 0:
            if "FCFF_NEGATIVE" not in str(self._warnings):
                self._warnings.append(
                    f"⚠ MODEL LIMITATION: Base FCFF is {self.base_fcff:,.0f} (negative or zero). "
                    f"Standard Reverse DCF cannot solve for implied growth — typical for Utilities/"
                    f"Infrastructure in heavy CapEx phase (e.g. Grid Modernisation). "
                    f"Consider: (1) using EV/EBITDA framework instead, (2) using lower CapEx assumption "
                    f"reflecting steady-state, or (3) extending forecast period until FCFF turns positive. "
                    f"Engine output: FCFF_NEGATIVE flag.")
            return 0.0  # Return 0% as a neutral placeholder
        lo,hi=-0.30,0.80
        for _ in range(max_iter):
            mid=(lo+hi)/2; ev=self._ev_from_fcf_growth(mid)
            if abs(ev-target)/max(target,1)<tol: return mid
            if ev>target: hi=mid
            else: lo=mid
        return (lo+hi)/2

    # ══ SCENARIOS (with probability weighting) ════════════════════════════════
    def scenario_analysis(self, base_g, offsets=(-0.03,0,0.03), probs=(0.25,0.50,0.25)):
        r={}; exp_price = 0.0
        for lbl,off,prob in zip(["Bear","Base","Bull"],offsets,probs):
            g=base_g+off; ev=self._ev_from_fcf_growth(g)
            eq=ev-self.net_debt-self.minority; fp=eq/self.shares if self.shares else 0
            up=fp/self.price-1 if self.price else 0
            r[lbl]={"growth_rate":g,"ev":ev,"fair_price":fp,"upside":up,"probability":prob}
            exp_price += fp * prob
        # Expected value + margin of safety
        r["expected_value"] = exp_price
        r["expected_upside"] = exp_price/self.price - 1 if self.price else 0
        r["margin_of_safety_price"] = exp_price * 0.80
        r["margin_of_safety_upside"] = (exp_price * 0.80)/self.price - 1 if self.price else 0
        return r

    # ══ TV DECOMPOSITION ══════════════════════════════════════════════════════
    def tv_decomposition(self, ig):
        w=self.config.wacc; tg=self.config.terminal_growth
        fcff=self.base_fcff; pv_e=0.0; yr=0
        for i in range(self.config.consensus_years):
            yr+=1; g=[self.consensus_growth_fy1,self.consensus_growth_fy2][min(i,1)]
            fcff*=(1+g); pv_e+=fcff/(1+w)**yr
        for i in range(self.config.implied_years):
            yr+=1; fcff*=(1+ig); pv_e+=fcff/(1+w)**yr
        for i in range(self.config.fade_years):
            yr+=1; fp=(i+1)/self.config.fade_years; g=ig*(1-fp)+tg*fp; fcff*=(1+g); pv_e+=fcff/(1+w)**yr
        tv=fcff*(1+tg)/(w-tg) if w>tg else 0; pv_tv=tv/(1+w)**yr; tot=pv_e+pv_tv
        return {"pv_explicit":pv_e,"pv_terminal":pv_tv,"total_ev":tot,
                "explicit_pct":pv_e/tot if tot else 0,"tv_pct":pv_tv/tot if tot else 0,
                "explicit_years":self.config.consensus_years+self.config.implied_years+self.config.fade_years}

    # ══ C-SCORES ══════════════════════════════════════════════════════════════
    def compute_c_score(self):
        """C-Score: detect earnings management. Uses 5-7Y windows + magnitude thresholds
        (not just trend direction) to avoid false positives from cyclicality, M&A, and
        strategic working-capital decisions. User can flag a major M&A year via Current
        sheet field 'Major MA Year' (4-digit YYYY) to skip the asset growth check."""
        h = self.hist; cs = CScore(details={})

        # Use last 5Y for trend tests; require magnitude, not just sign
        WIN = 5

        # 1. CFO/NI: declining only if median <0.8 (real quality issue), not just endpoints
        if "CFO" in h and "Net_Income" in h:
            ratio = (h["CFO"]/h["Net_Income"]).replace([np.inf,-np.inf],np.nan).dropna()
            recent = ratio.iloc[-WIN:] if len(ratio) >= WIN else ratio
            med = float(recent.median()) if len(recent) > 0 else 1.0
            if med < 0.8:
                cs.cfo_ni = 1; cs.details["CFO/NI"] = f"Weak ({med:.2f}x median)"
            else:
                cs.details["CFO/NI"] = f"OK ({med:.2f}x)"

        # 2. DSO: only flag if last 2Y avg vs first 2Y avg of window rises ≥15%
        if "Accounts_Receivable" in h and "Revenue" in h:
            dso_s = (h["Accounts_Receivable"]/h["Revenue"]*365).replace([np.inf,-np.inf],np.nan).dropna()
            recent = dso_s.iloc[-WIN:] if len(dso_s) >= WIN else dso_s
            if len(recent) >= 4:
                early = recent.iloc[:2].mean(); late = recent.iloc[-2:].mean()
                if early > 0 and (late/early - 1) >= 0.15:
                    cs.dso = 1; cs.details["DSO"] = f"Up {(late/early-1):+.0%} ({early:.0f}→{late:.0f}d)"
                else:
                    cs.details["DSO"] = f"OK ({late:.0f}d)"
            else:
                cs.details["DSO"] = "OK (insufficient data)"

        # 3. DSI: same logic — ≥15% rise required, ignore <5% noise
        if "Inventory" in h and "Revenue" in h:
            dsi_s = (h["Inventory"]/h["Revenue"]*365).replace([np.inf,-np.inf],np.nan).dropna()
            recent = dsi_s.iloc[-WIN:] if len(dsi_s) >= WIN else dsi_s
            if len(recent) >= 4:
                early = recent.iloc[:2].mean(); late = recent.iloc[-2:].mean()
                if early > 0 and (late/early - 1) >= 0.15:
                    cs.dsi = 1; cs.details["DSI"] = f"Up {(late/early-1):+.0%} ({early:.0f}→{late:.0f}d)"
                else:
                    cs.details["DSI"] = f"OK ({late:.0f}d)"
            else:
                cs.details["DSI"] = "OK (insufficient data)"

        # 4. Depr intensity: only flag if ≥30% drop (deferring real CapEx)
        if "DA" in h and "Total_Assets" in h:
            d = (h["DA"]/h["Total_Assets"]).replace([np.inf,-np.inf],np.nan).dropna()
            recent = d.iloc[-WIN:] if len(d) >= WIN else d
            if len(recent) >= 4:
                early = recent.iloc[:2].mean(); late = recent.iloc[-2:].mean()
                if early > 0 and (late/early - 1) <= -0.30:
                    cs.depr_intensity = 1; cs.details["Depr"] = f"Down {(late/early-1):+.0%}"
                else:
                    cs.details["Depr"] = "OK"
            else:
                cs.details["Depr"] = "OK (insufficient data)"

        # 5. Assets vs Rev: 5Y window, skipped if user flagged a major M&A year
        ma_year = self._safe_num(self.current.get("Major MA Year"))
        if "Total_Assets" in h and "Revenue" in h:
            ta = h["Total_Assets"].dropna(); rv = h["Revenue"].dropna()
            if ma_year and 1990 < ma_year < 2100:
                cs.details["Assets"] = f"Skipped (Major M&A {int(ma_year)})"
            elif len(ta) >= WIN+1 and len(rv) >= WIN+1:
                ag = (ta.iloc[-1]/ta.iloc[-(WIN+1)])**(1/WIN) - 1
                rg = (rv.iloc[-1]/rv.iloc[-(WIN+1)])**(1/WIN) - 1
                if ag > rg + 0.05:
                    cs.asset_growth = 1
                    cs.details["Assets"] = f"Asset gr ({ag:.1%} p.a.) > Rev gr ({rg:.1%} p.a.)"
                else:
                    cs.details["Assets"] = f"OK ({ag:.1%} vs {rg:.1%} p.a.)"
            else:
                cs.details["Assets"] = "OK (insufficient data)"

        cs.total = cs.cfo_ni + cs.dso + cs.dsi + cs.depr_intensity + cs.asset_growth
        return cs

    # ══ QUALITY ═══════════════════════════════════════════════════════════════
    def compute_quality(self):
        h=self.hist; qp=QualityProfile()
        if "EBIT" in h and "Book_Equity" in h and "Total_Debt" in h and "Cash" in h:
            nopat=h["EBIT"]*(1-self.tax_rate); ic=h["Book_Equity"]+h["Total_Debt"]-h["Cash"]
            roic=(nopat/ic).replace([np.inf,-np.inf],np.nan).dropna()
            if len(roic)>0: qp.roic_median=float(roic.median())
            if len(roic)>=3:
                if roic.iloc[-1]>roic.iloc[-3]+0.02: qp.roic_trend="improving"
                elif roic.iloc[-1]<roic.iloc[-3]-0.02: qp.roic_trend="declining"
                else: qp.roic_trend="stable"
        if "EBIT" in h and "Revenue" in h:
            m=(h["EBIT"]/h["Revenue"]).dropna(); qp.margin_stability=float(m.std()) if len(m)>1 else 0
        if "Revenue" in h:
            rv=h["Revenue"].dropna()
            if len(rv)>2: qp.revenue_volatility=float(rv.pct_change().dropna().std())
        if "CFO" in h and "Net_Income" in h:
            r=(h["CFO"]/h["Net_Income"]).replace([np.inf,-np.inf],np.nan).dropna()
            qp.fcf_conversion=float(r.median()) if len(r)>0 else 0
        if "DPS" in h and "Diluted_EPS" in h:
            p=(h["DPS"]/h["Diluted_EPS"]).replace([np.inf,-np.inf],np.nan).dropna()
            qp.payout_avg=float(p.median()) if len(p)>0 else 0
        if "Total_Debt" in h and "EBITDA" in h:
            d=self._last(h,"Total_Debt") or 0; e=self._last(h,"EBITDA") or 1
            qp.debt_ebitda=d/e if e else 0
        qp.c_score = self.compute_c_score()
        # Quality scoring (refined v2):
        # - ROIC: blend median + current to capture structural improvement
        # - Margin Stability: looser threshold for industrials (5% instead of 3%)
        # - C-Score: stronger bonus for clean 0/5 (+2 instead of +1)
        sc = 0
        # ROIC: use max of median (long-term) and current (latest) — captures structural improvement
        # e.g. ABB: median 13.2% (drag from pre-divestment years) but current 21% — credit current
        roic_effective = max(qp.roic_median, self.current_roic) if self.current_roic else qp.roic_median
        if roic_effective > 0.20: sc += 3   # exceptional
        elif roic_effective > 0.15: sc += 2
        elif roic_effective > 0.10: sc += 1
        # Margin Stability: tiered, looser for typical industrials
        if qp.margin_stability < 0.025: sc += 2     # exceptional (Healthcare/Software-tier)
        elif qp.margin_stability < 0.05: sc += 1    # solid (typical for high-quality industrials)
        # FCF conversion
        if qp.fcf_conversion > 1.1: sc += 2
        elif qp.fcf_conversion > 0.9: sc += 1
        # Leverage: tiered
        if qp.debt_ebitda < 1.5: sc += 2     # very strong
        elif qp.debt_ebitda < 2.5: sc += 1   # acceptable
        # C-Score: strong bonus for clean profile
        if qp.c_score.total == 0: sc += 2    # perfect — earnings quality is pristine
        elif qp.c_score.total <= 1: sc += 1
        # Subtract C-Score, capped at -3 (prevents single signal from killing grade)
        sc -= min(qp.c_score.total, 3)
        # Grade thresholds: A ≥7, B ≥5, C ≥2, D <2
        qp.grade = "A" if sc >= 7 else "B" if sc >= 5 else "C" if sc >= 2 else "D"
        return qp

    # ══ HISTORICAL MULTIPLES ══════════════════════════════════════════════════
    def historical_multiples(self):
        h=self.hist; result=[]
        for i in range(len(h)):
            yr=h.index[i].year
            p=h["Price_YE"].iloc[i] if "Price_YE" in h else np.nan
            eps=h["Diluted_EPS"].iloc[i] if "Diluted_EPS" in h else np.nan
            ebitda=h["EBITDA"].iloc[i] if "EBITDA" in h else np.nan
            rev=h["Revenue"].iloc[i] if "Revenue" in h else np.nan
            sh=h["Shares_Outstanding"].iloc[i] if "Shares_Outstanding" in h else np.nan
            debt=h["Total_Debt"].iloc[i] if "Total_Debt" in h else 0
            cash=h["Cash"].iloc[i] if "Cash" in h else 0
            lease=h["Lease_Liab"].iloc[i] if "Lease_Liab" in h else 0
            mi=h["Minority_Interest"].iloc[i] if "Minority_Interest" in h else 0
            mcap=p*sh if pd.notna(p) and pd.notna(sh) and sh>0 else np.nan
            ev=mcap+(debt or 0)+(lease or 0)-(cash or 0)+(mi or 0) if pd.notna(mcap) else np.nan
            pe=p/eps if pd.notna(p) and pd.notna(eps) and eps>0 else np.nan
            ev_eb=ev/ebitda if pd.notna(ev) and pd.notna(ebitda) and ebitda>0 else np.nan
            ps=mcap/rev if pd.notna(mcap) and pd.notna(rev) and rev>0 else np.nan
            nopat=h["EBIT"].iloc[i]*(1-self.tax_rate) if "EBIT" in h and pd.notna(h["EBIT"].iloc[i]) else 0
            da=h["DA"].iloc[i] if "DA" in h and pd.notna(h["DA"].iloc[i]) else 0
            cx=abs(h["CapEx"].iloc[i]) if "CapEx" in h and pd.notna(h["CapEx"].iloc[i]) else 0
            sbc=abs(h["SBC"].iloc[i]) if "SBC" in h and pd.notna(h["SBC"].iloc[i]) else 0
            fcff=nopat+da-cx-sbc; fy=fcff/mcap if pd.notna(mcap) and mcap>0 else np.nan
            result.append({"Year":yr,"P/E":pe,"EV/EBITDA":ev_eb,"P/Sales":ps,"FCF Yield":fy})
        return pd.DataFrame(result).set_index("Year")

    # ══ RETURN DECOMPOSITION ══════════════════════════════════════════════════
    def return_decomposition(self):
        h=self.hist
        if "Price_YE" not in h or "Revenue" not in h: return {"available":False}
        pr=h["Price_YE"].dropna(); rv=h["Revenue"].dropna()
        ebit=h.get("EBIT",pd.Series(dtype=float)).dropna()
        sh=h.get("Shares_Outstanding",pd.Series(dtype=float)).dropna()
        dps=h.get("DPS",pd.Series(dtype=float)).dropna()
        if len(pr)<2 or len(rv)<2: return {"available":False}
        n=len(pr)-1; p0,p1=pr.iloc[0],pr.iloc[-1]
        tr=(p1/p0)**(1/n)-1 if p0>0 else 0
        rg=(rv.iloc[-1]/rv.iloc[0])**(1/n)-1 if rv.iloc[0]>0 else 0
        m0=ebit.iloc[0]/rv.iloc[0] if len(ebit)>0 and rv.iloc[0]>0 else 0
        m1=ebit.iloc[-1]/rv.iloc[-1] if len(ebit)>0 and rv.iloc[-1]>0 else 0
        me=(m1/m0)**(1/n)-1 if m0>0 else 0
        # Shares: detect dilution vs buyback
        if len(sh)>=2 and sh.iloc[0]>0:
            share_change = (sh.iloc[-1]/sh.iloc[0])**(1/n)-1
            if share_change > 0.05:  # >5% dilution
                bb = -share_change  # negative = dilution
                self._warnings.append(f"INFO: Shares increased {share_change:.1%} p.a. (dilution, not buybacks)")
            else:
                bb = 1-(sh.iloc[-1]/sh.iloc[0])**(1/n)
        else: bb=0
        if len(dps)>0 and len(pr)>0:
            dy_list=[dps.iloc[i]/pr.iloc[i] for i in range(min(len(dps),len(pr))) if pr.iloc[i]>0 and pd.notna(dps.iloc[i])]
            dy=float(np.mean(dy_list)) if dy_list else 0
        else:
            dy=self._safe_num(self.current.get("Div Yield")) or 0
            if dy>1: dy/=100
        mexp=tr-rg-me-bb-dy
        return {"available":True,"start_year":str(pr.index[0].year),"end_year":str(pr.index[-1].year),
            "years":n,"total_return_ann":tr,"revenue_growth_ann":rg,"margin_effect_ann":me,
            "buyback_ann":bb,"dividend_yield":dy,"multiple_expansion_ann":mexp,
            "margin_first":m0,"margin_last":m1,"price_first":float(p0),"price_last":float(p1),
            "shares_first":float(sh.iloc[0]) if len(sh)>0 else 0,"shares_last":float(sh.iloc[-1]) if len(sh)>0 else 0}

    # ══ IMPLIED MULTIPLES (for Forward DCF) ═══════════════════════════════════
    def implied_multiples(self, fair_ev, projected_revenue=None, projected_ebit=None, projected_ebitda=None, projected_ni=None):
        """What multiples does your fair value imply?"""
        result = {}
        fair_eq = fair_ev - self.net_debt - self.minority
        fair_price = fair_eq / self.shares if self.shares else 0
        if projected_ebit and projected_ebit > 0:
            result["implied_EV/EBIT"] = fair_ev / projected_ebit
        if projected_ebitda and projected_ebitda > 0:
            result["implied_EV/EBITDA"] = fair_ev / projected_ebitda
        if projected_revenue and projected_revenue > 0:
            result["implied_P/Sales"] = (fair_price * self.shares) / projected_revenue
        if projected_ni and projected_ni > 0:
            result["implied_P/E"] = fair_price / (projected_ni / self.shares)
        result["fair_price"] = fair_price
        return result

    # ══ PLAUSIBILITY ══════════════════════════════════════════════════════════
    def plausibility_checks(self, ig):
        h=self.hist; rv=h.get("Revenue",pd.Series(dtype=float)).dropna(); n=len(rv)
        cagr5=(rv.iloc[-1]/rv.iloc[-6])**(1/5)-1 if n>=6 and rv.iloc[-6]>0 else ((rv.iloc[-1]/rv.iloc[0])**(1/max(n-1,1))-1 if n>=2 and rv.iloc[0]>0 else 0)
        cagr3=(rv.iloc[-1]/rv.iloc[-4])**(1/3)-1 if n>=4 and rv.iloc[-4]>0 else 0
        yoy=rv.pct_change().dropna(); mx=float(yoy.max()) if len(yoy)>0 else 0
        def _f(imp,hist,name):
            if hist==0: return {"flag":"🟡","check":name,"implied":f"{imp:.1%}","historical":"N/A","ratio":"N/A"}
            r=abs(imp/hist) if hist!=0 else 0
            if imp>0 and imp>mx and mx>0: f="🔴"
            elif r>2: f="🔴"
            elif r>1.5: f="🟡"
            else: f="🟢"
            return {"flag":f,"check":name,"implied":f"{imp:.1%}","historical":f"{hist:.1%}","ratio":f"{r:.1f}x"}
        checks=[_f(ig,cagr5,"vs 5Y CAGR"),_f(ig,cagr3,"vs 3Y CAGR"),
            {"flag":"🟢" if ig<=mx else "🔴","check":"vs Max","implied":f"{ig:.1%}","historical":f"{mx:.1%}",
             "ratio":"OK" if ig<=mx else "EXCEEDS"}]
        return checks,cagr5,cagr3,mx

    # ══ MAIN RUN ══════════════════════════════════════════════════════════════
    def run(self):
        ig=self.solve_implied_growth(); sc=self.scenario_analysis(ig)
        tv=self.tv_decomposition(ig); q=self.compute_quality()
        hm=self.historical_multiples(); rd=self.return_decomposition()
        pl,c5,c3,mx=self.plausibility_checks(ig)
        return {"ticker":self.ticker,"price":self.price,"market_ev":self.market_ev,
            "market_cap":self.market_cap,"implied_growth":ig,"wacc":self.config.wacc,
            "terminal_growth":self.config.terminal_growth,"base_fcff":self.base_fcff,
            "base_fcff_per_share":self.base_fcff_per_share,"ebit_margin":self.ebit_margin,
            "mid_cycle_margin":self.mid_cycle_margin,"margin_range":(self.margin_min,self.margin_max),
            "consensus_fy1":self.consensus_growth_fy1,"consensus_fy2":self.consensus_growth_fy2,
            "dso":self.dso,"dpi":self.dpi,
            "scenarios":sc,"tv_decomposition":tv,"quality":q,"historical_multiples":hm,
            "return_decomposition":rd,"plausibility":pl,"cagr_5y":c5,"cagr_3y":c3,
            "max_growth":mx,"roic_spread":self.current_roic-self.config.wacc,
            "roic":self.current_roic,"warnings":self._warnings,"peers":self.peers}

    # ══ HELPERS ═══════════════════════════════════════════════════════════════
    def _last(self,df,col):
        if col not in df: return None
        s=df[col].dropna(); return float(s.iloc[-1]) if len(s)>0 else None

    def _ratio(self,df,num,den,default=0,absolute=False):
        if num not in df or den not in df: return default
        n=df[num].dropna(); d=df[den].dropna()
        if len(n)<2 or len(d)<2: return default
        ml=min(len(n),len(d))
        r=abs(n.iloc[:ml].values)/d.iloc[:ml].values if absolute else n.iloc[:ml].values/d.iloc[:ml].values
        v=r[(r>-1)&(r<1)&~np.isnan(r)]; return float(np.median(v)) if len(v)>0 else default

    @staticmethod
    def _safe_num(val,default=None):
        if val is None: return default
        try: f=float(val); return f if not np.isnan(f) else default
        except: return default
