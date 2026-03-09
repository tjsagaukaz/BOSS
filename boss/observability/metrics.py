from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any


class MetricsReporter:
    def __init__(self, task_history, evaluation_store, artifact_store, lab_registry) -> None:
        self.task_history = task_history
        self.evaluation_store = evaluation_store
        self.artifact_store = artifact_store
        self.lab_registry = lab_registry

    def snapshot(
        self,
        *,
        project_name: str | None = None,
        task_limit: int = 1000,
        eval_limit: int = 1000,
        experiment_limit: int = 1000,
    ) -> dict[str, Any]:
        tasks = self.task_history.recent_tasks(project_name=project_name, limit=task_limit)
        eval_runs = [
            run
            for run in self.evaluation_store.recent_runs(limit=eval_limit)
            if not project_name or run["project_name"] == project_name
        ]
        experiments = [
            item
            for item in self.lab_registry.list_experiments(limit=experiment_limit)
            if not project_name or item["project_name"] == project_name
        ]
        artifacts = self.artifact_store.list_index(project_name=project_name, limit=None)

        role_durations: dict[str, list[float]] = defaultdict(list)
        role_success: dict[str, list[int]] = defaultdict(list)
        retry_count = 0
        node_counts: list[int] = []
        parallel_runs = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        total_estimated_cost = 0.0
        cost_observed = False

        for task in tasks:
            metadata = task.get("metadata", {}) or {}
            run_graph = metadata.get("run_graph", {}) or {}
            nodes = run_graph.get("nodes", [])
            if isinstance(nodes, list) and nodes:
                node_counts.append(len(nodes))
            if metadata.get("run_graph_parallel"):
                parallel_runs += 1
            token_usage = task.get("token_usage", {}) or {}
            total_input_tokens += int(token_usage.get("input_tokens", 0) or 0)
            total_output_tokens += int(token_usage.get("output_tokens", 0) or 0)
            total_tokens += int(token_usage.get("total_tokens", 0) or 0)
            if task.get("estimated_cost_usd") is not None:
                total_estimated_cost += float(task["estimated_cost_usd"])
                cost_observed = True
            for item in task.get("model_usage", []):
                role = str(item.get("role", "") or "unknown")
                role_durations[role].append(float(item.get("duration_seconds", 0.0) or 0.0))
                role_success[role].append(1 if str(task.get("status", "")).lower() == "completed" else 0)
            for step in self.task_history.list_steps(int(task["id"])):
                retry_count += max(int(step.get("iterations", 0) or 0) - 1, 0)

        agent_runtime = []
        for role in sorted(role_durations):
            durations = role_durations[role]
            successes = role_success.get(role, [])
            agent_runtime.append(
                {
                    "role": role,
                    "run_count": len(durations),
                    "avg_duration_seconds": float(mean(durations)) if durations else None,
                    "success_rate": float(sum(successes) / len(successes)) if successes else None,
                }
            )

        return {
            "project_name": project_name,
            "task_runs_recorded": len(tasks),
            "eval_runs_recorded": len(eval_runs),
            "artifacts_stored": len(artifacts),
            "benchmarks_executed": len(eval_runs),
            "experiments_executed": len(experiments),
            "agent_runtime": agent_runtime,
            "run_graph": {
                "avg_nodes_per_run": float(mean(node_counts)) if node_counts else None,
                "parallel_runs": parallel_runs,
                "parallel_mode": "enabled" if parallel_runs else "disabled",
                "retries_triggered": retry_count,
            },
            "token_usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
            },
            "trends": {
                "tasks": [self._window_success(tasks, window, success_value="completed") for window in (10, 50, 100)],
                "evaluations": [self._window_success(eval_runs, window, success_value="passed") for window in (10, 50, 100)],
            },
            "estimated_cost_usd": total_estimated_cost if cost_observed else None,
        }

    def _window_success(self, items: list[dict[str, Any]], window: int, *, success_value: str) -> dict[str, Any]:
        sample = items[:window]
        executed = [item for item in sample if str(item.get("status", "")).lower() not in {"running", "skipped"}]
        passed = sum(1 for item in executed if str(item.get("status", "")).lower() == success_value)
        return {
            "window": window,
            "attempted": len(executed),
            "passed": passed,
            "success_rate": float(passed / len(executed)) if executed else None,
        }
