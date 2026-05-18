"""Lightweight exogenous-regressor loaders — pandas only, no statsmodels.

These live OUTSIDE ``src.model.price_forecast`` on purpose. The hedge
signal, the ₹ ROI layer, and the Streamlit app all need the rig series
but must never transitively import statsmodels, or the slim Streamlit
Cloud deploy (streamlit/pandas/numpy/plotly) breaks. The single source
of truth for "rig count by month" / "monsoon by year" lives here; both
the SARIMAX model and the hedge signal consume it, never two ways.
"""

from __future__ import annotations

import pandas as pd

from src.features.guar_price import PROCESSED_DIR_DEFAULT

RIG_CSV = PROCESSED_DIR_DEFAULT / "rig_count_clean.csv"
MONSOON_CSV = PROCESSED_DIR_DEFAULT / "monsoon_clean.csv"


def rig_monthly() -> pd.Series:
    """Baker Hughes NA rig count, weekly → monthly mean, period-sorted."""
    rig = pd.read_csv(RIG_CSV, parse_dates=["week_start_date"])
    rig["month"] = rig["week_start_date"].dt.to_period("M").dt.to_timestamp()
    return rig.groupby("month")["rig_count"].mean().sort_index()


def monsoon_map() -> dict[int, float]:
    """Rajasthan monsoon LPA% keyed by year (annual source)."""
    mon = pd.read_csv(MONSOON_CSV)
    return dict(zip(mon["year"].astype(int), mon["lpa_pct"].astype(float), strict=False))
