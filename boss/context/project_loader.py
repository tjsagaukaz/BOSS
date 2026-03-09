from __future__ import annotations

from pathlib import Path
from typing import Callable

from boss.context.editor_state import EditorStateStore
from boss.context.project_indexer import ProjectIndexer
from boss.memory.context_retriever import ContextRetriever
from boss.memory.memory_store import MemoryStore
from boss.memory.vector_index import VectorIndex
from boss.project_brain import ProjectBrainStore
from boss.types import IndexedFile, ProjectContext, ProjectReference
from boss.workspace.git_listener import GitListener
from boss.workspace.workspace_state import WorkspaceStateStore


class ProjectLoader:
    def __init__(
        self,
        project_resolver: Callable[[str], ProjectReference],
        project_discovery: Callable[[], list[ProjectReference]],
        memory_store: MemoryStore,
        vector_index: VectorIndex,
        project_indexer: ProjectIndexer,
        editor_state: EditorStateStore,
        context_retriever: ContextRetriever,
        workspace_state: WorkspaceStateStore,
        git_listener: GitListener,
        project_brain: ProjectBrainStore,
    ) -> None:
        self.project_resolver = project_resolver
        self.project_discovery = project_discovery
        self.memory_store = memory_store
        self.vector_index = vector_index
        self.project_indexer = project_indexer
        self.editor_state = editor_state
        self.context_retriever = context_retriever
        self.workspace_state = workspace_state
        self.git_listener = git_listener
        self.project_brain = project_brain
    def discover_projects(self) -> list[str]:
        return [project.key for project in self.project_discovery()]

    def scan_project(self, project_name: str) -> ProjectContext:
        self.project_indexer.index_project(project_name=project_name, force=False)
        return self.load_project(project_name=project_name, auto_index=False)

    def load_project(
        self,
        project_name: str,
        task_hint: str | None = None,
        auto_index: bool = True,
    ) -> ProjectContext:
        reference = self.project_resolver(project_name)
        project_name = reference.key
        project_root = Path(reference.root).resolve()
        self.workspace_state.set_active_project(project_name)
        self.git_listener.capture_repository_state(project_name, project_root)
        project = self.memory_store.get_project(project_name)
        project_map = self.memory_store.get_project_map(project_name)

        if auto_index and (project is None or project_map is None):
            self.project_indexer.index_project(project_name=project_name, force=False)
            project = self.memory_store.get_project(project_name)
            project_map = self.memory_store.get_project_map(project_name)

        if project is None:
            self.project_indexer.index_project(project_name=project_name, force=False)
            project = self.memory_store.get_project(project_name)
            project_map = self.memory_store.get_project_map(project_name)

        if project is None:
            raise RuntimeError(f"Unable to load project metadata for '{project_name}'.")

        memory_entries = self.memory_store.list_memory_entries(project_name, limit=10)
        code_summaries = self.memory_store.list_code_summaries(project_name, limit=12)
        architecture_entries = self.memory_store.list_memory_entries(project_name, limit=5, category="architecture")
        semantic_results = (
            self.vector_index.semantic_search(query=task_hint, project_name=project_name, limit=8) if task_hint else []
        )
        relevant_files = self._select_relevant_files(project_name, semantic_results)
        relevant_memories = self.memory_store.semantic_search(project_name, task_hint, limit=5) if task_hint else []

        architecture_notes = [entry.content for entry in architecture_entries]
        if project_map and project_map.overview not in architecture_notes:
            architecture_notes.insert(0, project_map.overview)

        metadata = project.get("metadata") or {}
        if isinstance(metadata, str):
            import json

            metadata = json.loads(metadata or "{}")

        editor_state = self.editor_state.get_project_state(project_name)
        workspace_snapshot = self.workspace_state.snapshot(project_name)
        persistent_context = self.context_retriever.retrieve(project_name, task_hint=task_hint, limit=6)
        brain = self.project_brain.load(
            project_name,
            summary=project["summary"],
            project_map=project_map,
        )

        return ProjectContext(
            name=project_name,
            root=project_root,
            summary=project["summary"],
            file_count=int(metadata.get("file_count", 0)),
            languages={k: int(v) for k, v in metadata.get("languages", {}).items()},
            important_files=list(metadata.get("important_files", [])),
            architecture_notes=architecture_notes,
            memory_entries=memory_entries,
            code_summaries=code_summaries,
            project_map=project_map,
            relevant_files=relevant_files,
            semantic_results=semantic_results,
            relevant_memories=relevant_memories,
            active_file=workspace_snapshot.open_files[0] if workspace_snapshot.open_files else editor_state.get("active_file"),
            recent_files=workspace_snapshot.open_files[:10] or list(editor_state.get("recent_files", [])[:10]),
            recent_changes=workspace_snapshot.recent_edits[:8] or list(editor_state.get("recent_changes", [])[:8]),
            recent_searches=list(editor_state.get("recent_searches", [])[:5]),
            workspace_state=workspace_snapshot,
            project_profile=persistent_context.get("project_profile"),
            project_brain=brain,
            style_profile=persistent_context.get("style_profile"),
            relevant_solutions=list(persistent_context.get("relevant_solutions", [])),
            similar_tasks=list(persistent_context.get("similar_tasks", [])),
            knowledge_nodes=list(persistent_context.get("knowledge_nodes", [])),
            knowledge_edges=list(persistent_context.get("knowledge_edges", [])),
            graph_insights=list(persistent_context.get("graph_insights", [])),
            related_projects=list(persistent_context.get("related_projects", [])),
        )

    def _project_root(self, project_name: str) -> Path:
        reference = self.project_resolver(project_name)
        project_root = Path(reference.root).resolve()
        if not project_root.exists():
            raise FileNotFoundError(f"Project '{reference.key}' does not exist at {project_root}.")
        return project_root

    def _select_relevant_files(self, project_name: str, semantic_results: list[dict[str, object]]) -> list[IndexedFile]:
        file_paths: list[str] = []
        for result in semantic_results:
            metadata = result.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            file_path = metadata.get("file_path")
            if not isinstance(file_path, str) or file_path == "__project_map__":
                continue
            file_paths.append(file_path)

        relevant: list[IndexedFile] = []
        seen: set[str] = set()
        for file_path in file_paths:
            if file_path in seen:
                continue
            entry = self.memory_store.get_indexed_file(project_name, file_path)
            if entry is None:
                continue
            relevant.append(entry)
            seen.add(file_path)

        if relevant:
            return relevant[:8]
        return self.memory_store.list_indexed_files(project_name, limit=8)
