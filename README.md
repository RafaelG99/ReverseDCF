# CORE DCF v4

Ein FCFF-Rechner, zwei Fragen, drei Tabs. Input bleibt `core_dcf_template.xlsx` (Fundamentals HC, Current, WACC, optional Peers, optional Guidance).

## Idee
1. **Basis (normalisiert)** ist ein expliziter Input in der Sidebar. Das Tool schlägt Revenue, EBIT-Marge, D&A, CapEx, SBC, NWC und Steuersatz aus der Historie vor und zeigt eine Ampel: 🟢 Marge stabil, 🟡 zyklisch (Trimmed Mean), 🔴 Marge kreuzt null (du musst sie selbst setzen). Ohne belastbare Basis gibt es keine belastbare Reverse DCF, egal wie viele Tabs.
2. **Reverse DCF**: welches FCF-Wachstum (Stage 2, n Jahre nach 1-3 Konsensjahren, danach 10 Jahre Fade auf Tg) rechtfertigt den Kurs auf dieser Basis? Plausibilität gegen 5Y/3Y-CAGR und Konsens, Sensitivität WACC × Tg.
3. **Mein Pfad**: Wachstum, Marge, CapEx, D&A, SBC, Tax pro Jahr editierbar. Default = marktimplizierter Pfad, d.h. Fair Value = Kurs, bis du etwas änderst. Optional Segmente (Guidance-Sheet oder manuell) mit Planerfüllungs-Hebel, die die Wachstumszeile vorbefüllen. Output: Fair Value, Bear/Bull (Wachstum über Tg ×0.5/×1.5, Marge ∓2pp), Roll-Forward mit IRR, Sensitivität WACC × Wachstumsskala.
4. **Kontext**: Quality-Kennzahlen, historische Multiples, Peers. Tabellen, keine Charts.

## Konventionen
- EV → Equity läuft durch genau eine Funktion (`ev_to_equity`): Net Debt, Minorities und die BBG-EV-Adjustierung (falls "Use BBG EV" an). Dadurch ist der Basis-Pfad per Konstruktion = Kurs.
- Fundamentals-Währung ≠ Kurswährung (z.B. Cosmo EUR/CHF): Feld **FX Rate** im Current-Sheet = Kurswährung → Fundamentals-Währung (CHF→EUR = 1/0.94 = 1.064). Alle Fair Values werden in Kurswährung angezeigt.
- Keine Steuergutschrift auf negative EBITs. NWC = % des Umsatz-Zuwachses (aus DSO + DSI).
- Konsens FY3 (BEST_SALES_3BF) optional als drittes Stage-1-Jahr.

## Guidance-Sheet (optional)
```
Base Year   | 2025
Target Year | 2030
Segment | Base | Target | Scalable | Ramp
Gastro  | 54.3 | 57.6   | 0        |
GI Genius | 16.6 | 168  | 1        |
New Products | 0 | 216  | 1        | 0,5,21,53,100
```
Ramp = % des Ziels je Planjahr (leer = geometrisch). Scalable = Planerfüllungs-Hebel wirkt.

## Dateien
`app.py` · `reverse_dcf_engine.py` · `guidance_dcf.py` · `requirements.txt` · `templates/core_dcf_template.xlsx`
