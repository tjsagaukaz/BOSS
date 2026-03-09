from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from boss.memory.embeddings import EmbeddingService
from boss.types import CodeSummary, IndexedFile, MemoryEntry, ProjectMap


class MemoryStore:
    def __init__(self, db_path: str | Path, embeddings: EmbeddingService) -> None:
        self.db_path = Path(db_path)
        self.embeddings = embeddings
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert_project(
        self,
        name: str,
        path: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (name, path, summary, metadata, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    path = excluded.path,
                    summary = excluded.summary,
                    metadata = excluded.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (name, path, summary, json.dumps(metadata or {})),
            )

    def get_project(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def delete_project(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE name = ?", (name,))
            conn.execute("DELETE FROM project_maps WHERE project_name = ?", (name,))
            conn.execute("DELETE FROM indexed_files WHERE project_name = ?", (name,))
            conn.execute("DELETE FROM code_summaries WHERE project_name = ?", (name,))
            conn.execute("DELETE FROM memory_entries WHERE project_name = ?", (name,))
            conn.execute("DELETE FROM conversations WHERE project_name = ?", (name,))

    def add_memory_entry(
        self,
        project_name: str,
        category: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        force_local_embedding: bool = False,
    ) -> None:
        embedding = json.dumps(self.embeddings.embed(content, force_local=force_local_embedding))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries (project_name, category, content, metadata, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (project_name, category, content, json.dumps(metadata or {}), embedding),
            )

    def delete_memory_entries(self, project_name: str, category: str | None = None) -> None:
        query = "DELETE FROM memory_entries WHERE project_name = ?"
        params: list[Any] = [project_name]
        if category:
            query += " AND category = ?"
            params.append(category)
        with self._connect() as conn:
            conn.execute(query, params)

    def add_conversation_turn(
        self,
        project_name: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (project_name, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (project_name, role, content, json.dumps(metadata or {})),
            )

    def upsert_code_summary(
        self,
        project_name: str,
        file_path: str,
        language: str,
        summary: str,
        force_local_embedding: bool = False,
    ) -> None:
        embedding = json.dumps(self.embeddings.embed(summary, force_local=force_local_embedding))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO code_summaries (project_name, file_path, language, summary, embedding, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name, file_path) DO UPDATE SET
                    language = excluded.language,
                    summary = excluded.summary,
                    embedding = excluded.embedding,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (project_name, file_path, language, summary, embedding),
            )

    def delete_code_summary(self, project_name: str, file_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM code_summaries WHERE project_name = ? AND file_path = ?",
                (project_name, file_path),
            )

    def upsert_indexed_file(
        self,
        project_name: str,
        file_path: str,
        language: str,
        content_hash: str,
        size: int,
        modified_at: str,
        summary: str,
        purpose: str,
        symbols: list[str],
        dependencies: list[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO indexed_files (
                    project_name, file_path, language, content_hash, size, modified_at,
                    summary, purpose, symbols, dependencies, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name, file_path) DO UPDATE SET
                    language = excluded.language,
                    content_hash = excluded.content_hash,
                    size = excluded.size,
                    modified_at = excluded.modified_at,
                    summary = excluded.summary,
                    purpose = excluded.purpose,
                    symbols = excluded.symbols,
                    dependencies = excluded.dependencies,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_name,
                    file_path,
                    language,
                    content_hash,
                    size,
                    modified_at,
                    summary,
                    purpose,
                    json.dumps(symbols),
                    json.dumps(dependencies),
                ),
            )

    def get_indexed_file(self, project_name: str, file_path: str) -> IndexedFile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT file_path, language, content_hash, size, modified_at, summary, purpose, symbols, dependencies, updated_at
                FROM indexed_files
                WHERE project_name = ? AND file_path = ?
                """,
                (project_name, file_path),
            ).fetchone()
        return self._row_to_indexed_file(row) if row else None

    def list_indexed_files(self, project_name: str, limit: int | None = 200) -> list[IndexedFile]:
        query = """
            SELECT file_path, language, content_hash, size, modified_at, summary, purpose, symbols, dependencies, updated_at
            FROM indexed_files
            WHERE project_name = ?
            ORDER BY updated_at DESC, file_path ASC
        """
        params: list[Any] = [project_name]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_indexed_file(row) for row in rows]

    def delete_indexed_file(self, project_name: str, file_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM indexed_files WHERE project_name = ? AND file_path = ?",
                (project_name, file_path),
            )

    def upsert_project_map(self, project_name: str, project_map: ProjectMap) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_maps (
                    project_name, overview, languages, main_modules, entry_points, key_files, dependencies, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP))
                ON CONFLICT(project_name) DO UPDATE SET
                    overview = excluded.overview,
                    languages = excluded.languages,
                    main_modules = excluded.main_modules,
                    entry_points = excluded.entry_points,
                    key_files = excluded.key_files,
                    dependencies = excluded.dependencies,
                    indexed_at = COALESCE(NULLIF(excluded.indexed_at, ''), CURRENT_TIMESTAMP)
                """,
                (
                    project_name,
                    project_map.overview,
                    json.dumps(project_map.languages),
                    json.dumps(project_map.main_modules),
                    json.dumps(project_map.entry_points),
                    json.dumps(project_map.key_files),
                    json.dumps(project_map.dependencies),
                    project_map.indexed_at,
                ),
            )

    def get_project_map(self, project_name: str) -> ProjectMap | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT overview, languages, main_modules, entry_points, key_files, dependencies, indexed_at
                FROM project_maps
                WHERE project_name = ?
                """,
                (project_name,),
            ).fetchone()
        if row is None:
            return None
        return ProjectMap(
            name=project_name,
            overview=row["overview"],
            languages=json.loads(row["languages"] or "{}"),
            main_modules=json.loads(row["main_modules"] or "[]"),
            entry_points=json.loads(row["entry_points"] or "[]"),
            key_files=json.loads(row["key_files"] or "[]"),
            dependencies=json.loads(row["dependencies"] or "[]"),
            indexed_at=row["indexed_at"],
        )

    def list_memory_entries(
        self,
        project_name: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[MemoryEntry]:
        query = """
            SELECT category, content, metadata, created_at
            FROM memory_entries
            WHERE project_name = ?
        """
        params: list[Any] = [project_name]
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            MemoryEntry(
                category=row["category"],
                content=row["content"],
                created_at=row["created_at"],
                metadata=json.loads(row["metadata"] or "{}"),
            )
            for row in rows
        ]

    def list_code_summaries(self, project_name: str, limit: int = 12) -> list[CodeSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_path, language, summary, updated_at
                FROM code_summaries
                WHERE project_name = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (project_name, limit),
            ).fetchall()
        return [
            CodeSummary(
                file_path=row["file_path"],
                language=row["language"],
                summary=row["summary"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def semantic_search(self, project_name: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_vector = self.embeddings.embed(query)
        candidates: list[dict[str, Any]] = []
        with self._connect() as conn:
            memory_rows = conn.execute(
                """
                SELECT 'memory' AS kind, category AS label, content, metadata, embedding, created_at AS ts
                FROM memory_entries
                WHERE project_name = ?
                """,
                (project_name,),
            ).fetchall()
            summary_rows = conn.execute(
                """
                SELECT 'code' AS kind, file_path AS label, summary AS content, '{}' AS metadata, embedding, updated_at AS ts
                FROM code_summaries
                WHERE project_name = ?
                """,
                (project_name,),
            ).fetchall()

        for row in list(memory_rows) + list(summary_rows):
            embedding = json.loads(row["embedding"] or "[]")
            similarity = self.embeddings.cosine_similarity(query_vector, embedding)
            candidates.append(
                {
                    "kind": row["kind"],
                    "label": row["label"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "timestamp": row["ts"],
                    "score": similarity,
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:limit]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    name TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    embedding TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS code_summaries (
                    project_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_name, file_path)
                );

                CREATE TABLE IF NOT EXISTS indexed_files (
                    project_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    symbols TEXT NOT NULL DEFAULT '[]',
                    dependencies TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_name, file_path)
                );

                CREATE TABLE IF NOT EXISTS project_maps (
                    project_name TEXT PRIMARY KEY,
                    overview TEXT NOT NULL,
                    languages TEXT NOT NULL DEFAULT '{}',
                    main_modules TEXT NOT NULL DEFAULT '[]',
                    entry_points TEXT NOT NULL DEFAULT '[]',
                    key_files TEXT NOT NULL DEFAULT '[]',
                    dependencies TEXT NOT NULL DEFAULT '[]',
                    indexed_at TEXT NOT NULL
                );
                """
            )

    def _row_to_indexed_file(self, row: sqlite3.Row) -> IndexedFile:
        return IndexedFile(
            file_path=row["file_path"],
            language=row["language"],
            content_hash=row["content_hash"],
            size=int(row["size"]),
            modified_at=row["modified_at"],
            summary=row["summary"],
            purpose=row["purpose"],
            symbols=json.loads(row["symbols"] or "[]"),
            dependencies=json.loads(row["dependencies"] or "[]"),
            updated_at=row["updated_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
