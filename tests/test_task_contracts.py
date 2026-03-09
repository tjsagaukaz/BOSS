from __future__ import annotations

from boss.eval.task_contracts import find_symbol_occurrences, load_task_suite


def test_load_task_suite_inherits_defaults(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: demo_suite
project_name: demo
default_mode: code
auto_approve: false
max_iterations: 4
tasks:
  - name: add_auth
    description: "Implement JWT auth"
    allowed_paths:
      - auth/
    expected_files:
      - auth.py
    expected_file_contains:
      auth.py:
        - "class AuthService"
  - description: "Plan billing API"
    mode: plan
    auto_approve: true
""".strip(),
        encoding="utf-8",
    )

    suite = load_task_suite(suite_path)

    assert suite.name == "demo_suite"
    assert suite.project_name == "demo"
    assert suite.default_mode == "code"
    assert suite.sandbox_mode is None
    assert suite.keep_sandbox is False
    assert suite.auto_approve is False
    assert suite.max_iterations == 4
    assert len(suite.tasks) == 2
    assert suite.tasks[0].mode == "code"
    assert suite.tasks[0].auto_approve is False
    assert suite.tasks[0].max_iterations == 4
    assert suite.tasks[0].sandbox_mode is None
    assert suite.tasks[0].allowed_paths == ["auth/"]
    assert suite.tasks[0].expected_files == ["auth.py"]
    assert suite.tasks[0].expected_file_contains == {"auth.py": ["class AuthService"]}
    assert suite.tasks[0].expected_imports == []
    assert suite.tasks[0].metric_targets == {}
    assert suite.tasks[0].forbidden_output_contains == []
    assert suite.tasks[1].mode == "plan"
    assert suite.tasks[1].auto_approve is True


def test_find_symbol_occurrences_ignores_ignored_dirs(tmp_path):
    project_root = tmp_path / "demo"
    (project_root / "src").mkdir(parents=True)
    (project_root / "node_modules").mkdir()
    (project_root / "src" / "main.py").write_text("class BillingService:\n    pass\n", encoding="utf-8")
    (project_root / "node_modules" / "ignored.py").write_text("class BillingService:\n    pass\n", encoding="utf-8")

    matches = find_symbol_occurrences(project_root, "BillingService")

    assert matches == ["src/main.py"]


def test_load_task_suite_parses_forbidden_output_contains(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: drift_suite
project_name: demo
tasks:
  - name: direct_edit
    description: "Edit auth service directly"
    forbidden_output_contains:
      - "run the baseline benchmark"
      - "measure the current implementation without modifications"
""".strip(),
        encoding="utf-8",
    )

    suite = load_task_suite(suite_path)

    assert suite.tasks[0].forbidden_output_contains == [
        "run the baseline benchmark",
        "measure the current implementation without modifications",
    ]


def test_load_task_suite_parses_metric_targets_and_expected_imports(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: deterministic_suite
project_name: demo
tasks:
  - name: benchmark_auth
    description: "Benchmark auth middleware"
    mode: build
    expected_imports:
      - generated
    metric_targets:
      latency_ms:
        lte: 5
      tests_passed: true
""".strip(),
        encoding="utf-8",
    )

    suite = load_task_suite(suite_path)

    assert suite.tasks[0].expected_imports == ["generated"]
    assert suite.tasks[0].metric_targets == {
        "latency_ms": {"lte": 5.0},
        "tests_passed": {"eq": 1.0},
    }
