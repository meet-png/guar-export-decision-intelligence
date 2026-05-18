"""Phase 1 gate — is the Comtrade-derived guar export unit-value a usable price proxy?

Reads the canonical processed dataset and characterises the guar price series we
intend to forecast and act on. We must answer, before building anything:
  1. What columns/grain do we actually have?
  2. For guar (HS 130232 refined, 130239 other gums) with India as reporter:
     - monthly coverage 2019-2024 (gaps?)
     - the unit-value series (value / quantity) magnitude & units
     - does it land in the real-world band (~$1.5-$2.5/kg in 2024)?
     - how noisy is it (month-to-month %), and per-destination spread
This script only prints; it writes nothing. Throwaway-safe, kept for provenance.
"""

from pathlib import Path
import sys
import pandas as pd

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 40)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "processed" / "exports_clean.parquet"

if not PARQUET.exists():
    sys.exit(f"missing {PARQUET}")

df = (
    pd.read_csv(ROOT / "data" / "processed" / "exports_clean.csv")
    if not PARQUET.exists()
    else pd.read_parquet(PARQUET)
)

print("=" * 70)
print(f"rows={len(df):,}  cols={len(df.columns)}")
print("-- dtypes --")
print(df.dtypes)
print("-- head(3) --")
print(df.head(3).to_string())

# --- locate the columns we need, defensively (schema may vary) ---
cols = {c.lower(): c for c in df.columns}


def pick(*cands):
    for c in cands:
        if c in cols:
            return cols[c]
    return None


hs_col = pick("hs_code", "cmd_code", "cmdcode", "commodity_code", "hs")
val_col = pick(
    "fob_usd",
    "trade_value_usd",
    "trade_value",
    "primary_value",
    "primaryvalue",
    "value_usd",
    "value",
)
qty_col = pick(
    "quantity_kg",
    "qty",
    "quantity",
    "net_weight_kg",
    "netweight",
    "net_weight",
    "qty_kg",
    "weight_kg",
)
prc_col = pick("unit_price_usd", "unit_price", "unit_value", "price_usd")
part_col = pick(
    "dest_iso_alpha3",
    "dest_country_name",
    "partner_iso3",
    "partner",
    "partner_country",
    "partner_name",
    "partner_iso",
)
date_col = pick("shipment_date", "period", "date", "year_month", "ym")
yr_col = pick("year", "yr", "ref_year")
mo_col = pick("month", "mo", "ref_month")
flow_col = pick("flow", "flow_desc", "trade_flow", "flowcode")

print("\n-- resolved columns --")
for k, v in dict(
    hs=hs_col,
    value=val_col,
    qty=qty_col,
    price=prc_col,
    partner=part_col,
    date=date_col,
    year=yr_col,
    month=mo_col,
    flow=flow_col,
).items():
    print(f"  {k:8} -> {v}")

if hs_col:
    print("\n-- HS code distribution --")
    print(df[hs_col].astype(str).value_counts())
if flow_col:
    print("\n-- flow distribution --")
    print(df[flow_col].astype(str).value_counts())

# --- guar slice ---
if hs_col:
    g = df[df[hs_col].astype(str).str.startswith(("130232", "130239"))].copy()
    print(f"\n=== GUAR slice: {len(g):,} rows ===")
    if flow_col:
        g = g[g[flow_col].astype(str).str.lower().str.contains("export|x|2", na=False)]
        print(f"after export-flow filter: {len(g):,} rows")

    # build a period
    if date_col:
        g["_period"] = pd.to_datetime(g[date_col].astype(str), errors="coerce")
    elif yr_col and mo_col:
        g["_period"] = pd.to_datetime(
            g[yr_col].astype(str) + "-" + g[mo_col].astype(str).str.zfill(2) + "-01",
            errors="coerce",
        )
    else:
        g["_period"] = pd.NaT

    if val_col and qty_col:
        agg = (
            g.dropna(subset=["_period"])
            .groupby("_period")
            .agg(value=(val_col, "sum"), qty=(qty_col, "sum"))
            .reset_index()
        )
        agg["unit_usd_per_kg"] = agg["value"] / agg["qty"]
        print(
            f"\n-- monthly guar export unit-value (value/qty), n={len(agg)} months --"
        )
        print(f"   coverage: {agg['_period'].min()}  ->  {agg['_period'].max()}")
        # expected ~72 months for 2019-2024
        full = pd.period_range("2019-01", "2024-12", freq="M")
        have = set(agg["_period"].dt.to_period("M"))
        missing = [str(p) for p in full if p not in have]
        print(
            f"   months present {len(have)}/72  missing {len(missing)}: "
            f"{missing[:12]}{'...' if len(missing) > 12 else ''}"
        )
        print(agg.to_string(index=False))
        print("\n-- describe(unit_usd_per_kg) --")
        print(agg["unit_usd_per_kg"].describe())
        agg["pct_chg"] = agg["unit_usd_per_kg"].pct_change() * 100
        print(
            f"\n   month-to-month %chg: mean|abs|={agg['pct_chg'].abs().mean():.1f}%  "
            f"max|abs|={agg['pct_chg'].abs().max():.1f}%"
        )
        y24 = agg[agg["_period"].dt.year == 2024]["unit_usd_per_kg"]
        if len(y24):
            print(
                f"   2024 band: ${y24.min():.2f}-${y24.max():.2f}/kg "
                f"(research says real-world ~$1.5-$2.5/kg)"
            )
    else:
        print("!! cannot build unit value — missing value/qty columns")
else:
    print("!! no HS column resolved — inspect dtypes above")
