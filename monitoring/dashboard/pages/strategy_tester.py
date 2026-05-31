"""Strategy Tester page — real-time candlestick charts, adjustable parameters, one-shot execution."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from monitoring.dashboard.task_runner import get_task, is_task_running, run_task
from monitoring.dashboard.theme import (
    COLORWAY,
    MUTED_GREEN,
    MUTED_RED,
    STEEL_BLUE,
    get_plotly_template,
)


_SIGNAL_TASK = "strategy_tester_signal"
_EXEC_TASK = "strategy_tester_execute"


# ---------------------------------------------------------------------------
# Parameter sliders
# ---------------------------------------------------------------------------

def _render_param_sliders(strategy_name: str) -> dict[str, Any]:
    """Render strategy-specific sliders and return a params dict."""
    params: dict[str, Any] = {}

    if strategy_name == "mean_reversion":
        params["rsi_oversold"] = st.slider("RSI Oversold", 20, 45, 30)
        _bb_std_raw = st.slider("BB Std Dev", 1.0, 3.0, 2.0, step=0.1)
        params["bb_std"] = int(_bb_std_raw) if _bb_std_raw == int(_bb_std_raw) else _bb_std_raw
        params["bb_period"] = st.slider("BB Period", 10, 30, 20)
        params["rsi_exit"] = st.slider("RSI Exit", 40, 70, 50)
        params["stop_loss_pct"] = st.slider("Stop Loss %", 0.005, 0.05, 0.02, step=0.005, format="%.3f")

    elif strategy_name == "momentum":
        params["adx_min"] = st.slider("ADX Min", 10, 40, 25)
        params["ema_fast"] = st.slider("EMA Fast", 3, 15, 9)
        params["ema_slow"] = st.slider("EMA Slow", 10, 40, 21)
        params["atr_multiplier"] = st.slider("ATR Multiplier", 1.0, 4.0, 2.0, step=0.1)

    elif strategy_name == "swing":
        params["adx_trend_min"] = st.slider("ADX Trend Min", 10, 30, 20)
        params["rsi_overbought"] = st.slider("RSI Overbought", 55, 80, 65)
        params["ema_period"] = st.slider("EMA Period", 10, 50, 21)

    return params


# ---------------------------------------------------------------------------
# Data fetching + indicator enrichment
# ---------------------------------------------------------------------------

def _fetch_and_enrich(
    symbol: str,
    timeframe: str,
    lookback_days: int,
    strategy_name: str,
    params: dict[str, Any],
) -> pd.DataFrame:
    """Ingest bars, fetch from DB, compute indicators in memory (no persistence)."""
    from data.ingestion import ingest_symbol
    from data.storage import fetch_bars
    from data.indicators import (
        add_rsi,
        add_bollinger_bands,
        add_ema,
        add_macd,
        add_adx,
        add_atr,
    )

    ingest_symbol(symbol, timeframe, lookback_days)
    df = fetch_bars(symbol, timeframe, limit=300)

    if df.empty:
        return df

    if strategy_name == "mean_reversion":
        df = add_rsi(df, 14)
        df = add_bollinger_bands(df, period=params["bb_period"], std_dev=params["bb_std"])

    elif strategy_name == "momentum":
        df = add_ema(df, params["ema_fast"])
        df = add_ema(df, params["ema_slow"])
        df = add_macd(df)
        df = add_adx(df)
        df = add_atr(df)

    elif strategy_name == "swing":
        df = add_ema(df, params["ema_period"])
        df = add_adx(df)
        df = add_rsi(df, 14)

    return df


# ---------------------------------------------------------------------------
# Strategy instantiation with custom params
# ---------------------------------------------------------------------------

def _build_strategy_with_params(strategy_name: str, params: dict[str, Any]):
    """Instantiate a strategy and override instance vars with slider values."""
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.momentum import MomentumStrategy
    from strategies.swing import SwingStrategy

    if strategy_name == "mean_reversion":
        strategy = MeanReversionStrategy()
        strategy.rsi_oversold = params["rsi_oversold"]
        strategy.bb_std = params["bb_std"]
        strategy.bb_period = params["bb_period"]
        strategy.rsi_exit = params["rsi_exit"]
        strategy.stop_loss_pct = params["stop_loss_pct"]
        strategy.timeframe = params["timeframe"]

    elif strategy_name == "momentum":
        strategy = MomentumStrategy()
        strategy.adx_min = params["adx_min"]
        strategy.ema_fast = params["ema_fast"]
        strategy.ema_slow = params["ema_slow"]
        strategy.atr_multiplier = params["atr_multiplier"]
        strategy.timeframe = params["timeframe"]

    elif strategy_name == "swing":
        strategy = SwingStrategy()
        strategy._adx_trend_min = params["adx_trend_min"]
        strategy._rsi_overbought = params["rsi_overbought"]
        strategy._ema_period = params["ema_period"]
        strategy.primary_tf = params["timeframe"]

    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    strategy.name = f"test_{strategy_name}"
    return strategy


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def _generate_test_signal(strategy, df: pd.DataFrame, symbol: str):
    """Generate a signal using the configured strategy."""
    return strategy.generate_signal(df, symbol)


def _run_signal_generation(
    symbol: str,
    timeframe: str,
    lookback_days: int,
    strategy_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Task body: ingest, enrich, build strategy, generate signal. Returns the result dict."""
    logger.info(f"[strategy_tester] generating signal: {strategy_name} {symbol} {timeframe}")
    df = _fetch_and_enrich(symbol, timeframe, lookback_days, strategy_name, params)
    if df.empty:
        return {
            "df": df,
            "signal": None,
            "strategy_name": strategy_name,
            "params": params,
            "symbol": symbol,
            "timeframe": timeframe,
            "empty": True,
        }
    strategy = _build_strategy_with_params(strategy_name, params)
    signal = _generate_test_signal(strategy, df, symbol)
    return {
        "df": df,
        "signal": signal,
        "strategy_name": strategy_name,
        "params": params,
        "symbol": symbol,
        "timeframe": timeframe,
        "empty": False,
    }


# ---------------------------------------------------------------------------
# Candlestick chart
# ---------------------------------------------------------------------------

def _build_candlestick_chart(
    df: pd.DataFrame,
    strategy_name: str,
    params: dict[str, Any],
    signal,
    last_n_bars: int = 60,
) -> go.Figure:
    """Build a multi-row candlestick chart with strategy-specific overlays."""
    tail = df.tail(last_n_bars).copy()
    x = tail.index if tail.index.name == "timestamp" else list(range(len(tail)))

    # Determine subplot layout
    if strategy_name == "mean_reversion":
        row_heights = [0.7, 0.3]
        rows = 2
        subplot_titles = ("", "RSI")
    elif strategy_name == "momentum":
        row_heights = [0.7, 0.3]
        rows = 2
        subplot_titles = ("", "MACD")
    elif strategy_name == "swing":
        row_heights = [0.7, 0.3]
        rows = 2
        subplot_titles = ("", "RSI")
    else:
        row_heights = [1.0]
        rows = 1
        subplot_titles = ("",)

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )

    # Row 1: Candlestick
    fig.add_trace(
        go.Candlestick(
            x=x,
            open=tail["open"],
            high=tail["high"],
            low=tail["low"],
            close=tail["close"],
            increasing_line_color=MUTED_GREEN,
            decreasing_line_color=MUTED_RED,
            increasing_fillcolor=MUTED_GREEN,
            decreasing_fillcolor=MUTED_RED,
            name="Price",
            showlegend=False,
        ),
        row=1, col=1,
    )

    # Strategy-specific overlays on row 1
    if strategy_name == "mean_reversion":
        bb_p = params["bb_period"]
        bb_s = params["bb_std"]
        upper_col = f"bb_upper_{bb_p}_{bb_s}"
        mid_col = f"bb_mid_{bb_p}_{bb_s}"
        lower_col = f"bb_lower_{bb_p}_{bb_s}"

        if upper_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[upper_col], mode="lines", name="BB Upper",
                line=dict(color=STEEL_BLUE, width=1),
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=x, y=tail[lower_col], mode="lines", name="BB Lower",
                line=dict(color=STEEL_BLUE, width=1),
                fill="tonexty", fillcolor="rgba(86,123,149,0.08)",
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=x, y=tail[mid_col], mode="lines", name="BB Mid",
                line=dict(color=STEEL_BLUE, width=1, dash="dash"),
            ), row=1, col=1)

    elif strategy_name == "momentum":
        ema_fast_col = f"ema_{params['ema_fast']}"
        ema_slow_col = f"ema_{params['ema_slow']}"
        if ema_fast_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[ema_fast_col], mode="lines", name=f"EMA {params['ema_fast']}",
                line=dict(color=COLORWAY[1], width=1.5),
            ), row=1, col=1)
        if ema_slow_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[ema_slow_col], mode="lines", name=f"EMA {params['ema_slow']}",
                line=dict(color=COLORWAY[5], width=1.5),
            ), row=1, col=1)

    elif strategy_name == "swing":
        ema_col = f"ema_{params['ema_period']}"
        if ema_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[ema_col], mode="lines", name=f"EMA {params['ema_period']}",
                line=dict(color=STEEL_BLUE, width=1.5),
            ), row=1, col=1)

    # Signal marker on row 1
    if signal is not None:
        from strategies.base import Direction
        marker_symbol = "triangle-up" if signal.direction == Direction.BUY else "triangle-down"
        marker_color = MUTED_GREEN if signal.direction == Direction.BUY else MUTED_RED
        last_x = x.iloc[-1] if hasattr(x, "iloc") else x[-1]
        last_price = float(tail["close"].iloc[-1])
        fig.add_trace(go.Scatter(
            x=[last_x],
            y=[last_price],
            mode="markers",
            marker=dict(symbol=marker_symbol, size=16, color=marker_color, line=dict(width=1, color=marker_color)),
            name=signal.direction.value,
            showlegend=True,
        ), row=1, col=1)

    # Row 2: indicator subplot
    if strategy_name in ("mean_reversion", "swing"):
        rsi_col = "rsi_14"
        if rsi_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[rsi_col], mode="lines", name="RSI",
                line=dict(color=STEEL_BLUE, width=1.5),
            ), row=2, col=1)

            # Threshold lines
            if strategy_name == "mean_reversion":
                oversold = params.get("rsi_oversold", 30)
                exit_val = params.get("rsi_exit", 50)
                fig.add_hline(y=oversold, line_dash="dot", line_color=MUTED_GREEN, line_width=1, row=2, col=1)
                fig.add_hline(y=exit_val, line_dash="dot", line_color=MUTED_RED, line_width=1, row=2, col=1)
                fig.add_hrect(y0=0, y1=oversold, fillcolor=MUTED_GREEN, opacity=0.06, line_width=0, row=2, col=1)
            else:
                overbought = params.get("rsi_overbought", 65)
                fig.add_hline(y=overbought, line_dash="dot", line_color=MUTED_RED, line_width=1, row=2, col=1)
                fig.add_hline(y=30, line_dash="dot", line_color=MUTED_GREEN, line_width=1, row=2, col=1)

    elif strategy_name == "momentum":
        macd_col = "macd_12_26_9"
        macd_sig_col = "macd_signal_12_26_9"
        macd_hist_col = "macd_hist_12_26_9"

        if macd_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[macd_col], mode="lines", name="MACD",
                line=dict(color=STEEL_BLUE, width=1.5),
            ), row=2, col=1)
        if macd_sig_col in tail.columns:
            fig.add_trace(go.Scatter(
                x=x, y=tail[macd_sig_col], mode="lines", name="Signal",
                line=dict(color=COLORWAY[1], width=1.5),
            ), row=2, col=1)
        if macd_hist_col in tail.columns:
            hist_vals = tail[macd_hist_col]
            colors = [MUTED_GREEN if v >= 0 else MUTED_RED for v in hist_vals]
            fig.add_trace(go.Bar(
                x=x, y=hist_vals, name="Histogram",
                marker_color=colors, showlegend=False,
            ), row=2, col=1)

    # Layout
    fig.update_layout(
        template=get_plotly_template(),
        height=700,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=30, t=40, b=40),
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)

    return fig


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

def _execute_test_trade(signal) -> str:
    """Execute a one-shot trade through the full risk pipeline. Returns outcome string."""
    from execution.alpaca_client import make_alpaca_client
    from execution.order_engine import process_signal
    from risk.portfolio import PortfolioState, SECTOR_MAP
    from data.storage import fetch_open_positions

    try:
        client = make_alpaca_client()
        acct = client.get_account()
        account_value = float(acct.equity)

        positions = fetch_open_positions()
        open_syms = [p["symbol"] for p in positions]
        sector_map = {p["symbol"]: SECTOR_MAP.get(p["symbol"], "Unknown") for p in positions}
        portfolio = PortfolioState(open_positions=open_syms, sector_map=sector_map)

        # Test trades bypass the signals table — passing signal_db_id=None means
        # process_signal won't mark anything as approved/rejected, so test signals
        # never end up in fetch_pending_signals() and the paper trading loop won't
        # double-trade them. The submitted order still lands in the orders table.
        outcome = process_signal(
            signal=signal,
            portfolio_state=portfolio,
            client=client,
            account_value=account_value,
            signal_db_id=None,
        )

        if outcome.submitted:
            return f"Order submitted: {outcome.order_result.order_id} ({signal.direction.value} {signal.symbol})"
        else:
            return f"Rejected: {outcome.rejection_reason}"

    except Exception as exc:
        logger.error(f"[strategy_tester] execution error: {exc}")
        return f"Execution failed: {exc}"


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

def render_strategy_tester() -> None:
    st.header("Strategy Tester")

    # Warning banner
    st.warning(
        "This page sends **live orders** when you click Execute Trade. "
        "Use with caution — all trades are real and irreversible."
    )

    # --- Configuration panel ---
    col_cfg, col_params = st.columns([1, 1])

    with col_cfg:
        st.subheader("Configuration")
        strategy_name = st.selectbox(
            "Strategy",
            options=["mean_reversion", "momentum", "swing"],
            key="tester_strategy",
        )
        symbol = st.text_input("Symbol", value="AAPL", key="tester_symbol").strip().upper()
        if not symbol:
            st.error("Please enter a valid symbol.")
            return
        timeframe = st.selectbox(
            "Timeframe",
            options=["1d", "1h", "4h"],
            key="tester_timeframe",
        )
        lookback_days = st.number_input(
            "Lookback Days",
            min_value=30,
            max_value=730,
            value=365,
            key="tester_lookback",
        )

    # Clear stale state when config changes (cached result reference, not the task itself —
    # task results are immutable once completed and just become stale-but-harmless)
    _cfg_key = f"{strategy_name}_{symbol}_{timeframe}"
    if st.session_state.get("_tester_cfg_key") != _cfg_key:
        st.session_state["_tester_cfg_key"] = _cfg_key

    with col_params:
        st.subheader("Parameters")
        params = _render_param_sliders(strategy_name)
        params["timeframe"] = timeframe

    # --- Generate Signal ---
    signal_running = is_task_running(_SIGNAL_TASK)
    if st.button(
        "Generate Signal",
        type="primary",
        use_container_width=True,
        disabled=signal_running or is_task_running(_EXEC_TASK),
    ):
        try:
            run_task(
                _SIGNAL_TASK,
                _run_signal_generation,
                symbol, timeframe, int(lookback_days), strategy_name, params,
            )
            st.rerun()
        except RuntimeError as exc:
            st.warning(str(exc))

    signal_task = get_task(_SIGNAL_TASK)
    if signal_task is not None:
        if signal_task.status == "running":
            st.info("Fetching data and generating signal... (refresh or interact to update)")
        elif signal_task.status == "error":
            st.error(f"Signal generation failed: {signal_task.error}")

    # --- Results ---
    result = signal_task.result if signal_task and signal_task.status == "completed" else None
    if result is not None and result.get("empty"):
        st.error(f"No data available for {result['symbol']} [{result['timeframe']}]. Run data pipeline first.")
        result = None
    if result is not None:
        df = result["df"]
        signal = result["signal"]
        s_name = result["strategy_name"]
        s_params = result["params"]
        s_symbol = result["symbol"]

        st.divider()

        # Chart
        fig = _build_candlestick_chart(df, s_name, s_params, signal)
        st.plotly_chart(fig, use_container_width=True)

        # Signal card
        st.subheader("Signal Result")
        if signal is not None:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Direction", signal.direction.value)
            c2.metric("Price", f"${signal.price:,.2f}")
            c3.metric("Confidence", f"{signal.confidence:.0%}")
            c4.metric("Stop Loss", f"${signal.stop_loss:,.2f}" if signal.stop_loss else "N/A")

            # Execute Trade button
            st.divider()
            exec_col1, exec_col2 = st.columns([3, 1])
            with exec_col1:
                st.markdown(
                    f"**Ready to execute:** {signal.direction.value} {s_symbol} "
                    f"@ ${signal.price:,.2f} (confidence {signal.confidence:.0%})"
                )
                # Explicit confirmation gate: this places a REAL order. The button
                # stays disabled until the operator ticks the box, preventing an
                # accidental single click (or stray rerun) from trading.
                confirmed = st.checkbox(
                    f"I confirm placing a live {signal.direction.value} order for {s_symbol}",
                    key="tester_execute_confirm",
                    value=False,
                )
            with exec_col2:
                exec_running = is_task_running(_EXEC_TASK)
                if st.button(
                    "Execute Trade",
                    type="primary",
                    use_container_width=True,
                    disabled=exec_running or signal_running or not confirmed,
                ):
                    try:
                        run_task(_EXEC_TASK, _execute_test_trade, signal)
                        # Reset the gate so the next order requires re-confirmation.
                        st.session_state["tester_execute_confirm"] = False
                        st.rerun()
                    except RuntimeError as rexc:
                        st.warning(str(rexc))

            exec_task = get_task(_EXEC_TASK)
            if exec_task is not None:
                if exec_task.status == "running":
                    st.info("Submitting order...")
                elif exec_task.status == "error":
                    st.error(f"Execution failed: {exec_task.error}")
                elif exec_task.status == "completed" and exec_task.result:
                    if exec_task.result.startswith("Order submitted"):
                        st.success(exec_task.result)
                    else:
                        st.error(exec_task.result)
        else:
            st.info(f"No signal generated for {s_symbol} with current parameters. The strategy conditions were not met on the latest bar.")
