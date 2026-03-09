from __future__ import annotations

from pathlib import Path
import subprocess

from boss.eval.external_repos import ExternalRepoSync, load_external_repo_catalog


def _create_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=path, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.email", "boss@example.com"], cwd=path, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.name", "BOSS"], cwd=path, capture_output=True, text=True, check=True)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, text=True, check=True)


def test_load_external_repo_catalog(tmp_path):
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        """
name: demo_catalog
repos:
  - name: demo
    project_name: ext_demo
    git_url: https://example.invalid/demo.git
    tags: [python]
""".strip(),
        encoding="utf-8",
    )

    catalog = load_external_repo_catalog(catalog_path)

    assert catalog.name == "demo_catalog"
    assert catalog.repos[0].name == "demo"
    assert catalog.repos[0].project_name == "ext_demo"
    assert catalog.repos[0].tags == ["python"]


def test_external_repo_sync_clones_local_repo(tmp_path):
    source_repo = tmp_path / "source_repo"
    _create_git_repo(source_repo)
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        f"""
name: demo_catalog
repos:
  - name: demo
    project_name: ext_demo
    git_url: {source_repo}
""".strip(),
        encoding="utf-8",
    )

    sync = ExternalRepoSync(tmp_path / "projects")
    result = sync.sync(catalog_path)

    assert result["results"][0]["status"] == "cloned"
    assert (tmp_path / "projects" / "ext_demo" / ".git").exists()


def test_external_repo_sync_marks_existing_repo(tmp_path):
    source_repo = tmp_path / "source_repo"
    _create_git_repo(source_repo)
    projects_root = tmp_path / "projects"
    sync = ExternalRepoSync(projects_root)
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        f"""
name: demo_catalog
repos:
  - name: demo
    project_name: ext_demo
    git_url: {source_repo}
""".strip(),
        encoding="utf-8",
    )

    first = sync.sync(catalog_path)
    second = sync.sync(catalog_path)

    assert first["results"][0]["status"] == "cloned"
    assert second["results"][0]["status"] == "existing"
