from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any

try:  # pragma: no cover - dependency import is environment specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class ExternalRepoSpec:
    name: str
    project_name: str
    git_url: str
    reference: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalRepoCatalog:
    name: str
    path: str
    description: str = ""
    repos: list[ExternalRepoSpec] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_external_repo_catalog(path: str | Path) -> ExternalRepoCatalog:
    catalog_path = Path(path).expanduser().resolve()
    if not catalog_path.exists():
        raise FileNotFoundError(f"External repo catalog not found: {catalog_path}")
    if yaml is None:
        raise RuntimeError("PyYAML is required to load external repo catalogs.")
    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("External repo catalog must be a mapping with a top-level 'repos' key.")
    repos_raw = raw.get("repos")
    if not isinstance(repos_raw, list) or not repos_raw:
        raise ValueError("External repo catalog must define a non-empty 'repos' list.")
    catalog = ExternalRepoCatalog(
        name=str(raw.get("name", catalog_path.stem)).strip() or catalog_path.stem,
        path=str(catalog_path),
        description=str(raw.get("description", "")).strip(),
        metadata=_extra_metadata(raw, {"name", "description", "repos"}),
    )
    for item in repos_raw:
        if not isinstance(item, dict):
            raise ValueError("Each external repo entry must be a mapping.")
        catalog.repos.append(
            ExternalRepoSpec(
                name=str(item.get("name", "")).strip(),
                project_name=str(item.get("project_name", "")).strip(),
                git_url=str(item.get("git_url", "")).strip(),
                reference=_optional_string(item.get("reference") or item.get("branch") or item.get("tag")),
                tags=_as_list(item.get("tags")),
                notes=str(item.get("notes", "")).strip(),
                metadata=_extra_metadata(item, {"name", "project_name", "git_url", "reference", "branch", "tag", "tags", "notes"}),
            )
        )
    for repo in catalog.repos:
        if not repo.name or not repo.project_name or not repo.git_url:
            raise ValueError("External repo entries require name, project_name, and git_url.")
    return catalog


class ExternalRepoSync:
    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def sync(
        self,
        catalog_path: str | Path,
        only_repos: list[str] | None = None,
        update: bool = False,
    ) -> dict[str, Any]:
        catalog = load_external_repo_catalog(catalog_path)
        selected = {item.strip() for item in (only_repos or []) if item.strip()}
        results: list[dict[str, Any]] = []
        matched: set[str] = set()

        for repo in catalog.repos:
            if selected and repo.name not in selected and repo.project_name not in selected:
                continue
            matched.add(repo.name)
            matched.add(repo.project_name)
            results.append(self._sync_repo(repo, update=update))

        if selected:
            missing = selected - matched
            if missing:
                raise RuntimeError(f"External repos not found in catalog: {', '.join(sorted(missing))}")

        return {
            "name": catalog.name,
            "path": catalog.path,
            "description": catalog.description,
            "results": results,
        }

    def _sync_repo(self, repo: ExternalRepoSpec, *, update: bool) -> dict[str, Any]:
        destination = self.projects_root / repo.project_name
        if destination.exists() and (destination / ".git").exists():
            if update:
                self._git(destination, ["fetch", "--depth", "1", "origin", repo.reference or "HEAD"])
                if repo.reference:
                    self._git(destination, ["checkout", repo.reference])
                self._ensure_local_identity(destination)
                return {
                    "name": repo.name,
                    "project_name": repo.project_name,
                    "path": str(destination),
                    "status": "updated",
                    "reference": repo.reference,
                }
            return {
                "name": repo.name,
                "project_name": repo.project_name,
                "path": str(destination),
                "status": "existing",
                "reference": repo.reference,
            }

        clone_args = ["clone", "--depth", "1"]
        if repo.reference:
            clone_args.extend(["--branch", repo.reference, "--single-branch"])
        clone_args.extend([repo.git_url, str(destination)])
        self._git(self.projects_root, clone_args)
        self._ensure_local_identity(destination)
        return {
            "name": repo.name,
            "project_name": repo.project_name,
            "path": str(destination),
            "status": "cloned",
            "reference": repo.reference,
        }

    def _ensure_local_identity(self, repo_root: Path) -> None:
        self._git(repo_root, ["config", "user.email", "boss@example.com"])
        self._git(repo_root, ["config", "user.name", "BOSS"])

    def _git(self, cwd: Path, args: list[str]) -> None:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
            raise RuntimeError(message)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def _extra_metadata(raw: dict[str, Any], known_keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in known_keys}
