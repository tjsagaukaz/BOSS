from __future__ import annotations

from pathlib import Path

PLUGIN_NAME = "iOS"
PLUGIN_DESCRIPTION = "Provides discovery helpers for Xcode workspaces and projects."


def register(registry, context):
    project_root = Path(context["project_root"]).resolve()

    def find_xcode_targets(_args):
        matches = [
            str(path.relative_to(project_root))
            for pattern in ("*.xcworkspace", "*.xcodeproj")
            for path in project_root.rglob(pattern)
        ]
        return {"targets": sorted(set(matches))}

    registry.register_tool(
        name="find_xcode_targets",
        description="Find Xcode workspaces and project files in the active project.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=find_xcode_targets,
        category="plugin",
        plugin=PLUGIN_NAME,
    )
    return ["find_xcode_targets"]

