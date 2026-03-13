"""Cached data loaders for the Okane dashboard.

All @st.cache_data helpers live here so page modules can import them
without circular dependencies.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import streamlit as st
import yaml

# Ensure project root on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import CONFIG_DIR
from data.storage import (
    fetch_circuit_breaker_state,
    fetch_equity_snapshots,
    fetch_open_positions,
    fetch_orders,
    fetch_pending_signals,
    fetch_recent_signals,
    fetch_trades,
)
from datetime import datetime

STRATEGIES_YAML_PATH = CONFIG_DIR / "strategies.yaml"


@st.cache_data(ttl=30)
def cached_equity_snapshots(limit: int = 500) -> list[dict]:
    return fetch_equity_snapshots(limit=limit)


@st.cache_data(ttl=30)
def cached_open_positions() -> list[dict]:
    return fetch_open_positions()


@st.cache_data(ttl=30)
def cached_circuit_breaker() -> dict:
    return fetch_circuit_breaker_state()


@st.cache_data(ttl=30)
def cached_pending_signals() -> list[dict]:
    return fetch_pending_signals()


@st.cache_data(ttl=30)
def cached_recent_signals(
    limit: int = 200,
    strategy: str | None = None,
    symbol: str | None = None,
) -> list[dict]:
    return fetch_recent_signals(limit=limit, strategy=strategy, symbol=symbol)


@st.cache_data(ttl=30)
def cached_orders(
    status: str | None = None,
    symbol: str | None = None,
) -> list[dict]:
    return fetch_orders(status=status, symbol=symbol)


@st.cache_data(ttl=30)
def cached_trades(
    symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    return fetch_trades(symbol=symbol, start=start, end=end)


def load_strategies_yaml() -> dict:
    """Load strategies.yaml from disk (not cached — always fresh for editing)."""
    if STRATEGIES_YAML_PATH.exists():
        with open(STRATEGIES_YAML_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_strategies_yaml(data: dict) -> None:
    """Atomically write strategies.yaml using temp file + rename."""
    fd, tmp_path = tempfile.mkstemp(
        dir=str(CONFIG_DIR), suffix=".yaml.tmp", prefix=".strategies_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, str(STRATEGIES_YAML_PATH))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
