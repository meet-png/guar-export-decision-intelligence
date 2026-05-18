"""The one sentence a busy exporter reads first.

Synthesises WHEN + WHERE + ₹ into a single decisive recommended move,
its rupee figure, the one-line why, and the honest caveat. It invents
no new analysis — it *prioritises* the signals the tested engine already
produced into the single highest-leverage action.

Priority rule (transparent, not fitted):
* WHEN says LOCK_NOW  → the time-sensitive action wins: lock a forward
  to cap the demand-driven downside. WHERE is mentioned as the parallel
  structural play.
* WHEN says NO_TRIGGER → no urgent hedge; the standing structural
  problem leads: the US is saturated + tariffed, so begin diversifying
  to the higher-paying pivot — *if* there is a real positive ₹ gain.
* No urgent trigger AND no clean re-route gain → say so honestly:
  hold and monitor; do not manufacture an action.

The caveat is always carried: we do not forecast price; figures are on
a simulated profile until the exporter's real tonnage is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.features.guar_price import HS_PRIMARY
from src.model.hedge_signal import compute_hedge_signal
from src.product.exporter_roi import (
    DEFAULT_REROUTE_SHARE,
    USD_INR,
    ExporterProfile,
    compute_exporter_roi,
)

CAVEAT = (
    "We do NOT forecast the guar price (backtested ≈ a random walk). "
    "WHEN is a risk trigger; WHERE is structural. Figures are on a "
    "simulated profile until your real tonnage is loaded."
)
# Below this (₹ lakh/yr) a re-route gain is not worth leading with.
MIN_MATERIAL_UPLIFT_L = 1.0


@dataclass
class ExecMove:
    action: str  # LOCK_FORWARD_NOW | DIVERSIFY_FROM_US | HOLD_AND_MONITOR
    headline: str
    why: str
    caveat: str = CAVEAT


def _decide(
    *,
    trigger: str,
    rig_yoy_pct: float,
    downside_lakh: float,
    uplift_lakh: float,
    pivot_country: str,
    pivot_usd: float,
    us_usd: float,
    collapse_drawdown_pct: float,
    reroute_pct: float,
) -> ExecMove:
    """Pure decision logic — no I/O, both branches unit-testable."""
    if trigger == "LOCK_NOW":
        return ExecMove(
            action="LOCK_FORWARD_NOW",
            headline=(
                f"Lock a forward contract now — US drilling is cooling "
                f"({rig_yoy_pct:+.0f}% YoY) and ≈ ₹{downside_lakh:,.0f} L/yr of "
                f"price downside on your volume is capped by acting."
            ),
            why=(
                f"A cooling demand regime has historically preceded guar "
                f"price drawdowns (collapse-regime worst-decile "
                f"-{collapse_drawdown_pct:.0f}% over 6 mo). Locking removes "
                f"that exposure. In parallel, shifting {reroute_pct:.0f}% of "
                f"US volume to {pivot_country} adds ≈ ₹{uplift_lakh:,.0f} L/yr."
            ),
        )
    if uplift_lakh >= MIN_MATERIAL_UPLIFT_L:
        return ExecMove(
            action="DIVERSIFY_FROM_US",
            headline=(
                f"Start shifting volume off the US to {pivot_country} — "
                f"≈ ₹{uplift_lakh:,.0f} L/yr more, and it cuts a structural risk."
            ),
            why=(
                f"No urgent hedge trigger (US drilling {rig_yoy_pct:+.0f}% "
                f"YoY). But the US is saturated and tariffed; {pivot_country} "
                f"realises ${pivot_usd:.2f}/kg vs the US ${us_usd:.2f}. "
                f"≈ ₹{downside_lakh:,.0f} L/yr of bad-quarter downside is still "
                f"carried unhedged — keep watching the WHEN trigger."
            ),
        )
    return ExecMove(
        action="HOLD_AND_MONITOR",
        headline=(
            "No urgent move — hold, and watch the WHEN trigger. No cooling-"
            "demand signal now, and no clean re-route gain at current prices."
        ),
        why=(
            f"US drilling {rig_yoy_pct:+.0f}% YoY (no elevated downside) and "
            f"the best alternative market does not beat your US realisation "
            f"by a material margin. Manufacturing a move here would not be "
            f"honest. ≈ ₹{downside_lakh:,.0f} L/yr remains the unhedged "
            f"bad-quarter exposure to monitor."
        ),
    )


def _collapse_drawdown_pct(sig) -> float:
    for s in sig.scenarios or []:
        if "collapse" in s["regime"].lower():
            return float(s["hist_adverse_drawdown_pct"])
    return float(sig.hist_bad_quarter_pct)


def move_from_signals(roi, sig, reroute_share: float) -> ExecMove:
    """Synthesise from ALREADY-computed roi + sig (no recompute) — what
    the dashboard uses so it never doubles the engine work."""
    return _decide(
        trigger=sig.trigger,
        rig_yoy_pct=sig.rig_yoy_change_pct,
        downside_lakh=roi.downside_inr_year / 1e5,
        uplift_lakh=roi.reroute_uplift_inr_year / 1e5,
        pivot_country=roi.pivot_country,
        pivot_usd=roi.pivot_realised_usd_per_kg,
        us_usd=roi.us_realised_usd_per_kg,
        collapse_drawdown_pct=_collapse_drawdown_pct(sig),
        reroute_pct=reroute_share * 100,
    )


def top_move(
    profile: ExporterProfile | None = None,
    hs: str = HS_PRIMARY,
    reroute_share: float = DEFAULT_REROUTE_SHARE,
    usd_inr: float = USD_INR,
) -> ExecMove:
    roi = compute_exporter_roi(
        profile, hs=hs, reroute_share=reroute_share, usd_inr=usd_inr
    )
    sig = compute_hedge_signal(hs=hs)
    return move_from_signals(roi, sig, reroute_share)
