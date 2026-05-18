"""Canonical monthly guar export price series — the product spine.

Both pillars of *Guar Export Decision Intelligence* stand on one number:
the price an Indian guar exporter actually realises per kg, by month.

* **WHEN to sell** forecasts this series and turns the forecast into a
  dated SELL / WAIT / LOCK call.
* **WHERE to sell** decomposes it by destination ("you earn $X/kg to
  Germany vs $Y/kg to the US").

So the series is built **once**, made trustworthy, tested, and frozen
here. If this number is wrong, every rupee figure the product shows an
exporter is wrong — which is why this module exists separately from the
generic ``clean`` step and is the first thing we hardened.

Two distinct data defects, two distinct fixes
---------------------------------------------
1. **One destination mis-reports in an otherwise good month.** Handled
   by computing the monthly price as the **quantity-weighted median of
   destination-level unit prices** instead of ``Σvalue / Σquantity``.
   A weighted median is unmoved by a single bad partner yet still
   volume-representative of where the cluster actually transacts.

2. **A whole month's quantity reporting is corrupt.** Real example in
   our data — **Oct 2021**: every destination prints ~``$7/kg`` because
   total reported quantity collapsed ~70% (4.8M kg vs ~16M trend) while
   value stayed normal. No weighted median can fix this — there is no
   good partner to lean on. Feeding ``$7.17`` into a forecast would make
   the product lie to an exporter. So a **month-quality gate** flags any
   month whose robust price leaves the sane band *or* whose volume
   collapses far below its local norm, marks it ``is_imputed=True``, and
   replaces only the price with a transparent linear interpolation. The
   raw value is **kept in an audit column** — we repair in the open,
   never silently (PRD FR-2 honesty).

The column the pillars consume is ``price_usd_per_kg`` (clean, gap-free,
no known lies). ``robust_price_raw_usd_per_kg`` and
``naive_price_usd_per_kg`` are retained so any reviewer can see exactly
what we changed and why.

Honest limitations (carried, not hidden)
----------------------------------------
* This is *realised export value*, not a spot or forward quote. UN
  Comtrade publishes with a ~2-3 month lag, so the series is a strategic
  monthly signal — the product's real-time impact is the **decision it
  drives** (lock a forward contract now vs wait), not tick prices.
* HS **130232** (refined guar gum) is the product's primary series.
  **130239** (other mucilages/thickeners) is a thinner, noisier bucket
  built alongside but never blended in — mixing two products fabricates
  a price that is no one's reality. Treat 130239 as lower-confidence.

CLI
---
    python -m src.features.guar_price                 # build, write CSV
    python -m src.features.guar_price --no-write      # print primary only
    python -m src.features.guar_price --in PATH --out-dir DIR

Output
------
    data/processed/guar_price_monthly.csv
        tidy: period, hs_code, price_usd_per_kg, is_imputed,
        robust_price_raw_usd_per_kg, naive_price_usd_per_kg,
        n_destinations, total_qty_kg, total_fob_usd
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR_DEFAULT = PROJECT_ROOT / "data" / "processed"
IN_PATH_DEFAULT = PROCESSED_DIR_DEFAULT / "exports_clean.parquet"
OUT_NAME = "guar_price_monthly.csv"

# HS codes that *are* guar for this product.
HS_PRIMARY = "130232"  # refined guar gum — the product's default series
HS_SECONDARY = "130239"  # other mucilages/thickeners — kept, never blended
GUAR_HS_CODES = (HS_PRIMARY, HS_SECONDARY)

# A realised guar export price outside this band is almost certainly a
# reporting artifact, not a market move (research: real-world ~$1.5-$2.5/kg;
# we keep generous slack so genuine swings are never flagged).
SANE_PRICE_BAND_USD = (0.5, 5.0)

# Month-quality gate for whole-month quantity corruption: a month whose
# total volume falls below this fraction of its *local* (centred 12-month)
# median is treated as a reporting failure, not a market event. 0.35 catches
# the ~70% Oct-2021 collapse while leaving real seasonal troughs (which
# retain ~55-85% of local volume in this data) untouched.
QTY_COLLAPSE_FRAC = 0.35
LOCAL_WINDOW_MONTHS = 12

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("guar_price")


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class GuarPriceReport:
    rows_in: int = 0
    rows_after_guar_filter: int = 0
    rows_dropped_outlier: int = 0
    rows_dropped_nonpositive: int = 0
    months_built: int = 0
    months_imputed_primary: int = 0
    months_imputed_secondary: int = 0
    months_out_of_band_after: int = 0
    naive_robust_max_gap_pct: float = 0.0

    def log(self) -> None:
        log.info("=" * 70)
        log.info("Guar price series summary:")
        log.info("  rows ingested              : %d", self.rows_in)
        log.info("  rows after guar filter     : %d", self.rows_after_guar_filter)
        log.info("  rows dropped (outlier flag): %d", self.rows_dropped_outlier)
        log.info("  rows dropped (fob/qty <= 0): %d", self.rows_dropped_nonpositive)
        log.info("  months built (all HS)      : %d", self.months_built)
        log.info(
            "  months imputed  %s: %d   %s: %d",
            HS_PRIMARY,
            self.months_imputed_primary,
            HS_SECONDARY,
            self.months_imputed_secondary,
        )
        log.info(
            "  max naive-vs-robust gap    : %.1f%%  (where the fragile "
            "Σv/Σq ratio would have misled)",
            self.naive_robust_max_gap_pct,
        )
        if self.months_out_of_band_after:
            log.warning(
                "  %d month(s) STILL outside $%.1f-$%.1f/kg after repair — "
                "investigate before forecasting",
                self.months_out_of_band_after,
                *SANE_PRICE_BAND_USD,
            )
        else:
            log.info("  post-repair: all months inside the sane band ✓")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """Quantity-weighted median: the unit price at which cumulative traded
    volume first reaches half of the month's total volume.

    Robust to a single destination mis-reporting value or quantity.
    Returns ``nan`` for empty / zero-weight input rather than raising, so
    one bad month never aborts the whole series build.
    """
    if values.size == 0:
        return float("nan")
    total = weights.sum()
    if not np.isfinite(total) or total <= 0:
        return float("nan")
    order = np.argsort(values, kind="mergesort")
    v = values[order]
    cumw = np.cumsum(weights[order])
    return float(v[np.searchsorted(cumw, total / 2.0, side="left")])


def _load_clean(in_path: Path) -> pd.DataFrame:
    if in_path.exists():
        return pd.read_parquet(in_path)
    csv_fallback = in_path.with_suffix(".csv")
    if csv_fallback.exists():
        return pd.read_csv(csv_fallback, parse_dates=["shipment_date"])
    raise FileNotFoundError(
        f"Neither {in_path} nor {csv_fallback} exists — run "
        f"`python -m src.transform.clean` first."
    )


def _gate_and_impute(sub: pd.DataFrame) -> pd.DataFrame:
    """Per-HS month-quality gate. ``sub`` is one HS code's monthly rows,
    period-sorted. Flags structurally corrupt months and replaces only
    their price with a transparent linear interpolation.
    """
    sub = sub.sort_values("period").reset_index(drop=True)

    local_qty = (
        sub["total_qty_kg"]
        .rolling(LOCAL_WINDOW_MONTHS, min_periods=6, center=True)
        .median()
    )
    price_oob = ~sub["robust_price_raw_usd_per_kg"].between(*SANE_PRICE_BAND_USD)
    qty_collapsed = sub["total_qty_kg"] < (QTY_COLLAPSE_FRAC * local_qty)

    sub["is_imputed"] = (price_oob | qty_collapsed).fillna(False)
    kept = sub["robust_price_raw_usd_per_kg"].where(~sub["is_imputed"])
    sub["price_usd_per_kg"] = kept.interpolate(
        method="linear", limit_direction="both"
    ).round(4)
    return sub


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def build_guar_price(
    in_path: Path = IN_PATH_DEFAULT,
    report: GuarPriceReport | None = None,
) -> pd.DataFrame:
    """Build the canonical monthly guar price series for every guar HS
    code. Returns a tidy frame; does not write to disk.
    """
    report = report or GuarPriceReport()
    df = _load_clean(in_path)
    report.rows_in = len(df)

    df["hs_code"] = df["hs_code"].astype(str)
    g = df[df["hs_code"].isin(GUAR_HS_CODES)].copy()

    # Defensive: the cleaned set is India-as-reporter, but never assume.
    if "reporter_country_name" in g.columns:
        g = g[g["reporter_country_name"].astype(str).str.strip() == "India"].copy()
    report.rows_after_guar_filter = len(g)

    # Drop the per-HS 99th-pct outliers `clean` already flagged ...
    before = len(g)
    g = g[~g["is_outlier"].astype(bool)].copy()
    report.rows_dropped_outlier = before - len(g)

    # ... and anything non-positive that would poison a per-row price.
    before = len(g)
    g = g[(g["fob_usd"] > 0) & (g["quantity_kg"] > 0)].copy()
    report.rows_dropped_nonpositive = before - len(g)

    g["_period"] = pd.to_datetime(g["shipment_date"]).dt.to_period("M")
    g["_unit"] = g["fob_usd"] / g["quantity_kg"]

    rows: list[dict] = []
    for (period, hs), s in g.groupby(["_period", "hs_code"], sort=True):
        vals = s["_unit"].to_numpy(dtype=float)
        wts = s["quantity_kg"].to_numpy(dtype=float)
        total_qty = float(s["quantity_kg"].sum())
        total_fob = float(s["fob_usd"].sum())
        rows.append(
            {
                "period": period.to_timestamp(),
                "hs_code": hs,
                "robust_price_raw_usd_per_kg": round(_weighted_median(vals, wts), 4),
                "naive_price_usd_per_kg": round(
                    total_fob / total_qty if total_qty > 0 else float("nan"), 4
                ),
                "n_destinations": int(s["dest_iso_alpha3"].nunique()),
                "total_qty_kg": round(total_qty, 3),
                "total_fob_usd": round(total_fob, 2),
            }
        )

    base = pd.DataFrame(rows)
    series = (
        pd.concat(
            [_gate_and_impute(sub) for _, sub in base.groupby("hs_code")],
            ignore_index=True,
        )
        .sort_values(["hs_code", "period"])
        .reset_index(drop=True)
    )
    series = series[
        [
            "period",
            "hs_code",
            "price_usd_per_kg",
            "is_imputed",
            "robust_price_raw_usd_per_kg",
            "naive_price_usd_per_kg",
            "n_destinations",
            "total_qty_kg",
            "total_fob_usd",
        ]
    ]

    report.months_built = len(series)
    prim = series[series["hs_code"] == HS_PRIMARY]
    sec = series[series["hs_code"] == HS_SECONDARY]
    report.months_imputed_primary = int(prim["is_imputed"].sum())
    report.months_imputed_secondary = int(sec["is_imputed"].sum())
    report.months_out_of_band_after = int(
        (~series["price_usd_per_kg"].between(*SANE_PRICE_BAND_USD)).sum()
    )
    gap = (
        (series["naive_price_usd_per_kg"] - series["robust_price_raw_usd_per_kg"]).abs()
        / series["robust_price_raw_usd_per_kg"]
    ).replace([np.inf, -np.inf], np.nan)
    report.naive_robust_max_gap_pct = round(float(gap.max() * 100), 1)
    return series


def load_guar_price_series(
    hs: str = HS_PRIMARY,
    processed_dir: Path = PROCESSED_DIR_DEFAULT,
) -> pd.DataFrame:
    """The importable API both pillars use. Reads the built CSV (building
    it first if absent) and returns one HS code's monthly series — so no
    pillar ever re-derives the price its own way.
    """
    required = {"period", "hs_code", "price_usd_per_kg", "is_imputed"}
    out_path = processed_dir / OUT_NAME
    stale = out_path.exists() and not required.issubset(
        set(pd.read_csv(out_path, nrows=0).columns)
    )
    if not out_path.exists() or stale:
        # Absent OR written by an older schema — never let a pillar read a
        # stale spine. Rebuild rather than silently serve wrong numbers.
        write_guar_price(processed_dir=processed_dir)
    s = pd.read_csv(out_path, parse_dates=["period"])
    s = s[s["hs_code"].astype(str) == str(hs)].copy()
    if s.empty:
        raise ValueError(f"No guar price rows for HS {hs} in {out_path}")
    return s.sort_values("period").reset_index(drop=True)


def write_guar_price(
    in_path: Path = IN_PATH_DEFAULT,
    processed_dir: Path = PROCESSED_DIR_DEFAULT,
) -> Path:
    report = GuarPriceReport()
    series = build_guar_price(in_path, report)
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / OUT_NAME
    series.to_csv(out_path, index=False)
    log.info(
        "Wrote %s (%d rows, %.1f KB)",
        out_path.name,
        len(series),
        out_path.stat().st_size / 1024,
    )
    report.log()
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--in", dest="in_path", type=Path, default=IN_PATH_DEFAULT)
    p.add_argument("--out-dir", type=Path, default=PROCESSED_DIR_DEFAULT)
    p.add_argument(
        "--no-write",
        action="store_true",
        help="print the primary-HS series and diagnostics, write nothing",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.no_write:
        rep = GuarPriceReport()
        series = build_guar_price(args.in_path, rep)
        primary = series[series["hs_code"] == HS_PRIMARY]
        with pd.option_context("display.width", 160, "display.max_rows", 80):
            print(primary.to_string(index=False))
        rep.log()
    else:
        write_guar_price(args.in_path, args.out_dir)


if __name__ == "__main__":
    main()
