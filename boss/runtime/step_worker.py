from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

from boss.orchestrator import BOSSOrchestrator
from boss.types import ToolExecutionRecord


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        _emit({"status": "error", "error": f"Invalid step payload: {exc}"})
        return 1

    action = str(payload.get("action", "")).strip()
    if action != "engineer_step":
        _emit({"status": "error", "error": f"Unsupported step action: {action}"})
        return 1

    try:
        result = _run_engineer_step(payload)
    except Exception as exc:  # pragma: no cover - defensive
        _emit(
            {
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        return 1

    _emit(result)
    return 0


def _run_engineer_step(payload: dict[str, Any]) -> dict[str, Any]:
    root_dir = Path(str(payload["root_dir"])).resolve()
    project_name = str(payload["project_name"])
    task = str(payload["task"])
    plan_text = str(payload["plan_text"])
    auto_approve = bool(payload.get("auto_approve", False))
    request_options = payload.get("request_options", {}) or {}
    orchestrator = BOSSOrchestrator(root_dir)
    project_context = orchestrator.project_loader.load_project(
        project_name,
        task_hint=str(payload.get("task_hint") or task),
        auto_index=bool(payload.get("auto_index", False)),
    )
    toolbox = orchestrator.autonomous_loop._toolbox(project_context, auto_approve=auto_approve)
    engineer_tools = toolbox.build_tool_definitions(
        allow_write=bool(payload.get("allow_write", True)),
        allow_terminal=bool(payload.get("allow_terminal", True)),
        allow_commit=False,
        allow_tests=bool(payload.get("allow_tests", True)),
    )
    result = orchestrator.engineer.implement(
        task=task,
        project_context=project_context,
        plan_text=plan_text,
        tools=engineer_tools,
        audit_feedback=str(payload.get("audit_feedback", "")),
        request_options=request_options,
        task_contract=payload.get("task_contract"),
        execution_rules=payload.get("execution_rules"),
        execution_spine=payload.get("execution_spine"),
    )
    return {
        "status": "completed",
        "result": {
            "agent_name": result.agent_name,
            "provider": result.provider,
            "model": result.model,
            "text": result.text,
            "duration_seconds": result.duration_seconds,
            "usage": result.usage,
            "estimated_cost_usd": result.estimated_cost_usd,
            "tool_records": [_tool_record_payload(item) for item in result.tool_records],
        },
    }


def _tool_record_payload(record: ToolExecutionRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "arguments": record.arguments,
        "success": record.success,
        "result": record.result,
        "error": record.error,
        "started_at": record.started_at,
    }


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
