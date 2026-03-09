from __future__ import annotations

from typing import Any

from boss.memory.knowledge_graph import KnowledgeGraph
from boss.memory.memory_store import MemoryStore
from boss.memory.project_memory import ProjectMemoryStore
from boss.memory.solution_library import SolutionLibrary
from boss.memory.style_profile import StyleProfileStore
from boss.memory.task_history import TaskHistoryStore
from boss.memory.vector_index import VectorIndex


class ContextRetriever:
    def __init__(
        self,
        memory_store: MemoryStore,
        vector_index: VectorIndex,
        task_history: TaskHistoryStore,
        knowledge_graph: KnowledgeGraph,
        project_memory: ProjectMemoryStore,
        solution_library: SolutionLibrary,
        style_profile: StyleProfileStore,
        embeddings,
    ) -> None:
        self.memory_store = memory_store
        self.vector_index = vector_index
        self.task_history = task_history
        self.knowledge_graph = knowledge_graph
        self.project_memory = project_memory
        self.solution_library = solution_library
        self.style_profile = style_profile
        self.embeddings = embeddings

    def retrieve(self, project_name: str, task_hint: str | None = None, limit: int = 5) -> dict[str, Any]:
        query = (task_hint or project_name).strip() or project_name
        project_profile = self.project_memory.get_profile(project_name)
        style = self.style_profile.get_effective_profile(project_name)
        relevant_solutions = self.solution_library.search(
            query,
            project_name=project_name,
            limit=limit,
            verified_only=True,
        )
        similar_tasks = self.task_history.search_tasks(
            query=query,
            embeddings=self.embeddings,
            project_name=project_name,
            limit=limit,
        )
        if not similar_tasks:
            similar_tasks = self.task_history.search_tasks(
                query=query,
                embeddings=self.embeddings,
                project_name=None,
                limit=limit,
            )

        graph = self.knowledge_graph.project_graph(project_name)
        graph_insights = self.knowledge_graph.project_insights(project_name, limit=8)
        related_projects = self.knowledge_graph.related_projects(project_name, limit=limit)
        related_profiles = []
        for item in related_projects:
            related_profile = self.project_memory.get_profile(item["project_name"])
            if related_profile is not None:
                related_profiles.append(related_profile)

        if task_hint:
            memory_hits = self.memory_store.semantic_search(project_name, task_hint, limit=limit)
            vector_hits = self.vector_index.semantic_search(task_hint, project_name=project_name, limit=limit)
            if not graph_insights:
                for hit in vector_hits[:3]:
                    metadata = hit.get("metadata", {})
                    label = metadata.get("file_path", hit.get("document_id", "unknown")) if isinstance(metadata, dict) else "unknown"
                    graph_insights.append(f"Related context: {label}")
            if project_profile is not None and not project_profile.coding_patterns:
                project_profile.coding_patterns = [hit["label"] for hit in memory_hits[:3] if hit.get("label")]

        return {
            "project_profile": project_profile,
            "style_profile": style,
            "relevant_solutions": relevant_solutions,
            "similar_tasks": similar_tasks,
            "knowledge_nodes": graph["nodes"],
            "knowledge_edges": graph["edges"],
            "graph_insights": graph_insights,
            "related_projects": related_profiles,
        }

    def memory_snapshot(self, project_name: str) -> dict[str, Any]:
        return {
            "project_profile": self.project_memory.get_profile(project_name),
            "style_profile": self.style_profile.get_effective_profile(project_name),
            "solutions": self.solution_library.list_solutions(project_name=project_name, limit=10),
            "graph": self.knowledge_graph.project_graph(project_name),
            "graph_insights": self.knowledge_graph.project_insights(project_name, limit=12),
            "related_projects": self.knowledge_graph.related_projects(project_name, limit=8),
            "recent_tasks": self.task_history.recent_tasks(project_name=project_name, limit=8),
        }
