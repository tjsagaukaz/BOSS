from __future__ import annotations

from boss.engine.autonomous_loop import AutonomousDevelopmentLoop
from boss.types import PlanStepContract


def test_parse_structured_plan_builds_contracts():
    loop = object.__new__(AutonomousDevelopmentLoop)
    text = """
{
  "goal": "Create benchmark artifact",
  "steps": [
    {
      "title": "Create benchmark file",
      "objective": "Write the benchmark markdown artifact",
      "required_artifacts": ["benchmark_artifact.md"],
      "done_when": [
        "file:benchmark_artifact.md exists",
        "file:benchmark_artifact.md not_empty",
        "file:benchmark_artifact.md contains This file was generated during a BOSS evaluation benchmark."
      ],
      "validation_commands": []
    }
  ]
}
""".strip()

    plan = loop._parse_structured_plan("Create file", text)

    assert plan.goal == "Create benchmark artifact"
    assert plan.steps == ["Create benchmark file"]
    assert len(plan.contracts) == 1
    assert plan.contracts[0].required_artifacts == ["benchmark_artifact.md"]
    assert "file:benchmark_artifact.md not_empty" in plan.contracts[0].done_when


def test_validate_step_contract_detects_empty_required_artifact(tmp_path):
    loop = object.__new__(AutonomousDevelopmentLoop)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "benchmark_artifact.md").write_text("", encoding="utf-8")
    contract = PlanStepContract(
        title="Create benchmark file",
        objective="Write benchmark markdown",
        required_artifacts=["benchmark_artifact.md"],
        done_when=["file:benchmark_artifact.md not_empty"],
    )

    errors = loop._validate_step_contract(project_root, contract)

    assert any("empty" in error.lower() for error in errors)
