from __future__ import annotations

from boss.runtime.planning_spine import PlanningSpine
from boss.types import PlanStepContract, StructuredPlan


def test_planning_spine_parses_structured_steps():
    text = """
{
  "goal": "Add request logging middleware",
  "steps": [
    {
      "id": "S1",
      "title": "Create middleware module",
      "objective": "Create request logging middleware",
      "allowed_paths": ["middleware/"],
      "expected_outputs": ["middleware/logging.py"],
      "required_artifacts": ["middleware/logging.py"],
      "validation": ["tests_pass"],
      "done_when": ["file:middleware/logging.py exists"],
      "validation_commands": ["pytest tests/test_logging.py"]
    },
    {
      "id": "S2",
      "title": "Register middleware",
      "objective": "Register middleware in the app entrypoint",
      "allowed_paths": ["app.py"],
      "expected_outputs": ["app.py"],
      "validation": ["tests_pass"],
      "done_when": ["file:app.py contains LoggingMiddleware"],
      "validation_commands": []
    }
  ]
}
""".strip()

    spine = PlanningSpine.from_text(task_id="task-1", fallback_goal="fallback", text=text)
    plan = spine.to_structured_plan()

    assert plan.goal == "Add request logging middleware"
    assert [contract.step_id for contract in plan.contracts] == ["S1", "S2"]
    assert plan.contracts[0].allowed_paths == ["middleware/"]
    assert plan.contracts[0].expected_outputs == ["middleware/logging.py"]
    assert plan.contracts[0].agent_role == "engineer"
    assert plan.contracts[1].dependencies == []
    assert plan.contracts[1].done_when == ["file:app.py contains LoggingMiddleware"]


def test_planning_spine_parses_dependencies_and_agent_role():
    text = """
{
  "goal": "Add request logging middleware",
  "steps": [
    {
      "id": "S1",
      "title": "Create middleware module",
      "objective": "Create request logging middleware",
      "agent_role": "engineer",
      "allowed_paths": ["middleware/"],
      "expected_outputs": ["middleware/logging.py"],
      "required_artifacts": ["middleware/logging.py"],
      "validation": ["tests_pass"],
      "done_when": ["file:middleware/logging.py exists"],
      "validation_commands": ["pytest tests/test_logging.py"]
    },
    {
      "id": "S2",
      "title": "Add tests",
      "objective": "Add middleware tests",
      "agent_role": "test",
      "dependencies": ["S1"],
      "allowed_paths": ["tests/"],
      "expected_outputs": ["tests/test_logging.py"],
      "required_artifacts": ["tests/test_logging.py"],
      "validation": ["tests_pass"],
      "done_when": ["file:tests/test_logging.py exists"],
      "validation_commands": ["pytest tests/test_logging.py"]
    }
  ]
}
""".strip()

    spine = PlanningSpine.from_text(task_id="task-2", fallback_goal="fallback", text=text)
    plan = spine.to_structured_plan()

    assert plan.contracts[0].agent_role == "engineer"
    assert plan.contracts[1].agent_role == "test"
    assert plan.contracts[1].dependencies == ["S1"]


def test_planning_spine_validates_scope_and_expected_outputs(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "auth").mkdir()
    (project_root / "auth" / "service.py").write_text("def build_auth():\n    return True\n", encoding="utf-8")
    contract = PlanStepContract(
        title="Create auth service",
        step_id="S1",
        objective="Create auth service",
        allowed_paths=["auth/"],
        expected_outputs=["auth/service.py"],
        validation=["tests_pass"],
    )

    no_errors = PlanningSpine.validate_contract_outputs(
        project_root,
        contract,
        changed_files=["auth/service.py"],
    )
    scope_errors = PlanningSpine.validate_contract_outputs(
        project_root,
        contract,
        changed_files=["billing/service.py"],
    )

    assert no_errors == []
    assert any("outside allowed paths" in error for error in scope_errors)


def test_planning_spine_tracks_current_step_payload():
    plan = StructuredPlan(
        goal="Implement auth",
        steps=["Create service", "Wire endpoint"],
        contracts=[
            PlanStepContract(title="Create service", step_id="S1", objective="Create auth service"),
            PlanStepContract(title="Wire endpoint", step_id="S2", objective="Register auth route"),
        ],
        raw_text="",
    )

    spine = PlanningSpine.from_plan(task_id="task-99", plan=plan)
    spine.mark_attempt(0)
    current = spine.current_step_payload()

    assert current["current_step_number"] == 1
    assert current["step"]["id"] == "S1"
    assert current["step"]["attempts"] == 1

    spine.mark_completed(0)
    next_step = spine.current_step_payload()

    assert next_step["current_step_number"] == 2
    assert next_step["completed_steps"] == 1
    assert next_step["step"]["id"] == "S2"


def test_planning_spine_execution_payload_targets_specific_step():
    plan = StructuredPlan(
        goal="Implement auth",
        steps=["Create service", "Wire endpoint"],
        contracts=[
            PlanStepContract(title="Create service", step_id="S1", objective="Create auth service", agent_role="engineer"),
            PlanStepContract(
                title="Wire endpoint",
                step_id="S2",
                objective="Register auth route",
                agent_role="auditor",
                dependencies=["S1"],
            ),
        ],
        raw_text="",
    )

    spine = PlanningSpine.from_plan(task_id="task-100", plan=plan)
    payload = spine.execution_payload(1)

    assert payload["current_step_number"] == 2
    assert payload["step"]["id"] == "S2"
    assert payload["step"]["agent_role"] == "auditor"
    assert payload["step"]["dependencies"] == ["S1"]
