from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from boss.types import IndexedFile, ProjectMap, StyleProfile


class StyleProfileStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def analyze_project(
        self,
        project_name: str,
        project_root: str | Path,
        indexed_files: list[IndexedFile],
        project_map: ProjectMap | None = None,
    ) -> StyleProfile:
        root = Path(project_root)
        contents = self._sample_contents(root, indexed_files)
        indentation = self._detect_indentation(contents)
        naming = self._detect_naming(indexed_files)
        code_structure = self._detect_code_structure(indexed_files, project_map)
        test_style = self._detect_test_style(indexed_files, contents)
        error_handling = self._detect_error_handling(contents)
        notes = [
            f"Indentation prefers {indentation}.",
            f"Naming leans toward {', '.join(naming[:2]) or 'mixed identifiers'}.",
            f"Tests look {test_style.lower()}.",
            f"Error handling is {error_handling.lower()}.",
        ]
        profile = StyleProfile(
            project_name=project_name,
            indentation=indentation,
            naming_conventions=naming,
            code_structure=code_structure,
            test_style=test_style,
            error_handling_style=error_handling,
            notes=notes,
        )
        self.upsert_profile(profile)
        return profile

    def upsert_profile(self, profile: StyleProfile) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO style_profile (
                    project_name, indentation, naming_conventions, code_structure,
                    test_style, error_handling_style, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name) DO UPDATE SET
                    indentation = excluded.indentation,
                    naming_conventions = excluded.naming_conventions,
                    code_structure = excluded.code_structure,
                    test_style = excluded.test_style,
                    error_handling_style = excluded.error_handling_style,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    profile.project_name,
                    profile.indentation,
                    json.dumps(profile.naming_conventions),
                    profile.code_structure,
                    profile.test_style,
                    profile.error_handling_style,
                    json.dumps(profile.notes),
                ),
            )

    def get_profile(self, project_name: str) -> StyleProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT project_name, indentation, naming_conventions, code_structure,
                       test_style, error_handling_style, notes, updated_at
                FROM style_profile
                WHERE project_name = ?
                """,
                (project_name,),
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def delete_profile(self, project_name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM style_profile WHERE project_name = ?", (project_name,))

    def get_effective_profile(self, project_name: str | None = None) -> StyleProfile | None:
        if project_name:
            profile = self.get_profile(project_name)
            if profile is not None:
                return profile
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT project_name, indentation, naming_conventions, code_structure,
                       test_style, error_handling_style, notes, updated_at
                FROM style_profile
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def list_profiles(self, limit: int = 50) -> list[StyleProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT project_name, indentation, naming_conventions, code_structure,
                       test_style, error_handling_style, notes, updated_at
                FROM style_profile
                ORDER BY project_name ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def _sample_contents(self, root: Path, indexed_files: list[IndexedFile]) -> list[str]:
        contents: list[str] = []
        for entry in indexed_files[:40]:
            resolved = (root / entry.file_path).resolve()
            if not resolved.exists() or not resolved.is_file():
                continue
            contents.append(resolved.read_text(encoding="utf-8", errors="replace"))
        return contents

    def _detect_indentation(self, contents: list[str]) -> str:
        counts = Counter()
        for content in contents:
            for line in content.splitlines():
                if not line.strip():
                    continue
                if line.startswith("\t"):
                    counts["tabs"] += 1
                    continue
                match = re.match(r"^( +)\S", line)
                if not match:
                    continue
                indent = len(match.group(1))
                if indent % 4 == 0:
                    counts["4 spaces"] += 1
                elif indent % 2 == 0:
                    counts["2 spaces"] += 1
                else:
                    counts[f"{indent} spaces"] += 1
        if not counts:
            return "4 spaces"
        return counts.most_common(1)[0][0]

    def _detect_naming(self, indexed_files: list[IndexedFile]) -> list[str]:
        counts = Counter()
        for entry in indexed_files[:150]:
            for symbol in entry.symbols[:12]:
                if re.match(r"^[a-z]+(?:_[a-z0-9]+)+$", symbol):
                    counts["snake_case"] += 1
                elif re.match(r"^[a-z]+(?:[A-Z][a-z0-9]+)+$", symbol):
                    counts["camelCase"] += 1
                elif re.match(r"^[A-Z][A-Za-z0-9]+$", symbol):
                    counts["PascalCase"] += 1
            file_name = Path(entry.file_path).stem
            if "-" in file_name:
                counts["kebab-case files"] += 1
            elif "_" in file_name:
                counts["snake_case files"] += 1
        if not counts:
            return ["mixed"]
        return [name for name, _count in counts.most_common(3)]

    def _detect_code_structure(self, indexed_files: list[IndexedFile], project_map: ProjectMap | None) -> str:
        paths = [entry.file_path.lower() for entry in indexed_files[:150]]
        modules = project_map.main_modules if project_map else []
        if any("/service" in path or path.startswith("service") for path in paths):
            return "service-oriented modules with separated responsibilities"
        if any("/routes" in path or "/router" in path or path.startswith("api") for path in paths):
            return "layered API modules around routes and handlers"
        if any("/workers" in path or "/jobs" in path for path in paths):
            return "background-worker oriented structure"
        if modules:
            return f"modular structure centered on {', '.join(modules[:4])}"
        return "small focused modules"

    def _detect_test_style(self, indexed_files: list[IndexedFile], contents: list[str]) -> str:
        paths = [entry.file_path.lower() for entry in indexed_files[:150]]
        blob = "\n".join(contents[:20])
        if any("/tests/" in path or path.startswith("tests/") or path.endswith("_test.py") or "/test_" in path for path in paths):
            if "pytest" in blob:
                return "pytest-style function tests"
            return "Python test modules"
        if any(path.endswith(".spec.ts") or path.endswith(".test.ts") or path.endswith(".test.js") for path in paths):
            if "describe(" in blob or "it(" in blob:
                return "Jest-style describe/it tests"
            return "JavaScript test files"
        if any(path.endswith("_test.go") for path in paths):
            return "Go table-driven tests"
        if any(path.endswith(".rs") and "/tests/" in path for path in paths):
            return "Rust unit and integration tests"
        return "light or implicit test coverage"

    def _detect_error_handling(self, contents: list[str]) -> str:
        blob = "\n".join(contents[:20])
        patterns = Counter()
        patterns["exceptions"] += len(re.findall(r"\braise\b|\bthrow\b|HTTPException", blob))
        patterns["try_except"] += len(re.findall(r"\btry\b|\bexcept\b|\bcatch\b", blob))
        patterns["go_errors"] += len(re.findall(r"if err != nil|return err", blob))
        patterns["rust_results"] += len(re.findall(r"Result<|\?\s*$|unwrap_or", blob, flags=re.MULTILINE))
        winner = patterns.most_common(1)[0][0] if patterns else "exceptions"
        mapping = {
            "exceptions": "exception-driven",
            "try_except": "guarded with try/except blocks",
            "go_errors": "explicit error returns",
            "rust_results": "result-oriented propagation",
        }
        return mapping.get(winner, "exception-driven")

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS style_profile (
                    project_name TEXT PRIMARY KEY,
                    indentation TEXT NOT NULL,
                    naming_conventions TEXT NOT NULL DEFAULT '[]',
                    code_structure TEXT NOT NULL DEFAULT '',
                    test_style TEXT NOT NULL DEFAULT '',
                    error_handling_style TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_profile(self, row: sqlite3.Row) -> StyleProfile:
        return StyleProfile(
            project_name=row["project_name"],
            indentation=row["indentation"],
            naming_conventions=json.loads(row["naming_conventions"] or "[]"),
            code_structure=row["code_structure"],
            test_style=row["test_style"],
            error_handling_style=row["error_handling_style"],
            notes=json.loads(row["notes"] or "[]"),
            updated_at=row["updated_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
