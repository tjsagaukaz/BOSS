from __future__ import annotations

from pathlib import Path

from boss.runtime.permissions import PermissionManager


def test_permission_manager_upgrades_legacy_defaults_to_operator_profile(tmp_path):
    policy_path = tmp_path / "permissions_policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "workspace_write_mode: confirm",
                "project_write_mode: confirm",
                "destructive_mode: confirm",
                "allow_web_research: true",
                "allow_mcp: true",
                "allow_workspace_write: true",
                "writable_roots: []",
                "trusted_project_roots: []",
            ]
        ),
        encoding="utf-8",
    )

    manager = PermissionManager(policy_path)
    snapshot = manager.snapshot()

    assert snapshot["workspace_write_mode"] == "auto"
    assert snapshot["project_write_mode"] == "auto"
    assert snapshot["full_access_mode"] is False
    assert str(Path.home()) in snapshot["writable_roots"]
    assert str(Path.home()) in snapshot["trusted_project_roots"]


def test_permission_manager_preserves_explicit_confirm_policy(tmp_path):
    home = Path.home()
    policy_path = tmp_path / "permissions_policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "workspace_write_mode: confirm",
                "project_write_mode: confirm",
                "destructive_mode: confirm",
                "allow_web_research: true",
                "allow_mcp: true",
                "allow_workspace_write: true",
                f"writable_roots: ['{home}']",
                f"trusted_project_roots: ['{home}']",
            ]
        ),
        encoding="utf-8",
    )

    manager = PermissionManager(policy_path)
    snapshot = manager.snapshot()

    assert snapshot["workspace_write_mode"] == "confirm"
    assert snapshot["project_write_mode"] == "confirm"


def test_permission_manager_full_access_allows_writes_anywhere(tmp_path):
    policy_path = tmp_path / "permissions_policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "full_access_mode: true",
                "workspace_write_mode: confirm",
                "project_write_mode: confirm",
                "destructive_mode: confirm",
                "allow_web_research: true",
                "allow_mcp: true",
                "allow_workspace_write: false",
                "writable_roots: []",
                "trusted_project_roots: []",
            ]
        ),
        encoding="utf-8",
    )

    manager = PermissionManager(policy_path)

    assert manager.full_access_enabled() is True
    assert manager.write_allowed(tmp_path / "anywhere" / "demo.txt") is True
