"""
Reverse DCF Dashboard — Reverse + Forward DCF
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from reverse_dcf_engine import ReverseDCF, DCFParams
import tempfile

st.set_page_config(page_title="Reverse DCF", page_icon="📊", layout="wide")
C_TEAL, C_CORAL, C_AMBER, C_GREEN, C_RED = "#003850", "#F26B43", "#FBAE40", "#2ECC71", "#E74C3C"

# ── File Upload ───────────────────────────────────────────────────────────────
st.sidebar.title("📊 DCF Engine")
uploaded = st.sidebar.file_uploader("Upload reverse_dcf.xlsx", type=["xlsx"])
model = None
if uploaded:
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(uploaded.read()); tmp_path = tmp.name
    try:
        model = ReverseDCF.from_excel(tmp_path)
        st.sidebar.success(f"Loaded: {model.ticker}")
    except Exception as e:
        st.sidebar.error(f"Error: {e}")
if model is None:
    st.title("DCF Engine")
    st.info("Upload your reverse_dcf.xlsx (with HC data) to start.")
    st.stop()

# ── Shared WACC Sidebar ──────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("WACC")
p = model.params
c1, c2 = st.sidebar.columns(2)
rf = c1.number_input("Risk-Free (%)", value=round(p.risk_free*100,2), step=0.1, format="%.2f") / 100
erp = c2.number_input("ERP (%)", value=round(p.erp*100,1), step=0.5, format="%.1f") / 100
beta = c1.number_input("Beta", value=round(p.beta,2), step=0.05, format="%.2f")
cod = c2.number_input("CoD pre-tax (%)", value=round(p.cost_of_debt_pretax*100,1), step=0.1, format="%.1f") / 100
tax = c1.number_input("Tax Rate (%)", value=round(p.tax_rate*100,0), step=1.0, format="%.0f") / 100
we = c2.number_input("Eq. Weight (%)", value=round(p.equity_weight*100,0), step=1.0, format="%.0f") / 100

st.sidebar.markdown("---")
st.sidebar.subheader("Terminal")
tg = st.sidebar.slider("Terminal Growth (%)", 0.0, 4.0, round(p.terminal_growth*100,1), 0.1) / 100
tm = st.sidebar.slider("Terminal EBIT Margin (%)", 5.0, 40.0, round((p.terminal_ebit_margin or 0.15)*100,1), 0.5) / 100
proj = st.sidebar.slider("Projection Years", 3, 10, p.projection_years)

# Compute WACC
wacc_coe = rf + beta * erp
wacc_cod_at = cod * (1 - tax)
wacc = we * wacc_coe + (1 - we) * wacc_cod_at
st.sidebar.markdown(f"**WACC: {wacc:.2%}** (CoE: {wacc_coe:.2%})")

# ── TABS ──────────────────────────────────────────────────────────────────────
tab_reverse, tab_forward = st.tabs(["🔍 Reverse DCF", "🎯 Forward DCF (My View)"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: REVERSE DCF
# ══════════════════════════════════════════════════════════════════════════════
with tab_reverse:
    bull = st.sidebar.slider("Bull Offset (pp)", 0.0, 10.0, 3.0, 0.5, key="bull_r") / 100
    bear = st.sidebar.slider("Bear Offset (pp)", -10.0, 0.0, -3.0, 0.5, key="bear_r") / 100

    params = DCFParams(risk_free=rf, erp=erp, beta=beta, cost_of_debt_pretax=cod, tax_rate=tax,
        equity_weight=we, debt_weight=1-we, terminal_growth=tg, terminal_ebit_margin=tm,
        projection_years=proj, bull_growth_add=bull, bear_growth_add=bear)
    model.params = params
    model._prepare_data()
    r = model.run()

    ig = r["implied_growth"]
    hp = r["historical_profile"]
    red_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🔴")
    tv_pct = r["tv_decomposition"]["tv_pct"]
    roic_sp = r["roic_gate"]["spread"]

    reasons = []
    if hp.max_revenue_growth and ig > hp.max_revenue_growth > 0:
        reasons.append(f"Implied growth ({ig:.1%}) exceeds historical max ({hp.max_revenue_growth:.1%})")
    if hp.revenue_cagr_5y and hp.revenue_cagr_5y != 0 and abs(ig) > abs(hp.revenue_cagr_5y) * 3:
        reasons.append(f"Implied growth is {abs(ig/hp.revenue_cagr_5y):.0f}× the 5Y CAGR ({hp.revenue_cagr_5y:.1%})")
    if tv_pct > 0.90:
        reasons.append(f"Terminal Value = {tv_pct:.0%} of EV (high uncertainty)")
    if roic_sp < 0:
        reasons.append(f"ROIC < WACC — growth destroys value")

    if red_flags >= 3:
        verdict, v_color, v_icon = "OVERPRICED", C_RED, "🔴"
        v_detail = "Market expectations significantly exceed what the company has historically delivered."
        v_action = "The market prices in growth well beyond the historical track record. Requires a strong catalyst thesis to justify."
    elif red_flags >= 2:
        verdict, v_color, v_icon = "LIKELY OVERPRICED", C_CORAL, "🟠"
        v_detail = "Implied growth is stretched relative to historical fundamentals."
        v_action = "Market expects meaningfully higher growth than history suggests. Needs a clear reason why the future will differ."
    elif red_flags == 0 and roic_sp > 0 and ig >= 0:
        verdict, v_color, v_icon = "FAIRLY VALUED", C_GREEN, "🟢"
        v_detail = "Implied expectations are broadly consistent with the historical track record."
        v_action = "Expectations are achievable based on history. Returns will depend on execution vs. these expectations."
    elif ig < 0 and hp.revenue_cagr_5y and hp.revenue_cagr_5y >= 0:
        verdict, v_color, v_icon = "POTENTIALLY UNDERVALUED", C_GREEN, "🟢"
        v_detail = "Market implies revenue decline — potential opportunity if fundamentals hold."
        v_action = "Market prices in deterioration. If you believe the business is stable, this could be an opportunity."
    else:
        verdict, v_color, v_icon = "FAIR VALUE RANGE", C_AMBER, "🟡"
        v_detail = "Mixed signals — some implied expectations stretched, others reasonable."
        v_action = "No clear mispricing signal. Dig deeper into the specific flags below."

    st.title(f"Reverse DCF: {r['ticker']}")

    st.markdown(f"""
    <div style="background-color: {v_color}15; border-left: 5px solid {v_color};
                padding: 20px 24px; border-radius: 4px; margin-bottom: 20px;">
        <span style="font-size: 28px; font-weight: bold; color: {v_color};">{v_icon} {verdict}</span><br>
        <span style="font-size: 18px; color: #333; line-height: 1.6;">
            Market implies <b>{ig:.1%} p.a. revenue growth</b> over {proj} years to justify {r['price']:,.2f}. {v_detail}
        </span>
        <br><span style="font-size: 15px; color: #444;"><b>So what?</b> {v_action}</span>
        {"<br><span style='font-size: 13px; color: #666;'>" + " · ".join(reasons[:3]) + "</span>" if reasons else ""}
    </div>
    """, unsafe_allow_html=True)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Price", f"{r['price']:,.2f}")
    k2.metric("Implied Growth (p.a.)", f"{ig:.1%}", delta=f"vs {hp.revenue_cagr_5y:.1%} 5Y CAGR" if hp.revenue_cagr_5y else None)
    k3.metric("WACC", f"{r['wacc']:.2%}")
    k4.metric("TV % of EV", f"{tv_pct:.0%}")
    k5.metric("ROIC Spread", f"{roic_sp:+.1%}", delta="Creates Value" if r['roic_gate']['value_creating'] else "Destroys Value",
              delta_color="normal" if r['roic_gate']['value_creating'] else "inverse")

    st.markdown("---")

    # Scenario Fan + TV
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Scenario Fan")
        sc = r["scenarios"]; labels = ["Bear", "Base", "Bull"]
        prices = [sc[l]["fair_price"] for l in labels]; upsides = [sc[l]["upside_downside"] for l in labels]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=labels, y=prices, marker_color=[C_RED, C_AMBER, C_GREEN],
            text=[f"{p:,.1f}<br>({u:+.0%})" for p, u in zip(prices, upsides)],
            textposition="outside", textfont=dict(size=14, color=C_TEAL)))
        fig.add_hline(y=r["price"], line_dash="dash", line_color=C_TEAL, line_width=2,
                      annotation_text=f"Current: {r['price']:,.2f}", annotation_position="bottom right")
        fig.update_layout(height=400, showlegend=False, yaxis_title="Fair Price", plot_bgcolor="white", font=dict(family="Arial"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Base = growth that justifies current price. Bear/Bull = ±{abs(bear)*100:.0f}pp offset.")

    with right:
        st.subheader("TV Decomposition")
        tv = r["tv_decomposition"]
        fig2 = go.Figure(go.Pie(labels=["Explicit Period", "Terminal Value"], values=[tv["explicit_pct"], tv["tv_pct"]],
            marker_colors=[C_TEAL, C_CORAL], hole=0.5, textinfo="label+percent", textfont=dict(size=13)))
        fig2.update_layout(height=400, showlegend=False, font=dict(family="Arial"),
            annotations=[dict(text=f"TV<br>{tv['tv_pct']:.0%}", x=0.5, y=0.5, font_size=18, showarrow=False, font_color=C_CORAL)])
        st.plotly_chart(fig2, use_container_width=True)
        if tv_pct > 0.85:
            st.caption(f"⚠️ {tv_pct:.0%} depends on long-term assumptions. Use the sensitivity table to gauge the range.")

    st.markdown("---")

    # Plausibility + ROIC
    cl, cr = st.columns(2)
    with cl:
        st.subheader("Plausibility Checks")
        for c in r["plausibility"]:
            st.write(f"{c['flag']} **{c['check']}**: implied {c['implied']} vs hist {c['historical']} ({c['ratio']})")
        st.caption("🟢 <1.5× hist · 🟡 1.5–2× · 🔴 >2× or exceeds max")
    with cr:
        st.subheader("ROIC Gate")
        rg = r["roic_gate"]
        st.write(rg["verdict"])
        reinvest = rg.get("implied_reinvestment_rate", np.nan)
        if not np.isnan(reinvest):
            st.write(f"Implied reinvestment rate: **{reinvest:.0%}**")
            if reinvest > 1:
                st.caption(f"Needs external financing — reinvesting {reinvest:.0%} of earnings.")
            else:
                st.caption(f"Reinvests {reinvest:.0%}, leaving {max(0,1-reinvest):.0%} for dividends/buybacks.")
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(x=["ROIC", "WACC"], y=[rg["roic"], rg["wacc"]],
            marker_color=[C_GREEN if rg["value_creating"] else C_RED, C_TEAL],
            text=[f"{rg['roic']:.1%}", f"{rg['wacc']:.1%}"], textposition="outside"))
        fig3.update_layout(height=250, showlegend=False, yaxis_tickformat=".0%", plot_bgcolor="white", font=dict(family="Arial"))
        st.plotly_chart(fig3, use_container_width=True)

    # Performance Decomposition
    st.markdown("---")
    pd_data = r.get("performance_decomposition", {})
    if pd_data.get("available"):
        st.subheader(f"Performance Decomposition ({pd_data['start_year']}–{pd_data['end_year']})")
        st.caption("How was historical return generated?")
        pd_l, pd_r = st.columns([3, 2])
        with pd_l:
            comps = [("Revenue<br>Growth", pd_data["revenue_growth_ann"]), ("Margin<br>Effect", pd_data["margin_effect_ann"]),
                     ("Buyback<br>Yield", pd_data["buyback_ann"]), ("Dividend<br>Yield", pd_data["div_yield"])]
            vals = [c[1] for c in comps]
            fig_pd = go.Figure(go.Waterfall(x=[c[0] for c in comps], y=vals,
                connector={"line": {"color": "#ccc"}}, increasing={"marker": {"color": C_GREEN}},
                decreasing={"marker": {"color": C_RED}}, text=[f"{v:+.1%}" for v in vals],
                textposition="outside", textfont=dict(size=13)))
            total = sum(vals)
            fig_pd.add_trace(go.Bar(x=["Total<br>Return"], y=[total], marker_color=C_TEAL,
                text=[f"{total:+.1%}"], textposition="outside", textfont=dict(size=14, color=C_TEAL), width=0.5))
            fig_pd.update_layout(height=400, showlegend=False, yaxis_tickformat=".0%", plot_bgcolor="white", font=dict(family="Arial"))
            st.plotly_chart(fig_pd, use_container_width=True)
        with pd_r:
            st.write(f"Revenue Growth: **{pd_data['revenue_growth_ann']:+.1%}** p.a.")
            st.write(f"Margin Change: **{pd_data['margin_effect_ann']:+.1%}** p.a. ({pd_data['margin_first']:.1%} → {pd_data['margin_last']:.1%})")
            st.write(f"Buyback Yield: **{pd_data['buyback_ann']:+.1%}** p.a.")
            st.write(f"Dividend Yield: **{pd_data['div_yield']:.1%}**")
            st.write(f"EPS Growth: **{pd_data['eps_growth_ann']:+.1%}** p.a.")
            if pd_data["current_pe"] > 0:
                st.write(f"P/E: **{pd_data['current_pe']:.1f}x**")
            organic = pd_data["revenue_growth_ann"] + pd_data["margin_effect_ann"]
            if abs(total) > 0.001:
                st.write(f"**{organic/total*100:.0f}% fundamental** vs **{(1-organic/total)*100:.0f}% financial**")

    # Sensitivity
    st.markdown("---")
    st.subheader("Sensitivity: Implied Growth (WACC × Tg)")
    st.caption("Green = low expectations (cheap). Red = high expectations (expensive). Find your WACC/Tg and ask: is that growth achievable?")
    wacc_rng = np.arange(max(0.02, wacc-0.015), wacc+0.020, 0.005)
    tg_rng = np.arange(max(0.005, tg-0.01), tg+0.015, 0.005)
    rows = []
    for w in wacc_rng:
        row = {}
        for t in tg_rng:
            p2 = DCFParams(risk_free=rf, erp=erp, beta=beta, cost_of_debt_pretax=cod, tax_rate=tax,
                equity_weight=we, debt_weight=1-we, terminal_growth=t, projection_years=proj, terminal_ebit_margin=tm)
            p2.wacc_override = w
            m2 = ReverseDCF(model.hist, model.current, p2, ticker=r["ticker"])
            row[f"Tg={t:.1%}"] = m2.solve_implied_growth()
        row["WACC"] = w; rows.append(row)
    sdf = pd.DataFrame(rows).set_index("WACC")
    sdf.index = [f"{w:.1%}" for w in wacc_rng]
    st.dataframe(sdf.style.format("{:.1%}").background_gradient(cmap="RdYlGn", axis=None), use_container_width=True)

    # Model Inputs + Methodology
    st.markdown("---")
    with st.expander("Model Inputs"):
        mi1, mi2, mi3 = st.columns(3)
        with mi1:
            st.markdown("**FCFF Drivers**")
            fcff = model._fcff_from_revenue(model.base_revenue)
            st.write(f"Base Revenue: {model.base_revenue:,.0f}")
            st.write(f"EBIT Margin: {model.ebit_margin:.1%}")
            st.write(f"D&A/Rev: {model.da_pct:.1%} · CapEx/Rev: {model.capex_pct:.1%}")
            st.write(f"NWC/Rev: {model.nwc_pct:.1%} · Tax: {model.params.tax_rate:.1%}")
            st.write(f"**Base FCFF: {fcff:,.0f}** ({fcff/model.base_revenue:.1%} margin)")
        with mi2:
            st.markdown("**Valuation**")
            st.write(f"Market Cap: {model.market_cap:,.0f}")
            st.write(f"Net Debt: {model.net_debt:,.0f} · MI: {model.minority:,.0f}")
            st.write(f"**EV: {model.market_ev:,.0f}** · Shares: {model.shares:,.1f}")
        with mi3:
            st.markdown("**Historical**")
            st.write(f"5Y CAGR: {hp.revenue_cagr_5y:.1%}" if hp.revenue_cagr_5y else "5Y: N/A")
            st.write(f"3Y CAGR: {hp.revenue_cagr_3y:.1%}" if hp.revenue_cagr_3y else "3Y: N/A")
            st.write(f"Max Growth: {hp.max_revenue_growth:.1%}" if hp.max_revenue_growth else "N/A")
            st.write(f"ROIC: {hp.median_roic:.1%}" if hp.median_roic else "N/A")
            st.caption(f"Base: {'LTM' if model.ltm_data.get('Revenue') else 'FY'}")

    with st.expander("Verdict Methodology"):
        st.markdown("**Thresholds:** 🔴 OVERPRICED: 3+ red flags · 🟠 LIKELY OVERPRICED: 2 · 🟢 FAIRLY VALUED: 0 flags + ROIC>WACC · 🟡 FAIR RANGE: mixed")
        st.markdown("**Plausibility flags:** 🟢 <1.5× historical · 🟡 1.5–2× · 🔴 >2× or exceeds historical max")
        st.caption("This is a screening tool, not a price target. Always cross-check with your own analysis.")

    warnings = r.get("validation_warnings", [])
    if warnings:
        with st.expander(f"Data Validation ({len(warnings)} notes)"):
            for w in warnings:
                if "CRITICAL" in w: st.error(w)
                elif "WARNING" in w: st.warning(w)
                else: st.info(w)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: FORWARD DCF (MY VIEW)
# ══════════════════════════════════════════════════════════════════════════════
with tab_forward:
    st.title(f"Forward DCF: {model.ticker}")
    st.markdown("**Set your own assumptions → get your fair value → compare vs market and analyst consensus.**")

    # My View inputs
    st.sidebar.markdown("---")
    st.sidebar.subheader("My View")
    my_rev_growth = st.sidebar.slider("Revenue Growth (% p.a.)", -10.0, 40.0, 
        round((hp.revenue_cagr_5y or 0.05)*100, 1), 0.5, key="fwd_rev") / 100
    my_ebit_margin = st.sidebar.slider("Target EBIT Margin (%)", 5.0, 45.0,
        round(model.ebit_margin*100, 1), 0.5, key="fwd_margin") / 100
    my_capex = st.sidebar.slider("CapEx / Revenue (%)", 1.0, 15.0,
        round(model.capex_pct*100, 1), 0.5, key="fwd_capex") / 100

    # Forward DCF calculation
    fwd_wacc = wacc
    fwd_tg = tg
    fwd_tm = tm
    fwd_n = proj

    # Project cash flows
    rev = model.base_revenue
    pv_explicit = 0.0
    projection_table = []

    for t_yr in range(1, fwd_n + 1):
        rev *= (1 + my_rev_growth)
        # Linear margin fade from current to target
        margin_t = model.ebit_margin + (my_ebit_margin - model.ebit_margin) * (t_yr / fwd_n)
        ebit = rev * margin_t
        nopat = ebit * (1 - tax)
        da = rev * model.da_pct
        capex = rev * my_capex
        nwc = rev * abs(model.nwc_pct)
        fcff = nopat + da - capex - nwc
        pv = fcff / (1 + fwd_wacc) ** t_yr
        pv_explicit += pv
        projection_table.append({
            "Year": t_yr,
            "Revenue": rev,
            "EBIT Margin": margin_t,
            "EBIT": ebit,
            "NOPAT": nopat,
            "FCFF": fcff,
            "PV(FCFF)": pv,
        })

    # Terminal value
    terminal_fcff = rev * (1 + fwd_tg) * fwd_tm * (1 - tax) + rev * (1 + fwd_tg) * model.da_pct - rev * (1 + fwd_tg) * my_capex - rev * (1 + fwd_tg) * abs(model.nwc_pct)
    tv_val = terminal_fcff / (fwd_wacc - fwd_tg)
    pv_tv = tv_val / (1 + fwd_wacc) ** fwd_n
    total_ev = pv_explicit + pv_tv

    # Fair value
    fair_equity = total_ev - model.net_debt - model.minority
    fair_price = fair_equity / model.shares if model.shares else 0
    upside = (fair_price / model.price - 1) if model.price else 0

    # ANR target (from current data if available)
    anr_target = model._safe_numeric(model.current.get("Consensus_EPS_FY1"))  # placeholder
    # Try to get target price from current dict
    best_target = model._safe_numeric(model.current.get("Best_Target_Price"))

    # ── Display ───────────────────────────────────────────────────────────────
    # Verdict
    if upside > 0.15:
        fwd_verdict, fwd_color = "UNDERVALUED", C_GREEN
        fwd_text = f"Your assumptions imply **{upside:+.0%} upside**. The stock looks cheap if your view is correct."
    elif upside > 0:
        fwd_verdict, fwd_color = "SLIGHT UPSIDE", C_GREEN
        fwd_text = f"Your assumptions imply **{upside:+.0%} upside**. Marginal — the market roughly agrees with your view."
    elif upside > -0.15:
        fwd_verdict, fwd_color = "SLIGHT DOWNSIDE", C_AMBER
        fwd_text = f"Your assumptions imply **{upside:+.0%}**. The stock is slightly expensive relative to your view."
    else:
        fwd_verdict, fwd_color = "OVERVALUED", C_RED
        fwd_text = f"Your assumptions imply **{upside:+.0%} downside**. The stock is expensive relative to your view."

    st.markdown(f"""
    <div style="background-color: {fwd_color}15; border-left: 5px solid {fwd_color};
                padding: 20px 24px; border-radius: 4px; margin-bottom: 20px;">
        <span style="font-size: 28px; font-weight: bold; color: {fwd_color};">🎯 {fwd_verdict}</span><br>
        <span style="font-size: 18px; color: #333; line-height: 1.6;">
            Your fair value: <b>{fair_price:,.1f}</b> vs current price {model.price:,.2f} ({upside:+.1%}).
            {fwd_text}
        </span>
    </div>
    """, unsafe_allow_html=True)

    # KPIs
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Current Price", f"{model.price:,.2f}")
    f2.metric("My Fair Value", f"{fair_price:,.1f}", delta=f"{upside:+.1%}")
    f3.metric("My EV", f"{total_ev:,.0f}")
    tv_fwd_pct = pv_tv / total_ev if total_ev else 0
    f4.metric("TV %", f"{tv_fwd_pct:.0%}")

    st.markdown("---")

    # Assumptions vs Market
    st.subheader("My View vs Market Expectations")
    cmp_l, cmp_r = st.columns(2)
    with cmp_l:
        implied_g = r["implied_growth"]
        fig_cmp = go.Figure()
        fig_cmp.add_trace(go.Bar(
            x=["Revenue Growth", "EBIT Margin"],
            y=[my_rev_growth, my_ebit_margin],
            name="My View", marker_color=C_TEAL,
            text=[f"{my_rev_growth:.1%}", f"{my_ebit_margin:.1%}"], textposition="outside"))
        fig_cmp.add_trace(go.Bar(
            x=["Revenue Growth", "EBIT Margin"],
            y=[implied_g, model.ebit_margin],
            name="Market Implied", marker_color=C_CORAL,
            text=[f"{implied_g:.1%}", f"{model.ebit_margin:.1%}"], textposition="outside"))
        fig_cmp.update_layout(height=350, barmode="group", showlegend=True,
            yaxis_tickformat=".0%", plot_bgcolor="white", font=dict(family="Arial"))
        st.plotly_chart(fig_cmp, use_container_width=True)

    with cmp_r:
        st.markdown("**Comparison**")
        st.write(f"**Your Revenue Growth:** {my_rev_growth:.1%} p.a.")
        st.write(f"**Market Implied:** {implied_g:.1%} p.a.")
        diff = my_rev_growth - implied_g
        if diff > 0.02:
            st.write(f"→ You are **more bullish** than the market (+{diff:.1%}pp)")
        elif diff < -0.02:
            st.write(f"→ You are **more bearish** than the market ({diff:.1%}pp)")
        else:
            st.write(f"→ You roughly **agree** with the market")
        st.markdown("---")
        st.write(f"**Your EBIT Margin:** {my_ebit_margin:.1%} (target in Year {fwd_n})")
        st.write(f"**Current:** {model.ebit_margin:.1%}")

    # Projection Table
    st.markdown("---")
    st.subheader("Projected Cash Flows")
    proj_df = pd.DataFrame(projection_table)
    proj_df["Year"] = [f"Y{y}" for y in proj_df["Year"]]
    proj_df = proj_df.set_index("Year")

    # Add terminal row
    term_row = pd.DataFrame([{
        "Revenue": rev * (1 + fwd_tg),
        "EBIT Margin": fwd_tm,
        "EBIT": rev * (1 + fwd_tg) * fwd_tm,
        "NOPAT": rev * (1 + fwd_tg) * fwd_tm * (1 - tax),
        "FCFF": terminal_fcff,
        "PV(FCFF)": pv_tv,
    }], index=["Terminal"])

    display_df = pd.concat([proj_df, term_row])
    st.dataframe(display_df.style.format({
        "Revenue": "{:,.0f}", "EBIT": "{:,.0f}", "NOPAT": "{:,.0f}",
        "FCFF": "{:,.0f}", "PV(FCFF)": "{:,.0f}", "EBIT Margin": "{:.1%}",
    }), use_container_width=True)

    st.caption(f"WACC: {fwd_wacc:.2%} · Terminal Growth: {fwd_tg:.1%} · Terminal Margin: {fwd_tm:.1%}")

    # Bridge: Current Price → Fair Value
    st.markdown("---")
    st.subheader("Valuation Bridge")
    st.write(f"PV Explicit Period: **{pv_explicit:,.0f}**")
    st.write(f"PV Terminal Value: **{pv_tv:,.0f}**")
    st.write(f"Enterprise Value: **{total_ev:,.0f}**")
    st.write(f"– Net Debt: {model.net_debt:,.0f}")
    st.write(f"– Minority Interest: {model.minority:,.0f}")
    st.write(f"= Equity Value: **{fair_equity:,.0f}**")
    st.write(f"÷ Shares: {model.shares:,.1f}")
    st.write(f"= **Fair Price: {fair_price:,.1f}** ({upside:+.1%} vs {model.price:,.2f})")
