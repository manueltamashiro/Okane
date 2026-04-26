from __future__ import annotations

import time
from datetime import datetime, timezone

from loguru import logger

from data import storage
from execution.alpaca_client import AlpacaClientError, AlpacaClientProtocol
from execution.order_engine import process_signal
from monitoring.notifications import NotifierProtocol
from risk.circuit_breakers import check_and_update, reset_daily
from risk.portfolio import PortfolioState, add_position, remove_position
from strategies.base import Direction, Signal


class PaperTradingSession:
    """
    Polling-based paper trading session.

    Owns the in-memory PortfolioState and peak equity tracker.
    Delegates signal evaluation to process_signal() and notifications to the notifier.
    """

    def __init__(
        self,
        client: AlpacaClientProtocol,
        notifier: NotifierProtocol,
        symbols: list[str],
        poll_interval_seconds: int = 60,
    ) -> None:
        self._client = client
        self._notifier = notifier
        self._symbols = symbols
        self._poll_interval = poll_interval_seconds
        self._portfolio_state: PortfolioState = PortfolioState()
        self._peak_equity: float = 0.0
        self._session_open_equity: float = 0.0
        self._week_open_equity: float = 0.0
        self._running: bool = False
        self._halt_notified: bool = False
        self._last_signal_bar: dict[str, datetime] = {}  # symbol -> last bar timestamp used for signal gen

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialize state and enter the polling loop. Blocks until stop() is called."""
        logger.info("[paper_trading] starting session")
        storage.init_db()
        self._rebuild_portfolio_state()
        self._set_peak_equity()
        reset_daily()
        self._init_strategies()
        self._running = True

        try:
            account = self._client.get_account()
            self._session_open_equity = account.equity
            self._week_open_equity = self._get_week_open_equity(account.equity)
            self._notifier.send_session_started(account.equity)
            logger.info(f"[paper_trading] session started — equity=${account.equity:.2f}")
        except AlpacaClientError as exc:
            logger.error(f"[paper_trading] failed to fetch account on startup: {exc}")
            self._running = False
            return

        while self._running:
            try:
                self._poll_cycle()
            except AlpacaClientError as exc:
                logger.error(f"[paper_trading] Alpaca error in poll cycle: {exc}")
                self._notifier.send_error(f"Alpaca API error — session halted: {exc}")
                self._running = False
                break
            except Exception as exc:
                logger.error(f"[paper_trading] unexpected error in poll cycle: {exc}")
                self._notifier.send_error(f"Unexpected error — session halted: {exc}")
                self._running = False
                break
            time.sleep(self._poll_interval)

        logger.info("[paper_trading] session stopped")

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._running = False
        logger.info("[paper_trading] stop requested")

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    def _rebuild_portfolio_state(self) -> None:
        """Rebuild in-memory PortfolioState from the positions table."""
        open_positions = storage.fetch_open_positions()
        state = PortfolioState()
        for pos in open_positions:
            state = add_position(state, pos["symbol"])
        self._portfolio_state = state
        logger.info(f"[paper_trading] rebuilt portfolio: {[p['symbol'] for p in open_positions]}")

    def _set_peak_equity(self) -> None:
        """Set peak equity from historical snapshots, or from current account value."""
        peak = storage.fetch_peak_equity()
        if peak > 0.0:
            self._peak_equity = peak
            logger.info(f"[paper_trading] peak equity from history: ${self._peak_equity:.2f}")
        else:
            try:
                account = self._client.get_account()
                self._peak_equity = account.equity
            except AlpacaClientError:
                self._peak_equity = 0.0
            logger.info(f"[paper_trading] peak equity initialized: ${self._peak_equity:.2f}")

    def _get_week_open_equity(self, current_equity: float) -> float:
        """Find equity from 7 days ago, or use current if no history."""
        from datetime import timedelta
        week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        old_snapshots = storage.fetch_equity_snapshots(end=week_ago, limit=1)
        if old_snapshots:
            return old_snapshots[0]["equity"]
        return current_equity

    # ------------------------------------------------------------------
    # Strategy initialization
    # ------------------------------------------------------------------

    def _init_strategies(self) -> None:
        from strategies.mean_reversion import MeanReversionStrategy
        from strategies.momentum import MomentumStrategy
        from strategies.swing import SwingStrategy
        from strategies.meta_strategy import MetaStrategy
        mr = MeanReversionStrategy()
        mom = MomentumStrategy()
        sw = SwingStrategy()
        self._meta_strategy = MetaStrategy([mr, mom, sw])

    # ------------------------------------------------------------------
    # Market clock
    # ------------------------------------------------------------------

    def _is_market_closed(self) -> bool:
        """Return True if market is closed. Returns False on error (fail-open)."""
        try:
            clock = self._client.get_clock()
            return not clock.is_open
        except Exception as exc:
            logger.warning(f"[paper_trading] get_clock failed: {exc} — assuming market open")
            return False

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def _generate_signals(self) -> None:
        """Run data pipeline + MetaStrategy for each symbol, save actionable signals to DB.

        Fail-safe: exceptions per symbol are logged but never propagate.
        """
        from data.ingestion import ingest_symbol
        from data.indicators import calculate_and_store
        from strategies.base import save_signal

        for symbol in self._symbols:
            try:
                # Ingest recent data (5 days, not 365)
                ingest_symbol(symbol, timeframe="1d", lookback_days=5)

                # Time-gate: skip if no new bar since last generation
                latest_ts = storage.fetch_latest_bar_timestamp(symbol, "1d")
                if latest_ts and latest_ts == self._last_signal_bar.get(symbol):
                    logger.debug(f"[paper_trading] {symbol}: no new bar, skipping signal gen")
                    continue

                # Calculate indicators and get enriched DataFrame
                enriched_df = calculate_and_store(symbol, timeframe="1d", lookback_bars=300)
                if enriched_df.empty:
                    logger.warning(f"[paper_trading] {symbol}: no data for signal generation")
                    continue

                signal = self._meta_strategy.run(enriched_df, symbol)

                if signal is not None and signal.direction != Direction.HOLD:
                    save_signal(signal)
                    logger.info(f"[paper_trading] signal saved: {signal.direction.name} {symbol} confidence={signal.confidence:.2f}")
                    # Only lock the time-gate when a signal was actually saved.
                    # If MetaStrategy returned None, we retry next cycle so conditions
                    # that develop mid-session are not missed.
                    if latest_ts:
                        self._last_signal_bar[symbol] = latest_ts
                else:
                    logger.debug(f"[paper_trading] {symbol}: no qualifying signal this cycle")

            except Exception as exc:
                logger.warning(f"[paper_trading] signal generation failed for {symbol}: {exc}")

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _poll_cycle(self) -> None:
        """One tick: snapshot equity, evaluate circuit breakers, process pending signals, sync fills."""
        account = self._client.get_account()
        equity = account.equity

        # Update peak
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Compute P&L percentages
        daily_pnl_pct = (equity - self._session_open_equity) / self._session_open_equity if self._session_open_equity > 0 else 0.0
        weekly_pnl_pct = (equity - self._week_open_equity) / self._week_open_equity if self._week_open_equity > 0 else 0.0
        drawdown_pct = -(self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else 0.0

        # Write equity snapshot
        position_value = account.portfolio_value - account.cash
        storage.insert_equity_snapshot(
            equity=equity,
            cash=account.cash,
            position_value=position_value,
            daily_pnl=equity - self._session_open_equity,
            peak_equity=self._peak_equity,
            weekly_pnl=equity - self._week_open_equity,
        )

        # Always sync fills regardless of halt state (orders submitted before halt must be reconciled)
        self._sync_filled_orders()

        # Update circuit breakers
        cb_state = check_and_update(daily_pnl_pct, weekly_pnl_pct, drawdown_pct)
        if cb_state.get("daily_halt") or cb_state.get("weekly_halt") or cb_state.get("full_halt"):
            reason = cb_state.get("halt_reason") or "circuit breaker triggered"
            logger.warning(f"[paper_trading] halted — skipping signal processing: {reason}")
            if not self._halt_notified:
                self._notifier.send_halt_alert(reason)
                self._halt_notified = True
            return
        self._halt_notified = False  # reset when halt clears

        # Generate signals from latest market data.
        # Market-hours gate removed: paper trading submits orders that queue and fill
        # at next open, so there's no reason to block signal generation outside hours.
        self._generate_signals()

        # Process pending signals
        pending_signals = storage.fetch_pending_signals()
        for sig_row in pending_signals:
            if sig_row.get("signal_type") not in ("BUY", "SELL"):
                continue
            try:
                signal = self._row_to_signal(sig_row)
            except Exception as exc:
                logger.warning(f"[paper_trading] could not reconstruct signal {sig_row.get('id')}: {exc}")
                continue

            try:
                outcome = process_signal(
                    signal=signal,
                    portfolio_state=self._portfolio_state,
                    client=self._client,
                    account_value=equity,
                    signal_db_id=sig_row.get("id"),
                )
                if outcome.submitted and outcome.order_result:
                    logger.debug(f"[paper_trading] order submitted: {outcome.order_result.order_id}")
            except AlpacaClientError:
                raise  # propagate — outer handler stops the session
            except Exception as exc:
                logger.error(f"[paper_trading] process_signal failed for {sig_row.get('symbol')}: {exc}")

    # ------------------------------------------------------------------
    # Fill sync
    # ------------------------------------------------------------------

    def _sync_filled_orders(self) -> None:
        """Query Alpaca for pending order statuses and update DB + portfolio state."""
        pending = storage.fetch_pending_orders()
        for order_row in pending:
            order_id = order_row["alpaca_order_id"]
            try:
                result = self._client.get_order(order_id)
            except AlpacaClientError as exc:
                logger.error(f"[paper_trading] could not fetch order {order_id}: {exc}")
                continue

            if result.status == "filled" and result.filled_avg_price and result.filled_at:
                storage.update_order_filled(
                    order_id,
                    filled_avg_price=result.filled_avg_price,
                    filled_at=result.filled_at,
                )
                if order_row["side"] == "buy":
                    self._handle_buy_fill(order_row, result)
                else:
                    self._handle_sell_fill(order_row, result)

            elif result.status in ("cancelled", "rejected"):
                storage.update_order_status(order_id, result.status)
                logger.warning(f"[paper_trading] order {order_id} {result.status}")

    def _handle_buy_fill(self, order_row: dict, result) -> None:
        """Record fill, insert position, update portfolio state, notify."""
        symbol = order_row["symbol"]
        filled_at = result.filled_at
        filled_price = result.filled_avg_price

        signal_row = storage.fetch_signal_by_id(order_row["signal_id"]) if order_row.get("signal_id") else None
        strategy_name = signal_row["strategy"] if signal_row else "unknown"
        storage.insert_position(
            symbol=symbol,
            qty=order_row["qty"],
            entry_price=filled_price,
            stop_loss=order_row.get("stop_loss_price", 0.0),
            opened_at=filled_at,
            strategy_name=strategy_name,
            take_profit=order_row.get("take_profit_price"),
        )
        self._portfolio_state = add_position(self._portfolio_state, symbol)
        self._notifier.send_fill(symbol, "buy", order_row["qty"], filled_price, pnl=None)
        logger.info(f"[paper_trading] BUY FILL: {order_row['qty']} {symbol} @ ${filled_price:.2f}")

    def _handle_sell_fill(self, order_row: dict, result) -> None:
        """Record fill, create trade record, remove position, notify."""
        symbol = order_row["symbol"]
        filled_at = result.filled_at
        filled_price = result.filled_avg_price

        # Find the open position for PnL
        pos = storage.fetch_position_by_symbol(symbol)

        pnl: float | None = None
        if pos:
            pnl = (filled_price - pos["entry_price"]) * order_row["qty"]
            storage.insert_trade(
                symbol=symbol,
                strategy_name=pos.get("strategy_name", "paper_trading"),
                qty=order_row["qty"],
                entry_price=pos["entry_price"],
                exit_price=filled_price,
                opened_at=pos["opened_at"],
                closed_at=filled_at,
                exit_reason="signal",
            )
            storage.delete_position(symbol)
        else:
            logger.warning(f"[paper_trading] SELL FILL for {symbol} but no open position found in DB")

        self._portfolio_state = remove_position(self._portfolio_state, symbol)
        self._notifier.send_fill(symbol, "sell", order_row["qty"], filled_price, pnl=pnl)
        logger.info(f"[paper_trading] SELL FILL: {order_row['qty']} {symbol} @ ${filled_price:.2f} PnL=${pnl}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_signal(row: dict) -> Signal:
        """Reconstruct a Signal from a signals table row dict."""
        return Signal(
            symbol=row["symbol"],
            direction=Direction[row["signal_type"]],
            price=float(row["entry_price"] or 0),
            stop_loss=float(row["stop_loss"] or 0),
            confidence=float(row["strength"] or 0.5),
            strategy_name=row.get("strategy", "unknown"),
            timestamp=row["timestamp"] if isinstance(row["timestamp"], datetime) else datetime.now(timezone.utc).replace(tzinfo=None),
            timeframe=row.get("timeframe", "1d"),
            take_profit=float(row["take_profit"]) if row.get("take_profit") else None,
        )


def build_session(
    symbols: list[str] | None = None,
    poll_interval_seconds: int = 60,
) -> PaperTradingSession:
    """Convenience factory: build AlpacaClient and TelegramNotifier from settings."""
    from config.settings import DEFAULT_SYMBOLS
    from execution.alpaca_client import make_alpaca_client
    from monitoring.notifications import make_notifier

    syms = symbols or DEFAULT_SYMBOLS
    client = make_alpaca_client()
    notifier = make_notifier()
    return PaperTradingSession(client=client, notifier=notifier, symbols=syms, poll_interval_seconds=poll_interval_seconds)
