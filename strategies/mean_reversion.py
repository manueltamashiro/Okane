from __future__ import annotations

import pandas as pd
from loguru import logger

from strategies.base import Direction, Signal, Strategy, _check_df, _extract_timestamp, load_strategy_config


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(self) -> None:
        cfg = load_strategy_config()["mean_reversion"]
        self.bb_period: int = cfg["bollinger"]["period"]          # 20
        self.bb_std: int = cfg["bollinger"]["std_dev"]            # 2
        self.rsi_period: int = cfg["rsi"]["period"]               # 14
        self.rsi_oversold: float = cfg["rsi"]["oversold"]         # 30
        self.rsi_exit: float = cfg["rsi"]["exit_threshold"]       # 50
        self.stop_loss_pct: float = cfg["stop_loss_pct"]          # 0.02
        self.timeframe: str = cfg["timeframe"]                    # "1d"

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal | None:
        min_rows = max(self.rsi_period, self.bb_period) + 1
        rsi_col = f"rsi_{self.rsi_period}"
        bb_lower_col = f"bb_lower_{self.bb_period}_{self.bb_std}"
        bb_mid_col = f"bb_mid_{self.bb_period}_{self.bb_std}"
        required = [rsi_col, bb_lower_col, bb_mid_col, "close"]
        if not _check_df(df, symbol, self.name, min_rows, required):
            return None

        try:
            row = df.iloc[-1]
            close = float(row["close"])
            rsi = float(row[rsi_col])
            bb_lower = float(row[bb_lower_col])
            bb_mid = float(row[bb_mid_col])

            if pd.isna(rsi) or pd.isna(bb_lower) or pd.isna(bb_mid):
                return None

            ts = _extract_timestamp(df)

            # BUY: price below lower BB AND RSI oversold
            if close < bb_lower and rsi < self.rsi_oversold:
                confidence = max(0.0, min(1.0, (self.rsi_oversold - rsi) / self.rsi_oversold))
                stop_loss = close * (1 - self.stop_loss_pct)
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

            # SELL: price above mid BB OR RSI above exit threshold
            if close > bb_mid or rsi > self.rsi_exit:
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
            logger.warning(f"[mean_reversion] generate_signal failed for {symbol}: {exc}")
            return None
