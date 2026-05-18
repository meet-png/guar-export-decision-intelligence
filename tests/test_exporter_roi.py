"""Guards the rupee layer — the number the whole product is judged on.
These assert it is honest (risk framed as risk), scales correctly, and
the FX assumption is explicit and linear.
"""

from src.product.exporter_roi import (
    ExporterProfile,
    compute_exporter_roi,
)


def test_demo_profile_is_sane_and_labelled_simulated():
    p = ExporterProfile()
    assert "imulated" in p.name  # honest about being synthetic
    assert p.annual_kg == p.annual_tonnes * 1000
    assert 0 < sum(p.destination_mix.values()) <= 1.0
    assert p.us_share() > 0


def test_roi_numbers_finite_and_when_is_risk_not_prediction():
    roi = compute_exporter_roi()
    assert roi.downside_inr_year > 0
    assert roi.reroute_uplift_inr_year >= 0
    # WHEN must carry the honest "not a forecast" label, never a prediction
    assert "insufficient edge" in roi.when_label
    # uplift only ever booked on a positive price differential
    if roi.reroute_uplift_inr_year > 0:
        assert roi.pivot_realised_usd_per_kg > roi.us_realised_usd_per_kg


def test_downside_scales_with_volume():
    small = compute_exporter_roi(ExporterProfile(annual_tonnes=300))
    big = compute_exporter_roi(ExporterProfile(annual_tonnes=900))
    ratio = big.downside_inr_year / small.downside_inr_year
    assert abs(ratio - 3.0) < 0.01, "downside must scale linearly with tonnage"


def test_fx_is_explicit_and_linear():
    roi = compute_exporter_roi()
    f80 = roi.fx_sensitivity[80.0]["downside_lakh"]
    f87 = roi.fx_sensitivity[87.0]["downside_lakh"]
    # linear in FX: ratio of figures == ratio of rates
    assert abs((f87 / f80) - (87.0 / 80.0)) < 1e-6


def test_reroute_share_changes_uplift_proportionally():
    a = compute_exporter_roi(reroute_share=0.10)
    b = compute_exporter_roi(reroute_share=0.20)
    if a.reroute_uplift_inr_year > 0:
        assert abs(b.reroute_uplift_inr_year / a.reroute_uplift_inr_year - 2.0) < 0.01
