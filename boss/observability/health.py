from __future__ import annotations

import statistics
from typing import Any


class HealthReporter:
    def __init__(self, task_history, evaluation_store, artifact_store, workspace_state) -> None:
        self.task_history = task_history
        self.evaluation_store = evaluation_store
        self.artifact_store = artifact_store
        self.workspace_state = workspace_state

    def snapshot(
        self,
        *,
        project_name: str | None = None,
        task_limit: int = 100,
        eval_limit: int = 100,
    ) -> dict[str, Any]:
        task_metrics = self.task_history.success_metrics(project_name=project_name, limit=task_limit)
        eval_metrics = self.evaluation_store.success_metrics(project_name=project_name, limit=eval_limit)
        retries = self._task_retries(project_name=project_name, limit=task_limit)
        deadlocks = self._deadlock_count(project_name=project_name, limit=task_limit)
        step_timeouts = self._step_timeout_count(project_name=project_name, limit=task_limit)
        recent_eval_failures = self._recent_eval_failures(project_name=project_name, limit=min(eval_limit, 20))
        stale_tasks = len(self.task_history.stale_running_tasks(project_name=project_name))
        artifact_count = len(self.artifact_store.list_index(project_name=project_name, limit=None))
        workspace_watchers = self._workspace_status(project_name)
        status, reasons = self._classify(
            task_success_rate=task_metrics.get("success_rate"),
            eval_success_rate=eval_metrics.get("run_success_rate"),
            recent_eval_failures=recent_eval_failures,
            deadlocks=deadlocks,
            stale_tasks=stale_tasks,
            step_timeouts=step_timeouts,
        )
        return {
            "project_name": project_name,
            "status": status,
            "status_reasons": reasons,
            "autonomous_success_rate": task_metrics.get("success_rate"),
            "task_attempted": task_metrics.get("attempted", 0),
            "median_retries_per_task": float(statistics.median(retries)) if retries else None,
            "avg_retries_per_task": float(sum(retries) / len(retries)) if retries else None,
            "run_graph_deadlocks": deadlocks,
            "step_timeouts": step_timeouts,
            "recent_eval_failures": recent_eval_failures,
            "stale_tasks_detected": stale_tasks,
            "artifact_store_size": artifact_count,
            "workspace_watchers": workspace_watchers,
            "task_metrics": task_metrics,
            "evaluation_metrics": eval_metrics,
        }

    def _task_retries(self, *, project_name: str | None, limit: int) -> list[int]:
        retries: list[int] = []
        for task in self.task_history.recent_tasks(project_name=project_name, limit=limit):
            status = str(task.get("status", "")).lower()
            if status not in {"completed", "failed", "stopped", "aborted"}:
                continue
            step_retries = 0
            for step in self.task_history.list_steps(int(task["id"])):
                step_retries += max(int(step.get("iterations", 0) or 0) - 1, 0)
            retries.append(step_retries)
        return retries

    def _deadlock_count(self, *, project_name: str | None, limit: int) -> int:
        deadlocks = 0
        for task in self.task_history.recent_tasks(project_name=project_name, limit=limit):
            status = str(task.get("status", "")).lower()
            if status not in {"completed", "failed", "stopped", "aborted"}:
                continue
            task_errors = [str(item) for item in task.get("errors", [])]
            step_errors = [
                str(error)
                for step in self.task_history.list_steps(int(task["id"]))
                for error in step.get("errors", [])
            ]
            if "deadlock" in "\n".join(task_errors + step_errors).lower():
                deadlocks += 1
        return deadlocks

    def _step_timeout_count(self, *, project_name: str | None, limit: int) -> int:
        timeouts = 0
        for task in self.task_history.recent_tasks(project_name=project_name, limit=limit):
            task_errors = [str(item) for item in task.get("errors", [])]
            step_errors = [
                str(error)
                for step in self.task_history.list_steps(int(task["id"]))
                for error in step.get("errors", [])
            ]
            combined = "\n".join(task_errors + step_errors).lower()
            if "timed out" in combined or "timeout" in combined:
                timeouts += 1
        return timeouts

    def _recent_eval_failures(self, *, project_name: str | None, limit: int) -> int:
        failures = 0
        for run in self.evaluation_store.recent_runs(limit=limit):
            if project_name and run["project_name"] != project_name:
                continue
            if str(run.get("status", "")).lower() == "failed":
                failures += 1
        return failures

    def _workspace_status(self, project_name: str | None) -> str:
        active_project = project_name or self.workspace_state.active_project()
        if not active_project:
            return "idle"
        snapshot = self.workspace_state.snapshot(active_project)
        if snapshot.updated_at or snapshot.open_files or snapshot.recent_events:
            return "active"
        return "idle"

    def _classify(
        self,
        *,
        task_success_rate: Any,
        eval_success_rate: Any,
        recent_eval_failures: int,
        deadlocks: int,
        stale_tasks: int,
        step_timeouts: int,
    ) -> tuple[str, list[str]]:
        rates = [float(value) for value in (task_success_rate, eval_success_rate) if value is not None]
        effective_rate = min(rates) if rates else None
        reasons: list[str] = []
        if stale_tasks:
            reasons.append(f"{stale_tasks} stale task(s) detected.")
        if effective_rate is None:
            if stale_tasks:
                return "degraded", reasons
            return "warming_up", ["No completed autonomous or evaluation runs recorded yet."]
        if deadlocks:
            reasons.append(f"{deadlocks} recent run graph deadlock(s) detected.")
        if step_timeouts:
            reasons.append(f"{step_timeouts} recent step timeout(s) detected.")
        if recent_eval_failures:
            reasons.append(f"{recent_eval_failures} recent evaluation failure(s) detected.")
        if effective_rate >= 0.7 and deadlocks == 0 and recent_eval_failures <= 2 and stale_tasks == 0 and step_timeouts == 0:
            if not reasons:
                reasons.append("Autonomous success and evaluation stability are within target.")
            return "stable", reasons
        if effective_rate >= 0.5 and stale_tasks == 0 and step_timeouts == 0:
            if not reasons:
                reasons.append("Core loops are working, but reliability is still inconsistent.")
            return "unstable", reasons
        if not reasons:
            reasons.append("Autonomous success is below the current reliability threshold.")
        return "degraded", reasons
