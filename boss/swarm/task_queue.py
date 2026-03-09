from __future__ import annotations

import heapq
import itertools
import threading
from typing import Any

from boss.types import SwarmTask, utc_now_iso


class SwarmTaskQueue:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sequence = itertools.count(1)
        self._task_ids = itertools.count(1)
        self._pending: list[tuple[int, int, int]] = []
        self._tasks: dict[int, SwarmTask] = {}

    def enqueue(
        self,
        run_id: str,
        agent_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        priority: int = 100,
        max_retries: int = 1,
        depends_on: list[int] | None = None,
    ) -> SwarmTask:
        with self._lock:
            task = SwarmTask(
                task_id=next(self._task_ids),
                run_id=run_id,
                agent_type=agent_type,
                title=title,
                priority=priority,
                payload=dict(payload or {}),
                max_retries=max_retries,
                depends_on=list(depends_on or []),
            )
            self._tasks[task.task_id] = task
            heapq.heappush(self._pending, (task.priority, next(self._sequence), task.task_id))
            return task

    def dequeue(self, run_id: str | None = None, agent_type: str | None = None) -> SwarmTask | None:
        with self._lock:
            skipped: list[tuple[int, int, int]] = []
            selected: SwarmTask | None = None
            while self._pending:
                item = heapq.heappop(self._pending)
                task = self._tasks.get(item[2])
                if task is None:
                    continue
                if task.status != "pending":
                    continue
                if run_id is not None and task.run_id != run_id:
                    skipped.append(item)
                    continue
                if agent_type is not None and task.agent_type != agent_type:
                    skipped.append(item)
                    continue
                if task.depends_on and not self._dependencies_complete(task.depends_on):
                    skipped.append(item)
                    continue
                task.status = "running"
                task.updated_at = utc_now_iso()
                selected = task
                break
            for item in skipped:
                heapq.heappush(self._pending, item)
            return selected

    def complete(self, task_id: int, result: dict[str, Any] | None = None) -> SwarmTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task.status = "completed"
            task.result = dict(result or {})
            task.error = ""
            task.updated_at = utc_now_iso()
            return task

    def fail(self, task_id: int, error: str, result: dict[str, Any] | None = None) -> SwarmTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task.status = "failed"
            task.error = error
            task.result = dict(result or {})
            task.updated_at = utc_now_iso()
            return task

    def retry(self, task_id: int, error: str = "") -> SwarmTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.retries >= task.max_retries:
                task.status = "failed"
                task.error = error or task.error
                task.updated_at = utc_now_iso()
                return task
            task.retries += 1
            task.status = "pending"
            task.error = error
            task.updated_at = utc_now_iso()
            heapq.heappush(self._pending, (task.priority, next(self._sequence), task.task_id))
            return task

    def cancel(self, task_id: int) -> SwarmTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status == "completed":
                return task
            task.status = "cancelled"
            task.updated_at = utc_now_iso()
            return task

    def pause(self, task_id: int) -> SwarmTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status != "pending":
                return task
            task.status = "paused"
            task.updated_at = utc_now_iso()
            return task

    def resume(self, task_id: int) -> SwarmTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status != "paused":
                return task
            task.status = "pending"
            task.updated_at = utc_now_iso()
            heapq.heappush(self._pending, (task.priority, next(self._sequence), task.task_id))
            return task

    def get(self, task_id: int) -> SwarmTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(
        self,
        run_id: str | None = None,
        status: str | None = None,
    ) -> list[SwarmTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda item: (item.priority, item.created_at, item.task_id))
        if run_id is not None:
            tasks = [task for task in tasks if task.run_id == run_id]
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return tasks

    def cancel_run(self, run_id: str) -> list[SwarmTask]:
        updated: list[SwarmTask] = []
        for task in self.list_tasks(run_id=run_id):
            task_state = self.cancel(task.task_id)
            if task_state is not None:
                updated.append(task_state)
        return updated

    def pause_run(self, run_id: str) -> list[SwarmTask]:
        updated: list[SwarmTask] = []
        for task in self.list_tasks(run_id=run_id, status="pending"):
            task_state = self.pause(task.task_id)
            if task_state is not None:
                updated.append(task_state)
        return updated

    def resume_run(self, run_id: str) -> list[SwarmTask]:
        updated: list[SwarmTask] = []
        for task in self.list_tasks(run_id=run_id, status="paused"):
            task_state = self.resume(task.task_id)
            if task_state is not None:
                updated.append(task_state)
        return updated

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.list_tasks():
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts

    def _dependencies_complete(self, depends_on: list[int]) -> bool:
        for task_id in depends_on:
            task = self._tasks.get(task_id)
            if task is None or task.status != "completed":
                return False
        return True
