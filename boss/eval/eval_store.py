from __future__ import annotations

import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from boss.types import EvalRunResult, EvalTaskResult, ValidationOutcome


class EvaluationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_run(
        self,
        suite_name: str,
        suite_path: str,
        project_name: str,
        total_tasks: int,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO eval_runs (
                    suite_name, suite_path, project_name, status, total_tasks,
                    passed_tasks, failed_tasks, runtime_seconds, total_estimated_cost_usd,
                    metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, 'running', ?, 0, 0, 0, NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (suite_name, suite_path, project_name, total_tasks, json.dumps(metadata or {})),
            )
            return int(cursor.lastrowid)

    def record_task_result(self, run_id: int, result: EvalTaskResult) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO eval_task_results (
                    run_id, task_name, description, project_name, mode, status,
                    runtime_seconds, files_changed, errors, failure_category, output_summary,
                    validations, model_usage, token_usage, estimated_cost_usd, metadata,
                    created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.task_name,
                    result.description,
                    result.project_name,
                    result.mode,
                    result.status,
                    result.runtime_seconds,
                    json.dumps(result.files_changed),
                    json.dumps(result.errors),
                    result.failure_category,
                    result.output_summary,
                    json.dumps([_validation_payload(item) for item in result.validations]),
                    json.dumps(result.model_usage),
                    json.dumps(result.token_usage),
                    result.estimated_cost_usd,
                    json.dumps(result.metadata),
                    result.started_at,
                    result.completed_at,
                    result.completed_at,
                ),
            )
            return int(cursor.lastrowid)

    def finalize_run(
        self,
        run_id: int,
        status: str,
        passed_tasks: int,
        failed_tasks: int,
        runtime_seconds: float,
        total_estimated_cost_usd: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE eval_runs
                SET status = ?, passed_tasks = ?, failed_tasks = ?, runtime_seconds = ?,
                    total_estimated_cost_usd = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                """,
                (
                    status,
                    passed_tasks,
                    failed_tasks,
                    runtime_seconds,
                    total_estimated_cost_usd,
                    json.dumps(metadata or {}),
                    run_id,
                ),
            )

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM eval_runs
                ORDER BY run_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def run_with_tasks(self, run_id: int) -> EvalRunResult | None:
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM eval_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            task_rows = conn.execute(
                """
                SELECT *
                FROM eval_task_results
                WHERE run_id = ?
                ORDER BY eval_task_result_id ASC
                """,
                (run_id,),
            ).fetchall()
        if run_row is None:
            return None
        run = self._row_to_run(run_row)
        tasks = [self._row_to_task_result(row) for row in task_rows]
        return EvalRunResult(
            run_id=run["run_id"],
            suite_name=run["suite_name"],
            suite_path=run["suite_path"],
            project_name=run["project_name"],
            status=run["status"],
            total_tasks=run["total_tasks"],
            passed_tasks=run["passed_tasks"],
            failed_tasks=run["failed_tasks"],
            runtime_seconds=run["runtime_seconds"],
            total_estimated_cost_usd=run["total_estimated_cost_usd"],
            tasks=tasks,
            metadata=run["metadata"],
            started_at=run["created_at"],
            completed_at=run.get("completed_at") or run["updated_at"],
        )

    def failure_map_summary(self, project_name: str | None = None, limit: int = 20) -> dict[str, Any]:
        runs = self.recent_runs(limit=limit)
        counts: dict[str, int] = {}
        recent: list[dict[str, Any]] = []
        for run in runs:
            if project_name and run["project_name"] != project_name:
                continue
            detailed = self.run_with_tasks(int(run["run_id"]))
            if detailed is None:
                continue
            task_summaries: list[dict[str, Any]] = []
            for task in detailed.tasks:
                metadata = task.metadata or {}
                failure_map = [str(item) for item in metadata.get("failure_map", []) if str(item).strip()]
                for label in failure_map:
                    counts[label] = counts.get(label, 0) + 1
                task_summaries.append(
                    {
                        "task_name": task.task_name,
                        "status": task.status,
                        "failure_category": task.failure_category,
                        "failure_map_primary": metadata.get("failure_map_primary"),
                    }
                )
            if task_summaries:
                recent.append(
                    {
                        "run_id": detailed.run_id,
                        "suite_name": detailed.suite_name,
                        "project_name": detailed.project_name,
                        "status": detailed.status,
                        "tasks": task_summaries,
                    }
                )
        ordered_counts = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
        return {
            "counts": ordered_counts,
            "recent": recent[:10],
        }

    def success_metrics(self, project_name: str | None = None, limit: int = 50) -> dict[str, Any]:
        runs = self.recent_runs(limit=limit)
        filtered = [run for run in runs if not project_name or run["project_name"] == project_name]
        total_runs = len(filtered)
        passed_runs = sum(1 for run in filtered if run["status"] == "passed")
        failed_runs = sum(1 for run in filtered if run["status"] == "failed")
        skipped_runs = sum(1 for run in filtered if run["status"] == "skipped")
        aborted_runs = sum(1 for run in filtered if run["status"] == "aborted")
        executed_runs = passed_runs + failed_runs
        total_tasks = sum(int(run["total_tasks"]) for run in filtered)
        passed_tasks = sum(int(run["passed_tasks"]) for run in filtered)
        failed_tasks = sum(int(run["failed_tasks"]) for run in filtered)
        runtimes = [float(run["runtime_seconds"] or 0.0) for run in filtered]
        return {
            "run_count": total_runs,
            "executed_runs": executed_runs,
            "passed_runs": passed_runs,
            "failed_runs": failed_runs,
            "skipped_runs": skipped_runs,
            "aborted_runs": aborted_runs,
            "run_success_rate": float(passed_runs / executed_runs) if executed_runs else None,
            "total_tasks": total_tasks,
            "passed_tasks": passed_tasks,
            "failed_tasks": failed_tasks,
            "task_success_rate": float(passed_tasks / total_tasks) if total_tasks else None,
            "avg_runtime_seconds": float(sum(runtimes) / len(runtimes)) if runtimes else None,
            "median_runtime_seconds": float(statistics.median(runtimes)) if runtimes else None,
        }

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS eval_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suite_name TEXT NOT NULL,
                    suite_path TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_tasks INTEGER NOT NULL DEFAULT 0,
                    passed_tasks INTEGER NOT NULL DEFAULT 0,
                    failed_tasks INTEGER NOT NULL DEFAULT 0,
                    runtime_seconds REAL NOT NULL DEFAULT 0,
                    total_estimated_cost_usd REAL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS eval_task_results (
                    eval_task_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    task_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    runtime_seconds REAL NOT NULL DEFAULT 0,
                    files_changed TEXT NOT NULL DEFAULT '[]',
                    errors TEXT NOT NULL DEFAULT '[]',
                    failure_category TEXT,
                    output_summary TEXT NOT NULL DEFAULT '',
                    validations TEXT NOT NULL DEFAULT '[]',
                    model_usage TEXT NOT NULL DEFAULT '[]',
                    token_usage TEXT NOT NULL DEFAULT '{}',
                    estimated_cost_usd REAL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )

    def _row_to_run(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": int(row["run_id"]),
            "suite_name": row["suite_name"],
            "suite_path": row["suite_path"],
            "project_name": row["project_name"],
            "status": row["status"],
            "total_tasks": int(row["total_tasks"]),
            "passed_tasks": int(row["passed_tasks"]),
            "failed_tasks": int(row["failed_tasks"]),
            "runtime_seconds": float(row["runtime_seconds"] or 0.0),
            "total_estimated_cost_usd": float(row["total_estimated_cost_usd"]) if row["total_estimated_cost_usd"] is not None else None,
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def _row_to_task_result(self, row: sqlite3.Row) -> EvalTaskResult:
        validations = []
        for item in json.loads(row["validations"] or "[]"):
            if isinstance(item, dict):
                validations.append(
                    ValidationOutcome(
                        name=str(item.get("name", "")),
                        passed=bool(item.get("passed", False)),
                        message=str(item.get("message", "")),
                    )
                )
        return EvalTaskResult(
            task_name=row["task_name"],
            description=row["description"],
            project_name=row["project_name"],
            mode=row["mode"],
            status=row["status"],
            runtime_seconds=float(row["runtime_seconds"] or 0.0),
            files_changed=json.loads(row["files_changed"] or "[]"),
            errors=json.loads(row["errors"] or "[]"),
            failure_category=row["failure_category"],
            output_summary=row["output_summary"],
            validations=validations,
            model_usage=json.loads(row["model_usage"] or "[]"),
            token_usage=json.loads(row["token_usage"] or "{}"),
            estimated_cost_usd=float(row["estimated_cost_usd"]) if row["estimated_cost_usd"] is not None else None,
            metadata=json.loads(row["metadata"] or "{}"),
            started_at=row["created_at"],
            completed_at=row["completed_at"] or row["updated_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _validation_payload(item: ValidationOutcome) -> dict[str, Any]:
    return {"name": item.name, "passed": item.passed, "message": item.message}
