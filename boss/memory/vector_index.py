from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from boss.memory.embeddings import EmbeddingService


class VectorIndex:
    def __init__(
        self,
        db_path: str | Path,
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embeddings = embeddings or EmbeddingService(
            provider="openai",
            model="text-embedding-3-small",
            dimensions=1536,
        )
        self._initialize()

    def add_document(
        self,
        text: str,
        metadata: dict[str, Any],
        force_local_embedding: bool = False,
    ) -> str:
        document_id = str(
            metadata.get("document_id")
            or self._default_document_id(text=text, metadata=metadata)
        )
        embedding = json.dumps(self.embeddings.embed(text, force_local=force_local_embedding))
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        project_name = str(metadata.get("project_name", ""))
        kind = str(metadata.get("kind", "document"))
        file_path = str(metadata.get("file_path", ""))

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vector_documents (
                    document_id, project_name, kind, file_path, text, metadata, embedding, content_hash, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(document_id) DO UPDATE SET
                    project_name = excluded.project_name,
                    kind = excluded.kind,
                    file_path = excluded.file_path,
                    text = excluded.text,
                    metadata = excluded.metadata,
                    embedding = excluded.embedding,
                    content_hash = excluded.content_hash,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    project_name,
                    kind,
                    file_path,
                    text,
                    json.dumps(metadata),
                    embedding,
                    content_hash,
                ),
            )
        return document_id

    def semantic_search(
        self,
        query: str,
        project_name: str | None = None,
        limit: int = 8,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query_vector = self.embeddings.embed(query)
        sql = """
            SELECT document_id, project_name, kind, file_path, text, metadata, embedding, updated_at
            FROM vector_documents
            WHERE 1 = 1
        """
        params: list[Any] = []
        if project_name:
            sql += " AND project_name = ?"
            params.append(project_name)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            vector = json.loads(row["embedding"] or "[]")
            score = self.embeddings.cosine_similarity(query_vector, vector)
            metadata = json.loads(row["metadata"] or "{}")
            results.append(
                {
                    "document_id": row["document_id"],
                    "project_name": row["project_name"],
                    "kind": row["kind"],
                    "file_path": row["file_path"],
                    "text": row["text"],
                    "metadata": metadata,
                    "score": score,
                    "updated_at": row["updated_at"],
                }
            )

        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]

    def delete_documents(self, project_name: str, file_path: str | None = None, kind: str | None = None) -> None:
        sql = "DELETE FROM vector_documents WHERE project_name = ?"
        params: list[Any] = [project_name]
        if file_path is not None:
            sql += " AND file_path = ?"
            params.append(file_path)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        with self._connect() as conn:
            conn.execute(sql, params)

    def _default_document_id(self, text: str, metadata: dict[str, Any]) -> str:
        seed = "|".join(
            [
                str(metadata.get("project_name", "")),
                str(metadata.get("kind", "document")),
                str(metadata.get("file_path", "")),
                str(metadata.get("snippet_index", "")),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            ]
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_documents (
                    document_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    file_path TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    embedding TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "vector_documents", "file_path", "TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row[1] == column for row in rows):
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
