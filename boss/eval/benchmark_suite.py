from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from typing import Any

from boss.eval.task_contracts import load_task_suite

try:  # pragma: no cover - dependency import is environment specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class BenchmarkSuiteSpec:
    name: str
    suite_path: str
    project_name: str | None = None
    repeat: int = 1
    stop_on_failure: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkManifest:
    name: str
    path: str
    description: str = ""
    suites: list[BenchmarkSuiteSpec] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_benchmark_manifest(path: str | Path) -> BenchmarkManifest:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"Benchmark manifest not found: {manifest_path}")
    if yaml is None:
        raise RuntimeError("PyYAML is required to load benchmark manifests.")

    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Benchmark manifest must be a mapping with a top-level 'suites' key.")

    suites_raw = raw.get("suites")
    if not isinstance(suites_raw, list) or not suites_raw:
        raise ValueError("Benchmark manifest must define a non-empty 'suites' list.")

    manifest = BenchmarkManifest(
        name=str(raw.get("name", manifest_path.stem)).strip() or manifest_path.stem,
        path=str(manifest_path),
        description=str(raw.get("description", "")).strip(),
        metadata=_extra_metadata(raw, {"name", "description", "suites"}),
    )
    base_dir = manifest_path.parent
    for item in suites_raw:
        spec = _parse_suite_spec(item, base_dir=base_dir)
        if spec is not None:
            manifest.suites.append(spec)
    if not manifest.suites:
        raise ValueError("Benchmark manifest contains no enabled suites.")
    return manifest


class BenchmarkSuiteRunner:
    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator

    def run_manifest(
        self,
        manifest_path: str | Path,
        only_suites: list[str] | None = None,
        repeat_override: int | None = None,
    ) -> dict[str, Any]:
        manifest = load_benchmark_manifest(manifest_path)
        selected = {item.strip() for item in (only_suites or []) if item.strip()}
        suite_runs: list[dict[str, Any]] = []
        matched_names: set[str] = set()

        for spec in manifest.suites:
            if selected and spec.name not in selected:
                continue
            matched_names.add(spec.name)
            repeat_count = int(repeat_override or spec.repeat)
            for iteration in range(1, repeat_count + 1):
                suite = load_task_suite(spec.suite_path)
                suite.name = spec.name
                suite.project_name = spec.project_name or suite.project_name
                for contract in suite.tasks:
                    setup_metadata = {}
                    for key in (
                        "python_bin",
                        "setup_commands",
                        "setup_create_venv",
                        "setup_venv_dir",
                        "setup_timeout",
                        "skip_audit",
                    ):
                        value = suite.metadata.get(key, contract.metadata.get(key))
                        if value is not None:
                            setup_metadata[key] = value
                    contract.metadata = {
                        **contract.metadata,
                        "benchmark_mode": True,
                        "disable_prompt_evolution": True,
                        "disable_memory_writes": True,
                        "disable_solution_promotion": True,
                        **setup_metadata,
                    }
                suite.metadata = {
                    **suite.metadata,
                    "benchmark_name": manifest.name,
                    "benchmark_description": manifest.description,
                    "benchmark_suite": spec.name,
                    "benchmark_iteration": iteration,
                    "benchmark_repeat": repeat_count,
                    "benchmark_manifest_path": manifest.path,
                    "benchmark_mode": True,
                    **manifest.metadata,
                    **spec.metadata,
                }
                preflight = self._preflight_suite(suite)
                if not preflight["ready"]:
                    suite_runs.append(
                        self._skipped_suite_summary(
                            spec=spec,
                            iteration=iteration,
                            suite=suite,
                            reason=str(preflight.get("reason", "suite preflight failed")),
                        )
                    )
                    continue
                run_result = self.orchestrator.evaluate_task_suite(
                    suite=suite,
                    project_name=suite.project_name,
                    stop_on_failure=spec.stop_on_failure,
                )
                suite_runs.append(
                    self._suite_run_summary(
                        spec=spec,
                        iteration=iteration,
                        result=run_result,
                    )
                )

        if selected and matched_names != selected:
            missing = ", ".join(sorted(selected - matched_names))
            raise RuntimeError(f"Benchmark suites not found in manifest: {missing}")

        return self._aggregate_results(manifest=manifest, suite_runs=suite_runs)

    def _suite_run_summary(self, *, spec: BenchmarkSuiteSpec, iteration: int, result) -> dict[str, Any]:
        failure_categories: dict[str, int] = {}
        failure_map: dict[str, int] = {}
        task_iterations: list[int] = []
        for task in result.tasks:
            if task.failure_category:
                failure_categories[task.failure_category] = failure_categories.get(task.failure_category, 0) + 1
            for label in (task.metadata or {}).get("failure_map", []):
                key = str(label)
                failure_map[key] = failure_map.get(key, 0) + 1
            if "iterations" in (task.metadata or {}):
                task_iterations.append(int((task.metadata or {}).get("iterations", 0) or 0))
        return {
            "suite_name": spec.name,
            "suite_path": spec.suite_path,
            "project_name": result.project_name,
            "iteration": iteration,
            "run_id": result.run_id,
            "status": result.status,
            "total_tasks": result.total_tasks,
            "passed_tasks": result.passed_tasks,
            "failed_tasks": result.failed_tasks,
            "task_success_rate": float(result.passed_tasks / result.total_tasks) if result.total_tasks else None,
            "runtime_seconds": result.runtime_seconds,
            "avg_iterations": float(sum(task_iterations) / len(task_iterations)) if task_iterations else None,
            "estimated_cost_usd": result.total_estimated_cost_usd,
            "failure_categories": failure_categories,
            "failure_map": failure_map,
        }

    def _aggregate_results(self, *, manifest: BenchmarkManifest, suite_runs: list[dict[str, Any]]) -> dict[str, Any]:
        total_suite_runs = len(suite_runs)
        passed_suite_runs = sum(1 for item in suite_runs if item["status"] == "passed")
        failed_suite_runs = sum(1 for item in suite_runs if item["status"] == "failed")
        skipped_suite_runs = sum(1 for item in suite_runs if item["status"] == "skipped")
        executed_suite_runs = passed_suite_runs + failed_suite_runs
        total_tasks = sum(int(item["total_tasks"]) for item in suite_runs)
        passed_tasks = sum(int(item["passed_tasks"]) for item in suite_runs)
        failed_tasks = sum(int(item["failed_tasks"]) for item in suite_runs)
        runtimes = [float(item["runtime_seconds"] or 0.0) for item in suite_runs]

        failure_categories: dict[str, int] = {}
        failure_map: dict[str, int] = {}
        by_project: dict[str, dict[str, Any]] = {}
        by_suite: dict[str, dict[str, Any]] = {}

        for item in suite_runs:
            for name, count in item["failure_categories"].items():
                failure_categories[name] = failure_categories.get(name, 0) + int(count)
            for name, count in item["failure_map"].items():
                failure_map[name] = failure_map.get(name, 0) + int(count)

            project_bucket = by_project.setdefault(
                item["project_name"],
                {
                    "project_name": item["project_name"],
                    "suite_runs": 0,
                    "skipped_suite_runs": 0,
                    "total_tasks": 0,
                    "passed_tasks": 0,
                    "failed_tasks": 0,
                    "skip_reasons": [],
                },
            )
            project_bucket["suite_runs"] += 1
            if item["status"] == "skipped":
                project_bucket["skipped_suite_runs"] += 1
                if item.get("skip_reason"):
                    project_bucket["skip_reasons"].append(str(item["skip_reason"]))
            project_bucket["total_tasks"] += int(item["total_tasks"])
            project_bucket["passed_tasks"] += int(item["passed_tasks"])
            project_bucket["failed_tasks"] += int(item["failed_tasks"])
            project_bucket.setdefault("runtimes", []).append(float(item["runtime_seconds"] or 0.0))

            suite_bucket = by_suite.setdefault(
                item["suite_name"],
                {
                    "suite_name": item["suite_name"],
                    "project_name": item["project_name"],
                    "suite_runs": 0,
                    "skipped_suite_runs": 0,
                    "total_tasks": 0,
                    "passed_tasks": 0,
                    "failed_tasks": 0,
                    "run_ids": [],
                    "runtimes": [],
                    "iteration_values": [],
                    "skip_reasons": [],
                },
            )
            suite_bucket["suite_runs"] += 1
            if item["status"] == "skipped":
                suite_bucket["skipped_suite_runs"] += 1
                if item.get("skip_reason"):
                    suite_bucket["skip_reasons"].append(str(item["skip_reason"]))
            suite_bucket["total_tasks"] += int(item["total_tasks"])
            suite_bucket["passed_tasks"] += int(item["passed_tasks"])
            suite_bucket["failed_tasks"] += int(item["failed_tasks"])
            if item.get("run_id") is not None:
                suite_bucket["run_ids"].append(int(item["run_id"]))
            suite_bucket["runtimes"].append(float(item["runtime_seconds"] or 0.0))
            if item.get("avg_iterations") is not None:
                suite_bucket["iteration_values"].append(float(item["avg_iterations"]))

        projects = []
        for bucket in by_project.values():
            total = int(bucket["total_tasks"])
            bucket["task_success_rate"] = float(bucket["passed_tasks"] / total) if total else None
            bucket["avg_runtime_seconds"] = float(sum(bucket["runtimes"]) / len(bucket["runtimes"])) if bucket["runtimes"] else None
            bucket["median_runtime_seconds"] = float(statistics.median(bucket["runtimes"])) if bucket["runtimes"] else None
            bucket["stability_variance"] = self._stability_variance(bucket["passed_tasks"], total)
            bucket["task_variance"] = bucket["stability_variance"]
            bucket["stability"] = self._stability_label(bucket["stability_variance"])
            bucket["status"] = self._aggregate_status(bucket["suite_runs"], bucket["skipped_suite_runs"], bucket["failed_tasks"])
            bucket["skip_reason"] = bucket["skip_reasons"][0] if bucket["skip_reasons"] else None
            bucket.pop("runtimes", None)
            bucket.pop("skip_reasons", None)
            projects.append(bucket)
        projects.sort(key=lambda item: (-(item["task_success_rate"] or 0.0), item["project_name"]))

        suites = []
        for bucket in by_suite.values():
            total = int(bucket["total_tasks"])
            bucket["task_success_rate"] = float(bucket["passed_tasks"] / total) if total else None
            bucket["avg_runtime_seconds"] = float(sum(bucket["runtimes"]) / len(bucket["runtimes"])) if bucket["runtimes"] else None
            bucket["median_runtime_seconds"] = float(statistics.median(bucket["runtimes"])) if bucket["runtimes"] else None
            bucket["avg_iterations"] = float(sum(bucket["iteration_values"]) / len(bucket["iteration_values"])) if bucket["iteration_values"] else None
            bucket["stability_variance"] = self._stability_variance(bucket["passed_tasks"], total)
            bucket["task_variance"] = bucket["stability_variance"]
            bucket["stability"] = self._stability_label(bucket["stability_variance"])
            bucket["status"] = self._aggregate_status(bucket["suite_runs"], bucket["skipped_suite_runs"], bucket["failed_tasks"])
            bucket["skip_reason"] = bucket["skip_reasons"][0] if bucket["skip_reasons"] else None
            bucket.pop("runtimes", None)
            bucket.pop("iteration_values", None)
            bucket.pop("skip_reasons", None)
            suites.append(bucket)
        suites.sort(key=lambda item: item["suite_name"])

        return {
            "name": manifest.name,
            "path": manifest.path,
            "description": manifest.description,
            "total_suite_runs": total_suite_runs,
            "passed_suite_runs": passed_suite_runs,
            "failed_suite_runs": failed_suite_runs,
            "skipped_suite_runs": skipped_suite_runs,
            "executed_suite_runs": executed_suite_runs,
            "suite_run_success_rate": float(passed_suite_runs / executed_suite_runs) if executed_suite_runs else None,
            "suite_readiness_rate": float(executed_suite_runs / total_suite_runs) if total_suite_runs else None,
            "total_tasks": total_tasks,
            "passed_tasks": passed_tasks,
            "failed_tasks": failed_tasks,
            "task_success_rate": float(passed_tasks / total_tasks) if total_tasks else None,
            "avg_runtime_seconds": float(sum(runtimes) / len(runtimes)) if runtimes else None,
            "median_runtime_seconds": float(statistics.median(runtimes)) if runtimes else None,
            "stability_variance": self._stability_variance(passed_tasks, total_tasks),
            "task_variance": self._stability_variance(passed_tasks, total_tasks),
            "stability": self._stability_label(self._stability_variance(passed_tasks, total_tasks)),
            "failure_categories": dict(sorted(failure_categories.items(), key=lambda item: (-item[1], item[0]))),
            "failure_map": dict(sorted(failure_map.items(), key=lambda item: (-item[1], item[0]))),
            "projects": projects,
            "suites": suites,
            "suite_runs": suite_runs,
        }

    def _stability_variance(self, successes: int, attempts: int) -> float | None:
        if attempts <= 0:
            return None
        probability = float(successes / attempts)
        return probability * (1.0 - probability)

    def _stability_label(self, variance: float | None) -> str:
        if variance is None:
            return "unknown"
        if variance <= 0.02:
            return "high"
        if variance <= 0.12:
            return "medium"
        return "low"

    def _preflight_suite(self, suite) -> dict[str, Any]:
        project_name = str(suite.project_name or "").strip()
        if not project_name:
            return {"ready": False, "reason": "No project configured for suite."}

        project_root = Path(self.orchestrator.root_dir) / "projects" / project_name
        if not project_root.exists():
            return {"ready": False, "reason": f"Project '{project_name}' is not synced locally."}

        metadata = dict(getattr(suite, "metadata", {}) or {})
        reasons: list[str] = []

        required_python = str(metadata.get("required_python", "")).strip()
        resolved_python_bin = self._resolve_python_bin(metadata.get("python_bin"))
        requested_python = str(metadata.get("python_bin", "")).strip()
        if requested_python and resolved_python_bin is None:
            reasons.append(f"Interpreter not found: {requested_python}.")
        if required_python and resolved_python_bin is not None and not self._python_requirement_satisfied(required_python, resolved_python_bin):
            interpreter_version = self._python_version(resolved_python_bin)
            if interpreter_version is None:
                python_label = requested_python or "unknown"
            else:
                python_label = f"{interpreter_version[0]}.{interpreter_version[1]}"
            reasons.append(
                f"Requires Python {required_python}; current interpreter is {python_label}."
            )

        required_modules = self._as_list(metadata.get("required_modules"))
        missing_modules = [name for name in required_modules if importlib.util.find_spec(name) is None]
        if missing_modules:
            reasons.append(f"Missing modules: {', '.join(missing_modules)}.")

        required_commands = self._as_list(metadata.get("required_commands"))
        missing_commands = [name for name in required_commands if shutil.which(name) is None]
        if missing_commands:
            reasons.append(f"Missing commands: {', '.join(missing_commands)}.")

        if reasons:
            return {"ready": False, "reason": " ".join(reasons)}
        return {"ready": True, "reason": None}

    def _python_requirement_satisfied(self, requirement: str, python_bin: str | None = None) -> bool:
        text = requirement.strip()
        if not text:
            return True
        version_text = text[2:].strip() if text.startswith(">=") else text
        parts = [part for part in version_text.split(".") if part]
        try:
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except (IndexError, ValueError):
            return True
        interpreter_version = self._python_version(python_bin)
        if interpreter_version is None:
            return False
        return interpreter_version >= (major, minor)

    def _skipped_suite_summary(self, *, spec: BenchmarkSuiteSpec, iteration: int, suite, reason: str) -> dict[str, Any]:
        return {
            "suite_name": spec.name,
            "suite_path": spec.suite_path,
            "project_name": suite.project_name,
            "iteration": iteration,
            "run_id": None,
            "status": "skipped",
            "total_tasks": 0,
            "passed_tasks": 0,
            "failed_tasks": 0,
            "task_success_rate": None,
            "runtime_seconds": 0.0,
            "avg_iterations": None,
            "estimated_cost_usd": None,
            "failure_categories": {},
            "failure_map": {},
            "skip_reason": reason,
        }

    def _aggregate_status(self, suite_runs: int, skipped_suite_runs: int, failed_tasks: int) -> str:
        if suite_runs > 0 and skipped_suite_runs == suite_runs:
            return "skipped"
        if failed_tasks > 0:
            return "failed"
        if skipped_suite_runs > 0:
            return "mixed"
        return "passed"

    def _as_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        cleaned = str(value).strip()
        return [cleaned] if cleaned else []

    def _resolve_python_bin(self, value: Any) -> str | None:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in {"current", "default"}:
            return sys.executable
        candidate = Path(cleaned).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        executable = shutil.which(cleaned)
        return executable

    def _python_version(self, python_bin: str | None) -> tuple[int, int] | None:
        interpreter = python_bin or sys.executable
        try:
            completed = subprocess.run(
                [interpreter, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        output = completed.stdout.strip()
        try:
            major_text, minor_text = output.split(".", 1)
            return int(major_text), int(minor_text)
        except Exception:
            return None


def _parse_suite_spec(raw: Any, *, base_dir: Path) -> BenchmarkSuiteSpec | None:
    if isinstance(raw, str):
        suite_path = _resolve_suite_path(raw, base_dir)
        return BenchmarkSuiteSpec(name=Path(suite_path).stem, suite_path=suite_path)
    if not isinstance(raw, dict):
        raise ValueError("Each benchmark suite entry must be a string path or a mapping.")
    if raw.get("enabled", True) is False:
        return None
    suite_value = raw.get("suite") or raw.get("suite_path") or raw.get("path")
    if suite_value is None:
        raise ValueError("Benchmark suite entry must define 'suite' or 'suite_path'.")
    suite_path = _resolve_suite_path(str(suite_value), base_dir)
    name = str(raw.get("name", Path(suite_path).stem)).strip() or Path(suite_path).stem
    repeat = int(raw.get("repeat", 1))
    if repeat < 1:
        raise ValueError("Benchmark suite repeat must be at least 1.")
    return BenchmarkSuiteSpec(
        name=name,
        suite_path=suite_path,
        project_name=_optional_string(raw.get("project_name") or raw.get("project")),
        repeat=repeat,
        stop_on_failure=bool(raw["stop_on_failure"]) if "stop_on_failure" in raw else None,
        metadata=_extra_metadata(raw, {"name", "suite", "suite_path", "path", "project_name", "project", "repeat", "stop_on_failure", "enabled"}),
    )


def _resolve_suite_path(value: str, base_dir: Path) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"Benchmark suite not found: {candidate}")
    return str(candidate)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _extra_metadata(raw: dict[str, Any], known_keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in known_keys}
