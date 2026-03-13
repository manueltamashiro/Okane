"""Thread-based session manager for the Streamlit dashboard.

Owns the single PaperTradingSession instance and the threading.Thread that
runs it.  Pure Python — does NOT import Streamlit.
"""

from __future__ import annotations

import threading

from loguru import logger

from execution.paper_trading import PaperTradingSession, build_session

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_session: PaperTradingSession | None = None
_thread: threading.Thread | None = None
_error: str | None = None
_lock: threading.Lock = threading.Lock()
_symbols: list[str] = []
_poll_interval: int = 60


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_session(symbols: list[str], poll_interval: int) -> None:
    """Build a PaperTradingSession and run it on a daemon thread.

    Raises RuntimeError if a session is already running.
    """
    global _session, _thread, _error, _symbols, _poll_interval

    with _lock:
        # Thread-leak guard: refuse to spawn if a thread is still alive.
        if _thread is not None and _thread.is_alive():
            raise RuntimeError("A paper-trading session is already running")

        _error = None
        _symbols = list(symbols)
        _poll_interval = poll_interval

        logger.info(
            "[session_manager] building session — symbols={}, poll_interval={}s",
            _symbols,
            _poll_interval,
        )
        _session = build_session(symbols=_symbols, poll_interval_seconds=_poll_interval)

        _thread = threading.Thread(
            target=_run_session,
            name="paper-trading",
            daemon=True,
        )
        _thread.start()
        logger.info("[session_manager] thread started")


def stop_session() -> None:
    """Signal the running session to stop and wait for the thread to finish."""
    global _session, _thread

    with _lock:
        if _session is not None:
            logger.info("[session_manager] requesting session stop")
            _session.stop()

        if _thread is not None:
            _thread.join(timeout=5)
            logger.info("[session_manager] thread joined (alive={})", _thread.is_alive())
            _thread = None


def get_status() -> dict:
    """Return a snapshot of the session manager's state."""
    with _lock:
        return {
            "running": is_running(),
            "error": _error,
            "symbols": list(_symbols),
            "poll_interval": _poll_interval,
        }


def is_running() -> bool:
    """True when the background thread is alive and no error has been recorded."""
    return _thread is not None and _thread.is_alive() and _error is None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_session() -> None:
    """Wrapper executed on the background thread.

    Captures any exception so the dashboard can display it without crashing.
    """
    global _error

    try:
        if _session is not None:
            _session.start()  # blocks until stop() is called or an error occurs
    except Exception as exc:
        logger.error("[session_manager] session thread crashed: {}", exc)
        with _lock:
            _error = str(exc)
