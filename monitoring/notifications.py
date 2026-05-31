from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class NotifierProtocol(Protocol):
    def send_fill(self, symbol: str, side: str, qty: int, price: float, pnl: float | None) -> None: ...
    def send_halt_alert(self, reason: str) -> None: ...
    def send_error(self, message: str) -> None: ...
    def send_session_started(self, equity: float) -> None: ...


class NullNotifier:
    """No-op notifier for testing or when Telegram is not configured."""
    def send_fill(self, symbol: str, side: str, qty: int, price: float, pnl: float | None) -> None:
        pass
    def send_halt_alert(self, reason: str) -> None:
        pass
    def send_error(self, message: str) -> None:
        pass
    def send_session_started(self, equity: float) -> None:
        pass


class TelegramNotifier:
    """Sends alerts via Telegram Bot API (python-telegram-bot v20+ async)."""

    _MAX_MESSAGE_LEN = 3900

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    def _send(self, text: str) -> None:
        """Send a message. Never raises — Telegram failure must not halt trading."""
        if len(text) > self._MAX_MESSAGE_LEN:
            text = text[:self._MAX_MESSAGE_LEN] + "..."
        try:
            asyncio.run(self._async_send(text))
        except RuntimeError as exc:
            # Event loop already running (e.g. inside jupyter or some test runners)
            logger.warning(f"[notifications] asyncio.run failed ({exc}) — skipping Telegram send")
        except Exception as exc:
            logger.warning(f"[notifications] Telegram send failed: {exc}")

    async def _async_send(self, text: str) -> None:
        from telegram import Bot
        async with Bot(token=self._token) as bot:
            await bot.send_message(chat_id=self._chat_id, text=text)

    def send_fill(self, symbol: str, side: str, qty: int, price: float, pnl: float | None) -> None:
        pnl_str = f" | PnL: ${pnl:+.2f}" if pnl is not None else ""
        self._send(f"FILLED: {side.upper()} {qty} {symbol} @ ${price:.2f}{pnl_str}")

    def send_halt_alert(self, reason: str) -> None:
        self._send(f"TRADING HALTED: {reason}")

    def send_error(self, message: str) -> None:
        self._send(f"ERROR: {message}")

    def send_session_started(self, equity: float) -> None:
        self._send(f"Paper trading session started. Equity: ${equity:.2f}")


def make_notifier() -> TelegramNotifier | NullNotifier:
    """
    Return TelegramNotifier if credentials configured, NullNotifier otherwise.

    Uses the shared settings.telegram_configured() check so placeholder creds
    (e.g. 'your_bot_token_here') resolve to NullNotifier rather than a notifier
    that fails on every send.
    """
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, telegram_configured
    if telegram_configured():
        logger.debug("[notifications] TelegramNotifier active")
        return TelegramNotifier(token=TELEGRAM_BOT_TOKEN, chat_id=TELEGRAM_CHAT_ID)
    logger.warning("[notifications] TELEGRAM credentials not set — using NullNotifier")
    return NullNotifier()
