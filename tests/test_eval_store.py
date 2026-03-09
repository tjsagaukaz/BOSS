from __future__ import annotations

from boss.eval.eval_store import EvaluationStore
from boss.types import EvalTaskResult, ValidationOutcome


def test_evaluation_store_round_trip(tmp_path):
    store = EvaluationStore(tmp_path / "eval.db")
    run_id = store.create_run(
        suite_name="demo_suite",
        suite_path="/tmp/demo_suite.yaml",
        project_name="demo",
        total_tasks=1,
        metadata={"source": "test"},
    )
    task_result = EvalTaskResult(
        task_name="add_auth",
        description="Implement JWT auth",
        project_name="demo",
        mode="code",
        status="passed",
        runtime_seconds=1.25,
        files_changed=["auth.py"],
        validations=[ValidationOutcome(name="execution_status", passed=True, message="ok")],
        model_usage=[{"role": "engineer", "model": "fake", "total_tokens": 42}],
        token_usage={"input_tokens": 20, "output_tokens": 22, "total_tokens": 42},
        estimated_cost_usd=0.012,
        metadata={"iterations": 1},
    )

    store.record_task_result(run_id, task_result)
    store.finalize_run(
        run_id=run_id,
        status="passed",
        passed_tasks=1,
        failed_tasks=0,
        runtime_seconds=1.25,
        total_estimated_cost_usd=0.012,
        metadata={"source": "test"},
    )

    run = store.run_with_tasks(run_id)

    assert run is not None
    assert run.run_id == run_id
    assert run.status == "passed"
    assert run.passed_tasks == 1
    assert len(run.tasks) == 1
    assert run.tasks[0].task_name == "add_auth"
    assert run.tasks[0].files_changed == ["auth.py"]
    assert run.tasks[0].token_usage["total_tokens"] == 42


def test_evaluation_store_failure_map_summary(tmp_path):
    store = EvaluationStore(tmp_path / "eval.db")
    run_id = store.create_run(
        suite_name="demo_suite",
        suite_path="/tmp/demo_suite.yaml",
        project_name="demo",
        total_tasks=1,
    )
    task_result = EvalTaskResult(
        task_name="optimize_auth",
        description="Optimize auth flow",
        project_name="demo",
        mode="code",
        status="failed",
        runtime_seconds=2.0,
        errors=["required file not changed"],
        failure_category="bad_plan",
        validations=[ValidationOutcome(name="required_changed_files", passed=False, message="auth.py not changed")],
        metadata={"failure_map": ["plan_drift"], "failure_map_primary": "plan_drift"},
    )

    store.record_task_result(run_id, task_result)
    store.finalize_run(
        run_id=run_id,
        status="failed",
        passed_tasks=0,
        failed_tasks=1,
        runtime_seconds=2.0,
        total_estimated_cost_usd=None,
    )

    summary = store.failure_map_summary(project_name="demo")

    assert summary["counts"]["plan_drift"] == 1
    assert summary["recent"][0]["tasks"][0]["failure_map_primary"] == "plan_drift"


def test_evaluation_store_success_metrics(tmp_path):
    store = EvaluationStore(tmp_path / "eval.db")
    failed_run_id = store.create_run(
        suite_name="demo_suite",
        suite_path="/tmp/demo_suite.yaml",
        project_name="demo",
        total_tasks=2,
    )
    store.finalize_run(
        run_id=failed_run_id,
        status="failed",
        passed_tasks=1,
        failed_tasks=1,
        runtime_seconds=2.0,
        total_estimated_cost_usd=None,
    )
    skipped_run_id = store.create_run(
        suite_name="future_suite",
        suite_path="/tmp/future_suite.yaml",
        project_name="demo",
        total_tasks=0,
    )
    store.finalize_run(
        run_id=skipped_run_id,
        status="skipped",
        passed_tasks=0,
        failed_tasks=0,
        runtime_seconds=0.0,
        total_estimated_cost_usd=None,
    )
    aborted_run_id = store.create_run(
        suite_name="manual_suite",
        suite_path="/tmp/manual_suite.yaml",
        project_name="demo",
        total_tasks=0,
    )
    store.finalize_run(
        run_id=aborted_run_id,
        status="aborted",
        passed_tasks=0,
        failed_tasks=0,
        runtime_seconds=0.0,
        total_estimated_cost_usd=None,
    )

    metrics = store.success_metrics(project_name="demo")

    assert metrics["run_count"] == 3
    assert metrics["executed_runs"] == 1
    assert metrics["failed_runs"] == 1
    assert metrics["skipped_runs"] == 1
    assert metrics["aborted_runs"] == 1
    assert metrics["run_success_rate"] == 0.0
    assert metrics["passed_tasks"] == 1
    assert metrics["failed_tasks"] == 1
    assert metrics["task_success_rate"] == 0.5
