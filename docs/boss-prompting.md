# Boss Prompting Architecture

Boss uses a layered prompt system to compose agent instructions from independent, inspectable sources. This document explains the architecture, what goes where, and how Boss differs from vendor-specific instruction systems while incorporating their best ideas.

## Layer Stack

When an agent is created, the `PromptBuilder` assembles instructions from these layers in order:

| # | Layer | Source | Always Active |
|---|-------|--------|---------------|
| 1 | Core operating instructions | `boss/prompting/core_instructions.py` | Yes |
| 2 | Mode constraints | `boss/prompting/modes.py` | Yes |
| 3 | Role identity | `boss/prompting/modes.py` (mode-aware) | Yes |
| 4 | Project instructions | `BOSS.md` | Yes (if file exists) |
| 5 | Environment context | `.boss/environment.json` | Yes (if file exists) |
| 6 | Repo rules | `.boss/rules/*.md` (filtered) | Per-rule targeting |
| 7 | Review guidance | `.boss/review.md` + core review discipline | Review mode only |
| 8 | Frontend guidance | `boss/prompting/core_instructions.py` | Auto-detected |

Active layers are concatenated into the final instruction string. Inactive layers (e.g. review guidance outside review mode) are tracked in diagnostics but omitted from the prompt.

## What Goes Where

### `BOSS.md` — Project-Level Instructions

The primary durable instruction file. Contains:
- Product identity and working expectations
- Governance boundaries (permissions, memory, ignore files)
- Cross-stack verification commands
- Repo structure overview
- Instruction layering guidance

This file is loaded for every agent in every mode. Keep it focused on project-wide expectations that apply regardless of which specialist agent is active or which mode is selected.

### `.boss/rules/*.md` — Targeted Rules

Files in `.boss/rules/` carry TOML frontmatter that controls when they apply:

```toml
+++
title = "Backend Python Rules"
targets = ["code", "general"]        # which agent roles see this rule
modes = ["ask", "plan", "agent", "review"]  # which modes activate it
tags = ["backend", "python"]
always = false                       # if true, always included regardless of mode/target
+++
```

Rules are filtered by mode and agent target at build time. A rule with `targets = ["code"]` will not appear in the mac agent's instructions. A rule with `modes = ["review"]` will only appear in review mode.

Use rules for stack-specific conventions (Python patterns, SwiftUI patterns), mode-specific behavior (review discipline), or any instruction that should not apply universally.

### `.boss/review.md` — Review Behavior

Loaded as a prompt layer only when the mode is `review`. Contains evidence prioritization, finding format, and verification expectations specific to code review. Complements the review rules in `.boss/rules/30-review-mode.md` without duplicating them.

### `.boss/environment.json` — Environment Context

Machine-specific context: platform, workspace root, validation commands, constraints. Loaded as a prompt layer for all agents. This is where local environment details live so they do not need to be hardcoded in instruction text.

### `boss/prompting/core_instructions.py` — Core Operating Contract

Contains the `CORE_OPERATING` instructions (autonomy, persistence, tool discipline, output style, error handling) that apply to every agent in every mode. Also contains `REVIEW_DISCIPLINE` and `FRONTEND_GUIDANCE` blocks that activate conditionally.

These are in Python code rather than on-disk files because they are part of the Boss product itself, not project configuration. They should only change when the fundamental operating behavior of Boss agents changes.

### `boss/prompting/modes.py` — Mode and Role Definitions

Mode constraints (`ask`, `plan`, `agent`, `review`) and role identity sentences (`general`, `mac`, `research`, `reasoning`, `code`). Includes mode-aware role overrides (e.g. the `code` role becomes a "code reviewer" in review mode).

## What Does NOT Go in Durable Instructions

Transient context is handled separately by `SessionContextManager` and the memory injection system:

- **Session history**: recent conversation items
- **Memory injection**: relevant persisted knowledge recalled for this request
- **Session summaries**: compressed earlier conversation context

These are injected into the model input list as conversation items, not as part of the system instructions. They change per-request and should never be baked into durable instruction layers.

## Diagnostics

The prompt builder exposes diagnostics via `PromptResult.diagnostics()`, which returns:

```json
{
  "total_layers": 10,
  "active_layers": 7,
  "total_chars": 4200,
  "layers": [
    {"kind": "core", "source": "core_instructions.CORE_OPERATING", "active": true, "content_length": 1800},
    {"kind": "rule", "source": "/path/to/.boss/rules/00-core.md", "active": true, "content_length": 450},
    {"kind": "review", "source": "core_instructions.REVIEW_DISCIPLINE", "active": false, "content_length": 380}
  ]
}
```

The API exposes this at `GET /api/system/prompt-diagnostics?mode=agent&agent_name=general` for debugging which layers are active for a given configuration.

## Design Principles

### Layered, Not Monolithic

Instructions are composed from independent layers, each with a clear purpose and scope. No single file contains the complete prompt. This keeps each file maintainable, avoids duplication, and makes it easy to see which instructions apply to a given agent/mode combination.

### Boss-Native, Not Vendor-Specific

Boss does not use `.codex`, `.cursor`, or other vendor instruction files. The instruction system is Boss's own: `BOSS.md`, `.boss/rules/`, `.boss/review.md`, `.boss/environment.json`, and `.boss/config.toml`. The architecture is inspired by the same ideas that motivate vendor instruction files — repo-scoped, layered, inspectable — but the naming, format, and loading logic are Boss's own.

### Durable vs. Transient

The instruction system draws a hard line between durable instructions (project rules, operating discipline, mode constraints) and transient context (session history, memory recall, user messages). Durable instructions are assembled once at agent creation time and do not change within a conversation. Transient context is injected per-request.

### Inspectable

Every layer tracks its kind, source, active status, and content length. Diagnostics are available at build time and via the API. When debugging unexpected agent behavior, you can see exactly which instruction layers were active and what content they contributed.
