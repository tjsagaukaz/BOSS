from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class TerminalTools:
    DEFAULT_ALLOWED = {
        "git",
        "python",
        "python3",
        "pytest",
        "uv",
        "pip",
        "pip3",
        "rg",
        "ls",
        "cat",
        "wc",
        "head",
        "tail",
        "sed",
        "find",
        "make",
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "node",
        "cargo",
        "go",
    }
    BLOCKED_FRAGMENTS = ["&&", "||", ";", "|", ">", "<", "$(", "`"]

    def __init__(
        self,
        root: str | Path,
        full_access: bool = False,
        allowed_commands: set[str] | None = None,
        project_name: str | None = None,
        terminal_listener=None,
        test_listener=None,
    ) -> None:
        self.root = Path(root).resolve()
        self.full_access = full_access
        self.allowed_commands = allowed_commands or self.DEFAULT_ALLOWED
        self.project_name = project_name or "__workspace__"
        self.terminal_listener = terminal_listener
        self.test_listener = test_listener
        self.shell_executable = os.environ.get("SHELL", "/bin/zsh")

    def run_terminal(self, command: str, timeout: int = 120, workdir: str = ".") -> dict[str, object]:
        if not command.strip():
            raise ValueError("Command cannot be empty.")
        resolved_workdir = self._resolve_workdir(workdir)
        if self.full_access:
            return self._run_full_access_command(command=command, timeout=timeout, workdir=resolved_workdir)
        if any(fragment in command for fragment in self.BLOCKED_FRAGMENTS):
            raise PermissionError("Command contains blocked shell control operators.")

        parts = shlex.split(command)
        executable = parts[0]
        executable_name = Path(executable).name if "/" in executable else executable
        normalized_executable = self._normalize_executable_name(executable_name)
        if normalized_executable not in self.allowed_commands:
            raise PermissionError(f"Command '{executable}' is not allowed in the sandbox.")

        try:
            result = subprocess.run(
                parts,
                cwd=resolved_workdir,
                capture_output=True,
                text=True,
                timeout=min(timeout, 600),
                check=False,
            )
        except FileNotFoundError as exc:
            return {
                "command": command,
                "workdir": str(resolved_workdir),
                "exit_code": 127,
                "stdout": "",
                "stderr": str(exc),
            }
        payload = {
            "command": command,
            "workdir": str(resolved_workdir),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if self.terminal_listener is not None:
            self.terminal_listener.command_executed(
                self.project_name,
                command=command,
                result=payload,
                workdir=str(resolved_workdir),
            )
        return payload

    def _run_full_access_command(self, command: str, timeout: int, workdir: Path) -> dict[str, object]:
        try:
            result = subprocess.run(
                [self.shell_executable, "-lc", command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=min(timeout, 3600),
                check=False,
            )
        except FileNotFoundError as exc:
            return {
                "command": command,
                "workdir": str(workdir),
                "exit_code": 127,
                "stdout": "",
                "stderr": str(exc),
            }
        payload = {
            "command": command,
            "workdir": str(workdir),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if self.terminal_listener is not None:
            self.terminal_listener.command_executed(
                self.project_name,
                command=command,
                result=payload,
                workdir=str(workdir),
            )
        return payload

    def run_tests(self, workdir: str = ".", timeout: int = 1200, python_bin: str | None = None) -> dict[str, object]:
        resolved_workdir = self._resolve_workdir(workdir)
        commands = self._detect_test_commands(resolved_workdir, python_bin=python_bin)
        if not commands:
            payload = {
                "project_root": str(resolved_workdir),
                "commands": [],
                "results": [],
                "passed": True,
                "found": False,
                "message": "No supported test command detected.",
            }
            if self.test_listener is not None:
                self.test_listener.tests_ran(self.project_name, payload)
            return payload

        results: list[dict[str, object]] = []
        passed = True
        for command in commands:
            result = self.run_terminal(command=command, timeout=timeout, workdir=str(resolved_workdir))
            results.append(result)
            if int(result.get("exit_code", 1)) != 0:
                passed = False

        payload = {
            "project_root": str(resolved_workdir),
            "commands": commands,
            "results": results,
            "passed": passed,
            "found": True,
            "message": "Tests completed." if passed else "One or more test commands failed.",
        }
        if self.test_listener is not None:
            self.test_listener.tests_ran(self.project_name, payload)
        return payload

    def _resolve_workdir(self, workdir: str) -> Path:
        candidate = Path(workdir)
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        if self.full_access:
            return resolved
        if not self._is_relative_to(resolved, self.root):
            raise PermissionError(f"Working directory '{workdir}' is outside of the workspace root.")
        return resolved

    def _detect_test_commands(self, root: Path, python_bin: str | None = None) -> list[str]:
        commands: list[str] = []

        has_python = any(root.rglob("*.py"))
        if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or has_python:
            command = self._resolve_pytest_command(preferred_python=python_bin)
            if command:
                commands.append(command)
        if (root / "package.json").exists():
            commands.append("npm test")
        if (root / "Cargo.toml").exists():
            commands.append("cargo test")
        if (root / "go.mod").exists():
            commands.append("go test ./...")

        deduped: list[str] = []
        seen: set[str] = set()
        for command in commands:
            if command in seen:
                continue
            deduped.append(command)
            seen.add(command)
        return deduped

    def _resolve_pytest_command(self, preferred_python: str | None = None) -> str | None:
        candidates: list[str] = []
        if preferred_python and preferred_python not in candidates and self._python_has_pytest(preferred_python):
            candidates.append(preferred_python)
        for executable in [sys.executable, shutil.which("python3"), shutil.which("python")]:
            if executable and executable not in candidates and self._python_has_pytest(executable):
                candidates.append(executable)
        if candidates:
            return f"{candidates[0]} -m pytest"

        pytest_executable = shutil.which("pytest")
        if pytest_executable:
            return pytest_executable
        return None

    def _python_has_pytest(self, executable: str) -> bool:
        result = subprocess.run(
            [executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0

    def _normalize_executable_name(self, executable_name: str) -> str:
        if executable_name.startswith("python"):
            return "python3" if executable_name.startswith("python3") else "python"
        if executable_name.startswith("pip"):
            return "pip3" if executable_name.startswith("pip3") else "pip"
        return executable_name

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
