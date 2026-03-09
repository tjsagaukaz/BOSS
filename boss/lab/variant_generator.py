from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LabVariantDefinition:
    variant_id: str
    name: str
    hypothesis: str
    task_description: str
    kind: str
    mode: str
    benchmark_commands: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    required_changed_files: list[str] = field(default_factory=list)
    forbidden_output_contains: list[str] = field(default_factory=list)
    direct_engineer: bool = False
    plan_override: str = ""
    success_metric: str | None = None
    require_tests_passed: bool = True
    keep_sandbox: bool = False
    max_iterations: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)


class VariantGenerator:
    def generate(
        self,
        experiment_id: str,
        goal: str,
        variants: list[str] | None = None,
        benchmark_commands: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        primary_metric: str | None = None,
        max_iterations: int = 5,
    ) -> list[LabVariantDefinition]:
        benchmark_commands = list(benchmark_commands or [])
        allowed_paths = list(allowed_paths or [])
        target_files = [path for path in allowed_paths if path.strip() and not path.strip().endswith("/")]
        candidate_descriptions = [item.strip() for item in (variants or [goal]) if item.strip()]
        candidate_descriptions = candidate_descriptions[:2]

        definitions = [
            LabVariantDefinition(
                variant_id=f"{experiment_id}:baseline",
                name="baseline",
                hypothesis="Measure the current implementation without modifications.",
                task_description=f"Run the baseline benchmark for: {goal}",
                kind="baseline",
                mode="test",
                benchmark_commands=benchmark_commands,
                allowed_paths=allowed_paths,
                success_metric=primary_metric,
                require_tests_passed=False,
                keep_sandbox=False,
                max_iterations=1,
                metadata={"goal": goal},
            )
        ]

        for index, description in enumerate(candidate_descriptions, start=1):
            definitions.append(
                LabVariantDefinition(
                    variant_id=f"{experiment_id}:variant_{index}",
                    name=f"variant_{index}",
                    hypothesis=description,
                    task_description=self._candidate_task(goal, description, target_files, primary_metric),
                    kind="candidate",
                    mode="code",
                    benchmark_commands=benchmark_commands,
                    allowed_paths=allowed_paths,
                    target_files=target_files,
                    required_changed_files=target_files,
                    forbidden_output_contains=[
                        "verify current baseline latency",
                        "run the baseline benchmark",
                        "measure the current implementation without modifications",
                    ],
                    direct_engineer=True,
                    plan_override=self._candidate_plan_override(goal, description, target_files, primary_metric),
                    success_metric=primary_metric,
                    require_tests_passed=True,
                    keep_sandbox=True,
                    max_iterations=max_iterations,
                    metadata={
                        "goal": goal,
                        "target_files": target_files,
                        "success_metric": primary_metric,
                    },
                )
            )

        return definitions

    def _candidate_task(
        self,
        goal: str,
        hypothesis: str,
        target_files: list[str],
        primary_metric: str | None,
    ) -> str:
        lines = [
            f"Goal: {goal}",
            f"Variant hypothesis: {hypothesis}",
            "Variant contract:",
            "- Baseline benchmark has already been captured separately. Do not spend this task re-measuring or restating the baseline.",
            "- Make the smallest code change that satisfies the hypothesis.",
            "- Do not modify files outside the target set.",
            "- You must change at least one target file in this task.",
        ]
        if primary_metric:
            lines.append(f"- The target success metric is {primary_metric}.")
        if target_files:
            lines.append("Target files:")
            lines.extend(f"- {path}" for path in target_files)
        return "\n".join(lines)

    def _candidate_plan_override(
        self,
        goal: str,
        hypothesis: str,
        target_files: list[str],
        primary_metric: str | None,
    ) -> str:
        lines = [
            "Direct engineer contract.",
            f"Goal: {goal}",
            f"Hypothesis: {hypothesis}",
            "Do not create a new plan or baseline-verification step.",
            "Implement the requested optimization immediately.",
        ]
        if primary_metric:
            lines.append(f"Success metric: {primary_metric}")
        if target_files:
            lines.append("Allowed target files:")
            lines.extend(f"- {path}" for path in target_files)
        return "\n".join(lines)
