"""Guards Pillar WHEN's two credibility rules: no look-ahead, and an
honest model-vs-naive comparison actually gets computed.

These do NOT assert the model is good — that is an empirical finding we
report truthfully. They assert the harness cannot lie.
"""

import numpy as np
import pandas as pd
import pytest

from src.model.price_forecast import (
    MIN_TRAIN_MONTHS,
    assemble,
    load_operational_exog,
    rolling_backtest,
)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return assemble()


def test_frame_is_complete_and_monthly(frame):
    assert len(frame) == 72
    assert list(frame.columns) == [
        "price_usd_per_kg",
        "rig_prev_month",
        "monsoon_prev_year",
    ]
    assert not frame.isna().any().any()
    assert (frame.index.freqstr or pd.infer_freq(frame.index)) in ("MS", "MS-JAN")


def test_monsoon_regressor_has_no_lookahead(frame):
    """2021 must see 2020's monsoon (123.0), never 2021's own (102.0)."""
    val = frame.loc["2021-06-01", "monsoon_prev_year"]
    assert val == 123.0, f"expected prior-year 123.0, got {val} (look-ahead!)"


def test_rig_regressor_is_previous_month(frame):
    """rig_prev_month at month m must equal the rig mean of month m-1."""
    rig = pd.read_csv(
        load_operational_exog.__globals__["RIG_CSV"], parse_dates=["week_start_date"]
    )
    rig["month"] = rig["week_start_date"].dt.to_period("M").dt.to_timestamp()
    rig_m = rig.groupby("month")["rig_count"].mean()
    assert abs(frame.loc["2020-06-01", "rig_prev_month"] - rig_m["2020-05-01"]) < 1e-6


def test_backtest_runs_and_reports_naive_baselines():
    bt, rep = rolling_backtest(horizon=3)
    assert rep.n_origins >= 12, "too few backtest origins to trust the metric"
    # every credibility metric must be a finite, computed number
    for v in (
        rep.model_mape_pct,
        rep.rw_mape_pct,
        rep.snaive_mape_pct,
        rep.model_dir_hit_pct,
        rep.rw_dir_hit_pct,
    ):
        assert np.isfinite(v) and v >= 0
    assert set(rep.per_horizon) == {1, 2, 3}
    # the model's forecasts must actually vary (not a constant = silent RW)
    assert bt["model"].nunique() > 5


def test_no_origin_uses_fewer_than_min_train(monkeypatch):
    bt, _ = rolling_backtest(horizon=3)
    first_cut = bt["cut"].min()
    # first origin must sit at least MIN_TRAIN_MONTHS into the series
    full = pd.date_range("2019-01-01", "2024-12-01", freq="MS")
    assert list(full).index(first_cut) >= MIN_TRAIN_MONTHS - 1
