# CORE DCF Engine — Setup Notes (final)

## Streamlit Cloud Deployment

### 1. Repository structure
```
your-repo/
├── app.py
├── reverse_dcf_engine.py
├── ai_layer.py
├── requirements.txt
├── valterna_logo.png              # ← Logo file (next to app.py)
└── .streamlit/
    └── secrets.toml               # NEVER commit this
```

The PDF builder auto-discovers the logo. It searches in this order:
1. `valterna_logo.png` (next to `app.py`)
2. `logo.png`
3. `assets/valterna_logo.png`

If none of these exist, the PDF still renders — just without the logo (Valterna brand strip falls back to text-only). The app does not crash.

### 2. Secrets Setup

In Streamlit Cloud:
1. App settings → Secrets
2. Paste:

```toml
ANTHROPIC_API_KEY = "sk-ant-api03-..."
```

For local dev: create `.streamlit/secrets.toml` with the same content.

### 3. AI Layer Toggles (Sidebar)

Three toggles after upload:
- **Smart WACC/Tg Defaults** — Bottom-up plausibilization. Shows AI suggestion next to WACC slider.
- **Smart Forward DCF Pre-Fill** — Sector-aware Y1-Y10 trajectory.
- **AI Investment Commentary in PDF** — 1-page Executive Summary as Page 1.

All toggles disable gracefully if no API key is found. The app remains fully functional without AI — just without the smart augmentation.

### 4. Cost per Run (Opus 4.7)

With all 3 layers active: **~$0.10/run**, **~$0.06/run** with prompt caching after the first run.

At 30 runs/month: **$1.80–4.20/month**.

### 5. Caching

Each AI call cached in `st.session_state` per ticker + parameters. Re-running same ticker doesn't re-call the API. Changing WACC/Tg invalidates Forward DCF cache and Commentary cache, but reuses the WACC/Tg suggestion.

### 6. Optional Excel Fields (Current sheet)

Add these to your Excel template's `Current` sheet for tighter analysis:
- `Clean Margin` — manual override for Mid-Cycle Margin (decimal or percentage)
- `Major MA Year` — year of major M&A (skips Asset Growth check in C-Score)

Leave blank to use engine defaults.

### 7. Output: PDF Structure

The generated PDF has:
- **Page 1**: Valterna Investment Committee Memo (only if AI Commentary toggle is ON)
  - Headline + Investment Thesis
  - Bull / Base / Bear cases (color-coded)
  - Verdict box (LONG/ACCUMULATE/HOLD/TRIM/AVOID with entry level)
  - Catalysts & Risks bullets
- **Page 2**: Reverse DCF Cover (verdict, KPI strip, scenario fan, TV decomposition)
- **Page 3**: Plausibility checks, Model Inputs, Sensitivity grid
- **Page 4–5**: Quality Grade, C-Score, Historical Multiples
- **Page 5–6**: Return Decomposition (waterfall + components)
- **Page 6–7**: Peer Comparison
- **Page 7–8**: Forward DCF (My View vs Market, Cash Flows, Valuation Bridge, Implied Multiples)

Every page has Valterna logo top-left, ticker + date top-right, gold separator, "Valterna AG · CORE DCF Engine · Confidential" + Page Number footer.

### 8. Quick deploy checklist

- [ ] `valterna_logo.png` next to `app.py`
- [ ] `requirements.txt` includes `anthropic>=0.40`, `matplotlib>=3.7`, `kaleido==0.2.1`
- [ ] `ANTHROPIC_API_KEY` in Streamlit Cloud Secrets
- [ ] Streamlit Cloud reboot triggered after push
- [ ] Test: upload ABB Excel → 3 toggles visible & enabled → click Generate PDF → 8-page report with Valterna branding
