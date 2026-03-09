from __future__ import annotations

from pathlib import Path

from boss.workspace.roots_registry import WorkspaceRootsRegistry


def test_roots_registry_discovers_projects_and_resolves_by_name(tmp_path):
    projects_root = tmp_path / "repos"
    projects_root.mkdir()
    project = projects_root / "alpha"
    project.mkdir()
    (project / ".git").mkdir()

    registry = WorkspaceRootsRegistry(tmp_path / "workspace_roots.yaml")
    registry.add_root(name="repos", path=projects_root, mode="projects", discover_children=True, max_depth=1)

    projects = registry.discover_projects()

    assert any(item.name == "alpha" for item in projects)
    reference = registry.resolve("alpha")
    assert Path(reference.root) == project


def test_roots_registry_resolves_absolute_path_as_ad_hoc_reference(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='myapp'\n", encoding="utf-8")

    registry = WorkspaceRootsRegistry(tmp_path / "workspace_roots.yaml")

    reference = registry.resolve(str(project))

    assert reference.mode == "ad_hoc"
    assert Path(reference.root) == project


def test_roots_registry_upgrades_home_root_to_operator_defaults(tmp_path):
    config_path = tmp_path / "workspace_roots.yaml"
    config_path.write_text(
        "\n".join(
            [
                "roots:",
                f"- name: home",
                f"  path: {Path.home()}",
                "  mode: search",
                "  include_root: false",
                "  discover_children: false",
                "  max_depth: 1",
                "  enabled: true",
            ]
        ),
        encoding="utf-8",
    )

    registry = WorkspaceRootsRegistry(config_path)
    roots = registry.describe()

    assert roots[0]["mode"] == "both"
    assert roots[0]["discover_children"] is True
    assert roots[0]["max_depth"] == 2
