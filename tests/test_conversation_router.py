from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace

from boss.conversation import ConversationHistoryStore, ConversationRouter
from boss.types import (
    AgentResult,
    MemoryEntry,
    ModelRunResult,
    ProjectContext,
    ProjectMap,
    ProjectMemoryProfile,
    ResearchReport,
    ResearchSource,
    StyleProfile,
)


def _project_context() -> ProjectContext:
    return ProjectContext(
        name="legion",
        root=Path("/tmp/legion"),
        summary="Legion backend",
        file_count=12,
        languages={"Python": 12},
        important_files=["app.py"],
        architecture_notes=["Routes call services."],
        memory_entries=[MemoryEntry(category="decision", content="Prefer FastAPI patterns.", created_at="now")],
        code_summaries=[],
        project_map=ProjectMap(
            name="legion",
            overview="FastAPI app",
            languages={"Python": 12},
            main_modules=["api/", "services/"],
            entry_points=["app.py"],
            key_files=["app.py"],
            dependencies=["fastapi", "pytest"],
        ),
        relevant_files=[],
        semantic_results=[],
        relevant_memories=[],
        active_file=None,
        recent_files=[],
        recent_changes=[],
        recent_searches=[],
        project_profile=ProjectMemoryProfile(
            project_name="legion",
            description="Legion backend",
            primary_language="Python",
            frameworks=["FastAPI"],
            architecture="layered service api",
            key_modules=["api", "services"],
        ),
        style_profile=StyleProfile(
            project_name="legion",
            indentation="4 spaces",
            naming_conventions=["snake_case"],
            code_structure="small modules",
            test_style="pytest",
            error_handling_style="exceptions",
        ),
        relevant_solutions=[],
        similar_tasks=[],
        graph_insights=[],
        related_projects=[],
    )


class _FakeProjectLoader:
    def load_project(self, project_name: str, task_hint: str | None = None, auto_index: bool = True) -> ProjectContext:
        return _project_context()


class _FakeModelClient:
    provider = "openai"
    model = "gpt-5.4"

    def generate(self, prompt: str, system_prompt: str = "", tools=None, stream: bool = False, on_text_delta=None):
        text = "Here is the current architecture and the next safe action."
        if stream and on_text_delta:
            for chunk in ("Here is ", "the current architecture ", "and the next safe action."):
                time.sleep(0.01)
                on_text_delta(chunk)
        return ModelRunResult(
            text=text,
            provider=self.provider,
            model=self.model,
            duration_seconds=0.2,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


class _FakeRouter:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def client_for_request(self, role: str, prompt: str, tools=None, request_options=None):
        assert role == "conversation"
        return _FakeModelClient()

    def record_model_run(self, **payload) -> None:
        self.records.append(payload)


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.project_loader = _FakeProjectLoader()
        self.plan_calls: list[str] = []
        self.build_calls: list[str] = []
        self.ship_calls: list[str] = []
        self.folder_calls: list[tuple[str, bool, str | None]] = []
        self.project_brain_signals: list[tuple[str, str | None]] = []

    def get_active_project_name(self) -> str:
        return "legion"

    def plan(self, task: str) -> AgentResult:
        self.plan_calls.append(task)
        return AgentResult(agent_name="architect", provider="anthropic", model="claude-opus-4-6", text=f"Plan for {task}")

    def status(self):
        return {
            "active_project": "legion",
            "models": "conversation=openai:gpt-5.4",
            "indexed_at": "now",
            "current_task": None,
            "current_task_status": None,
            "active_file": None,
        }

    def build(self, task: str, auto_approve: bool = False, project_name: str | None = None, commit_changes: bool = True):
        self.build_calls.append(task)
        return SimpleNamespace(status="completed", task_id=7, final_result=f"Built {task}", changed_files=["app.py"])

    def ship(
        self,
        task: str,
        auto_approve: bool = False,
        project_name: str | None = None,
        commit_changes: bool = True,
        push_changes: bool = True,
        deep: bool = False,
        max_iterations: int = 10,
    ):
        self.ship_calls.append(task)
        return SimpleNamespace(
            status="completed",
            task_id=8,
            goal=task,
            final_result=f"Shipped {task}",
            changed_files=["app.py"],
            step_results=[],
            metadata={"shipping": {"status": "awaiting_commit", "message": "Review the diff, then approve the commit to continue shipping."}},
        )

    def task_status(self, task_id: int | None = None):
        return {
            "id": 7,
            "task": "Build auth middleware",
            "status": "running",
            "total_steps": 3,
            "files_changed": ["app.py"],
            "errors": [],
            "steps": [
                {"step_index": 0, "title": "Plan", "status": "completed"},
                {"step_index": 1, "title": "Implement", "status": "running"},
            ],
        }

    def stop_task(self, task_id: int | None = None):
        return {
            "id": 7,
            "status": "aborted",
        }

    def run_golden_tasks(self):
        return {"suite_runs_attempted": 1, "suite_runs_passed": 1, "suite_runs_skipped": 0}

    def recent_runs(self, project_name: str | None = None, limit: int = 20):
        return [{"title": "auth benchmark", "kind": "evaluation", "status": "completed"}]

    def available_project_catalog(self, include_internal: bool = False):
        return [
            {"key": "boss", "display_name": "BOSS", "root": "/Users/tj/BOSS"},
            {"key": "legion", "display_name": "Legion", "root": "/Users/tj/Legion"},
        ]

    def workspace_roots_snapshot(self, include_internal: bool = False):
        return {"search_roots": ["/Users/tj"]}

    def permissions_snapshot(self):
        return {
            "workspace_write_mode": "auto",
            "project_write_mode": "auto",
            "destructive_mode": "confirm",
            "writable_roots": ["/Users/tj"],
        }

    def portfolio_snapshot(self, include_internal: bool = False):
        return {
            "projects": [
                {"display_name": "BOSS", "focus": "operator mode", "next_priority": "UI polish"},
                {"display_name": "Legion", "focus": "runtime reliability", "next_priority": "parallel run graph"},
            ]
        }

    def research(self, query: str, project_name: str | None = None, use_web: bool = True, use_local: bool = True):
        return ResearchReport(
            query=query,
            summary="Research summary",
            sources=[ResearchSource(source_type="web", title="Example", citation="[W1]", url="https://example.com")],
            project_name=project_name,
        )

    def create_workspace_folder(
        self,
        path: str | None = None,
        *,
        switch_to: bool = True,
        project_name: str | None = None,
    ):
        self.folder_calls.append((path or "", switch_to, project_name))
        return {
            "path": f"/Users/tj/{path or 'new-project'}",
            "relative_path": path or "new-project",
            "created": True,
            "switched": switch_to,
            "project_name": path or "new-project",
            "message": f"Created {path or 'new-project'} and switched us there.",
        }

    def note_project_brain_signal(self, message: str, project_name: str | None = None) -> bool:
        self.project_brain_signals.append((message, project_name))
        return True


def test_conversation_router_returns_chat_reply_and_records_history(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("Explain the current architecture.")

    assert response["intent"] == "conversation"
    assert response["mode"] == "chat"
    assert "architecture" in response["reply"].lower()
    history = history_store.recent(project_name="legion")
    assert len(history) == 1
    assert history[0]["message"] == "Explain the current architecture."


def test_conversation_router_blocks_direct_task_like_requests_without_auto_approve(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("help me build JWT authentication")

    assert response["intent"] == "build"
    assert response["mode"] == "blocked"
    assert response["actions"] == []
    assert "auto-approve" in response["reply"].lower()


def test_conversation_router_executes_task_like_requests_when_auto_approve_is_enabled(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("fix the auth middleware", auto_approve=True)

    assert response["intent"] == "build"
    assert response["mode"] == "executed"
    assert response["conversation_type"] == "execution"
    assert response["reply"].startswith("Ok.")
    assert orchestrator.build_calls == ["fix the auth middleware"]


def test_conversation_router_blocks_mutating_execution_without_auto_approve(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("build JWT authentication", execute=True, auto_approve=False)

    assert response["intent"] == "build"
    assert response["mode"] == "blocked"
    assert "auto-approve" in response["reply"].lower()


def test_conversation_router_executes_explicit_plan_commands(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("plan add JWT authentication")

    assert response["intent"] == "plan"
    assert response["mode"] == "executed"
    assert orchestrator.plan_calls == ["add JWT authentication"]
    assert response["reply"] == "Plan for add JWT authentication"


def test_conversation_router_detects_natural_planning_requests(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("design the architecture for auth middleware")

    assert response["intent"] == "plan"
    assert response["mode"] == "executed"
    assert response["conversation_type"] == "planning"
    assert orchestrator.plan_calls == ["design the architecture for auth middleware"]


def test_conversation_router_supports_slash_build_commands(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("/build auth system")

    assert response["intent"] == "build"
    assert response["mode"] == "executed"
    assert response["conversation_type"] == "execution"
    assert orchestrator.build_calls == ["auth system"]


def test_conversation_router_supports_benchmark_intent(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("/benchmark")

    assert response["intent"] == "benchmark"
    assert response["mode"] == "executed"
    assert "benchmark signal" in response["reply"].lower()


def test_conversation_router_supports_autobuild_alias(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("/autobuild auth middleware")

    assert response["intent"] == "build"
    assert response["mode"] == "executed"
    assert orchestrator.build_calls == ["auth middleware"]


def test_conversation_router_supports_ship_alias(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("/ship barcode scanner")

    assert response["intent"] == "ship"
    assert response["mode"] == "executed"
    assert orchestrator.ship_calls == ["barcode scanner"]


def test_conversation_router_supports_loop_status(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("/loop status")

    assert response["intent"] == "loop_status"
    assert response["mode"] == "executed"
    assert "autonomous loop" in response["reply"].lower()
    assert "loop #7" in response["reply"].lower()


def test_conversation_router_supports_stop_command(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("/stop")

    assert response["intent"] == "stop"
    assert response["mode"] == "executed"
    assert "stop requested" in response["reply"].lower()


def test_conversation_router_executes_folder_creation_requests_immediately(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("can you create a new folder on my mac and we work from that", project_name="ai-behavioral-prediction")

    assert response["intent"] == "create_folder"
    assert response["mode"] == "executed"
    assert response["conversation_type"] == "execution"
    assert response["reply"].startswith("Ok.")
    assert orchestrator.folder_calls == [("new-project", True, "ai-behavioral-prediction")]


def test_conversation_router_executes_navigation_to_new_folder_requests_immediately(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("can you navigate to a new folder please we will start a fresh project", project_name="ai-behavioral-prediction")

    assert response["intent"] == "create_folder"
    assert response["mode"] == "executed"
    assert response["reply"].startswith("Ok.")
    assert orchestrator.folder_calls == [("new-project", True, "ai-behavioral-prediction")]


def test_conversation_router_executes_fresh_start_requests_immediately(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("we need to get out of this workspace and start something fresh", project_name="ai-behavioral-prediction")

    assert response["intent"] == "create_folder"
    assert response["mode"] == "executed"
    assert response["reply"].startswith("Ok.")
    assert orchestrator.folder_calls == [("new-project", True, "ai-behavioral-prediction")]


def test_conversation_router_stream_message_yields_meta_delta_and_done(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    events = list(router.stream_message("Explain the current architecture."))

    assert events[0]["type"] == "meta"
    assert events[0]["mode"] == "chat"
    assert events[0]["conversation_type"] == "discussion"
    deltas = [event["delta"] for event in events if event["type"] == "delta"]
    assert "".join(deltas) == "Here is the current architecture and the next safe action."
    assert events[-1]["type"] == "done"
    assert events[-1]["response"]["intent"] == "conversation"
    history = history_store.recent(project_name="legion")
    assert len(history) == 1
    assert history[0]["response"] == "Here is the current architecture and the next safe action."


def test_conversation_router_records_project_brain_signal(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    router.handle_message("we are focusing on reliability hardening right now")

    assert orchestrator.project_brain_signals == [("we are focusing on reliability hardening right now", "legion")]


def test_conversation_router_short_circuits_small_talk(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("hey boss")

    assert response["intent"] == "conversation"
    assert response["mode"] == "chat"
    assert response["reply"] == "Hey."
    assert orchestrator.project_brain_signals == []


def test_conversation_router_executes_research_intent(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("research auth middleware best practices")

    assert response["intent"] == "research"
    assert response["mode"] == "executed"
    assert "Research summary" in response["reply"]
    history = history_store.recent(project_name="legion")
    assert len(history) == 1
    assert history[0]["message"] == "research auth middleware best practices"


def test_conversation_router_stream_small_talk_yields_short_reply(tmp_path):
    orchestrator = _FakeOrchestrator()
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(orchestrator, history_store, _FakeRouter(), tmp_path)

    events = list(router.stream_message("hey boss"))

    assert events[0]["type"] == "meta"
    assert events[0]["mode"] == "chat"
    deltas = [event["delta"] for event in events if event["type"] == "delta"]
    assert len(deltas) == 1
    assert "hey" in deltas[0].lower()
    assert events[-1]["type"] == "done"
    assert orchestrator.project_brain_signals == []


def test_persona_prompt_is_not_founder_roleplay(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    persona_prompt = router._persona_system_prompt()

    assert "co-ceo" not in persona_prompt.lower()
    assert "plain language" in persona_prompt.lower()


def test_conversation_router_can_cancel_stream(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    events = router.stream_message("Explain the current architecture.")
    meta = next(events)
    assert meta["type"] == "meta"
    assert router.cancel_stream(project_name="legion", stream_id=meta["stream_id"]) is True
    remaining = list(events)

    assert any(event["type"] == "interrupted" for event in remaining)
    assert remaining[-1]["type"] == "done"
    assert remaining[-1]["response"]["mode"] == "interrupted"


def test_conversation_router_can_report_visible_projects(tmp_path):
    history_store = ConversationHistoryStore(tmp_path / "boss.db")
    router = ConversationRouter(_FakeOrchestrator(), history_store, _FakeRouter(), tmp_path)

    response = router.handle_message("what projects can you see on my mac")

    assert response["intent"] == "projects"
    assert response["mode"] == "executed"
    assert "BOSS" in response["reply"]
    assert "Legion" in response["reply"]
