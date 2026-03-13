"""Bot Control page — start/stop session, config."""

from __future__ import annotations

import streamlit as st

from config.settings import DEFAULT_SYMBOLS
from monitoring.session_manager import get_status, is_running, start_session, stop_session
from risk.circuit_breakers import reset_daily


def render_bot_control() -> None:
    st.header("Bot Control")

    # --- Status badge ---
    status = get_status()
    if status["error"]:
        st.error(f"Error: {status['error']}")
    elif status["running"]:
        st.success("Running")
    else:
        st.error("Stopped")

    # --- Config inputs ---
    symbols = st.multiselect(
        "Symbols",
        options=DEFAULT_SYMBOLS,
        default=status.get("symbols") or DEFAULT_SYMBOLS,
    )
    poll_interval = st.number_input(
        "Poll interval (seconds)",
        min_value=10,
        max_value=300,
        value=status.get("poll_interval", 60),
    )

    # --- Start / Stop buttons ---
    col_start, col_stop = st.columns(2)
    with col_start:
        if st.button("Start Session", type="primary", use_container_width=True):
            try:
                start_session(symbols=symbols, poll_interval=int(poll_interval))
                st.success("Session started")
            except Exception as exc:
                st.error(f"Failed to start: {exc}")

    with col_stop:
        if st.button("Stop Session", type="secondary", use_container_width=True):
            try:
                stop_session()
                st.success("Session stopped")
            except Exception as exc:
                st.error(f"Failed to stop: {exc}")

    st.divider()

    # --- Reset daily circuit breaker ---
    if st.button("Reset Daily Circuit Breaker"):
        reset_daily()
        st.success("Daily circuit breaker reset")

    # --- Active session warning ---
    if is_running():
        st.warning("Session is active. Config changes require restart.")
