from __future__ import annotations

from boss.workspace.workspace_state import WorkspaceStateStore


class EditorListener:
    def __init__(self, workspace_state: WorkspaceStateStore) -> None:
        self.workspace_state = workspace_state

    def file_opened(self, project_name: str, file_path: str) -> None:
        self.workspace_state.record_open_file(project_name, file_path)

    def file_changed(
        self,
        project_name: str,
        file_path: str,
        *,
        change_type: str = "edit",
        summary: str = "",
        diff_preview: str = "",
    ) -> None:
        self.workspace_state.record_edit(
            project_name,
            file_path,
            change_type=change_type,
            summary=summary,
            diff_preview=diff_preview,
        )

    def event(self, project_name: str, event_type: str, payload: dict[str, object]) -> None:
        self.workspace_state.record_event(project_name, event_type, payload)
