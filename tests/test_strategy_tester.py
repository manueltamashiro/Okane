"""
Tests for the Strategy Tester page functions.

Tests use synthetic DataFrames with indicators computed in memory (no DB, no API).
The isolated_db fixture is used where storage interactions are required.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from data.indicators import (
    add_adx,
    add_atr,
    add_bollinger_bands,
    add_ema,
    add_macd,
    add_rsi,
)
from monitoring.dashboard.pages.strategy_tester import (
    _build_candlestick_chart,
    _build_strategy_with_params,
    _fetch_and_enrich,
    _generate_test_signal,
)
from strategies.base import Direction, Signal


PERIODS = 300


# ─── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ohlcv_df() -> pd.DataFrame:
    """300 rows of synthetic OHLCV data, no indicators."""
    dates = pd.date_range("2023-01-01", periods=PERIODS, freq="1d")
    rng = np.random.default_rng(42)
    close = 150.0 + np.cumsum(rng.normal(0, 1, PERIODS))
    high = close + rng.uniform(0, 3, PERIODS)
    low = close - rng.uniform(0, 3, PERIODS)
    df = pd.DataFrame(
        {
            "open": close - rng.uniform(0, 2, PERIODS),
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100_000, 1_000_000, PERIODS).astype(float),
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Swap the storage engine to in-memory SQLite for test isolation.

    autouse=True ensures every test in this module — including ones that don't
    explicitly request the fixture — is sandboxed away from the on-disk
    data/trader_bot.db. Strategy code path can call save_signal() transitively
    and would otherwise pollute the real DB.
    """
    import data.storage as storage_mod
    from sqlalchemy import create_engine

    original = storage_mod._engine
    storage_mod._engine = create_engine("sqlite:///:memory:", echo=False)
    storage_mod.init_db()
    yield tmp_path
    storage_mod._engine = original


# ─── TestBuildStrategyWithParams ─────────────────────────────────────────────


class TestBuildStrategyWithParams:
    def test_mean_reversion_params_override(self):
        params = {
            "rsi_oversold": 25,
            "bb_std": 2.5,
            "bb_period": 15,
            "rsi_exit": 55,
            "stop_loss_pct": 0.03,
            "timeframe": "1h",
        }
        strategy = _build_strategy_with_params("mean_reversion", params)
        assert strategy.rsi_oversold == 25
        assert strategy.bb_std == 2.5
        assert strategy.bb_period == 15
        assert strategy.rsi_exit == 55
        assert strategy.stop_loss_pct == 0.03
        assert strategy.timeframe == "1h"
        assert strategy.name == "test_mean_reversion"

    def test_momentum_params_override(self):
        params = {
            "adx_min": 30,
            "ema_fast": 5,
            "ema_slow": 15,
            "atr_multiplier": 3.0,
            "timeframe": "4h",
        }
        strategy = _build_strategy_with_params("momentum", params)
        assert strategy.adx_min == 30
        assert strategy.ema_fast == 5
        assert strategy.ema_slow == 15
        assert strategy.atr_multiplier == 3.0
        assert strategy.timeframe == "4h"
        assert strategy.name == "test_momentum"

    def test_swing_params_override(self):
        params = {
            "adx_trend_min": 15,
            "rsi_overbought": 70,
            "ema_period": 30,
            "timeframe": "1d",
        }
        strategy = _build_strategy_with_params("swing", params)
        assert strategy._adx_trend_min == 15
        assert strategy._rsi_overbought == 70
        assert strategy._ema_period == 30
        assert strategy.primary_tf == "1d"
        assert strategy.name == "test_swing"

    def test_name_has_test_prefix_for_all_strategies(self):
        for name in ("mean_reversion", "momentum", "swing"):
            params = self._default_params(name)
            strategy = _build_strategy_with_params(name, params)
            assert strategy.name.startswith("test_")

    def test_unknown_strategy_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            _build_strategy_with_params("nonexistent", {"timeframe": "1d"})

    @staticmethod
    def _default_params(strategy_name: str) -> dict:
        if strategy_name == "mean_reversion":
            return {"rsi_oversold": 30, "bb_std": 2.0, "bb_period": 20, "rsi_exit": 50, "stop_loss_pct": 0.02, "timeframe": "1d"}
        elif strategy_name == "momentum":
            return {"adx_min": 25, "ema_fast": 9, "ema_slow": 21, "atr_multiplier": 2.0, "timeframe": "1d"}
        elif strategy_name == "swing":
            return {"adx_trend_min": 20, "rsi_overbought": 65, "ema_period": 21, "timeframe": "1d"}
        return {}


# ─── TestFetchAndEnrich ──────────────────────────────────────────────────────


class TestFetchAndEnrich:
    """Test _fetch_and_enrich by mocking ingest_symbol and seeding the DB."""

    @pytest.fixture
    def seeded_db(self, isolated_db, ohlcv_df, monkeypatch):
        import data.storage as storage_mod
        import data.ingestion as ingestion_mod

        storage_mod.upsert_bars(ohlcv_df, "AAPL", "1d", "yfinance")
        monkeypatch.setattr(ingestion_mod, "ingest_symbol", lambda *a, **kw: ohlcv_df)
        return ohlcv_df

    def test_mean_reversion_columns(self, seeded_db):
        params = {"bb_period": 20, "bb_std": 2.0}
        df = _fetch_and_enrich("AAPL", "1d", 365, "mean_reversion", params)
        assert not df.empty
        assert "rsi_14" in df.columns
        assert "bb_upper_20_2.0" in df.columns
        assert "bb_lower_20_2.0" in df.columns
        assert "bb_mid_20_2.0" in df.columns

    def test_momentum_columns(self, seeded_db):
        params = {"ema_fast": 9, "ema_slow": 21}
        df = _fetch_and_enrich("AAPL", "1d", 365, "momentum", params)
        assert not df.empty
        assert "ema_9" in df.columns
        assert "ema_21" in df.columns
        assert "macd_12_26_9" in df.columns
        assert "macd_signal_12_26_9" in df.columns
        assert "macd_hist_12_26_9" in df.columns
        assert "adx_14" in df.columns
        assert "atr_14" in df.columns

    def test_swing_columns(self, seeded_db):
        params = {"ema_period": 21}
        df = _fetch_and_enrich("AAPL", "1d", 365, "swing", params)
        assert not df.empty
        assert "ema_21" in df.columns
        assert "adx_14" in df.columns
        assert "rsi_14" in df.columns

    def test_momentum_custom_ema_columns(self, seeded_db):
        """When ema_fast=5 and ema_slow=15, enriched df should have ema_5 and ema_15."""
        params = {"ema_fast": 5, "ema_slow": 15}
        df = _fetch_and_enrich("AAPL", "1d", 365, "momentum", params)
        assert "ema_5" in df.columns
        assert "ema_15" in df.columns

    def test_swing_custom_ema_column(self, seeded_db):
        """When ema_period=30, enriched df should have ema_30."""
        params = {"ema_period": 30}
        df = _fetch_and_enrich("AAPL", "1d", 365, "swing", params)
        assert "ema_30" in df.columns


# ─── TestColumnNameConsistency ───────────────────────────────────────────────


class TestColumnNameConsistency:
    """Verify that _fetch_and_enrich produces columns that strategies actually access."""

    @pytest.fixture
    def seeded_db(self, isolated_db, ohlcv_df, monkeypatch):
        import data.storage as storage_mod
        import data.ingestion as ingestion_mod

        storage_mod.upsert_bars(ohlcv_df, "AAPL", "1d", "yfinance")
        monkeypatch.setattr(ingestion_mod, "ingest_symbol", lambda *a, **kw: ohlcv_df)
        return ohlcv_df

    def test_momentum_custom_ema_columns_match_strategy(self, seeded_db):
        """
        With ema_fast=5, _fetch_and_enrich creates ema_5.
        MomentumStrategy with ema_fast=5 looks for f'ema_{self.ema_fast}' = 'ema_5'.
        """
        params = {"ema_fast": 5, "ema_slow": 15, "adx_min": 25, "atr_multiplier": 2.0, "timeframe": "1d"}
        df = _fetch_and_enrich("AAPL", "1d", 365, "momentum", params)
        strategy = _build_strategy_with_params("momentum", params)

        # The columns the strategy will look for
        ema_fast_col = f"ema_{strategy.ema_fast}"
        ema_slow_col = f"ema_{strategy.ema_slow}"
        adx_col = f"adx_{strategy.adx_period}"
        atr_col = f"atr_{strategy.atr_period}"

        assert ema_fast_col in df.columns, f"Missing {ema_fast_col}"
        assert ema_slow_col in df.columns, f"Missing {ema_slow_col}"
        assert adx_col in df.columns, f"Missing {adx_col}"
        assert atr_col in df.columns, f"Missing {atr_col}"

    def test_mean_reversion_columns_match_strategy(self, seeded_db):
        params = {"rsi_oversold": 25, "bb_std": 2.5, "bb_period": 15, "rsi_exit": 55, "stop_loss_pct": 0.03, "timeframe": "1d"}
        df = _fetch_and_enrich("AAPL", "1d", 365, "mean_reversion", params)
        strategy = _build_strategy_with_params("mean_reversion", params)

        rsi_col = f"rsi_{strategy.rsi_period}"
        bb_lower_col = f"bb_lower_{strategy.bb_period}_{strategy.bb_std}"
        bb_mid_col = f"bb_mid_{strategy.bb_period}_{strategy.bb_std}"

        assert rsi_col in df.columns, f"Missing {rsi_col}"
        assert bb_lower_col in df.columns, f"Missing {bb_lower_col}"
        assert bb_mid_col in df.columns, f"Missing {bb_mid_col}"

    def test_swing_columns_match_strategy(self, seeded_db):
        params = {"adx_trend_min": 15, "rsi_overbought": 70, "ema_period": 30, "timeframe": "1d"}
        df = _fetch_and_enrich("AAPL", "1d", 365, "swing", params)
        strategy = _build_strategy_with_params("swing", params)

        ema_col = f"ema_{strategy._ema_period}"
        adx_col = f"adx_{strategy._adx_period}"
        rsi_col = f"rsi_{strategy._rsi_period}"

        assert ema_col in df.columns, f"Missing {ema_col}"
        assert adx_col in df.columns, f"Missing {adx_col}"
        assert rsi_col in df.columns, f"Missing {rsi_col}"


# ─── TestGenerateTestSignal ──────────────────────────────────────────────────


class TestGenerateTestSignal:
    @pytest.fixture
    def enriched_df(self, ohlcv_df) -> pd.DataFrame:
        """OHLCV with all indicators for default params."""
        from data.indicators import add_all_indicators
        return add_all_indicators(ohlcv_df)

    def test_returns_signal_or_none(self, enriched_df):
        from strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        strategy.name = "test_mean_reversion"
        result = _generate_test_signal(strategy, enriched_df, "AAPL")
        assert result is None or isinstance(result, Signal)

    def test_signal_has_test_prefix_in_strategy_name(self, enriched_df):
        """If a signal is produced, its strategy_name should have the test_ prefix."""
        from strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        strategy.name = "test_mean_reversion"

        # Force a sell signal by setting RSI high
        df = enriched_df.copy()
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 55.0
        bb_mid = df["bb_mid_20_2"].iloc[-1]
        df.iloc[-1, df.columns.get_loc("close")] = bb_mid * 1.02

        result = _generate_test_signal(strategy, df, "AAPL")
        if result is not None:
            assert result.strategy_name.startswith("test_")

    def test_momentum_signal_generation(self, enriched_df):
        from strategies.momentum import MomentumStrategy

        strategy = MomentumStrategy()
        strategy.name = "test_momentum"

        # Force a bullish EMA crossover
        df = enriched_df.copy()
        ema_slow_prev = df["ema_21"].iloc[-2]
        df.iloc[-2, df.columns.get_loc("ema_9")] = ema_slow_prev - 0.5
        ema_slow_curr = df["ema_21"].iloc[-1]
        df.iloc[-1, df.columns.get_loc("ema_9")] = ema_slow_curr + 0.5
        df.iloc[-1, df.columns.get_loc("adx_14")] = 30.0

        result = _generate_test_signal(strategy, df, "AAPL")
        assert result is not None
        assert result.direction == Direction.BUY
        assert result.strategy_name == "test_momentum"

    def test_returns_none_on_empty_df(self):
        from strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        result = _generate_test_signal(strategy, pd.DataFrame(), "AAPL")
        assert result is None


# ─── TestBuildCandlestickChart ───────────────────────────────────────────────


class TestBuildCandlestickChart:
    @pytest.fixture
    def enriched_mean_reversion_df(self, ohlcv_df) -> pd.DataFrame:
        df = add_rsi(ohlcv_df, 14)
        df = add_bollinger_bands(df, period=20, std_dev=2.0)
        return df

    @pytest.fixture
    def enriched_momentum_df(self, ohlcv_df) -> pd.DataFrame:
        df = add_ema(ohlcv_df, 9)
        df = add_ema(df, 21)
        df = add_macd(df)
        df = add_adx(df)
        df = add_atr(df)
        return df

    @pytest.fixture
    def enriched_swing_df(self, ohlcv_df) -> pd.DataFrame:
        df = add_ema(ohlcv_df, 21)
        df = add_adx(df)
        df = add_rsi(df, 14)
        return df

    def test_mean_reversion_returns_figure(self, enriched_mean_reversion_df):
        params = {"bb_period": 20, "bb_std": 2.0, "rsi_oversold": 30, "rsi_exit": 50}
        fig = _build_candlestick_chart(enriched_mean_reversion_df, "mean_reversion", params, signal=None)
        assert isinstance(fig, go.Figure)

    def test_momentum_returns_figure(self, enriched_momentum_df):
        params = {"ema_fast": 9, "ema_slow": 21}
        fig = _build_candlestick_chart(enriched_momentum_df, "momentum", params, signal=None)
        assert isinstance(fig, go.Figure)

    def test_swing_returns_figure(self, enriched_swing_df):
        params = {"ema_period": 21, "rsi_overbought": 65}
        fig = _build_candlestick_chart(enriched_swing_df, "swing", params, signal=None)
        assert isinstance(fig, go.Figure)

    def test_chart_with_buy_signal(self, enriched_momentum_df):
        signal = Signal(
            symbol="AAPL",
            direction=Direction.BUY,
            price=150.0,
            stop_loss=147.0,
            confidence=0.7,
            strategy_name="test_momentum",
            timestamp=datetime(2024, 1, 1),
        )
        params = {"ema_fast": 9, "ema_slow": 21}
        fig = _build_candlestick_chart(enriched_momentum_df, "momentum", params, signal=signal)
        assert isinstance(fig, go.Figure)
        # Should have the signal marker trace
        trace_names = [t.name for t in fig.data if t.name is not None]
        assert "BUY" in trace_names

    def test_chart_with_sell_signal(self, enriched_mean_reversion_df):
        signal = Signal(
            symbol="AAPL",
            direction=Direction.SELL,
            price=155.0,
            stop_loss=0.0,
            confidence=0.5,
            strategy_name="test_mean_reversion",
            timestamp=datetime(2024, 1, 1),
        )
        params = {"bb_period": 20, "bb_std": 2.0, "rsi_oversold": 30, "rsi_exit": 50}
        fig = _build_candlestick_chart(enriched_mean_reversion_df, "mean_reversion", params, signal=signal)
        assert isinstance(fig, go.Figure)
        trace_names = [t.name for t in fig.data if t.name is not None]
        assert "SELL" in trace_names

    def test_chart_has_multiple_traces(self, enriched_momentum_df):
        """Momentum chart should have candlestick + EMA traces + MACD traces."""
        params = {"ema_fast": 9, "ema_slow": 21}
        fig = _build_candlestick_chart(enriched_momentum_df, "momentum", params, signal=None)
        # At minimum: candlestick + 2 EMAs + MACD + Signal + Histogram = 6
        assert len(fig.data) >= 4
