"""CORE DCF Dashboard — 4-Stage Reverse DCF + Forward DCF"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from core_dcf_engine import CoreDCF, DCFConfig
import tempfile

st.set_page_config(page_title="CORE DCF", page_icon="📊", layout="wide")
C_TEAL,C_CORAL,C_AMBER,C_GREEN,C_RED = "#003850","#F26B43","#FBAE40","#2ECC71","#E74C3C"

# ── Upload ────────────────────────────────────────────────────────────────────
st.sidebar.title("📊 CORE DCF")
uploaded = st.sidebar.file_uploader("Upload core_dcf_template.xlsx", type=["xlsx"])
model = None
if uploaded:
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(uploaded.read()); tmp_path = tmp.name
    try:
        model = CoreDCF.from_excel(tmp_path)
        st.sidebar.success(f"Loaded: {model.ticker}")
    except Exception as e:
        st.sidebar.error(f"Error: {e}")
if model is None:
    st.title("CORE DCF Engine")
    st.info("Upload your core_dcf_template.xlsx (with HC data) to start.")
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
bbg_w = model.bbg_wacc
if bbg_w and 0.01 < bbg_w < 0.25:
    wacc = st.sidebar.number_input("WACC (%)", value=round(bbg_w*100,2), step=0.25, format="%.2f",
        help="Default: Bloomberg WACC") / 100
    st.sidebar.caption(f"BBG WACC: {bbg_w:.2%}")
else:
    wacc = st.sidebar.number_input("WACC (%)", value=8.0, step=0.25, format="%.2f") / 100

tg = st.sidebar.slider("Terminal Growth (%)", 0.0, 3.0, min(round(model.config.terminal_growth*100,1),1.5), 0.1) / 100
proj_years = st.sidebar.slider("Implied Period (Y)", 5, 15, model.config.implied_years)

model.config.wacc = wacc
model.config.terminal_growth = tg
model.config.implied_years = proj_years
model._prepare()
r = model.run()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["🔍 Reverse DCF", "📊 Quality & Multiples", "📈 Return Decomposition", "🎯 Forward DCF"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: REVERSE DCF
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    ig = r["implied_growth"]; c5 = r["cagr_5y"]; c3 = r["cagr_3y"]; mx = r["max_growth"]
    tv_pct = r["tv_decomposition"]["tv_pct"]
    spread = wacc - tg; roic_sp = r["roic_spread"]
    red_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🔴")

    # Verdict
    if spread < 0.02:
        verdict,v_color = "⚠ RESULT UNRELIABLE", C_RED
        v_action = f"WACC ({wacc:.2%}) too close to Tg ({tg:.2%}). Increase WACC or lower Tg."
    elif ig < -0.10:
        ev0 = model._ev_from_fcf_growth(0.0)
        ratio = ev0/model.market_ev if model.market_ev else 0
        verdict,v_color = "CHECK INPUTS", C_RED
        v_action = f"Even 0% growth gives {ratio:.1f}x market EV. WACC ({wacc:.2%}) likely too low — try 7-8%." if ratio>2 else "Extreme decline implied. Check data."
    elif ig < -0.03 and c5 > 0.02:
        verdict,v_color = "POTENTIALLY UNDERVALUED", C_GREEN
        v_action = f"Market prices in {ig:.1%} decline, but historically +{c5:.1%} p.a. Could be cheap if stable."
    elif -0.03 <= ig <= 0.03:
        if c5 > 0.05:
            verdict,v_color = "POTENTIALLY UNDERVALUED", C_GREEN
            v_action = f"Market implies flat ({ig:.1%}), but historical CAGR was {c5:.1%}. Modest expectations."
        else:
            verdict,v_color = "FAIRLY VALUED", C_AMBER
            v_action = f"Implied {ig:.1%} roughly in line with historical {c5:.1%}."
    elif red_flags >= 3:
        verdict,v_color = "OVERPRICED", C_RED
        v_action = "Market expects growth well beyond history."
    elif red_flags >= 2:
        verdict,v_color = "LIKELY OVERPRICED", C_CORAL
        v_action = "Expectations stretched vs history."
    elif red_flags == 0 and roic_sp > 0:
        verdict,v_color = "FAIRLY VALUED", C_GREEN
        v_action = "Expectations achievable based on history."
    else:
        verdict,v_color = "FAIR VALUE RANGE", C_AMBER
        v_action = "Mixed signals."

    st.title(f"{r['ticker']}")
    st.markdown(f"""<div style="background:{v_color}15;border-left:5px solid {v_color};padding:20px 24px;border-radius:4px;margin-bottom:20px;">
        <span style="font-size:28px;font-weight:bold;color:{v_color};">{verdict}</span><br>
        <span style="font-size:18px;color:#333;">Market implies <b>{ig:.1%} p.a. FCF growth</b> (Y3-{2+proj_years}) to justify {r['price']:,.2f}.</span>
        <br><span style="font-size:15px;color:#444;"><b>So what?</b> {v_action}</span>
    </div>""", unsafe_allow_html=True)

    k1,k2,k3,k4,k5,k6 = st.columns(6)
    k1.metric("Price", f"{r['price']:,.2f}")
    k2.metric("Implied Growth", f"{ig:.1%}", delta=f"vs {c5:.1%} 5Y" if c5 else None)
    k3.metric("WACC", f"{wacc:.2%}")
    k4.metric("TV %", f"{tv_pct:.0%}")
    k5.metric("ROIC Spread", f"{roic_sp:+.1%}", delta="Creates Value" if roic_sp>0 else "Destroys", delta_color="normal" if roic_sp>0 else "inverse")
    k6.metric("Quality", r["quality"].grade, delta=f"C-Score {r['quality'].c_score.total}/5")

    st.markdown("---")
    left,right = st.columns([3,2])
    with left:
        st.subheader("Scenario Fan")
        sc=r["scenarios"]; labels=["Bear","Base","Bull"]
        prices=[sc[l]["fair_price"] for l in labels]; ups=[sc[l]["upside"] for l in labels]
        fig=go.Figure()
        fig.add_trace(go.Bar(x=labels,y=prices,marker_color=[C_RED,C_AMBER,C_GREEN],
            text=[f"{p:,.1f}<br>({u:+.0%})" for p,u in zip(prices,ups)],textposition="outside",textfont=dict(size=14,color=C_TEAL)))
        fig.add_hline(y=r["price"],line_dash="dash",line_color=C_TEAL,line_width=2,
            annotation_text=f"Current: {r['price']:,.2f}",annotation_position="bottom right")
        anr = model._safe_num(model.current.get("Target Price"))
        if anr and anr > 0:
            fig.add_hline(y=anr,line_dash="dot",line_color=C_CORAL,line_width=1,
                annotation_text=f"ANR Target: {anr:,.0f}",annotation_position="top right")
        fig.update_layout(height=400,showlegend=False,yaxis_title="Fair Price",plot_bgcolor="white",font=dict(family="Arial"))
        st.plotly_chart(fig,use_container_width=True)
        st.caption(f"4-Stage DCF: Consensus({model.config.consensus_years}Y) → Implied({proj_years}Y) → Fade({model.config.fade_years}Y) → Terminal")

    with right:
        st.subheader("TV Decomposition")
        tv=r["tv_decomposition"]
        fig2=go.Figure(go.Pie(labels=["Explicit Period","Terminal Value"],values=[tv["explicit_pct"],tv["tv_pct"]],
            marker_colors=[C_TEAL,C_CORAL],hole=0.5,textinfo="label+percent",textfont=dict(size=13)))
        fig2.update_layout(height=400,showlegend=False,font=dict(family="Arial"),
            annotations=[dict(text=f"TV<br>{tv['tv_pct']:.0%}",x=0.5,y=0.5,font_size=18,showarrow=False,font_color=C_CORAL)])
        st.plotly_chart(fig2,use_container_width=True)
        st.caption(f"{tv['explicit_years']} years explicit period. {'⚠️ TV > 60% — sensitive to assumptions.' if tv_pct > 0.6 else '✅ Healthy TV split.'}")

    st.markdown("---")
    cl,cr = st.columns(2)
    with cl:
        st.subheader("Plausibility Checks")
        for c in r["plausibility"]:
            st.write(f"{c['flag']} **{c['check']}**: implied {c['implied']} vs hist {c['historical']} ({c['ratio']})")
    with cr:
        st.subheader("Model Inputs")
        st.write(f"Base Revenue: {model.base_revenue:,.0f} · EBIT Margin: {model.ebit_margin:.1%}")
        st.write(f"FCFF: {model.base_fcff:,.0f} ({model.base_fcff/model.base_revenue:.1%} margin) · FCFF/Share: {model.base_fcff_per_share:,.2f}")
        st.write(f"D&A: {model.da_pct:.1%} · CapEx: {model.capex_pct:.1%} · SBC: {model.sbc_pct:.1%} · Tax: {model.tax_rate:.1%}")
        st.write(f"MCap: {model.market_cap:,.0f} · Net Debt: {model.net_debt:,.0f} · EV: {model.market_ev:,.0f}")
        st.write(f"Consensus FY1: {model.consensus_growth_fy1:+.1%} · FY2: {model.consensus_growth_fy2:+.1%}")

    # Sensitivity
    st.markdown("---")
    st.subheader("Sensitivity: Implied Growth (WACC × Tg)")
    w_rng=np.arange(max(0.03,wacc-0.02),wacc+0.025,0.005)
    t_rng=np.arange(max(0.005,tg-0.01),tg+0.015,0.005)
    rows=[]
    for w in w_rng:
        row={}
        for t in t_rng:
            cfg=DCFConfig(wacc=w,terminal_growth=t,implied_years=proj_years)
            m2=CoreDCF(model.hist,model.current,cfg,ticker=r["ticker"])
            row[f"Tg={t:.1%}"]=m2.solve_implied_growth()
        row["WACC"]=w; rows.append(row)
    sdf=pd.DataFrame(rows).set_index("WACC"); sdf.index=[f"{w:.1%}" for w in w_rng]
    st.dataframe(sdf.style.format("{:.1%}").background_gradient(cmap="RdYlGn",axis=None),use_container_width=True)

    if r["warnings"]:
        with st.expander(f"Data Notes ({len(r['warnings'])})"):
            for w in r["warnings"]:
                if "WARNING" in w: st.warning(w)
                else: st.info(w)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: QUALITY & MULTIPLES
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.title(f"Quality & Multiples: {r['ticker']}")

    q = r["quality"]
    ql,qr = st.columns(2)
    with ql:
        st.subheader(f"Quality Grade: {q.grade}")
        st.write(f"ROIC (median): **{q.roic_median:.1%}** ({q.roic_trend})")
        st.write(f"Margin Stability: **{q.margin_stability:.2%}** std dev")
        st.write(f"Revenue Volatility: **{q.revenue_volatility:.1%}** std dev")
        st.write(f"FCF Conversion (CFO/NI): **{q.fcf_conversion:.2f}x**")
        st.write(f"Payout Ratio: **{q.payout_avg:.0%}**")
        st.write(f"Debt/EBITDA: **{q.debt_ebitda:.1f}x**")

    with qr:
        st.subheader(f"C-Score: {q.c_score.total}/5")
        st.caption("Lower = better quality. Each flag = 1 point.")
        for k,v in q.c_score.details.items():
            flag = "🔴" if ("Declining" in v or "Increasing" in v or ">" in v) else "🟢"
            st.write(f"{flag} **{k}**: {v}")

    # Historical Multiples
    st.markdown("---")
    st.subheader("Historical Multiples")
    hm = r["historical_multiples"]
    if not hm.empty and not hm.isna().all().all():
        fig_m = go.Figure()
        for col,color in [("P/E",C_TEAL),("EV/EBITDA",C_CORAL)]:
            if col in hm:
                s = hm[col].dropna()
                if len(s) > 0:
                    fig_m.add_trace(go.Scatter(x=s.index,y=s.values,name=col,line=dict(color=color,width=2),mode="lines+markers"))
        fig_m.update_layout(height=350,plot_bgcolor="white",font=dict(family="Arial"),yaxis_title="Multiple",legend=dict(orientation="h"))
        st.plotly_chart(fig_m,use_container_width=True)

        st.dataframe(hm.style.format({"P/E":"{:.1f}x","EV/EBITDA":"{:.1f}x","P/Sales":"{:.1f}x","FCF Yield":"{:.1%}"},na_rep="—"),use_container_width=True)

        # Summary stats
        st.markdown("**Multiple Ranges:**")
        for col in ["P/E","EV/EBITDA","P/Sales","FCF Yield"]:
            if col in hm:
                s = hm[col].dropna()
                if len(s) >= 3:
                    fmt = "{:.1f}x" if col != "FCF Yield" else "{:.1%}"
                    st.write(f"**{col}**: Current {fmt.format(s.iloc[-1])} · Median {fmt.format(s.median())} · Range {fmt.format(s.min())}–{fmt.format(s.max())}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: RETURN DECOMPOSITION
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.title(f"Return Decomposition: {r['ticker']}")
    rd = r["return_decomposition"]

    if rd.get("available"):
        st.subheader(f"Stock Return Breakdown ({rd['start_year']}–{rd['end_year']})")

        rl,rr = st.columns([3,2])
        with rl:
            comps = [("Revenue<br>Growth",rd["revenue_growth_ann"]),("Margin<br>Effect",rd["margin_effect_ann"]),
                ("Buyback",rd["buyback_ann"]),("Dividend",rd["dividend_yield"]),("Multiple<br>Expansion",rd["multiple_expansion_ann"])]
            vals=[c[1] for c in comps]
            fig_rd=go.Figure(go.Waterfall(x=[c[0] for c in comps],y=vals,
                connector={"line":{"color":"#ccc"}},increasing={"marker":{"color":C_GREEN}},
                decreasing={"marker":{"color":C_RED}},text=[f"{v:+.1%}" for v in vals],
                textposition="outside",textfont=dict(size=13)))
            fig_rd.add_trace(go.Bar(x=["Total<br>Return"],y=[rd["total_return_ann"]],marker_color=C_TEAL,
                text=[f"{rd['total_return_ann']:+.1%}"],textposition="outside",textfont=dict(size=14,color=C_TEAL),width=0.5))
            fig_rd.update_layout(height=420,showlegend=False,yaxis_tickformat=".0%",yaxis_title="Annualized",plot_bgcolor="white",font=dict(family="Arial"))
            st.plotly_chart(fig_rd,use_container_width=True)

        with rr:
            st.markdown("**Annualized Components**")
            st.write(f"📈 Revenue Growth: **{rd['revenue_growth_ann']:+.1%}**")
            st.write(f"📊 Margin Effect: **{rd['margin_effect_ann']:+.1%}** ({rd['margin_first']:.1%} → {rd['margin_last']:.1%})")
            st.write(f"🔄 Buyback Yield: **{rd['buyback_ann']:+.1%}** ({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f})")
            st.write(f"💰 Dividend Yield: **{rd['dividend_yield']:.1%}**")
            st.write(f"📐 Multiple Expansion: **{rd['multiple_expansion_ann']:+.1%}**")
            st.markdown("---")
            st.write(f"**Total Return: {rd['total_return_ann']:+.1%} p.a.**")
            st.write(f"Price: {rd['price_first']:,.1f} → {rd['price_last']:,.1f}")

            fundamental = rd["revenue_growth_ann"] + rd["margin_effect_ann"]
            financial = rd["buyback_ann"] + rd["dividend_yield"]
            multiple = rd["multiple_expansion_ann"]
            total = rd["total_return_ann"]
            if abs(total) > 0.005:
                st.markdown("---")
                if multiple > 0.02:
                    st.warning(f"⚠️ {multiple/total*100:.0f}% of return from multiple expansion — not sustainable")
                elif multiple < -0.02:
                    st.info(f"Multiple contracted {multiple:.1%} p.a. — fundamentals outperformed the stock")
                if fundamental / total > 0.7 if total > 0 else False:
                    st.success(f"✅ {fundamental/total*100:.0f}% of return from fundamentals — high quality")
    else:
        st.warning("Return decomposition not available. Need historical Price (YE) data in Fundamentals sheet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: FORWARD DCF
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.title(f"Forward DCF: {r['ticker']}")
    st.markdown("**Set your assumptions → get your fair value.**")

    n_fwd = model.config.consensus_years + proj_years
    year_cols = [f"Y{i}" for i in range(1, n_fwd+1)] + ["Terminal"]

    hist_cagr = r["cagr_5y"] if r["cagr_5y"] else 0.05
    defaults = {"Metric": ["Revenue Growth (%)", "EBIT Margin (%)", "CapEx/Rev (%)", "D&A/Rev (%)", "SBC/Rev (%)", "Tax Rate (%)"],
                "Base": ["—", round(model.ebit_margin*100,1), round(model.capex_pct*100,1), round(model.da_pct*100,1), round(model.sbc_pct*100,1), round(model.tax_rate*100,1)]}

    rev = model.base_revenue
    for col in year_cols:
        if col == "Terminal":
            defaults[col] = [round(tg*100,1), round(model.ebit_margin*100,1), round(model.capex_pct*100,1), round(model.da_pct*100,1), round(model.sbc_pct*100,1), round(model.tax_rate*100,1)]
        else:
            defaults[col] = [round(hist_cagr*100,1), round(model.ebit_margin*100,1), round(model.capex_pct*100,1), round(model.da_pct*100,1), round(model.sbc_pct*100,1), round(model.tax_rate*100,1)]

    df_def = pd.DataFrame(defaults).set_index("Metric")
    edited = st.data_editor(df_def, use_container_width=True, num_rows="fixed", key=f"fwd_{r['ticker']}")

    try:
        pv_e = 0.0; proj_rows = []; rev = model.base_revenue
        for i in range(n_fwd):
            col = f"Y{i+1}"
            g=float(edited.loc["Revenue Growth (%)",col])/100; m=float(edited.loc["EBIT Margin (%)",col])/100
            cx=float(edited.loc["CapEx/Rev (%)",col])/100; da=float(edited.loc["D&A/Rev (%)",col])/100
            sbc=float(edited.loc["SBC/Rev (%)",col])/100; tx=float(edited.loc["Tax Rate (%)",col])/100
            rev*=(1+g); ebit=rev*m; nopat=ebit*(1-tx); fcff=nopat+rev*da-rev*cx-rev*sbc
            pv=fcff/(1+wacc)**(i+1); pv_e+=pv
            proj_rows.append({"Year":col,"Revenue":rev,"Growth":g,"EBIT Margin":m,"EBIT":ebit,"FCFF":fcff,"PV":pv})

        tg_f=float(edited.loc["Revenue Growth (%)","Terminal"])/100; tm_f=float(edited.loc["EBIT Margin (%)","Terminal"])/100
        cx_f=float(edited.loc["CapEx/Rev (%)","Terminal"])/100; da_f=float(edited.loc["D&A/Rev (%)","Terminal"])/100
        sbc_f=float(edited.loc["SBC/Rev (%)","Terminal"])/100; tx_f=float(edited.loc["Tax Rate (%)","Terminal"])/100
        t_rev=rev*(1+tg_f); t_nopat=t_rev*tm_f*(1-tx_f); t_fcff=t_nopat+t_rev*da_f-t_rev*cx_f-t_rev*sbc_f
        if wacc<=tg_f: st.error("WACC must exceed Terminal Growth!"); st.stop()
        tv_v=t_fcff/(wacc-tg_f); pv_tv=tv_v/(1+wacc)**n_fwd; tot_ev=pv_e+pv_tv
        fair_eq=tot_ev-model.net_debt-model.minority; fp=fair_eq/model.shares if model.shares else 0
        up=fp/model.price-1 if model.price else 0

        if up>0.20: fv,fc="UNDERVALUED",C_GREEN
        elif up>0.05: fv,fc="SLIGHT UPSIDE",C_GREEN
        elif up>-0.05: fv,fc="FAIRLY VALUED",C_AMBER
        elif up>-0.20: fv,fc="SLIGHT DOWNSIDE",C_CORAL
        else: fv,fc="OVERVALUED",C_RED

        st.markdown(f"""<div style="background:{fc}15;border-left:5px solid {fc};padding:20px;border-radius:4px;margin:20px 0;">
            <span style="font-size:28px;font-weight:bold;color:{fc};">🎯 {fv}</span><br>
            <span style="font-size:18px;color:#333;">Fair value: <b>{fp:,.1f}</b> vs {model.price:,.2f} → <b>{up:+.1%}</b></span>
        </div>""",unsafe_allow_html=True)

        f1,f2,f3,f4=st.columns(4)
        f1.metric("Current",f"{model.price:,.2f}"); f2.metric("Fair Value",f"{fp:,.1f}",delta=f"{up:+.1%}")
        f3.metric("EV",f"{tot_ev:,.0f}"); f4.metric("TV%",f"{pv_tv/tot_ev:.0%}")

        # Comparison chart
        st.markdown("---")
        st.subheader("My View vs Market")
        avg_g=np.mean([float(edited.loc["Revenue Growth (%)",f"Y{i+1}"]) for i in range(n_fwd)])/100
        fig_c=go.Figure()
        fig_c.add_trace(go.Bar(x=["Rev Growth","EBIT Margin"],y=[avg_g,float(edited.loc["EBIT Margin (%)",f"Y{n_fwd}"])/100],
            name="My View",marker_color=C_TEAL,text=[f"{avg_g:.1%}",f"{float(edited.loc['EBIT Margin (%)',f'Y{n_fwd}'])/100:.1%}"],textposition="outside"))
        fig_c.add_trace(go.Bar(x=["Rev Growth","EBIT Margin"],y=[ig,model.ebit_margin],
            name="Market",marker_color=C_CORAL,text=[f"{ig:.1%}",f"{model.ebit_margin:.1%}"],textposition="outside"))
        fig_c.update_layout(height=300,barmode="group",yaxis_tickformat=".0%",plot_bgcolor="white",font=dict(family="Arial"))
        st.plotly_chart(fig_c,use_container_width=True)

        # Cash flow table
        st.markdown("---")
        st.subheader("Projected Cash Flows")
        pdf=pd.DataFrame(proj_rows).set_index("Year")
        trow=pd.DataFrame([{"Revenue":t_rev,"Growth":tg_f,"EBIT Margin":tm_f,"EBIT":t_rev*tm_f,"FCFF":t_fcff,"PV":pv_tv}],index=["Terminal"])
        st.dataframe(pd.concat([pdf,trow]).style.format({"Revenue":"{:,.0f}","Growth":"{:.1%}","EBIT Margin":"{:.1%}","EBIT":"{:,.0f}","FCFF":"{:,.0f}","PV":"{:,.0f}"}),use_container_width=True)

        # Bridge
        st.markdown("---")
        st.subheader("Valuation Bridge")
        bl,br=st.columns([2,3])
        with bl:
            st.write(f"PV Explicit: **{pv_e:,.0f}**"); st.write(f"PV Terminal: **{pv_tv:,.0f}**")
            st.write(f"= EV: **{tot_ev:,.0f}**"); st.write(f"− Net Debt: {model.net_debt:,.0f}")
            st.write(f"= Equity: **{fair_eq:,.0f}** ÷ {model.shares:,.1f} = **{fp:,.1f}**")
        with br:
            cur_ev=model.market_ev; cur_ebit=model.base_ebit or 1
            fin_ebit=proj_rows[-1]["EBIT"]
            ev_from_rev=cur_ev*(rev/model.base_revenue-1); ev_from_m=cur_ev*((float(edited.loc["EBIT Margin (%)",f"Y{n_fwd}"])/100)/model.ebit_margin-1) if model.ebit_margin else 0
            ev_res=tot_ev-cur_ev-ev_from_rev-ev_from_m
            fig_b=go.Figure(go.Waterfall(x=["Current EV","Revenue<br>Growth","Margin<br>Change","Multiple<br>& Other","Your EV"],
                y=[cur_ev,ev_from_rev,ev_from_m,ev_res,0],measure=["absolute","relative","relative","relative","total"],
                connector={"line":{"color":"#ccc"}},increasing={"marker":{"color":C_GREEN}},decreasing={"marker":{"color":C_RED}},
                totals={"marker":{"color":C_TEAL}},text=[f"{cur_ev:,.0f}",f"{ev_from_rev:+,.0f}",f"{ev_from_m:+,.0f}",f"{ev_res:+,.0f}",f"{tot_ev:,.0f}"],
                textposition="outside",textfont=dict(size=10)))
            fig_b.update_layout(height=350,showlegend=False,plot_bgcolor="white",font=dict(family="Arial"))
            st.plotly_chart(fig_b,use_container_width=True)

    except Exception as e:
        st.error(f"Error: {e}")
