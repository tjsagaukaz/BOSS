from __future__ import annotations

from typing import Any


FAILURE_MAP_ORDER = [
    "context_collapse",
    "plan_drift",
    "tool_misuse",
    "file_system_damage",
    "silent_failure",
    "infinite_loops",
    "memory_poisoning",
    "model_mismatch",
]


def classify_failure_map(
    *,
    failure_category: str | None = None,
    failed_validation_names: set[str] | None = None,
    errors: list[str] | None = None,
    tool_errors: list[str] | None = None,
    changed_files: list[str] | None = None,
    tests_passed: bool | None = None,
    audit_passed: bool | None = None,
    iteration: int | None = None,
    max_iterations: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    failed_validation_names = failed_validation_names or set()
    errors = errors or []
    tool_errors = tool_errors or []
    changed_files = changed_files or []
    metadata = metadata or {}

    labels: list[str] = []
    text = "\n".join([*errors, *tool_errors]).lower()

    if failure_category == "context_missing" or failed_validation_names.intersection({"expected_files", "expected_symbols"}):
        labels.append("context_collapse")

    direct_contract = bool(metadata.get("direct_engineer"))
    if failure_category in {"bad_plan", "validation_failure"}:
        labels.append("plan_drift")
    if failed_validation_names.intersection({"required_changed_files", "forbidden_output_contains"}):
        labels.append("plan_drift")
    if direct_contract and not changed_files and failure_category not in {"tool_error", "model_error"}:
        labels.append("plan_drift")
    if "baseline" in text and "already captured" in text:
        labels.append("plan_drift")

    if failure_category == "tool_error" or tool_errors:
        labels.append("tool_misuse")

    if failure_category == "scope_violation" or failed_validation_names.intersection({"allowed_paths", "forbidden_changed_files"}):
        labels.append("file_system_damage")

    if failure_category == "test_failure":
        labels.append("silent_failure")
    if failed_validation_names.intersection({"require_tests_passed", "validation_command", "expected_output_contains"}):
        labels.append("silent_failure")
    if tests_passed is False or audit_passed is False:
        labels.append("silent_failure")

    if max_iterations and iteration and iteration >= max_iterations:
        labels.append("infinite_loops")
    if "exceeded max iterations" in text or "stop requested" in text or "timed out" in text:
        labels.append("infinite_loops")

    if "solution library" in text or "unverified solution" in text or "memory entry" in text:
        labels.append("memory_poisoning")

    if failure_category == "model_error" or "model_not_found" in text or "does not have access" in text:
        labels.append("model_mismatch")

    ordered = [name for name in FAILURE_MAP_ORDER if name in labels]
    return ordered
