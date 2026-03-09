from __future__ import annotations

from typing import Any

from boss.types import PortfolioProject, ProjectBrain


class PortfolioManager:
    def __init__(self, roots_registry, project_brain_store, memory_store) -> None:
        self.roots_registry = roots_registry
        self.project_brain_store = project_brain_store
        self.memory_store = memory_store

    def snapshot(self, include_internal: bool = False) -> dict[str, Any]:
        projects: list[PortfolioProject] = []
        for reference in self.roots_registry.discover_projects():
            if not include_internal and self._is_internal(reference):
                continue
            stored = self.memory_store.get_project(reference.key)
            project_map = self.memory_store.get_project_map(reference.key)
            summary = str(stored["summary"]) if stored else ""
            brain = self.project_brain_store.load(reference.key, summary=summary, project_map=project_map)
            projects.append(self._portfolio_project(reference, brain))
        projects.sort(key=lambda item: (item.focus.lower(), item.display_name.lower()))
        return {
            "project_count": len(projects),
            "projects": [project.__dict__ for project in projects],
            "focuses": sorted({project.focus for project in projects if project.focus}),
            "top_priorities": [project.next_priority for project in projects if project.next_priority][:8],
        }

    def _is_internal(self, reference) -> bool:
        candidates = {
            str(reference.key).strip().lower(),
            str(reference.name).strip().lower(),
            str(reference.display_name).strip().lower(),
        }
        prefixes = ("__eval__", "eval-", "ael_", "ael-", "ext_", "ext-", "__bench__", "bench-")
        fragments = ("benchmark", "__eval__", "fixture", "sandbox")
        for candidate in candidates:
            if any(candidate.startswith(prefix) for prefix in prefixes):
                return True
            if any(fragment in candidate for fragment in fragments):
                return True
        return False

    def _portfolio_project(self, reference, brain: ProjectBrain) -> PortfolioProject:
        priorities = self.project_brain_store.effective_next_priorities(brain)
        return PortfolioProject(
            project_key=reference.key,
            display_name=reference.display_name or reference.key,
            root=reference.root,
            source_root=reference.source_root,
            mission=brain.mission,
            focus=brain.current_focus,
            next_priority=priorities[0] if priorities else "",
            updated_at=brain.updated_at,
        )
