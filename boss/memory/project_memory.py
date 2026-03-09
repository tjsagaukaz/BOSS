from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from boss.types import IndexedFile, ProjectMap, ProjectMemoryProfile


class ProjectMemoryStore:
    FRAMEWORK_ALIASES = {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "pytest": "pytest",
        "express": "Express",
        "next": "Next.js",
        "next.js": "Next.js",
        "react": "React",
        "vite": "Vite",
        "node": "Node.js",
        "nestjs": "NestJS",
        "vue": "Vue",
        "svelte": "Svelte",
        "tailwindcss": "Tailwind CSS",
        "swiftui": "SwiftUI",
        "uikit": "UIKit",
        "tokio": "Tokio",
        "actix_web": "Actix Web",
        "axum": "Axum",
        "bevy": "Bevy",
        "gin": "Gin",
        "fiber": "Fiber",
        "echo": "Echo",
    }

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def analyze_project(
        self,
        project_name: str,
        project_root: str | Path,
        project_map: ProjectMap,
        indexed_files: list[IndexedFile],
        related_projects: list[str] | None = None,
    ) -> ProjectMemoryProfile:
        root = Path(project_root)
        dependencies = {item.lower() for item in project_map.dependencies}
        for entry in indexed_files[:200]:
            dependencies.update(dependency.lower() for dependency in entry.dependencies[:12])

        frameworks = sorted(
            {
                label
                for dependency, label in self.FRAMEWORK_ALIASES.items()
                if dependency in dependencies
            }
        )

        description = self._project_description(root, fallback=project_map.overview)
        primary_language = self._primary_language(project_map.languages)
        coding_patterns = self._coding_patterns(project_map, indexed_files)
        architecture = self._architecture_summary(project_map, frameworks, coding_patterns, primary_language)

        profile = ProjectMemoryProfile(
            project_name=project_name,
            description=description,
            primary_language=primary_language,
            languages=list(project_map.languages.keys()),
            frameworks=frameworks,
            architecture=architecture,
            key_modules=project_map.main_modules[:10],
            coding_patterns=coding_patterns[:12],
            related_projects=sorted(set(related_projects or [])),
        )
        self.upsert_profile(profile)
        return profile

    def upsert_profile(self, profile: ProjectMemoryProfile) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_memory (
                    project_name, description, primary_language, languages, frameworks,
                    architecture, key_modules, coding_patterns, related_projects, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name) DO UPDATE SET
                    description = excluded.description,
                    primary_language = excluded.primary_language,
                    languages = excluded.languages,
                    frameworks = excluded.frameworks,
                    architecture = excluded.architecture,
                    key_modules = excluded.key_modules,
                    coding_patterns = excluded.coding_patterns,
                    related_projects = excluded.related_projects,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    profile.project_name,
                    profile.description,
                    profile.primary_language,
                    json.dumps(profile.languages),
                    json.dumps(profile.frameworks),
                    profile.architecture,
                    json.dumps(profile.key_modules),
                    json.dumps(profile.coding_patterns),
                    json.dumps(profile.related_projects),
                ),
            )

    def update_related_projects(self, project_name: str, related_projects: list[str]) -> None:
        profile = self.get_profile(project_name)
        if profile is None:
            return
        profile.related_projects = sorted(set(related_projects))
        self.upsert_profile(profile)

    def get_profile(self, project_name: str) -> ProjectMemoryProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT project_name, description, primary_language, languages, frameworks,
                       architecture, key_modules, coding_patterns, related_projects, updated_at
                FROM project_memory
                WHERE project_name = ?
                """,
                (project_name,),
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def delete_profile(self, project_name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM project_memory WHERE project_name = ?", (project_name,))

    def list_profiles(self, limit: int = 50) -> list[ProjectMemoryProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT project_name, description, primary_language, languages, frameworks,
                       architecture, key_modules, coding_patterns, related_projects, updated_at
                FROM project_memory
                ORDER BY project_name ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def _project_description(self, project_root: Path, fallback: str) -> str:
        readme_path = project_root / "README.md"
        if not readme_path.exists():
            return fallback
        content = readme_path.read_text(encoding="utf-8", errors="replace")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        filtered: list[str] = []
        for line in lines:
            if line.startswith("#"):
                continue
            if line.startswith("```"):
                continue
            filtered.append(line)
            if len(" ".join(filtered)) > 280:
                break
        description = " ".join(filtered)[:320].strip()
        return description or fallback

    def _primary_language(self, languages: dict[str, int]) -> str:
        if not languages:
            return "Unknown"
        return sorted(languages.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def _coding_patterns(self, project_map: ProjectMap, indexed_files: list[IndexedFile]) -> list[str]:
        patterns: list[str] = []
        file_paths = [entry.file_path.lower() for entry in indexed_files[:200]]
        joined_paths = "\n".join(file_paths)
        if re.search(r"(^|/)(api|routes?|router)(/|$)", joined_paths):
            patterns.append("API routing modules")
        if re.search(r"(^|/)(service|services)(/|$)", joined_paths):
            patterns.append("service-layer modules")
        if re.search(r"(^|/)(worker|workers|jobs)(/|$)", joined_paths):
            patterns.append("background workers")
        if re.search(r"(^|/)(auth|authentication)(/|$)", joined_paths):
            patterns.append("auth-focused modules")
        if re.search(r"(^|/)(billing|payments?)(/|$)", joined_paths):
            patterns.append("billing and payment flows")
        if project_map.entry_points:
            patterns.append("explicit application entry points")
        if not patterns:
            patterns.append("modular source files")
        return list(dict.fromkeys(patterns))

    def _architecture_summary(
        self,
        project_map: ProjectMap,
        frameworks: list[str],
        coding_patterns: list[str],
        primary_language: str,
    ) -> str:
        modules = ", ".join(project_map.main_modules[:6]) or "no clear modules yet"
        entries = ", ".join(project_map.entry_points[:4]) or "no clear entry points yet"
        framework_text = ", ".join(frameworks[:5]) or "no framework detected"
        pattern_text = ", ".join(coding_patterns[:4]) or "no consistent patterns detected"
        return (
            f"{primary_language} project with modules {modules}. "
            f"Entry points: {entries}. "
            f"Frameworks: {framework_text}. "
            f"Common patterns: {pattern_text}."
        )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_memory (
                    project_name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    primary_language TEXT NOT NULL,
                    languages TEXT NOT NULL DEFAULT '[]',
                    frameworks TEXT NOT NULL DEFAULT '[]',
                    architecture TEXT NOT NULL DEFAULT '',
                    key_modules TEXT NOT NULL DEFAULT '[]',
                    coding_patterns TEXT NOT NULL DEFAULT '[]',
                    related_projects TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_profile(self, row: sqlite3.Row) -> ProjectMemoryProfile:
        return ProjectMemoryProfile(
            project_name=row["project_name"],
            description=row["description"],
            primary_language=row["primary_language"],
            languages=json.loads(row["languages"] or "[]"),
            frameworks=json.loads(row["frameworks"] or "[]"),
            architecture=row["architecture"],
            key_modules=json.loads(row["key_modules"] or "[]"),
            coding_patterns=json.loads(row["coding_patterns"] or "[]"),
            related_projects=json.loads(row["related_projects"] or "[]"),
            updated_at=row["updated_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
