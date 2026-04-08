"""Loop budget and execution style definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExecutionStyle(StrEnum):
    """How the agent executes a task."""
    SINGLE_PASS = "single_pass"
    ITERATIVE = "iterative"


@dataclass(frozen=True)
class LoopBudget:
    """Resource limits for an iterative loop run.

    All limits are enforced between iterations — the engine never
    interrupts a running agent turn mid-execution.
    """

    max_attempts: int = 5
    max_commands: int = 30
    max_wall_seconds: float = 300.0
    max_test_failures: int | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            object.__setattr__(self, "max_attempts", 1)
        if self.max_commands < 1:
            object.__setattr__(self, "max_commands", 1)
        if self.max_wall_seconds <= 0:
            object.__setattr__(self, "max_wall_seconds", 30.0)

    def to_dict(self) -> dict:
        return {
            "max_attempts": self.max_attempts,
            "max_commands": self.max_commands,
            "max_wall_seconds": self.max_wall_seconds,
            "max_test_failures": self.max_test_failures,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LoopBudget:
        return cls(
            max_attempts=data.get("max_attempts", 5),
            max_commands=data.get("max_commands", 30),
            max_wall_seconds=data.get("max_wall_seconds", 300.0),
            max_test_failures=data.get("max_test_failures"),
        )
