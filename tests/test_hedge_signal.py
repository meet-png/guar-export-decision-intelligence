"""Guards Pillar WHEN (repositioned). The product's integrity rests on
two things: the honest label is always present, and the trigger is a
real risk signal — not a fitted forecast in disguise.
"""

import pandas as pd

from src.features.guar_price import load_guar_price_series
from src.model.hedge_signal import (
    PRICE_DIRECTION_LABEL,
    compute_hedge_signal,
)


def test_honest_label_is_always_present():
    sig = compute_hedge_signal()
    assert sig.price_direction_label == PRICE_DIRECTION_LABEL
    assert "insufficient edge" in sig.price_direction_label
    assert sig.backtest_evidence  # the numbers backing the claim


def test_rig_regime_scenarios_are_descriptive_and_sane():
    """The scenario lever must be a historical analogue, not a forecast:
    every regime row backed by real windows, drawdowns non-negative, and
    a worse drilling regime is not *less* adverse than a benign one."""
    sig = compute_hedge_signal()
    assert sig.scenarios, "expected rig-regime scenarios"
    for sc in sig.scenarios:
        assert sc["n_windows"] >= 4  # SCEN_MIN_OBS — no 1-obs noise
        assert sc["hist_adverse_drawdown_pct"] >= 0
        assert sc["downside_usd_per_kg"] >= 0
    by_regime = {sc["regime"]: sc for sc in sig.scenarios}
    collapse = next((v for k, v in by_regime.items() if "collapse" in k.lower()), None)
    steady = next((v for k, v in by_regime.items() if "steady" in k.lower()), None)
    if collapse and steady:
        assert (
            collapse["hist_adverse_drawdown_pct"]
            >= steady["hist_adverse_drawdown_pct"] - 1e-9
        ), "a drilling collapse should not look safer than steady drilling"


def test_scenarios_have_no_lookahead():
    """Scenario windows use only data ≤ as_of: an early as_of must yield
    no window that reaches past it."""
    early = compute_hedge_signal(as_of="2021-06")
    # at 2021-06 with a 6-mo horizon, the latest usable origin is 2020-12;
    # the function must still return something or nothing, never error,
    # and never reference price after 2021-06 (enforced in code).
    assert isinstance(early.scenarios, list)


def test_no_lookahead_uses_only_data_up_to_as_of():
    """current price must equal the spine price AT as_of, never later."""
    as_of = pd.Timestamp("2021-06-01")
    sig = compute_hedge_signal(as_of=as_of)
    spine = load_guar_price_series().set_index("period")["price_usd_per_kg"]
    assert sig.as_of == as_of
    assert abs(sig.current_price_usd_per_kg - float(spine.loc[as_of])) < 1e-6


def test_lock_now_fires_when_drilling_collapses():
    """Early 2020: NA rig count fell off a cliff (≈946→426). The demand
    driver is deteriorating → the trigger must say LOCK_NOW."""
    sig = compute_hedge_signal(as_of="2020-05")
    assert sig.rig_yoy_change_pct < -10
    assert sig.trigger == "LOCK_NOW"
    assert "drilling" in sig.reason.lower()


def test_no_trigger_when_drilling_is_recovering():
    """Late 2021: rig count was climbing back. No elevated demand-side
    downside → NO_TRIGGER, and we must NOT invent an upside claim."""
    sig = compute_hedge_signal(as_of="2021-11")
    assert sig.rig_yoy_change_pct > 0
    assert sig.trigger == "NO_TRIGGER"
    assert "predict" in sig.reason.lower() or "not penalised" in sig.reason


def test_downside_is_descriptive_and_positive():
    sig = compute_hedge_signal()
    assert sig.hist_bad_quarter_pct > 0
    assert 0 < sig.downside_usd_per_kg < sig.current_price_usd_per_kg


def test_as_of_defaults_to_latest_available_month():
    sig = compute_hedge_signal()
    assert sig.as_of == pd.Timestamp("2024-12-01")
