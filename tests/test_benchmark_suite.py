from __future__ import annotations

from pathlib import Path

from boss.eval.benchmark_suite import BenchmarkSuiteRunner, load_benchmark_manifest
from boss.types import EvalRunResult, EvalTaskResult


class FakeBenchmarkOrchestrator:
    def __init__(self, root_dir: Path) -> None:
        self.calls: list[dict[str, object]] = []
        self.run_id = 0
        self.root_dir = root_dir

    def evaluate_task_suite(self, suite, project_name=None, stop_on_failure=None):
        self.run_id += 1
        self.calls.append(
            {
                "suite_name": suite.name,
                "project_name": project_name,
                "stop_on_failure": stop_on_failure,
                "metadata": dict(suite.metadata),
            }
        )
        if suite.name == "token_service":
            tasks = [
                EvalTaskResult(
                    task_name="implement_token_service",
                    description="Implement token service",
                    project_name=str(project_name or suite.project_name),
                    mode="code",
                    status="failed",
                    runtime_seconds=1.0,
                    errors=["required file not changed"],
                    failure_category="bad_plan",
                    metadata={"failure_map": ["plan_drift"], "failure_map_primary": "plan_drift"},
                )
            ]
            return EvalRunResult(
                run_id=self.run_id,
                suite_name=suite.name,
                suite_path=suite.path,
                project_name=str(project_name or suite.project_name),
                status="failed",
                total_tasks=1,
                passed_tasks=0,
                failed_tasks=1,
                runtime_seconds=1.0,
                tasks=tasks,
            )
        tasks = [
            EvalTaskResult(
                task_name="task",
                description="task",
                project_name=str(project_name or suite.project_name),
                mode="code",
                status="passed",
                runtime_seconds=1.0,
                metadata={},
            )
        ]
        return EvalRunResult(
            run_id=self.run_id,
            suite_name=suite.name,
            suite_path=suite.path,
            project_name=str(project_name or suite.project_name),
            status="passed",
            total_tasks=1,
            passed_tasks=1,
            failed_tasks=0,
            runtime_seconds=1.0,
            tasks=tasks,
        )


def _write_suite(path: Path, name: str, project: str) -> None:
    path.write_text(
        (
            f"name: {name}\n"
            f"project_name: {project}\n"
            "tasks:\n"
            "  - name: task\n"
            "    mode: code\n"
            '    description: "Do work"\n'
        ),
        encoding="utf-8",
    )


def _write_project(root: Path, project: str) -> None:
    project_root = root / "projects" / project
    project_root.mkdir(parents=True, exist_ok=True)


def test_load_benchmark_manifest_resolves_relative_suite_paths(tmp_path):
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    suite_path = suites_dir / "demo.yaml"
    _write_suite(suite_path, "demo", "demo_project")
    manifest_path = tmp_path / "benchmark.yaml"
    manifest_path.write_text(
        """
name: demo_benchmark
description: Demo benchmark
suites:
  - name: demo_suite
    suite: suites/demo.yaml
    repeat: 2
""".strip(),
        encoding="utf-8",
    )

    manifest = load_benchmark_manifest(manifest_path)

    assert manifest.name == "demo_benchmark"
    assert manifest.suites[0].name == "demo_suite"
    assert manifest.suites[0].suite_path == str(suite_path.resolve())
    assert manifest.suites[0].repeat == 2


def test_benchmark_suite_runner_aggregates_results_and_filters_suites(tmp_path):
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    _write_suite(suites_dir / "auth.yaml", "auth_latency", "ael_auth_benchmark")
    _write_suite(suites_dir / "token.yaml", "token_service", "ael_token_service_benchmark")
    _write_project(tmp_path, "ael_auth_benchmark")
    _write_project(tmp_path, "ael_token_service_benchmark")
    manifest_path = tmp_path / "benchmark.yaml"
    manifest_path.write_text(
        """
name: local_reliability_benchmark
suites:
  - name: auth_latency
    suite: suites/auth.yaml
  - name: token_service
    suite: suites/token.yaml
""".strip(),
        encoding="utf-8",
    )

    runner = BenchmarkSuiteRunner(FakeBenchmarkOrchestrator(tmp_path))
    result = runner.run_manifest(manifest_path, only_suites=["auth_latency", "token_service"])

    assert result["total_suite_runs"] == 2
    assert result["executed_suite_runs"] == 2
    assert result["passed_suite_runs"] == 1
    assert result["failed_suite_runs"] == 1
    assert result["skipped_suite_runs"] == 0
    assert result["total_tasks"] == 2
    assert result["passed_tasks"] == 1
    assert result["failed_tasks"] == 1
    assert result["suite_run_success_rate"] == 0.5
    assert result["suite_readiness_rate"] == 1.0
    assert result["task_success_rate"] == 0.5
    assert result["task_variance"] == 0.25
    assert result["stability"] == "low"
    assert result["failure_categories"]["bad_plan"] == 1
    assert result["failure_map"]["plan_drift"] == 1
    assert {item["suite_name"] for item in result["suites"]} == {"auth_latency", "token_service"}
    assert runner.orchestrator.calls[0]["metadata"]["benchmark_mode"] is True


def test_benchmark_suite_runner_labels_perfect_repeats_as_high_stability(tmp_path):
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    _write_suite(suites_dir / "auth.yaml", "auth_latency", "ael_auth_benchmark")
    _write_project(tmp_path, "ael_auth_benchmark")
    manifest_path = tmp_path / "benchmark.yaml"
    manifest_path.write_text(
        """
name: local_reliability_benchmark
suites:
  - name: auth_latency
    suite: suites/auth.yaml
    repeat: 3
""".strip(),
        encoding="utf-8",
    )

    runner = BenchmarkSuiteRunner(FakeBenchmarkOrchestrator(tmp_path))
    result = runner.run_manifest(manifest_path)

    assert result["passed_tasks"] == 3
    assert result["task_variance"] == 0.0
    assert result["stability"] == "high"


def test_benchmark_suite_runner_skips_suite_when_prerequisites_are_missing(tmp_path):
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    suite_path = suites_dir / "future.yaml"
    suite_path.write_text(
        """
name: future_suite
project_name: ext_future
required_python: ">=9.9"
required_modules:
  - definitely_missing_module
tasks:
  - name: task
    mode: code
    description: "Do work"
""".strip(),
        encoding="utf-8",
    )
    _write_project(tmp_path, "ext_future")
    manifest_path = tmp_path / "benchmark.yaml"
    manifest_path.write_text(
        """
name: external_benchmark
suites:
  - name: future_suite
    suite: suites/future.yaml
""".strip(),
        encoding="utf-8",
    )

    runner = BenchmarkSuiteRunner(FakeBenchmarkOrchestrator(tmp_path))
    result = runner.run_manifest(manifest_path)

    assert result["total_suite_runs"] == 1
    assert result["executed_suite_runs"] == 0
    assert result["skipped_suite_runs"] == 1
    assert result["passed_tasks"] == 0
    assert result["failed_tasks"] == 0
    assert result["task_success_rate"] is None
    assert result["suite_run_success_rate"] is None
    assert result["suite_readiness_rate"] == 0.0
    assert not runner.orchestrator.calls
    suite = result["suites"][0]
    assert suite["status"] == "skipped"
    assert "Requires Python >=9.9" in suite["skip_reason"]
    assert "definitely_missing_module" in suite["skip_reason"]


def test_benchmark_suite_runner_repeat_override_repeats_each_suite(tmp_path):
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    _write_suite(suites_dir / "auth.yaml", "auth_latency", "ael_auth_benchmark")
    _write_project(tmp_path, "ael_auth_benchmark")
    manifest_path = tmp_path / "benchmark.yaml"
    manifest_path.write_text(
        """
name: local_reliability_benchmark
suites:
  - name: auth_latency
    suite: suites/auth.yaml
    repeat: 1
""".strip(),
        encoding="utf-8",
    )

    orchestrator = FakeBenchmarkOrchestrator(tmp_path)
    runner = BenchmarkSuiteRunner(orchestrator)
    result = runner.run_manifest(manifest_path, repeat_override=3)

    assert result["total_suite_runs"] == 3
    assert result["executed_suite_runs"] == 3
    assert len(orchestrator.calls) == 3
    assert all(call["metadata"]["benchmark_repeat"] == 3 for call in orchestrator.calls)


def test_repo_golden_tasks_manifest_loads():
    manifest = load_benchmark_manifest(Path("/Users/tj/BOSS/benchmarks/golden_tasks.yaml"))

    assert manifest.name == "boss_golden_tasks"
    assert {suite.name for suite in manifest.suites} == {"auth_latency", "token_service", "rate_limit"}
