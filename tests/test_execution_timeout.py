from __future__ import annotations

import time

import pytest

from boss.runtime.execution_timeout import ExecutionTimeoutError, call_with_timeout


def test_call_with_timeout_returns_result():
    result = call_with_timeout(1, lambda: "ok")
    assert result == "ok"


def test_call_with_timeout_raises_on_expiry():
    with pytest.raises(ExecutionTimeoutError, match="timed out"):
        call_with_timeout(0.05, time.sleep, 0.2, error_message="Engineer execution timed out")
