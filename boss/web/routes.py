from __future__ import annotations

import asyncio
import dataclasses
import json
import queue
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse


def create_routes(orchestrator, swarm_manager) -> APIRouter:
    router = APIRouter()

    @router.get("/tasks")
    def tasks() -> dict[str, Any]:
        return _serialize(swarm_manager.swarm_snapshot())

    @router.post("/tasks")
    def start_task(body: dict[str, Any]) -> dict[str, Any]:
        task = str(body.get("task", "")).strip()
        if not task:
            raise HTTPException(status_code=400, detail="Task text is required.")
        project_name = body.get("project_name")
        auto_approve = bool(body.get("auto_approve", False))
        return _serialize(orchestrator.start_swarm(task, project_name=project_name, auto_approve=auto_approve))

    @router.post("/tasks/{run_id}/pause")
    def pause_task(run_id: str) -> dict[str, Any]:
        payload = orchestrator.pause_swarm(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _serialize(payload)

    @router.post("/tasks/{run_id}/resume")
    def resume_task(run_id: str) -> dict[str, Any]:
        payload = orchestrator.resume_swarm(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _serialize(payload)

    @router.post("/tasks/{run_id}/cancel")
    def cancel_task(run_id: str) -> dict[str, Any]:
        payload = orchestrator.cancel_swarm(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _serialize(payload)

    @router.get("/agents")
    def agents() -> dict[str, Any]:
        return {"agents": _serialize(orchestrator.available_agents())}

    @router.get("/projects")
    def projects(include_internal: bool = False) -> dict[str, Any]:
        active = orchestrator.get_active_project_name()
        project_map = None
        if active:
            try:
                project_map = orchestrator.cached_project_map(active)
            except Exception:
                project_map = None
        return {
            "active_project": active,
            "projects": orchestrator.available_projects(include_internal=include_internal),
            "project_catalog": orchestrator.available_project_catalog(include_internal=include_internal),
            "workspace_roots": orchestrator.workspace_roots_snapshot(include_internal=include_internal),
            "status": orchestrator.status(),
            "project_map": _serialize(project_map),
        }

    @router.post("/projects/active")
    def set_active_project(body: dict[str, Any]) -> dict[str, Any]:
        project_name = str(body.get("project_name", "")).strip()
        if not project_name:
            raise HTTPException(status_code=400, detail="Project name is required.")
        context = orchestrator.set_active_project(project_name)
        return {
            "active_project": None if context.name == "__workspace__" else context.name,
            "project": _serialize(
                {
                    "name": context.name,
                    "root": str(context.root),
                    "summary": context.summary,
                }
            ),
        }

    @router.get("/workspace")
    def workspace(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.workspace_snapshot(project_name=project_name))

    @router.get("/roots")
    def roots(include_internal: bool = False) -> dict[str, Any]:
        return _serialize(orchestrator.workspace_roots_snapshot(include_internal=include_internal))

    @router.post("/roots")
    def add_root(body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        path = str(body.get("path", "")).strip()
        if not name or not path:
            raise HTTPException(status_code=400, detail="Root name and path are required.")
        return _serialize(
            orchestrator.add_workspace_root(
                name=name,
                path=path,
                mode=str(body.get("mode", "projects")),
                include_root=bool(body.get("include_root", False)),
                discover_children=bool(body.get("discover_children", True)),
                max_depth=int(body.get("max_depth", 1)),
            )
        )

    @router.delete("/roots/{name}")
    def remove_root(name: str) -> dict[str, Any]:
        try:
            return _serialize(orchestrator.remove_workspace_root(name))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/workspace/events")
    def workspace_event(body: dict[str, Any]) -> dict[str, Any]:
        event_type = str(body.get("event", "")).strip()
        if not event_type:
            raise HTTPException(status_code=400, detail="Workspace event type is required.")
        project_name = body.get("project_name")
        path = body.get("path") or body.get("file")
        base_metadata = body.get("metadata", {})
        if base_metadata is not None and not isinstance(base_metadata, dict):
            raise HTTPException(status_code=400, detail="Workspace event metadata must be an object.")
        metadata = dict(base_metadata or {})
        for key, value in body.items():
            if key in {"event", "project_name", "path", "file", "metadata"}:
                continue
            metadata[key] = value
        return _serialize(
            orchestrator.record_workspace_event(
                event_type,
                project_name=str(project_name) if project_name else None,
                path=str(path) if path else None,
                metadata=metadata if isinstance(metadata, dict) else None,
            )
        )

    @router.get("/memory")
    def memory(project_name: Optional[str] = None) -> dict[str, Any]:
        target = project_name or orchestrator.get_active_project_name()
        if not target:
            return {}
        return _serialize(orchestrator.memory_snapshot(target))

    @router.get("/portfolio")
    def portfolio(include_internal: bool = False) -> dict[str, Any]:
        return _serialize(orchestrator.portfolio_snapshot(include_internal=include_internal))

    @router.get("/brain")
    def brain(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.command_center_brain(project_name=project_name))

    @router.get("/next")
    def next_actions(project_name: Optional[str] = None, limit: int = 5) -> dict[str, Any]:
        return {"recommendations": _serialize(orchestrator.next_recommendations(project_name=project_name, limit=limit))}

    @router.get("/roadmap")
    def roadmap(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.project_roadmap(project_name=project_name))

    @router.get("/risks")
    def risks(project_name: Optional[str] = None, limit: int = 8) -> dict[str, Any]:
        return {"risks": _serialize(orchestrator.project_risks(project_name=project_name, limit=limit))}

    @router.get("/chat/history")
    def chat_history(project_name: Optional[str] = None, limit: int = 40) -> dict[str, Any]:
        return {
            "history": _serialize(
                orchestrator.conversation_history_snapshot(project_name=project_name, limit=limit)
            )
        }

    @router.get("/command-center")
    def command_center(
        project_name: Optional[str] = None,
        include_internal: bool = False,
        limit: int = 40,
    ) -> dict[str, Any]:
        workspace = orchestrator.workspace_snapshot(project_name=project_name)
        roots = orchestrator.workspace_roots_snapshot(include_internal=include_internal)
        active_project = workspace.get("active_project")
        if active_project == "__workspace__":
            active_project = None
        return {
            "projects": {
                "active_project": active_project,
                "project_catalog": _serialize(roots.get("projects", [])),
            },
            "brain": _serialize(orchestrator.command_center_brain(project_name=project_name)),
            "next": {
                "recommendations": _serialize(
                    orchestrator.next_recommendations(project_name=project_name, limit=5)
                )
            },
            "risks": {
                "risks": _serialize(orchestrator.project_risks(project_name=project_name, limit=8))
            },
            "workspace": _serialize(workspace),
            "activity": {"activities": _serialize(orchestrator.agent_activity_snapshot())},
            "timeline": {"events": _serialize(orchestrator.timeline_snapshot(limit=80))},
            "health": _serialize(orchestrator.health_snapshot(project_name=project_name)),
            "metrics": _serialize(orchestrator.metrics_snapshot(project_name=project_name)),
            "permissions": _serialize(orchestrator.permissions_snapshot()),
            "runs": {"runs": _serialize(orchestrator.recent_runs(project_name=project_name, limit=20))},
            "history": {
                "history": _serialize(
                    orchestrator.conversation_history_snapshot(project_name=project_name, limit=limit)
                )
            },
            "roots": _serialize(roots),
        }

    @router.post("/chat")
    def chat(body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message is required.")
        project_name = body.get("project_name")
        execute = bool(body.get("execute", False))
        auto_approve = bool(body.get("auto_approve", False))
        intent_override = body.get("intent")
        return _serialize(
            orchestrator.chat(
                message,
                project_name=project_name,
                execute=execute,
                auto_approve=auto_approve,
                intent_override=str(intent_override) if intent_override else None,
            )
        )

    @router.post("/research")
    def research(body: dict[str, Any]) -> dict[str, Any]:
        query = str(body.get("query", "")).strip()
        if not query:
            raise HTTPException(status_code=400, detail="Research query is required.")
        project_name = body.get("project_name")
        use_web = bool(body.get("use_web", True))
        use_local = bool(body.get("use_local", True))
        return _serialize(
            orchestrator.research(
                query,
                project_name=project_name,
                use_web=use_web,
                use_local=use_local,
            )
        )

    @router.post("/chat/stream")
    def chat_stream(body: dict[str, Any]) -> StreamingResponse:
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message is required.")
        project_name = body.get("project_name")
        execute = bool(body.get("execute", False))
        auto_approve = bool(body.get("auto_approve", False))
        intent_override = body.get("intent")

        def stream_events():
            try:
                for event in orchestrator.chat_stream(
                    message,
                    project_name=project_name,
                    execute=execute,
                    auto_approve=auto_approve,
                    intent_override=str(intent_override) if intent_override else None,
                ):
                    yield json.dumps(_serialize(event), ensure_ascii=False) + "\n"
            except Exception as exc:
                yield json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False) + "\n"

        return StreamingResponse(stream_events(), media_type="application/x-ndjson")

    @router.post("/chat/cancel")
    def chat_cancel(body: dict[str, Any]) -> dict[str, Any]:
        project_name = body.get("project_name")
        stream_id = body.get("stream_id")
        cancelled = orchestrator.cancel_chat_stream(
            project_name=str(project_name) if project_name else None,
            stream_id=str(stream_id) if stream_id else None,
        )
        return {"cancelled": cancelled}

    @router.get("/activity")
    def activity() -> dict[str, Any]:
        return {"activities": _serialize(orchestrator.agent_activity_snapshot())}

    @router.get("/timeline")
    def timeline(limit: int = 80) -> dict[str, Any]:
        return {"events": _serialize(orchestrator.timeline_snapshot(limit=limit))}

    @router.get("/evolution")
    def evolution(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.evolution_snapshot(project_name))

    @router.get("/models")
    def models() -> dict[str, Any]:
        return _serialize(orchestrator.model_catalog())

    @router.get("/permissions")
    def permissions() -> dict[str, Any]:
        return _serialize(orchestrator.permissions_snapshot())

    @router.get("/mcp")
    def mcp() -> dict[str, Any]:
        return _serialize(orchestrator.mcp_snapshot())

    @router.post("/mcp")
    def add_mcp(body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        transport = str(body.get("transport", "")).strip()
        target = str(body.get("target", "")).strip()
        if not name or not transport or not target:
            raise HTTPException(status_code=400, detail="Connector name, transport, and target are required.")
        return _serialize(
            orchestrator.add_mcp_connector(
                name=name,
                transport=transport,
                target=target,
                args=[str(item) for item in body.get("args", [])] if isinstance(body.get("args"), list) else None,
                capabilities=[str(item) for item in body.get("capabilities", [])]
                if isinstance(body.get("capabilities"), list)
                else None,
                enabled=bool(body.get("enabled", True)),
                description=str(body.get("description", "")),
            )
        )

    @router.delete("/mcp/{name}")
    def remove_mcp(name: str) -> dict[str, Any]:
        try:
            return _serialize(orchestrator.remove_mcp_connector(name))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/reliability")
    def reliability(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.reliability_snapshot(project_name))

    @router.get("/health")
    def health(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.health_snapshot(project_name))

    @router.get("/metrics")
    def metrics(project_name: Optional[str] = None) -> dict[str, Any]:
        return _serialize(orchestrator.metrics_snapshot(project_name))

    @router.get("/runs")
    def runs(project_name: Optional[str] = None, limit: int = 20) -> dict[str, Any]:
        return {"runs": _serialize(orchestrator.recent_runs(project_name=project_name, limit=limit))}

    @router.get("/loop/status")
    def loop_status(task_id: Optional[int] = None) -> dict[str, Any]:
        task = orchestrator.task_status(task_id=task_id)
        return {"task": _serialize(task)}

    @router.get("/runs/{run_id}")
    def run_details(run_id: str, kind: str = "auto", project_name: Optional[str] = None) -> dict[str, Any]:
        try:
            return _serialize(orchestrator.run_details(run_id, kind=kind, project_name=project_name))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/diff")
    def run_diff(run_id: str, kind: str = "auto", project_name: Optional[str] = None) -> dict[str, Any]:
        try:
            return _serialize(orchestrator.run_diff(run_id, kind=kind, project_name=project_name))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/commit")
    def approve_run_commit(run_id: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        payload = body or {}
        kind = str(payload.get("kind", "build"))
        project_name = payload.get("project_name")
        try:
            return _serialize(
                orchestrator.approve_run_commit(
                    run_id,
                    kind=kind,
                    project_name=str(project_name) if project_name else None,
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/commit/reject")
    def reject_run_commit(run_id: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        payload = body or {}
        kind = str(payload.get("kind", "build"))
        project_name = payload.get("project_name")
        reason = payload.get("reason")
        try:
            return _serialize(
                orchestrator.reject_run_commit(
                    run_id,
                    kind=kind,
                    project_name=str(project_name) if project_name else None,
                    reason=str(reason) if reason else None,
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/logs")
    def logs(limit: int = 100) -> dict[str, Any]:
        return {"logs": _serialize(orchestrator.recent_logs(limit=limit))}

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        subscriber = swarm_manager.subscribe()
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "timestamp": None,
                    "payload": _serialize(swarm_manager.swarm_snapshot()),
                }
            )
            while True:
                try:
                    event = await asyncio.to_thread(subscriber.get, True, 1.0)
                    await websocket.send_json(_serialize(event))
                except queue.Empty:
                    await websocket.send_json({"type": "heartbeat", "timestamp": None, "payload": {}})
        except WebSocketDisconnect:
            pass
        finally:
            swarm_manager.unsubscribe(subscriber)

    return router


def _serialize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _serialize(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
