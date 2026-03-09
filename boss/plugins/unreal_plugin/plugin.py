from __future__ import annotations

from pathlib import Path

PLUGIN_NAME = "Unreal Engine"
PLUGIN_DESCRIPTION = "Provides discovery helpers for Unreal Engine projects."


def register(registry, context):
    project_root = Path(context["project_root"]).resolve()

    def find_unreal_projects(_args):
        matches = [str(path.relative_to(project_root)) for path in project_root.rglob("*.uproject")]
        return {"projects": matches}

    registry.register_tool(
        name="find_unreal_projects",
        description="Find Unreal Engine .uproject files in the active project.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=find_unreal_projects,
        category="plugin",
        plugin=PLUGIN_NAME,
    )
    return ["find_unreal_projects"]

