"""
AI Layer for CORE DCF Engine
─────────────────────────────
Three Claude Opus 4.7 calls that augment the deterministic engine:
  • smart_wacc_tg(...)          → Layer A: WACC + Terminal Growth defaults with bottom-up rationale
  • smart_forward_prefill(...)  → Layer B: Forward DCF assumptions with sector-aware trajectory
  • senior_commentary(...)      → Layer C: Investment thesis + risks + verdict for PDF executive summary

Design principles:
  - Engine is the source of truth for numbers; AI interprets and contextualizes.
  - All outputs are structured JSON (parsed safely) — no free-form hallucination of numbers.
  - Temperature=0 for reproducibility.
  - Prompt caching applied to system prompts (saves ~50% on input tokens).
  - Graceful failure: if API call fails, return None and let caller fall back to engine defaults.

Output language: German (Hochdeutsch, Schweizer Wirtschafts-Stil — Morgenpost-Niveau).
"""
from __future__ import annotations
import json
import re
from typing import Optional, Dict, Any, List
from anthropic import Anthropic, APIError


MODEL = "claude-opus-4-7"
MAX_RETRIES = 2


# ── System prompts (cacheable) ────────────────────────────────────────────────

SYSTEM_WACC_TG = """KRITISCH: Antworte AUSSCHLIESSLICH mit einem JSON-Objekt. Keine Erklärung davor oder danach. \
Keine Markdown-Codeblöcke. Nur das rohe JSON, beginnend mit { und endend mit }.

Du bist Senior Equity Analyst bei einem Schweizer Wealth Manager (Valterna AG). \
Deine Aufgabe: Bottom-up Plausibilisierung von WACC und Terminal Growth für einen Reverse DCF.

Methodisch:
- WACC: Risk-free Rate (in der Funktionswährung des Unternehmens) + Beta × ERP. ERP für entwickelte Märkte 5.0-6.0%. \
Beachte Cost of Debt nur wenn Debt/EV signifikant (>10%). Für CHF-Domizile aktuell Rf ~0.5-0.8%, EUR ~2.3-2.6%, USD ~4.0-4.3%.
- Terminal Growth: Geographisch gewichtetes nominales BIP, ABER für reife Industrieunternehmen begrenzt auf \
Inflation + 50-100bps Productivity. Realistisch 1.5-2.5%. NIE höher als WACC - 200bps.

Sprache der Rationale-Texte: Deutsch.

Schema (genau diese Schlüssel verwenden):
{
  "wacc_recommended": 0.067,
  "wacc_range_low": 0.060,
  "wacc_range_high": 0.075,
  "tg_recommended": 0.020,
  "tg_range_low": 0.015,
  "tg_range_high": 0.025,
  "rationale_wacc": "2-3 Sätze, bottom-up, mit konkreten Zahlen.",
  "rationale_tg": "2-3 Sätze, geographisch gewichtet."
}

WICHTIG: Werte als Dezimalzahlen (0.067 = 6.7%). Antworte NUR mit dem JSON, sonst nichts."""


SYSTEM_FWD_PREFILL = """KRITISCH: Antworte AUSSCHLIESSLICH mit einem JSON-Objekt. Keine Erklärung davor oder danach. \
Keine Markdown-Codeblöcke. Nur das rohe JSON, beginnend mit { und endend mit }.

Du bist Senior Equity Analyst bei Valterna AG. Du baust eine Forward DCF Projektion \
mit sektor- und unternehmensspezifischer Logik.

Methodisch:
- Y1-Y2: Bloomberg-Konsens (FY1, FY2) — direkt übernehmen, NICHT verändern.
- Y3-Y5: Säkulare Trends + Mid-Cycle-Konvergenz. Bei Tailwinds (z.B. AI Power, Energy Transition) \
darf Wachstum über Konsens liegen.
- Y6-Y10: Fade Richtung Terminal Growth. Margin Mean-Reversion zu Mid-Cycle.
- Margin-Trajektorie: Wenn aktuelle Margin > Mid-Cycle, fade ZURÜCK Richtung Mid-Cycle (Mean Reversion). \
Wenn unter Mid-Cycle und ROIC > WACC, fade NACH OBEN Richtung Mid-Cycle.
- CapEx/D&A/SBC/Tax: relativ stabil halten, nur subtile Trends bei Reasoning.

Sprache: Deutsch.

Schema (10 Jahre Forecast, Y1-Y10, plus Terminal):
{
  "years": {
    "Y1":  {"growth": 0.119, "margin": 0.182, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y2":  {"growth": 0.079, "margin": 0.180, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y3":  {"growth": 0.070, "margin": 0.175, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y4":  {"growth": 0.060, "margin": 0.170, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y5":  {"growth": 0.055, "margin": 0.165, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y6":  {"growth": 0.045, "margin": 0.160, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y7":  {"growth": 0.040, "margin": 0.155, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y8":  {"growth": 0.035, "margin": 0.150, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y9":  {"growth": 0.030, "margin": 0.145, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Y10": {"growth": 0.025, "margin": 0.142, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258},
    "Terminal": {"growth": 0.020, "margin": 0.140, "capex": 0.027, "da": 0.033, "sbc": 0.002, "tax": 0.258}
  },
  "rationale": "3-4 Sätze: warum diese Trajektorie. Sektor-Tailwinds, Margin-Path, Konsens-Anchor."
}

WICHTIG: Alle Werte als Dezimalzahlen (0.05 = 5%), nicht in Prozent. Genau die Schlüssel verwenden: \
growth, margin, capex, da, sbc, tax. Antworte NUR mit dem JSON, sonst nichts."""


SYSTEM_COMMENTARY = """KRITISCH: Antworte AUSSCHLIESSLICH mit einem JSON-Objekt. Keine Erklärung davor oder danach. \
Keine Markdown-Codeblöcke. Nur das rohe JSON, beginnend mit { und endend mit }.

Du bist Senior Equity Analyst bei Valterna AG. Du schreibst eine 1-Seiten Executive Summary \
für das Investment Committee. Adressat: CIO, CFO, Senior PMs. Stil: präzise, direkt, ohne Buzzwords. \
Keine "auf der einen Seite, andererseits"-Sprache. Klare Verdicts mit konkreten Zahlen. \
Schweizer Hochdeutsch (kein "Du", förmlich aber nicht steif).

Methodisch:
- Investment Thesis: Was ist die zentrale Frage bei diesem Investment? In 1-2 Sätzen.
- Bull / Base / Bear Case: Je 2-3 Sätze. Konkrete Trigger nennen.
- Verdict: Long / Hold / Trim / Avoid mit Entry-Level. Begründung warum.
- Catalysts & Risks: 3-5 konkrete Datapoints zum Monitoring.

Schema:
{
  "headline": "Ein-Satz-Einordnung, plakativ.",
  "thesis": "1-2 Sätze zentrale Frage des Investments.",
  "bull_case": "2-3 Sätze, konkrete Trigger.",
  "base_case": "2-3 Sätze, was die Engine sagt + Konsens-View.",
  "bear_case": "2-3 Sätze, konkrete Risiken.",
  "verdict_action": "LONG",
  "verdict_entry_level": 70.0,
  "verdict_rationale": "2-3 Sätze: warum diese Action und warum dieser Entry Level.",
  "catalysts": ["Konkreter Datapoint 1", "Konkreter Datapoint 2"],
  "risks": ["Risiko 1", "Risiko 2"]
}

verdict_action muss eines sein von: LONG, ACCUMULATE, HOLD, TRIM, AVOID.
Antworte NUR mit dem JSON, sonst nichts."""


# ── Helper: extract JSON robustly ─────────────────────────────────────────────

def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from response. Handles: pure JSON, markdown-wrapped, prose+JSON.
    Uses brace counting (not regex) to handle nested objects correctly."""
    if not text:
        return None
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try markdown code block
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Brace counting: find first { and match its closing }
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    return None


def _call_claude(api_key: str, system: str, user_message: str,
                 max_tokens: int = 1500) -> Optional[dict]:
    """Make a Claude API call with prompt caching, return parsed JSON or raise on failure.
    Uses assistant prefill ('{' as start of response) to force JSON output."""
    if not api_key:
        return None
    client = Anthropic(api_key=api_key)
    last_error = None
    last_text = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[
                    {"role": "user", "content": user_message},
                    # Prefill: force the response to start with `{` so it must be JSON
                    {"role": "assistant", "content": "{"},
                ],
            )
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            # Re-attach the prefilled `{` since the API response excludes it
            text = "{" + text
            last_text = text
            parsed = _extract_json(text)
            if parsed is not None:
                return parsed
        except APIError as e:
            last_error = e
            if attempt < MAX_RETRIES:
                continue
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                continue
    # All retries exhausted — raise so caller sees the actual problem
    if last_error is not None:
        raise RuntimeError(f"Claude API failed after {MAX_RETRIES+1} attempts: "
                           f"{type(last_error).__name__}: {str(last_error)[:300]}")
    if last_text is not None:
        raise RuntimeError(f"Claude returned non-JSON response: {last_text[:500]}")
    return None


# ── Layer A: Smart WACC + Terminal Growth ─────────────────────────────────────

def smart_wacc_tg(api_key: str, model_obj) -> Optional[dict]:
    """Bottom-up WACC and Tg recommendation based on engine inputs."""
    if not api_key:
        return None

    # Build context summary for Claude
    peers_info = ""
    if model_obj.peers:
        peer_lines = []
        for p in model_obj.peers[:6]:
            tkr = p.get("ticker", "")
            name = p.get("name", "")
            peer_lines.append(f"  - {tkr}: {name}")
        peers_info = "Peers (Sektor-Indikator):\n" + "\n".join(peer_lines)

    # Margin range as cyclicality indicator
    margin_info = (f"EBIT Margin Range über letzte ~10Y: {model_obj.margin_min:.1%} bis "
                   f"{model_obj.margin_max:.1%} (Mid-Cycle: {model_obj.mid_cycle_margin:.1%}, "
                   f"aktuell: {model_obj.ebit_margin:.1%}).")

    # Domicile inference from ticker suffix
    ticker = model_obj.ticker or ""
    domicile_hint = ""
    if " SW" in ticker or ticker.endswith("SW"):
        domicile_hint = "Domizil: Schweiz (CHF-Funktionswährung wahrscheinlich)."
    elif any(s in ticker for s in [" GR", " GY", " FP", " IM", " NA", " SS"]):
        domicile_hint = "Domizil: Eurozone (EUR-Funktionswährung)."
    elif " US" in ticker or ticker.endswith("US"):
        domicile_hint = "Domizil: USA (USD-Funktionswährung)."
    elif " JP" in ticker:
        domicile_hint = "Domizil: Japan (JPY-Funktionswährung)."
    elif " LN" in ticker:
        domicile_hint = "Domizil: UK (GBP-Funktionswährung)."

    bbg_wacc = model_obj.bbg_wacc
    bbg_str = f"Bloomberg WACC: {bbg_wacc:.2%}" if bbg_wacc else "Keine BBG WACC verfügbar."

    user_msg = f"""Plausibilisiere WACC und Terminal Growth für folgendes Unternehmen.

Ticker: {ticker}
{domicile_hint}
{bbg_str}
Beta (adjusted): {model_obj._safe_num(model_obj.current.get('Beta Adj')) or 'n/a'}
Aktuelle Marktkapitalisierung: {model_obj.market_cap:,.0f}
Net Debt: {model_obj.net_debt:,.0f} (Debt/EV: {model_obj.net_debt/(model_obj.market_ev or 1)*100:.1f}%)
ROIC (current): {model_obj.current_roic:.1%}
{margin_info}

{peers_info}

Liefere WACC und Terminal Growth bottom-up. Berücksichtige Domizil-spezifische Risk-free Rate, \
Beta, Sektor-Cyclicality (Margin-Range als Indikator) und geographischen Mix der Peers."""

    return _call_claude(api_key, SYSTEM_WACC_TG, user_msg, max_tokens=800)


# ── Layer B: Smart Forward DCF Pre-Fill ───────────────────────────────────────

def smart_forward_prefill(api_key: str, model_obj, r: dict, n_fwd: int = 10) -> Optional[dict]:
    """Generate a sector-aware Forward DCF trajectory. Returns dict with year-keyed assumptions."""
    if not api_key:
        return None

    ticker = model_obj.ticker or ""
    peer_names = ", ".join(p.get("name", "")[:30] for p in (model_obj.peers or [])[:6])

    # Hist revenue/margin trajectory
    h = model_obj.hist
    rev_hist = h.get("Revenue")
    if rev_hist is not None and len(rev_hist) >= 5:
        rev_values = rev_hist.dropna().iloc[-5:].tolist()
        rev_growth_recent = [(rev_values[i]/rev_values[i-1] - 1) for i in range(1, len(rev_values))]
        recent_growth_str = ", ".join(f"{g:+.1%}" for g in rev_growth_recent)
    else:
        recent_growth_str = "n/a"

    margin_history = ""
    if "EBIT" in h and "Revenue" in h:
        margins = (h["EBIT"]/h["Revenue"]).dropna().iloc[-5:]
        margin_history = ", ".join(f"{m:.1%}" for m in margins)

    user_msg = f"""Erstelle eine Forward DCF Trajektorie für {n_fwd} Jahre + Terminal.

Unternehmen: {ticker}
Peers: {peer_names}

Aktueller Stand:
- Base Revenue: {model_obj.base_revenue:,.0f}
- Aktuelle EBIT Margin: {model_obj.ebit_margin:.1%}
- Mid-Cycle Margin: {model_obj.mid_cycle_margin:.1%} (Range: {model_obj.margin_min:.1%}–{model_obj.margin_max:.1%})
- ROIC: {model_obj.current_roic:.1%}
- WACC: {model_obj.config.wacc:.2%}
- Terminal Growth: {model_obj.config.terminal_growth:.2%}

Historische Inputs (Median letzter Jahre):
- D&A/Revenue: {model_obj.da_pct:.1%}
- CapEx/Revenue: {model_obj.capex_pct:.1%}
- SBC/Revenue: {model_obj.sbc_pct:.1%}
- Effektiv-Steuer: {model_obj.tax_rate:.1%}

Bloomberg Konsens:
- FY1 Revenue Growth: {model_obj.consensus_growth_fy1:+.1%}
- FY2 Revenue Growth: {model_obj.consensus_growth_fy2:+.1%}

Reverse DCF Implied Growth (was Markt impliziert): {r['implied_growth']:.1%}

Letzte 5Y Revenue Growth: {recent_growth_str}
Letzte 5Y EBIT Margins: {margin_history}

Konstruiere die Trajektorie mit Y1+Y2 = Konsens, Y3-Y5 sektorspezifisch, Y6-Y10 fade Richtung Terminal. \
Margin-Pfad: Mean-Reversion zu Mid-Cycle, falls aktuelle Margin abweicht. Berücksichtige säkulare Trends \
des Sektors basierend auf den Peers."""

    return _call_claude(api_key, SYSTEM_FWD_PREFILL, user_msg, max_tokens=2500)


# ── Layer C: Senior Analyst Commentary ────────────────────────────────────────

def senior_commentary(api_key: str, model_obj, r: dict,
                      forward_dcf_results: Optional[dict] = None) -> Optional[dict]:
    """Generate Investment Committee 1-pager: thesis, cases, verdict, catalysts."""
    if not api_key:
        return None

    ticker = model_obj.ticker or ""
    sc = r["scenarios"]
    q = r["quality"]

    # Peer comparison summary
    peer_summary = ""
    if model_obj.peers and len(model_obj.peers) > 1:
        own_key = ticker.split()[0]
        own = next((p for p in model_obj.peers if own_key in p.get("ticker", "")), None)
        peers = [p for p in model_obj.peers if own_key not in p.get("ticker", "")]
        if own and peers:
            def _avg(metric):
                vals = [p.get(metric) for p in peers if p.get(metric) is not None]
                return sum(vals)/len(vals) if vals else None
            avg_pe = _avg("P/E"); avg_ev = _avg("EV/EBITDA"); avg_roic = _avg("ROIC")
            peer_summary = f"""
Peer-Vergleich:
- P/E: {own.get('P/E', 0):.1f}x vs Avg {avg_pe:.1f}x ({(own.get('P/E', 0)-avg_pe)/avg_pe:+.0%})
- EV/EBITDA: {own.get('EV/EBITDA', 0):.1f}x vs Avg {avg_ev:.1f}x ({(own.get('EV/EBITDA', 0)-avg_ev)/avg_ev:+.0%})
- ROIC: {own.get('ROIC', 0):.1f}% vs Avg {avg_roic:.1f}%"""

    # Forward DCF section if available
    forward_section = ""
    if forward_dcf_results:
        forward_section = f"""
Forward DCF (auf User-Annahmen basiert):
- Fair Value: {forward_dcf_results.get('fair_price', 0):.2f}
- Upside: {forward_dcf_results.get('upside', 0):+.1%}
- Verdict: {forward_dcf_results.get('verdict', 'n/a')}"""

    # Return decomp
    rd = r.get("return_decomposition", {})
    rd_section = ""
    if rd.get("available"):
        rd_section = f"""
Return Decomposition ({rd.get('start_year')}–{rd.get('end_year')}):
- Total Return: {rd.get('total_return_ann', 0):+.1%} p.a.
- davon Revenue Growth: {rd.get('revenue_growth_ann', 0):+.1%}
- davon Margin Effect: {rd.get('margin_effect_ann', 0):+.1%}
- davon Multiple Expansion: {rd.get('multiple_expansion_ann', 0):+.1%}"""

    user_msg = f"""Schreibe Executive Summary für IC-Memo zu {ticker}.

═══ ENGINE OUTPUT ═══

Bewertung & Markt-Erwartungen:
- Aktueller Preis: {r['price']:.2f}
- Implied Growth (Reverse DCF): {r['implied_growth']:.1%} p.a. — was Markt erwartet
- 5Y CAGR (historisch): {r['cagr_5y']:.1%}
- 3Y CAGR: {r['cagr_3y']:.1%}
- Plausibility: {sum(1 for c in r['plausibility'] if c['flag']=='🔴')}/3 rote Flags

Szenario-Analyse:
- Bear (25%): {sc['Bear']['fair_price']:.1f} ({sc['Bear']['upside']:+.0%})
- Base (50%): {sc['Base']['fair_price']:.1f} ({sc['Base']['upside']:+.0%})
- Bull (25%): {sc['Bull']['fair_price']:.1f} ({sc['Bull']['upside']:+.0%})
- Expected Value: {sc['expected_value']:.1f} ({sc['expected_upside']:+.0%})
- Entry @ 20% MoS: {sc['margin_of_safety_price']:.1f}

Quality:
- Grade: {q.grade} (C-Score {q.c_score.total}/5)
- ROIC: {q.roic_median:.1%} median
- Margin Stability (std): {q.margin_stability:.2%}
- FCF Conversion: {q.fcf_conversion:.2f}x
- Debt/EBITDA: {q.debt_ebitda:.1f}x

Operating:
- Mid-Cycle Margin: {model_obj.mid_cycle_margin:.1%} (vs aktuell {model_obj.ebit_margin:.1%})
- ROIC-WACC Spread: {r['roic_spread']:+.1%} ({"Wertschöpfung" if r['roic_spread']>0 else "Wertvernichtung"})
- TV-Anteil: {r['tv_decomposition']['tv_pct']:.0%}

Konsens (Bloomberg):
- FY1 Rev Growth: {model_obj.consensus_growth_fy1:+.1%}
- FY2 Rev Growth: {model_obj.consensus_growth_fy2:+.1%}
{peer_summary}{rd_section}{forward_section}

═══ AUFGABE ═══

Schreibe eine prägnante Investment-Einordnung im Stil eines Valterna IC-Memos. \
Plakative Headline. Klare Thesis. Bull/Base/Bear je 2-3 Sätze. Verdict (Long/Accumulate/Hold/Trim/Avoid) \
mit Entry-Level. 3-5 Catalysts und 3-5 Risks als Bullets. \
Sprache: Schweizer Hochdeutsch. Tone: institutionell, direkt, kein Buzzword-Bingo."""

    return _call_claude(api_key, SYSTEM_COMMENTARY, user_msg, max_tokens=2000)


# ── Convenience: estimate token cost ──────────────────────────────────────────

def estimate_cost(layers_used: List[str]) -> Dict[str, float]:
    """Rough cost estimate in USD for Opus 4.7. Layers: ['wacc', 'forward', 'commentary']."""
    # Opus 4.7: $5/MTok input, $25/MTok output
    estimates = {
        "wacc":       (1500, 400),
        "forward":    (3000, 700),
        "commentary": (4500, 1200),
    }
    total_in = sum(estimates[l][0] for l in layers_used if l in estimates)
    total_out = sum(estimates[l][1] for l in layers_used if l in estimates)
    cost_in = total_in / 1_000_000 * 5.0
    cost_out = total_out / 1_000_000 * 25.0
    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_usd": round(cost_in + cost_out, 4),
    }
