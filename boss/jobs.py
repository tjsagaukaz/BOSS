from __future__ import annotations

import json
import re
import subprocess
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from boss.config import settings
from boss.control import default_workspace_root


_LOG_TAIL_LINE_LIMIT = 200
_MAX_PREVIEW_CHARS = 12_000
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "taken_over"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip_text(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3].rstrip() + "..."


class BackgroundJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    TAKEN_OVER = "taken_over"


@dataclass
class BackgroundJobRecord:
    job_id: str
    prompt: str
    mode: str
    session_id: str
    project_path: str | None
    status: str
    created_at: str
    updated_at: str
    log_path: str
    title: str
    started_at: str | None = None
    finished_at: str | None = None
    last_event_at: str | None = None
    latest_event: str | None = None
    error_message: str | None = None
    pending_run_id: str | None = None
    cancellation_requested_at: str | None = None
    resume_count: int = 0
    session_persisted: bool = False
    assistant_preview: str = ""
    initial_input_kind: str = "prepared_input"
    initial_input_payload: Any = field(default_factory=list)
    branch_mode: str | None = None
    branch_name: str | None = None
    task_slug: str | None = None
    branch_status: str | None = None
    branch_message: str | None = None
    branch_helper_path: str | None = None
    task_workspace_path: str | None = None
    execution_style: str | None = None
    loop_budget: dict | None = None
    loop_id: str | None = None


def ensure_background_job_dirs() -> tuple[Path, Path]:
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.job_logs_dir.mkdir(parents=True, exist_ok=True)
    return settings.jobs_dir, settings.job_logs_dir


def background_job_path(job_id: str) -> Path:
    jobs_dir, _logs_dir = ensure_background_job_dirs()
    return jobs_dir / f"{job_id}.json"


def background_job_log_path(job_id: str) -> Path:
    _jobs_dir, logs_dir = ensure_background_job_dirs()
    return logs_dir / f"{job_id}.jsonl"


def create_background_job(
    *,
    prompt: str,
    mode: str,
    session_id: str,
    project_path: str | None,
    initial_input_kind: str,
    initial_input_payload: Any,
    branch_mode: str | None = None,
    branch_name: str | None = None,
    task_slug: str | None = None,
    branch_status: str | None = None,
    branch_message: str | None = None,
    branch_helper_path: str | None = None,
    execution_style: str | None = None,
    loop_budget: dict | None = None,
    loop_id: str | None = None,
) -> BackgroundJobRecord:
    job_id = uuid.uuid4().hex
    log_path = background_job_log_path(job_id)
    created_at = _utcnow()
    record = BackgroundJobRecord(
        job_id=job_id,
        prompt=prompt,
        mode=mode,
        session_id=session_id,
        project_path=project_path,
        status=BackgroundJobStatus.QUEUED.value,
        created_at=created_at,
        updated_at=created_at,
        log_path=str(log_path),
        title=_job_title(prompt),
        initial_input_kind=initial_input_kind,
        initial_input_payload=initial_input_payload,
        branch_mode=branch_mode,
        branch_name=branch_name,
        task_slug=task_slug,
        branch_status=branch_status,
        branch_message=branch_message,
        branch_helper_path=branch_helper_path,
        execution_style=execution_style,
        loop_budget=loop_budget,
        loop_id=loop_id,
    )
    save_background_job(record)
    append_background_job_log(
        job_id,
        event_type="job_created",
        message=f"Queued background job in {mode} mode.",
        payload={
            "project_path": project_path,
            "session_id": session_id,
            "mode": mode,
        },
    )
    if branch_message:
        append_background_job_log(
            job_id,
            event_type="branch",
            message=branch_message,
            payload={
                "branch_mode": branch_mode,
                "branch_name": branch_name,
                "branch_status": branch_status,
                "helper_path": branch_helper_path,
            },
        )
    return record


def save_background_job(record: BackgroundJobRecord) -> Path:
    path = background_job_path(record.job_id)
    payload = asdict(record)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)
    return path


def load_background_job(job_id: str) -> BackgroundJobRecord | None:
    path = background_job_path(job_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _background_job_from_payload(payload)


def list_background_jobs(limit: int = 100) -> list[BackgroundJobRecord]:
    jobs_dir, _logs_dir = ensure_background_job_dirs()
    records: list[BackgroundJobRecord] = []
    for path in sorted(jobs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        record = _background_job_from_payload(payload)
        if record is None:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return records


def update_background_job(job_id: str, **changes: Any) -> BackgroundJobRecord:
    record = load_background_job(job_id)
    if record is None:
        raise FileNotFoundError(f"Background job {job_id} not found")
    updated = replace(record, **changes, updated_at=_utcnow())
    save_background_job(updated)
    return updated


def append_background_job_log(
    job_id: str,
    *,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = background_job_log_path(job_id)
    entry = {
        "timestamp": _utcnow(),
        "type": event_type,
        "message": message,
        "payload": payload or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def tail_background_job_log(job_id: str, limit: int = _LOG_TAIL_LINE_LIMIT) -> dict[str, Any]:
    path = background_job_log_path(job_id)
    if not path.exists():
        return {"job_id": job_id, "log_path": str(path), "entries": [], "text": "", "truncated": False}

    buffer: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    total = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            total += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                buffer.append(payload)

    entries = list(buffer)
    rendered = "\n".join(_render_log_entry(entry) for entry in entries)
    return {
        "job_id": job_id,
        "log_path": str(path),
        "entries": entries,
        "text": rendered,
        "truncated": total > len(entries),
    }


def recover_interrupted_background_jobs() -> int:
    recovered = 0
    for record in list_background_jobs(limit=500):
        if record.status not in {
            BackgroundJobStatus.QUEUED.value,
            BackgroundJobStatus.RUNNING.value,
        }:
            continue
        updated = update_background_job(
            record.job_id,
            status=BackgroundJobStatus.INTERRUPTED.value,
            error_message="Boss restarted before this background job finished. Resume to continue.",
            latest_event="Background job interrupted by local restart.",
        )
        append_background_job_log(
            updated.job_id,
            event_type="recovered",
            message="Marked as interrupted after local Boss restart.",
        )
        recovered += 1
    return recovered


def is_background_job_terminal(status: str) -> bool:
    return status in _TERMINAL_STATUSES


def summarize_background_job(record: BackgroundJobRecord) -> dict[str, Any]:
    return {
        "job_id": record.job_id,
        "title": record.title,
        "prompt": record.prompt,
        "mode": record.mode,
        "session_id": record.session_id,
        "project_path": record.project_path,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "last_event_at": record.last_event_at,
        "latest_event": record.latest_event,
        "log_path": record.log_path,
        "error_message": record.error_message,
        "pending_run_id": record.pending_run_id,
        "resume_count": record.resume_count,
        "session_persisted": record.session_persisted,
        "assistant_preview": record.assistant_preview,
        "branch_mode": record.branch_mode,
        "branch_name": record.branch_name,
        "task_slug": record.task_slug,
        "branch_status": record.branch_status,
        "branch_message": record.branch_message,
        "branch_helper_path": record.branch_helper_path,
    }


def prepare_task_branch(*, prompt: str, project_path: str | None, branch_mode: str) -> dict[str, str | None]:
    normalized_mode = (branch_mode or "off").strip().lower()
    if normalized_mode not in {"off", "suggest", "create"}:
        normalized_mode = "off"

    workspace = Path(project_path).expanduser() if project_path else default_workspace_root()
    repo_root = _git_repo_root(workspace)
    if repo_root is None:
        return {
            "branch_mode": normalized_mode,
            "branch_name": None,
            "task_slug": None,
            "branch_status": "not_git",
            "branch_message": "No git repository was detected for this background job.",
            "branch_helper_path": None,
        }

    task_slug = _task_slug(prompt)
    branch_name = f"boss/{task_slug}"
    helper_path = repo_root / "scripts" / "task_branch.sh"

    if normalized_mode == "off":
        return {
            "branch_mode": normalized_mode,
            "branch_name": branch_name,
            "task_slug": task_slug,
            "branch_status": "not_requested",
            "branch_message": f"Task branch suggestion available: {branch_name}",
            "branch_helper_path": str(helper_path) if helper_path.exists() else None,
        }

    if normalized_mode == "suggest":
        return {
            "branch_mode": normalized_mode,
            "branch_name": branch_name,
            "task_slug": task_slug,
            "branch_status": "suggested",
            "branch_message": f"Suggested task branch: {branch_name}",
            "branch_helper_path": str(helper_path) if helper_path.exists() else None,
        }

    if not helper_path.exists():
        return {
            "branch_mode": normalized_mode,
            "branch_name": branch_name,
            "task_slug": task_slug,
            "branch_status": "helper_missing",
            "branch_message": f"Task branch helper is missing. Suggested branch: {branch_name}",
            "branch_helper_path": None,
        }

    try:
        result = subprocess.run(
            [str(helper_path), task_slug],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {
            "branch_mode": normalized_mode,
            "branch_name": branch_name,
            "task_slug": task_slug,
            "branch_status": "create_failed",
            "branch_message": f"Task branch helper failed locally: {exc}",
            "branch_helper_path": str(helper_path),
        }

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return {
            "branch_mode": normalized_mode,
            "branch_name": branch_name,
            "task_slug": task_slug,
            "branch_status": "create_failed",
            "branch_message": output or f"Task branch helper failed for {branch_name}",
            "branch_helper_path": str(helper_path),
        }

    return {
        "branch_mode": normalized_mode,
        "branch_name": branch_name,
        "task_slug": task_slug,
        "branch_status": "created",
        "branch_message": output or f"Created or switched to {branch_name}",
        "branch_helper_path": str(helper_path),
    }


def _background_job_from_payload(payload: dict[str, Any]) -> BackgroundJobRecord | None:
    try:
        return BackgroundJobRecord(
            job_id=str(payload["job_id"]),
            prompt=str(payload.get("prompt", "")),
            mode=str(payload.get("mode", "agent")),
            session_id=str(payload.get("session_id", "")),
            project_path=str(payload["project_path"]) if payload.get("project_path") else None,
            status=str(payload.get("status", BackgroundJobStatus.QUEUED.value)),
            created_at=str(payload.get("created_at", _utcnow())),
            updated_at=str(payload.get("updated_at", _utcnow())),
            log_path=str(payload.get("log_path", background_job_log_path(str(payload["job_id"])))),
            title=str(payload.get("title", _job_title(str(payload.get("prompt", ""))))),
            started_at=_optional_string(payload.get("started_at")),
            finished_at=_optional_string(payload.get("finished_at")),
            last_event_at=_optional_string(payload.get("last_event_at")),
            latest_event=_optional_string(payload.get("latest_event")),
            error_message=_optional_string(payload.get("error_message")),
            pending_run_id=_optional_string(payload.get("pending_run_id")),
            cancellation_requested_at=_optional_string(payload.get("cancellation_requested_at")),
            resume_count=int(payload.get("resume_count", 0) or 0),
            session_persisted=bool(payload.get("session_persisted", False)),
            assistant_preview=str(payload.get("assistant_preview", "")),
            initial_input_kind=str(payload.get("initial_input_kind", "prepared_input")),
            initial_input_payload=payload.get("initial_input_payload", []),
            branch_mode=_optional_string(payload.get("branch_mode")),
            branch_name=_optional_string(payload.get("branch_name")),
            task_slug=_optional_string(payload.get("task_slug")),
            branch_status=_optional_string(payload.get("branch_status")),
            branch_message=_optional_string(payload.get("branch_message")),
            branch_helper_path=_optional_string(payload.get("branch_helper_path")),
            task_workspace_path=_optional_string(payload.get("task_workspace_path")),
            execution_style=_optional_string(payload.get("execution_style")),
            loop_budget=payload.get("loop_budget"),
            loop_id=_optional_string(payload.get("loop_id")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _job_title(prompt: str) -> str:
    clipped = _clip_text(prompt, 72)
    return clipped or "Background task"


def _task_slug(prompt: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", prompt.lower())
    filtered = [word for word in words if word not in {"the", "and", "for", "with", "that", "this", "into"}]
    slug_source = "-".join(filtered[:8]) or "background-task"
    slug = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-")
    if not slug:
        slug = "background-task"
    return slug[:48].rstrip("-")


def _git_repo_root(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    root = (result.stdout or "").strip()
    return Path(root).resolve(strict=False) if root else None


def _render_log_entry(entry: dict[str, Any]) -> str:
    timestamp = str(entry.get("timestamp", ""))
    event_type = str(entry.get("type", "event"))
    message = str(entry.get("message", "")).strip()
    prefix = event_type.replace("_", " ").upper()
    return f"[{timestamp}] {prefix}: {message}" if message else f"[{timestamp}] {prefix}"