from __future__ import annotations

from boss.eval.eval_store import EvaluationStore
from boss.eval.evaluator import EvaluationHarness
from pathlib import Path

from boss.types import AgentResult, AuditResult, AutonomousBuildResult, StructuredPlan, WorkflowResult


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self._active_project = None

    def get_active_project_name(self):
        return self._active_project

    def set_active_project(self, project_name):
        self._active_project = project_name
        return project_name

    def plan(self, task):
        return AgentResult(
            agent_name="architect",
            provider="fake",
            model="fake-plan-model",
            text=f"Planned task: {task}",
            duration_seconds=0.4,
            usage={"input_tokens": 10, "output_tokens": 14, "total_tokens": 24},
            estimated_cost_usd=0.001,
            tool_records=[],
        )


class FakeBuildOrchestrator:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self._active_project = None
        self.build_calls = []
        self.cleaned_projects = []
        self.project_indexer = _IndexSpy()

    def get_active_project_name(self):
        return self._active_project

    def set_active_project(self, project_name):
        self._active_project = project_name
        return project_name

    def _update_state(self, **updates):
        if "active_project" in updates:
            self._active_project = updates["active_project"]

    def build(
        self,
        task,
        auto_approve=False,
        max_iterations=10,
        commit_changes=False,
        project_name=None,
        store_knowledge=False,
        deep=False,
        benchmark_mode=False,
    ):
        self.build_calls.append(
            {
                "task": task,
                "auto_approve": auto_approve,
                "max_iterations": max_iterations,
                "commit_changes": commit_changes,
                "project_name": project_name,
                "store_knowledge": store_knowledge,
                "deep": deep,
                "benchmark_mode": benchmark_mode,
            }
        )
        target = Path(self.root_dir) / "projects" / str(project_name)
        (target / "generated.py").write_text("def generated():\n    return True\n", encoding="utf-8")
        return AutonomousBuildResult(
            task_id=99,
            project_name=str(project_name),
            goal=task,
            status="completed",
            plan=StructuredPlan(goal=task, steps=["generate file"]),
            runtime_seconds=1.5,
            step_results=[],
            final_result="build complete",
            changed_files=["generated.py"],
            errors=[],
            model_usage=[{"role": "engineer", "model": "fake-build-model", "total_tokens": 12}],
            token_usage={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
            estimated_cost_usd=0.002,
            metadata={"source": "test"},
        )

    def cleanup_project_artifacts(self, project_name, remove_directory=False):
        self.cleaned_projects.append((project_name, remove_directory))
        target = Path(self.root_dir) / "projects" / str(project_name)
        if remove_directory and target.exists():
            import shutil

            shutil.rmtree(target)


class FakeCodeOrchestrator:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self._active_project = None
        self.code_calls = []
        self.project_indexer = _IndexSpy()

    def get_active_project_name(self):
        return self._active_project

    def set_active_project(self, project_name):
        self._active_project = project_name
        return project_name

    def _update_state(self, **updates):
        if "active_project" in updates:
            self._active_project = updates["active_project"]

    def code(
        self,
        task,
        auto_approve=False,
        confirm_overwrite=None,
        max_iterations=2,
        project_name=None,
        store_knowledge=False,
        deep=False,
        skip_planning=False,
        plan_override=None,
        benchmark_mode=False,
        skip_audit=False,
    ):
        self.code_calls.append(
            {
                "task": task,
                "project_name": project_name,
                "skip_planning": skip_planning,
                "plan_override": plan_override,
                "deep": deep,
                "benchmark_mode": benchmark_mode,
                "skip_audit": skip_audit,
            }
        )
        return WorkflowResult(
            plan=AgentResult(
                agent_name="architect",
                provider="system",
                model="task_contract",
                text=plan_override or "Direct engineer contract.",
            ),
            implementation=AgentResult(
                agent_name="engineer",
                provider="fake",
                model="fake-engineer-model",
                text="Implementation summary: run the baseline benchmark before editing anything.",
                duration_seconds=0.8,
                usage={"input_tokens": 12, "output_tokens": 18, "total_tokens": 30},
                estimated_cost_usd=0.002,
                tool_records=[],
            ),
            audit=AuditResult(
                agent_name="auditor",
                provider="fake",
                model="fake-auditor-model",
                text="No issues detected.",
                passed=True,
                duration_seconds=0.4,
                usage={"input_tokens": 8, "output_tokens": 10, "total_tokens": 18},
                estimated_cost_usd=0.001,
                issues=[],
                tool_records=[],
            ),
            iterations=1,
            changed_files=[],
        )


class _IndexSpy:
    def __init__(self):
        self.calls = []

    def index_project(self, project_name, force=False, force_heuristic=False):
        self.calls.append(
            {
                "project_name": project_name,
                "force": force,
                "force_heuristic": force_heuristic,
            }
        )
        return None


def test_evaluation_harness_runs_plan_contract_and_persists_results(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "auth.py").write_text("class DemoToken:\n    pass\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: demo_eval
project_name: demo
tasks:
  - name: plan_auth
    mode: plan
    description: "Plan JWT authentication"
    expected_files:
      - auth.py
    expected_symbols:
      - DemoToken
    expected_output_contains:
      - "Plan"
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(FakeOrchestrator(tmp_path), store)

    result = harness.run_suite(suite_path)

    assert result.status == "passed"
    assert result.passed_tasks == 1
    assert result.failed_tasks == 0
    assert result.tasks[0].token_usage["total_tokens"] == 24
    stored = store.run_with_tasks(result.run_id)
    assert stored is not None
    assert stored.tasks[0].status == "passed"


def test_evaluation_harness_classifies_missing_expected_files(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: demo_eval
project_name: demo
stop_on_failure: true
tasks:
  - name: missing_file
    mode: plan
    description: "Plan JWT authentication"
    expected_files:
      - auth.py
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(FakeOrchestrator(tmp_path), store)

    result = harness.run_suite(suite_path)

    assert result.status == "failed"
    assert result.failed_tasks == 1
    assert result.tasks[0].failure_category == "context_missing"


def test_evaluation_harness_validates_metric_targets_and_writes_artifacts(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: deterministic_eval
project_name: demo
tasks:
  - name: generate_artifact
    mode: build
    description: "Generate the benchmark artifact module"
    expected_files:
      - generated.py
    expected_imports:
      - generated
    metric_targets:
      latency_ms:
        lte: 5
    validation_commands:
      - "{python_bin} -c \\"print('latency_ms: 4')\\""
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    from boss.artifacts import ArtifactStore

    harness = EvaluationHarness(FakeBuildOrchestrator(tmp_path), store, artifact_store=ArtifactStore(tmp_path / "artifacts"))

    result = harness.run_suite(suite_path)

    assert result.status == "passed"
    task = result.tasks[0]
    assert task.status == "passed"
    assert task.metadata["benchmark_metrics"]["latency_ms"] == 4.0
    artifact_path = Path(task.metadata["artifact_path"])
    assert artifact_path.exists()
    assert (artifact_path / "contract.json").exists()
    assert (artifact_path / "task_result.json").exists()
    assert Path(result.metadata["artifact_path"]).exists()


def test_evaluation_harness_fails_metric_target_contract(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: deterministic_eval
project_name: demo
tasks:
  - name: generate_artifact
    mode: build
    description: "Generate the benchmark artifact module"
    expected_files:
      - generated.py
    metric_targets:
      latency_ms:
        lte: 3
    validation_commands:
      - "{python_bin} -c \\"print('latency_ms: 4')\\""
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(FakeBuildOrchestrator(tmp_path), store)

    result = harness.run_suite(suite_path)

    assert result.status == "failed"
    assert any(item.name == "metric_targets" and not item.passed for item in result.tasks[0].validations)
    assert result.tasks[0].failure_category == "validation_failure"


def test_evaluation_harness_validates_expected_file_contents(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "auth.py").write_text("class DemoToken:\n    pass\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: content_eval
project_name: demo
tasks:
  - name: plan_auth
    mode: plan
    description: "Plan JWT authentication"
    expected_file_contains:
      auth.py:
        - "class DemoToken"
        - "pass"
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(FakeOrchestrator(tmp_path), store)

    result = harness.run_suite(suite_path)

    assert result.status == "passed"
    assert result.tasks[0].status == "passed"


def test_evaluation_harness_runs_setup_commands_in_benchmark_venv(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: setup_eval
project_name: demo
tasks:
  - name: plan_with_setup
    mode: plan
    description: "Plan setup-aware task"
    setup_create_venv: true
    setup_commands:
      - "{python_bin} -c \\"__import__('pathlib').Path('setup_marker.txt').write_text('ready', encoding='utf-8')\\""
    expected_files:
      - setup_marker.txt
    expected_file_contains:
      setup_marker.txt:
        - "ready"
    validation_commands:
      - "{python_bin} -m py_compile app.py"
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(FakeOrchestrator(tmp_path), store)

    result = harness.run_suite(suite_path)

    assert result.status == "passed"
    environment = result.tasks[0].metadata["execution_environment"]
    assert environment["created_venv"] is True
    assert environment["venv_dir"]
    assert environment["python_bin"].endswith("/bin/python")
    assert environment["setup_commands"]
    assert (project_root / "setup_marker.txt").read_text(encoding="utf-8") == "ready"


def test_evaluation_harness_defaults_null_venv_dir_metadata(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: setup_eval
project_name: demo
tasks:
  - name: plan_with_setup
    mode: plan
    description: "Plan setup-aware task"
    setup_create_venv: true
    setup_venv_dir:
    setup_commands:
      - "{python_bin} -c \\"print('ok')\\""
""".strip(),
        encoding="utf-8",
    )

    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(FakeOrchestrator(tmp_path), store)

    result = harness.run_suite(suite_path)

    assert result.status == "passed"
    environment = result.tasks[0].metadata["execution_environment"]
    assert environment["venv_dir"].endswith(".boss_benchmark_venv")
    assert "/None/" not in environment["python_bin"]


def test_evaluation_harness_sandboxes_build_tasks(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "main.py").write_text("print('demo')\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: build_eval
project_name: demo
tasks:
  - name: build_feature
    mode: build
    description: "Build auth flow"
    expected_files:
      - generated.py
""".strip(),
        encoding="utf-8",
    )

    orchestrator = FakeBuildOrchestrator(tmp_path)
    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(orchestrator, store)

    result = harness.run_suite(suite_path)

    assert result.status == "passed"
    assert len(orchestrator.build_calls) == 1
    build_call = orchestrator.build_calls[0]
    assert build_call["project_name"] != "demo"
    assert build_call["project_name"].startswith("__eval__demo__")
    assert build_call["store_knowledge"] is False
    assert build_call["benchmark_mode"] is False
    assert orchestrator.cleaned_projects == [(build_call["project_name"], False)]
    assert result.tasks[0].metadata["sandboxed"] is True
    assert not (tmp_path / "projects" / build_call["project_name"]).exists()


def test_evaluation_harness_flags_scope_violations_from_allowed_paths(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "main.py").write_text("print('demo')\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: build_eval
project_name: demo
tasks:
  - name: build_feature
    mode: build
    description: "Build auth flow"
    allowed_paths:
      - src/
    expected_files:
      - generated.py
""".strip(),
        encoding="utf-8",
    )

    orchestrator = FakeBuildOrchestrator(tmp_path)
    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(orchestrator, store)

    result = harness.run_suite(suite_path)

    assert result.status == "failed"
    assert result.tasks[0].failure_category == "scope_violation"
    assert any(item.name == "allowed_paths" and not item.passed for item in result.tasks[0].validations)


def test_evaluation_harness_direct_engineer_contract_sets_plan_drift_failure_map(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "auth_service.py").write_text("CACHE_ENABLED = False\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: code_eval
project_name: demo
tasks:
  - name: optimize_auth
    mode: code
    sandbox_mode: none
    description: "Enable auth token caching."
    required_changed_files:
      - auth_service.py
    forbidden_output_contains:
      - "run the baseline benchmark"
    direct_engineer: true
    plan_override: "Edit auth_service.py directly and do not restate baseline measurements."
""".strip(),
        encoding="utf-8",
    )

    orchestrator = FakeCodeOrchestrator(tmp_path)
    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(orchestrator, store)

    result = harness.run_suite(suite_path)

    assert result.status == "failed"
    assert result.tasks[0].failure_category == "bad_plan"
    assert result.tasks[0].metadata["failure_map_primary"] == "plan_drift"


def test_evaluation_harness_passes_skip_audit_to_code_workflow(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: code_eval
project_name: demo
tasks:
  - name: benchmark_code
    mode: code
    description: "Create a simple example file."
    sandbox_mode: none
    skip_audit: true
""".strip(),
        encoding="utf-8",
    )

    orchestrator = FakeCodeOrchestrator(tmp_path)
    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(orchestrator, store)

    result = harness.run_suite(suite_path)

    assert result.tasks[0].status == "passed"
    assert orchestrator.code_calls[0]["skip_audit"] is True
    assert result.tasks[0].metadata["failure_map"] == []
    assert all(item.passed for item in result.tasks[0].validations)
    assert orchestrator.code_calls[0]["skip_planning"] is False
    assert orchestrator.code_calls[0]["plan_override"] is None
    assert orchestrator.code_calls[0]["benchmark_mode"] is False


def test_evaluation_harness_benchmark_mode_uses_heuristic_index_for_sandbox_activation(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "auth_service.py").write_text("CACHE_ENABLED = False\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: benchmark_eval
project_name: demo
tasks:
  - name: optimize_auth
    mode: code
    sandbox_mode: none
    description: "Enable auth token caching."
    benchmark_mode: true
    direct_engineer: true
    plan_override: "Edit auth_service.py directly."
""".strip(),
        encoding="utf-8",
    )

    orchestrator = FakeCodeOrchestrator(tmp_path)
    store = EvaluationStore(tmp_path / "eval.db")
    harness = EvaluationHarness(orchestrator, store)

    result = harness.run_suite(suite_path)

    assert result.tasks[0].metadata["execution_project_name"] == "demo"
    assert orchestrator.project_indexer.calls[0]["project_name"] == "demo"
    assert orchestrator.project_indexer.calls[0]["force_heuristic"] is True
