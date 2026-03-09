from __future__ import annotations

from pathlib import Path
from typing import Callable

from boss.context.editor_state import EditorStateStore
from boss.ide.vscode_controller import VSCodeController
from boss.memory.embeddings import EmbeddingService
from boss.plugins.plugin_manager import PluginManager
from boss.tools.code_search import CodeSearch
from boss.tools.editor_tools import EditorTools
from boss.tools.file_tools import FileTools
from boss.tools.git_tools import GitTools
from boss.tools.terminal_tools import TerminalTools
from boss.tools.tool_registry import ToolRegistry
from boss.types import ToolDefinition, WriteConfirmationHandler


class Toolbox:
    def __init__(
        self,
        workspace_root: str | Path,
        project_root: str | Path,
        project_name: str,
        embeddings: EmbeddingService,
        editor_state: EditorStateStore,
        vscode_controller: VSCodeController,
        plugin_manager: PluginManager,
        full_access: bool = False,
        require_confirmation: bool = True,
        confirm_overwrite: Callable[[Path], bool] | None = None,
        confirm_write: WriteConfirmationHandler | None = None,
        editor_listener=None,
        terminal_listener=None,
        git_listener=None,
        test_listener=None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.project_root = Path(project_root).resolve()
        self.project_name = project_name
        self.full_access = full_access
        self.file_tools = FileTools(
            root=self.project_root,
            full_access=self.full_access,
            require_confirmation=require_confirmation,
            confirm_overwrite=confirm_overwrite,
            confirm_write=confirm_write,
            on_write=(
                lambda path, payload: editor_listener.file_changed(
                    self.project_name,
                    str(path.relative_to(self.project_root)),
                    change_type="write_file",
                    summary="Wrote file via tool",
                    diff_preview=str(payload.get("diff_preview", "")),
                )
            )
            if editor_listener is not None
            else None,
        )
        self.git_tools = GitTools(
            root=self.project_root,
            project_name=self.project_name,
            git_listener=git_listener,
        )
        self.terminal_tools = TerminalTools(
            root=self.project_root,
            full_access=self.full_access,
            project_name=self.project_name,
            terminal_listener=terminal_listener,
            test_listener=test_listener,
        )
        self.code_search = CodeSearch(root=self.project_root, embeddings=embeddings)
        self.editor_tools = EditorTools(
            root=self.project_root,
            project_name=project_name,
            file_tools=self.file_tools,
            vscode_controller=vscode_controller,
            editor_state=editor_state,
            full_access=self.full_access,
            editor_listener=editor_listener,
        )
        self.registry = ToolRegistry()
        self.plugin_manager = plugin_manager
        self._register_core_tools()
        self.loaded_plugins = self.plugin_manager.load_into_registry(
            self.registry,
            {
                "workspace_root": self.workspace_root,
                "project_root": self.project_root,
                "project_name": self.project_name,
                "editor_tools": self.editor_tools,
                "terminal_tools": self.terminal_tools,
            },
        )

    def build_tool_definitions(
        self,
        allow_write: bool = False,
        allow_terminal: bool = True,
        allow_commit: bool = False,
        allow_tests: bool = False,
        allow_editor: bool = True,
    ) -> list[ToolDefinition]:
        return self.registry.build_definitions(
            allow_write=allow_write,
            allow_terminal=allow_terminal,
            allow_commit=allow_commit,
            allow_tests=allow_tests,
            allow_editor=allow_editor,
        )

    def list_available_tools(self) -> list[dict[str, object]]:
        return self.registry.list_tools()

    def _register_core_tools(self) -> None:
        read_scope = (
            "Relative paths resolve from the active project; absolute paths are also allowed while full-access mode is enabled."
            if self.full_access
            else "Paths are scoped to the active project."
        )
        write_scope = (
            "Relative paths resolve from the active project; absolute paths are also allowed while full-access mode is enabled."
            if self.full_access
            else "Writes are scoped to the active project."
        )
        terminal_scope = (
            "Full-access mode allows arbitrary shell commands and absolute working directories."
            if self.full_access
            else "Commands run inside the active project sandbox."
        )
        self.registry.register_tool(
            name="read_file",
            description=f"Read a file. Use this before modifying code. {read_scope}",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=lambda args: self.file_tools.read_file(
                path=args["path"],
                start_line=int(args.get("start_line", 1)),
                end_line=int(args["end_line"]) if "end_line" in args else None,
            ),
            category="filesystem",
        )
        self.registry.register_tool(
            name="list_files",
            description=f"List files inside a directory. {read_scope}",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
                },
                "additionalProperties": False,
            },
            handler=lambda args: self.file_tools.list_files(
                directory=args.get("directory", "."),
                recursive=bool(args.get("recursive", True)),
                limit=int(args.get("limit", 200)),
            ),
            category="filesystem",
        )
        self.registry.register_tool(
            name="search_codebase",
            description="Search the active project with lexical and semantic matching.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda args: self.code_search.search_codebase(
                query=args["query"],
                limit=int(args.get("limit", 8)),
            ),
            category="search",
        )
        self.registry.register_tool(
            name="open_file",
            description=f"Open a file in VS Code. {read_scope}",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer", "minimum": 1},
                    "column": {"type": "integer", "minimum": 1, "default": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=lambda args: self.editor_tools.open_file(
                path=args["path"],
                line=int(args["line"]) if "line" in args else None,
                column=int(args.get("column", 1)),
            ),
            category="editor",
            capabilities={"editor"},
        )
        self.registry.register_tool(
            name="jump_to_symbol",
            description="Find a symbol and open the matching file in VS Code.",
            input_schema={
                "type": "object",
                "properties": {"symbol_name": {"type": "string"}},
                "required": ["symbol_name"],
                "additionalProperties": False,
            },
            handler=lambda args: self.editor_tools.jump_to_symbol(args["symbol_name"]),
            category="editor",
            capabilities={"editor"},
        )
        self.registry.register_tool(
            name="highlight_lines",
            description="Open a file and highlight a line range for inspection.",
            input_schema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["file", "start_line", "end_line"],
                "additionalProperties": False,
            },
            handler=lambda args: self.editor_tools.highlight_lines(
                file=args["file"],
                start_line=int(args["start_line"]),
                end_line=int(args["end_line"]),
            ),
            category="editor",
            capabilities={"editor"},
        )
        self.registry.register_tool(
            name="replace_code_block",
            description="Replace a specific line range in a file with new code.",
            input_schema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "new_code": {"type": "string"},
                },
                "required": ["file", "start_line", "end_line", "new_code"],
                "additionalProperties": False,
            },
            handler=lambda args: self.editor_tools.replace_code_block(
                file=args["file"],
                start_line=int(args["start_line"]),
                end_line=int(args["end_line"]),
                new_code=args["new_code"],
            ),
            category="editor",
            capabilities={"editor", "write"},
        )
        self.registry.register_tool(
            name="append_to_file",
            description="Append content to the end of a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file", "content"],
                "additionalProperties": False,
            },
            handler=lambda args: self.editor_tools.append_to_file(
                file=args["file"],
                content=args["content"],
            ),
            category="editor",
            capabilities={"editor", "write"},
        )
        self.registry.register_tool(
            name="write_file",
            description=f"Write or overwrite a file. Always read the file first if you are modifying existing code. {write_scope}",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=lambda args: self.file_tools.write_file(
                path=args["path"],
                content=args["content"],
                overwrite=bool(args.get("overwrite", False)),
            ),
            category="filesystem",
            capabilities={"write"},
        )
        self.registry.register_tool(
            name="run_terminal",
            description=f"Run a terminal command. {terminal_scope}",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
                    "workdir": {"type": "string", "default": "."},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=lambda args: self.terminal_tools.run_terminal(
                command=args["command"],
                timeout=int(args.get("timeout", 120)),
                workdir=args.get("workdir", "."),
            ),
            category="terminal",
            capabilities={"terminal"},
        )
        self.registry.register_tool(
            name="run_tests",
            description="Detect and run the supported test command(s) for the active project.",
            input_schema={
                "type": "object",
                "properties": {
                    "workdir": {"type": "string", "default": "."},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 1200},
                },
                "additionalProperties": False,
            },
            handler=lambda args: self.terminal_tools.run_tests(
                workdir=args.get("workdir", "."),
                timeout=int(args.get("timeout", 1200)),
            ),
            category="terminal",
            capabilities={"terminal", "tests"},
        )
        self.registry.register_tool(
            name="git_commit",
            description="Commit all current project changes to git with a commit message.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            handler=lambda args: self.git_tools.git_commit(message=args["message"]),
            category="git",
            capabilities={"commit"},
        )
