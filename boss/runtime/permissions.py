from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pragma: no cover - dependency availability is environment specific
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from boss.types import PermissionPolicy, utc_now_iso


class PermissionManager:
    DEFAULT_POLICY = {
        "full_access_mode": False,
        "workspace_write_mode": "auto",
        "project_write_mode": "auto",
        "destructive_mode": "confirm",
        "allow_web_research": True,
        "allow_mcp": True,
        "allow_workspace_write": True,
        "writable_roots": [str(Path.home())],
        "trusted_project_roots": [str(Path.home())],
    }

    def __init__(self, policy_path: str | Path) -> None:
        self.policy_path = Path(policy_path).resolve()
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_policy()
        self._ensure_operator_defaults()

    def load(self) -> PermissionPolicy:
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for permission policy loading.")
        raw = yaml.safe_load(self.policy_path.read_text(encoding="utf-8")) or {}
        merged = dict(self.DEFAULT_POLICY)
        merged.update({key: value for key, value in raw.items() if key in merged})
        return PermissionPolicy(
            full_access_mode=bool(merged["full_access_mode"]),
            workspace_write_mode=str(merged["workspace_write_mode"]).strip().lower() or "confirm",
            project_write_mode=str(merged["project_write_mode"]).strip().lower() or "confirm",
            destructive_mode=str(merged["destructive_mode"]).strip().lower() or "confirm",
            allow_web_research=bool(merged["allow_web_research"]),
            allow_mcp=bool(merged["allow_mcp"]),
            allow_workspace_write=bool(merged["allow_workspace_write"]),
            writable_roots=[str(item) for item in merged.get("writable_roots", [])],
            trusted_project_roots=[str(item) for item in merged.get("trusted_project_roots", [])],
            updated_at=utc_now_iso(),
        )

    def snapshot(self) -> dict[str, Any]:
        policy = self.load()
        return {
            "full_access_mode": policy.full_access_mode,
            "workspace_write_mode": policy.workspace_write_mode,
            "project_write_mode": policy.project_write_mode,
            "destructive_mode": policy.destructive_mode,
            "allow_web_research": policy.allow_web_research,
            "allow_mcp": policy.allow_mcp,
            "allow_workspace_write": policy.allow_workspace_write,
            "writable_roots": policy.writable_roots,
            "trusted_project_roots": policy.trusted_project_roots,
        }

    def web_research_allowed(self) -> bool:
        return self.load().allow_web_research

    def mcp_allowed(self) -> bool:
        return self.load().allow_mcp

    def full_access_enabled(self) -> bool:
        return self.load().full_access_mode

    def write_allowed(self, path: str | Path, *, project_root: str | Path | None = None) -> bool:
        policy = self.load()
        if policy.full_access_mode:
            return True
        candidate = Path(path).expanduser().resolve()
        if project_root is not None:
            root = Path(project_root).expanduser().resolve()
            if self._is_relative_to(candidate, root):
                return policy.project_write_mode in {"auto", "confirm"}
        if not policy.allow_workspace_write:
            return False
        for root_text in policy.writable_roots:
            if self._is_relative_to(candidate, Path(root_text).expanduser().resolve()):
                return policy.workspace_write_mode in {"auto", "confirm"}
        return False

    def _ensure_policy(self) -> None:
        if self.policy_path.exists():
            return
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for permission policy loading.")
        self.policy_path.write_text(yaml.safe_dump(self.DEFAULT_POLICY, sort_keys=False), encoding="utf-8")

    def _ensure_operator_defaults(self) -> None:
        if yaml is None:  # pragma: no cover
            raise RuntimeError("PyYAML is required for permission policy loading.")
        raw = yaml.safe_load(self.policy_path.read_text(encoding="utf-8")) or {}
        changed = False
        home_root = str(Path.home().resolve())
        existing_writable_roots = [str(item) for item in raw.get("writable_roots", []) or []]
        existing_trusted_roots = [str(item) for item in raw.get("trusted_project_roots", []) or []]

        writable_roots = list(existing_writable_roots)
        trusted_roots = list(existing_trusted_roots)

        if home_root not in writable_roots:
            writable_roots.append(home_root)
            raw["writable_roots"] = writable_roots
            changed = True

        if home_root not in trusted_roots:
            trusted_roots.append(home_root)
            raw["trusted_project_roots"] = trusted_roots
            changed = True

        looks_legacy = (
            str(raw.get("workspace_write_mode", "")).strip().lower() == "confirm"
            and str(raw.get("project_write_mode", "")).strip().lower() == "confirm"
            and not existing_writable_roots
            and not existing_trusted_roots
            and bool(raw.get("allow_workspace_write", True))
        )
        if looks_legacy:
            raw["workspace_write_mode"] = "auto"
            raw["project_write_mode"] = "auto"
            changed = True

        if changed:
            self.policy_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
