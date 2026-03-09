from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from boss.context.codebase_scanner import CodebaseScanner
from boss.types import TaskContract, TaskSuite

try:  # pragma: no cover - dependency import is environment specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


ALLOWED_MODES = {"plan", "code", "audit", "build", "test"}
ALLOWED_SANDBOX_MODES = {"auto", "copy", "worktree", "none"}


def load_task_suite(path: str | Path) -> TaskSuite:
    suite_path = Path(path).expanduser().resolve()
    if not suite_path.exists():
        raise FileNotFoundError(f"Task suite not found: {suite_path}")
    if yaml is None:
        raise RuntimeError("PyYAML is required to load evaluation task suites.")

    raw = yaml.safe_load(suite_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Task suite must be a mapping with a top-level 'tasks' key.")

    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("Task suite must define a non-empty 'tasks' list.")

    default_mode = str(raw.get("mode", raw.get("default_mode", "code"))).strip().lower() or "code"
    if default_mode not in ALLOWED_MODES:
        raise ValueError(f"Unsupported default mode '{default_mode}'.")

    suite = TaskSuite(
        name=str(raw.get("name", suite_path.stem)).strip() or suite_path.stem,
        path=str(suite_path),
        project_name=_optional_string(raw.get("project_name") or raw.get("project")),
        default_mode=default_mode,
        sandbox_mode=_parse_sandbox_mode(raw.get("sandbox_mode")),
        keep_sandbox=bool(raw.get("keep_sandbox", False)),
        auto_approve=bool(raw.get("auto_approve", True)),
        max_iterations=int(raw.get("max_iterations", 2)),
        stop_on_failure=bool(raw.get("stop_on_failure", False)),
        metadata=_extra_metadata(
            raw,
            known_keys={
                "name",
                "path",
                "project_name",
                "project",
                "mode",
                "default_mode",
                "sandbox_mode",
                "keep_sandbox",
                "auto_approve",
                "max_iterations",
                "stop_on_failure",
                "tasks",
            },
        ),
    )

    for item in tasks_raw:
        if not isinstance(item, dict):
            raise ValueError("Each task definition must be a mapping.")
        mode = str(item.get("mode", default_mode)).strip().lower() or default_mode
        if mode not in ALLOWED_MODES:
            raise ValueError(f"Unsupported task mode '{mode}'.")
        description = str(item.get("description", "")).strip()
        if not description:
            raise ValueError("Each task must define a non-empty 'description'.")
        name = str(item.get("name", description[:80])).strip() or description[:80]
        suite.tasks.append(
            TaskContract(
                name=name,
                description=description,
                mode=mode,
                project_name=_optional_string(item.get("project_name") or item.get("project") or suite.project_name),
                sandbox_mode=_parse_sandbox_mode(item.get("sandbox_mode"), default=suite.sandbox_mode),
                keep_sandbox=bool(item.get("keep_sandbox", suite.keep_sandbox)),
                allowed_paths=_as_list(item.get("allowed_paths")),
                expected_files=_as_list(item.get("expected_files")),
                expected_file_contains=_as_file_contains(item.get("expected_file_contains")),
                expected_imports=_as_list(item.get("expected_imports")),
                expected_symbols=_as_list(item.get("expected_symbols")),
                required_changed_files=_as_list(item.get("required_changed_files")),
                forbidden_changed_files=_as_list(item.get("forbidden_changed_files")),
                validation_commands=_as_list(item.get("validation_commands")),
                metric_targets=_as_metric_targets(item.get("metric_targets")),
                expected_output_contains=_as_list(item.get("expected_output_contains")),
                forbidden_output_contains=_as_list(item.get("forbidden_output_contains")),
                expected_status=_optional_string(item.get("expected_status")),
                require_tests_passed=bool(item.get("require_tests_passed", False)),
                auto_approve=bool(item.get("auto_approve", suite.auto_approve)),
                max_iterations=int(item.get("max_iterations", suite.max_iterations)),
                metadata=_extra_metadata(
                    item,
                    known_keys={
                        "name",
                        "description",
                        "mode",
                        "project_name",
                        "project",
                        "sandbox_mode",
                        "keep_sandbox",
                        "allowed_paths",
                        "expected_files",
                        "expected_file_contains",
                        "expected_imports",
                        "expected_symbols",
                        "required_changed_files",
                        "forbidden_changed_files",
                        "validation_commands",
                        "metric_targets",
                        "expected_output_contains",
                        "forbidden_output_contains",
                        "expected_status",
                        "require_tests_passed",
                        "auto_approve",
                        "max_iterations",
                    },
                ),
            )
        )

    return suite


def find_symbol_occurrences(project_root: str | Path, symbol_name: str) -> list[str]:
    root = Path(project_root).resolve()
    if not root.exists():
        return []

    symbol = symbol_name.strip()
    if not symbol:
        return []
    pattern = re.compile(rf"(?<!\w){re.escape(symbol)}(?!\w)")

    matches: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in CodebaseScanner.IGNORED_DIRS for part in relative_parts[:-1]):
            continue
        if path.suffix.lower() in CodebaseScanner.BINARY_EXTENSIONS:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(content):
            matches.append(str(path.relative_to(root)))
    return matches


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return [str(value).strip()]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _parse_sandbox_mode(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    cleaned = str(value).strip().lower()
    if not cleaned:
        return default
    if cleaned not in ALLOWED_SANDBOX_MODES:
        raise ValueError(f"Unsupported sandbox mode '{cleaned}'.")
    return cleaned


def _extra_metadata(raw: dict[str, Any], known_keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in known_keys}


def _as_file_contains(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("expected_file_contains must be a mapping of file paths to strings or string lists.")
    normalized: dict[str, list[str]] = {}
    for key, item in value.items():
        file_path = str(key).strip()
        if not file_path:
            continue
        normalized[file_path] = _as_list(item)
    return normalized


def _as_metric_targets(value: Any) -> dict[str, dict[str, float]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metric_targets must be a mapping of metric names to comparison objects.")

    normalized: dict[str, dict[str, float]] = {}
    alias_map = {
        "eq": "eq",
        "equals": "eq",
        "exact": "eq",
        "gte": "gte",
        "min": "gte",
        "minimum": "gte",
        "gt": "gt",
        "lte": "lte",
        "max": "lte",
        "maximum": "lte",
        "lt": "lt",
    }
    for key, raw_target in value.items():
        metric_name = str(key).strip()
        if not metric_name:
            continue
        if isinstance(raw_target, (int, float, bool, str)):
            normalized[metric_name] = {"eq": _metric_number(raw_target)}
            continue
        if not isinstance(raw_target, dict):
            raise ValueError(f"metric_targets.{metric_name} must be a scalar or mapping of comparison operators.")

        comparisons: dict[str, float] = {}
        for operator, raw_value in raw_target.items():
            normalized_operator = alias_map.get(str(operator).strip().lower())
            if normalized_operator is None:
                raise ValueError(f"Unsupported metric target operator '{operator}' for metric '{metric_name}'.")
            comparisons[normalized_operator] = _metric_number(raw_value)
        if not comparisons:
            raise ValueError(f"metric_targets.{metric_name} must define at least one comparison.")
        normalized[metric_name] = comparisons
    return normalized


def _metric_number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip()
    try:
        return float(cleaned)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Metric target values must be numeric, got '{value}'.") from exc
