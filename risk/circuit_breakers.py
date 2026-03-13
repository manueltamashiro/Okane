from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from config.settings import DAILY_LOSS_LIMIT, WEEKLY_LOSS_LIMIT, MAX_DRAWDOWN
from data.storage import circuit_breaker_state, get_engine


_DEFAULT_STATE: dict[str, Any] = {
    "id": 1,
    "daily_loss_pct": 0.0,
    "weekly_loss_pct": 0.0,
    "max_drawdown_pct": 0.0,
    "daily_halt": False,
    "weekly_halt": False,
    "full_halt": False,
    "halt_reason": None,
    "last_reset_date": None,
    "updated_at": None,
}


def load_state() -> dict[str, Any]:
    """
    Read the circuit_breaker_state singleton row.
    Returns safe defaults if the singleton row is missing.
    Raises on DB error — callers that must not trade through errors (is_halted) catch this.
    """
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            circuit_breaker_state.select().where(circuit_breaker_state.c.id == 1)
        ).fetchone()
    if row is None:
        logger.warning("[circuit_breakers] singleton row not found — returning safe defaults")
        return dict(_DEFAULT_STATE)
    return dict(row._mapping)


def save_state(state: dict[str, Any]) -> None:
    """Write updated circuit_breaker_state to DB. Raises on failure (fail-safe)."""
    engine = get_engine()
    from data.storage import _utcnow
    now = _utcnow()
    with engine.begin() as conn:
        conn.execute(
            circuit_breaker_state.update()
            .where(circuit_breaker_state.c.id == 1)
            .values(
                daily_loss_pct=state["daily_loss_pct"],
                weekly_loss_pct=state["weekly_loss_pct"],
                max_drawdown_pct=state["max_drawdown_pct"],
                daily_halt=state["daily_halt"],
                weekly_halt=state["weekly_halt"],
                full_halt=state["full_halt"],
                halt_reason=state.get("halt_reason"),
                last_reset_date=state.get("last_reset_date"),
                updated_at=now,
            )
        )


def _build_halt_reason(
    state: dict[str, Any],
    daily_triggered: bool,
    weekly_triggered: bool,
    full_triggered: bool,
) -> str | None:
    """Assemble or preserve a halt reason string."""
    existing = state.get("halt_reason") or ""
    parts = [existing] if existing else []
    if daily_triggered:
        parts.append(f"Daily loss limit breached: {state['daily_loss_pct']:.2%}")
    if weekly_triggered:
        parts.append(f"Weekly loss limit breached: {state['weekly_loss_pct']:.2%}")
    if full_triggered:
        parts.append(f"Max drawdown breached: {state['max_drawdown_pct']:.2%}")
    return " | ".join(parts) if parts else None


def check_and_update(
    daily_pnl_pct: float,
    weekly_pnl_pct: float,
    drawdown_pct: float,
) -> dict[str, Any]:
    """
    Evaluate halt thresholds and persist updated state.

    Idempotent: calling with the same arguments twice produces identical DB state.
    Once a halt is set, it persists until explicitly cleared (reset_daily / manual).
    Raises on DB write failure (fail-safe: halt rather than trade through an error).
    """
    state = load_state()

    # Update metrics
    state["daily_loss_pct"] = daily_pnl_pct
    state["weekly_loss_pct"] = weekly_pnl_pct
    state["max_drawdown_pct"] = drawdown_pct

    # Evaluate new halts (OR with existing — halts are sticky)
    daily_new = not state["daily_halt"] and daily_pnl_pct <= -DAILY_LOSS_LIMIT
    weekly_new = not state["weekly_halt"] and weekly_pnl_pct <= -WEEKLY_LOSS_LIMIT
    full_new = not state["full_halt"] and drawdown_pct <= -MAX_DRAWDOWN

    state["daily_halt"] = state["daily_halt"] or daily_new
    state["weekly_halt"] = state["weekly_halt"] or weekly_new
    state["full_halt"] = state["full_halt"] or full_new

    for triggered, label, detail in (
        (daily_new, "DAILY HALT", f"{daily_pnl_pct:.2%} loss"),
        (weekly_new, "WEEKLY HALT", f"{weekly_pnl_pct:.2%} loss"),
        (full_new, "FULL HALT", f"{drawdown_pct:.2%} drawdown — manual review required"),
    ):
        if triggered:
            logger.warning(f"[circuit_breakers] {label} triggered: {detail}")

    state["halt_reason"] = _build_halt_reason(state, daily_new, weekly_new, full_new)
    save_state(state)
    return state


def reset_daily() -> None:
    """
    Clear daily halt and reset daily loss counter.
    Safe to call multiple times per day — skips if already reset today.
    Does NOT clear weekly_halt or full_halt.
    """
    state = load_state()
    from data.storage import _utcnow
    today = _utcnow().strftime("%Y-%m-%d")
    if state.get("last_reset_date") == today:
        logger.debug("[circuit_breakers] reset_daily: already reset today, skipping")
        return
    state["daily_halt"] = False
    state["daily_loss_pct"] = 0.0
    state["last_reset_date"] = today
    # Only clear halt_reason if no other halts remain active
    if not state["weekly_halt"] and not state["full_halt"]:
        state["halt_reason"] = None
    save_state(state)
    logger.warning(f"[circuit_breakers] daily reset complete for {today}")


def is_halted() -> bool:
    """
    Return True if ANY halt flag is set. Never raises — returns True on error (fail-safe).
    """
    try:
        state = load_state()
        return bool(state["daily_halt"] or state["weekly_halt"] or state["full_halt"])
    except Exception as exc:
        logger.error(f"[circuit_breakers] is_halted check failed: {exc} — treating as halted")
        return True
