from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from config.settings import MAX_CONCURRENT_POSITIONS, MAX_SECTOR_EXPOSURE


# Simple sector map for default symbols. Extend as new symbols are added.
SECTOR_MAP: dict[str, str] = {
    "AAPL": "Technology",
    "VTV":  "Value ETF",
    "VTI":  "Broad ETF",
}


@dataclass
class PortfolioState:
    open_positions: list[str] = field(default_factory=list)
    sector_map: dict[str, str] = field(default_factory=dict)  # symbol → sector for open positions


def can_open_position(
    symbol: str,
    portfolio_state: PortfolioState,
) -> tuple[bool, str | None]:
    """
    Check whether a new position can be opened for the given symbol.

    Returns (True, None) if all constraints pass, or (False, reason) if rejected.
    Checks in order:
      1. No duplicate open position in same symbol
      2. Total positions < MAX_CONCURRENT_POSITIONS
      3. Sector exposure < MAX_SECTOR_EXPOSURE
    """
    if symbol in portfolio_state.open_positions:
        reason = f"Already have open position in {symbol}"
        logger.debug(f"[portfolio] rejected {symbol}: {reason}")
        return False, reason

    if len(portfolio_state.open_positions) >= MAX_CONCURRENT_POSITIONS:
        reason = f"Max concurrent positions ({MAX_CONCURRENT_POSITIONS}) reached"
        logger.debug(f"[portfolio] rejected {symbol}: {reason}")
        return False, reason

    sector = SECTOR_MAP.get(symbol, "Unknown")
    sector_count = sum(
        1 for sym in portfolio_state.open_positions
        if portfolio_state.sector_map.get(sym, "Unknown") == sector
    )
    if sector_count >= MAX_SECTOR_EXPOSURE:
        reason = f"Max sector exposure ({MAX_SECTOR_EXPOSURE}) reached for {sector}"
        logger.debug(f"[portfolio] rejected {symbol}: {reason}")
        return False, reason

    return True, None


def add_position(portfolio_state: PortfolioState, symbol: str) -> PortfolioState:
    """Return a new PortfolioState with symbol added. Sector derived from SECTOR_MAP. Does not mutate the original."""
    new_positions = list(portfolio_state.open_positions) + [symbol]
    new_sector_map = dict(portfolio_state.sector_map)
    new_sector_map[symbol] = SECTOR_MAP.get(symbol, "Unknown")
    return PortfolioState(open_positions=new_positions, sector_map=new_sector_map)


def remove_position(portfolio_state: PortfolioState, symbol: str) -> PortfolioState:
    """Return a new PortfolioState with symbol removed. No-op if symbol not present."""
    new_positions = [s for s in portfolio_state.open_positions if s != symbol]
    new_sector_map = {k: v for k, v in portfolio_state.sector_map.items() if k != symbol}
    return PortfolioState(open_positions=new_positions, sector_map=new_sector_map)
