"""Product validation gate — v1 PRD FR-2, extended to the v2 product.

v1 halted the pipeline before the DB load if any of 20 data-quality
expectations failed: never ship a wrong number. v2 has no DB, but it has
something more dangerous — a screen that tells an exporter to move
rupees. So the same gate applies to the *product artifacts*: the price
spine, the market radar, world-import headroom, the hedge signal, and
the ₹ ROI. Every check is an invariant the product's credibility
depends on; any failure exits non-zero and writes the failures into
``data/processed/product_validation_report.json``.

Run it before a deploy or in the pipeline:

    python -m src.validate_product            # exit 1 if any check fails
    python -m src.validate_product --quiet
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.features.guar_price import (
    HS_PRIMARY,
    PROCESSED_DIR_DEFAULT,
    SANE_PRICE_BAND_USD,
    build_guar_price,
)
from src.features.market_radar import build_market_radar
from src.model.hedge_signal import compute_hedge_signal
from src.product.exporter_roi import compute_exporter_roi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("validate_product")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _checks() -> list[Check]:
    out: list[Check] = []

    def chk(name: str, cond: bool, detail: str) -> None:
        out.append(Check(name, bool(cond), detail))

    # ---- Price spine -----------------------------------------------------
    spine = build_guar_price()
    prim = spine[spine["hs_code"] == HS_PRIMARY].sort_values("period")
    lo, hi = SANE_PRICE_BAND_USD
    chk(
        "spine.coverage_72_months",
        len(prim) == 72,
        f"{len(prim)} primary-HS months (expect 72)",
    )
    chk(
        "spine.price_in_band",
        prim["price_usd_per_kg"].between(lo, hi).all(),
        f"all months within ${lo}-${hi}/kg post-repair",
    )
    chk(
        "spine.no_nan_price",
        prim["price_usd_per_kg"].notna().all(),
        "consumable price has no holes",
    )
    n_imp = int(prim["is_imputed"].sum())
    chk(
        "spine.imputation_sparing",
        1 <= n_imp <= 4,
        f"{n_imp} primary months imputed (expect 1-4: sparing, not rewriting)",
    )

    # ---- Market radar + headroom ----------------------------------------
    radar = build_market_radar()
    chk(
        "radar.shares_sum_100",
        abs(radar["india_share_pct"].sum() - 100.0) < 0.5,
        f"india_share_pct sums to {radar['india_share_pct'].sum():.2f}",
    )
    for c in ("world_import_usd", "india_share_of_market_pct", "addressable_usd"):
        chk(f"radar.has_{c}", c in radar.columns, f"headroom column {c} present")
    ranked = radar[radar["pivot_score"].notna()]
    chk(
        "radar.pivot_score_bounded",
        ranked["pivot_score"].between(0, 100).all(),
        "every pivot_score in [0,100]",
    )
    top = ranked.sort_values("pivot_score", ascending=False).iloc[0]
    chk(
        "radar.top_pivot_not_us",
        top["dest_iso"] != "USA",
        f"top pivot = {top['dest_country']} (must never be the over-exposed US)",
    )
    chk(
        "radar.top_pivot_has_headroom_data",
        top["world_import_usd"] > 0,
        "the recommended pivot has measured world-import data, not a gap",
    )
    us = radar[radar["dest_iso"] == "USA"]
    if len(us):
        share_mkt = float(us["india_share_of_market_pct"].fillna(0).iloc[0])
        chk(
            "radar.us_saturation_visible",
            share_mkt > 30,
            f"India = {share_mkt:.0f}% of US guar imports (saturation signal)",
        )

    # ---- Hedge signal (WHEN) --------------------------------------------
    sig = compute_hedge_signal()
    chk(
        "when.honest_label_present",
        "insufficient edge" in sig.price_direction_label,
        "the no-forecast integrity label is present",
    )
    chk(
        "when.trigger_valid",
        sig.trigger in ("LOCK_NOW", "NO_TRIGGER"),
        f"trigger = {sig.trigger}",
    )
    if sig.scenarios:
        by = {s["regime"]: s for s in sig.scenarios}
        col = next((v for k, v in by.items() if "collapse" in k.lower()), None)
        std = next((v for k, v in by.items() if "steady" in k.lower()), None)
        chk(
            "when.scenarios_monotonic",
            not (col and std)
            or col["hist_adverse_drawdown_pct"]
            >= std["hist_adverse_drawdown_pct"] - 1e-9,
            "a drilling collapse is not historically safer than steady",
        )

    # ---- ₹ ROI -----------------------------------------------------------
    roi = compute_exporter_roi()
    chk(
        "roi.downside_positive",
        roi.downside_inr_year > 0,
        f"downside ≈ ₹{roi.downside_inr_year / 1e5:.1f}L/yr",
    )
    chk(
        "roi.uplift_non_negative",
        roi.reroute_uplift_inr_year >= 0,
        f"re-route uplift ≈ ₹{roi.reroute_uplift_inr_year / 1e5:.1f}L/yr",
    )
    chk(
        "roi.uplift_only_on_real_premium",
        roi.reroute_uplift_inr_year == 0
        or roi.pivot_realised_usd_per_kg > roi.us_realised_usd_per_kg,
        "uplift is only ever booked when the pivot pays more than the US",
    )
    f80 = roi.fx_sensitivity[80.0]["downside_lakh"]
    f87 = roi.fx_sensitivity[87.0]["downside_lakh"]
    chk(
        "roi.fx_linear",
        abs((f87 / f80) - (87.0 / 80.0)) < 1e-6,
        "every ₹ figure scales linearly with FX (assumption explicit)",
    )
    return out


def run_all(processed_dir: Path = PROCESSED_DIR_DEFAULT) -> tuple[bool, list[Check]]:
    results = _checks()
    ok = all(c.passed for c in results)
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": ok,
        "n_checks": len(results),
        "n_failed": sum(1 for c in results if not c.passed),
        "checks": [asdict(c) for c in results],
    }
    processed_dir.mkdir(parents=True, exist_ok=True)
    (processed_dir / "product_validation_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return ok, results


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    ok, results = run_all()
    if not args.quiet:
        for c in results:
            log.info("  [%s] %s — %s", "PASS" if c.passed else "FAIL", c.name, c.detail)
    failed = [c for c in results if not c.passed]
    if failed:
        log.error(
            "PRODUCT VALIDATION FAILED — %d/%d checks. Refusing to ship a "
            "wrong number (FR-2).",
            len(failed),
            len(results),
        )
        sys.exit(1)
    log.info("PRODUCT VALIDATION PASSED — %d/%d checks.", len(results), len(results))


if __name__ == "__main__":
    main()
