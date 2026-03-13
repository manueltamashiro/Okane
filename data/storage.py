"""
SQLite database interface using SQLAlchemy Core.

Convention: all timestamps are stored as UTC-naive datetimes (tzinfo stripped
before insert). This ensures consistent SQLite string serialization and reliable
deduplication via the unique constraints.

Tables:
  price_data            — OHLCV bars (Alpaca + yfinance)
  indicator_values      — Calculated technical indicators (EAV format)
  signals               — Strategy signals before and after risk filtering
  circuit_breaker_state — Singleton row tracking halt flags (survives restarts)
  orders                — Orders submitted to Alpaca (Phase 4)
  positions             — Currently open positions (Phase 4)
  trades                — Completed trade history with PnL (Phase 4)
  equity_snapshots      — Periodic equity/drawdown snapshots (Phase 4)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine

from config.settings import DB_URL


# ---------------------------------------------------------------------------
# Engine + schema
# ---------------------------------------------------------------------------

_engine: Engine | None = None
metadata = MetaData()


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- price_data ---
price_data = Table(
    "price_data",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(10), nullable=False),
    Column("timestamp", DateTime, nullable=False),   # UTC-naive
    Column("timeframe", String(10), nullable=False),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=False),
    Column("vwap", Float),
    Column("trade_count", Integer),
    Column("source", String(20), nullable=False),    # 'alpaca' | 'yfinance'
    Column("created_at", DateTime, default=_utcnow),
    UniqueConstraint("symbol", "timestamp", "timeframe", name="uq_price_data"),
)

# --- indicator_values (EAV — no schema migrations when adding indicators) ---
indicator_values = Table(
    "indicator_values",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(10), nullable=False),
    Column("timestamp", DateTime, nullable=False),   # UTC-naive
    Column("timeframe", String(10), nullable=False),
    Column("indicator_name", String(50), nullable=False),
    Column("value", Float, nullable=False),
    Column("created_at", DateTime, default=_utcnow),
    UniqueConstraint(
        "symbol", "timestamp", "timeframe", "indicator_name",
        name="uq_indicator_values",
    ),
)

# --- signals ---
signals = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(10), nullable=False),
    Column("timestamp", DateTime, nullable=False),   # UTC-naive
    Column("strategy", String(30), nullable=False),  # 'mean_reversion' | 'momentum' | 'swing' | 'meta'
    Column("signal_type", String(10), nullable=False),  # 'BUY' | 'SELL' | 'HOLD'
    Column("strength", Float),          # 0.0 – 1.0 for meta-strategy voting
    Column("entry_price", Float),
    Column("stop_loss", Float),
    Column("take_profit", Float),
    Column("timeframe", String(10)),
    Column("risk_approved", Boolean, default=None),  # None = not yet evaluated
    Column("risk_rejection_reason", Text),
    Column("notes", Text),              # JSON blob for extra context
    Column("created_at", DateTime, default=_utcnow),
)

# --- circuit_breaker_state (singleton row, id always = 1) ---
circuit_breaker_state = Table(
    "circuit_breaker_state",
    metadata,
    Column("id", Integer, primary_key=True),  # always 1
    Column("daily_loss_pct", Float, default=0.0),
    Column("weekly_loss_pct", Float, default=0.0),
    Column("max_drawdown_pct", Float, default=0.0),
    Column("daily_halt", Boolean, default=False),
    Column("weekly_halt", Boolean, default=False),
    Column("full_halt", Boolean, default=False),
    Column("halt_reason", Text),
    Column("last_reset_date", String(10)),  # 'YYYY-MM-DD'
    Column("updated_at", DateTime, default=_utcnow),
)

# --- orders ---
orders = Table(
    "orders", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("alpaca_order_id", String(50), nullable=False, unique=True),
    Column("signal_id", Integer),
    Column("symbol", String(10), nullable=False),
    Column("side", String(4), nullable=False),       # 'buy' | 'sell'
    Column("qty", Integer, nullable=False),
    Column("order_type", String(20), nullable=False),
    Column("time_in_force", String(10), nullable=False),
    Column("status", String(20), nullable=False),   # 'pending' | 'filled' | 'cancelled' | 'rejected'
    Column("submitted_at", DateTime, nullable=False),
    Column("filled_at", DateTime),
    Column("filled_avg_price", Float),
    Column("stop_loss_price", Float),
    Column("take_profit_price", Float),
    Column("created_at", DateTime, nullable=False),
)

# --- positions ---
positions = Table(
    "positions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(10), nullable=False, unique=True),
    Column("qty", Integer, nullable=False),
    Column("entry_price", Float, nullable=False),
    Column("stop_loss", Float, nullable=False),
    Column("take_profit", Float),
    Column("opened_at", DateTime, nullable=False),
    Column("strategy_name", String(30), nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

# --- trades ---
trades = Table(
    "trades", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(10), nullable=False),
    Column("strategy_name", String(30), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("entry_price", Float, nullable=False),
    Column("exit_price", Float, nullable=False),
    Column("pnl", Float, nullable=False),
    Column("pnl_pct", Float, nullable=False),
    Column("opened_at", DateTime, nullable=False),
    Column("closed_at", DateTime, nullable=False),
    Column("exit_reason", String(30), nullable=False),  # 'signal' | 'stop_loss' | 'take_profit'
    Column("holding_days", Integer),
    Column("created_at", DateTime, nullable=False),
)

# --- equity_snapshots ---
equity_snapshots = Table(
    "equity_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime, nullable=False),
    Column("equity", Float, nullable=False),
    Column("cash", Float, nullable=False),
    Column("position_value", Float, nullable=False),
    Column("daily_pnl", Float, nullable=False),
    Column("weekly_pnl", Float),
    Column("drawdown_pct", Float, nullable=False),
    Column("peak_equity", Float, nullable=False),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_utc_naive(ts: Any) -> datetime:
    """Convert any timestamp-like value to a UTC-naive datetime for storage."""
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = pd.Timestamp(ts).to_pydatetime()
    # Strip timezone so SQLite stores a consistent string format
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Engine initialization
# ---------------------------------------------------------------------------

def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL, echo=False, future=True)
        logger.info(f"Database engine created: {DB_URL}")
    return _engine


def init_db() -> None:
    """Create all tables if they don't exist and seed circuit breaker singleton."""
    engine = get_engine()
    metadata.create_all(engine)
    logger.info("Database schema initialized")

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT COUNT(*) FROM circuit_breaker_state WHERE id = 1")
        ).scalar()
        if not exists:
            conn.execute(
                circuit_breaker_state.insert().values(
                    id=1,
                    daily_loss_pct=0.0,
                    weekly_loss_pct=0.0,
                    max_drawdown_pct=0.0,
                    daily_halt=False,
                    weekly_halt=False,
                    full_halt=False,
                    halt_reason=None,
                    last_reset_date=None,
                    updated_at=_utcnow(),
                )
            )
            logger.info("Circuit breaker singleton row created")


# ---------------------------------------------------------------------------
# OHLCV repository
# ---------------------------------------------------------------------------

def upsert_bars(df: pd.DataFrame, symbol: str, timeframe: str, source: str) -> int:
    """
    Bulk-insert OHLCV bars into price_data, skipping rows that already exist.

    Args:
        df:        DataFrame indexed by timestamp with lowercase OHLCV columns.
                   Optional columns: vwap, trade_count.
        symbol:    Ticker symbol e.g. 'AAPL'
        timeframe: Canonical timeframe string e.g. '1d', '1h', '4h'
        source:    'alpaca' or 'yfinance'

    Returns:
        Number of rows inserted (duplicates silently skipped).
    """
    if df.empty:
        return 0

    engine = get_engine()
    now = _utcnow()

    rows: list[dict[str, Any]] = [
        {
            "symbol": symbol,
            "timestamp": _to_utc_naive(ts),
            "timeframe": timeframe,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "vwap": float(row["vwap"]) if "vwap" in row and pd.notna(row["vwap"]) else None,
            "trade_count": int(row["trade_count"]) if "trade_count" in row and pd.notna(row["trade_count"]) else None,
            "source": source,
            "created_at": now,
        }
        for ts, row in df.iterrows()
    ]

    # Bulk INSERT OR IGNORE — one round-trip for all rows
    with engine.begin() as conn:
        result = conn.execute(
            price_data.insert().prefix_with("OR IGNORE"), rows
        )
    inserted = result.rowcount
    logger.debug(f"upsert_bars: {inserted}/{len(rows)} rows inserted for {symbol} [{timeframe}]")
    return inserted


def fetch_bars(
    symbol: str,
    timeframe: str,
    limit: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """
    Retrieve OHLCV bars from price_data as a DataFrame indexed by UTC-naive timestamp.

    Returns DataFrame with columns: open, high, low, close, volume, vwap, trade_count, source
    """
    engine = get_engine()
    query = (
        price_data.select()
        .where(price_data.c.symbol == symbol)
        .where(price_data.c.timeframe == timeframe)
        .order_by(price_data.c.timestamp.asc())
    )
    if start:
        query = query.where(price_data.c.timestamp >= _to_utc_naive(start))
    if end:
        query = query.where(price_data.c.timestamp <= _to_utc_naive(end))
    if limit:
        # Return the most recent N rows: select timestamps descending, then re-order ascending
        ts_sub = select(price_data.c.timestamp).where(
            price_data.c.symbol == symbol
        ).where(
            price_data.c.timeframe == timeframe
        )
        if start:
            ts_sub = ts_sub.where(price_data.c.timestamp >= _to_utc_naive(start))
        if end:
            ts_sub = ts_sub.where(price_data.c.timestamp <= _to_utc_naive(end))
        ts_sub = ts_sub.order_by(price_data.c.timestamp.desc()).limit(limit).scalar_subquery()
        query = query.where(price_data.c.timestamp.in_(ts_sub))

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[c.key for c in price_data.c])
    df = df.set_index("timestamp").drop(columns=["id", "symbol", "timeframe", "created_at"])
    return df


# ---------------------------------------------------------------------------
# Indicator repository
# ---------------------------------------------------------------------------

def upsert_indicators(
    df: pd.DataFrame, symbol: str, timeframe: str, indicator_cols: list[str]
) -> int:
    """
    Bulk-store calculated indicator columns from a DataFrame.

    Args:
        df:              DataFrame indexed by timestamp, with indicator columns.
        symbol:          Ticker symbol.
        timeframe:       Canonical timeframe string.
        indicator_cols:  Column names to store (e.g. ['rsi_14', 'bb_lower_20_2']).

    Returns:
        Number of rows inserted (duplicates silently skipped).
    """
    engine = get_engine()
    now = _utcnow()
    rows = []

    for ts, row in df[indicator_cols].dropna(how="all").iterrows():
        ts_naive = _to_utc_naive(ts)
        for col in indicator_cols:
            val = row[col]
            if pd.isna(val):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": ts_naive,
                    "timeframe": timeframe,
                    "indicator_name": col,
                    "value": float(val),
                    "created_at": now,
                }
            )

    if not rows:
        return 0

    with engine.begin() as conn:
        result = conn.execute(
            indicator_values.insert().prefix_with("OR IGNORE"), rows
        )
    inserted = result.rowcount
    logger.debug(f"upsert_indicators: {inserted}/{len(rows)} values stored for {symbol} [{timeframe}]")
    return inserted


def upsert_signal(signal: "Signal") -> int:
    """
    Insert a Signal into the signals table.
    Returns 1 on insert, 0 if already exists (same symbol+timestamp+strategy).
    Uses INSERT OR IGNORE for idempotency.
    """
    engine = get_engine()
    ts = _to_utc_naive(signal.timestamp)
    with engine.begin() as conn:
        result = conn.execute(
            signals.insert().prefix_with("OR IGNORE"),
            {
                "symbol": signal.symbol,
                "timestamp": ts,
                "strategy": signal.strategy_name,
                "signal_type": signal.direction.value,
                "strength": signal.confidence,
                "entry_price": signal.price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "timeframe": signal.timeframe,
                "notes": str(signal.notes) if signal.notes else None,
            },
        )
        inserted = result.rowcount
    logger.debug(f"upsert_signal: {inserted} row inserted for {signal.symbol} [{signal.strategy_name}] @ {ts}")
    return inserted


def fetch_indicators(
    symbol: str,
    timeframe: str,
    indicator_names: list[str] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """
    Retrieve indicator values as a wide-format DataFrame (pivoted by indicator_name).

    Args:
        limit: Return only the most recent N timestamps (applied via subquery).

    Returns DataFrame indexed by timestamp with one column per indicator.
    """
    engine = get_engine()

    # If limit is set, fetch only the most recent N distinct timestamps via subquery
    if limit:
        sub = (
            select(indicator_values.c.timestamp)
            .where(indicator_values.c.symbol == symbol)
            .where(indicator_values.c.timeframe == timeframe)
            .distinct()
            .order_by(indicator_values.c.timestamp.desc())
            .limit(limit)
            .scalar_subquery()
        )
        query = (
            indicator_values.select()
            .where(indicator_values.c.symbol == symbol)
            .where(indicator_values.c.timeframe == timeframe)
            .where(indicator_values.c.timestamp.in_(sub))
            .order_by(indicator_values.c.timestamp.asc())
        )
    else:
        query = (
            indicator_values.select()
            .where(indicator_values.c.symbol == symbol)
            .where(indicator_values.c.timeframe == timeframe)
            .order_by(indicator_values.c.timestamp.asc())
        )

    if indicator_names:
        query = query.where(indicator_values.c.indicator_name.in_(indicator_names))

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    if not rows:
        return pd.DataFrame()

    df_long = pd.DataFrame(rows, columns=[c.key for c in indicator_values.c])
    df_wide = df_long.pivot(index="timestamp", columns="indicator_name", values="value")
    df_wide.index = pd.to_datetime(df_wide.index)
    df_wide.columns.name = None
    return df_wide


# ---------------------------------------------------------------------------
# Orders repository (Phase 4)
# ---------------------------------------------------------------------------

def insert_order(
    alpaca_order_id: str,
    symbol: str,
    side: str,
    qty: int,
    stop_loss_price: float,
    take_profit_price: float | None = None,
    signal_id: int | None = None,
) -> int:
    """Insert a new pending order. Returns the new row id."""
    now = _utcnow()
    with get_engine().begin() as conn:
        result = conn.execute(
            orders.insert().values(
                alpaca_order_id=alpaca_order_id,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="market",
                time_in_force="day",
                status="pending",
                submitted_at=now,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                created_at=now,
            )
        )
        return result.lastrowid


def update_order_filled(
    alpaca_order_id: str,
    filled_avg_price: float,
    filled_at: datetime,
) -> None:
    """Mark an order as filled."""
    with get_engine().begin() as conn:
        conn.execute(
            orders.update()
            .where(orders.c.alpaca_order_id == alpaca_order_id)
            .values(status="filled", filled_avg_price=filled_avg_price, filled_at=_to_utc_naive(filled_at))
        )


def update_order_status(alpaca_order_id: str, status: str) -> None:
    """Update order status (e.g. 'cancelled', 'rejected')."""
    with get_engine().begin() as conn:
        conn.execute(
            orders.update()
            .where(orders.c.alpaca_order_id == alpaca_order_id)
            .values(status=status)
        )


def fetch_order_by_alpaca_id(alpaca_order_id: str) -> dict | None:
    """Return order row as dict, or None if not found."""
    with get_engine().connect() as conn:
        row = conn.execute(
            orders.select().where(orders.c.alpaca_order_id == alpaca_order_id)
        ).fetchone()
    return dict(row._mapping) if row else None


def fetch_pending_orders() -> list[dict]:
    """Return all orders with status='pending'."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            orders.select().where(orders.c.status == "pending")
        ).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Positions repository (Phase 4)
# ---------------------------------------------------------------------------

def insert_position(
    symbol: str,
    qty: int,
    entry_price: float,
    stop_loss: float,
    opened_at: datetime,
    strategy_name: str,
    take_profit: float | None = None,
) -> None:
    """Insert an open position. Raises IntegrityError on duplicate symbol."""
    now = _utcnow()
    with get_engine().begin() as conn:
        conn.execute(
            positions.insert().values(
                symbol=symbol,
                qty=qty,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_at=_to_utc_naive(opened_at),
                strategy_name=strategy_name,
                updated_at=now,
            )
        )


def delete_position(symbol: str) -> None:
    """Remove a closed position. No-op if not found."""
    with get_engine().begin() as conn:
        conn.execute(positions.delete().where(positions.c.symbol == symbol))


def fetch_open_positions() -> list[dict]:
    """Return all open positions as list of dicts."""
    with get_engine().connect() as conn:
        rows = conn.execute(positions.select()).fetchall()
    return [dict(r._mapping) for r in rows]


def update_position_stop(symbol: str, stop_loss: float) -> None:
    """Update stop_loss for a position (trailing stop support)."""
    now = _utcnow()
    with get_engine().begin() as conn:
        conn.execute(
            positions.update()
            .where(positions.c.symbol == symbol)
            .values(stop_loss=stop_loss, updated_at=now)
        )


# ---------------------------------------------------------------------------
# Trades repository (Phase 4)
# ---------------------------------------------------------------------------

def insert_trade(
    symbol: str,
    strategy_name: str,
    qty: int,
    entry_price: float,
    exit_price: float,
    opened_at: datetime,
    closed_at: datetime,
    exit_reason: str,
) -> int:
    """Record a completed trade. Computes pnl and pnl_pct internally. Returns row id."""
    opened_at = _to_utc_naive(opened_at)
    closed_at = _to_utc_naive(closed_at)
    pnl = (exit_price - entry_price) * qty
    pnl_pct = pnl / (entry_price * qty) if entry_price * qty != 0 else 0.0
    holding_days = (closed_at - opened_at).days
    now = _utcnow()
    with get_engine().begin() as conn:
        result = conn.execute(
            trades.insert().values(
                symbol=symbol,
                strategy_name=strategy_name,
                qty=qty,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                opened_at=opened_at,
                closed_at=closed_at,
                exit_reason=exit_reason,
                holding_days=holding_days,
                created_at=now,
            )
        )
        return result.lastrowid


def fetch_trades(
    symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """Return trades filtered by optional symbol and date range."""
    query = trades.select()
    if symbol:
        query = query.where(trades.c.symbol == symbol)
    if start:
        query = query.where(trades.c.closed_at >= start)
    if end:
        query = query.where(trades.c.closed_at <= end)
    with get_engine().connect() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Equity snapshots repository (Phase 4)
# ---------------------------------------------------------------------------

def insert_equity_snapshot(
    equity: float,
    cash: float,
    position_value: float,
    daily_pnl: float,
    peak_equity: float,
    weekly_pnl: float | None = None,
) -> None:
    """Insert an equity snapshot. Drawdown computed internally."""
    drawdown_pct = max(0.0, (peak_equity - equity) / peak_equity) if peak_equity > 0 else 0.0
    now = _utcnow()
    with get_engine().begin() as conn:
        conn.execute(
            equity_snapshots.insert().values(
                timestamp=now,
                equity=equity,
                cash=cash,
                position_value=position_value,
                daily_pnl=daily_pnl,
                weekly_pnl=weekly_pnl,
                drawdown_pct=drawdown_pct,
                peak_equity=peak_equity,
            )
        )


def fetch_equity_snapshots(
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return equity snapshots ordered by timestamp descending."""
    query = equity_snapshots.select().order_by(equity_snapshots.c.timestamp.desc())
    if start:
        query = query.where(equity_snapshots.c.timestamp >= start)
    if end:
        query = query.where(equity_snapshots.c.timestamp <= end)
    if limit:
        query = query.limit(limit)
    with get_engine().connect() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Signals helpers (Phase 4 additions)
# ---------------------------------------------------------------------------

def mark_signal_risk_decision(signal_db_id: int, approved: bool, rejection_reason: str | None = None) -> None:
    """Update risk_approved and risk_rejection_reason in the signals table."""
    with get_engine().begin() as conn:
        conn.execute(
            signals.update()
            .where(signals.c.id == signal_db_id)
            .values(risk_approved=approved, risk_rejection_reason=rejection_reason)
        )


def fetch_signal_by_id(signal_id: int) -> dict | None:
    """Return a signal row by primary key, or None if not found."""
    with get_engine().connect() as conn:
        row = conn.execute(
            signals.select().where(signals.c.id == signal_id)
        ).fetchone()
    return dict(row._mapping) if row else None


def fetch_position_by_symbol(symbol: str) -> dict | None:
    """Return the open position for a symbol, or None if not found."""
    with get_engine().connect() as conn:
        row = conn.execute(
            positions.select().where(positions.c.symbol == symbol)
        ).fetchone()
    return dict(row._mapping) if row else None


def fetch_peak_equity() -> float:
    """Return the MAX equity ever recorded in equity_snapshots, or 0.0 if none."""
    with get_engine().connect() as conn:
        row = conn.execute(
            equity_snapshots.select().with_only_columns(func.max(equity_snapshots.c.equity))
        ).scalar()
    return float(row) if row is not None else 0.0


def fetch_pending_signals() -> list[dict]:
    """Return signals where risk_approved IS NULL (not yet evaluated by order engine)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            signals.select().where(signals.c.risk_approved.is_(None))
        ).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------

def get_db_status() -> dict:
    """Return a summary of database contents for the status page.

    Returns dict with keys:
      - bars: list of dicts with symbol, timeframe, count, first, last
      - circuit_breaker: dict of circuit breaker state
    """
    engine = get_engine()

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT symbol, timeframe, COUNT(*) as bars, "
            "MIN(timestamp) as first_ts, MAX(timestamp) as last_ts "
            "FROM price_data GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
        )).fetchall()

        bars = [
            {
                "symbol": r[0],
                "timeframe": r[1],
                "count": r[2],
                "first": str(r[3])[:10] if r[3] else None,
                "last": str(r[4])[:10] if r[4] else None,
            }
            for r in rows
        ]

        cb_row = conn.execute(
            text("SELECT * FROM circuit_breaker_state WHERE id = 1")
        ).fetchone()
        cb = dict(cb_row._mapping) if cb_row else {}

    return {"bars": bars, "circuit_breaker": cb}


# ---------------------------------------------------------------------------
# Dashboard helpers (read-only)
# ---------------------------------------------------------------------------

def fetch_recent_signals(
    limit: int = 100,
    strategy: str | None = None,
    symbol: str | None = None,
) -> list[dict]:
    """Return most recent signals ordered by created_at desc, with optional filters."""
    query = signals.select().order_by(signals.c.created_at.desc())
    if strategy:
        query = query.where(signals.c.strategy == strategy)
    if symbol:
        query = query.where(signals.c.symbol == symbol)
    query = query.limit(limit)
    with get_engine().connect() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r._mapping) for r in rows]


def fetch_orders(
    status: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return orders ordered by submitted_at desc, with optional filters."""
    query = orders.select().order_by(orders.c.submitted_at.desc())
    if status:
        query = query.where(orders.c.status == status)
    if symbol:
        query = query.where(orders.c.symbol == symbol)
    query = query.limit(limit)
    with get_engine().connect() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r._mapping) for r in rows]


def fetch_circuit_breaker_state() -> dict:
    """Return the singleton circuit breaker row (id=1) as a dict, or empty dict if not found."""
    with get_engine().connect() as conn:
        row = conn.execute(
            circuit_breaker_state.select().where(circuit_breaker_state.c.id == 1)
        ).fetchone()
    return dict(row._mapping) if row else {}
