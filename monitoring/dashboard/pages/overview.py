"""Overview page — KPIs, equity curve, open positions."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from monitoring.dashboard.theme import get_plotly_template
from monitoring.dashboard.cached_data import (
    cached_circuit_breaker,
    cached_equity_snapshots,
    cached_open_positions,
)


def render_overview() -> None:
    st.header("Overview")

    # --- Circuit breaker banner ---
    cb = cached_circuit_breaker()
    if cb:
        halted = cb.get("daily_halt") or cb.get("weekly_halt") or cb.get("full_halt")
        if halted:
            reason = cb.get("halt_reason") or "Unknown reason"
            st.error(f"HALTED: {reason}")
        else:
            st.success("Trading Active")
    else:
        st.warning("Circuit breaker state not available")

    # --- KPI metrics ---
    snapshots = cached_equity_snapshots(limit=500)
    if snapshots:
        latest = snapshots[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Equity", f"${latest.get('equity', 0):,.2f}")
        c2.metric("Cash", f"${latest.get('cash', 0):,.2f}")
        c3.metric("Daily P&L", f"${latest.get('daily_pnl', 0):+,.2f}")
        c4.metric("Weekly P&L", f"${latest.get('weekly_pnl', 0) or 0:+,.2f}")

        # --- Equity curve ---
        chrono = list(reversed(snapshots))
        timestamps = [s["timestamp"] for s in chrono]
        equities = [s["equity"] for s in chrono]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=timestamps, y=equities, mode="lines", name="Equity"))
        fig.update_layout(
            template=get_plotly_template(),
            title="Equity Curve",
            xaxis_title="Time",
            yaxis_title="Equity ($)",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No equity data yet")

    # --- Open positions ---
    st.subheader("Open Positions")
    positions = cached_open_positions()
    if positions:
        st.dataframe(pd.DataFrame(positions), use_container_width=True)
    else:
        st.info("No open positions")
