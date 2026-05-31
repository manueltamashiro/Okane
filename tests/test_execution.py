"""
Tests for Phase 4: execution (order_engine) and monitoring (performance, notifications).
Alpaca API is fully mocked via FakeAlpacaClient.
Telegram is mocked via NullNotifier.
All DB access uses isolated in-memory SQLite.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from execution.alpaca_client import AccountInfo, AlpacaClientError, ClockInfo, OrderResult, PositionInfo
from monitoring.notifications import NullNotifier
from strategies.base import Direction, Signal


# ---------------------------------------------------------------------------
# FakeAlpacaClient — defined here, co-located with the tests that use it
# ---------------------------------------------------------------------------

class FakeAlpacaClient:
    """Minimal mock satisfying AlpacaClientProtocol. Configurable per test."""

    def __init__(self) -> None:
        self.equity: float = 10_000.0
        self.cash: float = 10_000.0
        self.submitted_orders: list[dict] = []
        self._order_status: str = "accepted"
        self._fill_immediately: bool = True
        self._raise_on_submit: bool = False
        self._filled_price: float = 150.0

    def get_account(self) -> AccountInfo:
        return AccountInfo(
            equity=self.equity,
            cash=self.cash,
            buying_power=self.cash,
            portfolio_value=self.equity,
        )

    def submit_market_order(
        self, symbol: str, qty: int, side: str, time_in_force: str = "day",
        client_order_id: str | None = None,
    ) -> OrderResult:
        if self._raise_on_submit:
            raise AlpacaClientError("Simulated Alpaca failure")
        order_id = f"fake-{symbol}-{len(self.submitted_orders)}"
        self.submitted_orders.append({
            "symbol": symbol, "qty": qty, "side": side, "order_id": order_id,
            "client_order_id": client_order_id,
        })
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            status=self._order_status,
            filled_avg_price=None,
            filled_at=None,
        )

    def get_order(self, order_id: str) -> OrderResult:
        match = next((o for o in self.submitted_orders if o["order_id"] == order_id), None)
        if match is None:
            raise AlpacaClientError(f"Order {order_id} not found")
        if self._fill_immediately:
            return OrderResult(
                order_id=order_id,
                symbol=match["symbol"],
                side=match["side"],
                qty=match["qty"],
                status="filled",
                filled_avg_price=self._filled_price,
                filled_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        return OrderResult(
            order_id=order_id,
            symbol=match["symbol"],
            side=match["side"],
            qty=match["qty"],
            status="accepted",
            filled_avg_price=None,
            filled_at=None,
        )

    def get_open_positions(self) -> list[PositionInfo]:
        return []

    def get_clock(self) -> ClockInfo:
        return ClockInfo(is_open=True, next_open=None, next_close=None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db():
    """Swap storage engine to in-memory SQLite for every test."""
    import data.storage as storage_mod
    from sqlalchemy import create_engine
    original = storage_mod._engine
    storage_mod._engine = create_engine("sqlite:///:memory:", future=True, echo=False)
    storage_mod.init_db()
    # Reset withdrawal config cache so it is re-read fresh each test
    try:
        import risk.withdrawal as wd_mod
        wd_mod._WITHDRAWAL_CFG = None
    except Exception:
        pass
    yield storage_mod._engine
    storage_mod._engine = original


@pytest.fixture
def fake_client() -> FakeAlpacaClient:
    return FakeAlpacaClient()


@pytest.fixture
def null_notifier() -> NullNotifier:
    return NullNotifier()


@pytest.fixture
def buy_signal() -> Signal:
    return Signal(
        symbol="AAPL",
        direction=Direction.BUY,
        price=150.0,
        stop_loss=147.0,
        confidence=0.75,
        strategy_name="mean_reversion",
        timestamp=datetime(2024, 6, 1),
    )


@pytest.fixture
def sell_signal() -> Signal:
    return Signal(
        symbol="AAPL",
        direction=Direction.SELL,
        price=155.0,
        stop_loss=0.0,
        confidence=0.6,
        strategy_name="mean_reversion",
        timestamp=datetime(2024, 6, 2),
    )


@pytest.fixture
def empty_portfolio():
    from risk.portfolio import PortfolioState
    return PortfolioState()


# ---------------------------------------------------------------------------
# TestAlpacaClientProtocol
# ---------------------------------------------------------------------------

class TestAlpacaClientProtocol:
    def test_fake_client_satisfies_protocol(self):
        from execution.alpaca_client import AlpacaClientProtocol
        client = FakeAlpacaClient()
        assert isinstance(client, AlpacaClientProtocol)

    def test_fake_client_get_account_returns_account_info(self, fake_client):
        acct = fake_client.get_account()
        assert acct.equity == 10_000.0
        assert acct.cash == 10_000.0

    def test_fake_client_submit_order_records_order(self, fake_client):
        result = fake_client.submit_market_order("AAPL", 10, "buy")
        assert result.order_id.startswith("fake-AAPL")
        assert len(fake_client.submitted_orders) == 1

    def test_fake_client_raises_on_submit_when_configured(self, fake_client):
        fake_client._raise_on_submit = True
        with pytest.raises(AlpacaClientError):
            fake_client.submit_market_order("AAPL", 5, "buy")

    def test_fake_client_get_order_returns_filled_immediately(self, fake_client):
        result = fake_client.submit_market_order("AAPL", 10, "buy")
        order = fake_client.get_order(result.order_id)
        assert order.status == "filled"
        assert order.filled_avg_price == 150.0


# ---------------------------------------------------------------------------
# TestOrderEngine
# ---------------------------------------------------------------------------

class TestOrderEngine:
    def test_hold_signal_is_rejected(self, fake_client, empty_portfolio):
        from execution.order_engine import process_signal
        signal = Signal(
            symbol="AAPL", direction=Direction.HOLD,
            price=150.0, stop_loss=147.0, confidence=0.5,
            strategy_name="test", timestamp=datetime(2024, 1, 1),
        )
        outcome = process_signal(signal, empty_portfolio, fake_client, account_value=10_000.0)
        assert outcome.submitted is False
        assert "HOLD" in outcome.rejection_reason
        assert len(fake_client.submitted_orders) == 0

    def test_halted_circuit_breaker_blocks_buy(self, fake_client, empty_portfolio, buy_signal):
        from execution.order_engine import process_signal
        from risk.circuit_breakers import check_and_update
        check_and_update(-0.04, 0.0, 0.0)  # trigger daily halt
        outcome = process_signal(buy_signal, empty_portfolio, fake_client, account_value=10_000.0)
        assert outcome.submitted is False
        assert "circuit breaker" in outcome.rejection_reason.lower()
        assert len(fake_client.submitted_orders) == 0

    def test_buy_signal_approved_submits_order(self, fake_client, empty_portfolio, buy_signal):
        from execution.order_engine import process_signal
        outcome = process_signal(buy_signal, empty_portfolio, fake_client, account_value=50_000.0)
        assert outcome.submitted is True
        assert outcome.order_result is not None
        assert len(fake_client.submitted_orders) == 1
        assert fake_client.submitted_orders[0]["side"] == "buy"

    def test_sell_signal_with_no_open_position_is_rejected(self, fake_client, empty_portfolio, sell_signal):
        from execution.order_engine import process_signal
        outcome = process_signal(sell_signal, empty_portfolio, fake_client, account_value=10_000.0)
        assert outcome.submitted is False
        assert "no open position" in outcome.rejection_reason.lower()

    def test_zero_position_size_blocks_order(self, fake_client, empty_portfolio):
        from execution.order_engine import process_signal
        # Confidence=0.0 → kelly_shares=0 → position size=0
        tiny_signal = Signal(
            symbol="AAPL", direction=Direction.BUY,
            price=150.0, stop_loss=149.99, confidence=0.0,
            strategy_name="test", timestamp=datetime(2024, 1, 1),
        )
        outcome = process_signal(tiny_signal, empty_portfolio, fake_client, account_value=100.0)
        assert outcome.submitted is False

    def test_alpaca_error_on_submit_propagates(self, fake_client, empty_portfolio, buy_signal):
        from execution.order_engine import process_signal
        fake_client._raise_on_submit = True
        with pytest.raises(AlpacaClientError):
            process_signal(buy_signal, empty_portfolio, fake_client, account_value=50_000.0)

    def test_order_persisted_to_db_on_submit(self, fake_client, empty_portfolio, buy_signal):
        from data.storage import fetch_order_by_alpaca_id
        from execution.order_engine import process_signal
        outcome = process_signal(buy_signal, empty_portfolio, fake_client, account_value=50_000.0)
        assert outcome.submitted is True
        order_id = outcome.order_result.order_id
        row = fetch_order_by_alpaca_id(order_id)
        assert row is not None
        assert row["symbol"] == "AAPL"
        assert row["side"] == "buy"
        assert row["status"] == "pending"

    def test_rejected_signal_not_persisted_as_order(self, fake_client, empty_portfolio, buy_signal):
        from data.storage import fetch_pending_orders
        from execution.order_engine import process_signal
        from risk.circuit_breakers import check_and_update
        check_and_update(-0.04, 0.0, 0.0)
        process_signal(buy_signal, empty_portfolio, fake_client, account_value=50_000.0)
        assert fetch_pending_orders() == []


# ---------------------------------------------------------------------------
# TestPaperTradingSession
# ---------------------------------------------------------------------------

class TestPaperTradingSession:
    def _make_session(self, client, notifier=None, symbols=None):
        from execution.paper_trading import PaperTradingSession
        return PaperTradingSession(
            client=client,
            notifier=notifier or NullNotifier(),
            symbols=symbols or ["AAPL"],
            poll_interval_seconds=0,
        )

    def test_rebuild_portfolio_state_from_db(self, fake_client):
        from data.storage import insert_position
        from datetime import datetime as dt
        insert_position(
            symbol="AAPL", qty=10, entry_price=150.0, stop_loss=147.0,
            opened_at=dt(2024, 6, 1), strategy_name="test",
        )
        session = self._make_session(fake_client)
        session._rebuild_portfolio_state()
        assert "AAPL" in session._portfolio_state.open_positions

    def test_poll_cycle_snapshots_equity(self, fake_client):
        from data.storage import fetch_equity_snapshots
        session = self._make_session(fake_client)
        session._peak_equity = 10_000.0
        session._session_open_equity = 10_000.0
        session._week_open_equity = 10_000.0
        session._poll_cycle()
        snapshots = fetch_equity_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["equity"] == pytest.approx(10_000.0)

    def test_sync_buy_fill_creates_position(self, fake_client):
        from data.storage import fetch_open_positions, insert_order
        from execution.paper_trading import PaperTradingSession
        # Insert a pending BUY order
        insert_order(
            alpaca_order_id="fake-AAPL-0",
            symbol="AAPL", side="buy", qty=10,
            stop_loss_price=147.0,
        )
        fake_client.submitted_orders.append({"symbol": "AAPL", "qty": 10, "side": "buy", "order_id": "fake-AAPL-0"})

        session = self._make_session(fake_client)
        session._sync_filled_orders()

        positions = fetch_open_positions()
        assert any(p["symbol"] == "AAPL" for p in positions)
        assert "AAPL" in session._portfolio_state.open_positions

    def test_sync_sell_fill_creates_trade_and_removes_position(self, fake_client):
        from data.storage import fetch_open_positions, fetch_trades, insert_order, insert_position
        from datetime import datetime as dt
        # Insert open position
        insert_position(
            symbol="AAPL", qty=10, entry_price=150.0, stop_loss=147.0,
            opened_at=dt(2024, 6, 1), strategy_name="test",
        )
        # Insert pending SELL order
        insert_order(
            alpaca_order_id="fake-AAPL-sell-0",
            symbol="AAPL", side="sell", qty=10,
            stop_loss_price=0.0,
        )
        fake_client.submitted_orders.append({"symbol": "AAPL", "qty": 10, "side": "sell", "order_id": "fake-AAPL-sell-0"})

        from risk.portfolio import add_position, PortfolioState
        session = self._make_session(fake_client)
        session._portfolio_state = add_position(PortfolioState(), "AAPL")
        session._sync_filled_orders()

        positions = fetch_open_positions()
        assert not any(p["symbol"] == "AAPL" for p in positions)
        assert "AAPL" not in session._portfolio_state.open_positions
        trades = fetch_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "AAPL"

    def test_poll_cycle_skips_signals_when_halted(self, fake_client):
        from data.storage import fetch_pending_orders
        from risk.circuit_breakers import check_and_update
        check_and_update(-0.04, 0.0, 0.0)  # trigger halt

        session = self._make_session(fake_client)
        session._peak_equity = 10_000.0
        session._session_open_equity = 10_000.0
        session._week_open_equity = 10_000.0
        session._poll_cycle()

        assert fetch_pending_orders() == []
        assert len(fake_client.submitted_orders) == 0


# ---------------------------------------------------------------------------
# TestPerformanceTracker
# ---------------------------------------------------------------------------

class TestPerformanceTracker:
    def test_win_rate_zero_on_no_trades(self):
        from monitoring.performance import PerformanceTracker
        tracker = PerformanceTracker()
        metrics = tracker.compute_metrics()
        assert metrics.win_rate == 0.0
        assert metrics.total_trades == 0

    def test_win_rate_calculated_correctly(self):
        from data.storage import insert_trade
        from datetime import datetime as dt
        from monitoring.performance import PerformanceTracker
        # 3 wins, 2 losses
        for i, pnl_sign in enumerate([1, 1, 1, -1, -1]):
            entry = 100.0
            exit_ = 101.0 if pnl_sign > 0 else 99.0
            insert_trade(
                symbol="AAPL", strategy_name="test", qty=10,
                entry_price=entry, exit_price=exit_,
                opened_at=dt(2024, 6, i + 1), closed_at=dt(2024, 6, i + 2),
                exit_reason="signal",
            )
        tracker = PerformanceTracker()
        metrics = tracker.compute_metrics()
        assert metrics.total_trades == 5
        assert metrics.win_rate == pytest.approx(0.6)
        assert metrics.winning_trades == 3
        assert metrics.losing_trades == 2

    def test_sharpe_returns_zero_on_no_snapshots(self):
        from monitoring.performance import PerformanceTracker
        tracker = PerformanceTracker()
        metrics = tracker.compute_metrics()
        assert metrics.sharpe_ratio == 0.0

    def test_max_drawdown_computed_correctly(self):
        from monitoring.performance import PerformanceTracker
        tracker = PerformanceTracker()
        equity_curve = [10_000.0, 9_000.0, 9_500.0]
        dd = tracker._compute_max_drawdown(equity_curve)
        assert dd == pytest.approx(0.10)  # 10% peak-to-trough from 10000 → 9000

    def test_profit_factor_correct(self):
        from monitoring.performance import PerformanceTracker
        tracker = PerformanceTracker()
        pnl_values = [100.0, 200.0, -50.0, -100.0]
        pf = tracker._compute_profit_factor(pnl_values)
        assert pf == pytest.approx(2.0)  # (100+200) / (50+100)

    def test_profit_factor_infinity_on_no_losers(self):
        from monitoring.performance import PerformanceTracker
        tracker = PerformanceTracker()
        pf = tracker._compute_profit_factor([100.0, 200.0])
        assert pf == float("inf")

    def test_metrics_computed_at_is_set(self):
        from monitoring.performance import PerformanceTracker
        metrics = PerformanceTracker().compute_metrics()
        assert isinstance(metrics.computed_at, datetime)


# ---------------------------------------------------------------------------
# TestNotifications
# ---------------------------------------------------------------------------

class TestNotifications:
    def test_null_notifier_does_not_raise(self):
        from monitoring.notifications import NullNotifier
        n = NullNotifier()
        n.send_fill("AAPL", "buy", 10, 150.0, pnl=None)
        n.send_halt_alert("test reason")
        n.send_error("something broke")
        n.send_session_started(10_000.0)

    def test_null_notifier_satisfies_protocol(self):
        from monitoring.notifications import NotifierProtocol, NullNotifier
        assert isinstance(NullNotifier(), NotifierProtocol)

    def test_telegram_notifier_send_handles_error_gracefully(self):
        from monitoring.notifications import TelegramNotifier
        notifier = TelegramNotifier("fake-token", "fake-chat-id")
        with patch("telegram.Bot.send_message", new_callable=AsyncMock, side_effect=Exception("Network down")):
            # Must not raise even when Telegram is down
            notifier.send_halt_alert("test reason")

    def test_format_metrics_report_returns_string(self):
        from monitoring.performance import PerformanceTracker, format_metrics_report
        metrics = PerformanceTracker().compute_metrics()
        report = format_metrics_report(metrics)
        assert isinstance(report, str)
        assert "Sharpe" in report


# ---------------------------------------------------------------------------
# TestClockInfoDataclass
# ---------------------------------------------------------------------------

class TestClockInfoDataclass:
    def test_clock_info_fields(self):
        clock = ClockInfo(
            is_open=True,
            next_open=datetime(2024, 6, 3, 13, 30),
            next_close=datetime(2024, 6, 3, 20, 0),
        )
        assert clock.is_open is True
        assert clock.next_open == datetime(2024, 6, 3, 13, 30)
        assert clock.next_close == datetime(2024, 6, 3, 20, 0)

    def test_clock_info_none_fields(self):
        clock = ClockInfo(is_open=False, next_open=None, next_close=None)
        assert clock.is_open is False
        assert clock.next_open is None
        assert clock.next_close is None


# ---------------------------------------------------------------------------
# TestSignalsUniqueConstraint
# ---------------------------------------------------------------------------

class TestSignalsUniqueConstraint:
    def test_upsert_signal_deduplicates_same_symbol_timestamp_strategy(self):
        from data.storage import upsert_signal, fetch_pending_signals
        sig = Signal(
            symbol="AAPL",
            direction=Direction.BUY,
            price=150.0,
            stop_loss=147.0,
            confidence=0.8,
            strategy_name="meta",
            timestamp=datetime(2024, 6, 1),
        )
        upsert_signal(sig)
        upsert_signal(sig)  # duplicate
        pending = fetch_pending_signals()
        assert len(pending) == 1
        assert pending[0]["symbol"] == "AAPL"
        assert pending[0]["strategy"] == "meta"


# ---------------------------------------------------------------------------
# TestFetchLatestBarTimestamp
# ---------------------------------------------------------------------------

class TestFetchLatestBarTimestamp:
    def test_returns_max_timestamp_when_bars_exist(self):
        import pandas as pd
        import numpy as np
        from data.storage import upsert_bars, fetch_latest_bar_timestamp

        dates = pd.date_range("2025-01-01", periods=10, freq="1d")
        rng = np.random.default_rng(42)
        close = 150 + np.cumsum(rng.normal(0, 1, 10))
        df = pd.DataFrame(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100_000.0,
            },
            index=dates,
        )
        df.index.name = "timestamp"
        upsert_bars(df, "AAPL", "1d", "yfinance")

        result = fetch_latest_bar_timestamp("AAPL", "1d")
        assert result is not None
        expected = datetime(2025, 1, 10)
        assert result == expected

    def test_returns_none_when_no_bars(self):
        from data.storage import fetch_latest_bar_timestamp

        result = fetch_latest_bar_timestamp("NONEXISTENT", "1d")
        assert result is None


# ---------------------------------------------------------------------------
# TestGenerateSignals — signal generation in PaperTradingSession
# ---------------------------------------------------------------------------

class TestGenerateSignals:
    def _make_session(self, client, symbols=None):
        from execution.paper_trading import PaperTradingSession
        from monitoring.notifications import NullNotifier
        session = PaperTradingSession(
            client=client,
            notifier=NullNotifier(),
            symbols=symbols or ["AAPL"],
            poll_interval_seconds=0,
        )
        session._init_strategies()
        return session

    def _make_enriched_df(self):
        """Return a small DataFrame with all indicator columns needed by MetaStrategy."""
        import pandas as pd
        import numpy as np
        from data.indicators import add_all_indicators

        dates = pd.date_range("2024-01-01", periods=100, freq="1d")
        rng = np.random.default_rng(42)
        close = 150.0 + np.cumsum(rng.normal(0, 1, 100))
        df = pd.DataFrame(
            {
                "open": close - rng.uniform(0, 2, 100),
                "high": close + rng.uniform(0, 3, 100),
                "low": close - rng.uniform(0, 3, 100),
                "close": close,
                "volume": rng.integers(100_000, 1_000_000, 100).astype(float),
            },
            index=dates,
        )
        df.index.name = "timestamp"
        return add_all_indicators(df)

    def test_generate_signals_saves_meta_signal(self, fake_client):
        from data.storage import fetch_pending_signals

        session = self._make_session(fake_client)
        enriched_df = self._make_enriched_df()

        buy_signal = Signal(
            symbol="AAPL",
            direction=Direction.BUY,
            price=150.0,
            stop_loss=147.0,
            confidence=0.8,
            strategy_name="meta",
            timestamp=datetime(2024, 4, 9),
        )

        with (
            patch("data.ingestion.ingest_symbol"),
            patch("data.indicators.calculate_and_store", return_value=enriched_df),
            patch("data.storage.fetch_latest_bar_timestamp", return_value=datetime(2024, 4, 9)),
            patch.object(session._meta_strategy, "run", return_value=buy_signal),
        ):
            session._generate_signals()

        pending = fetch_pending_signals()
        assert len(pending) == 1
        assert pending[0]["strategy"] == "meta"
        assert pending[0]["signal_type"] == "BUY"

    def test_generate_signals_skips_hold(self, fake_client):
        from data.storage import fetch_pending_signals

        session = self._make_session(fake_client)
        enriched_df = self._make_enriched_df()

        with (
            patch("data.ingestion.ingest_symbol"),
            patch("data.indicators.calculate_and_store", return_value=enriched_df),
            patch("data.storage.fetch_latest_bar_timestamp", return_value=datetime(2024, 4, 9)),
            patch.object(session._meta_strategy, "run", return_value=None),
        ):
            session._generate_signals()

        pending = fetch_pending_signals()
        assert len(pending) == 0

    def test_generate_signals_time_gate(self, fake_client):
        session = self._make_session(fake_client)

        # Set time-gate: last bar timestamp is same as latest
        ts = datetime(2024, 4, 9)
        session._last_signal_bar["AAPL"] = ts

        with (
            patch("data.ingestion.ingest_symbol"),
            patch("data.storage.fetch_latest_bar_timestamp", return_value=ts),
            patch.object(session._meta_strategy, "run") as mock_run,
        ):
            session._generate_signals()

        mock_run.assert_not_called()

    def test_generate_signals_error_isolation(self, fake_client):
        session = self._make_session(fake_client)

        with (
            patch("data.ingestion.ingest_symbol", side_effect=RuntimeError("boom")),
        ):
            # Must not raise
            session._generate_signals()
