"""Guar Export Decision Intelligence — the one-screen product.

Written for the person who pays: a guar exporter, not an analyst. Plain
business language, rupees first, framed as *earn more / lose less / be
careful*. Every technical term, statistic and limitation lives in one
collapsible "How sure are we?" section so credibility is preserved
without burdening the exporter.

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
from src.product.exec_summary import move_from_signals
from src.product.exporter_roi import ExporterProfile, compute_exporter_roi


# ---------------------------------------------------------------------------
# Data prep — pure, testable, no Streamlit
# ---------------------------------------------------------------------------


@dataclass
class View:
    roi: object
    sig: object
    move: object
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
    move = move_from_signals(roi, sig, reroute_share)
    radar = load_market_radar()
    spine = load_guar_price_series()

    us_row = radar[radar["dest_iso"] == "USA"]
    us_share_pct = float(us_row["india_share_pct"].iloc[0]) if len(us_row) else 0.0

    ranked = radar[radar["pivot_score"].notna()]
    prem_cols = [
        "dest_country",
        "realised_usd_per_kg",
        "price_vs_portfolio_pct",
        "india_share_pct",
        "shift_flag",
    ]
    top_premium = (
        ranked.sort_values("realised_usd_per_kg", ascending=False)
        .head(10)
        .loc[:, prem_cols]
    )
    # The chart promises the exporter's own market (USA) in orange for
    # comparison, but the USA pays poorly so it is never in the top-10
    # payers. Append it so the benchmark bar actually exists — it lands
    # at the bottom, an honest "you are paid less than all of these".
    if "USA" not in set(top_premium["dest_country"]):
        top_premium = pd.concat(
            [top_premium, radar.loc[radar["dest_country"] == "USA", prem_cols]],
            ignore_index=True,
        )
    # Show the sharpest movers BOTH ways: a single descending sort buried
    # every FADING market below the surging ones, hiding the early
    # warnings the "markets to watch" panel exists to surface.
    shift_cols = [
        "dest_country",
        "india_share_pct",
        "realised_usd_per_kg",
        "recent_shift_pct",
        "shift_flag",
    ]
    surging = (
        ranked[ranked["shift_flag"] == "SURGING"]
        .sort_values("recent_shift_pct", ascending=False)
        .head(7)
    )
    fading = (
        ranked[ranked["shift_flag"] == "FADING"]
        .sort_values("recent_shift_pct", ascending=True)
        .head(7)
    )
    shifts = pd.concat([surging, fading]).loc[:, shift_cols]
    return View(roi, sig, move, radar, spine, us_share_pct, top_premium, shifts)


def inr(amount: float) -> str:
    """Indian-format a rupee amount: crore above 1cr, else lakh."""
    if abs(amount) >= 1e7:
        return f"₹{amount / 1e7:,.2f} crore"
    return f"₹{amount / 1e5:,.1f} lakh"


def rs_per_kg(usd: float, fx: float) -> str:
    """$/kg → a plain ₹/kg the exporter actually thinks in."""
    return f"₹{usd * fx:,.0f}/kg"


# ---------------------------------------------------------------------------
# Streamlit screen — written for the exporter, not the analyst
# ---------------------------------------------------------------------------


def main() -> None:  # pragma: no cover - exercised via `streamlit run`
    st.set_page_config(
        page_title="Guar Export — earn more, lose less",
        page_icon="🌱",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ---- Sidebar: "tell us about your business", in plain words --------
    st.sidebar.title("🌱 Your guar export business")
    st.sidebar.caption(
        "Move these to match your business — everything below updates " "instantly."
    )
    tonnes = st.sidebar.slider(
        "How many tonnes of guar do you export per year?", 100, 3000, 600, 50
    )
    us_share_pct = st.sidebar.slider(
        "About how much of it goes to the USA?",
        0,
        90,
        45,
        5,
        format="%d%%",
    )
    reroute_pct = st.sidebar.slider(
        "How much of that US volume would you try selling elsewhere?",
        0,
        50,
        20,
        5,
        format="%d%%",
    )
    fx = st.sidebar.number_input("Exchange rate (₹ for $1)", 75.0, 95.0, 83.0, 0.5)
    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Built only on **free public trade data** + US oil-drilling "
        "activity. The business shown is an example — in a real review we "
        "use *your* figures. This is decision guidance, not a guarantee."
    )

    v = assemble_view(tonnes, us_share_pct / 100.0, reroute_pct / 100.0, fx)

    st.title("Guar export: what to do to earn more and lose less")
    st.markdown(
        "Every shipment is two money decisions — **when to sell** and "
        "**where to sell**. Here is what the public trade data says about "
        "yours, in rupees."
    )

    # ---- The one thing to do first -----------------------------------
    st.success(f"### ✅ Do this first\n\n**{v.move.headline}**\n\n{v.move.why}")

    # ---- Plain straight talk (the honesty, no jargon) ----------------
    st.warning(
        "**Straight talk:** nobody can reliably predict the guar price — "
        "and we will not pretend to. Instead we tell you *when the risk of "
        "a price fall is high*, so you can lock a deal in time. We tested "
        "this honestly — the proof is in **“How sure are we?”** at the "
        "bottom."
    )

    # ---- Earn more / Lose less / Be careful --------------------------
    st.markdown("### What this means for your business")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.success(
            "**💰 Earn more — sell where you're paid better**\n\n"
            f"The USA pays you about **{rs_per_kg(v.roi.us_realised_usd_per_kg, fx)}**. "
            f"**{v.roi.pivot_country}** pays about "
            f"**{rs_per_kg(v.roi.pivot_realised_usd_per_kg, fx)}** for the "
            "same guar.\n\n"
            f"Moving {reroute_pct:.0f}% of your US sales there ≈ "
            f"**{inr(v.roi.reroute_uplift_inr_year)} extra per year**."
        )
    with c2:
        if v.sig.trigger == "LOCK_NOW":
            st.error(
                "**🛡️ Lose less — act now**\n\n"
                "US oil drilling (guar's single biggest use — fracking) has "
                "slowed sharply. Every time that happened before, guar "
                "prices tended to fall in the months after.\n\n"
                f"Locking a forward price now protects ≈ "
                f"**{inr(v.roi.downside_inr_year)} a year** of your money."
            )
        else:
            st.info(
                "**🛡️ Lose less — no rush this month**\n\n"
                "US oil drilling is steady, so there's no unusual "
                "price-fall danger right now.\n\n"
                f"But ≈ **{inr(v.roi.downside_inr_year)} a year** is what a "
                "bad patch could put at risk — keep watching this."
            )
    with c3:
        st.warning(
            "**⚠️ Be careful — too dependent on the USA**\n\n"
            f"**{v.us_share_pct:.0f}%** of India's guar goes to the USA. "
            "India already supplies a big share of everything the US buys, "
            "so there's little room to grow there — plus new US tariffs.\n\n"
            "Selling to more countries lowers this risk."
        )

    total = v.roi.reroute_uplift_inr_year + v.roi.downside_inr_year
    st.markdown(
        f"#### Bottom line: about **{inr(total)} a year** on your volume "
        "rides on these two decisions — today you make them on gut feel. "
        "This turns them into numbers."
    )

    # ---- The chart that DOES move with the volume sliders ------------
    st.markdown("### 💸 What it's worth on *your* volume")
    st.caption(
        "Move the three sliders on the left — **this chart moves with "
        "them.** (The market charts further down show prices *across "
        "countries*; those only change with the exchange rate, not with "
        "how much you export — that is correct, not a bug.)"
    )
    impact = pd.DataFrame(
        {
            "what": [
                "Loss you can protect / yr  (WHEN)",
                "Extra you can earn / yr  (WHERE)",
            ],
            "rs": [
                v.roi.downside_inr_year,
                v.roi.reroute_uplift_inr_year,
            ],
        }
    )
    fig_money = go.Figure(
        go.Bar(
            x=impact["rs"],
            y=impact["what"],
            orientation="h",
            marker_color=["#e76f51", "#2a9d8f"],
            text=[inr(x) for x in impact["rs"]],
            textposition="outside",
        )
    )
    fig_money.update_layout(
        height=240,
        xaxis_title="₹ per year, on the business set by your sliders",
        yaxis=dict(autorange="reversed"),
        # No numeric ticks: they auto-render in "millions" while every bar
        # is labelled in lakh/crore — two unit systems on one chart. The
        # ₹-formatted data labels are the scale the exporter reads.
        xaxis=dict(
            range=[0, float(impact["rs"].max()) * 1.35], showticklabels=False
        ),
        margin=dict(l=10, r=10, t=10, b=10),
    )
    st.plotly_chart(fig_money, use_container_width=True)

    st.markdown("---")

    # ---- Which countries pay the most (in ₹/kg) ----------------------
    st.subheader("Which countries pay the most for your guar")
    prem = v.top_premium.copy()
    prem["rs"] = prem["realised_usd_per_kg"] * fx
    fig = go.Figure(
        go.Bar(
            x=prem["rs"],
            y=prem["dest_country"],
            orientation="h",
            marker_color=[
                "#e76f51" if c == "USA" else "#2a9d8f" for c in prem["dest_country"]
            ],
            text=[f"₹{x:,.0f}" for x in prem["rs"]],
            textposition="outside",
        )
    )
    fig.update_layout(
        title="₹ per kg, by country (your main market — the USA — in orange)",
        height=380,
        yaxis=dict(autorange="reversed"),
        xaxis_title="₹ per kg",
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Longer bar = that country pays more per kg for the same guar. "
        "You mostly sell to the USA (orange) — several countries clearly "
        "pay more."
    )

    # ---- Price over the years (with the corrected month explained) ---
    st.subheader("Guar price over the years")
    sp = v.spine.copy()
    sp["rs"] = sp["price_usd_per_kg"] * fx
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=sp["period"],
            y=sp["rs"],
            name="Guar price (₹/kg)",
            line=dict(color="#264653", width=2),
        )
    )
    imp = sp[sp["is_imputed"]]
    if len(imp):
        fig2.add_trace(
            go.Scatter(
                x=imp["period"],
                y=imp["rs"],
                name="One month we corrected",
                mode="markers",
                marker=dict(color="#e76f51", size=12, symbol="x"),
            )
        )
    fig2.update_layout(
        height=320,
        yaxis_title="₹ per kg",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=10, b=10),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        "One month (Oct 2021) had a clearly wrong figure in the public "
        "data — it showed roughly 4× the real price. We caught it, marked "
        "it (✕) and corrected it. We don't hide bad data; we fix it in "
        "the open."
    )

    # ---- Markets to watch (plain) ------------------------------------
    if len(v.shifts):
        st.subheader("Markets to watch")
        w = v.shifts.copy()
        w["You sell there"] = w["india_share_pct"].map(lambda x: f"{x:.1f}%")
        w["They pay"] = (w["realised_usd_per_kg"] * fx).map(lambda x: f"₹{x:,.0f}/kg")
        w["Trend"] = w["shift_flag"].map(
            {"SURGING": "📈 Growing fast", "FADING": "📉 Shrinking"}
        )
        st.dataframe(
            w[["dest_country", "You sell there", "They pay", "Trend"]].rename(
                columns={"dest_country": "Country"}
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Growing markets are where guar demand is rising — good places "
            "to build new buyers. Shrinking ones are early warnings."
        )

    # ---- All the technical proof, collapsed by default ---------------
    with st.expander("How sure are we?  (the honest technical details)"):
        st.markdown(
            f"**On predicting price:** {v.sig.price_direction_label}. "
            f"Evidence — {v.sig.backtest_evidence}. {v.move.caveat}"
        )
        st.markdown(
            f"**WHEN signal internals:** trigger `{v.sig.trigger}`; US rig "
            f"count {v.sig.rig_level:,.0f} "
            f"({v.sig.rig_yoy_change_pct:+.1f}% vs a year ago); current "
            f"price in the {v.sig.price_pctile_12m:.0f}th percentile of the "
            f"last 12 months; worst-quarter analogue "
            f"−{v.sig.hist_bad_quarter_pct:.1f}%."
        )
        if v.sig.scenarios:
            st.markdown(
                "**What guar price actually did over the next 6 months, "
                "historically, after each US-drilling regime** (a "
                "historical analogue with sample sizes — *not* a forecast):"
            )
            _sc = pd.DataFrame(v.sig.scenarios)
            sc = pd.DataFrame(
                {
                    "US drilling regime": _sc["regime"],
                    "n windows": _sc["n_windows"],
                    # hist_adverse_drawdown_pct is stored as a POSITIVE
                    # magnitude of a drop — render it as the loss it is so
                    # it cannot read as a gain (the opposite meaning).
                    "Worst-decile 6-mo move": _sc["hist_adverse_drawdown_pct"].map(
                        lambda x: f"-{x:.1f}%" if x else "0.0%"
                    ),
                    "Median 6-mo move": _sc["hist_median_move_pct"].map(
                        lambda x: f"{x:+.1f}%"
                    ),
                }
            )
            st.dataframe(sc, use_container_width=True, hide_index=True)
        st.markdown(
            "**FX sensitivity** (every rupee figure scales linearly with "
            "the exchange rate):"
        )
        fx_tbl = pd.DataFrame(
            [
                {
                    "₹ per $1": r,
                    "Loss protected": inr(d["downside_lakh"] * 1e5),
                    "Extra from re-routing": inr(d["uplift_lakh"] * 1e5),
                }
                for r, d in v.roi.fx_sensitivity.items()
            ]
        )
        st.dataframe(fx_tbl, use_container_width=True, hide_index=True)
        st.caption(
            "Honest limitations: the example exporter is simulated; "
            "realised price is a lagged, mixed-grade public-trade proxy; "
            "market headroom uses world-import data (EU hubs re-export, so "
            "it overstates true demand there); the WHEN thresholds are "
            "economic rules of thumb, not fitted to price. Built on UN "
            "Comtrade + Baker Hughes + IMD — free public sources, data "
            "lags 6–18 months. Decision-framing, not investment advice."
        )
        st.caption(
            f"runtime: plotly {plotly.__version__} · streamlit " f"{st.__version__}"
        )


if __name__ == "__main__":
    main()
