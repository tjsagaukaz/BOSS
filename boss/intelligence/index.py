"""Code intelligence index: SQLite-backed symbol store with incremental updates.

Stores parsed SymbolGraphs in a local SQLite database alongside the existing
knowledge store.  Supports fast lookup by symbol name, kind, file path, and
project, plus reverse-import resolution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boss.config import settings
from boss.intelligence.parsers import (
    ImportDef,
    SymbolDef,
    SymbolGraph,
    SymbolKind,
    detect_language,
    parse_file,
)

logger = logging.getLogger(__name__)

_INDEX_DB_FILE = settings.app_data_dir / "code_index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT    NOT NULL UNIQUE,
    project_path    TEXT,
    language        TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,
    line_count      INTEGER NOT NULL DEFAULT 0,
    summary         TEXT    NOT NULL DEFAULT '',
    is_entry_point  INTEGER NOT NULL DEFAULT 0,
    is_test_file    INTEGER NOT NULL DEFAULT 0,
    indexed_at      REAL    NOT NULL,
    errors          TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS symbols (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES indexed_files(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    qualified_name  TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    line            INTEGER NOT NULL,
    end_line        INTEGER,
    parent          TEXT,
    signature       TEXT,
    docstring       TEXT,
    decorators      TEXT    NOT NULL DEFAULT '[]',
    exported        INTEGER NOT NULL DEFAULT 1,
    project_path    TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES indexed_files(id) ON DELETE CASCADE,
    module          TEXT    NOT NULL,
    names           TEXT    NOT NULL DEFAULT '[]',
    alias           TEXT,
    line            INTEGER NOT NULL DEFAULT 0,
    is_relative     INTEGER NOT NULL DEFAULT 0,
    project_path    TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_project ON symbols(project_path);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports(module);
CREATE INDEX IF NOT EXISTS idx_imports_project ON imports(project_path);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE INDEX IF NOT EXISTS idx_files_project ON indexed_files(project_path);
CREATE INDEX IF NOT EXISTS idx_files_language ON indexed_files(language);
"""


def _content_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class SymbolSearchResult:
    """A symbol search hit with context."""
    name: str
    qualified_name: str
    kind: str
    file_path: str
    line: int
    end_line: int | None
    parent: str | None
    signature: str | None
    docstring: str | None
    project_path: str | None
    exported: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line": self.line,
            "end_line": self.end_line,
            "parent": self.parent,
            "signature": self.signature,
            "docstring": self.docstring[:200] if self.docstring else None,
            "project_path": self.project_path,
            "exported": self.exported,
        }


@dataclass
class ImportSearchResult:
    """An import search hit."""
    module: str
    names: list[str]
    file_path: str
    line: int
    project_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "names": self.names,
            "file_path": self.file_path,
            "line": self.line,
            "project_path": self.project_path,
        }


class CodeIndex:
    """SQLite-backed code intelligence index."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _INDEX_DB_FILE
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Incremental indexing
    # ------------------------------------------------------------------

    def index_file(
        self,
        file_path: str,
        *,
        project_path: str | None = None,
        source: str | None = None,
    ) -> SymbolGraph | None:
        """Parse and index a single file.  Skips if content hash is unchanged."""
        lang = detect_language(file_path)
        if lang is None:
            return None

        if source is None:
            try:
                source = Path(file_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None

        content_hash = _content_hash(source)

        # Check if already indexed with same hash
        row = self._conn.execute(
            "SELECT id, content_hash FROM indexed_files WHERE file_path = ?",
            (file_path,),
        ).fetchone()
        if row and row["content_hash"] == content_hash:
            return None  # Already up to date

        graph = parse_file(file_path, source)
        if graph is None:
            return None

        now = time.time()

        # Delete old data if exists
        if row:
            file_id = row["id"]
            self._conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
            self._conn.execute("DELETE FROM imports WHERE file_id = ?", (file_id,))
            self._conn.execute(
                """UPDATE indexed_files SET
                    language=?, content_hash=?, line_count=?, summary=?,
                    is_entry_point=?, is_test_file=?, indexed_at=?,
                    errors=?, project_path=?
                WHERE id = ?""",
                (
                    graph.language, content_hash, graph.line_count,
                    graph.summary, int(graph.entry_point), int(graph.test_file),
                    now, json.dumps(graph.errors), project_path, file_id,
                ),
            )
        else:
            cursor = self._conn.execute(
                """INSERT INTO indexed_files
                    (file_path, project_path, language, content_hash, line_count,
                     summary, is_entry_point, is_test_file, indexed_at, errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    file_path, project_path, graph.language, content_hash,
                    graph.line_count, graph.summary, int(graph.entry_point),
                    int(graph.test_file), now, json.dumps(graph.errors),
                ),
            )
            file_id = cursor.lastrowid

        # Insert symbols
        for sym in graph.symbols:
            self._conn.execute(
                """INSERT INTO symbols
                    (file_id, name, qualified_name, kind, line, end_line,
                     parent, signature, docstring, decorators, exported, project_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    file_id, sym.name, sym.qualified_name, sym.kind.value,
                    sym.line, sym.end_line, sym.parent, sym.signature,
                    sym.docstring, json.dumps(sym.decorators), int(sym.exported),
                    project_path,
                ),
            )

        # Insert imports
        for imp in graph.imports:
            self._conn.execute(
                """INSERT INTO imports
                    (file_id, module, names, alias, line, is_relative, project_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    file_id, imp.module, json.dumps(imp.names), imp.alias,
                    imp.line, int(imp.is_relative), project_path,
                ),
            )

        return graph

    def commit(self) -> None:
        self._conn.commit()

    def index_project(
        self,
        project_path: str,
        file_paths: list[str],
    ) -> dict[str, int]:
        """Index a batch of files from a project.  Returns indexing stats."""
        indexed = 0
        skipped = 0
        errors = 0
        for fp in file_paths:
            try:
                result = self.index_file(fp, project_path=project_path)
                if result is not None:
                    indexed += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
                logger.debug("Failed to index %s", fp, exc_info=True)
        self.commit()
        return {"indexed": indexed, "skipped": skipped, "errors": errors}

    def remove_file(self, file_path: str) -> None:
        row = self._conn.execute(
            "SELECT id FROM indexed_files WHERE file_path = ?", (file_path,),
        ).fetchone()
        if row:
            fid = row["id"]
            self._conn.execute("DELETE FROM symbols WHERE file_id = ?", (fid,))
            self._conn.execute("DELETE FROM imports WHERE file_id = ?", (fid,))
            self._conn.execute("DELETE FROM indexed_files WHERE id = ?", (fid,))

    def prune_project(self, project_path: str, keep_paths: set[str]) -> int:
        """Remove indexed files for a project that are no longer in keep_paths."""
        rows = self._conn.execute(
            "SELECT id, file_path FROM indexed_files WHERE project_path = ?",
            (project_path,),
        ).fetchall()
        removed = 0
        for row in rows:
            if row["file_path"] not in keep_paths:
                fid = row["id"]
                self._conn.execute("DELETE FROM symbols WHERE file_id = ?", (fid,))
                self._conn.execute("DELETE FROM imports WHERE file_id = ?", (fid,))
                self._conn.execute("DELETE FROM indexed_files WHERE id = ?", (fid,))
                removed += 1
        if removed:
            self.commit()
        return removed

    # ------------------------------------------------------------------
    # Symbol queries
    # ------------------------------------------------------------------

    def find_symbol(
        self,
        name: str,
        *,
        kind: str | None = None,
        project_path: str | None = None,
        exported_only: bool = False,
        limit: int = 20,
    ) -> list[SymbolSearchResult]:
        """Find symbols by name (case-insensitive prefix match)."""
        conditions = ["s.name LIKE ?"]
        params: list[Any] = [f"{name}%"]

        if kind:
            conditions.append("s.kind = ?")
            params.append(kind)
        if project_path:
            conditions.append("s.project_path = ?")
            params.append(project_path)
        if exported_only:
            conditions.append("s.exported = 1")

        where = " AND ".join(conditions)

        # params for WHERE, then the ORDER BY CASE, then LIMIT
        query_params = [*params, name, limit]

        rows = self._conn.execute(
            f"""SELECT s.*, f.file_path
                FROM symbols s
                JOIN indexed_files f ON s.file_id = f.id
                WHERE {where}
                ORDER BY
                    CASE WHEN LOWER(s.name) = LOWER(?) THEN 0 ELSE 1 END,
                    s.exported DESC,
                    s.name
                LIMIT ?""",
            query_params,
        ).fetchall()
        return [self._row_to_symbol(row) for row in rows]

    def find_definition(
        self,
        name: str,
        *,
        project_path: str | None = None,
        limit: int = 10,
    ) -> list[SymbolSearchResult]:
        """Find definitions of a symbol (exact match, case-insensitive)."""
        conditions = ["LOWER(s.name) = LOWER(?)"]
        params: list[Any] = [name]

        if project_path:
            conditions.append("s.project_path = ?")
            params.append(project_path)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"""SELECT s.*, f.file_path
                FROM symbols s
                JOIN indexed_files f ON s.file_id = f.id
                WHERE {where}
                ORDER BY s.exported DESC, s.kind, f.file_path
                LIMIT ?""",
            params,
        ).fetchall()
        return [self._row_to_symbol(row) for row in rows]

    def find_importers(
        self,
        module_or_symbol: str,
        *,
        project_path: str | None = None,
        limit: int = 20,
    ) -> list[ImportSearchResult]:
        """Find files that import a given module or symbol name."""
        conditions = ["(i.module LIKE ? OR i.names LIKE ?)"]
        params: list[Any] = [f"%{module_or_symbol}%", f"%{module_or_symbol}%"]

        if project_path:
            conditions.append("i.project_path = ?")
            params.append(project_path)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"""SELECT i.*, f.file_path
                FROM imports i
                JOIN indexed_files f ON i.file_id = f.id
                WHERE {where}
                ORDER BY f.file_path
                LIMIT ?""",
            params,
        ).fetchall()
        return [self._row_to_import(row) for row in rows]

    def search_symbols(
        self,
        query: str,
        *,
        project_path: str | None = None,
        kinds: set[str] | None = None,
        limit: int = 20,
    ) -> list[SymbolSearchResult]:
        """Keyword search across symbol names, signatures, and docstrings."""
        tokens = [t.lower() for t in query.split() if len(t) >= 2]
        if not tokens:
            return []

        conditions = []
        params: list[Any] = []

        # Build OR match across name/signature/docstring
        token_clauses = []
        for token in tokens:
            token_clauses.append(
                "(LOWER(s.name) LIKE ? OR LOWER(s.signature) LIKE ? OR LOWER(s.docstring) LIKE ?)"
            )
            pattern = f"%{token}%"
            params.extend([pattern, pattern, pattern])
        conditions.append("(" + " OR ".join(token_clauses) + ")")

        if project_path:
            conditions.append("s.project_path = ?")
            params.append(project_path)
        if kinds:
            placeholders = ", ".join("?" for _ in kinds)
            conditions.append(f"s.kind IN ({placeholders})")
            params.extend(kinds)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"""SELECT s.*, f.file_path
                FROM symbols s
                JOIN indexed_files f ON s.file_id = f.id
                WHERE {where}
                ORDER BY s.exported DESC, s.name
                LIMIT ?""",
            params,
        ).fetchall()
        return [self._row_to_symbol(row) for row in rows]

    def entry_points(
        self, *, project_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Return likely entry point files for a project."""
        conditions = ["is_entry_point = 1"]
        params: list[Any] = []
        if project_path:
            conditions.append("project_path = ?")
            params.append(project_path)
        where = " AND ".join(conditions)

        rows = self._conn.execute(
            f"SELECT * FROM indexed_files WHERE {where} ORDER BY file_path",
            params,
        ).fetchall()
        return [
            {
                "file_path": r["file_path"],
                "language": r["language"],
                "summary": r["summary"],
                "line_count": r["line_count"],
            }
            for r in rows
        ]

    def test_files(
        self, *, project_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Return test files for a project."""
        conditions = ["is_test_file = 1"]
        params: list[Any] = []
        if project_path:
            conditions.append("project_path = ?")
            params.append(project_path)
        where = " AND ".join(conditions)

        rows = self._conn.execute(
            f"SELECT * FROM indexed_files WHERE {where} ORDER BY file_path",
            params,
        ).fetchall()
        return [
            {
                "file_path": r["file_path"],
                "language": r["language"],
                "summary": r["summary"],
                "line_count": r["line_count"],
            }
            for r in rows
        ]

    def project_graph(
        self, project_path: str
    ) -> dict[str, Any]:
        """Return a summary overview of a project's code structure."""
        files = self._conn.execute(
            "SELECT * FROM indexed_files WHERE project_path = ? ORDER BY file_path",
            (project_path,),
        ).fetchall()

        languages: dict[str, int] = {}
        total_lines = 0
        entry_points_list = []
        test_files_list = []
        for f in files:
            lang = f["language"]
            languages[lang] = languages.get(lang, 0) + 1
            total_lines += f["line_count"]
            if f["is_entry_point"]:
                entry_points_list.append(f["file_path"])
            if f["is_test_file"]:
                test_files_list.append(f["file_path"])

        symbols = self._conn.execute(
            """SELECT kind, COUNT(*) as cnt
               FROM symbols WHERE project_path = ?
               GROUP BY kind ORDER BY cnt DESC""",
            (project_path,),
        ).fetchall()
        symbol_counts = {r["kind"]: r["cnt"] for r in symbols}

        # Top-level exported symbols
        top_symbols = self._conn.execute(
            """SELECT s.name, s.kind, s.signature, f.file_path
               FROM symbols s
               JOIN indexed_files f ON s.file_id = f.id
               WHERE s.project_path = ? AND s.exported = 1 AND s.parent IS NULL
               ORDER BY s.kind, s.name LIMIT 50""",
            (project_path,),
        ).fetchall()

        # External dependencies
        ext_deps = self._conn.execute(
            """SELECT module, COUNT(*) as cnt
               FROM imports
               WHERE project_path = ? AND is_relative = 0
               GROUP BY module ORDER BY cnt DESC LIMIT 30""",
            (project_path,),
        ).fetchall()

        return {
            "project_path": project_path,
            "files_indexed": len(files),
            "total_lines": total_lines,
            "languages": languages,
            "symbol_counts": symbol_counts,
            "entry_points": entry_points_list,
            "test_files": test_files_list,
            "top_symbols": [
                {
                    "name": r["name"],
                    "kind": r["kind"],
                    "signature": r["signature"],
                    "file_path": r["file_path"],
                }
                for r in top_symbols
            ],
            "external_dependencies": [
                {"module": r["module"], "import_count": r["cnt"]}
                for r in ext_deps
            ],
        }

    def stats(self) -> dict[str, Any]:
        """Return index statistics."""
        file_count = self._conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0]
        symbol_count = self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        import_count = self._conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        languages = {
            r["language"]: r["cnt"]
            for r in self._conn.execute(
                "SELECT language, COUNT(*) as cnt FROM indexed_files GROUP BY language"
            ).fetchall()
        }
        return {
            "files_indexed": file_count,
            "symbols": symbol_count,
            "imports": import_count,
            "languages": languages,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_symbol(self, row: sqlite3.Row) -> SymbolSearchResult:
        return SymbolSearchResult(
            name=row["name"],
            qualified_name=row["qualified_name"],
            kind=row["kind"],
            file_path=row["file_path"],
            line=row["line"],
            end_line=row["end_line"],
            parent=row["parent"],
            signature=row["signature"],
            docstring=row["docstring"],
            project_path=row["project_path"],
            exported=bool(row["exported"]),
        )

    def _row_to_import(self, row: sqlite3.Row) -> ImportSearchResult:
        return ImportSearchResult(
            module=row["module"],
            names=json.loads(row["names"]),
            file_path=row["file_path"],
            line=row["line"],
            project_path=row["project_path"],
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_index: CodeIndex | None = None


def get_code_index() -> CodeIndex:
    global _index
    if _index is None:
        _index = CodeIndex()
    return _index
