"""Signals page — pending + recent signals."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from monitoring.dashboard.cached_data import (
    cached_pending_signals,
    cached_recent_signals,
    load_strategies_yaml,
)


def render_signals() -> None:
    st.header("Signals")

    # --- Pending count ---
    pending = cached_pending_signals()
    st.metric("Pending Signals", len(pending))

    # --- Filters ---
    strat_config = load_strategies_yaml()
    strategy_keys = [k for k in strat_config.keys() if k not in ("universe", "tax", "withdrawal")]
    strategy_options = ["All"] + strategy_keys

    col_strat, col_sym = st.columns(2)
    with col_strat:
        sig_strategy = st.selectbox("Strategy", strategy_options, key="sig_strategy_filter")
    with col_sym:
        sig_symbol = st.text_input("Symbol (blank = all)", key="sig_symbol_filter").strip().upper()

    strat_val = None if sig_strategy == "All" else sig_strategy
    sym_val = sig_symbol if sig_symbol else None
    signal_rows = cached_recent_signals(limit=200, strategy=strat_val, symbol=sym_val)

    if signal_rows:
        st.dataframe(pd.DataFrame(signal_rows), use_container_width=True)
    else:
        st.info("No signals match the selected filters")
