"""
Comprehensive pytest test suite for the Phase 1 data layer.

Covers:
  - data/storage.py  : _to_utc_naive, init_db, upsert_bars, fetch_bars,
                       upsert_indicators, fetch_indicators, circuit_breaker_state
  - data/indicators.py: add_rsi, add_bollinger_bands, add_ema, add_macd,
                        add_adx, add_atr, add_all_indicators
  - data/ingestion.py : _resample_ohlcv, YFinanceClient.fetch_bars, ingest_symbol

Test isolation: every test runs against an in-memory SQLite DB, not the real
./data/trader_bot.db, via the `isolated_db` autouse fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

import data.storage as storage_module
from data.indicators import (
    add_adx,
    add_all_indicators,
    add_atr,
    add_bollinger_bands,
    add_ema,
    add_macd,
    add_rsi,
)
from data.ingestion import YFinanceClient, _resample_ohlcv, ingest_symbol
from data.storage import (
    _to_utc_naive,
    fetch_bars,
    fetch_indicators,
    init_db,
    upsert_bars,
    upsert_indicators,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_PERIODS = 60  # number of rows in sample_ohlcv_df; referenced in assertions


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db():
    """
    Replace the global _engine singleton with an in-memory SQLite engine for
    each test. Explicitly restores to None on teardown so the real file-backed
    engine can never leak into subsequent tests via the module-level singleton.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    original = storage_module._engine
    storage_module._engine = engine
    storage_module.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            storage_module.circuit_breaker_state.insert().values(
                id=1,
                daily_loss_pct=0.0,
                weekly_loss_pct=0.0,
                max_drawdown_pct=0.0,
                daily_halt=False,
                weekly_halt=False,
                full_halt=False,
                halt_reason=None,
                last_reset_date="2026-01-01",
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
    yield engine
    storage_module._engine = original  # explicit reset — never leaves a real engine behind


@pytest.fixture
def sample_ohlcv_df():
    """SAMPLE_PERIODS rows of synthetic daily OHLCV with a UTC-naive DatetimeIndex."""
    dates = pd.date_range("2025-01-01", periods=SAMPLE_PERIODS, freq="1d")
    rng = np.random.default_rng(42)
    close = 150 + np.cumsum(rng.normal(0, 1, SAMPLE_PERIODS))
    df = pd.DataFrame(
        {
            "open": close - rng.uniform(0, 2, SAMPLE_PERIODS),
            "high": close + rng.uniform(0, 3, SAMPLE_PERIODS),
            "low": close - rng.uniform(0, 3, SAMPLE_PERIODS),
            "close": close,
            "volume": rng.integers(100_000, 1_000_000, SAMPLE_PERIODS).astype(float),
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


@pytest.fixture
def constant_ohlcv_df():
    """30 rows of flat-price OHLCV — used to test EMA on a constant series."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1d")
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0},
        index=dates,
    )
    df.index.name = "timestamp"
    return df


# ---------------------------------------------------------------------------
# Parametrized: copy-safety for all add_* indicator functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn,kwargs",
    [
        (add_rsi, {}),
        (add_bollinger_bands, {}),
        (add_ema, {"period": 9}),
        (add_macd, {}),
        (add_adx, {}),
        (add_atr, {}),
    ],
)
def test_add_indicator_does_not_mutate_input(fn, kwargs, sample_ohlcv_df):
    original_cols = list(sample_ohlcv_df.columns)
    fn(sample_ohlcv_df, **kwargs)
    assert list(sample_ohlcv_df.columns) == original_cols


@pytest.mark.parametrize(
    "fn,kwargs,expected_col",
    [
        (add_rsi, {"period": 7}, "rsi_7"),
        (add_ema, {"period": 50}, "ema_50"),
        (add_adx, {"period": 20}, "adx_20"),
        (add_atr, {"period": 7}, "atr_7"),
    ],
)
def test_add_indicator_custom_period_column_name(fn, kwargs, expected_col, sample_ohlcv_df):
    result = fn(sample_ohlcv_df, **kwargs)
    assert expected_col in result.columns


# ---------------------------------------------------------------------------
# TestToUtcNaive
# ---------------------------------------------------------------------------


class TestToUtcNaive:
    def test_utc_aware_datetime_returns_naive_with_same_utc_time(self):
        dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        result = _to_utc_naive(dt)
        assert result.tzinfo is None
        assert result == datetime(2025, 6, 15, 12, 30, 0)

    def test_utc_naive_datetime_returned_unchanged(self):
        dt = datetime(2025, 3, 10, 9, 0, 0)
        assert _to_utc_naive(dt) == dt

    def test_pd_timestamp_with_eastern_timezone_converts_to_utc(self):
        ts = pd.Timestamp("2025-08-20 18:45:00", tz="US/Eastern")
        result = _to_utc_naive(ts)
        assert result.tzinfo is None
        assert result.hour == 22   # US/Eastern in August = UTC-4
        assert result.minute == 45

    def test_pd_timestamp_utc_aware_strips_tzinfo(self):
        ts = pd.Timestamp("2025-01-01 00:00:00", tz="UTC")
        result = _to_utc_naive(ts)
        assert result.tzinfo is None
        assert result == datetime(2025, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# TestInitDb
# ---------------------------------------------------------------------------


def _count_circuit_breaker_rows(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM circuit_breaker_state")).scalar()


class TestInitDb:
    def test_creates_all_four_tables(self, isolated_db):
        storage_module.metadata.drop_all(isolated_db)
        init_db()
        with isolated_db.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
        assert {"price_data", "indicator_values", "signals", "circuit_breaker_state"} <= tables

    def test_circuit_breaker_singleton_exists_after_init_db(self, isolated_db):
        storage_module.metadata.drop_all(isolated_db)
        init_db()
        with isolated_db.connect() as conn:
            assert conn.execute(
                text("SELECT id FROM circuit_breaker_state WHERE id = 1")
            ).scalar() == 1

    def test_calling_init_db_twice_does_not_duplicate_singleton(self, isolated_db):
        storage_module.metadata.drop_all(isolated_db)
        init_db()
        init_db()
        assert _count_circuit_breaker_rows(isolated_db) == 1


# ---------------------------------------------------------------------------
# TestUpsertBars
# ---------------------------------------------------------------------------


class TestUpsertBars:
    def test_returns_correct_row_count_on_first_insert(self, isolated_db, sample_ohlcv_df):
        assert upsert_bars(sample_ohlcv_df, "AAPL", "1d", "yfinance") == SAMPLE_PERIODS

    def test_returns_zero_on_duplicate_insert(self, isolated_db, sample_ohlcv_df):
        upsert_bars(sample_ohlcv_df, "AAPL", "1d", "yfinance")
        assert upsert_bars(sample_ohlcv_df, "AAPL", "1d", "yfinance") == 0

    def test_empty_dataframe_returns_zero_and_no_db_write(self, isolated_db):
        assert upsert_bars(pd.DataFrame(), "AAPL", "1d", "yfinance") == 0
        with isolated_db.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM price_data")).scalar() == 0

    def test_optional_vwap_and_trade_count_stored_when_present(
        self, isolated_db, sample_ohlcv_df
    ):
        df = sample_ohlcv_df.copy()
        df["vwap"] = df["close"] * 1.001
        df["trade_count"] = 500
        upsert_bars(df, "AAPL", "1d", "alpaca")
        result = fetch_bars("AAPL", "1d")
        assert result["vwap"].notna().all()
        assert result["trade_count"].notna().all()

    def test_missing_vwap_and_trade_count_stored_as_null(self, isolated_db, sample_ohlcv_df):
        upsert_bars(sample_ohlcv_df, "AAPL", "1d", "yfinance")
        result = fetch_bars("AAPL", "1d")
        assert result["vwap"].isna().all()
        assert result["trade_count"].isna().all()

    def test_utc_aware_index_stored_as_utc_naive(self, isolated_db, sample_ohlcv_df):
        df_aware = sample_ohlcv_df.copy()
        df_aware.index = df_aware.index.tz_localize("UTC")
        upsert_bars(df_aware, "TSLA", "1d", "yfinance")
        result = fetch_bars("TSLA", "1d")
        assert result.index.tz is None
        assert result.index[0] == pd.Timestamp("2025-01-01")


# ---------------------------------------------------------------------------
# TestFetchBars
# ---------------------------------------------------------------------------


class TestFetchBars:
    @pytest.fixture(autouse=True)
    def _seed(self, isolated_db, sample_ohlcv_df):
        upsert_bars(sample_ohlcv_df, "AAPL", "1d", "yfinance")
        self._df = sample_ohlcv_df  # make fixture accessible for relative assertions

    def test_returns_dataframe_with_expected_columns(self):
        result = fetch_bars("AAPL", "1d")
        for col in ("open", "high", "low", "close", "volume", "vwap", "trade_count", "source"):
            assert col in result.columns

    def test_returns_correct_number_of_rows(self):
        assert len(fetch_bars("AAPL", "1d")) == SAMPLE_PERIODS

    def test_limit_returns_first_n_rows_ordered_asc(self):
        result = fetch_bars("AAPL", "1d", limit=10)
        assert len(result) == 10
        assert result.index.is_monotonic_increasing

    def test_start_filter_excludes_earlier_rows(self):
        cutoff = datetime(2025, 2, 1)
        result = fetch_bars("AAPL", "1d", start=cutoff)
        assert (result.index >= pd.Timestamp("2025-02-01")).all()

    def test_end_filter_excludes_later_rows(self):
        cutoff = datetime(2025, 1, 31)
        result = fetch_bars("AAPL", "1d", end=cutoff)
        assert (result.index <= pd.Timestamp("2025-01-31")).all()

    def test_unknown_symbol_returns_empty_dataframe(self):
        assert fetch_bars("NONEXISTENT", "1d").empty

    def test_wrong_timeframe_returns_empty_dataframe(self):
        assert fetch_bars("AAPL", "1h").empty


# ---------------------------------------------------------------------------
# TestUpsertIndicators
# ---------------------------------------------------------------------------


class TestUpsertIndicators:
    def test_returns_correct_count_of_non_nan_values(self, isolated_db):
        dates = pd.date_range("2025-01-01", periods=5, freq="1d")
        df = pd.DataFrame(
            {
                "rsi_14": [float("nan"), float("nan"), 45.0, 55.0, 60.0],
                "atr_14": [1.0, 2.0, 3.0, 4.0, 5.0],
            },
            index=dates,
        )
        df.index.name = "timestamp"
        assert upsert_indicators(df, "AAPL", "1d", ["rsi_14", "atr_14"]) == 8  # 3 + 5

    def test_nan_values_are_skipped(self, isolated_db):
        dates = pd.date_range("2025-01-01", periods=3, freq="1d")
        df = pd.DataFrame({"rsi_14": [float("nan")] * 3}, index=dates)
        df.index.name = "timestamp"
        assert upsert_indicators(df, "AAPL", "1d", ["rsi_14"]) == 0

    def test_idempotent_second_insert_returns_zero(self, isolated_db, sample_ohlcv_df):
        df = add_rsi(sample_ohlcv_df)
        upsert_indicators(df, "AAPL", "1d", ["rsi_14"])
        assert upsert_indicators(df, "AAPL", "1d", ["rsi_14"]) == 0

    def test_empty_indicator_cols_returns_zero(self, isolated_db, sample_ohlcv_df):
        assert upsert_indicators(sample_ohlcv_df, "AAPL", "1d", []) == 0


# ---------------------------------------------------------------------------
# TestFetchIndicators
# ---------------------------------------------------------------------------


class TestFetchIndicators:
    @pytest.fixture(autouse=True)
    def _seed(self, isolated_db, sample_ohlcv_df):
        df_with = add_rsi(sample_ohlcv_df)
        df_with = add_atr(df_with)
        upsert_indicators(df_with, "AAPL", "1d", ["rsi_14", "atr_14"])
        self._last_ts = sample_ohlcv_df.index[-1]  # for relative timestamp assertion

    def test_returns_wide_dataframe_with_one_column_per_indicator(self):
        result = fetch_indicators("AAPL", "1d")
        assert "rsi_14" in result.columns
        assert "atr_14" in result.columns

    def test_limit_returns_most_recent_n_timestamps_in_order(self):
        result = fetch_indicators("AAPL", "1d", limit=5)
        assert len(result) == 5
        assert result.index.is_monotonic_increasing
        # last timestamp matches the last date in the source fixture (relative, not hardcoded)
        assert result.index[-1] == pd.Timestamp(self._last_ts)

    def test_indicator_names_filter_returns_only_requested_column(self):
        result = fetch_indicators("AAPL", "1d", indicator_names=["rsi_14"])
        assert list(result.columns) == ["rsi_14"]

    def test_no_data_returns_empty_dataframe(self):
        assert fetch_indicators("NONEXISTENT", "1d").empty


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_singleton_has_id_one_after_seeding(self, isolated_db):
        with isolated_db.connect() as conn:
            assert conn.execute(
                text("SELECT id FROM circuit_breaker_state WHERE id = 1")
            ).scalar() == 1

    def test_only_one_row_exists_not_duplicated_on_re_init(self, isolated_db):
        init_db()
        assert _count_circuit_breaker_rows(isolated_db) == 1


# ---------------------------------------------------------------------------
# TestAddRsi
# ---------------------------------------------------------------------------


class TestAddRsi:
    def test_result_has_rsi_14_column(self, sample_ohlcv_df):
        assert "rsi_14" in add_rsi(sample_ohlcv_df).columns

    def test_rsi_values_in_range_0_to_100(self, sample_ohlcv_df):
        valid = add_rsi(sample_ohlcv_df)["rsi_14"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_first_13_rows_are_nan(self, sample_ohlcv_df):
        assert add_rsi(sample_ohlcv_df)["rsi_14"].iloc[:13].isna().all()

    def test_row_13_onwards_has_values(self, sample_ohlcv_df):
        assert pd.notna(add_rsi(sample_ohlcv_df)["rsi_14"].iloc[13])


# ---------------------------------------------------------------------------
# TestAddBollingerBands
# ---------------------------------------------------------------------------


class TestAddBollingerBands:
    def test_has_four_expected_columns(self, sample_ohlcv_df):
        result = add_bollinger_bands(sample_ohlcv_df)
        for col in ("bb_upper_20_2", "bb_mid_20_2", "bb_lower_20_2", "bb_pband_20_2"):
            assert col in result.columns

    def test_upper_gte_mid_gte_lower_where_not_nan(self, sample_ohlcv_df):
        result = add_bollinger_bands(sample_ohlcv_df).dropna(
            subset=["bb_upper_20_2", "bb_mid_20_2", "bb_lower_20_2"]
        )
        assert (result["bb_upper_20_2"] >= result["bb_mid_20_2"]).all()
        assert (result["bb_mid_20_2"] >= result["bb_lower_20_2"]).all()

    def test_first_19_rows_are_nan(self, sample_ohlcv_df):
        assert add_bollinger_bands(sample_ohlcv_df)["bb_upper_20_2"].iloc[:19].isna().all()


# ---------------------------------------------------------------------------
# TestAddEma
# ---------------------------------------------------------------------------


class TestAddEma:
    def test_has_ema_9_column(self, sample_ohlcv_df):
        assert "ema_9" in add_ema(sample_ohlcv_df, period=9).columns

    def test_ema_1_on_constant_series_equals_constant(self, constant_ohlcv_df):
        result = add_ema(constant_ohlcv_df, period=1)
        np.testing.assert_allclose(result["ema_1"].dropna().values, 100.0)


# ---------------------------------------------------------------------------
# TestAddMacd
# ---------------------------------------------------------------------------


class TestAddMacd:
    def test_has_three_expected_columns(self, sample_ohlcv_df):
        result = add_macd(sample_ohlcv_df)
        for col in ("macd_12_26_9", "macd_signal_12_26_9", "macd_hist_12_26_9"):
            assert col in result.columns

    def test_macd_hist_equals_macd_minus_signal_on_last_row(self, sample_ohlcv_df):
        last = add_macd(sample_ohlcv_df).iloc[-1]
        assert last["macd_hist_12_26_9"] == pytest.approx(
            last["macd_12_26_9"] - last["macd_signal_12_26_9"], abs=1e-10
        )

    def test_custom_params_create_correct_column_names(self, sample_ohlcv_df):
        result = add_macd(sample_ohlcv_df, fast=5, slow=10, signal=3)
        assert "macd_5_10_3" in result.columns


# ---------------------------------------------------------------------------
# TestAddAdx
# ---------------------------------------------------------------------------


class TestAddAdx:
    def test_has_three_expected_columns(self, sample_ohlcv_df):
        result = add_adx(sample_ohlcv_df)
        for col in ("adx_14", "adx_pos_14", "adx_neg_14"):
            assert col in result.columns

    def test_adx_values_in_range_0_to_100(self, sample_ohlcv_df):
        valid = add_adx(sample_ohlcv_df)["adx_14"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_non_nan_di_values_non_negative(self, sample_ohlcv_df):
        result = add_adx(sample_ohlcv_df)
        assert (result["adx_pos_14"].dropna() >= 0).all()
        assert (result["adx_neg_14"].dropna() >= 0).all()


# ---------------------------------------------------------------------------
# TestAddAtr
# ---------------------------------------------------------------------------


class TestAddAtr:
    def test_has_atr_14_column(self, sample_ohlcv_df):
        assert "atr_14" in add_atr(sample_ohlcv_df).columns

    def test_post_warmup_atr_values_are_positive(self, sample_ohlcv_df):
        # ta fills first (period-1) warmup rows with 0.0 instead of NaN
        post_warmup = add_atr(sample_ohlcv_df, period=14)["atr_14"].iloc[14:]
        assert (post_warmup > 0).all()


# ---------------------------------------------------------------------------
# TestAddAllIndicators
# ---------------------------------------------------------------------------


_EXPECTED_INDICATOR_COLS = {
    "rsi_14",
    "bb_upper_20_2", "bb_mid_20_2", "bb_lower_20_2", "bb_pband_20_2",
    "ema_9", "ema_21",
    "macd_12_26_9", "macd_signal_12_26_9", "macd_hist_12_26_9",
    "adx_14", "adx_pos_14", "adx_neg_14",
    "atr_14",
}


class TestAddAllIndicators:
    def test_returns_exactly_14_new_columns(self, sample_ohlcv_df):
        base_cols = set(sample_ohlcv_df.columns)
        new_cols = set(add_all_indicators(sample_ohlcv_df).columns) - base_cols
        assert len(new_cols) == 14

    def test_all_14_expected_columns_present(self, sample_ohlcv_df):
        result_cols = set(add_all_indicators(sample_ohlcv_df).columns)
        assert _EXPECTED_INDICATOR_COLS <= result_cols

    def test_original_ohlcv_values_unchanged(self, sample_ohlcv_df):
        original_close = sample_ohlcv_df["close"].copy()
        add_all_indicators(sample_ohlcv_df)
        pd.testing.assert_series_equal(sample_ohlcv_df["close"], original_close)


# ---------------------------------------------------------------------------
# TestResampleOhlcv
# ---------------------------------------------------------------------------


class TestResampleOhlcv:
    @pytest.fixture
    def hourly_df(self):
        """48 rows of 1h UTC-aware OHLCV (2 full days = 12 complete 4h groups)."""
        dates = pd.date_range("2025-01-01", periods=48, freq="1h", tz="UTC")
        rng = np.random.default_rng(7)
        close = 150 + np.cumsum(rng.normal(0, 0.1, 48))
        df = pd.DataFrame(
            {
                "open":   close - rng.uniform(0, 0.5, 48),
                "high":   close + rng.uniform(0, 0.5, 48),
                "low":    close - rng.uniform(0, 0.5, 48),
                "close":  close,
                "volume": rng.integers(10_000, 100_000, 48).astype(float),
            },
            index=dates,
        )
        df.index.name = "timestamp"
        return df

    def test_1h_to_4h_produces_quarter_the_rows(self, hourly_df):
        assert len(_resample_ohlcv(hourly_df, "4h")) == 12

    def test_open_is_first_of_group(self, hourly_df):
        assert _resample_ohlcv(hourly_df, "4h")["open"].iloc[0] == pytest.approx(
            hourly_df["open"].iloc[0]
        )

    def test_high_is_max_of_group(self, hourly_df):
        assert _resample_ohlcv(hourly_df, "4h")["high"].iloc[0] == pytest.approx(
            hourly_df["high"].iloc[:4].max()
        )

    def test_low_is_min_of_group(self, hourly_df):
        assert _resample_ohlcv(hourly_df, "4h")["low"].iloc[0] == pytest.approx(
            hourly_df["low"].iloc[:4].min()
        )

    def test_close_is_last_of_group(self, hourly_df):
        assert _resample_ohlcv(hourly_df, "4h")["close"].iloc[0] == pytest.approx(
            hourly_df["close"].iloc[3]
        )

    def test_volume_is_sum_of_group(self, hourly_df):
        assert _resample_ohlcv(hourly_df, "4h")["volume"].iloc[0] == pytest.approx(
            hourly_df["volume"].iloc[:4].sum()
        )

    def test_no_nan_rows_in_output(self, hourly_df):
        assert not _resample_ohlcv(hourly_df, "4h").isna().any(axis=1).any()

    def test_partial_trailing_group_is_included(self):
        """50 rows of hourly data → 12 complete 4h groups + 1 partial (2 bars). Partial kept."""
        dates = pd.date_range("2025-01-01", periods=50, freq="1h", tz="UTC")
        rng = np.random.default_rng(99)
        close = 150 + np.cumsum(rng.normal(0, 0.1, 50))
        partial_df = pd.DataFrame(
            {
                "open":   close - 0.1,
                "high":   close + 0.2,
                "low":    close - 0.2,
                "close":  close,
                "volume": 50_000.0,
            },
            index=dates,
        )
        partial_df.index.name = "timestamp"
        result = _resample_ohlcv(partial_df, "4h")
        assert len(result) == 13  # 12 complete + 1 partial


# ---------------------------------------------------------------------------
# TestYFinanceClient
# ---------------------------------------------------------------------------


class TestYFinanceClient:
    @pytest.fixture
    def yf_result(self, sample_ohlcv_df):
        """Pre-fetched result from a mocked YFinanceClient — shared across all tests."""
        aware_df = sample_ohlcv_df.copy()
        aware_df.index = aware_df.index.tz_localize("UTC")
        aware_df.columns = [c.capitalize() for c in aware_df.columns]

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = aware_df

        with patch("data.ingestion.yf.Ticker", return_value=mock_ticker):
            result = YFinanceClient().fetch_bars("AAPL", "1d", period="3mo")
        return result

    def test_returns_lowercase_columns(self, yf_result):
        for col in ("open", "high", "low", "close", "volume"):
            assert col in yf_result.columns

    def test_index_is_named_timestamp(self, yf_result):
        assert yf_result.index.name == "timestamp"

    def test_index_is_tz_aware_utc(self, yf_result):
        assert yf_result.index.tz is not None
        assert str(yf_result.index.tz) == "UTC"

    def test_columns_restricted_to_ohlcv(self, yf_result):
        assert set(yf_result.columns) == {"open", "high", "low", "close", "volume"}

    def test_unsupported_timeframe_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            YFinanceClient().fetch_bars("AAPL", "3d")


# ---------------------------------------------------------------------------
# TestIngestSymbol
# ---------------------------------------------------------------------------


class TestIngestSymbol:
    @pytest.fixture
    def mock_yf_df(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.index = df.index.tz_localize("UTC")
        return df

    def test_with_use_alpaca_false_calls_yfinance(self, isolated_db, mock_yf_df):
        with patch(
            "data.ingestion.YFinanceClient.fetch_bars", return_value=mock_yf_df
        ) as mock_fetch:
            ingest_symbol("AAPL", "1d", lookback_days=90, use_alpaca=False)
        mock_fetch.assert_called_once()

    def test_returns_dataframe_and_persists_to_db(self, isolated_db, mock_yf_df):
        with patch("data.ingestion.YFinanceClient.fetch_bars", return_value=mock_yf_df):
            result = ingest_symbol("AAPL", "1d", lookback_days=90, use_alpaca=False)
        assert isinstance(result, pd.DataFrame) and not result.empty
        assert len(fetch_bars("AAPL", "1d")) == SAMPLE_PERIODS

    def test_returns_empty_dataframe_gracefully_when_yfinance_fails(self, isolated_db):
        with patch(
            "data.ingestion.YFinanceClient.fetch_bars",
            side_effect=Exception("network error"),
        ):
            result = ingest_symbol("AAPL", "1d", lookback_days=90, use_alpaca=False)
        assert isinstance(result, pd.DataFrame) and result.empty

    def test_use_alpaca_false_skips_alpaca_client(self, isolated_db, mock_yf_df):
        with patch("data.ingestion.AlpacaClient") as mock_alpaca_cls, patch(
            "data.ingestion.YFinanceClient.fetch_bars", return_value=mock_yf_df
        ):
            ingest_symbol("AAPL", "1d", lookback_days=90, use_alpaca=False)
        mock_alpaca_cls.assert_not_called()


# ---------------------------------------------------------------------------
# prune_equity_snapshots
# ---------------------------------------------------------------------------


class TestPruneEquitySnapshots:
    """Retention pruning of the equity_snapshots table (backs `main.py prune`)."""

    def _insert_at(self, engine, ts: datetime, equity: float = 100_000.0) -> None:
        """Insert one snapshot with an explicit timestamp (bypasses _utcnow default)."""
        with engine.begin() as conn:
            conn.execute(
                storage_module.equity_snapshots.insert().values(
                    timestamp=ts,
                    equity=equity,
                    cash=equity,
                    position_value=0.0,
                    daily_pnl=0.0,
                    weekly_pnl=0.0,
                    drawdown_pct=0.0,
                    peak_equity=equity,
                )
            )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def test_deletes_only_rows_older_than_cutoff(self, isolated_db):
        from datetime import timedelta
        from data.storage import prune_equity_snapshots, fetch_equity_snapshots

        now = self._now()
        self._insert_at(isolated_db, now - timedelta(days=120))  # stale
        self._insert_at(isolated_db, now - timedelta(days=91))   # stale (just over)
        self._insert_at(isolated_db, now - timedelta(days=30))   # fresh
        self._insert_at(isolated_db, now)                        # fresh

        deleted = prune_equity_snapshots(older_than_days=90)

        assert deleted == 2
        assert len(fetch_equity_snapshots()) == 2

    def test_returns_zero_when_nothing_to_prune(self, isolated_db):
        from data.storage import prune_equity_snapshots

        self._insert_at(isolated_db, self._now())
        assert prune_equity_snapshots(older_than_days=90) == 0

    def test_zero_days_prunes_everything_in_the_past(self, isolated_db):
        from datetime import timedelta
        from data.storage import prune_equity_snapshots, fetch_equity_snapshots

        self._insert_at(isolated_db, self._now() - timedelta(seconds=5))
        deleted = prune_equity_snapshots(older_than_days=0)
        assert deleted == 1
        assert fetch_equity_snapshots() == []

    def test_negative_days_raises_value_error(self, isolated_db):
        from data.storage import prune_equity_snapshots

        with pytest.raises(ValueError):
            prune_equity_snapshots(older_than_days=-1)

    def test_empty_table_returns_zero(self, isolated_db):
        from data.storage import prune_equity_snapshots

        assert prune_equity_snapshots(older_than_days=90) == 0
