"""Guards the one-screen product. The UI body runs under `streamlit run`,
but all data prep is in `assemble_view`, which must be correct and must
not drag statsmodels onto the slim Streamlit Cloud deploy.
"""

import subprocess
import sys

import pytest

import streamlit_app as app


def test_screen_renders_without_exception():
    """Actually execute the Streamlit script in-process and assert it
    raised nothing — the only check that the UI itself works."""
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        pytest.skip("streamlit.testing.v1.AppTest unavailable")
    at = AppTest.from_file("streamlit_app.py", default_timeout=30).run()
    assert not at.exception, at.exception


def test_assemble_view_is_complete():
    v = app.assemble_view(tonnes=600, us_share=0.45, reroute_share=0.20, usd_inr=83.0)
    assert v.roi.downside_inr_year > 0
    assert v.roi.reroute_uplift_inr_year >= 0
    assert 30 < v.us_share_pct < 40  # US ≈ 35% of guar FOB
    # top payers sorted high→low, with the exporter's own USA market
    # appended as the comparison benchmark (it pays least, sits last).
    prem_no_usa = v.top_premium[v.top_premium["dest_country"] != "USA"][
        "realised_usd_per_kg"
    ].tolist()
    assert prem_no_usa == sorted(prem_no_usa, reverse=True)
    assert "USA" in set(v.top_premium["dest_country"])
    assert "insufficient edge" in v.sig.price_direction_label
    # both directions must be visible — surging AND the fading early
    # warnings (a one-way sort used to bury every fading market).
    assert set(v.shifts["shift_flag"]) <= {"SURGING", "FADING"}
    assert {"SURGING", "FADING"} <= set(v.shifts["shift_flag"])
    # a surging market shown must be actionable — paying strictly more
    # than the USA, the exporter's current market (a fast-growing market
    # that pays the same or less is no opportunity); FADING is a warning
    # at any price, so it is not guarded.
    surging_names = set(
        v.shifts.loc[v.shifts["shift_flag"] == "SURGING", "dest_country"]
    )
    guarded = v.radar[v.radar["dest_country"].isin(surging_names)]
    assert (guarded["realised_usd_per_kg"] > v.roi.us_realised_usd_per_kg).all()
    # the rig-regime scenario lever is carried to the screen
    assert v.sig.scenarios and all(
        "regime" in s and "hist_adverse_drawdown_pct" in s for s in v.sig.scenarios
    )
    # the decisive lead move is synthesised and honest
    assert v.move.action in {
        "LOCK_FORWARD_NOW",
        "DIVERSIFY_FROM_US",
        "HOLD_AND_MONITOR",
    }
    assert v.move.headline and "do NOT forecast" in v.move.caveat


def test_inr_formatting_lakh_and_crore():
    # plain words, not "L/Cr", so a non-technical exporter reads it
    assert app.inr(8_770_000).endswith("lakh")  # ₹87.7 lakh
    assert app.inr(25_000_000).endswith("crore")  # ₹2.50 crore


def test_view_reacts_to_inputs():
    small = app.assemble_view(300, 0.45, 0.20, 83.0)
    big = app.assemble_view(900, 0.45, 0.20, 83.0)
    assert big.roi.downside_inr_year > small.roi.downside_inr_year


def test_deploy_path_has_no_statsmodels():
    """Importing the app must not pull statsmodels — or Streamlit Cloud's
    slim (streamlit/pandas/numpy/plotly) deploy breaks. Checked in a clean
    subprocess so other tests importing the model can't mask a regression.
    """
    code = (
        "import streamlit_app, sys; "
        "bad=[m for m in sys.modules if 'statsmodels' in m]; "
        "sys.exit('LEAK:' + ','.join(bad) if bad else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
