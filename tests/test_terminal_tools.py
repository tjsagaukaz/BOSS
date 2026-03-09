from __future__ import annotations

import sys

from boss.tools.file_tools import FileTools
from boss.tools.terminal_tools import TerminalTools


def test_detect_test_commands_returns_working_pytest_command(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    tools = TerminalTools(root=tmp_path)
    commands = tools._detect_test_commands(tmp_path)

    assert len(commands) == 1
    assert "pytest" in commands[0]


def test_run_tests_executes_detected_python_test_command(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    tools = TerminalTools(root=tmp_path)
    result = tools.run_tests()

    assert result["found"] is True
    assert result["passed"] is True
    assert "pytest" in result["commands"][0]


def test_run_terminal_allows_absolute_python_executable(tmp_path):
    tools = TerminalTools(root=tmp_path)

    result = tools.run_terminal(f"{sys.executable} -c \"print('ok')\"")

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "ok"


def test_file_tools_full_access_allows_absolute_write_outside_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    tools = FileTools(root=root, full_access=True, require_confirmation=False)
    target = outside / "note.txt"
    result = tools.write_file(str(target), "ok", overwrite=True)

    assert result["path"] == str(target.resolve())
    assert target.read_text(encoding="utf-8") == "ok"


def test_run_terminal_full_access_allows_arbitrary_shell_and_absolute_workdir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    tools = TerminalTools(root=root, full_access=True)
    result = tools.run_terminal(
        "mkdir -p nested && printf 'ok' > nested/out.txt",
        workdir=str(outside),
    )

    assert result["exit_code"] == 0
    assert (outside / "nested" / "out.txt").read_text(encoding="utf-8") == "ok"
