"""Language-aware code parsing — extract symbols, imports, docstrings, and structure.

Architecture: regex-based extraction that covers the common patterns well enough
for code intelligence without requiring a C extension like tree-sitter.  Each
parser documents its limits and is designed to be swappable.

Each language parser returns the same SymbolGraph dataclass so the index layer
is language-agnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    PROTOCOL = "protocol"
    STRUCT = "struct"
    EXTENSION = "extension"
    PROPERTY = "property"


@dataclass
class SymbolDef:
    """A single symbol definition extracted from source code."""
    name: str
    kind: SymbolKind
    line: int
    end_line: int | None = None
    parent: str | None = None          # enclosing class/struct/etc.
    signature: str | None = None       # full signature line
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    exported: bool = True              # publicly visible?

    @property
    def qualified_name(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class ImportDef:
    """A single import statement."""
    module: str
    names: list[str] = field(default_factory=list)  # empty = whole-module import
    alias: str | None = None
    line: int = 0
    is_relative: bool = False


@dataclass
class SymbolGraph:
    """Parsed result for a single file — the core data structure for the index."""
    file_path: str
    language: str
    symbols: list[SymbolDef] = field(default_factory=list)
    imports: list[ImportDef] = field(default_factory=list)
    entry_point: bool = False
    test_file: bool = False
    summary: str = ""
    line_count: int = 0
    errors: list[str] = field(default_factory=list)  # parser warnings

    def definitions(self, *, kind: SymbolKind | None = None) -> list[SymbolDef]:
        if kind is None:
            return self.symbols
        return [s for s in self.symbols if s.kind == kind]

    def top_level(self) -> list[SymbolDef]:
        return [s for s in self.symbols if s.parent is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "symbols": [
                {
                    "name": s.name,
                    "kind": s.kind.value,
                    "line": s.line,
                    "end_line": s.end_line,
                    "parent": s.parent,
                    "signature": s.signature,
                    "docstring": s.docstring[:200] if s.docstring else None,
                    "decorators": s.decorators,
                    "exported": s.exported,
                }
                for s in self.symbols
            ],
            "imports": [
                {
                    "module": i.module,
                    "names": i.names,
                    "alias": i.alias,
                    "line": i.line,
                    "is_relative": i.is_relative,
                }
                for i in self.imports
            ],
            "entry_point": self.entry_point,
            "test_file": self.test_file,
            "summary": self.summary,
            "line_count": self.line_count,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Python parser
# ---------------------------------------------------------------------------

_PY_CLASS_RE = re.compile(
    r"^([ \t]*)class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", re.MULTILINE
)
_PY_FUNC_RE = re.compile(
    r"^([ \t]*)(async\s+)?def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([^\n:]+))?\s*:", re.MULTILINE
)
_PY_IMPORT_RE = re.compile(
    r"^(from\s+(\.{0,3}\w[\w.]*)\s+)?import\s+(.+)", re.MULTILINE
)
_PY_VAR_RE = re.compile(
    r"^([A-Z][A-Z_0-9]+)\s*(?::\s*[^=]+)?\s*=", re.MULTILINE
)
_PY_DECORATOR_RE = re.compile(r"^([ \t]*)@(\S+)", re.MULTILINE)
_PY_DOCSTRING_RE = re.compile(r'^\s*(?:\"\"\"(.*?)(?:\"\"\"|$)|\'\'\'(.*?)(?:\'\'\'|$))', re.DOTALL)


def _extract_docstring(lines: list[str], start_line: int) -> str | None:
    """Extract a docstring from lines following a def/class statement."""
    idx = start_line  # 0-indexed line number after the def/class
    if idx >= len(lines):
        return None
    stripped = lines[idx].strip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            # Single-line docstring
            if stripped.count(quote) >= 2 and stripped.endswith(quote) and len(stripped) > len(quote):
                return stripped[len(quote):-len(quote)].strip()
            # Multi-line
            doc_lines = [stripped[len(quote):]]
            for i in range(idx + 1, min(idx + 20, len(lines))):
                line = lines[i]
                if quote in line:
                    doc_lines.append(line[:line.index(quote)].strip())
                    return "\n".join(doc_lines).strip()
                doc_lines.append(line.strip())
            return "\n".join(doc_lines).strip()
    return None


def parse_python(source: str, file_path: str) -> SymbolGraph:
    """Parse Python source.

    Limits: regex-based, so nested classes and complex multi-line signatures
    are approximated.  Does not handle string-embedded code or conditional
    imports.
    """
    lines = source.splitlines()
    graph = SymbolGraph(
        file_path=file_path,
        language="python",
        line_count=len(lines),
    )

    # Gather decorators by line number
    decorator_map: dict[int, list[str]] = {}
    for m in _PY_DECORATOR_RE.finditer(source):
        line_no = source[:m.start()].count("\n") + 1
        dec_name = m.group(2)
        # attach to the next def/class
        decorator_map.setdefault(line_no, []).append(dec_name)

    # Determine class spans for parent assignment
    class_spans: list[tuple[str, int, int, int]] = []  # (name, start, indent_level, end)
    for m in _PY_CLASS_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(2)
        line_no = source[:m.start()].count("\n") + 1
        class_spans.append((name, line_no, indent, 0))

    # Estimate class end lines
    for i, (name, start, indent, _) in enumerate(class_spans):
        end = len(lines)
        for j in range(start, len(lines)):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                continue
            line_indent = len(lines[j]) - len(lines[j].lstrip())
            if j > start and line_indent <= indent and stripped:
                end = j
                break
        class_spans[i] = (name, start, indent, end)

    def _find_parent(line_no: int, indent: int) -> str | None:
        for name, start, cls_indent, end in class_spans:
            if start < line_no <= end and indent > cls_indent:
                return name
        return None

    # Parse classes
    for m in _PY_CLASS_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(2)
        bases = m.group(3) or ""
        line_no = source[:m.start()].count("\n") + 1
        parent = _find_parent(line_no, indent) if indent > 0 else None

        # Collect decorators from preceding lines
        decs = []
        for dec_line in sorted(decorator_map.keys()):
            if dec_line < line_no and line_no - dec_line <= 5:
                decs.extend(decorator_map[dec_line])

        docstring = _extract_docstring(lines, line_no)  # line after class:
        sig = f"class {name}({bases})" if bases else f"class {name}"
        end_line = None
        for cn, cs, ci, ce in class_spans:
            if cn == name and cs == line_no:
                end_line = ce
                break

        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.CLASS,
            line=line_no,
            end_line=end_line,
            parent=parent,
            signature=sig,
            docstring=docstring,
            decorators=decs,
            exported=not name.startswith("_"),
        ))

    # Parse functions
    for m in _PY_FUNC_RE.finditer(source):
        indent = len(m.group(1))
        async_prefix = m.group(2) or ""
        name = m.group(3)
        params = m.group(4) or ""
        return_type = (m.group(5) or "").strip()
        line_no = source[:m.start()].count("\n") + 1
        parent = _find_parent(line_no, indent)
        kind = SymbolKind.METHOD if parent else SymbolKind.FUNCTION

        decs = []
        for dec_line in sorted(decorator_map.keys()):
            if dec_line < line_no and line_no - dec_line <= 5:
                decs.extend(decorator_map[dec_line])

        docstring = _extract_docstring(lines, line_no)
        sig = f"{async_prefix}def {name}({params})"
        if return_type:
            sig += f" -> {return_type}"

        graph.symbols.append(SymbolDef(
            name=name,
            kind=kind,
            line=line_no,
            parent=parent,
            signature=sig,
            docstring=docstring,
            decorators=decs,
            exported=not name.startswith("_"),
        ))

    # Parse module-level constants
    for m in _PY_VAR_RE.finditer(source):
        name = m.group(1)
        line_no = source[:m.start()].count("\n") + 1
        indent = len(m.group(0)) - len(m.group(0).lstrip())
        if indent == 0:
            graph.symbols.append(SymbolDef(
                name=name,
                kind=SymbolKind.CONSTANT,
                line=line_no,
                exported=not name.startswith("_"),
            ))

    # Parse imports
    for m in _PY_IMPORT_RE.finditer(source):
        line_no = source[:m.start()].count("\n") + 1
        from_module = m.group(2)
        import_part = m.group(3).strip()
        is_relative = bool(m.group(1)) and m.group(1).strip().startswith("from .")

        if from_module:
            # from X import a, b, c
            names = [n.strip().split(" as ")[0].strip() for n in import_part.split(",") if n.strip()]
            alias = None
            graph.imports.append(ImportDef(
                module=from_module,
                names=names,
                line=line_no,
                is_relative=is_relative,
            ))
        else:
            # import X, import X as Y
            for part in import_part.split(","):
                part = part.strip()
                if " as " in part:
                    mod, alias = part.split(" as ", 1)
                    graph.imports.append(ImportDef(module=mod.strip(), alias=alias.strip(), line=line_no))
                else:
                    graph.imports.append(ImportDef(module=part, line=line_no))

    # Detect test file
    name_lower = Path(file_path).name.lower()
    graph.test_file = (
        name_lower.startswith("test_")
        or name_lower.endswith("_test.py")
        or "/tests/" in file_path
        or "/test/" in file_path
    )

    # Detect entry point
    graph.entry_point = (
        name_lower in {"main.py", "app.py", "api.py", "server.py", "manage.py", "cli.py", "__main__.py"}
        or 'if __name__' in source
    )

    # Build summary
    classes = [s.name for s in graph.symbols if s.kind == SymbolKind.CLASS]
    functions = [s.name for s in graph.symbols if s.kind == SymbolKind.FUNCTION]
    top_doc = _extract_docstring(lines, 0)
    parts = []
    if top_doc:
        parts.append(top_doc[:200])
    if classes:
        parts.append(f"Classes: {', '.join(classes[:10])}")
    if functions:
        parts.append(f"Functions: {', '.join(functions[:10])}")
    graph.summary = "; ".join(parts) if parts else Path(file_path).stem

    return graph


# ---------------------------------------------------------------------------
# Swift parser
# ---------------------------------------------------------------------------

_SW_CLASS_RE = re.compile(
    r"^([ \t]*)(?:(public|private|internal|open|fileprivate)\s+)?"
    r"(?:final\s+)?"
    r"(class|struct|enum|protocol|actor)\s+(\w+)"
    r"(?:\s*<[^>]*>)?"
    r"(?:\s*:\s*([^\{]+))?\s*\{",
    re.MULTILINE,
)
_SW_FUNC_RE = re.compile(
    r"^([ \t]*)(?:(public|private|internal|open|fileprivate)\s+)?"
    r"(?:static\s+|class\s+|override\s+|@\w+\s+)*"
    r"(?:(init|deinit)|func\s+(\w+))"
    r"\s*(?:<[^>]*>)?\s*\(([^)]*)\)"
    r"(?:\s*(?:throws|rethrows|async))?"
    r"(?:\s*->\s*([^\{\n]+))?",
    re.MULTILINE,
)
_SW_PROP_RE = re.compile(
    r"^([ \t]*)(?:(public|private|internal|open|fileprivate)\s+)?"
    r"(?:static\s+|class\s+|lazy\s+|@\w+\s+)*"
    r"(var|let)\s+(\w+)\s*(?::\s*([^=\{\n]+))?",
    re.MULTILINE,
)
_SW_IMPORT_RE = re.compile(r"^\s*import\s+(\w+)", re.MULTILINE)
_SW_EXTENSION_RE = re.compile(
    r"^([ \t]*)(?:(public|private|internal|fileprivate)\s+)?extension\s+(\w+)",
    re.MULTILINE,
)
_SW_TYPEALIAS_RE = re.compile(
    r"^([ \t]*)(?:(public|private|internal|fileprivate)\s+)?typealias\s+(\w+)\s*=\s*(.+)",
    re.MULTILINE,
)


def _swift_doc_comment(lines: list[str], line_no: int) -> str | None:
    """Extract a /// doc-comment block preceding a declaration."""
    doc_lines = []
    idx = line_no - 2  # 0-indexed line before the decl
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped.startswith("///"):
            doc_lines.insert(0, stripped[3:].strip())
            idx -= 1
        elif stripped.startswith("/*"):
            break
        else:
            break
    return "\n".join(doc_lines) if doc_lines else None


def parse_swift(source: str, file_path: str) -> SymbolGraph:
    """Parse Swift source.

    Limits: regex-based, so complex generics, nested types, and multi-line
    declarations are approximated.  Extension methods are attributed to the
    extended type when possible.
    """
    lines = source.splitlines()
    graph = SymbolGraph(
        file_path=file_path,
        language="swift",
        line_count=len(lines),
    )

    # Track type spans for parent assignment
    type_spans: list[tuple[str, int, int]] = []  # (name, start_line, indent)

    # Parse types (class/struct/enum/protocol/actor)
    for m in _SW_CLASS_RE.finditer(source):
        indent = len(m.group(1))
        access = m.group(2) or "internal"
        type_keyword = m.group(3)
        name = m.group(4)
        conformances = (m.group(5) or "").strip()
        line_no = source[:m.start()].count("\n") + 1
        kind_map = {
            "class": SymbolKind.CLASS,
            "struct": SymbolKind.STRUCT,
            "enum": SymbolKind.ENUM,
            "protocol": SymbolKind.PROTOCOL,
            "actor": SymbolKind.CLASS,
        }
        kind = kind_map.get(type_keyword, SymbolKind.CLASS)
        sig = f"{type_keyword} {name}"
        if conformances:
            sig += f": {conformances}"

        type_spans.append((name, line_no, indent))
        docstring = _swift_doc_comment(lines, line_no)

        graph.symbols.append(SymbolDef(
            name=name,
            kind=kind,
            line=line_no,
            signature=sig,
            docstring=docstring,
            exported=access not in ("private", "fileprivate"),
        ))

    # Parse extensions
    for m in _SW_EXTENSION_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(3)
        line_no = source[:m.start()].count("\n") + 1
        type_spans.append((name, line_no, indent))
        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.EXTENSION,
            line=line_no,
            signature=f"extension {name}",
        ))

    def _find_swift_parent(line_no: int, indent: int) -> str | None:
        for name, start, type_indent in type_spans:
            if start < line_no and indent > type_indent:
                return name
        return None

    # Parse functions
    for m in _SW_FUNC_RE.finditer(source):
        indent = len(m.group(1))
        access = m.group(2) or "internal"
        init_name = m.group(3)
        func_name = m.group(4)
        params = m.group(5) or ""
        return_type = (m.group(6) or "").strip()
        name = init_name or func_name or "unknown"
        line_no = source[:m.start()].count("\n") + 1
        parent = _find_swift_parent(line_no, indent)
        kind = SymbolKind.METHOD if parent else SymbolKind.FUNCTION
        sig = f"func {name}({params})"
        if return_type:
            sig += f" -> {return_type}"
        docstring = _swift_doc_comment(lines, line_no)

        graph.symbols.append(SymbolDef(
            name=name,
            kind=kind,
            line=line_no,
            parent=parent,
            signature=sig,
            docstring=docstring,
            exported=access not in ("private", "fileprivate"),
        ))

    # Parse properties
    for m in _SW_PROP_RE.finditer(source):
        indent = len(m.group(1))
        access = m.group(2) or "internal"
        var_let = m.group(3)
        name = m.group(4)
        type_annotation = (m.group(5) or "").strip()
        line_no = source[:m.start()].count("\n") + 1
        parent = _find_swift_parent(line_no, indent)
        kind = SymbolKind.CONSTANT if var_let == "let" and parent is None else SymbolKind.PROPERTY
        sig = f"{var_let} {name}"
        if type_annotation:
            sig += f": {type_annotation}"

        # Skip local variables (inside function bodies)
        if indent > 0 and parent is None:
            continue

        graph.symbols.append(SymbolDef(
            name=name,
            kind=kind,
            line=line_no,
            parent=parent,
            signature=sig,
            exported=access not in ("private", "fileprivate"),
        ))

    # Parse type aliases
    for m in _SW_TYPEALIAS_RE.finditer(source):
        name = m.group(3)
        target = m.group(4).strip()
        line_no = source[:m.start()].count("\n") + 1
        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.TYPE_ALIAS,
            line=line_no,
            signature=f"typealias {name} = {target}",
        ))

    # Parse imports
    for m in _SW_IMPORT_RE.finditer(source):
        module = m.group(1)
        line_no = source[:m.start()].count("\n") + 1
        graph.imports.append(ImportDef(module=module, line=line_no))

    # Detect test file
    name_lower = Path(file_path).name.lower()
    graph.test_file = (
        name_lower.endswith("tests.swift")
        or name_lower.endswith("test.swift")
        or "XCTest" in source
        or "/Tests/" in file_path
    )

    # Detect entry point
    graph.entry_point = (
        name_lower in {"main.swift", "app.swift"}
        or "@main" in source
        or "Package.swift" in file_path
    )

    # Build summary
    types = [s.name for s in graph.symbols if s.kind in (SymbolKind.CLASS, SymbolKind.STRUCT, SymbolKind.ENUM, SymbolKind.PROTOCOL)]
    funcs = [s.name for s in graph.symbols if s.kind == SymbolKind.FUNCTION]
    parts = []
    if types:
        parts.append(f"Types: {', '.join(types[:10])}")
    if funcs:
        parts.append(f"Functions: {', '.join(funcs[:10])}")
    graph.summary = "; ".join(parts) if parts else Path(file_path).stem

    return graph


# ---------------------------------------------------------------------------
# TypeScript / JavaScript parser
# ---------------------------------------------------------------------------

_TS_CLASS_RE = re.compile(
    r"^([ \t]*)(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"
    r"(?:\s*<[^>]*>)?"
    r"(?:\s+(?:extends|implements)\s+([^\{]+))?\s*\{",
    re.MULTILINE,
)
_TS_FUNC_RE = re.compile(
    r"^([ \t]*)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)",
    re.MULTILINE,
)
_TS_ARROW_RE = re.compile(
    r"^([ \t]*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[^=\n]+)?\s*=\s*(?:async\s+)?\(?([^)\n]*)\)?\s*(?::\s*[^=>\n]+)?\s*=>",
    re.MULTILINE,
)
_TS_INTERFACE_RE = re.compile(
    r"^([ \t]*)(?:export\s+)?interface\s+(\w+)(?:\s*<[^>]*>)?(?:\s+extends\s+([^\{]+))?\s*\{",
    re.MULTILINE,
)
_TS_TYPE_RE = re.compile(
    r"^([ \t]*)(?:export\s+)?type\s+(\w+)(?:\s*<[^>]*>)?\s*=",
    re.MULTILINE,
)
_TS_ENUM_RE = re.compile(
    r"^([ \t]*)(?:export\s+)?(?:const\s+)?enum\s+(\w+)\s*\{",
    re.MULTILINE,
)
_TS_IMPORT_RE = re.compile(
    r"""^import\s+(?:(?:type\s+)?(?:\{([^}]+)\}|(\w+)(?:\s*,\s*\{([^}]+)\})?)\s+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_TS_METHOD_RE = re.compile(
    r"^([ \t]+)(?:public|private|protected|static|async|readonly|\s)*(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_TS_CONST_RE = re.compile(
    r"^(?:export\s+)?const\s+([A-Z][A-Z_0-9]+)\s*(?::\s*[^=]+)?\s*=",
    re.MULTILINE,
)


def _ts_jsdoc(lines: list[str], line_no: int) -> str | None:
    """Extract a /** ... */ JSDoc block preceding a declaration."""
    idx = line_no - 2
    if idx < 0:
        return None
    stripped = lines[idx].strip()
    if stripped.endswith("*/"):
        doc_lines = []
        while idx >= 0:
            line = lines[idx].strip()
            doc_lines.insert(0, line.lstrip("/* ").rstrip("*/").strip())
            if line.startswith("/**") or line.startswith("/*"):
                break
            idx -= 1
        return "\n".join(l for l in doc_lines if l) or None
    return None


def parse_typescript(source: str, file_path: str) -> SymbolGraph:
    """Parse TypeScript or JavaScript source.

    Limits: regex-based, so template literals, computed property names,
    and complex destructuring patterns are missed.  JSX expressions are
    mostly ignored.
    """
    lines = source.splitlines()
    ext = Path(file_path).suffix.lower()
    lang = "typescript" if ext in (".ts", ".tsx") else "javascript"
    graph = SymbolGraph(
        file_path=file_path,
        language=lang,
        line_count=len(lines),
    )

    # Track class spans for parent assignment
    class_spans: list[tuple[str, int, int]] = []  # (name, line, indent)

    # Parse classes
    for m in _TS_CLASS_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(2)
        extends = (m.group(3) or "").strip()
        line_no = source[:m.start()].count("\n") + 1
        exported = "export" in source[max(0, m.start() - 20):m.start() + 10]
        sig = f"class {name}"
        if extends:
            sig += f" extends {extends}"
        docstring = _ts_jsdoc(lines, line_no)
        class_spans.append((name, line_no, indent))

        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.CLASS,
            line=line_no,
            signature=sig,
            docstring=docstring,
            exported=exported,
        ))

    # Parse interfaces (TS only)
    for m in _TS_INTERFACE_RE.finditer(source):
        name = m.group(2)
        extends = (m.group(3) or "").strip()
        line_no = source[:m.start()].count("\n") + 1
        sig = f"interface {name}"
        if extends:
            sig += f" extends {extends}"
        docstring = _ts_jsdoc(lines, line_no)

        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.INTERFACE,
            line=line_no,
            signature=sig,
            docstring=docstring,
            exported="export" in source[max(0, m.start() - 20):m.start() + 10],
        ))

    # Parse type aliases
    for m in _TS_TYPE_RE.finditer(source):
        name = m.group(2)
        line_no = source[:m.start()].count("\n") + 1
        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.TYPE_ALIAS,
            line=line_no,
            signature=f"type {name}",
            exported="export" in source[max(0, m.start() - 20):m.start() + 10],
        ))

    # Parse enums
    for m in _TS_ENUM_RE.finditer(source):
        name = m.group(2)
        line_no = source[:m.start()].count("\n") + 1
        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.ENUM,
            line=line_no,
            signature=f"enum {name}",
            exported="export" in source[max(0, m.start() - 20):m.start() + 10],
        ))

    # Parse functions
    for m in _TS_FUNC_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(2)
        params = m.group(3) or ""
        line_no = source[:m.start()].count("\n") + 1
        exported = "export" in source[max(0, m.start() - 20):m.start() + 10]
        docstring = _ts_jsdoc(lines, line_no)

        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.FUNCTION,
            line=line_no,
            signature=f"function {name}({params})",
            docstring=docstring,
            exported=exported,
        ))

    # Parse arrow function constants
    for m in _TS_ARROW_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(2)
        params = m.group(3) or ""
        line_no = source[:m.start()].count("\n") + 1
        if indent == 0:
            exported = "export" in source[max(0, m.start() - 20):m.start() + 10]
            docstring = _ts_jsdoc(lines, line_no)
            graph.symbols.append(SymbolDef(
                name=name,
                kind=SymbolKind.FUNCTION,
                line=line_no,
                signature=f"const {name} = ({params}) =>",
                docstring=docstring,
                exported=exported,
            ))

    # Parse constants
    for m in _TS_CONST_RE.finditer(source):
        name = m.group(1)
        line_no = source[:m.start()].count("\n") + 1
        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.CONSTANT,
            line=line_no,
            exported="export" in source[max(0, m.start() - 20):m.start() + 10],
        ))

    # Parse class methods
    known_names = {s.name for s in graph.symbols}
    for m in _TS_METHOD_RE.finditer(source):
        indent = len(m.group(1))
        name = m.group(2)
        params = m.group(3) or ""
        line_no = source[:m.start()].count("\n") + 1

        # Skip already-captured symbols and JS keywords
        if name in known_names or name in ("if", "for", "while", "switch", "catch", "return", "new", "throw"):
            continue

        # Find parent class
        parent = None
        for cls_name, cls_line, cls_indent in class_spans:
            if cls_line < line_no and indent > cls_indent:
                parent = cls_name

        if parent is None:
            continue  # Only capture methods inside classes

        docstring = _ts_jsdoc(lines, line_no)
        graph.symbols.append(SymbolDef(
            name=name,
            kind=SymbolKind.METHOD,
            line=line_no,
            parent=parent,
            signature=f"{name}({params})",
            docstring=docstring,
        ))

    # Parse imports
    for m in _TS_IMPORT_RE.finditer(source):
        named = m.group(1) or ""
        default = m.group(2) or ""
        extra_named = m.group(3) or ""
        module = m.group(4)
        line_no = source[:m.start()].count("\n") + 1
        names = []
        if default:
            names.append(default)
        for group in (named, extra_named):
            names.extend(n.strip().split(" as ")[0].strip() for n in group.split(",") if n.strip())
        graph.imports.append(ImportDef(
            module=module,
            names=names,
            line=line_no,
            is_relative=module.startswith("."),
        ))

    # Detect test file
    name_lower = Path(file_path).name.lower()
    graph.test_file = (
        name_lower.endswith(".test.ts")
        or name_lower.endswith(".test.js")
        or name_lower.endswith(".test.tsx")
        or name_lower.endswith(".test.jsx")
        or name_lower.endswith(".spec.ts")
        or name_lower.endswith(".spec.js")
        or "/__tests__/" in file_path
    )

    # Detect entry point
    graph.entry_point = name_lower in {
        "index.ts", "index.js", "index.tsx", "main.ts", "main.js",
        "app.ts", "app.js", "app.tsx", "server.ts", "server.js",
    }

    # Build summary
    types = [s.name for s in graph.symbols if s.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE, SymbolKind.ENUM)]
    funcs = [s.name for s in graph.symbols if s.kind == SymbolKind.FUNCTION]
    parts = []
    if types:
        parts.append(f"Types: {', '.join(types[:10])}")
    if funcs:
        parts.append(f"Functions: {', '.join(funcs[:10])}")
    graph.summary = "; ".join(parts) if parts else Path(file_path).stem

    return graph


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

_PARSERS = {
    "python": parse_python,
    "swift": parse_swift,
    "typescript": parse_typescript,
    "javascript": parse_typescript,
}


def detect_language(file_path: str) -> str | None:
    """Return the language name for a file, or None if unsupported."""
    ext = Path(file_path).suffix.lower()
    return _LANGUAGE_MAP.get(ext)


def parse_file(file_path: str, source: str | None = None) -> SymbolGraph | None:
    """Parse a file and return its SymbolGraph, or None if language is unsupported."""
    lang = detect_language(file_path)
    if lang is None:
        return None
    parser = _PARSERS.get(lang)
    if parser is None:
        return None
    if source is None:
        try:
            source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return parser(source, file_path)
