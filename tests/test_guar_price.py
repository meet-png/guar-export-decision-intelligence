"""Guards the product spine: the canonical guar price series.

If any of these fail, every rupee figure the product would show an
exporter is suspect — so these are intentionally strict.
"""

import pandas as pd
import pytest

from src.features.guar_price import (
    HS_PRIMARY,
    SANE_PRICE_BAND_USD,
    build_guar_price,
    load_guar_price_series,
)


@pytest.fixture(scope="module")
def series() -> pd.DataFrame:
    return build_guar_price()


def _primary(series: pd.DataFrame) -> pd.DataFrame:
    return (
        series[series["hs_code"] == HS_PRIMARY]
        .sort_values("period")
        .reset_index(drop=True)
    )


def test_full_monthly_coverage_no_gaps(series):
    p = _primary(series)
    expected = pd.date_range("2019-01-01", "2024-12-01", freq="MS")
    assert list(p["period"]) == list(expected), "primary HS must be 72 gap-free months"


def test_period_monotonic_unique(series):
    p = _primary(series)
    assert p["period"].is_monotonic_increasing
    assert p["period"].is_unique


def test_consumable_price_present_and_in_band(series):
    """price_usd_per_kg is what the pillars forecast — post-repair it must
    have no holes and never leave the sane band."""
    p = _primary(series)
    assert p["price_usd_per_kg"].notna().all()
    lo, hi = SANE_PRICE_BAND_USD
    assert p["price_usd_per_kg"].between(lo, hi).all()


def test_oct_2021_corrupt_month_is_flagged_and_repaired(series):
    """Oct-2021 is a whole-month quantity corruption: every destination
    prints ~$7/kg. The raw value must be preserved for audit, the month
    flagged is_imputed, and the consumable price interpolated back to a
    sane level between its honest neighbours (Sep ~$1.49, Nov ~$1.86).
    """
    p = _primary(series)
    row = p[p["period"] == pd.Timestamp("2021-10-01")]
    assert not row.empty
    r = row.iloc[0]
    assert bool(r["is_imputed"]) is True, "corrupt month must be flagged"
    assert (
        r["robust_price_raw_usd_per_kg"] > 4.0
    ), "raw artifact must be preserved in the audit column, not erased"
    assert 1.2 < r["price_usd_per_kg"] < 2.2, (
        f"repaired price implausible: ${r['price_usd_per_kg']:.2f} "
        f"(should interpolate between honest neighbours)"
    )


def test_imputation_is_sparing_for_primary(series):
    """A gate that rewrites half the series is worthless. The primary
    series is mostly honest data; only genuinely corrupt months get
    touched."""
    p = _primary(series)
    n_imputed = int(p["is_imputed"].sum())
    assert 1 <= n_imputed <= 4, f"primary imputed {n_imputed} months — expected ~1-4"


def test_robust_beats_naive_somewhere(series):
    """The whole reason for the weighted median: there must exist a month
    where the fragile Σv/Σq ratio materially disagrees with robust."""
    p = _primary(series)
    gap = (p["naive_price_usd_per_kg"] - p["robust_price_raw_usd_per_kg"]).abs()
    assert gap.max() > 0.1


def test_load_api_returns_sorted_nonempty():
    s = load_guar_price_series(hs=HS_PRIMARY)
    assert not s.empty
    assert s["period"].is_monotonic_increasing
    assert (s["hs_code"].astype(str) == HS_PRIMARY).all()
    assert "price_usd_per_kg" in s.columns
