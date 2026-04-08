+++
title = "Core Boss Rules"
targets = ["all"]
modes = ["ask", "plan", "agent", "review"]
tags = ["core", "product"]
always = true
+++

# Core Boss Rules

## Product Discipline
- Boss is local-first. All state, persistence, and diagnostics stay on the user's machine.
- Preserve existing API, SSE, and persistence contracts unless the task explicitly includes migration work.
- Prefer additive changes over rewrites. When changing a persisted format, keep reads backward compatible.
- Use `.boss/` control files for project policy. Do not hardcode special cases into agent or API code.

## Working Style
- Explore first: read files and search the codebase before making assumptions. Never guess at structure, paths, or APIs.
- Persist through errors: if a build or test fails, read the output, diagnose the root cause, and fix it. Do not report the first failure and stop.
- Prefer the smallest coherent change that addresses the root cause. Avoid over-engineering, speculative refactors, or adding features that were not requested.
- When the task is implementation, deliver working code. Do not end a turn with analysis or a plan unless the user asked for one.
- Do not revert or discard existing changes you did not make. If you encounter unexpected modifications in the working tree, work with them or ask.

## Output Style
- Be concise and direct. Lead with the result, not the process.
- Do not narrate intermediate steps, restate the question, or emit planning chatter.
- When work is complete, confirm briefly: what changed, what was verified, and any remaining risks.
- Do not dump large file contents the user can already see. Reference paths instead.

## Safety and Integrity
- Never fabricate file contents, test results, or command output.
- Do not use destructive git commands (`reset --hard`, `checkout --`, `push --force`) unless the user explicitly requests them.
- Respect the permission model. `edit`, `run`, and `external` actions require approval; do not bypass governance checks.
- Make runtime behavior debuggable with local diagnostics rather than hidden state.
