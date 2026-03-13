"""
Market data ingestion from Alpaca (primary) and yfinance (fallback).

Canonical internal timeframe strings:
  '1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'

Both sources normalize column names to lowercase before returning.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from loguru import logger

from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY

# ---------------------------------------------------------------------------
# Timeframe conversion helpers
# ---------------------------------------------------------------------------

# Internal → Alpaca TimeFrame objects
def _alpaca_timeframe(timeframe: str):
    """Convert internal timeframe string to alpaca-py TimeFrame object."""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    mapping = {
        "1m":  TimeFrame.Minute,
        "5m":  TimeFrame(5,  TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h":  TimeFrame.Hour,
        "4h":  TimeFrame(4,  TimeFrameUnit.Hour),
        "1d":  TimeFrame.Day,
        "1w":  TimeFrame.Week,
    }
    if timeframe not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}. Choose from {list(mapping)}")
    return mapping[timeframe]


# Internal → yfinance interval strings
_YFINANCE_INTERVAL: dict[str, str] = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "4h",    # yfinance does not natively support 4h; caller should use 1h and resample
    "1d":  "1d",
    "1w":  "1wk",
}


# ---------------------------------------------------------------------------
# Alpaca data client
# ---------------------------------------------------------------------------

class AlpacaClient:
    """
    Wraps alpaca-py StockHistoricalDataClient for OHLCV bar retrieval.

    Uses DataFeed.IEX (free tier). With a paid subscription, switch to SIP
    by changing the feed parameter.
    """

    def __init__(self) -> None:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.enums import DataFeed

        self._feed = DataFeed.IEX
        self._client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY or None,
            secret_key=ALPACA_SECRET_KEY or None,
        )
        logger.debug("AlpacaClient initialized (feed=IEX)")

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV bars for a single symbol.

        Args:
            symbol:    Ticker e.g. 'AAPL'
            timeframe: Internal timeframe string e.g. '1d'
            start:     Start datetime (UTC)
            end:       End datetime (UTC); defaults to now
            limit:     Max number of bars; None = all available

        Returns:
            DataFrame indexed by timestamp (UTC) with lowercase columns:
            open, high, low, close, volume, vwap, trade_count
        """
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.enums import Adjustment

        if end is None:
            end = datetime.now(timezone.utc)

        tf = _alpaca_timeframe(timeframe)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            limit=limit,
            adjustment=Adjustment.ALL,
            feed=self._feed,
        )

        logger.info(f"Fetching {symbol} [{timeframe}] from Alpaca: {start.date()} → {end.date()}")
        try:
            bar_set = self._client.get_stock_bars(request)
        except Exception as exc:
            logger.error(f"Alpaca fetch failed for {symbol}: {exc}")
            raise

        if symbol not in bar_set.data or not bar_set.data[symbol]:
            logger.warning(f"No bars returned from Alpaca for {symbol} [{timeframe}]")
            return pd.DataFrame()

        # Extract from BarSet multiindex DataFrame and flatten to single symbol
        df = bar_set.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")

        # Normalize column names to lowercase
        df.columns = [c.lower() for c in df.columns]
        df.index.name = "timestamp"

        # Ensure UTC-aware index
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        logger.info(f"Alpaca: {len(df)} bars fetched for {symbol} [{timeframe}]")
        return df

    def fetch_bars_multi(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: Optional[datetime] = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch bars for multiple symbols in one API call."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.enums import Adjustment

        if end is None:
            end = datetime.now(timezone.utc)

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=_alpaca_timeframe(timeframe),
            start=start,
            end=end,
            adjustment=Adjustment.ALL,
            feed=self._feed,
        )

        logger.info(f"Fetching {symbols} [{timeframe}] from Alpaca (multi-symbol)")
        try:
            bar_set = self._client.get_stock_bars(request)
        except Exception as exc:
            logger.error(f"Alpaca multi-symbol fetch failed: {exc}")
            raise

        result: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            if sym not in bar_set.data or not bar_set.data[sym]:
                logger.warning(f"No bars for {sym} in multi-symbol response")
                result[sym] = pd.DataFrame()
                continue

            df = bar_set.df
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(sym, level="symbol")

            df.columns = [c.lower() for c in df.columns]
            df.index.name = "timestamp"

            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")

            result[sym] = df

        return result


# ---------------------------------------------------------------------------
# yfinance data client (fallback)
# ---------------------------------------------------------------------------

class YFinanceClient:
    """
    Wraps yfinance Ticker.history() for historical OHLCV retrieval.
    Used as fallback when Alpaca keys are absent or for extended history.
    """

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        period: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV bars.

        Args:
            symbol:    Ticker e.g. 'AAPL'
            timeframe: Internal timeframe string e.g. '1d'
            start:     Start date; mutually exclusive with period
            end:       End date
            period:    yfinance period string e.g. '1y', '2y', 'max'

        Returns:
            DataFrame indexed by timestamp (UTC) with lowercase columns:
            open, high, low, close, volume
        """
        if timeframe not in _YFINANCE_INTERVAL:
            raise ValueError(f"Unsupported timeframe for yfinance: {timeframe!r}")

        interval = _YFINANCE_INTERVAL[timeframe]

        # yfinance doesn't support native 4h; resample from 1h
        resample_4h = timeframe == "4h"
        fetch_interval = "1h" if resample_4h else interval

        ticker = yf.Ticker(symbol)
        logger.info(f"Fetching {symbol} [{timeframe}] from yfinance")

        try:
            df = ticker.history(
                start=start,
                end=end,
                period=period,
                interval=fetch_interval,
                auto_adjust=True,
                prepost=False,
            )
        except Exception as exc:
            logger.error(f"yfinance fetch failed for {symbol}: {exc}")
            raise

        if df.empty:
            logger.warning(f"No data returned from yfinance for {symbol} [{timeframe}]")
            return pd.DataFrame()

        # Normalize column names to lowercase
        df.columns = [c.lower() for c in df.columns]

        # Drop yfinance-specific columns not needed
        df = df[["open", "high", "low", "close", "volume"]].copy()

        # Ensure UTC-aware index
        df.index.name = "timestamp"
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        # Resample 1h → 4h if needed
        if resample_4h:
            df = _resample_ohlcv(df, "4h")

        logger.info(f"yfinance: {len(df)} bars fetched for {symbol} [{timeframe}]")
        return df


def _resample_ohlcv(df: pd.DataFrame, target: str) -> pd.DataFrame:
    """Resample OHLCV data to a larger timeframe."""
    rule_map = {"4h": "4h", "1d": "1d", "1w": "7D"}
    rule = rule_map.get(target, target)
    resampled = df.resample(rule).agg(
        {
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }
    ).dropna()
    return resampled


# ---------------------------------------------------------------------------
# High-level ingestion pipeline
# ---------------------------------------------------------------------------

def ingest_symbol(
    symbol: str,
    timeframe: str = "1d",
    lookback_days: int = 365,
    use_alpaca: bool = True,
) -> pd.DataFrame:
    """
    Fetch bars for a symbol, store to DB, and return the DataFrame.

    Tries Alpaca first (if keys available and use_alpaca=True), falls back to yfinance.

    Args:
        symbol:       Ticker e.g. 'AAPL'
        timeframe:    Internal timeframe string e.g. '1d'
        lookback_days: How many calendar days of history to fetch
        use_alpaca:   Whether to attempt Alpaca first

    Returns:
        DataFrame of fetched bars (not necessarily all stored — duplicates skipped).
    """
    from data.storage import upsert_bars

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    df = pd.DataFrame()
    source = "unknown"

    if use_alpaca and ALPACA_API_KEY:
        try:
            client = AlpacaClient()
            df = client.fetch_bars(symbol, timeframe, start=start, end=end)
            source = "alpaca"
        except Exception as exc:
            logger.warning(f"Alpaca failed ({exc}), falling back to yfinance")

    if df.empty:
        try:
            yf_client = YFinanceClient()
            df = yf_client.fetch_bars(symbol, timeframe, start=start, end=end)
            source = "yfinance"
        except Exception as exc:
            logger.error(f"yfinance also failed for {symbol}: {exc}")
            return pd.DataFrame()

    if df.empty:
        logger.warning(f"No data fetched for {symbol} [{timeframe}]")
        return df

    inserted = upsert_bars(df, symbol=symbol, timeframe=timeframe, source=source)
    logger.info(f"Ingested {symbol} [{timeframe}]: {inserted} new bars stored (source={source})")
    return df


def ingest_universe(
    symbols: list[str],
    timeframe: str = "1d",
    lookback_days: int = 365,
    use_alpaca: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Ingest data for all symbols in the universe.

    Returns:
        Dict mapping symbol → DataFrame of fetched bars.
    """
    results: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            df = ingest_symbol(
                symbol,
                timeframe=timeframe,
                lookback_days=lookback_days,
                use_alpaca=use_alpaca,
            )
            results[symbol] = df
        except Exception as exc:
            logger.error(f"Failed to ingest {symbol}: {exc}")
            results[symbol] = pd.DataFrame()
    return results
