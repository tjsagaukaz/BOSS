from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


@dataclass
class PluginInfo:
    name: str
    description: str
    path: str
    tools: list[str] = field(default_factory=list)


class PluginManager:
    def __init__(self, plugins_root: str | Path) -> None:
        self.plugins_root = Path(plugins_root).resolve()
        self.plugins_root.mkdir(parents=True, exist_ok=True)
        self._plugins: list[PluginInfo] = []

    def discover_plugins(self) -> list[PluginInfo]:
        discovered: list[PluginInfo] = []
        for plugin_file in sorted(self.plugins_root.glob("*/plugin.py")):
            module = self._load_module(plugin_file)
            name = str(getattr(module, "PLUGIN_NAME", plugin_file.parent.name))
            description = str(getattr(module, "PLUGIN_DESCRIPTION", "No description provided."))
            discovered.append(PluginInfo(name=name, description=description, path=str(plugin_file.parent)))
        self._plugins = discovered
        return list(discovered)

    def load_into_registry(self, registry, context: dict[str, Any]) -> list[PluginInfo]:
        loaded: list[PluginInfo] = []
        for plugin_file in sorted(self.plugins_root.glob("*/plugin.py")):
            module = self._load_module(plugin_file)
            name = str(getattr(module, "PLUGIN_NAME", plugin_file.parent.name))
            description = str(getattr(module, "PLUGIN_DESCRIPTION", "No description provided."))
            tool_names: list[str] = []
            register = getattr(module, "register", None)
            if callable(register):
                maybe_tools = register(registry, context)
                if isinstance(maybe_tools, list):
                    tool_names = [str(item) for item in maybe_tools]
            loaded.append(
                PluginInfo(
                    name=name,
                    description=description,
                    path=str(plugin_file.parent),
                    tools=tool_names,
                )
            )
        self._plugins = loaded
        return list(loaded)

    def list_plugins(self) -> list[PluginInfo]:
        if not self._plugins:
            return self.discover_plugins()
        return list(self._plugins)

    def _load_module(self, plugin_file: Path) -> ModuleType:
        module_name = f"boss_plugin_{plugin_file.parent.name}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load plugin from {plugin_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

