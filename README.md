# Okane

**Modular algorithmic trading system built in Python.**

A $500 proof-of-concept designed to validate strategies in paper trading before scaling to $25K+ for supplemental income generation. Uses Alpaca Markets (commission-free) for both paper and live trading, with a Streamlit dashboard for monitoring and control.

---

## Architecture

```
                           +------------------+
                           |   Streamlit UI   |
                           |   (7 pages)      |
                           +--------+---------+
                                    |
                  +-----------------+------------------+
                  |                 |                   |
          +-------v------+  +------v-------+  +--------v--------+
          |  Monitoring  |  |  Execution   |  |    Backtest      |
          |  - Dashboard |  |  - Orders    |  |  - Backtrader    |
          |  - Telegram  |  |  - Alpaca    |  |  - Results       |
          |  - Metrics   |  |  - Paper     |  |                  |
          +--------------+  +------+-------+  +---------+--------+
                                   |                    |
                           +-------v--------------------v--------+
                           |          Risk Management            |
                           |  - Position sizing (half-Kelly)     |
                           |  - Circuit breakers (3%/5%/15%)     |
                           |  - Portfolio exposure limits        |
                           +----------------+--------------------+
                                            |
                           +----------------v--------------------+
                           |         Strategy Layer              |
                           |  - Mean Reversion (RSI + BB)        |
                           |  - Momentum (EMA + MACD + ADX)      |
                           |  - Swing (multi-timeframe)          |
                           |  - Meta-strategy (weighted voting)  |
                           +----------------+--------------------+
                                            |
                           +----------------v--------------------+
                           |           Data Layer                |
                           |  - Alpaca / yfinance ingestion      |
                           |  - Technical indicators (ta)        |
                           |  - SQLite storage (SQLAlchemy)      |
                           +-------------------------------------+
```

---

## Key Features

**Trading Strategies**
- Three independent strategies: Mean Reversion, Momentum, and Swing Trading
- Meta-strategy that aggregates signals via weighted voting
- All strategy parameters configurable via YAML (no hardcoded values)

**Risk Management**
- 1% max risk per trade with half-Kelly position sizing
- Multi-level circuit breakers: daily, weekly, and max drawdown limits
- VIX-aware position scaling and sector exposure caps

**Execution**
- Paper trading loop with Alpaca Markets API
- Event-driven order engine with risk gate validation
- Full order lifecycle tracking in SQLite

**Backtesting**
- Event-driven simulation via Backtrader
- Reproducible results with configurable date ranges
- Performance metrics: Sharpe ratio, drawdown, win rate

**Dashboard**
- 7-page Streamlit application with NYT-inspired theme
- Pages: Overview, Operations, Bot Control, Trades & Orders, Signals, Performance, Configuration
- Run pipeline, generate signals, and launch backtests directly from the UI
- Equity curves, KPI cards, and trade tables via Plotly

**Monitoring**
- Telegram notifications for signals, fills, and circuit breaker events
- Performance tracking with Sharpe ratio, max drawdown, and win rate

**Tax Optimization**
- Wash sale tracking with 30-day exclusion windows
- Specific lot identification for tax-efficient exits

---

## Getting Started

### Prerequisites

- Python 3.11+
- Alpaca Markets account (free paper trading)
- Telegram bot token (optional, for notifications)

### Setup

```bash
# Clone the repository
git clone https://github.com/your-username/okane.git
cd okane

# Create and activate virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your API keys

# Ingest market data and calculate indicators
python main.py pipeline

# Launch the dashboard
python main.py dashboard
```

### Environment Variables

| Variable | Description |
|---|---|
| `ALPACA_API_KEY` | Alpaca Markets API key |
| `ALPACA_SECRET_KEY` | Alpaca Markets secret key |
| `ALPACA_PAPER` | Set to `true` for paper trading |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID (optional) |
| `DB_PATH` | SQLite database path (default: `./data/trader_bot.db`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |

---

## CLI Reference

```bash
python main.py pipeline                                          # Fetch data + calculate indicators
python main.py ingest                                            # Fetch OHLCV data only
python main.py indicators                                        # Calculate indicators only
python main.py signals                                           # Run all strategies, print signals
python main.py backtest AAPL mean_reversion 2024-01-01 2024-12-31  # Backtest a strategy
python main.py status                                            # Show database summary
python main.py dashboard                                         # Launch Streamlit dashboard
```

---

## Trading Strategies

### Mean Reversion (Primary)

| Parameter | Value |
|---|---|
| Entry | Price < lower Bollinger Band (20, 2 sigma) AND RSI(14) < 30 |
| Exit | Price returns to middle BB OR RSI > 50 |
| Stop Loss | 2% below entry |
| Target Win Rate | 55-65% |

### Momentum (Secondary)

| Parameter | Value |
|---|---|
| Entry | EMA(9) crosses above EMA(21) AND ADX(14) > 25 |
| Exit | Reverse crossover OR trailing stop (2x ATR) |
| Stop Loss | Below recent swing low |
| Target Win Rate | 40-50% (larger wins compensate) |

### Swing Trading (Tertiary)

| Parameter | Value |
|---|---|
| Timeframes | Daily for direction, 4H/1H for entry |
| Hold Period | Days to weeks |
| Advantage | Fewer trades, lower costs, avoids PDT rule for accounts under $25K |

### Meta-Strategy

Aggregates signals from all three strategies using weighted voting. Each strategy contributes a BUY, SELL, or HOLD signal with a confidence score. The meta-strategy produces a final decision based on the weighted consensus.

---

## Risk Management

All risk parameters below are **hard-coded and cannot be overridden** at runtime. These are non-negotiable safety limits.

| Rule | Limit | Action |
|---|---|---|
| Max risk per trade | 1% of account | Order rejected if exceeded |
| Position sizing | Half-Kelly criterion, capped at 5% | Automatic sizing |
| Daily loss limit | 3% | Trading halted for the day |
| Weekly loss limit | 5% | Trading halted for the week |
| Max drawdown | 15% | ALL trading halted, manual review required |
| Max concurrent positions | 5 | New orders rejected |
| Max sector exposure | 3 positions per sector | New orders in sector rejected |
| VIX brake | VIX > 30 | Position sizes halved |

---

## Project Structure

```
okane/
├── config/
│   ├── settings.py              # Global config, env vars, risk limits
│   └── strategies.yaml          # Strategy parameters (all tunable values)
├── data/
│   ├── ingestion.py             # Alpaca/yfinance data fetching
│   ├── indicators.py            # Technical indicator calculations
│   └── storage.py               # SQLite interface (SQLAlchemy Core)
├── strategies/
│   ├── base.py                  # Abstract strategy class
│   ├── mean_reversion.py        # RSI + Bollinger Bands
│   ├── momentum.py              # EMA crossover + MACD + ADX
│   ├── swing.py                 # Multi-timeframe swing trading
│   └── meta_strategy.py         # Signal aggregation / weighted voting
├── risk/
│   ├── position_sizing.py       # Half-Kelly, fixed fractional
│   ├── circuit_breakers.py      # Daily/weekly/max drawdown limits
│   └── portfolio.py             # Exposure limits, correlation guard
├── execution/
│   ├── alpaca_client.py         # Alpaca API wrapper
│   ├── order_engine.py          # Signal to order translation + risk gates
│   └── paper_trading.py         # Paper trading polling loop
├── backtest/
│   ├── backtrader_runner.py     # Event-driven backtesting
│   └── results.py               # Backtest result analysis
├── monitoring/
│   ├── dashboard/               # 7-page Streamlit application
│   ├── notifications.py         # Telegram alerts
│   └── performance.py           # Sharpe, drawdown, win rate tracking
├── tax/
│   ├── wash_sale_tracker.py     # 30-day exclusion window tracking
│   ├── lot_tracker.py           # Specific lot identification
│   └── tax_reports.py           # Quarterly estimates, year-end export
├── tests/                       # 199 tests (pytest)
├── main.py                      # CLI entry point
├── requirements.txt
├── .env.example                 # Environment variable template
└── docs/
    ├── PLAN.md                  # Phase-by-phase plan
    └── progress.md              # Chronological progress log
```

---

## Development Status

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation (data ingestion, indicators, storage) | Complete |
| 2 | Strategies + Backtesting | Complete |
| 3 | Risk Management | Complete |
| 4 | Execution + Paper Trading | Complete |
| 5 | Monitoring Dashboard | Complete |
| 6 | Go-Live Gate (3-month paper trading validation) | Pending |
| 7 | Live Deployment ($100 -> $250 -> $500 ramp) | Pending |

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| Broker | Alpaca Markets (commission-free, IEX feed) |
| Fallback Data | yfinance |
| Database | SQLite via SQLAlchemy Core |
| Dashboard | Streamlit + Plotly |
| Backtesting | Backtrader |
| Indicators | ta (RSI, BB, EMA, MACD, ADX, ATR) |
| Logging | loguru |
| Notifications | Telegram Bot API |

---

## Testing

The test suite contains 199 tests covering all modules.

```bash
# Run the full test suite
pytest

# Run tests for a specific module
pytest tests/test_strategies.py
pytest tests/test_risk.py
pytest tests/test_execution.py

# Run with verbose output
pytest -v
```

Test coverage includes:
- Data ingestion and indicator calculations
- Strategy signal logic with mock market data
- Circuit breaker trigger conditions
- Position sizing calculations
- Order engine risk gate validation
- Wash sale and lot tracking

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
