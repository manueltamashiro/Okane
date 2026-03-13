from __future__ import annotations

import pandas as pd
from loguru import logger

from strategies.base import Direction, Signal, Strategy, _check_df, _extract_timestamp, load_strategy_config


class SwingStrategy(Strategy):
    name = "swing"

    def __init__(self) -> None:
        cfg = load_strategy_config()["swing"]
        self.primary_tf: str = cfg["primary_timeframe"]           # "1d"
        # PHASE 3 TODO: enforce hold_period_days (min=3, max=14) in the execution layer.
        # This requires position tracking across bars, which belongs in the order engine.
        self._ema_period: int = cfg["ema_period"]
        self._adx_period: int = cfg["adx_period"]
        self._rsi_period: int = cfg["rsi_period"]
        self._adx_trend_min: float = cfg["adx_trend_min"]
        self._rsi_overbought: float = cfg["rsi_overbought"]
        # PHASE 3 TODO: Multi-timeframe entry confirmation.
        # Current implementation uses primary_timeframe ("1d") only.
        # Phase 3 will add: fetch 4H/1H bars for the same symbol, compute entry
        # timing signals, and require alignment across all three timeframes.
        # This requires the execution layer to provide multi-timeframe data.

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal | None:
        min_rows = max(self._ema_period, self._adx_period, self._rsi_period) + 3
        ema_col = f"ema_{self._ema_period}"
        adx_col = f"adx_{self._adx_period}"
        rsi_col = f"rsi_{self._rsi_period}"
        required = [ema_col, adx_col, rsi_col, "close", "low"]
        if not _check_df(df, symbol, self.name, min_rows, required):
            return None

        try:
            curr = df.iloc[-1]
            last_3 = df.iloc[-3:]

            close = float(curr["close"])
            ema = float(curr[ema_col])
            adx = float(curr[adx_col])
            rsi = float(curr[rsi_col])

            if any(pd.isna(v) for v in [close, ema, adx, rsi]):
                return None

            ts = _extract_timestamp(df)

            bullish_bias = close > ema and adx > self._adx_trend_min

            # BUY: bullish bias + pullback to EMA + RSI in neutral zone (not extreme)
            if bullish_bias:
                ema_values = last_3[ema_col].values
                close_values = last_3["close"].values
                # Pullback to EMA: at least one of the last 3 bars touched/crossed EMA from below
                pullback = any(abs(c - e) / e < 0.01 for c, e in zip(close_values, ema_values))
                neutral_rsi = 40.0 < rsi < 60.0
                if pullback and neutral_rsi:
                    stop_loss = float(last_3["low"].min())
                    return Signal(
                        symbol=symbol,
                        direction=Direction.BUY,
                        price=close,
                        stop_loss=stop_loss,
                        confidence=0.4,
                        strategy_name=self.name,
                        timestamp=ts,
                        timeframe=self.primary_tf,
                    )

            # SELL: bearish bias (price below EMA) OR RSI overbought
            bearish_bias = close < ema or rsi > self._rsi_overbought
            if bearish_bias:
                return Signal(
                    symbol=symbol,
                    direction=Direction.SELL,
                    price=close,
                    stop_loss=0.0,
                    confidence=0.4,
                    strategy_name=self.name,
                    timestamp=ts,
                    timeframe=self.primary_tf,
                )

            return None

        except Exception as exc:
            logger.warning(f"[swing] generate_signal failed for {symbol}: {exc}")
            return None
