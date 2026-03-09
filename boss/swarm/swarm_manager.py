from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from boss.agents.documentation_agent import DocumentationAgent
from boss.agents.security_agent import SecurityAgent
from boss.agents.test_agent import TestAgent
from boss.swarm.agent_worker import AgentWorker
from boss.swarm.task_queue import SwarmTaskQueue
from boss.types import AgentWorkerResult, ProjectContext, StructuredPlan, SwarmRun, SwarmTask, utc_now_iso

if TYPE_CHECKING:
    from boss.orchestrator import BOSSOrchestrator


class SwarmManager:
    def __init__(self, orchestrator: "BOSSOrchestrator") -> None:
        self.orchestrator = orchestrator
        self.logger = logging.getLogger(self.__class__.__name__)
        self.state_path = self.orchestrator.root_dir / "data" / "swarm_state.json"
        self.task_queue = SwarmTaskQueue()
        self.executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="boss-swarm")
        self.test_agent = TestAgent(self.orchestrator.router, self.orchestrator.root_dir)
        self.security_agent = SecurityAgent(self.orchestrator.router, self.orchestrator.root_dir)
        self.documentation_agent = DocumentationAgent(self.orchestrator.router, self.orchestrator.root_dir)
        self._runs: dict[str, SwarmRun] = {}
        self._history_runs: dict[str, dict[str, Any]] = {}
        self._history_tasks: dict[str, dict[str, Any]] = {}
        self._run_controls: dict[str, dict[str, bool]] = {}
        self._run_threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=500)
        self._logs: deque[dict[str, Any]] = deque(maxlen=500)
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._load_state()
        self.workers = {
            "engineer": AgentWorker("Engineer Agent", "engineer", self._run_engineer, self.publish_event),
            "test": AgentWorker("Test Agent", "test", self._run_test, self.publish_event),
            "security": AgentWorker("Security Agent", "security", self._run_security, self.publish_event),
            "documentation": AgentWorker("Documentation Agent", "documentation", self._run_documentation, self.publish_event),
        }

    def start_run(
        self,
        goal: str,
        project_name: str | None = None,
        auto_approve: bool = False,
    ) -> dict[str, Any]:
        target_project = project_name or self.orchestrator.get_active_project_name()
        if not target_project:
            projects = self.orchestrator.available_projects()
            if not projects:
                raise RuntimeError("No projects are available for swarm execution.")
            target_project = projects[0]
            self.orchestrator.set_active_project(target_project)

        run_id = uuid.uuid4().hex[:10]
        run = SwarmRun(
            run_id=run_id,
            project_name=target_project,
            goal=goal,
            status="queued",
        )
        with self._lock:
            self._runs[run_id] = run
            self._run_controls[run_id] = {"pause": False, "cancel": False}
        self._save_state()

        thread = threading.Thread(
            target=self._run_swarm,
            args=(run_id, target_project, goal, auto_approve),
            name=f"boss-swarm-{run_id}",
            daemon=True,
        )
        self._run_threads[run_id] = thread
        thread.start()
        self.publish_event("run_started", self.serialize_run(run))
        return self.serialize_run(run)

    def list_runs(self) -> list[dict[str, Any]]:
        combined = dict(self._history_runs)
        with self._lock:
            for run in self._runs.values():
                combined[run.run_id] = self.serialize_run(run)
        runs = list(combined.values())
        runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return runs

    def list_tasks(self, run_id: str | None = None) -> list[dict[str, Any]]:
        combined = dict(self._history_tasks)
        for task in self.task_queue.list_tasks(run_id=run_id):
            combined[str(task.task_id)] = self.serialize_task(task)
        tasks = list(combined.values())
        if run_id is not None:
            tasks = [task for task in tasks if task.get("run_id") == run_id]
        tasks.sort(key=lambda item: (item.get("priority", 0), item.get("created_at", ""), item.get("task_id", 0)))
        return tasks

    def available_agents(self) -> list[dict[str, Any]]:
        return [asdict(worker.snapshot()) for worker in self.workers.values()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
        return self.serialize_run(run) if run else None

    def pause_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._get_run_object(run_id)
        if run is None:
            return None
        self._run_controls[run_id]["pause"] = True
        self.task_queue.pause_run(run_id)
        self._set_run_status(run_id, "pause_requested")
        self.append_log(run_id, "Pause requested.", level="warning")
        return self.serialize_run(run)

    def resume_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._get_run_object(run_id)
        if run is None:
            return None
        self._run_controls[run_id]["pause"] = False
        self.task_queue.resume_run(run_id)
        self._set_run_status(run_id, "running")
        self.append_log(run_id, "Run resumed.", level="info")
        return self.serialize_run(run)

    def cancel_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._get_run_object(run_id)
        if run is None:
            return None
        self._run_controls[run_id]["cancel"] = True
        self.task_queue.cancel_run(run_id)
        self._set_run_status(run_id, "cancel_requested")
        self.append_log(run_id, "Cancel requested.", level="warning")
        return self.serialize_run(run)

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._logs)[-limit:]

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "type": event_type,
            "timestamp": utc_now_iso(),
            "payload": payload,
        }
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Exception:
                continue

    def append_log(self, run_id: str | None, message: str, level: str = "info", agent: str | None = None) -> None:
        entry = {
            "run_id": run_id,
            "level": level,
            "agent": agent,
            "message": message,
            "timestamp": utc_now_iso(),
        }
        with self._lock:
            self._logs.append(entry)
            if run_id and run_id in self._runs:
                self._runs[run_id].logs.append(entry)
                self._runs[run_id].logs = self._runs[run_id].logs[-200:]
                self._runs[run_id].updated_at = entry["timestamp"]
        self._save_state()
        self.publish_event("log", entry)

    def serialize_run(self, run: SwarmRun | None) -> dict[str, Any]:
        if run is None:
            return {}
        payload = asdict(run)
        payload["tasks"] = self.list_tasks(run.run_id)
        return payload

    def serialize_task(self, task: SwarmTask) -> dict[str, Any]:
        return asdict(task)

    def swarm_snapshot(self) -> dict[str, Any]:
        return {
            "runs": self.list_runs(),
            "tasks": self.list_tasks(),
            "agents": self.available_agents(),
            "stats": self.task_queue.stats(),
        }

    def _run_swarm(self, run_id: str, project_name: str, goal: str, auto_approve: bool) -> None:
        try:
            self._set_run_status(run_id, "planning")
            self.append_log(run_id, f"Planning swarm run for {goal}", agent="architect")
            project_context = self.orchestrator.project_loader.load_project(project_name, task_hint=goal, auto_index=True)
            plan_result = self._run_architect(goal, project_context)
            plan = self._parse_structured_plan(goal, plan_result.text)
            self._update_run_plan(run_id, plan_result.text, plan.steps)
            self.append_log(run_id, "Architect plan created.", agent="architect")
            self._wait_if_paused(run_id)
            self._raise_if_cancelled(run_id)

            implementation_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="engineer",
                title="Implement feature changes",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "phase": "implementation",
                    "auto_approve": auto_approve,
                },
                priority=10,
                max_retries=1,
            )
            strategy_test_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="test",
                title="Design test strategy",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "phase": "strategy",
                },
                priority=20,
            )
            strategy_security_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="security",
                title="Review security risks",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "phase": "strategy",
                },
                priority=20,
            )
            strategy_docs_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="documentation",
                title="Draft documentation plan",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "phase": "strategy",
                },
                priority=30,
            )
            self._attach_tasks(run_id, [implementation_task, strategy_test_task, strategy_security_task, strategy_docs_task])
            phase_one = self._execute_phase(run_id, [implementation_task, strategy_test_task, strategy_security_task, strategy_docs_task])

            engineer_output = phase_one.get("engineer", {}).get("output", "")
            if not engineer_output:
                raise RuntimeError("Engineer agent did not return an implementation summary.")

            self._wait_if_paused(run_id)
            self._raise_if_cancelled(run_id)

            validation_test_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="test",
                title="Run tests and assess coverage",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "implementation_text": engineer_output,
                    "phase": "validation",
                },
                priority=20,
            )
            validation_security_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="security",
                title="Audit implementation security",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "implementation_text": engineer_output,
                    "phase": "validation",
                },
                priority=20,
            )
            finalize_docs_task = self.task_queue.enqueue(
                run_id=run_id,
                agent_type="documentation",
                title="Finalize documentation updates",
                payload={
                    "goal": goal,
                    "project_name": project_name,
                    "plan_text": plan_result.text,
                    "implementation_text": engineer_output,
                    "phase": "finalize",
                },
                priority=30,
            )
            self._attach_tasks(run_id, [validation_test_task, validation_security_task, finalize_docs_task])
            phase_two = self._execute_phase(run_id, [validation_test_task, validation_security_task, finalize_docs_task])

            feedback = self._collect_followup_feedback(phase_two)
            final_result = self._merge_outputs(run_id, goal, plan_result.text, phase_one, phase_two)

            if feedback:
                self.append_log(run_id, "Security or test feedback requires engineer follow-up.", level="warning")
                self._wait_if_paused(run_id)
                self._raise_if_cancelled(run_id)
                followup_engineer_task = self.task_queue.enqueue(
                    run_id=run_id,
                    agent_type="engineer",
                    title="Address test and security feedback",
                    payload={
                        "goal": goal,
                        "project_name": project_name,
                        "plan_text": plan_result.text,
                        "phase": "followup",
                        "feedback": feedback,
                        "auto_approve": auto_approve,
                    },
                    priority=15,
                    max_retries=1,
                )
                self._attach_tasks(run_id, [followup_engineer_task])
                followup_results = self._execute_phase(run_id, [followup_engineer_task])
                final_result += "\n\nEngineer Follow-up:\n" + followup_results.get("engineer", {}).get("output", "")
                engineer_output = followup_results.get("engineer", {}).get("output", engineer_output)

            self.orchestrator._store_task_knowledge(
                project_name=project_name,
                task=goal,
                solution_text=engineer_output,
                changed_files=self._swarm_changed_files(run_id),
                errors=[],
                metadata={
                    "mode": "swarm",
                    "plan": plan_result.text,
                    "outputs": self._current_run(run_id).results,
                },
            )
            self._finalize_run(run_id, status="completed", error="", final_output=final_result)
        except Exception as exc:
            self.append_log(run_id, f"Swarm failed: {exc}", level="error")
            self._finalize_run(run_id, status="failed", error=str(exc))

    def _run_architect(self, goal: str, project_context: ProjectContext):
        tools = self.orchestrator._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=False,
            allow_commit=False,
            allow_tests=False,
            allow_editor=True,
        )
        return self.orchestrator.architect.plan(task=goal, project_context=project_context, tools=tools)

    def _run_engineer(self, task: SwarmTask, progress) -> dict[str, Any]:
        payload = task.payload
        project_name = str(payload["project_name"])
        goal = str(payload["goal"])
        project_context = self.orchestrator.project_loader.load_project(project_name, task_hint=goal, auto_index=False)
        progress("Preparing implementation context", 0.15)
        tools = self.orchestrator._toolbox(
            project_context,
            auto_approve=bool(payload.get("auto_approve", True)),
        ).build_tool_definitions(
            allow_write=True,
            allow_terminal=True,
            allow_commit=False,
            allow_tests=True,
            allow_editor=True,
        )
        feedback = str(payload.get("feedback", ""))
        prompt = goal
        if payload.get("phase") == "followup" and feedback:
            prompt = f"{goal}\n\nAddress the following feedback:\n{feedback}"
        progress("Executing engineer agent", 0.45)
        result = self.orchestrator.engineer.implement(
            task=prompt,
            project_context=project_context,
            plan_text=str(payload.get("plan_text", "")),
            tools=tools,
            audit_feedback=feedback,
        )
        changed_files = self.orchestrator._extract_changed_files(result, project_context.root)
        self.orchestrator._record_editor_changes(project_name, result)
        self.orchestrator.project_indexer.index_project(project_name=project_name, force=False)
        self.orchestrator._record_turns(project_name, goal, result.text, category="swarm_engineer")
        progress("Implementation complete", 1.0)
        return {
            "status": "completed",
            "output": result.text,
            "metadata": {
                "changed_files": changed_files,
                "tool_records": [record.name for record in result.tool_records],
            },
        }

    def _run_test(self, task: SwarmTask, progress) -> dict[str, Any]:
        payload = task.payload
        project_name = str(payload["project_name"])
        goal = str(payload["goal"])
        project_context = self.orchestrator.project_loader.load_project(project_name, task_hint=goal, auto_index=False)
        tools = self.orchestrator._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=True,
            allow_commit=False,
            allow_tests=True,
            allow_editor=True,
        )
        progress("Reviewing testing scope", 0.2)
        test_result = {}
        test_summary = ""
        if payload.get("phase") == "validation":
            progress("Running local tests", 0.45)
            test_result = self.orchestrator._toolbox(project_context, auto_approve=True).terminal_tools.run_tests()
            test_summary = self._format_test_result(test_result)
        progress("Generating testing guidance", 0.75)
        result = self.test_agent.review(
            task=f"Review testing needs for: {goal}",
            project_context=project_context,
            plan_text=str(payload.get("plan_text", "")),
            implementation_text=str(payload.get("implementation_text", "")),
            test_results=test_summary,
            tools=tools,
        )
        passed = self.test_agent.passed(result) and (not test_result or bool(test_result.get("passed", False)))
        self.orchestrator._record_turns(project_name, goal, result.text, category="swarm_test")
        progress("Testing review complete", 1.0)
        return {
            "status": "completed",
            "output": result.text,
            "metadata": {
                "passed": passed,
                "test_result": test_result,
            },
        }

    def _run_security(self, task: SwarmTask, progress) -> dict[str, Any]:
        payload = task.payload
        project_name = str(payload["project_name"])
        goal = str(payload["goal"])
        project_context = self.orchestrator.project_loader.load_project(project_name, task_hint=goal, auto_index=False)
        tools = self.orchestrator._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=False,
            allow_commit=False,
            allow_tests=False,
            allow_editor=True,
        )
        progress("Reviewing security surface", 0.35)
        result = self.security_agent.review(
            task=f"Review the security posture for: {goal}",
            project_context=project_context,
            plan_text=str(payload.get("plan_text", "")),
            implementation_text=str(payload.get("implementation_text", "")),
            tools=tools,
        )
        passed = self.security_agent.passed(result)
        self.orchestrator._record_turns(project_name, goal, result.text, category="swarm_security")
        progress("Security review complete", 1.0)
        return {
            "status": "completed",
            "output": result.text,
            "metadata": {
                "passed": passed,
            },
        }

    def _run_documentation(self, task: SwarmTask, progress) -> dict[str, Any]:
        payload = task.payload
        project_name = str(payload["project_name"])
        goal = str(payload["goal"])
        project_context = self.orchestrator.project_loader.load_project(project_name, task_hint=goal, auto_index=False)
        tools = self.orchestrator._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=False,
            allow_commit=False,
            allow_tests=False,
            allow_editor=True,
        )
        progress("Drafting documentation", 0.5)
        result = self.documentation_agent.document(
            task=f"Document the requested work: {goal}",
            project_context=project_context,
            plan_text=str(payload.get("plan_text", "")),
            implementation_text=str(payload.get("implementation_text", "")),
            tools=tools,
        )
        self.orchestrator._record_turns(project_name, goal, result.text, category="swarm_docs")
        progress("Documentation draft complete", 1.0)
        return {
            "status": "completed",
            "output": result.text,
            "metadata": {
                "ready": self.documentation_agent.ready(result),
            },
        }

    def _execute_phase(self, run_id: str, tasks: list[SwarmTask]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        remaining = {task.task_id for task in tasks}

        while remaining:
            self._wait_if_paused(run_id)
            self._raise_if_cancelled(run_id)
            futures: dict[Future[AgentWorkerResult], SwarmTask] = {}
            for task in tasks:
                if task.task_id not in remaining:
                    continue
                current = self.task_queue.get(task.task_id)
                if current is None or current.status != "pending":
                    continue
                dequeued = self.task_queue.dequeue(run_id=run_id, agent_type=task.agent_type)
                if dequeued is None:
                    continue
                self.append_log(run_id, f"{task.agent_type} started: {task.title}", agent=task.agent_type)
                futures[self.executor.submit(self.workers[task.agent_type].run, dequeued)] = dequeued

            if not futures:
                time.sleep(0.1)
                continue

            for future in as_completed(futures):
                original_task = futures[future]
                worker_result = future.result()
                if worker_result.status == "completed":
                    self.task_queue.complete(original_task.task_id, result={"output": worker_result.output, **worker_result.metadata})
                    results[original_task.agent_type] = {
                        "task_id": original_task.task_id,
                        "output": worker_result.output,
                        "metadata": worker_result.metadata,
                    }
                    self._record_run_result(run_id, original_task.agent_type, results[original_task.agent_type])
                    self.append_log(run_id, f"{original_task.agent_type} completed: {original_task.title}", agent=original_task.agent_type)
                    remaining.discard(original_task.task_id)
                else:
                    retry_task = self.task_queue.retry(original_task.task_id, error=worker_result.error)
                    self.append_log(
                        run_id,
                        f"{original_task.agent_type} failed: {worker_result.error}",
                        level="error",
                        agent=original_task.agent_type,
                    )
                    if retry_task is None or retry_task.status == "failed":
                        self.task_queue.fail(original_task.task_id, worker_result.error)
                        results[original_task.agent_type] = {
                            "task_id": original_task.task_id,
                            "output": "",
                            "metadata": {},
                            "error": worker_result.error,
                        }
                        self._record_run_result(run_id, original_task.agent_type, results[original_task.agent_type])
                        remaining.discard(original_task.task_id)
        return results

    def _collect_followup_feedback(self, outputs: dict[str, dict[str, Any]]) -> str:
        sections: list[str] = []
        test_payload = outputs.get("test")
        if test_payload is not None and not bool(test_payload.get("metadata", {}).get("passed", False)):
            sections.append(f"Test Agent Feedback:\n{test_payload.get('output', '')}")
            test_result = test_payload.get("metadata", {}).get("test_result")
            if isinstance(test_result, dict):
                sections.append(f"Local Test Result:\n{self._format_test_result(test_result)}")
        security_payload = outputs.get("security")
        if security_payload is not None and not bool(security_payload.get("metadata", {}).get("passed", False)):
            sections.append(f"Security Agent Feedback:\n{security_payload.get('output', '')}")
        return "\n\n".join(section for section in sections if section.strip())

    def _merge_outputs(
        self,
        run_id: str,
        goal: str,
        plan_text: str,
        phase_one: dict[str, dict[str, Any]],
        phase_two: dict[str, dict[str, Any]],
    ) -> str:
        sections = [
            f"Goal: {goal}",
            f"Architect Plan:\n{plan_text.strip()}",
        ]
        for label, payload in (
            ("Engineer", phase_one.get("engineer")),
            ("Test Strategy", phase_one.get("test")),
            ("Security Strategy", phase_one.get("security")),
            ("Documentation Strategy", phase_one.get("documentation")),
            ("Test Validation", phase_two.get("test")),
            ("Security Validation", phase_two.get("security")),
            ("Documentation Final", phase_two.get("documentation")),
        ):
            if payload is None:
                continue
            body = payload.get("output", "") or payload.get("error", "")
            if body:
                sections.append(f"{label}:\n{body.strip()}")
        summary = "\n\n".join(section for section in sections if section.strip())
        self.append_log(run_id, "Swarm run merged all agent outputs.", agent="swarm")
        return summary

    def _parse_structured_plan(self, task: str, text: str) -> StructuredPlan:
        payload = text.strip()
        match = re.search(r"```json\s*(\{.*?\})\s*```", payload, re.DOTALL)
        if match:
            payload = match.group(1)
        try:
            data = json.loads(payload)
            goal = str(data.get("goal", task)).strip() or task
            steps = [str(step).strip() for step in data.get("steps", []) if str(step).strip()]
            if steps:
                return StructuredPlan(goal=goal, steps=steps[:8], raw_text=text)
        except Exception:
            pass

        steps: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^(\d+\.|-)\s+", stripped):
                steps.append(re.sub(r"^(\d+\.|-)\s+", "", stripped).strip())
        if not steps:
            steps = [
                "Implement the core feature",
                "Validate with tests and security review",
                "Update documentation and usage notes",
            ]
        return StructuredPlan(goal=task, steps=steps[:8], raw_text=text)

    def _format_test_result(self, payload: dict[str, Any]) -> str:
        if not payload:
            return "No test results available."
        if not payload.get("found", False):
            return str(payload.get("message", "No tests detected."))
        lines: list[str] = []
        for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            lines.append(
                f"{item.get('command', '')} exit={item.get('exit_code', '')}\n"
                f"STDOUT:\n{str(item.get('stdout', ''))[:300]}\n"
                f"STDERR:\n{str(item.get('stderr', ''))[:300]}"
            )
        return "\n\n".join(lines) or "Tests ran without detailed output."

    def _swarm_changed_files(self, run_id: str) -> list[str]:
        changed: list[str] = []
        for task in self.task_queue.list_tasks(run_id=run_id):
            files = task.result.get("changed_files", [])
            if isinstance(files, list):
                changed.extend(str(item) for item in files)
        return sorted(set(changed))

    def _attach_tasks(self, run_id: str, tasks: list[SwarmTask]) -> None:
        with self._lock:
            run = self._runs[run_id]
            for task in tasks:
                if task.task_id not in run.task_ids:
                    run.task_ids.append(task.task_id)
            run.updated_at = utc_now_iso()
        self._save_state()
        self.publish_event(
            "tasks_enqueued",
            {
                "run_id": run_id,
                "tasks": [self.serialize_task(task) for task in tasks],
            },
        )

    def _record_run_result(self, run_id: str, agent_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.results[agent_type] = payload
            run.updated_at = utc_now_iso()
        self._save_state()
        self.publish_event("task_result", {"run_id": run_id, "agent_type": agent_type, "result": payload})

    def _update_run_plan(self, run_id: str, plan_text: str, plan_steps: list[str]) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.plan_text = plan_text
            run.plan_steps = plan_steps
            run.updated_at = utc_now_iso()
        self._save_state()
        self.publish_event(
            "plan_updated",
            {
                "run_id": run_id,
                "plan_text": plan_text,
                "plan_steps": plan_steps,
            },
        )

    def _set_run_status(self, run_id: str, status: str) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.status = status
            run.updated_at = utc_now_iso()
        self._save_state()
        self.publish_event("run_status", {"run_id": run_id, "status": status})

    def _finalize_run(self, run_id: str, status: str, error: str = "", final_output: str = "") -> None:
        with self._lock:
            run = self._runs[run_id]
            run.status = status
            run.error = error
            if final_output:
                run.results["final"] = {"output": final_output}
            run.updated_at = utc_now_iso()
        self._save_state()
        self.publish_event("run_completed", self.serialize_run(run))

    def _wait_if_paused(self, run_id: str) -> None:
        controls = self._run_controls[run_id]
        while controls.get("pause") and not controls.get("cancel"):
            self._set_run_status(run_id, "paused")
            time.sleep(0.2)
        if not controls.get("cancel"):
            self._set_run_status(run_id, "running")

    def _raise_if_cancelled(self, run_id: str) -> None:
        if self._run_controls[run_id].get("cancel"):
            raise RuntimeError("Swarm run cancelled.")

    def _current_run(self, run_id: str) -> SwarmRun:
        with self._lock:
            return self._runs[run_id]

    def _get_run_object(self, run_id: str) -> SwarmRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        changed = False
        runs: dict[str, dict[str, Any]] = {}
        for item in payload.get("runs", []):
            if not isinstance(item, dict) or not item.get("run_id"):
                continue
            if item.get("status") not in {"completed", "failed", "cancelled", "stopped"}:
                item["status"] = "stopped"
                changed = True
            runs[str(item.get("run_id"))] = item
        self._history_runs = {
            run_id: item
            for run_id, item in runs.items()
        }
        tasks: dict[str, dict[str, Any]] = {}
        for item in payload.get("tasks", []):
            if not isinstance(item, dict) or item.get("task_id") is None:
                continue
            if item.get("status") not in {"completed", "failed", "cancelled", "stopped"}:
                item["status"] = "stopped"
                changed = True
            tasks[str(item.get("task_id"))] = item
        self._history_tasks = tasks
        for entry in payload.get("logs", []):
            if isinstance(entry, dict):
                self._logs.append(entry)
        if changed:
            self._save_state()

    def _save_state(self) -> None:
        with self._lock:
            runs = dict(self._history_runs)
            for run in self._runs.values():
                payload = asdict(run)
                payload["tasks"] = self.list_tasks(run.run_id)
                runs[run.run_id] = payload
                self._history_runs[run.run_id] = payload
            tasks = dict(self._history_tasks)
            for task in self.task_queue.list_tasks():
                serialized = self.serialize_task(task)
                tasks[str(task.task_id)] = serialized
                self._history_tasks[str(task.task_id)] = serialized
            payload = {
                "runs": list(runs.values())[-50:],
                "tasks": list(tasks.values())[-500:],
                "logs": list(self._logs)[-500:],
            }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
