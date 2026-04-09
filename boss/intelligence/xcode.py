"""Xcode / iOS project intelligence — inspect Apple project structure.

Parses .xcodeproj/project.pbxproj (old-style plist format), Info.plist,
entitlements, and xcscheme files to extract project structure without
requiring xcodebuild or Xcode itself.

Limitations (documented, not hidden):
- pbxproj parsing uses regex heuristics on the ASCII-plist format;
  it does not handle every edge case of the NeXTSTEP plist grammar.
- Scheme discovery reads XML from .xcscheme files but does not resolve
  all macro expansions.
- Signing identity detection is best-effort from build-settings strings.
"""

from __future__ import annotations

import plistlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Data models ─────────────────────────────────────────────────────


@dataclass
class XcodeTarget:
    """A single native or aggregate target inside an Xcode project."""
    name: str
    product_type: str          # e.g. com.apple.product-type.application
    bundle_identifier: str | None = None
    product_name: str | None = None
    build_configurations: list[str] = field(default_factory=list)
    signing_style: str | None = None   # "automatic" | "manual" | None
    team_id: str | None = None
    entitlements_file: str | None = None
    info_plist_file: str | None = None
    source_files: list[str] = field(default_factory=list)
    is_test_target: bool = False
    is_app_target: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "product_type": self.product_type,
            "bundle_identifier": self.bundle_identifier,
            "product_name": self.product_name,
            "build_configurations": self.build_configurations,
            "signing_style": self.signing_style,
            "team_id": self.team_id,
            "entitlements_file": self.entitlements_file,
            "info_plist_file": self.info_plist_file,
            "source_file_count": len(self.source_files),
            "is_test_target": self.is_test_target,
            "is_app_target": self.is_app_target,
        }


@dataclass
class XcodeScheme:
    """A build scheme discovered from .xcscheme files."""
    name: str
    build_targets: list[str] = field(default_factory=list)
    test_targets: list[str] = field(default_factory=list)
    launch_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "build_targets": self.build_targets,
            "test_targets": self.test_targets,
            "launch_target": self.launch_target,
        }


@dataclass
class XcodeProjectInfo:
    """Aggregated intelligence about an Xcode project."""
    project_path: str
    xcodeproj_path: str | None = None
    xcworkspace_path: str | None = None
    targets: list[XcodeTarget] = field(default_factory=list)
    schemes: list[XcodeScheme] = field(default_factory=list)
    build_configurations: list[str] = field(default_factory=list)
    has_swift_package: bool = False
    info_plists: list[str] = field(default_factory=list)
    entitlements_files: list[str] = field(default_factory=list)
    inferred: list[str] = field(default_factory=list)  # what was inferred vs confirmed
    errors: list[str] = field(default_factory=list)

    @property
    def app_targets(self) -> list[XcodeTarget]:
        return [t for t in self.targets if t.is_app_target]

    @property
    def test_targets(self) -> list[XcodeTarget]:
        return [t for t in self.targets if t.is_test_target]

    @property
    def likely_app_target(self) -> XcodeTarget | None:
        apps = self.app_targets
        if len(apps) == 1:
            return apps[0]
        # Prefer iOS app over watchOS/tvOS extensions
        for t in apps:
            if "watch" not in t.product_type and "extension" not in t.product_type:
                return t
        return apps[0] if apps else None

    def to_dict(self) -> dict[str, Any]:
        likely = self.likely_app_target
        return {
            "project_path": self.project_path,
            "xcodeproj_path": self.xcodeproj_path,
            "xcworkspace_path": self.xcworkspace_path,
            "targets": [t.to_dict() for t in self.targets],
            "schemes": [s.to_dict() for s in self.schemes],
            "build_configurations": self.build_configurations,
            "has_swift_package": self.has_swift_package,
            "info_plists": self.info_plists,
            "entitlements_files": self.entitlements_files,
            "likely_app_target": likely.to_dict() if likely else None,
            "test_targets": [t.to_dict() for t in self.test_targets],
            "inferred": self.inferred,
            "errors": self.errors,
        }

    def summary(self) -> str:
        lines: list[str] = []
        lines.append(f"Project: {self.project_path}")
        if self.xcodeproj_path:
            lines.append(f"Xcode project: {self.xcodeproj_path}")
        if self.xcworkspace_path:
            lines.append(f"Workspace: {self.xcworkspace_path}")
        if self.has_swift_package:
            lines.append("Swift Package Manager: yes")

        if self.build_configurations:
            lines.append(f"Build configurations: {', '.join(self.build_configurations)}")

        if self.targets:
            lines.append(f"\nTargets ({len(self.targets)}):")
            for t in self.targets:
                kind = "app" if t.is_app_target else ("test" if t.is_test_target else "other")
                bid = f" [{t.bundle_identifier}]" if t.bundle_identifier else ""
                signing = f" signing={t.signing_style}" if t.signing_style else ""
                lines.append(f"  - {t.name} ({kind}){bid}{signing}")

        likely = self.likely_app_target
        if likely:
            lines.append(f"\nLikely app target: {likely.name}")
            if likely.bundle_identifier:
                lines.append(f"  Bundle ID: {likely.bundle_identifier}")
            if likely.signing_style:
                lines.append(f"  Signing: {likely.signing_style}")
            if likely.team_id:
                lines.append(f"  Team: {likely.team_id}")

        if self.schemes:
            lines.append(f"\nSchemes ({len(self.schemes)}):")
            for s in self.schemes:
                lines.append(f"  - {s.name}")

        if self.entitlements_files:
            lines.append(f"\nEntitlements: {', '.join(self.entitlements_files)}")

        if self.info_plists:
            lines.append(f"Info.plist files: {', '.join(self.info_plists)}")

        if self.inferred:
            lines.append(f"\nInferred (not confirmed): {', '.join(self.inferred)}")
        if self.errors:
            lines.append(f"\nParse warnings: {'; '.join(self.errors)}")

        return "\n".join(lines)


# ── pbxproj parser ──────────────────────────────────────────────────

# The project.pbxproj file uses an old-style ASCII plist format.
# We extract key structures via targeted regex rather than a full parser.

_SECTION_RE = re.compile(
    r"/\*\s*Begin\s+((?:PBX|XC)\w+)\s+section\s*\*/\s*\n(.*?)/\*\s*End\s+\1\s+section\s*\*/",
    re.DOTALL,
)
_OBJECT_ID_RE = re.compile(
    r"([0-9A-Fa-f]{16,24})\s*/\*.*?\*/\s*=\s*\{",
)
_KV_RE = re.compile(
    r"(\w+)\s*=\s*(?:\"([^\"]*)\"|([^;\n]+?))\s*;",
)
_COMMENT_RE = re.compile(r"/\*.*?\*/")
_LIST_RE = re.compile(
    r"(\w+)\s*=\s*\((.*?)\)",
    re.DOTALL,
)
_LIST_ITEM_RE = re.compile(r"([0-9A-Fa-f]{16,24})\s*/\*.*?\*/")


def _extract_brace_body(text: str, start: int) -> str | None:
    """Extract the body between matched braces starting at `start` (the opening '{')."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
        i += 1
    return None


def _parse_pbxproj_sections(content: str) -> dict[str, dict[str, dict[str, str]]]:
    """Extract sections from a pbxproj file into {section_type: {object_id: {key: value}}}."""
    sections: dict[str, dict[str, dict[str, str]]] = {}
    for section_match in _SECTION_RE.finditer(content):
        section_type = section_match.group(1)
        section_body = section_match.group(2)
        objects: dict[str, dict[str, str]] = {}
        for obj_id_match in _OBJECT_ID_RE.finditer(section_body):
            obj_id = obj_id_match.group(1)
            brace_start = obj_id_match.end() - 1  # position of '{'
            obj_body = _extract_brace_body(section_body, brace_start)
            if obj_body is None:
                continue
            # Strip inline comments so KV regex isn't confused by quotes in comments
            obj_body_clean = _COMMENT_RE.sub("", obj_body)
            fields: dict[str, str] = {}
            for kv in _KV_RE.finditer(obj_body_clean):
                key = kv.group(1)
                value = kv.group(2) if kv.group(2) is not None else kv.group(3).strip()
                fields[key] = value
            # Also extract list fields (use original body for list items with comments)
            for list_match in _LIST_RE.finditer(obj_body):
                key = list_match.group(1)
                items = _LIST_ITEM_RE.findall(list_match.group(2))
                if items:
                    fields[f"_list_{key}"] = ",".join(items)
            objects[obj_id] = fields
        sections[section_type] = objects
    return sections


def _resolve_build_configs(
    sections: dict[str, dict[str, dict[str, str]]],
    config_list_id: str | None,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Resolve an XCConfigurationList to config names and their build settings."""
    if not config_list_id:
        return [], {}
    config_lists = sections.get("XCConfigurationList", {})
    config_list = config_lists.get(config_list_id, {})
    config_ids_str = config_list.get("_list_buildConfigurations", "")
    config_ids = [cid for cid in config_ids_str.split(",") if cid]

    build_configs = sections.get("XCBuildConfiguration", {})
    names: list[str] = []
    settings_map: dict[str, dict[str, str]] = {}
    for cid in config_ids:
        config = build_configs.get(cid, {})
        name = config.get("name", "")
        if name:
            names.append(name)
            settings_map[name] = config
    return names, settings_map


def parse_pbxproj(content: str) -> tuple[list[XcodeTarget], list[str], list[str]]:
    """Parse a project.pbxproj and return (targets, project_build_configs, errors)."""
    errors: list[str] = []
    try:
        sections = _parse_pbxproj_sections(content)
    except Exception as exc:
        return [], [], [f"Failed to parse pbxproj: {exc}"]

    native_targets = sections.get("PBXNativeTarget", {})
    if not native_targets:
        errors.append("No PBXNativeTarget section found")
        return [], [], errors

    # Get project-level build configurations
    project_section = sections.get("PBXProject", {})
    project_config_list_id = None
    for _pid, pfields in project_section.items():
        project_config_list_id = pfields.get("buildConfigurationList")
        break
    project_configs, _ = _resolve_build_configs(sections, project_config_list_id)

    targets: list[XcodeTarget] = []
    for _tid, tfields in native_targets.items():
        name = tfields.get("name", tfields.get("productName", "unknown"))
        product_type = tfields.get("productType", "").strip('"')

        is_test = "test" in product_type.lower()
        is_app = "application" in product_type.lower() and not is_test

        # Resolve target build configurations
        config_list_id = tfields.get("buildConfigurationList")
        config_names, config_settings = _resolve_build_configs(sections, config_list_id)

        # Extract signing and identity from build settings
        bundle_id = None
        signing_style = None
        team_id = None
        entitlements = None
        info_plist = None
        for _cname, csettings in config_settings.items():
            if not bundle_id:
                bundle_id = csettings.get("PRODUCT_BUNDLE_IDENTIFIER")
            if not signing_style:
                style = csettings.get("CODE_SIGN_STYLE")
                if style:
                    signing_style = style.strip('"').lower()
            if not team_id:
                tid = csettings.get("DEVELOPMENT_TEAM")
                if tid:
                    team_id = tid.strip('"')
            if not entitlements:
                ent = csettings.get("CODE_SIGN_ENTITLEMENTS")
                if ent:
                    entitlements = ent.strip('"')
            if not info_plist:
                ip = csettings.get("INFOPLIST_FILE")
                if ip:
                    info_plist = ip.strip('"')

        target = XcodeTarget(
            name=name,
            product_type=product_type,
            bundle_identifier=bundle_id,
            product_name=tfields.get("productName"),
            build_configurations=config_names,
            signing_style=signing_style,
            team_id=team_id,
            entitlements_file=entitlements,
            info_plist_file=info_plist,
            is_test_target=is_test,
            is_app_target=is_app,
        )
        targets.append(target)

    return targets, project_configs, errors


# ── Scheme parser ───────────────────────────────────────────────────


def parse_xcscheme(content: str, scheme_name: str) -> XcodeScheme | None:
    """Parse an .xcscheme XML file and extract build/test/launch targets."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None

    build_targets: list[str] = []
    test_targets: list[str] = []
    launch_target: str | None = None

    # Build action
    for entry in root.iter("BuildActionEntry"):
        for ref in entry.iter("BuildableReference"):
            name = ref.get("BlueprintName", "")
            if name:
                build_targets.append(name)

    # Test action
    for entry in root.iter("TestableReference"):
        for ref in entry.iter("BuildableReference"):
            name = ref.get("BlueprintName", "")
            if name:
                test_targets.append(name)

    # Launch action
    for ref in root.iter("BuildableProductRunnable"):
        for bref in ref.iter("BuildableReference"):
            name = bref.get("BlueprintName", "")
            if name:
                launch_target = name
                break

    return XcodeScheme(
        name=scheme_name,
        build_targets=build_targets,
        test_targets=test_targets,
        launch_target=launch_target,
    )


# ── Info.plist reader ───────────────────────────────────────────────


def read_info_plist(path: Path) -> dict[str, Any]:
    """Read an Info.plist file (binary or XML) and return its contents."""
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except Exception:
        return {}


def extract_plist_summary(plist: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from an Info.plist."""
    keys_of_interest = [
        "CFBundleIdentifier",
        "CFBundleName",
        "CFBundleDisplayName",
        "CFBundleShortVersionString",
        "CFBundleVersion",
        "MinimumOSVersion",
        "LSMinimumSystemVersion",
        "UIDeviceFamily",
        "UILaunchStoryboardName",
        "UIMainStoryboardFile",
        "CFBundleExecutable",
        "NSAppTransportSecurity",
        "ITSAppUsesNonExemptEncryption",
    ]
    summary: dict[str, Any] = {}
    for key in keys_of_interest:
        if key in plist:
            summary[key] = plist[key]
    return summary


# ── Entitlements reader ─────────────────────────────────────────────


def read_entitlements(path: Path) -> dict[str, Any]:
    """Read a .entitlements plist file."""
    return read_info_plist(path)  # same format


def summarize_entitlements(entitlements: dict[str, Any]) -> list[str]:
    """Return human-readable capability names from entitlements keys."""
    capability_map = {
        "com.apple.developer.applesignin": "Sign in with Apple",
        "com.apple.developer.associated-domains": "Associated Domains",
        "com.apple.developer.healthkit": "HealthKit",
        "com.apple.developer.icloud-container-identifiers": "iCloud",
        "com.apple.developer.in-app-payments": "Apple Pay",
        "com.apple.developer.nfc.readersession.formats": "NFC",
        "com.apple.developer.push-to-talk": "Push to Talk",
        "aps-environment": "Push Notifications",
        "com.apple.developer.maps": "Maps",
        "com.apple.developer.networking.vpn.api": "VPN",
        "com.apple.developer.siri": "Siri",
        "com.apple.external-accessory.wireless-configuration": "Wireless Accessory",
        "com.apple.security.app-sandbox": "App Sandbox",
        "com.apple.developer.kernel.increased-memory-limit": "Increased Memory Limit",
        "keychain-access-groups": "Keychain Sharing",
    }
    capabilities: list[str] = []
    for key in entitlements:
        label = capability_map.get(key)
        if label:
            capabilities.append(label)
        elif key.startswith("com.apple.developer.") or key.startswith("com.apple.security."):
            capabilities.append(key.split(".")[-1].replace("-", " ").title())
    return capabilities


# ── High-level project inspector ────────────────────────────────────


# Subdirectory names commonly used to nest the Apple project inside a
# cross-platform repo (React Native, Flutter, Capacitor, monorepos).
_APPLE_NESTED_DIRS = {"ios", "macos", "apple", "app", "native", "xcode"}


def _find_xcodeproj(project_path: Path) -> Path | None:
    """Find the first .xcodeproj directory at the root or one level deep."""
    # 1. Direct children
    try:
        for entry in sorted(project_path.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and entry.suffix == ".xcodeproj":
                return entry
    except OSError:
        pass
    # 2. One level of known nested directories
    try:
        for sub in sorted(project_path.iterdir(), key=lambda p: p.name.lower()):
            if not sub.is_dir() or sub.name.lower() not in _APPLE_NESTED_DIRS:
                continue
            for entry in sorted(sub.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir() and entry.suffix == ".xcodeproj":
                    return entry
    except OSError:
        pass
    return None


def _find_xcworkspace(project_path: Path) -> Path | None:
    """Find the first .xcworkspace directory at the root or one level deep."""
    def _scan_dir(directory: Path) -> Path | None:
        try:
            for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir() and entry.suffix == ".xcworkspace":
                    # Skip workspaces embedded inside .xcodeproj
                    if entry.parent.suffix == ".xcodeproj":
                        continue
                    return entry
        except OSError:
            pass
        return None

    # 1. Direct children
    result = _scan_dir(project_path)
    if result:
        return result
    # 2. One level of known nested directories
    try:
        for sub in sorted(project_path.iterdir(), key=lambda p: p.name.lower()):
            if not sub.is_dir() or sub.name.lower() not in _APPLE_NESTED_DIRS:
                continue
            result = _scan_dir(sub)
            if result:
                return result
    except OSError:
        pass
    return None


def _discover_schemes(project_path: Path) -> list[XcodeScheme]:
    """Discover schemes from xcshareddata and xcuserdata."""
    schemes: list[XcodeScheme] = []
    scheme_dirs: list[Path] = []

    # Look in .xcodeproj/xcshareddata/xcschemes/
    xcodeproj = _find_xcodeproj(project_path)
    if xcodeproj:
        shared = xcodeproj / "xcshareddata" / "xcschemes"
        if shared.is_dir():
            scheme_dirs.append(shared)
        # Also look in xcuserdata
        userdata = xcodeproj / "xcuserdata"
        if userdata.is_dir():
            try:
                for user_dir in userdata.iterdir():
                    user_schemes = user_dir / "xcschemes"
                    if user_schemes.is_dir():
                        scheme_dirs.append(user_schemes)
            except OSError:
                pass

    # Look in .xcworkspace/xcshareddata/xcschemes/
    xcworkspace = _find_xcworkspace(project_path)
    if xcworkspace:
        shared = xcworkspace / "xcshareddata" / "xcschemes"
        if shared.is_dir():
            scheme_dirs.append(shared)

    seen_names: set[str] = set()
    for scheme_dir in scheme_dirs:
        try:
            for scheme_file in sorted(scheme_dir.glob("*.xcscheme")):
                name = scheme_file.stem
                if name in seen_names:
                    continue
                seen_names.add(name)
                try:
                    content = scheme_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                scheme = parse_xcscheme(content, name)
                if scheme:
                    schemes.append(scheme)
        except OSError:
            pass

    return schemes


def _find_files_by_pattern(project_path: Path, patterns: list[str], max_depth: int = 3) -> list[Path]:
    """Find files matching any of the given glob patterns, up to max_depth."""
    results: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        try:
            for match in project_path.rglob(pattern):
                if not match.is_file():
                    continue
                key = str(match)
                if key in seen:
                    continue
                # Enforce depth limit
                try:
                    rel = match.relative_to(project_path)
                except ValueError:
                    continue
                if len(rel.parts) > max_depth + 1:
                    continue
                seen.add(key)
                results.append(match)
        except OSError:
            pass
    return sorted(results)


def inspect_xcode_project(project_path: str | Path) -> XcodeProjectInfo:
    """Inspect an Apple project directory and return structured intelligence.

    This is the main entry point. Safe to call on any directory — returns
    an empty-ish result if no Xcode artifacts are found.
    """
    project_path = Path(project_path)
    info = XcodeProjectInfo(project_path=str(project_path))

    # Check for Swift Package Manager
    if (project_path / "Package.swift").exists():
        info.has_swift_package = True

    # Find .xcodeproj
    xcodeproj = _find_xcodeproj(project_path)
    if xcodeproj:
        info.xcodeproj_path = str(xcodeproj.relative_to(project_path))
        pbxproj = xcodeproj / "project.pbxproj"
        if pbxproj.exists():
            try:
                content = pbxproj.read_text(encoding="utf-8", errors="replace")
                targets, configs, errors = parse_pbxproj(content)
                info.targets = targets
                info.build_configurations = configs
                info.errors.extend(errors)
            except OSError as exc:
                info.errors.append(f"Could not read pbxproj: {exc}")

    # Find .xcworkspace
    xcworkspace = _find_xcworkspace(project_path)
    if xcworkspace:
        info.xcworkspace_path = str(xcworkspace.relative_to(project_path))

    # Discover schemes
    info.schemes = _discover_schemes(project_path)

    # Find Info.plist files
    plists = _find_files_by_pattern(project_path, ["Info.plist", "*/Info.plist", "*/*/Info.plist"])
    for plist_path in plists:
        try:
            rel = str(plist_path.relative_to(project_path))
        except ValueError:
            rel = str(plist_path)
        # Skip plists inside build/derived directories
        if any(part in {"DerivedData", "build", ".build", "Pods"} for part in plist_path.parts):
            continue
        info.info_plists.append(rel)

    # Find entitlements files
    ent_files = _find_files_by_pattern(project_path, ["*.entitlements"])
    for ent_path in ent_files:
        try:
            rel = str(ent_path.relative_to(project_path))
        except ValueError:
            rel = str(ent_path)
        info.entitlements_files.append(rel)

    # Mark what was inferred
    if info.targets and not xcodeproj:
        info.inferred.append("targets from heuristics")
    if info.schemes and not any(s.build_targets for s in info.schemes):
        info.inferred.append("scheme names only, no target references")

    return info
