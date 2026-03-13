"""Operations page — Pipeline, Signals, Backtest, Status."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from monitoring.dashboard.theme import get_plotly_template

from config.settings import DEFAULT_SYMBOLS
from monitoring.dashboard.task_runner import get_task, is_task_running, run_task


def _render_task_status(task_name: str) -> None:
    """Show status badge, elapsed time, and scrollable log output for a task."""
    task = get_task(task_name)
    if task is None:
        st.info("Not yet run")
        return

    # Status badge
    if task.status == "running":
        now = datetime.now(timezone.utc) if task.started_at and task.started_at.tzinfo else datetime.now()
        elapsed = (now - task.started_at).total_seconds() if task.started_at else 0
        st.warning(f"Running... ({elapsed:.0f}s elapsed)")
    elif task.status == "completed":
        st.success("Completed")
    elif task.status == "error":
        st.error(f"Error: {task.error}")
    else:
        st.info("Idle")

    # Log output
    if task.output_lines:
        st.code("\n".join(task.output_lines), language="text")


def render_operations() -> None:
    st.header("Operations")

    # =================================================================
    # 1. Data Pipeline
    # =================================================================
    st.subheader("Data Pipeline")

    pipeline_symbols = st.multiselect(
        "Symbols",
        options=DEFAULT_SYMBOLS,
        default=DEFAULT_SYMBOLS,
        key="pipeline_symbols",
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Run Full Pipeline", type="primary", use_container_width=True,
                      disabled=is_task_running("pipeline")):
            from main import cmd_pipeline
            run_task("pipeline", cmd_pipeline, list(pipeline_symbols))
            st.rerun()

    with col2:
        if st.button("Run Ingest Only", use_container_width=True,
                      disabled=is_task_running("ingest")):
            from main import cmd_ingest
            run_task("ingest", cmd_ingest, list(pipeline_symbols))
            st.rerun()

    with col3:
        if st.button("Run Indicators Only", use_container_width=True,
                      disabled=is_task_running("indicators")):
            from main import cmd_indicators
            run_task("indicators", cmd_indicators, list(pipeline_symbols))
            st.rerun()

    # Show status for whichever pipeline task was last run
    for tname in ["pipeline", "ingest", "indicators"]:
        if get_task(tname) is not None:
            with st.expander(f"{tname} output", expanded=is_task_running(tname)):
                _render_task_status(tname)

    st.divider()

    # =================================================================
    # 2. Signal Generation
    # =================================================================
    st.subheader("Signal Generation")

    signal_symbols = st.multiselect(
        "Symbols",
        options=DEFAULT_SYMBOLS,
        default=DEFAULT_SYMBOLS,
        key="signal_symbols",
    )

    if st.button("Generate Signals", type="primary", use_container_width=True,
                  disabled=is_task_running("signals")):
        from main import cmd_signals
        run_task("signals", cmd_signals, list(signal_symbols))
        st.rerun()

    _render_task_status("signals")

    st.divider()

    # =================================================================
    # 3. Backtest
    # =================================================================
    st.subheader("Backtest")

    with st.form("backtest_form"):
        bcol1, bcol2 = st.columns(2)
        with bcol1:
            bt_symbol = st.text_input("Symbol", value="AAPL", key="bt_symbol")
            bt_strategy = st.selectbox(
                "Strategy",
                options=["mean_reversion", "momentum", "swing"],
                key="bt_strategy",
            )
        with bcol2:
            bt_start = st.date_input("Start Date", value=datetime(2024, 1, 1), key="bt_start")
            bt_end = st.date_input("End Date", value=datetime(2024, 12, 31), key="bt_end")

        bt_submitted = st.form_submit_button(
            "Run Backtest", type="primary",
            disabled=is_task_running("backtest"),
        )

    if bt_submitted:
        def _run_backtest(symbol: str, strategy_name: str, start: datetime, end: datetime) -> dict:
            from strategies.mean_reversion import MeanReversionStrategy
            from strategies.momentum import MomentumStrategy
            from strategies.swing import SwingStrategy
            from backtest.backtrader_runner import run_backtest as bt_run
            from backtest.results import save_result

            strategy_map = {
                "mean_reversion": MeanReversionStrategy,
                "momentum": MomentumStrategy,
                "swing": SwingStrategy,
            }
            strategy = strategy_map[strategy_name]()
            result = bt_run(strategy, symbol, start, end)
            if "error" not in result:
                save_result(result)
            return result

        start_dt = datetime.combine(bt_start, datetime.min.time())
        end_dt = datetime.combine(bt_end, datetime.max.time())
        run_task("backtest", _run_backtest, bt_symbol.strip().upper(), bt_strategy, start_dt, end_dt)
        st.rerun()

    # Backtest status + results
    bt_task = get_task("backtest")
    if bt_task is not None:
        _render_task_status("backtest")

        if bt_task.status == "completed" and bt_task.result:
            result = bt_task.result
            if "error" in result:
                st.error(f"Backtest error: {result['error']}")
            else:
                # KPI cards
                k1, k2, k3, k4, k5 = st.columns(5)
                k1.metric("Total Return", f"{result.get('total_return_pct', 0):.2f}%")
                k2.metric("Sharpe Ratio", f"{result.get('sharpe_ratio', 0):.2f}")
                k3.metric("Win Rate", f"{result.get('win_rate', 0):.1%}")
                k4.metric("Max Drawdown", f"{result.get('max_drawdown_pct', 0):.2f}%")
                k5.metric("Total Trades", str(result.get('total_trades', 0)))

                # Equity curve if available
                if "equity_curve" in result:
                    eq_data = result["equity_curve"]
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=list(range(len(eq_data))),
                        y=eq_data,
                        mode="lines",
                        name="Equity",
                    ))
                    fig.update_layout(
                        template=get_plotly_template(),
                        title="Backtest Equity Curve",
                        xaxis_title="Bar",
                        yaxis_title="Equity ($)",
                        height=400,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                # Trade list if available
                if "trades" in result and result["trades"]:
                    st.subheader("Trade List")
                    st.dataframe(pd.DataFrame(result["trades"]), use_container_width=True)

    st.divider()

    # =================================================================
    # 4. Database Status
    # =================================================================
    st.subheader("Database Status")

    if st.button("Refresh Status", key="refresh_db_status"):
        st.cache_data.clear()

    from data.storage import get_db_status
    db_status = get_db_status()

    # Bar count table
    if db_status["bars"]:
        st.markdown("**Price Data**")
        st.dataframe(
            pd.DataFrame(db_status["bars"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No price data in database. Run the pipeline first.")

    # Circuit breaker state
    cb = db_status["circuit_breaker"]
    if cb:
        st.markdown("**Circuit Breaker State**")
        cb1, cb2, cb3 = st.columns(3)

        with cb1:
            if cb.get("daily_halt"):
                st.error(f"Daily: HALTED ({cb.get('daily_loss_pct', 0):.2%} loss)")
            else:
                st.success(f"Daily: OK ({cb.get('daily_loss_pct', 0):.2%} loss)")
        with cb2:
            if cb.get("weekly_halt"):
                st.error(f"Weekly: HALTED ({cb.get('weekly_loss_pct', 0):.2%} loss)")
            else:
                st.success(f"Weekly: OK ({cb.get('weekly_loss_pct', 0):.2%} loss)")
        with cb3:
            if cb.get("full_halt"):
                st.error(f"Full: HALTED ({cb.get('max_drawdown_pct', 0):.2%} DD)")
            else:
                st.success(f"Full: OK ({cb.get('max_drawdown_pct', 0):.2%} DD)")

        if cb.get("halt_reason"):
            st.warning(f"Halt Reason: {cb['halt_reason']}")

    # Auto-refresh while tasks are running
    if any(is_task_running(t) for t in ["pipeline", "ingest", "indicators", "signals", "backtest"]):
        time.sleep(2)
        st.rerun()
