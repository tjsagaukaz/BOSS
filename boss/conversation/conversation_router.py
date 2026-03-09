from __future__ import annotations

import json
import queue
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # pragma: no cover - dependency availability is environment specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from boss.runtime import ContextEnvelopeBuilder
from boss.types import (
    AgentResult,
    AuditResult,
    MemoryEntry,
    ProjectBrain,
    ProjectContext,
    ProjectMap,
    ProjectMemoryProfile,
    StyleProfile,
    WorkflowResult,
)


class ConversationInterrupted(RuntimeError):
    """Raised when a live conversation stream is cancelled."""


class ConversationRouter:
    MUTATING_INTENTS = {"code", "build", "ship"}
    ACTION_KEYWORDS = ("build", "implement", "add", "create", "fix", "refactor", "optimize")
    PLANNING_KEYWORDS = ("design", "architect", "plan out", "draft architecture", "create plan", "map out")
    RESEARCH_KEYWORDS = ("research", "deep research", "look up", "investigate", "compare", "find out")
    EXECUTION_KEYWORDS = ("run tests", "test the", "audit", "benchmark", "build", "code", "implement")
    FOLDER_CREATE_PATTERN = re.compile(
        r"\b(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\b(?:\s+(?:called|named))?(?:\s+(?P<name>[^.?!,\n]+))?",
        re.IGNORECASE,
    )
    FRESH_START_PATTERNS = (
        re.compile(r"\bstart (?:something )?fresh\b", re.IGNORECASE),
        re.compile(r"\bnew project\b", re.IGNORECASE),
        re.compile(r"\bfresh project\b", re.IGNORECASE),
        re.compile(r"\bstart a fresh project\b", re.IGNORECASE),
        re.compile(r"\bstart a new project\b", re.IGNORECASE),
        re.compile(r"\bget out of this workspace\b", re.IGNORECASE),
    )
    DIRECT_REQUEST_PREFIXES = (
        "can you ",
        "could you ",
        "help me ",
        "please ",
        "create ",
        "make ",
        "build ",
        "ship ",
        "run ",
        "open ",
        "search ",
        "inspect ",
        "audit ",
        "test ",
        "benchmark ",
        "design ",
        "plan ",
        "fix ",
        "implement ",
    )
    FILE_REFERENCE_PATTERN = re.compile(r"(?:(?:[\w.-]+/)+[\w.-]+|\b[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|go|swift|cpp|c|md|yaml|yml|json|toml|sql)\b)")
    SLASH_COMMAND_PATTERN = re.compile(
        r"^/(plan|build|autobuild|ship|code|test|audit|benchmark|research|search|project|stop|loop)\b(?:\s+(.*))?$",
        re.IGNORECASE,
    )
    PROJECT_VISIBILITY_PATTERNS = (
        re.compile(r"\b(what|which|show)\s+(projects|repos|repositories)\b", re.IGNORECASE),
        re.compile(r"\bwhat can you see on my mac\b", re.IGNORECASE),
        re.compile(r"\bwhat projects can you see\b", re.IGNORECASE),
    )
    ACCESS_PATTERNS = (
        re.compile(r"\b(access|permissions|operator mode|full access)\b", re.IGNORECASE),
    )
    DEFAULT_PERSONA = {
        "assistant_name": "BOSS",
        "user_name": "",
        "relationship": "engineering partner",
        "tone": ["direct", "practical", "calm", "technically sharp"],
        "defaults": {
            "address_user_as": "only when helpful",
            "reply_style": "concise",
            "learn_from_history": True,
        },
        "working_style": [
            "Default to the smallest useful next action.",
            "Keep planning proportional to task risk.",
            "Treat clear requests as operational, not ceremonial.",
        ],
        "communication_preferences": [
            "Use plain language.",
            "Avoid hype, roleplay, and branded catchphrases.",
            "Keep replies concise unless more depth is useful.",
        ],
        "user_preferences": [],
        "rules": [
            "Answer directly and keep the reply grounded in the available project context.",
            "Prefer practical next steps over abstract brainstorming.",
            "Use plain language instead of internal system jargon.",
            "Keep default replies concise unless the user asks for depth.",
            "If something is blocked, explain it plainly and give the fastest next step.",
            "Adapt to the user's working style from recent history and configured preferences without making a show of it.",
        ],
        "banned_phrases": [
            "I am here in workspace mode",
            "I cannot browse your Mac unless",
        ],
    }
    SMALL_TALK_PATTERNS = (
        re.compile(r"^(hi|hello|hey|yo)(\s+boss)?[!.?]*$", re.IGNORECASE),
        re.compile(r"^(good morning|good afternoon|good evening)(\s+boss)?[!.?]*$", re.IGNORECASE),
        re.compile(r"^(what'?s up|sup)(\s+boss)?[!.?]*$", re.IGNORECASE),
        re.compile(r"^(thanks|thank you)(\s+boss)?[!.?]*$", re.IGNORECASE),
    )
    USER_PREFERENCE_PATTERNS = (
        ("communication", re.compile(r"\b(?:be|keep|stay)\b(?:\s+\w+){0,4}\s+\b(?:brief|short|concise)\b", re.IGNORECASE), "Keep replies concise by default."),
        ("communication", re.compile(r"\bplain language\b|\bno jargon\b|\bnormal\b", re.IGNORECASE), "Use plain language and keep the tone natural."),
        ("workflow", re.compile(r"\bplan (?:it )?first\b|\bstart with a plan\b", re.IGNORECASE), "Plan first when the task is risky or broad."),
        ("workflow", re.compile(r"\bjust (?:do it|ship it|build it)\b|\bdefault to execution\b", re.IGNORECASE), "Default to execution when intent is clear."),
        ("product", re.compile(r"\bhide (?:the )?internal\b|\bkeep .*internal .*hidden\b|\binternal details .*not shown\b", re.IGNORECASE), "Keep internal system detail hidden unless explicitly requested."),
        ("product", re.compile(r"\bdon't hardcode\b|\bdo not hardcode\b", re.IGNORECASE), "Avoid hardcoded user-facing behavior and adapt from context instead."),
    )
    USER_NAME_PREFERENCE_PATTERN = re.compile(r"\b(?:call me|my name is)\s+([a-z0-9][a-z0-9 .'-]{0,40})", re.IGNORECASE)
    AVOID_NAME_PREFERENCE_PATTERN = re.compile(r"\b(?:don't|do not)\s+call me\s+([a-z0-9][a-z0-9 .'-]{0,40})", re.IGNORECASE)

    def __init__(
        self,
        orchestrator,
        history_store,
        router,
        root_dir: str | Path,
    ) -> None:
        self.orchestrator = orchestrator
        self.history_store = history_store
        self.router = router
        self.root_dir = Path(root_dir).resolve()
        self.prompt_path = self.root_dir / "boss" / "prompts" / "conversation_prompt.txt"
        self.persona_path = self.root_dir / "config" / "persona.yaml"
        self.persona = self._load_persona()
        self.envelope_builder = ContextEnvelopeBuilder()
        self._active_streams: dict[str, threading.Event] = {}
        self._stream_lock = threading.RLock()

    def handle_message(
        self,
        message: str,
        *,
        project_name: str | None = None,
        execute: bool = False,
        auto_approve: bool = False,
        intent_override: str | None = None,
    ) -> dict[str, Any]:
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("Message is required.")

        active_project = project_name or self.orchestrator.get_active_project_name()
        self._capture_user_preferences(cleaned)
        is_small_talk = self._is_small_talk(cleaned)

        detected = self._detect_intent(cleaned, intent_override=intent_override, project_name_hint=active_project)
        if active_project and not is_small_talk:
            self.orchestrator.note_project_brain_signal(cleaned, project_name=active_project)
        history = self.history_store.recent(project_name=active_project, limit=8)
        implicit_execute = bool(detected.get("explicit_command"))
        should_execute = execute or implicit_execute or self._should_auto_execute(detected, cleaned, auto_approve=auto_approve)
        direct_request = self._looks_direct_request(cleaned.lower())

        if detected["intent"] in self.MUTATING_INTENTS and should_execute and not auto_approve and not implicit_execute:
            response = {
                "intent": detected["intent"],
                "conversation_type": "execution",
                "mode": "blocked",
                "project_name": active_project,
                "reply": self._blocked_execution_reply(detected),
                "actions": [],
                "result": None,
            }
            self._record_history(active_project, cleaned, response)
            return response

        if detected["intent"] in self.MUTATING_INTENTS and not should_execute:
            response = {
                "intent": detected["intent"],
                "conversation_type": "execution",
                "mode": "suggested",
                "project_name": active_project,
                "reply": self._suggest_execution_reply(detected, active_project),
                "actions": [],
                "result": None,
            }
            self._record_history(active_project, cleaned, response)
            return response

        if detected["intent"] == "task_suggestion":
            if should_execute:
                response = self._execute_task_suggestion(
                    detected,
                    project_name=active_project,
                    auto_approve=auto_approve,
                )
                self._record_history(active_project, cleaned, response)
                return response
            if direct_request:
                response = {
                    "intent": "build",
                    "conversation_type": "execution",
                    "mode": "blocked",
                    "project_name": active_project,
                    "reply": self._blocked_execution_reply({"intent": "build", "task": str(detected.get("task", ""))}),
                    "actions": [],
                    "result": None,
                }
                self._record_history(active_project, cleaned, response)
                return response
            response = {
                "intent": "conversation",
                "conversation_type": "discussion",
                "mode": "chat",
                "project_name": active_project,
                "reply": self._conversation_reply(
                    message=cleaned,
                    project_name=active_project,
                    history=history,
                    suggested_task=str(detected.get("task", "")),
                    conversation_type="discussion",
                ),
                "actions": [],
                "result": None,
            }
            self._record_history(active_project, cleaned, response)
            return response

        if detected["intent"] == "conversation":
            response = {
                "intent": "conversation",
                "conversation_type": "discussion",
                "mode": "chat",
                "project_name": active_project,
                "reply": self._conversation_reply(
                    message=cleaned,
                    project_name=active_project,
                    history=history,
                    conversation_type="discussion",
                ),
                "actions": [],
                "result": None,
            }
            self._record_history(active_project, cleaned, response)
            return response

        response = self._execute_intent(detected, project_name=active_project, auto_approve=auto_approve)
        response["conversation_type"] = self._conversation_type_for_intent(response["intent"])
        self._record_history(active_project, cleaned, response)
        return response

    def stream_message(
        self,
        message: str,
        *,
        project_name: str | None = None,
        execute: bool = False,
        auto_approve: bool = False,
        intent_override: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("Message is required.")

        active_project = project_name or self.orchestrator.get_active_project_name()
        self._capture_user_preferences(cleaned)
        is_small_talk = self._is_small_talk(cleaned)
        detected = self._detect_intent(cleaned, intent_override=intent_override, project_name_hint=active_project)
        if active_project and not is_small_talk:
            self.orchestrator.note_project_brain_signal(cleaned, project_name=active_project)
        implicit_execute = bool(detected.get("explicit_command"))
        should_execute = execute or implicit_execute or self._should_auto_execute(detected, cleaned, auto_approve=auto_approve)

        if detected["intent"] == "task_suggestion" and should_execute:
            response = self.handle_message(
                cleaned,
                project_name=active_project,
                execute=should_execute,
                auto_approve=auto_approve,
                intent_override=intent_override,
            )
            yield self._stream_meta_event(response)
            reply = str(response.get("reply", ""))
            if reply:
                yield {"type": "delta", "delta": reply}
            yield {"type": "done", "response": response}
            return

        if detected["intent"] not in {"conversation", "task_suggestion"}:
            response = self.handle_message(
                cleaned,
                project_name=active_project,
                execute=should_execute,
                auto_approve=auto_approve,
                intent_override=intent_override,
            )
            yield self._stream_meta_event(response)
            reply = str(response.get("reply", ""))
            if reply:
                yield {"type": "delta", "delta": reply}
            yield {"type": "done", "response": response}
            return

        history = self.history_store.recent(project_name=active_project, limit=8)
        actions: list[dict[str, Any]] = []
        mode = "chat"
        intent = "conversation"
        conversation_type = "discussion"
        stream_id = f"stream_{uuid.uuid4().hex[:10]}"
        cancel_event = threading.Event()
        self._register_stream(active_project, stream_id, cancel_event)
        yield {
            "type": "meta",
            "stream_id": stream_id,
            "intent": intent,
            "conversation_type": conversation_type,
            "mode": mode,
            "project_name": active_project,
            "actions": actions,
        }

        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def worker() -> None:
            chunks: list[str] = []
            try:
                suggested_task = str(detected.get("task", "")) if detected["intent"] == "task_suggestion" else None
                if cancel_event.is_set():
                    raise ConversationInterrupted("Conversation stream cancelled.")

                def on_delta(delta: str) -> None:
                    if cancel_event.is_set():
                        raise ConversationInterrupted("Conversation stream cancelled.")
                    chunks.append(delta)
                    events.put({"type": "delta", "delta": delta})

                reply = self._conversation_reply(
                    message=cleaned,
                    project_name=active_project,
                    history=history,
                    suggested_task=suggested_task,
                    conversation_type=conversation_type,
                    stream=True,
                    on_text_delta=on_delta,
                )
                if cancel_event.is_set():
                    raise ConversationInterrupted("Conversation stream cancelled.")
                response = {
                    "intent": intent,
                    "conversation_type": conversation_type,
                    "mode": mode,
                    "project_name": active_project,
                    "reply": reply,
                    "actions": actions,
                    "result": None,
                }
                self._record_history(active_project, cleaned, response)
                events.put({"type": "done", "response": response})
            except ConversationInterrupted:
                response = {
                    "intent": intent,
                    "conversation_type": conversation_type,
                    "mode": "interrupted",
                    "project_name": active_project,
                    "reply": "".join(chunks).strip() or "Stopped. Ready for the next move.",
                    "actions": actions,
                    "result": None,
                }
                self._record_history(active_project, cleaned, response)
                events.put({"type": "interrupted", "stream_id": stream_id})
                events.put({"type": "done", "response": response})
            except Exception as exc:
                events.put({"type": "error", "error": str(exc)})
                events.put(
                    {
                        "type": "done",
                        "response": {
                            "intent": intent,
                            "conversation_type": conversation_type,
                            "mode": "blocked",
                            "project_name": active_project,
                            "reply": str(exc),
                            "actions": [],
                            "result": None,
                        },
                    }
                )
            finally:
                self._clear_stream(active_project, stream_id)

        thread = threading.Thread(target=worker, name="boss-chat-stream", daemon=True)
        thread.start()

        while True:
            event = events.get()
            yield event
            if event.get("type") == "done":
                break

    def cancel_stream(self, project_name: str | None = None, stream_id: str | None = None) -> bool:
        scope = project_name or self.orchestrator.get_active_project_name() or "__workspace__"
        with self._stream_lock:
            if stream_id:
                token = self._active_streams.get(f"{scope}:{stream_id}")
                if token is not None:
                    token.set()
                    return True
            for key, token in self._active_streams.items():
                if key.startswith(f"{scope}:"):
                    token.set()
                    return True
        return False

    def history(self, project_name: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
        return self.history_store.recent(project_name=project_name, limit=limit)

    def _execute_intent(
        self,
        detected: dict[str, Any],
        *,
        project_name: str | None,
        auto_approve: bool,
    ) -> dict[str, Any]:
        intent = str(detected["intent"])
        result: Any = None
        if intent == "plan":
            result = self.orchestrator.plan(str(detected["task"]))
            reply = result.text
        elif intent == "code":
            result = self.orchestrator.code(
                str(detected["task"]),
                auto_approve=auto_approve,
                project_name=project_name,
                max_iterations=2,
            )
            reply = self._format_workflow_result(result)
        elif intent == "build":
            result = self.orchestrator.build(
                str(detected["task"]),
                auto_approve=auto_approve,
                project_name=project_name,
                commit_changes=False,
            )
            reply = self._format_build_result(result)
        elif intent == "ship":
            result = self.orchestrator.ship(
                str(detected["task"]),
                auto_approve=auto_approve,
                project_name=project_name,
                commit_changes=True,
                push_changes=True,
            )
            reply = self._format_build_result(result)
        elif intent == "audit":
            result = self.orchestrator.audit(str(detected.get("task") or "Audit the active project for defects and risks."))
            reply = result.text
        elif intent == "status":
            result = self.orchestrator.status()
            reply = self._format_status(result)
        elif intent == "index":
            result = self.orchestrator.index_project(project_name=project_name)
            reply = (
                f"Indexed {result.project_name}: total={result.total_files}, "
                f"indexed={result.indexed_files}, changed={result.changed_files}, skipped={result.skipped_files}."
            )
        elif intent == "map":
            result = self.orchestrator.project_map(project_name=project_name)
            reply = self._format_project_map(result)
        elif intent == "search":
            result = self.orchestrator.search(str(detected["query"]))
            reply = self._format_search(result)
        elif intent == "research":
            result = self.orchestrator.research(str(detected["query"]), project_name=project_name, use_web=True, use_local=True)
            reply = self._format_research(result)
        elif intent == "benchmark":
            result = self.orchestrator.run_golden_tasks()
            reply = self._format_benchmark_result(result)
        elif intent == "loop_status":
            result = self.orchestrator.task_status()
            reply = self._format_task_status(result)
        elif intent == "stop":
            result = self.orchestrator.stop_task()
            reply = self._format_stop_result(result)
        elif intent == "memory":
            result = self.orchestrator.memory_snapshot(project_name=project_name)
            reply = self._format_memory(result)
        elif intent == "solutions":
            query = str(detected.get("query", "")).strip() or None
            result = self.orchestrator.solutions(query=query, limit=8)
            reply = self._format_solutions(result)
        elif intent == "tests":
            result = self.orchestrator.run_tests(project_name=project_name)
            reply = self._format_test_result(result)
        elif intent == "project":
            result = self.orchestrator.set_active_project(str(detected["project_name"]))
            reply = f"Switched active project to {result.name}."
        elif intent == "create_folder":
            result = self.orchestrator.create_workspace_folder(
                str(detected.get("path") or ""),
                switch_to=bool(detected.get("switch_to", True)),
                project_name=project_name,
            )
            reply = f"Ok. {str(result.get('message') or 'Folder is ready.')}"
        elif intent == "open":
            result = self.orchestrator.open_file(str(detected["path"]))
            reply = str(result.get("message") or f"Opened {detected['path']}.")
        elif intent == "jump":
            result = self.orchestrator.jump_to_symbol(str(detected["symbol"]))
            reply = str(result.get("message") or f"Jumped to {detected['symbol']}.")
        elif intent == "projects":
            result = self.orchestrator.portfolio_snapshot(include_internal=False)
            reply = self._format_portfolio(result)
        elif intent == "permissions":
            result = self.orchestrator.permissions_snapshot()
            reply = self._format_permissions(result)
        elif intent == "dashboard":
            reply = "Open the BOSS dashboard at the current web app root."
        else:
            reply = self._conversation_reply(
                message=str(detected.get("task") or detected.get("query") or ""),
                project_name=project_name,
                history=[],
                conversation_type="discussion",
            )
            intent = "conversation"
        return {
            "intent": intent,
            "mode": "executed",
            "project_name": project_name,
            "reply": reply,
            "actions": [],
            "result": self._serialize_result(result),
        }

    def _execute_task_suggestion(
        self,
        detected: dict[str, Any],
        *,
        project_name: str | None,
        auto_approve: bool,
    ) -> dict[str, Any]:
        task = str(detected.get("task", "")).strip()
        result = self.orchestrator.build(
            task,
            auto_approve=auto_approve,
            project_name=project_name,
            commit_changes=False,
        )
        reply = f"Ok.\n\n{self._format_build_result(result)}"
        return {
            "intent": "build",
            "conversation_type": "execution",
            "mode": "executed",
            "project_name": project_name,
            "reply": reply,
            "actions": [],
            "result": self._serialize_result(result),
        }

    def _conversation_reply(
        self,
        *,
        message: str,
        project_name: str | None,
        history: list[dict[str, Any]],
        suggested_task: str | None = None,
        conversation_type: str = "discussion",
        stream: bool = False,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> str:
        context = self._project_context(project_name, task_hint=message)
        history_lines: list[str] = []
        for item in history[-4:]:
            history_lines.append(f"User: {item['message']}")
            history_lines.append(f"BOSS: {item['response']}")
        if suggested_task:
            history_lines.append(f"Suggested task: {suggested_task}")
        history_lines.append(self._session_continuity_brief(project_name, history))
        preference_brief = self._preference_brief(project_name)
        if preference_brief:
            history_lines.append(preference_brief)
        history_lines.append(self._workspace_brief())
        history_lines.append(self._recent_runs_brief(project_name))
        referenced_files = self._referenced_files(message, history)
        if referenced_files:
            history_lines.append("Referenced Files:")
            history_lines.extend(f"- {item}" for item in referenced_files[:8])
        supplemental = "\n".join(history_lines).strip()
        prompt = self.envelope_builder.build(
            role="conversation",
            task=message,
            project_context=context,
            tools=[],
            supplemental_context=supplemental,
            task_contract={
                "goal": (
                    "Answer clearly and practically. "
                    "Reason about engineering strategy, repo state, architecture, and next actions without exposing chain-of-thought."
                ),
                "project_name": project_name or "workspace",
                "suggested_task": suggested_task or "",
                "conversation_type": conversation_type,
            },
            execution_rules=[
                "Do not claim that code changed unless an explicit execution step already happened.",
                "Never expose chain-of-thought or hidden reasoning. Only provide the final reasoning and recommendation.",
                "If the user sounds action-oriented but has not explicitly executed, suggest concrete next actions.",
                "Keep replies concise unless the user asks for depth.",
                "Use plain language over internal system jargon.",
                "Do not force a persona or a response template.",
                "Ground claims in project brain, workspace state, recent runs, and architecture context.",
            ],
        ).render()
        system_prompt = self._load_system_prompt(project_name=project_name)
        client = self.router.client_for_request(
            "conversation",
            prompt=prompt,
            tools=[],
            request_options={"mode": "conversation"},
        )
        try:
            result = client.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                tools=[],
                stream=stream,
                on_text_delta=on_text_delta,
            )
            self.router.record_model_run(
                role="conversation",
                provider=result.provider,
                model=result.model,
                duration_seconds=result.duration_seconds,
                success=True,
                metadata={"mode": "conversation"},
            )
            return result.text.strip() or self._fallback_reply(message, project_name, suggested_task)
        except Exception as exc:
            self.router.record_model_run(
                role="conversation",
                provider=str(getattr(client, "provider", "unknown")),
                model=str(getattr(client, "model", "unknown")),
                duration_seconds=0.0,
                success=False,
                metadata={"mode": "conversation", "error": str(exc)},
            )
            return self._fallback_reply(message, project_name, suggested_task)

    def _fallback_reply(self, message: str, project_name: str | None, suggested_task: str | None) -> str:
        if self._is_small_talk(message):
            return self._small_talk_reply(message, project_name)
        if suggested_task:
            return "I can do that. I can plan it first or start implementing."
        if project_name:
            return f"I’m here. Tell me what you want to do in {project_name}."
        return "I’m here. Tell me what you want to work on."

    def _is_small_talk(self, message: str) -> bool:
        text = message.strip()
        if not text:
            return False
        if len(text.split()) > 4:
            return False
        return any(pattern.match(text) for pattern in self.SMALL_TALK_PATTERNS)

    def _small_talk_reply(self, message: str, project_name: str | None) -> str:
        lowered = message.strip().lower()
        if "thank" in lowered:
            return "You're welcome."
        return "Hey."

    def _project_context(self, project_name: str | None, task_hint: str) -> ProjectContext:
        if project_name:
            try:
                return self.orchestrator.project_loader.load_project(project_name, task_hint=task_hint, auto_index=True)
            except Exception:
                pass
        return self.orchestrator.workspace_context()

    def _record_history(self, project_name: str | None, message: str, response: dict[str, Any]) -> None:
        recent_history = self.history_store.recent(project_name=project_name, limit=6)
        self.history_store.append_turn(
            project_name=project_name,
            message=message,
            response=str(response.get("reply", "")),
            intent=str(response.get("intent", "conversation")),
            metadata={
                "mode": response.get("mode"),
                "conversation_type": response.get("conversation_type", self._conversation_type_for_intent(str(response.get("intent", "conversation")))),
                "actions": response.get("actions", []),
                "result": response.get("result"),
                "referenced_files": self._referenced_files(message, recent_history),
            },
        )

    def _stream_meta_event(self, response: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "meta",
            "intent": response.get("intent"),
            "conversation_type": response.get("conversation_type", self._conversation_type_for_intent(str(response.get("intent", "conversation")))),
            "mode": response.get("mode"),
            "project_name": response.get("project_name"),
            "actions": response.get("actions", []),
        }

    def _load_system_prompt(self, project_name: str | None = None) -> str:
        persona_block = self._persona_system_prompt(project_name=project_name)
        if self.prompt_path.exists():
            prompt = self.prompt_path.read_text(encoding="utf-8").strip()
            return f"{persona_block}\n\n{prompt}".strip()
        return (
            f"{persona_block}\n\n"
            "You are BOSS, a local engineering assistant. "
            "Respond naturally, stay grounded in the provided context, "
            "and suggest the next concrete action when it is useful."
        )

    def _suggest_execution_reply(self, detected: dict[str, Any], project_name: str | None) -> str:
        action = str(detected["intent"])
        task = str(detected.get("task", ""))
        if action == "ship":
            return f"Ok. I can ship `{task}`."
        if action == "build":
            return f"Ok. I can build `{task}`."
        if action == "code":
            return "Ok. I can make that change."
        return f"Ok. I can run `{task}`."

    def _blocked_execution_reply(self, detected: dict[str, Any]) -> str:
        action = str(detected.get("intent", "task"))
        task = str(detected.get("task", "")).strip()
        if task:
            return (
                f"Ok. I can {action} `{task}`.\n\n"
                "Turn on Auto-approve and send it again, or tell me to plan it first."
            )
        return "Ok. I can do that. Turn on Auto-approve and send it again, or tell me to plan it first."

    def _build_actions(self, detected: dict[str, Any]) -> list[dict[str, Any]]:
        intent = str(detected["intent"])
        if intent in {"build", "code", "ship", "task_suggestion"}:
            return []
        if intent == "conversation":
            return []
        if intent == "create_folder":
            folder_path = str(detected.get("path") or "")
            return [
                {
                    "label": "Open Folder",
                    "intent": "open",
                    "message": folder_path,
                    "execute": True,
                    "auto_approve": True,
                }
            ] if folder_path else []
        return []

    def _detect_intent(
        self,
        message: str,
        *,
        intent_override: str | None = None,
        project_name_hint: str | None = None,
    ) -> dict[str, Any]:
        text = re.sub(r"\s+", " ", message.strip())
        lowered = text.lower()
        if intent_override:
            return self._coerce_override(intent_override, text, project_name_hint=project_name_hint)
        slash_match = self.SLASH_COMMAND_PATTERN.match(text)
        if slash_match:
            command, remainder = slash_match.groups()
            result = self._command_intent(command.lower(), (remainder or "").strip())
            result["explicit_command"] = True
            return result
        command_match = re.match(
            r"^(plan|build|autobuild|ship|code|audit|index|map|memory|solutions|search|research|open|jump|project|tests?|status|stop|loop)\b[:\s-]*(.*)$",
            lowered,
        )
        if command_match:
            command, remainder = command_match.groups()
            return self._command_intent(command, text[len(command_match.group(1)):].strip(" :-"))
        if lowered in {"dashboard", "open dashboard", "show dashboard"}:
            return {"intent": "dashboard"}
        if any(pattern.search(text) for pattern in self.PROJECT_VISIBILITY_PATTERNS):
            return {"intent": "projects"}
        if any(pattern.search(text) for pattern in self.ACCESS_PATTERNS):
            return {"intent": "permissions"}
        if lowered.startswith("what should we build next") or lowered.startswith("boss what should we build next"):
            return {"intent": "conversation"}
        if any(pattern.search(text) for pattern in self.FRESH_START_PATTERNS):
            return {"intent": "create_folder", "path": "new-project", "switch_to": True}
        folder_request = self._folder_request(text, project_name_hint=project_name_hint)
        if folder_request is not None:
            return folder_request
        if lowered.startswith("run tests") or lowered == "test":
            return {"intent": "tests"}
        if lowered in {"loop status", "status of the loop", "what's the loop doing", "what is the loop doing"}:
            return {"intent": "loop_status"}
        if lowered == "stop":
            return {"intent": "stop"}
        if lowered.startswith("benchmark") or lowered.startswith("run benchmark") or lowered == "/benchmark":
            return {"intent": "benchmark"}
        if any(lowered.startswith(keyword) for keyword in self.PLANNING_KEYWORDS):
            return {"intent": "plan", "task": self._normalize_task(text)}
        if any(keyword in lowered for keyword in self.RESEARCH_KEYWORDS):
            return {"intent": "research", "query": self._normalize_task(text)}
        if any(keyword in lowered for keyword in self.ACTION_KEYWORDS) and not lowered.endswith("?"):
            task = self._normalize_task(text)
            return {"intent": "task_suggestion", "task": task}
        return {"intent": "conversation"}

    def _coerce_override(self, intent_override: str, message: str, *, project_name_hint: str | None = None) -> dict[str, Any]:
        override = intent_override.strip().lower()
        if override in {"plan", "build", "code", "audit", "ship"}:
            return {"intent": override, "task": self._normalize_task(message)}
        if override in {"tests", "status", "index", "map", "memory", "stop", "loop_status"}:
            return {"intent": override}
        if override == "solutions":
            return {"intent": "solutions", "query": message.strip()}
        if override == "search":
            return {"intent": "search", "query": message.strip()}
        if override == "research":
            return {"intent": "research", "query": message.strip()}
        if override == "benchmark":
            return {"intent": "benchmark"}
        if override == "create_folder":
            return self._folder_request(message, project_name_hint=project_name_hint) or {"intent": "conversation"}
        if override in {"projects", "portfolio"}:
            return {"intent": "projects"}
        if override == "permissions":
            return {"intent": "permissions"}
        return {"intent": "conversation"}

    def _command_intent(self, command: str, remainder: str) -> dict[str, Any]:
        cleaned = remainder.strip()
        if command in {"plan", "build", "autobuild", "ship", "code", "audit"}:
            normalized_command = "build" if command == "autobuild" else command
            if normalized_command == "audit":
                return {"intent": normalized_command, "task": self._normalize_task(cleaned)}
            if normalized_command in {"plan", "build", "code", "ship"}:
                return {"intent": "tests" if normalized_command == "test" else normalized_command, "task": self._normalize_task(cleaned)}
        if command == "stop":
            return {"intent": "stop"}
        if command == "loop":
            if not cleaned or cleaned.lower() == "status":
                return {"intent": "loop_status"}
            return {"intent": "conversation"}
        if command in {"test", "tests"}:
            return {"intent": "tests"}
        if command == "benchmark":
            return {"intent": "benchmark"}
        if command == "search":
            return {"intent": "search", "query": cleaned}
        if command == "research":
            return {"intent": "research", "query": cleaned}
        if command == "open":
            return {"intent": "open", "path": cleaned}
        if command == "jump":
            return {"intent": "jump", "symbol": cleaned}
        if command == "project":
            return {"intent": "project", "project_name": cleaned}
        if command in {"projects", "portfolio"}:
            return {"intent": "projects"}
        if command == "permissions":
            return {"intent": "permissions"}
        if command in {"mkdir", "folder"}:
            return self._folder_request(cleaned, project_name_hint=None) or {"intent": "conversation"}
        if command == "solutions":
            return {"intent": "solutions", "query": cleaned}
        return {"intent": command}

    def _normalize_task(self, message: str) -> str:
        cleaned = re.sub(r"^(please|can you|could you|help me|boss)\s+", "", message.strip(), flags=re.IGNORECASE)
        if cleaned and cleaned[-1] == "?":
            cleaned = cleaned[:-1].strip()
        return cleaned or message.strip()

    def _conversation_type_for_intent(self, intent: str) -> str:
        lowered = str(intent or "conversation").lower()
        if lowered in {"plan"}:
            return "planning"
        if lowered in {"build", "ship", "code", "audit", "tests", "benchmark", "stop", "loop_status", "create_folder"}:
            return "execution"
        return "discussion"

    def _folder_request(self, message: str, project_name_hint: str | None) -> dict[str, Any] | None:
        match = self.FOLDER_CREATE_PATTERN.search(message.strip())
        if not match:
            lowered = message.strip().lower()
            if any(phrase in lowered for phrase in ("new folder", "fresh project", "new project")) and any(
                phrase in lowered for phrase in ("navigate", "move", "switch", "go", "work from")
            ):
                return {"intent": "create_folder", "path": "new-project", "switch_to": True}
            return None
        explicit_name = str(match.group("name") or "").strip().strip("`\"' ")
        explicit_name = re.split(r"\b(?:and|so|then)\b", explicit_name, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        explicit_name = re.sub(r"\bon my mac\b", "", explicit_name, flags=re.IGNORECASE).strip()
        explicit_name = re.sub(r"\bfor me\b", "", explicit_name, flags=re.IGNORECASE).strip()
        explicit_name = re.sub(r"\s{2,}", " ", explicit_name).strip(" -")
        path = explicit_name
        if not path:
            path = "new-project"
        return {"intent": "create_folder", "path": path, "switch_to": True}

    def _normalize_folder_name(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
        return slug or "new-project"

    def _should_auto_execute(self, detected: dict[str, Any], message: str, *, auto_approve: bool) -> bool:
        intent = str(detected.get("intent", "")).lower()
        lowered = message.strip().lower()
        if intent == "create_folder":
            return True
        if lowered.endswith("?") and not lowered.startswith("can you ") and not lowered.startswith("could you "):
            return False
        if intent in self.MUTATING_INTENTS:
            return self._looks_direct_request(lowered)
        if intent in {"open", "jump", "search", "research", "project", "tests", "audit", "benchmark", "stop", "loop_status"}:
            return self._looks_direct_request(lowered)
        if intent == "plan":
            return self._looks_direct_request(lowered)
        if intent == "task_suggestion" and auto_approve:
            return self._looks_direct_request(lowered)
        return False

    def _looks_direct_request(self, lowered: str) -> bool:
        normalized = lowered.strip()
        return any(normalized.startswith(prefix) for prefix in self.DIRECT_REQUEST_PREFIXES)

    def _register_stream(self, project_name: str | None, stream_id: str, cancel_event: threading.Event) -> None:
        scope = project_name or "__workspace__"
        with self._stream_lock:
            self._active_streams[f"{scope}:{stream_id}"] = cancel_event

    def _clear_stream(self, project_name: str | None, stream_id: str) -> None:
        scope = project_name or "__workspace__"
        with self._stream_lock:
            self._active_streams.pop(f"{scope}:{stream_id}", None)

    def _referenced_files(self, message: str, history: list[dict[str, Any]]) -> list[str]:
        candidates = self.FILE_REFERENCE_PATTERN.findall(message or "")
        for item in history[-4:]:
            candidates.extend(self.FILE_REFERENCE_PATTERN.findall(str(item.get("message", ""))))
            candidates.extend(self.FILE_REFERENCE_PATTERN.findall(str(item.get("response", ""))))
        seen: set[str] = set()
        files: list[str] = []
        for candidate in candidates:
            normalized = str(candidate).strip().strip("`")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            files.append(normalized)
        return files[:12]

    def _recent_runs_brief(self, project_name: str | None) -> str:
        if not hasattr(self.orchestrator, "recent_runs"):
            return "Recent Runs: unavailable"
        try:
            runs = self.orchestrator.recent_runs(project_name=project_name, limit=5)
        except Exception:
            return "Recent Runs: unavailable"
        if not runs:
            return "Recent Runs: none recorded"
        lines = ["Recent Runs:"]
        for item in runs[:5]:
            title = str(item.get("title") or item.get("identifier") or item.get("kind") or "run")
            status = str(item.get("status") or "unknown")
            kind = str(item.get("kind") or "run")
            lines.append(f"- {title} [{kind}] status={status}")
        return "\n".join(lines)

    def _format_status(self, status: dict[str, Any]) -> str:
        return (
            f"Active project: {status.get('active_project') or 'None'}\n"
            f"Models: {status.get('models') or 'Not configured'}\n"
            f"Indexed: {status.get('indexed_at') or 'Not indexed'}\n"
            f"Current task: {status.get('current_task') or 'None'} [{status.get('current_task_status') or 'idle'}]\n"
            f"Active file: {status.get('active_file') or 'None'}"
        )

    def _workspace_brief(self) -> str:
        projects = self.orchestrator.available_project_catalog(include_internal=False)[:10]
        roots = self.orchestrator.workspace_roots_snapshot(include_internal=False)
        permissions = self.orchestrator.permissions_snapshot()
        project_names = ", ".join(item.get("display_name") or item.get("key") for item in projects) or "No indexed projects yet"
        search_roots = ", ".join(roots.get("search_roots", [])[:5]) or "Unknown"
        return "\n".join(
            [
                f"Visible Projects: {project_names}",
                f"Search Roots: {search_roots}",
                (
                    "Access Profile: "
                    f"workspace_write={permissions.get('workspace_write_mode', 'unknown')}, "
                    f"project_write={permissions.get('project_write_mode', 'unknown')}, "
                    f"destructive={permissions.get('destructive_mode', 'unknown')}"
                ),
            ]
        )

    def _format_portfolio(self, snapshot: dict[str, Any]) -> str:
        projects = snapshot.get("projects", []) or []
        if not projects:
            return "No registered projects yet. Add a workspace root and I’ll map it."
        lines = ["Visible projects:", ""]
        for project in projects[:12]:
            display = project.get("display_name") or project.get("project_key") or "Unknown"
            focus = project.get("focus") or "No focus set yet"
            next_priority = project.get("next_priority") or "No next priority recorded"
            lines.append(f"- {display}: focus = {focus}; next = {next_priority}")
        if len(projects) > 12:
            lines.append(f"- ...plus {len(projects) - 12} more")
        return "\n".join(lines)

    def _format_permissions(self, snapshot: dict[str, Any]) -> str:
        writable = ", ".join(snapshot.get("writable_roots", [])[:5]) or "None"
        return (
            "Current access profile.\n\n"
            f"- Workspace write mode: {snapshot.get('workspace_write_mode', 'unknown')}\n"
            f"- Project write mode: {snapshot.get('project_write_mode', 'unknown')}\n"
            f"- Destructive actions: {snapshot.get('destructive_mode', 'unknown')}\n"
            f"- Writable roots: {writable}\n"
            "- Remaining limits come from OS-level app permissions."
        )

    def _load_persona(self) -> dict[str, Any]:
        if yaml is None or not self.persona_path.exists():
            return dict(self.DEFAULT_PERSONA)
        raw = yaml.safe_load(self.persona_path.read_text(encoding="utf-8")) or {}
        merged = dict(self.DEFAULT_PERSONA)
        merged.update({key: value for key, value in raw.items() if key in merged})
        defaults = dict(self.DEFAULT_PERSONA.get("defaults", {}))
        defaults.update(raw.get("defaults", {}) or {})
        merged["defaults"] = defaults
        return merged

    def _persona_system_prompt(self, project_name: str | None = None) -> str:
        assistant_name = str(self.persona.get("assistant_name", "BOSS")).strip() or "BOSS"
        user_name = str(self.persona.get("user_name", "")).strip()
        relationship = str(self.persona.get("relationship", "engineering partner")).strip()
        tone = ", ".join(str(item) for item in self.persona.get("tone", []) if str(item).strip())
        working_style = "\n".join(f"- {item}" for item in self.persona.get("working_style", []) if str(item).strip())
        communication = "\n".join(
            f"- {item}" for item in self.persona.get("communication_preferences", []) if str(item).strip()
        )
        configured_preferences = [str(item).strip() for item in self.persona.get("user_preferences", []) if str(item).strip()]
        learned_preferences = [
            str(item.get("preference", "")).strip()
            for item in self.history_store.recent_preferences(project_name=project_name, limit=8)
            if str(item.get("preference", "")).strip()
        ]
        merged_preferences = self._merge_unique_preferences(configured_preferences, learned_preferences)
        user_preferences = "\n".join(f"- {item}" for item in merged_preferences)
        rules = "\n".join(f"- {item}" for item in self.persona.get("rules", []) if str(item).strip())
        banned = "\n".join(f"- {item}" for item in self.persona.get("banned_phrases", []) if str(item).strip())
        user_reference = f"{user_name}'s" if user_name else "the user's"
        addressing = (
            f"Address the user as {user_name} only when it feels natural."
            if user_name
            else "Do not force a name or personal label into replies."
        )
        return (
            f"You are {assistant_name}, {user_reference} {relationship}.\n"
            f"Voice: {tone or 'direct, practical, calm'}.\n"
            f"Be clear, grounded, and concise. {addressing}\n"
            "Do not sound like a helpdesk bot, compliance banner, or hype-heavy assistant.\n"
            "Prefer plain language over internal system jargon.\n"
            "If access exists, use it without narrating generic limitations.\n"
            "Adapt to the user's working style from recent history and the configured preferences below.\n"
            f"Working style preferences:\n{working_style or '- Default to the smallest useful next action.'}\n"
            f"Communication preferences:\n{communication or '- Use plain language.'}\n"
            f"Known user preferences:\n{user_preferences or '- None recorded yet. Infer cautiously from recent history.'}\n"
            f"Behavior rules:\n{rules or '- Be direct and practical.'}\n"
            f"Avoid these phrases:\n{banned or '- I am here in workspace mode'}"
        )

    def _capture_user_preferences(self, message: str) -> None:
        for category, preference in self._extract_user_preferences(message):
            self.history_store.upsert_preference(
                preference=preference,
                category=category,
                project_name=None,
                source_message=message,
            )

    def _extract_user_preferences(self, message: str) -> list[tuple[str, str]]:
        text = str(message or "").strip()
        if not text:
            return []
        matches: list[tuple[str, str]] = []
        for category, pattern, preference in self.USER_PREFERENCE_PATTERNS:
            if pattern.search(text):
                matches.append((category, preference))
        avoid_name_match = self.AVOID_NAME_PREFERENCE_PATTERN.search(text)
        if avoid_name_match:
            avoided = avoid_name_match.group(1).strip(" .,!?:;")
            if avoided:
                matches.append(("communication", f"Do not address the user as {avoided}."))
        name_match = self.USER_NAME_PREFERENCE_PATTERN.search(text)
        if name_match and not avoid_name_match:
            preferred_name = name_match.group(1).strip(" .,!?:;")
            if preferred_name:
                matches.append(("communication", f"Address the user as {preferred_name} when it feels natural."))
        return self._dedupe_preferences(matches)

    def _preference_brief(self, project_name: str | None) -> str:
        preferences = self.history_store.recent_preferences(project_name=project_name, limit=6)
        if not preferences:
            return ""
        lines = ["Saved User Preferences:"]
        for item in preferences:
            category = str(item.get("category", "general")).strip() or "general"
            preference = str(item.get("preference", "")).strip()
            if preference:
                lines.append(f"- [{category}] {preference}")
        return "\n".join(lines)

    def _session_continuity_brief(self, project_name: str | None, history: list[dict[str, Any]]) -> str:
        if not history:
            return "Session Continuity: no prior conversation recorded."
        last_turn = history[-1]
        recent_intents = []
        for item in history[-4:]:
            intent = str(item.get("intent", "")).strip()
            if intent and intent not in recent_intents:
                recent_intents.append(intent)
        last_request = self._one_line(str(last_turn.get("message", "")), limit=140)
        last_response = self._one_line(str(last_turn.get("response", "")), limit=180)
        scope = project_name or "workspace"
        lines = [f"Session Continuity ({scope}):"]
        if last_request:
            lines.append(f"- Last user request: {last_request}")
        if last_response:
            lines.append(f"- Last BOSS reply: {last_response}")
        if recent_intents:
            lines.append(f"- Recent intents: {', '.join(recent_intents[:4])}")
        return "\n".join(lines)

    def _merge_unique_preferences(self, configured: list[str], learned: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in [*configured, *learned]:
            normalized = str(item).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
        return merged

    def _dedupe_preferences(self, items: list[tuple[str, str]]) -> list[tuple[str, str]]:
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for category, preference in items:
            key = (category.strip().lower(), preference.strip().lower())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            deduped.append((category, preference))
        return deduped

    def _format_project_map(self, project_map: ProjectMap) -> str:
        languages = ", ".join(project_map.languages.keys()) or "Unknown"
        modules = ", ".join(project_map.main_modules[:8]) or "None"
        key_files = ", ".join(project_map.key_files[:8]) or "None"
        return (
            f"Project map for {project_map.name}.\n\n"
            f"Overview: {project_map.overview}\n"
            f"Languages: {languages}\n"
            f"Modules: {modules}\n"
            f"Key files: {key_files}"
        )

    def _format_search(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return "I didn't find a strong semantic match yet."
        lines = ["Top semantic matches:", ""]
        for item in results[:5]:
            metadata = item.get("metadata") if isinstance(item, dict) else {}
            file_path = metadata.get("file_path", "unknown") if isinstance(metadata, dict) else "unknown"
            preview = str(item.get("text", "")).replace("\n", " ")[:120]
            lines.append(f"- {file_path} (score={float(item.get('score', 0.0)):.3f}): {preview}")
        return "\n".join(lines)

    def _format_research(self, result) -> str:
        lines = [result.summary.strip()]
        if result.sources:
            lines.append("")
            lines.append("Sources:")
            for source in result.sources[:6]:
                location = source.url or source.file_path
                lines.append(f"- {source.citation} {source.title} {location}".strip())
        return "\n".join(lines).strip()

    def _format_benchmark_result(self, result: dict[str, Any]) -> str:
        attempted = int(result.get("suite_runs_attempted", 0) or 0)
        passed = int(result.get("suite_runs_passed", 0) or 0)
        skipped = int(result.get("suite_runs_skipped", 0) or 0)
        rate = f"{round((passed / attempted) * 100)}%" if attempted else "n/a"
        lines = [
            "Latest benchmark summary.",
            "",
            f"Suite runs: {passed}/{attempted} passed",
            f"Success rate: {rate}",
        ]
        if skipped:
            lines.append(f"Skipped: {skipped}")
        failure_map = result.get("failure_map") or {}
        primary_failure = str(failure_map.get("primary") or "").strip()
        if primary_failure:
            lines.extend(["", f"Primary failure mode: {primary_failure}"])
        return "\n".join(lines)

    def _format_memory(self, snapshot: dict[str, Any]) -> str:
        profile = snapshot.get("project_profile")
        style = snapshot.get("style_profile")
        graph = snapshot.get("graph_insights") or []
        return (
            f"Project: {self._field(profile, 'project_name', 'unknown')}\n"
            f"Description: {self._field(profile, 'description', 'No description')}\n"
            f"Architecture: {self._field(profile, 'architecture', 'Unknown')}\n"
            f"Frameworks: {', '.join(self._field(profile, 'frameworks', [])) or 'None'}\n"
            f"Style: {self._field(style, 'indentation', 'Unknown')} | {self._field(style, 'test_style', 'Unknown')}\n"
            f"Graph insights: {graph[0] if graph else 'None'}"
        )

    def _format_solutions(self, solutions: list[Any]) -> str:
        if not solutions:
            return "No reusable solutions recorded."
        lines = []
        for entry in solutions[:5]:
            title = getattr(entry, "title", "")
            description = getattr(entry, "description", "")
            tags = ", ".join(getattr(entry, "tags", [])[:4])
            lines.append(f"- {title}: {description[:120]} ({tags or 'no tags'})")
        return "\n".join(lines)

    def _format_test_result(self, result: dict[str, Any]) -> str:
        if not result.get("found", False):
            return str(result.get("message", "No tests detected."))
        status = "passed" if result.get("passed", False) else "failed"
        commands = ", ".join(result.get("commands", [])[:3]) or "No commands recorded"
        return f"Tests {status}. Commands: {commands}"

    def _format_workflow_result(self, result: WorkflowResult) -> str:
        return (
            f"Code run finished after {result.iterations} iteration(s).\n"
            f"Changed files: {', '.join(result.changed_files) or 'none'}\n\n"
            f"Audit: {'passed' if result.audit.passed else 'needs follow-up'}\n"
            f"{result.audit.text}"
        )

    def _format_build_result(self, result) -> str:
        summary = self._build_run_summary(result)
        goal = str(summary.get("goal", "")).strip() or "the requested build"
        status = str(summary.get("status", "")).strip().lower()
        completed_steps = int(summary.get("completed_steps", 0) or 0)
        total_steps = int(summary.get("total_steps", 0) or 0)
        failed_steps = int(summary.get("failed_steps", 0) or 0)
        changed_files = list(summary.get("changed_files", []) or [])
        shipping_status = str(summary.get("shipping_status", "")).strip()

        if status == "completed":
            lead = f"Finished {goal}."
        elif status in {"failed", "stopped", "aborted"}:
            lead = f"Stopped {goal} with follow-up needed."
        else:
            lead = f"Build status for {goal}: {status or 'running'}."

        details: list[str] = []
        if total_steps:
            details.append(f"{completed_steps}/{total_steps} steps completed")
        if failed_steps:
            details.append(f"{failed_steps} step(s) need follow-up")
        if changed_files:
            if len(changed_files) <= 3:
                details.append(f"Changed {', '.join(changed_files)}")
            else:
                details.append(f"Changed {len(changed_files)} files")
        if shipping_status:
            details.append(f"Shipping: {shipping_status.replace('_', ' ')}")

        final_result = self._one_line(getattr(result, "final_result", ""))
        if final_result and final_result.lower() not in lead.lower():
            details.append(final_result)

        if not details:
            return lead
        return f"{lead} {' '.join(details)}"

    def _format_task_status(self, task: dict[str, Any] | None) -> str:
        if not task:
            return "There isn't an active build run right now."
        steps = task.get("steps", []) or []
        completed = sum(1 for step in steps if str(step.get("status", "")).lower() == "completed")
        running = next((step for step in steps if str(step.get("status", "")).lower() == "running"), None)
        next_step = running or next((step for step in steps if str(step.get("status", "")).lower() not in {"completed"}), None)
        lines = [
            "Current build run.",
            "",
            f"Goal: {task.get('task') or 'Unknown task'}",
            f"Run #{task.get('id')}",
            f"Status: {task.get('status') or 'unknown'}",
            f"Iteration: {completed}/{task.get('total_steps', len(steps)) or len(steps)} step(s) completed",
        ]
        if next_step:
            lines.append(f"Current step: {next_step.get('title') or 'Working'} [{next_step.get('status') or 'running'}]")
        files_changed = task.get("files_changed", []) or []
        if files_changed:
            lines.append(f"Files changed: {', '.join(files_changed[:6])}")
        errors = task.get("errors", []) or []
        if errors:
            lines.extend(["", f"Latest issue: {errors[-1]}"])
        return "\n".join(lines)

    def _format_stop_result(self, task: dict[str, Any] | None) -> str:
        if not task:
            return "There wasn't an active build run to stop."
        return (
            "Stop requested.\n\n"
            f"Run #{task.get('id')} is now {task.get('status', 'stopping')}."
        )

    def _serialize_result(self, result: Any) -> Any:
        if result is None:
            return None
        if isinstance(result, ProjectMap):
            return {
                "name": result.name,
                "overview": result.overview,
                "languages": result.languages,
                "main_modules": result.main_modules,
                "entry_points": result.entry_points,
                "key_files": result.key_files,
                "dependencies": result.dependencies,
            }
        if isinstance(result, (AgentResult, AuditResult)):
            return {
                "provider": result.provider,
                "model": result.model,
                "text": result.text,
                "duration_seconds": result.duration_seconds,
            }
        if isinstance(result, WorkflowResult):
            return {
                "iterations": result.iterations,
                "changed_files": result.changed_files,
                "audit_passed": result.audit.passed,
            }
        if hasattr(result, "status") and hasattr(result, "final_result") and hasattr(result, "task_id"):
            summary = self._build_run_summary(result)
            return {
                "status": result.status,
                "task_id": result.task_id,
                "final_result": result.final_result,
                "changed_files": getattr(result, "changed_files", []),
                "run_kind": "build",
                "artifact_path": str((getattr(result, "metadata", {}) or {}).get("artifact_path", "") or ""),
                "shipping": dict((getattr(result, "metadata", {}) or {}).get("shipping", {}) or {}),
                "internal_summary": summary,
            }
        if isinstance(result, list):
            serialized = []
            for item in result[:8]:
                if hasattr(item, "title"):
                    serialized.append(
                        {
                            "title": getattr(item, "title", ""),
                            "description": getattr(item, "description", ""),
                            "tags": getattr(item, "tags", []),
                        }
                    )
                else:
                    serialized.append(item)
            return serialized
        if isinstance(result, dict):
            return result
        return json.loads(json.dumps(result, default=str))

    def _build_run_summary(self, result: Any) -> dict[str, Any]:
        step_results = list(getattr(result, "step_results", []) or [])
        plan = getattr(result, "plan", None)
        plan_steps = list(getattr(plan, "steps", []) or [])
        total_steps = len(step_results) or len(plan_steps)
        completed_steps = sum(1 for step in step_results if str(getattr(step, "status", "")).lower() == "completed")
        failed_steps = sum(
            1
            for step in step_results
            if str(getattr(step, "status", "")).lower() in {"failed", "stopped", "aborted"}
        )
        retries = sum(max(int(getattr(step, "iterations", 0) or 0) - 1, 0) for step in step_results)
        changed_files = list(getattr(result, "changed_files", []) or [])
        metadata = dict(getattr(result, "metadata", {}) or {})
        shipping = dict(metadata.get("shipping", {}) or {})
        return {
            "goal": str(getattr(result, "goal", "") or "Build task"),
            "status": str(getattr(result, "status", "") or "unknown"),
            "planned_steps": len(plan_steps),
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "failed_steps": failed_steps,
            "retries": retries,
            "changed_files": changed_files,
            "changed_files_count": len(changed_files),
            "shipping_status": str(shipping.get("status", "") or ""),
            "details_available": bool(getattr(result, "task_id", None)),
            "roles": ["architect", "engineer", "test", "auditor"],
        }

    def _one_line(self, value: Any, limit: int = 220) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1].rstrip()}…"

    def _field(self, value: Any, key: str, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)
