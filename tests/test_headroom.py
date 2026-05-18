"""Guards the headroom layer — each market's total world guar imports.
The strategic upgrade rests on this being parsed correctly and the
US-saturation / Germany-headroom facts being real, not asserted.
"""

import pandas as pd
import pytest

from src.features.headroom import load_world_imports


@pytest.fixture(scope="module")
def world() -> pd.DataFrame:
    w = load_world_imports()
    if w.empty:
        pytest.skip("no worldimp_*.json raw pulls present (gitignored)")
    return w


def test_schema_and_no_dupes(world):
    assert {"dest_iso", "world_import_usd", "world_import_usd_last"} <= set(
        world.columns
    )
    assert world["dest_iso"].is_unique
    assert (world["world_import_usd"] > 0).all()
    # ISO codes only (aggregates like WLD/_X filtered out)
    assert world["dest_iso"].str.fullmatch(r"[A-Z]{3}").all()


def test_germany_is_the_big_buyer_and_us_is_large(world):
    w = world.set_index("dest_iso")
    assert "DEU" in w.index and "USA" in w.index
    # Germany is the world's largest guar importer in this data
    assert (
        world.sort_values("world_import_usd", ascending=False).iloc[0]["dest_iso"]
        == "DEU"
    )
    assert w.loc["DEU", "world_import_usd"] > w.loc["USA", "world_import_usd"]


def test_us_saturation_visible_via_radar():
    """India should show as ~43% of the US market and a low single-digit
    share of Germany — the whole strategic reframe."""
    from src.features.market_radar import build_market_radar

    r = build_market_radar().set_index("dest_iso")
    assert 35 < r.loc["USA", "india_share_of_market_pct"] < 55
    assert r.loc["DEU", "india_share_of_market_pct"] < 15
    # addressable headroom present and ordered sanely
    assert r.loc["DEU", "addressable_usd"] > r.loc["USA", "addressable_usd"]
