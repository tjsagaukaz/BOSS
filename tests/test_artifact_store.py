from __future__ import annotations

import json
from pathlib import Path

from boss.artifacts import ArtifactStore
from boss.types import AutonomousBuildResult, PlanStepContract, StepExecutionResult, StructuredPlan


def test_artifact_store_writes_build_artifact_bundle(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "generated.py").write_text("def generated():\n    return True\n", encoding="utf-8")
    store = ArtifactStore(tmp_path / "artifacts")
    result = AutonomousBuildResult(
        task_id=42,
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

    artifact_path = Path(store.write_build_artifact(task="Generate module", result=result, project_root=project_root))

    assert artifact_path.exists()
    assert (artifact_path / "summary.json").exists()
    assert (artifact_path / "plan.json").exists()
    assert (artifact_path / "run_graph.json").exists()
    assert (artifact_path / "step_results.json").exists()
    assert (artifact_path / "files" / "generated.py").exists()
    index_payload = json.loads((tmp_path / "artifacts" / "index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["kind"] == "build_task"
    assert index_payload["entries"][0]["task_id"] == 42
    assert index_payload["entries"][0]["project_name"] == "demo"
    assert index_payload["entries"][0]["timestamp"]


def test_artifact_store_backfills_index_from_existing_artifacts(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "generated.py").write_text("def generated():\n    return True\n", encoding="utf-8")
    store = ArtifactStore(tmp_path / "artifacts")
    store.write_build_artifact(task="Generate module", result=AutonomousBuildResult(
        task_id=7,
        project_name="demo",
        goal="Generate module",
        status="completed",
        plan=StructuredPlan(goal="Generate module", steps=["Create module"]),
        final_result="Completed 1 step.",
        changed_files=["generated.py"],
    ), project_root=project_root)

    (tmp_path / "artifacts" / "index.json").unlink()

    reloaded = ArtifactStore(tmp_path / "artifacts")
    entries = reloaded.list_index()

    assert len(entries) == 1
    assert entries[0]["kind"] == "build_task"
    assert entries[0]["task_id"] == 7
    assert entries[0]["artifact_path"].endswith("task_000007")
