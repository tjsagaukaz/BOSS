from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Callable

from boss.types import WriteConfirmationHandler


class FileTools:
    def __init__(
        self,
        root: str | Path,
        full_access: bool = False,
        require_confirmation: bool = True,
        confirm_overwrite: Callable[[Path], bool] | None = None,
        confirm_write: WriteConfirmationHandler | None = None,
        on_write: Callable[[Path, dict[str, Any]], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.full_access = full_access
        self.require_confirmation = require_confirmation
        self.confirm_overwrite = confirm_overwrite
        self.confirm_write = confirm_write
        self.on_write = on_write

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, object]:
        resolved = self._resolve_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")
        content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        start_index = max(start_line - 1, 0)
        end_index = end_line if end_line is not None else len(lines)
        excerpt = "\n".join(lines[start_index:end_index])
        return {
            "path": str(resolved),
            "start_line": start_line,
            "end_line": end_index,
            "content": excerpt,
        }

    def write_file(self, path: str, content: str, overwrite: bool = False) -> dict[str, object]:
        resolved = self._resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        existed = resolved.exists()
        previous = resolved.read_text(encoding="utf-8", errors="replace") if existed else ""
        changed = (not existed) or previous != content
        diff_preview = self._build_diff_preview(resolved, previous, content, existed)

        if existed and not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing file {resolved}. Re-run with overwrite=true."
            )
        if changed and self.require_confirmation:
            if self.confirm_write is not None:
                if not self.confirm_write(resolved, diff_preview, existed):
                    raise PermissionError(f"Write not approved for {resolved}.")
            elif existed and overwrite:
                if self.confirm_overwrite is None:
                    raise PermissionError(f"Overwrite confirmation required for {resolved}.")
                if not self.confirm_overwrite(resolved):
                    raise PermissionError(f"Overwrite not approved for {resolved}.")

        resolved.write_text(content, encoding="utf-8")
        payload = {
            "path": str(resolved),
            "existed": existed,
            "changed": changed,
            "size": len(content),
            "diff_preview": diff_preview,
        }
        if changed and self.on_write is not None:
            self.on_write(resolved, payload)
        return payload

    def list_files(self, directory: str = ".", recursive: bool = True, limit: int = 200) -> dict[str, object]:
        resolved = self._resolve_path(directory)
        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {resolved}")

        display_root = self.root if self._is_relative_to(resolved, self.root) else resolved
        iterator = resolved.rglob("*") if recursive else resolved.glob("*")
        files = [
            self._display_path(path, base=display_root)
            for path in iterator
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(display_root).parts)
        ]
        return {
            "directory": str(resolved),
            "count": len(files),
            "files": files[:limit],
        }

    def _resolve_path(self, path: str) -> Path:
        candidate = Path(path)
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        if self.full_access:
            return resolved
        if not self._is_relative_to(resolved, self.root):
            raise PermissionError(f"Path '{path}' is outside of the workspace root.")
        return resolved

    def _display_path(self, path: Path, *, base: Path | None = None) -> str:
        anchor = base or self.root
        try:
            return str(path.relative_to(anchor))
        except ValueError:
            return str(path)

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _build_diff_preview(self, path: Path, before: str, after: str, existed: bool) -> str:
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        from_file = f"{path.name} (previous)" if existed else "/dev/null"
        to_file = str(path.name)
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=from_file,
            tofile=to_file,
            lineterm="",
        )
        preview = "\n".join(diff)
        if not preview and not existed:
            preview = "\n".join(
                difflib.unified_diff([], after_lines, fromfile="/dev/null", tofile=str(path.name), lineterm="")
            )
        return preview[:8000]
