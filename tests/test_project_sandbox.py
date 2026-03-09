from __future__ import annotations

import subprocess

from boss.eval.project_sandbox import ProjectSandboxManager


def test_project_sandbox_manager_copies_project_and_ignores_build_artifacts(tmp_path):
    projects_root = tmp_path / "projects"
    source_root = projects_root / "demo"
    (source_root / "src").mkdir(parents=True)
    (source_root / "node_modules").mkdir()
    (source_root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source_root / "node_modules" / "skip.js").write_text("ignored\n", encoding="utf-8")

    manager = ProjectSandboxManager(projects_root)
    sandbox = manager.create_copy("demo", "build auth")

    assert sandbox.sandbox_project_name.startswith("__eval__demo__")
    assert (sandbox.sandbox_root / "src" / "main.py").exists()
    assert not (sandbox.sandbox_root / "node_modules").exists()

    manager.cleanup(sandbox)
    assert not sandbox.sandbox_root.exists()


def test_project_sandbox_manager_creates_and_cleans_worktree(tmp_path):
    projects_root = tmp_path / "projects"
    source_root = projects_root / "demo"
    source_root.mkdir(parents=True)
    (source_root / "main.py").write_text("print('demo')\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.email", "boss@example.com"], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.name", "BOSS"], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "add", "."], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=source_root, capture_output=True, text=True, check=True)

    manager = ProjectSandboxManager(projects_root)
    sandbox = manager.create_sandbox("demo", "auth benchmark", mode="worktree")

    assert sandbox.sandbox_mode == "worktree"
    assert sandbox.branch_name is not None
    assert (sandbox.sandbox_root / "main.py").exists()

    manager.cleanup(sandbox)

    branch_check = subprocess.run(
        ["git", "branch", "--list", str(sandbox.branch_name)],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert branch_check.stdout.strip() == ""
    assert not sandbox.sandbox_root.exists()
