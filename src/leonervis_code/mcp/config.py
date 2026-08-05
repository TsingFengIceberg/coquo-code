"""Versioned, credential-free MCP server configuration storage."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import tempfile
from threading import RLock
from urllib.parse import urlsplit, urlunsplit

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MCP_CONFIGURATION_SCHEMA_VERSION = 2
SUPPORTED_MCP_CONFIGURATION_SCHEMA_VERSIONS = (1, 2)
MAX_MCP_CONFIGURATION_BYTES = 1024 * 1024
MAX_MCP_SERVERS_PER_SCOPE = 64
MAX_MCP_SERVER_NAME_CHARACTERS = 64
MAX_MCP_ARGUMENTS = 64
MAX_MCP_ARGUMENT_CHARACTERS = 1024
MAX_MCP_ARGUMENT_BYTES = 4096
MAX_MCP_ARGV_BYTES = 16 * 1024
MAX_MCP_CWD_CHARACTERS = 4096
MAX_MCP_ENVIRONMENT_BINDINGS = 32
_SERVER_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class McpConfigurationError(ValueError):
    """One MCP configuration is malformed, unsafe, stale, or unavailable."""


class McpTransport(StrEnum):
    """Closed MCP transport set implemented by the current client."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


class McpTrustMode(StrEnum):
    """Process authority granted to one configured MCP server."""

    CONFINED_STDIO = "confined-stdio"
    REMOTE_HTTPS = "remote-https"


@dataclass(frozen=True)
class McpServerConfiguration:
    """One revisioned local or remote MCP server definition without secret values."""

    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    endpoint: str | None = None
    bearer_token_env: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret_env: str | None = None
    oauth_scopes: tuple[str, ...] = ()
    expose_workspace_root: bool = False
    resource_subscriptions: tuple[str, ...] = ()
    enabled: bool = False
    transport: McpTransport = McpTransport.STDIO
    trust: McpTrustMode = McpTrustMode.CONFINED_STDIO
    revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _SERVER_NAME.fullmatch(self.name) is None:
            raise McpConfigurationError(
                "MCP server name must start with a lowercase letter and contain only "
                "lowercase ASCII letters, digits, dot, underscore, or hyphen"
            )
        if self.transport is McpTransport.STDIO:
            if self.trust is not McpTrustMode.CONFINED_STDIO:
                raise McpConfigurationError("stdio MCP servers require confined-stdio trust")
            _validate_command(self.command)
            _validate_args(self.args)
            _validate_cwd(self.cwd)
            _validate_environment(self.environment)
            if (
                any(
                    value is not None
                    for value in (
                        self.endpoint,
                        self.bearer_token_env,
                        self.oauth_client_id,
                        self.oauth_client_secret_env,
                    )
                )
                or self.oauth_scopes
            ):
                raise McpConfigurationError("stdio MCP servers cannot contain remote fields")
        elif self.transport is McpTransport.STREAMABLE_HTTP:
            if self.trust is not McpTrustMode.REMOTE_HTTPS:
                raise McpConfigurationError("remote MCP servers require remote-https trust")
            if self.command or self.args or self.cwd != "." or self.environment:
                raise McpConfigurationError("remote MCP servers cannot contain stdio fields")
            object.__setattr__(self, "endpoint", _canonical_remote_endpoint(self.endpoint))
            _validate_optional_environment_name(self.bearer_token_env, "bearer token")
            _validate_oauth(self)
        else:
            raise McpConfigurationError("MCP server transport is unsupported")
        if type(self.enabled) is not bool:
            raise McpConfigurationError("MCP server enabled flag must be boolean")
        if type(self.expose_workspace_root) is not bool:
            raise McpConfigurationError("MCP workspace-root exposure flag must be boolean")
        _validate_resource_subscriptions(self.resource_subscriptions)
        if type(self.transport) is not McpTransport:
            raise McpConfigurationError("MCP server transport is invalid")
        if type(self.trust) is not McpTrustMode:
            raise McpConfigurationError("MCP server trust mode is invalid")
        if type(self.revision) is not int or self.revision < 1:
            raise McpConfigurationError("MCP server revision must be positive")

    @classmethod
    def from_mapping(cls, value: object) -> McpServerConfiguration:
        if not isinstance(value, dict):
            raise McpConfigurationError("MCP server entry must be a JSON object")
        allowed = {
            "args",
            "command",
            "cwd",
            "enabled",
            "environment",
            "endpoint",
            "bearer_token_env",
            "oauth_client_id",
            "oauth_client_secret_env",
            "oauth_scopes",
            "expose_workspace_root",
            "resource_subscriptions",
            "name",
            "revision",
            "transport",
            "trust",
        }
        unknown = set(value) - allowed
        if unknown:
            raise McpConfigurationError(f"MCP server contains unknown field: {sorted(unknown)[0]}")
        legacy = {
            "args",
            "command",
            "cwd",
            "enabled",
            "environment",
            "name",
            "revision",
            "transport",
            "trust",
        }
        v2_without_root = allowed - {"expose_workspace_root", "resource_subscriptions"}
        if set(value) not in {
            frozenset(allowed),
            frozenset(v2_without_root),
            frozenset(legacy),
        }:
            missing = sorted(legacy - set(value))
            if not missing:
                missing = sorted(allowed - set(value))
            raise McpConfigurationError(f"MCP server is missing required field: {missing[0]}")
        raw_args = value["args"]
        raw_environment = value["environment"]
        raw_oauth_scopes = value.get("oauth_scopes", [])
        raw_subscriptions = value.get("resource_subscriptions", [])
        if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
            raise McpConfigurationError("MCP server args must be an array of strings")
        if not isinstance(raw_environment, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in raw_environment.items()
        ):
            raise McpConfigurationError(
                "MCP server environment must map target names to source environment names"
            )
        if not isinstance(raw_oauth_scopes, list) or not all(
            isinstance(item, str) for item in raw_oauth_scopes
        ):
            raise McpConfigurationError("MCP OAuth scopes must be an array of strings")
        if not isinstance(raw_subscriptions, list) or not all(
            isinstance(item, str) for item in raw_subscriptions
        ):
            raise McpConfigurationError("MCP resource subscriptions must be an array of strings")
        try:
            transport = McpTransport(value["transport"])
            trust = McpTrustMode(value["trust"])
        except (TypeError, ValueError):
            raise McpConfigurationError(
                "MCP server transport or trust mode is unsupported"
            ) from None
        return cls(
            name=value["name"],
            command=value["command"],
            args=tuple(raw_args),
            cwd=value["cwd"],
            environment=tuple(sorted(raw_environment.items())),
            endpoint=value.get("endpoint"),
            bearer_token_env=value.get("bearer_token_env"),
            oauth_client_id=value.get("oauth_client_id"),
            oauth_client_secret_env=value.get("oauth_client_secret_env"),
            oauth_scopes=tuple(raw_oauth_scopes),
            expose_workspace_root=value.get("expose_workspace_root", False),
            resource_subscriptions=tuple(raw_subscriptions),
            enabled=value["enabled"],
            transport=transport,
            trust=trust,
            revision=value["revision"],
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "args": list(self.args),
            "command": self.command,
            "cwd": self.cwd,
            "enabled": self.enabled,
            "environment": dict(self.environment),
            "endpoint": self.endpoint,
            "bearer_token_env": self.bearer_token_env,
            "oauth_client_id": self.oauth_client_id,
            "oauth_client_secret_env": self.oauth_client_secret_env,
            "oauth_scopes": list(self.oauth_scopes),
            "expose_workspace_root": self.expose_workspace_root,
            "resource_subscriptions": list(self.resource_subscriptions),
            "name": self.name,
            "revision": self.revision,
            "transport": self.transport.value,
            "trust": self.trust.value,
        }

    def same_definition(self, other: McpServerConfiguration) -> bool:
        if not isinstance(other, McpServerConfiguration):
            return False
        return replace(self, revision=1) == replace(other, revision=1)


@dataclass(frozen=True)
class McpServerEntry:
    """One configured server together with its owning configuration scope."""

    scope: str
    configuration: McpServerConfiguration

    def __post_init__(self) -> None:
        if self.scope not in {"user", "project"}:
            raise McpConfigurationError("MCP server scope must be user or project")
        if not isinstance(self.configuration, McpServerConfiguration):
            raise McpConfigurationError("MCP server entry configuration is invalid")


def default_user_mcp_path(environment: Mapping[str, str] | None = None) -> Path:
    """Return the XDG-aware user MCP registry path."""
    env = os.environ if environment is None else environment
    configured = env.get("XDG_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return root / "leonervis-code" / "mcp-servers.json"


def default_project_mcp_path(workspace: Path) -> Path:
    """Return the workspace-local MCP registry path."""
    return Path(workspace) / ".leonervis-code" / "mcp-servers.json"


class McpServerStore:
    """Atomically manage user and project MCP server definitions."""

    def __init__(self, user_path: Path, project_path: Path) -> None:
        self.user_path = Path(user_path)
        self.project_path = Path(project_path)
        self._thread_lock = RLock()
        self._transaction_depth = 0

    @classmethod
    def for_workspace(
        cls,
        workspace: Path,
        *,
        environment: Mapping[str, str] | None = None,
        user_path: Path | None = None,
        project_path: Path | None = None,
    ) -> McpServerStore:
        return cls(
            user_path or default_user_mcp_path(environment),
            project_path or default_project_mcp_path(workspace),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._thread_lock:
            if self._transaction_depth:
                self._transaction_depth += 1
                try:
                    yield
                finally:
                    self._transaction_depth -= 1
                return
            lock_paths = sorted(
                {
                    self.user_path.parent / ".mcp-servers.lock",
                    self.project_path.parent / ".mcp-servers.lock",
                },
                key=str,
            )
            streams = []
            try:
                for path in lock_paths:
                    stream = _open_lock_file(path)
                    _lock_stream(stream)
                    streams.append(stream)
                self._transaction_depth = 1
                yield
            finally:
                self._transaction_depth = 0
                for stream in reversed(streams):
                    try:
                        _unlock_stream(stream)
                    finally:
                        stream.close()

    def list_servers(self) -> tuple[McpServerEntry, ...]:
        user = self._load(self.user_path, "user")
        project = self._load(self.project_path, "project")
        collisions = set(user) & set(project)
        if collisions:
            raise McpConfigurationError(
                f"MCP server name is configured in both scopes: {sorted(collisions)[0]}"
            )
        entries = [
            *(McpServerEntry("user", server) for server in user.values()),
            *(McpServerEntry("project", server) for server in project.values()),
        ]
        return tuple(sorted(entries, key=lambda entry: entry.configuration.name))

    def get_server(self, name: str, *, scope: str | None = None) -> McpServerEntry:
        _validate_server_name(name)
        if scope is not None:
            _validate_scope(scope)
        matches = [
            entry
            for entry in self.list_servers()
            if entry.configuration.name == name and (scope is None or entry.scope == scope)
        ]
        if not matches:
            suffix = f" in {scope} scope" if scope is not None else ""
            raise McpConfigurationError(f"MCP server does not exist{suffix}: {name}")
        return matches[0]

    def add_server(
        self,
        configuration: McpServerConfiguration,
        *,
        scope: str,
        replace_existing: bool = False,
        expected_revision: int | None = None,
    ) -> McpServerEntry:
        if not isinstance(configuration, McpServerConfiguration):
            raise McpConfigurationError("MCP server configuration is invalid")
        _validate_scope(scope)
        with self.transaction():
            target_path = self._path_for_scope(scope)
            other_path = self._path_for_scope("project" if scope == "user" else "user")
            target = self._load(target_path, scope)
            other = self._load(other_path, "project" if scope == "user" else "user")
            if configuration.name in other:
                raise McpConfigurationError(
                    f"MCP server name is already configured in the other scope: {configuration.name}"
                )
            existing = target.get(configuration.name)
            if existing is None:
                if replace_existing and expected_revision is not None:
                    raise McpConfigurationError(
                        f"MCP server does not exist in {scope} scope: {configuration.name}"
                    )
                if len(target) >= MAX_MCP_SERVERS_PER_SCOPE:
                    raise McpConfigurationError(
                        f"MCP server limit reached in {scope} scope ({MAX_MCP_SERVERS_PER_SCOPE})"
                    )
                stored = replace(configuration, revision=1)
            else:
                if not replace_existing:
                    raise McpConfigurationError(
                        f"MCP server already exists in {scope} scope: {configuration.name}; "
                        "use --replace to update it"
                    )
                _check_revision(existing, expected_revision)
                if existing.same_definition(configuration):
                    return McpServerEntry(scope, existing)
                stored = replace(configuration, revision=existing.revision + 1)
            self._write(target_path, {**target, stored.name: stored})
            return McpServerEntry(scope, stored)

    def set_enabled(
        self,
        name: str,
        *,
        scope: str,
        enabled: bool,
        expected_revision: int | None = None,
    ) -> McpServerEntry:
        _validate_server_name(name)
        _validate_scope(scope)
        if type(enabled) is not bool:
            raise McpConfigurationError("MCP server enabled flag must be boolean")
        with self.transaction():
            path = self._path_for_scope(scope)
            servers = self._load(path, scope)
            try:
                current = servers[name]
            except KeyError:
                raise McpConfigurationError(
                    f"MCP server does not exist in {scope} scope: {name}"
                ) from None
            _check_revision(current, expected_revision)
            if current.enabled == enabled:
                return McpServerEntry(scope, current)
            updated = replace(current, enabled=enabled, revision=current.revision + 1)
            self._write(path, {**servers, name: updated})
            return McpServerEntry(scope, updated)

    def remove_server(
        self,
        name: str,
        *,
        scope: str,
        expected_revision: int | None = None,
    ) -> None:
        _validate_server_name(name)
        _validate_scope(scope)
        with self.transaction():
            path = self._path_for_scope(scope)
            servers = self._load(path, scope)
            try:
                current = servers[name]
            except KeyError:
                raise McpConfigurationError(
                    f"MCP server does not exist in {scope} scope: {name}"
                ) from None
            _check_revision(current, expected_revision)
            updated = dict(servers)
            del updated[name]
            self._write(path, updated)

    def set_resource_subscriptions(
        self,
        name: str,
        *,
        scope: str,
        subscriptions: tuple[str, ...],
        expected_revision: int | None = None,
    ) -> McpServerEntry:
        _validate_server_name(name)
        _validate_scope(scope)
        _validate_resource_subscriptions(subscriptions)
        with self.transaction():
            path = self._path_for_scope(scope)
            servers = self._load(path, scope)
            try:
                current = servers[name]
            except KeyError:
                raise McpConfigurationError(
                    f"MCP server does not exist in {scope} scope: {name}"
                ) from None
            _check_revision(current, expected_revision)
            if current.resource_subscriptions == subscriptions:
                return McpServerEntry(scope, current)
            updated = replace(
                current,
                resource_subscriptions=subscriptions,
                revision=current.revision + 1,
            )
            self._write(path, {**servers, name: updated})
            return McpServerEntry(scope, updated)

    def _path_for_scope(self, scope: str) -> Path:
        return self.user_path if scope == "user" else self.project_path

    @staticmethod
    def _load(path: Path, scope: str) -> dict[str, McpServerConfiguration]:
        data = _read_json(path)
        if data is None:
            return {}
        if set(data) != {"schema_version", "servers"}:
            unknown = set(data) - {"schema_version", "servers"}
            if unknown:
                raise McpConfigurationError(
                    f"{scope} MCP configuration contains unknown field: {sorted(unknown)[0]}"
                )
            raise McpConfigurationError(f"{scope} MCP configuration is missing a required field")
        if data["schema_version"] not in SUPPORTED_MCP_CONFIGURATION_SCHEMA_VERSIONS:
            raise McpConfigurationError(f"unsupported {scope} MCP configuration schema version")
        raw_servers = data["servers"]
        if not isinstance(raw_servers, dict):
            raise McpConfigurationError(f"{scope} MCP servers must be a JSON object")
        if len(raw_servers) > MAX_MCP_SERVERS_PER_SCOPE:
            raise McpConfigurationError(
                f"MCP server limit exceeded in {scope} scope ({MAX_MCP_SERVERS_PER_SCOPE})"
            )
        servers: dict[str, McpServerConfiguration] = {}
        for name, value in raw_servers.items():
            if not isinstance(name, str):
                raise McpConfigurationError(f"{scope} MCP server key must be text")
            server = McpServerConfiguration.from_mapping(value)
            if server.name != name:
                raise McpConfigurationError(f"{scope} MCP server key/name mismatch: {name}")
            servers[name] = server
        return servers

    @staticmethod
    def _write(path: Path, servers: Mapping[str, McpServerConfiguration]) -> None:
        _atomic_write(
            path,
            {
                "schema_version": MCP_CONFIGURATION_SCHEMA_VERSION,
                "servers": {name: server.as_mapping() for name, server in sorted(servers.items())},
            },
        )


def parse_environment_bindings(values: list[str] | tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Parse repeatable TARGET=SOURCE environment references without reading values."""
    if not isinstance(values, (list, tuple)):
        raise McpConfigurationError("MCP environment bindings are invalid")
    bindings: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, str) or value.count("=") != 1:
            raise McpConfigurationError("MCP environment binding must be TARGET=SOURCE")
        target, source = value.split("=", 1)
        bindings.append((target, source))
    result = tuple(sorted(bindings))
    _validate_environment(result)
    return result


def _validate_server_name(name: object) -> None:
    if not isinstance(name, str) or _SERVER_NAME.fullmatch(name) is None:
        raise McpConfigurationError("MCP server name is invalid")


def _validate_command(command: object) -> None:
    if not isinstance(command, str) or not command or _contains_control(command):
        raise McpConfigurationError("MCP server command must be nonblank control-free text")
    if len(command) > 4096 or len(command.encode("utf-8")) > 16 * 1024:
        raise McpConfigurationError("MCP server command exceeds the supported size")
    if not Path(command).is_absolute() or PureWindowsPath(command).is_absolute():
        raise McpConfigurationError("MCP server command must be an absolute POSIX path")


def _validate_args(args: object) -> None:
    if not isinstance(args, tuple) or len(args) > MAX_MCP_ARGUMENTS:
        raise McpConfigurationError(
            f"MCP server args must contain at most {MAX_MCP_ARGUMENTS} items"
        )
    total = 0
    for item in args:
        if not isinstance(item, str) or _contains_control(item):
            raise McpConfigurationError("MCP server args must be control-free strings")
        encoded = item.encode("utf-8")
        if len(item) > MAX_MCP_ARGUMENT_CHARACTERS or len(encoded) > MAX_MCP_ARGUMENT_BYTES:
            raise McpConfigurationError("MCP server argument exceeds the supported size")
        total += len(encoded)
    if total > MAX_MCP_ARGV_BYTES:
        raise McpConfigurationError("MCP server argv exceeds the supported size")


def _validate_cwd(cwd: object) -> None:
    if not isinstance(cwd, str) or not cwd or _contains_control(cwd):
        raise McpConfigurationError("MCP server cwd must be nonblank control-free text")
    if len(cwd) > MAX_MCP_CWD_CHARACTERS or len(cwd.encode("utf-8")) > 16 * 1024:
        raise McpConfigurationError("MCP server cwd exceeds the supported size")
    path = Path(cwd)
    if path.is_absolute() or PureWindowsPath(cwd).is_absolute() or ".." in path.parts:
        raise McpConfigurationError("MCP server cwd must stay within the workspace")
    if cwd != "." and (not path.parts or any(part in {"", "."} for part in path.parts)):
        raise McpConfigurationError("MCP server cwd is not canonical")


def _validate_environment(environment: object) -> None:
    if not isinstance(environment, tuple) or len(environment) > MAX_MCP_ENVIRONMENT_BINDINGS:
        raise McpConfigurationError(
            f"MCP server environment supports at most {MAX_MCP_ENVIRONMENT_BINDINGS} bindings"
        )
    if tuple(sorted(environment)) != environment:
        raise McpConfigurationError("MCP server environment bindings must be canonical")
    targets: set[str] = set()
    for binding in environment:
        if (
            not isinstance(binding, tuple)
            or len(binding) != 2
            or not all(isinstance(item, str) for item in binding)
        ):
            raise McpConfigurationError("MCP server environment binding is invalid")
        target, source = binding
        if (
            _ENVIRONMENT_NAME.fullmatch(target) is None
            or _ENVIRONMENT_NAME.fullmatch(source) is None
        ):
            raise McpConfigurationError("MCP server environment name is invalid")
        if target in targets:
            raise McpConfigurationError("MCP server environment target is duplicated")
        targets.add(target)


def _validate_optional_environment_name(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or _ENVIRONMENT_NAME.fullmatch(value) is None:
        raise McpConfigurationError(f"MCP {label} environment name is invalid")


def _validate_oauth(configuration: McpServerConfiguration) -> None:
    client_id = configuration.oauth_client_id
    secret_env = configuration.oauth_client_secret_env
    scopes = configuration.oauth_scopes
    if configuration.bearer_token_env is not None and client_id is not None:
        raise McpConfigurationError("MCP bearer and OAuth authentication are mutually exclusive")
    if client_id is None:
        if secret_env is not None or scopes:
            raise McpConfigurationError("MCP OAuth scopes or secret require a client ID")
        return
    if (
        not isinstance(client_id, str)
        or not client_id
        or len(client_id) > 512
        or _contains_control(client_id)
    ):
        raise McpConfigurationError("MCP OAuth client ID is invalid")
    _validate_optional_environment_name(secret_env, "OAuth client secret")
    if not isinstance(scopes, tuple) or len(scopes) > 32:
        raise McpConfigurationError("MCP OAuth scopes are invalid")
    if tuple(sorted(set(scopes))) != scopes:
        raise McpConfigurationError("MCP OAuth scopes must be unique and canonical")
    for scope in scopes:
        if (
            not isinstance(scope, str)
            or not scope
            or len(scope) > 256
            or any(character.isspace() or ord(character) < 0x21 for character in scope)
        ):
            raise McpConfigurationError("MCP OAuth scope is invalid")


def _validate_resource_subscriptions(values: object) -> None:
    if not isinstance(values, tuple) or len(values) > 64 or tuple(sorted(set(values))) != values:
        raise McpConfigurationError("MCP resource subscriptions must be unique and canonical")
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or _contains_control(value)
            or any(character.isspace() for character in value)
            or not urlsplit(value).scheme
            or urlsplit(value).fragment
        ):
            raise McpConfigurationError("MCP resource subscription URI is invalid")


def _canonical_remote_endpoint(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _contains_control(value)
    ):
        raise McpConfigurationError("remote MCP endpoint is invalid")
    if len(value) > 4096 or len(value.encode("utf-8")) > 4096:
        raise McpConfigurationError("remote MCP endpoint exceeds the supported size")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise McpConfigurationError("remote MCP endpoint is malformed") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and port != 443)
    ):
        raise McpConfigurationError(
            "remote MCP endpoint must be credential-free HTTPS on the standard port"
        )
    host = parsed.hostname.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise McpConfigurationError("remote MCP endpoint hostname is invalid") from None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def _check_revision(
    configuration: McpServerConfiguration,
    expected_revision: int | None,
) -> None:
    if expected_revision is None:
        return
    if type(expected_revision) is not int or expected_revision < 1:
        raise McpConfigurationError("expected MCP server revision must be positive")
    if configuration.revision != expected_revision:
        raise McpConfigurationError(
            f"MCP server revision conflict: expected {expected_revision}, "
            f"found {configuration.revision}"
        )


def _validate_scope(scope: object) -> None:
    if scope not in {"user", "project"}:
        raise McpConfigurationError("MCP server scope must be user or project")


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise McpConfigurationError(f"MCP configuration path must not be a symlink: {path}")
    if not path.exists():
        return None
    _ensure_safe_existing_chain(path)
    _ensure_safe_path(path, require_file=True)
    try:
        if path.stat().st_size > MAX_MCP_CONFIGURATION_BYTES:
            raise McpConfigurationError(
                f"MCP configuration exceeds {MAX_MCP_CONFIGURATION_BYTES} bytes: {path}"
            )
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream, parse_constant=_reject_json_constant)
    except McpConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise McpConfigurationError(f"MCP configuration is unreadable or invalid: {path}") from None
    if not isinstance(data, dict):
        raise McpConfigurationError(f"MCP configuration must be a JSON object: {path}")
    return data


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    parent = path.parent
    _ensure_safe_parent_chain(parent)
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    except OSError:
        raise McpConfigurationError(
            f"could not create or secure MCP configuration directory: {parent}"
        ) from None
    _ensure_safe_path(parent, require_file=False)
    if path.exists() or path.is_symlink():
        _ensure_safe_path(path, require_file=True)
    payload = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_MCP_CONFIGURATION_BYTES:
        raise McpConfigurationError(
            f"MCP configuration exceeds {MAX_MCP_CONFIGURATION_BYTES} bytes: {path}"
        )
    descriptor: int | None = None
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _fsync_directory(parent)
    except OSError:
        raise McpConfigurationError(f"could not write MCP configuration: {path}") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _reject_json_constant(value: str) -> None:
    raise McpConfigurationError(f"MCP configuration contains invalid JSON constant: {value}")


def _open_lock_file(path: Path):
    _ensure_safe_parent_chain(path.parent)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    except OSError:
        raise McpConfigurationError(
            f"could not create MCP configuration lock directory: {path.parent}"
        ) from None
    _ensure_safe_path(path.parent, require_file=False)
    if path.is_symlink():
        raise McpConfigurationError(f"MCP configuration lock must not be a symlink: {path}")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a+b")
    except OSError:
        raise McpConfigurationError(f"could not open MCP configuration lock: {path}") from None


def _lock_stream(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except OSError:
        raise McpConfigurationError("could not lock MCP configuration") from None


def _unlock_stream(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _ensure_safe_existing_chain(path: Path) -> None:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            break
        if current.is_symlink():
            raise McpConfigurationError(
                f"MCP configuration path must not contain a symlink: {current}"
            )


def _ensure_safe_parent_chain(path: Path) -> None:
    _ensure_safe_existing_chain(path)
    current = path
    while not current.exists() and not current.is_symlink():
        if current == current.parent:
            break
        current = current.parent
    if current.is_symlink():
        raise McpConfigurationError(f"MCP configuration path must not contain a symlink: {current}")


def _ensure_safe_path(path: Path, *, require_file: bool) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise McpConfigurationError(f"MCP configuration path is unavailable: {path}") from None
    expected = stat.S_ISREG(info.st_mode) if require_file else stat.S_ISDIR(info.st_mode)
    if not expected or stat.S_ISLNK(info.st_mode):
        kind = "regular file" if require_file else "directory"
        raise McpConfigurationError(f"MCP configuration path must be a {kind}: {path}")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
