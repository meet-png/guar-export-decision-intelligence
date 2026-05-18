"""Pillar WHEN, repositioned — a risk/hedge trigger, not a price oracle.

Why this shape
--------------
The honest backtest (:mod:`src.model.price_forecast`) showed monthly guar
price is ≈ a random walk: the model does not beat "next month = this
month" and has no 3-month directional skill. Selling a point forecast
would be a lie that ends a pilot meeting. So WHEN does not predict price.
It says so, in one sentence, on the product surface:

    "Price direction: insufficient edge — hedge trigger shown instead"

and then delivers what *is* defensible: a **risk-management trigger**
driven by the one genuine leading indicator. US rig count (Baker Hughes,
published *weekly*) is the swing demand for guar — fracking. It moves
months ahead of the price and far ahead of Comtrade's reporting lag. A
sustained fall in drilling is a structural signal that demand-side
**downside risk** to the guar price is elevated — the moment an exporter
should consider locking a forward contract to cap that risk.

Not a forecast in disguise
--------------------------
The trigger thresholds are **economic heuristics, deliberately NOT fitted
to the price history** — fitting them would smuggle back the very
predictive claim the backtest refused. They are conservative, explainable
and tunable, and we say so. The downside magnitude is purely
*descriptive*: the worst-decile 3-month move guar price has actually made
historically — "a bad quarter has looked like this", not "this will
happen".

Known limitation (stated, not hidden)
-------------------------------------
The ingested rig source (``rig_count_clean.csv``) is an *annual* Baker
Hughes figure broadcast to weekly — so a within-year momentum signal is
identically zero and only steps each January. The trigger therefore uses
**year-over-year** rig change, which is the real resolution of the data
and a sound demand-*regime* signal. Swapping in the true weekly Baker
Hughes series (ingestion + schema already exist from v1) is a drop-in
upgrade that would enable a finer monthly trigger — roadmap, not pretend.

The rupee personalisation (exposure on the exporter's own tonnage) is a
thin wrapper added with the simulated-private layer; this module produces
the decision and the $/kg risk it rests on.

CLI
---
    python -m src.model.hedge_signal            # signal for the latest month
    python -m src.model.hedge_signal --as-of 2020-03
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.features.guar_price import (
    HS_PRIMARY,
    PROCESSED_DIR_DEFAULT,
    load_guar_price_series,
)
from src.features.regressors import rig_monthly

# The honest label that must appear wherever a WHEN price view would be
# expected. Wording locked by product decision (2026-05-18).
PRICE_DIRECTION_LABEL = (
    "Price direction: insufficient edge — hedge trigger shown instead"
)
# Headline of the committed backtest (commit 6f952c9). Stored, not
# recomputed on every call, so the signal path stays cheap; the demo can
# attach a fresh BacktestReport when it wants the live numbers.
BACKTEST_EVIDENCE = (
    "no-look-ahead backtest: model MAPE 3.6% vs random-walk 3.5% (h=1), "
    "direction-hit 58% (h=1) → 32% (h=3) — point forecast not sold"
)

# --- Economic heuristics. NOT fitted to price. Conservative & tunable. ---
# YoY (not 3-mo) because the rig source is annual-broadcast — see docstring.
RIG_YOY_DROP_THRESHOLD_PCT = -10.0  # YoY fall in NA drilling = demand cooling
PRICE_HIGH_PCTILE = 60.0  # current price in upper part of its 12-mo range
DOWNSIDE_TAIL_Q = 0.10  # worst-decile 3-mo move = the "bad quarter" analogue

# Rig-regime scenario lever. For each regime, we report what guar price
# ACTUALLY did over the next SCEN_HORIZON_M months in history when
# drilling was in that regime — a descriptive empirical analogue, NOT a
# forecast (the backtest forbids forecasts). Buckets by rig YoY %.
SCEN_HORIZON_M = 6
RIG_REGIMES = (
    ("Drilling collapse (YoY ≤ -25%)", -25.0),
    ("Drilling cooling (YoY -25% to -8%)", -8.0),
    ("Drilling steady/up (YoY > -8%)", float("inf")),
)
SCEN_MIN_OBS = 4  # don't report a regime backed by < 4 historical windows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hedge_signal")


@dataclass
class HedgeSignal:
    as_of: pd.Timestamp
    current_price_usd_per_kg: float
    price_pctile_12m: float
    rig_level: float
    rig_yoy_change_pct: float
    realized_vol_6m_pct: float
    hist_bad_quarter_pct: float
    downside_usd_per_kg: float
    trigger: str  # "LOCK_NOW" | "NO_TRIGGER"
    reason: str
    price_direction_label: str = PRICE_DIRECTION_LABEL
    backtest_evidence: str = BACKTEST_EVIDENCE
    scenarios: list = field(default_factory=list)

    def log(self) -> None:
        log.info("=" * 70)
        log.info("HEDGE SIGNAL  (as of %s)", self.as_of.date())
        log.info("  %s", self.price_direction_label)
        log.info("  evidence: %s", self.backtest_evidence)
        log.info("  ---")
        log.info(
            "  current price      : $%.3f/kg  (%.0f%%ile of trailing 12 mo)",
            self.current_price_usd_per_kg,
            self.price_pctile_12m,
        )
        log.info(
            "  US rig count       : %.0f  (%+.1f%% year-on-year)",
            self.rig_level,
            self.rig_yoy_change_pct,
        )
        log.info(
            "  realised vol (6 mo): %.1f%%   bad-quarter analogue: -%.1f%%",
            self.realized_vol_6m_pct,
            self.hist_bad_quarter_pct,
        )
        log.info(
            "  >> %s  —  downside at risk ≈ $%.3f/kg",
            self.trigger,
            self.downside_usd_per_kg,
        )
        log.info("  why: %s", self.reason)
        if self.scenarios:
            log.info(
                "  --- rig-regime scenarios (historical analogue, not a forecast) ---"
            )
            for sc in self.scenarios:
                log.info(
                    "  %-38s n=%-3d worst-decile -%.1f%%  (median %+.1f%%)  "
                    "≈ $%.3f/kg",
                    sc["regime"],
                    sc["n_windows"],
                    sc["hist_adverse_drawdown_pct"],
                    sc["hist_median_move_pct"],
                    sc["downside_usd_per_kg"],
                )


def _rig_yoy_at(rig: pd.Series, ts: pd.Timestamp) -> float:
    """Year-on-year rig change at ts, look-ahead-safe via asof()."""
    now = rig.asof(ts)
    prev = rig.asof(ts - pd.DateOffset(months=12))
    if pd.isna(now) or pd.isna(prev) or not prev:
        return float("nan")
    return (float(now) - float(prev)) / float(prev) * 100


def _rig_regime_scenarios(
    price_hist: pd.Series, rig: pd.Series, as_of: pd.Timestamp, current: float
) -> list[dict]:
    """Descriptive scenario lever. In the months ≤ as_of where US drilling
    was in regime R, what did guar price actually do over the next
    SCEN_HORIZON_M months? Reports the worst-decile (adverse) and median
    forward move per regime. This is a historical *analogue*, NOT a
    forecast — overlapping windows, small sample (both flagged). Uses only
    data ≤ as_of."""
    h = SCEN_HORIZON_M
    rows = []
    for t in price_hist.index:
        t_h = t + pd.DateOffset(months=h)
        if t_h > as_of or t_h not in price_hist.index:
            continue
        ry = _rig_yoy_at(rig, t)
        if pd.isna(ry):
            continue
        rows.append((ry, price_hist.loc[t_h] / price_hist.loc[t] - 1.0))
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=["rig_yoy", "fwd"])
    out: list[dict] = []
    lo = -float("inf")
    for label, hi in RIG_REGIMES:
        bucket = df[(df["rig_yoy"] > lo) & (df["rig_yoy"] <= hi)]
        lo = hi
        if len(bucket) < SCEN_MIN_OBS:
            continue
        adverse = float(bucket["fwd"].quantile(DOWNSIDE_TAIL_Q))
        adverse_pct = round(-min(adverse, 0.0) * 100, 1)  # positive = a drop
        out.append(
            {
                "regime": label,
                "n_windows": int(len(bucket)),
                "hist_adverse_drawdown_pct": adverse_pct,
                "hist_median_move_pct": round(float(bucket["fwd"].median()) * 100, 1),
                "downside_usd_per_kg": round(current * adverse_pct / 100, 4),
            }
        )
    return out


def compute_hedge_signal(
    as_of: str | pd.Timestamp | None = None,
    hs: str = HS_PRIMARY,
    processed_dir: Path = PROCESSED_DIR_DEFAULT,
) -> HedgeSignal:
    """Risk trigger as of a decision date, using ONLY data ≤ as_of."""
    s = load_guar_price_series(hs=hs, processed_dir=processed_dir)
    price = s.set_index("period")["price_usd_per_kg"].astype(float).sort_index()
    rig = rig_monthly()

    if as_of is None:
        as_of = min(price.index.max(), rig.index.max())
    as_of = pd.Timestamp(as_of)
    if as_of.day != 1:
        as_of = as_of.to_period("M").to_timestamp()

    p_hist = price[price.index <= as_of]
    if len(p_hist) < 15:
        raise ValueError(f"insufficient price history at/under {as_of.date()}")
    current = float(p_hist.iloc[-1])

    win12 = p_hist[p_hist.index > as_of - pd.DateOffset(months=12)]
    price_pctile = float((win12 < current).mean() * 100)

    # rig: as-of level and its YEAR-ON-YEAR change (the source is
    # annual-broadcast, so YoY is its true resolution), look-ahead-safe
    # via asof() which only ever returns a value at-or-before the date.
    rig_now = float(rig.asof(as_of))
    rig_1y_ago = rig.asof(as_of - pd.DateOffset(months=12))
    if pd.isna(rig_1y_ago) or not rig_1y_ago:
        rig_yoy = 0.0  # < 1 yr of rig history — cannot judge the regime
    else:
        rig_yoy = (rig_now - float(rig_1y_ago)) / float(rig_1y_ago) * 100

    rets = p_hist.pct_change()
    realized_vol = float(rets.tail(6).std(ddof=0) * 100)

    chg3 = p_hist.pct_change(3).dropna()
    bad_quarter_pct = float(abs(chg3.quantile(DOWNSIDE_TAIL_Q)) * 100)
    downside_usd = round(current * bad_quarter_pct / 100, 4)

    scenarios = _rig_regime_scenarios(p_hist, rig, as_of, current)

    if rig_yoy <= RIG_YOY_DROP_THRESHOLD_PCT:
        trigger = "LOCK_NOW"
        reason = (
            f"US drilling down {rig_yoy:+.1f}% year-on-year — the leading "
            f"demand driver for guar is in a cooling regime, so price "
            f"downside risk is elevated."
        )
        if price_pctile >= PRICE_HIGH_PCTILE:
            reason += (
                f" You are also near a 12-month price high "
                f"({price_pctile:.0f}%ile): a good level to lock before "
                f"demand-driven erosion."
            )
    else:
        trigger = "NO_TRIGGER"
        reason = (
            f"US drilling {rig_yoy:+.1f}% year-on-year — demand driver not "
            f"deteriorating, so no elevated demand-side downside. Holding is "
            f"not penalised (we do not claim to predict an upside)."
        )

    return HedgeSignal(
        as_of=as_of,
        current_price_usd_per_kg=round(current, 4),
        price_pctile_12m=round(price_pctile, 1),
        rig_level=round(rig_now, 1),
        rig_yoy_change_pct=round(rig_yoy, 1),
        realized_vol_6m_pct=round(realized_vol, 1),
        hist_bad_quarter_pct=round(bad_quarter_pct, 1),
        downside_usd_per_kg=downside_usd,
        trigger=trigger,
        reason=reason,
        scenarios=scenarios,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--as-of", default=None, help="decision date, e.g. 2020-03")
    p.add_argument("--hs", default=HS_PRIMARY)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    compute_hedge_signal(as_of=args.as_of, hs=args.hs).log()


if __name__ == "__main__":
    main()
