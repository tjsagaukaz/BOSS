"""Sandbox detection: inspect macOS sandbox capabilities and report honestly."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SandboxCapability(StrEnum):
    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SandboxReport:
    platform: str
    sandbox_exec_available: bool
    sandbox_exec_status: str  # SandboxCapability value
    sandbox_exec_note: str
    app_sandbox_available: bool
    app_sandbox_note: str
    enforcement_level: str  # "boss_policy" or "os_sandbox"
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "sandbox_exec_available": self.sandbox_exec_available,
            "sandbox_exec_status": self.sandbox_exec_status,
            "sandbox_exec_note": self.sandbox_exec_note,
            "app_sandbox_available": self.app_sandbox_available,
            "app_sandbox_note": self.app_sandbox_note,
            "enforcement_level": self.enforcement_level,
            "recommendations": list(self.recommendations),
        }


def detect_sandbox_capabilities() -> SandboxReport:
    """Detect what sandbox capabilities are realistically available on this system."""
    system = platform.system()

    if system != "Darwin":
        return SandboxReport(
            platform=system,
            sandbox_exec_available=False,
            sandbox_exec_status=SandboxCapability.UNAVAILABLE.value,
            sandbox_exec_note=f"sandbox-exec is macOS-only; running on {system}",
            app_sandbox_available=False,
            app_sandbox_note=f"App Sandbox is macOS-only; running on {system}",
            enforcement_level="boss_policy",
            recommendations=(
                "Boss enforces execution policy at the application level.",
                "No OS-level sandbox is available on this platform.",
            ),
        )

    # Check sandbox-exec availability
    sandbox_exec_path = shutil.which("sandbox-exec")
    sandbox_exec_available = sandbox_exec_path is not None

    if sandbox_exec_available:
        # sandbox-exec exists but is deprecated since macOS 10.15+
        sandbox_exec_status = SandboxCapability.DEPRECATED.value
        sandbox_exec_note = (
            "sandbox-exec is present but deprecated since macOS Catalina. "
            "Apple has not provided a replacement CLI tool. "
            "Boss does not rely on it for isolation because it may be removed "
            "in a future macOS release without notice."
        )
    else:
        sandbox_exec_status = SandboxCapability.UNAVAILABLE.value
        sandbox_exec_note = "sandbox-exec binary not found on PATH."

    # App Sandbox check — only applies to .app bundles signed with entitlements
    app_sandbox_available = False
    app_sandbox_note = (
        "App Sandbox requires a signed .app bundle with sandbox entitlements. "
        "The Boss backend runs as a Python process, not a sandboxed app bundle. "
        "OS-level App Sandbox is not applicable to this runtime."
    )

    recommendations = [
        "Boss enforces execution policy at the application level (boss_policy).",
        "Command prefixes, write paths, and network access are checked before execution.",
        "Environment variables are scrubbed of secrets in non-full-access profiles.",
    ]

    if sandbox_exec_available:
        recommendations.append(
            "sandbox-exec is available but deprecated; Boss does not use it. "
            "If you need kernel-enforced isolation, consider containerized execution."
        )

    return SandboxReport(
        platform=system,
        sandbox_exec_available=sandbox_exec_available,
        sandbox_exec_status=sandbox_exec_status,
        sandbox_exec_note=sandbox_exec_note,
        app_sandbox_available=app_sandbox_available,
        app_sandbox_note=app_sandbox_note,
        enforcement_level="boss_policy",
        recommendations=tuple(recommendations),
    )


def sandbox_status_payload() -> dict[str, Any]:
    """Return a diagnostic payload describing sandbox capabilities."""
    return detect_sandbox_capabilities().to_dict()
