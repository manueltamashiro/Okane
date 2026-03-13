"""Performance page — Sharpe, drawdown, win rate, per-strategy breakdown."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from monitoring.dashboard.cached_data import cached_trades
from monitoring.performance import PerformanceTracker


def render_performance() -> None:
    st.header("Performance")

    # --- Date range ---
    col_start, col_end = st.columns(2)
    with col_start:
        perf_start = st.date_input(
            "Start date",
            value=datetime.now() - timedelta(days=30),
            key="perf_start",
        )
    with col_end:
        perf_end = st.date_input("End date", value=datetime.now(), key="perf_end")

    start_dt = datetime.combine(perf_start, datetime.min.time()) if perf_start else None
    end_dt = datetime.combine(perf_end, datetime.max.time()) if perf_end else None

    tracker = PerformanceTracker()
    metrics = tracker.compute_metrics(start=start_dt, end=end_dt)

    # --- 6 metric cards ---
    c1, c2, c3 = st.columns(3)
    c1.metric("Sharpe Ratio", f"{metrics.sharpe_ratio:.2f}")
    c2.metric("Max Drawdown", f"{metrics.max_drawdown_pct:.1%}")
    c3.metric("Win Rate", f"{metrics.win_rate:.1%}")

    c4, c5, c6 = st.columns(3)
    pf_display = "\u221e" if metrics.profit_factor == float("inf") else f"{metrics.profit_factor:.2f}"
    c4.metric("Profit Factor", pf_display)
    c5.metric("Total P&L", f"${metrics.total_pnl:+,.2f}")
    c6.metric("Total Trades", str(metrics.total_trades))

    # --- Per-strategy breakdown ---
    st.subheader("Per-Strategy Breakdown")
    trade_rows = cached_trades(start=start_dt, end=end_dt)
    if trade_rows:
        df = pd.DataFrame(trade_rows)
        if "strategy_name" in df.columns and "pnl" in df.columns:
            grouped = df.groupby("strategy_name").agg(
                total_trades=("pnl", "count"),
                total_pnl=("pnl", "sum"),
                wins=("pnl", lambda x: (x > 0).sum()),
            ).reset_index()
            grouped["win_rate"] = (grouped["wins"] / grouped["total_trades"]).map("{:.1%}".format)
            grouped["total_pnl"] = grouped["total_pnl"].map("${:+,.2f}".format)
            grouped = grouped.drop(columns=["wins"])
            grouped.columns = ["Strategy", "Total Trades", "Total P&L", "Win Rate"]
            st.dataframe(grouped, use_container_width=True, hide_index=True)
        else:
            st.info("Trade data missing expected columns")
    else:
        st.info("No trades in the selected period")
