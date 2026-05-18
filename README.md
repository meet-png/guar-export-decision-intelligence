# Guar Export Decision Intelligence

> **A decision tool for an Indian guar-gum exporter. It answers two
> money questions — *when* to lock a forward contract and *where* to
> sell — on free public data, in rupees, on your own tonnage. And it
> openly tells you the one thing it cannot do.**

> ⚙️ Product fork of **JEIS v1** — a portfolio analytics project, kept
> frozen, separate, and untouched as the original artifact. This repo is
> v1 re-moulded into a focused, paid-pilot product. v1's furniture-
> cluster dashboard and this are entirely different apps.

---

## The product in one breath

A guar exporter makes two recurring money decisions on gut feel: *when*
to sell or lock a contract, and *where* to ship. This turns six years of
public trade data into both — and quantifies each in **₹ on the
exporter's own volume**.

**Sample exporter (600 t/yr, 45% to the US — simulated for the demo):**

| Decision | What it says | Worth on this volume |
|---|---|---|
| **WHEN** | US drilling is guar's swing demand; when it cools, price downside is elevated → lock a forward to cap it | **≈ ₹87.7 L/yr** of downside at risk, capped by acting |
| **WHERE** | You're **34.6%** exposed to the now-tariffed US; Japan pays **$2.71/kg** vs the US **$1.66** on the same HS | Re-routing 20% of US volume ≈ **₹47 L/yr** more |

## What it deliberately does **not** do — the point, not a caveat

It does **not** forecast the guar price. We built the SARIMAX model and
backtested it honestly with no look-ahead: it does **not** beat a random
walk (MAPE 3.6% vs 3.5% at 1 month; direction hit-rate 58% → 32% by 3
months). So the product **says so on its own front screen** and sells a
*risk trigger*, not a crystal ball. A tool that oversells a forecast
loses its user money and its builder credibility. That refusal is the
product's spine.

## How it works

```
UN Comtrade (HS 130232, India exports, 2019–2024)
        │   robust qty-weighted price; corrupt months (e.g. Oct-2021
        ▼   printed $7/kg) flagged + repaired in the open, raw kept
  data spine  ──────────────────────────────────────────────────────┐
        │                                                            │
        ├─▶ WHEN  src/model/hedge_signal.py                           │
        │     honest "no price forecast" label + backtest evidence,   │
        │     then a rig-count (Baker Hughes, weekly) risk trigger;   │
        │     thresholds are economic heuristics, NOT fitted          │
        │                                                            │
        ├─▶ WHERE src/features/market_radar.py                        │
        │     per-destination realised $/kg, US concentration,        │
        │     transparent pivot score, SURGING/FADING monitor         │
        │                                                            │
        └─▶ ₹    src/product/exporter_roi.py                          │
              both pillars × a (simulated) exporter profile × an      │
              explicit, single-sourced FX → rupees/year               │
                                    │                                 │
                                    ▼                                 │
                       streamlit_app.py  — one decision screen ◀──────┘
```

Ingest / clean / validate plumbing is inherited from JEIS v1. The
product logic (`src/features/guar_price.py`, `src/features/market_radar.py`,
`src/model/`, `src/product/`) and the one-screen app are new and
test-covered. **33 tests** assert the things credibility depends on:
no look-ahead, model-vs-naive honesty, corrupt-month repair, the
rupee math, and that the screen renders.

## Run it

```bash
# Quick demo — slim deps only (the app reads committed CSVs)
pip install -r requirements.txt          # streamlit, pandas, numpy, plotly
streamlit run streamlit_app.py           # → http://localhost:8501

# Full env (rebuild the spine/radar, run the model + tests)
pip install -r requirements-pipeline.txt
python -m src.features.guar_price        # rebuild the price spine CSV
python -m src.features.market_radar      # rebuild the market radar CSV
python -m src.model.price_forecast       # the honest backtest
python -m src.product.exporter_roi       # the ₹ headline
pytest -q                                # 33 tests
```

The deployed app path is intentionally statsmodels-free (verified by a
subprocess test) so the slim Streamlit Cloud deploy stays fast. *(Not
yet deployed — by choice; no public remote until intended.)*

## Honest limitations (carried, not hidden)

- **The exporter profile is simulated.** Public core + a clearly
  labelled synthetic private layer; the *mechanics* are real, the books
  are a stand-in until a pilot supplies real numbers.
- **Realised price is a proxy** — UN Comtrade export value ÷ quantity,
  lagged 6–18 months, mixed grade. Robust (qty-weighted, outliers
  dropped); corrupt months repaired transparently with an audit column.
- **WHERE is India-reporter only.** It measures India's business and
  realised price per market; each market's *total* world imports
  (headroom) is a flagged roadmap item needing a Comtrade mirror pull —
  not faked.
- **No price forecast** (shown above). WHEN is a risk trigger; its rig
  signal is year-over-year because the ingested rig source is annual-
  broadcast (true weekly Baker Hughes is a documented drop-in upgrade).
- **FX is explicit** (₹83/USD, RBI FY2024, env-overridable); every
  rupee figure scales linearly and ships with a sensitivity table.

Decision-framing on public data — not investment advice.

## Pilot kit

`docs/pilot/` — the one-page [brief](docs/pilot/PILOT_BRIEF.md), the
20-minute [conversation script](docs/pilot/CONVERSATION_SCRIPT.md) (with
an objection table), and the [pricing](docs/pilot/PRICING.md) one-pager.

## Tech

Python 3.11 · pandas/numpy · statsmodels SARIMAX (pipeline only) ·
Streamlit + Plotly (slim deploy) · pytest · ruff. Data: UN Comtrade
Plus, Baker Hughes NA rig count, IMD Rajasthan monsoon — all free,
public, reproducible. No company data used.

## Provenance & license

Derived from **JEIS v1** (Jodhpur Export Intelligence System), a solo
portfolio project kept frozen as its own artifact. MIT — see
[LICENSE](LICENSE). Built by [Meet Kabra](https://github.com/meet-png)
with AI pair-programming; every analytical and architectural decision is
intended to be defensible under scrutiny.
