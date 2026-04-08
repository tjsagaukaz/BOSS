# Boss Project Instructions

Boss is a local-first personal agent with a Python backend and a native SwiftUI macOS client.

## Durable Product Rules

- Keep the Boss product surface vendor-neutral and Boss-native.
- Preserve existing chat, SSE, and persistence contracts unless there is a clear migration path.
- Prefer additive changes over rewrites.
- Keep runtime state, logs, and diagnostics local.
- Use repo-scoped Boss control files in `.boss/` for project policy instead of hardcoded special cases.

## Validation Expectations

- Validate backend changes locally with `cd /Users/tj/boss && python3 -m compileall boss`.
- Validate the macOS client locally with `cd /Users/tj/boss/BossApp && swift build`.
- SwiftUI and macOS automation behavior must remain locally testable on macOS.
- When a task changes both backend and app behavior, verify both sides before calling the work done.

## Execution Expectations

- Prefer read and search actions before edit or run actions.
- Keep permission prompts understandable and scoped to the real action.
- Treat `.bossignore` as a repo-scoped access boundary and `.bossindexignore` as an indexing boundary.
