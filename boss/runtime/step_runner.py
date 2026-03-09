from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class StepRunnerResult:
    status: str
    payload: dict[str, Any]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False


class StepRunner:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        python_executable: str | None = None,
        module_name: str = "boss.runtime.step_worker",
        default_timeout_seconds: int = 120,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.python_executable = python_executable or sys.executable
        self.module_name = module_name
        self.default_timeout_seconds = default_timeout_seconds

    def run_engineer_step(self, payload: dict[str, Any], timeout_seconds: int | None = None) -> StepRunnerResult:
        envelope = {"action": "engineer_step", **payload}
        return self._run_subprocess(envelope, timeout_seconds=timeout_seconds)

    def _run_subprocess(self, payload: dict[str, Any], timeout_seconds: int | None = None) -> StepRunnerResult:
        proc = subprocess.Popen(
            [self.python_executable, "-m", self.module_name],
            cwd=self.root_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        timeout = int(timeout_seconds or self.default_timeout_seconds)
        try:
            stdout, stderr = proc.communicate(input=json.dumps(payload), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return StepRunnerResult(
                status="timeout",
                payload={"error": f"Engineer subprocess timed out after {timeout}s."},
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                timed_out=True,
            )

        parsed = self._parse_stdout(stdout)
        if proc.returncode not in {0, None}:
            message = parsed.get("error") or stderr.strip() or f"Engineer subprocess exited with code {proc.returncode}."
            parsed.setdefault("error", message)
            return StepRunnerResult(
                status="error",
                payload=parsed,
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
            )

        status = str(parsed.get("status", "completed"))
        return StepRunnerResult(
            status=status,
            payload=parsed,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
        )

    def _parse_stdout(self, stdout: str) -> dict[str, Any]:
        cleaned = (stdout or "").strip()
        if not cleaned:
            return {"status": "error", "error": "Engineer subprocess returned no output."}
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return {
                "status": "error",
                "error": f"Engineer subprocess returned invalid JSON: {exc}",
                "raw_stdout": cleaned,
            }
        if isinstance(parsed, dict):
            return parsed
        return {"status": "error", "error": "Engineer subprocess returned an invalid payload.", "raw_stdout": cleaned}
