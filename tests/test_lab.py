from __future__ import annotations

from pathlib import Path
import subprocess

from boss.eval.project_sandbox import ProjectSandboxManager
from boss.lab.experiment_manager import ExperimentManager
from boss.lab.lab_registry import LabRegistry
from boss.lab.result_analyzer import ResultAnalyzer
from boss.lab.variant_generator import VariantGenerator


def test_variant_generator_creates_baseline_and_candidates():
    generator = VariantGenerator()

    variants = generator.generate(
        experiment_id="exp-1",
        goal="Optimize auth",
        variants=["Cache tokens", "Refactor middleware"],
        benchmark_commands=["pytest"],
        allowed_paths=["auth/"],
        max_iterations=4,
    )

    assert [item.kind for item in variants] == ["baseline", "candidate", "candidate"]
    assert variants[0].mode == "test"
    assert variants[1].mode == "code"
    assert variants[1].benchmark_commands == ["pytest"]
    assert variants[1].allowed_paths == ["auth/"]
    assert variants[1].direct_engineer is True
    assert variants[1].required_changed_files == []
    assert variants[1].max_iterations == 4


def test_variant_generator_promotes_file_allowed_paths_to_target_files():
    generator = VariantGenerator()

    variants = generator.generate(
        experiment_id="exp-2",
        goal="Optimize auth",
        variants=["Enable token cache"],
        benchmark_commands=["python3 benchmark_auth.py"],
        allowed_paths=["auth_service.py"],
        primary_metric="latency_ms",
    )

    candidate = variants[1]
    assert candidate.target_files == ["auth_service.py"]
    assert candidate.required_changed_files == ["auth_service.py"]
    assert candidate.direct_engineer is True
    assert candidate.plan_override


def test_result_analyzer_prefers_lower_latency_metric():
    analyzer = ResultAnalyzer()

    recommendation = analyzer.analyze(
        primary_metric="latency_ms",
        metric_direction="minimize",
        variants=[
            {"variant_id": "baseline", "kind": "baseline", "status": "passed", "runtime_seconds": 4.0, "metrics": {"latency_ms": 120.0}},
            {"variant_id": "variant_a", "kind": "candidate", "status": "passed", "runtime_seconds": 3.5, "metrics": {"latency_ms": 72.0}},
            {"variant_id": "variant_b", "kind": "candidate", "status": "passed", "runtime_seconds": 3.2, "metrics": {"latency_ms": 95.0}},
        ],
    )

    assert recommendation["recommended_variant_id"] == "variant_a"
    assert recommendation["improvement_percent"] > 0


def test_lab_registry_round_trip(tmp_path):
    registry = LabRegistry(tmp_path / "boss_memory.db")
    generator = VariantGenerator()
    experiment_id = "auth-lab-1"

    registry.create_experiment(
        experiment_id=experiment_id,
        project_name="demo",
        goal="Optimize auth",
        primary_metric="latency_ms",
        metric_direction="minimize",
        benchmark_commands=["pytest"],
        allowed_paths=["auth/"],
        metadata={"source": "test"},
    )
    variant = generator.generate(
        experiment_id=experiment_id,
        goal="Optimize auth",
        variants=["Cache tokens"],
        benchmark_commands=["pytest"],
        allowed_paths=["auth/"],
    )[1]
    registry.add_variant(experiment_id, variant)
    registry.record_variant_result(
        variant.variant_id,
        status="passed",
        eval_run_id=7,
        runtime_seconds=12.5,
        sandbox_project_name="__eval__demo__cache",
        sandbox_path="/tmp/cache",
        sandbox_mode="worktree",
        branch_name="boss-lab-demo-cache",
        base_revision="abc123",
        changed_files=["auth/cache.py"],
        metrics={"latency_ms": 72.0},
        output_summary="ok",
        errors=[],
        metadata={"benchmark": True},
    )
    registry.finalize_experiment(
        experiment_id,
        status="completed",
        recommendation={"recommended_variant_id": variant.variant_id, "reason": "faster", "confidence": 0.8},
    )

    experiment = registry.experiment_with_variants(experiment_id)

    assert experiment is not None
    assert experiment["recommended_variant_id"] == variant.variant_id
    assert len(experiment["variants"]) == 1
    assert experiment["variants"][0]["metrics"]["latency_ms"] == 72.0


def test_experiment_manager_applies_variant_files(tmp_path):
    root_dir = tmp_path / "workspace"
    source_root = root_dir / "projects" / "demo"
    sandbox_root = root_dir / "projects" / "__eval__demo__variant"
    source_root.mkdir(parents=True)
    sandbox_root.mkdir(parents=True)
    (source_root / "auth.py").write_text("def auth():\n    return 'old'\n", encoding="utf-8")
    (sandbox_root / "auth.py").write_text("def auth():\n    return 'new'\n", encoding="utf-8")

    registry = LabRegistry(tmp_path / "boss_memory.db")
    generator = VariantGenerator()
    experiment_id = "auth-lab-apply"
    registry.create_experiment(
        experiment_id=experiment_id,
        project_name="demo",
        goal="Optimize auth",
        primary_metric="latency_ms",
        metric_direction="minimize",
        benchmark_commands=[],
        allowed_paths=["auth.py"],
        metadata={},
    )
    variant = generator.generate(experiment_id=experiment_id, goal="Optimize auth", variants=["Refactor auth"])[1]
    registry.add_variant(experiment_id, variant)
    registry.record_variant_result(
        variant.variant_id,
        status="passed",
        eval_run_id=9,
        runtime_seconds=3.0,
        sandbox_project_name="__eval__demo__variant",
        sandbox_path=str(sandbox_root),
        sandbox_mode="copy",
        branch_name=None,
        base_revision=None,
        changed_files=["auth.py"],
        metrics={"latency_ms": 72.0},
        output_summary="ok",
        errors=[],
        metadata={},
    )
    registry.finalize_experiment(
        experiment_id,
        status="completed",
        recommendation={"recommended_variant_id": variant.variant_id, "reason": "faster", "confidence": 0.8},
    )
    manager = ExperimentManager(
        root_dir=root_dir,
        orchestrator=object(),
        registry=registry,
        variant_generator=generator,
        benchmark_runner=object(),
        result_analyzer=ResultAnalyzer(),
    )

    result = manager.apply_variant(variant.variant_id, auto_approve=True)

    assert result["applied"] is True
    assert result["applied_files"] == ["auth.py"]
    assert "return 'new'" in (source_root / "auth.py").read_text(encoding="utf-8")


def test_experiment_manager_cherry_picks_worktree_variant(tmp_path):
    root_dir = tmp_path / "workspace"
    projects_root = root_dir / "projects"
    source_root = projects_root / "demo"
    source_root.mkdir(parents=True)
    (source_root / "auth.py").write_text("def auth():\n    return 'old'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.email", "boss@example.com"], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.name", "BOSS"], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "add", "."], cwd=source_root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=source_root, capture_output=True, text=True, check=True)

    sandbox_manager = ProjectSandboxManager(projects_root)
    sandbox = sandbox_manager.create_sandbox("demo", "optimize auth", mode="worktree")
    (sandbox.sandbox_root / "auth.py").write_text("def auth():\n    return 'new'\n", encoding="utf-8")

    registry = LabRegistry(tmp_path / "boss_memory.db")
    generator = VariantGenerator()
    experiment_id = "auth-lab-worktree"
    registry.create_experiment(
        experiment_id=experiment_id,
        project_name="demo",
        goal="Optimize auth",
        primary_metric="latency_ms",
        metric_direction="minimize",
        benchmark_commands=[],
        allowed_paths=["auth.py"],
        metadata={},
    )
    variant = generator.generate(experiment_id=experiment_id, goal="Optimize auth", variants=["Refactor auth"])[1]
    registry.add_variant(experiment_id, variant)
    registry.record_variant_result(
        variant.variant_id,
        status="passed",
        eval_run_id=10,
        runtime_seconds=2.0,
        sandbox_project_name=sandbox.sandbox_project_name,
        sandbox_path=str(sandbox.sandbox_root),
        sandbox_mode="worktree",
        branch_name=sandbox.branch_name,
        base_revision=sandbox.base_revision,
        changed_files=["auth.py"],
        metrics={"latency_ms": 72.0},
        output_summary="ok",
        errors=[],
        metadata={},
    )
    registry.finalize_experiment(
        experiment_id,
        status="completed",
        recommendation={"recommended_variant_id": variant.variant_id, "reason": "faster", "confidence": 0.8},
    )
    manager = ExperimentManager(
        root_dir=root_dir,
        orchestrator=object(),
        registry=registry,
        variant_generator=generator,
        benchmark_runner=object(),
        result_analyzer=ResultAnalyzer(),
    )

    result = manager.apply_variant(variant.variant_id, auto_approve=True)

    assert result["applied"] is True
    assert result["apply_method"] == "git_cherry_pick"
    assert result["commit_revision"]
    assert "return 'new'" in (source_root / "auth.py").read_text(encoding="utf-8")
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "BOSS AEL apply" in log.stdout


def test_experiment_manager_rejects_sandbox_active_project(tmp_path):
    class SandboxOrchestrator:
        def get_active_project_name(self):
            return "__eval__demo__candidate"

    registry = LabRegistry(tmp_path / "boss_memory.db")
    manager = ExperimentManager(
        root_dir=tmp_path,
        orchestrator=SandboxOrchestrator(),
        registry=registry,
        variant_generator=VariantGenerator(),
        benchmark_runner=object(),
        result_analyzer=ResultAnalyzer(),
    )

    try:
        manager.start_experiment(goal="Optimize auth")
    except RuntimeError as exc:
        assert "sandbox" in str(exc).lower()
    else:
        raise AssertionError("Expected start_experiment to reject sandbox projects.")
