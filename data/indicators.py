"""
Technical indicator calculation library wrapping the `ta` package.

All indicators are calculated from a price DataFrame (indexed by timestamp,
lowercase OHLCV columns) and returned as additional columns on a copy of
that DataFrame.

Indicators used per strategy:
  Mean Reversion:  RSI(14), Bollinger Bands(20, 2σ)
  Momentum:        EMA(9), EMA(21), MACD(12,26,9), ADX(14), ATR(14)
  Swing:           All of the above across multiple timeframes

NOTE: ADXIndicator and AverageTrueRange in `ta` use Python loops internally,
not vectorized NumPy. They are fast enough for daily/hourly bars but will
become a bottleneck for large tick datasets.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from data.storage import fetch_bars, upsert_indicators


# ---------------------------------------------------------------------------
# Individual indicator functions
# ---------------------------------------------------------------------------

def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add RSI column: rsi_{period}"""
    df = df.copy()
    df[f"rsi_{period}"] = RSIIndicator(close=df["close"], window=period, fillna=False).rsi()
    return df


def add_bollinger_bands(
    df: pd.DataFrame, period: int = 20, std_dev: int = 2
) -> pd.DataFrame:
    """
    Add Bollinger Band columns:
      bb_upper_{period}_{std_dev}
      bb_mid_{period}_{std_dev}
      bb_lower_{period}_{std_dev}
      bb_pband_{period}_{std_dev}   (% position within bands, 0–1)
    """
    df = df.copy()
    suffix = f"{period}_{std_dev}"
    bb = BollingerBands(close=df["close"], window=period, window_dev=std_dev, fillna=False)
    df[f"bb_upper_{suffix}"] = bb.bollinger_hband()
    df[f"bb_mid_{suffix}"]   = bb.bollinger_mavg()
    df[f"bb_lower_{suffix}"] = bb.bollinger_lband()
    df[f"bb_pband_{suffix}"] = bb.bollinger_pband()
    return df


def add_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Add EMA column: ema_{period}"""
    df = df.copy()
    df[f"ema_{period}"] = EMAIndicator(close=df["close"], window=period, fillna=False).ema_indicator()
    return df


def add_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """
    Add MACD columns:
      macd_{fast}_{slow}_{signal}
      macd_signal_{fast}_{slow}_{signal}
      macd_hist_{fast}_{slow}_{signal}
    """
    df = df.copy()
    suffix = f"{fast}_{slow}_{signal}"
    m = MACD(close=df["close"], window_fast=fast, window_slow=slow, window_sign=signal, fillna=False)
    df[f"macd_{suffix}"]        = m.macd()
    df[f"macd_signal_{suffix}"] = m.macd_signal()
    df[f"macd_hist_{suffix}"]   = m.macd_diff()
    return df


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Add ADX columns:
      adx_{period}       (trend strength 0–100; > 25 means strong trend)
      adx_pos_{period}   (+DI)
      adx_neg_{period}   (-DI)

    Requires high, low, close columns.
    Warmup: needs at least 2x period rows before values stabilize.
    """
    df = df.copy()
    adx = ADXIndicator(
        high=df["high"], low=df["low"], close=df["close"],
        window=period, fillna=False,
    )
    df[f"adx_{period}"]     = adx.adx()
    df[f"adx_pos_{period}"] = adx.adx_pos()
    df[f"adx_neg_{period}"] = adx.adx_neg()
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Add ATR column: atr_{period}
    Used for trailing stop calculation: stop = entry - (multiplier × ATR).
    Requires high, low, close columns.
    """
    df = df.copy()
    df[f"atr_{period}"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"],
        window=period, fillna=False,
    ).average_true_range()
    return df


# ---------------------------------------------------------------------------
# Strategy-level indicator sets
# ---------------------------------------------------------------------------

def add_mean_reversion_indicators(
    df: pd.DataFrame,
    rsi_period: int = 14,
    bb_period: int = 20,
    bb_std: int = 2,
) -> pd.DataFrame:
    """Add all indicators needed for the mean reversion strategy."""
    df = add_rsi(df, period=rsi_period)
    df = add_bollinger_bands(df, period=bb_period, std_dev=bb_std)
    return df


def add_momentum_indicators(
    df: pd.DataFrame,
    ema_fast: int = 9,
    ema_slow: int = 21,
    adx_period: int = 14,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Add all indicators needed for the momentum strategy."""
    df = add_ema(df, period=ema_fast)
    df = add_ema(df, period=ema_slow)
    df = add_macd(df)
    df = add_adx(df, period=adx_period)
    df = add_atr(df, period=atr_period)
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add every indicator used by any strategy (union of all sets)."""
    df = add_mean_reversion_indicators(df)
    df = add_momentum_indicators(df)
    return df


# ---------------------------------------------------------------------------
# High-level pipeline: calculate + store
# ---------------------------------------------------------------------------

def calculate_and_store(
    symbol: str,
    timeframe: str,
    lookback_bars: int = 300,
) -> pd.DataFrame:
    """
    Load price data from DB, calculate all indicators, store results, return
    enriched DataFrame.

    Args:
        symbol:        Ticker e.g. 'AAPL'
        timeframe:     Internal timeframe string e.g. '1d'
        lookback_bars: Number of bars to load (more = more accurate warmup)

    Returns:
        DataFrame with all indicator columns appended (NaN for warmup rows).
    """
    df = fetch_bars(symbol, timeframe, limit=lookback_bars)

    if df.empty:
        logger.warning(f"No price data in DB for {symbol} [{timeframe}] — run ingest first")
        return df

    if len(df) < 30:
        logger.warning(
            f"Only {len(df)} bars for {symbol} [{timeframe}] — indicators may be unreliable"
        )

    df = add_all_indicators(df)

    # Identify indicator columns (everything except OHLCV source columns)
    base_cols = {"open", "high", "low", "close", "volume", "vwap", "trade_count", "source"}
    indicator_cols = [c for c in df.columns if c not in base_cols]

    upsert_indicators(df, symbol=symbol, timeframe=timeframe, indicator_cols=indicator_cols)
    logger.info(
        f"Calculated {len(indicator_cols)} indicators for {symbol} [{timeframe}] "
        f"({len(df)} bars)"
    )
    return df


def calculate_universe(
    symbols: list[str],
    timeframe: str = "1d",
    lookback_bars: int = 300,
) -> dict[str, pd.DataFrame]:
    """Calculate and store indicators for all symbols."""
    results: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            results[symbol] = calculate_and_store(symbol, timeframe, lookback_bars)
        except Exception as exc:
            logger.error(f"Indicator calculation failed for {symbol}: {exc}")
            results[symbol] = pd.DataFrame()
    return results
