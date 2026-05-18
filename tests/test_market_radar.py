"""Guards Pillar WHERE. The radar makes no forecast, so these assert the
structural facts are right and the pivot logic is sane and diversifying.
"""

import shutil

import pandas as pd
import pytest

from src.features.market_radar import (
    IN_PATH_DEFAULT,
    MIN_TOTAL_FOB_USD,
    build_market_radar,
    load_market_radar,
)


@pytest.fixture(scope="module")
def radar() -> pd.DataFrame:
    return build_market_radar()


def test_shape_and_no_holes(radar):
    assert len(radar) > 100  # ~134 guar destinations
    for c in (
        "dest_iso",
        "india_share_pct",
        "realised_usd_per_kg",
        "price_vs_portfolio_pct",
        "fta_status",
    ):
        assert radar[c].notna().all()
    assert abs(radar["india_share_pct"].sum() - 100.0) < 0.5  # shares sum to 100


def test_us_is_the_concentration_risk_not_the_pivot(radar):
    us = radar[radar["dest_iso"] == "USA"].iloc[0]
    assert 30 < us["india_share_pct"] < 40, "US should be ~35% of guar FOB"
    assert us["fta_status"] == "TARIFF_STRESSED"
    ranked = radar[radar["pivot_score"].notna()].sort_values(
        "pivot_score", ascending=False
    )
    assert (
        ranked.iloc[0]["dest_iso"] != "USA"
    ), "the pivot target must never be the market we are over-exposed to"


def test_price_differential_is_real_and_signed(radar):
    """Japan pays ~2x Netherlands — the rupee headline must reflect it."""
    jpn = radar[radar["dest_iso"] == "JPN"].iloc[0]
    nld = radar[radar["dest_iso"] == "NLD"].iloc[0]
    assert jpn["realised_usd_per_kg"] > nld["realised_usd_per_kg"]
    assert jpn["price_vs_portfolio_pct"] > 0
    assert jpn["realised_usd_per_kg"] > 2.0


def test_pivot_score_and_shift_flag_well_formed(radar):
    ranked = radar[radar["pivot_score"].notna()]
    assert (ranked["pivot_score"].between(0, 100)).all()
    assert set(radar["shift_flag"].dropna()) <= {"SURGING", "FADING", "STABLE"}
    # materiality gate actually filters the long tail
    assert ranked["total_fob_usd"].min() >= MIN_TOTAL_FOB_USD
    assert len(ranked) < len(radar)


def test_headroom_columns_present_and_strategic(radar):
    """The 10x upgrade: the radar must carry world-import headroom and
    reflect that the US is saturated while big markets are under-served."""
    for c in ("world_import_usd", "india_share_of_market_pct", "addressable_usd"):
        assert c in radar.columns
    us = radar[radar["dest_iso"] == "USA"].iloc[0]
    deu = radar[radar["dest_iso"] == "DEU"].iloc[0]
    # India is heavily penetrated in the US, barely in Germany
    assert us["india_share_of_market_pct"] > deu["india_share_of_market_pct"]
    assert us["india_share_of_market_pct"] > 30
    # the chosen top pivot must actually have measured headroom
    top = (
        radar[radar["pivot_score"].notna()]
        .sort_values("pivot_score", ascending=False)
        .iloc[0]
    )
    assert top["world_import_usd"] > 0


def test_deploy_path_rebuilds_from_csv_without_parquet(tmp_path):
    """Streamlit Cloud ships exports_clean.csv, never the .parquet. If the
    committed market_radar.csv ever goes stale there, the radar must
    rebuild from the committed CSV (same fallback as the price spine) —
    not crash with a parquet FileNotFoundError that can't reproduce
    locally (local has the parquet). Regression for that asymmetry.
    """
    csv_src = IN_PATH_DEFAULT.with_suffix(".csv")  # committed exports_clean.csv
    assert csv_src.exists(), "committed exports_clean.csv must exist for deploy"
    shutil.copy(csv_src, tmp_path / "exports_clean.csv")
    missing_parquet = tmp_path / "exports_clean.parquet"
    assert not missing_parquet.exists()

    out = build_market_radar(in_path=missing_parquet)  # parquet absent → CSV path

    assert len(out) > 100
    assert {"dest_iso", "pivot_score", "world_import_usd"}.issubset(out.columns)
    assert out[out["dest_iso"] == "USA"].iloc[0]["fta_status"] == "TARIFF_STRESSED"


def test_load_api_and_determinism():
    a = load_market_radar()
    b = build_market_radar().reset_index(drop=True)
    assert not a.empty
    assert len(a) == len(b)
    # deterministic top pivot
    assert (
        a.sort_values("pivot_score", ascending=False).iloc[0]["dest_iso"]
        == b.sort_values("pivot_score", ascending=False).iloc[0]["dest_iso"]
    )
