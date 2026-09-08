"""
CORE DCF Engine v4 — one FCFF engine, two questions.

  1. Reverse:  which FCF growth (Stage 2) does the market price imply, given an explicit normalized base?
  2. Forward:  what is the company worth under MY path (growth / margin per year, optional segments)?

Design rules
  - The normalized base year is an explicit input (`Base`). The engine only PROPOSES it (with a confidence flag).
  - EV → equity goes through exactly one function (`ev_to_equity`), so Base path == price by construction.
  - No AI, no PDF, no C-Scores, no return decomposition. Context = quality metrics, historical multiples, peers.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np
import pandas as pd


# ══ INPUT OBJECTS ═════════════════════════════════════════════════════════════
@dataclass
class DCFConfig:
    wacc: float = 0.08
    terminal_growth: float = 0.015
    consensus_years: int = 2        # Stage 1: consensus revenue growth (FY1/FY2[/FY3])
    implied_years: int = 8          # Stage 2: solved (reverse) or user path (forward)
    fade_years: int = 10            # Stage 3: linear fade of growth to Tg
    use_bbg_ev: bool = True         # solver target: BBG EV (incl. adjustments) vs MCap + net debt + MI


@dataclass
class Base:
    """Normalized base year. Everything the DCF grows from."""
    revenue: float
    ebit_margin: float
    da_pct: float
    capex_pct: float
    sbc_pct: float
    nwc_pct: float                  # NWC investment as % of incremental revenue
    tax_rate: float

    def fcff(self, revenue: Optional[float] = None, margin: Optional[float] = None, d_rev: float = 0.0) -> float:
        rev = self.revenue if revenue is None else revenue
        m = self.ebit_margin if margin is None else margin
        ebit = rev * m
        nopat = ebit - max(ebit, 0) * self.tax_rate          # no tax credit on losses
        return nopat + rev * self.da_pct - rev * self.capex_pct - rev * self.sbc_pct - self.nwc_pct * d_rev


# ══ ENGINE ════════════════════════════════════════════════════════════════════
class CoreDCF:
    def __init__(self, hist: pd.DataFrame, current: dict, config: Optional[DCFConfig] = None,
                 ticker: str = "", ltm_data: Optional[dict] = None):
        self.hist = hist.copy(); self.current = current
        self.config = config or DCFConfig(); self.ticker = ticker
        self.ltm_data = ltm_data or {}; self.peers: List[dict] = []; self.guidance = None
        self.warnings: List[str] = []
        self._prepare()

    # ── Excel loader (unchanged template) ─────────────────────────────────
    @classmethod
    def from_excel(cls, path):
        xl = pd.ExcelFile(path)
        raw = pd.read_excel(xl, "Fundamentals", header=None)
        ticker_raw = raw.iloc[1, 1] if len(raw) > 1 and pd.notna(raw.iloc[1, 1]) else ""
        ticker = str(ticker_raw).replace(" Equity", "").replace(" Index", "").strip()
        hc_header_row = None
        for i in range(len(raw)):
            if "HARD COPY" in str(raw.iloc[i, 0]).upper(): hc_header_row = i + 1; break
        if hc_header_row is None: raise ValueError("No HARD COPY section in Fundamentals")
        hc = pd.read_excel(xl, "Fundamentals", header=hc_header_row)
        hc = hc[[c for c in hc.columns if not str(c).startswith("Unnamed")]]
        first_col = hc.columns[0]
        valid_rows = []
        for idx, row in hc.iterrows():
            label = str(row[first_col]).strip().upper()
            if any(s in label for s in ["LTM", "FY1", "DERIVED", "SUMMARY"]): break
            if label.replace(".", "").isdigit(): valid_rows.append(idx)
        hc_years = hc.loc[valid_rows].copy().set_index(first_col)
        hc_years.index = pd.to_datetime([str(int(float(y))) for y in hc_years.index], format="%Y")
        hc_years.index.name = "Date"
        col_map = {"Revenue": "Revenue", "Gross Profit": "Gross_Profit", "EBIT": "EBIT", "EBITDA": "EBITDA",
                   "D&A": "DA", "Tax Expense": "Tax_Expense", "Interest Exp": "Interest_Expense",
                   "Net Income": "Net_Income", "SBC": "SBC", "Diluted EPS": "Diluted_EPS", "DPS": "DPS",
                   "Total Debt": "Total_Debt", "Lease Liab": "Lease_Liab", "Cash & Equiv": "Cash",
                   "Minority Int": "Minority_Interest", "Shares Out": "Shares_Outstanding",
                   "Dil Shares": "Diluted_Shares", "Book Equity": "Book_Equity", "Total Assets": "Total_Assets",
                   "CapEx": "CapEx", "CFO": "CFO", "Chg in NWC": "Change_NWC",
                   "Accts Recv": "Accounts_Receivable", "Inventory": "Inventory",
                   "Curr Assets": "Current_Assets", "Price YE": "Price_YE"}
        hist = hc_years.rename(columns=col_map)
        hist = hist[[v for v in col_map.values() if v in hist.columns]].apply(pd.to_numeric, errors="coerce")
        ltm_data = {}
        ltm = hc[hc[first_col].astype(str).str.upper() == "LTM"]
        if not ltm.empty:
            for o, m in col_map.items():
                v = ltm.iloc[0].get(o)
                if pd.notna(v):
                    try: ltm_data[m] = float(v)
                    except (TypeError, ValueError): pass
        curr_raw = pd.read_excel(xl, "Current", header=2).dropna(subset=["Field"])
        hc_col = "Hard Copy" if "Hard Copy" in curr_raw.columns else curr_raw.columns[2]
        current = dict(zip(curr_raw["Field"], curr_raw[hc_col]))
        config = DCFConfig()
        if "WACC" in xl.sheet_names:
            wdf = pd.read_excel(xl, "WACC", header=2)
            for _, row in wdf.iterrows():
                p = str(row.get("Parameter", "")).strip(); v = cls._safe_num(row.get("Value"))
                if v is None: continue
                if ("BBG WACC" in p or "Manual Override" in p) and 0.01 < v < 0.25: config.wacc = v
                elif "Terminal Growth" in p and 0 <= v < 0.05: config.terminal_growth = v
        peers = []
        if "Peers" in xl.sheet_names:
            pr = pd.read_excel(xl, "Peers", header=None); hdr_count = 0; hc_hdr = 2
            for i in range(len(pr)):
                if str(pr.iloc[i, 0]).strip() == "Ticker" and str(pr.iloc[i, 1]).strip() == "Name":
                    hdr_count += 1
                    if hdr_count == 2: hc_hdr = i; break
            for j in range(1, 8):
                ri = hc_hdr + j
                if ri >= len(pr): break
                tkr = pr.iloc[ri, 0]
                if pd.isna(tkr) or "Peer Avg" in str(tkr): continue
                row = {"Ticker": str(tkr).replace(" Equity", "").strip(), "Name": str(pr.iloc[ri, 1] if pd.notna(pr.iloc[ri, 1]) else "")}
                for ci, cn in enumerate(["P/E", "EV/EBITDA", "P/Sales", "FCF", "Div Yld", "ROIC", "Gross Mrg", "EBIT Mrg"]):
                    row[cn] = cls._safe_num(pr.iloc[ri, 2 + ci]) if 2 + ci < len(pr.columns) else None
                peers.append(row)
        obj = cls(hist, current, config, ticker, ltm_data); obj.peers = peers
        try:
            from guidance_dcf import read_guidance_sheet
            obj.guidance = read_guidance_sheet(xl)
        except Exception as e:
            obj.warnings.append(f"Guidance sheet ignored: {e}")
        return obj

    # ── preparation: historical ratios, base proposal, market data ────────
    def _prepare(self):
        h = self.hist; w = self.warnings
        fy_rev = self._last(h, "Revenue") or 1.0; fy_ebit = self._last(h, "EBIT") or 0.0
        ltm_rev = self.ltm_data.get("Revenue")
        if ltm_rev and 0.5 < ltm_rev / fy_rev < 1.5:
            self.base_revenue, self.base_ebit = ltm_rev, self.ltm_data.get("EBIT", fy_ebit)
            w.append(f"Base revenue = LTM ({ltm_rev:,.0f})")
        else:
            self.base_revenue, self.base_ebit = fy_rev, fy_ebit
        self.current_margin = self.base_ebit / self.base_revenue if self.base_revenue else 0.0

        # Margin history → proposal + confidence
        m = (h["EBIT"] / h["Revenue"]).replace([np.inf, -np.inf], np.nan).dropna() if {"EBIT", "Revenue"} <= set(h.columns) else pd.Series(dtype=float)
        self.margin_hist = m
        if len(m) >= 3:
            recent = m.iloc[-7:]; lo, hi = np.percentile(recent, [20, 80])
            trimmed = recent[(recent >= lo) & (recent <= hi)]
            proposal = float(trimmed.mean()) if len(trimmed) else float(recent.median())
            std = float(recent.std()); rng = (float(m.min()), float(m.max()))
            if rng[0] < -0.02 and rng[1] > 0 or proposal <= 0:
                conf, why = "red", f"Margin range {rng[0]:.0%}–{rng[1]:.0%} crosses zero: no steady state in history. Set the base margin yourself."
            elif std > 0.05:
                conf, why = "amber", f"Margin std {std:.1%} (last 7Y): cyclical. Proposal = trimmed mean {proposal:.1%}, current {self.current_margin:.1%}."
            else:
                conf, why = "green", f"Margin stable (std {std:.1%}). Proposal = trimmed mean {proposal:.1%}."
        else:
            proposal, conf, why = self.current_margin, "amber", "Fewer than 3 years of margin history."
        self.margin_proposal, self.margin_confidence, self.margin_reason = proposal, conf, why

        # Ratios (medians)
        da = self._ratio(h, "DA", "Revenue", 0.03); capex = self._ratio(h, "CapEx", "Revenue", 0.05, absolute=True)
        sbc = self._ratio(h, "SBC", "Revenue", 0.0, absolute=True)
        dso = dpi = 0.0
        if {"Accounts_Receivable", "Revenue"} <= set(h.columns):
            s = (h["Accounts_Receivable"] / h["Revenue"] * 365).dropna(); dso = float(s.median()) if len(s) >= 2 else 0.0
        if {"Inventory", "Revenue"} <= set(h.columns):
            s = (h["Inventory"] / h["Revenue"] * 365).dropna(); dpi = float(s.median()) if len(s) >= 2 else 0.0
        self.dso, self.dpi = dso, dpi
        nwc = min((dso + dpi) / 365, 0.40)
        rates = []
        if {"Tax_Expense", "EBIT"} <= set(h.columns):
            r = (h["Tax_Expense"] / h["EBIT"]).replace([np.inf, -np.inf], np.nan).dropna()
            rates = [x for x in r if 0 < x < 0.5]
        tax = float(np.median(rates)) if rates else 0.20
        self.base_proposal = Base(self.base_revenue, proposal, da, capex, sbc, nwc, tax)
        self.base = self.base_proposal                      # until set_base() is called

        # Market data
        self.price_local = self._safe_num(self.current.get("Price")) or 0.0
        mcap = self._safe_num(self.current.get("Market Cap")) or 0.0
        if self.base_revenue > 0 and mcap / self.base_revenue > 5000:
            mcap /= 1e6; w.append("Market cap normalized (÷1M)")
        shares_bbg = self._safe_num(self.current.get("Shares Out")); shares_hist = self._last(h, "Shares_Outstanding")
        self.shares = shares_bbg or shares_hist or 1.0
        if shares_bbg and shares_hist and abs(shares_bbg / shares_hist - 1) > 0.05:
            w.append(f"Shares: BBG current {shares_bbg:,.1f} vs last FY {shares_hist:,.1f} ({shares_bbg/shares_hist-1:+.1%}) — using BBG")
        # pence quotation
        if self.price_local > 0 and mcap > 0 and 70 < self.price_local * self.shares / mcap < 130:
            self.price_local /= 100; w.append("Price converted from pence to GBP")
        # FX: quote currency → fundamentals currency (Current sheet field 'FX Rate')
        self.fx = self._safe_num(self.current.get("FX Rate")) or 1.0
        if self.fx != 1.0:
            mcap *= self.fx; w.append(f"FX Rate {self.fx} applied: market cap / price converted to fundamentals currency")
        self.price = self.price_local * self.fx
        debt = self._last(h, "Total_Debt") or 0.0; lease = self._last(h, "Lease_Liab") or 0.0
        cash = self._last(h, "Cash") or 0.0; mi = self._last(h, "Minority_Interest") or 0.0
        self.market_cap, self.net_debt, self.minority = mcap, debt + lease - cash, mi
        computed_ev = mcap + self.net_debt + mi
        bbg_ev = self._safe_num(self.current.get("EV"))
        if bbg_ev and self.base_revenue > 0 and bbg_ev / self.base_revenue > 5000: bbg_ev /= 1e6
        if bbg_ev and self.fx != 1.0: bbg_ev *= self.fx
        self.ev_adjustment = 0.0; self.market_ev = computed_ev; self.ev_source = "computed"
        if bbg_ev and self.config.use_bbg_ev and bbg_ev > mcap * 0.9 and abs(bbg_ev / computed_ev - 1) < 0.30:
            self.market_ev, self.ev_source, self.ev_adjustment = bbg_ev, "BBG", bbg_ev - computed_ev
            if abs(self.ev_adjustment) > mcap * 0.01:
                w.append(f"BBG EV {bbg_ev:,.0f} used; differs from MCap+NetDebt+MI ({computed_ev:,.0f}) by {self.ev_adjustment:+,.0f} — treated as debt-like claim")
        elif bbg_ev and not self.config.use_bbg_ev:
            w.append(f"BBG EV ignored (toggle off); EV = MCap + Net Debt + MI = {computed_ev:,.0f}")

        # Consensus growth (Stage 1)
        c = [self._safe_num(self.current.get(k)) for k in ("Cons Rev FY1", "Cons Rev FY2", "Cons Rev FY3")]
        g = []
        prev = self.base_revenue
        for v in c:
            if v and prev > 0 and v / prev > 0.5: g.append(v / prev - 1); prev = v
            else: break
        self.consensus_growth = g or [0.0]
        self.has_consensus_fy3 = len(g) >= 3
        self.bbg_wacc = self._safe_num(self.current.get("BBG WACC"))
        if self.bbg_wacc and self.bbg_wacc > 1: self.bbg_wacc /= 100
        roic = self._safe_num(self.current.get("ROIC")) or 0.0
        self.current_roic = roic / 100 if abs(roic) > 1 else roic
        self.target_price_local = self._safe_num(self.current.get("Target Price"))

    def set_base(self, base: Base):
        self.base = base

    # ── EV ↔ equity (single source of truth) ─────────────────────────────
    def ev_to_equity(self, ev: float) -> float:
        return ev - self.net_debt - self.minority - self.ev_adjustment

    def price_from_ev(self, ev: float, extra_cash: float = 0.0) -> float:
        """Fair value per share in QUOTE currency."""
        return (self.ev_to_equity(ev) + extra_cash) / self.shares / self.fx if self.shares else 0.0

    # ── Stage growth list for the reverse DCF ────────────────────────────
    def _growth_list(self, ig: float) -> List[float]:
        c = self.config; cons = self.consensus_growth[:c.consensus_years]
        cons = cons + [cons[-1]] * (c.consensus_years - len(cons))
        fade = [ig + (c.terminal_growth - ig) * (i + 1) / c.fade_years for i in range(c.fade_years)]
        return cons + [ig] * c.implied_years + fade

    def ev_from_growth(self, ig: float) -> Dict[str, float]:
        """4-stage DCF on the normalized base with constant margin. Returns ev, pv_explicit, pv_tv."""
        w, tg = self.config.wacc, self.config.terminal_growth
        if w <= tg: return {"ev": np.inf, "pv_explicit": np.inf, "pv_tv": np.inf}
        rev = self.base.revenue; pv = 0.0
        for t, g in enumerate(self._growth_list(ig), start=1):
            rev_new = rev * (1 + g); f = self.base.fcff(rev_new, d_rev=rev_new - rev); rev = rev_new
            pv += f / (1 + w) ** t
        n = len(self._growth_list(ig))
        f_tv = self.base.fcff(rev * (1 + tg), d_rev=rev * tg)
        pv_tv = f_tv / (w - tg) / (1 + w) ** n
        return {"ev": pv + pv_tv, "pv_explicit": pv, "pv_tv": pv_tv}

    def solve_implied_growth(self, lo: float = -0.30, hi: float = 0.80) -> Dict[str, object]:
        """Bisection for Stage-2 growth such that DCF EV = market EV."""
        out = {"growth": np.nan, "reliable": False, "reason": ""}
        if self.market_ev <= 0: out["reason"] = "EV ≤ 0"; return out
        if self.base.fcff() <= 0:
            out["reason"] = f"Base FCFF {self.base.fcff():,.0f} ≤ 0 at margin {self.base.ebit_margin:.1%} — raise the base margin or use the path tab"
            return out
        if self.config.wacc - self.config.terminal_growth < 0.02:
            out["reason"] = "WACC − Tg < 2pp"; return out
        f = lambda g: self.ev_from_growth(g)["ev"] - self.market_ev
        if f(lo) > 0: out.update(growth=lo, reason="Price below even −30% p.a. decline — check inputs"); return out
        if f(hi) < 0: out.update(growth=hi, reason="No growth ≤ 80% p.a. justifies the price — base FCFF too small vs EV"); return out
        for _ in range(100):
            mid = (lo + hi) / 2
            if f(mid) > 0: hi = mid
            else: lo = mid
            if hi - lo < 1e-6: break
        out.update(growth=(lo + hi) / 2, reliable=True)
        return out

    def sensitivity_implied(self, waccs, tgs) -> pd.DataFrame:
        rows = {}
        w0, t0 = self.config.wacc, self.config.terminal_growth
        for w in waccs:
            row = []
            for t in tgs:
                self.config.wacc, self.config.terminal_growth = w, t
                row.append(self.solve_implied_growth()["growth"])
            rows[f"{w:.1%}"] = row
        self.config.wacc, self.config.terminal_growth = w0, t0
        return pd.DataFrame(rows, index=[f"Tg {t:.1%}" for t in tgs]).T

    # ── Forward: value an explicit path ──────────────────────────────────
    def path_defaults(self, ig: float, n: int) -> pd.DataFrame:
        """Market-implied path (so the forward tab starts AT the price): growth = Stage 1/2 list, margin = base."""
        gl = self._growth_list(ig)[:n]; b = self.base
        return pd.DataFrame({"Growth": gl, "EBIT Margin": [b.ebit_margin] * n, "CapEx/Rev": [b.capex_pct] * n,
                             "D&A/Rev": [b.da_pct] * n, "SBC/Rev": [b.sbc_pct] * n, "Tax": [b.tax_rate] * n},
                            index=[f"Y{i}" for i in range(1, n + 1)])

    def value_path(self, path: pd.DataFrame, growth_scale: float = 1.0, margin_shift: float = 0.0) -> Dict[str, object]:
        """path: rows Y1..Yn with Growth, EBIT Margin, CapEx/Rev, D&A/Rev, SBC/Rev, Tax (fractions).
        After Yn: growth fades linearly to Tg over fade_years, ratios held. Returns EV, fair price (quote ccy), table."""
        w, tg, nf = self.config.wacc, self.config.terminal_growth, self.config.fade_years
        if w <= tg: raise ValueError("WACC must exceed terminal growth")
        rows = []; rev = self.base.revenue; pv = 0.0; t = 0
        def step(g, m, cx, da, sbc, tax, phase):
            nonlocal rev, pv, t
            t += 1; rev_new = rev * (1 + g); ebit = rev_new * m
            nopat = ebit - max(ebit, 0) * tax
            f = nopat + rev_new * (da - cx - sbc) - self.base.nwc_pct * (rev_new - rev)
            pv += f / (1 + w) ** t; rev = rev_new
            rows.append({"Year": t, "Phase": phase, "Revenue": rev, "Growth": g, "EBIT Margin": m, "EBIT": ebit,
                         "EBITDA": ebit + rev * da, "FCFF": f, "PV": f / (1 + w) ** t})
        for _, r in path.iterrows():
            g = tg + (float(r["Growth"]) - tg) * growth_scale
            step(g, float(r["EBIT Margin"]) + margin_shift, float(r["CapEx/Rev"]), float(r["D&A/Rev"]), float(r["SBC/Rev"]), float(r["Tax"]), "Path")
        last = path.iloc[-1]; g_last = tg + (float(last["Growth"]) - tg) * growth_scale; m_last = float(last["EBIT Margin"]) + margin_shift
        for i in range(nf):
            g = g_last + (tg - g_last) * (i + 1) / nf
            step(g, m_last, float(last["CapEx/Rev"]), float(last["D&A/Rev"]), float(last["SBC/Rev"]), float(last["Tax"]), "Fade")
        f_tv = rows[-1]["FCFF"] * (1 + tg); pv_tv = f_tv / (w - tg) / (1 + w) ** t
        ev = pv + pv_tv; tbl = pd.DataFrame(rows).set_index("Year")
        return {"ev": ev, "pv_explicit": pv, "pv_tv": pv_tv, "tv_pct": pv_tv / ev if ev else np.nan,
                "price": self.price_from_ev(ev), "upside": self.price_from_ev(ev) / self.price_local - 1 if self.price_local else np.nan,
                "table": tbl, "ev_ebitda_yn": ev / tbl["EBITDA"].iloc[len(path) - 1] if tbl["EBITDA"].iloc[len(path) - 1] > 0 else np.nan}

    def roll_forward(self, path: pd.DataFrame, years: int, **kw) -> List[float]:
        """Fair value per share (quote ccy) at end of year 0..years if the path plays out (FCFF accumulates as cash)."""
        res = self.value_path(path, **kw); tbl = res["table"]; w, tg = self.config.wacc, self.config.terminal_growth
        f = tbl["FCFF"].to_numpy(); n = len(f); out = []
        for t0 in range(years + 1):
            t = np.arange(1, n + 1) - t0; m = t > 0
            pv = (f[m] / (1 + w) ** t[m]).sum(); pv_tv = f[-1] * (1 + tg) / (w - tg) / (1 + w) ** (n - t0)
            out.append(self.price_from_ev(pv + pv_tv, extra_cash=f[:t0].sum()))
        return out

    # ── Context ──────────────────────────────────────────────────────────
    def plausibility(self, ig: float) -> List[dict]:
        rv = self.hist["Revenue"].dropna() if "Revenue" in self.hist else pd.Series(dtype=float); n = len(rv)
        c5 = (rv.iloc[-1] / rv.iloc[-6]) ** 0.2 - 1 if n >= 6 and rv.iloc[-6] > 0 else np.nan
        c3 = (rv.iloc[-1] / rv.iloc[-4]) ** (1 / 3) - 1 if n >= 4 and rv.iloc[-4] > 0 else np.nan
        cons = np.mean(self.consensus_growth) if self.consensus_growth else np.nan
        out = []
        for name, hist in [("5Y revenue CAGR", c5), ("3Y revenue CAGR", c3), ("Consensus FY1-FY2", cons)]:
            if np.isnan(hist): out.append({"check": name, "hist": np.nan, "flag": "⚪"}); continue
            flag = "🟢" if ig <= hist + 0.01 else "🟡" if ig <= max(hist * 1.5, hist + 0.03) else "🔴"
            out.append({"check": name, "hist": hist, "flag": flag})
        return out

    def quality(self) -> Dict[str, float]:
        h = self.hist; q = {}
        if {"EBIT", "Book_Equity", "Total_Debt", "Cash"} <= set(h.columns):
            ic = h["Book_Equity"] + h["Total_Debt"] - h["Cash"]
            roic = (h["EBIT"] * (1 - self.base.tax_rate) / ic).replace([np.inf, -np.inf], np.nan).dropna()
            q["ROIC median"] = float(roic.median()) if len(roic) else np.nan
            q["ROIC std"] = float(roic.std()) if len(roic) > 1 else np.nan
        q["EBIT margin std"] = float(self.margin_hist.std()) if len(self.margin_hist) > 1 else np.nan
        if "Revenue" in h and len(h["Revenue"].dropna()) > 2: q["Revenue growth std"] = float(h["Revenue"].pct_change().dropna().std())
        if {"CFO", "Net_Income"} <= set(h.columns):
            r = (h["CFO"] / h["Net_Income"]).replace([np.inf, -np.inf], np.nan).dropna(); q["CFO / NI median"] = float(r.median()) if len(r) else np.nan
        if {"DPS", "Diluted_EPS"} <= set(h.columns):
            p = (h["DPS"] / h["Diluted_EPS"]).replace([np.inf, -np.inf], np.nan).dropna(); q["Payout median"] = float(p.median()) if len(p) else np.nan
        d = self._last(h, "Total_Debt") or 0.0; e = self._last(h, "EBITDA") or np.nan
        q["Net debt / EBITDA"] = self.net_debt / e if e and e > 0 else np.nan
        q["ROIC − WACC"] = self.current_roic - self.config.wacc
        return q

    def historical_multiples(self) -> pd.DataFrame:
        h = self.hist; out = []
        for i in range(len(h)):
            g = lambda c: h[c].iloc[i] if c in h and pd.notna(h[c].iloc[i]) else np.nan
            p, sh, eps, ebitda, rev = g("Price_YE"), g("Shares_Outstanding"), g("Diluted_EPS"), g("EBITDA"), g("Revenue")
            mcap = p * sh if sh > 0 else np.nan
            ev = mcap + (g("Total_Debt") if not np.isnan(g("Total_Debt")) else 0) + (g("Lease_Liab") if not np.isnan(g("Lease_Liab")) else 0) \
                 - (g("Cash") if not np.isnan(g("Cash")) else 0) + (g("Minority_Interest") if not np.isnan(g("Minority_Interest")) else 0)
            out.append({"Year": h.index[i].year, "P/E": p / eps if eps > 0 else np.nan, "EV/EBITDA": ev / ebitda if ebitda > 0 else np.nan,
                        "P/Sales": mcap / rev if rev > 0 else np.nan, "EBIT margin": g("EBIT") / rev if rev > 0 else np.nan})
        return pd.DataFrame(out).set_index("Year")

    # ── Helpers ──────────────────────────────────────────────────────────
    def _last(self, df, col):
        if col not in df: return None
        s = df[col].dropna(); return float(s.iloc[-1]) if len(s) else None

    def _ratio(self, df, num, den, default, absolute=False):
        if num not in df or den not in df: return default
        r = (df[num].abs() if absolute else df[num]) / df[den]
        v = r.replace([np.inf, -np.inf], np.nan).dropna(); v = v[(v > -1) & (v < 1)]
        return float(v.median()) if len(v) else default

    @staticmethod
    def _safe_num(val, default=None):
        if val is None: return default
        try:
            f = float(val); return f if not np.isnan(f) else default
        except (TypeError, ValueError):
            return default
