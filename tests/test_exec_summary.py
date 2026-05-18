"""The lead sentence must be decisive AND honest. Decision logic is
pure, so all three branches are tested deterministically; the caveat is
never dropped.
"""

from src.product.exec_summary import CAVEAT, _decide, top_move

_BASE = dict(
    rig_yoy_pct=-30.0,
    downside_lakh=88.0,
    uplift_lakh=47.0,
    pivot_country="Japan",
    pivot_usd=2.71,
    us_usd=1.66,
    collapse_drawdown_pct=13.0,
    reroute_pct=20.0,
)


def test_lock_now_leads_with_the_hedge():
    m = _decide(trigger="LOCK_NOW", **_BASE)
    assert m.action == "LOCK_FORWARD_NOW"
    assert "Lock a forward" in m.headline
    assert "88" in m.headline  # the ₹ downside figure leads
    assert m.caveat == CAVEAT


def test_no_trigger_with_real_gain_leads_with_diversify():
    m = _decide(trigger="NO_TRIGGER", **_BASE)
    assert m.action == "DIVERSIFY_FROM_US"
    assert "Japan" in m.headline and "47" in m.headline
    assert "saturated" in m.why


def test_no_trigger_no_gain_is_honest_hold():
    args = {**_BASE, "uplift_lakh": 0.2}
    m = _decide(trigger="NO_TRIGGER", **args)
    assert m.action == "HOLD_AND_MONITOR"
    assert "No urgent move" in m.headline
    # must not manufacture a rupee promise it can't back
    assert "honest" in m.why.lower()


def test_caveat_always_states_no_forecast():
    for trig in ("LOCK_NOW", "NO_TRIGGER"):
        m = _decide(trigger=trig, **_BASE)
        assert "do NOT forecast" in m.caveat


def test_top_move_runs_on_real_data():
    m = top_move()
    assert m.action in {
        "LOCK_FORWARD_NOW",
        "DIVERSIFY_FROM_US",
        "HOLD_AND_MONITOR",
    }
    assert m.headline and m.why and m.caveat
