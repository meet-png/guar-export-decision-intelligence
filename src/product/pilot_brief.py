"""Auto-fill the one-page pilot brief with a specific exporter's live
numbers. Markdown out (no PDF dependency — print to PDF from any
viewer); the static template lives at docs/pilot/PILOT_BRIEF.md, this
is its data-bound twin for an actual pilot conversation.

Every figure comes from the tested engine (no re-analysis), and the
honest caveat + the "we don't forecast price" line are non-removable.

CLI
---
    python -m src.product.pilot_brief                       # default profile → stdout
    python -m src.product.pilot_brief --tonnes 900 --us-share 0.6 --out brief.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.model.hedge_signal import compute_hedge_signal
from src.product.exec_summary import move_from_signals
from src.product.exporter_roi import (
    DEFAULT_REROUTE_SHARE,
    USD_INR,
    ExporterProfile,
    compute_exporter_roi,
)


def render_brief(
    profile: ExporterProfile | None = None,
    reroute_share: float = DEFAULT_REROUTE_SHARE,
    usd_inr: float = USD_INR,
) -> str:
    profile = profile or ExporterProfile()
    roi = compute_exporter_roi(profile, reroute_share=reroute_share, usd_inr=usd_inr)
    sig = compute_hedge_signal()
    move = move_from_signals(roi, sig, reroute_share)

    def lakh(x: float) -> str:
        return f"₹{x / 1e5:,.1f} L/yr"

    scen = (
        "\n".join(
            f"| {s['regime']} | {s['n_windows']} | -{s['hist_adverse_drawdown_pct']:.1f}% "
            f"| {s['hist_median_move_pct']:+.1f}% |"
            for s in sig.scenarios
        )
        or "| (insufficient history) | | | |"
    )

    return f"""# Guar Export Decision Intelligence — Pilot Brief

*Auto-generated for: **{profile.name}** · {profile.annual_tonnes:,.0f} t/yr ·
US share {profile.us_share() * 100:.0f}% · FX ₹{usd_inr:.0f}/USD. These are
illustrative on this profile — in the pilot they are replaced with yours.*

---

## ➤ Your #1 move

**{move.headline}**

{move.why}

> {move.caveat}

## The two decisions, in your rupees

| | What it says | Worth on your volume |
|---|---|---|
| **WHEN** | Trigger **{sig.trigger}** — US drilling {sig.rig_yoy_change_pct:+.0f}% YoY. {sig.reason} | **{lakh(roi.downside_inr_year)}** of price downside at risk, capped by acting |
| **WHERE** | US is **{roi.us_realised_usd_per_kg:.2f}/kg** & saturated; **{roi.pivot_country}** pays **${roi.pivot_realised_usd_per_kg:.2f}/kg** | Re-route {reroute_share * 100:.0f}% of US volume ≈ **{lakh(roi.reroute_uplift_inr_year)}** more |

## If US drilling shifts (historical analogue, NOT a forecast)

| Drilling regime | n windows | Worst-decile 6-mo move | Median |
|---|---|---|---|
{scen}

## What this deliberately does not do

It does **not** forecast the guar price — backtested honestly, the model
does not beat a random walk, so we sell a *risk trigger*, not a crystal
ball. That refusal is the product's spine.

## The pilot ask

20 minutes. Your real annual tonnage and rough destination mix → we
recompute every figure above to **your** business, live. No data leaves
the room.
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--tonnes", type=float, default=None)
    p.add_argument("--us-share", type=float, default=None)
    p.add_argument("--reroute-share", type=float, default=DEFAULT_REROUTE_SHARE)
    p.add_argument("--out", type=Path, default=None)
    return p.parse_args()


def main() -> None:
    a = _parse_args()
    prof = ExporterProfile()
    if a.tonnes:
        prof.annual_tonnes = a.tonnes
    if a.us_share is not None:
        prof.destination_mix["USA"] = a.us_share
    md = render_brief(prof, reroute_share=a.reroute_share)
    if a.out:
        a.out.write_text(md, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
