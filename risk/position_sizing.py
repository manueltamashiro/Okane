from __future__ import annotations

import math

from loguru import logger

from config.settings import (
    MAX_RISK_PER_TRADE,
    MAX_POSITION_SIZE_PCT,
    VIX_BRAKE_THRESHOLD,
    VIX_BRAKE_MULTIPLIER,
)
from strategies.base import Signal


def calculate_position_size(
    signal: Signal,
    account_value: float,
    vix: float | None = None,
) -> int:
    """
    Calculate share count for a BUY signal applying:
      1. Fixed fractional risk (1% of account)
      2. Half-Kelly proxy (confidence * 0.5 as Kelly fraction)
      3. 5% portfolio cap
      4. VIX brake (halve if VIX > threshold)

    Returns 0 for any invalid input. Never raises.
    """
    if account_value <= 0:
        logger.warning(f"[position_sizing] invalid account_value={account_value}")
        return 0
    if signal.price <= 0:
        logger.warning(f"[position_sizing] invalid price={signal.price} for {signal.symbol}")
        return 0

    risk_per_share = signal.price - signal.stop_loss
    if risk_per_share <= 0:
        logger.warning(
            f"[position_sizing] stop_loss ({signal.stop_loss}) >= price ({signal.price}) "
            f"for {signal.symbol} — returning 0"
        )
        return 0

    # Fixed fractional: dollar risk budget
    risk_dollars = account_value * MAX_RISK_PER_TRADE
    raw_shares = risk_dollars / risk_per_share

    # Half-Kelly proxy: scale by confidence * 0.5 (confidence clamped to [0, 1])
    confidence = max(0.0, min(1.0, signal.confidence))
    kelly_shares = raw_shares * (confidence * 0.5)

    # 5% portfolio cap
    max_cap_shares = math.floor((account_value * MAX_POSITION_SIZE_PCT) / signal.price)

    shares = min(kelly_shares, max_cap_shares)

    # VIX brake
    if vix is not None and vix > VIX_BRAKE_THRESHOLD:
        shares = shares * VIX_BRAKE_MULTIPLIER

    result = max(0, math.floor(shares))
    logger.debug(
        f"[position_sizing] {signal.symbol}: {result} shares "
        f"(account={account_value:.0f}, risk/share={risk_per_share:.2f}, "
        f"confidence={confidence:.2f}, vix={vix})"
    )
    return result
