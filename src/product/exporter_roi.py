"""The rupee number — both pillars, on one exporter's own volume.

This is the line a businessman buys on:

    "Locking now caps ≈ ₹X of demand-driven downside on your 600 t, and
     re-routing 20% of your US volume to <pivot> is ≈ ₹Y/year more."

Design honesty (every figure is defensible or it is worthless here)
-------------------------------------------------------------------
* **The exporter is SIMULATED.** Per the data decision (public core +
  simulated private layer), :class:`ExporterProfile` is a clearly
  labelled synthetic Jodhpur guar SME, not a real client's books. The
  product *mechanics* are real; the tonnage/mix are a stand-in.
* **WHEN value = loss *capped*, not loss predicted.** We do not say
  "you will lose ₹X" (the backtest forbids that). We say "₹X of
  demand-driven downside is at risk under a historical bad-quarter
  analogue; locking a forward caps it." Risk reduction, framed as risk.
* **WHERE value = *addressable* uplift, pre-switching-cost.** Re-routing
  volume is not free or instant (relationships, logistics). The figure
  is the gross price differential on a conservative re-route share,
  explicitly labelled as the prize a pilot conversation then nets down.
* **FX is explicit and single-sourced.** v1 hardcoded ``INR_PER_USD``
  in three notebooks. Here there is ONE rate, dated, env-overridable,
  and every rupee figure is shown to scale linearly with it.

CLI
---
    python -m src.product.exporter_roi               # demo profile
    python -m src.product.exporter_roi --tonnes 1000 --us-share 0.55
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, field

from src.features.guar_price import HS_PRIMARY, load_guar_price_series
from src.features.market_radar import load_market_radar
from src.model.hedge_signal import compute_hedge_signal

# ---- The ONE FX rate. Dated, single-sourced, env-overridable. ----------
# Default = RBI reference annual average, FY2024 (~₹83/USD). Override with
# USD_INR_RATE in the environment. Every ₹ figure scales linearly with it
# (a 5% INR move = 5% on the rupee number) — we show that sensitivity.
USD_INR = float(os.getenv("USD_INR_RATE", "83.0"))
FX_SENSITIVITY_RATES = (80.0, 83.0, 85.0, 87.0)
KG_PER_TONNE = 1000

# Conservative default: how much of the over-exposed US volume a pilot
# would realistically test re-routing in year one. Tunable; deliberately
# modest so the headline is credible, not a fantasy ceiling.
DEFAULT_REROUTE_SHARE = 0.20

# Re-route only to a market India ALREADY ships to materially. A high
# momentum score on a tiny market (e.g. Nigeria at 0.1% share) is not a
# defensible place to send 50+ tonnes — it has no proven absorptive
# capacity and the exporter has no relationship there. Requiring an
# existing real channel makes the rupee headline survive scrutiny.
MIN_PIVOT_SHARE_PCT = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("exporter_roi")


@dataclass
class ExporterProfile:
    """A SIMULATED guar SME. Mechanics real, books synthetic."""

    name: str = "Simulated Jodhpur guar exporter (demo)"
    annual_tonnes: float = 600.0
    # Destination mix — deliberately US-heavy, as many cluster SMEs are
    # buyer-concentrated. Shares need not be exhaustive; the remainder is
    # treated as realised at the national price.
    destination_mix: dict = field(
        default_factory=lambda: {
            "USA": 0.45,
            "DEU": 0.15,
            "CHN": 0.12,
            "ARE": 0.08,
        }
    )

    @property
    def annual_kg(self) -> float:
        return self.annual_tonnes * KG_PER_TONNE

    def us_share(self) -> float:
        return float(self.destination_mix.get("USA", 0.0))


@dataclass
class ExporterROI:
    profile_name: str
    annual_tonnes: float
    usd_inr: float
    # WHEN — downside capped
    when_trigger: str
    when_label: str
    downside_usd_per_kg: float
    downside_inr_year: float
    # WHERE — addressable uplift
    current_blended_usd_per_kg: float
    pivot_country: str
    pivot_realised_usd_per_kg: float
    us_realised_usd_per_kg: float
    reroute_share: float
    reroute_uplift_inr_year: float
    fx_sensitivity: dict = field(default_factory=dict)

    def log(self) -> None:
        log.info("=" * 70)
        log.info("EXPORTER RUPEE IMPACT — %s", self.profile_name)
        log.info(
            "  volume %.0f t/yr   FX ₹%.0f/USD (override USD_INR_RATE)",
            self.annual_tonnes,
            self.usd_inr,
        )
        log.info("  ---  WHEN (risk capped, not predicted)  ---")
        log.info("  %s", self.when_label)
        log.info(
            "  trigger %s — downside at risk ≈ $%.3f/kg  ≈  ₹%.1f lakh/yr",
            self.when_trigger,
            self.downside_usd_per_kg,
            self.downside_inr_year / 1e5,
        )
        log.info("  ---  WHERE (addressable uplift, pre-switching-cost)  ---")
        log.info(
            "  current blended realised ≈ $%.3f/kg; US ≈ $%.3f/kg",
            self.current_blended_usd_per_kg,
            self.us_realised_usd_per_kg,
        )
        log.info(
            "  re-route %.0f%% of US volume → %s ($%.3f/kg)  ≈  ₹%.1f lakh/yr",
            self.reroute_share * 100,
            self.pivot_country,
            self.pivot_realised_usd_per_kg,
            self.reroute_uplift_inr_year / 1e5,
        )
        log.info("  ---  FX sensitivity (linear)  ---")
        for rate, vals in self.fx_sensitivity.items():
            log.info(
                "  ₹%.0f/USD : downside ₹%.1fL  | uplift ₹%.1fL",
                rate,
                vals["downside_lakh"],
                vals["uplift_lakh"],
            )


def _realised_lookup(radar, national: float) -> dict:
    by_iso = dict(zip(radar["dest_iso"], radar["realised_usd_per_kg"], strict=False))
    by_iso["_NATIONAL"] = national
    return by_iso


def _pick_pivot(radar, exclude_iso: set[str]):
    """The credible re-route target: a market that (a) India already
    ships to materially (proven absorptive capacity + an existing
    relationship → low switching friction), (b) pays a real price
    premium, and (c) is not the over-exposed US. Among those, take the
    highest realised price — the ROI is about rupees per kg, so the
    money differential, not raw momentum, decides."""
    cand = radar[
        radar["pivot_score"].notna()
        & (radar["price_vs_portfolio_pct"] > 0)
        & (radar["india_share_pct"] >= MIN_PIVOT_SHARE_PCT)
        & (~radar["dest_iso"].isin(exclude_iso))
    ].sort_values("realised_usd_per_kg", ascending=False)
    if cand.empty:
        # Relaxed fallback: drop the materiality floor but never the
        # positive-premium / not-US guards.
        cand = radar[
            radar["pivot_score"].notna()
            & (radar["price_vs_portfolio_pct"] > 0)
            & (~radar["dest_iso"].isin(exclude_iso))
        ].sort_values("realised_usd_per_kg", ascending=False)
    return cand.iloc[0]


def compute_exporter_roi(
    profile: ExporterProfile | None = None,
    hs: str = HS_PRIMARY,
    reroute_share: float = DEFAULT_REROUTE_SHARE,
    usd_inr: float = USD_INR,
) -> ExporterROI:
    profile = profile or ExporterProfile()
    radar = load_market_radar(hs=hs)
    national = float(load_guar_price_series(hs=hs)["price_usd_per_kg"].mean())
    realised = _realised_lookup(radar, national)

    # current blended realised price across the exporter's mix; the
    # unspecified remainder realises at the national price.
    mix = profile.destination_mix
    spec = sum(mix.values())
    blended = (
        sum(share * realised.get(iso, national) for iso, share in mix.items())
        + max(0.0, 1.0 - spec) * national
    )
    us_realised = float(realised.get("USA", national))

    # ---- WHEN: downside capped on this volume ----
    sig = compute_hedge_signal(hs=hs)
    downside_inr = sig.downside_usd_per_kg * profile.annual_kg * usd_inr

    # ---- WHERE: addressable re-route uplift ----
    pivot = _pick_pivot(radar, exclude_iso={"USA"} | set(mix.keys()))
    pivot_price = float(pivot["realised_usd_per_kg"])
    uplift_usd_kg = max(0.0, pivot_price - us_realised)
    reroute_kg = profile.us_share() * profile.annual_kg * reroute_share
    uplift_inr = uplift_usd_kg * reroute_kg * usd_inr

    fx = {
        rate: {
            "downside_lakh": sig.downside_usd_per_kg * profile.annual_kg * rate / 1e5,
            "uplift_lakh": uplift_usd_kg * reroute_kg * rate / 1e5,
        }
        for rate in FX_SENSITIVITY_RATES
    }

    return ExporterROI(
        profile_name=profile.name,
        annual_tonnes=profile.annual_tonnes,
        usd_inr=usd_inr,
        when_trigger=sig.trigger,
        when_label=sig.price_direction_label,
        downside_usd_per_kg=sig.downside_usd_per_kg,
        downside_inr_year=round(downside_inr, 2),
        current_blended_usd_per_kg=round(blended, 4),
        pivot_country=str(pivot["dest_country"]),
        pivot_realised_usd_per_kg=round(pivot_price, 4),
        us_realised_usd_per_kg=round(us_realised, 4),
        reroute_share=reroute_share,
        reroute_uplift_inr_year=round(uplift_inr, 2),
        fx_sensitivity=fx,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--tonnes", type=float, default=None)
    p.add_argument("--us-share", type=float, default=None)
    p.add_argument("--reroute-share", type=float, default=DEFAULT_REROUTE_SHARE)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    prof = ExporterProfile()
    if args.tonnes:
        prof.annual_tonnes = args.tonnes
    if args.us_share is not None:
        prof.destination_mix["USA"] = args.us_share
    compute_exporter_roi(prof, reroute_share=args.reroute_share).log()


if __name__ == "__main__":
    main()
