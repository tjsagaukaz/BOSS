from __future__ import annotations

from typing import Any

from boss.lab.variant_generator import LabVariantDefinition
from boss.types import TaskContract, TaskSuite


class BenchmarkRunner:
    def __init__(self, evaluator) -> None:
        self.evaluator = evaluator

    def run_variant(
        self,
        *,
        experiment_id: str,
        project_name: str,
        variant: LabVariantDefinition,
        auto_approve: bool = True,
        deep: bool = False,
    ) -> dict[str, Any]:
        contract = TaskContract(
            name=variant.name,
            description=variant.task_description,
            mode=variant.mode,
            project_name=project_name,
            sandbox_mode="auto",
            keep_sandbox=variant.keep_sandbox,
            allowed_paths=variant.allowed_paths,
            required_changed_files=variant.required_changed_files,
            validation_commands=variant.benchmark_commands,
            forbidden_output_contains=variant.forbidden_output_contains,
            require_tests_passed=variant.require_tests_passed,
            auto_approve=auto_approve,
            max_iterations=variant.max_iterations,
            metadata={
                "lab_experiment_id": experiment_id,
                "lab_variant_id": variant.variant_id,
                "lab_kind": variant.kind,
                "lab_hypothesis": variant.hypothesis,
                "direct_engineer": variant.direct_engineer,
                "plan_override": variant.plan_override,
                "target_files": variant.target_files,
                "success_metric": variant.success_metric,
                "deep": deep,
                **variant.metadata,
            },
        )
        suite = TaskSuite(
            name=f"lab_{experiment_id}_{variant.name}",
            path=f"lab://{experiment_id}/{variant.variant_id}",
            project_name=project_name,
            default_mode=variant.mode,
            sandbox_mode="auto",
            keep_sandbox=variant.keep_sandbox,
            auto_approve=auto_approve,
            max_iterations=variant.max_iterations,
            stop_on_failure=True,
            tasks=[contract],
            metadata={"lab": True, "experiment_id": experiment_id, "variant_id": variant.variant_id},
        )
        run = self.evaluator.run_task_suite(suite, project_name=project_name, stop_on_failure=True)
        task = run.tasks[0]

        metrics = dict(task.metadata.get("benchmark_metrics", {}))
        metrics.setdefault("runtime_seconds", float(task.runtime_seconds))
        if task.metadata.get("validation_command_results"):
            for item in task.metadata["validation_command_results"]:
                if not isinstance(item, dict):
                    continue
                command = str(item.get("command", "")).strip()
                if not command:
                    continue
                metrics[f"runtime::{command}"] = float(item.get("runtime_seconds", 0.0) or 0.0)

        return {
            "variant_id": variant.variant_id,
            "kind": variant.kind,
            "eval_run_id": run.run_id,
            "status": task.status,
            "runtime_seconds": float(task.runtime_seconds),
            "sandbox_project_name": task.metadata.get("execution_project_name"),
            "sandbox_path": task.metadata.get("sandbox_path"),
            "sandbox_mode": task.metadata.get("sandbox_mode"),
            "branch_name": task.metadata.get("sandbox_branch"),
            "base_revision": task.metadata.get("sandbox_base_revision"),
            "changed_files": list(task.files_changed),
            "metrics": metrics,
            "output_summary": task.output_summary,
            "errors": list(task.errors),
            "metadata": dict(task.metadata),
        }
