from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass
class ProjectSandbox:
    source_project_name: str
    sandbox_project_name: str
    sandbox_root: Path
    sandbox_mode: str = "copy"
    branch_name: str | None = None
    git_root: Path | None = None
    base_revision: str | None = None
    is_git_repo: bool = False


class ProjectSandboxManager:
    IGNORE_NAMES = {
        "node_modules",
        "dist",
        "build",
        ".venv",
        ".boss_benchmark_venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }

    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def create_sandbox(self, project_name: str, label: str, mode: str = "auto") -> ProjectSandbox:
        normalized = mode.strip().lower() or "auto"
        if normalized == "copy":
            return self.create_copy(project_name, label)
        if normalized == "worktree":
            return self.create_worktree(project_name, label)
        if normalized != "auto":
            raise ValueError(f"Unsupported sandbox mode '{mode}'.")

        try:
            return self.create_worktree(project_name, label)
        except RuntimeError:
            return self.create_copy(project_name, label)

    def create_copy(self, project_name: str, label: str) -> ProjectSandbox:
        source_root = (self.projects_root / project_name).resolve()
        if not source_root.exists():
            raise FileNotFoundError(f"Project '{project_name}' does not exist in {self.projects_root}.")

        sandbox_project_name = self._sandbox_name(project_name, label)
        sandbox_root = self.projects_root / sandbox_project_name
        if sandbox_root.exists():
            shutil.rmtree(sandbox_root)
        shutil.copytree(source_root, sandbox_root, ignore=self._ignore)

        branch_name = None
        is_git_repo = (sandbox_root / ".git").exists()
        if is_git_repo:
            branch_name = f"boss-eval-{self._slug(label)}"[:48]
            try:
                subprocess.run(
                    ["git", "checkout", "-b", branch_name],
                    cwd=sandbox_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                branch_name = None

        return ProjectSandbox(
            source_project_name=project_name,
            sandbox_project_name=sandbox_project_name,
            sandbox_root=sandbox_root,
            sandbox_mode="copy",
            branch_name=branch_name,
            is_git_repo=is_git_repo,
        )

    def create_worktree(self, project_name: str, label: str) -> ProjectSandbox:
        source_root = (self.projects_root / project_name).resolve()
        if not source_root.exists():
            raise FileNotFoundError(f"Project '{project_name}' does not exist in {self.projects_root}.")

        git_root = self._git_toplevel(source_root)
        if git_root is None or git_root != source_root:
            raise RuntimeError(f"Project '{project_name}' is not a git repo rooted at {source_root}.")
        if not self._is_clean_git_repo(git_root):
            raise RuntimeError(f"Project '{project_name}' has uncommitted changes; refusing to branch a worktree from a dirty repo.")

        sandbox_project_name = self._sandbox_name(project_name, label)
        sandbox_root = self.projects_root / sandbox_project_name
        if sandbox_root.exists():
            shutil.rmtree(sandbox_root)

        branch_name = self._branch_name(project_name, label)
        base_revision = self._git_stdout(git_root, ["rev-parse", "HEAD"])
        if not base_revision:
            raise RuntimeError(f"Unable to resolve HEAD for project '{project_name}'.")

        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(sandbox_root), base_revision],
            cwd=git_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to create git worktree.")

        return ProjectSandbox(
            source_project_name=project_name,
            sandbox_project_name=sandbox_project_name,
            sandbox_root=sandbox_root,
            sandbox_mode="worktree",
            branch_name=branch_name,
            git_root=git_root,
            base_revision=base_revision,
            is_git_repo=True,
        )

    def cleanup(self, sandbox: ProjectSandbox) -> None:
        if sandbox.sandbox_mode == "worktree" and sandbox.git_root is not None:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(sandbox.sandbox_root)],
                cwd=sandbox.git_root,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=sandbox.git_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if sandbox.branch_name:
                subprocess.run(
                    ["git", "branch", "-D", sandbox.branch_name],
                    cwd=sandbox.git_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
        if sandbox.sandbox_root.exists():
            shutil.rmtree(sandbox.sandbox_root)

    def _ignore(self, _directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in self.IGNORE_NAMES}

    def _sandbox_name(self, project_name: str, label: str) -> str:
        return f"__eval__{self._slug(project_name)}__{self._slug(label)}"

    def _branch_name(self, project_name: str, label: str) -> str:
        suffix = uuid4().hex[:8]
        return f"boss-lab-{self._slug(project_name)}-{self._slug(label)}-{suffix}"[:63]

    def _slug(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return cleaned[:40] or "run"

    def _git_toplevel(self, root: Path) -> Path | None:
        output = self._git_stdout(root, ["rev-parse", "--show-toplevel"])
        if not output:
            return None
        return Path(output).resolve()

    def _is_clean_git_repo(self, root: Path) -> bool:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return not status.stdout.strip()

    def _git_stdout(self, root: Path, args: list[str]) -> str | None:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        return output or None
