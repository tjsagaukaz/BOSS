from __future__ import annotations

from boss.memory.task_history import TaskHistoryStore


def test_task_history_failure_map_summary_aggregates_step_metadata(tmp_path):
    store = TaskHistoryStore(tmp_path / "boss.db")
    task_id = store.create_task(project_name="demo", task="Optimize auth")
    store.set_plan(task_id, {"goal": "Optimize auth", "steps": ["Edit auth service"]})
    store.start_step(task_id, 0, "Edit auth service")
    store.fail_step(
        task_id=task_id,
        step_index=0,
        errors=["required file not changed"],
        iterations=1,
        engineer_output="",
        test_output={},
        audit_output="",
        metadata={
            "failure_map": ["plan_drift"],
            "failure_map_primary": "plan_drift",
        },
    )
    store.finalize_task(
        task_id=task_id,
        status="failed",
        files_changed=[],
        errors=["required file not changed"],
        final_result="failed",
        metadata={"failure_map_counts": {"plan_drift": 1}},
    )

    summary = store.failure_map_summary(project_name="demo")

    assert summary["counts"]["plan_drift"] == 1
    assert summary["recent"][0]["failure_map_primary"] == "plan_drift"
    assert summary["recent"][0]["step_failures"][0]["failure_map_primary"] == "plan_drift"


def test_task_history_success_metrics(tmp_path):
    store = TaskHistoryStore(tmp_path / "boss.db")
    completed_id = store.create_task(project_name="demo", task="Completed task")
    store.start_step(completed_id, 0, "Step one")
    store.complete_step(
        completed_id,
        0,
        files_changed=["app.py"],
        commit_message="",
        iterations=2,
        engineer_output="ok",
        test_output={},
        audit_output="",
    )
    store.finalize_task(completed_id, status="completed", files_changed=[], errors=[], final_result="ok")
    failed_id = store.create_task(project_name="demo", task="Failed task")
    store.start_step(failed_id, 0, "Step one")
    store.fail_step(
        failed_id,
        0,
        errors=["nope"],
        iterations=1,
        engineer_output="",
        test_output={},
        audit_output="",
    )
    store.finalize_task(failed_id, status="failed", files_changed=[], errors=["nope"], final_result="failed")

    metrics = store.success_metrics(project_name="demo")

    assert metrics["attempted"] == 2
    assert metrics["completed"] == 1
    assert metrics["failed"] == 1
    assert metrics["success_rate"] == 0.5
    assert metrics["step_attempted"] == 2
    assert metrics["step_completed"] == 1
    assert metrics["step_failed"] == 1
    assert metrics["step_success_rate"] == 0.5
    assert metrics["avg_step_iterations"] == 1.5


def test_task_history_reconciles_stale_running_tasks(tmp_path):
    store = TaskHistoryStore(tmp_path / "boss.db")
    task_id = store.create_task(project_name="demo", task="Stale task")
    store.set_plan(task_id, {"goal": "Stale task", "steps": ["Step one"]})
    store.start_step(task_id, 0, "Step one")
    store.merge_task_metadata(task_id, {"owner_pid": 999999})

    reconciled = store.reconcile_stale_tasks(project_name="demo")

    assert len(reconciled) == 1
    task = store.task_with_steps(task_id)
    assert task is not None
    assert task["status"] == "aborted"
    assert "marked as aborted during reconciliation" in task["final_result"]
    assert task["steps"][0]["status"] == "aborted"


def test_task_history_success_metrics_counts_aborted_runs(tmp_path):
    store = TaskHistoryStore(tmp_path / "boss.db")
    task_id = store.create_task(project_name="demo", task="Aborted task")
    store.start_step(task_id, 0, "Step one")
    store.abort_task(task_id, reason="Task runtime exited.")

    metrics = store.success_metrics(project_name="demo")

    assert metrics["attempted"] == 1
    assert metrics["aborted"] == 1
    assert metrics["success_rate"] == 0.0
