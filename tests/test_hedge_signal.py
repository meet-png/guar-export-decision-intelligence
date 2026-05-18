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
