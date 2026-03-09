from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from boss.context.editor_state import EditorStateStore
from boss.ide.vscode_controller import VSCodeController
from boss.tools.file_tools import FileTools


class EditorTools:
    IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules", "dist", "build"}

    def __init__(
        self,
        root: str | Path,
        project_name: str,
        file_tools: FileTools,
        vscode_controller: VSCodeController,
        editor_state: EditorStateStore,
        full_access: bool = False,
        editor_listener=None,
    ) -> None:
        self.root = Path(root).resolve()
        self.project_name = project_name
        self.file_tools = file_tools
        self.vscode_controller = vscode_controller
        self.editor_state = editor_state
        self.full_access = full_access
        self.editor_listener = editor_listener

    def open_file(self, path: str, line: int | None = None, column: int = 1) -> dict[str, object]:
        resolved = self._resolve_path(path)
        result = self.vscode_controller.open_file(resolved, line=line, column=column)
        relative = self._display_path(resolved)
        self.editor_state.set_active_file(self.project_name, relative)
        if self.editor_listener is not None:
            self.editor_listener.file_opened(self.project_name, relative)
        return {
            "path": str(resolved),
            "line": line,
            "column": column,
            "editor_result": result,
        }

    def jump_to_symbol(self, symbol_name: str) -> dict[str, object]:
        cached = self.editor_state.get_cached_search(self.project_name, f"symbol:{symbol_name}")
        if cached:
            for match in cached:
                file_path = str(match.get("file", ""))
                line = int(match.get("line", 1))
                if self._symbol_exists(file_path, symbol_name, line=line):
                    return self.open_file(file_path, line=line)

        matches: list[dict[str, Any]] = []
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        for file_path in self._iter_files():
            relative_path = str(file_path.relative_to(self.root))
            metadata = self.editor_state.get_file_metadata(self.project_name, relative_path)
            stat = file_path.stat()
            if metadata and metadata.get("mtime") == stat.st_mtime:
                symbols = metadata.get("symbols", [])
            else:
                symbols = self._extract_symbols(file_path)
                self.editor_state.set_file_metadata(
                    self.project_name,
                    relative_path,
                    {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "symbols": symbols,
                    },
                )
            if symbol_name not in symbols and not any(symbol_name.lower() in symbol.lower() for symbol in symbols):
                continue

            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines, start=1):
                if pattern.search(line):
                    matches.append({"file": relative_path, "line": index})
                    break

        self.editor_state.cache_search(self.project_name, f"symbol:{symbol_name}", matches)
        if not matches:
            return {
                "symbol": symbol_name,
                "found": False,
                "message": f"Symbol '{symbol_name}' not found.",
            }
        return self.open_file(matches[0]["file"], line=int(matches[0]["line"]))

    def highlight_lines(self, file: str, start_line: int, end_line: int) -> dict[str, object]:
        resolved = self._resolve_path(file)
        content = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        snippet = "\n".join(content[max(start_line - 1, 0) : max(end_line, start_line)])
        editor_result = self.vscode_controller.open_file(resolved, line=start_line, column=1)
        relative = self._display_path(resolved)
        self.editor_state.set_active_file(self.project_name, relative)
        if self.editor_listener is not None:
            self.editor_listener.file_opened(self.project_name, relative)
        return {
            "path": str(resolved),
            "start_line": start_line,
            "end_line": end_line,
            "snippet": snippet,
            "editor_result": editor_result,
        }

    def replace_code_block(self, file: str, start_line: int, end_line: int, new_code: str) -> dict[str, object]:
        resolved = self._resolve_path(file)
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        replacement_lines = new_code.splitlines()
        updated_lines = lines[: max(start_line - 1, 0)] + replacement_lines + lines[end_line:]
        content = "\n".join(updated_lines)
        if resolved.read_text(encoding="utf-8", errors="replace").endswith("\n"):
            content += "\n"
        tool_path = self._tool_path(resolved)
        display_path = self._display_path(resolved)
        result = self.file_tools.write_file(tool_path, content=content, overwrite=True)
        self.editor_state.record_change(
            self.project_name,
            display_path,
            change_type="replace_code_block",
            summary=f"Replaced lines {start_line}-{end_line}",
            diff_preview=str(result.get("diff_preview", "")),
        )
        if self.editor_listener is not None:
            self.editor_listener.file_changed(
                self.project_name,
                display_path,
                change_type="replace_code_block",
                summary=f"Replaced lines {start_line}-{end_line}",
                diff_preview=str(result.get("diff_preview", "")),
            )
        return result

    def append_to_file(self, file: str, content: str) -> dict[str, object]:
        resolved = self._resolve_path(file)
        existing = resolved.read_text(encoding="utf-8", errors="replace") if resolved.exists() else ""
        updated = existing + content
        tool_path = self._tool_path(resolved)
        display_path = self._display_path(resolved)
        result = self.file_tools.write_file(
            tool_path,
            content=updated,
            overwrite=resolved.exists(),
        )
        self.editor_state.record_change(
            self.project_name,
            display_path,
            change_type="append_to_file",
            summary="Appended content to file",
            diff_preview=str(result.get("diff_preview", "")),
        )
        if self.editor_listener is not None:
            self.editor_listener.file_changed(
                self.project_name,
                display_path,
                change_type="append_to_file",
                summary="Appended content to file",
                diff_preview=str(result.get("diff_preview", "")),
            )
        return result

    def _resolve_path(self, path: str) -> Path:
        candidate = Path(path)
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        if self.full_access:
            return resolved
        if not self._is_relative_to(resolved, self.root):
            raise PermissionError(f"Path '{path}' is outside of the project root.")
        return resolved

    def _tool_path(self, path: Path) -> str:
        return self._display_path(path) if self._is_relative_to(path, self.root) else str(path)

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _iter_files(self):
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(self.root).parts
            if any(part in self.IGNORED_DIRS or part.startswith(".") for part in relative_parts[:-1]):
                continue
            yield path

    def _extract_symbols(self, path: Path) -> list[str]:
        content = path.read_text(encoding="utf-8", errors="replace")
        patterns = [
            r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*export\s+function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*struct\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*trait\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]
        symbols: list[str] = []
        for line in content.splitlines():
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    symbols.append(match.group(1))
        return list(dict.fromkeys(symbols))

    def _symbol_exists(self, file_path: str, symbol_name: str, line: int | None = None) -> bool:
        try:
            resolved = self._resolve_path(file_path)
        except Exception:
            return False
        if not resolved.exists() or not resolved.is_file():
            return False

        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        if line is not None and 1 <= line <= len(lines):
            return bool(pattern.search(lines[line - 1]))
        return any(pattern.search(content_line) for content_line in lines)
