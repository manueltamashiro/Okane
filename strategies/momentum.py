from __future__ import annotations

import pandas as pd
from loguru import logger

from strategies.base import Direction, Signal, Strategy, _check_df, _extract_timestamp, load_strategy_config


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(self) -> None:
        cfg = load_strategy_config()["momentum"]
        self.ema_fast: int = cfg["ema_fast"]                              # 9
        self.ema_slow: int = cfg["ema_slow"]                              # 21
        self.adx_period: int = cfg["adx"]["period"]                       # 14
        self.adx_min: float = cfg["adx"]["min_strength"]                  # 25
        self.atr_period: int = cfg["atr"]["period"]                       # 14
        self.atr_multiplier: float = cfg["trailing_stop_atr_multiplier"]  # 2.0
        self.timeframe: str = cfg["timeframe"]                            # "1d"

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal | None:
        min_rows = max(self.ema_slow, self.adx_period, self.atr_period) + 2
        ema_fast_col = f"ema_{self.ema_fast}"
        ema_slow_col = f"ema_{self.ema_slow}"
        adx_col = f"adx_{self.adx_period}"
        atr_col = f"atr_{self.atr_period}"
        macd_hist_col = "macd_hist_12_26_9"
        required = [ema_fast_col, ema_slow_col, adx_col, atr_col, "close"]
        if not _check_df(df, symbol, self.name, min_rows, required):
            return None

        try:
            prev = df.iloc[-2]
            curr = df.iloc[-1]

            close = float(curr["close"])
            adx = float(curr[adx_col])
            atr = float(curr[atr_col])

            prev_ema_fast = float(prev[ema_fast_col])
            prev_ema_slow = float(prev[ema_slow_col])
            curr_ema_fast = float(curr[ema_fast_col])
            curr_ema_slow = float(curr[ema_slow_col])

            if any(pd.isna(v) for v in [adx, atr, prev_ema_fast, prev_ema_slow, curr_ema_fast, curr_ema_slow]):
                return None

            ts = _extract_timestamp(df)

            bullish_crossover = prev_ema_fast < prev_ema_slow and curr_ema_fast > curr_ema_slow
            bearish_crossover = prev_ema_fast > prev_ema_slow and curr_ema_fast < curr_ema_slow

            # BUY: bullish EMA crossover AND ADX confirms trend strength
            if bullish_crossover and adx > self.adx_min:
                confidence = min(1.0, adx / 100.0)
                # MACD histogram bonus
                if macd_hist_col in df.columns:
                    macd_hist = curr.get(macd_hist_col, float('nan'))
                    if not pd.isna(macd_hist) and float(macd_hist) > 0:
                        confidence = min(1.0, confidence + 0.1)
                stop_loss = close - (self.atr_multiplier * atr)
                return Signal(
                    symbol=symbol,
                    direction=Direction.BUY,
                    price=close,
                    stop_loss=stop_loss,
                    confidence=confidence,
                    strategy_name=self.name,
                    timestamp=ts,
                    timeframe=self.timeframe,
                )

            # SELL: bearish EMA crossover
            if bearish_crossover:
                return Signal(
                    symbol=symbol,
                    direction=Direction.SELL,
                    price=close,
                    stop_loss=0.0,
                    confidence=0.5,
                    strategy_name=self.name,
                    timestamp=ts,
                    timeframe=self.timeframe,
                )

            return None

        except Exception as exc:
            logger.warning(f"[momentum] generate_signal failed for {symbol}: {exc}")
            return None
