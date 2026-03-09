from __future__ import annotations

from rich.console import Console

from boss.dashboard.task_dashboard import TaskDashboard


def test_task_dashboard_render_task_shows_failure_map_summary():
    dashboard = TaskDashboard()
    task = {
        "id": 7,
        "project_name": "demo",
        "task": "Optimize auth",
        "status": "failed",
        "plan": {"steps": ["Edit auth service"]},
        "steps": [
            {
                "step_index": 0,
                "title": "Edit auth service",
                "status": "failed",
                "iterations": 1,
                "files_changed": ["auth.py"],
                "metadata": {"failure_map_primary": "plan_drift"},
            }
        ],
        "metadata": {"failure_map_counts": {"plan_drift": 1}},
    }

    rendered = dashboard.render_task(task)
    console = Console(record=True, width=120)
    console.print(rendered)
    output = console.export_text()

    assert "Failure Map" in output
    assert "plan_drift=1" in output


def test_task_dashboard_render_reliability_shows_task_and_eval_patterns():
    dashboard = TaskDashboard()
    rendered = dashboard.render_reliability(
        {
            "project_name": "demo",
            "tasks": {"counts": {"plan_drift": 2}},
            "evaluations": {"counts": {"tool_misuse": 1}},
        }
    )
    console = Console(record=True, width=120)
    console.print(rendered)
    output = console.export_text()

    assert "Reliability Snapshot" in output
    assert "plan_drift=2" in output
    assert "tool_misuse=1" in output
