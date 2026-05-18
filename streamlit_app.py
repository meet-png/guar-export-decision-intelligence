"""Guar Export Decision Intelligence — the one-screen product.

A single decision screen for a guar exporter:

  * WHEN  — should I lock a forward contract now? (honest risk trigger;
            we openly do NOT forecast price)
  * WHERE — I'm over-exposed to the US; which market pays more and is
            growing?
  * ₹     — what is that worth on MY tonnage?

The UI is a thin view over the tested ``src`` modules — no analytics are
re-implemented here, so the screen can never disagree with the engine.
``assemble_view`` holds all data prep so it is unit-testable without a
Streamlit runtime. Reads only committed CSVs + pandas/numpy/plotly, so
the slim Streamlit Cloud deploy stays intact (no statsmodels on this
path — verified by test).

Run locally:   streamlit run streamlit_app.py
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import plotly
import plotly.graph_objects as go
import streamlit as st

from src.features.guar_price import load_guar_price_series
from src.features.market_radar import load_market_radar
from src.model.hedge_signal import compute_hedge_signal
from src.product.exporter_roi import ExporterProfile, compute_exporter_roi


# ---------------------------------------------------------------------------
# Data prep — pure, testable, no Streamlit
# ---------------------------------------------------------------------------


@dataclass
class View:
    roi: object
    sig: object
    radar: pd.DataFrame
    spine: pd.DataFrame
    us_share_pct: float
    top_premium: pd.DataFrame
    shifts: pd.DataFrame


def assemble_view(
    tonnes: float, us_share: float, reroute_share: float, usd_inr: float
) -> View:
    """Everything the screen renders, computed from the tested engine."""
    profile = ExporterProfile(annual_tonnes=tonnes)
    profile.destination_mix["USA"] = us_share
    roi = compute_exporter_roi(profile, reroute_share=reroute_share, usd_inr=usd_inr)
    sig = compute_hedge_signal()
    radar = load_market_radar()
    spine = load_guar_price_series()

    us_row = radar[radar["dest_iso"] == "USA"]
    us_share_pct = float(us_row["india_share_pct"].iloc[0]) if len(us_row) else 0.0

    ranked = radar[radar["pivot_score"].notna()]
    top_premium = (
        ranked.sort_values("realised_usd_per_kg", ascending=False)
        .head(10)
        .loc[
            :,
            [
                "dest_country",
                "realised_usd_per_kg",
                "price_vs_portfolio_pct",
                "india_share_pct",
                "shift_flag",
            ],
        ]
    )
    shifts = (
        ranked[ranked["shift_flag"].isin(["SURGING", "FADING"])]
        .sort_values("recent_shift_pct", ascending=False)
        .loc[
            :,
            [
                "dest_country",
                "india_share_pct",
                "realised_usd_per_kg",
                "recent_shift_pct",
                "shift_flag",
            ],
        ]
    )
    return View(roi, sig, radar, spine, us_share_pct, top_premium, shifts)


def inr(amount: float) -> str:
    """Indian-format a rupee amount: crore above 1cr, else lakh."""
    if abs(amount) >= 1e7:
        return f"₹{amount / 1e7:,.2f} Cr"
    return f"₹{amount / 1e5:,.1f} L"


# ---------------------------------------------------------------------------
# Streamlit screen
# ---------------------------------------------------------------------------


def main() -> None:  # pragma: no cover - exercised via `streamlit run`
    st.set_page_config(
        page_title="Guar Export Decision Intelligence",
        page_icon="🌱",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.sidebar.title("🌱 Guar Export Decision Intelligence")
    st.sidebar.caption("WHEN to sell · WHERE to sell — in your rupees")
    st.sidebar.markdown("---")
    st.sidebar.subheader("Your business")
    st.sidebar.caption(
        "**Simulated** for the demo. In a pilot these are *your* real "
        "numbers — nothing else about the product changes."
    )
    tonnes = st.sidebar.slider("Annual guar volume (tonnes)", 100, 3000, 600, 50)
    us_share = st.sidebar.slider(
        "Share of volume going to the US",
        0.0,
        0.9,
        0.45,
        0.05,
        format="%.0f%%",
    )
    reroute = st.sidebar.slider(
        "US volume you'd test re-routing", 0.0, 0.5, 0.20, 0.05, format="%.0f%%"
    )
    fx = st.sidebar.number_input("FX assumption (₹ per $1)", 75.0, 95.0, 83.0, 0.5)
    st.sidebar.markdown("---")
    st.sidebar.info(
        "**Data:** UN Comtrade (India guar exports 2019–2024) + Baker "
        "Hughes rig count + IMD monsoon — free public sources only.\n\n"
        "**Honesty:** trade data lags 6–18 months; figures are decision-"
        "framing, not booked. Every assumption is shown, not hidden."
    )
    st.sidebar.caption(
        f"runtime: plotly {plotly.__version__} · streamlit {st.__version__}"
    )

    v = assemble_view(tonnes, us_share, reroute, fx)

    st.title("Two money decisions on every guar shipment")
    st.markdown(
        "You decide *when* to sell and *where* to sell — today on gut. "
        "Here is each, on data, in your rupees."
    )
    # The integrity statement — shown, never buried.
    st.warning(f"**{v.sig.price_direction_label}**  ·  {v.sig.backtest_evidence}")

    a, b, c = st.columns(3)
    a.metric(
        "WHEN — downside at risk / yr",
        inr(v.roi.downside_inr_year),
        f"trigger: {v.roi.when_trigger}",
        delta_color="off",
    )
    b.metric(
        "WHERE — re-route uplift / yr",
        inr(v.roi.reroute_uplift_inr_year),
        f"{reroute * 100:.0f}% of US vol → {v.roi.pivot_country}",
    )
    c.metric(
        "US concentration",
        f"{v.us_share_pct:.1f}%",
        "of India's guar FOB — the exposure",
        delta_color="off",
    )

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("WHEN — lock a forward, or hold?")
        badge = "🔴 LOCK NOW" if v.sig.trigger == "LOCK_NOW" else "🟢 NO TRIGGER"
        st.markdown(f"### {badge}")
        st.markdown(
            f"- US rig count **{v.sig.rig_level:,.0f}** "
            f"(**{v.sig.rig_yoy_change_pct:+.1f}%** year-on-year)\n"
            f"- Current price **${v.sig.current_price_usd_per_kg:.3f}/kg** "
            f"({v.sig.price_pctile_12m:.0f}%ile of last 12 mo)\n"
            f"- Bad-quarter analogue **−{v.sig.hist_bad_quarter_pct:.1f}%** "
            f"→ ≈ **{inr(v.roi.downside_inr_year)}/yr** at risk on your volume"
        )
        st.info(v.sig.reason)

    with right:
        st.subheader("WHERE — which market pays more?")
        st.markdown(
            f"You send **{v.us_share_pct:.0f}%** of India's guar to the US "
            f"(${v.roi.us_realised_usd_per_kg:.2f}/kg). Re-routing "
            f"**{reroute * 100:.0f}%** of your US volume to "
            f"**{v.roi.pivot_country}** "
            f"(${v.roi.pivot_realised_usd_per_kg:.2f}/kg) ≈ "
            f"**{inr(v.roi.reroute_uplift_inr_year)}/yr**."
        )
        prem = v.top_premium.copy()
        fig = go.Figure(
            go.Bar(
                x=prem["realised_usd_per_kg"],
                y=prem["dest_country"],
                orientation="h",
                marker_color=[
                    "#e76f51" if c == "USA" else "#2a9d8f" for c in prem["dest_country"]
                ],
                text=[f"${x:.2f}" for x in prem["realised_usd_per_kg"]],
                textposition="outside",
            )
        )
        fig.add_vline(
            x=v.roi.current_blended_usd_per_kg,
            line_dash="dot",
            line_color="gray",
            annotation_text="your blended",
        )
        fig.update_layout(
            title="Realised $/kg — top-paying markets",
            height=360,
            yaxis=dict(autorange="reversed"),
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("Price spine — and the data we repaired in the open")
    sp = v.spine.copy()
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=sp["period"],
            y=sp["price_usd_per_kg"],
            name="Guar price ($/kg, used)",
            line=dict(color="#264653", width=2),
        )
    )
    imp = sp[sp["is_imputed"]]
    if len(imp):
        fig2.add_trace(
            go.Scatter(
                x=imp["period"],
                y=imp["price_usd_per_kg"],
                name="Repaired month (corrupt source)",
                mode="markers",
                marker=dict(color="#e76f51", size=11, symbol="x"),
            )
        )
    fig2.update_layout(
        height=340,
        yaxis_title="USD / kg",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=10, b=10),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "The red ✕ is a month whose source quantity was corrupt (Oct-2021, "
        "printed ~$7/kg). We flag and interpolate it transparently and keep "
        "the raw value in an audit column — we never feed a known lie to the "
        "model. That visible honesty *is* the product."
    )

    if len(v.shifts):
        st.subheader("Radar — markets turning (the recurring monitor)")
        st.dataframe(
            v.shifts.rename(
                columns={
                    "dest_country": "Market",
                    "india_share_pct": "India share %",
                    "realised_usd_per_kg": "$/kg",
                    "recent_shift_pct": "12mo vs prior %",
                    "shift_flag": "Flag",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("FX sensitivity (every ₹ figure scales linearly)"):
        fx_tbl = pd.DataFrame(
            [
                {
                    "₹ per $1": r,
                    "Downside capped": inr(d["downside_lakh"] * 1e5),
                    "Re-route uplift": inr(d["uplift_lakh"] * 1e5),
                }
                for r, d in v.roi.fx_sensitivity.items()
            ]
        )
        st.dataframe(fx_tbl, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.caption(
        "**Honest limitations.** Exporter profile is *simulated* (public "
        "core + simulated private layer). Realised price is a lagged, "
        "mixed-grade Comtrade proxy. WHERE is India-reporter only — each "
        "market's *total* world imports (headroom) is a flagged roadmap "
        "item, not faked. FTA status is a curated, dated reference. WHEN "
        "does not forecast price (shown above) — it is a risk trigger, and "
        "its thresholds are economic heuristics, not fitted. Decision-"
        "framing, not investment advice."
    )


if __name__ == "__main__":
    main()
