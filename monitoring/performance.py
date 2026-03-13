from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger


@dataclass
class PerformanceMetrics:
    sharpe_ratio: float
    max_drawdown_pct: float        # e.g. 0.05 = 5%
    win_rate: float                # e.g. 0.60 = 60%
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    avg_trade_pnl: float
    avg_winning_trade: float
    avg_losing_trade: float
    current_equity: float
    peak_equity: float
    computed_at: datetime
    period_start: datetime | None
    period_end: datetime | None


class PerformanceTracker:
    """Read-only metrics computed from trades and equity_snapshots tables."""

    def __init__(self, engine=None) -> None:
        # engine param kept for testability but not used — storage.get_engine() is the singleton
        pass

    def compute_metrics(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> PerformanceMetrics:
        from data.storage import fetch_trades, fetch_equity_snapshots

        trade_rows = fetch_trades(start=start, end=end)
        snapshots = fetch_equity_snapshots(start=start, end=end)

        # Equity curve (snapshots are returned newest-first, reverse for chronological)
        equity_curve = [s["equity"] for s in reversed(snapshots)]
        current_equity = equity_curve[-1] if equity_curve else 0.0
        peak_equity = max(equity_curve) if equity_curve else 0.0

        # Daily returns for Sharpe — group snapshots by date
        daily_returns = self._compute_daily_returns(list(reversed(snapshots)))

        pnl_values = [t["pnl"] for t in trade_rows]
        winning = [p for p in pnl_values if p > 0]
        losing = [p for p in pnl_values if p <= 0]

        total_pnl = sum(pnl_values)
        return PerformanceMetrics(
            sharpe_ratio=self._compute_sharpe(daily_returns),
            max_drawdown_pct=self._compute_max_drawdown(equity_curve),
            win_rate=self._compute_win_rate(pnl_values),
            profit_factor=self._compute_profit_factor(pnl_values),
            total_trades=len(trade_rows),
            winning_trades=len(winning),
            losing_trades=len(losing),
            total_pnl=total_pnl,
            avg_trade_pnl=total_pnl / len(pnl_values) if pnl_values else 0.0,
            avg_winning_trade=sum(winning) / len(winning) if winning else 0.0,
            avg_losing_trade=sum(losing) / len(losing) if losing else 0.0,
            current_equity=current_equity,
            peak_equity=peak_equity,
            computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            period_start=start,
            period_end=end,
        )

    def _compute_daily_returns(self, snapshots: list[dict]) -> list[float]:
        """Group snapshots by date, take last equity per day, compute daily returns."""
        by_date: dict[str, float] = {}
        for s in snapshots:
            ts = s["timestamp"]
            date_key = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            by_date[date_key] = s["equity"]  # last write wins (snapshots are chronological)

        equities = list(by_date.values())
        if len(equities) < 2:
            return []
        return [
            (equities[i] / equities[i - 1]) - 1.0
            for i in range(1, len(equities))
        ]

    def _compute_sharpe(self, daily_returns: list[float]) -> float:
        """Annualized Sharpe ratio. Returns 0.0 if insufficient data."""
        if len(daily_returns) < 2:
            return 0.0
        n = len(daily_returns)
        mean = sum(daily_returns) / n
        variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    def _compute_max_drawdown(self, equity_curve: list[float]) -> float:
        """Maximum peak-to-trough drawdown as a fraction (0.0–1.0)."""
        if len(equity_curve) < 2:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak
                max_dd = max(max_dd, dd)
        return max_dd

    def _compute_win_rate(self, pnl_values: list[float]) -> float:
        """Fraction of trades with pnl > 0."""
        if not pnl_values:
            return 0.0
        return sum(1 for p in pnl_values if p > 0) / len(pnl_values)

    def _compute_profit_factor(self, pnl_values: list[float]) -> float:
        """Gross profit / abs(gross loss)."""
        gross_profit = sum(p for p in pnl_values if p > 0)
        gross_loss = abs(sum(p for p in pnl_values if p < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss


def format_metrics_report(metrics: PerformanceMetrics) -> str:
    """Human-readable report suitable for Telegram or CLI output."""
    lines = [
        "=== Performance Report ===",
        f"Computed: {metrics.computed_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Equity: ${metrics.current_equity:,.2f} (peak ${metrics.peak_equity:,.2f})",
        f"Sharpe: {metrics.sharpe_ratio:.2f}",
        f"Max Drawdown: {metrics.max_drawdown_pct:.1%}",
        f"Win Rate: {metrics.win_rate:.1%}",
        f"Profit Factor: {metrics.profit_factor:.2f}",
        f"Total PnL: ${metrics.total_pnl:+,.2f}",
        f"Trades: {metrics.total_trades} ({metrics.winning_trades}W / {metrics.losing_trades}L)",
        f"Avg Trade: ${metrics.avg_trade_pnl:+.2f}",
    ]
    return "\n".join(lines)
