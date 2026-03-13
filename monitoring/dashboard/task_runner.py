"""Background task runner for the Okane dashboard.

Follows the session_manager.py pattern: module-level state protected by
threading.Lock, daemon threads for execution.  Pure Python — does NOT
import Streamlit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from loguru import logger


@dataclass
class TaskState:
    name: str
    status: str = "idle"  # idle | running | completed | error
    thread: threading.Thread | None = None
    output_lines: deque = field(default_factory=lambda: deque(maxlen=500))
    result: Any = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


_tasks: dict[str, TaskState] = {}
_lock: threading.Lock = threading.Lock()


def _capture_sink(message, task_name: str, output_lines: deque) -> None:
    """Loguru sink that captures log lines for a specific task thread."""
    # Only capture messages from the task's thread
    if threading.current_thread().name == task_name:
        output_lines.append(message.rstrip())


def _run_task(task: TaskState, fn: Callable, args: tuple, kwargs: dict) -> None:
    """Thread target — runs fn and captures result/error."""
    sink_id = None
    try:
        # Add a temporary loguru sink filtered to this thread
        sink_id = logger.add(
            lambda msg: _capture_sink(msg, task.name, task.output_lines),
            format="{time:HH:mm:ss} | {level: <8} | {message}",
            filter=lambda record: record["thread"].name == task.name,
            level="DEBUG",
        )

        result = fn(*args, **kwargs)

        with _lock:
            task.result = result
            task.status = "completed"
            task.completed_at = datetime.now(timezone.utc)

    except Exception as exc:
        logger.error(f"[task_runner] task '{task.name}' failed: {exc}")
        with _lock:
            task.error = str(exc)
            task.status = "error"
            task.completed_at = datetime.now(timezone.utc)

    finally:
        if sink_id is not None:
            logger.remove(sink_id)


def run_task(name: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
    """Spawn a daemon thread to run fn. Raises RuntimeError if same-named task is running."""
    with _lock:
        existing = _tasks.get(name)
        if existing and existing.thread and existing.thread.is_alive():
            raise RuntimeError(f"Task '{name}' is already running")

        task = TaskState(
            name=name,
            status="running",
            started_at=datetime.now(timezone.utc),
            output_lines=deque(maxlen=500),
        )
        _tasks[name] = task

    thread = threading.Thread(
        target=_run_task,
        args=(task, fn, args, kwargs),
        name=name,
        daemon=True,
    )
    task.thread = thread
    thread.start()
    logger.info(f"[task_runner] started task '{name}'")


def get_task(name: str) -> TaskState | None:
    """Return a snapshot of the named task, or None if never run."""
    with _lock:
        return _tasks.get(name)


def is_task_running(name: str) -> bool:
    """True if the named task has an alive thread."""
    with _lock:
        task = _tasks.get(name)
        return task is not None and task.thread is not None and task.thread.is_alive()


def cancel_task(name: str) -> None:
    """Best-effort cancellation — just marks the task. Python threads can't be killed."""
    with _lock:
        task = _tasks.get(name)
        if task:
            task.status = "error"
            task.error = "Cancelled by user"
            task.completed_at = datetime.now(timezone.utc)
