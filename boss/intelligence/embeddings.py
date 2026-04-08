"""Pluggable embeddings interface for semantic code search.

The base class defines the contract.  Concrete backends implement it.
When no embeddings backend is available, the retrieval layer falls back
to keyword/BM25 search only and sets a capability flag so callers know.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boss.config import settings

logger = logging.getLogger(__name__)

_EMBEDDINGS_DB_FILE = settings.app_data_dir / "embeddings.db"


def _check_network_allowed() -> bool:
    """Return True if outbound network calls are allowed under the current runner policy.

    If no runner is active, defaults to allowed (no enforcement).
    """
    try:
        from boss.runner.engine import current_runner
        from boss.runner.policy import CommandVerdict
        runner = current_runner()
        if runner is None:
            return True
        verdict = runner.check_network("api.openai.com")
        return verdict == CommandVerdict.ALLOWED
    except ImportError:
        return True  # runner module not available — no enforcement


@dataclass
class EmbeddingRecord:
    """A stored embedding vector with metadata."""
    chunk_id: str
    file_path: str
    project_path: str | None
    content: str
    line_start: int
    line_end: int
    vector: list[float]


class EmbeddingsBackend(ABC):
    """Abstract interface for an embeddings provider.

    Subclass this to plug in OpenAI embeddings, local sentence-transformers,
    or any other vector model.  The retrieval layer calls only these methods.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name for diagnostics."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Dimensionality of the embedding vectors."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.  Must return one vector per input text."""

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Embed a single search query.  May use a different prompt prefix."""

    @property
    def available(self) -> bool:
        """Whether this backend is ready to use (API key present, model loaded, etc.)."""
        return True


class OpenAIEmbeddingsBackend(EmbeddingsBackend):
    """Concrete embeddings backend using the OpenAI Embeddings API.

    Uses text-embedding-3-small by default for cost/speed balance.
    """

    def __init__(self, model: str = "text-embedding-3-small"):
        self._model = model
        self._dimensions = 1536

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def available(self) -> bool:
        return bool(settings.cloud_api_key)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.available:
            raise RuntimeError("OpenAI API key not configured for embeddings")
        # Check runner network policy before making outbound API calls
        if not _check_network_allowed():
            raise RuntimeError("Embeddings API call blocked by runner network policy")
        from openai import OpenAI
        client = OpenAI(api_key=settings.cloud_api_key)
        # Truncate texts to avoid token limits
        truncated = [t[:8000] for t in texts]
        response = client.embeddings.create(
            model=self._model,
            input=truncated,
        )
        return [item.embedding for item in response.data]

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_texts([query[:8000]])
        return vectors[0]


class EmbeddingsStore:
    """SQLite-backed vector storage with brute-force cosine similarity search.

    For the scale of a local coding agent (tens of thousands of chunks max),
    brute-force search is fast enough.  This avoids adding a vector DB
    dependency while the interface stays swappable.
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _EMBEDDINGS_DB_FILE
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS embeddings (
                chunk_id    TEXT    PRIMARY KEY,
                file_path   TEXT    NOT NULL,
                project_path TEXT,
                content     TEXT    NOT NULL,
                line_start  INTEGER NOT NULL DEFAULT 0,
                line_end    INTEGER NOT NULL DEFAULT 0,
                vector_json TEXT    NOT NULL,
                content_hash TEXT   NOT NULL,
                created_at  REAL   NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_emb_file ON embeddings(file_path);
            CREATE INDEX IF NOT EXISTS idx_emb_project ON embeddings(project_path);
        """)

    def close(self) -> None:
        self._conn.close()

    def store_embeddings(
        self,
        records: list[EmbeddingRecord],
    ) -> int:
        """Store embedding records.  Returns count of new/updated records."""
        stored = 0
        for rec in records:
            content_hash = hashlib.sha256(rec.content.encode("utf-8")).hexdigest()[:16]
            existing = self._conn.execute(
                "SELECT content_hash FROM embeddings WHERE chunk_id = ?",
                (rec.chunk_id,),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                continue  # unchanged
            self._conn.execute(
                """INSERT OR REPLACE INTO embeddings
                    (chunk_id, file_path, project_path, content, line_start, line_end,
                     vector_json, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.chunk_id, rec.file_path, rec.project_path, rec.content,
                    rec.line_start, rec.line_end,
                    json.dumps(rec.vector), content_hash, time.time(),
                ),
            )
            stored += 1
        self._conn.commit()
        return stored

    def search(
        self,
        query_vector: list[float],
        *,
        project_path: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Brute-force cosine similarity search."""
        conditions = []
        params: list[Any] = []
        if project_path:
            conditions.append("project_path = ?")
            params.append(project_path)
        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = self._conn.execute(
            f"SELECT * FROM embeddings{where_clause}",
            params,
        ).fetchall()

        scored = []
        for row in rows:
            stored_vec = json.loads(row["vector_json"])
            sim = _cosine_similarity(query_vector, stored_vec)
            scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sim, row in scored[:limit]:
            results.append({
                "chunk_id": row["chunk_id"],
                "file_path": row["file_path"],
                "project_path": row["project_path"],
                "content": row["content"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "similarity": round(sim, 4),
            })
        return results

    def get_file_hashes(self, file_path: str) -> dict[str, str]:
        """Return {chunk_id: content_hash} for all chunks of a file."""
        rows = self._conn.execute(
            "SELECT chunk_id, content_hash FROM embeddings WHERE file_path = ?",
            (file_path,),
        ).fetchall()
        return {row["chunk_id"]: row["content_hash"] for row in rows}

    def remove_stale_chunks(self, file_path: str, keep_chunk_ids: set[str]) -> int:
        """Remove chunks for a file whose chunk_id is not in *keep_chunk_ids*."""
        rows = self._conn.execute(
            "SELECT chunk_id FROM embeddings WHERE file_path = ?",
            (file_path,),
        ).fetchall()
        removed = 0
        for row in rows:
            if row["chunk_id"] not in keep_chunk_ids:
                self._conn.execute(
                    "DELETE FROM embeddings WHERE chunk_id = ?", (row["chunk_id"],)
                )
                removed += 1
        if removed:
            self._conn.commit()
        return removed

    def remove_file(self, file_path: str) -> None:
        self._conn.execute("DELETE FROM embeddings WHERE file_path = ?", (file_path,))
        self._conn.commit()

    def prune_project(self, project_path: str, keep_paths: set[str]) -> int:
        rows = self._conn.execute(
            "SELECT chunk_id, file_path FROM embeddings WHERE project_path = ?",
            (project_path,),
        ).fetchall()
        removed = 0
        for row in rows:
            if row["file_path"] not in keep_paths:
                self._conn.execute("DELETE FROM embeddings WHERE chunk_id = ?", (row["chunk_id"],))
                removed += 1
        if removed:
            self._conn.commit()
        return removed

    def stats(self) -> dict[str, Any]:
        count = self._conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        return {"embeddings_stored": count}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_backend: EmbeddingsBackend | None = None
_store: EmbeddingsStore | None = None


def get_embeddings_backend() -> EmbeddingsBackend | None:
    """Return the configured embeddings backend, or None if unavailable."""
    global _backend
    if _backend is None:
        candidate = OpenAIEmbeddingsBackend()
        if candidate.available:
            _backend = candidate
    return _backend


def get_embeddings_store() -> EmbeddingsStore:
    global _store
    if _store is None:
        _store = EmbeddingsStore()
    return _store
