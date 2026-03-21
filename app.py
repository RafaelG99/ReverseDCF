"""
DCF Engine — Reverse + Forward DCF
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from reverse_dcf_engine import ReverseDCF, DCFParams
import tempfile

st.set_page_config(page_title="DCF Engine", page_icon="📊", layout="wide")
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
proj = st.sidebar.slider("Projection Years", 3, 10, p.projection_years)

# BBG WACC as default, with manual override option
bbg_wacc_raw = model._safe_numeric(model.current.get("BBG_WACC"))
if bbg_wacc_raw and bbg_wacc_raw > 1:
    bbg_wacc_raw = bbg_wacc_raw / 100
bbg_wacc_available = bbg_wacc_raw and 0.01 < bbg_wacc_raw < 0.25

if bbg_wacc_available:
    wacc = st.sidebar.number_input("WACC (%)", value=round(bbg_wacc_raw*100, 2), step=0.25, format="%.2f",
                                    help="Default: Bloomberg WACC. Override manually if needed.") / 100
    st.sidebar.caption(f"BBG WACC: {bbg_wacc_raw:.2%}")
else:
    wacc_coe = rf + beta * erp
    wacc = we * wacc_coe + (1 - we) * cod * (1 - tax)
    st.sidebar.caption(f"WACC (own CAPM): {wacc:.2%}")

st.sidebar.markdown(f"**WACC: {wacc:.2%}**")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_reverse, tab_forward = st.tabs(["🔍 Reverse DCF", "🎯 Forward DCF (My View)"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: REVERSE DCF
# ══════════════════════════════════════════════════════════════════════════════
with tab_reverse:
    tm_r = st.sidebar.slider("Terminal EBIT Margin — Reverse (%)", 5.0, 45.0,
        round((p.terminal_ebit_margin or 0.15)*100, 1), 0.5, key="tm_r") / 100
    bull = st.sidebar.slider("Bull Offset (pp)", 0.0, 10.0, 3.0, 0.5, key="bull_r") / 100
    bear = st.sidebar.slider("Bear Offset (pp)", -10.0, 0.0, -3.0, 0.5, key="bear_r") / 100

    params = DCFParams(risk_free=rf, erp=erp, beta=beta, cost_of_debt_pretax=cod, tax_rate=tax,
        equity_weight=we, debt_weight=1-we, terminal_growth=tg, terminal_ebit_margin=tm_r,
        projection_years=proj, bull_growth_add=bull, bear_growth_add=bear)
    params.wacc_override = wacc  # Use the sidebar WACC (BBG or manual)
    model.params = params
    model._prepare_data()
    r = model.run()

    ig = r["implied_growth"]
    hp = r["historical_profile"]
    red_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🔴")
    tv_pct = r["tv_decomposition"]["tv_pct"]
    roic_sp = r["roic_gate"]["spread"]
    wacc_tg_spread = wacc - tg

    reasons = []
    if hp.max_revenue_growth and ig > hp.max_revenue_growth > 0:
        reasons.append(f"Implied growth ({ig:.1%}) exceeds historical max ({hp.max_revenue_growth:.1%})")
    if ig > 0 and hp.revenue_cagr_5y and hp.revenue_cagr_5y > 0 and ig > hp.revenue_cagr_5y * 3:
        reasons.append(f"Implied growth is {ig/hp.revenue_cagr_5y:.0f}x the 5Y CAGR ({hp.revenue_cagr_5y:.1%})")
    if tv_pct > 0.90:
        reasons.append(f"TV = {tv_pct:.0%} of EV (high uncertainty)")
    if roic_sp < 0:
        reasons.append("ROIC < WACC — growth destroys value")
    if ig < -0.03 and hp.revenue_cagr_5y and hp.revenue_cagr_5y > 0.02:
        reasons.append(f"Market implies decline ({ig:.1%}) despite positive historical growth ({hp.revenue_cagr_5y:.1%})")
    if wacc_tg_spread < 0.02:
        reasons.append(f"WACC-Tg spread only {wacc_tg_spread:.1%} — result highly sensitive to assumptions")

    # Verdict logic:
    # 1. First check if result is unreliable (spread too tight)
    # 2. Then classify based on implied growth vs history
    cagr = hp.revenue_cagr_5y or 0

    if wacc_tg_spread < 0.02:
        verdict, v_color = "⚠ RESULT UNRELIABLE", C_RED
        v_action = f"WACC ({wacc:.2%}) is too close to Terminal Growth ({tg:.2%}). The {wacc_tg_spread:.1%} spread makes the DCF output meaningless. Increase WACC or lower Terminal Growth."
    elif ig < -0.10:
        verdict, v_color = "CHECK INPUTS", C_RED
        v_action = f"Implied decline of {ig:.1%} p.a. is extreme. Likely a data issue (wrong WACC, half-year data, or post-M&A distortion). Verify the inputs before drawing conclusions."
    elif ig < -0.03 and cagr > 0.02:
        verdict, v_color = "POTENTIALLY UNDERVALUED", C_GREEN
        v_action = f"Market prices in {ig:.1%} annual decline, but historically the company grew {cagr:.1%} p.a. If the business is stable, this looks cheap."
    elif -0.03 <= ig <= 0.03:
        # Near zero — fairly valued zone
        if cagr > 0.05:
            verdict, v_color = "POTENTIALLY UNDERVALUED", C_GREEN
            v_action = f"Market implies roughly flat growth ({ig:.1%}), but historically the company grew {cagr:.1%} p.a. Modest expectations — could be an opportunity."
        else:
            verdict, v_color = "FAIRLY VALUED", C_AMBER
            v_action = f"Market implies {ig:.1%} growth — broadly in line with the historical {cagr:.1%} trajectory. No strong mispricing signal."
    elif ig > 0 and red_flags >= 3:
        verdict, v_color = "OVERPRICED", C_RED
        v_action = "Market prices in growth well beyond history. Needs a strong catalyst to justify."
    elif ig > 0 and red_flags >= 2:
        verdict, v_color = "LIKELY OVERPRICED", C_CORAL
        v_action = "Expectations are stretched. Needs a clear reason why the future differs from the past."
    elif ig > 0 and red_flags == 0 and roic_sp > 0:
        verdict, v_color = "FAIRLY VALUED", C_GREEN
        v_action = "Expectations are achievable. Returns depend on execution vs. these expectations."
    else:
        verdict, v_color = "FAIR VALUE RANGE", C_AMBER
        v_action = "Mixed signals. Dig deeper into the flags below."

    st.title(f"Reverse DCF: {r['ticker']}")
    st.markdown(f"""
    <div style="background-color: {v_color}15; border-left: 5px solid {v_color}; padding: 20px 24px; border-radius: 4px; margin-bottom: 20px;">
        <span style="font-size: 28px; font-weight: bold; color: {v_color};">{verdict}</span><br>
        <span style="font-size: 18px; color: #333;">Market implies <b>{ig:.1%} p.a. revenue growth</b> over {proj} years to justify {r['price']:,.2f}.</span>
        <br><span style="font-size: 15px; color: #444;"><b>So what?</b> {v_action}</span>
        {"<br><span style='font-size: 13px; color: #666;'>" + " · ".join(reasons[:3]) + "</span>" if reasons else ""}
    </div>""", unsafe_allow_html=True)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Price", f"{r['price']:,.2f}")
    k2.metric("Implied Growth (p.a.)", f"{ig:.1%}", delta=f"vs {hp.revenue_cagr_5y:.1%} 5Y" if hp.revenue_cagr_5y else None)
    bbg_wacc_display = getattr(model, 'bbg_wacc', None)
    k3.metric("WACC", f"{r['wacc']:.2%}", 
              delta=f"BBG: {bbg_wacc_display:.2%}" if bbg_wacc_display else None, delta_color="off")
    k4.metric("TV % of EV", f"{tv_pct:.0%}")
    k5.metric("ROIC Spread", f"{roic_sp:+.1%}", delta="Creates Value" if r['roic_gate']['value_creating'] else "Destroys Value",
              delta_color="normal" if r['roic_gate']['value_creating'] else "inverse")
    st.markdown("---")

    # Scenario + TV
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Scenario Fan")
        sc = r["scenarios"]; labels = ["Bear", "Base", "Bull"]
        prices = [sc[l]["fair_price"] for l in labels]; upsides = [sc[l]["upside_downside"] for l in labels]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=labels, y=prices, marker_color=[C_RED, C_AMBER, C_GREEN],
            text=[f"{p:,.1f}<br>({u:+.0%})" for p, u in zip(prices, upsides)], textposition="outside", textfont=dict(size=14, color=C_TEAL)))
        fig.add_hline(y=r["price"], line_dash="dash", line_color=C_TEAL, line_width=2,
                      annotation_text=f"Current: {r['price']:,.2f}", annotation_position="bottom right")
        fig.update_layout(height=400, showlegend=False, yaxis_title="Fair Price", plot_bgcolor="white", font=dict(family="Arial"))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader("TV Decomposition")
        tv = r["tv_decomposition"]
        fig2 = go.Figure(go.Pie(labels=["Explicit Period", "Terminal Value"], values=[tv["explicit_pct"], tv["tv_pct"]],
            marker_colors=[C_TEAL, C_CORAL], hole=0.5, textinfo="label+percent", textfont=dict(size=13)))
        fig2.update_layout(height=400, showlegend=False, font=dict(family="Arial"),
            annotations=[dict(text=f"TV<br>{tv['tv_pct']:.0%}", x=0.5, y=0.5, font_size=18, showarrow=False, font_color=C_CORAL)])
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown("---")
    cl, cr = st.columns(2)
    with cl:
        st.subheader("Plausibility Checks")
        for c in r["plausibility"]:
            st.write(f"{c['flag']} **{c['check']}**: implied {c['implied']} vs hist {c['historical']} ({c['ratio']})")
        st.caption("🟢 <1.5× · 🟡 1.5–2× · 🔴 >2× or exceeds max")
    with cr:
        st.subheader("ROIC Gate")
        rg = r["roic_gate"]; st.write(rg["verdict"])
        reinvest = rg.get("implied_reinvestment_rate", np.nan)
        if not np.isnan(reinvest):
            st.write(f"Implied reinvestment rate: **{reinvest:.0%}**")
            st.caption(f"{'Needs external financing.' if reinvest > 1 else f'Reinvests {reinvest:.0%}, {max(0,1-reinvest):.0%} left for dividends/buybacks.'}")
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
        pd_l, pd_r = st.columns([3, 2])
        with pd_l:
            comps = [("Revenue<br>Growth", pd_data["revenue_growth_ann"]), ("Margin<br>Effect", pd_data["margin_effect_ann"]),
                     ("Buyback<br>Yield", pd_data["buyback_ann"]), ("Dividend<br>Yield", pd_data["div_yield"])]
            vals = [c[1] for c in comps]; total = sum(vals)
            fig_pd = go.Figure(go.Waterfall(x=[c[0] for c in comps], y=vals, connector={"line": {"color": "#ccc"}},
                increasing={"marker": {"color": C_GREEN}}, decreasing={"marker": {"color": C_RED}},
                text=[f"{v:+.1%}" for v in vals], textposition="outside", textfont=dict(size=13)))
            fig_pd.add_trace(go.Bar(x=["Total"], y=[total], marker_color=C_TEAL, text=[f"{total:+.1%}"],
                textposition="outside", textfont=dict(size=14, color=C_TEAL), width=0.5))
            fig_pd.update_layout(height=400, showlegend=False, yaxis_tickformat=".0%", plot_bgcolor="white", font=dict(family="Arial"))
            st.plotly_chart(fig_pd, use_container_width=True)
        with pd_r:
            st.write(f"Revenue Growth: **{pd_data['revenue_growth_ann']:+.1%}** p.a.")
            st.write(f"Margin: **{pd_data['margin_effect_ann']:+.1%}** p.a. ({pd_data['margin_first']:.1%} → {pd_data['margin_last']:.1%})")
            st.write(f"Buyback: **{pd_data['buyback_ann']:+.1%}** p.a. ({pd_data['shares_first']:,.0f} → {pd_data['shares_last']:,.0f})")
            st.write(f"Dividend: **{pd_data['div_yield']:.1%}**")
            if pd_data["current_pe"] > 0: st.write(f"P/E: **{pd_data['current_pe']:.1f}x**")
            organic = pd_data["revenue_growth_ann"] + pd_data["margin_effect_ann"]
            if abs(total) > 0.001:
                st.write(f"**{organic/total*100:.0f}% fundamental** vs **{(1-organic/total)*100:.0f}% financial**")

    # Sensitivity
    st.markdown("---")
    st.subheader("Sensitivity: Implied Growth (WACC × Tg)")
    st.caption("Green = low implied growth (cheap). Red = high (expensive).")
    wacc_rng = np.arange(max(0.02, wacc-0.015), wacc+0.020, 0.005)
    tg_rng = np.arange(max(0.005, tg-0.01), tg+0.015, 0.005)
    rows = []
    for w in wacc_rng:
        row = {}
        for t in tg_rng:
            p2 = DCFParams(risk_free=rf, erp=erp, beta=beta, cost_of_debt_pretax=cod, tax_rate=tax,
                equity_weight=we, debt_weight=1-we, terminal_growth=t, projection_years=proj, terminal_ebit_margin=tm_r)
            p2.wacc_override = w
            m2 = ReverseDCF(model.hist, model.current, p2, ticker=r["ticker"])
            row[f"Tg={t:.1%}"] = m2.solve_implied_growth()
        row["WACC"] = w; rows.append(row)
    sdf = pd.DataFrame(rows).set_index("WACC"); sdf.index = [f"{w:.1%}" for w in wacc_rng]
    st.dataframe(sdf.style.format("{:.1%}").background_gradient(cmap="RdYlGn", axis=None), use_container_width=True)

    # Expanders
    with st.expander("Model Inputs"):
        mi1, mi2, mi3 = st.columns(3)
        with mi1:
            fcff = model._fcff_from_revenue(model.base_revenue)
            st.markdown("**FCFF Drivers**")
            st.write(f"Revenue: {model.base_revenue:,.0f} · EBIT Margin: {model.ebit_margin:.1%}")
            st.write(f"D&A: {model.da_pct:.1%} · CapEx: {model.capex_pct:.1%} · NWC: {model.nwc_pct:.1%} · Tax: {tax:.1%}")
            st.write(f"**FCFF: {fcff:,.0f}** ({fcff/model.base_revenue:.1%})")
        with mi2:
            st.markdown("**Valuation**")
            st.write(f"MCap: {model.market_cap:,.0f} · Net Debt: {model.net_debt:,.0f} · MI: {model.minority:,.0f}")
            st.write(f"**EV: {model.market_ev:,.0f}** · Shares: {model.shares:,.1f}")
        with mi3:
            st.markdown("**Historical**")
            st.write(f"5Y CAGR: {hp.revenue_cagr_5y:.1%}" if hp.revenue_cagr_5y else "N/A")
            st.write(f"Max: {hp.max_revenue_growth:.1%}" if hp.max_revenue_growth else "N/A")
            st.write(f"ROIC: {hp.median_roic:.1%}" if hp.median_roic else "N/A")
            st.caption(f"Base: {'LTM' if model.ltm_data.get('Revenue') else 'FY'}")

    warnings = r.get("validation_warnings", [])
    if warnings:
        with st.expander(f"Data Notes ({len(warnings)})"):
            for w in warnings:
                if "CRITICAL" in w:
                    st.error(w)
                elif "WARNING" in w:
                    st.warning(w)
                else:
                    st.info(w)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: FORWARD DCF (MY VIEW)
# ══════════════════════════════════════════════════════════════════════════════
with tab_forward:
    st.title(f"Forward DCF: {model.ticker}")
    st.markdown("**Build your own projection → get your fair value → compare vs market.**")

    # ── Editable Projection Table ─────────────────────────────────────────────
    st.subheader("Projection Assumptions")
    st.caption("Edit any cell. Defaults are based on the latest available data (LTM or last FY).")

    n_years = proj
    year_cols = [f"Y{i}" for i in range(1, n_years + 1)] + ["Terminal"]
    
    # Defaults: historical ratios carried forward
    base_rev = model.base_revenue
    base_margin = model.ebit_margin
    hist_cagr = hp.revenue_cagr_5y if hp.revenue_cagr_5y else 0.05

    # Build default projection
    default_data = {
        "Metric": [
            "Revenue",
            "Revenue Growth (%)",
            "EBIT Margin (%)",
            "CapEx / Rev (%)",
            "D&A / Rev (%)",
            "NWC / Rev (%)",
            "Tax Rate (%)",
        ],
        "Base": [
            round(base_rev, 0),
            "—",
            round(base_margin * 100, 1),
            round(model.capex_pct * 100, 1),
            round(model.da_pct * 100, 1),
            round(model.nwc_pct * 100, 1),
            round(tax * 100, 1),
        ],
    }

    rev = base_rev
    for i, col in enumerate(year_cols):
        if col == "Terminal":
            default_data[col] = [
                "—",
                round(tg * 100, 1),
                round((p.terminal_ebit_margin or base_margin) * 100, 1),
                round(model.capex_pct * 100, 1),
                round(model.da_pct * 100, 1),
                round(model.nwc_pct * 100, 1),
                round(tax * 100, 1),
            ]
        else:
            growth = round(hist_cagr * 100, 1)
            rev_proj = rev * (1 + hist_cagr)
            default_data[col] = [
                round(rev_proj, 0),
                growth,
                round(base_margin * 100, 1),
                round(model.capex_pct * 100, 1),
                round(model.da_pct * 100, 1),
                round(model.nwc_pct * 100, 1),
                round(tax * 100, 1),
            ]
            rev = rev_proj

    df_defaults = pd.DataFrame(default_data).set_index("Metric")

    edited = st.data_editor(
        df_defaults,
        use_container_width=True,
        num_rows="fixed",
        key=f"fwd_table_{model.ticker}",
    )

    # ── Parse edited table and compute DCF ────────────────────────────────────
    st.markdown("---")

    try:
        # Extract values from edited table
        pv_explicit = 0.0
        projection_rows = []
        rev = base_rev

        for i in range(n_years):
            col = f"Y{i+1}"
            rev_growth = float(edited.loc["Revenue Growth (%)", col]) / 100
            margin = float(edited.loc["EBIT Margin (%)", col]) / 100
            capex_pct = float(edited.loc["CapEx / Rev (%)", col]) / 100
            da_pct = float(edited.loc["D&A / Rev (%)", col]) / 100
            nwc_pct = float(edited.loc["NWC / Rev (%)", col]) / 100
            tax_pct = float(edited.loc["Tax Rate (%)", col]) / 100

            rev = rev * (1 + rev_growth)
            ebit = rev * margin
            nopat = ebit * (1 - tax_pct)
            da = rev * da_pct
            capex = rev * capex_pct
            nwc = rev * abs(nwc_pct)
            fcff = nopat + da - capex - nwc
            pv = fcff / (1 + wacc) ** (i + 1)
            pv_explicit += pv

            projection_rows.append({
                "Year": col,
                "Revenue": rev,
                "Growth": rev_growth,
                "EBIT Margin": margin,
                "EBIT": ebit,
                "NOPAT": nopat,
                "FCFF": fcff,
                "PV(FCFF)": pv,
            })

        # Terminal
        t_growth = float(edited.loc["Revenue Growth (%)", "Terminal"]) / 100
        t_margin = float(edited.loc["EBIT Margin (%)", "Terminal"]) / 100
        t_capex = float(edited.loc["CapEx / Rev (%)", "Terminal"]) / 100
        t_da = float(edited.loc["D&A / Rev (%)", "Terminal"]) / 100
        t_nwc = float(edited.loc["NWC / Rev (%)", "Terminal"]) / 100
        t_tax = float(edited.loc["Tax Rate (%)", "Terminal"]) / 100

        term_rev = rev * (1 + t_growth)
        term_ebit = term_rev * t_margin
        term_nopat = term_ebit * (1 - t_tax)
        term_fcff = term_nopat + term_rev * t_da - term_rev * t_capex - term_rev * abs(t_nwc)

        if wacc <= t_growth:
            st.error("WACC must be greater than Terminal Growth!")
            st.stop()

        tv_val = term_fcff / (wacc - t_growth)
        pv_tv = tv_val / (1 + wacc) ** n_years
        total_ev = pv_explicit + pv_tv

        fair_equity = total_ev - model.net_debt - model.minority
        fair_price = fair_equity / model.shares if model.shares else 0
        upside = (fair_price / model.price - 1) if model.price else 0

        # ── Verdict ───────────────────────────────────────────────────────────
        if upside > 0.20:
            fwd_v, fwd_c = "UNDERVALUED", C_GREEN
        elif upside > 0.05:
            fwd_v, fwd_c = "SLIGHT UPSIDE", C_GREEN
        elif upside > -0.05:
            fwd_v, fwd_c = "FAIRLY VALUED", C_AMBER
        elif upside > -0.20:
            fwd_v, fwd_c = "SLIGHT DOWNSIDE", C_CORAL
        else:
            fwd_v, fwd_c = "OVERVALUED", C_RED

        st.markdown(f"""
        <div style="background-color: {fwd_c}15; border-left: 5px solid {fwd_c}; padding: 20px 24px; border-radius: 4px; margin-bottom: 20px;">
            <span style="font-size: 28px; font-weight: bold; color: {fwd_c};">🎯 {fwd_v}</span><br>
            <span style="font-size: 18px; color: #333;">
                Your fair value: <b>{fair_price:,.1f}</b> vs current price {model.price:,.2f} → <b>{upside:+.1%}</b>
            </span>
        </div>""", unsafe_allow_html=True)

        # KPIs
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Current Price", f"{model.price:,.2f}")
        f2.metric("My Fair Value", f"{fair_price:,.1f}", delta=f"{upside:+.1%}")
        f3.metric("Enterprise Value", f"{total_ev:,.0f}")
        tv_fwd_pct = pv_tv / total_ev if total_ev else 0
        f4.metric("TV %", f"{tv_fwd_pct:.0%}")

        st.markdown("---")

        # My View vs Market
        st.subheader("My View vs Market Implied")
        implied_g = r["implied_growth"]
        avg_my_growth = np.mean([float(edited.loc["Revenue Growth (%)", f"Y{i+1}"]) for i in range(n_years)]) / 100
        avg_my_margin = float(edited.loc["EBIT Margin (%)", f"Y{n_years}"]) / 100

        cmp_l, cmp_r = st.columns([3, 2])
        with cmp_l:
            fig_cmp = go.Figure()
            fig_cmp.add_trace(go.Bar(x=["Revenue Growth (avg)", "EBIT Margin (Y5)"],
                y=[avg_my_growth, avg_my_margin], name="My View", marker_color=C_TEAL,
                text=[f"{avg_my_growth:.1%}", f"{avg_my_margin:.1%}"], textposition="outside"))
            fig_cmp.add_trace(go.Bar(x=["Revenue Growth (avg)", "EBIT Margin (Y5)"],
                y=[implied_g, model.ebit_margin], name="Market Implied", marker_color=C_CORAL,
                text=[f"{implied_g:.1%}", f"{model.ebit_margin:.1%}"], textposition="outside"))
            fig_cmp.update_layout(height=350, barmode="group", yaxis_tickformat=".0%", plot_bgcolor="white", font=dict(family="Arial"))
            st.plotly_chart(fig_cmp, use_container_width=True)
        with cmp_r:
            diff = avg_my_growth - implied_g
            st.write(f"**Your avg growth:** {avg_my_growth:.1%} p.a.")
            st.write(f"**Market implied:** {implied_g:.1%} p.a.")
            if diff > 0.02:
                st.write(f"→ You are **more bullish** than the market (+{diff:.1%}pp)")
            elif diff < -0.02:
                st.write(f"→ You are **more bearish** ({diff:+.1%}pp)")
            else:
                st.write("→ You roughly **agree** with the market")

        # Projected Cash Flows
        st.markdown("---")
        st.subheader("Projected Cash Flows")
        proj_df = pd.DataFrame(projection_rows).set_index("Year")
        term_row = pd.DataFrame([{"Revenue": term_rev, "Growth": t_growth, "EBIT Margin": t_margin,
            "EBIT": term_ebit, "NOPAT": term_nopat, "FCFF": term_fcff, "PV(FCFF)": pv_tv}], index=["Terminal"])
        display_df = pd.concat([proj_df, term_row])
        st.dataframe(display_df.style.format({
            "Revenue": "{:,.0f}", "Growth": "{:.1%}", "EBIT Margin": "{:.1%}",
            "EBIT": "{:,.0f}", "NOPAT": "{:,.0f}", "FCFF": "{:,.0f}", "PV(FCFF)": "{:,.0f}",
        }), use_container_width=True)

        # Valuation Bridge + Return Decomposition
        st.markdown("---")
        st.subheader("Valuation Bridge & Return Decomposition")
        
        bridge_l, bridge_r = st.columns([2, 3])
        with bridge_l:
            st.markdown("**EV → Equity → Fair Price**")
            st.write(f"PV Explicit: **{pv_explicit:,.0f}**")
            st.write(f"PV Terminal: **{pv_tv:,.0f}**")
            st.write(f"= EV: **{total_ev:,.0f}**")
            st.write(f"− Net Debt: {model.net_debt:,.0f}")
            st.write(f"− MI: {model.minority:,.0f}")
            st.write(f"= Equity: **{fair_equity:,.0f}**")
            st.write(f"÷ Shares: {model.shares:,.1f}")
            st.write(f"= **Fair Price: {fair_price:,.1f}** ({upside:+.1%})")
        
        with bridge_r:
            # Return decomposition: Current Price → Fair Price
            # What drives the gap?
            # 1. Revenue Growth: how much of the upside comes from revenue growing?
            # 2. Margin Expansion: EBIT margin today vs your Y5 target
            # 3. Multiple: implied EV/EBIT today vs your implied EV/EBIT
            
            current_ev = model.market_ev
            current_ebit = model.base_ebit if model.base_ebit else model.base_revenue * model.ebit_margin
            current_ev_ebit = current_ev / current_ebit if current_ebit else 0
            
            # Your projections
            final_rev = projection_rows[-1]["Revenue"]
            final_ebit = projection_rows[-1]["EBIT"]
            your_ev_ebit = total_ev / final_ebit if final_ebit else 0
            
            rev_change = (final_rev / model.base_revenue - 1) if model.base_revenue else 0
            margin_change = avg_my_margin - model.ebit_margin
            multiple_change = your_ev_ebit - current_ev_ebit
            
            # Approximate decomposition of fair price
            # Fair Price ≈ Current × (1 + Rev Growth) × (1 + Margin Effect) × (1 + Multiple Effect)
            rev_effect = rev_change  # total revenue growth over projection period
            margin_effect = (avg_my_margin / model.ebit_margin - 1) if model.ebit_margin else 0
            
            st.markdown("**What drives the difference?**")
            
            decomp_items = [
                ("Revenue Growth", f"{rev_change:+.0%} over {n_years}Y", 
                 f"Revenue grows from {model.base_revenue:,.0f} to {final_rev:,.0f}"),
                ("Margin Change", f"{model.ebit_margin:.1%} → {avg_my_margin:.1%} ({margin_change:+.1%}pp)",
                 "Higher margins = more profit per CHF revenue" if margin_change > 0 else "Lower margins = less profit per CHF revenue" if margin_change < 0 else "Margins unchanged"),
                ("EV/EBIT Multiple", f"{current_ev_ebit:.1f}x → {your_ev_ebit:.1f}x ({your_ev_ebit - current_ev_ebit:+.1f}x)",
                 "Multiple expansion = market pays more per CHF profit" if your_ev_ebit > current_ev_ebit else "Multiple contraction = market pays less per CHF profit" if your_ev_ebit < current_ev_ebit else "Multiple unchanged"),
            ]
            
            for label, value, explanation in decomp_items:
                st.write(f"**{label}:** {value}")
                st.caption(explanation)
            
            # Waterfall: Current EV → Your EV
            ev_from_rev = current_ev * rev_change  # EV increase from revenue alone
            ev_from_margin = current_ev * margin_effect  # EV increase from margin
            ev_residual = total_ev - current_ev - ev_from_rev - ev_from_margin  # multiple/other
            
            fig_bridge = go.Figure(go.Waterfall(
                x=["Current EV", "Revenue<br>Growth", "Margin<br>Change", "Multiple<br>& Other", "Your EV"],
                y=[current_ev, ev_from_rev, ev_from_margin, ev_residual, 0],
                measure=["absolute", "relative", "relative", "relative", "total"],
                connector={"line": {"color": "#ccc"}},
                increasing={"marker": {"color": C_GREEN}},
                decreasing={"marker": {"color": C_RED}},
                totals={"marker": {"color": C_TEAL}},
                text=[f"{current_ev:,.0f}", f"{ev_from_rev:+,.0f}", f"{ev_from_margin:+,.0f}", 
                      f"{ev_residual:+,.0f}", f"{total_ev:,.0f}"],
                textposition="outside", textfont=dict(size=11),
            ))
            fig_bridge.update_layout(height=380, showlegend=False, plot_bgcolor="white", 
                                    font=dict(family="Arial"), yaxis_title="Enterprise Value")
            st.plotly_chart(fig_bridge, use_container_width=True)

    except Exception as e:
        st.error(f"Calculation error: {e}. Check your inputs in the table above.")
