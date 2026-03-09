from __future__ import annotations

from pathlib import Path

from boss.artifacts import ArtifactStore, RunReplayManager
from boss.types import (
    AutonomousBuildResult,
    EvalRunResult,
    EvalTaskResult,
    PlanStepContract,
    StepExecutionResult,
    StructuredPlan,
    ValidationOutcome,
)


class FakeOrchestrator:
    def __init__(self) -> None:
        self.build_calls: list[dict[str, object]] = []
        self.eval_calls: list[dict[str, object]] = []

    def build(
        self,
        task: str,
        *,
        auto_approve: bool,
        commit_changes: bool,
        project_name: str | None,
        store_knowledge: bool,
    ) -> AutonomousBuildResult:
        self.build_calls.append(
            {
                "task": task,
                "auto_approve": auto_approve,
                "commit_changes": commit_changes,
                "project_name": project_name,
                "store_knowledge": store_knowledge,
            }
        )
        return AutonomousBuildResult(
            task_id=99,
            project_name=project_name or "demo",
            goal=task,
            status="completed",
            plan=StructuredPlan(goal=task, steps=["Replay build"]),
            final_result="Replayed build.",
            metadata={"artifact_path": "/tmp/replayed-build"},
        )

    def evaluate_suite(self, *, suite_path: str, project_name: str | None) -> EvalRunResult:
        self.eval_calls.append({"suite_path": suite_path, "project_name": project_name})
        return EvalRunResult(
            run_id=88,
            suite_name="Replay Suite",
            suite_path=suite_path,
            project_name=project_name or "demo",
            status="completed",
            total_tasks=1,
            passed_tasks=1,
            failed_tasks=0,
            runtime_seconds=1.0,
            metadata={"artifact_path": "/tmp/replayed-eval"},
        )


def _build_result(task_id: int = 42) -> AutonomousBuildResult:
    return AutonomousBuildResult(
        task_id=task_id,
        project_name="demo",
        goal="Generate module",
        status="completed",
        plan=StructuredPlan(
            goal="Generate module",
            steps=["Create module"],
            contracts=[PlanStepContract(title="Create module", step_id="S1", objective="Create generated module")],
        ),
        runtime_seconds=1.0,
        step_results=[
            StepExecutionResult(
                step_index=0,
                step_title="Create module",
                status="completed",
                iterations=1,
                changed_files=["generated.py"],
            )
        ],
        final_result="Completed 1 step.",
        changed_files=["generated.py"],
        metadata={"run_graph": {"nodes": [{"id": "S1"}]}},
    )


def test_run_replay_analysis_and_dry_run_for_build_artifact(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "generated.py").write_text("def generated():\n    return True\n", encoding="utf-8")
    store = ArtifactStore(tmp_path / "artifacts")
    store.write_build_artifact(task="Generate module", result=_build_result(), project_root=project_root)

    replay = RunReplayManager(store, FakeOrchestrator())
    analysis = replay.replay(42, kind="build", mode="analysis")
    dry_run = replay.replay(42, kind="build", mode="dry-run")

    assert analysis["kind"] == "build_task"
    assert analysis["summary"]["task"] == "Generate module"
    assert analysis["plan"]["steps"] == ["Create module"]
    assert analysis["run_graph"]["nodes"] == [{"id": "S1"}]
    assert "summary.json" in dry_run["available_files"]
    assert dry_run["mode"] == "dry-run"


def test_run_replay_full_build_uses_stored_summary(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "generated.py").write_text("def generated():\n    return True\n", encoding="utf-8")
    store = ArtifactStore(tmp_path / "artifacts")
    store.write_build_artifact(task="Generate module", result=_build_result(), project_root=project_root)
    orchestrator = FakeOrchestrator()

    replay = RunReplayManager(store, orchestrator)
    result = replay.replay(42, kind="build", mode="full", auto_approve=True)

    assert result["replayed_task_id"] == 99
    assert result["artifact_path"] == "/tmp/replayed-build"
    assert orchestrator.build_calls == [
        {
            "task": "Generate module",
            "auto_approve": True,
            "commit_changes": False,
            "project_name": "demo",
            "store_knowledge": False,
        }
    ]


def test_run_replay_full_evaluation_uses_stored_suite(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    task = EvalTaskResult(
        task_name="auth_benchmark_gate",
        description="Replay deterministic evaluation",
        project_name="demo",
        mode="test",
        status="passed",
        runtime_seconds=0.5,
        files_changed=[],
        validations=[ValidationOutcome(name="tests_pass", passed=True, message="Tests passed.")],
    )
    run = EvalRunResult(
        run_id=37,
        suite_name="Deterministic Eval",
        suite_path="/tmp/demo_eval.yaml",
        project_name="demo",
        status="completed",
        total_tasks=1,
        passed_tasks=1,
        failed_tasks=0,
        runtime_seconds=0.75,
        tasks=[task],
    )
    store.write_evaluation_run_artifact(run)
    orchestrator = FakeOrchestrator()

    replay = RunReplayManager(store, orchestrator)
    result = replay.replay(37, kind="evaluation", mode="full")

    assert result["replayed_run_id"] == 88
    assert result["artifact_path"] == "/tmp/replayed-eval"
    assert orchestrator.eval_calls == [{"suite_path": "/tmp/demo_eval.yaml", "project_name": "demo"}]
