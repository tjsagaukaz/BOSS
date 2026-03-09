from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from boss.web.routes import create_routes


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.chat_calls: list[dict[str, object]] = []
        self.active_project = "legion"
        self.workspace_root = "/Users/tj"
        self.commit_status = "pending"
        self.active_thread_id = "thread_1"
        self.threads = [
            {
                "id": "thread_1",
                "project_name": "legion",
                "title": "Architecture chat",
                "updated_at": "now",
                "turn_count": 1,
            }
        ]

    def chat(
        self,
        message: str,
        project_name: str | None = None,
        thread_id: str | None = None,
        execute: bool = False,
        auto_approve: bool = False,
        intent_override: str | None = None,
    ) -> dict[str, object]:
        self.chat_calls.append(
            {
                "message": message,
                "project_name": project_name,
                "thread_id": thread_id,
                "execute": execute,
                "auto_approve": auto_approve,
                "intent_override": intent_override,
            }
        )
        return {
            "reply": "ok",
            "intent": "conversation",
            "mode": "chat",
            "project_name": "legion",
            "thread_id": thread_id or self.active_thread_id,
            "actions": [],
            "result": None,
        }

    def chat_stream(
        self,
        message: str,
        project_name: str | None = None,
        thread_id: str | None = None,
        execute: bool = False,
        auto_approve: bool = False,
        intent_override: str | None = None,
    ):
        self.chat_calls.append(
            {
                "message": message,
                "project_name": project_name,
                "thread_id": thread_id,
                "execute": execute,
                "auto_approve": auto_approve,
                "intent_override": intent_override,
                "stream": True,
            }
        )
        yield {
            "type": "meta",
            "intent": "conversation",
            "mode": "chat",
            "project_name": "legion",
            "thread_id": thread_id or self.active_thread_id,
            "actions": [],
        }
        yield {"type": "delta", "delta": "hello "}
        yield {
            "type": "done",
            "response": {
                "reply": "hello world",
                "intent": "conversation",
                "mode": "chat",
                "project_name": "legion",
                "thread_id": thread_id or self.active_thread_id,
                "actions": [],
                "result": None,
            },
        }

    def conversation_history_snapshot(
        self,
        project_name: str | None = None,
        limit: int = 40,
        thread_id: str | None = None,
    ):
        return [
            {
                "id": 1,
                "thread_id": thread_id or self.active_thread_id,
                "message": "hello",
                "response": "ok",
                "intent": "conversation",
                "metadata": {},
                "created_at": "now",
            }
        ]

    def conversation_threads_snapshot(self, project_name: str | None = None, limit: int = 12):
        return self.threads[:limit]

    def latest_conversation_thread(self, project_name: str | None = None):
        return self.threads[0]

    def create_conversation_thread(self, project_name: str | None = None, title: str = "New chat"):
        thread = {
            "id": f"thread_{len(self.threads) + 1}",
            "project_name": project_name or self.active_project,
            "title": title,
            "updated_at": "now",
            "turn_count": 0,
        }
        self.threads.insert(0, thread)
        self.active_thread_id = str(thread["id"])
        return thread

    def delete_conversation_thread(self, thread_id: str, project_name: str | None = None):
        before = len(self.threads)
        self.threads = [thread for thread in self.threads if thread["id"] != thread_id]
        return len(self.threads) != before

    def agent_activity_snapshot(self):
        return [{"agent": "engineer", "status": "running", "message": "Editing auth.py", "project_name": "legion", "updated_at": "now"}]

    def timeline_snapshot(self, limit: int = 80):
        return [{"sequence": 1, "title": "Engineer started", "status": "running", "agent": "engineer", "project_name": "legion", "message": "auth.py", "timestamp": "now"}]

    def workspace_snapshot(self, project_name: str | None = None):
        return {
            "active_project": project_name or self.active_project,
            "open_files": ["auth.py"],
            "recent_edits": [{"file": "auth.py", "type": "edit", "summary": "Changed auth"}],
            "recent_events": [{"type": "terminal_command", "command": "pytest", "timestamp": "now"}],
            "recent_terminal_commands": [{"command": "pytest", "exit_code": 1, "stdout": "tests failed"}],
            "last_terminal_command": "pytest",
            "last_terminal_result": {"command": "pytest", "exit_code": 1, "stdout": "tests failed"},
            "last_test_results": {"passed": False, "failure_summary": "test_auth failed"},
            "last_git_diff": "auth.py | 4 ++--",
            "last_git_status": {"summary": " M auth.py", "dirty": True},
            "last_commit": {},
            "last_editor_event": {"type": "file_opened", "file": "auth.py"},
            "updated_at": "now",
        }

    def record_workspace_event(
        self,
        event_type: str,
        *,
        project_name: str | None = None,
        path: str | None = None,
        metadata: dict[str, object] | None = None,
    ):
        return {
            "active_project": project_name or self.active_project,
            "last_editor_event": {"type": event_type, "file": path},
            "open_files": [path] if path else [],
            "recent_edits": [],
            "recent_terminal_commands": [],
            "last_terminal_command": "",
            "last_terminal_result": {},
            "last_test_results": {},
            "last_git_diff": "",
            "last_git_status": {},
            "last_commit": {},
            "updated_at": "now",
        }

    def get_active_project_name(self):
        return self.active_project

    def set_active_project(self, project_name: str):
        if project_name in {"workspace", "__workspace__", "all", "none"}:
            self.active_project = None
            return type("Context", (), {"name": "__workspace__", "root": self.workspace_root, "summary": "Workspace mode"})()
        self.active_project = project_name
        return type("Context", (), {"name": project_name, "root": f"/tmp/{project_name}", "summary": f"{project_name} summary"})()

    def create_workspace_folder(
        self,
        path: str | None = None,
        *,
        switch_to: bool = True,
        project_name: str | None = None,
    ):
        return {
            "path": path or "/Users/tj/new-project",
            "project_name": project_name or "new-project",
            "switched": switch_to,
        }

    def health_snapshot(self, project_name: str | None = None):
        return {
            "project_name": project_name or self.active_project,
            "status": "stable",
            "autonomous_success_rate": 0.75,
            "recent_eval_failures": 1,
            "artifact_store_size": 12,
            "workspace_watchers": "active",
            "status_reasons": ["Autonomous success and evaluation stability are within target."],
        }

    def metrics_snapshot(self, project_name: str | None = None):
        return {
            "project_name": project_name or self.active_project,
            "task_runs_recorded": 10,
            "eval_runs_recorded": 4,
            "artifacts_stored": 12,
            "benchmarks_executed": 4,
            "experiments_executed": 1,
            "agent_runtime": [
                {"role": "engineer", "run_count": 5, "avg_duration_seconds": 6.8, "success_rate": 0.8},
            ],
            "run_graph": {
                "avg_nodes_per_run": 3.4,
                "parallel_runs": 0,
                "parallel_mode": "disabled",
                "retries_triggered": 7,
            },
            "token_usage": {"total_tokens": 1234},
        }

    def workspace_roots_snapshot(self, include_internal: bool = False):
        return {
            "primary_root": "/Users/tj",
            "search_roots": ["/Users/tj"],
            "roots": [{"name": "home", "path": "/Users/tj", "mode": "search", "enabled": True}],
            "projects": [{"key": "legion", "display_name": "legion", "root": "/tmp/legion", "source_root": "boss_projects"}],
        }

    def available_project_catalog(self, include_internal: bool = False):
        return [{"key": "legion", "display_name": "legion", "root": "/tmp/legion", "source_root": "boss_projects"}]

    def research(self, query: str, project_name: str | None = None, use_web: bool = True, use_local: bool = True):
        return {
            "query": query,
            "summary": "Research summary",
            "sources": [{"citation": "[W1]", "title": "Example", "url": "https://example.com", "source_type": "web"}],
        }

    def permissions_snapshot(self):
        return {"allow_web_research": True, "allow_mcp": True}

    def mcp_snapshot(self):
        return {"allowed": True, "connectors": [{"name": "filesystem", "transport": "stdio", "healthy": True, "target": "python3", "capabilities": ["files"]}]}

    def portfolio_snapshot(self, include_internal: bool = False):
        return {"project_count": 1, "projects": [{"display_name": "legion", "focus": "reliability", "next_priority": "research mode", "root": "/tmp/legion"}]}

    def command_center_brain(self, project_name: str | None = None):
        return {
            "project_name": project_name or self.active_project or "__workspace__",
            "brain": {
                "project_name": project_name or self.active_project or "__workspace__",
                "mission": "Ship a reliable engineering runtime",
                "current_focus": "Reliability hardening",
                "architecture": ["run graph", "eval harness"],
                "milestones": ["workspace awareness"],
                "recent_progress": ["deterministic evaluation live"],
                "open_problems": ["parallel run graph disabled"],
                "next_priorities": ["parallel run graph validation"],
                "known_risks": ["step timeout risk"],
                "recent_artifacts": [],
                "updated_at": "now",
            },
            "policy": {"update_mode": "confirm"},
            "pending_proposals": 0,
            "artifact_count": 12,
        }

    def next_recommendations(self, project_name: str | None = None, limit: int = 5):
        return [{"title": "Enable parallel run graph", "reason": "Parallel mode is still off.", "source": "brain", "score": 5}]

    def project_roadmap(self, project_name: str | None = None):
        return {
            "project_name": project_name or self.active_project or "__workspace__",
            "mission": "Ship a reliable engineering runtime",
            "focus": "Reliability hardening",
            "completed": ["workspace awareness"],
            "in_progress": ["parallel run graph validation"],
            "future": ["observability dashboard"],
            "pending_proposals": 0,
        }

    def project_risks(self, project_name: str | None = None, limit: int = 8):
        return [{"title": "Parallel run graph not validated", "reason": "Parallel mode is still disabled.", "source": "brain", "severity": "HIGH"}]

    def add_workspace_root(self, **kwargs):
        return {"root": kwargs, "snapshot": self.workspace_roots_snapshot()}

    def remove_workspace_root(self, name: str):
        return {"removed": name, "snapshot": self.workspace_roots_snapshot()}

    def add_mcp_connector(self, **kwargs):
        return {"connector": kwargs, "snapshot": self.mcp_snapshot()}

    def remove_mcp_connector(self, name: str):
        return {"removed": name, "snapshot": self.mcp_snapshot()}

    def recent_runs(self, project_name: str | None = None, limit: int = 20):
        return [
            {
                "kind": "build_task",
                "identifier": 41,
                "status": "completed",
                "project_name": project_name or self.active_project,
                "title": "auth_benchmark_gate",
                "timestamp": "now",
                "artifact_path": "/tmp/artifacts/tasks/task_000041",
                "symbol": "S",
            }
        ][:limit]

    def run_details(self, identifier: str | int, *, kind: str = "auto", project_name: str | None = None):
        if str(identifier) == "404":
            raise FileNotFoundError("Run not found.")
        return {
            "kind": "evaluation_run" if kind == "evaluation" else "build_task",
            "identifier": identifier,
            "project_name": project_name or self.active_project,
            "status": "passed",
            "summary": {
                "task": "auth_benchmark_gate",
                "graph_nodes": 3,
                "retries": 1,
                "runtime_seconds": 12.4,
            },
            "artifact_path": "/tmp/artifacts/evaluations/run_000041",
        }

    def run_diff(self, identifier: str | int, *, kind: str = "auto", project_name: str | None = None):
        if str(identifier) == "404":
            raise FileNotFoundError("Run not found.")
        return {
            "kind": "build_task",
            "identifier": identifier,
            "project_name": project_name or self.active_project,
            "status": "completed",
            "artifact_path": "/tmp/artifacts/tasks/task_000041",
            "files": [
                {
                    "path": "scanner_view.swift",
                    "status": "modified",
                    "diff": "@@ -1 +1 @@\n-old\n+new\n",
                }
            ],
        }

    def approve_run_commit(self, identifier: str | int, *, kind: str = "build", project_name: str | None = None):
        self.commit_status = "committed"
        return {
            "approved": True,
            "status": "committed",
            "message": "Implement scanner view",
            "task": self.task_status(),
            "commit_result": {"committed": True, "commit": "abc123", "message": "Implement scanner view"},
        }

    def reject_run_commit(
        self,
        identifier: str | int,
        *,
        kind: str = "build",
        project_name: str | None = None,
        reason: str | None = None,
    ):
        self.commit_status = "rejected"
        return {
            "rejected": True,
            "status": "rejected",
            "message": reason or "Commit rejected",
            "task": self.task_status(),
        }

    def task_status(self, task_id: int | None = None):
        return {
            "id": 17,
            "task": "Add barcode scanner",
            "status": "running",
            "total_steps": 3,
            "files_changed": ["scanner_view.swift"],
            "metadata": {"commit_gate": {"status": self.commit_status, "pending_steps": [{"step_index": 1, "step_title": "Implement scanner", "message": "Implement scanner view"}]}},
            "steps": [
                {"step_index": 0, "title": "Architect plan", "status": "completed"},
                {"step_index": 1, "title": "Implement scanner", "status": "running", "metadata": {"commit_gate": {"status": self.commit_status}}},
            ],
        }


class _FakeSwarmManager:
    def swarm_snapshot(self):
        return {"runs": [], "tasks": [], "stats": {}}

    def subscribe(self):
        raise RuntimeError("not used")

    def unsubscribe(self, subscriber):
        return None


def test_chat_route_returns_orchestrator_response():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post("/chat", json={"message": "plan auth", "execute": True, "intent": "plan"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "ok"
    assert orchestrator.chat_calls[0]["message"] == "plan auth"
    assert orchestrator.chat_calls[0]["execute"] is True
    assert orchestrator.chat_calls[0]["intent_override"] == "plan"


def test_research_route_returns_report():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post("/research", json={"query": "auth research"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "Research summary"
    assert payload["sources"][0]["citation"] == "[W1]"


def test_chat_history_route_returns_history():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.get("/chat/history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_thread_id"] == "thread_1"
    assert payload["threads"][0]["title"] == "Architecture chat"
    assert len(payload["history"]) == 1
    assert payload["history"][0]["message"] == "hello"


def test_chat_thread_routes_create_and_delete_threads():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    created = client.post("/chat/threads", json={"title": "Fresh chat"})
    assert created.status_code == 200
    assert created.json()["title"] == "Fresh chat"

    deleted = client.delete(f"/chat/threads/{created.json()['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_chat_route_requires_message():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post("/chat", json={})

    assert response.status_code == 400


def test_projects_active_route_switches_project():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post("/projects/active", json={"project_name": "myfiltr"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_project"] == "myfiltr"
    assert orchestrator.active_project == "myfiltr"


def test_projects_active_route_can_switch_to_workspace_mode():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post("/projects/active", json={"project_name": "__workspace__"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_project"] is None


def test_projects_create_route_creates_workspace_folder():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post("/projects/create", json={"path": "new-app", "switch_to": True})

    assert response.status_code == 200
    assert response.json()["path"] == "new-app"


def test_roots_route_returns_workspace_roots():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.get("/roots")

    assert response.status_code == 200
    payload = response.json()
    assert payload["primary_root"] == "/Users/tj"
    assert payload["roots"][0]["name"] == "home"


def test_portfolio_permissions_and_mcp_routes():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    portfolio = client.get("/portfolio")
    permissions = client.get("/permissions")
    mcp = client.get("/mcp")

    assert portfolio.status_code == 200
    assert portfolio.json()["project_count"] == 1
    assert permissions.status_code == 200
    assert permissions.json()["allow_web_research"] is True
    assert mcp.status_code == 200
    assert mcp.json()["connectors"][0]["name"] == "filesystem"


def test_command_center_brain_and_strategy_routes():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    brain = client.get("/brain")
    next_actions = client.get("/next")
    roadmap = client.get("/roadmap")
    risks = client.get("/risks")

    assert brain.status_code == 200
    assert brain.json()["brain"]["mission"] == "Ship a reliable engineering runtime"
    assert next_actions.status_code == 200
    assert next_actions.json()["recommendations"][0]["title"] == "Enable parallel run graph"
    assert roadmap.status_code == 200
    assert roadmap.json()["focus"] == "Reliability hardening"
    assert risks.status_code == 200
    assert risks.json()["risks"][0]["severity"] == "HIGH"


def test_command_center_route_returns_bundled_snapshot():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.get("/command-center")

    assert response.status_code == 200
    payload = response.json()
    assert payload["projects"]["active_project"] == "legion"
    assert payload["brain"]["brain"]["current_focus"] == "Reliability hardening"
    assert payload["workspace"]["recent_terminal_commands"][0]["command"] == "pytest"
    assert payload["history"]["active_thread_id"] == "thread_1"
    assert payload["history"]["threads"][0]["title"] == "Architecture chat"
    assert payload["history"]["history"][0]["message"] == "hello"


def test_chat_stream_route_returns_ndjson_events():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    with client.stream("POST", "/chat/stream", json={"message": "hello"}) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert '"type": "meta"' in lines[0]
    assert '"thread_id": "thread_1"' in lines[0]
    assert '"type": "delta"' in lines[1]
    assert '"type": "done"' in lines[2]
    assert orchestrator.chat_calls[0]["stream"] is True


def test_activity_and_timeline_routes_return_runtime_state():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    activity = client.get("/activity")
    timeline = client.get("/timeline")

    assert activity.status_code == 200
    assert timeline.status_code == 200
    assert activity.json()["activities"][0]["agent"] == "engineer"
    assert timeline.json()["events"][0]["title"] == "Engineer started"


def test_workspace_routes_return_snapshot_and_accept_events():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    snapshot = client.get("/workspace")
    updated = client.post("/workspace/events", json={"event": "file_opened", "path": "billing.py"})

    assert snapshot.status_code == 200
    assert updated.status_code == 200
    assert snapshot.json()["open_files"][0] == "auth.py"
    assert updated.json()["last_editor_event"]["type"] == "file_opened"
    assert updated.json()["open_files"][0] == "billing.py"


def test_workspace_event_route_accepts_top_level_terminal_payload():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.post(
        "/workspace/events",
        json={
            "event": "terminal_command",
            "project_name": "legion",
            "command": "pytest",
            "workdir": "/tmp/legion",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_project"] == "legion"


def test_observability_routes_return_aggregated_data():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    health = client.get("/health")
    metrics = client.get("/metrics")
    runs = client.get("/runs?limit=5")
    details = client.get("/runs/41?kind=evaluation")

    assert health.status_code == 200
    assert metrics.status_code == 200
    assert runs.status_code == 200
    assert details.status_code == 200
    assert health.json()["status"] == "stable"
    assert metrics.json()["task_runs_recorded"] == 10
    assert runs.json()["runs"][0]["identifier"] == 41
    assert details.json()["summary"]["graph_nodes"] == 3


def test_loop_status_route_returns_current_autonomous_task():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.get("/loop/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["id"] == 17
    assert payload["task"]["status"] == "running"


def test_run_diff_and_commit_routes_return_review_data():
    app = FastAPI()
    orchestrator = _FakeOrchestrator()
    app.include_router(create_routes(orchestrator, _FakeSwarmManager()))
    client = TestClient(app)

    diff = client.get("/runs/41/diff?kind=build")
    approve = client.post("/runs/41/commit", json={"kind": "build"})
    reject = client.post("/runs/41/commit/reject", json={"kind": "build", "reason": "Needs revision"})

    assert diff.status_code == 200
    assert diff.json()["files"][0]["path"] == "scanner_view.swift"
    assert approve.status_code == 200
    assert approve.json()["status"] == "committed"
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"


def test_run_details_route_returns_not_found_for_unknown_run():
    app = FastAPI()
    app.include_router(create_routes(_FakeOrchestrator(), _FakeSwarmManager()))
    client = TestClient(app)

    response = client.get("/runs/404?kind=evaluation")

    assert response.status_code == 404
