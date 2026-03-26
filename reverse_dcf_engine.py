"""
CORE DCF Engine v2 — 4-Stage Reverse DCF + C-Scores + Quality + Return Decomposition
Based on CORE (Zeltner & Co) architecture.

Stages:
  S1 (Y1-2):   Consensus FCF growth
  S2 (Y3-10):  Implied growth (SOLVED)
  S3 (Y11-20): Linear fade to terminal
  S4:          Gordon Growth perpetuity
"""
import pandas as pd, numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

@dataclass
class DCFConfig:
    wacc: float = 0.08
    terminal_growth: float = 0.015
    fade_growth: float = 0.025
    consensus_years: int = 2
    implied_years: int = 8
    fade_years: int = 10

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
        self.ltm_data = ltm_data or {}; self._warnings = []; self._prepare()

    @classmethod
    def from_excel(cls, path):
        xl = pd.ExcelFile(path)
        raw = pd.read_excel(xl, "Fundamentals", header=None)
        ticker_raw = raw.iloc[1, 1] if len(raw) > 1 and pd.notna(raw.iloc[1, 1]) else ""
        ticker = str(ticker_raw).replace(" Equity", "").replace(" Index", "").strip()

        hc_header_row = None
        for i in range(len(raw)):
            if "HARD COPY" in str(raw.iloc[i, 0]).upper():
                hc_header_row = i + 1; break
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

        return cls(hist, current, config, ticker, ltm_data)

    def _prepare(self):
        h = self.hist; self._warnings = []
        fy_rev = self._last(h,"Revenue") or 1; fy_ebit = self._last(h,"EBIT") or 0
        ltm_rev = self.ltm_data.get("Revenue")
        if ltm_rev and fy_rev > 0 and 0.5 < ltm_rev/fy_rev < 1.5:
            self.base_revenue = ltm_rev; self.base_ebit = self.ltm_data.get("EBIT", fy_ebit)
            self._warnings.append(f"INFO: Using LTM Revenue ({ltm_rev:,.0f})")
        else:
            self.base_revenue = fy_rev; self.base_ebit = fy_ebit

        self.ebit_margin = self.base_ebit / self.base_revenue if self.base_revenue else 0.15
        self.da_pct = self._ratio(h,"DA","Revenue",0.03)
        self.capex_pct = self._ratio(h,"CapEx","Revenue",0.05,absolute=True)
        self.sbc_pct = self._ratio(h,"SBC","Revenue",0.0,absolute=True)

        nwc_s = h.get("Change_NWC",pd.Series(dtype=float)).dropna()
        rev_s = h.get("Revenue",pd.Series(dtype=float)).dropna()
        if len(nwc_s)>2 and len(rev_s)>=len(nwc_s):
            nwc_pct = nwc_s / rev_s.iloc[:len(nwc_s)].values
            self.nwc_pct = 0.0 if nwc_pct.std()>0.05 or (nwc_pct.max()>0 and nwc_pct.min()<0) else float(nwc_pct.median())
        else: self.nwc_pct = 0.0

        tax_rates = []
        if "Tax_Expense" in h and "EBIT" in h:
            for i in range(len(h)):
                t=h["Tax_Expense"].iloc[i]; e=h["EBIT"].iloc[i]
                if pd.notna(t) and pd.notna(e) and e!=0:
                    r=t/e
                    if 0<r<0.50: tax_rates.append(r)
        self.tax_rate = float(np.median(tax_rates)) if tax_rates else 0.20

        self.base_fcff = self._compute_fcff(self.base_revenue)
        shares = self._last(h,"Shares_Outstanding") or self._safe_num(self.current.get("Shares Out")) or 1
        self.shares = shares; self.base_fcff_per_share = self.base_fcff/shares if shares else 0

        self.price = self._safe_num(self.current.get("Price")) or 0
        mcap = self._safe_num(self.current.get("Market Cap")) or 0
        if self.base_revenue>0 and mcap>0 and mcap/self.base_revenue>5000:
            mcap /= 1e6; self._warnings.append("INFO: Market Cap normalized (÷1M)")

        debt = self._last(h,"Total_Debt") or 0; lease = self._last(h,"Lease_Liab") or 0
        cash = self._last(h,"Cash") or 0; mi = self._last(h,"Minority_Interest") or 0
        self.market_cap = mcap; self.net_debt = debt+lease-cash; self.minority = mi
        self.market_ev = mcap + self.net_debt + mi

        cons_fy1 = self._safe_num(self.current.get("Cons Rev FY1"))
        cons_fy2 = self._safe_num(self.current.get("Cons Rev FY2"))
        # Sanity check: if consensus < 50% of base revenue, it's likely a partial year → ignore
        if cons_fy1 and self.base_revenue > 0 and cons_fy1 / self.base_revenue < 0.5:
            self._warnings.append(f"WARNING: Cons Rev FY1 ({cons_fy1:,.0f}) looks like partial year vs base ({self.base_revenue:,.0f}) — using 0% consensus growth")
            cons_fy1 = None
        self.consensus_growth_fy1 = cons_fy1/self.base_revenue-1 if cons_fy1 and self.base_revenue>0 else 0.0
        self.consensus_growth_fy2 = cons_fy2/cons_fy1-1 if cons_fy2 and cons_fy1 and cons_fy1>0 else self.consensus_growth_fy1

        bbg_w = self._safe_num(self.current.get("BBG WACC"))
        if bbg_w and bbg_w>1: bbg_w /= 100
        self.bbg_wacc = bbg_w
        roic = self._safe_num(self.current.get("ROIC"))
        if roic and roic>1: roic /= 100
        self.current_roic = roic or 0

    def _compute_fcff(self, revenue, margin_override=None):
        m = margin_override if margin_override is not None else self.ebit_margin
        nopat = revenue * m * (1 - self.tax_rate)
        return nopat + revenue*self.da_pct - revenue*self.capex_pct - revenue*abs(self.nwc_pct) - revenue*self.sbc_pct

    def _ev_from_fcf_growth(self, ig):
        w = self.config.wacc; tg = self.config.terminal_growth
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
        lo,hi=-0.30,0.80
        for _ in range(max_iter):
            mid=(lo+hi)/2; ev=self._ev_from_fcf_growth(mid)
            if abs(ev-target)/max(target,1)<tol: return mid
            if ev>target: hi=mid
            else: lo=mid
        return (lo+hi)/2

    def scenario_analysis(self, base_g, offsets=(-0.03,0,0.03)):
        r={}
        for lbl,off in zip(["Bear","Base","Bull"],offsets):
            g=base_g+off; ev=self._ev_from_fcf_growth(g)
            eq=ev-self.net_debt-self.minority; fp=eq/self.shares if self.shares else 0
            up=fp/self.price-1 if self.price else 0
            r[lbl]={"growth_rate":g,"ev":ev,"fair_price":fp,"upside":up}
        return r

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

    def compute_c_score(self):
        h=self.hist; cs=CScore(details={})
        def _tr(s,n=3):
            s=s.dropna(); return len(s)>=n and s.iloc[-1]>s.iloc[0]
        def _td(s,n=3):
            s=s.dropna(); return len(s)>=n and s.iloc[-1]<s.iloc[0]
        if "CFO" in h and "Net_Income" in h:
            r=(h["CFO"]/h["Net_Income"]).replace([np.inf,-np.inf],np.nan)
            if _td(r): cs.cfo_ni=1; cs.details["CFO/NI"]="Declining"
            else: cs.details["CFO/NI"]="OK"
        if "Accounts_Receivable" in h and "Revenue" in h:
            d=h["Accounts_Receivable"]/h["Revenue"]*365
            if _tr(d): cs.dso=1; cs.details["DSO"]="Increasing"
            else: cs.details["DSO"]="OK"
        if "Inventory" in h and "Revenue" in h:
            d=h["Inventory"]/h["Revenue"]*365
            if _tr(d): cs.dsi=1; cs.details["DSI"]="Increasing"
            else: cs.details["DSI"]="OK"
        if "DA" in h and "Total_Assets" in h:
            d=h["DA"]/h["Total_Assets"]
            if _td(d): cs.depr_intensity=1; cs.details["Depr"]="Declining"
            else: cs.details["Depr"]="OK"
        if "Total_Assets" in h and "Revenue" in h:
            ta=h["Total_Assets"].dropna(); rv=h["Revenue"].dropna()
            if len(ta)>=3 and len(rv)>=3:
                ag=ta.iloc[-1]/ta.iloc[-3]-1; rg=rv.iloc[-1]/rv.iloc[-3]-1
                if ag>rg+0.05: cs.asset_growth=1; cs.details["Assets"]=f"Asset gr ({ag:.1%}) > Rev gr ({rg:.1%})"
                else: cs.details["Assets"]="OK"
        cs.total=cs.cfo_ni+cs.dso+cs.dsi+cs.depr_intensity+cs.asset_growth
        return cs

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
            m=(h["EBIT"]/h["Revenue"]).dropna()
            qp.margin_stability=float(m.std()) if len(m)>1 else 0
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
        qp.c_score=self.compute_c_score()
        sc=0
        if qp.roic_median>0.15: sc+=2
        elif qp.roic_median>0.10: sc+=1
        if qp.margin_stability<0.03: sc+=1
        if qp.fcf_conversion>1.0: sc+=1
        if qp.debt_ebitda<2.0: sc+=1
        if qp.c_score.total<=1: sc+=1
        sc-=qp.c_score.total
        qp.grade="A" if sc>=5 else "B" if sc>=3 else "C" if sc>=1 else "D"
        return qp

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
        bb=1-(sh.iloc[-1]/sh.iloc[0])**(1/n) if len(sh)>=2 and sh.iloc[0]>0 else 0
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

    def plausibility_checks(self, ig):
        h=self.hist; rv=h.get("Revenue",pd.Series(dtype=float)).dropna()
        n=len(rv)
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

    def run(self):
        ig=self.solve_implied_growth(); sc=self.scenario_analysis(ig)
        tv=self.tv_decomposition(ig); q=self.compute_quality()
        hm=self.historical_multiples(); rd=self.return_decomposition()
        pl,c5,c3,mx=self.plausibility_checks(ig)
        return {"ticker":self.ticker,"price":self.price,"market_ev":self.market_ev,
            "market_cap":self.market_cap,"implied_growth":ig,"wacc":self.config.wacc,
            "terminal_growth":self.config.terminal_growth,"base_fcff":self.base_fcff,
            "base_fcff_per_share":self.base_fcff_per_share,"ebit_margin":self.ebit_margin,
            "consensus_fy1":self.consensus_growth_fy1,"consensus_fy2":self.consensus_growth_fy2,
            "scenarios":sc,"tv_decomposition":tv,"quality":q,"historical_multiples":hm,
            "return_decomposition":rd,"plausibility":pl,"cagr_5y":c5,"cagr_3y":c3,
            "max_growth":mx,"roic_spread":self.current_roic-self.config.wacc,
            "roic":self.current_roic,"warnings":self._warnings}

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
