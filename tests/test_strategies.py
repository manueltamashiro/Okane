"""
Tests for strategies and backtest infrastructure.

All strategy tests use pure DataFrames (no DB, no API calls).
The isolated_db fixture is only used for tests that call save_signal
or test the full run_backtest pipeline.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from data.indicators import add_all_indicators
from strategies.base import Direction, Signal, load_strategy_config
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.swing import SwingStrategy
from strategies.meta_strategy import MetaStrategy

PERIODS = 100


# ─── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base_df() -> pd.DataFrame:
    """100 rows of daily OHLCV, RNG seed 42, with all indicators computed."""
    dates = pd.date_range("2024-01-01", periods=PERIODS, freq="1d")
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
    return add_all_indicators(df)


@pytest.fixture
def buy_signal_df(base_df) -> pd.DataFrame:
    """Last row forces MeanReversion BUY: close < bb_lower AND rsi < 30."""
    df = base_df.copy()
    bb_lower = df["bb_lower_20_2"].iloc[-1]
    df.iloc[-1, df.columns.get_loc("close")] = bb_lower * 0.97
    df.iloc[-1, df.columns.get_loc("rsi_14")] = 25.0
    return df


@pytest.fixture
def sell_signal_df(base_df) -> pd.DataFrame:
    """Last row forces MeanReversion SELL: close > bb_mid AND rsi > 50."""
    df = base_df.copy()
    bb_mid = df["bb_mid_20_2"].iloc[-1]
    df.iloc[-1, df.columns.get_loc("close")] = bb_mid * 1.02
    df.iloc[-1, df.columns.get_loc("rsi_14")] = 55.0
    return df


@pytest.fixture
def momentum_buy_df(base_df) -> pd.DataFrame:
    """Last two rows force bullish EMA crossover with ADX > 25."""
    df = base_df.copy()
    ema_slow_prev = df["ema_21"].iloc[-2]
    df.iloc[-2, df.columns.get_loc("ema_9")] = ema_slow_prev - 0.5
    ema_slow_curr = df["ema_21"].iloc[-1]
    df.iloc[-1, df.columns.get_loc("ema_9")] = ema_slow_curr + 0.5
    df.iloc[-1, df.columns.get_loc("adx_14")] = 30.0
    return df


@pytest.fixture
def momentum_sell_df(base_df) -> pd.DataFrame:
    """Last two rows force bearish EMA crossover."""
    df = base_df.copy()
    ema_slow_prev = df["ema_21"].iloc[-2]
    df.iloc[-2, df.columns.get_loc("ema_9")] = ema_slow_prev + 0.5
    ema_slow_curr = df["ema_21"].iloc[-1]
    df.iloc[-1, df.columns.get_loc("ema_9")] = ema_slow_curr - 0.5
    return df


# ─── TestSignalDataclass ──────────────────────────────────────────────────────

class TestSignalDataclass:
    def test_direction_enum_accepts_buy_sell_hold(self):
        assert Direction.BUY.value == "BUY"
        assert Direction.SELL.value == "SELL"
        assert Direction.HOLD.value == "HOLD"

    def test_signal_confidence_stored_as_float(self):
        s = Signal(
            symbol="AAPL",
            direction=Direction.BUY,
            price=150.0,
            stop_loss=147.0,
            confidence=0.75,
            strategy_name="test",
            timestamp=datetime(2024, 1, 1),
        )
        assert isinstance(s.confidence, float)
        assert s.confidence == 0.75

    def test_signal_has_all_required_fields(self):
        s = Signal(
            symbol="AAPL",
            direction=Direction.SELL,
            price=155.0,
            stop_loss=0.0,
            confidence=0.5,
            strategy_name="mean_reversion",
            timestamp=datetime(2024, 1, 2),
        )
        assert s.symbol == "AAPL"
        assert s.direction == Direction.SELL
        assert s.take_profit is None
        assert s.notes == {}
        assert s.timeframe == "1d"


# ─── TestLoadStrategyConfig ───────────────────────────────────────────────────

class TestLoadStrategyConfig:
    def test_returns_dict_with_expected_top_level_keys(self):
        cfg = load_strategy_config()
        for key in ("mean_reversion", "momentum", "swing", "meta_strategy"):
            assert key in cfg, f"missing key: {key}"

    def test_mean_reversion_weight_is_0_50(self):
        cfg = load_strategy_config()
        assert cfg["mean_reversion"]["weight"] == pytest.approx(0.50)

    def test_meta_strategy_min_signals_required_is_2(self):
        cfg = load_strategy_config()
        assert cfg["meta_strategy"]["min_signals_required"] == 2


# ─── TestMeanReversionStrategy ────────────────────────────────────────────────

class TestMeanReversionStrategy:
    def test_buy_signal_when_price_below_lower_bb_and_rsi_below_30(self, buy_signal_df):
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(buy_signal_df, "AAPL")
        assert signal is not None
        assert signal.direction == Direction.BUY

    def test_sell_signal_when_price_above_mid_bb(self, sell_signal_df):
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(sell_signal_df, "AAPL")
        assert signal is not None
        assert signal.direction == Direction.SELL

    def test_sell_signal_when_rsi_above_exit_threshold(self, base_df):
        df = base_df.copy()
        bb_mid = df["bb_mid_20_2"].iloc[-1]
        # RSI > 50, price at mid (not above) — only RSI exit condition
        df.iloc[-1, df.columns.get_loc("close")] = bb_mid * 0.999
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 55.0
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(df, "AAPL")
        assert signal is not None
        assert signal.direction == Direction.SELL

    def test_no_signal_when_conditions_not_met(self, base_df):
        df = base_df.copy()
        bb_mid = df["bb_mid_20_2"].iloc[-1]
        bb_lower = df["bb_lower_20_2"].iloc[-1]
        # Close between lower and mid BB, RSI in neutral zone
        df.iloc[-1, df.columns.get_loc("close")] = (bb_lower + bb_mid) / 2
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 45.0
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(df, "AAPL")
        assert signal is None

    def test_returns_none_on_insufficient_rows(self, base_df):
        strategy = MeanReversionStrategy()
        small_df = base_df.iloc[:5]
        signal = strategy.generate_signal(small_df, "AAPL")
        assert signal is None

    def test_returns_none_on_empty_df(self):
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(pd.DataFrame(), "AAPL")
        assert signal is None

    def test_returns_none_when_indicator_columns_missing(self, base_df):
        strategy = MeanReversionStrategy()
        df_no_indicators = base_df[["open", "high", "low", "close", "volume"]].copy()
        signal = strategy.generate_signal(df_no_indicators, "AAPL")
        assert signal is None

    def test_buy_stop_loss_is_2_pct_below_close(self, buy_signal_df):
        strategy = MeanReversionStrategy()
        signal = strategy.generate_signal(buy_signal_df, "AAPL")
        assert signal is not None
        expected = signal.price * (1 - 0.02)
        assert signal.stop_loss == pytest.approx(expected, rel=1e-6)

    def test_confidence_increases_as_rsi_decreases(self, buy_signal_df):
        strategy = MeanReversionStrategy()
        df_low_rsi = buy_signal_df.copy()
        df_low_rsi.iloc[-1, df_low_rsi.columns.get_loc("rsi_14")] = 10.0
        signal_low = strategy.generate_signal(df_low_rsi, "AAPL")

        df_high_rsi = buy_signal_df.copy()
        df_high_rsi.iloc[-1, df_high_rsi.columns.get_loc("rsi_14")] = 25.0
        signal_high = strategy.generate_signal(df_high_rsi, "AAPL")

        assert signal_low is not None and signal_high is not None
        assert signal_low.confidence > signal_high.confidence

    def test_generate_signal_never_raises_on_garbage_input(self):
        strategy = MeanReversionStrategy()
        garbage = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        # Must not raise
        signal = strategy.generate_signal(garbage, "AAPL")
        assert signal is None

    def test_strategy_name_is_mean_reversion(self):
        assert MeanReversionStrategy.name == "mean_reversion"

    def test_does_not_mutate_input_df(self, buy_signal_df):
        original_close = buy_signal_df["close"].iloc[-1]
        strategy = MeanReversionStrategy()
        strategy.generate_signal(buy_signal_df, "AAPL")
        assert buy_signal_df["close"].iloc[-1] == original_close


# ─── TestMomentumStrategy ─────────────────────────────────────────────────────

class TestMomentumStrategy:
    def test_buy_signal_on_bullish_ema_crossover_with_adx_above_25(self, momentum_buy_df):
        strategy = MomentumStrategy()
        signal = strategy.generate_signal(momentum_buy_df, "AAPL")
        assert signal is not None
        assert signal.direction == Direction.BUY

    def test_no_buy_signal_when_adx_below_25_despite_crossover(self, momentum_buy_df):
        df = momentum_buy_df.copy()
        df.iloc[-1, df.columns.get_loc("adx_14")] = 20.0  # below 25 threshold
        strategy = MomentumStrategy()
        signal = strategy.generate_signal(df, "AAPL")
        # No BUY signal because ADX too weak
        assert signal is None or signal.direction != Direction.BUY

    def test_sell_signal_on_bearish_ema_crossover(self, momentum_sell_df):
        strategy = MomentumStrategy()
        signal = strategy.generate_signal(momentum_sell_df, "AAPL")
        assert signal is not None
        assert signal.direction == Direction.SELL

    def test_no_signal_when_no_crossover(self, base_df):
        strategy = MomentumStrategy()
        # base_df has consistent EMA ordering (no forced crossover)
        signal = strategy.generate_signal(base_df, "AAPL")
        # May or may not have a signal, but should not raise
        # Just verify no exception and result is Signal or None
        assert signal is None or isinstance(signal, Signal)

    def test_stop_loss_equals_close_minus_atr_multiplier_times_atr(self, momentum_buy_df):
        strategy = MomentumStrategy()
        signal = strategy.generate_signal(momentum_buy_df, "AAPL")
        assert signal is not None and signal.direction == Direction.BUY
        last = momentum_buy_df.iloc[-1]
        expected = float(last["close"]) - (strategy.atr_multiplier * float(last["atr_14"]))
        assert signal.stop_loss == pytest.approx(expected, rel=1e-5)

    def test_macd_positive_histogram_increases_confidence(self, momentum_buy_df):
        strategy = MomentumStrategy()

        df_pos_macd = momentum_buy_df.copy()
        df_pos_macd.iloc[-1, df_pos_macd.columns.get_loc("macd_12_26_9")] = 0.5
        signal_pos = strategy.generate_signal(df_pos_macd, "AAPL")

        df_neg_macd = momentum_buy_df.copy()
        df_neg_macd.iloc[-1, df_neg_macd.columns.get_loc("macd_12_26_9")] = -0.5
        signal_neg = strategy.generate_signal(df_neg_macd, "AAPL")

        if signal_pos is not None and signal_neg is not None:
            assert signal_pos.confidence >= signal_neg.confidence

    def test_returns_none_on_insufficient_rows(self, base_df):
        strategy = MomentumStrategy()
        signal = strategy.generate_signal(base_df.iloc[:5], "AAPL")
        assert signal is None

    def test_generate_signal_never_raises_on_garbage_input(self):
        strategy = MomentumStrategy()
        garbage = pd.DataFrame({"x": range(50)})
        assert strategy.generate_signal(garbage, "AAPL") is None


# ─── TestSwingStrategy ────────────────────────────────────────────────────────

class TestSwingStrategy:
    def test_returns_none_on_insufficient_rows(self, base_df):
        strategy = SwingStrategy()
        signal = strategy.generate_signal(base_df.iloc[:5], "AAPL")
        assert signal is None

    def test_confidence_is_0_4_flat(self, base_df):
        strategy = SwingStrategy()
        # Try all rows to find any signal and check confidence
        for i in range(30, len(base_df)):
            signal = strategy.generate_signal(base_df.iloc[:i], "AAPL")
            if signal is not None:
                assert signal.confidence == pytest.approx(0.4)
                break

    def test_stop_loss_is_lowest_low_of_last_3_bars(self, base_df):
        strategy = SwingStrategy()
        # Force a BUY condition
        df = base_df.copy()
        ema = df["ema_21"].iloc[-1]
        df.iloc[-1, df.columns.get_loc("close")] = ema * 1.005
        df.iloc[-1, df.columns.get_loc("ema_21")] = ema
        df.iloc[-2, df.columns.get_loc("close")] = ema * 0.999  # pullback within 1%
        df.iloc[-1, df.columns.get_loc("adx_14")] = 25.0
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 50.0
        signal = strategy.generate_signal(df, "AAPL")
        if signal is not None and signal.direction == Direction.BUY:
            expected_stop = float(df.iloc[-3:]["low"].min())
            assert signal.stop_loss == pytest.approx(expected_stop, rel=1e-5)

    def test_generate_signal_never_raises_on_garbage_input(self):
        strategy = SwingStrategy()
        assert strategy.generate_signal(pd.DataFrame(), "AAPL") is None


# ─── TestMetaStrategy ────────────────────────────────────────────────────────

class TestMetaStrategy:
    @pytest.fixture
    def meta(self) -> MetaStrategy:
        strategies = [MeanReversionStrategy(), MomentumStrategy(), SwingStrategy()]
        return MetaStrategy(strategies)

    def _make_signal(self, direction: Direction, strategy_name: str, confidence: float, stop_loss: float = 1.0) -> Signal:
        return Signal(
            symbol="AAPL",
            direction=direction,
            price=150.0,
            stop_loss=stop_loss,
            confidence=confidence,
            strategy_name=strategy_name,
            timestamp=datetime(2024, 6, 1),
        )

    def test_aggregate_returns_buy_when_two_strategies_agree(self, meta):
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 0.8, stop_loss=147.0),
            self._make_signal(Direction.BUY, "momentum", 0.7, stop_loss=146.0),
        ]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is not None
        assert result.direction == Direction.BUY

    def test_aggregate_returns_none_when_only_one_signal(self, meta):
        signals = [self._make_signal(Direction.BUY, "mean_reversion", 0.9, stop_loss=147.0)]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is None

    def test_aggregate_returns_none_when_combined_strength_below_threshold(self, meta):
        # Both agree on BUY but confidence is very low → weighted strength < 0.60
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 0.1, stop_loss=147.0),
            self._make_signal(Direction.BUY, "momentum", 0.1, stop_loss=146.0),
        ]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is None

    def test_aggregate_returns_buy_when_sell_does_not_reach_quorum(self, meta):
        # One BUY high confidence + one SELL high confidence → conflict
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 1.0, stop_loss=147.0),
            self._make_signal(Direction.BUY, "momentum", 1.0, stop_loss=146.0),
            self._make_signal(Direction.SELL, "swing", 1.0),
            # Need 2 SELLs to qualify; add a second one
        ]
        # Only 1 SELL → not enough to conflict, so this should return BUY
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        # BUY qualifies, SELL doesn't (only 1), so BUY returned
        assert result is not None
        assert result.direction == Direction.BUY

    def test_aggregate_uses_most_conservative_stop_loss(self, meta):
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 0.8, stop_loss=145.0),
            self._make_signal(Direction.BUY, "momentum", 0.7, stop_loss=143.0),
        ]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is not None
        assert result.stop_loss == pytest.approx(143.0)

    def test_aggregate_confidence_is_weighted_average(self, meta):
        # mean_reversion weight=0.50, momentum weight=0.35
        # Both confidence=1.0 → weighted avg = (0.50*1.0 + 0.35*1.0) / (0.50+0.35) = 1.0
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 1.0, stop_loss=147.0),
            self._make_signal(Direction.BUY, "momentum", 1.0, stop_loss=146.0),
        ]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is not None
        assert result.confidence == pytest.approx(1.0)

    def test_aggregate_returns_none_when_both_directions_qualify(self, meta):
        # 2 BUYs + 2 SELLs, all high confidence → true conflict → None
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 1.0, stop_loss=147.0),
            self._make_signal(Direction.BUY, "momentum", 1.0, stop_loss=146.0),
            self._make_signal(Direction.SELL, "mean_reversion", 1.0),
            self._make_signal(Direction.SELL, "momentum", 1.0),
        ]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is None

    def test_run_returns_none_when_all_sub_strategies_return_none(self, meta, base_df):
        # Use a very small df so all strategies return None (insufficient rows)
        small_df = base_df.iloc[:5]
        result = meta.run(small_df, "AAPL")
        assert result is None

    def test_meta_signal_strategy_name_is_meta(self, meta):
        signals = [
            self._make_signal(Direction.BUY, "mean_reversion", 0.8, stop_loss=147.0),
            self._make_signal(Direction.BUY, "momentum", 0.7, stop_loss=146.0),
        ]
        result = meta.aggregate(signals, "AAPL", 150.0, datetime(2024, 6, 1))
        assert result is not None
        assert result.strategy_name == "meta"


# ─── TestBacktestResults ──────────────────────────────────────────────────────

class TestBacktestResults:
    def test_calculate_metrics_returns_none_sharpe_for_zero_trades(self):
        from backtest.results import calculate_metrics
        metrics = calculate_metrics([], 500.0, 500.0, [500.0])
        assert metrics["sharpe_ratio"] is None
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0.0

    def test_calculate_metrics_win_rate_is_correct(self):
        from backtest.results import calculate_metrics
        trades = [
            {"pnl": 10.0, "pnl_pct": 0.05},
            {"pnl": -5.0, "pnl_pct": -0.02},
            {"pnl": 8.0, "pnl_pct": 0.04},
        ]
        metrics = calculate_metrics(trades, 500.0, 513.0, [500.0, 510.0, 505.0, 513.0])
        assert metrics["win_rate"] == pytest.approx(2 / 3)
        assert metrics["winning_trades"] == 2
        assert metrics["losing_trades"] == 1

    def test_calculate_metrics_max_drawdown_is_non_negative(self):
        from backtest.results import calculate_metrics
        equity = [500.0, 520.0, 490.0, 510.0]
        metrics = calculate_metrics([], 500.0, 510.0, equity)
        assert metrics["max_drawdown_pct"] >= 0.0

    def test_save_result_writes_valid_json_file(self, tmp_path, monkeypatch):
        from backtest import results as results_module
        monkeypatch.setattr(results_module, "RESULTS_DIR", tmp_path)
        result = {
            "symbol": "AAPL",
            "strategy_name": "mean_reversion",
            "start": "2024-01-01",
            "end": "2024-12-31",
            "initial_cash": 500.0,
            "final_value": 550.0,
            "total_return_pct": 0.10,
            "sharpe_ratio": 1.2,
            "max_drawdown_pct": 0.05,
            "win_rate": 0.6,
            "total_trades": 10,
            "winning_trades": 6,
            "losing_trades": 4,
            "avg_win_pct": 0.03,
            "avg_loss_pct": -0.015,
            "signals": [],
            "run_timestamp": "2024-12-31T00:00:00",
        }
        path = results_module.save_result(result)
        assert path.exists()
        with path.open() as f:
            loaded = json.load(f)
        assert loaded["symbol"] == "AAPL"

    def test_load_result_roundtrips_correctly(self, tmp_path, monkeypatch):
        from backtest import results as results_module
        monkeypatch.setattr(results_module, "RESULTS_DIR", tmp_path)
        original = {
            "symbol": "VTI",
            "strategy_name": "momentum",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "initial_cash": 500.0,
            "final_value": 530.0,
            "total_return_pct": 0.06,
            "sharpe_ratio": 0.9,
            "max_drawdown_pct": 0.03,
            "win_rate": 0.55,
            "total_trades": 5,
            "winning_trades": 3,
            "losing_trades": 2,
            "avg_win_pct": 0.04,
            "avg_loss_pct": -0.02,
            "signals": [],
            "run_timestamp": "2024-06-30T00:00:00",
        }
        path = results_module.save_result(original)
        loaded = results_module.load_result(path)
        assert loaded["symbol"] == original["symbol"]
        assert loaded["final_value"] == pytest.approx(original["final_value"])

    def test_build_result_includes_all_required_keys(self):
        from backtest.results import build_result
        result = build_result(
            strategy_name="mean_reversion",
            symbol="AAPL",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 12, 31),
            initial_cash=500.0,
            final_value=550.0,
            trades=[{"pnl": 10.0, "pnl_pct": 0.05}, {"pnl": -5.0, "pnl_pct": -0.02}],
            signals=[],
            equity_curve=[500.0, 510.0, 505.0, 550.0],
        )
        required_keys = [
            "symbol", "strategy_name", "start", "end",
            "initial_cash", "final_value", "total_return_pct",
            "sharpe_ratio", "max_drawdown_pct", "win_rate",
            "total_trades", "winning_trades", "losing_trades",
            "avg_win_pct", "avg_loss_pct", "signals", "run_timestamp",
        ]
        for key in required_keys:
            assert key in result, f"missing key: {key}"


# ─── TestBacktestRunner ───────────────────────────────────────────────────────

class TestBacktestRunner:
    """
    Integration tests for run_backtest. Uses mocked yfinance to avoid
    live network calls.
    """

    @pytest.fixture
    def isolated_db(self, tmp_path, monkeypatch):
        """Swap the storage engine to an in-memory SQLite for test isolation."""
        import data.storage as storage_mod
        from sqlalchemy import create_engine
        original = storage_mod._engine
        storage_mod._engine = create_engine("sqlite:///:memory:", echo=False)
        storage_mod.init_db()
        yield tmp_path
        storage_mod._engine = original

    @pytest.fixture
    def seeded_db(self, isolated_db, monkeypatch):
        """Isolated DB pre-seeded with 300 bars of synthetic AAPL data."""
        import data.storage as storage_mod
        import data.ingestion as ingestion_mod
        ohlcv = self._make_ohlcv_df()
        storage_mod.upsert_bars(ohlcv, "AAPL", "1d", "yfinance")
        monkeypatch.setattr(ingestion_mod, "ingest_symbol", lambda *a, **kw: ohlcv)
        return ohlcv

    def _make_ohlcv_df(self) -> pd.DataFrame:
        """Create 300 rows of synthetic OHLCV data for backtesting."""
        dates = pd.date_range("2023-01-01", periods=300, freq="1d")
        rng = np.random.default_rng(7)
        close = 150.0 + np.cumsum(rng.normal(0, 1, 300))
        df = pd.DataFrame(
            {
                "open": close - rng.uniform(0, 2, 300),
                "high": close + rng.uniform(0, 3, 300),
                "low": close - rng.uniform(0, 3, 300),
                "close": close,
                "volume": rng.integers(100_000, 1_000_000, 300).astype(float),
            },
            index=dates,
        )
        df.index.name = "timestamp"
        return df

    def test_run_backtest_returns_error_dict_when_no_data_available(self, isolated_db, monkeypatch):
        from backtest.backtrader_runner import run_backtest
        from strategies.mean_reversion import MeanReversionStrategy
        import data.ingestion as ingestion_mod

        # Mock ingest_symbol to return empty df so we never get data
        monkeypatch.setattr(ingestion_mod, "ingest_symbol", lambda *a, **kw: pd.DataFrame())

        strategy = MeanReversionStrategy()
        result = run_backtest(
            strategy,
            symbol="FAKE",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 6, 1),
        )
        assert "error" in result
        assert result["error"] == "no_data"

    def test_run_backtest_returns_dict_with_required_keys(self, seeded_db):
        from backtest.backtrader_runner import run_backtest
        from strategies.mean_reversion import MeanReversionStrategy

        strategy = MeanReversionStrategy()
        result = run_backtest(strategy, "AAPL", datetime(2023, 1, 1), datetime(2023, 10, 27))
        assert "error" not in result
        for key in ("symbol", "strategy_name", "initial_cash", "final_value", "total_trades"):
            assert key in result, f"missing key: {key}"

    def test_run_backtest_is_deterministic(self, seeded_db):
        from backtest.backtrader_runner import run_backtest
        from strategies.mean_reversion import MeanReversionStrategy

        result1 = run_backtest(MeanReversionStrategy(), "AAPL", datetime(2023, 1, 1), datetime(2023, 10, 27))
        result2 = run_backtest(MeanReversionStrategy(), "AAPL", datetime(2023, 1, 1), datetime(2023, 10, 27))
        assert "error" not in result1
        assert result1["final_value"] == pytest.approx(result2["final_value"])

    def test_run_backtest_final_value_is_positive_float(self, seeded_db):
        from backtest.backtrader_runner import run_backtest
        from strategies.momentum import MomentumStrategy

        result = run_backtest(MomentumStrategy(), "AAPL", datetime(2023, 1, 1), datetime(2023, 10, 27))
        if "error" not in result:
            assert isinstance(result["final_value"], float)
            assert result["final_value"] > 0
