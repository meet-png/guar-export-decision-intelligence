"""Pillar WHEN — guar price forecast engine, with an honest backtest.

What this forecasts (and why it differs from v1)
------------------------------------------------
v1's notebook-06 forecast monthly export *value* (price × volume) and its
own Limitations note conceded: *"No price signal. FOB USD is price ×
volume."* For a timing decision ("should I sell now or wait?") value is
the wrong target — a value spike could be more volume at a lower price.
This engine forecasts the **price** itself, off the cleaned spine
(:func:`src.features.guar_price.load_guar_price_series`,
``price_usd_per_kg``, HS 130232), which is the variable the decision
actually turns on.

Two credibility rules, enforced in code
---------------------------------------
1. **No look-ahead.** A forecast may use only what an exporter would
   actually know on the decision date. Exogenous drivers are therefore
   *lagged to availability* and then *held flat* across the forecast
   horizon — we never feed the model the future rig count it is trying
   to help predict around. (v1's CV passed realised future exog into the
   forecast window; that flatters the model and is not how it would run.)
       * rig count  → previous month's Baker Hughes NA mean (published
         weekly, so last month is genuinely known).
       * monsoon    → previous *calendar year's* Rajasthan LPA% (a year's
         monsoon is only known after it ends; the prior year always is).
2. **Skill vs naive.** Commodity prices are famously close to a random
   walk. A model that cannot beat "next month = this month" has no
   product value, so every backtest reports the model *and* two naive
   baselines (random-walk, seasonal-naive) on the same windows and the
   same metrics. We report this even when it is unflattering — the
   pilot's trust depends on it.

Metrics that matter for a *timing* product
------------------------------------------
* **Direction hit-rate** — did we get the sign of the move right? This
  is the metric a SELL/WAIT/LOCK call lives or dies by.
* **MAPE** — level accuracy, for context and the ROI math later.
Both are computed per horizon (h = 1 month: sell-now-vs-next;
h = 3 months: lock-a-quarter-forward).

CLI
---
    python -m src.model.price_forecast              # run backtest, print report
    python -m src.model.price_forecast --horizon 3
"""

from __future__ import annotations

import argparse
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import adfuller

from src.features.guar_price import HS_PRIMARY, load_guar_price_series

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR_DEFAULT = PROJECT_ROOT / "data" / "processed"
RIG_CSV = PROCESSED_DIR_DEFAULT / "rig_count_clean.csv"
MONSOON_CSV = PROCESSED_DIR_DEFAULT / "monsoon_clean.csv"

# Parsimonious, explainable, *stable* spec. We model LOG price: guar price
# is strictly positive with multiplicative dynamics, so logs stabilise the
# variance and make explosive/negative forecasts impossible. d is ADF-driven
# on the log series (not assumed). No seasonal differencing: on only 72
# monthly points, d=1 *and* seasonal D=1 over-differences and blows up; guar
# price seasonality is weak anyway — the real structure is in rig/monsoon,
# which enter as exog. Stationarity/invertibility are ENFORCED so the AR
# roots cannot explode (a model that emits 1e21 is not a product).
ORDER_PQ = (1, 1)  # (p, q); d decided by ADF on log price
SEASONAL_ORDER = (0, 0, 0, 0)

# Backtest geometry: enough history to fit a seasonal model, then expand.
MIN_TRAIN_MONTHS = 48
STEP_MONTHS = 1
DEFAULT_HORIZON = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("price_forecast")


# ---------------------------------------------------------------------------
# Exogenous drivers — lagged to availability, never peeking
# ---------------------------------------------------------------------------


def load_operational_exog(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Monthly exog aligned to ``index``, lagged so every value is one an
    exporter would actually know on the first of that month.

    * ``rig_prev_month`` — Baker Hughes NA rig count, weekly→monthly mean,
      shifted +1 month (last month is known; this month is not yet).
    * ``monsoon_prev_year`` — Rajasthan LPA%, the previous calendar
      year's figure (the only monsoon fully known during the year).
    """
    rig = pd.read_csv(RIG_CSV, parse_dates=["week_start_date"])
    rig["month"] = rig["week_start_date"].dt.to_period("M").dt.to_timestamp()
    rig_m = rig.groupby("month")["rig_count"].mean()
    rig_prev = rig_m.reindex(index).ffill().bfill().shift(1)

    mon = pd.read_csv(MONSOON_CSV)
    mon_map = dict(zip(mon["year"].astype(int), mon["lpa_pct"].astype(float)))
    monsoon_prev = pd.Series(
        [mon_map.get(ts.year - 1, np.nan) for ts in index], index=index
    )

    exog = pd.DataFrame(
        {"rig_prev_month": rig_prev, "monsoon_prev_year": monsoon_prev},
        index=index,
    )
    # Edges (first month has no prior month; first year no prior year):
    # back/forward fill the boundary so the matrix has no holes. This is
    # an availability stand-in, not future information.
    return exog.ffill().bfill()


def _decide_d(y: pd.Series) -> int:
    """ADF-driven differencing: d=0 if already stationary, else d=1.
    Test-driven, not assumed (a credibility point, mirroring v1)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = adfuller(y.dropna())[1]
    return 0 if p < 0.05 else 1


def assemble(
    hs: str = HS_PRIMARY, processed_dir: Path = PROCESSED_DIR_DEFAULT
) -> pd.DataFrame:
    """The modelling frame: monthly price target + lagged exog, gap-free."""
    s = load_guar_price_series(hs=hs, processed_dir=processed_dir)
    y = s.set_index("period")["price_usd_per_kg"].astype(float)
    y.index = pd.DatetimeIndex(y.index, freq="MS")
    exog = load_operational_exog(y.index)
    frame = pd.concat([y.rename("price_usd_per_kg"), exog], axis=1)
    if frame.isna().any().any():
        raise ValueError("assembled frame has NaNs — spine/exog misaligned")
    return frame


# ---------------------------------------------------------------------------
# Fit / forecast
# ---------------------------------------------------------------------------


def _fit(y: pd.Series, exog: pd.DataFrame):
    """Fit ARIMAX on LOG price with enforced stability."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ly = np.log(y)
        p, q = ORDER_PQ
        model = SARIMAX(
            ly,
            exog=exog,
            order=(p, _decide_d(ly), q),
            seasonal_order=SEASONAL_ORDER,
            enforce_stationarity=True,
            enforce_invertibility=True,
        )
        return model.fit(disp=False)


def _forecast_flat_exog(res, last_exog: pd.Series, horizon: int) -> np.ndarray:
    """Forecast ``horizon`` steps holding exog flat at the last known
    (lagged) value — the honest 'no new information' assumption, since
    future rig count / monsoon are not knowable at the decision date.
    Result is exponentiated back from log space (always positive)."""
    future_exog = pd.DataFrame(
        np.tile(last_exog.values, (horizon, 1)), columns=last_exog.index
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        log_fc = res.forecast(horizon, exog=future_exog).to_numpy()
    return np.exp(log_fc)


# ---------------------------------------------------------------------------
# Honest rolling-origin backtest
# ---------------------------------------------------------------------------


@dataclass
class BacktestReport:
    hs: str = HS_PRIMARY
    horizon: int = DEFAULT_HORIZON
    n_origins: int = 0
    chosen_d: int = 0
    model_mape_pct: float = 0.0
    rw_mape_pct: float = 0.0
    snaive_mape_pct: float = 0.0
    model_dir_hit_pct: float = 0.0
    rw_dir_hit_pct: float = 0.0
    per_horizon: dict = field(default_factory=dict)

    @property
    def beats_random_walk(self) -> bool:
        return (
            self.model_mape_pct < self.rw_mape_pct
            and self.model_dir_hit_pct >= self.rw_dir_hit_pct
        )

    def log(self) -> None:
        log.info("=" * 70)
        log.info(
            "Backtest — HS %s, horizon %d mo, %d origins, ADF d=%d",
            self.hs,
            self.horizon,
            self.n_origins,
            self.chosen_d,
        )
        log.info("  %-22s %8s %8s %8s", "", "MODEL", "RWALK", "SNAIVE")
        log.info(
            "  %-22s %7.1f%% %7.1f%% %7.1f%%",
            "MAPE (lower better)",
            self.model_mape_pct,
            self.rw_mape_pct,
            self.snaive_mape_pct,
        )
        log.info(
            "  %-22s %7.1f%% %7.1f%% %8s",
            "Direction hit (higher)",
            self.model_dir_hit_pct,
            self.rw_dir_hit_pct,
            "—",
        )
        for h, m in sorted(self.per_horizon.items()):
            log.info(
                "  h=%d:  MAPE %.1f%%  dir-hit %.1f%%  (n=%d)",
                h,
                m["mape"],
                m["dir_hit"],
                m["n"],
            )
        verdict = (
            "MODEL ADDS SKILL over a random walk ✓"
            if self.beats_random_walk
            else "MODEL DOES NOT clearly beat a random walk — report honestly, "
            "do not oversell the point forecast"
        )
        log.info("  VERDICT: %s", verdict)


def rolling_backtest(
    hs: str = HS_PRIMARY,
    horizon: int = DEFAULT_HORIZON,
    processed_dir: Path = PROCESSED_DIR_DEFAULT,
) -> tuple[pd.DataFrame, BacktestReport]:
    """Expanding-window, no-look-ahead backtest of the price model against
    random-walk and seasonal-naive baselines. Returns (per-step rows,
    report)."""
    frame = assemble(hs, processed_dir)
    y = frame["price_usd_per_kg"]
    exog = frame.drop(columns=["price_usd_per_kg"])
    n = len(y)

    rep = BacktestReport(hs=hs, horizon=horizon, chosen_d=_decide_d(np.log(y)))
    rows: list[dict] = []

    for cut in range(MIN_TRAIN_MONTHS, n - horizon + 1, STEP_MONTHS):
        y_tr, x_tr = y.iloc[:cut], exog.iloc[:cut]
        y_te = y.iloc[cut : cut + horizon].to_numpy()
        anchor = y_tr.iloc[-1]  # last observed price = decision-day price

        try:
            res = _fit(y_tr, x_tr)
            yhat = _forecast_flat_exog(res, exog.iloc[cut - 1], horizon)
        except Exception as exc:  # noqa: BLE001 — one bad fit must not abort
            log.warning("fit failed at cut=%d: %s", cut, exc)
            continue

        rw = np.full(horizon, anchor)  # random walk: flat at last value
        snaive = (
            y.iloc[cut - 12 : cut - 12 + horizon].to_numpy() if cut - 12 >= 0 else rw
        )
        for h in range(1, horizon + 1):
            a = y_te[h - 1]
            rows.append(
                {
                    "cut": y_tr.index[-1],
                    "h": h,
                    "actual": a,
                    "model": yhat[h - 1],
                    "rw": rw[h - 1],
                    "snaive": snaive[h - 1] if h - 1 < len(snaive) else np.nan,
                    "anchor": anchor,
                }
            )

    bt = pd.DataFrame(rows)
    rep.n_origins = bt["cut"].nunique()

    def _mape(col: str) -> float:
        d = bt.dropna(subset=[col])
        return float((d[col] - d["actual"]).abs().div(d["actual"]).mean() * 100)

    def _dir_hit(col: str) -> float:
        d = bt.dropna(subset=[col])
        pred_up = np.sign(d[col] - d["anchor"])
        true_up = np.sign(d["actual"] - d["anchor"])
        return float((pred_up == true_up).mean() * 100)

    rep.model_mape_pct = round(_mape("model"), 1)
    rep.rw_mape_pct = round(_mape("rw"), 1)
    rep.snaive_mape_pct = round(_mape("snaive"), 1)
    rep.model_dir_hit_pct = round(_dir_hit("model"), 1)
    rep.rw_dir_hit_pct = round(_dir_hit("rw"), 1)
    for h, g in bt.groupby("h"):
        gg = g.dropna(subset=["model"])
        rep.per_horizon[int(h)] = {
            "mape": round(
                float(
                    (gg["model"] - gg["actual"]).abs().div(gg["actual"]).mean() * 100
                ),
                1,
            ),
            "dir_hit": round(
                float(
                    (
                        np.sign(gg["model"] - gg["anchor"])
                        == np.sign(gg["actual"] - gg["anchor"])
                    ).mean()
                    * 100
                ),
                1,
            ),
            "n": int(len(gg)),
        }
    return bt, rep


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--hs", default=HS_PRIMARY)
    p.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    _, rep = rolling_backtest(hs=args.hs, horizon=args.horizon)
    rep.log()


if __name__ == "__main__":
    main()
