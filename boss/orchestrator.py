from __future__ import annotations

from collections import deque
import json
import logging
import os
import re
import signal
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from rich.console import Group
from rich.console import Console

from boss.agents.architect_agent import ArchitectAgent
from boss.artifacts import ArtifactStore, RunReplayManager
from boss.agents.auditor_agent import AuditorAgent
from boss.agents.engineer_agent import EngineerAgent
from boss.configuration import load_config, load_runtime_config
from boss.conversation import ConversationHistoryStore, ConversationRouter
from boss.context.codebase_scanner import CodebaseScanner
from boss.context.editor_state import EditorStateStore
from boss.context.file_summarizer import FileSummarizer
from boss.context.project_indexer import ProjectIndexer
from boss.context.project_loader import ProjectLoader
from boss.dashboard.task_dashboard import TaskDashboard
from boss.engine.autonomous_loop import AutonomousDevelopmentLoop
from boss.eval import BenchmarkSuiteRunner, EvaluationHarness, EvaluationStore, ExternalRepoSync
from boss.evolution.plugin_generator import PluginGenerator
from boss.evolution.prompt_optimizer import PromptOptimizer
from boss.ide.vscode_controller import VSCodeController
from boss.lab import BenchmarkRunner, ExperimentManager, LabRegistry, ResultAnalyzer, VariantGenerator
from boss.learning.task_analyzer import TaskAnalyzer
from boss.memory.context_retriever import ContextRetriever
from boss.memory.embeddings import EmbeddingService
from boss.memory.knowledge_graph import KnowledgeGraph
from boss.memory.memory_store import MemoryStore
from boss.memory.project_memory import ProjectMemoryStore
from boss.memory.solution_library import SolutionLibrary
from boss.memory.style_profile import StyleProfileStore
from boss.memory.task_history import TaskHistoryStore
from boss.memory.vector_index import VectorIndex
from boss.models.local_model_manager import LocalModelManager
from boss.observability import HealthReporter, MetricsReporter, RunLedger
from boss.plugins.plugin_manager import PluginManager
from boss.portfolio import PortfolioManager
from boss.project_brain import ProjectBrainStore
from boss.research import ResearchEngine
from boss.router import ModelRouter
from boss.runtime import call_with_timeout
from boss.runtime.permissions import PermissionManager
from boss.swarm.swarm_manager import SwarmManager
from boss.tools import Toolbox
from boss.tools.git_tools import GitTools
from boss.voice.voice_interface import VoiceInterface
from boss.mcp import MCPConnectorRegistry
from boss.workspace import (
    EditorListener,
    GitListener,
    TerminalListener,
    TestListener,
    WorkspaceRootsRegistry,
    WorkspaceStateStore,
)
from boss.types import (
    AuditResult,
    AutonomousBuildResult,
    AgentResult,
    EvalRunResult,
    MemoryEntry,
    ProjectBrain,
    ProjectContext,
    ProjectIndexResult,
    ProjectMap,
    ProjectMemoryProfile,
    ProjectReference,
    SolutionEntry,
    StyleProfile,
    WorkflowResult,
    utc_now_iso,
)


class BOSSOrchestrator:
    ENGINEER_TIMEOUT_SECONDS = 120
    ENGINEER_MAX_TOOL_ROUNDS = 8
    WORKSPACE_ALIASES = {"__workspace__", "workspace", "all", "none"}

    def __init__(self, root_dir: str | Path, console: Console | None = None) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.roots_registry = WorkspaceRootsRegistry(self.root_dir / "data" / "workspace_roots.yaml")
        explicit_workspace_root = os.environ.get("BOSS_WORKSPACE_ROOT")
        self.workspace_root = (
            Path(explicit_workspace_root).expanduser().resolve()
            if explicit_workspace_root
            else self.roots_registry.primary_search_root()
        )
        self._ad_hoc_projects: dict[str, ProjectReference] = {}
        self.state_path = self.root_dir / ".boss_state.json"
        self.console = console or Console()
        self.logger = logging.getLogger(self.__class__.__name__)
        self._runtime_lock = threading.RLock()
        self._agent_activity: dict[str, dict[str, Any]] = {}
        self._timeline: deque[dict[str, Any]] = deque(maxlen=300)
        self._timeline_counter = 0
        self.artifact_store = ArtifactStore(self.root_dir / "artifacts")
        self.run_replay = RunReplayManager(self.artifact_store, self)
        self.config = load_config(self.root_dir / "config" / "models.yaml")
        self.runtime_config = load_runtime_config(self.root_dir / "config" / "runtime.yaml")
        embeddings_cfg = self.config.embeddings
        self.embeddings = EmbeddingService(
            provider=str(embeddings_cfg.get("provider", "local")),
            model=str(embeddings_cfg.get("model", "hashed-256")),
            dimensions=int(embeddings_cfg.get("dimensions", 256)),
        )
        db_path = self._memory_db_path()
        self.memory_store = MemoryStore(db_path, self.embeddings)
        self.vector_index = VectorIndex(db_path)
        self.task_history = TaskHistoryStore(db_path)
        self.evaluation_store = EvaluationStore(db_path)
        self.lab_registry = LabRegistry(db_path)
        self.project_brain = ProjectBrainStore(
            self.root_dir / "data" / "project_brains",
            task_history=self.task_history,
            evaluation_store=self.evaluation_store,
        )
        self.knowledge_graph = KnowledgeGraph(db_path)
        self.project_memory = ProjectMemoryStore(db_path)
        self.solution_library = SolutionLibrary(db_path, self.embeddings)
        self.style_profile = StyleProfileStore(db_path)
        self.local_model_manager = LocalModelManager(db_path)
        self.task_analyzer = TaskAnalyzer(db_path, self.task_history, self.solution_library, self.knowledge_graph)
        self.prompt_optimizer = PromptOptimizer(self.root_dir, db_path, self.task_analyzer, self.style_profile)
        self.editor_state = EditorStateStore(self.root_dir / "data" / "editor_state.json")
        self.workspace_state = WorkspaceStateStore(self.root_dir / "data" / "workspace_state.json")
        self.permission_manager = PermissionManager(self.root_dir / "data" / "permissions_policy.yaml")
        self.mcp_registry = MCPConnectorRegistry(self.root_dir / "data" / "mcp_connectors.yaml")
        self.editor_listener = EditorListener(self.workspace_state)
        self.terminal_listener = TerminalListener(self.workspace_state)
        self.test_listener = TestListener(self.workspace_state)
        self.git_listener = GitListener(self.workspace_state)
        self.vscode_controller = VSCodeController()
        self.plugin_manager = PluginManager(self.root_dir / "boss" / "plugins")
        self.plugin_generator = PluginGenerator(self.root_dir / "boss" / "plugins")
        self.task_dashboard = TaskDashboard()
        self.router = ModelRouter(self.config, local_model_manager=self.local_model_manager)
        self.scanner = CodebaseScanner()
        self.file_summarizer = FileSummarizer(llm_client=self.router.client_for_role("engineer"))
        self.project_indexer = ProjectIndexer(
            project_resolver=self.resolve_project_reference,
            memory_store=self.memory_store,
            vector_index=self.vector_index,
            scanner=self.scanner,
            file_summarizer=self.file_summarizer,
            knowledge_graph=self.knowledge_graph,
            project_memory=self.project_memory,
            style_profile=self.style_profile,
        )
        self.context_retriever = ContextRetriever(
            memory_store=self.memory_store,
            vector_index=self.vector_index,
            task_history=self.task_history,
            knowledge_graph=self.knowledge_graph,
            project_memory=self.project_memory,
            solution_library=self.solution_library,
            style_profile=self.style_profile,
            embeddings=self.embeddings,
        )
        self.project_loader = ProjectLoader(
            project_resolver=self.resolve_project_reference,
            project_discovery=self.roots_registry.discover_projects,
            memory_store=self.memory_store,
            vector_index=self.vector_index,
            project_indexer=self.project_indexer,
            editor_state=self.editor_state,
            context_retriever=self.context_retriever,
            workspace_state=self.workspace_state,
            git_listener=self.git_listener,
            project_brain=self.project_brain,
        )
        self.architect = ArchitectAgent(self.router, self.root_dir)
        self.engineer = EngineerAgent(self.router, self.root_dir)
        self.auditor = AuditorAgent(self.router, self.root_dir)
        self.autonomous_loop = AutonomousDevelopmentLoop(
            root_dir=self.root_dir,
            project_loader=self.project_loader,
            project_indexer=self.project_indexer,
            architect=self.architect,
            engineer=self.engineer,
            auditor=self.auditor,
            embeddings=self.embeddings,
            task_history=self.task_history,
            editor_state=self.editor_state,
            plugin_manager=self.plugin_manager,
            vscode_controller=self.vscode_controller,
            dashboard=self.task_dashboard,
            console=self.console,
            editor_listener=self.editor_listener,
            terminal_listener=self.terminal_listener,
            test_listener=self.test_listener,
            git_listener=self.git_listener,
            runtime_timeouts=self.runtime_config.timeouts,
            full_access=self.permission_manager.full_access_enabled(),
        )
        self.swarm_manager = SwarmManager(self)
        self.voice_interface = VoiceInterface()
        self.conversation_history = ConversationHistoryStore(db_path)
        self.conversation_router = ConversationRouter(
            orchestrator=self,
            history_store=self.conversation_history,
            router=self.router,
            root_dir=self.root_dir,
        )
        self.research_engine = ResearchEngine(
            router=self.router,
            roots_registry=self.roots_registry,
            permission_manager=self.permission_manager,
            workspace_root=self.workspace_root,
        )
        self.portfolio_manager = PortfolioManager(
            roots_registry=self.roots_registry,
            project_brain_store=self.project_brain,
            memory_store=self.memory_store,
        )
        self.evaluator = EvaluationHarness(self, self.evaluation_store, artifact_store=self.artifact_store)
        self.benchmark_runner = BenchmarkSuiteRunner(self)
        self.health_reporter = HealthReporter(
            self.task_history,
            self.evaluation_store,
            self.artifact_store,
            self.workspace_state,
        )
        self.metrics_reporter = MetricsReporter(
            self.task_history,
            self.evaluation_store,
            self.artifact_store,
            self.lab_registry,
        )
        self.run_ledger = RunLedger(
            self.artifact_store,
            self.evaluation_store,
            self.task_history,
            self.lab_registry,
            self.run_replay,
        )
        self.external_repo_sync = ExternalRepoSync(self.root_dir / "projects")
        self.experiment_manager = ExperimentManager(
            root_dir=self.root_dir,
            orchestrator=self,
            registry=self.lab_registry,
            variant_generator=VariantGenerator(),
            benchmark_runner=BenchmarkRunner(self.evaluator),
            result_analyzer=ResultAnalyzer(),
        )
        self._reconcile_runtime_state()

    def available_projects(self, include_internal: bool = False) -> list[str]:
        return [item["key"] for item in self.available_project_catalog(include_internal=include_internal)]

    def available_project_catalog(self, include_internal: bool = False) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for reference in self.roots_registry.discover_projects():
            if not include_internal and self._is_internal_project_reference(reference):
                continue
            catalog.append(self._project_reference_record(reference))
        return catalog

    def workspace_roots_snapshot(self, include_internal: bool = False) -> dict[str, Any]:
        all_projects = [self._project_reference_record(reference) for reference in self.roots_registry.discover_projects()]
        visible_projects = [project for project in all_projects if include_internal or not bool(project.get("internal"))]
        return {
            "primary_root": str(self.workspace_root),
            "search_roots": [str(path) for path in self._search_roots(include_root_dir=False)],
            "roots": self.roots_registry.describe(),
            "projects": visible_projects,
            "hidden_projects": [project for project in all_projects if bool(project.get("internal"))],
            "hidden_project_count": len([project for project in all_projects if bool(project.get("internal"))]),
        }

    def set_active_project(self, project_name: str) -> ProjectContext:
        normalized = self._normalize_project_name(project_name)
        if normalized is None:
            return self.clear_active_project()
        reference = self.resolve_project_reference(normalized)
        context = self.project_loader.scan_project(reference.key)
        self._update_state(active_project=reference.key)
        self.workspace_state.set_active_project(reference.key)
        return context

    def clear_active_project(self) -> ProjectContext:
        self._update_state(active_project=None)
        self.workspace_state.set_active_project(None)
        return self.workspace_context()

    def get_active_project_name(self) -> str | None:
        return self._normalize_project_name(self._load_state().get("active_project"))

    def get_current_task_id(self) -> int | None:
        current = self._load_state().get("current_task_id")
        return int(current) if current is not None else None

    def scan_project(self, project_name: str | None = None) -> ProjectContext:
        name = self._normalize_project_name(project_name) or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        return self.project_loader.scan_project(name)

    def index_project(self, project_name: str | None = None, force: bool = False) -> ProjectIndexResult:
        name = self._normalize_project_name(project_name) or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        return self.project_indexer.index_project(project_name=name, force=force)

    def project_map(self, project_name: str | None = None) -> ProjectMap:
        name = self._normalize_project_name(project_name) or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        self.project_indexer.index_project(project_name=name, force=False)
        project_map = self.memory_store.get_project_map(name)
        if project_map is None:
            raise RuntimeError(f"No project map is available for '{name}'.")
        return project_map

    def cached_project_map(self, project_name: str | None = None) -> ProjectMap | None:
        name = self._normalize_project_name(project_name) or self.get_active_project_name()
        if not name:
            return None
        return self.memory_store.get_project_map(name)

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        project_name = self.get_active_project_name()
        scope_key = project_name or "__workspace__"
        cached = self.editor_state.get_cached_search(scope_key, query)
        if cached:
            return cached[:limit]
        if project_name:
            self.project_indexer.index_project(project_name=project_name, force=False)
            results = self.vector_index.semantic_search(query=query, project_name=project_name, limit=limit)
        else:
            results = self._workspace_search(query=query, limit=limit)
        self.editor_state.cache_search(scope_key, query, results)
        return results

    def open_file(self, path: str, line: int | None = None) -> dict[str, Any]:
        target = self._resolve_workspace_path(path)
        project_name = self.get_active_project_name() or "__workspace__"
        relative = self._display_path(target)
        self.editor_state.set_active_file(project_name, relative)
        self.editor_listener.file_opened(project_name, relative)
        return self.vscode_controller.open_file(target, line=line)

    def create_workspace_folder(
        self,
        path: str | None = None,
        *,
        switch_to: bool = True,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        requested = str(path or "").strip()
        if not requested:
            seed = project_name or self.get_active_project_name() or "new-project"
            requested = self._slugify_name(seed)

        candidate = Path(requested).expanduser()
        if not candidate.is_absolute():
            candidate = (self.workspace_root / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if not self.permission_manager.write_allowed(candidate):
            raise PermissionError(f"Write not allowed for '{candidate}'.")
        if candidate.exists() and not candidate.is_dir():
            raise NotADirectoryError(f"Path exists but is not a directory: {candidate}")

        existed = candidate.exists()
        candidate.mkdir(parents=True, exist_ok=True)

        context = None
        if switch_to:
            try:
                context = self.set_active_project(str(candidate))
            except Exception:
                self._update_state(active_project=str(candidate))
                self.workspace_state.set_active_project(str(candidate))

        relative = self._display_path(candidate)
        self.workspace_state.record_event(
            "__workspace__",
            "workspace_folder_created",
            {
                "path": str(candidate),
                "relative_path": relative,
                "switched": switch_to,
                "created": not existed,
            },
        )

        return {
            "path": str(candidate),
            "relative_path": relative,
            "created": not existed,
            "switched": switch_to,
            "project_name": context.name if context is not None else (str(candidate) if switch_to else None),
            "message": (
                f"Created {relative} and switched us there."
                if switch_to and not existed
                else f"Switched us to {relative}."
                if switch_to
                else f"Created {relative}."
            ),
        }

    def jump_to_symbol(self, symbol_name: str) -> dict[str, Any]:
        project_name = self.get_active_project_name()
        if project_name is None:
            toolbox = Toolbox(
                workspace_root=self.workspace_root,
                project_root=self.workspace_root,
                project_name="__workspace__",
                embeddings=self.embeddings,
                editor_state=self.editor_state,
                vscode_controller=self.vscode_controller,
                plugin_manager=self.plugin_manager,
                full_access=self.permission_manager.full_access_enabled(),
                require_confirmation=False,
                editor_listener=self.editor_listener,
                terminal_listener=self.terminal_listener,
                git_listener=self.git_listener,
                test_listener=self.test_listener,
            )
            return toolbox.editor_tools.jump_to_symbol(symbol_name)

        context = self.load_active_project(task_hint=symbol_name, auto_index=True)
        toolbox = self._toolbox(context, auto_approve=True)
        result = toolbox.editor_tools.jump_to_symbol(symbol_name)
        if result.get("found") is False:
            semantic = self.search(symbol_name, limit=5)
            for item in semantic:
                metadata = item.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                file_path = metadata.get("file_path")
                if not isinstance(file_path, str) or file_path == "__project_map__":
                    continue
                candidate = (context.root / file_path).resolve()
                if not candidate.exists() or not candidate.is_file():
                    continue
                pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                for line_number, line in enumerate(lines, start=1):
                    if pattern.search(line):
                        self.editor_state.cache_search(
                            project_name,
                            f"symbol:{symbol_name}",
                            [{"file": file_path, "line": line_number}],
                        )
                        return toolbox.editor_tools.open_file(file_path, line=line_number)
        return result

    def available_tools(self) -> list[dict[str, Any]]:
        project_name = self.get_active_project_name()
        if project_name is None:
            project_root = self.workspace_root
            project_name = "__workspace__"
        else:
            project_root = self.load_active_project(auto_index=False).root
        toolbox = Toolbox(
            workspace_root=self.workspace_root,
            project_root=project_root,
            project_name=project_name,
            embeddings=self.embeddings,
            editor_state=self.editor_state,
            vscode_controller=self.vscode_controller,
            plugin_manager=self.plugin_manager,
            full_access=self.permission_manager.full_access_enabled(),
            require_confirmation=False,
            editor_listener=self.editor_listener,
            terminal_listener=self.terminal_listener,
            git_listener=self.git_listener,
            test_listener=self.test_listener,
        )
        return toolbox.list_available_tools()

    def available_plugins(self) -> list[dict[str, Any]]:
        project_name = self.get_active_project_name()
        if project_name is None:
            project_root = self.workspace_root
            project_name = "__workspace__"
        else:
            project_root = self.load_active_project(auto_index=False).root
        toolbox = Toolbox(
            workspace_root=self.workspace_root,
            project_root=project_root,
            project_name=project_name,
            embeddings=self.embeddings,
            editor_state=self.editor_state,
            vscode_controller=self.vscode_controller,
            plugin_manager=self.plugin_manager,
            full_access=self.permission_manager.full_access_enabled(),
            require_confirmation=False,
            editor_listener=self.editor_listener,
            terminal_listener=self.terminal_listener,
            git_listener=self.git_listener,
            test_listener=self.test_listener,
        )
        return [
            {
                "name": plugin.name,
                "description": plugin.description,
                "path": plugin.path,
                "tools": plugin.tools,
            }
            for plugin in toolbox.loaded_plugins
        ]

    def available_agents(self) -> list[dict[str, Any]]:
        return self.swarm_manager.available_agents()

    def chat(
        self,
        message: str,
        project_name: str | None = None,
        thread_id: str | None = None,
        execute: bool = False,
        auto_approve: bool = False,
        intent_override: str | None = None,
    ) -> dict[str, Any]:
        return self.conversation_router.handle_message(
            message,
            project_name=project_name,
            thread_id=thread_id,
            execute=execute,
            auto_approve=auto_approve,
            intent_override=intent_override,
        )

    def chat_stream(
        self,
        message: str,
        project_name: str | None = None,
        thread_id: str | None = None,
        execute: bool = False,
        auto_approve: bool = False,
        intent_override: str | None = None,
    ):
        return self.conversation_router.stream_message(
            message,
            project_name=project_name,
            thread_id=thread_id,
            execute=execute,
            auto_approve=auto_approve,
            intent_override=intent_override,
        )

    def cancel_chat_stream(self, project_name: str | None = None, stream_id: str | None = None) -> bool:
        return self.conversation_router.cancel_stream(project_name=project_name, stream_id=stream_id)

    def research(
        self,
        query: str,
        *,
        project_name: str | None = None,
        use_web: bool = True,
        use_local: bool = True,
    ):
        target_project = self._normalize_project_name(project_name) or self.get_active_project_name()
        project_context = None
        if target_project:
            project_context = self.project_loader.load_project(target_project, task_hint=query, auto_index=True)
        report = self.research_engine.research(
            query,
            project_name=target_project,
            project_context=project_context,
            use_web=use_web,
            use_local=use_local,
        )
        self.append_timeline_event(
            title="Research completed",
            status="completed",
            agent="research",
            project_name=target_project or "__workspace__",
            message=query,
            metadata={"sources": len(report.sources), "mode": report.mode},
        )
        return report

    def permissions_snapshot(self) -> dict[str, Any]:
        return self.permission_manager.snapshot()

    def mcp_snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.permission_manager.mcp_allowed(),
            "connectors": self.mcp_registry.health(),
        }

    def portfolio_snapshot(self, include_internal: bool = False) -> dict[str, Any]:
        return self.portfolio_manager.snapshot(include_internal=include_internal)

    def add_workspace_root(
        self,
        *,
        name: str,
        path: str,
        mode: str = "projects",
        include_root: bool = False,
        discover_children: bool = True,
        max_depth: int = 1,
    ) -> dict[str, Any]:
        root = self.roots_registry.add_root(
            name=name,
            path=path,
            mode=mode,
            include_root=include_root,
            discover_children=discover_children,
            max_depth=max_depth,
        )
        return {"root": root.__dict__, "snapshot": self.workspace_roots_snapshot()}

    def remove_workspace_root(self, name: str) -> dict[str, Any]:
        removed = self.roots_registry.remove_root(name)
        if not removed:
            raise FileNotFoundError(f"Workspace root '{name}' not found.")
        return {"removed": name, "snapshot": self.workspace_roots_snapshot()}

    def add_mcp_connector(
        self,
        *,
        name: str,
        transport: str,
        target: str,
        args: list[str] | None = None,
        capabilities: list[str] | None = None,
        enabled: bool = True,
        description: str = "",
    ) -> dict[str, Any]:
        connector = self.mcp_registry.add_connector(
            name=name,
            transport=transport,
            target=target,
            args=args,
            capabilities=capabilities,
            enabled=enabled,
            description=description,
        )
        return {"connector": connector.__dict__, "snapshot": self.mcp_snapshot()}

    def remove_mcp_connector(self, name: str) -> dict[str, Any]:
        removed = self.mcp_registry.remove_connector(name)
        if not removed:
            raise FileNotFoundError(f"MCP connector '{name}' not found.")
        return {"removed": name, "snapshot": self.mcp_snapshot()}

    def conversation_history_snapshot(
        self,
        project_name: str | None = None,
        limit: int = 40,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        name = project_name or self.get_active_project_name()
        return self.conversation_router.history(project_name=name, limit=limit, thread_id=thread_id)

    def conversation_threads_snapshot(
        self,
        project_name: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        name = project_name or self.get_active_project_name()
        return self.conversation_history.list_threads(project_name=name, limit=limit)

    def latest_conversation_thread(self, project_name: str | None = None) -> dict[str, Any] | None:
        name = project_name or self.get_active_project_name()
        return self.conversation_history.latest_thread(project_name=name)

    def create_conversation_thread(
        self,
        project_name: str | None = None,
        title: str = "New chat",
    ) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        return self.conversation_history.start_thread(project_name=name, title=title)

    def delete_conversation_thread(self, thread_id: str, project_name: str | None = None) -> bool:
        name = project_name or self.get_active_project_name()
        return self.conversation_history.delete_thread(thread_id, project_name=name)

    def workspace_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        target = self._normalize_project_name(project_name) or self.get_active_project_name() or "__workspace__"
        project_root = self._project_root_for_name(target) if target != "__workspace__" else self.workspace_root
        if project_root.exists():
            self.git_listener.capture_repository_state(target, project_root)
        snapshot = self.workspace_state.snapshot(target)
        return {
            "active_project": self.get_active_project_name(),
            "scope": target,
            "workspace_root": str(self.workspace_root),
            "search_roots": [str(path) for path in self._search_roots(include_root_dir=False)],
            "open_files": snapshot.open_files,
            "recent_edits": snapshot.recent_edits,
            "recent_events": snapshot.recent_events,
            "recent_terminal_commands": snapshot.recent_terminal_commands,
            "last_terminal_command": snapshot.last_terminal_command,
            "last_terminal_result": snapshot.last_terminal_result,
            "last_test_results": snapshot.last_test_results,
            "last_git_diff": snapshot.last_git_diff,
            "last_git_status": snapshot.last_git_status,
            "last_commit": snapshot.last_commit,
            "last_editor_event": snapshot.last_editor_event,
            "updated_at": snapshot.updated_at,
        }

    def record_workspace_event(
        self,
        event_type: str,
        *,
        project_name: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = project_name or self.get_active_project_name() or "__workspace__"
        payload = dict(metadata or {})
        if path:
            payload["path"] = path
        self.workspace_state.record_event(target, event_type, payload)
        return self.workspace_snapshot(target)

    def agent_activity_snapshot(self) -> list[dict[str, Any]]:
        with self._runtime_lock:
            activities = list(self._agent_activity.values())
        activities.sort(key=lambda item: (str(item.get("agent", "")), str(item.get("updated_at", ""))), reverse=False)
        return activities

    def timeline_snapshot(self, limit: int = 80) -> list[dict[str, Any]]:
        with self._runtime_lock:
            events = list(self._timeline)
        events.sort(key=lambda item: int(item.get("sequence", 0)), reverse=True)
        return events[:limit]

    def swarm_runs(self) -> list[dict[str, Any]]:
        return self.swarm_manager.list_runs()

    def swarm_tasks(self, run_id: str | None = None) -> list[dict[str, Any]]:
        return self.swarm_manager.list_tasks(run_id=run_id)

    def swarm_status(self) -> dict[str, Any]:
        return self.swarm_manager.swarm_snapshot()

    def start_swarm(self, task: str, project_name: str | None = None, auto_approve: bool = False) -> dict[str, Any]:
        return self.swarm_manager.start_run(task, project_name=project_name, auto_approve=auto_approve)

    def pause_swarm(self, run_id: str) -> dict[str, Any] | None:
        return self.swarm_manager.pause_run(run_id)

    def resume_swarm(self, run_id: str) -> dict[str, Any] | None:
        return self.swarm_manager.resume_run(run_id)

    def cancel_swarm(self, run_id: str) -> dict[str, Any] | None:
        return self.swarm_manager.cancel_run(run_id)

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.swarm_manager.recent_logs(limit=limit)

    def learn_project(self, project_name: str | None = None, force: bool = True) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        self.project_indexer.index_project(project_name=name, force=force)
        return self.context_retriever.memory_snapshot(name)

    def memory_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        self.project_indexer.index_project(project_name=name, force=False)
        return self.context_retriever.memory_snapshot(name)

    def evolution_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        task_summary = self.task_analyzer.summary(project_name=name)
        prompt_metrics = self.prompt_optimizer.metrics()
        generated_plugins = self.plugin_generator.generated_plugins(limit=50)
        model_status = self.router.model_status()
        solutions_count = len(self.solution_library.list_solutions(project_name=name, limit=200))
        reliability = self.reliability_snapshot(name)
        return {
            "project_name": name,
            "tasks_completed": task_summary.get("tasks_completed", 0),
            "tasks_failed": task_summary.get("tasks_failed", 0),
            "solutions_learned": solutions_count,
            "common_errors": task_summary.get("common_errors", []),
            "frequent_solutions": task_summary.get("frequent_solutions", []),
            "prompt_improvements": prompt_metrics.get("optimization_count", 0),
            "last_prompt_optimization_at": prompt_metrics.get("last_optimized_at"),
            "plugins_generated": len(generated_plugins),
            "generated_plugins": generated_plugins[:10],
            "models": model_status,
            "reliability": reliability,
        }

    def solutions(self, query: str | None = None, limit: int = 20) -> list[SolutionEntry]:
        project_name = self.get_active_project_name()
        if query and project_name:
            return self.solution_library.search(query=query, project_name=project_name, limit=limit)
        if query:
            return self.solution_library.search(query=query, project_name=None, limit=limit)
        return self.solution_library.list_solutions(project_name=project_name, limit=limit)

    def knowledge_graph_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        self.project_indexer.index_project(project_name=name, force=False)
        graph = self.knowledge_graph.project_graph(name)
        graph["insights"] = self.knowledge_graph.project_insights(name, limit=12)
        graph["related_projects"] = self.knowledge_graph.related_projects(name, limit=8)
        return graph

    def load_active_project(self, task_hint: str | None = None, auto_index: bool = True) -> ProjectContext:
        project_name = self.get_active_project_name()
        if not project_name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        return self.project_loader.load_project(project_name, task_hint=task_hint, auto_index=auto_index)

    def plan(self, task: str) -> AgentResult:
        project_context = self.load_active_project(task_hint=task, auto_index=True)
        tools = self._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=False,
            allow_commit=False,
            allow_tests=False,
            allow_editor=True,
        )
        self.publish_agent_activity(
            "architect",
            "planning",
            f"Generating plan for: {task}",
            project_name=project_context.name,
        )
        self.append_timeline_event(
            title="Architect planning started",
            status="running",
            agent="architect",
            project_name=project_context.name,
            message=task,
        )
        try:
            result = self.architect.plan(task=task, project_context=project_context, tools=tools)
        except Exception as exc:
            self.publish_agent_activity(
                "architect",
                "failed",
                f"Planning failed: {exc}",
                project_name=project_context.name,
            )
            self.append_timeline_event(
                title="Architect planning failed",
                status="failed",
                agent="architect",
                project_name=project_context.name,
                message=str(exc),
            )
            raise
        self.publish_agent_activity("architect", "idle", "Idle", project_name=project_context.name)
        self.append_timeline_event(
            title="Architect plan completed",
            status="completed",
            agent="architect",
            project_name=project_context.name,
            message=task,
            metadata={"model": result.model},
        )
        self._record_turns(project_context.name, task, result.text, category="plan")
        return result

    def code(
        self,
        task: str,
        auto_approve: bool = False,
        confirm_overwrite: Callable[[Path], bool] | None = None,
        max_iterations: int = 2,
        project_name: str | None = None,
        store_knowledge: bool = True,
        deep: bool = False,
        skip_planning: bool = False,
        plan_override: str | None = None,
        benchmark_mode: bool = False,
        skip_audit: bool = False,
    ) -> WorkflowResult:
        target_project = project_name or self.get_active_project_name()
        if not target_project:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        project_context = self.project_loader.load_project(target_project, task_hint=task, auto_index=True)
        self.append_timeline_event(
            title="Code workflow started",
            status="running",
            agent="engineer",
            project_name=target_project,
            message=task,
            metadata={"benchmark_mode": benchmark_mode},
        )

        override_text = (plan_override or "").strip()
        if skip_planning:
            plan_text = override_text or "Direct engineer contract."
            plan_result = AgentResult(
                agent_name="architect",
                provider="system",
                model="task_contract",
                text=plan_text,
            )
            self.publish_agent_activity("architect", "idle", "Planning skipped by contract.", project_name=target_project)
            self.append_timeline_event(
                title="Architect planning skipped",
                status="completed",
                agent="architect",
                project_name=target_project,
                message="Direct engineer contract.",
            )
        else:
            architect_tools = self._toolbox(project_context, auto_approve=True).build_tool_definitions(
                allow_write=False,
                allow_terminal=False,
                allow_commit=False,
                allow_tests=False,
                allow_editor=True,
            )
            self.publish_agent_activity(
                "architect",
                "planning",
                f"Creating execution plan for: {task}",
                project_name=target_project,
            )
            self.append_timeline_event(
                title="Architect planning started",
                status="running",
                agent="architect",
                project_name=target_project,
                message=task,
            )
            try:
                plan_result = self.architect.plan(task=task, project_context=project_context, tools=architect_tools)
            except Exception as exc:
                self.publish_agent_activity(
                    "architect",
                    "failed",
                    f"Planning failed: {exc}",
                    project_name=target_project,
                )
                self.append_timeline_event(
                    title="Architect planning failed",
                    status="failed",
                    agent="architect",
                    project_name=target_project,
                    message=str(exc),
                )
                raise
            plan_text = plan_result.text
            self.publish_agent_activity("architect", "idle", "Idle", project_name=target_project)
            self.append_timeline_event(
                title="Architect plan completed",
                status="completed",
                agent="architect",
                project_name=target_project,
                message=task,
                metadata={"model": plan_result.model},
            )
            if override_text:
                plan_text = f"{plan_text}\n\nAdditional execution contract:\n{override_text}"

        engineer_tools = self._toolbox(
            project_context,
            auto_approve=auto_approve,
            confirm_overwrite=confirm_overwrite,
        ).build_tool_definitions(
            allow_write=True,
            allow_terminal=True,
            allow_commit=False,
            allow_tests=True,
            allow_editor=True,
        )
        self.publish_agent_activity(
            "engineer",
            "running",
            "Implementing code changes (attempt 1)",
            project_name=target_project,
        )
        self.append_timeline_event(
            title="Engineer implementation started",
            status="running",
            agent="engineer",
            project_name=target_project,
            message=task,
            metadata={"attempt": 1},
        )
        try:
            implementation_result = call_with_timeout(
                self.ENGINEER_TIMEOUT_SECONDS,
                self.engineer.implement,
                task=task,
                project_context=project_context,
                plan_text=plan_text,
                tools=engineer_tools,
                request_options={
                    "mode": "code",
                    "attempt": 1,
                    "deep": deep,
                    "complexity": "normal",
                    "direct_engineer": skip_planning,
                    "benchmark_mode": benchmark_mode,
                    "timeout_seconds": self.ENGINEER_TIMEOUT_SECONDS,
                    "max_tool_rounds": self.ENGINEER_MAX_TOOL_ROUNDS,
                },
                error_message=f"Engineer execution timed out after {self.ENGINEER_TIMEOUT_SECONDS}s.",
            )
        except Exception as exc:
            self.publish_agent_activity(
                "engineer",
                "failed",
                f"Implementation failed: {exc}",
                project_name=target_project,
            )
            self.append_timeline_event(
                title="Engineer implementation failed",
                status="failed",
                agent="engineer",
                project_name=target_project,
                message=str(exc),
                metadata={"attempt": 1},
            )
            raise
        changed_files = self._extract_changed_files(implementation_result, project_context.root)
        self._record_editor_changes(project_context.name, implementation_result)
        refreshed_context = self._refresh_project_context(project_context.name, task)
        self.publish_agent_activity(
            "engineer",
            "completed",
            f"Updated {len(changed_files)} file(s).",
            project_name=target_project,
            metadata={"changed_files": changed_files},
        )
        self.append_timeline_event(
            title="Engineer implementation completed",
            status="completed",
            agent="engineer",
            project_name=target_project,
            message=", ".join(changed_files) or "No files changed.",
            metadata={"attempt": 1, "changed_files": changed_files},
        )

        if skip_audit:
            audit_result = AuditResult(
                agent_name="auditor",
                provider="system",
                model="skipped",
                text="Audit skipped by execution contract.",
                passed=True,
            )
            self.publish_agent_activity("auditor", "idle", "Audit skipped.", project_name=target_project)
            self.append_timeline_event(
                title="Audit skipped",
                status="completed",
                agent="auditor",
                project_name=target_project,
                message="Audit skipped by execution contract.",
            )
        else:
            audit_tools = self._toolbox(refreshed_context, auto_approve=True).build_tool_definitions(
                allow_write=False,
                allow_terminal=True,
                allow_commit=False,
                allow_tests=True,
                allow_editor=True,
            )
            self.publish_agent_activity(
                "auditor",
                "reviewing",
                "Reviewing engineer changes",
                project_name=target_project,
                metadata={"changed_files": changed_files},
            )
            self.append_timeline_event(
                title="Auditor review started",
                status="running",
                agent="auditor",
                project_name=target_project,
                message=task,
                metadata={"attempt": 1, "changed_files": changed_files},
            )
            audit_result = self.auditor.audit(
                task=task,
                project_context=refreshed_context,
                plan_text=plan_text,
                implementation_text=implementation_result.text,
                changed_files=changed_files,
                tools=audit_tools,
            )
            self.publish_agent_activity(
                "auditor",
                "completed" if audit_result.passed else "failed",
                "Audit passed." if audit_result.passed else "Audit requested fixes.",
                project_name=target_project,
            )
            self.append_timeline_event(
                title="Auditor review completed" if audit_result.passed else "Auditor requested changes",
                status="completed" if audit_result.passed else "failed",
                agent="auditor",
                project_name=target_project,
                message=audit_result.text[:240],
                metadata={"attempt": 1},
            )

        iterations = 1
        while not skip_audit and not audit_result.passed and iterations < max_iterations:
            self.publish_agent_activity(
                "engineer",
                "running",
                f"Applying audit feedback (attempt {iterations + 1})",
                project_name=target_project,
            )
            self.append_timeline_event(
                title="Engineer retry started",
                status="running",
                agent="engineer",
                project_name=target_project,
                message=task,
                metadata={"attempt": iterations + 1},
            )
            try:
                implementation_result = call_with_timeout(
                    self.ENGINEER_TIMEOUT_SECONDS,
                    self.engineer.implement,
                    task=task,
                    project_context=refreshed_context,
                    plan_text=plan_text,
                    tools=engineer_tools,
                    audit_feedback=audit_result.text,
                    request_options={
                        "mode": "code",
                        "attempt": iterations + 1,
                        "deep": deep,
                        "complexity": "high",
                        "direct_engineer": skip_planning,
                        "benchmark_mode": benchmark_mode,
                        "timeout_seconds": self.ENGINEER_TIMEOUT_SECONDS,
                        "max_tool_rounds": self.ENGINEER_MAX_TOOL_ROUNDS,
                    },
                    error_message=f"Engineer execution timed out after {self.ENGINEER_TIMEOUT_SECONDS}s.",
                )
            except Exception as exc:
                self.publish_agent_activity(
                    "engineer",
                    "failed",
                    f"Implementation failed: {exc}",
                    project_name=target_project,
                )
                self.append_timeline_event(
                    title="Engineer retry failed",
                    status="failed",
                    agent="engineer",
                    project_name=target_project,
                    message=str(exc),
                    metadata={"attempt": iterations + 1},
                )
                raise
            changed_files = self._extract_changed_files(implementation_result, refreshed_context.root)
            self._record_editor_changes(refreshed_context.name, implementation_result)
            refreshed_context = self._refresh_project_context(project_context.name, task)
            self.publish_agent_activity(
                "engineer",
                "completed",
                f"Updated {len(changed_files)} file(s).",
                project_name=target_project,
                metadata={"changed_files": changed_files},
            )
            self.append_timeline_event(
                title="Engineer retry completed",
                status="completed",
                agent="engineer",
                project_name=target_project,
                message=", ".join(changed_files) or "No files changed.",
                metadata={"attempt": iterations + 1, "changed_files": changed_files},
            )
            self.publish_agent_activity(
                "auditor",
                "reviewing",
                f"Reviewing retry attempt {iterations + 1}",
                project_name=target_project,
            )
            self.append_timeline_event(
                title="Auditor review started",
                status="running",
                agent="auditor",
                project_name=target_project,
                message=task,
                metadata={"attempt": iterations + 1},
            )
            audit_result = self.auditor.audit(
                task=task,
                project_context=refreshed_context,
                plan_text=plan_text,
                implementation_text=implementation_result.text,
                changed_files=changed_files,
                tools=audit_tools,
            )
            self.publish_agent_activity(
                "auditor",
                "completed" if audit_result.passed else "failed",
                "Audit passed." if audit_result.passed else "Audit requested fixes.",
                project_name=target_project,
            )
            self.append_timeline_event(
                title="Auditor review completed" if audit_result.passed else "Auditor requested changes",
                status="completed" if audit_result.passed else "failed",
                agent="auditor",
                project_name=target_project,
                message=audit_result.text[:240],
                metadata={"attempt": iterations + 1},
            )
            iterations += 1

        if store_knowledge and not benchmark_mode:
            self._record_turns(project_context.name, task, plan_text, category="plan")
            self._record_turns(project_context.name, task, implementation_result.text, category="implementation")
            self._record_turns(project_context.name, task, audit_result.text, category="audit")

        self.publish_agent_activity("engineer", "idle", "Idle", project_name=target_project)
        self.publish_agent_activity("auditor", "idle", "Idle", project_name=target_project)
        self.append_timeline_event(
            title="Code workflow completed" if audit_result.passed or skip_audit else "Code workflow needs follow-up",
            status="completed" if audit_result.passed or skip_audit else "failed",
            agent="engineer",
            project_name=target_project,
            message=task,
            metadata={"iterations": iterations, "changed_files": changed_files},
        )

        workflow = WorkflowResult(
            plan=plan_result,
            implementation=implementation_result,
            audit=audit_result,
            iterations=iterations,
            changed_files=changed_files,
        )
        self.project_brain.record_task_completion(
            project_context.name,
            task=task,
            status="completed" if audit_result.passed or skip_audit else "failed",
            changed_files=changed_files,
            errors=[] if audit_result.passed or skip_audit else [audit_result.text],
            metadata={
                "plan": plan_result.text,
                "steps": [task],
                "iterations": iterations,
            },
        )
        if store_knowledge and not benchmark_mode:
            self._store_task_knowledge(
                project_name=project_context.name,
                task=task,
                solution_text=implementation_result.text,
                changed_files=changed_files,
                errors=[] if audit_result.passed else [audit_result.text],
                metadata={
                    "plan": plan_result.text,
                    "audit": audit_result.text,
                    "iterations": iterations,
                    "models": {
                        "architect": {"provider": plan_result.provider, "model": plan_result.model},
                        "engineer": {"provider": implementation_result.provider, "model": implementation_result.model},
                        "auditor": {"provider": audit_result.provider, "model": audit_result.model},
                    },
                    "durations": {
                        "architect": plan_result.duration_seconds,
                        "engineer": implementation_result.duration_seconds,
                        "auditor": audit_result.duration_seconds,
                    },
                    "status": "passed" if audit_result.passed else "needs_followup",
                    "direct_engineer": skip_planning,
                    "benchmark_mode": benchmark_mode,
                    "skip_audit": skip_audit,
                },
            )
        return workflow

    def build(
        self,
        task: str,
        auto_approve: bool = False,
        max_iterations: int = 10,
        commit_changes: bool = True,
        project_name: str | None = None,
        store_knowledge: bool = True,
        deep: bool = False,
        benchmark_mode: bool = False,
    ) -> AutonomousBuildResult:
        target_project = project_name or self.get_active_project_name()
        if not target_project:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        self.append_timeline_event(
            title="Autonomous build started",
            status="running",
            agent="architect",
            project_name=target_project,
            message=task,
            metadata={"benchmark_mode": benchmark_mode},
        )

        def handle_loop_event(event: dict[str, Any]) -> None:
            kind = str(event.get("kind", ""))
            payload = dict(event)
            payload.pop("kind", None)
            if kind == "activity":
                self.publish_agent_activity(
                    str(payload.get("agent", "boss")),
                    str(payload.get("status", "running")),
                    str(payload.get("message", "")),
                    project_name=str(payload.get("project_name") or target_project),
                    task_id=int(payload["task_id"]) if payload.get("task_id") is not None else None,
                    metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                )
            elif kind == "timeline":
                self.append_timeline_event(
                    title=str(payload.get("title", "Build event")),
                    status=str(payload.get("status", "running")),
                    agent=str(payload.get("agent")) if payload.get("agent") else None,
                    project_name=str(payload.get("project_name") or target_project),
                    task_id=int(payload["task_id"]) if payload.get("task_id") is not None else None,
                    message=str(payload.get("message", "")) if payload.get("message") is not None else None,
                    metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                )

        result = self.autonomous_loop.run(
            project_name=target_project,
            task=task,
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            commit_changes=commit_changes,
            deep=deep,
            benchmark_mode=benchmark_mode,
            current_task_callback=self._set_current_task,
            event_callback=handle_loop_event,
        )
        artifact_path = self.artifact_store.write_build_artifact(
            task=task,
            result=result,
            project_root=self._project_root_for_name(target_project),
        )
        result.metadata["artifact_path"] = artifact_path
        self.task_history.merge_task_metadata(result.task_id, {"artifact_path": artifact_path})
        self.project_brain.record_task_completion(
            target_project,
            task=task,
            status=result.status,
            changed_files=result.changed_files,
            errors=result.errors,
            metadata={
                "steps": [step.step_title for step in result.step_results],
                **dict(result.metadata or {}),
            },
            artifact_path=artifact_path,
        )
        self.publish_agent_activity("architect", "idle", "Idle", project_name=target_project)
        self.publish_agent_activity("engineer", "idle", "Idle", project_name=target_project)
        self.publish_agent_activity("test", "idle", "Idle", project_name=target_project)
        self.publish_agent_activity("auditor", "idle", "Idle", project_name=target_project)
        self.append_timeline_event(
            title="Autonomous build completed" if result.status == "completed" else "Autonomous build stopped",
            status="completed" if result.status == "completed" else result.status,
            agent="engineer",
            project_name=target_project,
            task_id=result.task_id,
            message=result.final_result,
            metadata={"changed_files": result.changed_files, "status": result.status},
        )
        if store_knowledge and not benchmark_mode:
            self._store_task_knowledge(
                project_name=target_project,
                task=task,
                solution_text=result.final_result,
                changed_files=result.changed_files,
                errors=result.errors,
                metadata={
                    "task_id": result.task_id,
                    "status": result.status,
                    "goal": result.goal,
                    "steps": [step.step_title for step in result.step_results],
                    "runtime_seconds": result.runtime_seconds,
                    "token_usage": result.token_usage,
                    "estimated_cost_usd": result.estimated_cost_usd,
                    "benchmark_mode": benchmark_mode,
                },
            )
        return result

    def ship(
        self,
        task: str,
        auto_approve: bool = False,
        max_iterations: int = 10,
        commit_changes: bool = True,
        push_changes: bool = True,
        project_name: str | None = None,
        store_knowledge: bool = True,
        deep: bool = False,
    ) -> AutonomousBuildResult:
        result = self.build(
            task=task,
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            commit_changes=commit_changes,
            project_name=project_name,
            store_knowledge=store_knowledge,
            deep=deep,
            benchmark_mode=False,
        )
        shipping = self._shipping_summary(
            result=result,
            commit_changes=commit_changes,
            push_changes=push_changes,
            deploy_changes=False,
        )
        result.metadata["shipping"] = shipping
        self.task_history.merge_task_metadata(result.task_id, {"shipping": shipping})
        if shipping.get("status") == "ready_to_push":
            refreshed = self.task_history.task_with_steps(result.task_id)
            if refreshed is not None:
                push_payload = self._ship_push(refreshed, shipping)
                result.metadata["shipping"] = push_payload
                self.task_history.merge_task_metadata(result.task_id, {"shipping": push_payload})
        return result

    def evaluate_suite(
        self,
        suite_path: str | Path,
        project_name: str | None = None,
        stop_on_failure: bool | None = None,
    ) -> EvalRunResult:
        result = self.evaluator.run_suite(
            suite_path=suite_path,
            project_name=project_name,
            stop_on_failure=stop_on_failure,
        )
        if result.project_name:
            failure_map = self.evaluation_store.failure_map_summary(project_name=result.project_name, limit=15).get("counts", {})
            self.project_brain.record_evaluation(
                result.project_name,
                suite_name=result.suite_name,
                status=result.status,
                passed_tasks=result.passed_tasks,
                total_tasks=result.total_tasks,
                runtime_seconds=result.runtime_seconds,
                artifact_path=str((result.metadata or {}).get("artifact_path", "") or ""),
                failure_map=failure_map if isinstance(failure_map, dict) else None,
            )
        return result

    def evaluate_task_suite(
        self,
        suite,
        project_name: str | None = None,
        stop_on_failure: bool | None = None,
    ) -> EvalRunResult:
        result = self.evaluator.run_task_suite(
            suite=suite,
            project_name=project_name,
            stop_on_failure=stop_on_failure,
        )
        if result.project_name:
            failure_map = self.evaluation_store.failure_map_summary(project_name=result.project_name, limit=15).get("counts", {})
            self.project_brain.record_evaluation(
                result.project_name,
                suite_name=result.suite_name,
                status=result.status,
                passed_tasks=result.passed_tasks,
                total_tasks=result.total_tasks,
                runtime_seconds=result.runtime_seconds,
                artifact_path=str((result.metadata or {}).get("artifact_path", "") or ""),
                failure_map=failure_map if isinstance(failure_map, dict) else None,
            )
        return result

    def evaluation_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.evaluation_store.recent_runs(limit=limit)

    def evaluation_run(self, run_id: int) -> EvalRunResult | None:
        return self.evaluation_store.run_with_tasks(run_id)

    def artifact_index(
        self,
        *,
        kind: str | None = None,
        project_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.artifact_store.list_index(kind=kind, project_name=project_name, limit=limit)

    def replay_run(
        self,
        identifier: int,
        *,
        kind: str = "auto",
        mode: str = "analysis",
        auto_approve: bool = False,
    ) -> dict[str, Any]:
        return self.run_replay.replay(identifier, kind=kind, mode=mode, auto_approve=auto_approve)

    def reliability_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        return {
            "project_name": name,
            "tasks": {
                **self.task_history.failure_map_summary(project_name=name, limit=25),
                "metrics": self.task_history.success_metrics(project_name=name, limit=100),
            },
            "evaluations": {
                **self.evaluation_store.failure_map_summary(project_name=name, limit=15),
                "metrics": self.evaluation_store.success_metrics(project_name=name, limit=50),
            },
        }

    def health_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        return self.health_reporter.snapshot(project_name=name)

    def metrics_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        return self.metrics_reporter.snapshot(project_name=name)

    def recent_runs(self, project_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        name = project_name or self.get_active_project_name()
        return self.run_ledger.recent(project_name=name, limit=limit)

    def run_details(
        self,
        identifier: str | int,
        *,
        kind: str = "auto",
        project_name: str | None = None,
    ) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        return self.run_ledger.details(identifier, kind=kind, project_name=name)

    def run_diff(
        self,
        identifier: str | int,
        *,
        kind: str = "auto",
        project_name: str | None = None,
    ) -> dict[str, Any]:
        details = self.run_details(identifier, kind=kind, project_name=project_name)
        artifact_path = str(details.get("artifact_path", "") or "").strip()
        if not artifact_path:
            raise FileNotFoundError(f"No artifact diff is available for run '{identifier}'.")
        payload = self.artifact_store.read_diff_bundle(artifact_path)
        payload.update(
            {
                "kind": str(details.get("kind", kind)),
                "identifier": details.get("identifier", identifier),
                "project_name": details.get("project_name", project_name or self.get_active_project_name()),
                "status": details.get("status"),
                "summary": details.get("summary", {}),
            }
        )
        return payload

    def approve_run_commit(
        self,
        identifier: str | int,
        *,
        kind: str = "build",
        project_name: str | None = None,
    ) -> dict[str, Any]:
        if kind.strip().lower() not in {"build", "auto"}:
            raise RuntimeError("Commit approval is only supported for build runs.")
        details = self.run_details(identifier, kind="build", project_name=project_name)
        task = details.get("task")
        if not isinstance(task, dict):
            raise FileNotFoundError(f"Build task '{identifier}' not found.")
        task_id = int(task["id"])
        gate = dict((task.get("metadata", {}) or {}).get("commit_gate", {}) or {})
        if str(gate.get("status", "")).lower() != "pending":
            refreshed = self.task_history.task_with_steps(task_id)
            return {
                "approved": False,
                "status": str(gate.get("status", "ready") or "ready"),
                "message": "No pending commit gate for this run.",
                "task": refreshed,
            }

        project_root = self._project_root_for_name(str(task["project_name"]))
        commit_message = self._commit_gate_message(task, gate)
        git_tools = GitTools(project_root, project_name=str(task["project_name"]), git_listener=self.git_listener)
        commit_result = git_tools.git_commit(commit_message)
        committed = bool(commit_result.get("committed")) if isinstance(commit_result, dict) else False
        result_message = (
            str(commit_result.get("message", "")).strip()
            if isinstance(commit_result, dict)
            else "Commit finished."
        )
        lowered_message = result_message.lower()
        status = "committed" if committed else ("skipped" if "no changes to commit" in lowered_message else "failed")
        gate_payload: dict[str, Any] = {
            **gate,
            "status": status,
            "message": result_message or commit_message,
            "updated_at": utc_now_iso(),
        }
        if committed and isinstance(commit_result, dict):
            gate_payload["commit"] = str(commit_result.get("commit", ""))
            gate_payload["approved_at"] = utc_now_iso()

        self.task_history.merge_task_metadata(task_id, {"commit_gate": gate_payload})
        for step in task.get("steps", []):
            step_gate = dict((step.get("metadata", {}) or {}).get("commit_gate", {}) or {})
            if str(step_gate.get("status", "")).lower() != "pending":
                continue
            updated_step_gate = {
                **step_gate,
                "status": status,
                "message": result_message or commit_message,
                "updated_at": gate_payload["updated_at"],
            }
            if committed:
                updated_step_gate["commit"] = gate_payload.get("commit", "")
                updated_step_gate["approved_at"] = gate_payload.get("approved_at", "")
            self.task_history.merge_step_metadata(
                task_id,
                int(step["step_index"]),
                {"commit_gate": updated_step_gate},
                commit_message=commit_message,
            )
        refreshed = self.task_history.task_with_steps(task_id)
        if refreshed is not None:
            shipping = dict((refreshed.get("metadata", {}) or {}).get("shipping", {}) or {})
            if str(shipping.get("status", "")).lower() == "awaiting_commit" and bool(shipping.get("push_requested")):
                push_payload = self._ship_push(refreshed, shipping)
                refreshed = self.task_history.task_with_steps(task_id)
                if refreshed is not None:
                    refreshed.setdefault("metadata", {})
                    refreshed["metadata"]["shipping"] = push_payload
        return {
            "approved": committed,
            "status": status,
            "message": result_message or commit_message,
            "task": refreshed,
            "commit_result": commit_result,
        }

    def reject_run_commit(
        self,
        identifier: str | int,
        *,
        kind: str = "build",
        project_name: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if kind.strip().lower() not in {"build", "auto"}:
            raise RuntimeError("Commit rejection is only supported for build runs.")
        details = self.run_details(identifier, kind="build", project_name=project_name)
        task = details.get("task")
        if not isinstance(task, dict):
            raise FileNotFoundError(f"Build task '{identifier}' not found.")
        task_id = int(task["id"])
        gate = dict((task.get("metadata", {}) or {}).get("commit_gate", {}) or {})
        message = (reason or "Commit rejected. Review the diff and revise the changes.").strip()
        gate_payload = {
            **gate,
            "status": "rejected",
            "message": message,
            "rejected_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        self.task_history.merge_task_metadata(task_id, {"commit_gate": gate_payload})
        for step in task.get("steps", []):
            step_gate = dict((step.get("metadata", {}) or {}).get("commit_gate", {}) or {})
            if str(step_gate.get("status", "")).lower() != "pending":
                continue
            self.task_history.merge_step_metadata(
                task_id,
                int(step["step_index"]),
                {
                    "commit_gate": {
                        **step_gate,
                        "status": "rejected",
                        "message": message,
                        "rejected_at": gate_payload["rejected_at"],
                        "updated_at": gate_payload["updated_at"],
                    }
                },
                commit_message=str(step.get("commit_message", "") or ""),
            )
        refreshed = self.task_history.task_with_steps(task_id)
        return {
            "rejected": True,
            "status": "rejected",
            "message": message,
            "task": refreshed,
        }

    def project_brain_snapshot(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        project = self.memory_store.get_project(name)
        project_map = self.memory_store.get_project_map(name)
        brain = self.project_brain.load(
            name,
            summary=str(project["summary"]) if project else "",
            project_map=project_map,
        )
        pending = self.project_brain.list_proposals(project_name=name, status="pending", limit=200)
        return {
            "project_name": name,
            "brain": brain,
            "policy": self.project_brain.policy(),
            "pending_proposals": len(pending),
            "artifact_count": len(self.artifact_store.list_index(project_name=name, limit=1000)),
        }

    def command_center_brain(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if name:
            return self.project_brain_snapshot(name)

        portfolio = self.portfolio_snapshot(include_internal=False)
        focuses = [str(item) for item in portfolio.get("focuses", []) if str(item).strip()]
        priorities = [str(item) for item in portfolio.get("top_priorities", []) if str(item).strip()]
        visible_projects = self.available_project_catalog(include_internal=False)
        return {
            "project_name": "__workspace__",
            "brain": ProjectBrain(
                project_name="__workspace__",
                mission="Run the active engineering portfolio with clear workspace awareness.",
                current_focus=focuses[0] if focuses else "Workspace operator mode",
                architecture=[
                    "Workspace-first search and navigation",
                    "Project-aware chat and execution",
                    "Research, benchmarks, and observability",
                ],
                brain_rules=[
                    "Prefer workspace-first discovery before asking the user to restate context.",
                    "Keep destructive actions on confirm unless the user explicitly asks otherwise.",
                    "Use the fastest high-signal path to an answer, then suggest the next move.",
                ],
                milestones=["Native app", "Workspace awareness", "Deterministic evaluation", "Observability surface"],
                recent_progress=[f"{len(visible_projects)} visible projects discovered across the workspace."],
                open_problems=[],
                next_priorities=priorities[:6] or ["Review top projects", "Index priority repos", "Inspect recent failures"],
                known_risks=[],
                recent_artifacts=[],
                updated_at=utc_now_iso(),
            ),
            "policy": self.project_brain.policy(),
            "pending_proposals": 0,
            "artifact_count": len(self.artifact_store.list_index(project_name=None, limit=1000)),
        }

    def next_recommendations(self, project_name: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        name = project_name or self.get_active_project_name()
        if not name:
            portfolio = self.portfolio_snapshot(include_internal=False)
            recommendations: list[dict[str, Any]] = []
            for project in portfolio.get("projects", [])[:limit]:
                display = str(project.get("display_name", "")).strip()
                next_priority = str(project.get("next_priority", "")).strip() or "Review project state"
                focus = str(project.get("focus", "")).strip() or "No focus recorded"
                recommendations.append(
                    {
                        "title": f"{display}: {next_priority}",
                        "reason": f"{display} is currently focused on {focus}.",
                        "source": "portfolio",
                        "score": 4,
                    }
                )
            return recommendations[:limit]

        snapshot = self.project_brain_snapshot(project_name)
        brain = snapshot["brain"]
        reliability = self.reliability_snapshot(snapshot["project_name"])
        task_metrics = reliability["tasks"]["metrics"]
        eval_metrics = reliability["evaluations"]["metrics"]
        pending_count = int(snapshot["pending_proposals"])
        artifact_count = int(snapshot["artifact_count"])
        recommendations: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}

        def add(title: str, reason: str, source: str, score: int) -> None:
            cleaned = str(title).strip()
            if not cleaned:
                return
            key = cleaned.lower()
            if key in seen:
                seen[key]["score"] = int(seen[key]["score"]) + score
                if reason not in seen[key]["reasons"]:
                    seen[key]["reasons"].append(reason)
                return
            item = {
                "title": cleaned,
                "reason": reason,
                "reasons": [reason],
                "source": source,
                "score": score,
            }
            seen[key] = item
            recommendations.append(item)

        for item in brain.next_priorities[:limit]:
            add(item, "Recorded in the governed project brain as an active priority.", "brain", 5)
        for item in brain.open_problems[:limit]:
            add(item, "Open problem tracked in the project brain.", "brain", 4)
        for item in brain.known_risks[:limit]:
            add(item, "Known risk tracked in the project brain.", "brain", 3)
        if eval_metrics.get("failed_runs", 0):
            add(
                "Stabilize recent evaluation failures",
                f"{eval_metrics['failed_runs']} recent evaluation run(s) failed.",
                "evaluations",
                4,
            )
        if task_metrics.get("attempted", 0) and task_metrics.get("success_rate") is not None and float(task_metrics["success_rate"]) < 0.7:
            add(
                "Improve autonomous task success rate",
                f"Recent autonomous success rate is {float(task_metrics['success_rate']) * 100:.0f}%.",
                "tasks",
                3,
            )
        if artifact_count > 100:
            add(
                "Review artifact growth and observability",
                f"{artifact_count} artifacts are stored; retrieval and dashboard views should stay ahead of growth.",
                "artifacts",
                2,
            )
        if pending_count:
            add(
                "Review pending brain updates",
                f"{pending_count} project-brain proposal(s) are waiting for approval.",
                "brain_policy",
                1,
            )
        for item in recommendations:
            item["reason"] = " ".join(item.pop("reasons"))
        recommendations.sort(key=lambda item: (-int(item["score"]), str(item["title"])))
        return recommendations[:limit]

    def project_roadmap(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            portfolio = self.portfolio_snapshot(include_internal=False)
            projects = portfolio.get("projects", [])
            return {
                "project_name": "__workspace__",
                "mission": "Coordinate the active engineering portfolio.",
                "focus": "Workspace operator mode",
                "completed": ["Native app", "Workspace awareness", "Observability surface"],
                "in_progress": [str(item.get("display_name", "")) for item in projects[:3]],
                "future": [str(item.get("next_priority", "")) for item in projects[:6] if str(item.get("next_priority", "")).strip()],
                "pending_proposals": 0,
            }

        snapshot = self.project_brain_snapshot(project_name)
        brain = snapshot["brain"]
        completed = brain.milestones[:6] or brain.recent_progress[:6]
        in_progress = self.project_brain.effective_next_priorities(brain)[:6]
        seen = {item.lower() for item in [*completed, *in_progress]}
        future = [item for item in brain.architecture if item.lower() not in seen][:6]
        if not future:
            future = [f"Mitigate risk: {item}" for item in brain.known_risks[:3]]
        return {
            "project_name": snapshot["project_name"],
            "mission": brain.mission,
            "focus": brain.current_focus,
            "completed": completed,
            "in_progress": in_progress,
            "future": future,
            "pending_proposals": snapshot["pending_proposals"],
        }

    def project_risks(self, project_name: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        name = project_name or self.get_active_project_name()
        if not name:
            health = self.health_snapshot(project_name=None)
            risks: list[dict[str, Any]] = []
            if health.get("recent_eval_failures"):
                risks.append(
                    {
                        "title": "Recent workspace evaluation failures",
                        "reason": f"{health['recent_eval_failures']} recent evaluation failure(s) were recorded.",
                        "source": "evaluations",
                        "severity": "HIGH",
                    }
                )
            if health.get("step_timeouts"):
                risks.append(
                    {
                        "title": "Recent workspace step timeouts",
                        "reason": f"{health['step_timeouts']} recent step timeout(s) were detected.",
                        "source": "tasks",
                        "severity": "MEDIUM",
                    }
                )
            return risks[:limit]

        snapshot = self.project_brain_snapshot(project_name)
        brain = snapshot["brain"]
        reliability = self.reliability_snapshot(snapshot["project_name"])
        task_metrics = reliability["tasks"]["metrics"]
        eval_metrics = reliability["evaluations"]["metrics"]
        artifact_count = int(snapshot["artifact_count"])
        risks: list[dict[str, Any]] = []
        seen: set[str] = set()

        def severity_for(text: str) -> str:
            lowered = text.lower()
            if any(token in lowered for token in ("failed", "disabled", "blocked", "regression", "corrupt", "critical")):
                return "HIGH"
            if any(token in lowered for token in ("risk", "missing", "drift", "stability", "coverage", "proposal")):
                return "MEDIUM"
            return "LOW"

        def add(title: str, reason: str, source: str, severity: str | None = None) -> None:
            cleaned = str(title).strip()
            if not cleaned:
                return
            key = cleaned.lower()
            if key in seen:
                return
            seen.add(key)
            risks.append(
                {
                    "title": cleaned,
                    "reason": reason,
                    "source": source,
                    "severity": severity or severity_for(cleaned),
                }
            )

        for item in brain.known_risks[:limit]:
            add(item, "Tracked as a known project risk in the Active Project Brain.", "brain")
        for item in brain.open_problems[:limit]:
            add(item, "Tracked as an unresolved problem in the Active Project Brain.", "brain")
        if eval_metrics.get("failed_runs", 0):
            add(
                "Evaluation failures remain unresolved",
                f"{eval_metrics['failed_runs']} failed evaluation run(s) were recorded recently.",
                "evaluations",
                severity="HIGH",
            )
        if task_metrics.get("attempted", 0) >= 3 and task_metrics.get("success_rate") is not None and float(task_metrics["success_rate"]) < 0.7:
            add(
                "Autonomous task success rate is below target",
                f"Recent success rate is {float(task_metrics['success_rate']) * 100:.0f}%.",
                "tasks",
                severity="MEDIUM",
            )
        if artifact_count > 100:
            add(
                "Artifact corpus is growing quickly",
                f"{artifact_count} stored artifacts may need stronger indexing and dashboard support.",
                "artifacts",
                severity="MEDIUM",
            )
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        risks.sort(key=lambda item: (severity_order.get(str(item["severity"]), 3), str(item["title"])))
        return risks[:limit]

    def note_project_brain_signal(self, message: str, project_name: str | None = None) -> bool:
        name = project_name or self.get_active_project_name()
        if not name:
            return False
        return self.project_brain.record_conversation_signal(name, message)

    def brain_policy(self) -> dict[str, Any]:
        return self.project_brain.policy()

    def brain_proposals(
        self,
        project_name: str | None = None,
        status: str | None = "pending",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        name = project_name or self.get_active_project_name()
        return self.project_brain.list_proposals(project_name=name, status=status, limit=limit)

    def approve_brain_proposal(self, proposal_id: int) -> dict[str, Any]:
        result = self.project_brain.approve_proposal(proposal_id)
        result["policy"] = self.project_brain.policy()
        pending = self.project_brain.list_proposals(project_name=result["project_name"], status="pending", limit=200)
        result["pending_proposals"] = len(pending)
        return result

    def reject_brain_proposal(self, proposal_id: int) -> dict[str, Any]:
        result = self.project_brain.reject_proposal(proposal_id)
        result["policy"] = self.project_brain.policy()
        pending = self.project_brain.list_proposals(project_name=result["project_name"], status="pending", limit=200)
        result["pending_proposals"] = len(pending)
        return result

    def brain_rules(self, project_name: str | None = None) -> dict[str, Any]:
        snapshot = self.project_brain_snapshot(project_name)
        brain = snapshot["brain"]
        return {
            "project_name": snapshot["project_name"],
            "rules": list(brain.brain_rules),
            "policy": snapshot["policy"],
            "pending_proposals": snapshot["pending_proposals"],
        }

    def add_brain_rule(self, rule: str, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        project = self.memory_store.get_project(name)
        project_map = self.memory_store.get_project_map(name)
        brain = self.project_brain.add_rule(
            name,
            rule,
            summary=str(project["summary"]) if project else "",
            project_map=project_map,
        )
        pending = self.project_brain.list_proposals(project_name=name, status="pending", limit=200)
        return {
            "project_name": name,
            "status": "applied",
            "brain": brain,
            "policy": self.project_brain.policy(),
            "pending_proposals": len(pending),
        }

    def remove_brain_rule(self, rule: str, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        project = self.memory_store.get_project(name)
        project_map = self.memory_store.get_project_map(name)
        brain = self.project_brain.remove_rule(
            name,
            rule,
            summary=str(project["summary"]) if project else "",
            project_map=project_map,
        )
        pending = self.project_brain.list_proposals(project_name=name, status="pending", limit=200)
        return {
            "project_name": name,
            "status": "applied",
            "brain": brain,
            "policy": self.project_brain.policy(),
            "pending_proposals": len(pending),
        }

    def reset_project_brain(self, project_name: str | None = None) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if not name:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        project = self.memory_store.get_project(name)
        project_map = self.memory_store.get_project_map(name)
        brain = self.project_brain.reset(
            name,
            summary=str(project["summary"]) if project else "",
            project_map=project_map,
        )
        pending = self.project_brain.list_proposals(project_name=name, status="pending", limit=200)
        return {
            "project_name": name,
            "status": "reset",
            "brain": brain,
            "policy": self.project_brain.policy(),
            "pending_proposals": len(pending),
        }

    def benchmark_manifest(
        self,
        manifest_path: str | Path,
        only_suites: list[str] | None = None,
        repeat_override: int | None = None,
    ) -> dict[str, Any]:
        return self.benchmark_runner.run_manifest(
            manifest_path,
            only_suites=only_suites,
            repeat_override=repeat_override,
        )

    def golden_tasks_manifest_path(self) -> Path:
        return self.root_dir / "benchmarks" / "golden_tasks.yaml"

    def run_golden_tasks(
        self,
        *,
        only_suites: list[str] | None = None,
        repeat_override: int | None = None,
    ) -> dict[str, Any]:
        result = self.benchmark_manifest(
            self.golden_tasks_manifest_path(),
            only_suites=only_suites,
            repeat_override=repeat_override,
        )
        result["benchmark_kind"] = "golden_tasks"
        return result

    def sync_external_benchmark_repos(
        self,
        catalog_path: str | Path,
        only_repos: list[str] | None = None,
        update: bool = False,
    ) -> dict[str, Any]:
        return self.external_repo_sync.sync(catalog_path, only_repos=only_repos, update=update)

    def start_lab_experiment(
        self,
        goal: str,
        project_name: str | None = None,
        variants: list[str] | None = None,
        benchmark_commands: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        primary_metric: str | None = None,
        metric_direction: str = "minimize",
        auto_approve: bool = True,
        max_iterations: int = 5,
        deep: bool = False,
    ) -> dict[str, Any]:
        return self.experiment_manager.start_experiment(
            goal=goal,
            project_name=project_name,
            variants=variants,
            benchmark_commands=benchmark_commands,
            allowed_paths=allowed_paths,
            primary_metric=primary_metric,
            metric_direction=metric_direction,
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            deep=deep,
        )

    def lab_experiments(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.experiment_manager.list_experiments(limit=limit)

    def lab_results(self, experiment_id: str) -> dict[str, Any]:
        return self.experiment_manager.experiment_results(experiment_id)

    def apply_lab_variant(
        self,
        variant_id: str,
        auto_approve: bool = False,
        confirm_callback: Callable[[str], bool] | None = None,
        preview_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        return self.experiment_manager.apply_variant(
            variant_id,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            preview_callback=preview_callback,
        )

    def run_tests(
        self,
        workdir: str = ".",
        project_name: str | None = None,
        python_bin: str | None = None,
    ) -> dict[str, Any]:
        target_project = project_name or self.get_active_project_name()
        if not target_project:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        project_context = self.project_loader.load_project(target_project, auto_index=False)
        toolbox = self._toolbox(project_context, auto_approve=True)
        self.publish_agent_activity("test", "running", "Running project tests", project_name=target_project)
        self.append_timeline_event(
            title="Test run started",
            status="running",
            agent="test",
            project_name=target_project,
            message=workdir,
        )
        result = toolbox.terminal_tools.run_tests(workdir=workdir, python_bin=python_bin)
        self.publish_agent_activity(
            "test",
            "completed" if result.get("passed", False) else "failed",
            "Tests passed." if result.get("passed", False) else str(result.get("message", "Tests failed.")),
            project_name=target_project,
        )
        self.append_timeline_event(
            title="Test run completed" if result.get("passed", False) else "Test run failed",
            status="completed" if result.get("passed", False) else "failed",
            agent="test",
            project_name=target_project,
            message=str(result.get("message") or ", ".join(result.get("commands", []))),
            metadata={"commands": result.get("commands", [])},
        )
        return result

    def improve(
        self,
        project_name: str | None = None,
        roles: list[str] | None = None,
        write_files: bool = True,
    ) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        analyses = self.task_analyzer.analyze_recent(project_name=name, limit=20)
        optimization = self.prompt_optimizer.optimize(project_name=name, roles=roles, write_files=write_files)
        return {
            "project_name": name,
            "analysis_count": len(analyses),
            "analyses": analyses,
            "prompt_optimizations": optimization.get("optimizations", []),
            "metrics": self.evolution_snapshot(name),
        }

    def evolve(
        self,
        project_name: str | None = None,
        plugin_request: str | None = None,
        auto_confirm: bool = False,
        confirm_callback: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        name = project_name or self.get_active_project_name()
        if name:
            self.project_indexer.index_project(project_name=name, force=False)
        improvement = self.improve(project_name=name, write_files=True)
        plugin_result = None
        if plugin_request:
            plugin_result = self.plugin_generator.generate_plugin(
                plugin_request,
                auto_confirm=auto_confirm,
                confirm_callback=confirm_callback,
            )
        return {
            "project_name": name,
            "improvement": improvement,
            "plugin": plugin_result,
            "models": self.router.model_status(),
            "metrics": self.evolution_snapshot(name),
        }

    def model_catalog(self) -> dict[str, Any]:
        return self.router.model_status()

    def select_local_model(self, model: str, backend: str | None = None) -> dict[str, Any]:
        return self.local_model_manager.select_model(model=model, backend=backend)

    def voice_command(
        self,
        transcript: str | None = None,
        timeout: int = 5,
        phrase_time_limit: int = 15,
    ) -> dict[str, Any]:
        return self.voice_interface.listen(
            transcript=transcript,
            timeout=timeout,
            phrase_time_limit=phrase_time_limit,
        )

    def task_status(self, task_id: int | None = None) -> dict[str, Any] | None:
        target_id = task_id or self.get_current_task_id()
        if target_id is not None:
            task = self.task_history.task_with_steps(target_id)
            if task:
                return task
        project_name = self.get_active_project_name()
        latest = self.task_history.latest_task(project_name=project_name, running_only=False)
        if latest is None:
            return None
        return self.task_history.task_with_steps(int(latest["id"]))

    def stop_task(self, task_id: int | None = None) -> dict[str, Any] | None:
        target_id = task_id or self.get_current_task_id()
        if target_id is None:
            project_name = self.get_active_project_name()
            latest = self.task_history.latest_task(project_name=project_name, running_only=True)
            if latest is None:
                return None
            target_id = int(latest["id"])
        self.task_history.request_stop(target_id)
        task = self.task_history.get_task(target_id)
        if task is not None:
            owner_pid = (task.get("metadata", {}) or {}).get("owner_pid")
            try:
                owner_pid_int = int(owner_pid) if owner_pid is not None else None
            except (TypeError, ValueError):
                owner_pid_int = None
            if owner_pid_int is not None and owner_pid_int != os.getpid() and self.task_history._process_alive(owner_pid_int):
                try:
                    os.kill(owner_pid_int, signal.SIGTERM)
                except OSError:
                    pass
                self.task_history.abort_task(
                    target_id,
                    reason="Stop requested; task process terminated and task marked as aborted.",
                )
                if self.get_current_task_id() == target_id:
                    self._set_current_task(None)
            elif owner_pid_int is None or not self.task_history._process_alive(owner_pid_int):
                self.task_history.abort_task(
                    target_id,
                    reason="Stop requested after task runtime had already exited; task marked as aborted.",
                )
                if self.get_current_task_id() == target_id:
                    self._set_current_task(None)
        return self.task_history.task_with_steps(target_id)

    def dashboard(self, task_id: int | None = None):
        project_name = self.get_active_project_name()
        return Group(
            self.task_dashboard.render_task(self.task_status(task_id=task_id)),
            self.task_dashboard.render_reliability(self.reliability_snapshot(project_name)),
        )

    def cleanup_project_artifacts(self, project_name: str, remove_directory: bool = False) -> None:
        self.memory_store.delete_project(project_name)
        self.vector_index.delete_documents(project_name)
        self.project_memory.delete_profile(project_name)
        self.style_profile.delete_profile(project_name)
        self.knowledge_graph.delete_project(project_name)
        self.editor_state.delete_project_state(project_name)
        if remove_directory:
            project_root = self._project_root_for_name(project_name)
            if project_root.exists():
                shutil.rmtree(project_root)

    def audit(self, task: str = "Audit the active project for defects and risks.") -> AuditResult:
        project_context = self.load_active_project(task_hint=task, auto_index=True)
        audit_tools = self._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=True,
            allow_commit=False,
            allow_tests=True,
            allow_editor=True,
        )
        self.publish_agent_activity("auditor", "reviewing", task, project_name=project_context.name)
        self.append_timeline_event(
            title="Audit started",
            status="running",
            agent="auditor",
            project_name=project_context.name,
            message=task,
        )
        audit_result = self.auditor.audit(
            task=task,
            project_context=project_context,
            plan_text="No plan supplied.",
            implementation_text="Review the current state of the codebase.",
            changed_files=[],
            tools=audit_tools,
        )
        self.publish_agent_activity(
            "auditor",
            "idle" if audit_result.passed else "failed",
            "Audit passed." if audit_result.passed else "Audit found issues.",
            project_name=project_context.name,
        )
        self.append_timeline_event(
            title="Audit completed" if audit_result.passed else "Audit found issues",
            status="completed" if audit_result.passed else "failed",
            agent="auditor",
            project_name=project_context.name,
            message=audit_result.text[:240],
        )
        self._record_turns(project_context.name, task, audit_result.text, category="audit")
        return audit_result

    def status(self) -> dict[str, Any]:
        active_project = self.get_active_project_name()
        project_map = self.memory_store.get_project_map(active_project) if active_project else None
        project = self.memory_store.get_project(active_project) if active_project else None
        brain = (
            self.project_brain.load(
                active_project,
                summary=str(project["summary"]) if project else "",
                project_map=project_map,
            )
            if active_project
            else None
        )
        current_task = self.task_status(self.get_current_task_id()) if self.get_current_task_id() else None
        active_file = self.editor_state.active_file(active_project or "__workspace__")
        return {
            "active_project": active_project,
            "workspace_root": str(self.workspace_root),
            "workspace_roots": [str(path) for path in self._search_roots(include_root_dir=False)],
            "models": self.router.describe_models(),
            "indexed_at": project_map.indexed_at if project_map else None,
            "current_task": current_task["task"] if current_task else None,
            "current_task_status": current_task["status"] if current_task else None,
            "active_file": active_file,
            "project_mission": brain.mission if brain else None,
            "project_focus": brain.current_focus if brain else None,
            "next_priority": (self.project_brain.effective_next_priorities(brain)[0] if brain else None),
            "pending_brain_proposals": len(self.project_brain.list_proposals(project_name=active_project, status="pending", limit=200))
            if active_project
            else 0,
        }

    def _toolbox(
        self,
        project_context: ProjectContext,
        auto_approve: bool,
        confirm_overwrite: Callable[[Path], bool] | None = None,
    ) -> Toolbox:
        return Toolbox(
            workspace_root=self.workspace_root,
            project_root=project_context.root,
            project_name=project_context.name,
            embeddings=self.embeddings,
            editor_state=self.editor_state,
            vscode_controller=self.vscode_controller,
            plugin_manager=self.plugin_manager,
            full_access=self.permission_manager.full_access_enabled(),
            require_confirmation=False if self.permission_manager.full_access_enabled() else not auto_approve,
            confirm_overwrite=confirm_overwrite,
            editor_listener=self.editor_listener,
            terminal_listener=self.terminal_listener,
            git_listener=self.git_listener,
            test_listener=self.test_listener,
        )

    def _refresh_project_context(self, project_name: str, task: str) -> ProjectContext:
        self.project_indexer.index_project(project_name=project_name, force=False)
        return self.project_loader.load_project(project_name, task_hint=task, auto_index=False)

    def _extract_changed_files(self, agent_result: AgentResult, project_root: Path) -> list[str]:
        changed: list[str] = []
        for record in agent_result.tool_records:
            if record.name not in {"write_file", "replace_code_block", "append_to_file"}:
                continue
            if not record.success or not isinstance(record.result, dict):
                continue
            path = record.result.get("path")
            if not path:
                continue
            candidate = Path(str(path))
            try:
                changed.append(str(candidate.relative_to(project_root)))
            except ValueError:
                changed.append(str(path))
        return sorted(set(changed))

    def _record_editor_changes(self, project_name: str, agent_result: AgentResult) -> None:
        project_root = self._project_root_for_name(project_name)
        for record in agent_result.tool_records:
            if record.name not in {"write_file", "replace_code_block", "append_to_file"}:
                continue
            if not record.success or not isinstance(record.result, dict):
                continue
            path = str(record.result.get("path", ""))
            diff_preview = str(record.result.get("diff_preview", ""))
            try:
                relative = str(Path(path).resolve().relative_to(project_root))
            except Exception:
                relative = path
            self.editor_state.record_change(
                project_name,
                relative,
                change_type=record.name,
                summary="Agent modified file",
                diff_preview=diff_preview,
            )

    def _record_turns(self, project_name: str, task: str, content: str, category: str) -> None:
        self.memory_store.add_conversation_turn(project_name, "user", task, {"category": category})
        self.memory_store.add_conversation_turn(project_name, "assistant", content, {"category": category})
        self.memory_store.add_memory_entry(project_name, category, content, {"task": task})

    def _resolve_workspace_path(self, path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()

        active_project = self.get_active_project_name()
        search_roots = []
        if active_project:
            search_roots.append(self._project_root_for_name(active_project))
        search_roots.extend(self._search_roots())

        for root in search_roots:
            direct = (root / candidate).resolve()
            if direct.exists():
                return direct
            matches = list(root.rglob(candidate.name))
            if matches:
                return matches[0].resolve()
        raise FileNotFoundError(f"Unable to resolve '{path}' in the workspace or active project.")

    def _slugify_name(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
        return slug or "new-project"

    def _shipping_summary(
        self,
        *,
        result: AutonomousBuildResult,
        commit_changes: bool,
        push_changes: bool,
        deploy_changes: bool,
    ) -> dict[str, Any]:
        gate = dict((result.metadata or {}).get("commit_gate", {}) or {})
        gate_status = str(gate.get("status", "")).lower()
        status = "build_only"
        message = "Shipping pipeline was not requested."
        if not commit_changes:
            status = "build_only"
            message = "Build completed without commit or push."
        elif result.status != "completed":
            status = "build_failed"
            message = "Shipping paused because the autonomous build did not complete cleanly."
        elif gate_status == "pending":
            status = "awaiting_commit"
            message = "Review the diff, then approve the commit to continue shipping."
        elif gate_status == "committed":
            status = "ready_to_push" if push_changes else "committed"
            message = "Commit completed." if not push_changes else "Commit completed. Ready to push."
        elif gate_status in {"failed", "skipped", "rejected"}:
            status = f"commit_{gate_status}"
            message = str(gate.get("message", "") or "Commit stage did not complete.")
        return {
            "requested": True,
            "commit_requested": bool(commit_changes),
            "push_requested": bool(push_changes),
            "deploy_requested": bool(deploy_changes),
            "status": status,
            "message": message,
            "push_result": None,
            "deploy_result": {"configured": False, "status": "not_configured"} if deploy_changes else None,
        }

    def _ship_push(self, task: dict[str, Any], shipping: dict[str, Any]) -> dict[str, Any]:
        project_name = str(task.get("project_name", "") or "")
        project_root = self._project_root_for_name(project_name)
        git_tools = GitTools(project_root, project_name=project_name, git_listener=self.git_listener)
        push_result = git_tools.git_push()
        pushed = bool(push_result.get("pushed")) if isinstance(push_result, dict) else False
        updated = {
            **shipping,
            "status": "pushed" if pushed else "push_failed",
            "message": (
                str(push_result.get("message", "")).strip()
                if isinstance(push_result, dict)
                else "Push finished."
            ),
            "push_result": push_result,
        }
        self.task_history.merge_task_metadata(int(task["id"]), {"shipping": updated})
        return updated

    def _commit_gate_message(self, task: dict[str, Any], gate: dict[str, Any]) -> str:
        pending_steps = gate.get("pending_steps", [])
        if isinstance(pending_steps, list) and len(pending_steps) == 1:
            message = str((pending_steps[0] or {}).get("message", "")).strip()
            if message:
                return message
        if isinstance(pending_steps, list) and len(pending_steps) > 1:
            step_titles = [str((item or {}).get("step_title", "")).strip() for item in pending_steps]
            step_titles = [item for item in step_titles if item]
            if step_titles:
                return f"Complete {task.get('task', 'build task')}: {', '.join(step_titles[:3])}"
        message = str(gate.get("message", "")).strip()
        if message:
            return message
        return f"Complete {task.get('task', 'build task')}"

    def _display_path(self, path: Path) -> str:
        for base in [*self._search_roots(include_root_dir=True), self.root_dir / "projects"]:
            try:
                return str(path.relative_to(base.resolve()))
            except ValueError:
                continue
        return str(path)

    def _workspace_search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if shutil.which("rg") is None:
            return Toolbox(
                workspace_root=self.workspace_root,
                project_root=self.workspace_root,
                project_name="__workspace__",
                embeddings=self.embeddings,
                editor_state=self.editor_state,
                vscode_controller=self.vscode_controller,
                plugin_manager=self.plugin_manager,
                full_access=self.permission_manager.full_access_enabled(),
                require_confirmation=False,
                editor_listener=self.editor_listener,
                terminal_listener=self.terminal_listener,
                git_listener=self.git_listener,
                test_listener=self.test_listener,
            ).code_search.search_codebase(query=query, limit=limit)["matches"]

        command = [
            "rg",
            "--vimgrep",
            "--no-messages",
            "-F",
            "-i",
            "-m",
            "1",
            "--glob",
            "!.git",
            "--glob",
            "!node_modules",
            "--glob",
            "!dist",
            "--glob",
            "!build",
            "--glob",
            "!.venv",
            "--glob",
            "!Library",
            "--glob",
            "!.Trash",
            query,
        ]
        matches: list[dict[str, Any]] = []
        seen_files: set[str] = set()
        for root in self._search_roots(include_root_dir=False):
            result = subprocess.run(
                [*command, str(root)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode not in {0, 1} and not result.stdout.strip():
                continue
            for raw_line in result.stdout.splitlines():
                parts = raw_line.split(":", 3)
                if len(parts) != 4:
                    continue
                file_path, line_number, _column, text = parts
                try:
                    relative_path = str(Path(file_path).resolve().relative_to(root))
                except ValueError:
                    relative_path = file_path
                scoped_path = relative_path if root == self.workspace_root else f"{root.name}/{relative_path}"
                if scoped_path in seen_files:
                    continue
                seen_files.add(scoped_path)
                matches.append(
                    {
                        "kind": "workspace_file",
                        "score": round(max(0.1, 1.0 - (len(matches) * 0.01)), 3),
                        "text": f"line {line_number}: {text.strip()}",
                        "metadata": {
                            "file_path": scoped_path,
                            "line_number": int(line_number),
                            "scope": "workspace",
                            "root": str(root),
                        },
                    }
                )
                if len(matches) >= limit:
                    return matches
        return matches

    def _normalize_project_name(self, project_name: str | None) -> str | None:
        if project_name is None:
            return None
        cleaned = str(project_name).strip()
        if not cleaned:
            return None
        if cleaned.lower() in self.WORKSPACE_ALIASES:
            return None
        return cleaned

    def workspace_context(self) -> ProjectContext:
        snapshot = self.workspace_state.snapshot("__workspace__")
        search_roots = ", ".join(str(path) for path in self._search_roots(include_root_dir=False)) or str(self.workspace_root)
        return ProjectContext(
            name="__workspace__",
            root=self.workspace_root,
            summary=f"Workspace mode across roots: {search_roots}.",
            file_count=0,
            languages={},
            important_files=[],
            architecture_notes=["Workspace mode is broad and not tied to a single indexed project."],
            memory_entries=[
                MemoryEntry(
                    category="workspace",
                    content=f"Workspace search roots: {search_roots}",
                    created_at=utc_now_iso(),
                )
            ],
            code_summaries=[],
            project_map=None,
            active_file=snapshot.open_files[0] if snapshot.open_files else None,
            recent_files=snapshot.open_files[:10],
            recent_changes=snapshot.recent_edits[:8],
            recent_searches=list(self.editor_state.get_project_state("__workspace__").get("recent_searches", [])[:5]),
            workspace_state=snapshot,
            project_profile=ProjectMemoryProfile(
                project_name="__workspace__",
                description=f"Workspace spanning {search_roots}",
                primary_language="Mixed",
            ),
            project_brain=ProjectBrain(
                project_name="__workspace__",
                mission="General workspace mode",
                current_focus="Ad hoc local assistance",
                brain_rules=[
                    "Prefer broad search and direct inspection before asking the user to restate context.",
                    "Treat destructive actions as confirm-only.",
                ],
            ),
            style_profile=StyleProfile(
                project_name="__workspace__",
                indentation="Unknown",
            ),
        )

    def resolve_project_reference(self, project_name: str) -> ProjectReference:
        if project_name in self._ad_hoc_projects:
            return self._ad_hoc_projects[project_name]
        reference = self.roots_registry.resolve(project_name)
        if reference.mode == "ad_hoc":
            self._ad_hoc_projects[reference.key] = reference
        return reference

    def _project_root_for_name(self, project_name: str) -> Path:
        return Path(self.resolve_project_reference(project_name).root).resolve()

    def _project_reference_record(self, reference: ProjectReference) -> dict[str, Any]:
        return {
            "key": reference.key,
            "name": reference.name,
            "display_name": reference.display_name or reference.key,
            "root": reference.root,
            "source_root": reference.source_root,
            "relative_path": reference.relative_path,
            "mode": reference.mode,
            "internal": self._is_internal_project_reference(reference),
        }

    def _is_internal_project_reference(self, reference: ProjectReference) -> bool:
        candidates = {
            str(reference.key).strip().lower(),
            str(reference.name).strip().lower(),
            Path(reference.root).name.strip().lower(),
        }
        internal_prefixes = (
            "__eval__",
            "eval-",
            "ael_",
            "ael-",
            "ext_",
            "ext-",
            "__bench__",
            "bench-",
        )
        internal_fragments = ("benchmark", "__eval__", "fixture", "sandbox")
        for candidate in candidates:
            if any(candidate.startswith(prefix) for prefix in internal_prefixes):
                return True
            if any(fragment in candidate for fragment in internal_fragments):
                return True
        return False

    def _search_roots(self, include_root_dir: bool = True) -> list[Path]:
        roots: list[Path] = []
        seen: set[Path] = set()
        for candidate in [self.workspace_root, *self.roots_registry.search_roots()]:
            resolved = Path(candidate).expanduser().resolve()
            if resolved in seen:
                continue
            roots.append(resolved)
            seen.add(resolved)
        if include_root_dir and self.root_dir not in seen:
            roots.append(self.root_dir)
        return roots

    def _store_task_knowledge(
        self,
        project_name: str,
        task: str,
        solution_text: str,
        changed_files: list[str],
        errors: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        project_root = self._project_root_for_name(project_name)
        self.project_indexer.index_project(project_name=project_name, force=False)
        solution = self.solution_library.capture_task_solution(
            project_name=project_name,
            task=task,
            solution_text=solution_text,
            changed_files=changed_files,
            project_root=project_root,
            errors=errors,
            metadata=metadata,
        )
        if solution is not None:
            self.knowledge_graph.link_solution(project_name, solution)
            self.memory_store.add_memory_entry(
                project_name,
                "solution",
                f"{solution.title}: {solution.description}",
                {
                    "solution_id": solution.solution_id,
                    "tags": solution.tags,
                    "files": changed_files,
                    "errors": errors,
                },
            )
        try:
            self._run_self_improvement(
                project_name=project_name,
                task=task,
                solution_text=solution_text,
                changed_files=changed_files,
                errors=errors,
                metadata=metadata,
            )
        except Exception as exc:
            self.logger.warning("Self-improvement update failed for %s: %s", project_name, exc)

    def _memory_db_path(self) -> Path:
        db_path = self.root_dir / "data" / "boss_memory.db"
        legacy_path = self.root_dir / "data" / "boss_memory.sqlite"
        if not db_path.exists() and legacy_path.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy_path, db_path)
        return db_path

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _update_state(self, **updates: Any) -> None:
        state = self._load_state()
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        self._save_state(state)

    def _set_current_task(self, task_id: int | None) -> None:
        self._update_state(current_task_id=task_id)

    def _reconcile_runtime_state(self) -> None:
        active_project = self.get_active_project_name()
        if active_project:
            try:
                self.resolve_project_reference(active_project)
            except Exception:
                self.clear_active_project()
        reconciled = self.task_history.reconcile_stale_tasks()
        if not reconciled:
            current_task_id = self.get_current_task_id()
            if current_task_id is not None:
                current = self.task_history.get_task(current_task_id)
                if current is None or str(current.get("status", "")).lower() != "running":
                    self._set_current_task(None)
            return

        current_task_id = self.get_current_task_id()
        if current_task_id is not None and any(int(task["id"]) == current_task_id for task in reconciled):
            self._set_current_task(None)

    def publish_agent_activity(
        self,
        agent: str,
        status: str,
        message: str,
        *,
        project_name: str | None = None,
        task_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "agent": agent,
            "status": status,
            "message": message,
            "project_name": project_name,
            "task_id": task_id,
            "metadata": metadata or {},
            "updated_at": self._timestamp(),
        }
        with self._runtime_lock:
            self._agent_activity[agent] = payload
        if hasattr(self, "swarm_manager"):
            self.swarm_manager.publish_event("agent_activity", payload)
        return payload

    def append_timeline_event(
        self,
        *,
        title: str,
        status: str,
        agent: str | None = None,
        project_name: str | None = None,
        task_id: int | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._runtime_lock:
            self._timeline_counter += 1
            payload = {
                "sequence": self._timeline_counter,
                "timestamp": self._timestamp(),
                "title": title,
                "status": status,
                "agent": agent,
                "project_name": project_name,
                "task_id": task_id,
                "message": message or "",
                "metadata": metadata or {},
            }
            self._timeline.append(payload)
        if hasattr(self, "swarm_manager"):
            self.swarm_manager.publish_event("timeline", payload)
        return payload

    def _timestamp(self) -> str:
        return utc_now_iso()

    def _run_self_improvement(
        self,
        project_name: str,
        task: str,
        solution_text: str,
        changed_files: list[str],
        errors: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(metadata or {})
        task_id = payload.get("task_id")
        if task_id is not None:
            task_record = self.task_history.task_with_steps(int(task_id))
            if task_record is not None:
                self.task_analyzer.analyze_task_record(task_record, metadata=payload)
            else:
                self.task_analyzer.analyze_completion(
                    project_name=project_name,
                    task=task,
                    status=str(payload.get("status", "completed")),
                    solution_text=solution_text,
                    changed_files=changed_files,
                    errors=errors,
                    metadata=payload,
                )
        else:
            self.task_analyzer.analyze_completion(
                project_name=project_name,
                task=task,
                status=str(payload.get("status", "completed")),
                solution_text=solution_text,
                changed_files=changed_files,
                errors=errors,
                metadata=payload,
            )
        self.prompt_optimizer.optimize(project_name=project_name, write_files=True)
