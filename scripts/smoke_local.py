#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE_URL = os.getenv("BOSS_BASE_URL", "http://127.0.0.1:8321")
RUN_CHAT = os.getenv("BOSS_SMOKE_CHAT", "0").strip().lower() in {"1", "true", "yes", "on"}
CHAT_MESSAGE = os.getenv("BOSS_SMOKE_CHAT_MESSAGE", "Reply with the single word ok.")


def main() -> int:
    status = get_json("/api/system/status")
    diagnostics = status.get("diagnostics") or {}
    git = status.get("git") or {}
    print(
        "PASS /api/system/status "
        f"provider_mode={status.get('provider_mode')} pid={status.get('process_id')} git={git.get('summary')}"
    )

    required_diagnostics = {
        "git_available",
        "git_summary",
        "pending_memory_count",
        "pending_jobs_count",
        "lock_consistent",
        "boss_control_healthy",
    }
    missing_diagnostics = sorted(required_diagnostics.difference(diagnostics.keys()))
    if missing_diagnostics:
        raise RuntimeError(f"/api/system/status diagnostics missing fields: {', '.join(missing_diagnostics)}")

    memory_stats = get_json("/api/memory/stats")
    print(
        "PASS /api/memory/stats "
        f"projects={memory_stats.get('projects')} files_indexed={memory_stats.get('files_indexed')}"
    )

    overview = get_json(
        "/api/memory/overview?" + urllib.parse.urlencode(
            {
                "session_id": f"smoke-memory-{int(time.time())}",
                "message": "Summarize the currently injected memory for smoke coverage.",
            }
        )
    )
    governance = overview.get("governance") or {}
    print(
        "PASS /api/memory/overview "
        f"pending={governance.get('pending_candidates')} pinned={governance.get('pinned_memories')}"
    )

    review = get_json("/api/review/capabilities")
    print(
        "PASS /api/review/capabilities "
        f"default_target={review.get('default_target')} git_available={review.get('git_available')}"
    )

    jobs = get_json("/api/jobs")
    if not isinstance(jobs, list):
        raise RuntimeError("/api/jobs did not return a list")
    print(f"PASS /api/jobs count={len(jobs)}")

    job_store_summary = smoke_background_job_store()
    print(f"PASS local background job store {job_store_summary}")

    if RUN_CHAT:
        final_text = run_chat_roundtrip(CHAT_MESSAGE)
        print(f"PASS /api/chat text={final_text!r}")
    else:
        print("SKIP /api/chat roundtrip (set BOSS_SMOKE_CHAT=1 to enable)")

    print("Boss local smoke passed.")
    return 0


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def smoke_background_job_store() -> str:
    from boss.config import settings
    from boss.jobs import (
        BackgroundJobStatus,
        append_background_job_log,
        create_background_job,
        load_background_job,
        tail_background_job_log,
        update_background_job,
    )

    root = Path(tempfile.mkdtemp(prefix="boss-smoke-jobs-"))
    original_jobs_dir = settings.jobs_dir
    original_job_logs_dir = settings.job_logs_dir
    try:
        object.__setattr__(settings, "jobs_dir", root / "jobs")
        object.__setattr__(settings, "job_logs_dir", root / "job-logs")

        job = create_background_job(
            prompt="Smoke background lifecycle",
            mode="agent",
            session_id="smoke-background-session",
            project_path=str(Path(__file__).resolve().parents[1]),
            initial_input_kind="prepared_input",
            initial_input_payload=[{"role": "user", "content": "Smoke background lifecycle"}],
            branch_mode="suggest",
            branch_name="boss/smoke-background-lifecycle",
            task_slug="smoke-background-lifecycle",
            branch_status="suggested",
            branch_message="Suggested task branch: boss/smoke-background-lifecycle",
        )
        update_background_job(
            job.job_id,
            status=BackgroundJobStatus.WAITING_PERMISSION.value,
            pending_run_id="smoke-pending-run",
        )
        append_background_job_log(
            job.job_id,
            event_type="permission_request",
            message="Waiting for permission: Run command",
        )
        loaded = load_background_job(job.job_id)
        if loaded is None:
            raise RuntimeError("Background job smoke could not reload the job record")
        tail = tail_background_job_log(job.job_id, limit=10)
        if "Waiting for permission" not in tail.get("text", ""):
            raise RuntimeError("Background job smoke log tail did not include the permission event")
        return f"status={loaded.status} branch={loaded.branch_name}"
    finally:
        object.__setattr__(settings, "jobs_dir", original_jobs_dir)
        object.__setattr__(settings, "job_logs_dir", original_job_logs_dir)


def run_chat_roundtrip(message: str) -> str:
    payload = json.dumps(
        {
            "message": message,
            "session_id": f"smoke-local-{int(time.time())}",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    text_fragments: list[str] = []
    error_message: str | None = None
    event_type: str | None = None
    data_line: str | None = None

    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"/api/chat returned HTTP {response.status}")

        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("event: "):
                event_type = line[7:]
                continue
            if line.startswith("data: "):
                data_line = line[6:]
                continue
            if line:
                continue

            if not data_line:
                event_type = None
                continue

            payload = json.loads(data_line)
            payload_type = payload.get("type") or event_type
            if payload_type == "text":
                content = payload.get("content")
                if isinstance(content, str):
                    text_fragments.append(content)
            elif payload_type == "error":
                error_message = str(payload.get("message") or "Unknown chat error")
            elif payload_type == "done":
                break

            event_type = None
            data_line = None

    if error_message:
        raise RuntimeError(error_message)

    final_text = "".join(text_fragments).strip()
    if not final_text:
        raise RuntimeError("/api/chat completed without any assistant text")
    return final_text


if __name__ == "__main__":
    raise SystemExit(main())