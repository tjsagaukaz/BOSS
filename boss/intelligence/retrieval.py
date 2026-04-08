"""Hybrid retrieval layer: combines symbol lookup, keyword search,
existing memory/file-chunk search, and optional semantic (embedding) search
into a single ranked result set.

Each search source produces scored results.  The retrieval layer normalises
scores into [0, 1], deduplicates, and returns a merged ranked list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ResultKind(str, Enum):
    """Which search source produced the result."""
    symbol = "symbol"
    file_chunk = "file_chunk"
    memory = "memory"
    semantic = "semantic"


@dataclass
class RetrievalResult:
    """A single result from the hybrid retrieval pipeline."""
    kind: ResultKind
    score: float
    file_path: str | None
    line: int | None
    end_line: int | None
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "score": round(self.score, 4),
            "file_path": self.file_path,
            "line": self.line,
            "end_line": self.end_line,
            "content": self.content[:600],
            "metadata": self.metadata,
        }


@dataclass
class RetrievalCapabilities:
    """Reports which search backends are available."""
    symbol_search: bool = True
    keyword_search: bool = True
    memory_search: bool = True
    semantic_search: bool = False


def hybrid_search(
    query: str,
    *,
    project_path: str | None = None,
    limit: int = 15,
    enable_semantic: bool = True,
    enable_memory: bool = True,
    symbol_weight: float = 1.0,
    keyword_weight: float = 0.8,
    memory_weight: float = 0.6,
    semantic_weight: float = 0.9,
) -> tuple[list[RetrievalResult], RetrievalCapabilities]:
    """Run a hybrid search combining multiple retrieval backends.

    Returns (results, capabilities) so callers can tell which backends
    actually contributed.
    """
    results: list[RetrievalResult] = []
    caps = RetrievalCapabilities()

    # 1. Symbol lookup from code index
    try:
        results.extend(_search_symbols(query, project_path, symbol_weight))
    except Exception:
        caps.symbol_search = False
        logger.debug("Symbol search failed", exc_info=True)

    # 2. Keyword / BM25-style search across symbols
    try:
        results.extend(_search_keywords(query, project_path, keyword_weight))
    except Exception:
        caps.keyword_search = False
        logger.debug("Keyword search failed", exc_info=True)

    # 3. Existing knowledge store memory + file chunk search
    if enable_memory:
        try:
            results.extend(_search_memory(query, project_path, memory_weight))
        except Exception:
            caps.memory_search = False
            logger.debug("Memory search failed", exc_info=True)

    # 4. Semantic (embedding) search
    if enable_semantic:
        try:
            sem_results = _search_semantic(query, project_path, semantic_weight)
            if sem_results is not None:
                results.extend(sem_results)
                caps.semantic_search = True
        except Exception:
            caps.semantic_search = False
            logger.debug("Semantic search failed", exc_info=True)

    # Deduplicate and rank
    merged = _deduplicate(results)
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged[:limit], caps


def capabilities() -> RetrievalCapabilities:
    """Return which retrieval backends are currently available."""
    caps = RetrievalCapabilities()
    try:
        from boss.intelligence.embeddings import get_embeddings_backend
        backend = get_embeddings_backend()
        caps.semantic_search = backend is not None and backend.available
    except Exception:
        caps.semantic_search = False
    return caps


# ---------------------------------------------------------------------------
# Backend searchers
# ---------------------------------------------------------------------------

def _search_symbols(
    query: str,
    project_path: str | None,
    weight: float,
) -> list[RetrievalResult]:
    """Exact/prefix symbol name lookup."""
    from boss.intelligence.index import get_code_index

    idx = get_code_index()
    results: list[RetrievalResult] = []

    # Try each token as a potential symbol name
    tokens = query.split()
    for token in tokens:
        if len(token) < 2:
            continue
        hits = idx.find_symbol(token, project_path=project_path, limit=5)
        for hit in hits:
            # Score: exact match gets 1.0, prefix match drops
            name_lower = hit.name.lower()
            token_lower = token.lower()
            if name_lower == token_lower:
                base_score = 1.0
            elif name_lower.startswith(token_lower):
                base_score = 0.8
            else:
                base_score = 0.5

            content_parts = []
            if hit.signature:
                content_parts.append(hit.signature)
            if hit.docstring:
                content_parts.append(hit.docstring[:200])
            content = "\n".join(content_parts) or hit.qualified_name

            results.append(RetrievalResult(
                kind=ResultKind.symbol,
                score=base_score * weight,
                file_path=hit.file_path,
                line=hit.line,
                end_line=hit.end_line,
                content=content,
                metadata={
                    "name": hit.name,
                    "qualified_name": hit.qualified_name,
                    "kind": hit.kind,
                    "parent": hit.parent,
                    "exported": hit.exported,
                },
            ))

    return results


def _search_keywords(
    query: str,
    project_path: str | None,
    weight: float,
) -> list[RetrievalResult]:
    """Keyword search across symbol names, signatures, docstrings."""
    from boss.intelligence.index import get_code_index

    idx = get_code_index()
    hits = idx.search_symbols(query, project_path=project_path, limit=10)
    results: list[RetrievalResult] = []

    for i, hit in enumerate(hits):
        # Position-based score decay
        base_score = max(0.3, 1.0 - i * 0.07)

        content_parts = []
        if hit.signature:
            content_parts.append(hit.signature)
        if hit.docstring:
            content_parts.append(hit.docstring[:200])
        content = "\n".join(content_parts) or hit.qualified_name

        results.append(RetrievalResult(
            kind=ResultKind.symbol,
            score=base_score * weight,
            file_path=hit.file_path,
            line=hit.line,
            end_line=hit.end_line,
            content=content,
            metadata={
                "name": hit.name,
                "qualified_name": hit.qualified_name,
                "kind": hit.kind,
                "parent": hit.parent,
                "source": "keyword",
            },
        ))

    return results


def _search_memory(
    query: str,
    project_path: str | None,
    weight: float,
) -> list[RetrievalResult]:
    """Search the existing knowledge store for memories and file chunks."""
    from boss.memory.knowledge import get_knowledge_store

    ks = get_knowledge_store()
    results: list[RetrievalResult] = []

    # Search file chunks (most relevant for code context)
    chunks = ks.search_file_chunks(
        query, limit=8, project_path=project_path, touch_results=False,
    )
    for i, chunk in enumerate(chunks):
        base_score = max(0.3, 1.0 - i * 0.08)
        results.append(RetrievalResult(
            kind=ResultKind.file_chunk,
            score=base_score * weight,
            file_path=chunk.file_path,
            line=chunk.chunk_index * 30 if hasattr(chunk, "chunk_index") else None,
            end_line=None,
            content=chunk.content[:500],
            metadata={
                "chunk_id": chunk.id,
                "memory_kind": chunk.memory_kind if hasattr(chunk, "memory_kind") else "file_chunk",
            },
        ))

    # Search general memories
    memories = ks.search_memories(
        query, limit=5, project_path=project_path, touch_results=False,
    )
    for i, mem in enumerate(memories):
        if mem.source_table == "file_chunks":
            continue  # Already covered above
        base_score = max(0.2, 0.8 - i * 0.1)
        results.append(RetrievalResult(
            kind=ResultKind.memory,
            score=base_score * weight,
            file_path=None,
            line=None,
            end_line=None,
            content=mem.content[:500],
            metadata={
                "source_table": mem.source_table,
                "memory_kind": mem.memory_kind,
                "category": mem.category,
            },
        ))

    return results


def _search_semantic(
    query: str,
    project_path: str | None,
    weight: float,
) -> list[RetrievalResult] | None:
    """Semantic search using embeddings.  Returns None if unavailable.

    Checks the runner network policy before making any outbound API call.
    If a runner is active and network is DISABLED, semantic search is skipped
    entirely to respect the trust boundary.
    """
    # Gate on runner network policy — embedding calls are outbound API requests
    try:
        from boss.runner.engine import current_runner
        from boss.runner.policy import CommandVerdict
        runner = current_runner()
        if runner is not None:
            verdict = runner.check_network("api.openai.com")
            if verdict == CommandVerdict.DENIED:
                logger.debug("Semantic search skipped: runner network policy DENIED")
                return None
            if verdict == CommandVerdict.PROMPT:
                logger.debug("Semantic search skipped: runner network policy requires PROMPT")
                return None
    except ImportError:
        pass  # runner module not available — no enforcement

    from boss.intelligence.embeddings import get_embeddings_backend, get_embeddings_store

    backend = get_embeddings_backend()
    if backend is None or not backend.available:
        return None

    store = get_embeddings_store()
    query_vec = backend.embed_query(query)
    hits = store.search(query_vec, project_path=project_path, limit=10)

    results: list[RetrievalResult] = []
    for hit in hits:
        results.append(RetrievalResult(
            kind=ResultKind.semantic,
            score=hit["similarity"] * weight,
            file_path=hit["file_path"],
            line=hit["line_start"],
            end_line=hit["line_end"],
            content=hit["content"][:500],
            metadata={"chunk_id": hit["chunk_id"]},
        ))

    return results


# ---------------------------------------------------------------------------
# Merging and deduplication
# ---------------------------------------------------------------------------

def _deduplicate(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """Merge results that refer to the same code location, keeping the best score."""
    seen: dict[str, RetrievalResult] = {}
    for r in results:
        key = _dedup_key(r)
        existing = seen.get(key)
        if existing is None:
            seen[key] = r
        elif r.score > existing.score:
            # Keep higher score, merge metadata
            merged_meta = {**existing.metadata, **r.metadata}
            r.metadata = merged_meta
            seen[key] = r

    return list(seen.values())


def _dedup_key(r: RetrievalResult) -> str:
    """Generate a deduplication key based on file path and line range."""
    if r.file_path and r.line is not None:
        return f"{r.file_path}:{r.line}"
    if r.file_path:
        return r.file_path
    # For memory results, use content prefix as key
    return f"{r.kind.value}:{r.content[:80]}"
