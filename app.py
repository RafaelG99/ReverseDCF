"""CORE DCF Dashboard — 4-Stage Reverse DCF + Forward DCF"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from reverse_dcf_engine import CoreDCF, DCFConfig
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
use_midcycle = st.sidebar.checkbox("Use Mid-Cycle Margin for Base FCFF", value=True,
    help="ON (default): Base FCFF normalized to mid-cycle margin (trimmed mean last 7Y). "
         "OFF: Base FCFF uses current margin (peak/trough sensitive).")

# ── AI Layer Controls ─────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### 🤖 AI Layer (Claude Opus 4.7)")
api_key = st.secrets.get("ANTHROPIC_API_KEY", "") if hasattr(st, "secrets") else ""
ai_available = bool(api_key)
if not ai_available:
    st.sidebar.caption("⚠ No API key. Add ANTHROPIC_API_KEY in secrets.toml.")

ai_smart_wacc = st.sidebar.checkbox("Smart WACC/Tg Defaults", value=ai_available, disabled=not ai_available,
    help="Bottom-up plausibilization of WACC and Terminal Growth.")
ai_smart_fwd = st.sidebar.checkbox("Smart Forward DCF Pre-Fill", value=ai_available, disabled=not ai_available,
    help="Sector-aware Forward DCF trajectory based on consensus + secular trends.")
ai_commentary = st.sidebar.checkbox("AI Investment Commentary in PDF", value=ai_available, disabled=not ai_available,
    help="1-page Executive Summary: thesis, cases, verdict, catalysts.")

# Apply AI WACC/Tg if user hasn't manually overridden them
ai_wacc_tg = None
if ai_smart_wacc and ai_available:
    cache_key = f"ai_wacc_tg_{model.ticker}"
    if cache_key not in st.session_state:
        with st.sidebar:
            with st.spinner("AI: WACC/Tg analysis..."):
                from ai_layer import smart_wacc_tg
                st.session_state[cache_key] = smart_wacc_tg(api_key, model)
    ai_wacc_tg = st.session_state.get(cache_key)
    if ai_wacc_tg:
        st.sidebar.caption(f"💡 AI suggests WACC {ai_wacc_tg.get('wacc_recommended', 0):.2%}, "
                          f"Tg {ai_wacc_tg.get('tg_recommended', 0):.2%}")

model.config.wacc = wacc
model.config.terminal_growth = tg
model.config.implied_years = proj_years
model.config.use_midcycle_margin = use_midcycle
model._prepare()
r = model.run()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🔍 Reverse DCF", "📊 Quality & Multiples", "📈 Return Decomposition", "👥 Peers", "🎯 Forward DCF"])

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

    # Expected Value + Margin of Safety
    sc = r["scenarios"]
    e1,e2,e3 = st.columns(3)
    e1.metric("Expected Value", f"{sc['expected_value']:,.1f}", delta=f"{sc['expected_upside']:+.1%}")
    e2.metric("Entry (20% MoS)", f"{sc['margin_of_safety_price']:,.1f}", delta=f"{sc['margin_of_safety_upside']:+.1%}")
    mid_m = r.get("mid_cycle_margin", model.ebit_margin)
    m_range = r.get("margin_range", (mid_m, mid_m))
    e3.metric("Mid-Cycle Margin", f"{mid_m:.1%}", delta=f"Range: {m_range[0]:.1%}–{m_range[1]:.1%}", delta_color="off")

    st.markdown("---")
    left,right = st.columns([3,2])
    with left:
        st.subheader("Scenario Fan")
        sc=r["scenarios"]; labels=["Bear (25%)","Base (50%)","Bull (25%)"]
        prices=[sc[l.split()[0]]["fair_price"] for l in labels]; ups=[sc[l.split()[0]]["upside"] for l in labels]
        fig=go.Figure()
        fig.add_trace(go.Bar(x=labels,y=prices,marker_color=[C_RED,C_AMBER,C_GREEN],
            text=[f"{p:,.1f}<br>({u:+.0%})" for p,u in zip(prices,ups)],textposition="outside",textfont=dict(size=14,color=C_TEAL)))
        fig.add_hline(y=r["price"],line_dash="dash",line_color=C_TEAL,line_width=2,
            annotation_text=f"Current: {r['price']:,.2f}",annotation_position="bottom right")
        fig.add_hline(y=sc["expected_value"],line_dash="dot",line_color="#8E44AD",line_width=2,
            annotation_text=f"Expected: {sc['expected_value']:,.1f}",annotation_position="top left")
        fig.add_hline(y=sc["margin_of_safety_price"],line_dash="dashdot",line_color="#27AE60",line_width=1,
            annotation_text=f"Entry (MoS): {sc['margin_of_safety_price']:,.1f}",annotation_position="bottom left")
        anr = model._safe_num(model.current.get("Target Price"))
        if anr and anr > 0:
            fig.add_hline(y=anr,line_dash="dot",line_color=C_CORAL,line_width=1,
                annotation_text=f"ANR: {anr:,.0f}",annotation_position="top right")
        fig.update_layout(height=420,showlegend=False,yaxis_title="Fair Price",plot_bgcolor="white",font=dict(family="Arial"))
        st.plotly_chart(fig,use_container_width=True)
        st.caption(f"Expected Value = 25%×Bear + 50%×Base + 25%×Bull. Entry = Expected × 80%.")

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
        st.write(f"Base Revenue: {model.base_revenue:,.0f} · EBIT Margin: {model.ebit_margin:.1%} (Mid-Cycle: {model.mid_cycle_margin:.1%})")
        st.write(f"FCFF: {model.base_fcff:,.0f} ({model.base_fcff/model.base_revenue:.1%} margin) · FCFF/Share: {model.base_fcff_per_share:,.2f}")
        st.write(f"D&A: {model.da_pct:.1%} · CapEx: {model.capex_pct:.1%} · SBC: {model.sbc_pct:.1%} · Tax: {model.tax_rate:.1%}")
        st.write(f"DSO: {model.dso:.0f} days · DPI: {model.dpi:.0f} days · NWC/Rev: {model.nwc_change_pct:.1%}")
        st.write(f"MCap: {model.market_cap:,.0f} · Net Debt: {model.net_debt:,.0f} (Lease: {model.lease_liab:,.0f}) · EV: {model.market_ev:,.0f}")
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
            cfg=DCFConfig(wacc=w,terminal_growth=t,implied_years=proj_years,use_midcycle_margin=use_midcycle)
            m2=CoreDCF(model.hist,model.current,cfg,ticker=r["ticker"])
            row[f"Tg={t:.1%}"]=m2.solve_implied_growth()
        row["WACC"]=w; rows.append(row)
    sdf=pd.DataFrame(rows).set_index("WACC"); sdf.index=[f"{w:.1%}" for w in w_rng]
    try:
        st.dataframe(sdf.style.format("{:.1%}").background_gradient(cmap="RdYlGn",axis=None),use_container_width=True)
    except ImportError:
        # matplotlib not installed — fall back to plain formatted table
        st.dataframe(sdf.style.format("{:.1%}"),use_container_width=True)
        st.caption("⚠ Install matplotlib for color-coded heatmap.")

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
            # Detect "bad" details: any string starting with non-OK markers
            is_bad = not v.startswith("OK") and not v.startswith("Skipped")
            flag = "🔴" if is_bad else "🟢"
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
            # Detect if buyback is actually dilution
            is_dilution = rd["buyback_ann"] < -0.05  # shares increased >5% p.a.
            bb_label = "Dilution" if is_dilution else "Buyback"
            
            comps = [("Revenue<br>Growth",rd["revenue_growth_ann"]),("Margin<br>Effect",rd["margin_effect_ann"]),
                (bb_label,rd["buyback_ann"]),("Dividend",rd["dividend_yield"]),("Multiple<br>Expansion",rd["multiple_expansion_ann"])]
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
            if is_dilution:
                st.write(f"🔻 Dilution: **{rd['buyback_ann']:+.1%}** ({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f} shares)")
                st.caption("⚠️ Shares increased significantly (IPO, capital raises, M&A). Multiple Expansion below is distorted.")
            else:
                st.write(f"🔄 Buyback Yield: **{rd['buyback_ann']:+.1%}** ({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f})")
            st.write(f"💰 Dividend Yield: **{rd['dividend_yield']:.1%}**")
            st.write(f"📐 Multiple Expansion: **{rd['multiple_expansion_ann']:+.1%}**{'  ⚠️ unreliable (dilution distortion)' if is_dilution else ''}")
            st.markdown("---")
            st.write(f"**Total Return: {rd['total_return_ann']:+.1%} p.a.**")
            st.write(f"Price: {rd['price_first']:,.1f} → {rd['price_last']:,.1f}")

            fundamental = rd["revenue_growth_ann"] + rd["margin_effect_ann"]
            financial = rd["buyback_ann"] + rd["dividend_yield"]
            multiple = rd["multiple_expansion_ann"]
            total = rd["total_return_ann"]
            if abs(total) > 0.005 and not is_dilution:
                st.markdown("---")
                if multiple > 0.02:
                    st.warning(f"⚠️ {multiple/total*100:.0f}% of return from multiple expansion — not sustainable")
                elif multiple < -0.02:
                    st.info(f"Multiple contracted {multiple:.1%} p.a. — fundamentals outperformed the stock")
                if total > 0 and fundamental / total > 0.7:
                    st.success(f"✅ {fundamental/total*100:.0f}% of return from fundamentals — high quality")
    else:
        st.warning("Return decomposition not available. Need historical Price (YE) data in Fundamentals sheet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: PEERS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.title(f"Peer Comparison: {r['ticker']}")
    peers = r.get("peers", [])
    if peers:
        pdf = pd.DataFrame(peers)
        # First row is the main ticker
        main = pdf[pdf["ticker"].str.contains(model.ticker.split()[0])].head(1) if model.ticker else pd.DataFrame()
        peer_only = pdf[~pdf["ticker"].str.contains(model.ticker.split()[0])] if model.ticker else pdf

        st.dataframe(pdf[["ticker","name","P/E","EV/EBITDA","P/Sales","Div Yld","ROIC","Gross Mrg","EBIT Mrg"]].style.format(
            {"P/E":"{:.1f}x","EV/EBITDA":"{:.1f}x","P/Sales":"{:.1f}x","Div Yld":"{:.1f}%","ROIC":"{:.1f}%","Gross Mrg":"{:.1f}%","EBIT Mrg":"{:.1f}%"},na_rep="—"),
            use_container_width=True)

        # Peer average vs main ticker
        if len(peer_only) > 0 and len(main) > 0:
            st.markdown("---")
            st.subheader("vs Peer Average")
            for metric in ["P/E","EV/EBITDA","P/Sales","ROIC","Gross Mrg","EBIT Mrg"]:
                vals = peer_only[metric].dropna()
                if len(vals) > 0 and main[metric].notna().any():
                    avg = vals.mean(); own = float(main[metric].iloc[0])
                    diff = own - avg
                    fmt = "{:.1f}x" if metric in ["P/E","EV/EBITDA","P/Sales"] else "{:.1f}%"
                    prem = "premium" if diff > 0 else "discount"
                    st.write(f"**{metric}**: {fmt.format(own)} vs Peer Avg {fmt.format(avg)} ({prem}: {abs(diff):.1f})")

        # Bar chart
        if len(peers) > 1:
            st.markdown("---")
            st.subheader("Valuation Comparison")
            for metric, fmt in [("P/E", "x"), ("EV/EBITDA", "x")]:
                vals = [(p["ticker"], p.get(metric)) for p in peers if p.get(metric) is not None]
                if vals:
                    fig_p = go.Figure(go.Bar(
                        x=[v[0] for v in vals], y=[v[1] for v in vals],
                        marker_color=[C_TEAL if model.ticker.split()[0] in v[0] else C_CORAL for v in vals],
                        text=[f"{v[1]:.1f}{fmt}" for v in vals], textposition="outside"))
                    fig_p.update_layout(height=300, title=metric, showlegend=False, plot_bgcolor="white", font=dict(family="Arial"))
                    st.plotly_chart(fig_p, use_container_width=True)
    else:
        st.info("No peer data found. Add peer tickers in the Peers sheet of your Excel.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5: FORWARD DCF
# ══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.title(f"Forward DCF: {r['ticker']}")
    st.markdown("**Set your assumptions → get your fair value.**")

    n_fwd = model.config.consensus_years + proj_years
    year_cols = [f"Y{i}" for i in range(1, n_fwd+1)] + ["Terminal"]

    # Default growth = implied growth from Reverse DCF (what the market expects)
    # This way you START at market expectations and adjust from there
    implied_g = r["implied_growth"]
    mid_margin = r.get("mid_cycle_margin", model.ebit_margin)

    # Try AI-powered Smart Pre-Fill first
    ai_fwd = None
    if ai_smart_fwd and ai_available:
        cache_key_fwd = f"ai_fwd_{r['ticker']}_{n_fwd}_{round(wacc,4)}_{round(tg,4)}"
        if cache_key_fwd not in st.session_state:
            with st.spinner("AI: Building sector-aware Forward DCF trajectory..."):
                from ai_layer import smart_forward_prefill
                st.session_state[cache_key_fwd] = smart_forward_prefill(api_key, model, r, n_fwd=n_fwd)
        ai_fwd = st.session_state.get(cache_key_fwd)

    if ai_fwd and "years" in ai_fwd:
        st.caption(f"💡 **AI Pre-Fill aktiv:** {ai_fwd.get('rationale', '')}")
    else:
        st.caption(f"Defaults: Implied growth ({implied_g:.1%}) from Reverse DCF. "
                   f"Margin fades from current ({model.ebit_margin:.1%}) to mid-cycle ({mid_margin:.1%}). "
                   f"Adjust to reflect YOUR view.")

    defaults = {"Metric": ["Revenue Growth (%)", "EBIT Margin (%)", "CapEx/Rev (%)", "D&A/Rev (%)", "SBC/Rev (%)", "Tax Rate (%)"],
                "Base": ["—", round(model.ebit_margin*100,1), round(model.capex_pct*100,1), round(model.da_pct*100,1), round(model.sbc_pct*100,1), round(model.tax_rate*100,1)]}

    def _ai_year(year_key):
        """Pull AI values if available and valid; else None."""
        if not ai_fwd or "years" not in ai_fwd:
            return None
        y = ai_fwd["years"].get(year_key)
        if not isinstance(y, dict):
            return None
        try:
            return [round(y["growth"]*100, 1), round(y["margin"]*100, 1),
                    round(y["capex"]*100, 1), round(y["da"]*100, 1),
                    round(y["sbc"]*100, 1), round(y["tax"]*100, 1)]
        except (KeyError, TypeError, ValueError):
            return None

    rev = model.base_revenue
    for i, col in enumerate(year_cols):
        ai_vals = _ai_year(col)
        if ai_vals is not None:
            defaults[col] = ai_vals
        elif col == "Terminal":
            defaults[col] = [round(tg*100,1), round(mid_margin*100,1), round(model.capex_pct*100,1), round(model.da_pct*100,1), round(model.sbc_pct*100,1), round(model.tax_rate*100,1)]
        else:
            # Growth: start at implied, gradually fade toward terminal
            fade = i / max(n_fwd - 1, 1)
            g_default = implied_g * (1 - fade * 0.5) + tg * (fade * 0.5)  # fade halfway toward Tg
            # Margin: fade from current toward mid-cycle
            m_default = model.ebit_margin + (mid_margin - model.ebit_margin) * fade * 0.5
            defaults[col] = [round(g_default*100,1), round(m_default*100,1), round(model.capex_pct*100,1), round(model.da_pct*100,1), round(model.sbc_pct*100,1), round(model.tax_rate*100,1)]

    df_def = pd.DataFrame(defaults).set_index("Metric")
    edited = st.data_editor(df_def, use_container_width=True, num_rows="fixed", key=f"fwd_{r['ticker']}")
    # Cache for PDF export
    st.session_state["fwd_edited"] = edited
    st.session_state["fwd_n"] = n_fwd

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

        # Implied Multiples — what does your fair value imply?
        st.markdown("---")
        st.subheader("Implied Multiples & Plausibility")
        last_row = proj_rows[-1]
        last_ni = last_row["EBIT"] * (1 - float(edited.loc["Tax Rate (%)", f"Y{n_fwd}"]) / 100)
        impl = model.implied_multiples(tot_ev, projected_revenue=last_row["Revenue"],
            projected_ebit=last_row["EBIT"], projected_ebitda=last_row.get("EBITDA", last_row["EBIT"]*1.3),
            projected_ni=last_ni)
        hm = r["historical_multiples"]
        im1,im2,im3 = st.columns(3)
        for col_w, metric, hist_col in [(im1,"implied_EV/EBIT","EV/EBITDA"),(im2,"implied_P/E","P/E"),(im3,"implied_P/Sales","P/Sales")]:
            val = impl.get(metric)
            if val and not np.isnan(val):
                h_med = hm[hist_col].dropna().median() if hist_col in hm and len(hm[hist_col].dropna()) > 0 else None
                label = metric.replace("implied_","")
                if h_med and not np.isnan(h_med):
                    col_w.metric(label, f"{val:.1f}x",
                        delta=f"vs {h_med:.1f}x hist median",
                        delta_color="inverse" if val > h_med * 1.2 else "normal")
                else:
                    col_w.metric(label, f"{val:.1f}x")
            
        # Margin of Safety
        st.markdown("---")
        mos_price = fp * 0.80
        mos_up = mos_price / model.price - 1 if model.price else 0
        st.markdown(f"""<div style="background:#27AE6015;border-left:5px solid #27AE60;padding:15px 20px;border-radius:4px;">
            <span style="font-size:16px;color:#333;">
                <b>Entry Target (20% Margin of Safety):</b> {mos_price:,.1f} ({mos_up:+.1%} from current)
                {'— ✅ Current price is below entry target' if model.price < mos_price else '— ⚠️ Wait for better entry'}
            </span>
        </div>""", unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PDF EXPORT
# ══════════════════════════════════════════════════════════════════════════════
def _fig_to_png(fig, w=900, h=420, scale=2):
    """Plotly figure → PNG bytes via Kaleido."""
    return fig.to_image(format="png", width=w, height=h, scale=scale, engine="kaleido")


def _build_scenario_fan(r, model, C_TEAL, C_CORAL, C_AMBER, C_GREEN, C_RED):
    sc = r["scenarios"]; labels = ["Bear (25%)", "Base (50%)", "Bull (25%)"]
    prices = [sc[l.split()[0]]["fair_price"] for l in labels]
    ups = [sc[l.split()[0]]["upside"] for l in labels]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=prices, marker_color=[C_RED, C_AMBER, C_GREEN],
        text=[f"{p:,.1f}<br>({u:+.0%})" for p, u in zip(prices, ups)],
        textposition="outside", textfont=dict(size=14, color=C_TEAL)))
    fig.add_hline(y=r["price"], line_dash="dash", line_color=C_TEAL, line_width=2,
        annotation_text=f"Current: {r['price']:,.2f}", annotation_position="bottom right")
    fig.add_hline(y=sc["expected_value"], line_dash="dot", line_color="#8E44AD", line_width=2,
        annotation_text=f"Expected: {sc['expected_value']:,.1f}", annotation_position="top left")
    fig.add_hline(y=sc["margin_of_safety_price"], line_dash="dashdot", line_color="#27AE60", line_width=1,
        annotation_text=f"Entry (MoS): {sc['margin_of_safety_price']:,.1f}", annotation_position="bottom left")
    fig.update_layout(height=420, showlegend=False, yaxis_title="Fair Price",
        plot_bgcolor="white", font=dict(family="Arial"),
        margin=dict(l=50, r=30, t=30, b=40))
    return fig


def _build_tv_pie(r, C_TEAL, C_CORAL):
    tv = r["tv_decomposition"]
    fig = go.Figure(go.Pie(labels=["Explicit Period", "Terminal Value"],
        values=[tv["explicit_pct"], tv["tv_pct"]],
        marker_colors=[C_TEAL, C_CORAL], hole=0.5,
        textinfo="label+percent", textfont=dict(size=13)))
    fig.update_layout(height=380, showlegend=False, font=dict(family="Arial"),
        margin=dict(l=20, r=20, t=20, b=20),
        annotations=[dict(text=f"TV<br>{tv['tv_pct']:.0%}", x=0.5, y=0.5,
            font_size=18, showarrow=False, font_color=C_CORAL)])
    return fig


def _build_multiples_chart(r, C_TEAL, C_CORAL):
    hm = r["historical_multiples"]
    if hm.empty: return None
    fig = go.Figure()
    for col, color in [("P/E", C_TEAL), ("EV/EBITDA", C_CORAL)]:
        if col in hm:
            s = hm[col].dropna()
            if len(s) > 0:
                fig.add_trace(go.Scatter(x=s.index, y=s.values, name=col,
                    line=dict(color=color, width=2), mode="lines+markers"))
    fig.update_layout(height=320, plot_bgcolor="white", font=dict(family="Arial"),
        yaxis_title="Multiple", legend=dict(orientation="h"),
        margin=dict(l=50, r=30, t=20, b=40))
    return fig


def _build_return_decomp(rd, C_TEAL, C_GREEN, C_RED):
    is_dilution = rd["buyback_ann"] < -0.05
    bb_label = "Dilution" if is_dilution else "Buyback"
    comps = [("Revenue<br>Growth", rd["revenue_growth_ann"]),
             ("Margin<br>Effect", rd["margin_effect_ann"]),
             (bb_label, rd["buyback_ann"]),
             ("Dividend", rd["dividend_yield"]),
             ("Multiple<br>Expansion", rd["multiple_expansion_ann"])]
    vals = [c[1] for c in comps]
    fig = go.Figure(go.Waterfall(x=[c[0] for c in comps], y=vals,
        connector={"line": {"color": "#ccc"}},
        increasing={"marker": {"color": C_GREEN}},
        decreasing={"marker": {"color": C_RED}},
        text=[f"{v:+.1%}" for v in vals],
        textposition="outside", textfont=dict(size=13)))
    fig.add_trace(go.Bar(x=["Total<br>Return"], y=[rd["total_return_ann"]],
        marker_color=C_TEAL, text=[f"{rd['total_return_ann']:+.1%}"],
        textposition="outside", textfont=dict(size=14, color=C_TEAL), width=0.5))
    fig.update_layout(height=400, showlegend=False, yaxis_tickformat=".0%",
        yaxis_title="Annualized", plot_bgcolor="white", font=dict(family="Arial"),
        margin=dict(l=60, r=30, t=20, b=60))
    return fig


def _build_peer_chart(peers, model_ticker, metric, fmt, C_TEAL, C_CORAL):
    vals = [(p["ticker"], p.get(metric)) for p in peers if p.get(metric) is not None]
    if not vals: return None
    own_key = model_ticker.split()[0] if model_ticker else ""
    fig = go.Figure(go.Bar(
        x=[v[0] for v in vals], y=[v[1] for v in vals],
        marker_color=[C_TEAL if own_key and own_key in v[0] else C_CORAL for v in vals],
        text=[f"{v[1]:.1f}{fmt}" for v in vals], textposition="outside"))
    fig.update_layout(height=300, title=metric, showlegend=False,
        plot_bgcolor="white", font=dict(family="Arial"),
        margin=dict(l=40, r=20, t=40, b=40))
    return fig


def _build_fwd_compare(avg_g, last_m, ig, model_margin, C_TEAL, C_CORAL):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=["Rev Growth", "EBIT Margin"], y=[avg_g, last_m],
        name="My View", marker_color=C_TEAL,
        text=[f"{avg_g:.1%}", f"{last_m:.1%}"], textposition="outside"))
    fig.add_trace(go.Bar(x=["Rev Growth", "EBIT Margin"], y=[ig, model_margin],
        name="Market", marker_color=C_CORAL,
        text=[f"{ig:.1%}", f"{model_margin:.1%}"], textposition="outside"))
    fig.update_layout(height=300, barmode="group", yaxis_tickformat=".0%",
        plot_bgcolor="white", font=dict(family="Arial"),
        margin=dict(l=50, r=30, t=20, b=40))
    return fig


def _build_bridge(cur_ev, ev_from_rev, ev_from_m, ev_res, tot_ev, C_TEAL, C_GREEN, C_RED):
    fig = go.Figure(go.Waterfall(
        x=["Current EV", "Revenue<br>Growth", "Margin<br>Change", "Multiple<br>& Other", "Your EV"],
        y=[cur_ev, ev_from_rev, ev_from_m, ev_res, 0],
        measure=["absolute", "relative", "relative", "relative", "total"],
        connector={"line": {"color": "#ccc"}},
        increasing={"marker": {"color": C_GREEN}},
        decreasing={"marker": {"color": C_RED}},
        totals={"marker": {"color": C_TEAL}},
        text=[f"{cur_ev:,.0f}", f"{ev_from_rev:+,.0f}", f"{ev_from_m:+,.0f}",
              f"{ev_res:+,.0f}", f"{tot_ev:,.0f}"],
        textposition="outside", textfont=dict(size=10)))
    fig.update_layout(height=350, showlegend=False, plot_bgcolor="white",
        font=dict(family="Arial"), margin=dict(l=50, r=30, t=20, b=40))
    return fig


def build_pdf(model, r, wacc, tg, proj_years, edited_df, n_fwd, ai_summary=None):
    """Generate full multi-page PDF report covering all 5 tabs.
    If ai_summary dict is provided (from senior_commentary), prepend a Valterna-styled
    Executive Summary as Page 1."""
    from io import BytesIO
    from datetime import datetime
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
        Paragraph, Spacer, Table, TableStyle, Image as RLImage,
        PageBreak, KeepTogether)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    # ── Valterna Corporate Design ────────────────────────────────────────────
    # Primary palette
    C_NAVY     = "#003850"   # Dark Teal — Primary
    C_GOLD     = "#BD9755"   # Muted Gold — Primary Accent
    C_AMBER    = "#FBAE40"   # Bright Amber — Secondary Accent
    C_CORAL    = "#F26B43"   # Coral — Tertiary
    C_GREEN    = "#2ECC71"
    C_RED      = "#E74C3C"
    C_BG_LIGHT = "#F0F3F5"   # Light background
    C_BG_TEAL  = "#E8F0F3"   # Tinted background for highlights
    C_BORDER   = "#D0D5DB"
    C_GRAY_DARK = "#4A5568"
    C_GRAY_MID  = "#A0A8B0"

    # Backwards-compat aliases (rest of code uses these names)
    C_TEAL = C_NAVY

    # ── Font registration: try Century Gothic, fallback Helvetica ────────────
    FONT_REG = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"
    cg_paths = [
        # Common Windows paths
        "C:/Windows/Fonts/GOTHIC.TTF",
        "C:/Windows/Fonts/GOTHICB.TTF",
        # macOS
        "/Library/Fonts/Century Gothic.ttf",
        # Linux/Streamlit Cloud — try local fonts dir
        "fonts/CenturyGothic.ttf",
        "fonts/CenturyGothicBold.ttf",
    ]
    try:
        for p_reg, p_bold in [(cg_paths[0], cg_paths[1]),
                              (cg_paths[2], cg_paths[2]),
                              (cg_paths[4], cg_paths[5])]:
            if os.path.exists(p_reg):
                pdfmetrics.registerFont(TTFont("CenturyGothic", p_reg))
                if os.path.exists(p_bold):
                    pdfmetrics.registerFont(TTFont("CenturyGothic-Bold", p_bold))
                    FONT_REG = "CenturyGothic"
                    FONT_BOLD = "CenturyGothic-Bold"
                else:
                    FONT_REG = "CenturyGothic"
                    FONT_BOLD = "CenturyGothic"
                break
    except Exception:
        pass  # silent fallback to Helvetica

    buf = BytesIO()

    # ── Logo path resolution (same dir as app.py, optional) ───────────────────
    logo_path = None
    for p in ["valterna_logo.png", "logo.png", "assets/valterna_logo.png"]:
        if os.path.exists(p):
            logo_path = p
            break

    # ── Page header/footer drawing functions ──────────────────────────────────
    page_w, page_h = A4
    margin_l = 1.6 * cm
    margin_r = 1.6 * cm

    def _on_page(canvas, doc):
        """Draw header + footer on every page. Mini style works for all pages."""
        canvas.saveState()
        # Logo (top-left)
        if logo_path:
            try:
                logo_w = 3.2 * cm
                logo_h = logo_w * (52.6 / 263.2)
                canvas.drawImage(logo_path, margin_l, page_h - 1.0*cm - logo_h,
                    width=logo_w, height=logo_h, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        # Right side: ticker + date
        canvas.setFont(FONT_BOLD, 8)
        canvas.setFillColor(colors.HexColor(C_NAVY))
        canvas.drawRightString(page_w - margin_r, page_h - 1.1*cm, r['ticker'])
        canvas.setFont(FONT_REG, 7.5)
        canvas.setFillColor(colors.HexColor(C_GRAY_MID))
        canvas.drawRightString(page_w - margin_r, page_h - 1.4*cm,
            f"{datetime.now():%d %B %Y}")
        # Gold separator line
        canvas.setStrokeColor(colors.HexColor(C_GOLD))
        canvas.setLineWidth(0.8)
        canvas.line(margin_l, page_h - 1.85*cm, page_w - margin_r, page_h - 1.85*cm)
        # Footer line
        canvas.setStrokeColor(colors.HexColor(C_BORDER))
        canvas.setLineWidth(0.4)
        canvas.line(margin_l, 1.0*cm, page_w - margin_r, 1.0*cm)
        # Footer text
        canvas.setFont(FONT_REG, 7)
        canvas.setFillColor(colors.HexColor(C_GRAY_MID))
        canvas.drawString(margin_l, 0.65*cm, "Valterna AG · CORE DCF Engine · Confidential")
        canvas.drawRightString(page_w - margin_r, 0.65*cm, f"Page {doc.page}")
        canvas.restoreState()

    # Frame: leave space for header (top 2.2cm) and footer (bottom 1.3cm)
    frame = Frame(margin_l, 1.3*cm,
        page_w - margin_l - margin_r, page_h - 2.2*cm - 1.3*cm,
        id='content', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    main_template = PageTemplate(id='main', frames=[frame], onPage=_on_page)

    doc = BaseDocTemplate(buf, pagesize=A4,
        leftMargin=margin_l, rightMargin=margin_r,
        topMargin=2.2*cm, bottomMargin=1.3*cm,
        title=f"CORE DCF Report – {r['ticker']}",
        author="Valterna AG",
        pageTemplates=[main_template])

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName=FONT_BOLD,
        fontSize=18, textColor=colors.HexColor(C_NAVY), spaceAfter=8, spaceBefore=4, leading=22)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName=FONT_BOLD,
        fontSize=12, textColor=colors.HexColor(C_NAVY), spaceAfter=8, spaceBefore=12, leading=15,
        borderPadding=(0, 0, 4, 0),  # bottom padding before underline
        borderColor=colors.HexColor(C_GOLD),
        borderWidth=0)  # we'll draw underline differently via small Table
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontName=FONT_BOLD,
        fontSize=10.5, textColor=colors.HexColor(C_NAVY), spaceAfter=4, spaceBefore=8, leading=13,
        textTransform="uppercase")
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=FONT_REG,
        fontSize=9.5, leading=13, spaceAfter=3, textColor=colors.HexColor(C_GRAY_DARK))
    small = ParagraphStyle("small", parent=styles["BodyText"], fontName=FONT_REG,
        fontSize=8, leading=10, textColor=colors.HexColor("#666666"))
    # Verdict: smaller (14pt) so it doesn't overlap KPI strip; left-aligned in its own paragraph
    verdict_style = lambda c: ParagraphStyle("v", fontName=FONT_BOLD,
        fontSize=14, textColor=colors.HexColor(c), spaceAfter=4, spaceBefore=2,
        alignment=TA_LEFT, leading=17)

    def _section_header(text):
        """Section header with subtle gold underline — replaces h2 calls for visual consistency."""
        para = Paragraph(text, h2)
        underline = Table([[" "]], colWidths=[18.0*cm], rowHeights=[0.5])
        underline.setStyle(TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.HexColor(C_GOLD)),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return [para, underline, Spacer(1, 0.2*cm)]

    def _data_table_style(header_rows=1, alt_rows=True, highlight_last=False):
        """Standard data table style: light header, alternating rows, soft grid."""
        style = [
            # Header row(s)
            ("BACKGROUND", (0, 0), (-1, header_rows-1), colors.HexColor(C_NAVY)),
            ("TEXTCOLOR", (0, 0), (-1, header_rows-1), colors.white),
            ("FONTNAME", (0, 0), (-1, header_rows-1), FONT_BOLD),
            ("FONTSIZE", (0, 0), (-1, header_rows-1), 8.5),
            ("ALIGN", (0, 0), (-1, header_rows-1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, header_rows-1), 6),
            ("TOPPADDING", (0, 0), (-1, header_rows-1), 6),
            # Body rows
            ("FONTNAME", (0, header_rows), (-1, -1), FONT_REG),
            ("FONTSIZE", (0, header_rows), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, header_rows), (-1, -1), colors.HexColor(C_GRAY_DARK)),
            ("ALIGN", (0, header_rows), (-1, -1), "CENTER"),
            ("BOTTOMPADDING", (0, header_rows), (-1, -1), 5),
            ("TOPPADDING", (0, header_rows), (-1, -1), 5),
            # Soft grid
            ("LINEBELOW", (0, header_rows-1), (-1, header_rows-1), 1.2, colors.HexColor(C_GOLD)),
            ("LINEBELOW", (0, header_rows), (-1, -2), 0.25, colors.HexColor(C_BORDER)),
        ]
        if highlight_last:
            style += [
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor(C_BG_TEAL)),
                ("FONTNAME", (0, -1), (-1, -1), FONT_BOLD),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor(C_NAVY)),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor(C_GOLD)),
            ]
        return TableStyle(style)

    story = []

    # ══ PAGE 0: VALTERNA EXECUTIVE SUMMARY (only if AI summary provided) ═════
    if ai_summary:
        # Brand strip is now drawn by cover_template; just add title block
        # Ticker title block
        title_para = ParagraphStyle("title_p", fontName=FONT_BOLD,
            fontSize=22, textColor=colors.HexColor(C_NAVY), leading=26, spaceAfter=2)
        sub_para = ParagraphStyle("sub_p", fontName=FONT_REG,
            fontSize=10, textColor=colors.HexColor(C_GRAY_MID), leading=12, spaceAfter=8)
        story.append(Paragraph(f"{r['ticker']}  <font color='{C_GOLD}'>·</font>  "
                              f"<font size='14'>{r['price']:.2f}</font>", title_para))
        story.append(Paragraph(f"Reverse DCF Analyse  ·  WACC {wacc:.2%}  ·  Tg {tg:.2%}  ·  "
                              f"Quality {r['quality'].grade}",
                              sub_para))
        story.append(Spacer(1, 0.3*cm))

        # Headline — gold accent box
        headline = ai_summary.get("headline", "")
        if headline:
            headline_style = ParagraphStyle("headline", fontName=FONT_BOLD,
                fontSize=12, textColor=colors.HexColor(C_NAVY), leading=16,
                leftIndent=0, spaceAfter=8)
            headline_table = Table([[Paragraph(headline, headline_style)]],
                colWidths=[18.0*cm])
            headline_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(C_BG_TEAL)),
                ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(C_GOLD)),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.append(headline_table)
            story.append(Spacer(1, 0.4*cm))

        # Investment Thesis
        thesis = ai_summary.get("thesis", "")
        if thesis:
            story.append(Paragraph("INVESTMENT THESIS", h3))
            story.append(Paragraph(thesis, body))
            story.append(Spacer(1, 0.3*cm))

        # Three cases (Bull / Base / Bear) as side-by-side table
        bull = ai_summary.get("bull_case", "")
        base = ai_summary.get("base_case", "")
        bear = ai_summary.get("bear_case", "")
        if bull or base or bear:
            case_label = ParagraphStyle("case_label", fontName=FONT_BOLD,
                fontSize=9, textColor=colors.white, leading=11, alignment=TA_CENTER)
            case_body = ParagraphStyle("case_body", fontName=FONT_REG,
                fontSize=9, textColor=colors.HexColor(C_GRAY_DARK), leading=12)

            cases_tbl = Table([
                [Paragraph("BULL CASE", case_label),
                 Paragraph("BASE CASE", case_label),
                 Paragraph("BEAR CASE", case_label)],
                [Paragraph(bull, case_body),
                 Paragraph(base, case_body),
                 Paragraph(bear, case_body)]
            ], colWidths=[6.0*cm, 6.0*cm, 6.0*cm])
            cases_tbl.setStyle(TableStyle([
                # Header row colors
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor(C_GREEN)),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor(C_NAVY)),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor(C_RED)),
                # Body row
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor(C_BG_LIGHT)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 1), (-1, 1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
            ]))
            story.append(cases_tbl)
            story.append(Spacer(1, 0.4*cm))

        # Verdict — large gold-accented block
        action = ai_summary.get("verdict_action", "")
        entry = ai_summary.get("verdict_entry_level", None)
        rationale = ai_summary.get("verdict_rationale", "")
        if action:
            action_color_map = {
                "LONG": C_GREEN, "ACCUMULATE": C_GREEN,
                "HOLD": C_AMBER,
                "TRIM": C_CORAL, "AVOID": C_RED,
            }
            action_color = action_color_map.get(action.upper(), C_NAVY)

            action_style = ParagraphStyle("action", fontName=FONT_BOLD, fontSize=18,
                textColor=colors.HexColor(action_color), leading=22)
            entry_style = ParagraphStyle("entry", fontName=FONT_REG, fontSize=10,
                textColor=colors.HexColor("#FFFFFF"), leading=13)
            rationale_style = ParagraphStyle("rat", fontName=FONT_REG, fontSize=9.5,
                textColor=colors.HexColor("#E8F0F3"), leading=13)

            entry_str = ""
            if entry is not None:
                try:
                    entry_str = f"Entry Target: <b>{float(entry):.2f}</b>"
                    if r.get("price"):
                        diff = float(entry)/r["price"] - 1
                        entry_str += f"  ({diff:+.0%} vs current)"
                except (TypeError, ValueError):
                    pass

            verdict_box_content = [
                [Paragraph(f"VERDICT", ParagraphStyle("vlabel", fontName=FONT_BOLD,
                    fontSize=8, textColor=colors.HexColor(C_GOLD), leading=10))],
                [Paragraph(action.upper(), action_style)],
                [Paragraph(entry_str, entry_style)] if entry_str else [Spacer(1, 1)],
                [Paragraph(rationale, rationale_style)] if rationale else [Spacer(1, 1)],
            ]
            verdict_tbl = Table(verdict_box_content, colWidths=[18.0*cm])
            verdict_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(C_NAVY)),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (0, 0), 10),
                ("BOTTOMPADDING", (0, 0), (0, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
                ("TOPPADDING", (0, 2), (-1, 2), 0),
                ("BOTTOMPADDING", (0, 2), (-1, 2), 6),
                ("TOPPADDING", (0, 3), (-1, 3), 0),
                ("BOTTOMPADDING", (0, 3), (-1, 3), 12),
            ]))
            story.append(verdict_tbl)
            story.append(Spacer(1, 0.4*cm))

        # Catalysts & Risks side-by-side
        catalysts = ai_summary.get("catalysts", []) or []
        risks = ai_summary.get("risks", []) or []
        if catalysts or risks:
            cat_label = ParagraphStyle("cat_label", fontName=FONT_BOLD,
                fontSize=10, textColor=colors.HexColor(C_NAVY), leading=12, spaceAfter=6)
            cat_item = ParagraphStyle("cat_item", fontName=FONT_REG,
                fontSize=9, textColor=colors.HexColor(C_GRAY_DARK), leading=12,
                leftIndent=10, bulletIndent=2, spaceAfter=2)

            cat_para = [Paragraph("CATALYSTS", cat_label)]
            for c in catalysts[:5]:
                cat_para.append(Paragraph(f"<font color='{C_GREEN}'>●</font>  {c}", cat_item))
            risk_para = [Paragraph("RISKS", cat_label)]
            for rk in risks[:5]:
                risk_para.append(Paragraph(f"<font color='{C_CORAL}'>●</font>  {rk}", cat_item))

            cr_tbl = Table([[cat_para, risk_para]], colWidths=[9.0*cm, 9.0*cm])
            cr_tbl.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("LEFTPADDING", (1, 0), (1, 0), 8),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ]))
            story.append(cr_tbl)

        # Footer note for the executive summary page
        story.append(Spacer(1, 0.6*cm))
        footer_style = ParagraphStyle("foot", fontName=FONT_REG, fontSize=7.5,
            textColor=colors.HexColor(C_GRAY_MID), leading=10, alignment=TA_LEFT)
        story.append(Paragraph(
            "AI-generierte Einordnung basierend auf CORE DCF Engine Output. "
            "Quantitative Details auf den folgenden Seiten. Nicht als Anlageberatung zu verstehen.",
            footer_style))

        story.append(PageBreak())

    # ══ PAGE 1: COVER + REVERSE DCF VERDICT ══════════════════════════════════
    # Compact ticker title block (header bar already shows logo + date + ticker)
    cover_title = ParagraphStyle("cov_t", fontName=FONT_BOLD,
        fontSize=20, textColor=colors.HexColor(C_NAVY), leading=24, spaceAfter=2)
    cover_sub = ParagraphStyle("cov_s", fontName=FONT_REG,
        fontSize=9, textColor=colors.HexColor(C_GRAY_MID), leading=12, spaceAfter=10)
    story.append(Paragraph(f"Reverse DCF Analysis  <font color='{C_GOLD}'>·</font>  "
                          f"<font color='{C_NAVY}' size='14'>{r['price']:.2f}</font>",
                          cover_title))
    story.append(Paragraph(
        f"WACC {wacc:.2%}  ·  Tg {tg:.2%}  ·  Implied Period {proj_years}Y  ·  "
        f"Base FCFF: {'Mid-Cycle' if model.config.use_midcycle_margin else 'Current'} margin",
        cover_sub))

    # Verdict box (replicate Tab 1 logic)
    ig = r["implied_growth"]; c5 = r["cagr_5y"]; c3 = r["cagr_3y"]
    tv_pct = r["tv_decomposition"]["tv_pct"]; spread = wacc - tg
    roic_sp = r["roic_spread"]
    red_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🔴")

    if spread < 0.02:
        verdict, v_color = "RESULT UNRELIABLE", C_RED
        v_action = f"WACC ({wacc:.2%}) too close to Tg ({tg:.2%})."
    elif ig < -0.10:
        ev0 = model._ev_from_fcf_growth(0.0)
        ratio = ev0 / model.market_ev if model.market_ev else 0
        verdict, v_color = "CHECK INPUTS", C_RED
        v_action = (f"Even 0% growth gives {ratio:.1f}x market EV. WACC ({wacc:.2%}) likely too low."
                    if ratio > 2 else "Extreme decline implied. Check data.")
    elif ig < -0.03 and c5 > 0.02:
        verdict, v_color = "POTENTIALLY UNDERVALUED", C_GREEN
        v_action = f"Market prices in {ig:.1%} decline, but historically +{c5:.1%} p.a."
    elif -0.03 <= ig <= 0.03:
        if c5 > 0.05:
            verdict, v_color = "POTENTIALLY UNDERVALUED", C_GREEN
            v_action = f"Market implies flat ({ig:.1%}), but historical CAGR was {c5:.1%}."
        else:
            verdict, v_color = "FAIRLY VALUED", C_AMBER
            v_action = f"Implied {ig:.1%} roughly in line with historical {c5:.1%}."
    elif red_flags >= 3:
        verdict, v_color = "OVERPRICED", C_RED
        v_action = "Market expects growth well beyond history."
    elif red_flags >= 2:
        verdict, v_color = "LIKELY OVERPRICED", C_CORAL
        v_action = "Expectations stretched vs history."
    elif red_flags == 0 and roic_sp > 0:
        verdict, v_color = "FAIRLY VALUED", C_GREEN
        v_action = "Expectations achievable based on history."
    else:
        verdict, v_color = "FAIR VALUE RANGE", C_AMBER
        v_action = "Mixed signals."

    story.append(Paragraph(verdict, verdict_style(v_color)))
    story.append(Paragraph(
        f"Market implies <b>{ig:.1%} p.a. FCF growth</b> (Y3-{2+proj_years}) "
        f"to justify {r['price']:,.2f}.", body))
    story.append(Paragraph(f"<b>So what?</b> {v_action}", body))
    story.append(Spacer(1, 0.3*cm))

    # KPI table (6 metrics)
    sc = r["scenarios"]; q = r["quality"]
    kpi_data = [
        ["Price", "Implied Growth", "WACC", "TV %", "ROIC Spread", "Quality"],
        [f"{r['price']:,.2f}",
         f"{ig:.1%}",
         f"{wacc:.2%}",
         f"{tv_pct:.0%}",
         f"{roic_sp:+.1%}",
         f"{q.grade} (C-Score {q.c_score.total}/5)"]
    ]
    kpi_tbl = Table(kpi_data, colWidths=[2.9*cm]*6)
    kpi_tbl.setStyle(TableStyle([
        # Header row: light tinted background, navy text, gold underline
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_BG_TEAL)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(C_NAVY)),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor(C_GOLD)),
        # Data row
        ("FONTNAME", (0, 1), (-1, 1), FONT_BOLD),
        ("FONTSIZE", (0, 1), (-1, 1), 11),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor(C_NAVY)),
        # Common
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.3*cm))

    # Expected Value strip
    mid_m = r.get("mid_cycle_margin", model.ebit_margin)
    m_range = r.get("margin_range", (mid_m, mid_m))
    ev_data = [
        ["Expected Value", "Entry (20% MoS)", "Mid-Cycle Margin"],
        [f"{sc['expected_value']:,.1f}  ({sc['expected_upside']:+.1%})",
         f"{sc['margin_of_safety_price']:,.1f}  ({sc['margin_of_safety_upside']:+.1%})",
         f"{mid_m:.1%}  (Range: {m_range[0]:.1%}–{m_range[1]:.1%})"]
    ]
    ev_tbl = Table(ev_data, colWidths=[5.8*cm]*3)
    ev_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_BG_LIGHT)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(C_GRAY_DARK)),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), FONT_BOLD),
        ("FONTSIZE", (0, 1), (-1, 1), 10),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor(C_NAVY)),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
        ("TOPPADDING", (0, 1), (-1, 1), 5),
    ]))
    story.append(ev_tbl)
    story.append(Spacer(1, 0.4*cm))

    # Scenario Fan + TV Pie side by side
    story.extend(_section_header("Scenario Fan & TV Decomposition"))
    fan_png = _fig_to_png(_build_scenario_fan(r, model, C_TEAL, C_CORAL, C_AMBER, C_GREEN, C_RED), w=700, h=400)
    pie_png = _fig_to_png(_build_tv_pie(r, C_TEAL, C_CORAL), w=500, h=400)
    fan_img = RLImage(BytesIO(fan_png), width=10.6*cm, height=6.0*cm)
    pie_img = RLImage(BytesIO(pie_png), width=7.0*cm, height=5.6*cm)
    chart_tbl = Table([[fan_img, pie_img]], colWidths=[10.8*cm, 7.2*cm])
    chart_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(chart_tbl)
    story.append(Paragraph(
        f"Expected Value = 25%×Bear + 50%×Base + 25%×Bull. Entry = Expected × 80%. "
        f"{'TV > 60%, sensitive to assumptions.' if tv_pct > 0.6 else 'Healthy TV split.'}",
        small))

    story.append(PageBreak())

    # ══ PAGE 2: PLAUSIBILITY + INPUTS + SENSITIVITY ══════════════════════════
    story.extend(_section_header("Plausibility Checks"))
    plaus_data = [["Flag", "Check", "Implied", "Historical", "Ratio"]]
    flag_map = {"🟢": "OK", "🟡": "WARN", "🔴": "FAIL"}
    for c in r["plausibility"]:
        plaus_data.append([flag_map.get(c["flag"], c["flag"]),
            c["check"], c["implied"], c["historical"], c["ratio"]])
    plaus_tbl = Table(plaus_data, colWidths=[1.6*cm, 4.0*cm, 2.6*cm, 2.6*cm, 2.4*cm])
    plaus_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
    ]
    for i, c in enumerate(r["plausibility"], start=1):
        if c["flag"] == "🔴":
            plaus_style.append(("BACKGROUND", (0, i), (0, i), colors.HexColor(C_RED)))
            plaus_style.append(("TEXTCOLOR", (0, i), (0, i), colors.white))
        elif c["flag"] == "🟡":
            plaus_style.append(("BACKGROUND", (0, i), (0, i), colors.HexColor(C_AMBER)))
        elif c["flag"] == "🟢":
            plaus_style.append(("BACKGROUND", (0, i), (0, i), colors.HexColor(C_GREEN)))
            plaus_style.append(("TEXTCOLOR", (0, i), (0, i), colors.white))
    plaus_tbl.setStyle(TableStyle(plaus_style))
    story.append(plaus_tbl)
    story.append(Spacer(1, 0.4*cm))

    # Model Inputs
    story.extend(_section_header("Model Inputs"))
    inputs_lines = [
        f"<b>Base Revenue:</b> {model.base_revenue:,.0f} · "
        f"<b>EBIT Margin:</b> {model.ebit_margin:.1%} (Mid-Cycle: {model.mid_cycle_margin:.1%})",
        f"<b>FCFF:</b> {model.base_fcff:,.0f} ({model.base_fcff/model.base_revenue:.1%} margin) · "
        f"<b>FCFF/Share:</b> {model.base_fcff_per_share:,.2f}",
        f"<b>D&amp;A:</b> {model.da_pct:.1%} · <b>CapEx:</b> {model.capex_pct:.1%} · "
        f"<b>SBC:</b> {model.sbc_pct:.1%} · <b>Tax:</b> {model.tax_rate:.1%}",
        f"<b>DSO:</b> {model.dso:.0f} days · <b>DPI:</b> {model.dpi:.0f} days · "
        f"<b>NWC/Rev:</b> {model.nwc_change_pct:.1%}",
        f"<b>MCap:</b> {model.market_cap:,.0f} · <b>Net Debt:</b> {model.net_debt:,.0f} "
        f"(Lease: {model.lease_liab:,.0f}) · <b>EV:</b> {model.market_ev:,.0f}",
        f"<b>Consensus FY1:</b> {model.consensus_growth_fy1:+.1%} · "
        f"<b>FY2:</b> {model.consensus_growth_fy2:+.1%}",
    ]
    for ln in inputs_lines:
        story.append(Paragraph(ln, body))
    story.append(Spacer(1, 0.4*cm))

    # Sensitivity table (recompute as in app)
    story.extend(_section_header("Sensitivity: Implied Growth (WACC × Tg)"))
    w_rng = np.arange(max(0.03, wacc-0.02), wacc+0.025, 0.005)
    t_rng = np.arange(max(0.005, tg-0.01), tg+0.015, 0.005)
    sens_header = [""] + [f"Tg={t:.1%}" for t in t_rng]
    sens_data = [sens_header]
    for w in w_rng:
        row = [f"{w:.1%}"]
        for t in t_rng:
            cfg = DCFConfig(wacc=w, terminal_growth=t, implied_years=proj_years,
                use_midcycle_margin=model.config.use_midcycle_margin)
            m2 = CoreDCF(model.hist, model.current, cfg, ticker=r["ticker"])
            row.append(f"{m2.solve_implied_growth():.1%}")
        sens_data.append(row)
    sens_tbl = Table(sens_data, colWidths=[1.7*cm] + [2.0*cm]*len(t_rng))
    sens_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(C_TEAL)),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
    ]
    sens_tbl.setStyle(TableStyle(sens_style))
    story.append(sens_tbl)

    # Warnings if any
    if r.get("warnings"):
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph("Data Notes", h3))
        for w in r["warnings"]:
            story.append(Paragraph(f"• {w}", small))

    story.append(PageBreak())

    # ══ PAGE 3: QUALITY & MULTIPLES ═══════════════════════════════════════════
    story.extend(_section_header("Quality & Multiples"))
    story.append(Spacer(1, 0.2*cm))

    # Quality + C-Score side-by-side
    qual_lines = [
        Paragraph(f"<b>Quality Grade: {q.grade}</b>", h3),
        Paragraph(f"ROIC (median): <b>{q.roic_median:.1%}</b> ({q.roic_trend})", body),
        Paragraph(f"Margin Stability: <b>{q.margin_stability:.2%}</b> std dev", body),
        Paragraph(f"Revenue Volatility: <b>{q.revenue_volatility:.1%}</b> std dev", body),
        Paragraph(f"FCF Conversion (CFO/NI): <b>{q.fcf_conversion:.2f}x</b>", body),
        Paragraph(f"Payout Ratio: <b>{q.payout_avg:.0%}</b>", body),
        Paragraph(f"Debt/EBITDA: <b>{q.debt_ebitda:.1f}x</b>", body),
    ]
    cscore_lines = [Paragraph(f"<b>C-Score: {q.c_score.total}/5</b>", h3),
                    Paragraph("Lower = better quality. Each flag = 1 point.", small)]
    for k, v in q.c_score.details.items():
        is_bad = not v.startswith("OK") and not v.startswith("Skipped")
        marker = "[FAIL]" if is_bad else "[OK]"
        color = C_RED if is_bad else C_GREEN
        cscore_lines.append(Paragraph(
            f"<font color='{color}'><b>{marker}</b></font> <b>{k}</b>: {v}", body))

    ql_tbl = Table([[qual_lines, cscore_lines]], colWidths=[9.0*cm, 9.0*cm])
    ql_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(ql_tbl)
    story.append(Spacer(1, 0.4*cm))

    # Historical multiples chart + table
    story.extend(_section_header("Historical Multiples"))
    mult_fig = _build_multiples_chart(r, C_TEAL, C_CORAL)
    if mult_fig is not None:
        mult_png = _fig_to_png(mult_fig, w=900, h=320)
        story.append(RLImage(BytesIO(mult_png), width=18.0*cm, height=6.4*cm))
        story.append(Spacer(1, 0.2*cm))

        # Table
        hm = r["historical_multiples"]
        hm_cols = [c for c in ["P/E", "EV/EBITDA", "P/Sales", "FCF Yield"] if c in hm.columns]
        hm_data = [["Year"] + hm_cols]
        for yr in hm.index:
            row = [str(yr)]
            for col in hm_cols:
                v = hm.loc[yr, col]
                if pd.isna(v):
                    row.append("—")
                elif col == "FCF Yield":
                    row.append(f"{v:.1%}")
                else:
                    row.append(f"{v:.1f}x")
            hm_data.append(row)
        hm_tbl = Table(hm_data, colWidths=[1.8*cm] + [3.4*cm]*len(hm_cols))
        hm_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ]))
        story.append(hm_tbl)
        story.append(Spacer(1, 0.3*cm))

        # Summary stats — kept together to avoid orphan single line on next page
        ranges_block = [Paragraph("Multiple Ranges", h3)]
        for col in ["P/E", "EV/EBITDA", "P/Sales", "FCF Yield"]:
            if col in hm:
                s = hm[col].dropna()
                if len(s) >= 3:
                    fmt = "{:.1f}x" if col != "FCF Yield" else "{:.1%}"
                    ranges_block.append(Paragraph(
                        f"<b>{col}</b>: Current {fmt.format(s.iloc[-1])} · "
                        f"Median {fmt.format(s.median())} · "
                        f"Range {fmt.format(s.min())}–{fmt.format(s.max())}", body))
        story.append(KeepTogether(ranges_block))

    story.append(Spacer(1, 0.4*cm))

    # ══ RETURN DECOMPOSITION ═══════════════════════════════════════════════════
    story.extend(_section_header("Return Decomposition"))
    rd = r["return_decomposition"]
    if rd.get("available"):
        story.append(Paragraph(
            f"Stock Return Breakdown ({rd['start_year']}–{rd['end_year']})", h2))

        rd_png = _fig_to_png(_build_return_decomp(rd, C_TEAL, C_GREEN, C_RED), w=900, h=400)
        story.append(RLImage(BytesIO(rd_png), width=18.0*cm, height=8.0*cm))
        story.append(Spacer(1, 0.3*cm))

        is_dilution = rd["buyback_ann"] < -0.05
        story.append(Paragraph("Annualized Components", h3))
        comp_lines = [
            f"Revenue Growth: <b>{rd['revenue_growth_ann']:+.1%}</b>",
            f"Margin Effect: <b>{rd['margin_effect_ann']:+.1%}</b> "
            f"({rd['margin_first']:.1%} → {rd['margin_last']:.1%})",
        ]
        if is_dilution:
            comp_lines.append(
                f"Dilution: <b>{rd['buyback_ann']:+.1%}</b> "
                f"({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f} shares)")
        else:
            comp_lines.append(
                f"Buyback Yield: <b>{rd['buyback_ann']:+.1%}</b> "
                f"({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f})")
        comp_lines.append(f"Dividend Yield: <b>{rd['dividend_yield']:.1%}</b>")
        comp_lines.append(
            f"Multiple Expansion: <b>{rd['multiple_expansion_ann']:+.1%}</b>"
            f"{' (unreliable due to dilution)' if is_dilution else ''}")
        comp_lines.append(f"<b>Total Return: {rd['total_return_ann']:+.1%} p.a.</b>")
        comp_lines.append(f"Price: {rd['price_first']:,.1f} → {rd['price_last']:,.1f}")
        for ln in comp_lines:
            story.append(Paragraph(f"• {ln}", body))

        # Quality interpretation
        fundamental = rd["revenue_growth_ann"] + rd["margin_effect_ann"]
        multiple = rd["multiple_expansion_ann"]; total = rd["total_return_ann"]
        if abs(total) > 0.005 and not is_dilution:
            story.append(Spacer(1, 0.2*cm))
            if multiple > 0.02:
                story.append(Paragraph(
                    f"<b>WARNING:</b> {multiple/total*100:.0f}% of return from multiple expansion, "
                    f"not sustainable.", body))
            elif multiple < -0.02:
                story.append(Paragraph(
                    f"<b>NOTE:</b> Multiple contracted {multiple:.1%} p.a., "
                    f"fundamentals outperformed the stock.", body))
            if total > 0 and fundamental / total > 0.7:
                story.append(Paragraph(
                    f"<b>QUALITY:</b> {fundamental/total*100:.0f}% of return from fundamentals, "
                    f"high quality.", body))
    else:
        story.append(Paragraph("Return decomposition not available. "
            "Need historical Price (YE) data in Fundamentals sheet.", body))

    story.append(PageBreak())

    # ══ PAGE 5: PEERS ══════════════════════════════════════════════════════════
    story.extend(_section_header("Peer Comparison"))
    peers = r.get("peers", [])
    if peers:
        # Peer table
        own_key = model.ticker.split()[0] if model.ticker else ""
        peer_cols = ["P/E", "EV/EBITDA", "P/Sales", "Div Yld", "ROIC", "Gross Mrg", "EBIT Mrg"]
        peer_data = [["Ticker", "Name"] + peer_cols]
        for p in peers:
            row = [p.get("ticker", ""), str(p.get("name", ""))[:24]]
            for col in peer_cols:
                v = p.get(col)
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    row.append("—")
                elif col in ("P/E", "EV/EBITDA", "P/Sales"):
                    row.append(f"{v:.1f}x")
                else:
                    row.append(f"{v:.1f}%")
            peer_data.append(row)
        peer_tbl = Table(peer_data, colWidths=[2.0*cm, 3.6*cm] + [1.7*cm]*len(peer_cols))
        ptbl_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (1, 1), (1, -1), "LEFT"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ]
        # Highlight main row
        for i, p in enumerate(peers, start=1):
            if own_key and own_key in p.get("ticker", ""):
                ptbl_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff4ec")))
                ptbl_style.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
        peer_tbl.setStyle(TableStyle(ptbl_style))
        story.append(peer_tbl)
        story.append(Spacer(1, 0.4*cm))

        # vs Peer Average
        pdf_p = pd.DataFrame(peers)
        if own_key:
            main_p = pdf_p[pdf_p["ticker"].str.contains(own_key)].head(1)
            peer_only = pdf_p[~pdf_p["ticker"].str.contains(own_key)]
            if len(peer_only) > 0 and len(main_p) > 0:
                story.extend(_section_header("vs Peer Average"))
                for metric in ["P/E", "EV/EBITDA", "P/Sales", "ROIC", "Gross Mrg", "EBIT Mrg"]:
                    vals = peer_only[metric].dropna() if metric in peer_only else pd.Series(dtype=float)
                    if len(vals) > 0 and metric in main_p and main_p[metric].notna().any():
                        avg = vals.mean(); own = float(main_p[metric].iloc[0])
                        diff = own - avg
                        fmt = "{:.1f}x" if metric in ["P/E", "EV/EBITDA", "P/Sales"] else "{:.1f}%"
                        prem = "premium" if diff > 0 else "discount"
                        story.append(Paragraph(
                            f"<b>{metric}</b>: {fmt.format(own)} vs Peer Avg "
                            f"{fmt.format(avg)} ({prem}: {abs(diff):.1f})", body))
                story.append(Spacer(1, 0.3*cm))

        # Charts
        if len(peers) > 1:
            story.extend(_section_header("Valuation Comparison"))
            for metric, fmt in [("P/E", "x"), ("EV/EBITDA", "x")]:
                pf = _build_peer_chart(peers, model.ticker, metric, fmt, C_TEAL, C_CORAL)
                if pf is not None:
                    pf_png = _fig_to_png(pf, w=900, h=300)
                    story.append(RLImage(BytesIO(pf_png), width=16.0*cm, height=5.4*cm))
                    story.append(Spacer(1, 0.2*cm))
    else:
        story.append(Paragraph("No peer data found. Add peer tickers in the Peers sheet.", body))

    story.append(PageBreak())

    # ══ PAGE 6: FORWARD DCF ═══════════════════════════════════════════════════
    story.extend(_section_header("Forward DCF"))
    story.append(Paragraph("My View vs Market: Fair Value Estimate", h3))
    story.append(Spacer(1, 0.2*cm))

    # Recompute Forward DCF using cached edited DataFrame
    try:
        pv_e = 0.0; proj_rows = []; rev = model.base_revenue
        for i in range(n_fwd):
            col = f"Y{i+1}"
            g = float(edited_df.loc["Revenue Growth (%)", col]) / 100
            m = float(edited_df.loc["EBIT Margin (%)", col]) / 100
            cx = float(edited_df.loc["CapEx/Rev (%)", col]) / 100
            da = float(edited_df.loc["D&A/Rev (%)", col]) / 100
            sbc = float(edited_df.loc["SBC/Rev (%)", col]) / 100
            tx = float(edited_df.loc["Tax Rate (%)", col]) / 100
            rev *= (1 + g); ebit = rev * m; nopat = ebit * (1 - tx)
            fcff = nopat + rev*da - rev*cx - rev*sbc
            pv = fcff / (1 + wacc)**(i+1); pv_e += pv
            proj_rows.append({"Year": col, "Revenue": rev, "Growth": g,
                "EBIT Margin": m, "EBIT": ebit, "FCFF": fcff, "PV": pv})

        tg_f = float(edited_df.loc["Revenue Growth (%)", "Terminal"]) / 100
        tm_f = float(edited_df.loc["EBIT Margin (%)", "Terminal"]) / 100
        cx_f = float(edited_df.loc["CapEx/Rev (%)", "Terminal"]) / 100
        da_f = float(edited_df.loc["D&A/Rev (%)", "Terminal"]) / 100
        sbc_f = float(edited_df.loc["SBC/Rev (%)", "Terminal"]) / 100
        tx_f = float(edited_df.loc["Tax Rate (%)", "Terminal"]) / 100
        t_rev = rev * (1 + tg_f); t_nopat = t_rev * tm_f * (1 - tx_f)
        t_fcff = t_nopat + t_rev*da_f - t_rev*cx_f - t_rev*sbc_f

        if wacc <= tg_f:
            story.append(Paragraph("<b>ERROR:</b> WACC must exceed Terminal Growth.", body))
        else:
            tv_v = t_fcff / (wacc - tg_f); pv_tv = tv_v / (1 + wacc)**n_fwd
            tot_ev = pv_e + pv_tv
            fair_eq = tot_ev - model.net_debt - model.minority
            fp = fair_eq / model.shares if model.shares else 0
            up = fp / model.price - 1 if model.price else 0

            if up > 0.20: fv, fc = "UNDERVALUED", C_GREEN
            elif up > 0.05: fv, fc = "SLIGHT UPSIDE", C_GREEN
            elif up > -0.05: fv, fc = "FAIRLY VALUED", C_AMBER
            elif up > -0.20: fv, fc = "SLIGHT DOWNSIDE", C_CORAL
            else: fv, fc = "OVERVALUED", C_RED

            story.append(Paragraph(fv, verdict_style(fc)))
            story.append(Paragraph(
                f"Fair value: <b>{fp:,.1f}</b> vs {model.price:,.2f} → <b>{up:+.1%}</b>", body))
            story.append(Spacer(1, 0.2*cm))

            # KPI strip
            fwd_kpi = [["Current", "Fair Value", "EV", "TV %"],
                       [f"{model.price:,.2f}", f"{fp:,.1f}  ({up:+.1%})",
                        f"{tot_ev:,.0f}", f"{pv_tv/tot_ev:.0%}"]]
            fwd_tbl = Table(fwd_kpi, colWidths=[4.5*cm]*4)
            fwd_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ]))
            story.append(fwd_tbl)
            story.append(Spacer(1, 0.4*cm))

            # My View vs Market
            story.extend(_section_header("My View vs Market"))
            avg_g = np.mean([float(edited_df.loc["Revenue Growth (%)", f"Y{i+1}"])
                            for i in range(n_fwd)]) / 100
            last_m = float(edited_df.loc["EBIT Margin (%)", f"Y{n_fwd}"]) / 100
            cmp_png = _fig_to_png(
                _build_fwd_compare(avg_g, last_m, ig, model.ebit_margin, C_TEAL, C_CORAL),
                w=900, h=300)
            story.append(RLImage(BytesIO(cmp_png), width=16.0*cm, height=5.4*cm))
            story.append(Spacer(1, 0.3*cm))

            # Cash flow table
            story.extend(_section_header("Projected Cash Flows"))
            cf_data = [["Year", "Revenue", "Growth", "EBIT Mrg", "EBIT", "FCFF", "PV"]]
            for row_p in proj_rows:
                cf_data.append([row_p["Year"], f"{row_p['Revenue']:,.0f}",
                    f"{row_p['Growth']:.1%}", f"{row_p['EBIT Margin']:.1%}",
                    f"{row_p['EBIT']:,.0f}", f"{row_p['FCFF']:,.0f}", f"{row_p['PV']:,.0f}"])
            cf_data.append(["Terminal", f"{t_rev:,.0f}", f"{tg_f:.1%}", f"{tm_f:.1%}",
                f"{t_rev*tm_f:,.0f}", f"{t_fcff:,.0f}", f"{pv_tv:,.0f}"])
            cf_tbl = Table(cf_data, colWidths=[1.6*cm, 2.6*cm, 1.6*cm, 1.8*cm, 2.6*cm, 2.6*cm, 2.6*cm])
            cf_style = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fff4ec")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
            cf_tbl.setStyle(TableStyle(cf_style))
            story.append(cf_tbl)
            story.append(Spacer(1, 0.4*cm))

            # Bridge
            story.append(PageBreak())
            story.extend(_section_header("Forward DCF (continued)"))
            story.extend(_section_header("Valuation Bridge"))
            cur_ev = model.market_ev
            ev_from_rev = cur_ev * (rev / model.base_revenue - 1)
            ev_from_m = (cur_ev * ((float(edited_df.loc["EBIT Margin (%)", f"Y{n_fwd}"]) / 100)
                / model.ebit_margin - 1)) if model.ebit_margin else 0
            ev_res = tot_ev - cur_ev - ev_from_rev - ev_from_m
            br_png = _fig_to_png(
                _build_bridge(cur_ev, ev_from_rev, ev_from_m, ev_res, tot_ev, C_TEAL, C_GREEN, C_RED),
                w=900, h=350)
            story.append(RLImage(BytesIO(br_png), width=18.0*cm, height=7.0*cm))
            story.append(Spacer(1, 0.2*cm))

            br_lines = [
                f"PV Explicit: <b>{pv_e:,.0f}</b>",
                f"PV Terminal: <b>{pv_tv:,.0f}</b>",
                f"= EV: <b>{tot_ev:,.0f}</b>",
                f"− Net Debt: {model.net_debt:,.0f}",
                f"= Equity: <b>{fair_eq:,.0f}</b> ÷ {model.shares:,.1f} = <b>{fp:,.1f}</b>",
            ]
            for ln in br_lines:
                story.append(Paragraph(f"• {ln}", body))
            story.append(Spacer(1, 0.4*cm))

            # Implied Multiples
            story.extend(_section_header("Implied Multiples & Plausibility"))
            last_row = proj_rows[-1]
            last_ni = last_row["EBIT"] * (1 - float(
                edited_df.loc["Tax Rate (%)", f"Y{n_fwd}"]) / 100)
            impl = model.implied_multiples(tot_ev,
                projected_revenue=last_row["Revenue"],
                projected_ebit=last_row["EBIT"],
                projected_ebitda=last_row.get("EBITDA", last_row["EBIT"] * 1.3),
                projected_ni=last_ni)
            hm = r["historical_multiples"]
            im_data = [["Metric", "Implied", "Hist Median", "Verdict"]]
            for metric, hist_col in [("implied_EV/EBIT", "EV/EBITDA"),
                                       ("implied_P/E", "P/E"),
                                       ("implied_P/Sales", "P/Sales")]:
                val = impl.get(metric)
                if val and not np.isnan(val):
                    h_med = (hm[hist_col].dropna().median()
                        if hist_col in hm and len(hm[hist_col].dropna()) > 0 else None)
                    label = metric.replace("implied_", "")
                    if h_med and not np.isnan(h_med):
                        verd = "STRETCHED" if val > h_med * 1.2 else (
                            "CHEAP" if val < h_med * 0.8 else "in line")
                        im_data.append([label, f"{val:.1f}x", f"{h_med:.1f}x", verd])
                    else:
                        im_data.append([label, f"{val:.1f}x", "—", "—"])
            im_tbl = Table(im_data, colWidths=[3.5*cm, 3.0*cm, 3.0*cm, 4.0*cm])
            im_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_TEAL)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ]))
            story.append(im_tbl)
            story.append(Spacer(1, 0.4*cm))

            # Margin of Safety
            mos_price = fp * 0.80
            mos_up = mos_price / model.price - 1 if model.price else 0
            mos_note = ("Current price is below entry target."
                if model.price < mos_price else "Wait for better entry.")
            story.append(Paragraph(
                f"<b>Entry Target (20% Margin of Safety):</b> {mos_price:,.1f} "
                f"({mos_up:+.1%} from current), {mos_note}", body))
    except Exception as e:
        story.append(Paragraph(f"<b>Forward DCF error:</b> {e}", body))

    doc.build(story)
    return buf.getvalue()


# ── Sidebar PDF Export Button ────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### 📄 Report")

if st.sidebar.button("Generate PDF", use_container_width=True):
    edited_df = st.session_state.get("fwd_edited")
    n_fwd = st.session_state.get("fwd_n")
    if edited_df is None or n_fwd is None:
        st.sidebar.warning("Open the Forward DCF tab once to initialize assumptions, "
            "then re-click.")
    else:
        ai_summary = None
        # Generate Senior Commentary if toggle is on
        if ai_commentary and ai_available:
            cache_key_cmt = f"ai_cmt_{r['ticker']}_{round(wacc,4)}_{round(tg,4)}"
            if cache_key_cmt not in st.session_state:
                with st.spinner("AI: Crafting Investment Committee memo..."):
                    from ai_layer import senior_commentary
                    # Compute current Forward DCF result for context
                    fwd_result = None
                    try:
                        pv_e_tmp = 0.0; rev_tmp = model.base_revenue
                        for i in range(n_fwd):
                            colY = f"Y{i+1}"
                            g_=float(edited_df.loc["Revenue Growth (%)",colY])/100
                            m_=float(edited_df.loc["EBIT Margin (%)",colY])/100
                            cx_=float(edited_df.loc["CapEx/Rev (%)",colY])/100
                            da_=float(edited_df.loc["D&A/Rev (%)",colY])/100
                            sbc_=float(edited_df.loc["SBC/Rev (%)",colY])/100
                            tx_=float(edited_df.loc["Tax Rate (%)",colY])/100
                            rev_tmp*=(1+g_); ebit_=rev_tmp*m_; nopat_=ebit_*(1-tx_)
                            fcff_=nopat_+rev_tmp*da_-rev_tmp*cx_-rev_tmp*sbc_
                            pv_e_tmp+=fcff_/(1+wacc)**(i+1)
                        tg_f_=float(edited_df.loc["Revenue Growth (%)","Terminal"])/100
                        tm_f_=float(edited_df.loc["EBIT Margin (%)","Terminal"])/100
                        cx_f_=float(edited_df.loc["CapEx/Rev (%)","Terminal"])/100
                        da_f_=float(edited_df.loc["D&A/Rev (%)","Terminal"])/100
                        sbc_f_=float(edited_df.loc["SBC/Rev (%)","Terminal"])/100
                        tx_f_=float(edited_df.loc["Tax Rate (%)","Terminal"])/100
                        t_rev_=rev_tmp*(1+tg_f_); t_nopat_=t_rev_*tm_f_*(1-tx_f_)
                        t_fcff_=t_nopat_+t_rev_*da_f_-t_rev_*cx_f_-t_rev_*sbc_f_
                        if wacc > tg_f_:
                            tv_v_=t_fcff_/(wacc-tg_f_); pv_tv_=tv_v_/(1+wacc)**n_fwd
                            tot_ev_=pv_e_tmp+pv_tv_
                            fair_eq_=tot_ev_-model.net_debt-model.minority
                            fp_=fair_eq_/model.shares if model.shares else 0
                            up_=fp_/model.price-1 if model.price else 0
                            fwd_result = {"fair_price": fp_, "upside": up_,
                                "verdict": ("UNDERVALUED" if up_>0.20 else "FAIRLY VALUED"
                                    if abs(up_)<0.05 else "OVERVALUED" if up_<-0.20 else "MIXED")}
                    except Exception:
                        pass
                    st.session_state[cache_key_cmt] = senior_commentary(
                        api_key, model, r, forward_dcf_results=fwd_result)
            ai_summary = st.session_state.get(cache_key_cmt)

        with st.spinner("Building PDF..."):
            try:
                pdf_bytes = build_pdf(model, r, wacc, tg, proj_years, edited_df, n_fwd,
                                     ai_summary=ai_summary)
                kb = len(pdf_bytes)/1024
                ai_tag = " [AI]" if ai_summary else ""
                st.sidebar.success(f"✓ {kb:.0f} KB{ai_tag}")
                st.sidebar.download_button(
                    "⬇ Download PDF",
                    data=pdf_bytes,
                    file_name=f"CORE_DCF_{r['ticker'].replace(' ', '_')}_{pd.Timestamp.now():%Y%m%d}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception as e:
                st.sidebar.error(f"PDF error: {e}")
                import traceback
                with st.expander("Traceback"):
                    st.code(traceback.format_exc())

st.sidebar.caption("Tip: Forward DCF tab assumptions feed into the PDF.")
