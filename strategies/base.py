from __future__ import annotations
import yaml
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd
from loguru import logger

from config.settings import CONFIG_DIR


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    symbol: str
    direction: Direction
    price: float
    stop_loss: float
    confidence: float          # 0.0–1.0
    strategy_name: str
    timestamp: datetime
    timeframe: str = "1d"
    take_profit: float | None = None
    notes: dict[str, Any] = field(default_factory=dict)


def load_strategy_config() -> dict[str, Any]:
    """Load strategies.yaml. Callers should cache in __init__."""
    path = CONFIG_DIR / "strategies.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


def _extract_timestamp(df: pd.DataFrame) -> datetime:
    """Extract the last bar's timestamp as a plain datetime."""
    ts = df.index[-1]
    return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def _check_df(
    df: pd.DataFrame | None,
    symbol: str,
    strategy_name: str,
    min_rows: int,
    required: list[str],
) -> bool:
    """Return False (and log) if df is too short or missing required columns."""
    if df is None or len(df) < min_rows:
        logger.debug(f"[{strategy_name}] insufficient rows for {symbol}: {len(df) if df is not None else 0}")
        return False
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.debug(f"[{strategy_name}] missing columns for {symbol}: {missing}")
        return False
    return True


def save_signal(signal: Signal) -> None:
    """Persist a Signal to the signals table. Raises on DB failure (fail-safe)."""
    from data.storage import upsert_signal
    upsert_signal(signal)


class Strategy(ABC):
    name: str  # set by each subclass as class attribute

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal | None:
        """
        Evaluate the latest bar of an enriched DataFrame and return a Signal.

        Args:
            df:     DataFrame indexed by timestamp, containing OHLCV + all
                    indicator columns. Must have at least warmup_bars rows.
            symbol: Ticker symbol being evaluated.

        Returns:
            Signal if a BUY or SELL condition is met, else None.
            Must never raise — catches all exceptions internally.
        """
        ...
