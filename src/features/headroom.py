"""Market headroom — each country's TOTAL guar imports from the world.

Parses the date-stamped ``worldimp_{hs}_{year}_*.json`` raw pulls
(``src.ingest.comtrade_world_imports``) into one number per destination:
how much guar that country buys from the *whole world* per year. The
radar joins this to India's own export flow to get the strategic figure
the product was previously missing —

    india_share_of_market = India→country  /  country's world imports

A *small* share of a *large* market = real, quantified untapped headroom,
not just "a country India happens to ship little to".

FR-1: newest date-stamp per year wins; raw files never overwritten.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR_DEFAULT = PROJECT_ROOT / "data" / "raw"
GUAR_HS = "130232"

# Comtrade reporter codes that are groupings, not single countries.
_NON_COUNTRY_ISO = {"WLD", "_X", "S19", "X1", "EUR", "ASA", "R20", "F19"}

log = logging.getLogger("headroom")


def _latest_per_year(raw_dir: Path, hs_code: str) -> list[Path]:
    """One path per year — the most recent date-stamped pull (FR-1)."""
    best: dict[str, Path] = {}
    for p in sorted(raw_dir.glob(f"worldimp_{hs_code}_*.json")):
        parts = p.stem.split("_")  # worldimp_{hs}_{year}_{stamp}
        if len(parts) < 4:
            continue
        year = parts[2]
        best[year] = p  # alphabetical sort → newest stamp last → wins
    return list(best.values())


def load_world_imports(
    hs_code: str = GUAR_HS, raw_dir: Path = RAW_DIR_DEFAULT
) -> pd.DataFrame:
    """Per-country world guar imports. Columns: dest_iso, world_import_usd
    (window total), world_import_usd_last (most recent year). Empty frame
    if no raw pulls exist yet (radar then degrades gracefully)."""
    files = _latest_per_year(raw_dir, hs_code)
    if not files:
        log.warning(
            "no worldimp_%s_*.json in %s — headroom unavailable", hs_code, raw_dir
        )
        return pd.DataFrame(
            columns=["dest_iso", "world_import_usd", "world_import_usd_last"]
        )

    rows: list[dict] = []
    for path in files:
        year = path.stem.split("_")[2]
        payload = json.loads(path.read_text(encoding="utf-8"))
        for r in payload.get("data") or []:
            iso = (r.get("reporterISO") or "").strip().upper()
            val = r.get("primaryValue")
            if (
                len(iso) == 3
                and iso.isalpha()
                and iso not in _NON_COUNTRY_ISO
                and val is not None
            ):
                rows.append({"dest_iso": iso, "year": int(year), "usd": float(val)})

    if not rows:
        return pd.DataFrame(
            columns=["dest_iso", "world_import_usd", "world_import_usd_last"]
        )

    df = pd.DataFrame(rows)
    by_cy = df.groupby(["dest_iso", "year"], as_index=False)["usd"].sum()
    last_year = by_cy["year"].max()
    out = (
        by_cy.groupby("dest_iso")["usd"].sum().rename("world_import_usd").reset_index()
    )
    last = (
        by_cy[by_cy["year"] == last_year]
        .set_index("dest_iso")["usd"]
        .rename("world_import_usd_last")
    )
    out = out.merge(last, on="dest_iso", how="left")
    out["world_import_usd"] = out["world_import_usd"].round(2)
    out["world_import_usd_last"] = out["world_import_usd_last"].round(2)
    return out.sort_values("world_import_usd", ascending=False).reset_index(drop=True)
