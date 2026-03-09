from __future__ import annotations

import subprocess
from pathlib import Path


class GitTools:
    def __init__(self, root: str | Path, project_name: str | None = None, git_listener=None) -> None:
        self.root = Path(root).resolve()
        self.project_name = project_name or "__workspace__"
        self.git_listener = git_listener

    def git_commit(self, message: str) -> dict[str, object]:
        if not message.strip():
            raise ValueError("Commit message cannot be empty.")
        if not (self.root / ".git").exists():
            raise RuntimeError(f"{self.root} is not a git repository.")

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if self.git_listener is not None:
            self.git_listener.capture_repository_state(self.project_name, self.root)
        if not status.stdout.strip():
            payload = {"committed": False, "message": "No changes to commit."}
            if self.git_listener is not None:
                self.git_listener.commit_recorded(self.project_name, self.root, message, payload)
            return payload

        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True, text=True, check=True)
        commit = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if commit.returncode != 0:
            payload = {
                "committed": False,
                "message": commit.stderr.strip() or commit.stdout.strip(),
            }
            if self.git_listener is not None:
                self.git_listener.commit_recorded(self.project_name, self.root, message, payload)
            return payload
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = {"committed": True, "commit": rev.stdout.strip(), "message": message}
        if self.git_listener is not None:
            self.git_listener.commit_recorded(self.project_name, self.root, message, payload)
        return payload

    def git_push(self, remote: str = "origin", branch: str | None = None, set_upstream: bool = False) -> dict[str, object]:
        if not (self.root / ".git").exists():
            raise RuntimeError(f"{self.root} is not a git repository.")

        if branch is None:
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
            if branch_result.returncode != 0:
                return {"pushed": False, "message": branch_result.stderr.strip() or "Unable to determine current branch."}
            branch = branch_result.stdout.strip()

        remote_result = subprocess.run(
            ["git", "remote"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        remotes = {item.strip() for item in remote_result.stdout.splitlines() if item.strip()}
        if remote not in remotes:
            return {"pushed": False, "message": f"Remote '{remote}' is not configured."}

        command = ["git", "push"]
        if set_upstream:
            command.extend(["--set-upstream", remote, branch])
        else:
            command.extend([remote, branch])
        push = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if push.returncode != 0:
            return {
                "pushed": False,
                "remote": remote,
                "branch": branch,
                "message": push.stderr.strip() or push.stdout.strip(),
            }
        payload = {
            "pushed": True,
            "remote": remote,
            "branch": branch,
            "message": push.stdout.strip() or f"Pushed {branch} to {remote}.",
        }
        return payload
