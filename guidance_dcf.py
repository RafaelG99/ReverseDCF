"""
Guidance DCF — segment-driven DCF on management guidance (e.g. CMD "vision" charts).

Valuation object differs from the historical Reverse DCF: instead of solving for the FCF growth
that justifies the price on a historical base, we take a management plan (segments Base → Target),
derive the cost structure the plan implies (contribution margin + fixed cost base), value it, and
solve for the *plan fulfilment* (0..1+) that the current price discounts.

Pure numpy; no Streamlit imports. Used by app.py tab "Guidance DCF" and reusable in notebooks.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np
import pandas as pd


@dataclass
class Segment:
    name: str
    base: float                     # revenue in base year
    target: float                   # revenue in target year (100% fulfilment)
    scalable: bool = False          # does the fulfilment lever apply to this segment?
    ramp: Optional[List[float]] = None  # fraction of target per plan year (len = target-base), else geometric/linear


@dataclass
class GuidanceInputs:
    base_year: int
    target_year: int
    segments: List[Segment]
    ebitda_base: float              # guidance EBITDA in base year
    ebitda_target: float            # guidance EBITDA in target year
    # cash-flow bridge
    tax_rate: float = 0.20
    da_base: float = 0.0            # D&A in base year (absolute)
    da_lt_pct: float = 0.05         # long-run D&A / revenue
    capex_pct: float = 0.04
    sbc_pct: float = 0.025
    nwc_incr_pct: float = 0.15      # NWC investment as % of incremental revenue
    opex_growth: float = 0.015      # growth of fixed cost base
    # post-plan fade
    g_post: float = 0.12            # growth in first fade year
    fade_years: int = 10
    margin_fade_to: float = 0.35    # EBITDA margin at end of fade
    # valuation
    wacc: float = 0.075
    tg: float = 0.015
    net_cash: float = 0.0           # in guidance currency (= -(net debt + minorities + EV adj.))
    shares: float = 1.0
    fx: float = 1.0                 # guidance currency → price currency (e.g. EURCHF)
    price: float = 0.0              # current price in price currency

    # ── derived cost structure ────────────────────────────────────────────
    @property
    def n_plan(self) -> int:
        return self.target_year - self.base_year

    @property
    def rev_base(self) -> float:
        return sum(s.base for s in self.segments)

    @property
    def rev_target(self) -> float:
        return sum(s.target for s in self.segments)

    @property
    def contribution_margin(self) -> float:
        d = self.rev_target - self.rev_base
        return (self.ebitda_target - self.ebitda_base) / d if d > 0 else 0.3

    @property
    def opex_base(self) -> float:
        return self.contribution_margin * self.rev_base - self.ebitda_base


# ── paths ─────────────────────────────────────────────────────────────────────
def segment_paths(g: GuidanceInputs, fulfilment: float = 1.0, scale_map=None) -> pd.DataFrame:
    """Revenue per segment for plan years base+1 .. target.
    fulfilment scales targets of scalable segments; scale_map {name: factor} scales single segments (scenarios).
    Cost structure (contribution margin, opex) stays anchored on the full plan."""
    n = g.n_plan; t = np.arange(1, n + 1); scale_map = scale_map or {}
    out = {}
    for s in g.segments:
        tgt = s.target * (fulfilment if s.scalable else 1.0) * scale_map.get(s.name, 1.0)
        if s.ramp is not None and len(s.ramp) == n:
            path = tgt * np.asarray(s.ramp, dtype=float)
        elif s.base > 0 and tgt > 0:
            path = s.base * (tgt / s.base) ** (t / n)               # geometric
        else:
            path = s.base + (tgt - s.base) * t / n                   # linear (from/to zero)
        out[s.name] = path
    df = pd.DataFrame(out, index=[g.base_year + i for i in t])
    df["Revenue"] = df.sum(axis=1)
    return df


def fcff_path(g: GuidanceInputs, fulfilment: float = 1.0, scale_map=None) -> pd.DataFrame:
    """Full explicit path (plan + fade) with EBITDA, FCFF. Index = years."""
    seg = segment_paths(g, fulfilment, scale_map)
    n = g.n_plan
    rev_plan = seg["Revenue"].to_numpy()
    opex = g.opex_base * (1 + g.opex_growth) ** np.arange(1, n + 1)
    ebitda_plan = g.contribution_margin * rev_plan - opex
    da_plan = np.linspace(g.da_base, g.da_lt_pct * rev_plan[-1], n + 1)[1:]

    gf = np.linspace(g.g_post, g.tg, g.fade_years)
    rev_fade = rev_plan[-1] * np.cumprod(1 + gf)
    m_end = ebitda_plan[-1] / rev_plan[-1] if rev_plan[-1] > 0 else 0
    m_fade = np.linspace(m_end, g.margin_fade_to, g.fade_years + 1)[1:]
    ebitda_fade = rev_fade * m_fade
    da_fade = g.da_lt_pct * rev_fade

    rev = np.r_[rev_plan, rev_fade]; ebitda = np.r_[ebitda_plan, ebitda_fade]; da = np.r_[da_plan, da_fade]
    rev_prev = np.r_[g.rev_base, rev[:-1]]
    ebit = ebitda - da
    tax = -np.maximum(ebit, 0) * g.tax_rate
    fcff = ebit + tax + da - g.capex_pct * rev - g.sbc_pct * rev - g.nwc_incr_pct * (rev - rev_prev)
    years = np.arange(g.base_year + 1, g.base_year + 1 + len(rev))
    df = pd.DataFrame({"Revenue": rev, "Growth": rev / rev_prev - 1, "EBITDA": ebitda,
                       "EBITDA Margin": np.where(rev > 0, ebitda / np.where(rev > 0, rev, 1), 0),
                       "D&A": da, "EBIT": ebit, "FCFF": fcff,
                       "Phase": ["Plan"] * n + ["Fade"] * g.fade_years}, index=years)
    for c in seg.columns:
        if c != "Revenue":
            df[c] = seg[c].reindex(years)
    return df


# ── valuation ─────────────────────────────────────────────────────────────────
def value(g: GuidanceInputs, fulfilment: float = 1.0, t0: int = 0, wacc: Optional[float] = None, scale_map=None):
    """EV (guidance ccy) and fair value / share (price ccy) at time t0 (0 = today, 1 = end of base_year+1, ...).
    Returns dict(ev, equity, price, tv_pct, cash_t0)."""
    w = g.wacc if wacc is None else wacc
    if w <= g.tg:
        return {"ev": np.nan, "equity": np.nan, "price": np.nan, "tv_pct": np.nan, "cash": np.nan}
    df = fcff_path(g, fulfilment, scale_map); f = df["FCFF"].to_numpy(); n = len(f)
    t = np.arange(1, n + 1) - t0
    mask = t > 0
    pv = (f[mask] / (1 + w) ** t[mask]).sum()
    tv = f[-1] * (1 + g.tg) / (w - g.tg)
    pv_tv = tv / (1 + w) ** (n - t0)
    ev = pv + pv_tv
    cash = g.net_cash + f[:t0].sum()                 # FCFF accumulates as cash (no dividends)
    eq = ev + cash
    return {"ev": ev, "equity": eq, "price": eq / g.shares * g.fx if g.shares else np.nan,
            "tv_pct": pv_tv / ev if ev else np.nan, "cash": cash}


def implied_fulfilment(g: GuidanceInputs, wacc: Optional[float] = None, lo: float = 0.0, hi: float = 3.0):
    """Plan fulfilment (scale on scalable segments) at which fair value = price. None if not bracketed."""
    if not g.price or not any(s.scalable for s in g.segments):
        return None
    f = lambda s: value(g, s, wacc=wacc)["price"] - g.price
    flo, fhi = f(lo), f(hi)
    if np.isnan(flo) or np.isnan(fhi) or flo * fhi > 0:
        return None
    for _ in range(80):                              # bisection, monotone in s
        mid = (lo + hi) / 2; fm = f(mid)
        if abs(fm) < 1e-6: return mid
        if fm * flo < 0: hi = mid
        else: lo, flo = mid, fm
    return (lo + hi) / 2


def sensitivity(g: GuidanceInputs, waccs, fulfilments) -> pd.DataFrame:
    rows = {}
    for w in waccs:
        rows[f"{w:.1%}"] = [value(g, s, wacc=w)["price"] for s in fulfilments]
    return pd.DataFrame(rows, index=[f"{s:.0%}" for s in fulfilments]).T


def scenarios(g: GuidanceInputs, wacc: Optional[float] = None) -> pd.DataFrame:
    """Plan / scalable segments only partially delivered / stall. Returns fair value per scenario + implied P(plan)."""
    def with_scale(scale_map):
        return value(g, 1.0, wacc=wacc, scale_map=scale_map)["price"]
    scal = [s.name for s in g.segments if s.scalable]
    # "Downside": the most speculative scalable segment (largest target with base == 0, else largest target) at zero
    spec = sorted([s for s in g.segments if s.scalable], key=lambda s: (s.base > 0, -s.target))
    downside = {spec[0].name: 0.0} if spec else {}
    stall = {**downside, **{n: 0.5 for n in scal if n not in downside}}
    rows = [("Plan", with_scale({})),
            (f"ohne {spec[0].name}" if spec else "Downside", with_scale(downside)),
            ("Stall", with_scale(stall))]
    df = pd.DataFrame(rows, columns=["Szenario", "Fair Value"]).set_index("Szenario")
    df["Upside"] = df["Fair Value"] / g.price - 1 if g.price else np.nan
    return df


def implied_probability(df_scen: pd.DataFrame, price: float):
    """P(plan) such that p*Plan + (1-p)*Downside = price."""
    hi, lo = df_scen["Fair Value"].iloc[0], df_scen["Fair Value"].iloc[1]
    return (price - lo) / (hi - lo) if hi != lo else np.nan


# ── Excel loader for optional 'Guidance' sheet ────────────────────────────────
def read_guidance_sheet(xl: pd.ExcelFile):
    """Parse optional sheet 'Guidance':
        key/value block (Base Year, Target Year, EBITDA Base, EBITDA Target, FX, and any GuidanceInputs field)
        then a table with header row: Segment | Base | Target | Scalable | Ramp (comma-separated % of target)
    Returns dict(params=..., segments=[Segment]) or None."""
    if "Guidance" not in xl.sheet_names:
        return None
    raw = pd.read_excel(xl, "Guidance", header=None)
    params, segs, hdr = {}, [], None
    for i in range(len(raw)):
        k = str(raw.iloc[i, 0]).strip()
        if k.lower() == "segment":
            hdr = i; break
        v = raw.iloc[i, 1] if raw.shape[1] > 1 else None
        if k and k != "nan" and pd.notna(v):
            try: params[k] = float(v)
            except (TypeError, ValueError): params[k] = v
    if hdr is not None:
        tbl = pd.read_excel(xl, "Guidance", header=hdr).dropna(subset=["Segment"])
        for _, r in tbl.iterrows():
            if pd.isna(r.get("Base")) or pd.isna(r.get("Target")):
                continue                                  # notes / blank rows
            ramp = None
            rv = r.get("Ramp")
            if pd.notna(rv) and str(rv).strip():
                ramp = [float(x) / 100 for x in str(rv).replace(";", ",").split(",")]
            sc = r.get("Scalable")
            sc = (float(sc) != 0) if isinstance(sc, (int, float)) and not pd.isna(sc) else str(sc).strip().lower() in ("1", "true", "yes", "ja", "x", "y")
            segs.append(Segment(str(r["Segment"]), float(r["Base"]), float(r["Target"]), sc, ramp))
    return {"params": params, "segments": segs}


def default_segments_from_model(base_revenue: float, base_year: int, cagr: float = 0.10):
    """Fallback when no guidance sheet: one segment, historical-style growth."""
    return [Segment("Total", base_revenue, base_revenue * (1 + cagr) ** 5, True, None)], base_year, base_year + 5
