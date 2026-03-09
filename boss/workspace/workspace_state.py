from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from boss.types import WorkspaceState, utc_now_iso


class WorkspaceStateStore:
    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def set_active_project(self, project_name: str | None) -> None:
        self._state["active_project"] = project_name
        self._save()

    def active_project(self) -> str | None:
        value = self._state.get("active_project")
        return str(value) if value else None

    def snapshot(self, project_name: str) -> WorkspaceState:
        state = self.get_project_state(project_name)
        return WorkspaceState(
            active_project=project_name,
            open_files=list(state.get("open_files", [])[:10]),
            recent_edits=list(state.get("recent_edits", [])[:10]),
            recent_events=list(state.get("recent_events", [])[:20]),
            recent_terminal_commands=list(state.get("recent_terminal_commands", [])[:10]),
            last_terminal_command=str(state.get("last_terminal_command", "")),
            last_terminal_result=dict(state.get("last_terminal_result", {})),
            last_test_results=dict(state.get("last_test_results", {})),
            last_git_diff=str(state.get("last_git_diff", "")),
            last_git_status=dict(state.get("last_git_status", {})),
            last_commit=dict(state.get("last_commit", {})),
            last_editor_event=dict(state.get("last_editor_event", {})),
            updated_at=str(state.get("updated_at", "")),
        )

    def get_project_state(self, project_name: str) -> dict[str, Any]:
        projects = self._state.setdefault("projects", {})
        return projects.setdefault(
            project_name,
            {
                "open_files": [],
                "recent_edits": [],
                "recent_events": [],
                "recent_terminal_commands": [],
                "last_terminal_command": "",
                "last_terminal_result": {},
                "last_test_results": {},
                "last_git_diff": "",
                "last_git_status": {},
                "last_commit": {},
                "last_editor_event": {},
                "updated_at": "",
            },
        )

    def record_open_file(self, project_name: str, file_path: str) -> None:
        state = self.get_project_state(project_name)
        self._push_unique(state["open_files"], file_path, max_items=20)
        event = {
            "type": "file_opened",
            "file": file_path,
            "timestamp": utc_now_iso(),
        }
        state["last_editor_event"] = event
        self._append_event(state, event)
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_file_closed(self, project_name: str, file_path: str) -> None:
        state = self.get_project_state(project_name)
        state["open_files"] = [item for item in state.get("open_files", []) if item != file_path]
        event = {
            "type": "file_closed",
            "file": file_path,
            "timestamp": utc_now_iso(),
        }
        state["last_editor_event"] = event
        self._append_event(state, event)
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_edit(
        self,
        project_name: str,
        file_path: str,
        *,
        change_type: str,
        summary: str = "",
        diff_preview: str = "",
    ) -> None:
        state = self.get_project_state(project_name)
        self._push_unique(state["open_files"], file_path, max_items=20)
        payload = {
            "file": file_path,
            "type": change_type,
            "summary": summary[:300],
            "diff_preview": diff_preview[:1200],
            "timestamp": utc_now_iso(),
        }
        state["recent_edits"] = [payload] + [item for item in state["recent_edits"] if item.get("file") != file_path]
        state["recent_edits"] = state["recent_edits"][:20]
        state["last_editor_event"] = payload
        self._append_event(
            state,
            {
                "type": "file_changed",
                "file": file_path,
                "change_type": change_type,
                "summary": summary[:300],
                "timestamp": payload["timestamp"],
            },
        )
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_terminal_command(
        self,
        project_name: str,
        *,
        command: str,
        result: dict[str, Any],
        workdir: str,
    ) -> None:
        state = self.get_project_state(project_name)
        payload = {
            "command": command,
            "workdir": workdir,
            "exit_code": result.get("exit_code"),
            "timestamp": utc_now_iso(),
            "stdout": str(result.get("stdout", ""))[:400],
            "stderr": str(result.get("stderr", ""))[:400],
        }
        state["last_terminal_command"] = command
        state["last_terminal_result"] = payload
        state["recent_terminal_commands"] = [
            payload,
            *[item for item in state["recent_terminal_commands"] if item.get("command") != command],
        ][:20]
        self._append_event(
            state,
            {
                "type": "terminal_command",
                "command": command,
                "workdir": workdir,
                "exit_code": result.get("exit_code"),
                "timestamp": payload["timestamp"],
            },
        )
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_test_result(self, project_name: str, result: dict[str, Any]) -> None:
        state = self.get_project_state(project_name)
        state["last_test_results"] = dict(result)
        self._append_event(
            state,
            {
                "type": "test_results",
                "passed": result.get("passed"),
                "failed_tests": list(result.get("failed_tests", []))[:10],
                "timestamp": utc_now_iso(),
            },
        )
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_git_state(
        self,
        project_name: str,
        *,
        diff_text: str | None = None,
        status: dict[str, Any] | None = None,
    ) -> None:
        state = self.get_project_state(project_name)
        if diff_text is not None:
            state["last_git_diff"] = diff_text[:2000]
        if status is not None:
            state["last_git_status"] = status
            self._append_event(
                state,
                {
                    "type": "git_status",
                    "summary": status.get("summary"),
                    "dirty": status.get("dirty"),
                    "timestamp": utc_now_iso(),
                },
            )
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_commit(self, project_name: str, message: str, result: dict[str, Any]) -> None:
        state = self.get_project_state(project_name)
        payload = {
            "message": message,
            "committed": bool(result.get("committed", False)),
            "commit": result.get("commit"),
            "status_message": result.get("message"),
            "timestamp": utc_now_iso(),
        }
        state["last_commit"] = payload
        self._append_event(
            state,
            {
                "type": "git_commit",
                "message": message,
                "committed": payload["committed"],
                "commit": payload["commit"],
                "timestamp": payload["timestamp"],
            },
        )
        state["updated_at"] = utc_now_iso()
        self._save()

    def record_event(self, project_name: str, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "file_opened":
            path = str(payload.get("path", ""))
            if path:
                self.record_open_file(project_name, path)
            return
        if event_type == "file_closed":
            path = str(payload.get("path", ""))
            if path:
                self.record_file_closed(project_name, path)
            return
        if event_type in {"file_changed", "file_saved"}:
            path = str(payload.get("path", ""))
            if path:
                self.record_edit(
                    project_name,
                    path,
                    change_type=str(payload.get("change_type", event_type)),
                    summary=str(payload.get("summary", "")),
                    diff_preview=str(payload.get("diff_preview", "")),
                )
            return
        if event_type == "terminal_command":
            command = str(payload.get("command", "")).strip()
            if command:
                self.record_terminal_command(
                    project_name,
                    command=command,
                    result={
                        "command": command,
                        "exit_code": payload.get("exit_code"),
                        "stdout": str(payload.get("stdout", "")),
                        "stderr": str(payload.get("stderr", "")),
                    },
                    workdir=str(payload.get("workdir", "")),
                )
            return
        if event_type == "test_results":
            self.record_test_result(project_name, dict(payload))
            return
        if event_type == "workspace_changed":
            event = {
                "type": "workspace_changed",
                "workspace_folders": list(payload.get("workspace_folders", []))
                if isinstance(payload.get("workspace_folders"), list)
                else [],
                "timestamp": utc_now_iso(),
            }
            state = self.get_project_state(project_name)
            state["last_editor_event"] = event
            self._append_event(state, event)
            state["updated_at"] = utc_now_iso()
            self._save()
            return
        state = self.get_project_state(project_name)
        event = {
            "type": event_type,
            "payload": payload,
            "timestamp": utc_now_iso(),
        }
        state["last_editor_event"] = event
        self._append_event(state, event)
        state["updated_at"] = utc_now_iso()
        self._save()

    def _push_unique(self, items: list[str], value: str, max_items: int) -> None:
        deduped = [item for item in items if item != value]
        items[:] = [value] + deduped[: max_items - 1]

    def _append_event(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        state["recent_events"] = [event] + list(state.get("recent_events", []))
        state["recent_events"] = state["recent_events"][:50]

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"active_project": None, "projects": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"active_project": None, "projects": {}}

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
