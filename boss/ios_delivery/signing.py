"""Secure iOS signing and authentication credential management.

Loads credential *references* (paths, key IDs, issuer IDs) from a local
config file at ``~/.boss/ios-signing.json``.  Never stores or logs the
actual private key contents — only reports availability:

    available / missing / unreadable / invalid

The config file structure::

    {
        "api_key": {
            "key_id": "ABC123XYZ",
            "issuer_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            "key_path": "~/.boss/keys/AuthKey_ABC123XYZ.p8"
        },
        "team_id": "ABCD1234EF",
        "fastlane": {
            "match_git_url": "git@github.com:org/certs.git",
            "match_type": "appstore",
            "match_readonly": true
        },
        "keychain": {
            "name": "login",
            "allow_create": false
        }
    }

All fields are optional.  Boss reports what is present and usable so
the user knows what still needs setup.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from boss.config import settings

logger = logging.getLogger(__name__)


# ── Status enum ─────────────────────────────────────────────────────


class CredentialStatus(StrEnum):
    """Status of a single credential component."""

    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    INVALID = "invalid"
    INSECURE_PERMISSIONS = "insecure_permissions"
    NOT_CONFIGURED = "not_configured"


# ── Config data models ──────────────────────────────────────────────


@dataclass(frozen=True)
class APIKeyConfig:
    """App Store Connect API key reference.

    ``key_path`` should point to the .p8 file on disk.  Boss never reads
    the key contents — it only checks that the file exists, is readable,
    and looks structurally valid (begins with the expected PEM header).
    """

    key_id: str
    issuer_id: str
    key_path: str  # path to .p8 file

    def to_dict(self) -> dict[str, Any]:
        """Serialize without leaking the key path in full."""
        return {
            "key_id": self.key_id,
            "issuer_id": _redact_uuid(self.issuer_id),
            "key_path_configured": bool(self.key_path),
        }


@dataclass(frozen=True)
class FastlaneConfig:
    """Optional fastlane match / pilot configuration reference."""

    match_git_url: str | None = None
    match_type: str = "appstore"  # appstore, adhoc, development, enterprise
    match_readonly: bool = True
    api_key_path: str | None = None  # optional separate API key JSON for fastlane

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_git_url_configured": self.match_git_url is not None,
            "match_type": self.match_type,
            "match_readonly": self.match_readonly,
            "api_key_path_configured": self.api_key_path is not None,
        }


@dataclass(frozen=True)
class KeychainConfig:
    """Keychain assumptions for local signing."""

    name: str = "login"
    allow_create: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "allow_create": self.allow_create,
        }


@dataclass(frozen=True)
class SigningConfig:
    """Complete signing configuration snapshot."""

    api_key: APIKeyConfig | None = None
    team_id: str | None = None
    fastlane: FastlaneConfig | None = None
    keychain: KeychainConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "team_id_configured": self.team_id is not None,
        }
        if self.api_key:
            d["api_key"] = self.api_key.to_dict()
        else:
            d["api_key"] = None
        if self.fastlane:
            d["fastlane"] = self.fastlane.to_dict()
        else:
            d["fastlane"] = None
        if self.keychain:
            d["keychain"] = self.keychain.to_dict()
        else:
            d["keychain"] = None
        return d


# ── Config loading ──────────────────────────────────────────────────


def _signing_config_path() -> Path:
    """Return the path to the iOS signing config file."""
    return settings.app_data_dir / "ios-signing.json"


class ConfigFileCorrupt(Exception):
    """Raised when ios-signing.json exists but cannot be parsed."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def load_signing_config() -> SigningConfig | None:
    """Load the signing config from disk.

    Returns ``None`` if the file doesn't exist.  Raises
    :class:`ConfigFileCorrupt` if the file exists but is malformed, so
    callers can distinguish "no config" from "broken config."
    """
    path = _signing_config_path()
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load iOS signing config at %s: %s", path, type(exc).__name__)
        raise ConfigFileCorrupt(f"{type(exc).__name__}") from exc

    if not isinstance(data, dict):
        logger.warning("iOS signing config at %s is not a JSON object", path)
        raise ConfigFileCorrupt("Config is not a JSON object")

    return _parse_config(data)


def _parse_config(data: dict[str, Any]) -> SigningConfig:
    """Parse the raw JSON dict into a typed ``SigningConfig``."""
    api_key: APIKeyConfig | None = None
    ak = data.get("api_key")
    if isinstance(ak, dict):
        key_id = ak.get("key_id", "").strip()
        issuer_id = ak.get("issuer_id", "").strip()
        key_path = ak.get("key_path", "").strip()
        if key_id and issuer_id and key_path:
            # Expand ~ and env vars in key_path
            key_path = str(Path(os.path.expanduser(os.path.expandvars(key_path))))
            api_key = APIKeyConfig(
                key_id=key_id,
                issuer_id=issuer_id,
                key_path=key_path,
            )

    team_id: str | None = None
    raw_team = data.get("team_id")
    if isinstance(raw_team, str) and raw_team.strip():
        team_id = raw_team.strip()

    fastlane: FastlaneConfig | None = None
    fl = data.get("fastlane")
    if isinstance(fl, dict):
        fl_api_key_path = fl.get("api_key_path")
        if isinstance(fl_api_key_path, str) and fl_api_key_path.strip():
            fl_api_key_path = str(Path(os.path.expanduser(os.path.expandvars(fl_api_key_path))))
        else:
            fl_api_key_path = None
        match_url = fl.get("match_git_url")
        fastlane = FastlaneConfig(
            match_git_url=match_url if isinstance(match_url, str) and match_url.strip() else None,
            match_type=fl.get("match_type", "appstore"),
            match_readonly=fl.get("match_readonly", True),
            api_key_path=fl_api_key_path,
        )

    keychain: KeychainConfig | None = None
    kc = data.get("keychain")
    if isinstance(kc, dict):
        keychain = KeychainConfig(
            name=kc.get("name", "login"),
            allow_create=bool(kc.get("allow_create", False)),
        )

    return SigningConfig(
        api_key=api_key,
        team_id=team_id,
        fastlane=fastlane,
        keychain=keychain,
    )


# ── Credential diagnostics ─────────────────────────────────────────


@dataclass
class CredentialCheck:
    """Result of probing a single credential component."""

    name: str
    status: str  # CredentialStatus value
    detail: str  # human-readable explanation (never secrets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class SigningReadiness:
    """Aggregate readiness report for iOS signing credentials."""

    config_file_exists: bool
    config_file_corrupt: bool = False
    config_corrupt_reason: str = ""
    checks: list[CredentialCheck] = field(default_factory=list)

    @property
    def can_upload(self) -> bool:
        """True if enough credentials are available for App Store upload."""
        api_key_ok = any(
            c.name == "api_key" and c.status == CredentialStatus.AVAILABLE
            for c in self.checks
        )
        return api_key_ok

    @property
    def can_sign(self) -> bool:
        """True if code-signing prerequisites appear present."""
        team_ok = any(
            c.name == "team_id" and c.status == CredentialStatus.AVAILABLE
            for c in self.checks
        )
        return team_ok

    def to_dict(self) -> dict[str, Any]:
        d = {
            "config_file_exists": self.config_file_exists,
            "config_file_corrupt": self.config_file_corrupt,
            "can_upload": self.can_upload,
            "can_sign": self.can_sign,
            "checks": [c.to_dict() for c in self.checks],
        }
        if self.config_file_corrupt:
            d["config_corrupt_reason"] = self.config_corrupt_reason
        return d


def check_signing_readiness(config: SigningConfig | None = None) -> SigningReadiness:
    """Probe all signing credentials and return a structured report.

    Never reads private key contents.  Only checks existence, file
    permissions, and structural indicators (PEM header presence).
    """
    config_exists = _signing_config_path().exists()
    config_corrupt = False
    corrupt_reason = ""

    if config is None:
        try:
            config = load_signing_config()
        except ConfigFileCorrupt as exc:
            config_corrupt = True
            corrupt_reason = exc.reason

    checks: list[CredentialCheck] = []

    if config_corrupt:
        # File exists but is malformed — report INVALID for every check
        # so the user sees the real problem instead of "not configured."
        for name in ("api_key", "team_id", "fastlane", "keychain"):
            checks.append(CredentialCheck(
                name=name,
                status=CredentialStatus.INVALID,
                detail=f"Config file is malformed: {corrupt_reason}",
            ))
    else:
        # ── API Key ──
        checks.append(_check_api_key(config))

        # ── Team ID ──
        checks.append(_check_team_id(config))

        # ── Fastlane ──
        checks.append(_check_fastlane(config))

        # ── Keychain ──
        checks.append(_check_keychain(config))

    return SigningReadiness(
        config_file_exists=config_exists,
        config_file_corrupt=config_corrupt,
        config_corrupt_reason=corrupt_reason,
        checks=checks,
    )


def _check_api_key(config: SigningConfig | None) -> CredentialCheck:
    """Check App Store Connect API key configuration."""
    if config is None or config.api_key is None:
        return CredentialCheck(
            name="api_key",
            status=CredentialStatus.NOT_CONFIGURED,
            detail="No api_key section in ios-signing.json",
        )

    ak = config.api_key

    if not ak.key_id or not ak.issuer_id:
        return CredentialCheck(
            name="api_key",
            status=CredentialStatus.INVALID,
            detail="key_id or issuer_id is empty",
        )

    key_path = Path(ak.key_path)
    if not key_path.exists():
        return CredentialCheck(
            name="api_key",
            status=CredentialStatus.MISSING,
            detail=f"Key file not found: {_safe_path(key_path)}",
        )

    # Check readability without reading the full contents
    if not os.access(key_path, os.R_OK):
        return CredentialCheck(
            name="api_key",
            status=CredentialStatus.UNREADABLE,
            detail=f"Key file not readable: {_safe_path(key_path)}",
        )

    # Minimal structural check: read only the first line to confirm PEM header
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            first_line = f.readline(128).strip()
        if "PRIVATE KEY" not in first_line:
            return CredentialCheck(
                name="api_key",
                status=CredentialStatus.INVALID,
                detail="Key file does not appear to be a valid .p8 (PEM) file",
            )
    except OSError:
        return CredentialCheck(
            name="api_key",
            status=CredentialStatus.UNREADABLE,
            detail=f"Could not read key file header: {_safe_path(key_path)}",
        )

    # Reject overly permissive file permissions
    if _check_key_file_permissions(key_path):
        return CredentialCheck(
            name="api_key",
            status=CredentialStatus.INSECURE_PERMISSIONS,
            detail=(
                f"Key file {_safe_path(key_path)} is world-readable. "
                f"Run: chmod 600 {key_path}"
            ),
        )

    return CredentialCheck(
        name="api_key",
        status=CredentialStatus.AVAILABLE,
        detail=f"API key {ak.key_id} configured with valid .p8 file",
    )


def _check_team_id(config: SigningConfig | None) -> CredentialCheck:
    """Check team_id configuration."""
    if config is None or not config.team_id:
        return CredentialCheck(
            name="team_id",
            status=CredentialStatus.NOT_CONFIGURED,
            detail="No team_id in ios-signing.json",
        )

    # Basic format check: Apple team IDs are 10 alphanumeric characters
    tid = config.team_id
    if not (len(tid) == 10 and tid.isalnum()):
        return CredentialCheck(
            name="team_id",
            status=CredentialStatus.INVALID,
            detail="team_id does not look like a valid Apple Team ID (expect 10 alphanumeric chars)",
        )

    return CredentialCheck(
        name="team_id",
        status=CredentialStatus.AVAILABLE,
        detail=f"Team ID {tid} configured",
    )


def _check_fastlane(config: SigningConfig | None) -> CredentialCheck:
    """Check fastlane match/pilot configuration."""
    if config is None or config.fastlane is None:
        return CredentialCheck(
            name="fastlane",
            status=CredentialStatus.NOT_CONFIGURED,
            detail="No fastlane section in ios-signing.json",
        )

    fl = config.fastlane
    issues: list[str] = []

    # Check match git URL
    if fl.match_git_url:
        if not fl.match_readonly:
            issues.append("match_readonly is false — Boss will not write to the match repo")

    # Check fastlane API key path if provided
    if fl.api_key_path:
        api_path = Path(fl.api_key_path)
        if not api_path.exists():
            issues.append(f"fastlane api_key_path not found: {_safe_path(api_path)}")
        elif not os.access(api_path, os.R_OK):
            issues.append(f"fastlane api_key_path not readable: {_safe_path(api_path)}")

    if issues:
        return CredentialCheck(
            name="fastlane",
            status=CredentialStatus.INVALID,
            detail="; ".join(issues),
        )

    parts: list[str] = []
    if fl.match_git_url:
        parts.append(f"match={fl.match_type}")
    if fl.api_key_path:
        parts.append("api_key_json=yes")
    detail = ", ".join(parts) if parts else "configured (minimal)"

    return CredentialCheck(
        name="fastlane",
        status=CredentialStatus.AVAILABLE,
        detail=f"fastlane: {detail}",
    )


def _check_keychain(config: SigningConfig | None) -> CredentialCheck:
    """Check keychain configuration assumptions."""
    if config is None or config.keychain is None:
        return CredentialCheck(
            name="keychain",
            status=CredentialStatus.NOT_CONFIGURED,
            detail="No keychain section — will use default login keychain",
        )

    kc = config.keychain
    return CredentialCheck(
        name="keychain",
        status=CredentialStatus.AVAILABLE,
        detail=f"Keychain '{kc.name}', allow_create={kc.allow_create}",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _redact_uuid(value: str) -> str:
    """Partially redact a UUID-like string for safe display."""
    if len(value) >= 12:
        return value[:4] + "…" + value[-4:]
    return "***"


def _safe_path(path: Path) -> str:
    """Return a display-safe version of a path (collapse home dir)."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _check_key_file_permissions(path: Path) -> bool:
    """Return True if the key file has insecure (world-readable) permissions.

    Does not change file permissions — only checks.  The user should
    ``chmod 600`` their .p8 file.
    """
    try:
        mode = path.stat().st_mode
        if mode & stat.S_IROTH:
            logger.warning(
                "iOS API key file %s is world-readable (mode %o). "
                "Run: chmod 600 %s",
                _safe_path(path),
                mode & 0o777,
                path,
            )
            return True
    except OSError:
        pass
    return False


def signing_summary(readiness: SigningReadiness) -> str:
    """One-line human-readable signing status for diagnostics UI."""
    avail = [c.name for c in readiness.checks if c.status == CredentialStatus.AVAILABLE]
    missing = [c.name for c in readiness.checks if c.status != CredentialStatus.AVAILABLE
               and c.status != CredentialStatus.NOT_CONFIGURED]
    not_cfg = [c.name for c in readiness.checks if c.status == CredentialStatus.NOT_CONFIGURED]

    parts: list[str] = []
    if avail:
        parts.append(f"ready: {', '.join(avail)}")
    if missing:
        parts.append(f"issues: {', '.join(missing)}")
    if not_cfg:
        parts.append(f"not configured: {', '.join(not_cfg)}")

    if not parts:
        return "No signing configuration found"
    return " | ".join(parts)
