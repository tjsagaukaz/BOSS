"""Filesystem tools — let agents read files and explore directory trees."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from boss.control import is_path_allowed_for_agent
from boss.execution import ExecutionType, display_value, governed_function_tool, scope_value


_MAX_READ_BYTES = 100_000  # ~100 KB per read
_MAX_GREP_MATCHES = 30
_MAX_DIR_ENTRIES = 200


@governed_function_tool(
    execution_type=ExecutionType.READ,
    title="Read File",
    describe_call=lambda params: f'Read {params.get("path", "file")}',
    scope_key=lambda params: scope_value("fs", "read"),
    scope_label=lambda params: display_value(params.get("path"), fallback="file"),
)
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a file's contents. Returns the text with line numbers.

    Args:
        path: Absolute path to the file.
        start_line: First line to read (1-based). Defaults to 1.
        end_line: Last line to read (1-based, inclusive). 0 means read to end (up to limit).
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"File not found: {path}"
    if not is_path_allowed_for_agent(p):
        return f"Access denied: {path}"

    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading {path}: {exc}"

    lines = raw.splitlines()
    total = len(lines)

    start = max(1, start_line)
    end = end_line if end_line > 0 else total
    end = min(end, total)

    if start > total:
        return f"{path}: {total} lines total, start_line {start} is past end of file."

    selected = lines[start - 1 : end]

    # Truncate if too large
    output_lines: list[str] = []
    byte_count = 0
    for idx, line in enumerate(selected, start=start):
        entry = f"{idx:>5} | {line}"
        byte_count += len(entry) + 1
        if byte_count > _MAX_READ_BYTES:
            output_lines.append(f"  ... (truncated at {idx - 1}, file has {total} lines)")
            break
        output_lines.append(entry)

    header = f"{path} ({total} lines)"
    if start > 1 or end < total:
        header += f" [showing lines {start}-{min(end, start + len(output_lines) - 1)}]"
    return header + "\n" + "\n".join(output_lines)


@governed_function_tool(
    execution_type=ExecutionType.READ,
    title="List Directory",
    describe_call=lambda params: f'List {params.get("path", "directory")}',
    scope_key=lambda params: scope_value("fs", "list"),
    scope_label=lambda params: display_value(params.get("path"), fallback="directory"),
)
def list_directory(path: str, depth: int = 1) -> str:
    """List files and subdirectories at a path.

    Args:
        path: Absolute path to the directory.
        depth: How many levels deep to recurse (1 = immediate children, 2 = one level of nesting). Max 3.
    """
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"Not a directory: {path}"
    if not is_path_allowed_for_agent(p):
        return f"Access denied: {path}"

    depth = max(1, min(depth, 3))
    entries: list[str] = []

    def _walk(current: Path, prefix: str, remaining_depth: int) -> None:
        if len(entries) >= _MAX_DIR_ENTRIES:
            return
        try:
            children = sorted(current.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                entries.append(f"{prefix}{child.name}/")
                if remaining_depth > 1:
                    _walk(child, prefix + "  ", remaining_depth - 1)
            else:
                entries.append(f"{prefix}{child.name}")

    _walk(p, "", depth)
    if not entries:
        return f"{path}: empty directory"
    result = f"{path} ({len(entries)} entries):\n" + "\n".join(entries)
    if len(entries) >= _MAX_DIR_ENTRIES:
        result += "\n  ... (truncated)"
    return result


@governed_function_tool(
    execution_type=ExecutionType.SEARCH,
    title="Grep Codebase",
    describe_call=lambda params: f'Grep for "{params.get("pattern", "")}"',
    scope_key=lambda params: scope_value("fs", "grep"),
    scope_label=lambda params: f"Grep: {display_value(params.get('pattern'), fallback='pattern')}",
)
def grep_codebase(
    pattern: str,
    path: str = "",
    file_glob: str = "",
    max_results: int = 20,
) -> str:
    """Search for a text pattern across files in a directory tree.

    Args:
        pattern: Text or regex pattern to search for (case-insensitive).
        path: Root directory to search in. Defaults to the workspace root.
        file_glob: Optional glob to filter filenames (e.g. '*.py', '*.swift').
        max_results: Maximum number of matching lines to return.
    """
    root = Path(path).expanduser() if path else None
    if root and not root.is_dir():
        return f"Not a directory: {path}"
    if root and not is_path_allowed_for_agent(root):
        return f"Access denied: {path}"

    # Determine search root — fall back to workspace if not specified
    if root is None:
        from boss.control import load_boss_control
        control = load_boss_control(None)
        root = control.root

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"Invalid pattern: {exc}"

    max_results = max(1, min(max_results, _MAX_GREP_MATCHES))
    matches: list[str] = []
    files_searched = 0

    _SKIP_DIRS = {
        ".git", ".build", "__pycache__", "node_modules", ".venv",
        "venv", ".tox", "dist", "build", ".eggs", ".mypy_cache",
    }
    _SKIP_EXTENSIONS = {
        ".pyc", ".pyo", ".o", ".a", ".dylib", ".so",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
        ".zip", ".tar", ".gz", ".bz2",
    }

    for file_path in _walk_files(root, skip_dirs=_SKIP_DIRS, skip_extensions=_SKIP_EXTENSIONS):
        if file_glob and not fnmatch.fnmatch(file_path.name, file_glob):
            continue
        if not is_path_allowed_for_agent(file_path):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files_searched += 1
        for line_num, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
                matches.append(f"{rel}:{line_num}: {line.rstrip()[:200]}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    if not matches:
        return f'No matches for "{pattern}" in {root} ({files_searched} files searched).'
    header = f'Found {len(matches)} match(es) for "{pattern}" ({files_searched} files searched):'
    return header + "\n" + "\n".join(matches)


def _walk_files(
    root: Path,
    *,
    skip_dirs: set[str],
    skip_extensions: set[str],
) -> list[Path]:
    """Recursively collect files, skipping hidden/build directories."""
    results: list[Path] = []
    max_files = 5000

    def _recurse(current: Path) -> None:
        if len(results) >= max_files:
            return
        try:
            children = sorted(current.iterdir())
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                if child.name in skip_dirs:
                    continue
                _recurse(child)
            elif child.is_file():
                if child.suffix.lower() in skip_extensions:
                    continue
                results.append(child)
                if len(results) >= max_files:
                    return

    _recurse(root)
    return results
