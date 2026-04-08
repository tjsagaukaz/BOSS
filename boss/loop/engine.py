"""Loop engine: bounded autonomous edit-run-test-fix lifecycle.

The engine wraps the normal agent streaming flow.  Each iteration runs
the agent with accumulated context (prior attempt results, test output,
diff summary).  Budget checks happen *between* iterations so a running
agent turn is never interrupted.

Permission gates pause the loop — the engine yields the same
``permission_request`` SSE events that the single-pass flow uses, letting
the frontend / job system handle approval and resume.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from boss.loop.policy import ExecutionStyle, LoopBudget
from boss.loop.state import (
    AttemptCommand,
    LoopAttempt,
    LoopPhase,
    LoopState,
    StopReason,
    save_loop_state,
)

logger = logging.getLogger(__name__)

_TAIL_LIMIT = 2000  # chars of stdout/stderr to keep per command


def _clip(text: str, limit: int = _TAIL_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _build_iteration_prompt(
    *,
    task: str,
    attempt_number: int,
    micro_plan: list[str],
    prior_attempts: list[LoopAttempt],
    phase: str,
) -> str:
    """Build the agent prompt for this iteration, including loop context."""
    parts: list[str] = []

    parts.append(f"## Task\n{task}\n")

    if micro_plan:
        plan_text = "\n".join(f"{i+1}. {step}" for i, step in enumerate(micro_plan))
        parts.append(f"## Micro-Plan\n{plan_text}\n")

    parts.append(f"## Loop Context\nAttempt {attempt_number}. Phase: {phase}.\n")

    if prior_attempts:
        parts.append("## Prior Attempts")
        for attempt in prior_attempts[-3:]:  # keep last 3 for context window
            status = "PASSED" if attempt.test_passed else "FAILED"
            parts.append(f"\n### Attempt {attempt.attempt_number} [{status}]")
            if attempt.error:
                parts.append(f"Error: {attempt.error}")
            if attempt.test_output_tail:
                parts.append(f"Test output:\n```\n{attempt.test_output_tail}\n```")
            if attempt.diff_summary:
                parts.append(f"Diff:\n```\n{attempt.diff_summary}\n```")
            if attempt.assistant_output:
                tail = _clip(attempt.assistant_output, 1500)
                parts.append(f"Assistant output (tail):\n{tail}")

    if attempt_number == 1:
        parts.append(
            "\n## Instructions\n"
            "1. Understand the task and gather context.\n"
            "2. Propose a brief micro-plan of edits and tests.\n"
            "3. Make the edits.\n"
            "4. Run the test/build command to verify.\n"
            "5. If tests pass, reply with LOOP_RESULT:SUCCESS.\n"
            "6. If tests fail, reply with LOOP_RESULT:RETRY and explain what to fix.\n"
            "7. If the task cannot be completed, reply with LOOP_RESULT:STOP and explain why.\n"
        )
    else:
        parts.append(
            "\n## Instructions (Retry)\n"
            "The previous attempt did not pass. Review the test output above, "
            "fix the issue, re-run the test, and report LOOP_RESULT:SUCCESS, "
            "LOOP_RESULT:RETRY, or LOOP_RESULT:STOP.\n"
        )

    return "\n".join(parts)


def _parse_loop_result(text: str) -> str | None:
    """Extract LOOP_RESULT directive from assistant output."""
    for line in reversed(text.splitlines()):
        stripped = line.strip().upper()
        if "LOOP_RESULT:SUCCESS" in stripped:
            return "success"
        if "LOOP_RESULT:RETRY" in stripped:
            return "retry"
        if "LOOP_RESULT:STOP" in stripped:
            return "stop"
    return None


class LoopEngine:
    """Drives the bounded iterative loop.

    Usage::

        engine = LoopEngine(...)
        async for sse_chunk in engine.run():
            yield sse_chunk  # forward to client
    """

    def __init__(
        self,
        *,
        task: str,
        session_id: str,
        budget: LoopBudget,
        mode: str = "agent",
        workspace_root: str | None = None,
        loop_id: str | None = None,
        job_id: str | None = None,
        resume_state: LoopState | None = None,
    ):
        self._task = task
        self._session_id = session_id
        self._budget = budget
        self._mode = mode
        self._workspace_root = workspace_root
        self._job_id = job_id

        if resume_state:
            self._state = resume_state
            self._state.pending_run_id = None  # clear stale pending
        else:
            lid = loop_id or uuid.uuid4().hex
            self._state = LoopState(
                loop_id=lid,
                session_id=session_id,
                task_description=task,
                budget=budget.to_dict(),
                execution_style=ExecutionStyle.ITERATIVE.value,
                started_at=time.time(),
                job_id=job_id,
                workspace_root=workspace_root,
            )

    @property
    def state(self) -> LoopState:
        return self._state

    async def run(self) -> AsyncIterator[str]:
        """Run the iterative loop, yielding SSE events."""
        # Emit initial loop status
        yield _sse_event({
            "type": "loop_status",
            "loop_id": self._state.loop_id,
            "status": "started",
            "budget": self._budget.to_dict(),
            "task": self._task,
        })
        save_loop_state(self._state)

        while True:
            # --- Budget checks between iterations ---
            stop = self._check_budget()
            if stop:
                self._state.stop_reason = stop.value
                self._state.finished_at = time.time()
                save_loop_state(self._state)
                yield _sse_event({
                    "type": "loop_status",
                    "loop_id": self._state.loop_id,
                    "status": "stopped",
                    "stop_reason": stop.value,
                    "attempt": self._state.current_attempt,
                })
                return

            # Start new attempt
            self._state.current_attempt += 1
            attempt = LoopAttempt(
                attempt_number=self._state.current_attempt,
                started_at=time.time(),
            )
            self._state.attempts.append(attempt)
            self._state.phase = LoopPhase.PLAN.value if self._state.current_attempt == 1 else LoopPhase.EDIT.value

            yield _sse_event({
                "type": "loop_attempt",
                "loop_id": self._state.loop_id,
                "attempt_number": self._state.current_attempt,
                "phase": self._state.phase,
                "budget_remaining": self._budget_remaining(),
            })

            save_loop_state(self._state)

            # Build iteration prompt
            prompt = _build_iteration_prompt(
                task=self._task,
                attempt_number=self._state.current_attempt,
                micro_plan=self._state.micro_plan,
                prior_attempts=self._state.attempts[:-1],
                phase=self._state.phase,
            )

            # Run agent iteration — stream through and collect results
            assistant_text = ""
            permission_blocked = False
            commands_this_attempt: list[AttemptCommand] = []
            test_output = ""

            try:
                async for chunk in self._run_agent_iteration(prompt):
                    payload = _try_parse_sse(chunk)

                    if payload is not None:
                        event_type = payload.get("type", "")

                        # Track tool calls as commands
                        if event_type == "tool_call":
                            cmd_name = payload.get("name", "")
                            if cmd_name:
                                cmd_rec = AttemptCommand(
                                    command=f"{cmd_name}({payload.get('arguments', '')[:200]})",
                                    exit_code=None,
                                    stdout_tail="",
                                    stderr_tail="",
                                    verdict=payload.get("execution_type", "unknown"),
                                    timestamp=time.time(),
                                )
                                commands_this_attempt.append(cmd_rec)
                                self._state.total_commands += 1

                        elif event_type == "tool_result":
                            output = payload.get("output", "")
                            if commands_this_attempt:
                                commands_this_attempt[-1].stdout_tail = _clip(output)
                                # Heuristic: detect test output
                                if any(kw in output.lower() for kw in ("passed", "failed", "error", "ok", "fail")):
                                    test_output = _clip(output, 3000)

                        elif event_type == "text":
                            assistant_text += payload.get("content", "")

                        elif event_type == "permission_request":
                            permission_blocked = True
                            self._state.pending_run_id = payload.get("run_id")
                            self._state.phase = LoopPhase.EDIT.value
                            save_loop_state(self._state)
                            yield chunk
                            # Stop the loop — will resume when permission resolves
                            attempt.finished_at = time.time()
                            attempt.commands = commands_this_attempt
                            attempt.assistant_output = _clip(assistant_text, 4000)
                            attempt.stop_reason = StopReason.APPROVAL_BLOCKED.value
                            self._state.stop_reason = StopReason.APPROVAL_BLOCKED.value
                            save_loop_state(self._state)
                            yield _sse_event({
                                "type": "loop_status",
                                "loop_id": self._state.loop_id,
                                "status": "paused",
                                "stop_reason": StopReason.APPROVAL_BLOCKED.value,
                                "attempt": self._state.current_attempt,
                            })
                            return

                        elif event_type == "done":
                            # Suppress per-iteration done events — the loop
                            # wrapper emits the final done when all iterations
                            # are complete.  Forwarding these would cause the
                            # client to stop listening after the first pass.
                            continue

                    # Forward everything except suppressed events to client
                    yield chunk

            except Exception as exc:
                logger.exception("Loop iteration %d failed", self._state.current_attempt)
                attempt.error = str(exc)[:1000]
                attempt.finished_at = time.time()
                attempt.commands = commands_this_attempt
                attempt.assistant_output = _clip(assistant_text, 4000)
                self._state.stop_reason = StopReason.ERROR.value
                self._state.finished_at = time.time()
                save_loop_state(self._state)
                yield _sse_event({
                    "type": "loop_status",
                    "loop_id": self._state.loop_id,
                    "status": "stopped",
                    "stop_reason": StopReason.ERROR.value,
                    "error": attempt.error,
                    "attempt": self._state.current_attempt,
                })
                return

            if permission_blocked:
                return

            # Finalize attempt
            attempt.finished_at = time.time()
            attempt.commands = commands_this_attempt
            attempt.assistant_output = _clip(assistant_text, 4000)
            attempt.test_output_tail = test_output

            # Parse loop result from assistant output
            result = _parse_loop_result(assistant_text)

            if result == "success":
                attempt.test_passed = True
                self._state.stop_reason = StopReason.SUCCESS.value
                self._state.finished_at = time.time()
                self._state.phase = LoopPhase.DONE.value
                save_loop_state(self._state)
                yield _sse_event({
                    "type": "loop_status",
                    "loop_id": self._state.loop_id,
                    "status": "completed",
                    "stop_reason": StopReason.SUCCESS.value,
                    "attempt": self._state.current_attempt,
                })
                return

            if result == "stop":
                self._state.stop_reason = StopReason.ERROR.value
                self._state.finished_at = time.time()
                self._state.phase = LoopPhase.DONE.value
                save_loop_state(self._state)
                yield _sse_event({
                    "type": "loop_status",
                    "loop_id": self._state.loop_id,
                    "status": "stopped",
                    "stop_reason": "agent_stopped",
                    "attempt": self._state.current_attempt,
                })
                return

            # Treat as retry (explicit or implicit)
            attempt.test_passed = False
            self._state.total_test_failures += 1
            self._state.phase = LoopPhase.INSPECT.value

            # Extract micro-plan from first attempt assistant output
            if self._state.current_attempt == 1 and not self._state.micro_plan:
                self._state.micro_plan = _extract_micro_plan(assistant_text)

            save_loop_state(self._state)
            # Continue loop

    def _check_budget(self) -> StopReason | None:
        """Check all budget limits.  Return a stop reason or None."""
        if self._state.current_attempt >= self._budget.max_attempts:
            return StopReason.MAX_ATTEMPTS

        if self._state.total_commands >= self._budget.max_commands:
            return StopReason.MAX_COMMANDS

        if self._state.elapsed_seconds >= self._budget.max_wall_seconds:
            return StopReason.MAX_WALL_TIME

        if (
            self._budget.max_test_failures is not None
            and self._state.total_test_failures >= self._budget.max_test_failures
        ):
            return StopReason.MAX_FAILURES

        return None

    def _budget_remaining(self) -> dict:
        return {
            "attempts": max(0, self._budget.max_attempts - self._state.current_attempt),
            "commands": max(0, self._budget.max_commands - self._state.total_commands),
            "wall_seconds": max(0.0, self._budget.max_wall_seconds - self._state.elapsed_seconds),
        }

    async def _run_agent_iteration(self, prompt: str) -> AsyncIterator[str]:
        """Run one agent pass using the existing streaming infrastructure."""
        # Import here to avoid circular imports
        from boss.api import _stream_chat_run
        from boss.context.manager import SessionContextManager

        ctx = SessionContextManager()
        prepared = ctx.prepare_input(self._session_id, prompt)

        async for chunk in _stream_chat_run(
            run_input=prepared.model_input,
            session_id=self._session_id,
            emit_session=False,
            mode=self._mode,
            workspace_root=self._workspace_root,
            loop_id=self._state.loop_id,
        ):
            yield chunk


def _try_parse_sse(chunk: str) -> dict | None:
    """Try to parse an SSE data line into a dict."""
    if not chunk.startswith("data: "):
        return None
    try:
        return json.loads(chunk[6:].strip())
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_micro_plan(text: str) -> list[str]:
    """Extract numbered steps from assistant output as a micro-plan."""
    import re

    steps: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)[.)]\s+(.+)", line)
        if m:
            steps.append(m.group(2).strip())
    return steps[:10]  # cap at 10 steps
