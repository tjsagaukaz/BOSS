from __future__ import annotations

import json

from boss.project_brain import ProjectBrainStore
from boss.types import ProjectMap


class _FakeTaskHistory:
    def recent_tasks(self, project_name: str | None = None, limit: int = 10):
        return [
            {"task": "Implement run graph runtime", "status": "completed"},
            {"task": "Stabilize parallel graph execution", "status": "failed"},
        ]


class _FakeEvaluationStore:
    def recent_runs(self, limit: int = 20):
        return [
            {"project_name": "legion", "suite_name": "external_reliability", "status": "failed"},
            {"project_name": "legion", "suite_name": "local_reliability", "status": "passed"},
        ]


def test_project_brain_seeds_from_project_context_and_history(tmp_path):
    store = ProjectBrainStore(
        tmp_path / "brains",
        task_history=_FakeTaskHistory(),
        evaluation_store=_FakeEvaluationStore(),
    )

    brain = store.load(
        "legion",
        summary="Autonomous engineering platform runtime",
        project_map=ProjectMap(
            name="legion",
            overview="Run graph runtime",
            languages={"Python": 10},
            main_modules=["runtime/", "eval/"],
            entry_points=["main.py"],
            key_files=["main.py", "router.py"],
            dependencies=["typer"],
        ),
    )

    assert brain.mission == "Autonomous engineering platform runtime"
    assert "runtime/" in brain.architecture
    assert any("Implement run graph runtime" in item for item in brain.recent_progress)
    assert any("Stabilize parallel graph execution" in item for item in brain.open_problems)
    assert any("Resolve external_reliability evaluation failures" == item for item in brain.next_priorities)
    payload = json.loads((tmp_path / "brains" / "legion.json").read_text(encoding="utf-8"))
    assert payload["project"] == "legion"
    policy = (tmp_path / "brain_policy.yaml").read_text(encoding="utf-8")
    assert "update_mode: confirm" in policy


def test_project_brain_creates_pending_proposal_and_applies_on_approval(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    store.load("legion", summary="Autonomous engineering platform runtime")

    updated = store.record_conversation_signal("legion", "we are focusing on reliability hardening right now")
    assert updated is True
    proposals = store.list_proposals(project_name="legion", status="pending", limit=10)
    assert len(proposals) == 1
    assert proposals[0]["source"] == "explicit_signal"

    result = store.approve_proposal(int(proposals[0]["id"]))
    brain = result["brain"]
    assert brain.current_focus == "reliability hardening right now"


def test_project_brain_eval_runs_apply_automatically_under_default_policy(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    store.load("legion", summary="Autonomous engineering platform runtime")

    brain = store.record_evaluation(
        "legion",
        suite_name="external_reliability",
        status="failed",
        passed_tasks=2,
        total_tasks=5,
        runtime_seconds=12.5,
        artifact_path="/tmp/eval_artifact",
        failure_map={"plan_drift": 2},
    )

    assert any("Evaluation failed: external_reliability" in item for item in brain.open_problems)
    assert brain.recent_artifacts[0]["artifact_path"] == "/tmp/eval_artifact"
    assert store.list_proposals(project_name="legion", status="pending", limit=10) == []


def test_project_brain_milestones_require_confirmation_by_default(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    store.load("legion", summary="Autonomous engineering platform runtime")

    brain = store.record_task_completion(
        "legion",
        task="Implement run replay",
        status="completed",
        changed_files=["boss/artifacts/run_replay.py", "cli/boss_cli.py"],
        artifact_path="/tmp/run_replay_artifact",
    )

    assert all("Implement run replay" not in item for item in brain.recent_progress)
    proposals = store.list_proposals(project_name="legion", status="pending", limit=10)
    assert len(proposals) == 1
    approved = store.approve_proposal(int(proposals[0]["id"]))
    approved_brain = approved["brain"]
    assert approved_brain.recent_progress[0].startswith("Implement run replay completed")
    assert approved_brain.milestones[0] == "Implement run replay"
    assert approved_brain.recent_artifacts[0]["artifact_path"] == "/tmp/run_replay_artifact"


def test_project_brain_reject_proposal_leaves_brain_unchanged(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    original = store.load("legion", summary="Autonomous engineering platform runtime")

    store.record_conversation_signal("legion", "we are focusing on artifact observability right now")
    proposals = store.list_proposals(project_name="legion", status="pending", limit=10)
    assert len(proposals) == 1

    result = store.reject_proposal(int(proposals[0]["id"]))
    assert result["status"] == "rejected"

    brain = store.load("legion", summary="Autonomous engineering platform runtime")
    assert brain.current_focus == original.current_focus


def test_project_brain_conversation_signal_can_propose_brain_rule(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    store.load("legion", summary="Autonomous engineering platform runtime")

    updated = store.record_conversation_signal("legion", "never use Flask for new APIs")

    assert updated is True
    proposals = store.list_proposals(project_name="legion", status="pending", limit=10)
    assert len(proposals) == 1
    assert proposals[0]["proposal"]["brain_rules_add"] == ["Never use Flask for new APIs."]

    approved = store.approve_proposal(int(proposals[0]["id"]))
    assert approved["brain"].brain_rules == ["Never use Flask for new APIs."]


def test_project_brain_manual_add_and_remove_rule_apply_immediately(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    store.load("legion", summary="Autonomous engineering platform runtime")

    brain = store.add_rule("legion", "Prefer FastAPI for new HTTP services")
    assert brain.brain_rules == ["Prefer FastAPI for new HTTP services."]

    brain = store.remove_rule("legion", "Prefer FastAPI for new HTTP services")
    assert brain.brain_rules == []


def test_project_brain_reset_restores_previous_stable_snapshot(tmp_path):
    store = ProjectBrainStore(tmp_path / "brains")
    store.load("legion", summary="Autonomous engineering platform runtime")
    store.record_conversation_signal("legion", "we are focusing on artifact observability right now")
    proposal = store.list_proposals(project_name="legion", status="pending", limit=10)[0]
    store.approve_proposal(int(proposal["id"]))

    changed = store.load("legion", summary="Autonomous engineering platform runtime")
    assert changed.current_focus == "artifact observability right now"

    reset = store.reset("legion", summary="Autonomous engineering platform runtime")
    assert reset.current_focus != "artifact observability right now"
