"""Boss-native layered prompt assembly system.

Composes durable instructions from multiple layers:

1. **Core operating instructions** — autonomy, tool discipline, output style
2. **Mode-specific instructions** — what the agent can/cannot do in this mode
3. **Agent-role instructions** — specialist behavior (mac, code, research, etc.)
4. **Project instructions** — from BOSS.md
5. **Environment context** — from .boss/environment.json
6. **Repo rules** — from .boss/rules/*.md, filtered by mode and agent target
7. **Review guidance** — from .boss/review.md, only in review mode
8. **Frontend guidance** — triggered by UI-related task signals

Transient context (memory injection, session history) is handled separately
by the context manager and is NOT part of the durable instruction layers.
"""

from boss.prompting.builder import PromptBuilder, PromptResult
from boss.prompting.layers import PromptLayer, PromptLayerKind

__all__ = [
    "PromptBuilder",
    "PromptResult",
    "PromptLayer",
    "PromptLayerKind",
]
