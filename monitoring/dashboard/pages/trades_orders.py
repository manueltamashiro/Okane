"""Trades & Orders page — filterable order and trade tables."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from monitoring.dashboard.cached_data import cached_orders, cached_trades


def render_trades_orders() -> None:
    st.header("Trades & Orders")

    tab_orders, tab_trades = st.tabs(["Orders", "Trades"])

    # --- Orders tab ---
    with tab_orders:
        col_status, col_sym = st.columns(2)
        with col_status:
            status_options = ["All", "pending", "filled", "cancelled", "rejected"]
            order_status = st.selectbox("Status", status_options, key="order_status_filter")
        with col_sym:
            order_symbol = st.text_input("Symbol (blank = all)", key="order_symbol_filter").strip().upper()

        status_val = None if order_status == "All" else order_status
        symbol_val = order_symbol if order_symbol else None
        order_rows = cached_orders(status=status_val, symbol=symbol_val)

        if order_rows:
            st.dataframe(pd.DataFrame(order_rows), use_container_width=True)
        else:
            st.info("No orders match the selected filters")

    # --- Trades tab ---
    with tab_trades:
        col_sym_t, col_start, col_end = st.columns(3)
        with col_sym_t:
            trade_symbol = st.text_input("Symbol (blank = all)", key="trade_symbol_filter").strip().upper()
        with col_start:
            trade_start = st.date_input("Start date", value=datetime.now() - timedelta(days=90), key="trade_start")
        with col_end:
            trade_end = st.date_input("End date", value=datetime.now(), key="trade_end")

        sym_val = trade_symbol if trade_symbol else None
        start_dt = datetime.combine(trade_start, datetime.min.time()) if trade_start else None
        end_dt = datetime.combine(trade_end, datetime.max.time()) if trade_end else None
        trade_rows = cached_trades(symbol=sym_val, start=start_dt, end=end_dt)

        if trade_rows:
            st.dataframe(pd.DataFrame(trade_rows), use_container_width=True)
        else:
            st.info("No trades match the selected filters")
