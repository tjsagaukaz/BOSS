from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from boss.types import AgentWorkerResult, AgentWorkerStatus, SwarmTask, utc_now_iso


class AgentWorker:
    def __init__(
        self,
        agent_name: str,
        role: str,
        handler: Callable[[SwarmTask, Callable[[str, float | None], None]], dict[str, Any]],
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.role = role
        self.handler = handler
        self.event_callback = event_callback
        self._lock = threading.Lock()
        self._status = AgentWorkerStatus(agent_name=agent_name, role=role)

    def run(self, task: SwarmTask) -> AgentWorkerResult:
        self._set_status(
            status="running",
            current_task=task.title,
            current_run_id=task.run_id,
            progress=0.05,
            last_message=f"Starting {task.title}",
        )
        try:
            payload = self.handler(task, self._progress_callback(task))
            result = AgentWorkerResult(
                task_id=task.task_id,
                run_id=task.run_id,
                agent_type=task.agent_type,
                status=str(payload.get("status", "completed")),
                output=str(payload.get("output", "")),
                metadata=dict(payload.get("metadata", {})),
                error=str(payload.get("error", "")),
            )
            self._set_status(
                status="idle",
                current_task="",
                current_run_id=None,
                progress=1.0,
                last_message=f"Finished {task.title}",
            )
            return result
        except Exception as exc:
            self._set_status(
                status="idle",
                current_task="",
                current_run_id=None,
                progress=0.0,
                last_message=f"Failed {task.title}: {exc}",
            )
            return AgentWorkerResult(
                task_id=task.task_id,
                run_id=task.run_id,
                agent_type=task.agent_type,
                status="failed",
                output="",
                metadata={},
                error=str(exc),
            )

    def snapshot(self) -> AgentWorkerStatus:
        with self._lock:
            return AgentWorkerStatus(
                agent_name=self._status.agent_name,
                role=self._status.role,
                status=self._status.status,
                current_task=self._status.current_task,
                current_run_id=self._status.current_run_id,
                progress=self._status.progress,
                last_message=self._status.last_message,
                updated_at=self._status.updated_at,
            )

    def _progress_callback(self, task: SwarmTask) -> Callable[[str, float | None], None]:
        def callback(message: str, progress: float | None = None) -> None:
            self._set_status(
                status="running",
                current_task=task.title,
                current_run_id=task.run_id,
                progress=progress if progress is not None else self._status.progress,
                last_message=message,
            )

        return callback

    def _set_status(
        self,
        status: str,
        current_task: str,
        current_run_id: str | None,
        progress: float,
        last_message: str,
    ) -> None:
        with self._lock:
            self._status.status = status
            self._status.current_task = current_task
            self._status.current_run_id = current_run_id
            self._status.progress = progress
            self._status.last_message = last_message
            self._status.updated_at = utc_now_iso()
            updated_at = self._status.updated_at
        if self.event_callback is not None:
            self.event_callback(
                "agent_status",
                {
                    "agent_name": self.agent_name,
                    "role": self.role,
                    "status": status,
                    "current_task": current_task,
                    "current_run_id": current_run_id,
                    "progress": progress,
                    "last_message": last_message,
                    "updated_at": updated_at,
                },
            )
