from __future__ import annotations

from boss.workspace import EditorListener, TerminalListener, TestListener as WorkspaceTestListener, WorkspaceStateStore


def test_workspace_state_records_editor_and_terminal_signals(tmp_path):
    store = WorkspaceStateStore(tmp_path / "workspace.json")
    editor = EditorListener(store)
    terminal = TerminalListener(store)

    store.set_active_project("legion")
    editor.file_opened("legion", "auth.py")
    editor.file_changed("legion", "auth.py", change_type="write_file", summary="Updated auth", diff_preview="+ token")
    terminal.command_executed(
        "legion",
        command="pytest",
        workdir="/tmp/legion",
        result={"command": "pytest", "exit_code": 1, "stdout": "", "stderr": "failed"},
    )

    snapshot = store.snapshot("legion")
    assert snapshot.active_project == "legion"
    assert snapshot.open_files[0] == "auth.py"
    assert snapshot.recent_edits[0]["file"] == "auth.py"
    assert snapshot.last_terminal_command == "pytest"
    assert snapshot.recent_events[0]["type"] == "terminal_command"


def test_workspace_state_tracks_failed_tests(tmp_path):
    store = WorkspaceStateStore(tmp_path / "workspace.json")
    listener = WorkspaceTestListener(store)

    listener.tests_ran(
        "legion",
        {
            "found": True,
            "passed": False,
            "message": "One or more test commands failed.",
            "results": [
                {
                    "command": "pytest",
                    "exit_code": 1,
                    "stdout": "FAILED tests/test_auth.py::test_rate_limit - AssertionError",
                    "stderr": "",
                }
            ],
        },
    )

    snapshot = store.snapshot("legion")
    assert snapshot.last_test_results["passed"] is False
    assert snapshot.last_test_results["failed_tests"][0] == "tests/test_auth.py::test_rate_limit"


def test_workspace_state_handles_file_closed_and_workspace_changed(tmp_path):
    store = WorkspaceStateStore(tmp_path / "workspace.json")
    store.record_open_file("legion", "auth.py")
    store.record_event("legion", "file_closed", {"path": "auth.py"})
    store.record_event("legion", "workspace_changed", {"workspace_folders": ["legion", "myfiltr"]})

    snapshot = store.snapshot("legion")
    assert snapshot.open_files == []
    assert snapshot.recent_events[0]["type"] == "workspace_changed"
