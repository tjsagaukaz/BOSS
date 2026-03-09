from __future__ import annotations

import re
from typing import Any

from boss.workspace.workspace_state import WorkspaceStateStore

__test__ = False


class TestListener:
    FAILED_PATTERN = re.compile(r"FAILED\s+([^\s]+)")

    def __init__(self, workspace_state: WorkspaceStateStore) -> None:
        self.workspace_state = workspace_state

    def tests_ran(self, project_name: str, result: dict[str, Any]) -> None:
        payload = dict(result)
        payload["failed_tests"] = self._extract_failed_tests(result)
        payload["failure_summary"] = self._failure_summary(result)
        self.workspace_state.record_test_result(project_name, payload)

    def _extract_failed_tests(self, result: dict[str, Any]) -> list[str]:
        failed: list[str] = []
        for item in result.get("results", []) if isinstance(result.get("results"), list) else []:
            stdout = str(item.get("stdout", ""))
            stderr = str(item.get("stderr", ""))
            for source in (stdout, stderr):
                failed.extend(match.group(1) for match in self.FAILED_PATTERN.finditer(source))
        return list(dict.fromkeys(failed))[:10]

    def _failure_summary(self, result: dict[str, Any]) -> str:
        if not result.get("found", False):
            return str(result.get("message", "No tests detected."))
        if result.get("passed", False):
            return "Tests passed."
        lines: list[str] = []
        for item in result.get("results", []) if isinstance(result.get("results"), list) else []:
            stdout = str(item.get("stdout", "")).strip()
            stderr = str(item.get("stderr", "")).strip()
            if stderr:
                lines.append(stderr.splitlines()[-1])
            elif stdout:
                lines.append(stdout.splitlines()[-1])
        return " | ".join(line for line in lines if line)[:400] or str(result.get("message", "Tests failed."))
