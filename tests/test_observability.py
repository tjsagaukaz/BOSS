from __future__ import annotations

from boss.artifacts import ArtifactStore
from boss.eval.eval_store import EvaluationStore
from boss.lab.lab_registry import LabRegistry
from boss.memory.task_history import TaskHistoryStore
from boss.observability import HealthReporter, MetricsReporter, RunLedger
from boss.types import (
    AutonomousBuildResult,
    EvalRunResult,
    EvalTaskResult,
    PlanStepContract,
    StepExecutionResult,
    StructuredPlan,
    ValidationOutcome,
)
from boss.workspace.workspace_state import WorkspaceStateStore


class FakeReplayManager:
    def replay(self, identifier: int, *, kind: str = "auto", mode: str = "analysis", auto_approve: bool = False):
        if kind == "build":
            return {
                "artifact_path": f"/tmp/build/{identifier}",
                "run_graph": {"nodes": [{"id": "S1"}, {"id": "S2"}]},
                "summary": {"task_id": identifier},
            }
        return {
            "artifact_path": f"/tmp/eval/{identifier}",
            "summary": {"run_id": identifier},
            "tasks": [],
        }


def _record_completed_task(store: TaskHistoryStore, *, project_name: str, task: str, status: str = "completed") -> int:
    task_id = store.create_task(project_name=project_name, task=task)
    store.start_step(task_id, 0, "Implement change")
    if status == "completed":
        store.complete_step(
            task_id,
            0,
            files_changed=["app.py"],
            commit_message="",
            iterations=2,
            engineer_output="ok",
            test_output={},
            audit_output="",
        )
    else:
        store.fail_step(
            task_id,
            0,
            errors=["run graph deadlock"],
            iterations=3,
            engineer_output="",
            test_output={},
            audit_output="",
        )
    store.finalize_task(
        task_id,
        status=status,
        files_changed=["app.py"] if status == "completed" else [],
        errors=[] if status == "completed" else ["run graph deadlock"],
        final_result="done" if status == "completed" else "failed",
        model_usage=[{"role": "engineer", "duration_seconds": 6.5}],
        token_usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        estimated_cost_usd=0.02,
        metadata={
            "run_graph": {"nodes": [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}]},
            "run_graph_parallel": False,
        },
    )
    return task_id


def test_health_reporter_classifies_stable(tmp_path):
    task_history = TaskHistoryStore(tmp_path / "boss.db")
    evaluation_store = EvaluationStore(tmp_path / "boss.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    workspace_state = WorkspaceStateStore(tmp_path / "workspace.json")

    _record_completed_task(task_history, project_name="demo", task="Add middleware")

    run_id = evaluation_store.create_run("suite", "/tmp/suite.yaml", "demo", total_tasks=1)
    evaluation_store.record_task_result(
        run_id,
        EvalTaskResult(
            task_name="add_middleware",
            description="Add middleware",
            project_name="demo",
            mode="code",
            status="passed",
            runtime_seconds=1.0,
            validations=[ValidationOutcome(name="tests", passed=True, message="ok")],
        ),
    )
    evaluation_store.finalize_run(run_id, "passed", 1, 0, 1.0, None)

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    artifact_store.write_build_artifact(
        task="Add middleware",
        result=AutonomousBuildResult(
            task_id=1,
            project_name="demo",
            goal="Add middleware",
            status="completed",
            plan=StructuredPlan(goal="Add middleware", steps=["Implement change"]),
            changed_files=["app.py"],
        ),
        project_root=project_root,
    )

    workspace_state.set_active_project("demo")
    workspace_state.record_open_file("demo", "app.py")

    snapshot = HealthReporter(task_history, evaluation_store, artifact_store, workspace_state).snapshot(project_name="demo")

    assert snapshot["status"] == "stable"
    assert snapshot["median_retries_per_task"] == 1.0
    assert snapshot["artifact_store_size"] == 1
    assert snapshot["workspace_watchers"] == "active"


def test_health_reporter_flags_stale_tasks(tmp_path):
    task_history = TaskHistoryStore(tmp_path / "boss.db")
    evaluation_store = EvaluationStore(tmp_path / "boss.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    workspace_state = WorkspaceStateStore(tmp_path / "workspace.json")

    task_id = task_history.create_task(project_name="demo", task="Hung task")
    task_history.start_step(task_id, 0, "Implement change")
    task_history.merge_task_metadata(task_id, {"owner_pid": 999999})

    snapshot = HealthReporter(task_history, evaluation_store, artifact_store, workspace_state).snapshot(project_name="demo")

    assert snapshot["stale_tasks_detected"] == 1
    assert snapshot["status"] == "degraded"
    assert any("stale task" in reason.lower() for reason in snapshot["status_reasons"])


def test_metrics_reporter_aggregates_runtime_and_counts(tmp_path):
    task_history = TaskHistoryStore(tmp_path / "boss.db")
    evaluation_store = EvaluationStore(tmp_path / "boss.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    lab_registry = LabRegistry(tmp_path / "boss.db")

    task_id = _record_completed_task(task_history, project_name="demo", task="Add middleware")
    task_history.merge_task_metadata(task_id, {"run_graph": {"nodes": [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}]}})

    run_id = evaluation_store.create_run("suite", "/tmp/suite.yaml", "demo", total_tasks=1)
    evaluation_store.finalize_run(run_id, "passed", 1, 0, 1.0, 0.01)

    lab_registry.create_experiment(
        experiment_id="exp_1",
        project_name="demo",
        goal="Improve latency",
        primary_metric="latency_ms",
        metric_direction="minimize",
        benchmark_commands=["pytest"],
        allowed_paths=["app.py"],
    )

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    artifact_store.write_build_artifact(
        task="Add middleware",
        result=AutonomousBuildResult(
            task_id=task_id,
            project_name="demo",
            goal="Add middleware",
            status="completed",
            plan=StructuredPlan(
                goal="Add middleware",
                steps=["Implement change"],
                contracts=[PlanStepContract(title="Implement change", step_id="S1")],
            ),
            step_results=[StepExecutionResult(step_index=0, step_title="Implement change", status="completed", iterations=2)],
            changed_files=["app.py"],
            model_usage=[{"role": "engineer", "duration_seconds": 6.5}],
            token_usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            estimated_cost_usd=0.02,
            metadata={"run_graph": {"nodes": [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}]}},
        ),
        project_root=project_root,
    )

    snapshot = MetricsReporter(task_history, evaluation_store, artifact_store, lab_registry).snapshot(project_name="demo")

    assert snapshot["task_runs_recorded"] == 1
    assert snapshot["eval_runs_recorded"] == 1
    assert snapshot["artifacts_stored"] == 1
    assert snapshot["experiments_executed"] == 1
    assert snapshot["run_graph"]["avg_nodes_per_run"] == 3.0
    assert snapshot["run_graph"]["retries_triggered"] == 1
    assert snapshot["agent_runtime"][0]["role"] == "engineer"
    assert snapshot["agent_runtime"][0]["avg_duration_seconds"] == 6.5


def test_run_ledger_lists_runs_and_returns_details(tmp_path):
    task_history = TaskHistoryStore(tmp_path / "boss.db")
    evaluation_store = EvaluationStore(tmp_path / "boss.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    lab_registry = LabRegistry(tmp_path / "boss.db")
    replay = FakeReplayManager()

    task_id = _record_completed_task(task_history, project_name="demo", task="Add middleware")
    task_history.merge_task_metadata(task_id, {"artifact_path": f"/tmp/build/{task_id}"})
    run_id = evaluation_store.create_run("suite", "/tmp/suite.yaml", "demo", total_tasks=1)
    evaluation_store.record_task_result(
        run_id,
        EvalTaskResult(
            task_name="add_middleware",
            description="Add middleware",
            project_name="demo",
            mode="code",
            status="passed",
            runtime_seconds=1.0,
            validations=[ValidationOutcome(name="tests", passed=True, message="ok")],
            metadata={"iterations": 1},
        ),
    )
    evaluation_store.finalize_run(run_id, "passed", 1, 0, 1.0, None)

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    artifact_store.write_build_artifact(
        task="Add middleware",
        result=AutonomousBuildResult(
            task_id=task_id,
            project_name="demo",
            goal="Add middleware",
            status="completed",
            plan=StructuredPlan(goal="Add middleware", steps=["Implement change"]),
            changed_files=["app.py"],
            metadata={"run_graph": {"nodes": [{"id": "S1"}, {"id": "S2"}]}},
        ),
        project_root=project_root,
    )
    artifact_store.write_evaluation_run_artifact(
        EvalRunResult(
            run_id=run_id,
            suite_name="suite",
            suite_path="/tmp/suite.yaml",
            project_name="demo",
            status="passed",
            total_tasks=1,
            passed_tasks=1,
            failed_tasks=0,
            runtime_seconds=1.0,
            tasks=[],
        )
    )

    lab_registry.create_experiment(
        experiment_id="exp_1",
        project_name="demo",
        goal="Improve latency",
        primary_metric="latency_ms",
        metric_direction="minimize",
        benchmark_commands=["pytest"],
        allowed_paths=["app.py"],
    )

    ledger = RunLedger(artifact_store, evaluation_store, task_history, lab_registry, replay)
    entries = ledger.recent(project_name="demo", limit=10)

    assert {item["kind"] for item in entries} >= {"build_task", "evaluation_run", "experiment"}
    assert {item["symbol"] for item in entries} >= {"S", "R", "E"}

    build_details = ledger.details(task_id, kind="build", project_name="demo")
    assert build_details["kind"] == "build_task"
    assert build_details["summary"]["graph_nodes"] == 3

    eval_details = ledger.details(run_id, kind="evaluation", project_name="demo")
    assert eval_details["kind"] == "evaluation_run"
    assert eval_details["summary"]["passed_tasks"] == 1

    experiment_details = ledger.details("exp_1", kind="experiment", project_name="demo")
    assert experiment_details["kind"] == "experiment"
    assert experiment_details["summary"]["goal"] == "Improve latency"
