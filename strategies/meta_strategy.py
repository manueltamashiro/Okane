from __future__ import annotations
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from strategies.base import Direction, Signal, Strategy, _extract_timestamp, load_strategy_config


class MetaStrategy:
    name = "meta"

    def __init__(self, strategies: list[Strategy]) -> None:
        cfg = load_strategy_config()
        self.strategies: list[Strategy] = strategies
        self.min_signals: int = cfg["meta_strategy"]["min_signals_required"]
        self.min_strength: float = cfg["meta_strategy"]["min_combined_strength"]
        self._weights: dict[str, float] = {
            "mean_reversion": cfg["mean_reversion"]["weight"],
            "momentum":       cfg["momentum"]["weight"],
            "swing":          cfg["swing"]["weight"],
        }

    def aggregate(
        self,
        signals: list[Signal],
        symbol: str,
        price: float,
        timestamp: datetime,
    ) -> Signal | None:
        """
        Apply weighted voting across signals that share the same direction.

        Returns a composite Signal with direction=BUY/SELL if:
          - At least min_signals_required strategies agree on direction
          - Combined weighted strength >= min_combined_strength
        Otherwise returns None.
        """
        if not signals:
            return None

        # Filter out signals from unknown strategies (zero weight = excluded from voting)
        unknown = [s.strategy_name for s in signals if s.strategy_name not in self._weights]
        for name in unknown:
            logger.warning(f"[meta] unknown strategy '{name}' ignored in vote for {symbol}")
        signals = [s for s in signals if s.strategy_name in self._weights]
        if not signals:
            return None

        buy_signals = [s for s in signals if s.direction == Direction.BUY]
        sell_signals = [s for s in signals if s.direction == Direction.SELL]

        def weighted_strength(bucket: list[Signal]) -> float:
            total_weight = sum(self._weights.get(s.strategy_name, 0.0) for s in bucket)
            if total_weight == 0:
                return 0.0
            weighted_sum = sum(self._weights.get(s.strategy_name, 0.0) * s.confidence for s in bucket)
            return weighted_sum / total_weight

        buy_strength = weighted_strength(buy_signals)
        sell_strength = weighted_strength(sell_signals)

        buy_qualifies = len(buy_signals) >= self.min_signals and buy_strength >= self.min_strength
        sell_qualifies = len(sell_signals) >= self.min_signals and sell_strength >= self.min_strength

        # Conflicting: both directions qualify → no signal
        if buy_qualifies and sell_qualifies:
            logger.debug(f"[meta] conflicting signals for {symbol}, returning None")
            return None

        if buy_qualifies:
            stop_loss = min(s.stop_loss for s in buy_signals if s.stop_loss > 0) if any(s.stop_loss > 0 for s in buy_signals) else 0.0
            return Signal(
                symbol=symbol,
                direction=Direction.BUY,
                price=price,
                stop_loss=stop_loss,
                confidence=buy_strength,
                strategy_name=self.name,
                timestamp=timestamp,
            )

        if sell_qualifies:
            return Signal(
                symbol=symbol,
                direction=Direction.SELL,
                price=price,
                stop_loss=0.0,
                confidence=sell_strength,
                strategy_name=self.name,
                timestamp=timestamp,
            )

        return None

    def run(self, df: pd.DataFrame, symbol: str) -> Signal | None:
        """
        Call generate_signal on all sub-strategies, collect non-None results,
        then aggregate.
        """
        collected: list[Signal] = []
        for strategy in self.strategies:
            try:
                signal = strategy.generate_signal(df, symbol)
                if signal is not None:
                    collected.append(signal)
            except Exception as exc:
                logger.warning(f"[meta] sub-strategy {strategy.name} failed for {symbol}: {exc}")

        if not df.empty:
            price = float(df.iloc[-1]["close"])
            ts = _extract_timestamp(df)
        else:
            return None

        return self.aggregate(collected, symbol, price, ts)
