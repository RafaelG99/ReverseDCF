"""CORE DCF v4 — Reverse DCF · Mein Pfad · Kontext"""
import tempfile
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from reverse_dcf_engine import CoreDCF, Base
import guidance_dcf as gd

st.set_page_config(page_title="CORE DCF", page_icon="📊", layout="wide")
TEAL, CORAL, AMBER, GREEN, RED = "#003850", "#F26B43", "#FBAE40", "#2ECC71", "#E74C3C"
FLAG = {"green": ("🟢", GREEN), "amber": ("🟡", AMBER), "red": ("🔴", RED)}
pct = lambda x: "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.1%}"


# ── Upload ────────────────────────────────────────────────────────────────────
st.sidebar.title("📊 CORE DCF v4")
up = st.sidebar.file_uploader("core_dcf_template.xlsx", type=["xlsx"])
if not up:
    st.title("CORE DCF v4"); st.info("Upload core_dcf_template.xlsx (Fundamentals HC + Current + WACC, optional Peers / Guidance)."); st.stop()
with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    tmp.write(up.read()); tmp_path = tmp.name
try:
    model = CoreDCF.from_excel(tmp_path)
except Exception as e:
    st.sidebar.error(f"Load error: {e}"); st.stop()
st.sidebar.success(f"{model.ticker}" + ("  ·  Guidance ✓" if model.guidance else ""))

# ── Valuation parameters ──────────────────────────────────────────────────────
st.sidebar.markdown("### Parameter")
w_default = model.bbg_wacc if model.bbg_wacc and 0.01 < model.bbg_wacc < 0.25 else model.config.wacc
wacc = st.sidebar.number_input("WACC (%)", value=round(w_default * 100, 2), step=0.25, format="%.2f",
                               help=f"BBG WACC: {model.bbg_wacc:.2%}" if model.bbg_wacc else None) / 100
tg = st.sidebar.slider("Terminal growth (%)", 0.0, 3.0, min(round(model.config.terminal_growth * 100, 1), 1.5), 0.1) / 100
n_impl = st.sidebar.slider("Stage 2 years (implied / path)", 5, 15, 8)
n_cons = st.sidebar.radio("Stage 1 consensus years", [2, 3], horizontal=True) if model.has_consensus_fy3 else 2
use_bbg_ev = st.sidebar.checkbox("Use BBG EV (incl. adjustments)", value=True,
                                 help="OFF: EV = market cap + net debt + minorities. Switch off if the BBG adjustment looks spurious.")
model.config.wacc, model.config.terminal_growth = wacc, tg
model.config.implied_years, model.config.consensus_years, model.config.use_bbg_ev = n_impl, n_cons, use_bbg_ev
model._prepare()

# ── Normalized base (explicit input) ──────────────────────────────────────────
st.sidebar.markdown("### Basis (normalisiert)")
icon, color = FLAG[model.margin_confidence]
st.sidebar.markdown(f"{icon} {model.margin_reason}")
bp = model.base_proposal
key = f"base_{model.ticker}"
if key not in st.session_state:
    st.session_state[key] = {"margin": bp.ebit_margin, "capex": bp.capex_pct, "da": bp.da_pct, "sbc": bp.sbc_pct, "nwc": bp.nwc_pct, "tax": bp.tax_rate}
sb = st.session_state[key]
if "revenue" not in sb: sb["revenue"] = model.base_revenue
sb["revenue"] = st.sidebar.number_input("Base revenue", value=float(sb["revenue"]), step=1.0, format="%.1f",
                                        help=f"Reported {model.base_revenue:,.1f}. Override e.g. for recurring-only guidance bases.")
c1, c2 = st.sidebar.columns(2)
sb["margin"] = c1.number_input("EBIT margin (%)", value=round(sb["margin"] * 100, 1), step=0.5, format="%.1f",
                               help=f"Current {model.current_margin:.1%} · proposal {bp.ebit_margin:.1%}") / 100
sb["tax"] = c2.number_input("Tax rate (%)", value=round(sb["tax"] * 100, 1), step=1.0, format="%.1f") / 100
c3, c4 = st.sidebar.columns(2)
sb["capex"] = c3.number_input("CapEx/Rev (%)", value=round(sb["capex"] * 100, 1), step=0.5, format="%.1f") / 100
sb["da"] = c4.number_input("D&A/Rev (%)", value=round(sb["da"] * 100, 1), step=0.5, format="%.1f") / 100
c5, c6 = st.sidebar.columns(2)
sb["sbc"] = c5.number_input("SBC/Rev (%)", value=round(sb["sbc"] * 100, 1), step=0.5, format="%.1f") / 100
sb["nwc"] = c6.number_input("NWC/ΔRev (%)", value=round(sb["nwc"] * 100, 1), step=1.0, format="%.1f",
                            help=f"From DSO {model.dso:.0f}d + DSI {model.dpi:.0f}d") / 100
if st.sidebar.button("Reset to proposal"):
    del st.session_state[key]; st.rerun()
base = Base(sb["revenue"], sb["margin"], sb["da"], sb["capex"], sb["sbc"], sb["nwc"], sb["tax"])
model.set_base(base)
base_fcff = base.fcff()
st.sidebar.caption(f"Revenue {base.revenue:,.0f} · Base FCFF **{base_fcff:,.0f}** ({base_fcff/model.base_revenue:.1%}) · "
                   f"FCF yield on EV **{base_fcff/model.market_ev:.1%}**" if model.market_ev else "")

sol = model.solve_implied_growth(); ig = sol["growth"]
tab1, tab2, tab3 = st.tabs(["🔍 Reverse DCF", "🛠 Mein Pfad", "📚 Kontext"])


# ══ TAB 1: REVERSE DCF ════════════════════════════════════════════════════════
with tab1:
    st.title(model.ticker)
    checks = model.plausibility(ig) if sol["reliable"] else []
    reds = sum(c["flag"] == "🔴" for c in checks); greens = sum(c["flag"] == "🟢" for c in checks)
    if not sol["reliable"]:
        verdict, vc, line = "NOT SOLVABLE", RED, sol["reason"]
    else:
        line = f"Market implies <b>{ig:.1%} p.a.</b> FCF growth for {n_impl} years (after {n_cons}Y consensus) to justify {model.price_local:,.2f}."
        if model.margin_confidence == "red": verdict, vc = "BASE UNCERTAIN", AMBER; line += " Base margin set manually — treat as scenario."
        elif reds >= 2: verdict, vc = "ABOVE HISTORY", CORAL
        elif greens >= 2: verdict, vc = "BELOW / IN LINE WITH HISTORY", GREEN
        else: verdict, vc = "STRETCHED", AMBER
    st.markdown(f"""<div style="background:{vc}15;border-left:5px solid {vc};padding:18px 22px;border-radius:4px;margin-bottom:16px;">
        <span style="font-size:26px;font-weight:bold;color:{vc};">{verdict}</span><br><span style="font-size:16px;color:#333;">{line}</span></div>""",
        unsafe_allow_html=True)
    k = st.columns(5)
    k[0].metric("Price", f"{model.price_local:,.2f}")
    k[1].metric("Implied growth", pct(ig) if sol["reliable"] else "n/a")
    k[2].metric("Base FCFF", f"{base_fcff:,.0f}", delta=f"{base_fcff/model.market_ev:.1%} yield" if model.market_ev else None, delta_color="off")
    ev0 = model.ev_from_growth(ig if sol["reliable"] else 0.0)
    k[3].metric("TV share", pct(ev0["pv_tv"] / ev0["ev"]) if np.isfinite(ev0["ev"]) and ev0["ev"] else "n/a")
    k[4].metric("EV / EBITDA (LTM)", f"{model.market_ev / (model.base_ebit + model.base_revenue*base.da_pct):.1f}x"
                if (model.base_ebit + model.base_revenue * base.da_pct) > 0 else "n/m")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Implied growth: WACC × Tg")
        w_rng = np.round(np.arange(max(0.03, wacc - 0.02), wacc + 0.021, 0.005), 4)
        t_rng = np.round(np.arange(max(0.0, tg - 0.01), tg + 0.011, 0.005), 4)
        sdf = model.sensitivity_implied(w_rng, t_rng)
        try: st.dataframe(sdf.style.format("{:.1%}").background_gradient(cmap="RdYlGn_r", axis=None), use_container_width=True)
        except ImportError: st.dataframe(sdf.style.format("{:.1%}"), use_container_width=True)
    with right:
        st.subheader("Plausibility")
        for c in checks: st.write(f"{c['flag']} implied {pct(ig)} vs **{c['check']}** {pct(c['hist'])}")
        if model.target_price_local: st.write(f"⚪ Analyst target {model.target_price_local:,.1f} ({model.target_price_local/model.price_local-1:+.0%})")
        st.subheader("Inputs used")
        st.write(f"EV {model.market_ev:,.0f} ({model.ev_source}) = MCap {model.market_cap:,.0f} + net debt {model.net_debt:,.0f} + MI {model.minority:,.0f}"
                 + (f" + adj. {model.ev_adjustment:,.0f}" if model.ev_adjustment else ""))
        st.write(f"Base: revenue {base.revenue:,.0f} · EBIT margin {base.ebit_margin:.1%} (current {model.current_margin:.1%}) · "
                 f"D&A {base.da_pct:.1%} · CapEx {base.capex_pct:.1%} · SBC {base.sbc_pct:.1%} · NWC {base.nwc_pct:.0%} of ΔRev · tax {base.tax_rate:.0%}")
        st.write("Consensus: " + " · ".join(f"FY{i+1} {g:+.1%}" for i, g in enumerate(model.consensus_growth)) + f" (Stage 1 = {n_cons}Y)")
        st.write(f"Shares {model.shares:,.2f}" + (f" · FX {model.fx}" if model.fx != 1 else ""))
    if model.warnings:
        with st.expander(f"Data notes ({len(model.warnings)})"):
            for w in model.warnings: st.write("• " + w)


# ══ TAB 2: MEIN PFAD ══════════════════════════════════════════════════════════
with tab2:
    st.title(f"Mein Pfad: {model.ticker}")
    n_path = n_cons + n_impl
    g_prefill = None

    # Optional segments (Guidance sheet or manual)
    use_seg = st.checkbox("Revenue from segments (management plan)", value=bool(model.guidance))
    if use_seg:
        gsh = model.guidance or {}
        n_plan = int(st.number_input("Plan years", 3, 10, int(gsh.get("n_years", 5))))
        seg_default = pd.DataFrame([{"Segment": s.name, "Base": s.base, "Target": s.target, "Scalable": s.scalable,
                                     "Ramp %": ",".join(f"{x*100:.0f}" for x in s.ramp) if s.ramp else ""} for s in gsh.get("segments", [])]
                                   or [{"Segment": "Total", "Base": model.base_revenue, "Target": model.base_revenue * 1.5, "Scalable": True, "Ramp %": ""}])
        seg_edit = st.data_editor(seg_default, num_rows="dynamic", use_container_width=True, key=f"seg_{model.ticker}",
                                  column_config={"Scalable": st.column_config.CheckboxColumn(help="Plan-fulfilment lever applies")})
        segs = []
        for _, r in seg_edit.iterrows():
            if pd.isna(r["Segment"]) or str(r["Segment"]).strip() == "": continue
            rt = str(r["Ramp %"] or "").strip(); ramp = [float(x) / 100 for x in rt.replace(";", ",").split(",")] if rt else None
            if ramp and len(ramp) != n_plan: st.warning(f"{r['Segment']}: ramp needs {n_plan} values — ignored"); ramp = None
            segs.append(gd.Segment(str(r["Segment"]), float(r["Base"] or 0), float(r["Target"] or 0), bool(r["Scalable"]), ramp))
        fulfil = st.slider("Plan fulfilment (scalable segments)", 0.0, 1.5, 1.0, 0.05, format="%.2f") if any(s.scalable for s in segs) else 1.0
        seg_base = sum(s.base for s in segs)
        if segs and seg_base > 0:
            if abs(seg_base / model.base_revenue - 1) > 0.05:
                st.caption(f"Segment base {seg_base:,.0f} ≠ model base revenue {model.base_revenue:,.0f} (e.g. recurring vs. total). "
                           f"Growth rates are applied to the model base; absolute level difference is treated as non-recurring.")
            g_seg = gd.growth_row(segs, n_plan, fulfil)
            g_prefill = (g_seg + [tg + (g_seg[-1] - tg) * 0.5] * (n_path - n_plan))[:n_path]
            with st.expander("Segment revenue path"):
                st.dataframe(gd.segment_paths(segs, n_plan, fulfil).T.style.format("{:,.0f}"), use_container_width=True)

    # Path table (percent units in the editor)
    defaults = model.path_defaults(ig if sol["reliable"] else 0.05, n_path)
    if g_prefill is not None: defaults["Growth"] = g_prefill
    pkey = f"path_{model.ticker}_{n_path}_{use_seg}_{round(fulfil, 2) if use_seg else 0}"
    st.caption("Rows = years, values in %. Default growth = " + ("segment plan" if g_prefill is not None else "market-implied path (fair value = price)") +
               ", margin = base. After the last year, growth fades linearly to Tg over 10 years, margin held.")
    disp = (defaults * 100).round(1)
    disp.columns = ["Growth %", "EBIT margin %", "CapEx/Rev %", "D&A/Rev %", "SBC/Rev %", "Tax %"]
    edited = st.data_editor(disp.T, use_container_width=True, key=pkey)   # metrics as rows, years as columns
    path = (edited.T / 100.0); path.columns = defaults.columns
    f1, f2 = st.columns(2)
    g_fade = f1.number_input("Growth in first fade year (%)", value=round(min(float(path['Growth'].iloc[-1]), 0.10) * 100, 1), step=0.5, format="%.1f",
                             help="Fade starts here and goes linearly to Tg over 10 years. Default: last path growth, capped at 10%.") / 100
    m_fade = f2.number_input("EBIT margin at end of fade (%)", value=round(float(path['EBIT Margin'].iloc[-1]) * 100, 1), step=0.5, format="%.1f",
                             help="Terminal margin. Default: last path margin (no fade).") / 100
    fade_kw = dict(g_fade_start=g_fade, margin_fade_to=m_fade)

    try:
        res = model.value_path(path, **fade_kw)
        bear = model.value_path(path, growth_scale=0.5, margin_shift=-0.02, **fade_kw)
        bull = model.value_path(path, growth_scale=1.5, margin_shift=+0.02, **fade_kw)
        up = res["upside"]; vc = GREEN if up > 0.2 else AMBER if up > -0.1 else RED
        st.markdown(f"""<div style="background:{vc}15;border-left:5px solid {vc};padding:16px 22px;border-radius:4px;margin:12px 0;">
            <span style="font-size:24px;font-weight:bold;color:{vc};">Fair value {res['price']:,.1f}  ({up:+.0%})</span>
            <span style="font-size:15px;color:#333;">  ·  Bear {bear['price']:,.0f} / Bull {bull['price']:,.0f}  ·  price {model.price_local:,.2f}</span></div>""",
            unsafe_allow_html=True)
        k = st.columns(5)
        k[0].metric("EV", f"{res['ev']:,.0f}"); k[1].metric("TV share", pct(res["tv_pct"]))
        k[2].metric(f"EV / EBITDA Y{n_path}", f"{res['ev_ebitda_yn']:.1f}x" if np.isfinite(res["ev_ebitda_yn"]) else "n/m")
        avg_g = (path["Growth"] + 1).prod() ** (1 / n_path) - 1
        k[3].metric(f"Revenue CAGR Y1-Y{n_path}", pct(avg_g), delta=f"implied {pct(ig)}" if sol["reliable"] else None, delta_color="off")
        k[4].metric(f"EBIT margin Y{n_path}", pct(path["EBIT Margin"].iloc[-1]), delta=f"base {base.ebit_margin:.1%}", delta_color="off")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Fair value roll-forward")
            yrs = min(n_path, 10); rf = model.roll_forward(path, yrs, **fade_kw)
            rf_bear = model.roll_forward(path, yrs, growth_scale=0.5, margin_shift=-0.02, **fade_kw)
            rf_bull = model.roll_forward(path, yrs, growth_scale=1.5, margin_shift=+0.02, **fade_kw)
            x = list(range(yrs + 1)); fig = go.Figure()
            fig.add_trace(go.Scatter(x=x, y=rf_bull, mode="lines", name="Bull", line=dict(color=GREEN, dash="dot")))
            fig.add_trace(go.Scatter(x=x, y=rf, mode="lines+markers+text", name="Path", text=[f"{v:,.0f}" for v in rf], textposition="top center", line=dict(color=TEAL, width=3)))
            fig.add_trace(go.Scatter(x=x, y=rf_bear, mode="lines", name="Bear", line=dict(color=RED, dash="dot")))
            fig.add_hline(y=model.price_local, line_dash="dash", line_color=CORAL, annotation_text=f"price {model.price_local:,.2f}")
            fig.update_layout(height=380, plot_bgcolor="white", font=dict(family="Arial"), xaxis_title="years from today", legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig, use_container_width=True)
            irr = (rf[-1] / model.price_local) ** (1 / yrs) - 1 if model.price_local and rf[-1] > 0 else np.nan
            st.caption(f"If the path plays out: {rf[-1]:,.0f} after {yrs}Y → IRR {pct(irr)} p.a. Bear = growth above Tg halved, margin −2pp; Bull = ×1.5, +2pp.")
        with c2:
            st.subheader("Fair value: WACC × growth scale")
            w_rng = np.round(np.arange(max(0.03, wacc - 0.02), wacc + 0.021, 0.01), 4); s_rng = [0.25, 0.5, 0.75, 1.0, 1.25]
            rows = {}
            for w in w_rng:
                model.config.wacc = w
                rows[f"{w:.1%}"] = [model.value_path(path, growth_scale=s, **fade_kw)["price"] for s in s_rng]
            model.config.wacc = wacc
            sens = pd.DataFrame(rows, index=[f"{s:.0%} of growth" for s in s_rng]).T
            try: st.dataframe(sens.style.format("{:,.0f}").background_gradient(cmap="RdYlGn", axis=None), use_container_width=True)
            except ImportError: st.dataframe(sens.style.format("{:,.0f}"), use_container_width=True)
            st.caption("Growth scale applies to growth above Tg in every path year. 100% = your path.")
        with st.expander("Cash-flow table"):
            st.dataframe(res["table"].style.format({"Revenue": "{:,.0f}", "Growth": "{:.1%}", "EBIT Margin": "{:.1%}", "EBIT": "{:,.0f}",
                                                    "EBITDA": "{:,.0f}", "FCFF": "{:,.0f}", "PV": "{:,.0f}"}), use_container_width=True)
    except Exception as e:
        st.error(f"Path error: {e}")


# ══ TAB 3: KONTEXT ════════════════════════════════════════════════════════════
with tab3:
    st.title(f"Kontext: {model.ticker}")
    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("Quality")
        q = model.quality()
        st.dataframe(pd.DataFrame({"Value": [pct(v) if abs(v) < 5 else f"{v:.1f}x" for v in q.values()]}, index=list(q.keys())), use_container_width=True)
        st.caption("ROIC uses the base tax rate on EBIT / (equity + debt − cash).")
    with c2:
        st.subheader("Historical multiples (year-end price)")
        hm = model.historical_multiples()
        st.dataframe(hm.style.format({"P/E": "{:.1f}x", "EV/EBITDA": "{:.1f}x", "P/Sales": "{:.1f}x", "EBIT margin": "{:.1%}"}, na_rep="–"), use_container_width=True)
        med = hm.median(numeric_only=True)
        st.caption("Median: " + " · ".join(f"{k} {v:.1f}x" for k, v in med.items() if k != "EBIT margin" and np.isfinite(v)))
    st.subheader("Peers (Bloomberg, current)")
    if model.peers:
        pdf_ = pd.DataFrame(model.peers).set_index("Ticker")
        st.dataframe(pdf_.style.format({"P/E": "{:.1f}x", "EV/EBITDA": "{:.1f}x", "P/Sales": "{:.1f}x", "FCF": "{:,.0f}", "Div Yld": "{:.1f}%",
                                        "ROIC": "{:.1f}%", "Gross Mrg": "{:.1f}%", "EBIT Mrg": "{:.1f}%"}, na_rep="–"), use_container_width=True)
    else:
        st.caption("No Peers sheet.")
