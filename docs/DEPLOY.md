# Deploy — GitHub + Streamlit Community Cloud

Verified deploy-ready: a fresh clone with only the slim
`requirements.txt` (streamlit · pandas · numpy · plotly) renders the
screen with no exception, no DB, no API key. `pyarrow` is pulled by
Streamlit itself; no extra requirement is needed. `statsmodels` is
deliberately **off** the deploy path (subprocess-tested).

## 1 · Push to GitHub

The repo is fully committed locally (clean tree). It has **no remote
yet** — by design. Create one, then push `main`.

Target: **public** repo named **`guar-export-decision-intelligence`**.

**With the GitHub CLI (if installed):**
```bash
gh repo create guar-export-decision-intelligence --public --source=. --remote=origin --push
```

**Without it (gh is not installed here):** create an **empty** public
repo named `guar-export-decision-intelligence` on github.com — no
README/license/.gitignore (the repo must be empty or the first push is
rejected). The `origin` remote is already wired locally, so then just:
```bash
git push -u origin main
```
(If your GitHub username is not `meet-png`, fix the remote first:
`git remote set-url origin https://github.com/<you>/guar-export-decision-intelligence.git`)

> Tip: from this Claude Code session you can run either as `! <command>`
> so the output lands here. Do **not** add `.env` — it is gitignored and
> must stay so (gitleaks would also block it).

## 2 · Deploy on Streamlit Community Cloud

1. <https://share.streamlit.io> → **New app** → pick the repo.
2. Branch `main` · **Main file path = `streamlit_app.py`**.
3. *Advanced settings* → Python **3.11**.
4. **No secrets.** The app reads only committed CSVs
   (`guar_price_monthly.csv`, `market_radar.csv`, `rig_count_clean.csv`)
   — it behaves identically locally and on Cloud.
5. Deploy. First load is ~30 s (cold start), then instant.

## 3 · Post-deploy smoke (30 seconds)

- The honest banner renders at the top: *"Price direction: insufficient
  edge — hedge trigger shown instead"* with the backtest line.
- The three ₹ metrics populate; the sidebar sliders recompute them.
- The price chart shows the red ✕ on the repaired Oct-2021 month.
- Put the live URL in the README badge and the pilot brief.

## Updating a deployed app

`git push` to `main` → Streamlit Cloud auto-redeploys. If the spine or
radar logic changes, regenerate and commit the CSVs first:

```bash
python -m src.features.guar_price
python -m src.features.market_radar
git add data/processed/guar_price_monthly.csv data/processed/market_radar.csv
git commit -m "data: refresh spine + radar"
git push
```
