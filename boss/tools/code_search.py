from __future__ import annotations

import re
from pathlib import Path

from boss.memory.embeddings import EmbeddingService


class CodeSearch:
    IGNORED_DIRS = {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
    MAX_FILE_SIZE = 200_000

    def __init__(self, root: str | Path, embeddings: EmbeddingService) -> None:
        self.root = Path(root).resolve()
        self.embeddings = embeddings

    def search_codebase(self, query: str, limit: int = 8) -> dict[str, object]:
        query_terms = [token for token in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if len(token) > 1]
        query_vector = self.embeddings.embed(query)
        results: list[dict[str, object]] = []

        for file_path in self._iter_files():
            text = file_path.read_text(encoding="utf-8", errors="replace")
            lowered = text.lower()
            lexical_score = sum(lowered.count(term) for term in query_terms)
            summary_text = f"{file_path.relative_to(self.root)}\n{text[:1500]}"
            semantic_score = self.embeddings.cosine_similarity(query_vector, self.embeddings.embed(summary_text))
            score = lexical_score * 2.5 + semantic_score
            if lexical_score == 0 and semantic_score < 0.18:
                continue

            results.append(
                {
                    "path": str(file_path.relative_to(self.root)),
                    "score": round(score, 4),
                    "excerpt": self._build_excerpt(text, query_terms),
                }
            )

        results.sort(key=lambda item: item["score"], reverse=True)
        return {"query": query, "matches": results[:limit]}

    def _iter_files(self):
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(self.root).parts
            if any(part in self.IGNORED_DIRS or part.startswith(".") for part in relative_parts[:-1]):
                continue
            if path.stat().st_size > self.MAX_FILE_SIZE:
                continue
            yield path

    def _build_excerpt(self, text: str, query_terms: list[str]) -> str:
        lines = text.splitlines()
        if not lines:
            return ""
        for index, line in enumerate(lines):
            lowered = line.lower()
            if any(term in lowered for term in query_terms):
                start = max(index - 2, 0)
                end = min(index + 3, len(lines))
                return "\n".join(lines[start:end])
        return "\n".join(lines[:5])

