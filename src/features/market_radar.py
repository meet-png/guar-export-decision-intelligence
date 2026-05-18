"""Pillar WHERE — the market-pivot radar (the heavier, defensible pillar).

Why this is the stronger pillar
-------------------------------
WHEN had to concede it cannot forecast price (honest backtest). WHERE
makes *no forecast*: it is structural analysis of where India's guar
business actually is, what each destination actually pays, and where it
is moving — facts, not predictions. That is exactly why it carries the
product's analytical weight.

The three buyer questions it answers (from the India-reporter spine)
--------------------------------------------------------------------
1. **"How exposed am I?"** — India ships ~35% of its guar to the US,
   which is now tariff-stressed. Concentration is quantified per market.
2. **"Which country pays me more?"** — realised $/kg differs ~2x across
   destinations (Japan ≈ $2.75 vs Netherlands ≈ $1.29 for the same HS).
   ``price_vs_portfolio_pct`` is the rupee-relevant headline.
3. **"Where should I push as the US closes?"** — a transparent
   ``pivot_score`` blending realised-price premium, recent momentum,
   diversification value (away from the US), and FTA/tariff friendliness.

Recurring radar, not a one-shot report
--------------------------------------
``shift_flag`` (SURGING / FADING / STABLE from last-12-mo vs prior-12-mo)
is the monitored signal that makes this a subscription, not a consulting
deliverable — re-runs flag a market turning before the exporter feels it.

Honest limitations (carried, not hidden)
----------------------------------------
* **India-reporter only.** This measures *India's* business per market
  and the price India realises there. It does NOT yet have each market's
  *total* world imports, so "India's share of that market / headroom" is
  a roadmap dimension needing a Comtrade mirror pull — flagged, not faked.
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
W_PRICE = 0.35  # realised-price premium vs the national portfolio
W_MOMENTUM = 0.25  # recent 12-mo vs prior 12-mo direction
W_DIVERSIFY = 0.20  # value of reducing single-market (US) dependence
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
    top_pivot: str = ""
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
            "  US concentration        : %.1f%% of India's guar FOB  "
            "(the exposure to diversify away from)",
            self.us_share_pct,
        )
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

    eligible = (radar["total_fob_usd"] >= MIN_TOTAL_FOB_USD) & (
        radar["n_active_months"] >= MIN_ACTIVE_MONTHS
    )
    r = radar[eligible].copy()
    report.n_ranked = len(r)

    # Transparent composite. Each component normalised to 0-1 by rank
    # across the eligible set, then a documented weighted sum.
    price_n = _pctrank(r["price_vs_portfolio_pct"])
    mom_n = _pctrank(r["recent_shift_pct"].fillna(r["recent_shift_pct"].min()))
    # diversification value: reward markets that are NOT the dominant one;
    # smaller existing share = more diversification headroom for the
    # exporter (and the US, the dominant one, scores 0 here by design).
    divers_n = 1.0 - _pctrank(r["india_share_pct"])
    fta_n = r["fta_status"].map(FTA_SCORE).astype(float)

    r["pivot_score"] = (
        W_PRICE * price_n + W_MOMENTUM * mom_n + W_DIVERSIFY * divers_n + W_FTA * fta_n
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
    report.us_share_pct = float(us["india_share_pct"].iloc[0]) if len(us) else 0.0
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
    required = {"dest_iso", "pivot_score", "price_vs_portfolio_pct"}
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
