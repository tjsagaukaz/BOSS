from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from boss.types import utc_now_iso


class EditorStateStore:
    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def get_project_state(self, project_name: str) -> dict[str, Any]:
        projects = self._state.setdefault("projects", {})
        return projects.setdefault(
            project_name,
            {
                "active_file": None,
                "recent_files": [],
                "recent_changes": [],
                "recent_searches": [],
                "file_metadata": {},
            },
        )

    def active_file(self, project_name: str) -> str | None:
        return self.get_project_state(project_name).get("active_file")

    def recent_files(self, project_name: str, limit: int = 10) -> list[str]:
        return list(self.get_project_state(project_name).get("recent_files", [])[:limit])

    def recent_changes(self, project_name: str, limit: int = 10) -> list[dict[str, Any]]:
        return list(self.get_project_state(project_name).get("recent_changes", [])[:limit])

    def recent_searches(self, project_name: str, limit: int = 5) -> list[dict[str, Any]]:
        return list(self.get_project_state(project_name).get("recent_searches", [])[:limit])

    def set_active_file(self, project_name: str, file_path: str) -> None:
        state = self.get_project_state(project_name)
        state["active_file"] = file_path
        self._push_unique(state["recent_files"], file_path, max_items=20)
        self._save()

    def record_change(
        self,
        project_name: str,
        file_path: str,
        change_type: str,
        summary: str = "",
        diff_preview: str = "",
    ) -> None:
        state = self.get_project_state(project_name)
        self._push_unique(state["recent_files"], file_path, max_items=20)
        state["active_file"] = file_path
        change = {
            "file": file_path,
            "type": change_type,
            "summary": summary[:300],
            "diff_preview": diff_preview[:1000],
            "timestamp": utc_now_iso(),
        }
        state["recent_changes"] = [change] + [item for item in state["recent_changes"] if item.get("file") != file_path]
        state["recent_changes"] = state["recent_changes"][:20]
        self._save()

    def cache_search(self, project_name: str, query: str, results: list[dict[str, Any]]) -> None:
        state = self.get_project_state(project_name)
        payload = {
            "query": query,
            "results": results[:10],
            "timestamp": utc_now_iso(),
        }
        state["recent_searches"] = [payload] + [item for item in state["recent_searches"] if item.get("query") != query]
        state["recent_searches"] = state["recent_searches"][:20]
        self._save()

    def get_cached_search(self, project_name: str, query: str) -> list[dict[str, Any]] | None:
        for item in self.get_project_state(project_name).get("recent_searches", []):
            if item.get("query") == query:
                return list(item.get("results", []))
        return None

    def get_file_metadata(self, project_name: str, file_path: str) -> dict[str, Any] | None:
        return self.get_project_state(project_name).get("file_metadata", {}).get(file_path)

    def set_file_metadata(self, project_name: str, file_path: str, metadata: dict[str, Any]) -> None:
        state = self.get_project_state(project_name)
        state.setdefault("file_metadata", {})[file_path] = metadata
        self._save()

    def delete_project_state(self, project_name: str) -> None:
        projects = self._state.setdefault("projects", {})
        if project_name in projects:
            del projects[project_name]
            self._save()

    def _push_unique(self, items: list[str], value: str, max_items: int) -> None:
        deduped = [item for item in items if item != value]
        items[:] = [value] + deduped[: max_items - 1]

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"projects": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"projects": {}}

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
