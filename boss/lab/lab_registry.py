from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from boss.lab.variant_generator import LabVariantDefinition


class LabRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_experiment(
        self,
        experiment_id: str,
        project_name: str,
        goal: str,
        primary_metric: str | None,
        metric_direction: str,
        benchmark_commands: list[str],
        allowed_paths: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO lab_experiments (
                    experiment_id, project_name, goal, status, primary_metric, metric_direction,
                    benchmark_commands, allowed_paths, metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    experiment_id,
                    project_name,
                    goal,
                    primary_metric,
                    metric_direction,
                    json.dumps(benchmark_commands),
                    json.dumps(allowed_paths),
                    json.dumps(metadata or {}),
                ),
            )

    def add_variant(self, experiment_id: str, variant: LabVariantDefinition) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO lab_variants (
                    variant_id, experiment_id, name, hypothesis, task_description, kind, mode, status,
                    benchmark_commands, allowed_paths, metadata, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    variant.variant_id,
                    experiment_id,
                    variant.name,
                    variant.hypothesis,
                    variant.task_description,
                    variant.kind,
                    variant.mode,
                    json.dumps(variant.benchmark_commands),
                    json.dumps(variant.allowed_paths),
                    json.dumps(variant.metadata),
                ),
            )

    def record_variant_result(
        self,
        variant_id: str,
        *,
        status: str,
        eval_run_id: int | None,
        runtime_seconds: float,
        sandbox_project_name: str | None,
        sandbox_path: str | None,
        sandbox_mode: str | None,
        branch_name: str | None,
        base_revision: str | None,
        changed_files: list[str],
        metrics: dict[str, Any],
        output_summary: str,
        errors: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE lab_variants
                SET status = ?, eval_run_id = ?, runtime_seconds = ?, sandbox_project_name = ?, sandbox_path = ?,
                    sandbox_mode = ?, branch_name = ?, base_revision = ?, changed_files = ?, metrics = ?,
                    output_summary = ?, errors = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE variant_id = ?
                """,
                (
                    status,
                    eval_run_id,
                    runtime_seconds,
                    sandbox_project_name,
                    sandbox_path,
                    sandbox_mode,
                    branch_name,
                    base_revision,
                    json.dumps(changed_files),
                    json.dumps(metrics),
                    output_summary,
                    json.dumps(errors),
                    json.dumps(metadata or {}),
                    variant_id,
                ),
            )

    def finalize_experiment(
        self,
        experiment_id: str,
        *,
        status: str,
        recommendation: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        recommendation = recommendation or {}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE lab_experiments
                SET status = ?, recommended_variant_id = ?, recommendation_reason = ?,
                    recommendation_confidence = ?, recommendation_metadata = ?, metadata = ?,
                    updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
                WHERE experiment_id = ?
                """,
                (
                    status,
                    recommendation.get("recommended_variant_id"),
                    recommendation.get("reason"),
                    recommendation.get("confidence"),
                    json.dumps(recommendation),
                    json.dumps(metadata or {}),
                    experiment_id,
                ),
            )

    def list_experiments(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM lab_experiments
                ORDER BY created_at DESC, experiment_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_experiment(row) for row in rows]

    def experiment_with_variants(self, experiment_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            experiment = conn.execute(
                "SELECT * FROM lab_experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            variants = conn.execute(
                """
                SELECT *
                FROM lab_variants
                WHERE experiment_id = ?
                ORDER BY created_at ASC, variant_id ASC
                """,
                (experiment_id,),
            ).fetchall()
        if experiment is None:
            return None
        payload = self._row_to_experiment(experiment)
        payload["variants"] = [self._row_to_variant(row) for row in variants]
        return payload

    def variant_with_experiment(self, variant_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    v.*,
                    e.project_name,
                    e.goal,
                    e.primary_metric,
                    e.metric_direction,
                    e.recommended_variant_id
                FROM lab_variants v
                JOIN lab_experiments e ON e.experiment_id = v.experiment_id
                WHERE v.variant_id = ?
                """,
                (variant_id,),
            ).fetchone()
        if row is None:
            return None
        payload = self._row_to_variant(row)
        payload["project_name"] = row["project_name"]
        payload["goal"] = row["goal"]
        payload["primary_metric"] = row["primary_metric"]
        payload["metric_direction"] = row["metric_direction"]
        payload["recommended_variant_id"] = row["recommended_variant_id"]
        return payload

    def mark_variant_applied(self, variant_id: str, metadata: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE lab_variants
                SET applied_at = CURRENT_TIMESTAMP, metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE variant_id = ?
                """,
                (json.dumps(metadata or {}), variant_id),
            )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lab_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    primary_metric TEXT,
                    metric_direction TEXT NOT NULL DEFAULT 'minimize',
                    benchmark_commands TEXT NOT NULL DEFAULT '[]',
                    allowed_paths TEXT NOT NULL DEFAULT '[]',
                    recommended_variant_id TEXT,
                    recommendation_reason TEXT,
                    recommendation_confidence REAL,
                    recommendation_metadata TEXT NOT NULL DEFAULT '{}',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS lab_variants (
                    variant_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    eval_run_id INTEGER,
                    runtime_seconds REAL NOT NULL DEFAULT 0,
                    sandbox_project_name TEXT,
                    sandbox_path TEXT,
                    sandbox_mode TEXT,
                    branch_name TEXT,
                    base_revision TEXT,
                    benchmark_commands TEXT NOT NULL DEFAULT '[]',
                    allowed_paths TEXT NOT NULL DEFAULT '[]',
                    changed_files TEXT NOT NULL DEFAULT '[]',
                    metrics TEXT NOT NULL DEFAULT '{}',
                    output_summary TEXT NOT NULL DEFAULT '',
                    errors TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    applied_at TEXT,
                    FOREIGN KEY (experiment_id) REFERENCES lab_experiments (experiment_id)
                );
                """
            )

    def _row_to_experiment(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "experiment_id": row["experiment_id"],
            "project_name": row["project_name"],
            "goal": row["goal"],
            "status": row["status"],
            "primary_metric": row["primary_metric"],
            "metric_direction": row["metric_direction"],
            "benchmark_commands": json.loads(row["benchmark_commands"] or "[]"),
            "allowed_paths": json.loads(row["allowed_paths"] or "[]"),
            "recommended_variant_id": row["recommended_variant_id"],
            "recommendation_reason": row["recommendation_reason"],
            "recommendation_confidence": float(row["recommendation_confidence"]) if row["recommendation_confidence"] is not None else None,
            "recommendation_metadata": json.loads(row["recommendation_metadata"] or "{}"),
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def _row_to_variant(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "variant_id": row["variant_id"],
            "experiment_id": row["experiment_id"],
            "name": row["name"],
            "hypothesis": row["hypothesis"],
            "task_description": row["task_description"],
            "kind": row["kind"],
            "mode": row["mode"],
            "status": row["status"],
            "eval_run_id": int(row["eval_run_id"]) if row["eval_run_id"] is not None else None,
            "runtime_seconds": float(row["runtime_seconds"] or 0.0),
            "sandbox_project_name": row["sandbox_project_name"],
            "sandbox_path": row["sandbox_path"],
            "sandbox_mode": row["sandbox_mode"],
            "branch_name": row["branch_name"],
            "base_revision": row["base_revision"],
            "benchmark_commands": json.loads(row["benchmark_commands"] or "[]"),
            "allowed_paths": json.loads(row["allowed_paths"] or "[]"),
            "changed_files": json.loads(row["changed_files"] or "[]"),
            "metrics": json.loads(row["metrics"] or "{}"),
            "output_summary": row["output_summary"],
            "errors": json.loads(row["errors"] or "[]"),
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "applied_at": row["applied_at"],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
