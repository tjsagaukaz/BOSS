from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from boss.learning.task_analyzer import TaskAnalyzer
from boss.memory.style_profile import StyleProfileStore


class PromptOptimizer:
    ROLE_FILES = {
        "architect": "architect_prompt.txt",
        "engineer": "engineer_prompt.txt",
        "auditor": "auditor_prompt.txt",
        "test": "test_prompt.txt",
        "security": "security_prompt.txt",
        "documentation": "documentation_prompt.txt",
    }

    def __init__(
        self,
        root_dir: str | Path,
        db_path: str | Path,
        task_analyzer: TaskAnalyzer,
        style_profile: StyleProfileStore,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.db_path = Path(db_path)
        self.task_analyzer = task_analyzer
        self.style_profile = style_profile
        self.prompts_dir = self.root_dir / "boss" / "prompts"
        self.optimized_dir = self.prompts_dir / "optimized"
        self.optimized_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def optimize(
        self,
        project_name: str | None = None,
        roles: list[str] | None = None,
        write_files: bool = True,
    ) -> dict[str, Any]:
        target_roles = roles or list(self.ROLE_FILES.keys())
        task_summary = self.task_analyzer.summary(project_name=project_name)
        style = self.style_profile.get_effective_profile(project_name)
        results: list[dict[str, Any]] = []
        for role in target_roles:
            if role not in self.ROLE_FILES:
                continue
            base_path = self.prompts_dir / self.ROLE_FILES[role]
            base_prompt = base_path.read_text(encoding="utf-8").strip()
            instructions = self._role_instructions(role, task_summary, style)
            optimized_prompt = self._compose_prompt(base_prompt, instructions)
            latest = self._latest_for_role(role)
            version = self._next_version(role)
            changed = latest is None or latest.get("prompt_text") != optimized_prompt
            if write_files:
                (self.optimized_dir / self.ROLE_FILES[role]).write_text(optimized_prompt + "\n", encoding="utf-8")
                if changed:
                    self._store_optimization(
                        role=role,
                        project_name=project_name,
                        version=version,
                        prompt_text=optimized_prompt,
                        instructions=instructions,
                        metrics=task_summary,
                    )
                else:
                    version = int(latest.get("version", version)) if latest else version
            elif latest is not None:
                version = int(latest.get("version", version))
            results.append(
                {
                    "role": role,
                    "version": version,
                    "instructions": instructions,
                    "path": str(self.optimized_dir / self.ROLE_FILES[role]),
                    "changed": changed,
                }
            )
        return {
            "project_name": project_name,
            "optimizations": results,
            "summary": task_summary,
        }

    def latest_optimizations(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, project_name, version, prompt_text, instructions, metrics, created_at, updated_at
                FROM prompt_optimizations
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_optimization(row) for row in rows]

    def metrics(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS optimization_count, MAX(updated_at) AS last_optimized_at
                FROM prompt_optimizations
                """
            ).fetchone()
        return {
            "optimization_count": int(row["optimization_count"]) if row else 0,
            "last_optimized_at": row["last_optimized_at"] if row else None,
        }

    def _role_instructions(self, role: str, summary: dict[str, Any], style) -> list[str]:
        common_errors = list(summary.get("common_errors", []))
        frequent_solutions = list(summary.get("frequent_solutions", []))
        failed = int(summary.get("tasks_failed", 0))
        completed = int(summary.get("tasks_completed", 0))
        failure_pressure = failed > 0 and failed >= max(1, completed // 3)

        instructions: list[str] = []
        if role == "engineer":
            instructions.extend(
                [
                    "Use the stored style profile before writing code, especially naming and test structure.",
                    "When a task changes behavior, update or add the smallest relevant tests in the same pass.",
                ]
            )
            if failure_pressure:
                instructions.append("Inspect the exact target file and symbol before any write tool call.")
            if "test failure" in common_errors:
                instructions.append("Run the narrowest relevant tests before finalizing the implementation summary.")
            if "missing symbol or path" in common_errors:
                instructions.append("Verify paths and symbol names with search_codebase or jump_to_symbol before editing.")
        elif role == "architect":
            instructions.extend(
                [
                    "Produce plans that isolate risk and keep the first step directly executable.",
                    "Prefer plans that call out existing modules and files instead of abstract layers.",
                ]
            )
            if "security issue" in common_errors:
                instructions.append("Include explicit security validation steps for auth, permissions, and webhook inputs.")
        elif role == "auditor":
            instructions.extend(
                [
                    "Bias toward concrete failure modes and cite the smallest risky file or symbol you can identify.",
                    "Treat missing tests for changed behavior as a review finding when appropriate.",
                ]
            )
            if "test failure" in common_errors:
                instructions.append("Use failing test output as primary evidence when it is available.")
        elif role == "test":
            instructions.extend(
                [
                    "Prefer the project's existing test framework and directory style instead of introducing a new one.",
                    "Focus coverage on the changed code paths and the main failure cases.",
                ]
            )
        elif role == "security":
            instructions.extend(
                [
                    "Prioritize auth boundaries, secrets handling, input validation, and webhook verification paths.",
                    "Prefer actionable fixes over generic policy commentary.",
                ]
            )
        elif role == "documentation":
            instructions.extend(
                [
                    "Document changed entry points, setup steps, and operational behavior with minimal filler.",
                    "Keep docs aligned with the actual command names and file paths in the repository.",
                ]
            )

        if frequent_solutions:
            instructions.append(
                "Reuse established solution patterns when they fit: " + ", ".join(frequent_solutions[:4]) + "."
            )
        if style is not None:
            style_notes = [style.indentation]
            if style.naming_conventions:
                style_notes.append(", ".join(style.naming_conventions[:2]))
            if style.test_style:
                style_notes.append(style.test_style)
            instructions.append("Follow the dominant code style: " + "; ".join(style_notes) + ".")

        deduped = []
        seen = set()
        for item in instructions:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped[:8]

    def _compose_prompt(self, base_prompt: str, instructions: list[str]) -> str:
        if not instructions:
            return base_prompt
        lines = [base_prompt, "", "Optimized Guidance:"]
        lines.extend(f"- {item}" for item in instructions)
        return "\n".join(lines).strip()

    def _store_optimization(
        self,
        role: str,
        project_name: str | None,
        version: int,
        prompt_text: str,
        instructions: list[str],
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prompt_optimizations (
                    role, project_name, version, prompt_text, instructions, metrics, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    role,
                    project_name or "",
                    version,
                    prompt_text,
                    json.dumps(instructions),
                    json.dumps(metrics),
                ),
            )

    def _next_version(self, role: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM prompt_optimizations WHERE role = ?",
                (role,),
            ).fetchone()
        return int(row["version"] or 0) + 1

    def _latest_for_role(self, role: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT role, project_name, version, prompt_text, instructions, metrics, created_at, updated_at
                FROM prompt_optimizations
                WHERE role = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (role,),
            ).fetchone()
        return self._row_to_optimization(row) if row else None

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_optimizations (
                    optimization_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    project_name TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL,
                    prompt_text TEXT NOT NULL,
                    instructions TEXT NOT NULL DEFAULT '[]',
                    metrics TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_optimization(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "role": row["role"],
            "project_name": row["project_name"] or None,
            "version": int(row["version"]),
            "prompt_text": row["prompt_text"],
            "instructions": json.loads(row["instructions"] or "[]"),
            "metrics": json.loads(row["metrics"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
