from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class VSCodeController:
    def __init__(self, code_binary: str = "code") -> None:
        self.code_binary = code_binary

    def is_available(self) -> bool:
        return shutil.which(self.code_binary) is not None

    def open_file(self, path: str | Path, line: int | None = None, column: int = 1) -> dict[str, object]:
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"File not found: {target}")
        if self.is_available():
            command = [self.code_binary, "--reuse-window"]
            if line is not None:
                command.extend(["--goto", f"{target}:{line}:{column}"])
            else:
                command.append(str(target))
        else:
            command = ["open", "-a", "Visual Studio Code", str(target)]
        return self._run(command)

    def reveal_file(self, path: str | Path) -> dict[str, object]:
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"File not found: {target}")
        command = [self.code_binary, "--reuse-window", str(target)] if self.is_available() else ["open", "-a", "Visual Studio Code", str(target)]
        return self._run(command)

    def open_workspace(self, project_path: str | Path) -> dict[str, object]:
        target = Path(project_path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Workspace not found: {target}")
        command = [self.code_binary, "--reuse-window", str(target)] if self.is_available() else ["open", "-a", "Visual Studio Code", str(target)]
        return self._run(command)

    def focus_editor(self) -> dict[str, object]:
        result = subprocess.run(
            ["open", "-a", "Visual Studio Code"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "command": "open -a Visual Studio Code",
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def _run(self, command: list[str]) -> dict[str, object]:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return {
            "command": " ".join(command),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
