# Trader Bot — Living Plan

> **Last updated**: 2026-03-11
> Updated after every development session. See `docs/progress.md` for the full history.

---

## Guiding Rules
- Claude Code **orchestrates only** — all implementation delegated to subagents
- Every phase ends with a **verification checklist** — `pytest` must pass before moving on
- All changes logged in `docs/progress.md`

---

## Phase 1 — Foundation ✅ COMPLETE

**Goal**: Data layer, indicator library, SQLite schema.

### Deliverables
| File | Status |
|---|---|
| `config/settings.py` | ✅ Done |
| `config/strategies.yaml` | ✅ Done |
| `data/storage.py` | ✅ Done |
| `data/ingestion.py` | ✅ Done |
| `data/indicators.py` | ✅ Done |
| `main.py` | ✅ Done |
| `.env.example` | ✅ Done |

### What was built
- SQLAlchemy Core schema: `price_data`, `indicator_values`, `signals`, `circuit_breaker_state`
- Alpaca (IEX feed) primary + yfinance fallback data ingestion
- Full indicator set: RSI(14), BB(20,2), EMA(9,21), MACD(12,26,9), ADX(14), ATR(14)
- CLI: `python main.py pipeline` ingests and calculates indicators for AAPL, VTV, VTI
- All timestamps stored UTC-naive for consistent SQLite deduplication
- Bulk INSERT OR IGNORE (idempotent pipeline)

### Key decisions
- `ta` library used instead of `pandas-ta` (repo gone from PyPI)
- SQLAlchemy Core (not ORM) for performance on bulk inserts
- EAV format for `indicator_values` (no migrations when adding indicators)
- Each `add_*` indicator function returns a copy (no caller mutation)

---

## Phase 1b — Foundation Verification ✅ COMPLETE

**Goal**: Full test coverage for the data layer before building strategies on top of it.

### Deliverables
| File | Description |
|---|---|
| `tests/test_data.py` | Unit + integration tests for storage, ingestion, indicators |

### Test cases required
**storage.py**
- [ ] `upsert_bars` inserts correct row count on first run
- [ ] `upsert_bars` inserts 0 rows on second run (idempotency)
- [ ] `fetch_bars` returns correct shape and index type
- [ ] `fetch_bars` respects `limit`, `start`, `end` filters
- [ ] `upsert_indicators` stores all non-NaN values
- [ ] `upsert_indicators` is idempotent
- [ ] `fetch_indicators` returns wide-format DataFrame with correct columns
- [ ] `fetch_indicators(limit=N)` returns exactly N timestamps
- [ ] Circuit breaker singleton row created on `init_db()`
- [ ] Re-running `init_db()` does not duplicate the singleton

**ingestion.py**
- [ ] `YFinanceClient.fetch_bars` returns DataFrame with lowercase columns
- [ ] `YFinanceClient.fetch_bars` returns UTC-naive index
- [ ] `_resample_ohlcv` correctly aggregates 1h → 4h OHLCV
- [ ] `ingest_symbol` falls back to yfinance when Alpaca keys missing
- [ ] `ingest_symbol` calls `upsert_bars` and returns fetched DataFrame

**indicators.py**
- [ ] Each `add_*` function returns a copy (does not mutate caller's DataFrame)
- [ ] `add_rsi` produces values in [0, 100] range
- [ ] `add_bollinger_bands` upper > mid > lower always
- [ ] `add_macd` MACD line = fast EMA - slow EMA (spot check)
- [ ] `add_adx` warmup NaNs for first 28 rows (2x period)
- [ ] `add_all_indicators` produces exactly 14 columns on a 250-row DataFrame
- [ ] `calculate_and_store` round-trips: stores to DB and retrieves same values

### Verification gate
```
pytest tests/test_data.py -v
```
**Must pass 100% before starting Phase 2.**

---

## Phase 2 — Strategies + Backtesting ✅ COMPLETE

**Goal**: Implement the three trading strategies and validate them via backtesting.

### Deliverables
| File | Description |
|---|---|
| `strategies/base.py` | Abstract `Strategy` base class with `generate_signal()` interface |
| `strategies/mean_reversion.py` | RSI + Bollinger Bands strategy |
| `strategies/momentum.py` | EMA crossover + MACD + ADX strategy |
| `strategies/swing.py` | Multi-timeframe swing strategy |
| `strategies/meta_strategy.py` | Weighted-vote signal aggregator |
| `backtest/backtrader_runner.py` | Backtrader event-driven runner |
| `backtest/results.py` | Result parsing: Sharpe, win rate, max drawdown |
| `tests/test_strategies.py` | Strategy signal logic tests (mock data) |

### Strategy verification checklist
- [ ] `generate_signal()` returns `Signal` or `None` — never raises
- [ ] Mean reversion: BUY when price < lower BB AND RSI < 30
- [ ] Mean reversion: SELL when price > mid BB OR RSI > 50
- [ ] Momentum: BUY when EMA(9) crosses above EMA(21) AND ADX > 25
- [ ] Momentum: stop loss triggers at 2x ATR trailing
- [ ] Meta-strategy: requires `min_signals_required` (2) strategies to agree
- [ ] Backtest on AAPL 1-year: Sharpe > 0 (better than random)
- [ ] Backtest results reproducible with same data (deterministic)
- [ ] All strategy tests pass: `pytest tests/test_strategies.py -v`

### Verification gate
```
pytest tests/test_strategies.py -v
python main.py backtest --symbol AAPL --strategy mean_reversion
```
Results saved to `backtest/results/` and reviewed before Phase 3.

---

## Phase 3 — Risk Module ✅ COMPLETE

**Goal**: Implement all risk management rules as enforced code (not just config).

### Deliverables
| File | Description |
|---|---|
| `risk/position_sizing.py` | Half-Kelly criterion, 5% cap, VIX brake |
| `risk/circuit_breakers.py` | Daily/weekly/max drawdown halt logic |
| `risk/portfolio.py` | Max 5 positions, max 3 per sector |
| `risk/withdrawal.py` | 50/30/20 withdrawal policy |
| `tests/test_risk.py` | Risk rule enforcement tests |

### Risk verification checklist
- [ ] Position size never exceeds 5% of portfolio
- [ ] Position size never risks more than 1% of account
- [ ] VIX > 30 halves position sizes
- [ ] 3% daily loss triggers `daily_halt = True` in circuit_breaker_state
- [ ] 5% weekly loss triggers `weekly_halt = True`
- [ ] 15% drawdown triggers `full_halt = True` and sends Telegram alert
- [ ] Halts persist across process restarts (read from DB, not memory)
- [ ] Max 5 concurrent positions enforced (6th signal rejected)
- [ ] Max 3 positions in same sector enforced
- [ ] All risk tests pass: `pytest tests/test_risk.py -v`

---

## Phase 4 — Execution + Paper Trading ✅ COMPLETE

**Goal**: Wire signals through risk → orders → Alpaca paper trading API.

**Prerequisite**: Alpaca account and paper trading keys.

### Deliverables
| File | Description |
|---|---|
| `execution/alpaca_client.py` | ✅ Done |
| `execution/order_engine.py` | ✅ Done |
| `execution/paper_trading.py` | ✅ Done |
| `monitoring/notifications.py` | ✅ Done |
| `monitoring/performance.py` | ✅ Done |
| `tests/test_execution.py` | ✅ Done — 29 tests passing |

### Paper trading verification checklist
- [x] All unit tests pass: `pytest tests/test_execution.py` — 29/29 ✅
- [x] `pytest tests/` — 199/199 ✅
- [ ] Orders submitted to Alpaca paper API successfully (requires live keys)
- [ ] Filled orders stored in `orders` and `trades` tables
- [ ] Telegram notification sent on every fill
- [ ] Telegram notification sent on every circuit breaker trigger
- [ ] Performance metrics updated after each fill
- [ ] 3-month paper trading run with daily monitoring
- [ ] Win rate ≥ 45% across all strategies
- [ ] Max drawdown during paper run < 10%
- [ ] Sharpe ratio > 0.5

---

## Phase 5 — Go-Live Gate 🔲 NEXT

**ALL criteria must be met before deploying real money.**

### Go-Live Checklist
- [ ] `pytest` passes 100% (all test files)
- [ ] 90+ day paper trading run completed
- [ ] Paper trading win rate ≥ 45%
- [ ] Paper trading Sharpe > 0.5
- [ ] Paper trading max drawdown < 10%
- [ ] Circuit breakers verified to trigger and halt correctly
- [ ] Telegram notifications working reliably
- [ ] Alpaca live account funded with $500
- [ ] Tax tracking modules operational
- [ ] Manual review of last 20 paper trades

### Live Deployment Ramp
1. $100 → 2 weeks → assess
2. $250 → 4 weeks → assess
3. $500 → ongoing

---

## Phase 6 — Monitoring Dashboard ✅ COMPLETE

**Goal**: Streamlit dashboard with monitoring, bot control, AND operations.

### Deliverables
| File | Status |
|---|---|
| `monitoring/dashboard.py` | ✅ Done — thin shim (delegates to package) |
| `monitoring/dashboard/app.py` | ✅ Done — main entry point, 7-page dispatch |
| `monitoring/dashboard/theme.py` | ✅ Done — NYT-style CSS + Plotly template |
| `monitoring/dashboard/task_runner.py` | ✅ Done — background thread task execution |
| `monitoring/dashboard/cached_data.py` | ✅ Done — extracted cache helpers |
| `monitoring/dashboard/pages/` | ✅ Done — 7 page modules |
| `monitoring/session_manager.py` | ✅ Done — thread-based session lifecycle |
| `.streamlit/config.toml` | ✅ Done — NYT theme config |
| `data/storage.py` (additions) | ✅ Done — `get_db_status()`, `fetch_recent_signals`, `fetch_orders`, `fetch_circuit_breaker_state` |
| `main.py` (updates) | ✅ Done — `cmd_status()` uses `get_db_status()`, `python main.py dashboard` |

### Dashboard pages
1. **Overview** — equity curve (Plotly, themed), KPI cards, circuit breaker banner, open positions
2. **Operations** — Pipeline/Ingest/Indicators buttons, Signal generation, Backtest form + results, DB status
3. **Bot Control** — start/stop session, symbol select, poll interval, CB reset
4. **Trades & Orders** — filterable order/trade tables
5. **Signals** — pending count, signal history with strategy/symbol filters
6. **Performance** — Sharpe, drawdown, win rate, profit factor, per-strategy breakdown
7. **Configuration** — editable strategy YAML + read-only risk limits

### Architecture
- Package-based structure (1 module per page, shared cached_data + theme)
- Background task runner (thread-based, follows session_manager pattern)
- NYT-style theme: Playfair Display headers, Source Sans 3 body, dark sidebar, muted editorial Plotly colorway
- Operations page wraps CLI commands — no duplicate terminals needed

### Future
- Cloudflare Tunnel for remote access

---

## Dependency Status

| Package | Version | Status | Notes |
|---|---|---|---|
| Python | 3.11.9 | ✅ | via pyenv |
| alpaca-py | 0.43.2 | ✅ | IEX feed (free tier) |
| yfinance | 1.2.0 | ✅ | Active fallback |
| pandas | 2.3.3 | ✅ | |
| numpy | 2.4.2 | ✅ | |
| ta | 0.11.0 | ✅ | Replaces pandas-ta |
| backtrader | 1.9.78 | ✅ | |
| sqlalchemy | 2.0.48 | ✅ | Core only |
| python-telegram-bot | 22.6 | ✅ | |
| loguru | 0.7.3 | ✅ | |
| vectorbt | — | ⏸ | Complex deps; add in Phase 2 if needed |

---

## Blockers / Open Items

| Item | Owner | Priority |
|---|---|---|
| Alpaca paper trading account + API keys | User | High — needed to run paper trading |
| Telegram bot setup (token + chat ID) | User | Medium — needed for live notifications |
| 3-month paper trading run | User | High — required before go-live gate |
