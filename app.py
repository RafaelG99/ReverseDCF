"""CORE DCF Dashboard — 4-Stage Reverse DCF + Forward DCF"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from reverse_dcf_engine import CoreDCF, DCFConfig
import guidance_dcf as gd
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
        try:
            model.guidance = gd.read_guidance_sheet(pd.ExcelFile(tmp_path))
        except Exception as _ge:
            model.guidance = None; st.sidebar.warning(f"Guidance sheet ignored: {_ge}")
        st.sidebar.success(f"Loaded: {model.ticker}" + (" · Guidance ✓" if getattr(model, "guidance", None) else ""))
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
if getattr(model, "has_consensus_fy3", False):
    cons_years = st.sidebar.radio("Consensus Years (Stage 1)", [2, 3], index=0, horizontal=True,
        help="3 = also use BEST_SALES_3BF. Relevant for launch/ramp cases where FY3 consensus differs strongly from FY2.")
else:
    cons_years = 2
use_bbg_ev = st.sidebar.checkbox("Use BBG EV (incl. adjustments)", value=True,
    help="ON: solver targets Bloomberg ENTERPRISE_VALUE (pension/other adjustments treated as debt-like in all per-share conversions). "
         "OFF: EV = MCap + Net Debt + Minorities. Switch OFF if the BBG adjustment looks spurious (FX mix, contingent items).")
use_midcycle = st.sidebar.checkbox("Use Mid-Cycle Margin for Base FCFF", value=True,
    help="ON (default): Base FCFF normalized to mid-cycle margin (trimmed mean last 7Y). "
         "OFF: Base FCFF uses current margin (peak/trough sensitive).")

# ── AI Layer Controls ─────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### 🤖 AI Layer (Claude Opus 4.7)")
try:
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
except Exception:          # no secrets.toml (local run without AI layer)
    api_key = ""
ai_available = bool(api_key)
if not ai_available:
    st.sidebar.caption("⚠ No API key. Add ANTHROPIC_API_KEY in secrets.toml.")
else:
    st.sidebar.caption(f"✓ API Key loaded ({api_key[:12]}...{api_key[-4:]})")

# Try to import anthropic package — show error if missing
try:
    import anthropic as _anthropic_check
    _anthropic_version = _anthropic_check.__version__
    st.sidebar.caption(f"✓ anthropic SDK v{_anthropic_version}")
except ImportError as _e:
    st.sidebar.error(f"❌ anthropic package not installed: {_e}")
    ai_available = False

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
                try:
                    from ai_layer import smart_wacc_tg
                    result = smart_wacc_tg(api_key, model)
                    if result is None:
                        st.sidebar.warning("⚠ AI returned no result (parse failure or API error). "
                                           "Check Manage app → Logs.")
                    st.session_state[cache_key] = result
                except Exception as _e:
                    st.sidebar.error(f"❌ AI call failed: {type(_e).__name__}: {str(_e)[:200]}")
                    st.session_state[cache_key] = None
    ai_wacc_tg = st.session_state.get(cache_key)
    if ai_wacc_tg:
        st.sidebar.caption(f"💡 AI suggests WACC {ai_wacc_tg.get('wacc_recommended', 0):.2%}, "
                          f"Tg {ai_wacc_tg.get('tg_recommended', 0):.2%}")

model.config.wacc = wacc
model.config.terminal_growth = tg
model.config.implied_years = proj_years
model.config.consensus_years = cons_years
model.config.use_midcycle_margin = use_midcycle
model.config.use_bbg_ev = use_bbg_ev
model._prepare()
r = model.run()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🔍 Reverse DCF", "📊 Quality & Multiples", "📈 Return Decomposition", "👥 Peers", "🎯 Forward DCF", "🧭 Guidance DCF"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: REVERSE DCF
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    ig = r["implied_growth"]; c5 = r["cagr_5y"]; c3 = r["cagr_3y"]; mx = r["max_growth"]
    tv_pct = r["tv_decomposition"]["tv_pct"]
    spread = wacc - tg; roic_sp = r["roic_spread"]
    red_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🔴")

    # Check if engine flagged the result as unreliable (negative FCFF, bound-pinning)
    ig_unreliable = getattr(model, "_ig_unreliable", False)
    ig_unreliable_reason = getattr(model, "_ig_unreliable_reason", None)

    # Verdict
    if ig_unreliable:
        verdict, v_color = "⚠ MODEL LIMITATION", C_RED
        v_action = (f"{ig_unreliable_reason}. Reverse DCF cannot solve meaningfully here. "
                    f"Use Forward DCF tab or alternative valuation framework.")
    elif spread < 0.02:
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
    if ig_unreliable:
        ig_line = "Reverse DCF cannot solve meaningfully — use Forward DCF tab."
    else:
        ig_line = f"Market implies <b>{ig:.1%} p.a. FCF growth</b> (Y3-{2+proj_years}) to justify {r['price']:,.2f}."
    st.markdown(f"""<div style="background:{v_color}15;border-left:5px solid {v_color};padding:20px 24px;border-radius:4px;margin-bottom:20px;">
        <span style="font-size:28px;font-weight:bold;color:{v_color};">{verdict}</span><br>
        <span style="font-size:18px;color:#333;">{ig_line}</span>
        <br><span style="font-size:15px;color:#444;"><b>So what?</b> {v_action}</span>
    </div>""", unsafe_allow_html=True)

    k1,k2,k3,k4,k5,k6 = st.columns(6)
    k1.metric("Price", f"{r['price']:,.2f}")
    if ig_unreliable:
        k2.metric("Implied Growth", "n/a", delta="UNRELIABLE", delta_color="inverse")
    else:
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
        fy3_txt = f" · FY3: {model.consensus_growth_fy3:+.1%}{'' if cons_years == 3 else ' (unused)'}" if getattr(model, "has_consensus_fy3", False) else ""
        st.write(f"Consensus FY1: {model.consensus_growth_fy1:+.1%} · FY2: {model.consensus_growth_fy2:+.1%}{fy3_txt}")
        if getattr(model, "_ev_adjustment", 0):
            st.write(f"EV source: BBG · adjustment vs MCap+NetDebt: {model._ev_adjustment:+,.0f} (treated as debt-like claim in all per-share conversions)")

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
            
            margin_nm = rd.get("margin_nm", False)
            mult_label = "Multiple<br>& Margin" if margin_nm else "Multiple<br>Expansion"
            comps = [("Revenue<br>Growth",rd["revenue_growth_ann"]),("Margin<br>Effect",rd["margin_effect_ann"]),
                (bb_label,rd["buyback_ann"]),("Dividend",rd["dividend_yield"]),(mult_label,rd["multiple_expansion_ann"])]
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
            if margin_nm:
                st.write(f"📊 Margin Effect: **n/m** ({rd['margin_first']:.1%} → {rd['margin_last']:.1%} crosses zero; folded into multiple)")
            else:
                st.write(f"📊 Margin Effect: **{rd['margin_effect_ann']:+.1%}** ({rd['margin_first']:.1%} → {rd['margin_last']:.1%})")
            if is_dilution:
                st.write(f"🔻 Dilution: **{rd['buyback_ann']:+.1%}** ({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f} shares)")
                st.caption("⚠️ Shares increased significantly (IPO, capital raises, M&A). Multiple Expansion below is distorted.")
            else:
                st.write(f"🔄 Buyback Yield: **{rd['buyback_ann']:+.1%}** ({rd['shares_first']:,.0f} → {rd['shares_last']:,.0f})")
            st.write(f"💰 Dividend Yield: **{rd['dividend_yield']:.1%}**")
            st.write(f"📐 {'Multiple & Margin (residual)' if margin_nm else 'Multiple Expansion'}: **{rd['multiple_expansion_ann']:+.1%}**{'  ⚠️ unreliable (dilution distortion)' if is_dilution else ''}")
            st.markdown("---")
            st.write(f"**Total Return: {rd['total_return_ann']:+.1%} p.a.**")
            st.write(f"Price: {rd['price_first']:,.1f} → {rd['price_last']:,.1f}")

            fundamental = rd["revenue_growth_ann"] + rd["margin_effect_ann"]
            financial = rd["buyback_ann"] + rd["dividend_yield"]
            multiple = rd["multiple_expansion_ann"]
            total = rd["total_return_ann"]
            if abs(total) > 0.005 and not is_dilution and not margin_nm:
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
            proj_rows.append({"Year":col,"Revenue":rev,"Growth":g,"EBIT Margin":m,"EBIT":ebit,"EBITDA":ebit+rev*da,"FCFF":fcff,"PV":pv})

        tg_f=float(edited.loc["Revenue Growth (%)","Terminal"])/100; tm_f=float(edited.loc["EBIT Margin (%)","Terminal"])/100
        cx_f=float(edited.loc["CapEx/Rev (%)","Terminal"])/100; da_f=float(edited.loc["D&A/Rev (%)","Terminal"])/100
        sbc_f=float(edited.loc["SBC/Rev (%)","Terminal"])/100; tx_f=float(edited.loc["Tax Rate (%)","Terminal"])/100
        t_rev=rev*(1+tg_f); t_nopat=t_rev*tm_f*(1-tx_f); t_fcff=t_nopat+t_rev*da_f-t_rev*cx_f-t_rev*sbc_f
        if wacc<=tg_f: st.error("WACC must exceed Terminal Growth!"); st.stop()
        tv_v=t_fcff/(wacc-tg_f); pv_tv=tv_v/(1+wacc)**n_fwd; tot_ev=pv_e+pv_tv
        fair_eq=model.ev_to_equity(tot_ev); fp=fair_eq/model.shares if model.shares else 0
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
            st.write(f"= EV: **{tot_ev:,.0f}**"); st.write(f"− Net Debt: {model.net_debt:,.0f} · Minorities: {model.minority:,.0f} · EV adj.: {getattr(model, '_ev_adjustment', 0):,.0f}")
            st.write(f"= Equity: **{fair_eq:,.0f}** ÷ {model.shares:,.1f} = **{fp:,.1f}**")
        with br:
            cur_ev=model.market_ev; cur_ebit=model.base_ebit or 1
            fin_ebit=proj_rows[-1]["EBIT"]
            ev_from_rev=cur_ev*(rev/model.base_revenue-1)
            # Margin leg only meaningful on a positive base margin; otherwise the ratio explodes
            # (e.g. -3% → 11.5% gives -483% "margin change"). Fold into residual instead.
            m_final=float(edited.loc["EBIT Margin (%)",f"Y{n_fwd}"])/100
            bridge_margin_ok = model.ebit_margin > 0.01 and m_final > 0
            ev_from_m=cur_ev*(m_final/model.ebit_margin-1) if bridge_margin_ok else 0.0
            ev_res=tot_ev-cur_ev-ev_from_rev-ev_from_m
            if not bridge_margin_ok:
                st.caption(f"⚠ Base EBIT margin {model.ebit_margin:.1%} ≤ 0: margin leg not meaningful, shown as 0 and folded into 'Multiple & Other'.")
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
        for col_w, metric, hist_col in [(im1,"implied_EV/EBITDA","EV/EBITDA"),(im2,"implied_P/E","P/E"),(im3,"implied_P/Sales","P/Sales")]:
            val = impl.get(metric)
            if val and not np.isnan(val):
                h_med = hm[hist_col].dropna().median() if hist_col in hm and len(hm[hist_col].dropna()) > 0 else None
                label = metric.replace("implied_","")
                if h_med and not np.isnan(h_med) and h_med > 0 and h_med < 60:
                    col_w.metric(label, f"{val:.1f}x",
                        delta=f"vs {h_med:.1f}x hist median",
                        delta_color="inverse" if val > h_med * 1.2 else "normal")
                elif h_med and not np.isnan(h_med):
                    col_w.metric(label, f"{val:.1f}x", delta=f"hist median {h_med:.0f}x n/m", delta_color="off")
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
# TAB 6: GUIDANCE DCF (segment-driven, management plan → implied plan fulfilment)
# ══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.title(f"Guidance DCF: {r['ticker']}")
    st.markdown("**Management plan (segments Base → Target) → fair value → what share of the plan does the price discount?** "
                "Cost structure is derived from the guidance (contribution margin × revenue − fixed cost base), not assumed as a margin.")

    gs = getattr(model, "guidance", None) or {}
    gp = gs.get("params", {}); gsegs = gs.get("segments", [])
    _last_year = int(model.hist.index[-1].year) if len(model.hist) else 2025
    if not gsegs:
        gsegs, _by, _ty = gd.default_segments_from_model(model.base_revenue, _last_year)
        st.info("No 'Guidance' sheet in the template — starting from a single 'Total' segment. "
                "Edit the segment table below or add a sheet 'Guidance' (see Readme in Excel template).")
    else:
        _by, _ty = int(gp.get("Base Year", _last_year)), int(gp.get("Target Year", _last_year + 5))

    g1, g2, g3, g4 = st.columns(4)
    base_year = g1.number_input("Base Year", value=_by, step=1, format="%d")
    target_year = g2.number_input("Target Year", value=_ty, step=1, format="%d")
    ebitda_base = g3.number_input("EBITDA Base (guidance)", value=float(gp.get("EBITDA Base", model.base_ebit + model.base_revenue*model.da_pct)), format="%.1f")
    ebitda_target = g4.number_input("EBITDA Target (guidance)", value=float(gp.get("EBITDA Target", ebitda_base*2)), format="%.1f")

    seg_df = pd.DataFrame([{"Segment": s.name, "Base": s.base, "Target": s.target, "Scalable": s.scalable,
                            "Ramp (% of target per year)": ",".join(f"{x*100:.0f}" for x in s.ramp) if s.ramp else ""} for s in gsegs])
    st.caption("Segments: **Scalable** = the plan-fulfilment lever applies (uncertain growth drivers). "
               "**Ramp** = optional S-curve as % of target per plan year (e.g. `0,5,21,53,100`); empty = geometric path.")
    seg_edit = st.data_editor(seg_df, num_rows="dynamic", use_container_width=True, key=f"guid_seg_{r['ticker']}",
        column_config={"Scalable": st.column_config.CheckboxColumn(), "Base": st.column_config.NumberColumn(format="%.1f"),
                       "Target": st.column_config.NumberColumn(format="%.1f")})

    with st.expander("Cash-flow bridge, fade & currency", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        fx = c1.number_input("FX guidance → price ccy", value=float(gp.get("FX", 1.0)), format="%.4f",
                             help="e.g. guidance in EUR, share price in CHF → EURCHF ≈ 0.94. 1.0 if same currency.")
        tax_g = c2.number_input("Tax rate", value=float(gp.get("Tax Rate", model.tax_rate)), format="%.3f")
        da_base = c3.number_input("D&A base year (abs.)", value=float(gp.get("D&A Base", model.base_revenue*model.da_pct)), format="%.1f")
        da_lt = c4.number_input("D&A / Rev long-run", value=float(gp.get("D&A LT %", 0.05)), format="%.3f")
        c5, c6, c7, c8 = st.columns(4)
        capex_g = c5.number_input("CapEx / Rev", value=float(gp.get("CapEx %", model.capex_pct)), format="%.3f")
        sbc_g = c6.number_input("SBC / Rev", value=float(gp.get("SBC %", model.sbc_pct)), format="%.3f")
        nwc_g = c7.number_input("NWC / Δ Rev", value=float(gp.get("NWC %", 0.15)), format="%.3f")
        opex_g = c8.number_input("Fixed-cost growth p.a.", value=float(gp.get("Opex Growth", 0.015)), format="%.3f")
        c9, c10, c11 = st.columns(3)
        g_post = c9.number_input("Growth 1st fade year", value=float(gp.get("Post-Plan Growth", 0.12)), format="%.3f")
        fade_yrs = int(c10.number_input("Fade years", value=int(gp.get("Fade Years", 10)), step=1, format="%d"))
        m_fade = c11.number_input("EBITDA margin end of fade", value=float(gp.get("Margin Fade To", 0.35)), format="%.3f")

    # Build inputs
    segs = []
    for _, row in seg_edit.iterrows():
        if pd.isna(row["Segment"]) or str(row["Segment"]).strip() == "": continue
        ramp_txt = str(row.get("Ramp (% of target per year)", "") or "").strip()
        ramp = [float(x)/100 for x in ramp_txt.replace(";", ",").split(",")] if ramp_txt else None
        if ramp is not None and len(ramp) != int(target_year - base_year):
            st.warning(f"Segment '{row['Segment']}': ramp needs {int(target_year-base_year)} values, got {len(ramp)} — ignored.")
            ramp = None
        segs.append(gd.Segment(str(row["Segment"]), float(row["Base"] or 0), float(row["Target"] or 0), bool(row["Scalable"]), ramp))
    net_cash_g = -(model.net_debt + model.minority + getattr(model, "_ev_adjustment", 0.0))
    ginp = gd.GuidanceInputs(int(base_year), int(target_year), segs, ebitda_base, ebitda_target,
        tax_rate=tax_g, da_base=da_base, da_lt_pct=da_lt, capex_pct=capex_g, sbc_pct=sbc_g, nwc_incr_pct=nwc_g,
        opex_growth=opex_g, g_post=g_post, fade_years=fade_yrs, margin_fade_to=m_fade,
        wacc=wacc, tg=tg, net_cash=net_cash_g, shares=model.shares, fx=fx, price=model.price)

    if ginp.n_plan < 1 or ginp.rev_target <= ginp.rev_base:
        st.error("Target year must be after base year and target revenue above base revenue."); st.stop()

    lv1, lv2 = st.columns([2, 3])
    fulfil = lv1.slider("Plan fulfilment (scalable segments)", 0.0, 1.5, 1.0, 0.05, format="%.2f")
    lv2.caption(f"Implied cost structure: contribution margin **{ginp.contribution_margin:.1%}**, fixed cost base **{ginp.opex_base:,.1f}** "
                f"(+{opex_g:.1%} p.a.) · WACC {wacc:.2%} / Tg {tg:.2%} from sidebar · Net cash used: {net_cash_g:,.1f} "
                f"(incl. minorities & EV adj.) · Shares {model.shares:,.2f}")

    try:
        res = gd.value(ginp, fulfil)
        impl = gd.implied_fulfilment(ginp)
        scen = gd.scenarios(ginp)
        p_plan = gd.implied_probability(scen, model.price)
        path = gd.fcff_path(ginp, fulfil)
        plan_path = gd.fcff_path(ginp, 1.0)
        n = ginp.n_plan

        # Verdict box
        up = res["price"]/model.price - 1 if model.price else 0
        if impl is None: imp_txt = "not solvable (price outside 0–300% fulfilment range)"
        else:
            rev_impl = gd.segment_paths(ginp, impl)["Revenue"].iloc[-1]
            ebitda_impl = gd.fcff_path(ginp, impl)["EBITDA"].iloc[n-1]
            cagr_impl = (rev_impl/ginp.rev_base)**(1/n)-1
            imp_txt = (f"<b>{impl:.0%}</b> of the plan's scalable growth → Revenue {target_year}: <b>{rev_impl:,.0f}</b> (plan {ginp.rev_target:,.0f}), "
                       f"EBITDA <b>{ebitda_impl:,.0f}</b> (plan {ebitda_target:,.0f}), CAGR <b>{cagr_impl:.1%}</b> "
                       f"(plan {(ginp.rev_target/ginp.rev_base)**(1/n)-1:.1%}, Reverse DCF implied FCF growth {r['implied_growth']:.1%})")
        vc = C_GREEN if up > 0.2 else C_AMBER if up > -0.1 else C_RED
        st.markdown(f"""<div style="background:{vc}15;border-left:5px solid {vc};padding:18px 22px;border-radius:4px;margin:14px 0;">
            <span style="font-size:24px;font-weight:bold;color:{vc};">Fair value @ {fulfil:.0%} fulfilment: {res['price']:,.1f} ({up:+.0%})</span><br>
            <span style="font-size:15px;color:#333;">Market discounts {imp_txt}</span></div>""", unsafe_allow_html=True)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Price", f"{model.price:,.2f}")
        m2.metric("Fair Value (today)", f"{res['price']:,.1f}", delta=f"{up:+.1%}")
        m3.metric("Implied fulfilment", f"{impl:.0%}" if impl is not None else "n/a")
        m4.metric("Implied P(Plan)", f"{p_plan:.0%}" if p_plan == p_plan else "n/a",
                  delta=f"vs '{scen.index[1]}'", delta_color="off",
                  help="p such that p × Plan + (1−p) × downside = price. Negative: price is below the downside case → WACC too low or market disbelieves it.")
        m5.metric("TV %", f"{res['tv_pct']:.0%}")

        st.markdown("---")
        ch1, ch2 = st.columns(2)
        with ch1:
            st.subheader("Revenue by segment (plan)")
            seg_cols = [s.name for s in segs]
            years_x = [str(base_year)] + [str(y) for y in path.index[:n]]
            figs = go.Figure()
            palette = [C_TEAL, C_CORAL, C_AMBER, C_GREEN, "#8E44AD", "#7F8C8D"]
            for i, sname in enumerate(seg_cols):
                s_base = next(s.base for s in segs if s.name == sname)
                figs.add_trace(go.Bar(name=sname, x=years_x, y=[s_base] + list(path[sname].iloc[:n]), marker_color=palette[i % len(palette)]))
            figs.add_trace(go.Scatter(name="EBITDA", x=years_x, y=[ebitda_base] + list(path["EBITDA"].iloc[:n]), mode="lines+markers+text",
                text=[f"{v:,.0f}" for v in [ebitda_base] + list(path["EBITDA"].iloc[:n])], textposition="top center", line=dict(color="#000", width=2)))
            figs.update_layout(barmode="stack", height=400, plot_bgcolor="white", font=dict(family="Arial"), legend=dict(orientation="h", y=-0.15))
            st.plotly_chart(figs, use_container_width=True)
        with ch2:
            st.subheader("Fair value roll-forward vs price")
            rf_years = list(range(int(base_year), int(target_year)+1))
            rf_vals = [gd.value(ginp, fulfil, t0=t)["price"] for t in range(n+1)]
            ex10 = [None] + [(10*path["EBITDA"].iloc[t-1] + gd.value(ginp, fulfil, t0=t)["cash"])/model.shares*fx for t in range(1, n+1)]
            ex15 = [None] + [(15*path["EBITDA"].iloc[t-1] + gd.value(ginp, fulfil, t0=t)["cash"])/model.shares*fx for t in range(1, n+1)]
            figr = go.Figure()
            figr.add_trace(go.Scatter(x=rf_years, y=rf_vals, mode="lines+markers+text", name="DCF fair value",
                text=[f"{v:,.0f}" for v in rf_vals], textposition="top center", line=dict(color=C_TEAL, width=3)))
            figr.add_trace(go.Scatter(x=rf_years, y=ex10, mode="lines", name="10x EBITDA exit", line=dict(color=C_AMBER, dash="dot")))
            figr.add_trace(go.Scatter(x=rf_years, y=ex15, mode="lines", name="15x EBITDA exit", line=dict(color=C_GREEN, dash="dot")))
            figr.add_hline(y=model.price, line_dash="dash", line_color=C_CORAL, annotation_text=f"Price {model.price:,.2f}")
            figr.update_layout(height=400, plot_bgcolor="white", font=dict(family="Arial"), legend=dict(orientation="h", y=-0.15), yaxis_title="Price ccy")
            st.plotly_chart(figr, use_container_width=True)
            irr = (rf_vals[-1]/model.price)**(1/n)-1 if model.price and rf_vals[-1] > 0 else float("nan")
            st.caption(f"If the {fulfil:.0%} case plays out: {rf_vals[-1]:,.0f} at end {target_year} → IRR from today {irr:.1%} p.a. (cash accumulates, no dividends).")

        st.markdown("---")
        s1, s2 = st.columns([2, 3])
        with s1:
            st.subheader("Scenarios")
            figsc = go.Figure(go.Bar(x=list(scen.index), y=scen["Fair Value"], marker_color=[C_GREEN, C_AMBER, C_RED],
                text=[f"{v:,.0f}<br>({u:+.0%})" for v, u in zip(scen["Fair Value"], scen["Upside"])], textposition="outside"))
            figsc.add_hline(y=model.price, line_dash="dash", line_color=C_TEAL, annotation_text=f"Price {model.price:,.2f}")
            figsc.update_layout(height=380, plot_bgcolor="white", font=dict(family="Arial"), showlegend=False)
            st.plotly_chart(figsc, use_container_width=True)
            st.caption("Downside = most speculative scalable segment at zero; Stall = downside + other scalable segments at 50%.")
        with s2:
            st.subheader("Sensitivity: fair value (WACC × plan fulfilment)")
            w_rng = np.round(np.arange(max(0.04, wacc-0.02), wacc+0.031, 0.01), 3)
            f_rng = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2]
            sdf = gd.sensitivity(ginp, w_rng, f_rng)
            try:
                st.dataframe(sdf.style.format("{:,.0f}").background_gradient(cmap="RdYlGn", axis=None), use_container_width=True)
            except ImportError:
                st.dataframe(sdf.style.format("{:,.0f}"), use_container_width=True)
            st.caption(f"Rows: WACC · Columns: fulfilment of scalable segments · Price {model.price:,.2f}")

        st.markdown("---")
        st.subheader("Cash-flow path")
        show = path[["Phase", "Revenue", "Growth", "EBITDA", "EBITDA Margin", "D&A", "EBIT", "FCFF"]].copy()
        st.dataframe(show.style.format({"Revenue": "{:,.0f}", "Growth": "{:.1%}", "EBITDA": "{:,.0f}", "EBITDA Margin": "{:.1%}",
                                        "D&A": "{:,.0f}", "EBIT": "{:,.0f}", "FCFF": "{:,.0f}"}), use_container_width=True, height=380)
        st.session_state["guidance_result"] = {"fair_value": res["price"], "implied_fulfilment": impl, "p_plan": p_plan,
                                               "scenarios": scen, "fulfilment": fulfil}
    except Exception as e:
        st.error(f"Guidance DCF error: {e}")
        import traceback
        with st.expander("Traceback"): st.code(traceback.format_exc())


# ══════════════════════════════════════════════════════════════════════════════
# PDF EXPORT
# ══════════════════════════════════════════════════════════════════════════════
def _placeholder_png(w=900, h=420, text="Chart unavailable (kaleido/Chrome missing)"):
    """Minimal PNG so the PDF still builds when static image export is not available."""
    try:
        from PIL import Image, ImageDraw
        from io import BytesIO
        im = Image.new("RGB", (w, h), "#F5F5F5"); d = ImageDraw.Draw(im); d.text((20, h//2), text, fill="#888888")
        buf = BytesIO(); im.save(buf, format="PNG"); return buf.getvalue()
    except Exception:
        import zlib, struct
        raw = b"".join(b"\x00" + b"\xf5\xf5\xf5" * w for _ in range(h))
        def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

def _fig_to_png(fig, w=900, h=420, scale=2):
    """Plotly figure → PNG bytes. Plotly 6 removed the `engine` kwarg (kaleido v1);
    without it the call works on plotly 5 + kaleido 0.2.1 AND plotly 6 + kaleido >= 1.0."""
    try:
        return fig.to_image(format="png", width=w, height=h, scale=scale)
    except Exception as e:
        if "_png_warned" not in st.session_state:
            st.sidebar.warning(f"PDF charts unavailable ({type(e).__name__}). "
                               f"Pin plotly<6 + kaleido==0.2.1, or plotly>=6 + kaleido>=1.0 with chromium in packages.txt.")
            st.session_state["_png_warned"] = True
        return _placeholder_png(w, h)


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
    ig_unreliable = getattr(model, "_ig_unreliable", False)
    ig_unreliable_reason = getattr(model, "_ig_unreliable_reason", None)

    if ig_unreliable:
        verdict, v_color = "MODEL LIMITATION", C_RED
        v_action = (f"{ig_unreliable_reason}. Reverse DCF cannot solve meaningfully — "
                    f"use Forward DCF or alternative framework.")
    elif spread < 0.02:
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
    if ig_unreliable:
        story.append(Paragraph(
            f"Reverse DCF cannot solve meaningfully — see Forward DCF section for "
            f"alternative valuation framework.", body))
    else:
        story.append(Paragraph(
            f"Market implies <b>{ig:.1%} p.a. FCF growth</b> (Y3-{2+proj_years}) "
            f"to justify {r['price']:,.2f}.", body))
    story.append(Paragraph(f"<b>So what?</b> {v_action}", body))
    story.append(Spacer(1, 0.3*cm))

    # KPI table (6 metrics)
    sc = r["scenarios"]; q = r["quality"]
    ig_display = "n/a" if ig_unreliable else f"{ig:.1%}"
    kpi_data = [
        ["Price", "Implied Growth", "WACC", "TV %", "ROIC Spread", "Quality"],
        [f"{r['price']:,.2f}",
         ig_display,
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
            fair_eq = model.ev_to_equity(tot_ev)
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
                            fair_eq_=model.ev_to_equity(tot_ev_)
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
