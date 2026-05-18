# Screenshots / GIF to capture for the README

The README references the files below. It reads fine without them
(graceful text), but the visuals make it top-notch. Capture from the
**live app**, drop the files in this folder with these exact names, and
`git add` them — the README picks them up automatically.

> Capture from: **https://ca4enua2zdbbnteqxastgb.streamlit.app/**
> First set the app to **Public** (Streamlit Cloud → app → Settings →
> Sharing) so the screens reflect what an exporter actually sees.

## Required (1 file — biggest impact)

**`app-hero.png`** — one full-width screenshot of the *top* of the app:
the title, the green **"✅ Do this first"** box, and the three cards
(Earn more / Lose less / Be careful) visible together.
- Browser zoom ~90–100%, window ~1400 px wide, light theme.
- Crop to content (no OS chrome). Keep under ~1 MB (PNG, ~1400 px wide).

## Recommended (1 file — shows it's interactive)

**`app.gif`** — a 10–15 s screen recording: load the page, scroll slowly
top → bottom (hero → 3 cards → "countries that pay most" chart → price
chart), then drag one sidebar slider so the rupee numbers visibly
recompute.
- Export as GIF, ~1000–1200 px wide, **under 8 MB** (GitHub inlines it;
  smaller = faster). Tools: ScreenToGif / LICEcap / Kap.

## Optional (nice, not needed)

- **`where-chart.png`** — just the "Which countries pay the most" bar.
- **`price-chart.png`** — the price-over-years chart with the red ✕
  corrected month.

## Notes

- Do **not** commit raw recordings — `docs/img/*.mp4` is gitignored on
  purpose; commit only the exported `.gif` / `.png`.
- The old `dashboard.gif` was v1's furniture app and was removed — v2
  must show v2's screen, nothing from v1.
