"""UN Comtrade — each country's TOTAL guar imports from the world.

The market radar started India-reporter only: it knew where *India* ships
and what India realises, but not how big each market actually is or how
penetrated India already is there. That made "where to pivot" descriptive,
not strategic. This module closes that gap by pulling, per importing
country, total guar (HS 130232) imports **from the world** (flowCode=M,
partnerCode=0 = World, reporterCode omitted = all reporters). Combined
with India's own export flow it yields the strategic number:

    india_share_of_market = India→country  /  country's world imports

A low share into a large, growing market = real untapped headroom.

Design constraints (same as the India client)
---------------------------------------------
* PRD FR-1: raw files are date-stamped and never overwritten. Prefixed
  ``worldimp_`` (NOT ``comtrade_``) so ``transform/clean.py``'s
  ``comtrade_*.json`` glob can never ingest them by accident.
* Free tier 500 calls/day. One call per year (~6 total) — trivial.
* Auth / retry / backoff reuse the proven India client's helpers.

CLI
---
    python -m src.ingest.comtrade_world_imports                # 2019-2024
    python -m src.ingest.comtrade_world_imports --year 2024
    python -m src.ingest.comtrade_world_imports --dry-run

Output
------
    data/raw/worldimp_{hs}_{year}_{YYYYMMDD}.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.ingest.comtrade_api import (
    COMTRADE_BASE_URL,
    ComtradeAuthError,
    ComtradeRateLimitError,
    ComtradeServerError,
    _api_key,
    _build_monthly_period,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"

GUAR_HS = "130232"
DEFAULT_YEARS = range(2019, 2025)
WORLD_PARTNER = "0"

log = logging.getLogger("comtrade_world_imports")


@retry(
    retry=retry_if_exception_type((ComtradeServerError, requests.RequestException)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    before_sleep=before_sleep_log(log, logging.WARNING),
)
def _call(hs_code: str, year: int, timeout: float = 60.0) -> dict:
    """All-reporter, World-partner, imports — one year of monthly rows."""
    params = {
        "freqCode": "M",
        "clCode": "HS",
        "period": _build_monthly_period(year),
        # reporterCode omitted → Comtrade returns every importing country.
        "cmdCode": hs_code,
        "flowCode": "M",  # imports
        "partnerCode": WORLD_PARTNER,  # the single World aggregate per reporter
        "maxRecords": "100000",
        "format": "JSON",
        "includeDesc": "true",
    }
    headers = {"Ocp-Apim-Subscription-Key": _api_key()}
    log.info("GET Comtrade world imports — HS %s, year %d", hs_code, year)
    resp = requests.get(
        COMTRADE_BASE_URL, params=params, headers=headers, timeout=timeout
    )
    if resp.status_code in (401, 403):
        raise ComtradeAuthError(f"Comtrade {resp.status_code} — check key.")
    if resp.status_code == 429:
        raise ComtradeRateLimitError("Comtrade 429 — daily budget exhausted.")
    if 500 <= resp.status_code < 600:
        raise ComtradeServerError(f"Comtrade {resp.status_code}: {resp.text[:200]}")
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as exc:
        raise ComtradeServerError(f"Non-JSON: {resp.text[:200]}") from exc


def _out_path(hs_code: str, year: int, today: datetime | None = None) -> Path:
    stamp = (today or datetime.utcnow()).strftime("%Y%m%d")
    return RAW_DIR / f"worldimp_{hs_code}_{year}_{stamp}.json"


def fetch_year(hs_code: str, year: int, *, dry_run: bool = False) -> int:
    """Pull one year; return row count. Never overwrites (FR-1)."""
    payload = _call(hs_code, year)
    rows = len(payload.get("data") or [])
    if dry_run:
        log.info("  [dry-run] HS %s %d — %d rows, not written", hs_code, year, rows)
        return rows
    path = _out_path(hs_code, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("  wrote %s (%d rows)", path.name, rows)
    return rows


def fetch_all(
    hs_code: str = GUAR_HS, years=DEFAULT_YEARS, *, dry_run: bool = False
) -> int:
    total = 0
    for y in years:
        total += fetch_year(hs_code, y, dry_run=dry_run)
    log.info("Done — %d total rows across %s", total, list(years))
    return total


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--hs-code", default=GUAR_HS)
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    a = _parse_args()
    years = [a.year] if a.year else DEFAULT_YEARS
    fetch_all(a.hs_code, years, dry_run=a.dry_run)


if __name__ == "__main__":
    main()
