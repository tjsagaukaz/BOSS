# Boss Project Instructions

Boss is a local-first personal agent split between a Python backend (`boss/`) and a native SwiftUI macOS client (`BossApp/`). This file is the primary durable instruction source for all Boss agent work.

## Product Identity

Boss is a single-user, local-first system. All state, logs, diagnostics, and persistence stay on the user's machine. The product surface is Boss-native — no vendor-specific naming, no hosted state, no cloud persistence beyond the configured LLM API.

## Working Expectations

Explore before acting. Read files, search symbols, and inspect structure before making edits. Never guess at paths, APIs, or module boundaries when a search would confirm them.

Persist until the task is done. If something breaks, diagnose and fix it. If a test fails, read the output, understand the cause, and retry with a corrected approach. Do not stop at analysis when implementation is expected. Do not end a turn by restating what you found — finish the work.

Deliver working code, not plans. Unless the user explicitly asks for a plan or the mode is `plan`/`ask`, the expected output is implemented, verified changes. If some details are ambiguous, make reasonable assumptions and complete a working version.

Be concise. Do not narrate your process. Do not emit planning chatter, status updates, or step-by-step commentary. The streaming UI already shows tool calls. When work is done, confirm briefly what changed and why.

## Governance Boundaries

- Tool calls classified as `edit`, `run`, or `external` require user approval unless a stored permission rule already covers them. `read`, `search`, and `plan` calls are auto-allowed.
- Pending approval state must survive across restarts. Never discard or overwrite pending run files.
- `.bossignore` is an access boundary. `.bossindexignore` is an indexing boundary. Respect both.
- Memory injection and distillation are controlled by `.boss/config.toml`. Do not assume they are always on.

## Cross-Stack Conventions

- Backend changes: verify with `cd /Users/tj/boss && python3 -m compileall boss`.
- Client changes: verify with `cd /Users/tj/boss/BossApp && swift build`.
- When a task touches both stacks, verify both before calling the work done.
- Regression harness: `cd /Users/tj/boss && /Users/tj/boss/.venv/bin/python -m unittest tests.test_regression_harness`.
- Full release check: `bash /Users/tj/boss/scripts/release_check.sh`.

## Repo Structure

- `boss/` — Python backend: API, agents, execution governance, memory, loop engine, prompting, tools.
- `BossApp/Sources/` — SwiftUI macOS client: API client, chat UI, permissions, jobs, diagnostics.
- `.boss/` — Project control files: config, rules, environment, review behavior.
- `docs/` — Design notes and architecture documents.
- `scripts/` — Dev tooling: smoke tests, release checks, branch helpers.

## Instruction Layering

Boss uses a layered prompt architecture. This file (BOSS.md) provides project-level instructions. Mode-specific and role-specific behavior is defined in the prompting subsystem. Repo-scoped rules live in `.boss/rules/*.md`. Review behavior lives in `.boss/review.md`. Transient context (session history, memory injection) is handled separately and is never part of durable instructions.

Do not duplicate instruction content across layers. If a behavior belongs in a rule file, put it there. If it belongs in the core operating instructions, put it in `boss/prompting/core_instructions.py`.
