from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from config.settings import BASE_DIR

RESULTS_DIR = BASE_DIR / "backtest" / "results"


def calculate_metrics(
    trades: list[dict[str, Any]],
    initial_cash: float,
    final_value: float,
    equity_curve: list[float],
) -> dict[str, Any]:
    """
    Compute Sharpe, max drawdown, win rate from trade log and equity curve.
    Returns dict with all metric keys (None for Sharpe if < 2 data points).
    """
    total_trades = len(trades)
    winning = [t for t in trades if t.get("pnl", 0) > 0]
    losing = [t for t in trades if t.get("pnl", 0) <= 0]

    win_rate = len(winning) / total_trades if total_trades > 0 else 0.0
    avg_win = sum(t["pnl_pct"] for t in winning) / len(winning) if winning else 0.0
    avg_loss = sum(t["pnl_pct"] for t in losing) / len(losing) if losing else 0.0

    # Sharpe ratio (annualized) and max drawdown — computed from the same curve array
    sharpe = None
    max_drawdown = 0.0
    if len(equity_curve) >= 2:
        curve = np.array(equity_curve, dtype=float)
        daily_returns = np.diff(curve) / curve[:-1]
        std = np.std(daily_returns)
        if std > 0:
            sharpe = float(np.mean(daily_returns) / std * np.sqrt(252))
        peak = np.maximum.accumulate(curve)
        safe_peak = np.where(peak > 0, peak, np.inf)
        max_drawdown = float(np.max((peak - curve) / safe_peak))

    total_return_pct = (final_value - initial_cash) / initial_cash if initial_cash > 0 else 0.0

    return {
        "total_return_pct": total_return_pct,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_drawdown,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
    }


def build_result(
    strategy_name: str,
    symbol: str,
    start: datetime,
    end: datetime,
    initial_cash: float,
    final_value: float,
    trades: list[dict[str, Any]],
    signals: list[Any],  # list of Signal objects
    equity_curve: list[float],
) -> dict[str, Any]:
    """Assemble the full result dict from raw backtest output."""
    metrics = calculate_metrics(trades, initial_cash, final_value, equity_curve)

    serialized_signals = [
        {
            "symbol": s.symbol,
            "direction": s.direction.value,
            "price": s.price,
            "stop_loss": s.stop_loss,
            "confidence": s.confidence,
            "strategy_name": s.strategy_name,
            "timestamp": s.timestamp.isoformat() if hasattr(s.timestamp, "isoformat") else str(s.timestamp),
            "timeframe": s.timeframe,
        }
        for s in signals
    ]

    return {
        "symbol": symbol,
        "strategy_name": strategy_name,
        "start": start.isoformat() if hasattr(start, 'isoformat') else str(start),
        "end": end.isoformat() if hasattr(end, 'isoformat') else str(end),
        "initial_cash": initial_cash,
        "final_value": final_value,
        "signals": serialized_signals,
        "run_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        **metrics,
    }


def save_result(result: dict[str, Any]) -> Path:
    """
    Write result dict to backtest/results/{symbol}_{strategy}_{date}.json.
    Creates the results/ directory if it does not exist.
    Returns the path written.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d")
    filename = f"{result['symbol']}_{result['strategy_name']}_{date_str}.json"
    path = RESULTS_DIR / filename
    with path.open("w") as f:
        json.dump(result, f, indent=2, default=str)
    return path


def load_result(path: Path) -> dict[str, Any]:
    """Load a saved result JSON. Used in tests to verify reproducibility."""
    with path.open() as f:
        return json.load(f)
