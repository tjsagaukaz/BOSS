from __future__ import annotations

from typing import Any


class RunLedger:
    def __init__(self, artifact_store, evaluation_store, task_history, lab_registry, run_replay) -> None:
        self.artifact_store = artifact_store
        self.evaluation_store = evaluation_store
        self.task_history = task_history
        self.lab_registry = lab_registry
        self.run_replay = run_replay

    def recent(self, *, project_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        ledger: list[dict[str, Any]] = []
        artifact_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in self.artifact_store.list_index(project_name=project_name, limit=None):
            kind = str(entry.get("kind", ""))
            identifier = entry.get("run_id")
            if identifier is None:
                identifier = entry.get("task_id")
            key = (kind, str(identifier))
            artifact_lookup[key] = entry
            ledger.append(
                {
                    "kind": kind,
                    "identifier": identifier,
                    "status": str(entry.get("status", "")),
                    "project_name": str(entry.get("project_name", "")),
                    "title": str(entry.get("task_name", "")),
                    "timestamp": str(entry.get("timestamp", "")),
                    "artifact_path": str(entry.get("artifact_path", "")),
                    "symbol": self._symbol_for_kind(kind),
                }
            )
        for task in self.task_history.recent_tasks(project_name=project_name, limit=200):
            key = ("build_task", str(task["id"]))
            if key in artifact_lookup:
                continue
            ledger.append(
                {
                    "kind": "build_task",
                    "identifier": task["id"],
                    "status": str(task.get("status", "")),
                    "project_name": str(task.get("project_name", "")),
                    "title": str(task.get("task", "")),
                    "timestamp": str(task.get("completed_at") or task.get("updated_at") or task.get("created_at") or ""),
                    "artifact_path": str((task.get("metadata", {}) or {}).get("artifact_path", "")),
                    "symbol": "S",
                }
            )
        for run in self.evaluation_store.recent_runs(limit=200):
            if project_name and run["project_name"] != project_name:
                continue
            key = ("evaluation_run", str(run["run_id"]))
            if key in artifact_lookup:
                continue
            ledger.append(
                {
                    "kind": "evaluation_run",
                    "identifier": run["run_id"],
                    "status": str(run.get("status", "")),
                    "project_name": str(run.get("project_name", "")),
                    "title": str(run.get("suite_name", "")),
                    "timestamp": str(run.get("completed_at") or run.get("updated_at") or run.get("created_at") or ""),
                    "artifact_path": "",
                    "symbol": "R",
                }
            )
        for experiment in self.lab_registry.list_experiments(limit=1000):
            if project_name and experiment["project_name"] != project_name:
                continue
            ledger.append(
                {
                    "kind": "experiment",
                    "identifier": experiment["experiment_id"],
                    "status": str(experiment.get("status", "")),
                    "project_name": str(experiment.get("project_name", "")),
                    "title": str(experiment.get("goal", "")),
                    "timestamp": str(experiment.get("completed_at") or experiment.get("updated_at") or experiment.get("created_at") or ""),
                    "artifact_path": "",
                    "symbol": "E",
                }
            )
        ledger.sort(key=lambda item: (item["timestamp"], str(item["identifier"])), reverse=True)
        return ledger[:limit]

    def details(
        self,
        identifier: str | int,
        *,
        kind: str = "auto",
        project_name: str | None = None,
    ) -> dict[str, Any]:
        normalized_kind = kind.strip().lower()
        if normalized_kind == "experiment" or (not self._is_int(identifier) and normalized_kind == "auto"):
            experiment = self.lab_registry.experiment_with_variants(str(identifier))
            if experiment is None:
                raise FileNotFoundError(f"Experiment '{identifier}' not found.")
            return {
                "kind": "experiment",
                "identifier": experiment["experiment_id"],
                "project_name": experiment["project_name"],
                "status": experiment["status"],
                "summary": experiment,
                "variants": experiment.get("variants", []),
                "artifact_path": "",
            }

        numeric_id = int(identifier)
        if normalized_kind == "build":
            return self._build_details(numeric_id)
        if normalized_kind in {"eval", "evaluation"}:
            return self._evaluation_details(numeric_id)
        if normalized_kind == "auto":
            recent = self.recent(project_name=project_name, limit=500)
            matches = [item for item in recent if str(item["identifier"]) == str(numeric_id)]
            for preferred_kind in ("build_task", "evaluation_run"):
                for item in matches:
                    if item["kind"] == preferred_kind:
                        chosen = "build" if preferred_kind == "build_task" else "evaluation"
                        return self.details(numeric_id, kind=chosen, project_name=project_name)
            if matches:
                item = matches[0]
                chosen = "build" if item["kind"] == "build_task" else "evaluation"
                return self.details(item["identifier"], kind=chosen, project_name=project_name)
        raise FileNotFoundError(f"Run '{identifier}' not found.")

    def _build_details(self, task_id: int) -> dict[str, Any]:
        task = self.task_history.task_with_steps(task_id)
        if task is None:
            raise FileNotFoundError(f"Build task {task_id} not found.")
        try:
            analysis = self.run_replay.replay(task_id, kind="build", mode="analysis")
        except FileNotFoundError:
            analysis = {}
        metadata = task.get("metadata", {}) or {}
        return {
            "kind": "build_task",
            "identifier": task_id,
            "project_name": task["project_name"],
            "status": task["status"],
            "summary": {
                "task": task["task"],
                "status": task["status"],
                "graph_nodes": len((metadata.get("run_graph", {}) or {}).get("nodes", [])),
                "retries": sum(max(int(step.get("iterations", 0) or 0) - 1, 0) for step in task.get("steps", [])),
                "runtime_seconds": task.get("runtime_seconds"),
                "artifact_path": str(metadata.get("artifact_path", "")),
            },
            "task": task,
            "analysis": analysis,
            "artifact_path": str(metadata.get("artifact_path", "")),
        }

    def _evaluation_details(self, run_id: int) -> dict[str, Any]:
        run = self.evaluation_store.run_with_tasks(run_id)
        if run is None:
            raise FileNotFoundError(f"Evaluation run {run_id} not found.")
        try:
            analysis = self.run_replay.replay(run_id, kind="evaluation", mode="analysis")
        except FileNotFoundError:
            analysis = {}
        return {
            "kind": "evaluation_run",
            "identifier": run_id,
            "project_name": run.project_name,
            "status": run.status,
            "summary": {
                "suite_name": run.suite_name,
                "status": run.status,
                "graph_nodes": None,
                "retries": sum(int((task.metadata or {}).get("iterations", 0) or 0) for task in run.tasks),
                "runtime_seconds": run.runtime_seconds,
                "artifact_path": str(analysis.get("artifact_path", "")),
                "passed_tasks": run.passed_tasks,
                "failed_tasks": run.failed_tasks,
            },
            "run": run,
            "analysis": analysis,
            "artifact_path": str(analysis.get("artifact_path", "")),
        }

    def _symbol_for_kind(self, kind: str) -> str:
        if kind == "build_task":
            return "S"
        if kind == "evaluation_run":
            return "R"
        if kind == "experiment":
            return "E"
        return "?"

    def _is_int(self, value: str | int) -> bool:
        try:
            int(value)
            return True
        except (TypeError, ValueError):
            return False
