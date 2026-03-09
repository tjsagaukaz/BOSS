from __future__ import annotations

import subprocess

from boss.runtime.step_runner import StepRunner


class _FakeProcess:
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0, timeout: bool = False) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout = timeout
        self.killed = False

    def communicate(self, input=None, timeout=None):
        if self._timeout and not self.killed:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_step_runner_returns_completed_payload(monkeypatch, tmp_path):
    fake = _FakeProcess(stdout='{"status":"completed","result":{"text":"ok","agent_name":"engineer","provider":"openai","model":"gpt","duration_seconds":1.0,"usage":{},"tool_records":[]}}')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    runner = StepRunner(tmp_path)

    result = runner.run_engineer_step({"root_dir": str(tmp_path), "project_name": "demo"})

    assert result.status == "completed"
    assert result.payload["result"]["text"] == "ok"


def test_step_runner_kills_timed_out_process(monkeypatch, tmp_path):
    fake = _FakeProcess(timeout=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    runner = StepRunner(tmp_path, default_timeout_seconds=1)

    result = runner.run_engineer_step({"root_dir": str(tmp_path), "project_name": "demo"}, timeout_seconds=1)

    assert result.status == "timeout"
    assert result.timed_out is True
    assert fake.killed is True
