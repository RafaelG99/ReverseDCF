# CORE DCF Engine — Setup Notes

## Streamlit Cloud Deployment

### 1. Repository structure
```
your-repo/
├── app.py
├── reverse_dcf_engine.py
├── ai_layer.py
├── requirements.txt
├── fonts/                         # OPTIONAL: Century Gothic for full Valterna branding
│   ├── CenturyGothic.ttf
│   └── CenturyGothicBold.ttf
└── .streamlit/
    └── secrets.toml               # NEVER commit this file
```

### 2. Secrets Setup

In Streamlit Cloud:
1. Go to your app settings: https://share.streamlit.io
2. Click "Settings" → "Secrets"
3. Add this content:

```toml
ANTHROPIC_API_KEY = "sk-ant-api03-..."
```

For local development: create `.streamlit/secrets.toml` in your project root with the same content.

### 3. Century Gothic (optional, for branding)

If you want Century Gothic fonts (Valterna corporate font), upload TTF files to a `fonts/` directory in your repo. The PDF generator auto-detects them. Without them, it falls back to Helvetica (clean fallback, still uses Valterna colors).

You can extract Century Gothic from a Windows machine at `C:/Windows/Fonts/GOTHIC.TTF` and `GOTHICB.TTF`. Note: Microsoft licensing applies — only use on private/internal projects.

### 4. AI Layer Toggles (Sidebar)

After upload, three toggles appear in the sidebar:
- **Smart WACC/Tg Defaults**: Bottom-up plausibilization. Shows AI suggestion next to the WACC slider.
- **Smart Forward DCF Pre-Fill**: Sector-aware Y1-Y10 trajectory in Forward DCF tab.
- **AI Investment Commentary in PDF**: Adds a 1-page Executive Summary as the new Page 1 of the PDF.

All toggles are **disabled gracefully** if no API key is found.

### 5. Cost per Run (Opus 4.7)

Approximate cost when all 3 layers are active:
- **~$0.10/run** (with Opus 4.7's tokenizer overhead)
- **~$0.06/run** with prompt caching after the first run

At 30 runs/month: **$1.80–4.20/month**.

### 6. Caching Strategy

Each AI call is cached in `st.session_state` per ticker + parameters. Re-running the same ticker with the same WACC/Tg does **not** re-call the API. Changing WACC/Tg invalidates the Forward DCF and Commentary caches but reuses the WACC/Tg suggestion (since that's input-independent for same ticker).

### 7. Optional Excel Fields

In `Current` sheet, you can add these new optional fields:
- `Clean Margin` — Manual override for Mid-Cycle Margin (decimal or percentage)
- `Major MA Year` — Year of major M&A (skips Asset Growth check in C-Score)

Leave them blank to use engine defaults.
