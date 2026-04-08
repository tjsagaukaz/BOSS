"""Prompt layer data structures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PromptLayerKind(StrEnum):
    """Classification of a prompt layer for diagnostics."""

    CORE = "core"
    MODE = "mode"
    ROLE = "role"
    PROJECT = "project"
    ENVIRONMENT = "environment"
    RULE = "rule"
    REVIEW = "review"
    FRONTEND = "frontend"


@dataclass(frozen=True)
class PromptLayer:
    """A single named layer of instructions.

    ``kind`` classifies the layer for diagnostics.
    ``source`` is a human-readable label (e.g. the rule file name).
    ``content`` is the rendered text.
    ``active`` indicates whether the layer was actually included in the
    final prompt.  Layers may be loaded but excluded (e.g. review
    guidance outside review mode) and still appear in diagnostics with
    ``active=False``.
    """

    kind: PromptLayerKind
    source: str
    content: str
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "active": self.active,
            "content_length": len(self.content),
        }
