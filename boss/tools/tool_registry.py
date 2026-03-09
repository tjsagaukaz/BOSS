from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from boss.types import ToolDefinition


@dataclass
class RegisteredTool:
    definition: ToolDefinition
    category: str = "core"
    plugin: str | None = None
    capabilities: set[str] = field(default_factory=set)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler,
        category: str = "core",
        plugin: str | None = None,
        capabilities: set[str] | None = None,
    ) -> None:
        self._tools[name] = RegisteredTool(
            definition=ToolDefinition(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=handler,
            ),
            category=category,
            plugin=plugin,
            capabilities=set(capabilities or set()),
        )

    def register_definition(
        self,
        definition: ToolDefinition,
        category: str = "core",
        plugin: str | None = None,
        capabilities: set[str] | None = None,
    ) -> None:
        self._tools[definition.name] = RegisteredTool(
            definition=definition,
            category=category,
            plugin=plugin,
            capabilities=set(capabilities or set()),
        )

    def build_definitions(
        self,
        allow_write: bool = False,
        allow_terminal: bool = True,
        allow_commit: bool = False,
        allow_tests: bool = False,
        allow_editor: bool = True,
    ) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for registered in self._tools.values():
            caps = registered.capabilities
            if "write" in caps and not allow_write:
                continue
            if "terminal" in caps and not allow_terminal:
                continue
            if "commit" in caps and not allow_commit:
                continue
            if "tests" in caps and not allow_tests:
                continue
            if "editor" in caps and not allow_editor:
                continue
            definitions.append(registered.definition)
        return definitions

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": registered.definition.name,
                "description": registered.definition.description,
                "category": registered.category,
                "plugin": registered.plugin,
                "capabilities": sorted(registered.capabilities),
            }
            for registered in sorted(self._tools.values(), key=lambda item: item.definition.name)
        ]

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

