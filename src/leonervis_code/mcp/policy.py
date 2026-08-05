"""Exact fingerprint-bound local permission policy for MCP tool contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import RLock

from leonervis_code.core.permissions import PermissionAction

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MCP_TOOL_POLICY_SCHEMA_VERSION = 1
MAX_MCP_TOOL_POLICY_BYTES = 1024 * 1024
MAX_MCP_TOOL_POLICIES_PER_SCOPE = 512
_QUALIFIED_NAME = re.compile(r"mcp_[a-z0-9_]{1,60}\Z")
_SERVER_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_SCHEMA_FINGERPRINT = re.compile(r"mcp-schema-v1-[0-9a-f]{64}\Z")
_PROTOCOL_VERSION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_POLICY_ID_DOMAIN = b"leonervis-code-mcp-tool-policy-set-v1\0"
_ALLOWED_ACTIONS = frozenset({PermissionAction.WORKSPACE_READ, PermissionAction.DANGEROUS})


class McpToolPolicyError(ValueError):
    """One local MCP policy file or mutation is invalid, unsafe, or stale."""


class McpPolicyDisposition(StrEnum):
    DEFAULT = "default"
    APPLIED = "applied"
    STALE = "stale"


@dataclass(frozen=True)
class McpToolPolicyRule:
    qualified_name: str
    configured_name: str
    server_scope: str
    configuration_revision: int
    remote_name: str
    protocol_version: str
    schema_fingerprint: str
    action: PermissionAction
    revision: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.qualified_name, str)
            or _QUALIFIED_NAME.fullmatch(self.qualified_name) is None
        ):
            raise McpToolPolicyError("MCP policy qualified tool name is invalid")
        if (
            not isinstance(self.configured_name, str)
            or _SERVER_NAME.fullmatch(self.configured_name) is None
        ):
            raise McpToolPolicyError("MCP policy configured server name is invalid")
        if self.server_scope not in {"user", "project"}:
            raise McpToolPolicyError("MCP policy server scope is invalid")
        if type(self.configuration_revision) is not int or self.configuration_revision < 1:
            raise McpToolPolicyError("MCP policy server revision must be positive")
        if not _bounded_text(self.remote_name, 256):
            raise McpToolPolicyError("MCP policy remote tool name is invalid")
        if (
            not isinstance(self.protocol_version, str)
            or _PROTOCOL_VERSION.fullmatch(self.protocol_version) is None
        ):
            raise McpToolPolicyError("MCP policy protocol version is invalid")
        if (
            not isinstance(self.schema_fingerprint, str)
            or _SCHEMA_FINGERPRINT.fullmatch(self.schema_fingerprint) is None
        ):
            raise McpToolPolicyError("MCP policy schema fingerprint is invalid")
        if type(self.action) is not PermissionAction or self.action not in _ALLOWED_ACTIONS:
            raise McpToolPolicyError("MCP policy action must be workspace-read or dangerous")
        if type(self.revision) is not int or self.revision < 1:
            raise McpToolPolicyError("MCP policy revision must be positive")

    def as_mapping(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "configuration_revision": self.configuration_revision,
            "configured_name": self.configured_name,
            "protocol_version": self.protocol_version,
            "qualified_name": self.qualified_name,
            "remote_name": self.remote_name,
            "revision": self.revision,
            "schema_fingerprint": self.schema_fingerprint,
            "server_scope": self.server_scope,
        }

    @classmethod
    def from_mapping(cls, value: object) -> McpToolPolicyRule:
        if not isinstance(value, dict):
            raise McpToolPolicyError("MCP policy rule must be a JSON object")
        fields = {
            "action",
            "configuration_revision",
            "configured_name",
            "protocol_version",
            "qualified_name",
            "remote_name",
            "revision",
            "schema_fingerprint",
            "server_scope",
        }
        if set(value) != fields:
            raise McpToolPolicyError("MCP policy rule fields are invalid")
        try:
            action = PermissionAction(value["action"])
        except (TypeError, ValueError):
            raise McpToolPolicyError("MCP policy action is invalid") from None
        return cls(
            qualified_name=value["qualified_name"],
            configured_name=value["configured_name"],
            server_scope=value["server_scope"],
            configuration_revision=value["configuration_revision"],
            remote_name=value["remote_name"],
            protocol_version=value["protocol_version"],
            schema_fingerprint=value["schema_fingerprint"],
            action=action,
            revision=value["revision"],
        )

    def matches(
        self,
        *,
        configured_name: str,
        server_scope: str,
        configuration_revision: int,
        remote_name: str,
        protocol_version: str,
        schema_fingerprint: str,
    ) -> bool:
        return (
            self.configured_name == configured_name
            and self.server_scope == server_scope
            and self.configuration_revision == configuration_revision
            and self.remote_name == remote_name
            and self.protocol_version == protocol_version
            and self.schema_fingerprint == schema_fingerprint
        )


def default_user_mcp_policy_path(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    config_home = env.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "leonervis-code" / "mcp-tool-policies.json"


def default_project_mcp_policy_path(workspace: Path) -> Path:
    return Path(workspace) / ".leonervis-code" / "mcp-tool-policies.json"


class McpToolPolicyStore:
    """Two-scope, symlink-safe, revisioned MCP policy storage."""

    def __init__(self, user_path: Path, project_path: Path) -> None:
        self.user_path = Path(user_path)
        self.project_path = Path(project_path)
        self._lock = RLock()

    @classmethod
    def for_workspace(
        cls,
        workspace: Path,
        *,
        environment: Mapping[str, str] | None = None,
        user_path: Path | None = None,
        project_path: Path | None = None,
    ) -> McpToolPolicyStore:
        return cls(
            user_path or default_user_mcp_policy_path(environment),
            project_path or default_project_mcp_policy_path(workspace),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            streams = []
            try:
                for path in sorted(
                    {
                        self.user_path.parent / ".mcp-tool-policies.lock",
                        self.project_path.parent / ".mcp-tool-policies.lock",
                    },
                    key=str,
                ):
                    stream = _open_lock(path)
                    _lock_stream(stream)
                    streams.append(stream)
                yield
            finally:
                for stream in reversed(streams):
                    try:
                        _unlock_stream(stream)
                    finally:
                        stream.close()

    def list_rules(self) -> tuple[tuple[str, McpToolPolicyRule], ...]:
        user = self._load(self.user_path, "user")
        project = self._load(self.project_path, "project")
        collisions = set(user) & set(project)
        if collisions:
            raise McpToolPolicyError("MCP policy exists in both scopes: " + sorted(collisions)[0])
        rules = [
            *(("user", rule) for rule in user.values()),
            *(("project", rule) for rule in project.values()),
        ]
        return tuple(sorted(rules, key=lambda item: item[1].qualified_name))

    def get_rule(
        self, qualified_name: str, *, policy_scope: str | None = None
    ) -> tuple[str, McpToolPolicyRule]:
        _validate_qualified_name(qualified_name)
        _validate_policy_scope(policy_scope)
        matches = [
            item
            for item in self.list_rules()
            if item[1].qualified_name == qualified_name
            and (policy_scope is None or item[0] == policy_scope)
        ]
        if not matches:
            raise McpToolPolicyError("MCP policy does not exist: " + qualified_name)
        return matches[0]

    @property
    def policy_id(self) -> str:
        manifest = [
            {"policy_scope": scope, "rule": rule.as_mapping()} for scope, rule in self.list_rules()
        ]
        digest = hashlib.sha256(
            _POLICY_ID_DOMAIN + _canonical_json(manifest).encode("utf-8")
        ).hexdigest()
        return f"mcp-policy-set-v1-{digest}"

    def resolve(
        self,
        *,
        qualified_name: str,
        configured_name: str,
        server_scope: str,
        configuration_revision: int,
        remote_name: str,
        protocol_version: str,
        schema_fingerprint: str,
    ) -> tuple[McpPolicyDisposition, PermissionAction, int | None]:
        try:
            _, rule = self.get_rule(qualified_name)
        except McpToolPolicyError as error:
            if str(error).startswith("MCP policy does not exist:"):
                return McpPolicyDisposition.DEFAULT, PermissionAction.DANGEROUS, None
            raise
        if rule.matches(
            configured_name=configured_name,
            server_scope=server_scope,
            configuration_revision=configuration_revision,
            remote_name=remote_name,
            protocol_version=protocol_version,
            schema_fingerprint=schema_fingerprint,
        ):
            return McpPolicyDisposition.APPLIED, rule.action, rule.revision
        return McpPolicyDisposition.STALE, PermissionAction.DANGEROUS, rule.revision

    def set_rule(
        self,
        rule: McpToolPolicyRule,
        *,
        policy_scope: str,
        replace_existing: bool = False,
        expected_revision: int | None = None,
    ) -> McpToolPolicyRule:
        if not isinstance(rule, McpToolPolicyRule):
            raise McpToolPolicyError("MCP policy rule is invalid")
        _validate_policy_scope(policy_scope, required=True)
        with self.transaction():
            target_path = self._path(policy_scope)
            other_scope = "project" if policy_scope == "user" else "user"
            target = self._load(target_path, policy_scope)
            other = self._load(self._path(other_scope), other_scope)
            if rule.qualified_name in other:
                raise McpToolPolicyError(
                    "MCP policy already exists in the other scope: " + rule.qualified_name
                )
            current = target.get(rule.qualified_name)
            if current is None:
                if expected_revision is not None:
                    raise McpToolPolicyError("MCP policy does not exist: " + rule.qualified_name)
                if len(target) >= MAX_MCP_TOOL_POLICIES_PER_SCOPE:
                    raise McpToolPolicyError("MCP policy limit reached")
                stored = replace(rule, revision=1)
            else:
                if not replace_existing:
                    raise McpToolPolicyError(
                        "MCP policy already exists; use --replace: " + rule.qualified_name
                    )
                _check_revision(current, expected_revision)
                if replace(current, revision=rule.revision) == rule:
                    return current
                stored = replace(rule, revision=current.revision + 1)
            self._write(target_path, {**target, stored.qualified_name: stored})
            return stored

    def clear_rule(
        self,
        qualified_name: str,
        *,
        policy_scope: str,
        expected_revision: int | None = None,
    ) -> None:
        _validate_qualified_name(qualified_name)
        _validate_policy_scope(policy_scope, required=True)
        with self.transaction():
            path = self._path(policy_scope)
            rules = self._load(path, policy_scope)
            try:
                current = rules[qualified_name]
            except KeyError:
                raise McpToolPolicyError("MCP policy does not exist: " + qualified_name) from None
            _check_revision(current, expected_revision)
            updated = dict(rules)
            del updated[qualified_name]
            self._write(path, updated)

    def _path(self, scope: str) -> Path:
        return self.user_path if scope == "user" else self.project_path

    @staticmethod
    def _load(path: Path, scope: str) -> dict[str, McpToolPolicyRule]:
        data = _read_json(path)
        if data is None:
            return {}
        if set(data) != {"policies", "schema_version"}:
            raise McpToolPolicyError(f"{scope} MCP policy file fields are invalid")
        if data["schema_version"] != MCP_TOOL_POLICY_SCHEMA_VERSION:
            raise McpToolPolicyError(f"unsupported {scope} MCP policy schema version")
        raw = data["policies"]
        if not isinstance(raw, dict) or len(raw) > MAX_MCP_TOOL_POLICIES_PER_SCOPE:
            raise McpToolPolicyError(f"{scope} MCP policies are invalid")
        result: dict[str, McpToolPolicyRule] = {}
        for name, value in raw.items():
            rule = McpToolPolicyRule.from_mapping(value)
            if name != rule.qualified_name:
                raise McpToolPolicyError(f"{scope} MCP policy key/name mismatch")
            result[name] = rule
        return result

    @staticmethod
    def _write(path: Path, rules: Mapping[str, McpToolPolicyRule]) -> None:
        _atomic_write(
            path,
            {
                "policies": {name: rule.as_mapping() for name, rule in sorted(rules.items())},
                "schema_version": MCP_TOOL_POLICY_SCHEMA_VERSION,
            },
        )


def _bounded_text(value: object, limit: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and len(value) <= limit
        and len(value.encode("utf-8")) <= limit * 4
    )


def _validate_policy_scope(scope: object, *, required: bool = False) -> None:
    if scope is None and not required:
        return
    if scope not in {"user", "project"}:
        raise McpToolPolicyError("MCP policy scope must be user or project")


def _validate_qualified_name(value: object) -> None:
    if not isinstance(value, str) or _QUALIFIED_NAME.fullmatch(value) is None:
        raise McpToolPolicyError("MCP policy qualified tool name is invalid")


def _check_revision(rule: McpToolPolicyRule, expected: int | None) -> None:
    if expected is None:
        return
    if type(expected) is not int or expected < 1:
        raise McpToolPolicyError("expected MCP policy revision must be positive")
    if rule.revision != expected:
        raise McpToolPolicyError(
            f"MCP policy revision conflict: expected {expected}, found {rule.revision}"
        )


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise McpToolPolicyError("MCP policy path must not be a symlink")
    if not path.exists():
        return None
    _ensure_safe_chain(path)
    _ensure_kind(path, file=True)
    try:
        if path.stat().st_size > MAX_MCP_TOOL_POLICY_BYTES:
            raise McpToolPolicyError("MCP policy file exceeds its size limit")
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=_closed_object, parse_constant=_reject)
    except McpToolPolicyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise McpToolPolicyError("MCP policy file is unreadable or invalid") from None
    if not isinstance(value, dict):
        raise McpToolPolicyError("MCP policy file must be a JSON object")
    return value


def _atomic_write(path: Path, value: dict[str, object]) -> None:
    parent = path.parent
    _ensure_safe_chain(parent)
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    except OSError:
        raise McpToolPolicyError("could not create MCP policy directory") from None
    _ensure_kind(parent, file=False)
    if path.exists() or path.is_symlink():
        _ensure_kind(path, file=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_MCP_TOOL_POLICY_BYTES:
        raise McpToolPolicyError("MCP policy file exceeds its size limit")
    descriptor: int | None = None
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(parent)
    except OSError:
        raise McpToolPolicyError("could not write MCP policy file") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _open_lock(path: Path):
    _ensure_safe_chain(path.parent)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    _ensure_kind(path.parent, file=False)
    if path.is_symlink():
        raise McpToolPolicyError("MCP policy lock must not be a symlink")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "a+b")


def _lock_stream(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except OSError:
        raise McpToolPolicyError("could not lock MCP policy storage") from None


def _unlock_stream(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _ensure_safe_chain(path: Path) -> None:
    current = Path(path.absolute().anchor)
    for part in path.absolute().parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            break
        if current.is_symlink():
            raise McpToolPolicyError("MCP policy path must not contain a symlink")


def _ensure_kind(path: Path, *, file: bool) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise McpToolPolicyError("MCP policy path is unavailable") from None
    valid = stat.S_ISREG(mode) if file else stat.S_ISDIR(mode)
    if not valid or stat.S_ISLNK(mode):
        raise McpToolPolicyError("MCP policy path has an unsafe file type")


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject(value: str) -> None:
    raise ValueError(value)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _fsync_directory(path: Path) -> None:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
