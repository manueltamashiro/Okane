"""
Tests for Phase 3 risk module: position_sizing, circuit_breakers, portfolio, withdrawal.

Circuit breaker tests use isolated_db (DB-touching).
All other tests are pure (no DB, no network).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from strategies.base import Direction, Signal


# ─── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db():
    """
    Swap storage engine to in-memory SQLite for every test.
    Restores original engine on teardown.
    """
    import data.storage as storage_mod
    from sqlalchemy import create_engine
    original = storage_mod._engine
    storage_mod._engine = create_engine("sqlite:///:memory:", future=True, echo=False)
    storage_mod.init_db()
    yield storage_mod._engine
    storage_mod._engine = original


@pytest.fixture
def sample_signal() -> Signal:
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
def empty_portfolio():
    from risk.portfolio import PortfolioState
    return PortfolioState()


# ─── TestPositionSizing ───────────────────────────────────────────────────────

class TestPositionSizing:
    def test_returns_zero_for_stop_loss_above_price(self):
        from risk.position_sizing import calculate_position_size
        signal = Signal(
            symbol="AAPL", direction=Direction.BUY,
            price=100.0, stop_loss=105.0, confidence=0.8,
            strategy_name="test", timestamp=datetime(2024, 1, 1),
        )
        assert calculate_position_size(signal, 10_000.0) == 0

    def test_returns_zero_for_stop_loss_equal_to_price(self):
        from risk.position_sizing import calculate_position_size
        signal = Signal(
            symbol="AAPL", direction=Direction.BUY,
            price=100.0, stop_loss=100.0, confidence=0.8,
            strategy_name="test", timestamp=datetime(2024, 1, 1),
        )
        assert calculate_position_size(signal, 10_000.0) == 0

    def test_returns_zero_for_zero_account_value(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        assert calculate_position_size(sample_signal, 0.0) == 0

    def test_returns_zero_for_negative_account_value(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        assert calculate_position_size(sample_signal, -500.0) == 0

    def test_result_never_exceeds_5pct_of_account(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        for account in [1_000.0, 10_000.0, 50_000.0, 500_000.0]:
            shares = calculate_position_size(sample_signal, account)
            assert shares * sample_signal.price <= account * 0.05 + 1e-6, (
                f"account={account}: {shares} shares * {sample_signal.price} exceeds 5%"
            )

    def test_result_never_risks_more_than_1pct_of_account(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        risk_per_share = sample_signal.price - sample_signal.stop_loss  # 3.0
        for account in [1_000.0, 10_000.0, 50_000.0]:
            shares = calculate_position_size(sample_signal, account)
            assert shares * risk_per_share <= account * 0.01 + 1e-6, (
                f"account={account}: {shares} * {risk_per_share} exceeds 1%"
            )

    def test_vix_above_threshold_halves_position(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        base = calculate_position_size(sample_signal, 50_000.0, vix=None)
        with_vix = calculate_position_size(sample_signal, 50_000.0, vix=35.0)
        assert with_vix == max(0, base // 2)

    def test_vix_below_threshold_has_no_effect(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        base = calculate_position_size(sample_signal, 50_000.0, vix=None)
        with_low_vix = calculate_position_size(sample_signal, 50_000.0, vix=25.0)
        assert with_low_vix == base

    def test_vix_exactly_at_threshold_does_not_brake(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        base = calculate_position_size(sample_signal, 50_000.0, vix=None)
        at_threshold = calculate_position_size(sample_signal, 50_000.0, vix=30.0)
        assert at_threshold == base

    def test_returns_integer(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        result = calculate_position_size(sample_signal, 10_000.0)
        assert isinstance(result, int)

    def test_lower_confidence_reduces_shares(self, sample_signal):
        from risk.position_sizing import calculate_position_size
        account = 50_000.0
        high = calculate_position_size(
            Signal(symbol="AAPL", direction=Direction.BUY, price=150.0, stop_loss=147.0,
                   confidence=0.9, strategy_name="test", timestamp=datetime(2024, 1, 1)),
            account,
        )
        low = calculate_position_size(
            Signal(symbol="AAPL", direction=Direction.BUY, price=150.0, stop_loss=147.0,
                   confidence=0.2, strategy_name="test", timestamp=datetime(2024, 1, 1)),
            account,
        )
        assert high >= low

    def test_never_raises_on_garbage_input(self):
        from risk.position_sizing import calculate_position_size
        garbage = Signal(
            symbol="", direction=Direction.BUY,
            price=0.0, stop_loss=-1.0, confidence=0.0,
            strategy_name="", timestamp=datetime(2024, 1, 1),
        )
        result = calculate_position_size(garbage, 0.0)
        assert result == 0


# ─── TestCircuitBreakers ──────────────────────────────────────────────────────

class TestCircuitBreakers:
    def test_load_state_returns_dict_with_expected_keys(self):
        from risk.circuit_breakers import load_state
        state = load_state()
        for key in ("daily_halt", "weekly_halt", "full_halt", "daily_loss_pct",
                    "weekly_loss_pct", "max_drawdown_pct", "halt_reason"):
            assert key in state, f"missing key: {key}"

    def test_daily_loss_triggers_daily_halt(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(-0.04, 0.0, 0.0)
        assert state["daily_halt"] is True
        assert state["weekly_halt"] is False
        assert state["full_halt"] is False

    def test_weekly_loss_triggers_weekly_halt(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(0.0, -0.06, 0.0)
        assert state["weekly_halt"] is True
        assert state["daily_halt"] is False
        assert state["full_halt"] is False

    def test_max_drawdown_triggers_full_halt(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(0.0, 0.0, -0.20)
        assert state["full_halt"] is True
        assert state["daily_halt"] is False
        assert state["weekly_halt"] is False

    def test_no_breach_sets_no_halt_flags(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(-0.01, -0.02, -0.05)
        assert state["daily_halt"] is False
        assert state["weekly_halt"] is False
        assert state["full_halt"] is False

    def test_check_and_update_is_idempotent(self):
        from risk.circuit_breakers import check_and_update, load_state
        check_and_update(-0.04, -0.06, -0.20)
        state_after_first = load_state()
        check_and_update(-0.04, -0.06, -0.20)
        state_after_second = load_state()
        assert state_after_first["daily_halt"] == state_after_second["daily_halt"]
        assert state_after_first["weekly_halt"] == state_after_second["weekly_halt"]
        assert state_after_first["full_halt"] == state_after_second["full_halt"]

    def test_full_halt_persists_after_reset_daily(self):
        from risk.circuit_breakers import check_and_update, reset_daily, is_halted
        check_and_update(0.0, 0.0, -0.20)
        reset_daily()
        assert is_halted() is True

    def test_reset_daily_clears_only_daily_halt(self):
        from risk.circuit_breakers import check_and_update, reset_daily, load_state
        check_and_update(-0.04, -0.06, 0.0)
        reset_daily()
        state = load_state()
        assert state["daily_halt"] is False
        assert state["daily_loss_pct"] == 0.0
        assert state["weekly_halt"] is True  # untouched
        assert state["halt_reason"] is not None  # preserved because weekly halt still active

    def test_is_halted_returns_true_if_any_flag_set(self):
        from risk.circuit_breakers import check_and_update, is_halted
        check_and_update(-0.04, 0.0, 0.0)  # only daily
        assert is_halted() is True

    def test_is_halted_returns_true_after_weekly_halt(self):
        from risk.circuit_breakers import check_and_update, is_halted
        check_and_update(0.0, -0.06, 0.0)  # only weekly
        assert is_halted() is True

    def test_is_halted_returns_false_when_no_flags(self):
        from risk.circuit_breakers import is_halted
        assert is_halted() is False

    def test_daily_loss_at_exact_threshold_triggers_halt(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(-0.03, 0.0, 0.0)  # exactly -3%
        assert state["daily_halt"] is True

    def test_daily_loss_just_below_threshold_does_not_trigger(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(-0.0299, 0.0, 0.0)
        assert state["daily_halt"] is False

    def test_halt_reason_set_when_triggered(self):
        from risk.circuit_breakers import check_and_update
        state = check_and_update(-0.04, 0.0, 0.0)
        assert state["halt_reason"] is not None
        assert len(state["halt_reason"]) > 0

    def test_once_full_halted_stays_halted_on_recovery(self):
        from risk.circuit_breakers import check_and_update
        check_and_update(0.0, 0.0, -0.20)  # trigger full halt
        state = check_and_update(0.0, 0.0, -0.01)  # "recovered" — but halt persists
        assert state["full_halt"] is True


# ─── TestPortfolio ────────────────────────────────────────────────────────────

class TestPortfolio:
    def test_can_open_position_returns_true_for_empty_portfolio(self, sample_signal, empty_portfolio):
        from risk.portfolio import can_open_position
        allowed, reason = can_open_position(sample_signal.symbol, empty_portfolio)
        assert allowed is True
        assert reason is None

    def test_rejects_sixth_position(self, sample_signal):
        from risk.portfolio import PortfolioState, can_open_position
        # 5 different symbols already open
        state = PortfolioState(
            open_positions=["MSFT", "GOOG", "AMZN", "TSLA", "META"],
            sector_map={"MSFT": "Tech", "GOOG": "Tech", "AMZN": "Tech", "TSLA": "Auto", "META": "Tech"},
        )
        allowed, reason = can_open_position(sample_signal.symbol, state)
        assert allowed is False
        assert reason is not None

    def test_rejects_duplicate_symbol(self, sample_signal):
        from risk.portfolio import PortfolioState, can_open_position
        state = PortfolioState(
            open_positions=["AAPL"],
            sector_map={"AAPL": "Technology"},
        )
        allowed, reason = can_open_position(sample_signal.symbol, state)
        assert allowed is False
        assert "AAPL" in reason

    def test_rejects_on_sector_exposure_limit(self):
        from risk.portfolio import PortfolioState, can_open_position
        # 3 Technology stocks already open
        tech_stocks = ["MSFT", "GOOG", "AMZN"]
        state = PortfolioState(
            open_positions=tech_stocks,
            sector_map={s: "Technology" for s in tech_stocks},
        )
        # AAPL is Technology → should be rejected
        allowed, reason = can_open_position("AAPL", state)
        assert allowed is False
        assert "Technology" in reason

    def test_different_sector_allowed_when_another_sector_is_full(self):
        from risk.portfolio import PortfolioState, can_open_position
        # 3 Technology stocks, but VTI is Broad ETF
        state = PortfolioState(
            open_positions=["MSFT", "GOOG", "AMZN"],
            sector_map={"MSFT": "Technology", "GOOG": "Technology", "AMZN": "Technology"},
        )
        allowed, reason = can_open_position("VTI", state)
        assert allowed is True

    def test_add_position_does_not_mutate_original(self, empty_portfolio):
        from risk.portfolio import add_position
        original_len = len(empty_portfolio.open_positions)
        new_state = add_position(empty_portfolio, "AAPL")
        assert len(empty_portfolio.open_positions) == original_len
        assert "AAPL" in new_state.open_positions

    def test_remove_position_removes_symbol(self):
        from risk.portfolio import PortfolioState, remove_position
        state = PortfolioState(
            open_positions=["AAPL", "VTI"],
            sector_map={"AAPL": "Technology", "VTI": "Broad ETF"},
        )
        new_state = remove_position(state, "AAPL")
        assert "AAPL" not in new_state.open_positions
        assert "VTI" in new_state.open_positions

    def test_remove_nonexistent_symbol_does_not_raise(self, empty_portfolio):
        from risk.portfolio import remove_position
        result = remove_position(empty_portfolio, "FAKE")
        assert result.open_positions == []

    def test_unknown_symbol_gets_unknown_sector(self, empty_portfolio):
        from risk.portfolio import can_open_position
        allowed, reason = can_open_position("FAKE", empty_portfolio)
        assert allowed is True  # empty portfolio, no sector conflict


# ─── TestWithdrawal ───────────────────────────────────────────────────────────

class TestWithdrawal:
    def test_ratios_sum_to_realized_pnl(self):
        from risk.withdrawal import calculate_withdrawal
        pnl = 1000.0
        result = calculate_withdrawal(pnl)
        total = result["reinvest"] + result["tax_reserve"] + result["income"]
        assert total == pytest.approx(pnl)

    def test_correct_split_on_1000_pnl(self):
        from risk.withdrawal import calculate_withdrawal
        result = calculate_withdrawal(1000.0)
        assert result["reinvest"] == pytest.approx(500.0)
        assert result["tax_reserve"] == pytest.approx(300.0)
        assert result["income"] == pytest.approx(200.0)

    def test_zero_pnl_returns_all_zeros(self):
        from risk.withdrawal import calculate_withdrawal
        result = calculate_withdrawal(0.0)
        assert result["reinvest"] == pytest.approx(0.0)
        assert result["tax_reserve"] == pytest.approx(0.0)
        assert result["income"] == pytest.approx(0.0)

    def test_can_withdraw_false_during_drawdown(self):
        from risk.withdrawal import can_withdraw
        assert can_withdraw(portfolio_value=10_500.0, base_value=10_000.0, current_drawdown=0.06) is False

    def test_can_withdraw_false_below_safety_cushion(self):
        from risk.withdrawal import can_withdraw
        # portfolio < base * 1.10
        assert can_withdraw(portfolio_value=10_900.0, base_value=10_000.0, current_drawdown=0.01) is False

    def test_can_withdraw_true_when_all_conditions_met(self):
        from risk.withdrawal import can_withdraw
        assert can_withdraw(portfolio_value=11_100.0, base_value=10_000.0, current_drawdown=0.01) is True

    def test_can_withdraw_false_at_exactly_drawdown_threshold(self):
        from risk.withdrawal import can_withdraw
        # current_drawdown == 0.05 → should be False (>= threshold)
        assert can_withdraw(portfolio_value=11_100.0, base_value=10_000.0, current_drawdown=0.05) is False

    def test_can_withdraw_true_at_exactly_safety_cushion(self):
        from risk.withdrawal import can_withdraw
        # portfolio_value == base * 1.10 → should be True (not strictly less than)
        assert can_withdraw(portfolio_value=11_000.0, base_value=10_000.0, current_drawdown=0.01) is True
