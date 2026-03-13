# Trader Bot — Progress Log

Chronological record of all completed work. Updated after every session.

---

## 2026-03-08 — Session 1: Environment + Phase 1 Foundation

### Environment Setup
- Identified Python 3.14 incompatibility with `numba` (pandas-ta dep)
- Created venv using Python 3.11.9 (pyenv)
- Replaced `pandas-ta` with `ta>=0.11.0` (original repo gone from PyPI)
- Commented out `vectorbt` (complex build deps; deferred)
- All other packages installed successfully

**Packages confirmed working:**
alpaca-py 0.43.2, yfinance 1.2.0, pandas 2.3.3, numpy 2.4.2, ta 0.11.0,
backtrader 1.9.78, sqlalchemy 2.0.48, python-telegram-bot 22.6, loguru 0.7.3

### Phase 1: Foundation — COMPLETE

**Files created:**

| File | Description |
|---|---|
| `config/settings.py` | Env var loading, hard-coded risk limits (1%/3%/5%/15%), path setup |
| `config/strategies.yaml` | All strategy params: BB(20,2), RSI(14), EMA(9,21), ADX(14), ATR(14), weights |
| `data/storage.py` | SQLAlchemy Core schema + bulk insert/fetch for 4 tables |
| `data/ingestion.py` | Alpaca IEX (primary) + yfinance (fallback), timeframe normalization |
| `data/indicators.py` | RSI, BB, EMA, MACD, ADX, ATR — all return copies, no mutation |
| `main.py` | CLI: `ingest`, `indicators`, `pipeline`, `status` commands |
| `.env.example` | Credential template |
| `docs/PLAN.md` | Living plan document with per-phase verification checklists |
| `docs/progress.md` | This file |

**Database tables created:**
- `price_data` — OHLCV bars (symbol, timestamp UTC-naive, timeframe, OHLCV, vwap, source)
- `indicator_values` — EAV format (symbol, timestamp, timeframe, indicator_name, value)
- `signals` — Strategy output with risk approval tracking
- `circuit_breaker_state` — Singleton row, persists halt flags across restarts

**Verified working:**
```
python main.py pipeline
```
- AAPL, VTV, VTI: 250 daily bars + ~1729 hourly bars each
- 14 indicators calculated and stored per symbol per timeframe
- Second run: 0 rows inserted (idempotent deduplication confirmed)

**Bugs fixed during code review:**
1. Timezone-aware vs UTC-naive mismatch → all timestamps stripped to UTC-naive before storage
2. Row-by-row INSERTs → bulk INSERT OR IGNORE (one round-trip)
3. Deprecated pandas offset aliases `"1D"`, `"1W"` → `"1d"`, `"7D"`
4. In-place DataFrame mutation in `add_*` functions → each returns `df.copy()`
5. `fetch_indicators(limit=N)` fetched all rows then trimmed → SQL subquery limit
6. Positional `SELECT *` column access in `main.py` → named `._mapping` access

### CLAUDE.md Updates
- Added Agent Orchestration Rules (orchestrator-only, subagents implement)
- Added subagent assignment table
- Added per-phase workflow (Explore → Design → Implement → Simplify → QA → Verify → Log)
- Fixed architecture: `ta` not `pandas-ta`, Telegram not Slack, no vectorbt
- Referenced `docs/PLAN.md` and `docs/progress.md` as live documents
- Updated phases to include verification steps

---

## 2026-03-08 — Session 2: Governance + Phase 1b Verification

### CLAUDE.md + Governance
- Added **Agent Orchestration Rules**: Claude Code orchestrates only; all implementation via subagents
- Added subagent assignment table (explorer, architect, reviewer, simplify skill)
- Added 7-step per-feature workflow: Explore → Design → Implement → Simplify → QA → Verify → Log
- Created `docs/PLAN.md` with per-phase verification checklists and dependency status
- Created `docs/progress.md` (this file)

### Phase 1b: Foundation Verification — COMPLETE

**File created:** `tests/test_data.py`

**77 tests, all passing in 0.63s** (`pytest tests/test_data.py -v`)

Test classes:
- `test_add_indicator_does_not_mutate_input` (parametrized, 6 cases)
- `test_add_indicator_custom_period_column_name` (parametrized, 4 cases)
- `TestToUtcNaive` (4 tests)
- `TestInitDb` (3 tests)
- `TestUpsertBars` (6 tests)
- `TestFetchBars` (7 tests — autouse seed fixture)
- `TestUpsertIndicators` (4 tests)
- `TestFetchIndicators` (4 tests — autouse seed fixture)
- `TestCircuitBreaker` (2 tests)
- `TestAddRsi`, `TestAddBollingerBands`, `TestAddEma`, `TestAddMacd`, `TestAddAdx`, `TestAddAtr`
- `TestAddAllIndicators` (3 tests)
- `TestResampleOhlcv` (8 tests incl. partial trailing group edge case)
- `TestYFinanceClient` (5 tests — shared pre-fetched result fixture)
- `TestIngestSymbol` (4 tests — fully mocked)

**Quality improvements applied during simplify/QA:**
- 6 copy-mutation tests collapsed into 1 parametrized test
- 4 custom-period column name tests collapsed into 1 parametrized test
- `TestFetchBars` autouse `_seed` fixture eliminates 6 repeated `upsert_bars` calls
- `TestFetchIndicators` autouse `_seed` fixture eliminates repeated indicator computation
- `TestYFinanceClient` shared `yf_result` fixture eliminates 4 repeated patch contexts
- `isolated_db` explicitly resets `_engine` to original on teardown (no singleton leak)
- `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)`
- Hardcoded `pd.Timestamp("2025-03-01")` → relative `self._last_ts` comparison
- Magic `60` → `SAMPLE_PERIODS` constant
- `constant_ohlcv_df` extracted as shared fixture (for EMA constant-series test)
- `_count_circuit_breaker_rows()` helper extracted (shared by TestInitDb + TestCircuitBreaker)
- Fixed SAWarning in `storage.py`: Subquery → `scalar_subquery()` in `fetch_indicators`
- Added partial trailing group edge case to `TestResampleOhlcv`

---

## 2026-03-10 — Session 3: Phase 2 Strategies + Backtesting

### Phase 2: Strategies + Backtesting — COMPLETE

**Files created:**

| File | Description |
|---|---|
| `strategies/base.py` | `Direction` enum, `Signal` dataclass, `Strategy` ABC, `load_strategy_config`, `save_signal`, `_extract_timestamp`, `_check_df` helpers |
| `strategies/mean_reversion.py` | BUY: price < lower BB AND RSI < 30; SELL: price > mid BB OR RSI > 50 |
| `strategies/momentum.py` | BUY: EMA(9) crossover EMA(21) AND ADX > 25; SELL: reverse crossover; stop loss = close - 2×ATR |
| `strategies/swing.py` | Daily-only; BUY: bullish bias + pullback to EMA + neutral RSI; Phase 3 multi-TF TODO |
| `strategies/meta_strategy.py` | Weighted-vote aggregator; min 2 strategies + 0.60 combined strength; filters unknown strategies |
| `backtest/backtrader_runner.py` | Backtrader adapter; delegates to domain `generate_signal` per bar; yfinance fallback ingest |
| `backtest/results.py` | Sharpe, max drawdown, win rate metrics; JSON save/load; `datetime.now(UTC)` convention |
| `backtest/results/.gitkeep` | Directory placeholder |
| `tests/test_strategies.py` | 49 tests, all passing |

**Modified files:**
- `data/storage.py` — added `upsert_signal()` with INSERT OR IGNORE + debug logging
- `main.py` — added `signals` and `backtest` CLI commands

**Test results:** `pytest tests/test_strategies.py` → **49 passed** | `pytest tests/test_data.py` → **77 passed** | **126 total, 0 failures**

**Bugs fixed during Simplify + QA passes:**
1. `macd_hist_col` was `"macd_12_26_9"` (MACD line) → fixed to `"macd_hist_12_26_9"` (histogram) — silent wrong-column bug
2. `_entry_price` never reset after trade close → `pnl_pct` for second trade used first trade's entry price
3. Max drawdown division by zero when equity curve reaches 0 → added `safe_peak = np.where(peak > 0, peak, np.inf)`
4. `datetime.utcnow()` in `results.py` → `datetime.now(timezone.utc).replace(tzinfo=None)` (deprecated API, codebase convention)
5. `logger.error` for expected no-data case → `logger.warning` (convention fix)
6. Dead import `Signal as _Signal` in `upsert_signal` → removed
7. Redundant `pd.Timestamp()` wrap in `upsert_signal` → `_to_utc_naive(signal.timestamp)` directly
8. Duplicate `np.array` conversion (Sharpe + drawdown) → single conversion before both blocks; `import math` → `np.sqrt`
9. `self.mode` stored but never used in MetaStrategy → removed
10. `self.min_hold`/`self.max_hold` stored but unused in SwingStrategy → removed, Phase 3 TODO added
11. Unknown strategy names silently corrupting MetaStrategy vote counts → filter + warning
12. `not df.empty and len(df) > 0` redundant double-check → `not df.empty`
13. `build_result` for-loop signal serialization → list comprehension
14. Lazy `build_result` import in backtrader_runner → moved to top-level
15. `pd.to_datetime()` no-op on already-DatetimeIndex → removed
16. `import math` redundant (numpy already imported) → removed
17. Strategy instantiation inside symbol loop in `cmd_signals` (12 YAML reads) → hoisted outside loop (4 reads)
18. Timestamp extraction duplicated across 4 files → extracted `_extract_timestamp()` helper in base.py
19. DF guard boilerplate duplicated across 3 strategies → extracted `_check_df()` helper in base.py
20. 3 `TestBacktestRunner` tests repeated setup → extracted `seeded_db` fixture
21. Missing conflict path test in MetaStrategy → added `test_aggregate_returns_none_when_both_directions_qualify`

---

## 2026-03-10 — Session 4: Phase 3 Risk Module

### Phase 3: Risk Module — COMPLETE

**Files created:**

| File | Description |
|---|---|
| `risk/position_sizing.py` | Fixed fractional + half-Kelly + 5% cap + VIX brake; confidence clamped to [0,1] |
| `risk/circuit_breakers.py` | `load_state`, `save_state`, `check_and_update`, `reset_daily`, `is_halted`; fail-safe on DB error |
| `risk/portfolio.py` | `PortfolioState`, `can_open_position(symbol)`, `add_position` (sector from SECTOR_MAP), `remove_position` |
| `risk/withdrawal.py` | `calculate_withdrawal` (50/30/20), `can_withdraw`; lazy YAML init via `_get_cfg()` |
| `tests/test_risk.py` | 45 tests, all passing |

**Modified files:**
- `data/storage.py` — circuit breaker singleton seeded with `last_reset_date=None` (not today)

**Test results:** `pytest tests/` → **170 passed** | 0 failures (test_data=77, test_strategies=49, test_risk=45)

**Bugs fixed during Simplify + QA passes:**
1. `circuit_breakers.py` — `load_state` swallowed all exceptions returning permissive defaults → now re-raises DB errors so `is_halted()` catch → True (fail-safe)
2. `circuit_breakers.py` — `reset_daily()` cleared `halt_reason` even when weekly/full halt still active → now preserves when other halts remain
3. `circuit_breakers.py` — `logger.error` for expected missing-row case → `logger.warning`; `logger.info` → `logger.warning`
4. `circuit_breakers.py` — 3 parallel `if X_new: logger.warning(...)` blocks → unified loop
5. `portfolio.py` — dual sector-map sources (SECTOR_MAP for incoming, portfolio_state.sector_map for existing) → fixed: `add_position` always derives sector from `SECTOR_MAP`; signature drops `sector` parameter
6. `portfolio.py` — `can_open_position(signal, ...)` → `can_open_position(symbol: str, ...)` removes unnecessary coupling to strategies layer
7. `position_sizing.py` — `signal.confidence > 1.0` could exceed 1% risk cap → clamped to `[0.0, 1.0]`
8. `withdrawal.py` — `_WITHDRAWAL_CFG` loaded at module import (YAML file read on every import) → lazy init via `_get_cfg()`
9. `data/storage.py` — init_db seeded `last_reset_date=today` causing `reset_daily()` to always skip → seeded as `None`

---

## 2026-03-10 — Session 5: Phase 4 Execution + Paper Trading

### Phase 4: Execution + Paper Trading — COMPLETE

**Files created:**

| File | Description |
|---|---|
| `execution/alpaca_client.py` | `AlpacaClientProtocol` (runtime_checkable), `AlpacaClient` (alpaca-py 0.43.2 wrapper), `AccountInfo`/`OrderResult`/`PositionInfo` dataclasses, `make_alpaca_client()` factory |
| `execution/order_engine.py` | `process_signal()` with 4 risk gates (HOLD skip, circuit breaker, portfolio capacity, position sizing/SELL validation); `OrderOutcome` dataclass |
| `execution/paper_trading.py` | `PaperTradingSession` — polling loop, fill sync, portfolio state tracking; `build_session()` factory |
| `monitoring/notifications.py` | `NotifierProtocol`, `TelegramNotifier` (python-telegram-bot 22.6 async), `NullNotifier`, `make_notifier()` factory |
| `monitoring/performance.py` | `PerformanceTracker` — Sharpe, max drawdown, win rate, profit factor; `PerformanceMetrics` dataclass; `format_metrics_report()` |
| `tests/test_execution.py` | 29 tests, all passing |

**Modified files:**
- `data/storage.py` — 4 new tables (`orders`, `positions`, `trades`, `equity_snapshots`); 17 new CRUD functions; `_utcnow()` helper; all `datetime.utcnow()` replaced; `_to_utc_naive()` applied to all incoming datetime params; `fetch_position_by_symbol()` and `fetch_peak_equity()` helpers added
- `risk/circuit_breakers.py` — `load_state()` re-raises DB errors (fail-safe pattern)

**Test results:** `pytest tests/` → **199 passed** | 0 failures

**Bugs fixed during Simplify + QA passes:**
1. `paper_trading.py` — `signal_db_id` never passed to `process_signal` → signals stayed `risk_approved=NULL` forever, reprocessed every poll cycle causing duplicate orders → fixed: pass `signal_db_id=sig_row.get("id")`
2. `paper_trading.py` — `_sync_filled_orders` skipped during circuit breaker halt → orders filled/cancelled at Alpaca while halted never reconciled → fixed: sync fills before halt check (always runs)
3. `paper_trading.py` — `is_halted()` called redundantly after `check_and_update()` (second DB round-trip) → replaced with inline `cb_state` field check
4. `paper_trading.py` — `_set_peak_equity` loaded up to 1000 rows for Python `max()` → replaced with `fetch_peak_equity()` SQL MAX query
5. `paper_trading.py` — `_handle_sell_fill` did full `fetch_open_positions()` table scan for single symbol → replaced with `fetch_position_by_symbol()`
6. `paper_trading.py` — `direction_map` dict for Direction enum → replaced with `Direction[row["signal_type"]]` (enum by-name lookup)
7. `order_engine.py` — SELL gate used `fetch_open_positions()` (full scan, unguarded DB error) → replaced with `fetch_position_by_symbol()` wrapped in try/except
8. `order_engine.py` — Gates 2 and 3 split into two separate `if signal.direction == Direction.BUY:` checks → merged into single BUY block
9. `alpaca_client.py` — unrecognized `time_in_force` silently fell back to GTC → now raises `AlpacaClientError`
10. `notifications.py` — `logger.info` for "TelegramNotifier active" → `logger.debug` (convention)
11. `performance.py` — `sum(pnl_values)` computed twice → extracted `total_pnl` variable
12. `data/storage.py` — `datetime.utcnow()` used in 7 places (deprecated in Python 3.12) → added `_utcnow()` helper, replaced all occurrences
13. `data/storage.py` — datetime params passed to write functions without `_to_utc_naive()` → applied in `update_order_filled`, `insert_position`, `insert_trade`

---

## 2026-03-11 — Session 6: Phase 6 Dashboard + Full Code Review

### Full Codebase Code Review

Three parallel reviewers (bugs, DRY/quality, conventions) across all 18 source files. Key fixes:
1. `data/storage.py` — `fetch_bars(limit=N)` returned oldest N rows (ASC + LIMIT) → fixed with DESC subquery to return most recent N (critical: indicators were computed on stale data)
2. `data/indicators.py` — `add_momentum_indicators` duplicated `EMAIndicator`, `MACD`, `ADXIndicator`, `AverageTrueRange` inline → now chains existing `add_ema`, `add_macd`, `add_adx`, `add_atr` functions
3. `strategies/base.py` — `save_signal` swallowed DB errors (violates fail-safe) → now propagates
4. `execution/paper_trading.py` — halt alert sent every poll cycle (Telegram spam) → added `_halt_notified` flag
5. `execution/paper_trading.py` — `strategy_name` hardcoded as `"paper_trading"` on position insert → now looks up from originating signal via `fetch_signal_by_id`
6. `execution/alpaca_client.py` — dead `import GetOrderByIdRequest` removed
7. `risk/circuit_breakers.py` — inlined `datetime.now(timezone.utc).replace(tzinfo=None)` → uses `_utcnow()` from storage
8. `strategies/swing.py` — hardcoded parameters → now loaded from `strategies.yaml`
9. `data/storage.py` — `signals.c.risk_approved == None` → `.is_(None)` (idiomatic SQLAlchemy)
10. `data/storage.py` — inline `from sqlalchemy import select/func` → module-level imports

### Phase 6: Monitoring Dashboard — COMPLETE

**Files created:**

| File | Description |
|---|---|
| `monitoring/dashboard.py` | 6-page Streamlit app: Overview, Bot Control, Trades & Orders, Signals, Performance, Configuration |
| `monitoring/session_manager.py` | Thread-based session lifecycle: `start_session`, `stop_session`, `get_status`, `is_running` |

**Modified files:**
- `data/storage.py` — 3 new dashboard helpers: `fetch_recent_signals`, `fetch_orders`, `fetch_circuit_breaker_state`; also `fetch_signal_by_id` added during code review fixes
- `main.py` — added `python main.py dashboard` command
- `config/strategies.yaml` — added swing strategy parameters (ema_period, adx_period, rsi_period, adx_trend_min, rsi_overbought)

**Test results:** `pytest tests/` → **199 passed** | 0 failures

**Dashboard features:**
- Real-time equity curve with Plotly
- Start/stop paper trading session from the UI (thread-based)
- Configure symbols, poll interval, strategy parameters
- Manual circuit breaker reset
- Filterable order, trade, and signal tables
- Performance metrics with per-strategy breakdown
- Atomic YAML config editing with save button
- 30-second data cache (TTL) to avoid DB thrashing

---

## 2026-03-11 — Session 7: Dashboard Redesign — Operations Integration + NYT Theme

### Dashboard Package Refactor

Refactored the monolithic 544-line `monitoring/dashboard.py` into a modular package.

**New package structure:**

| File | Description |
|---|---|
| `monitoring/dashboard/__init__.py` | Empty package marker |
| `monitoring/dashboard/app.py` | Main entry: page_config, CSS inject, sidebar nav, 7-page dispatch |
| `monitoring/dashboard/theme.py` | NYT-style CSS (Playfair Display headers, Source Sans 3 body) + Plotly template |
| `monitoring/dashboard/task_runner.py` | Background thread task execution (follows session_manager pattern) |
| `monitoring/dashboard/cached_data.py` | Extracted @st.cache_data helpers + YAML helpers |
| `monitoring/dashboard/pages/__init__.py` | Empty package marker |
| `monitoring/dashboard/pages/overview.py` | Equity curve, KPIs, open positions |
| `monitoring/dashboard/pages/bot_control.py` | Start/stop session, config |
| `monitoring/dashboard/pages/trades_orders.py` | Filterable order/trade tables |
| `monitoring/dashboard/pages/signals.py` | Pending + recent signals |
| `monitoring/dashboard/pages/performance.py` | Sharpe, drawdown, win rate, per-strategy |
| `monitoring/dashboard/pages/configuration.py` | Editable strategy YAML + risk limits |
| `monitoring/dashboard/pages/operations.py` | NEW — Pipeline, Signals, Backtest, DB Status |
| `.streamlit/config.toml` | Streamlit theme: serif font, NYT colors |

**Modified files:**
- `monitoring/dashboard.py` — replaced 544-line monolith with thin shim (imports + calls `main()`)
- `data/storage.py` — added `get_db_status()` function (bar counts + circuit breaker state)
- `main.py` — `cmd_status()` now calls `get_db_status()` instead of raw SQL

### Operations Page (NEW)

New 7th dashboard page with 4 sections:
1. **Data Pipeline** — Run Full Pipeline / Ingest Only / Indicators Only with symbol multiselect
2. **Signal Generation** — Generate signals for selected symbols
3. **Backtest** — Symbol, strategy, date range form; results show 5 KPI cards + equity curve + trade list
4. **Database Status** — Bar count table per symbol/timeframe + circuit breaker state cards

All operations run via background threads (task_runner.py), preventing UI freezes. Auto-refresh while tasks are running.

### NYT-Style Theme

- **Typography**: Playfair Display (serif headers), Source Sans 3 (sans-serif body/data) via Google Fonts
- **Color palette**: #121212 near-black, #FFFFFF white, #F7F7F5 warm gray, #567B95 steel blue accent
- **Styling**: Double-border h1, single-border h2, uppercase metric labels, rectangular buttons, dark sidebar
- **Plotly template**: Matching font family, muted editorial colorway, clean grid
- **Base theme**: `.streamlit/config.toml` with serif font, black primary, warm gray secondary

### Test Results

`pytest tests/` → **199 passed** | 0 failures (no regressions)

---

## Next: Phase 5 — Go-Live Gate

**Prerequisite blockers**: Alpaca paper trading account + Telegram bot setup (user-owned).
3-month paper trading run required before go-live.
