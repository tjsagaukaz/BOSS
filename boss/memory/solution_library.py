from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from boss.memory.embeddings import EmbeddingService
from boss.types import SolutionEntry


class SolutionLibrary:
    CODE_BLOCK_PATTERN = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)

    def __init__(self, db_path: str | Path, embeddings: EmbeddingService) -> None:
        self.db_path = Path(db_path)
        self.embeddings = embeddings
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add_solution(
        self,
        title: str,
        description: str,
        code_snippet: str,
        tags: list[str],
        projects: list[str],
        source_task: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SolutionEntry:
        normalized_tags = sorted({tag.strip() for tag in tags if tag.strip()})
        normalized_projects = sorted({project.strip() for project in projects if project.strip()})
        payload = {
            "title": title.strip(),
            "description": description.strip(),
            "code_snippet": code_snippet.strip(),
            "tags": normalized_tags,
            "projects": normalized_projects,
            "source_task": source_task.strip(),
            "metadata": metadata or {},
        }
        content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        embedding = json.dumps(self.embeddings.embed(self._embedding_text(payload)))

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO solutions (
                    content_hash, title, description, code_snippet, tags, projects,
                    source_task, metadata, embedding, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(content_hash) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    code_snippet = excluded.code_snippet,
                    tags = excluded.tags,
                    projects = excluded.projects,
                    source_task = excluded.source_task,
                    metadata = excluded.metadata,
                    embedding = excluded.embedding,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    content_hash,
                    payload["title"],
                    payload["description"],
                    payload["code_snippet"],
                    json.dumps(normalized_tags),
                    json.dumps(normalized_projects),
                    payload["source_task"],
                    json.dumps(payload["metadata"]),
                    embedding,
                ),
            )
            row = conn.execute(
                """
                SELECT solution_id, title, description, code_snippet, tags, projects,
                       source_task, metadata, created_at, updated_at
                FROM solutions
                WHERE content_hash = ?
                """,
                (content_hash,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to upsert solution entry.")
        return self._row_to_solution(row)

    def capture_task_solution(
        self,
        project_name: str,
        task: str,
        solution_text: str,
        changed_files: list[str],
        project_root: str | Path,
        errors: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SolutionEntry | None:
        summary_text = solution_text.strip()
        if not task.strip() and not summary_text:
            return None

        snippet = self._extract_snippet(solution_text=solution_text, project_root=project_root, changed_files=changed_files)
        title = self._solution_title(task)
        description = summary_text[:500].strip() or f"Implementation for task: {task.strip()}"
        tags = self._extract_tags(task, summary_text, changed_files)
        if errors:
            tags.extend("bugfix" if error else "" for error in errors[:1])
        metadata_payload = dict(metadata or {})
        metadata_payload["verified"] = self._is_verified_solution(
            status=str(metadata_payload.get("status", "")),
            errors=errors or [],
        )
        metadata_payload.update(
            {
                "changed_files": changed_files,
                "errors": errors or [],
            }
        )
        return self.add_solution(
            title=title,
            description=description,
            code_snippet=snippet,
            tags=list(dict.fromkeys([tag for tag in tags if tag])),
            projects=[project_name],
            source_task=task,
            metadata=metadata_payload,
        )

    def search(
        self,
        query: str,
        project_name: str | None = None,
        limit: int = 8,
        verified_only: bool = False,
    ) -> list[SolutionEntry]:
        query_vector = self.embeddings.embed(query)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT solution_id, title, description, code_snippet, tags, projects,
                       source_task, metadata, embedding, created_at, updated_at
                FROM solutions
                ORDER BY updated_at DESC
                """
            ).fetchall()

        results: list[SolutionEntry] = []
        for row in rows:
            projects = json.loads(row["projects"] or "[]")
            if project_name and project_name not in projects:
                continue
            metadata = json.loads(row["metadata"] or "{}")
            if verified_only and not bool(metadata.get("verified", False)):
                continue
            score = self.embeddings.cosine_similarity(query_vector, json.loads(row["embedding"] or "[]"))
            entry = self._row_to_solution(row)
            entry.score = score
            results.append(entry)

        results.sort(key=lambda item: ((item.score or 0.0), item.updated_at), reverse=True)
        if results or project_name is None:
            return results[:limit]

        return self.search(query=query, project_name=None, limit=limit, verified_only=verified_only)

    def list_solutions(
        self,
        project_name: str | None = None,
        limit: int = 50,
        verified_only: bool = False,
    ) -> list[SolutionEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT solution_id, title, description, code_snippet, tags, projects,
                       source_task, metadata, created_at, updated_at
                FROM solutions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        results = [self._row_to_solution(row) for row in rows]
        if verified_only:
            results = [entry for entry in results if bool(entry.metadata.get("verified", False))]
        if project_name is None:
            return results
        return [entry for entry in results if project_name in entry.projects]

    def _solution_title(self, task: str) -> str:
        cleaned = re.sub(r"\s+", " ", task.strip())
        if not cleaned:
            return "Reusable solution"
        shortened = cleaned[:80]
        return shortened[0].upper() + shortened[1:]

    def _extract_tags(self, task: str, solution_text: str, changed_files: list[str]) -> list[str]:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", f"{task} {solution_text}".lower())
        deny = {"implement", "update", "create", "build", "task", "current", "step", "project", "file", "files"}
        ranked = [token for token in tokens if token not in deny]
        file_tags = [Path(file_path).stem.lower() for file_path in changed_files[:5]]
        tags = ranked[:8] + file_tags
        return list(dict.fromkeys(tags))[:12]

    def _extract_snippet(self, solution_text: str, project_root: str | Path, changed_files: list[str]) -> str:
        match = self.CODE_BLOCK_PATTERN.search(solution_text)
        if match:
            return match.group(1).strip()[:2000]

        root = Path(project_root)
        for file_path in changed_files[:3]:
            resolved = (root / file_path).resolve()
            if not resolved.exists() or not resolved.is_file():
                continue
            content = resolved.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                return "\n".join(content.splitlines()[:80]).strip()[:2000]
        return solution_text[:2000].strip()

    def _embedding_text(self, payload: dict[str, Any]) -> str:
        return json.dumps(
            {
                "title": payload["title"],
                "description": payload["description"],
                "tags": payload["tags"],
                "code_snippet": payload["code_snippet"][:1500],
                "projects": payload["projects"],
            },
            indent=2,
        )

    def _is_verified_solution(self, status: str, errors: list[str]) -> bool:
        return status.strip().lower() in {"passed", "completed"} and not errors

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS solutions (
                    solution_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    code_snippet TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    projects TEXT NOT NULL DEFAULT '[]',
                    source_task TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    embedding TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_solution(self, row: sqlite3.Row) -> SolutionEntry:
        return SolutionEntry(
            solution_id=int(row["solution_id"]),
            title=row["title"],
            description=row["description"],
            code_snippet=row["code_snippet"],
            tags=json.loads(row["tags"] or "[]"),
            projects=json.loads(row["projects"] or "[]"),
            source_task=row["source_task"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
