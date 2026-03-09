from __future__ import annotations

import os
import signal
import threading
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


class ExecutionTimeoutError(RuntimeError):
    pass


def call_with_timeout(
    timeout_seconds: float | None,
    callback: Callable[..., T],
    *args: Any,
    error_message: str | None = None,
    **kwargs: Any,
) -> T:
    if timeout_seconds is None or timeout_seconds <= 0:
        return callback(*args, **kwargs)
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return callback(*args, **kwargs)

    message = error_message or f"Execution timed out after {float(timeout_seconds):.0f}s."

    def _handle_timeout(signum, frame):  # type: ignore[unused-argument]
        raise ExecutionTimeoutError(message)

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    signal.signal(signal.SIGALRM, _handle_timeout)
    try:
        return callback(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
