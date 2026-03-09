from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Iterable

try:  # pragma: no cover - dependency availability is environment-specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from boss.types import ProjectReference, WorkspaceRoot


class WorkspaceRootsRegistry:
    DISCOVERY_CACHE_TTL_SECONDS = 20.0
    DEFAULT_ROOTS = [
        {
            "name": "home",
            "path": str(Path.home()),
            "mode": "both",
            "include_root": False,
            "discover_children": True,
            "max_depth": 2,
            "enabled": True,
        },
        {
            "name": "boss_projects",
            "path": str((Path(__file__).resolve().parents[2] / "projects").resolve()),
            "mode": "projects",
            "include_root": False,
            "discover_children": True,
            "max_depth": 1,
            "enabled": True,
        },
    ]
    PROJECT_MARKERS = {".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "setup.py", "requirements.txt"}

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).resolve()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._project_cache: tuple[float, list[ProjectReference], float] | None = None
        self._ensure_config()
        self._ensure_operator_defaults()

    def list_roots(self) -> list[WorkspaceRoot]:
        raw = self._load_raw()
        roots: list[WorkspaceRoot] = []
        for item in raw.get("roots", []) or []:
            roots.append(
                WorkspaceRoot(
                    name=str(item.get("name", "")).strip(),
                    path=str(item.get("path", "")).strip(),
                    mode=str(item.get("mode", "search")).strip().lower() or "search",
                    include_root=bool(item.get("include_root", False)),
                    discover_children=bool(item.get("discover_children", False)),
                    max_depth=max(0, int(item.get("max_depth", 1) or 1)),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        return [root for root in roots if root.name and root.path]

    def search_roots(self) -> list[Path]:
        return [Path(root.path).expanduser().resolve() for root in self.list_roots() if root.enabled and root.mode in {"search", "both"}]

    def project_roots(self) -> list[WorkspaceRoot]:
        return [root for root in self.list_roots() if root.enabled and root.mode in {"projects", "both"}]

    def primary_search_root(self) -> Path:
        roots = self.search_roots()
        if roots:
            return roots[0]
        return Path.home().resolve()

    def discover_projects(self) -> list[ProjectReference]:
        cached = self._project_cache
        config_mtime = self.config_path.stat().st_mtime if self.config_path.exists() else 0.0
        now = time.time()
        if cached and cached[2] == config_mtime and now - cached[0] < self.DISCOVERY_CACHE_TTL_SECONDS:
            return list(cached[1])

        candidates: list[tuple[WorkspaceRoot, Path, str]] = []
        for root in self.project_roots():
            base = Path(root.path).expanduser().resolve()
            if not base.exists():
                continue
            if root.include_root and self._looks_like_project(base):
                candidates.append((root, base, "."))
            if not root.discover_children:
                continue
            for candidate in self._discover_children(base, root.max_depth):
                relative = self._relative_display(candidate, base)
                candidates.append((root, candidate, relative))
        references = self._build_references(candidates)
        self._project_cache = (now, list(references), config_mtime)
        return references

    def resolve(self, identifier: str) -> ProjectReference:
        text = str(identifier).strip()
        if not text:
            raise ValueError("Project identifier is required.")

        candidate_path = Path(text).expanduser()
        if candidate_path.is_absolute() and candidate_path.exists():
            return self._ad_hoc_reference(candidate_path.resolve())

        projects = self.discover_projects()
        exact_key = [project for project in projects if project.key == text]
        if exact_key:
            return exact_key[0]

        lowered = text.lower()
        exact_name = [project for project in projects if project.name.lower() == lowered]
        if len(exact_name) == 1:
            return exact_name[0]
        exact_display = [project for project in projects if project.display_name.lower() == lowered]
        if len(exact_display) == 1:
            return exact_display[0]
        basename = [project for project in projects if Path(project.root).name.lower() == lowered]
        if len(basename) == 1:
            return basename[0]
        if len(exact_name) > 1 or len(exact_display) > 1 or len(basename) > 1:
            choices = ", ".join(sorted({project.display_name or project.key for project in (exact_name or exact_display or basename)})[:8])
            raise ValueError(f"Project identifier '{identifier}' is ambiguous. Matches: {choices}")
        raise FileNotFoundError(f"Unknown project '{identifier}'. Add a project root or provide an absolute path.")

    def add_root(
        self,
        *,
        name: str,
        path: str | Path,
        mode: str = "projects",
        include_root: bool = False,
        discover_children: bool = True,
        max_depth: int = 1,
    ) -> WorkspaceRoot:
        roots = self._load_raw().get("roots", []) or []
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("Root name is required.")
        if any(str(item.get("name", "")).strip() == normalized_name for item in roots):
            raise ValueError(f"Workspace root '{normalized_name}' already exists.")
        entry = {
            "name": normalized_name,
            "path": str(Path(path).expanduser().resolve()),
            "mode": str(mode).strip().lower() or "projects",
            "include_root": bool(include_root),
            "discover_children": bool(discover_children),
            "max_depth": max(0, int(max_depth)),
            "enabled": True,
        }
        roots.append(entry)
        self._write_raw({"roots": roots})
        self._invalidate_cache()
        return WorkspaceRoot(**entry)

    def remove_root(self, name: str) -> bool:
        roots = self._load_raw().get("roots", []) or []
        updated = [item for item in roots if str(item.get("name", "")).strip() != str(name).strip()]
        if len(updated) == len(roots):
            return False
        self._write_raw({"roots": updated})
        self._invalidate_cache()
        return True

    def describe(self) -> list[dict[str, object]]:
        roots = []
        for root in self.list_roots():
            roots.append(
                {
                    "name": root.name,
                    "path": str(Path(root.path).expanduser().resolve()),
                    "mode": root.mode,
                    "enabled": root.enabled,
                    "include_root": root.include_root,
                    "discover_children": root.discover_children,
                    "max_depth": root.max_depth,
                }
            )
        return roots

    def _build_references(self, candidates: Iterable[tuple[WorkspaceRoot, Path, str]]) -> list[ProjectReference]:
        materialized = list(candidates)
        basename_counts: dict[str, int] = {}
        for _root, path, _relative in materialized:
            basename = path.name.strip().lower()
            basename_counts[basename] = basename_counts.get(basename, 0) + 1

        references: list[ProjectReference] = []
        used_keys: set[str] = set()
        for root, path, relative in materialized:
            name = path.name
            base_key = self._slug(name)
            key = base_key if basename_counts.get(name.lower(), 0) == 1 else f"{base_key}@{self._slug(root.name)}"
            if key in used_keys:
                suffix = self._slug(relative.replace("/", "-"))
                key = f"{key}-{suffix or self._fingerprint(str(path))[:6]}"
            used_keys.add(key)
            display_name = name if basename_counts.get(name.lower(), 0) == 1 else f"{name} ({root.name})"
            references.append(
                ProjectReference(
                    key=key,
                    name=name,
                    root=str(path),
                    source_root=root.name,
                    relative_path=relative,
                    display_name=display_name,
                    mode="registered",
                )
            )
        references.sort(key=lambda item: item.display_name.lower())
        return references

    def _ad_hoc_reference(self, path: Path) -> ProjectReference:
        return ProjectReference(
            key=f"path:{self._fingerprint(str(path))[:12]}",
            name=path.name,
            root=str(path),
            source_root="path",
            relative_path=".",
            display_name=f"{path.name} (path)",
            mode="ad_hoc",
        )

    def _discover_children(self, root: Path, max_depth: int) -> list[Path]:
        matches: list[Path] = []
        queue: list[tuple[Path, int]] = [(root, 0)]
        seen: set[Path] = set()
        while queue:
            current, depth = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if depth >= max_depth:
                continue
            try:
                children = [path for path in current.iterdir() if path.is_dir() and not path.name.startswith(".")]
            except OSError:
                continue
            for child in children:
                if self._looks_like_project(child):
                    matches.append(child)
                elif depth + 1 < max_depth:
                    queue.append((child, depth + 1))
        return matches

    def _looks_like_project(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        for marker in self.PROJECT_MARKERS:
            if (path / marker).exists():
                return True
        try:
            sample_files = 0
            for child in path.iterdir():
                if child.is_file() and child.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".swift", ".cpp", ".cc"}:
                    sample_files += 1
                if sample_files >= 2:
                    return True
        except OSError:
            return False
        return False

    def _relative_display(self, path: Path, root: Path) -> str:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return "."
        return "." if str(relative) == "." else str(relative)

    def _fingerprint(self, value: str) -> str:
        return hashlib.sha1(value.encode("utf-8")).hexdigest()

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "project"

    def _ensure_config(self) -> None:
        if self.config_path.exists():
            return
        self._write_raw({"roots": list(self.DEFAULT_ROOTS)})

    def _ensure_operator_defaults(self) -> None:
        raw = self._load_raw()
        roots = raw.get("roots", []) or []
        changed = False
        for item in roots:
            name = str(item.get("name", "")).strip().lower()
            path = str(item.get("path", "")).strip()
            if name == "home" or Path(path).expanduser().resolve() == Path.home().resolve():
                if str(item.get("mode", "search")).strip().lower() == "search":
                    item["mode"] = "both"
                    changed = True
                if not bool(item.get("discover_children", False)):
                    item["discover_children"] = True
                    changed = True
                if int(item.get("max_depth", 1) or 1) < 2:
                    item["max_depth"] = 2
                    changed = True
        if changed:
            self._write_raw({"roots": roots})
            self._invalidate_cache()

    def _load_raw(self) -> dict:
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for workspace root configuration.")
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if "roots" not in raw:
            raw["roots"] = list(self.DEFAULT_ROOTS)
        return raw

    def _write_raw(self, payload: dict) -> None:
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for workspace root configuration.")
        self.config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def _invalidate_cache(self) -> None:
        self._project_cache = None
