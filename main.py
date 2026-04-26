"""
Trader Bot — Entry Point

Usage:
  python main.py ingest        Fetch latest OHLCV data for all symbols
  python main.py indicators    Calculate + store technical indicators
  python main.py pipeline      Run ingest + indicators in sequence
  python main.py status        Show DB summary (symbols, bar counts, circuit breaker)
  python main.py signals          Run all strategies on DB data, print signals
  python main.py backtest [symbol] [strategy] [start] [end]
                                  Run backtest, save JSON results
  python main.py dashboard     Launch the Streamlit monitoring dashboard
"""

import sys
from loguru import logger

from config.settings import (
    DEFAULT_SYMBOLS,
    LOG_FILE,
    LOG_LEVEL,
    validate_config,
)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=LOG_LEVEL, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(LOG_FILE, level="DEBUG", rotation="10 MB", retention="30 days",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_ingest(symbols: list[str]) -> None:
    """Fetch and store OHLCV data for all symbols and key timeframes."""
    from data.storage import init_db
    from data.ingestion import ingest_universe

    init_db()

    for timeframe in ["1d", "1h"]:
        logger.info(f"Ingesting [{timeframe}] for {symbols}")
        results = ingest_universe(symbols, timeframe=timeframe, lookback_days=365)
        for sym, df in results.items():
            status = f"{len(df)} bars" if not df.empty else "NO DATA"
            logger.info(f"  {sym} [{timeframe}]: {status}")


def cmd_indicators(symbols: list[str]) -> None:
    """Calculate and store technical indicators for all symbols."""
    from data.indicators import calculate_universe

    for timeframe in ["1d", "1h"]:
        logger.info(f"Calculating indicators [{timeframe}] for {symbols}")
        results = calculate_universe(symbols, timeframe=timeframe, lookback_bars=300)
        for sym, df in results.items():
            if df.empty:
                logger.warning(f"  {sym} [{timeframe}]: skipped (no data)")
            else:
                ind_cols = [c for c in df.columns
                            if c not in {"open","high","low","close","volume","vwap","trade_count","source"}]
                logger.info(f"  {sym} [{timeframe}]: {len(ind_cols)} indicators on {len(df)} bars")


def cmd_pipeline(symbols: list[str]) -> None:
    """Run ingest → indicators in sequence."""
    cmd_ingest(symbols)
    cmd_indicators(symbols)


def cmd_signals(symbols: list[str]) -> None:
    """Run all strategies on stored DB data and print signals for each symbol."""
    from data.storage import init_db, fetch_bars
    from data.indicators import add_all_indicators
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.momentum import MomentumStrategy
    from strategies.swing import SwingStrategy
    from strategies.meta_strategy import MetaStrategy
    from strategies.base import save_signal, Direction

    init_db()

    mr = MeanReversionStrategy()
    mom = MomentumStrategy()
    sw = SwingStrategy()
    meta = MetaStrategy([mr, mom, sw])

    for symbol in symbols:
        df = fetch_bars(symbol, "1d")
        if df.empty:
            logger.warning(f"  {symbol}: no data in DB, skipping")
            continue

        enriched_df = add_all_indicators(df)

        for strategy in (mr, mom, sw):
            signal = strategy.generate_signal(enriched_df, symbol)
            logger.info(f"  {symbol} [{strategy.__class__.__name__}]: {signal}")

        meta_signal = meta.run(enriched_df, symbol)
        if meta_signal is not None and meta_signal.direction != Direction.HOLD:
            save_signal(meta_signal)
            logger.info(f"  {symbol} [MetaStrategy]: {meta_signal} (SAVED)")
        else:
            logger.info(f"  {symbol} [MetaStrategy]: {meta_signal}")


def cmd_backtest(
    symbol: str,
    strategy_name: str,
    start_str: str,
    end_str: str,
) -> None:
    """Run a backtest for one symbol + strategy and save results to JSON."""
    from datetime import datetime
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.momentum import MomentumStrategy
    from strategies.swing import SwingStrategy
    from backtest.backtrader_runner import run_backtest
    from backtest.results import save_result

    start = datetime.fromisoformat(start_str)
    end = datetime.fromisoformat(end_str)

    strategy_map = {
        "mean_reversion": MeanReversionStrategy,
        "momentum": MomentumStrategy,
        "swing": SwingStrategy,
    }

    if strategy_name not in strategy_map:
        print(f"Unknown strategy '{strategy_name}'. Choose from: {', '.join(strategy_map)}")
        sys.exit(2)

    strategy = strategy_map[strategy_name]()

    result = run_backtest(strategy, symbol, start, end)

    if "error" in result:
        print(f"Backtest error: {result['error']}")
        sys.exit(1)

    path = save_result(result)
    print(f"Results saved to: {path}")

    print(
        f"\n=== Backtest Summary ===\n"
        f"  Symbol:          {symbol}\n"
        f"  Strategy:        {strategy_name}\n"
        f"  Total return:    {result.get('total_return_pct', 'N/A')}\n"
        f"  Sharpe ratio:    {result.get('sharpe_ratio', 'N/A')}\n"
        f"  Win rate:        {result.get('win_rate', 'N/A')}\n"
        f"  Max drawdown:    {result.get('max_drawdown_pct', 'N/A')}\n"
        f"  Total trades:    {result.get('total_trades', 'N/A')}\n"
    )


def cmd_dashboard() -> None:
    """Launch the Streamlit dashboard."""
    import subprocess
    from config.settings import BASE_DIR
    dashboard_path = BASE_DIR / "monitoring" / "dashboard.py"
    subprocess.run(["streamlit", "run", str(dashboard_path)], check=True)


def cmd_status() -> None:
    """Print a summary of what's in the database."""
    from data.storage import get_db_status, init_db

    init_db()
    status = get_db_status()

    print("\n=== Price Data ===")
    if status["bars"]:
        for r in status["bars"]:
            print(f"  {r['symbol']:6s} [{r['timeframe']:4s}]  {r['count']:5d} bars  "
                  f"{r['first']} → {r['last']}")
    else:
        print("  (empty — run: python main.py ingest)")

    cb = status["circuit_breaker"]
    print("\n=== Circuit Breaker ===")
    if cb:
        print(f"  Daily halt:  {cb['daily_halt']}  ({cb['daily_loss_pct']:.2%} loss)")
        print(f"  Weekly halt: {cb['weekly_halt']}  ({cb['weekly_loss_pct']:.2%} loss)")
        print(f"  Full halt:   {cb['full_halt']}  ({cb['max_drawdown_pct']:.2%} drawdown)")
        if cb.get("halt_reason"):
            print(f"  Reason: {cb['halt_reason']}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COMMANDS = {
    "ingest":     lambda: cmd_ingest(DEFAULT_SYMBOLS),
    "indicators": lambda: cmd_indicators(DEFAULT_SYMBOLS),
    "pipeline":   lambda: cmd_pipeline(DEFAULT_SYMBOLS),
    "dashboard":  cmd_dashboard,
    "status":     cmd_status,
    "signals":    lambda: cmd_signals(DEFAULT_SYMBOLS),
    "backtest":   lambda: cmd_backtest(
        sys.argv[2] if len(sys.argv) > 2 else "AAPL",
        sys.argv[3] if len(sys.argv) > 3 else "mean_reversion",
        sys.argv[4] if len(sys.argv) > 4 else "2024-01-01",
        sys.argv[5] if len(sys.argv) > 5 else "2024-12-31",
    ),
}


def main() -> None:
    setup_logging()

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1]

    # Validate config for commands that need API access
    if command in ("ingest", "pipeline"):
        try:
            validate_config()
        except EnvironmentError as exc:
            logger.warning(f"Config warning: {exc}")
            logger.info("Proceeding with yfinance fallback (no Alpaca keys)")

    logger.info(f"Running command: {command}")
    COMMANDS[command]()
    logger.info("Done.")


if __name__ == "__main__":
    main()
