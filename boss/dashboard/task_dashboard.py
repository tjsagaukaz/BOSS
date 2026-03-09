from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from boss.types import StepExecutionResult, StructuredPlan


class TaskDashboard:
    def render_live(
        self,
        project_name: str,
        plan: StructuredPlan,
        step_results: list[StepExecutionResult],
        active_message: str = "",
    ):
        table = Table(title="BOSS TASK DASHBOARD")
        table.add_column("Step")
        table.add_column("Status")
        table.add_column("Iterations", justify="right")
        table.add_column("Files")

        results_by_step = {result.step_index: result for result in step_results}
        for index, title in enumerate(plan.steps):
            result = results_by_step.get(index)
            status = self._status_icon(result.status) if result else "•"
            label = result.status if result else "pending"
            files = ", ".join((result.changed_files if result else [])[:3])
            table.add_row(
                f"{index + 1}. {title}",
                f"{status} {label}",
                str(result.iterations if result else 0),
                files,
            )

        header = (
            f"Project: {project_name}\n"
            f"Goal: {plan.goal or 'Pending'}\n"
            f"{active_message or 'Starting...'}"
        )
        return Panel(table, title=header, expand=True)

    def render_task(self, task: dict[str, Any] | None):
        if task is None:
            return Panel("No task history available.", title="BOSS TASK DASHBOARD", expand=False)

        plan = task.get("plan") or {}
        steps = list(plan.get("steps", []))
        step_rows = {step.get("step_index", 0): step for step in task.get("steps", [])}
        table = Table(title="BOSS TASK DASHBOARD")
        table.add_column("Step")
        table.add_column("Status")
        table.add_column("Iterations", justify="right")
        table.add_column("Files")
        table.add_column("Failure Map")

        if steps:
            for index, title in enumerate(steps):
                step = step_rows.get(index)
                status = self._status_icon(step.get("status", "pending") if step else "pending")
                label = step.get("status", "pending") if step else "pending"
                files = ", ".join((step.get("files_changed", []) if step else [])[:3])
                failure_map = ""
                if step:
                    failure_map = str((step.get("metadata", {}) or {}).get("failure_map_primary") or "")
                table.add_row(
                    f"{index + 1}. {title}",
                    f"{status} {label}",
                    str(step.get("iterations", 0) if step else 0),
                    files,
                    failure_map,
                )
        else:
            table.add_row("1. No plan available", "• pending", "0", "", "")

        header = (
            f"Project: {task.get('project_name', 'Unknown')}\n"
            f"Goal: {task.get('task', '')}\n"
            f"Status: {task.get('status', 'unknown')}"
        )
        metadata = task.get("metadata", {}) or {}
        failure_counts = metadata.get("failure_map_counts", {}) or {}
        summary = "No failure telemetry recorded."
        if failure_counts:
            ordered = sorted(failure_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
            summary = ", ".join(f"{name}={count}" for name, count in ordered)
        summary_panel = Panel(summary, title="Failure Map", expand=False)
        return Group(Panel(table, title=header, expand=True), summary_panel)

    def render_reliability(self, snapshot: dict[str, Any] | None):
        snapshot = snapshot or {}
        task_section = (snapshot.get("tasks") or {}) if isinstance(snapshot, dict) else {}
        eval_section = (snapshot.get("evaluations") or {}) if isinstance(snapshot, dict) else {}
        task_counts = task_section.get("counts") or {}
        eval_counts = eval_section.get("counts") or {}
        task_metrics = task_section.get("metrics") or {}
        eval_metrics = eval_section.get("metrics") or {}
        table = Table(title="Reliability Snapshot")
        table.add_column("Source")
        table.add_column("Success")
        table.add_column("Top Patterns")
        table.add_row("Tasks", self._format_success(task_metrics, "completed", "attempted", "success_rate"), self._format_counts(task_counts))
        table.add_row("Evaluations", self._format_success(eval_metrics, "passed_tasks", "total_tasks", "task_success_rate"), self._format_counts(eval_counts))
        return Panel(table, title=f"Project Reliability: {snapshot.get('project_name') or 'Unknown'}", expand=False)

    def _status_icon(self, status: str) -> str:
        mapping = {
            "completed": "✓",
            "running": "⏳",
            "failed": "✗",
            "stopped": "■",
            "aborted": "■",
            "pending": "•",
        }
        return mapping.get(status, "•")

    def _format_counts(self, counts: dict[str, Any]) -> str:
        if not counts:
            return "No patterns recorded"
        ordered = sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
        return ", ".join(f"{name}={count}" for name, count in ordered[:5])

    def _format_success(self, metrics: dict[str, Any], success_key: str, total_key: str, rate_key: str) -> str:
        if not metrics:
            return "n/a"
        rate = metrics.get(rate_key)
        success = int(metrics.get(success_key, 0) or 0)
        total = int(metrics.get(total_key, 0) or 0)
        parts = [f"{success}/{total}" if rate is None else f"{int(round(float(rate) * 100))}% ({success}/{total})"]
        step_rate = metrics.get("step_success_rate")
        step_completed = int(metrics.get("step_completed", 0) or 0)
        step_attempted = int(metrics.get("step_attempted", 0) or 0)
        if step_rate is not None:
            parts.append(f"steps={int(round(float(step_rate) * 100))}% ({step_completed}/{step_attempted})")
        avg_iterations = metrics.get("avg_iterations")
        if avg_iterations is not None:
            parts.append(f"iter={float(avg_iterations):.2f}")
        avg_step_iterations = metrics.get("avg_step_iterations")
        if avg_step_iterations is not None:
            parts.append(f"step_iter={float(avg_step_iterations):.2f}")
        median_runtime = metrics.get("median_runtime_seconds")
        if median_runtime is not None:
            parts.append(f"median={float(median_runtime):.2f}s")
        return " | ".join(parts)
