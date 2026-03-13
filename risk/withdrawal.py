from __future__ import annotations

from loguru import logger

from strategies.base import load_strategy_config


_WITHDRAWAL_CFG: dict | None = None


def _get_cfg() -> dict:
    global _WITHDRAWAL_CFG
    if _WITHDRAWAL_CFG is None:
        cfg = load_strategy_config()["withdrawal"]
        total = cfg["reinvest_pct"] + cfg["tax_reserve_pct"] + cfg["income_pct"]
        assert abs(total - 1.0) < 1e-9, f"Withdrawal ratios must sum to 1.0, got {total}"
        _WITHDRAWAL_CFG = cfg
    return _WITHDRAWAL_CFG


def calculate_withdrawal(realized_pnl: float) -> dict[str, float]:
    """
    Split realized P&L into reinvest / tax_reserve / income per the 50/30/20 rule.
    Returns a dict with the three allocations.
    """
    cfg = _get_cfg()
    return {
        "reinvest":    realized_pnl * cfg["reinvest_pct"],
        "tax_reserve": realized_pnl * cfg["tax_reserve_pct"],
        "income":      realized_pnl * cfg["income_pct"],
    }


def can_withdraw(
    portfolio_value: float,
    base_value: float,
    current_drawdown: float,
) -> bool:
    """
    Return False if withdrawal should be paused:
      - Drawdown >= pause_drawdown_threshold (0.05)
      - Portfolio below safety cushion (base * 1.10)
    """
    cfg = _get_cfg()
    pause_threshold = cfg["pause_drawdown_threshold"]
    safety_cushion = cfg["safety_cushion_pct"]

    if current_drawdown >= pause_threshold:
        logger.debug(f"[withdrawal] paused: drawdown {current_drawdown:.2%} >= threshold {pause_threshold:.2%}")
        return False

    min_value = base_value * (1 + safety_cushion)
    if portfolio_value < min_value:
        logger.debug(f"[withdrawal] paused: portfolio {portfolio_value:.2f} < safety cushion {min_value:.2f}")
        return False

    return True
