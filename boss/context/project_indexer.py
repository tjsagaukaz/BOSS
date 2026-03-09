from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from boss.context.codebase_scanner import CodebaseScanner
from boss.context.file_summarizer import FileSummarizer
from boss.memory.knowledge_graph import KnowledgeGraph
from boss.memory.memory_store import MemoryStore
from boss.memory.project_memory import ProjectMemoryStore
from boss.memory.style_profile import StyleProfileStore
from boss.memory.vector_index import VectorIndex
from boss.types import IndexedFile, ProjectIndexResult, ProjectMap, ProjectReference, utc_now_iso


class ProjectIndexer:
    def __init__(
        self,
        project_resolver: Callable[[str], ProjectReference],
        memory_store: MemoryStore,
        vector_index: VectorIndex,
        scanner: CodebaseScanner,
        file_summarizer: FileSummarizer,
        knowledge_graph: KnowledgeGraph | None = None,
        project_memory: ProjectMemoryStore | None = None,
        style_profile: StyleProfileStore | None = None,
    ) -> None:
        self.project_resolver = project_resolver
        self.memory_store = memory_store
        self.vector_index = vector_index
        self.scanner = scanner
        self.file_summarizer = file_summarizer
        self.knowledge_graph = knowledge_graph
        self.project_memory = project_memory
        self.style_profile = style_profile

    def index_project(
        self,
        project_name: str,
        force: bool = False,
        force_heuristic: bool = False,
    ) -> ProjectIndexResult:
        reference = self.project_resolver(project_name)
        project_name = reference.key
        project_root = Path(reference.root).resolve()
        if not project_root.exists():
            raise FileNotFoundError(f"Project '{project_name}' does not exist at {project_root}.")

        scan = self.scanner.scan(project_name=project_name, project_root=project_root)
        existing = {entry.file_path: entry for entry in self.memory_store.list_indexed_files(project_name, limit=20_000)}
        current_paths = {file.relative_path for file in scan.files}

        removed_files = sorted(set(existing) - current_paths)
        use_local_embeddings = force_heuristic
        for file_path in removed_files:
            self.memory_store.delete_indexed_file(project_name, file_path)
            self.memory_store.delete_code_summary(project_name, file_path)
            self.vector_index.delete_documents(project_name, file_path=file_path)

        changed_files = 0
        skipped_files = 0
        indexed_files = 0
        for scanned_file in scan.files:
            indexed_entry = existing.get(scanned_file.relative_path)
            if indexed_entry and not force and indexed_entry.content_hash == scanned_file.content_hash:
                skipped_files += 1
                continue

            summary = self.file_summarizer.summarize_file(scanned_file, force_heuristic=force_heuristic)
            indexed_files += 1
            changed_files += 1

            self.memory_store.upsert_indexed_file(
                project_name=project_name,
                file_path=summary.file_path,
                language=summary.language,
                content_hash=scanned_file.content_hash,
                size=scanned_file.size,
                modified_at=str(scanned_file.modified_at),
                summary=summary.summary,
                purpose=summary.purpose,
                symbols=summary.symbols,
                dependencies=summary.dependencies,
            )
            self.memory_store.upsert_code_summary(
                project_name=project_name,
                file_path=summary.file_path,
                language=summary.language,
                summary=summary.summary,
                force_local_embedding=use_local_embeddings,
            )

            self.vector_index.delete_documents(project_name, file_path=summary.file_path)
            self.vector_index.add_document(
                text=self._summary_document_text(summary),
                metadata={
                    "project_name": project_name,
                    "kind": "file_summary",
                    "file_path": summary.file_path,
                    "language": summary.language,
                    "dependencies": summary.dependencies,
                },
                force_local_embedding=use_local_embeddings,
            )
            for index, snippet in enumerate(summary.snippets[:3]):
                self.vector_index.add_document(
                    text=snippet,
                    metadata={
                        "project_name": project_name,
                        "kind": "code_snippet",
                        "file_path": summary.file_path,
                        "language": summary.language,
                        "snippet_index": index,
                        "summary": summary.summary,
                    },
                    force_local_embedding=use_local_embeddings,
                )

        indexed_entries = self.memory_store.list_indexed_files(project_name, limit=20_000)
        project_map = self._build_project_map(project_name, scan, indexed_entries)
        self.memory_store.upsert_project(
            name=project_name,
            path=str(project_root),
            summary=project_map.overview,
            metadata={
                "file_count": len(scan.files),
                "languages": project_map.languages,
                "important_files": project_map.key_files,
                "indexed_at": project_map.indexed_at,
                "main_modules": project_map.main_modules,
                "dependencies": project_map.dependencies,
            },
        )
        self.memory_store.upsert_project_map(project_name, project_map)
        self.memory_store.delete_memory_entries(project_name, category="architecture")
        self.memory_store.add_memory_entry(
            project_name=project_name,
            category="architecture",
            content=project_map.overview,
            metadata={
                "main_modules": project_map.main_modules,
                "entry_points": project_map.entry_points,
                "key_files": project_map.key_files,
                "dependencies": project_map.dependencies,
            },
            force_local_embedding=use_local_embeddings,
        )
        self.vector_index.delete_documents(project_name, kind="architecture_note")
        self.vector_index.add_document(
            text=self._project_map_document_text(project_map),
            metadata={
                "project_name": project_name,
                "kind": "architecture_note",
                "file_path": "__project_map__",
            },
            force_local_embedding=use_local_embeddings,
        )
        project_profile = None
        if self.project_memory is not None:
            project_profile = self.project_memory.analyze_project(
                project_name=project_name,
                project_root=project_root,
                project_map=project_map,
                indexed_files=indexed_entries,
            )
        if self.style_profile is not None and (force or changed_files > 0 or self.style_profile.get_profile(project_name) is None):
            self.style_profile.analyze_project(
                project_name=project_name,
                project_root=project_root,
                indexed_files=indexed_entries,
                project_map=project_map,
            )
        if self.knowledge_graph is not None:
            self.knowledge_graph.rebuild_project_graph(
                project_name=project_name,
                project_map=project_map,
                indexed_files=indexed_entries,
                project_profile=project_profile,
            )
            if self.project_memory is not None:
                related = [item["project_name"] for item in self.knowledge_graph.related_projects(project_name, limit=12)]
                self.project_memory.update_related_projects(project_name, related)

        return ProjectIndexResult(
            project_name=project_name,
            total_files=len(scan.files),
            indexed_files=indexed_files,
            changed_files=changed_files,
            removed_files=len(removed_files),
            skipped_files=skipped_files,
            project_map=project_map,
        )

    def _build_project_map(
        self,
        project_name: str,
        scan,
        indexed_entries: list[IndexedFile],
    ) -> ProjectMap:
        key_files = list(dict.fromkeys(scan.important_files + scan.entry_points))[:15]
        if not key_files:
            key_files = [entry.file_path for entry in indexed_entries[:10]]

        dependency_candidates: set[str] = set(scan.dependencies)
        for entry in indexed_entries[:100]:
            dependency_candidates.update(entry.dependencies)

        overview = (
            f"Project {project_name} has {len(scan.files)} indexed files. "
            f"Languages: {self._format_languages(scan.languages)}. "
            f"Main modules: {', '.join(scan.main_modules[:6]) or 'none detected'}. "
            f"Entry points: {', '.join(scan.entry_points[:6]) or 'none detected'}. "
            f"Key files: {', '.join(key_files[:6]) or 'none detected'}. "
            f"Dependencies: {', '.join(sorted(dependency_candidates)[:10]) or 'none detected'}."
        )
        project_map = ProjectMap(
            name=project_name,
            overview=overview,
            languages=scan.languages,
            main_modules=scan.main_modules,
            entry_points=scan.entry_points,
            key_files=key_files,
            dependencies=sorted(dependency_candidates),
            indexed_at=utc_now_iso(),
        )
        return project_map

    def _summary_document_text(self, summary) -> str:
        payload = {
            "file": summary.file_path,
            "language": summary.language,
            "purpose": summary.purpose,
            "summary": summary.summary,
            "symbols": summary.symbols[:10],
            "dependencies": summary.dependencies[:10],
        }
        return json.dumps(payload, indent=2)

    def _project_map_document_text(self, project_map: ProjectMap) -> str:
        payload = {
            "project": project_map.name,
            "overview": project_map.overview,
            "languages": project_map.languages,
            "main_modules": project_map.main_modules,
            "entry_points": project_map.entry_points,
            "key_files": project_map.key_files,
            "dependencies": project_map.dependencies,
        }
        return json.dumps(payload, indent=2)

    def _format_languages(self, languages: dict[str, int]) -> str:
        if not languages:
            return "none detected"
        return ", ".join(f"{name} ({count})" for name, count in sorted(languages.items(), key=lambda item: (-item[1], item[0]))[:6])
