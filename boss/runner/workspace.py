"""Task workspace isolation: git worktrees and temp workspaces."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from boss.config import settings


class WorkspaceStrategy(StrEnum):
    GIT_WORKTREE = "git_worktree"
    TEMP_DIRECTORY = "temp_directory"


class WorkspaceState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CLEANED_UP = "cleaned_up"


@dataclass
class TaskWorkspace:
    workspace_id: str
    strategy: str
    state: str
    source_path: str
    workspace_path: str
    task_slug: str
    branch_name: str | None
    created_at: float
    updated_at: float
    cleaned_up_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _workspaces_dir() -> Path:
    d = settings.app_data_dir / "task-workspaces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _workspace_meta_path(workspace_id: str) -> Path:
    return _workspaces_dir() / f"{workspace_id}.json"


def _save_workspace(ws: TaskWorkspace) -> None:
    path = _workspace_meta_path(ws.workspace_id)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(ws.to_dict(), indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _is_git_repo(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _git_repo_root(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _current_branch(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def create_task_workspace(
    *,
    source_path: str | Path,
    task_slug: str,
    branch_name: str | None = None,
) -> TaskWorkspace:
    """Create an isolated task workspace.

    Uses git worktree when the source is a git repo, otherwise creates
    a scoped temp directory.
    """
    source = Path(source_path).resolve()
    workspace_id = str(uuid.uuid4())
    now = time.time()

    if _is_git_repo(source):
        return _create_worktree_workspace(
            workspace_id=workspace_id,
            source=source,
            task_slug=task_slug,
            branch_name=branch_name,
            now=now,
        )
    else:
        return _create_temp_workspace(
            workspace_id=workspace_id,
            source=source,
            task_slug=task_slug,
            now=now,
        )


def _create_worktree_workspace(
    *,
    workspace_id: str,
    source: Path,
    task_slug: str,
    branch_name: str | None,
    now: float,
) -> TaskWorkspace:
    repo_root = _git_repo_root(source) or source
    base_branch = _current_branch(repo_root) or "main"
    worktree_branch = branch_name or f"boss/task-{task_slug}"

    # Create worktree directory under the Boss data directory
    worktree_parent = settings.app_data_dir / "worktrees"
    worktree_parent.mkdir(parents=True, exist_ok=True)
    worktree_path = worktree_parent / f"{task_slug}-{workspace_id[:8]}"

    try:
        # Create a new branch and worktree
        subprocess.run(
            ["git", "worktree", "add", "-b", worktree_branch, str(worktree_path), base_branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        # Branch may already exist — try without -b
        try:
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), worktree_branch],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError:
            # Fall back to temp directory strategy
            return _create_temp_workspace(
                workspace_id=workspace_id,
                source=source,
                task_slug=task_slug,
                now=now,
            )

    ws = TaskWorkspace(
        workspace_id=workspace_id,
        strategy=WorkspaceStrategy.GIT_WORKTREE.value,
        state=WorkspaceState.CREATED.value,
        source_path=str(repo_root),
        workspace_path=str(worktree_path),
        task_slug=task_slug,
        branch_name=worktree_branch,
        created_at=now,
        updated_at=now,
        metadata={
            "base_branch": base_branch,
            "repo_root": str(repo_root),
        },
    )
    _save_workspace(ws)
    return ws


def _create_temp_workspace(
    *,
    workspace_id: str,
    source: Path,
    task_slug: str,
    now: float,
) -> TaskWorkspace:
    temp_parent = settings.app_data_dir / "task-temps"
    temp_parent.mkdir(parents=True, exist_ok=True)
    workspace_path = temp_parent / f"{task_slug}-{workspace_id[:8]}"
    workspace_path.mkdir(parents=True, exist_ok=True)

    # Copy the source tree so the workspace is usable for real work.
    if source.is_dir():
        _copy_source_tree(source, workspace_path)

    ws = TaskWorkspace(
        workspace_id=workspace_id,
        strategy=WorkspaceStrategy.TEMP_DIRECTORY.value,
        state=WorkspaceState.CREATED.value,
        source_path=str(source),
        workspace_path=str(workspace_path),
        task_slug=task_slug,
        branch_name=None,
        created_at=now,
        updated_at=now,
        metadata={"copied_from": str(source)},
    )
    _save_workspace(ws)
    return ws


# Directories that should never be copied into a temp task workspace.
_COPY_IGNORE_DIRS: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".build", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    ".eggs", "*.egg-info",
}


def _copy_source_tree(source: Path, dest: Path) -> None:
    """Shallow-copy source into dest, skipping heavy/transient directories."""
    def _ignore(directory: str, entries: list[str]) -> set[str]:
        ignored: set[str] = set()
        for entry in entries:
            if entry in _COPY_IGNORE_DIRS:
                ignored.add(entry)
            elif any(entry.endswith(suffix) for suffix in (".egg-info",)):
                ignored.add(entry)
        return ignored

    shutil.copytree(source, dest, ignore=_ignore, dirs_exist_ok=True)


def load_task_workspace(workspace_id: str) -> TaskWorkspace | None:
    path = _workspace_meta_path(workspace_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TaskWorkspace(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def update_task_workspace(workspace_id: str, **updates: Any) -> TaskWorkspace | None:
    ws = load_task_workspace(workspace_id)
    if ws is None:
        return None
    for key, value in updates.items():
        if hasattr(ws, key):
            setattr(ws, key, value)
    ws.updated_at = time.time()
    _save_workspace(ws)
    return ws


def cleanup_task_workspace(workspace_id: str) -> bool:
    """Clean up a task workspace. Removes worktree or temp directory."""
    ws = load_task_workspace(workspace_id)
    if ws is None:
        return False

    workspace_path = Path(ws.workspace_path)

    if ws.strategy == WorkspaceStrategy.GIT_WORKTREE.value:
        # Remove the git worktree
        source = Path(ws.source_path)
        try:
            subprocess.run(
                ["git", "worktree", "remove", str(workspace_path), "--force"],
                cwd=str(source),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    # Remove the directory if it still exists
    if workspace_path.exists():
        try:
            shutil.rmtree(workspace_path)
        except OSError:
            pass

    ws.state = WorkspaceState.CLEANED_UP.value
    ws.cleaned_up_at = time.time()
    ws.updated_at = time.time()
    _save_workspace(ws)
    return True


def list_task_workspaces(
    *,
    state: str | None = None,
    limit: int = 50,
) -> list[TaskWorkspace]:
    """List task workspaces, optionally filtered by state."""
    workspaces: list[TaskWorkspace] = []
    ws_dir = _workspaces_dir()

    for meta_path in sorted(ws_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(workspaces) >= limit:
            break
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            ws = TaskWorkspace(**data)
            if state is None or ws.state == state:
                workspaces.append(ws)
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    return workspaces
