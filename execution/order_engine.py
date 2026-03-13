from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from data import storage
from execution.alpaca_client import AlpacaClientError, AlpacaClientProtocol, OrderResult
from risk.circuit_breakers import is_halted
from risk.portfolio import PortfolioState, can_open_position
from risk.position_sizing import calculate_position_size
from strategies.base import Direction, Signal


@dataclass
class OrderOutcome:
    submitted: bool
    order_result: OrderResult | None
    rejection_reason: str | None
    signal_id: int | None


def process_signal(
    signal: Signal,
    portfolio_state: PortfolioState,
    client: AlpacaClientProtocol,
    account_value: float,
    vix: float | None = None,
    signal_db_id: int | None = None,
) -> OrderOutcome:
    """
    Run all risk gates and submit a market order if approved.
    Never raises for validation failures.
    Raises AlpacaClientError if order submission fails (caller must halt on this).
    """

    def _reject(reason: str) -> OrderOutcome:
        logger.debug(f"[order_engine] signal rejected for {signal.symbol}: {reason}")
        if signal_db_id is not None:
            try:
                storage.mark_signal_risk_decision(signal_db_id, approved=False, rejection_reason=reason)
            except Exception as exc:
                logger.error(f"[order_engine] failed to write risk rejection to DB: {exc}")
        return OrderOutcome(submitted=False, order_result=None, rejection_reason=reason, signal_id=signal_db_id)

    # Gate 0 — HOLD signals never generate orders
    if signal.direction == Direction.HOLD:
        return _reject("HOLD signal — no action")

    # Gate 1 — Circuit breaker
    try:
        halted = is_halted()
    except Exception as exc:
        logger.error(f"[order_engine] is_halted() threw: {exc} — treating as halted")
        halted = True
    if halted:
        return _reject("circuit breaker is active")

    side = "buy" if signal.direction == Direction.BUY else "sell"

    if signal.direction == Direction.BUY:
        # Gate 2 — Portfolio capacity
        try:
            allowed, reason = can_open_position(signal.symbol, portfolio_state)
        except Exception as exc:
            logger.error(f"[order_engine] can_open_position threw: {exc}")
            return _reject(f"portfolio check error: {exc}")
        if not allowed:
            return _reject(reason or "portfolio capacity exceeded")

        # Gate 3 — Position sizing
        qty = calculate_position_size(signal, account_value, vix)
        if qty == 0:
            return _reject("position size calculated as 0 shares")
    else:
        # Gate 4 — SELL validation: must have an open position to sell
        try:
            pos = storage.fetch_position_by_symbol(signal.symbol)
        except Exception as exc:
            logger.error(f"[order_engine] fetch_position_by_symbol threw: {exc}")
            return _reject(f"DB error fetching position: {exc}")
        if pos is None:
            return _reject(f"no open position in {signal.symbol} to sell")
        qty = pos["qty"]

    # Submit order
    try:
        order_result = client.submit_market_order(symbol=signal.symbol, qty=qty, side=side)
    except AlpacaClientError:
        logger.error(f"[order_engine] Alpaca order submission failed for {signal.symbol}")
        raise  # propagate to session manager — must halt on order failure

    # Persist order to DB
    try:
        storage.insert_order(
            alpaca_order_id=order_result.order_id,
            symbol=signal.symbol,
            side=side,
            qty=qty,
            stop_loss_price=signal.stop_loss,
            take_profit_price=signal.take_profit,
            signal_id=signal_db_id,
        )
    except Exception as exc:
        logger.error(
            f"[order_engine] CRITICAL: order {order_result.order_id} submitted to Alpaca "
            f"but insert_order failed: {exc}"
        )
        raise

    # Mark signal as approved
    if signal_db_id is not None:
        try:
            storage.mark_signal_risk_decision(signal_db_id, approved=True)
        except Exception as exc:
            logger.error(f"[order_engine] failed to write risk approval to DB: {exc}")

    logger.debug(f"[order_engine] {side.upper()} {qty} {signal.symbol} submitted: {order_result.order_id}")
    return OrderOutcome(submitted=True, order_result=order_result, rejection_reason=None, signal_id=signal_db_id)
