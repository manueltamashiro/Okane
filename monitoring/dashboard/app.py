"""Okane Dashboard — main entry point.

Configures Streamlit, initializes the DB, and dispatches to page modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Ensure project root on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data.storage import init_db
from monitoring.dashboard.theme import inject_css

from monitoring.dashboard.pages.overview import render_overview
from monitoring.dashboard.pages.operations import render_operations
from monitoring.dashboard.pages.bot_control import render_bot_control
from monitoring.dashboard.pages.trades_orders import render_trades_orders
from monitoring.dashboard.pages.signals import render_signals
from monitoring.dashboard.pages.performance import render_performance
from monitoring.dashboard.pages.configuration import render_configuration
from monitoring.dashboard.pages.strategy_tester import render_strategy_tester


def main() -> None:
    st.set_page_config(page_title="Okane", page_icon="\U0001f4ca", layout="wide")
    inject_css()

    # DB init (once per Streamlit server lifetime)
    if not st.session_state.get("db_initialized"):
        init_db()
        st.session_state["db_initialized"] = True

    PAGES = {
        "Overview": render_overview,
        "Operations": render_operations,
        "Bot Control": render_bot_control,
        "Trades & Orders": render_trades_orders,
        "Signals": render_signals,
        "Performance": render_performance,
        "Configuration": render_configuration,
        "Strategy Tester": render_strategy_tester,
    }

    # Sidebar with NYT-style branding
    st.sidebar.markdown("# OKANE")
    st.sidebar.markdown("*Algorithmic Trading System*")
    page = st.sidebar.radio("", list(PAGES.keys()), label_visibility="collapsed")
    PAGES[page]()
