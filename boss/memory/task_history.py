from __future__ import annotations

import json
import os
import sqlite3
import statistics
from pathlib import Path
from typing import Any


class TaskHistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_task(self, project_name: str, task: str) -> int:
        metadata = json.dumps(
            {
                "owner_pid": os.getpid(),
                "owner_runtime": "boss",
            }
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO task_history (
                    project_name, task, status, current_step_index, total_steps,
                    plan, files_changed, errors, final_result, stop_requested,
                    runtime_seconds, model_usage, token_usage, estimated_cost_usd, metadata,
                    created_at, updated_at
                )
                VALUES (?, ?, 'running', -1, 0, '', '[]', '[]', '', 0, 0, '[]', '{}', NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (project_name, task, metadata),
            )
            return int(cursor.lastrowid)

    def set_plan(self, task_id: int, plan: dict[str, Any]) -> None:
        steps = plan.get("steps", [])
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_history
                SET plan = ?, total_steps = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(plan), len(steps), task_id),
            )

    def start_step(self, task_id: int, step_index: int, title: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_steps (
                    task_id, step_index, title, status, engineer_output, test_output,
                    audit_output, files_changed, errors, commit_message, iterations,
                    runtime_seconds, model_usage, token_usage, estimated_cost_usd, tool_errors, metadata,
                    started_at, updated_at
                )
                VALUES (?, ?, ?, 'running', '', '', '', '[]', '[]', '', 0, 0, '[]', '{}', NULL, '[]', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(task_id, step_index) DO UPDATE SET
                    title = excluded.title,
                    status = 'running',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (task_id, step_index, title),
            )
            conn.execute(
                """
                UPDATE task_history
                SET current_step_index = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (step_index, task_id),
            )

    def record_step_attempt(
        self,
        task_id: int,
        step_index: int,
        engineer_output: str,
        test_output: dict[str, Any],
        audit_output: str,
        files_changed: list[str],
        errors: list[str],
        iterations: int,
        runtime_seconds: float = 0.0,
        model_usage: list[dict[str, Any]] | None = None,
        token_usage: dict[str, int] | None = None,
        estimated_cost_usd: float | None = None,
        tool_errors: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_steps
                SET engineer_output = ?, test_output = ?, audit_output = ?,
                    files_changed = ?, errors = ?, iterations = ?, runtime_seconds = ?,
                    model_usage = ?, token_usage = ?, estimated_cost_usd = ?, tool_errors = ?, metadata = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND step_index = ?
                """,
                (
                    engineer_output,
                    json.dumps(test_output),
                    audit_output,
                    json.dumps(files_changed),
                    json.dumps(errors),
                    iterations,
                    runtime_seconds,
                    json.dumps(model_usage or []),
                    json.dumps(token_usage or {}),
                    estimated_cost_usd,
                    json.dumps(tool_errors or []),
                    json.dumps(metadata or {}),
                    task_id,
                    step_index,
                ),
            )

    def complete_step(
        self,
        task_id: int,
        step_index: int,
        files_changed: list[str],
        commit_message: str,
        iterations: int,
        engineer_output: str,
        test_output: dict[str, Any],
        audit_output: str,
        runtime_seconds: float = 0.0,
        model_usage: list[dict[str, Any]] | None = None,
        token_usage: dict[str, int] | None = None,
        estimated_cost_usd: float | None = None,
        tool_errors: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_steps
                SET status = 'completed',
                    files_changed = ?, commit_message = ?, iterations = ?,
                    engineer_output = ?, test_output = ?, audit_output = ?, runtime_seconds = ?,
                    model_usage = ?, token_usage = ?, estimated_cost_usd = ?, tool_errors = ?, metadata = ?,
                    updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND step_index = ?
                """,
                (
                    json.dumps(files_changed),
                    commit_message,
                    iterations,
                    engineer_output,
                    json.dumps(test_output),
                    audit_output,
                    runtime_seconds,
                    json.dumps(model_usage or []),
                    json.dumps(token_usage or {}),
                    estimated_cost_usd,
                    json.dumps(tool_errors or []),
                    json.dumps(metadata or {}),
                    task_id,
                    step_index,
                ),
            )

    def fail_step(
        self,
        task_id: int,
        step_index: int,
        errors: list[str],
        iterations: int,
        engineer_output: str,
        test_output: dict[str, Any],
        audit_output: str,
        runtime_seconds: float = 0.0,
        model_usage: list[dict[str, Any]] | None = None,
        token_usage: dict[str, int] | None = None,
        estimated_cost_usd: float | None = None,
        tool_errors: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_steps
                SET status = 'failed',
                    errors = ?, iterations = ?,
                    engineer_output = ?, test_output = ?, audit_output = ?, runtime_seconds = ?,
                    model_usage = ?, token_usage = ?, estimated_cost_usd = ?, tool_errors = ?, metadata = ?,
                    updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND step_index = ?
                """,
                (
                    json.dumps(errors),
                    iterations,
                    engineer_output,
                    json.dumps(test_output),
                    audit_output,
                    runtime_seconds,
                    json.dumps(model_usage or []),
                    json.dumps(token_usage or {}),
                    estimated_cost_usd,
                    json.dumps(tool_errors or []),
                    json.dumps(metadata or {}),
                    task_id,
                    step_index,
                ),
            )

    def finalize_task(
        self,
        task_id: int,
        status: str,
        files_changed: list[str],
        errors: list[str],
        final_result: str,
        runtime_seconds: float = 0.0,
        model_usage: list[dict[str, Any]] | None = None,
        token_usage: dict[str, int] | None = None,
        estimated_cost_usd: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_history
                SET status = ?, files_changed = ?, errors = ?, final_result = ?,
                    runtime_seconds = ?, model_usage = ?, token_usage = ?, estimated_cost_usd = ?, metadata = ?,
                    updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(files_changed),
                    json.dumps(errors),
                    final_result,
                    runtime_seconds,
                    json.dumps(model_usage or []),
                    json.dumps(token_usage or {}),
                    estimated_cost_usd,
                    json.dumps(metadata or {}),
                    task_id,
                ),
            )

    def abort_task(self, task_id: int, reason: str = "Task runtime was interrupted or is no longer active.") -> None:
        task = self.get_task(task_id)
        if task is None or str(task.get("status", "")).lower() != "running":
            return
        errors = list(task.get("errors", []))
        if reason not in errors:
            errors.append(reason)
        metadata = dict(task.get("metadata", {}) or {})
        metadata["abort_reason"] = reason
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_steps
                SET status = 'aborted',
                    errors = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND status = 'running'
                """,
                (json.dumps([reason]), task_id),
            )
            conn.execute(
                """
                UPDATE task_history
                SET status = 'aborted',
                    errors = ?,
                    final_result = ?,
                    stop_requested = 0,
                    metadata = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running'
                """,
                (
                    json.dumps(errors),
                    reason,
                    json.dumps(metadata),
                    task_id,
                ),
            )

    def merge_task_metadata(self, task_id: int, metadata: dict[str, Any]) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata FROM task_history WHERE id = ?",
                (task_id,),
            ).fetchone()
            current = json.loads(row["metadata"] or "{}") if row else {}
            merged = {**current, **(metadata or {})}
            conn.execute(
                """
                UPDATE task_history
                SET metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(merged), task_id),
            )

    def merge_step_metadata(
        self,
        task_id: int,
        step_index: int,
        metadata: dict[str, Any],
        *,
        commit_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT metadata, commit_message
                FROM task_steps
                WHERE task_id = ? AND step_index = ?
                """,
                (task_id, step_index),
            ).fetchone()
            current = json.loads(row["metadata"] or "{}") if row else {}
            merged = {**current, **(metadata or {})}
            next_commit_message = commit_message if commit_message is not None else (row["commit_message"] if row else "")
            conn.execute(
                """
                UPDATE task_steps
                SET metadata = ?, commit_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND step_index = ?
                """,
                (json.dumps(merged), next_commit_message or "", task_id, step_index),
            )

    def request_stop(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_history
                SET stop_requested = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )

    def clear_stop(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_history
                SET stop_requested = 0, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )

    def is_stop_requested(self, task_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stop_requested FROM task_history WHERE id = ?",
                (task_id,),
            ).fetchone()
        return bool(row and row["stop_requested"])

    def latest_task(self, project_name: str | None = None, running_only: bool = False) -> dict[str, Any] | None:
        query = "SELECT * FROM task_history WHERE 1 = 1"
        params: list[Any] = []
        if project_name:
            query += " AND project_name = ?"
            params.append(project_name)
        if running_only:
            query += " AND status = 'running'"
        query += " ORDER BY id DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_to_task(row) if row else None

    def recent_tasks(self, project_name: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        query = "SELECT * FROM task_history WHERE 1 = 1"
        params: list[Any] = []
        if project_name:
            query += " AND project_name = ?"
            params.append(project_name)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def running_tasks(self, project_name: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM task_history WHERE status = 'running'"
        params: list[Any] = []
        if project_name:
            query += " AND project_name = ?"
            params.append(project_name)
        query += " ORDER BY id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def stale_running_tasks(self, project_name: str | None = None) -> list[dict[str, Any]]:
        stale: list[dict[str, Any]] = []
        for task in self.running_tasks(project_name=project_name, limit=None):
            metadata = task.get("metadata", {}) or {}
            owner_pid = metadata.get("owner_pid")
            if owner_pid is None:
                stale.append(task)
                continue
            try:
                pid = int(owner_pid)
            except (TypeError, ValueError):
                stale.append(task)
                continue
            if not self._process_alive(pid):
                stale.append(task)
        return stale

    def reconcile_stale_tasks(self, project_name: str | None = None) -> list[dict[str, Any]]:
        stale = self.stale_running_tasks(project_name=project_name)
        reconciled: list[dict[str, Any]] = []
        for task in stale:
            self.abort_task(
                int(task["id"]),
                reason="Task runtime was interrupted before completion; marked as aborted during reconciliation.",
            )
            refreshed = self.get_task(int(task["id"]))
            if refreshed is not None:
                reconciled.append(refreshed)
        return reconciled

    def search_tasks(
        self,
        query: str,
        embeddings,
        project_name: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        candidates = self.recent_tasks(project_name=project_name, limit=200)
        if not candidates:
            return []
        query_vector = embeddings.embed(query)
        results: list[dict[str, Any]] = []
        for task in candidates:
            text = f"{task['task']}\n{task['final_result']}\n{' '.join(task.get('files_changed', []))}"
            score = embeddings.cosine_similarity(query_vector, embeddings.embed(text))
            results.append(
                {
                    "id": task["id"],
                    "project_name": task["project_name"],
                    "task": task["task"],
                    "status": task["status"],
                    "final_result": task["final_result"],
                    "files_changed": task.get("files_changed", []),
                    "score": score,
                    "completed_at": task.get("completed_at"),
                }
            )
        results.sort(key=lambda item: (item["score"], item["id"]), reverse=True)
        return results[:limit]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM task_history WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_steps
                WHERE task_id = ?
                ORDER BY step_index ASC
                """,
                (task_id,),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def task_with_steps(self, task_id: int) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        task["steps"] = self.list_steps(task_id)
        return task

    def failure_map_summary(self, project_name: str | None = None, limit: int = 50) -> dict[str, Any]:
        tasks = self.recent_tasks(project_name=project_name, limit=limit)
        counts: dict[str, int] = {}
        recent: list[dict[str, Any]] = []
        for task in tasks:
            metadata = task.get("metadata", {})
            failure_counts = metadata.get("failure_map_counts", {})
            counted_from_task = False
            if isinstance(failure_counts, dict):
                for label, value in failure_counts.items():
                    counts[str(label)] = counts.get(str(label), 0) + int(value or 0)
                counted_from_task = True

            primary = None
            step_summaries: list[dict[str, Any]] = []
            for step in self.list_steps(int(task["id"])):
                step_metadata = step.get("metadata", {})
                step_primary = step_metadata.get("failure_map_primary")
                if primary is None and step_primary:
                    primary = str(step_primary)
                step_summaries.append(
                    {
                        "step_index": step["step_index"],
                        "title": step["title"],
                        "status": step["status"],
                        "failure_map_primary": step_primary,
                    }
                )
                if not counted_from_task:
                    for label in step_metadata.get("failure_map", []):
                        counts[str(label)] = counts.get(str(label), 0) + 1

            if primary or task.get("status") in {"failed", "stopped", "aborted"}:
                recent.append(
                    {
                        "task_id": task["id"],
                        "task": task["task"],
                        "status": task["status"],
                        "failure_map_primary": primary,
                        "step_failures": step_summaries,
                    }
                )

        ordered_counts = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
        return {
            "counts": ordered_counts,
            "recent": recent[:10],
        }

    def success_metrics(self, project_name: str | None = None, limit: int = 100) -> dict[str, Any]:
        tasks = self.recent_tasks(project_name=project_name, limit=limit)
        attempted = 0
        completed = 0
        failed = 0
        stopped = 0
        aborted = 0
        running = 0
        runtimes: list[float] = []
        iteration_values: list[int] = []
        step_attempted = 0
        step_completed = 0
        step_failed = 0
        step_stopped = 0
        for task in tasks:
            status = str(task.get("status", "")).strip().lower()
            if status == "running":
                running += 1
                continue
            if status not in {"completed", "failed", "stopped", "aborted"}:
                continue
            attempted += 1
            if status == "completed":
                completed += 1
            elif status == "failed":
                failed += 1
            elif status == "stopped":
                stopped += 1
            elif status == "aborted":
                aborted += 1
            runtimes.append(float(task.get("runtime_seconds", 0.0) or 0.0))
            for step in self.list_steps(int(task["id"])):
                step_attempted += 1
                step_status = str(step.get("status", "")).strip().lower()
                if step_status == "completed":
                    step_completed += 1
                elif step_status == "failed":
                    step_failed += 1
                elif step_status == "stopped":
                    step_stopped += 1
                elif step_status == "aborted":
                    step_stopped += 1
                iteration_values.append(int(step.get("iterations", 0) or 0))
        success_rate = float(completed / attempted) if attempted else None
        return {
            "attempted": attempted,
            "completed": completed,
            "failed": failed,
            "stopped": stopped,
            "aborted": aborted,
            "running": running,
            "success_rate": success_rate,
            "avg_iterations": float(sum(iteration_values) / len(iteration_values)) if iteration_values else None,
            "avg_step_iterations": float(sum(iteration_values) / len(iteration_values)) if iteration_values else None,
            "step_attempted": step_attempted,
            "step_completed": step_completed,
            "step_failed": step_failed,
            "step_stopped": step_stopped,
            "step_success_rate": float(step_completed / step_attempted) if step_attempted else None,
            "avg_runtime_seconds": float(sum(runtimes) / len(runtimes)) if runtimes else None,
            "median_runtime_seconds": float(statistics.median(runtimes)) if runtimes else None,
        }

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    task TEXT NOT NULL,
                    plan TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    current_step_index INTEGER NOT NULL DEFAULT -1,
                    total_steps INTEGER NOT NULL DEFAULT 0,
                    files_changed TEXT NOT NULL DEFAULT '[]',
                    errors TEXT NOT NULL DEFAULT '[]',
                    final_result TEXT NOT NULL DEFAULT '',
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    runtime_seconds REAL NOT NULL DEFAULT 0,
                    model_usage TEXT NOT NULL DEFAULT '[]',
                    token_usage TEXT NOT NULL DEFAULT '{}',
                    estimated_cost_usd REAL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS task_steps (
                    task_id INTEGER NOT NULL,
                    step_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    engineer_output TEXT NOT NULL DEFAULT '',
                    test_output TEXT NOT NULL DEFAULT '{}',
                    audit_output TEXT NOT NULL DEFAULT '',
                    files_changed TEXT NOT NULL DEFAULT '[]',
                    errors TEXT NOT NULL DEFAULT '[]',
                    commit_message TEXT NOT NULL DEFAULT '',
                    iterations INTEGER NOT NULL DEFAULT 0,
                    runtime_seconds REAL NOT NULL DEFAULT 0,
                    model_usage TEXT NOT NULL DEFAULT '[]',
                    token_usage TEXT NOT NULL DEFAULT '{}',
                    estimated_cost_usd REAL,
                    tool_errors TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (task_id, step_index)
                );
                """
            )
            self._ensure_column(conn, "task_history", "runtime_seconds", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "task_history", "model_usage", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "task_history", "token_usage", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "task_history", "estimated_cost_usd", "REAL")
            self._ensure_column(conn, "task_history", "metadata", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "task_steps", "runtime_seconds", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "task_steps", "model_usage", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "task_steps", "token_usage", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "task_steps", "estimated_cost_usd", "REAL")
            self._ensure_column(conn, "task_steps", "tool_errors", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "task_steps", "metadata", "TEXT NOT NULL DEFAULT '{}'")

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "project_name": row["project_name"],
            "task": row["task"],
            "plan": json.loads(row["plan"] or "{}") if row["plan"] else {},
            "status": row["status"],
            "current_step_index": int(row["current_step_index"]),
            "total_steps": int(row["total_steps"]),
            "files_changed": json.loads(row["files_changed"] or "[]"),
            "errors": json.loads(row["errors"] or "[]"),
            "final_result": row["final_result"],
            "stop_requested": bool(row["stop_requested"]),
            "runtime_seconds": float(row["runtime_seconds"] or 0.0),
            "model_usage": json.loads(row["model_usage"] or "[]"),
            "token_usage": json.loads(row["token_usage"] or "{}"),
            "estimated_cost_usd": float(row["estimated_cost_usd"]) if row["estimated_cost_usd"] is not None else None,
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def _row_to_step(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": int(row["task_id"]),
            "step_index": int(row["step_index"]),
            "title": row["title"],
            "status": row["status"],
            "engineer_output": row["engineer_output"],
            "test_output": json.loads(row["test_output"] or "{}"),
            "audit_output": row["audit_output"],
            "files_changed": json.loads(row["files_changed"] or "[]"),
            "errors": json.loads(row["errors"] or "[]"),
            "commit_message": row["commit_message"],
            "iterations": int(row["iterations"]),
            "runtime_seconds": float(row["runtime_seconds"] or 0.0),
            "model_usage": json.loads(row["model_usage"] or "[]"),
            "token_usage": json.loads(row["token_usage"] or "{}"),
            "estimated_cost_usd": float(row["estimated_cost_usd"]) if row["estimated_cost_usd"] is not None else None,
            "tool_errors": json.loads(row["tool_errors"] or "[]"),
            "metadata": json.loads(row["metadata"] or "{}"),
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _process_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row[1] == column for row in rows):
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
