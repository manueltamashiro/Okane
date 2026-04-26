from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from loguru import logger


class AlpacaClientError(RuntimeError):
    """Raised when Alpaca API calls fail. Wraps the original exception."""


@dataclass
class AccountInfo:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str              # 'buy' | 'sell'
    qty: int
    status: str            # 'accepted' | 'filled' | 'cancelled' | 'rejected'
    filled_avg_price: float | None
    filled_at: datetime | None   # UTC-naive


@dataclass
class ClockInfo:
    is_open: bool
    next_open: datetime | None
    next_close: datetime | None


@dataclass
class PositionInfo:
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float


@runtime_checkable
class AlpacaClientProtocol(Protocol):
    def get_account(self) -> AccountInfo: ...
    def submit_market_order(self, symbol: str, qty: int, side: str, time_in_force: str = "day") -> OrderResult: ...
    def get_order(self, order_id: str) -> OrderResult: ...
    def get_open_positions(self) -> list[PositionInfo]: ...
    def get_clock(self) -> ClockInfo: ...


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    """Convert any datetime to UTC-naive. Returns None if input is None."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class AlpacaClient:
    """Wraps alpaca-py TradingClient. All exceptions from alpaca-py are caught and re-raised as AlpacaClientError."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        try:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
            logger.debug(f"[alpaca_client] initialized (paper={paper})")
        except Exception as exc:
            raise AlpacaClientError(f"Failed to initialize TradingClient: {exc}") from exc

    def get_account(self) -> AccountInfo:
        try:
            acct = self._client.get_account()
            return AccountInfo(
                equity=float(acct.equity),
                cash=float(acct.cash),
                buying_power=float(acct.buying_power),
                portfolio_value=float(acct.portfolio_value),
            )
        except Exception as exc:
            raise AlpacaClientError(f"get_account failed: {exc}") from exc

    def submit_market_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        time_in_force: str = "day",
    ) -> OrderResult:
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            tif_map = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC, "ioc": TimeInForce.IOC, "fok": TimeInForce.FOK}
            tif_key = time_in_force.lower()
            if tif_key not in tif_map:
                raise AlpacaClientError(f"Unrecognized time_in_force value: {time_in_force!r}")
            tif_enum = tif_map[tif_key]
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif_enum,
            )
            order = self._client.submit_order(order_data=request)
            logger.debug(f"[alpaca_client] order submitted: {order.id} {side} {qty} {symbol}")
            return OrderResult(
                order_id=str(order.id),
                symbol=symbol,
                side=side.lower(),
                qty=qty,
                status=str(order.status.value) if hasattr(order.status, "value") else str(order.status),
                filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                filled_at=_to_utc_naive(order.filled_at),
            )
        except AlpacaClientError:
            raise
        except Exception as exc:
            raise AlpacaClientError(f"submit_market_order failed for {symbol}: {exc}") from exc

    def get_order(self, order_id: str) -> OrderResult:
        try:
            order = self._client.get_order_by_id(order_id)
            return OrderResult(
                order_id=str(order.id),
                symbol=str(order.symbol),
                side=str(order.side.value) if hasattr(order.side, "value") else str(order.side),
                qty=int(order.qty or 0),
                status=str(order.status.value) if hasattr(order.status, "value") else str(order.status),
                filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                filled_at=_to_utc_naive(order.filled_at),
            )
        except AlpacaClientError:
            raise
        except Exception as exc:
            raise AlpacaClientError(f"get_order failed for {order_id}: {exc}") from exc

    def get_open_positions(self) -> list[PositionInfo]:
        try:
            raw = self._client.get_all_positions()
            return [
                PositionInfo(
                    symbol=str(p.symbol),
                    qty=int(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(p.current_price) if p.current_price else 0.0,
                    unrealized_pnl=float(p.unrealized_pl) if p.unrealized_pl else 0.0,
                )
                for p in raw
            ]
        except Exception as exc:
            raise AlpacaClientError(f"get_open_positions failed: {exc}") from exc

    def get_clock(self) -> ClockInfo:
        try:
            clock = self._client.get_clock()
            return ClockInfo(
                is_open=clock.is_open,
                next_open=_to_utc_naive(clock.next_open),
                next_close=_to_utc_naive(clock.next_close),
            )
        except Exception as exc:
            raise AlpacaClientError(f"get_clock failed: {exc}") from exc


def make_alpaca_client(
    api_key: str | None = None,
    secret_key: str | None = None,
    paper: bool | None = None,
) -> AlpacaClient:
    """Construct AlpacaClient using config/settings.py defaults if args are None."""
    from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER
    key = api_key or ALPACA_API_KEY
    secret = secret_key or ALPACA_SECRET_KEY
    use_paper = paper if paper is not None else ALPACA_PAPER
    if not key or not secret:
        raise EnvironmentError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")
    return AlpacaClient(api_key=key, secret_key=secret, paper=use_paper)
