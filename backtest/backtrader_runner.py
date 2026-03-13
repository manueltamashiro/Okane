from __future__ import annotations

from datetime import datetime
from typing import Any

import backtrader as bt
import pandas as pd
from loguru import logger

from backtest.results import build_result
from data.indicators import add_all_indicators
from data.ingestion import ingest_symbol
from data.storage import fetch_bars, init_db
from strategies.base import Direction, Signal, Strategy


class _BtStrategyAdapter(bt.Strategy):
    """
    Internal Backtrader strategy that delegates signal generation to a
    domain Strategy instance. Signals and trade log stored on self
    for extraction after the run.
    """
    params = (
        ("domain_strategy", None),
        ("symbol", ""),
        ("enriched_df", None),
    )

    def __init__(self) -> None:
        self.signals: list[Signal] = []
        self.trades: list[dict[str, Any]] = []
        self.equity_curve: list[float] = []
        self._entry_price: float | None = None
        # PHASE 3 TODO: track _entry_date to compute trade duration for swing strategy validation

    def next(self) -> None:
        self.equity_curve.append(self.broker.getvalue())
        idx = len(self.data)
        df_slice = self.p.enriched_df.iloc[:idx]
        if len(df_slice) < 2:
            return

        try:
            signal = self.p.domain_strategy.generate_signal(df_slice, self.p.symbol)
        except Exception as exc:
            logger.warning(f"[backtest] generate_signal error: {exc}")
            return

        if signal is None:
            return

        if signal.direction == Direction.BUY and not self.position:
            # Size: 95% of available cash at current close price
            close = self.data.close[0]
            cash = self.broker.getcash()
            size = int((cash * 0.95) / close)
            if size > 0:
                self.buy(size=size)
                self._entry_price = close
                self.signals.append(signal)

        elif signal.direction == Direction.SELL and self.position:
            self.close()
            self.signals.append(signal)

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return
        entry = self._entry_price or trade.price
        pnl = trade.pnl
        pnl_pct = pnl / (entry * trade.size) if (entry and trade.size) else 0.0
        self.trades.append({
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "size": trade.size,
            "commission": trade.commission,
        })
        self._entry_price = None


def run_backtest(
    strategy: Strategy,
    symbol: str,
    start: datetime,
    end: datetime,
    initial_cash: float = 500.0,
    timeframe: str = "1d",
    commission: float = 0.0,
) -> dict[str, Any]:
    """
    Execute a backtest for a single strategy on a single symbol.

    Fetches data from SQLite; ingests via yfinance if not present.
    Returns a results dict (see backtest/results.py for schema).
    """
    init_db()

    # Fetch data from DB
    df = fetch_bars(symbol, timeframe, start=start, end=end)
    if df.empty:
        logger.info(f"[backtest] no data in DB for {symbol}, ingesting via yfinance...")
        ingest_symbol(symbol, timeframe=timeframe, use_alpaca=False)
        df = fetch_bars(symbol, timeframe, start=start, end=end)

    if df.empty:
        logger.warning(f"[backtest] no data available for {symbol} {timeframe}")
        return {
            "error": "no_data",
            "symbol": symbol,
            "strategy_name": strategy.name,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    # Need enough bars for indicator warm-up (longest window is ~26 for MACD)
    MIN_BARS = 30
    if len(df) < MIN_BARS:
        logger.warning(f"[backtest] only {len(df)} bars for {symbol} (need {MIN_BARS}+)")
        return {
            "error": f"insufficient_data: only {len(df)} bars (need {MIN_BARS}+). Try a wider date range.",
            "symbol": symbol,
            "strategy_name": strategy.name,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    # Compute indicators
    enriched_df = add_all_indicators(df)

    # Build Backtrader feed (OHLCV only for the feed, pass enriched separately)
    feed_df = enriched_df[["open", "high", "low", "close", "volume"]].copy()

    data_feed = bt.feeds.PandasData(dataname=feed_df)

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.adddata(data_feed)
    cerebro.addstrategy(
        _BtStrategyAdapter,
        domain_strategy=strategy,
        symbol=symbol,
        enriched_df=enriched_df,
    )

    logger.info(f"[backtest] running {strategy.name} on {symbol} {start.date()} to {end.date()}")
    results = cerebro.run()
    adapter = results[0]

    final_value = cerebro.broker.getvalue()

    return build_result(
        strategy_name=strategy.name,
        symbol=symbol,
        start=start,
        end=end,
        initial_cash=initial_cash,
        final_value=final_value,
        trades=adapter.trades,
        signals=adapter.signals,
        equity_curve=adapter.equity_curve,
    )
