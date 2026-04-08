+++
title = "Backend Python Rules"
targets = ["code", "general"]
modes = ["ask", "plan", "agent", "review"]
tags = ["backend", "python", "fastapi"]
+++

# Backend Python Rules

## Code Conventions
- Follow existing patterns: naming, module structure, import style, and error handling. If you must diverge, state why.
- Prefer stdlib-first changes. Do not add a new dependency unless it is clearly justified and the stdlib cannot do the job.
- New filesystem paths must go through `boss.config.Settings` with sane local defaults. Do not scatter path literals.
- Keep type annotations consistent with the surrounding code. Do not add annotations to code you are not changing.

## API and Contract Discipline
- FastAPI endpoint changes must be backward compatible. Add fields to SSE payloads if needed; do not rename or remove fields the app already uses.
- When adding an endpoint, include the route, method, request/response shape, and any SSE events in the implementation — not in a separate spec document.
- Permission-gated tool calls must register clear human-readable scope labels so approval prompts stay understandable.

## Persistence and State
- Memory, execution state, and runtime metadata must remain readable across restarts.
- Pending approval runs live in `~/.boss/pending_runs/` and must survive process restarts. Never silently discard them.
- Background job state lives in `~/.boss/jobs/`. Job status transitions must be explicit (running → completed/failed/cancelled).

## Verification
- Always verify with `python3 -m compileall boss` before calling backend work done.
- Run `python -m unittest tests.test_regression_harness` when the change touches agents, execution, control, or API surface.
- Do not claim tests pass without running them.
