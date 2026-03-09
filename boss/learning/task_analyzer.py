from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from boss.memory.knowledge_graph import KnowledgeGraph
from boss.memory.solution_library import SolutionLibrary
from boss.memory.task_history import TaskHistoryStore


class TaskAnalyzer:
    ERROR_TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}")

    def __init__(
        self,
        db_path: str | Path,
        task_history: TaskHistoryStore,
        solution_library: SolutionLibrary,
        knowledge_graph: KnowledgeGraph,
    ) -> None:
        self.db_path = Path(db_path)
        self.task_history = task_history
        self.solution_library = solution_library
        self.knowledge_graph = knowledge_graph
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def analyze_completion(
        self,
        project_name: str,
        task: str,
        status: str,
        solution_text: str,
        changed_files: list[str],
        errors: list[str],
        metadata: dict[str, Any] | None = None,
        task_ref: int | None = None,
    ) -> dict[str, Any]:
        merged_metadata = dict(metadata or {})
        common_errors = self._extract_common_errors(errors, merged_metadata)
        frequent_solutions = self._extract_solution_patterns(task, solution_text, changed_files)
        metrics = {
            "success": status in {"completed", "passed"},
            "file_count": len(changed_files),
            "error_count": len(errors),
            "step_count": len(merged_metadata.get("steps", [])),
            "iterations": int(merged_metadata.get("iterations", 0) or 0),
            "mode": merged_metadata.get("mode", "interactive"),
        }

        analysis_id = self._insert_analysis(
            project_name=project_name,
            task=task,
            status=status,
            common_errors=common_errors,
            frequent_solutions=frequent_solutions,
            metrics=metrics,
            files_changed=changed_files,
            metadata=merged_metadata,
            task_ref=task_ref,
        )
        self._update_graph(project_name, task, status, common_errors, frequent_solutions)
        return {
            "analysis_id": analysis_id,
            "project_name": project_name,
            "task": task,
            "status": status,
            "common_errors": common_errors,
            "frequent_solutions": frequent_solutions,
            "metrics": metrics,
            "files_changed": changed_files,
        }

    def analyze_task_record(
        self,
        task_record: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        steps = list(task_record.get("steps", []))
        iterations = sum(int(step.get("iterations", 0)) for step in steps)
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("steps", [step.get("title", "") for step in steps])
        merged_metadata.setdefault("iterations", iterations)
        return self.analyze_completion(
            project_name=str(task_record.get("project_name", "")),
            task=str(task_record.get("task", "")),
            status=str(task_record.get("status", "unknown")),
            solution_text=str(task_record.get("final_result", "")),
            changed_files=list(task_record.get("files_changed", [])),
            errors=list(task_record.get("errors", [])),
            metadata=merged_metadata,
            task_ref=int(task_record.get("id", 0)) if task_record.get("id") is not None else None,
        )

    def analyze_recent(
        self,
        project_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        tasks = self.task_history.recent_tasks(project_name=project_name, limit=limit)
        analyzed: list[dict[str, Any]] = []
        for task in tasks:
            task_id = int(task["id"])
            if self._already_analyzed(task_id):
                continue
            task_record = self.task_history.task_with_steps(task_id) or task
            analyzed.append(self.analyze_task_record(task_record))
        return analyzed

    def recent_analyses(self, project_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = """
            SELECT analysis_id, project_name, task, status, common_errors, frequent_solutions,
                   metrics, files_changed, metadata, task_ref, created_at, updated_at
            FROM task_analysis
            WHERE 1 = 1
        """
        params: list[Any] = []
        if project_name:
            query += " AND project_name = ?"
            params.append(project_name)
        query += " ORDER BY analysis_id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_analysis(row) for row in rows]

    def summary(self, project_name: str | None = None) -> dict[str, Any]:
        analyses = self.recent_analyses(project_name=project_name, limit=100)
        errors = Counter()
        solutions = Counter()
        completed = 0
        failed = 0
        for item in analyses:
            if item["status"] in {"completed", "passed"}:
                completed += 1
            elif item["status"] in {"failed", "stopped", "needs_followup"}:
                failed += 1
            errors.update(item["common_errors"])
            solutions.update(item["frequent_solutions"])
        return {
            "tasks_completed": completed,
            "tasks_failed": failed,
            "common_errors": [name for name, _count in errors.most_common(8)],
            "frequent_solutions": [name for name, _count in solutions.most_common(8)],
            "analyses": analyses[:10],
        }

    def _extract_common_errors(self, errors: list[str], metadata: dict[str, Any]) -> list[str]:
        tokens = Counter()
        sources = list(errors)
        model_errors = metadata.get("errors")
        if isinstance(model_errors, list):
            sources.extend(str(item) for item in model_errors)
        for error in sources:
            lowered = error.lower()
            if "test" in lowered or "pytest" in lowered:
                tokens["test failure"] += 1
            if "security" in lowered or "auth" in lowered or "permission" in lowered:
                tokens["security issue"] += 1
            if "import" in lowered or "module" in lowered:
                tokens["dependency mismatch"] += 1
            if "not found" in lowered or "symbol" in lowered:
                tokens["missing symbol or path"] += 1
            if "type" in lowered or "mypy" in lowered or "typing" in lowered:
                tokens["type mismatch"] += 1
            for token in self.ERROR_TOKEN_PATTERN.findall(lowered):
                if token in {"error", "failed", "failure", "line", "step", "project"}:
                    continue
                if token in {"pytest", "tests"}:
                    tokens["test failure"] += 1
                elif token in {"auth", "security"}:
                    tokens["security issue"] += 1
        if not tokens and errors:
            tokens["general failure"] += len(errors)
        return [name for name, _count in tokens.most_common(6)]

    def _extract_solution_patterns(self, task: str, solution_text: str, changed_files: list[str]) -> list[str]:
        patterns = Counter()
        lowered = f"{task}\n{solution_text}".lower()
        if "stripe" in lowered:
            patterns["stripe integration"] += 1
        if "auth" in lowered or "jwt" in lowered:
            patterns["authentication flow"] += 1
        if "test" in lowered:
            patterns["test coverage update"] += 1
        if "webhook" in lowered:
            patterns["webhook handler"] += 1
        if "api" in lowered or "endpoint" in lowered:
            patterns["api endpoint"] += 1
        return [name for name, _count in patterns.most_common(8)]

    def _update_graph(
        self,
        project_name: str,
        task: str,
        status: str,
        common_errors: list[str],
        frequent_solutions: list[str],
    ) -> None:
        outcome_node = self.knowledge_graph.upsert_node(
            node_type="concept",
            name=f"{project_name}:{task[:120]}",
            metadata={"kind": "task_outcome", "status": status},
        )
        for label in common_errors[:6]:
            error_node = self.knowledge_graph.upsert_node(
                node_type="concept",
                name=label,
                metadata={"kind": "task_error"},
            )
            self.knowledge_graph.add_edge(outcome_node.node_id, error_node.node_id, "encountered_issue")
        for label in frequent_solutions[:6]:
            solution_node = self.knowledge_graph.upsert_node(
                node_type="concept",
                name=label,
                metadata={"kind": "solution_pattern"},
            )
            self.knowledge_graph.add_edge(outcome_node.node_id, solution_node.node_id, "applies_pattern")

    def _insert_analysis(
        self,
        project_name: str,
        task: str,
        status: str,
        common_errors: list[str],
        frequent_solutions: list[str],
        metrics: dict[str, Any],
        files_changed: list[str],
        metadata: dict[str, Any],
        task_ref: int | None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO task_analysis (
                    project_name, task, status, common_errors, frequent_solutions,
                    metrics, files_changed, metadata, task_ref, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    project_name,
                    task,
                    status,
                    json.dumps(common_errors),
                    json.dumps(frequent_solutions),
                    json.dumps(metrics),
                    json.dumps(files_changed),
                    json.dumps(metadata),
                    task_ref,
                ),
            )
            return int(cursor.lastrowid)

    def _already_analyzed(self, task_ref: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM task_analysis WHERE task_ref = ? LIMIT 1",
                (task_ref,),
            ).fetchone()
        return row is not None

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_analysis (
                    analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    common_errors TEXT NOT NULL DEFAULT '[]',
                    frequent_solutions TEXT NOT NULL DEFAULT '[]',
                    metrics TEXT NOT NULL DEFAULT '{}',
                    files_changed TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    task_ref INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_analysis(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "analysis_id": int(row["analysis_id"]),
            "project_name": row["project_name"],
            "task": row["task"],
            "status": row["status"],
            "common_errors": json.loads(row["common_errors"] or "[]"),
            "frequent_solutions": json.loads(row["frequent_solutions"] or "[]"),
            "metrics": json.loads(row["metrics"] or "{}"),
            "files_changed": json.loads(row["files_changed"] or "[]"),
            "metadata": json.loads(row["metadata"] or "{}"),
            "task_ref": row["task_ref"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
