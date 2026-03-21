"""
Reverse DCF Dashboard
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from reverse_dcf_engine import ReverseDCF, DCFParams
import tempfile

st.set_page_config(page_title="Reverse DCF", page_icon="📊", layout="wide")
C_TEAL, C_CORAL, C_AMBER, C_GREEN, C_RED = "#003850", "#F26B43", "#FBAE40", "#2ECC71", "#E74C3C"

st.sidebar.title("📊 Reverse DCF")
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
    st.title("Reverse DCF Engine")
    st.info("Upload your reverse_dcf.xlsx (with HC data) to start.")
    st.stop()

# ── Sidebar params ────────────────────────────────────────────────────────────
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
st.sidebar.subheader("Model")
tg = st.sidebar.slider("Terminal Growth (%)", 0.0, 4.0, round(p.terminal_growth*100,1), 0.1) / 100
tm = st.sidebar.slider("Terminal EBIT Margin (%)", 5.0, 35.0, round((p.terminal_ebit_margin or 0.15)*100,1), 0.5) / 100
proj = st.sidebar.slider("Projection Years", 3, 10, p.projection_years)
bull = st.sidebar.slider("Bull Offset (pp)", 0.0, 10.0, round(p.bull_growth_add*100,1), 0.5) / 100
bear = st.sidebar.slider("Bear Offset (pp)", -10.0, 0.0, round(p.bear_growth_add*100,1), 0.5) / 100

params = DCFParams(risk_free=rf, erp=erp, beta=beta, cost_of_debt_pretax=cod, tax_rate=tax,
    equity_weight=we, debt_weight=1-we, terminal_growth=tg, terminal_ebit_margin=tm,
    projection_years=proj, bull_growth_add=bull, bear_growth_add=bear)
model.params = params
model._prepare_data()
r = model.run()

# ── Verdict Logic (transparent thresholds) ────────────────────────────────────
ig = r["implied_growth"]
hp = r["historical_profile"]
red_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🔴")
yellow_flags = sum(1 for c in r["plausibility"] if c["flag"] == "🟡")
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
if ig > 0.25:
    reasons.append(f"Implied growth ({ig:.1%}) is very high")

# Verdict thresholds:
# OVERPRICED: 3+ red flags (implied growth far exceeds all historical benchmarks)
# LIKELY OVERPRICED: 2 red flags
# FAIRLY VALUED: 0 red flags, ROIC > WACC, positive growth
# UNDERVALUED: negative implied growth with positive historical growth
# FAIR VALUE RANGE: everything else
if red_flags >= 3:
    verdict, v_color, v_icon = "OVERPRICED", C_RED, "🔴"
    v_detail = "Market expectations significantly exceed what the company has historically delivered."
    v_action = "The market is pricing in growth well beyond the historical track record. Either the company has a strong catalyst justifying this, or the stock is expensive relative to fundamentals."
elif red_flags >= 2:
    verdict, v_color, v_icon = "LIKELY OVERPRICED", C_CORAL, "🟠"
    v_detail = "Implied growth is stretched relative to historical fundamentals."
    v_action = "Market expects meaningfully higher growth than history suggests. Requires a clear thesis for why the future will differ from the past."
elif red_flags == 0 and roic_sp > 0 and ig >= 0:
    verdict, v_color, v_icon = "FAIRLY VALUED", C_GREEN, "🟢"
    v_detail = "Implied expectations are broadly consistent with the historical track record."
    v_action = "Market expectations are achievable based on history. Not a screaming buy or sell — returns will depend on execution vs. these expectations."
elif ig < 0 and hp.revenue_cagr_5y and hp.revenue_cagr_5y >= 0:
    verdict, v_color, v_icon = "POTENTIALLY UNDERVALUED", C_GREEN, "🟢"
    v_detail = "Market implies revenue decline — potential opportunity if fundamentals hold."
    v_action = "The market is pricing in deterioration. If you believe the business is stable, this could represent an opportunity."
else:
    verdict, v_color, v_icon = "FAIR VALUE RANGE", C_AMBER, "🟡"
    v_detail = "Mixed signals — some implied expectations stretched, others reasonable."
    v_action = "No clear mispricing signal. Dig deeper into the specific red/yellow flags below."

# ── Title + Banner ────────────────────────────────────────────────────────────
st.title(f"Reverse DCF: {r['ticker']}")

st.markdown(f"""
<div style="background-color: {v_color}15; border-left: 5px solid {v_color};
            padding: 20px 24px; border-radius: 4px; margin-bottom: 20px;">
    <span style="font-size: 28px; font-weight: bold; color: {v_color};">
        {v_icon} {verdict}
    </span><br>
    <span style="font-size: 18px; color: #333; line-height: 1.6;">
        Market implies <b>{ig:.1%} p.a. revenue growth</b> over {proj} years to justify the current price of {r['price']:,.2f}.
        {v_detail}
    </span>
    <br><span style="font-size: 15px; color: #444; line-height: 1.5;">
        <b>So what?</b> {v_action}
    </span>
    {"<br><span style='font-size: 13px; color: #666;'>" + " · ".join(reasons[:3]) + "</span>" if reasons else ""}
</div>
""", unsafe_allow_html=True)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Price", f"{r['price']:,.2f}")
k2.metric("Implied Growth (p.a.)", f"{ig:.1%}",
          delta=f"vs {hp.revenue_cagr_5y:.1%} 5Y CAGR" if hp.revenue_cagr_5y else None)
k3.metric("WACC", f"{r['wacc']:.2%}")
k4.metric("TV % of EV", f"{tv_pct:.0%}")
k5.metric("ROIC Spread", f"{roic_sp:+.1%}",
          delta="Creates Value" if r['roic_gate']['value_creating'] else "Destroys Value",
          delta_color="normal" if r['roic_gate']['value_creating'] else "inverse")

st.markdown("---")

# ── Scenario Fan + TV ─────────────────────────────────────────────────────────
left, right = st.columns([3, 2])
with left:
    st.subheader("Scenario Fan")
    sc = r["scenarios"]
    labels = ["Bear", "Base", "Bull"]
    prices = [sc[l]["fair_price"] for l in labels]
    upsides = [sc[l]["upside_downside"] for l in labels]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=prices, marker_color=[C_RED, C_AMBER, C_GREEN],
        text=[f"{p:,.1f}<br>({u:+.0%})" for p, u in zip(prices, upsides)],
        textposition="outside", textfont=dict(size=14, color=C_TEAL)))
    fig.add_hline(y=r["price"], line_dash="dash", line_color=C_TEAL, line_width=2,
                  annotation_text=f"Current: {r['price']:,.2f}", annotation_position="bottom right")
    fig.update_layout(height=400, showlegend=False, yaxis_title="Fair Price",
                     plot_bgcolor="white", font=dict(family="Arial"))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Bear assumes {sc['Bear']['growth_rate']:.1%} growth, Bull {sc['Bull']['growth_rate']:.1%}. "
               f"Base ({sc['Base']['growth_rate']:.1%}) is the rate that exactly justifies today's price.")

with right:
    st.subheader("TV Decomposition")
    tv = r["tv_decomposition"]
    fig2 = go.Figure(go.Pie(labels=["Explicit Period", "Terminal Value"],
        values=[tv["explicit_pct"], tv["tv_pct"]], marker_colors=[C_TEAL, C_CORAL],
        hole=0.5, textinfo="label+percent", textfont=dict(size=13)))
    fig2.update_layout(height=400, showlegend=False, font=dict(family="Arial"),
        annotations=[dict(text=f"TV<br>{tv['tv_pct']:.0%}", x=0.5, y=0.5,
                         font_size=18, showarrow=False, font_color=C_CORAL)])
    st.plotly_chart(fig2, use_container_width=True)
    if tv_pct > 0.85:
        st.caption(f"⚠️ {tv_pct:.0%} of the valuation depends on long-term assumptions (terminal growth, terminal margin). "
                   f"Small changes in WACC or Tg will significantly shift the fair value. Use the sensitivity table below to gauge the range.")
    else:
        st.caption(f"{tv_pct:.0%} of value comes from the terminal period. A healthy split — the near-term cash flows provide meaningful valuation support.")

# ── Plausibility + ROIC ──────────────────────────────────────────────────────
st.markdown("---")
cl, cr = st.columns(2)
with cl:
    st.subheader("Plausibility Checks")
    for c in r["plausibility"]:
        st.write(f"{c['flag']} **{c['check']}**: implied {c['implied']} vs hist {c['historical']} ({c['ratio']})")
    st.caption("🟢 = plausible (within 1.5× historical) · 🟡 = stretched (1.5–2×) · 🔴 = implausible (>2× or exceeds max). "
               "These compare what the market implies against what the company has actually delivered.")

with cr:
    st.subheader("ROIC Gate")
    rg = r["roic_gate"]
    st.write(rg["verdict"])
    reinvest = rg.get("implied_reinvestment_rate", np.nan)
    if not np.isnan(reinvest):
        st.write(f"Implied reinvestment rate: **{reinvest:.0%}**")
        if reinvest > 1:
            st.caption(f"A reinvestment rate of {reinvest:.0%} means the company would need to reinvest more than its entire earnings to achieve the implied growth. This is only possible with external financing.")
        elif reinvest > 0.5:
            st.caption(f"The company would need to reinvest {reinvest:.0%} of earnings — leaving {1-reinvest:.0%} for dividends/buybacks.")
    
    st.caption(f"**What this means:** ROIC ({rg['roic']:.1%}) measures how efficiently the company turns invested capital into profit. "
               f"When ROIC > WACC ({rg['wacc']:.1%}), every CHF of growth creates value. "
               f"The spread of {roic_sp:+.1%} means {'each 1% of growth adds roughly {:.1f}% of extra value.'.format(roic_sp*10) if roic_sp > 0 else 'growth actually destroys shareholder value.'}")
    
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(x=["ROIC", "WACC"], y=[rg["roic"], rg["wacc"]],
        marker_color=[C_GREEN if rg["value_creating"] else C_RED, C_TEAL],
        text=[f"{rg['roic']:.1%}", f"{rg['wacc']:.1%}"], textposition="outside"))
    fig3.update_layout(height=250, showlegend=False, yaxis_tickformat=".0%",
                      plot_bgcolor="white", font=dict(family="Arial"))
    st.plotly_chart(fig3, use_container_width=True)

# ── Performance Decomposition ─────────────────────────────────────────────────
st.markdown("---")
pd_data = r.get("performance_decomposition", {})
if pd_data.get("available"):
    st.subheader(f"Performance Decomposition ({pd_data['start_year']}–{pd_data['end_year']})")
    st.caption("How was historical performance generated? Real growth, margin expansion, financial engineering, or just multiple inflation?")
    
    pd_left, pd_right = st.columns([3, 2])
    with pd_left:
        components = [
            ("Revenue<br>Growth", pd_data["revenue_growth_ann"]),
            ("Margin<br>Effect", pd_data["margin_effect_ann"]),
            ("Buyback<br>Yield", pd_data["buyback_ann"]),
            ("Dividend<br>Yield", pd_data["div_yield"]),
        ]
        labels = [c[0] for c in components]
        values = [c[1] for c in components]
        
        fig_pd = go.Figure(go.Waterfall(x=labels, y=values,
            connector={"line": {"color": "#ccc"}},
            increasing={"marker": {"color": C_GREEN}},
            decreasing={"marker": {"color": C_RED}},
            text=[f"{v:+.1%}" for v in values], textposition="outside", textfont=dict(size=13)))
        total = sum(values)
        fig_pd.add_trace(go.Bar(x=["Total<br>Shareholder<br>Return"], y=[total],
            marker_color=C_TEAL, text=[f"{total:+.1%}"], textposition="outside",
            textfont=dict(size=14, color=C_TEAL), width=0.5))
        fig_pd.update_layout(height=400, showlegend=False, yaxis_tickformat=".0%",
            yaxis_title="Annualized Contribution", plot_bgcolor="white", font=dict(family="Arial"))
        st.plotly_chart(fig_pd, use_container_width=True)
    
    with pd_right:
        st.markdown("**Annualized Components**")
        st.write(f"Revenue Growth: **{pd_data['revenue_growth_ann']:+.1%}** p.a.")
        st.write(f"Margin Change: **{pd_data['margin_effect_ann']:+.1%}** p.a. ({pd_data['margin_first']:.1%} → {pd_data['margin_last']:.1%})")
        st.write(f"Buyback Yield: **{pd_data['buyback_ann']:+.1%}** p.a. ({pd_data['shares_first']:,.0f} → {pd_data['shares_last']:,.0f} shares)")
        st.write(f"Dividend Yield: **{pd_data['div_yield']:.1%}**")
        st.markdown("---")
        st.write(f"EPS Growth: **{pd_data['eps_growth_ann']:+.1%}** p.a.")
        if pd_data["current_pe"] > 0:
            st.write(f"Current P/E: **{pd_data['current_pe']:.1f}x**")
        st.markdown("---")
        organic = pd_data["revenue_growth_ann"] + pd_data["margin_effect_ann"]
        financial = pd_data["buyback_ann"] + pd_data["div_yield"]
        if abs(total) > 0.001:
            organic_pct = organic / total * 100 if total != 0 else 0
            st.write(f"**{organic_pct:.0f}% fundamental** (rev + margin) vs **{100-organic_pct:.0f}% financial** (buyback + div)")
            if organic_pct > 70:
                st.caption("✅ Returns are primarily driven by real business growth — this is high-quality compounding.")
            elif organic_pct > 40:
                st.caption("Mixed quality — meaningful contribution from both business growth and capital returns.")
            else:
                st.caption("⚠️ Returns are primarily driven by buybacks/dividends, not business growth. Sustainable only if cash generation persists.")

# ── Sensitivity ───────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Sensitivity: Implied Growth (WACC × Terminal Growth)")
st.caption("Each cell shows the implied annual revenue growth at that WACC/Tg combination. "
           "Green = low expectations (potentially cheap). Red = high expectations (potentially expensive). "
           "Find your best estimate of WACC and Tg, then ask: is that growth rate achievable?")

wacc_rng = np.arange(max(0.02, r["wacc"]-0.015), r["wacc"]+0.020, 0.005)
tg_rng = np.arange(max(0.005, tg-0.01), tg+0.015, 0.005)
rows = []
for w in wacc_rng:
    row = {}
    for t in tg_rng:
        p2 = DCFParams(risk_free=rf, erp=erp, beta=beta, cost_of_debt_pretax=cod,
            tax_rate=tax, equity_weight=we, debt_weight=1-we,
            terminal_growth=t, projection_years=proj, terminal_ebit_margin=tm)
        p2.wacc_override = w
        m2 = ReverseDCF(model.hist, model.current, p2, ticker=r["ticker"])
        ig2 = m2.solve_implied_growth()
        row[f"Tg={t:.1%}"] = ig2
    row["WACC"] = w
    rows.append(row)
sdf = pd.DataFrame(rows).set_index("WACC")
sdf.index = [f"{w:.1%}" for w in wacc_rng]
st.dataframe(sdf.style.format("{:.1%}").background_gradient(cmap="RdYlGn", axis=None), use_container_width=True)

# ── Model Inputs ──────────────────────────────────────────────────────────────
st.markdown("---")
with st.expander("Model Inputs"):
    mi1, mi2, mi3 = st.columns(3)
    with mi1:
        st.markdown("**FCFF Drivers**")
        st.write(f"Base Revenue: {model.base_revenue:,.0f}")
        st.write(f"EBIT Margin: {model.ebit_margin:.1%}")
        st.write(f"D&A / Rev: {model.da_pct:.1%}")
        st.write(f"CapEx / Rev: {model.capex_pct:.1%}")
        st.write(f"NWC / Rev: {model.nwc_pct:.1%}")
        st.write(f"Tax Rate: {model.params.tax_rate:.1%}")
        fcff = model._fcff_from_revenue(model.base_revenue)
        st.write(f"**Base FCFF: {fcff:,.0f}** ({fcff/model.base_revenue:.1%} margin)")
    with mi2:
        st.markdown("**Valuation**")
        st.write(f"Market Cap: {model.market_cap:,.0f}")
        st.write(f"Net Debt: {model.net_debt:,.0f}")
        st.write(f"Minority Interest: {model.minority:,.0f}")
        st.write(f"**Enterprise Value: {model.market_ev:,.0f}**")
        st.write(f"Shares: {model.shares:,.1f}")
    with mi3:
        st.markdown("**Historical Profile**")
        st.write(f"5Y Rev CAGR: {hp.revenue_cagr_5y:.1%}" if hp.revenue_cagr_5y else "5Y CAGR: N/A")
        st.write(f"3Y Rev CAGR: {hp.revenue_cagr_3y:.1%}" if hp.revenue_cagr_3y else "3Y CAGR: N/A")
        st.write(f"Max Growth: {hp.max_revenue_growth:.1%}" if hp.max_revenue_growth else "N/A")
        st.write(f"ROIC: {hp.median_roic:.1%}" if hp.median_roic else "N/A")
        ltm_note = "LTM" if model.ltm_data.get("Revenue") else "FY"
        st.caption(f"Base data: {ltm_note}. Historical profile from FY data only.")

# ── Verdict Methodology ──────────────────────────────────────────────────────
with st.expander("Verdict Methodology"):
    st.markdown("""
    **How the verdict is determined:**
    
    Each plausibility check compares the implied growth against historical benchmarks:
    - 🟢 **Plausible**: implied growth within 1.5× the historical benchmark
    - 🟡 **Stretched**: implied growth 1.5–2× the benchmark  
    - 🔴 **Implausible**: implied growth >2× the benchmark or exceeds historical maximum
    
    **Verdict thresholds:**
    - **OVERPRICED** (🔴): 3+ red flags — market expects far more than history supports
    - **LIKELY OVERPRICED** (🟠): 2 red flags — expectations are stretched
    - **FAIRLY VALUED** (🟢): 0 red flags, ROIC > WACC, positive implied growth
    - **POTENTIALLY UNDERVALUED** (🟢): negative implied growth despite positive historical growth
    - **FAIR VALUE RANGE** (🟡): mixed signals
    
    **Important caveats:**
    - This is a *screening tool*, not a price target generator
    - High TV % (>80%) means the result is very sensitive to WACC/Tg assumptions
    - The verdict compares against *historical* performance — companies with genuine catalysts may justify higher expectations
    - Always cross-check with your own fundamental analysis
    """)

# ── Warnings ──────────────────────────────────────────────────────────────────
warnings = r.get("validation_warnings", [])
if warnings:
    with st.expander(f"Data Validation ({len(warnings)} notes)", expanded=False):
        for w in warnings:
            if "CRITICAL" in w: st.error(w)
            elif "WARNING" in w: st.warning(w)
            else: st.info(w)

# ── Historical Data ──────────────────────────────────────────────────────────
with st.expander("Historical Data"):
    hd = model.hist.copy(); hd.index = hd.index.strftime("%Y")
    st.dataframe(hd.style.format("{:,.0f}", na_rep="—"), use_container_width=True)
