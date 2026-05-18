"""Pillar WHERE — the market-pivot radar (the heavier, defensible pillar).

Why this is the stronger pillar
-------------------------------
WHEN had to concede it cannot forecast price (honest backtest). WHERE
makes *no forecast*: it is structural analysis of where India's guar
business actually is, what each destination actually pays, and where it
is moving — facts, not predictions. That is exactly why it carries the
product's analytical weight.

The buyer questions it answers
------------------------------
1. **"How exposed am I?"** — India ships ~35% of its guar to the US,
   which is tariff-stressed *and* saturated: India already supplies
   ~43% of total US guar imports, so there is little room to grow there.
2. **"Which country pays me more?"** — realised $/kg differs ~2x across
   destinations (Japan ≈ $2.71 vs Netherlands ≈ $1.29 for the same HS).
3. **"Where is the real untapped demand?"** — joins each market's
   **total world guar imports** to India's flow → ``india_share_of_market``.
   Germany buys $6B of guar from the world; India supplies ~4%. France
   $1.2B at ~2%. *That* is headroom, quantified — not "a country India
   happens to undership."
4. **"Where should I push?"** — a transparent ``pivot_score`` blending
   realised-price premium, **strategic headroom**, recent momentum, and
   FTA/tariff friendliness.

Recurring radar, not a one-shot report
--------------------------------------
``shift_flag`` (SURGING / FADING / STABLE from last-12-mo vs prior-12-mo)
is the monitored signal that makes this a subscription, not a consulting
deliverable — re-runs flag a market turning before the exporter feels it.

Honest limitations (carried, not hidden)
----------------------------------------
* **EU hub re-export.** Large hub importers (Germany, Netherlands,
  Belgium) include heavy intra-EU and re-export flow, so their
  world-import figure OVERSTATES true end-demand. Headroom is an
  opportunity *indicator*, not a guaranteed addressable market — said,
  not hidden. (Markets with no world-import pull score 0 on headroom,
  never win by default; the radar still emits the columns so the
  committed CSV schema is stable on a fresh clone.)
* Realised price is the same lagged, mixed-grade Comtrade proxy as the
  spine (robust qty-weighted, ``is_outlier`` rows dropped).
* ``fta_status`` is a small **curated, dated** reference, not a live
  tariff feed. "Good to deal with" also has non-data factors (payment
  behaviour, logistics) this cannot see — stated, not pretended.

CLI
---
    python -m src.features.market_radar              # build, write CSV
    python -m src.features.market_radar --no-write   # print the radar
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.features.guar_price import (
    HS_PRIMARY,
    _weighted_median,
    load_guar_price_series,
)
from src.features.headroom import load_world_imports

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR_DEFAULT = PROJECT_ROOT / "data" / "processed"
IN_PATH_DEFAULT = PROCESSED_DIR_DEFAULT / "exports_clean.parquet"
OUT_NAME = "market_radar.csv"

# Materiality gate: 134 destinations, most sporadic. Rank only real
# markets — enough cumulative business and a non-sporadic presence.
MIN_TOTAL_FOB_USD = 2_000_000
MIN_ACTIVE_MONTHS = 18

# shift_flag thresholds on (last 12 mo vs prior 12 mo) FOB change.
SURGE_PCT = 20.0
FADE_PCT = -20.0

# Curated, DATED tariff-friendliness reference (public, well-established):
# - US: 50% handicraft-era tariff regime + 2025 trade stress on Indian
#   goods broadly; treated as the exposure to diversify AWAY from.
# - FTA partners with near-zero duty for Indian exports:
#   UAE CEPA (2022), India-Australia ECTA (2022).
# Anything unlisted = MFN/UNKNOWN (neutral). As of 2026-05; not a feed.
FTA_STATUS = {
    "USA": "TARIFF_STRESSED",
    "ARE": "FTA",  # UAE CEPA
    "AUS": "FTA",  # India-Australia ECTA
}
FTA_SCORE = {"FTA": 1.0, "MFN": 0.5, "UNKNOWN": 0.5, "TARIFF_STRESSED": 0.0}

# pivot_score weights — documented, explainable, NOT fitted to anything.
W_PRICE = 0.30  # realised-price premium vs the national portfolio
W_HEADROOM = 0.30  # big WORLD market where India is under-penetrated
W_MOMENTUM = 0.20  # recent 12-mo vs prior 12-mo direction
W_FTA = 0.20  # tariff/FTA friendliness

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("market_radar")


@dataclass
class RadarReport:
    hs: str = HS_PRIMARY
    portfolio_usd_per_kg: float = 0.0
    n_destinations: int = 0
    n_ranked: int = 0
    us_share_pct: float = 0.0
    us_share_of_market_pct: float = 0.0
    top_pivot: str = ""
    top_headroom: str = ""
    n_surging: int = 0
    n_fading: int = 0

    def log(self) -> None:
        log.info("=" * 70)
        log.info("Market radar — HS %s", self.hs)
        log.info("  national realised price : $%.3f/kg", self.portfolio_usd_per_kg)
        log.info(
            "  destinations: %d total, %d ranked (materiality gate)",
            self.n_destinations,
            self.n_ranked,
        )
        log.info(
            "  US concentration        : %.1f%% of India's guar FOB; India "
            "supplies %.0f%% of the US market (saturated, not just tariffed)",
            self.us_share_pct,
            self.us_share_of_market_pct,
        )
        log.info("  biggest untapped market : %s", self.top_headroom)
        log.info("  top pivot target        : %s", self.top_pivot)
        log.info(
            "  shift alerts            : %d surging, %d fading",
            self.n_surging,
            self.n_fading,
        )


def _pctrank(s: pd.Series) -> pd.Series:
    """Rank-based 0-1 normalisation — robust to the long tail of tiny
    markets in a way min-max is not."""
    return s.rank(pct=True)


def build_market_radar(
    in_path: Path = IN_PATH_DEFAULT,
    hs: str = HS_PRIMARY,
    processed_dir: Path = PROCESSED_DIR_DEFAULT,
    report: RadarReport | None = None,
) -> pd.DataFrame:
    """Per-destination market radar for one guar HS code. No forecast —
    structural facts + a transparent, documented pivot score."""
    report = report or RadarReport(hs=hs)
    df = pd.read_parquet(in_path)
    df["hs_code"] = df["hs_code"].astype(str)
    g = df[
        (df["hs_code"] == hs)
        & (~df["is_outlier"].astype(bool))
        & (df["fob_usd"] > 0)
        & (df["quantity_kg"] > 0)
    ].copy()
    if "reporter_country_name" in g.columns:
        g = g[g["reporter_country_name"].astype(str).str.strip() == "India"].copy()

    g["shipment_date"] = pd.to_datetime(g["shipment_date"])
    g["_unit"] = g["fob_usd"] / g["quantity_kg"]
    total_fob = g["fob_usd"].sum()

    # National realised price = the spine's mean over the same window, so
    # WHERE and the price spine agree on "India's price" (one source).
    spine = load_guar_price_series(hs=hs, processed_dir=processed_dir)
    portfolio_price = float(spine["price_usd_per_kg"].mean())

    asof = g["shipment_date"].max()
    last12_lo = asof - pd.DateOffset(months=11)
    prev12_lo = asof - pd.DateOffset(months=23)

    rows: list[dict] = []
    for (iso, name), s in g.groupby(["dest_iso_alpha3", "dest_country_name"]):
        realised = _weighted_median(
            s["_unit"].to_numpy(float), s["quantity_kg"].to_numpy(float)
        )
        last12 = s.loc[s["shipment_date"] >= last12_lo, "fob_usd"].sum()
        prev12 = s.loc[
            (s["shipment_date"] >= prev12_lo) & (s["shipment_date"] < last12_lo),
            "fob_usd",
        ].sum()
        shift = (last12 - prev12) / prev12 * 100 if prev12 > 0 else np.nan
        fta = FTA_STATUS.get(iso, "MFN")
        rows.append(
            {
                "dest_iso": iso,
                "dest_country": name,
                "total_fob_usd": round(float(s["fob_usd"].sum()), 2),
                "india_share_pct": round(s["fob_usd"].sum() / total_fob * 100, 2),
                "realised_usd_per_kg": round(float(realised), 4),
                "price_vs_portfolio_pct": round(
                    (realised / portfolio_price - 1) * 100, 1
                ),
                "recent_shift_pct": (
                    round(float(shift), 1) if pd.notna(shift) else np.nan
                ),
                "n_active_months": int(s["shipment_date"].nunique()),
                "fta_status": fta,
            }
        )

    radar = pd.DataFrame(rows)
    report.n_destinations = len(radar)

    # --- Strategic headroom -------------------------------------------------
    # Each market's TOTAL guar imports from the world, and how little of
    # that India currently supplies. A large market with a low India share
    # = real, quantified untapped opportunity — far stronger than the old
    # proxy of "thin in India's own book". Degrades gracefully if the
    # world-import raw pulls are absent (columns still emitted → the
    # committed CSV schema stays stable on a fresh clone).
    #
    # Honest caveat: large EU hub importers (Germany, Netherlands, Belgium)
    # include heavy intra-EU / re-export flow, so their world-import figure
    # OVERSTATES true end-demand. Headroom is an opportunity *indicator*,
    # not a guaranteed addressable market — stated, not hidden.
    world = load_world_imports(hs_code=hs)
    radar = radar.merge(world, on="dest_iso", how="left")
    radar["india_share_of_market_pct"] = np.where(
        radar["world_import_usd"] > 0,
        (radar["total_fob_usd"] / radar["world_import_usd"] * 100).clip(upper=100),
        np.nan,
    ).round(1)
    radar["addressable_usd"] = np.where(
        radar["world_import_usd"] > 0,
        radar["world_import_usd"]
        * (1 - radar["india_share_of_market_pct"].fillna(0) / 100),
        np.nan,
    ).round(2)

    eligible = (radar["total_fob_usd"] >= MIN_TOTAL_FOB_USD) & (
        radar["n_active_months"] >= MIN_ACTIVE_MONTHS
    )
    r = radar[eligible].copy()
    report.n_ranked = len(r)

    # Transparent composite. Each component normalised to 0-1 by rank
    # across the eligible set, then a documented weighted sum.
    price_n = _pctrank(r["price_vs_portfolio_pct"])
    mom_n = _pctrank(r["recent_shift_pct"].fillna(r["recent_shift_pct"].min()))
    # Strategic headroom: rank by addressable space = big world market ×
    # low India penetration. Markets with no world-import data score 0
    # here rather than win by default (US scores low: India already
    # supplies ~43% of it — saturated, not just tariffed).
    headroom_n = _pctrank(r["addressable_usd"].fillna(0.0))
    fta_n = r["fta_status"].map(FTA_SCORE).astype(float)

    r["pivot_score"] = (
        W_PRICE * price_n
        + W_HEADROOM * headroom_n
        + W_MOMENTUM * mom_n
        + W_FTA * fta_n
    ) * 100
    r["pivot_score"] = r["pivot_score"].round(1)
    r["shift_flag"] = np.select(
        [r["recent_shift_pct"] >= SURGE_PCT, r["recent_shift_pct"] <= FADE_PCT],
        ["SURGING", "FADING"],
        default="STABLE",
    )

    out = radar.merge(
        r[["dest_iso", "pivot_score", "shift_flag"]], on="dest_iso", how="left"
    ).sort_values("pivot_score", ascending=False, na_position="last")
    out = out.reset_index(drop=True)

    report.portfolio_usd_per_kg = round(portfolio_price, 4)
    us = out[out["dest_iso"] == "USA"]
    if len(us):
        report.us_share_pct = float(us["india_share_pct"].iloc[0])
        report.us_share_of_market_pct = float(
            us["india_share_of_market_pct"].fillna(0).iloc[0]
        )
    hr = out[out["addressable_usd"].notna()].sort_values(
        "addressable_usd", ascending=False
    )
    report.top_headroom = (
        f"{hr.iloc[0]['dest_country']} (world ${hr.iloc[0]['world_import_usd'] / 1e9:.1f}B, "
        f"India only {hr.iloc[0]['india_share_of_market_pct']:.1f}%)"
        if len(hr)
        else "—"
    )
    ranked = out[out["pivot_score"].notna()]
    report.top_pivot = (
        f"{ranked.iloc[0]['dest_country']} "
        f"(score {ranked.iloc[0]['pivot_score']:.0f}, "
        f"{ranked.iloc[0]['price_vs_portfolio_pct']:+.0f}% price vs avg)"
        if len(ranked)
        else "—"
    )
    report.n_surging = int((out["shift_flag"] == "SURGING").sum())
    report.n_fading = int((out["shift_flag"] == "FADING").sum())
    return out


def load_market_radar(
    hs: str = HS_PRIMARY, processed_dir: Path = PROCESSED_DIR_DEFAULT
) -> pd.DataFrame:
    """Importable API for the dashboard / pilot brief."""
    out_path = processed_dir / OUT_NAME
    required = {
        "dest_iso",
        "pivot_score",
        "price_vs_portfolio_pct",
        "world_import_usd",
        "india_share_of_market_pct",
    }
    stale = out_path.exists() and not required.issubset(
        set(pd.read_csv(out_path, nrows=0).columns)
    )
    if not out_path.exists() or stale:
        write_market_radar(hs=hs, processed_dir=processed_dir)
    return pd.read_csv(out_path)


def write_market_radar(
    in_path: Path = IN_PATH_DEFAULT,
    hs: str = HS_PRIMARY,
    processed_dir: Path = PROCESSED_DIR_DEFAULT,
) -> Path:
    rep = RadarReport(hs=hs)
    radar = build_market_radar(in_path, hs, processed_dir, rep)
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / OUT_NAME
    radar.to_csv(out_path, index=False)
    log.info(
        "Wrote %s (%d rows, %.1f KB)",
        out_path.name,
        len(radar),
        out_path.stat().st_size / 1024,
    )
    rep.log()
    return out_path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--in", dest="in_path", type=Path, default=IN_PATH_DEFAULT)
    p.add_argument("--hs", default=HS_PRIMARY)
    p.add_argument("--out-dir", type=Path, default=PROCESSED_DIR_DEFAULT)
    p.add_argument("--no-write", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.no_write:
        rep = RadarReport(hs=args.hs)
        radar = build_market_radar(args.in_path, args.hs, args.out_dir, rep)
        cols = [
            "dest_country",
            "india_share_pct",
            "realised_usd_per_kg",
            "price_vs_portfolio_pct",
            "recent_shift_pct",
            "fta_status",
            "pivot_score",
            "shift_flag",
        ]
        with pd.option_context("display.width", 160, "display.max_rows", 25):
            print(radar[cols].head(15).to_string(index=False))
        rep.log()
    else:
        write_market_radar(args.in_path, args.hs, args.out_dir)


if __name__ == "__main__":
    main()
