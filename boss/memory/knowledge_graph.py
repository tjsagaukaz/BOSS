from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from boss.types import KnowledgeEdge, KnowledgeNode, ProjectMap, ProjectMemoryProfile, SolutionEntry


class KnowledgeGraph:
    PROJECT_RELATIONSHIPS = {"depends_on", "uses_framework", "follows_pattern"}

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert_node(
        self,
        node_type: str,
        name: str,
        project_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeNode:
        project_key = project_name or ""
        payload = json.dumps(metadata or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_nodes (project_name, node_type, name, metadata, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name, node_type, name) DO UPDATE SET
                    metadata = excluded.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (project_key, node_type, name, payload),
            )
            row = conn.execute(
                """
                SELECT node_id, project_name, node_type, name, metadata, updated_at
                FROM knowledge_nodes
                WHERE project_name = ? AND node_type = ? AND name = ?
                """,
                (project_key, node_type, name),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to upsert knowledge node '{name}'.")
        return self._row_to_node(row)

    def add_edge(
        self,
        source_node_id: int,
        target_node_id: int,
        relationship: str,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeEdge:
        payload = json.dumps(metadata or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_edges (source_node_id, target_node_id, relationship, weight, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_node_id, target_node_id, relationship) DO UPDATE SET
                    weight = excluded.weight,
                    metadata = excluded.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (source_node_id, target_node_id, relationship, weight, payload),
            )
            row = conn.execute(
                """
                SELECT edge_id, source_node_id, target_node_id, relationship, weight, metadata, updated_at
                FROM knowledge_edges
                WHERE source_node_id = ? AND target_node_id = ? AND relationship = ?
                """,
                (source_node_id, target_node_id, relationship),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to upsert knowledge edge.")
        return self._row_to_edge(row)

    def rebuild_project_graph(
        self,
        project_name: str,
        project_map: ProjectMap,
        indexed_files,
        project_profile: ProjectMemoryProfile | None = None,
    ) -> None:
        self.clear_project_subgraph(project_name)

        project_node = self.upsert_node(
            node_type="project",
            name=project_name,
            project_name=project_name,
            metadata={
                "overview": project_map.overview,
                "languages": project_map.languages,
                "entry_points": project_map.entry_points[:12],
                "key_files": project_map.key_files[:15],
            },
        )

        file_lookup: dict[str, KnowledgeNode] = {}
        for file_path in project_map.key_files[:20]:
            file_node = self.upsert_node(
                node_type="file",
                name=file_path,
                project_name=project_name,
                metadata={"important": True},
            )
            file_lookup[file_path] = file_node
            self.add_edge(project_node.node_id, file_node.node_id, "has_key_file")

        for module in project_map.main_modules[:15]:
            module_node = self.upsert_node(
                node_type="module",
                name=module,
                project_name=project_name,
                metadata={"module": module},
            )
            self.add_edge(project_node.node_id, module_node.node_id, "contains_module")

        for dependency in project_map.dependencies[:25]:
            dependency_node = self.upsert_node(
                node_type="concept",
                name=dependency,
                metadata={"kind": "dependency"},
            )
            self.add_edge(project_node.node_id, dependency_node.node_id, "depends_on")

        if project_profile is not None:
            for framework in project_profile.frameworks[:12]:
                framework_node = self.upsert_node(
                    node_type="concept",
                    name=framework,
                    metadata={"kind": "framework"},
                )
                self.add_edge(project_node.node_id, framework_node.node_id, "uses_framework")
            for pattern in project_profile.coding_patterns[:12]:
                pattern_node = self.upsert_node(
                    node_type="concept",
                    name=pattern,
                    metadata={"kind": "pattern"},
                )
                self.add_edge(project_node.node_id, pattern_node.node_id, "follows_pattern")

        for entry in indexed_files[:80]:
            file_node = file_lookup.get(entry.file_path)
            if file_node is None:
                file_node = self.upsert_node(
                    node_type="file",
                    name=entry.file_path,
                    project_name=project_name,
                    metadata={"language": entry.language},
                )
                file_lookup[entry.file_path] = file_node

            module_name = entry.file_path.split("/", 1)[0]
            if module_name and module_name != entry.file_path:
                module_node = self.upsert_node(
                    node_type="module",
                    name=module_name,
                    project_name=project_name,
                    metadata={"module": module_name},
                )
                self.add_edge(module_node.node_id, file_node.node_id, "contains_file")

            for symbol in entry.symbols[:5]:
                symbol_node = self.upsert_node(
                    node_type="concept",
                    name=symbol,
                    metadata={"kind": "symbol"},
                )
                self.add_edge(file_node.node_id, symbol_node.node_id, "defines_symbol")

        self._sync_project_relationships(project_name)

    def link_solution(self, project_name: str, solution: SolutionEntry) -> None:
        project_node = self.upsert_node(
            node_type="project",
            name=project_name,
            project_name=project_name,
            metadata={"project": project_name},
        )
        solution_node = self.upsert_node(
            node_type="solution",
            name=solution.title,
            metadata={"solution_id": solution.solution_id, "tags": solution.tags},
        )
        self.add_edge(
            project_node.node_id,
            solution_node.node_id,
            "uses_solution",
            metadata={"projects": solution.projects, "source_task": solution.source_task},
        )
        for tag in solution.tags[:10]:
            concept_node = self.upsert_node(
                node_type="concept",
                name=tag,
                metadata={"kind": "solution_tag"},
            )
            self.add_edge(solution_node.node_id, concept_node.node_id, "references")
        self._sync_project_relationships(project_name)

    def clear_project_subgraph(self, project_name: str) -> None:
        with self._connect() as conn:
            node_rows = conn.execute(
                "SELECT node_id, node_type FROM knowledge_nodes WHERE project_name = ?",
                (project_name,),
            ).fetchall()
            node_ids = [int(row["node_id"]) for row in node_rows]
            if node_ids:
                placeholders = ", ".join("?" for _ in node_ids)
                conn.execute(
                    f"DELETE FROM knowledge_edges WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})",
                    node_ids + node_ids,
                )
            conn.execute(
                "DELETE FROM knowledge_nodes WHERE project_name = ? AND node_type != 'project'",
                (project_name,),
            )

    def delete_project(self, project_name: str) -> None:
        with self._connect() as conn:
            node_rows = conn.execute(
                "SELECT node_id FROM knowledge_nodes WHERE project_name = ?",
                (project_name,),
            ).fetchall()
            node_ids = [int(row["node_id"]) for row in node_rows]
            if node_ids:
                placeholders = ", ".join("?" for _ in node_ids)
                conn.execute(
                    f"DELETE FROM knowledge_edges WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})",
                    node_ids + node_ids,
                )
                conn.execute(
                    f"DELETE FROM knowledge_nodes WHERE node_id IN ({placeholders})",
                    node_ids,
                )

    def project_graph(self, project_name: str) -> dict[str, list[Any]]:
        with self._connect() as conn:
            owned_rows = conn.execute(
                "SELECT node_id FROM knowledge_nodes WHERE project_name = ?",
                (project_name,),
            ).fetchall()
            owned_ids = {int(row["node_id"]) for row in owned_rows}
            if not owned_ids:
                return {"nodes": [], "edges": []}

            placeholders = ", ".join("?" for _ in owned_ids)
            edge_rows = conn.execute(
                f"""
                SELECT edge_id, source_node_id, target_node_id, relationship, weight, metadata, updated_at
                FROM knowledge_edges
                WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})
                ORDER BY relationship ASC, edge_id ASC
                """,
                list(owned_ids) + list(owned_ids),
            ).fetchall()
            related_ids = set(owned_ids)
            for row in edge_rows:
                related_ids.add(int(row["source_node_id"]))
                related_ids.add(int(row["target_node_id"]))

            node_placeholders = ", ".join("?" for _ in related_ids)
            node_rows = conn.execute(
                f"""
                SELECT node_id, project_name, node_type, name, metadata, updated_at
                FROM knowledge_nodes
                WHERE node_id IN ({node_placeholders})
                ORDER BY node_type ASC, name ASC
                """,
                list(related_ids),
            ).fetchall()

        return {
            "nodes": [self._row_to_node(row) for row in node_rows],
            "edges": [self._row_to_edge(row) for row in edge_rows],
        }

    def related_projects(self, project_name: str, limit: int = 5) -> list[dict[str, Any]]:
        graph = self.project_graph(project_name)
        nodes = {node.node_id: node for node in graph["nodes"]}
        project_node = next((node for node in graph["nodes"] if node.node_type == "project" and node.name == project_name), None)
        if project_node is None:
            return []

        current_neighbors = {
            edge.target_node_id
            for edge in graph["edges"]
            if edge.source_node_id == project_node.node_id and edge.relationship in self.PROJECT_RELATIONSHIPS
        }
        related: list[dict[str, Any]] = []
        all_projects = [node for node in self.list_nodes(node_type="project") if node.name != project_name]
        for other in all_projects:
            other_neighbors = self._project_neighbor_ids(other.node_id)
            shared = current_neighbors & other_neighbors
            if not shared:
                continue
            shared_names = sorted(nodes[node_id].name for node_id in shared if node_id in nodes)[:10]
            if not shared_names:
                shared_names = self._node_names(shared)
            related.append(
                {
                    "project_name": other.name,
                    "shared_count": len(shared),
                    "shared_nodes": shared_names,
                }
            )
        related.sort(key=lambda item: (-item["shared_count"], item["project_name"]))
        return related[:limit]

    def project_insights(self, project_name: str, limit: int = 10) -> list[str]:
        graph = self.project_graph(project_name)
        nodes = {node.node_id: node for node in graph["nodes"]}
        project_node = next((node for node in graph["nodes"] if node.node_type == "project" and node.name == project_name), None)
        if project_node is None:
            return []

        insights: list[str] = []
        for edge in graph["edges"]:
            if edge.source_node_id != project_node.node_id:
                continue
            target = nodes.get(edge.target_node_id)
            if target is None:
                continue
            insights.append(f"{project_name} {edge.relationship.replace('_', ' ')} {target.name}")
        if not insights:
            insights.append(f"{project_name} has no graph relationships recorded yet.")
        return insights[:limit]

    def list_nodes(
        self,
        project_name: str | None = None,
        node_type: str | None = None,
        limit: int = 200,
    ) -> list[KnowledgeNode]:
        query = """
            SELECT node_id, project_name, node_type, name, metadata, updated_at
            FROM knowledge_nodes
            WHERE 1 = 1
        """
        params: list[Any] = []
        if project_name is not None:
            query += " AND project_name = ?"
            params.append(project_name)
        if node_type is not None:
            query += " AND node_type = ?"
            params.append(node_type)
        query += " ORDER BY node_type ASC, name ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def list_edges_for_project(self, project_name: str, limit: int = 300) -> list[KnowledgeEdge]:
        graph = self.project_graph(project_name)
        return graph["edges"][:limit]

    def _sync_project_relationships(self, project_name: str) -> None:
        project_nodes = {node.name: node for node in self.list_nodes(node_type="project", limit=500)}
        project_node = project_nodes.get(project_name)
        if project_node is None:
            return

        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM knowledge_edges
                WHERE relationship = 'related_project' AND (source_node_id = ? OR target_node_id = ?)
                """,
                (project_node.node_id, project_node.node_id),
            )

        current_neighbors = self._project_neighbor_ids(project_node.node_id)
        for other_name, other_node in project_nodes.items():
            if other_name == project_name:
                continue
            shared = current_neighbors & self._project_neighbor_ids(other_node.node_id)
            if not shared:
                continue
            metadata = {"shared_nodes": self._node_names(shared)[:10]}
            self.add_edge(
                project_node.node_id,
                other_node.node_id,
                "related_project",
                weight=float(len(shared)),
                metadata=metadata,
            )
            self.add_edge(
                other_node.node_id,
                project_node.node_id,
                "related_project",
                weight=float(len(shared)),
                metadata=metadata,
            )

    def _project_neighbor_ids(self, project_node_id: int) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT target_node_id
                FROM knowledge_edges
                WHERE source_node_id = ? AND relationship IN ('depends_on', 'uses_framework', 'follows_pattern')
                """,
                (project_node_id,),
            ).fetchall()
        return {int(row["target_node_id"]) for row in rows}

    def _node_names(self, node_ids: set[int]) -> list[str]:
        if not node_ids:
            return []
        placeholders = ", ".join("?" for _ in node_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT name FROM knowledge_nodes WHERE node_id IN ({placeholders}) ORDER BY name ASC",
                list(node_ids),
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    node_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL DEFAULT '',
                    node_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_name, node_type, name)
                );

                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_node_id INTEGER NOT NULL,
                    target_node_id INTEGER NOT NULL,
                    relationship TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_node_id, target_node_id, relationship)
                );
                """
            )

    def _row_to_node(self, row: sqlite3.Row) -> KnowledgeNode:
        project_name = row["project_name"] or None
        return KnowledgeNode(
            node_id=int(row["node_id"]),
            project_name=project_name,
            node_type=row["node_type"],
            name=row["name"],
            metadata=json.loads(row["metadata"] or "{}"),
            updated_at=row["updated_at"],
        )

    def _row_to_edge(self, row: sqlite3.Row) -> KnowledgeEdge:
        return KnowledgeEdge(
            edge_id=int(row["edge_id"]),
            source_node_id=int(row["source_node_id"]),
            target_node_id=int(row["target_node_id"]),
            relationship=row["relationship"],
            weight=float(row["weight"]),
            metadata=json.loads(row["metadata"] or "{}"),
            updated_at=row["updated_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
