from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

try:  # pragma: no cover - dependency availability is environment specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from boss.types import MCPConnector


class MCPConnectorRegistry:
    DEFAULT_CONNECTORS = {
        "connectors": [
            {
                "name": "filesystem",
                "transport": "stdio",
                "target": "python3",
                "args": ["-m", "http.server", "--help"],
                "capabilities": ["files", "inspection"],
                "enabled": False,
                "description": "Placeholder local filesystem connector definition.",
            }
        ]
    }

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).resolve()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_config()

    def list_connectors(self) -> list[MCPConnector]:
        raw = self._load_raw()
        connectors: list[MCPConnector] = []
        for item in raw.get("connectors", []) or []:
            connectors.append(
                MCPConnector(
                    name=str(item.get("name", "")).strip(),
                    transport=str(item.get("transport", "stdio")).strip().lower(),
                    target=str(item.get("target", "")).strip(),
                    capabilities=[str(value) for value in item.get("capabilities", []) if str(value).strip()],
                    enabled=bool(item.get("enabled", True)),
                    args=[str(value) for value in item.get("args", [])],
                    description=str(item.get("description", "")).strip(),
                    metadata=dict(item.get("metadata", {}) or {}),
                )
            )
        return [connector for connector in connectors if connector.name and connector.target]

    def add_connector(
        self,
        *,
        name: str,
        transport: str,
        target: str,
        args: list[str] | None = None,
        capabilities: list[str] | None = None,
        enabled: bool = True,
        description: str = "",
    ) -> MCPConnector:
        raw = self._load_raw()
        connectors = raw.get("connectors", []) or []
        if any(str(item.get("name", "")).strip() == name for item in connectors):
            raise ValueError(f"MCP connector '{name}' already exists.")
        entry = {
            "name": name,
            "transport": transport,
            "target": target,
            "args": list(args or []),
            "capabilities": list(capabilities or []),
            "enabled": bool(enabled),
            "description": description,
        }
        connectors.append(entry)
        raw["connectors"] = connectors
        self._write_raw(raw)
        return MCPConnector(
            name=name,
            transport=transport,
            target=target,
            args=list(args or []),
            capabilities=list(capabilities or []),
            enabled=enabled,
            description=description,
        )

    def remove_connector(self, name: str) -> bool:
        raw = self._load_raw()
        connectors = raw.get("connectors", []) or []
        updated = [item for item in connectors if str(item.get("name", "")).strip() != name]
        if len(updated) == len(connectors):
            return False
        raw["connectors"] = updated
        self._write_raw(raw)
        return True

    def health(self) -> list[dict[str, Any]]:
        health: list[dict[str, Any]] = []
        for connector in self.list_connectors():
            status = {
                "name": connector.name,
                "transport": connector.transport,
                "target": connector.target,
                "enabled": connector.enabled,
                "capabilities": connector.capabilities,
                "description": connector.description,
                "healthy": False,
                "detail": "Disabled" if not connector.enabled else "",
            }
            if not connector.enabled:
                health.append(status)
                continue
            if connector.transport == "stdio":
                executable = connector.target if "/" in connector.target else shutil.which(connector.target)
                status["healthy"] = executable is not None
                status["detail"] = executable or f"Executable '{connector.target}' not found."
                health.append(status)
                continue
            if connector.transport in {"http", "https"}:
                try:
                    result = subprocess.run(
                        ["curl", "-I", "-sS", connector.target],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    status["healthy"] = result.returncode == 0
                    status["detail"] = "reachable" if status["healthy"] else (result.stderr.strip() or "Unreachable")
                except Exception as exc:  # pragma: no cover - defensive
                    status["healthy"] = False
                    status["detail"] = str(exc)
                health.append(status)
                continue
            status["detail"] = f"Unsupported transport '{connector.transport}'."
            health.append(status)
        return health

    def _ensure_config(self) -> None:
        if self.config_path.exists():
            return
        self._write_raw(dict(self.DEFAULT_CONNECTORS))

    def _load_raw(self) -> dict[str, Any]:
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for MCP connector configuration.")
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if "connectors" not in raw:
            raw["connectors"] = []
        return raw

    def _write_raw(self, raw: dict[str, Any]) -> None:
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for MCP connector configuration.")
        self.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
