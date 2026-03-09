from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from boss.workspace.workspace_state import WorkspaceStateStore


class GitListener:
    def __init__(self, workspace_state: WorkspaceStateStore) -> None:
        self.workspace_state = workspace_state

    def capture_repository_state(self, project_name: str, project_root: str | Path) -> None:
        root = Path(project_root).resolve()
        if shutil.which("git") is None or not (root / ".git").exists():
            return
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        diff = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.workspace_state.record_git_state(
            project_name,
            diff_text=(diff.stdout.strip() or diff.stderr.strip()),
            status={
                "dirty": bool(status.stdout.strip()),
                "summary": status.stdout.strip(),
            },
        )

    def commit_recorded(self, project_name: str, project_root: str | Path, message: str, result: dict[str, object]) -> None:
        self.workspace_state.record_commit(project_name, message, result)
        self.capture_repository_state(project_name, project_root)
