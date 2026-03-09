from __future__ import annotations

from boss.workspace.workspace_state import WorkspaceStateStore


class TerminalListener:
    def __init__(self, workspace_state: WorkspaceStateStore) -> None:
        self.workspace_state = workspace_state

    def command_executed(
        self,
        project_name: str,
        *,
        command: str,
        result: dict[str, object],
        workdir: str,
    ) -> None:
        self.workspace_state.record_terminal_command(
            project_name,
            command=command,
            result=result,
            workdir=workdir,
        )
