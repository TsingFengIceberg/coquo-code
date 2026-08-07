"""Strict declarative Hook configuration and frozen preauthorization policy."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile
from threading import RLock

from leonervis_code.core.contracts import ToolArguments
from leonervis_code.core.extensions import ExtensionSourceKind
from leonervis_code.core.hook_contracts import (
    HookActionOutcome,
    HookAuditEntry,
    HookAuditMatch,
    HookEffect,
    HookEvent,
    HookHandlerResult,
    HookHandlerSpec,
    aggregate_hook_effect,
)
from leonervis_code.core.permissions import PermissionAction

if os.name == "nt":
    import msvcrt
else:
    import fcntl


LEGACY_HOOK_CONFIGURATION_SCHEMA_VERSION = 1
OBSERVATION_HOOK_CONFIGURATION_SCHEMA_VERSION = 2
HOOK_CONFIGURATION_SCHEMA_VERSION = 3
HOOK_SET_SNAPSHOT_VERSION = 3
MAX_HOOK_CONFIGURATION_BYTES = 256 * 1024
MAX_HOOKS_PER_SCOPE = 64
MAX_HOOK_MATCHER_VALUES = 32
MAX_HOOK_MESSAGE_CHARACTERS = 1024
MAX_HOOK_MESSAGE_BYTES = 4096
MAX_HOOK_PATH_CHARACTERS = 4096
MAX_HOOK_ADVISORY_CHARACTERS = 2048
HOOK_IMPORT_SCHEMA_VERSION = 1
_HOOK_ID = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_HOOK_SET_ID_DOMAIN = b"leonervis-code-hook-set-snapshot-v3\0"
_PATH_ARGUMENT_KEYS = frozenset({"path", "source", "destination", "cwd"})


class HookConfigurationError(ValueError):
    """One Hook configuration is malformed, unsafe, stale, or unavailable."""


class HookSource(StrEnum):
    """Tool provenance classes exposed to deterministic Hook matching."""

    BUILTIN = "builtin"
    MCP = "mcp"


@dataclass(frozen=True)
class HookRule:
    """One revisioned declarative rule with an optional pinned local handler."""

    hook_id: str
    effect: HookEffect
    message: str = ""
    tool_names: tuple[str, ...] = ()
    permission_actions: tuple[PermissionAction, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    action_outcomes: tuple[HookActionOutcome, ...] = ()
    sources: tuple[HookSource, ...] = ()
    handler: HookHandlerSpec | None = None
    enabled: bool = False
    event: HookEvent = HookEvent.BEFORE_ACTION_AUTHORIZATION
    revision: int = 1

    def __post_init__(self) -> None:
        _validate_hook_id(self.hook_id)
        if type(self.event) is not HookEvent:
            raise HookConfigurationError("Hook event is invalid")
        if type(self.effect) is not HookEffect:
            raise HookConfigurationError("Hook effect is invalid")
        if type(self.enabled) is not bool:
            raise HookConfigurationError("Hook enabled flag must be boolean")
        if type(self.revision) is not int or self.revision < 1:
            raise HookConfigurationError("Hook revision must be positive")
        _validate_canonical_tuple(self.tool_names, "tool names", _validate_tool_name)
        _validate_canonical_tuple(
            self.permission_actions,
            "permission actions",
            lambda value: _validate_enum(value, PermissionAction, "permission action"),
        )
        if PermissionAction.UNKNOWN in self.permission_actions:
            raise HookConfigurationError("Hook cannot match the unknown permission action")
        _validate_canonical_tuple(self.path_prefixes, "path prefixes", _validate_path_prefix)
        _validate_canonical_tuple(
            self.action_outcomes,
            "action outcomes",
            lambda value: _validate_enum(value, HookActionOutcome, "action outcome"),
        )
        _validate_canonical_tuple(
            self.sources,
            "sources",
            lambda value: _validate_enum(value, HookSource, "source"),
        )
        if not isinstance(self.message, str) or _contains_control(self.message):
            raise HookConfigurationError("Hook message must be control-free text")
        if (
            len(self.message) > MAX_HOOK_MESSAGE_CHARACTERS
            or len(self.message.encode("utf-8")) > MAX_HOOK_MESSAGE_BYTES
        ):
            raise HookConfigurationError("Hook message exceeds the supported size")
        if self.effect is not HookEffect.CONTINUE and not self.message.strip():
            raise HookConfigurationError(f"Hook {self.effect.value} effect requires a message")
        if self.handler is not None:
            if type(self.handler) is not HookHandlerSpec:
                raise HookConfigurationError("Hook handler is invalid")
            if self.effect is not HookEffect.CONTINUE or self.message:
                raise HookConfigurationError(
                    "executable Hook rules must use continue with an empty static message"
                )
        if self.event is not HookEvent.BEFORE_ACTION_AUTHORIZATION and self.effect in {
            HookEffect.DENY,
            HookEffect.REQUIRE_ASK,
        }:
            raise HookConfigurationError(
                "observational Hook events support only continue or advisory"
            )
        if self.event is HookEvent.BEFORE_ACTION_AUTHORIZATION and self.action_outcomes:
            raise HookConfigurationError("preauthorization Hook cannot match action outcomes")
        if not self.event.is_action_event and any(
            (
                self.tool_names,
                self.permission_actions,
                self.path_prefixes,
                self.action_outcomes,
                self.sources,
            )
        ):
            raise HookConfigurationError("lifecycle Hook events cannot contain action matchers")

    @classmethod
    def from_mapping(cls, value: object) -> HookRule:
        if not isinstance(value, dict):
            raise HookConfigurationError("Hook entry must be a JSON object")
        fields = {
            "effect",
            "enabled",
            "event",
            "hook_id",
            "handler",
            "message",
            "action_outcomes",
            "path_prefixes",
            "permission_actions",
            "revision",
            "sources",
            "tool_names",
        }
        unknown = set(value) - fields
        if unknown:
            raise HookConfigurationError(f"Hook contains unknown field: {sorted(unknown)[0]}")
        missing = fields - set(value)
        if missing:
            raise HookConfigurationError(f"Hook is missing required field: {sorted(missing)[0]}")
        return cls(
            hook_id=_required_text(value["hook_id"], "Hook ID"),
            handler=(None if value["handler"] is None else _parse_handler(value["handler"])),
            event=_parse_enum(value["event"], HookEvent, "Hook event"),
            effect=_parse_enum(value["effect"], HookEffect, "Hook effect"),
            message=_required_text(value["message"], "Hook message", allow_empty=True),
            action_outcomes=tuple(
                _parse_enum(item, HookActionOutcome, "Hook action outcome")
                for item in _array(value["action_outcomes"], "Hook action outcomes")
            ),
            tool_names=_text_tuple(value["tool_names"], "Hook tool names"),
            permission_actions=tuple(
                _parse_enum(item, PermissionAction, "Hook permission action")
                for item in _array(value["permission_actions"], "Hook permission actions")
            ),
            path_prefixes=_text_tuple(value["path_prefixes"], "Hook path prefixes"),
            sources=tuple(
                _parse_enum(item, HookSource, "Hook source")
                for item in _array(value["sources"], "Hook sources")
            ),
            enabled=value["enabled"],
            revision=value["revision"],
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "action_outcomes": [outcome.value for outcome in self.action_outcomes],
            "effect": self.effect.value,
            "enabled": self.enabled,
            "event": self.event.value,
            "hook_id": self.hook_id,
            "handler": self.handler.as_mapping() if self.handler is not None else None,
            "message": self.message,
            "path_prefixes": list(self.path_prefixes),
            "permission_actions": [action.value for action in self.permission_actions],
            "revision": self.revision,
            "sources": [source.value for source in self.sources],
            "tool_names": list(self.tool_names),
        }

    def same_definition(self, other: HookRule) -> bool:
        return replace(self, revision=1) == replace(other, revision=1)


@dataclass(frozen=True)
class HookEntry:
    """One Hook plus its configuration scope."""

    scope: str
    rule: HookRule

    def __post_init__(self) -> None:
        _validate_scope(self.scope)
        if not isinstance(self.rule, HookRule):
            raise HookConfigurationError("Hook entry rule is invalid")


@dataclass(frozen=True)
class HookSetSnapshot:
    """One immutable deterministic Hook configuration frozen for a Turn."""

    entries: tuple[HookEntry, ...]
    version: int = HOOK_SET_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != HOOK_SET_SNAPSHOT_VERSION:
            raise HookConfigurationError("unsupported Hook set snapshot version")
        if not isinstance(self.entries, tuple):
            raise HookConfigurationError("Hook set entries are invalid")
        names: set[str] = set()
        for entry in self.entries:
            if not isinstance(entry, HookEntry):
                raise HookConfigurationError("Hook set contains an invalid entry")
            if entry.rule.hook_id in names:
                raise HookConfigurationError("Hook set contains a duplicate Hook ID")
            names.add(entry.rule.hook_id)
        if tuple(sorted(self.entries, key=lambda item: item.rule.hook_id)) != self.entries:
            raise HookConfigurationError("Hook set entries are not canonical")

    @property
    def active_entries(self) -> tuple[HookEntry, ...]:
        return tuple(entry for entry in self.entries if entry.rule.enabled)

    def get(self, hook_id: str) -> HookEntry:
        _validate_hook_id(hook_id)
        for entry in self.entries:
            if entry.rule.hook_id == hook_id:
                return entry
        raise HookConfigurationError(f"Hook does not exist: {hook_id}")

    @property
    def snapshot_id(self) -> str:
        manifest = {
            "entries": [
                {"scope": entry.scope, "rule": entry.rule.as_mapping()} for entry in self.entries
            ],
            "version": self.version,
        }
        payload = json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(_HOOK_SET_ID_DOMAIN + payload).hexdigest()
        return f"hooks-v{self.version}-{digest}"


@dataclass(frozen=True)
class HookEvaluation:
    """Pure aggregate result for one frozen preauthorization event."""

    denied_by: str | None = None
    deny_message: str | None = None
    require_ask_by: tuple[str, ...] = ()
    advisories: tuple[tuple[str, str], ...] = ()
    matches: tuple[HookAuditMatch, ...] = ()
    matched_entries: tuple[HookEntry, ...] = ()

    @property
    def requires_ask(self) -> bool:
        return bool(self.require_ask_by)

    @property
    def advisory_text(self) -> str | None:
        if not self.advisories:
            return None
        text = "\n".join(
            f"Hook advisory [{hook_id}]: {message}" for hook_id, message in self.advisories
        )
        return text[:MAX_HOOK_ADVISORY_CHARACTERS]

    def audit_entry(
        self,
        *,
        event: HookEvent,
        hook_set_id: str,
        subject_id: str,
        tool_name: str | None = None,
        permission_action: str | None = None,
        source: str | None = None,
        action_outcome: HookActionOutcome | None = None,
    ) -> HookAuditEntry:
        return HookAuditEntry(
            event=event,
            hook_set_id=hook_set_id,
            subject_id=subject_id,
            matches=self.matches,
            result=aggregate_hook_effect(self.matches),
            tool_name=tool_name,
            permission_action=permission_action,
            source=source,
            action_outcome=action_outcome,
        )


class HookStore:
    """Atomically manage user and project declarative Hook definitions."""

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
    ) -> HookStore:
        return cls(
            user_path or default_user_hooks_path(environment),
            project_path or default_project_hooks_path(workspace),
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
                    self.user_path.parent / ".hooks.lock",
                    self.project_path.parent / ".hooks.lock",
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

    def list_hooks(self) -> tuple[HookEntry, ...]:
        user = self._load(self.user_path, "user")
        project = self._load(self.project_path, "project")
        collisions = set(user) & set(project)
        if collisions:
            raise HookConfigurationError(
                f"Hook ID is configured in both scopes: {sorted(collisions)[0]}"
            )
        entries = [
            *(HookEntry("user", rule) for rule in user.values()),
            *(HookEntry("project", rule) for rule in project.values()),
        ]
        return tuple(sorted(entries, key=lambda entry: entry.rule.hook_id))

    def snapshot(self) -> HookSetSnapshot:
        return HookSetSnapshot(self.list_hooks())

    def get_hook(self, hook_id: str, *, scope: str | None = None) -> HookEntry:
        _validate_hook_id(hook_id)
        if scope is not None:
            _validate_scope(scope)
        matches = [
            entry
            for entry in self.list_hooks()
            if entry.rule.hook_id == hook_id and (scope is None or entry.scope == scope)
        ]
        if not matches:
            suffix = f" in {scope} scope" if scope is not None else ""
            raise HookConfigurationError(f"Hook does not exist{suffix}: {hook_id}")
        return matches[0]

    def add_hook(
        self,
        rule: HookRule,
        *,
        scope: str,
        replace_existing: bool = False,
        expected_revision: int | None = None,
    ) -> HookEntry:
        if not isinstance(rule, HookRule):
            raise HookConfigurationError("Hook rule is invalid")
        _validate_scope(scope)
        with self.transaction():
            path = self._path_for_scope(scope)
            target = self._load(path, scope)
            other = self._load(
                self.project_path if scope == "user" else self.user_path,
                "project" if scope == "user" else "user",
            )
            if rule.hook_id in other:
                raise HookConfigurationError(
                    f"Hook ID is already configured in the other scope: {rule.hook_id}"
                )
            existing = target.get(rule.hook_id)
            if existing is None:
                if replace_existing and expected_revision is not None:
                    raise HookConfigurationError(
                        f"Hook does not exist in {scope} scope: {rule.hook_id}"
                    )
                if len(target) >= MAX_HOOKS_PER_SCOPE:
                    raise HookConfigurationError(
                        f"Hook limit reached in {scope} scope ({MAX_HOOKS_PER_SCOPE})"
                    )
                stored = replace(rule, revision=1)
            else:
                if not replace_existing:
                    raise HookConfigurationError(
                        f"Hook already exists in {scope} scope: {rule.hook_id}; use --replace"
                    )
                _check_revision(existing, expected_revision)
                if existing.same_definition(rule):
                    return HookEntry(scope, existing)
                stored = replace(rule, revision=existing.revision + 1)
            self._write(path, {**target, stored.hook_id: stored})
            return HookEntry(scope, stored)

    def set_enabled(
        self,
        hook_id: str,
        *,
        scope: str,
        enabled: bool,
        expected_revision: int | None = None,
    ) -> HookEntry:
        _validate_hook_id(hook_id)
        _validate_scope(scope)
        if type(enabled) is not bool:
            raise HookConfigurationError("Hook enabled flag must be boolean")
        with self.transaction():
            path = self._path_for_scope(scope)
            hooks = self._load(path, scope)
            if hook_id not in hooks:
                raise HookConfigurationError(f"Hook does not exist in {scope} scope: {hook_id}")
            current = hooks[hook_id]
            _check_revision(current, expected_revision)
            if current.enabled == enabled:
                return HookEntry(scope, current)
            updated = replace(current, enabled=enabled, revision=current.revision + 1)
            self._write(path, {**hooks, hook_id: updated})
            return HookEntry(scope, updated)

    def remove_hook(
        self,
        hook_id: str,
        *,
        scope: str,
        expected_revision: int | None = None,
    ) -> None:
        _validate_hook_id(hook_id)
        _validate_scope(scope)
        with self.transaction():
            path = self._path_for_scope(scope)
            hooks = self._load(path, scope)
            if hook_id not in hooks:
                raise HookConfigurationError(f"Hook does not exist in {scope} scope: {hook_id}")
            _check_revision(hooks[hook_id], expected_revision)
            updated = dict(hooks)
            del updated[hook_id]
            self._write(path, updated)

    def _path_for_scope(self, scope: str) -> Path:
        return self.user_path if scope == "user" else self.project_path

    @staticmethod
    def _load(path: Path, scope: str) -> dict[str, HookRule]:
        data = _read_json(path)
        if data is None:
            return {}
        fields = {"hooks", "schema_version"}
        unknown = set(data) - fields
        if unknown:
            raise HookConfigurationError(
                f"{scope} Hook configuration contains unknown field: {sorted(unknown)[0]}"
            )
        if set(data) != fields:
            raise HookConfigurationError(f"{scope} Hook configuration is missing a required field")
        schema_version = data["schema_version"]
        if schema_version not in {
            LEGACY_HOOK_CONFIGURATION_SCHEMA_VERSION,
            OBSERVATION_HOOK_CONFIGURATION_SCHEMA_VERSION,
            HOOK_CONFIGURATION_SCHEMA_VERSION,
        }:
            raise HookConfigurationError(f"unsupported {scope} Hook configuration schema version")
        raw_hooks = data["hooks"]
        if not isinstance(raw_hooks, dict):
            raise HookConfigurationError(f"{scope} Hooks must be a JSON object")
        if len(raw_hooks) > MAX_HOOKS_PER_SCOPE:
            raise HookConfigurationError(
                f"Hook limit exceeded in {scope} scope ({MAX_HOOKS_PER_SCOPE})"
            )
        hooks: dict[str, HookRule] = {}
        for hook_id, value in raw_hooks.items():
            if not isinstance(hook_id, str):
                raise HookConfigurationError(f"{scope} Hook key must be text")
            if schema_version == LEGACY_HOOK_CONFIGURATION_SCHEMA_VERSION:
                if not isinstance(value, dict):
                    raise HookConfigurationError("Hook entry must be a JSON object")
                value = {**value, "action_outcomes": []}
            if schema_version in {
                LEGACY_HOOK_CONFIGURATION_SCHEMA_VERSION,
                OBSERVATION_HOOK_CONFIGURATION_SCHEMA_VERSION,
            }:
                if not isinstance(value, dict):
                    raise HookConfigurationError("Hook entry must be a JSON object")
                value = {**value, "handler": None}
            rule = HookRule.from_mapping(value)
            if rule.hook_id != hook_id:
                raise HookConfigurationError(f"{scope} Hook key/ID mismatch: {hook_id}")
            hooks[hook_id] = rule
        return hooks

    @staticmethod
    def _write(path: Path, hooks: Mapping[str, HookRule]) -> None:
        _atomic_write(
            path,
            {
                "hooks": {hook_id: rule.as_mapping() for hook_id, rule in sorted(hooks.items())},
                "schema_version": HOOK_CONFIGURATION_SCHEMA_VERSION,
            },
        )


def default_user_hooks_path(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    config_home = values.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "leonervis-code" / "hooks.json"


def default_project_hooks_path(workspace: Path) -> Path:
    return Path(workspace) / ".leonervis-code" / "hooks.json"


def hook_handler_import_template() -> dict[str, object]:
    """Return one strict disabled local-handler import template."""
    return {
        "hook": HookRule(
            hook_id="replace-with-hook-id",
            event=HookEvent.AFTER_ACTION,
            effect=HookEffect.CONTINUE,
            handler=HookHandlerSpec(
                executable="hooks/replace-with-handler",
                arguments=(),
                timeout_seconds=10,
                executable_sha256="0" * 64,
            ),
        ).as_mapping(),
        "schema_version": HOOK_IMPORT_SCHEMA_VERSION,
    }


def load_hook_import_file(workspace: Path, relative_path: str) -> HookRule:
    """Strictly load one workspace-local import candidate without following symlinks."""
    _validate_path_prefix(relative_path)
    if relative_path == ".":
        raise HookConfigurationError("Hook import path must identify a file")
    root = Path(workspace).resolve()
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            raise HookConfigurationError("Hook import file is unavailable") from None
        if stat.S_ISLNK(info.st_mode):
            raise HookConfigurationError("Hook import path must not contain symbolic links")
    if not stat.S_ISREG(info.st_mode):
        raise HookConfigurationError("Hook import path must identify a regular file")
    if info.st_size > MAX_HOOK_CONFIGURATION_BYTES:
        raise HookConfigurationError("Hook import file exceeds its byte bound")
    try:
        payload = current.read_bytes()
        value = json.loads(payload, parse_constant=_reject_json_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise HookConfigurationError("Hook import file is unreadable or invalid") from None
    if not isinstance(value, dict) or set(value) != {"hook", "schema_version"}:
        raise HookConfigurationError("Hook import file must be a closed object")
    if value["schema_version"] != HOOK_IMPORT_SCHEMA_VERSION:
        raise HookConfigurationError("Hook import schema version is unsupported")
    rule = HookRule.from_mapping(value["hook"])
    return replace(rule, enabled=False, revision=1)


def evaluate_before_action_authorization(
    snapshot: HookSetSnapshot,
    *,
    tool_name: str,
    action: PermissionAction,
    arguments: ToolArguments,
    source_kind: ExtensionSourceKind,
) -> HookEvaluation:
    """Evaluate one action against an immutable Hook set without side effects."""
    return _evaluate_action(
        snapshot,
        event=HookEvent.BEFORE_ACTION_AUTHORIZATION,
        tool_name=tool_name,
        action=action,
        arguments=arguments,
        source_kind=source_kind,
        outcome=None,
    )


def evaluate_after_action(
    snapshot: HookSetSnapshot,
    *,
    tool_name: str,
    action: PermissionAction,
    arguments: ToolArguments,
    source_kind: ExtensionSourceKind,
    outcome: HookActionOutcome,
) -> HookEvaluation:
    """Observe one terminal action result without changing its authoritative outcome."""
    if type(outcome) is not HookActionOutcome:
        raise ValueError("after-action Hook outcome is invalid")
    return _evaluate_action(
        snapshot,
        event=HookEvent.AFTER_ACTION,
        tool_name=tool_name,
        action=action,
        arguments=arguments,
        source_kind=source_kind,
        outcome=outcome,
    )


def evaluate_lifecycle_event(
    snapshot: HookSetSnapshot,
    *,
    event: HookEvent,
) -> HookEvaluation:
    """Evaluate one side-effect-free Turn or Task lifecycle observation."""
    if not isinstance(snapshot, HookSetSnapshot):
        raise ValueError("Hook set snapshot is invalid")
    if type(event) is not HookEvent or event.is_action_event:
        raise ValueError("lifecycle Hook event is invalid")
    matched = tuple(entry for entry in snapshot.active_entries if entry.rule.event is event)
    return _evaluation_from_matches(matched)


def _evaluate_action(
    snapshot: HookSetSnapshot,
    *,
    event: HookEvent,
    tool_name: str,
    action: PermissionAction,
    arguments: ToolArguments,
    source_kind: ExtensionSourceKind,
    outcome: HookActionOutcome | None,
) -> HookEvaluation:
    if not isinstance(snapshot, HookSetSnapshot):
        raise ValueError("Hook set snapshot is invalid")
    if event not in {
        HookEvent.BEFORE_ACTION_AUTHORIZATION,
        HookEvent.AFTER_ACTION,
    }:
        raise ValueError("action Hook event is invalid")
    source = HookSource.MCP if source_kind is ExtensionSourceKind.MCP else HookSource.BUILTIN
    paths = _extract_workspace_paths(arguments)
    matched = tuple(
        entry
        for entry in snapshot.active_entries
        if _matches(
            entry.rule,
            event=event,
            tool_name=tool_name,
            action=action,
            paths=paths,
            source=source,
            outcome=outcome,
        )
    )
    return _evaluation_from_matches(matched)


def _evaluation_from_matches(matched: tuple[HookEntry, ...]) -> HookEvaluation:
    denied = next((entry for entry in matched if entry.rule.effect is HookEffect.DENY), None)
    required = tuple(
        entry.rule.hook_id for entry in matched if entry.rule.effect is HookEffect.REQUIRE_ASK
    )
    advisories = tuple(
        (entry.rule.hook_id, entry.rule.message)
        for entry in matched
        if entry.rule.effect is HookEffect.ADVISORY
    )
    matches = tuple(HookAuditMatch(entry.rule.hook_id, entry.rule.effect) for entry in matched)
    return HookEvaluation(
        denied_by=denied.rule.hook_id if denied is not None else None,
        deny_message=denied.rule.message if denied is not None else None,
        require_ask_by=(() if denied is not None else required),
        advisories=(() if denied is not None else advisories),
        matches=matches,
        matched_entries=matched,
    )


def apply_handler_results(
    evaluation: HookEvaluation,
    results: Mapping[str, HookHandlerResult],
) -> HookEvaluation:
    """Replace matched executable-rule placeholders with strict handler results."""
    if not isinstance(evaluation, HookEvaluation) or not isinstance(results, Mapping):
        raise ValueError("Hook handler result application is invalid")
    resolved: list[tuple[HookEntry, HookEffect, str]] = []
    for entry in evaluation.matched_entries:
        result = results.get(entry.rule.hook_id)
        if entry.rule.handler is None:
            if result is not None:
                raise ValueError("static Hook rule cannot have a handler result")
            resolved.append((entry, entry.rule.effect, entry.rule.message))
            continue
        if type(result) is not HookHandlerResult:
            raise ValueError("executable Hook rule requires one handler result")
        resolved.append((entry, result.effect, result.message))
    if set(results) != {
        entry.rule.hook_id for entry in evaluation.matched_entries if entry.rule.handler is not None
    }:
        raise ValueError("Hook handler results do not match evaluated rules")
    denied = next((item for item in resolved if item[1] is HookEffect.DENY), None)
    required = tuple(item[0].rule.hook_id for item in resolved if item[1] is HookEffect.REQUIRE_ASK)
    advisories = tuple(
        (item[0].rule.hook_id, item[2]) for item in resolved if item[1] is HookEffect.ADVISORY
    )
    matches = tuple(HookAuditMatch(item[0].rule.hook_id, item[1]) for item in resolved)
    return HookEvaluation(
        denied_by=denied[0].rule.hook_id if denied is not None else None,
        deny_message=denied[2] if denied is not None else None,
        require_ask_by=(() if denied is not None else required),
        advisories=(() if denied is not None else advisories),
        matches=matches,
        matched_entries=evaluation.matched_entries,
    )


def _matches(
    rule: HookRule,
    *,
    event: HookEvent,
    tool_name: str,
    action: PermissionAction,
    paths: tuple[str, ...],
    source: HookSource,
    outcome: HookActionOutcome | None,
) -> bool:
    if rule.event is not event:
        return False
    if rule.tool_names and tool_name not in rule.tool_names:
        return False
    if rule.permission_actions and action not in rule.permission_actions:
        return False
    if rule.sources and source not in rule.sources:
        return False
    if rule.path_prefixes and not any(
        _path_has_prefix(path, prefix) for path in paths for prefix in rule.path_prefixes
    ):
        return False
    if rule.action_outcomes and outcome not in rule.action_outcomes:
        return False
    return True


def _extract_workspace_paths(arguments: ToolArguments) -> tuple[str, ...]:
    values: set[str] = set()
    for key, value in arguments.as_mapping().items():
        if key not in _PATH_ARGUMENT_KEYS or not isinstance(value, str):
            continue
        try:
            _validate_path_prefix(value)
        except HookConfigurationError:
            continue
        values.add(value)
    return tuple(sorted(values))


def _path_has_prefix(path: str, prefix: str) -> bool:
    return prefix == "." or path == prefix or path.startswith(prefix + "/")


def _validate_hook_id(value: object) -> None:
    if not isinstance(value, str) or _HOOK_ID.fullmatch(value) is None:
        raise HookConfigurationError("Hook ID is invalid")


def _validate_tool_name(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 160
        or not value.isascii()
        or _contains_control(value)
    ):
        raise HookConfigurationError("Hook tool name is invalid")


def _validate_path_prefix(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_HOOK_PATH_CHARACTERS
        or _contains_control(value)
    ):
        raise HookConfigurationError("Hook path prefix is invalid")
    path = PurePosixPath(value)
    if (
        PureWindowsPath(value).is_absolute()
        or path.is_absolute()
        or ".." in path.parts
        or (value != "." and (not path.parts or any(part in {"", "."} for part in path.parts)))
        or str(path) != value
    ):
        raise HookConfigurationError("Hook path prefix must be a canonical workspace-relative path")


def _validate_canonical_tuple(values: object, label: str, validator) -> None:
    if not isinstance(values, tuple) or len(values) > MAX_HOOK_MATCHER_VALUES:
        raise HookConfigurationError(
            f"Hook {label} must contain at most {MAX_HOOK_MATCHER_VALUES} values"
        )
    for value in values:
        validator(value)
    if tuple(sorted(set(values), key=lambda item: str(item))) != values:
        raise HookConfigurationError(f"Hook {label} must be unique and canonical")


def _validate_enum(value: object, enum_type, label: str) -> None:
    if type(value) is not enum_type:
        raise HookConfigurationError(f"Hook {label} is invalid")


def _parse_enum(value: object, enum_type, label: str):
    if not isinstance(value, str):
        raise HookConfigurationError(f"{label} must be text")
    try:
        return enum_type(value)
    except ValueError:
        raise HookConfigurationError(f"{label} is unsupported: {value}") from None


def _parse_handler(value: object) -> HookHandlerSpec:
    try:
        return HookHandlerSpec.from_mapping(value)
    except ValueError as error:
        raise HookConfigurationError(str(error)) from None


def _required_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise HookConfigurationError(f"{label} must be text")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise HookConfigurationError(f"{label} must be an array")
    return value


def _text_tuple(value: object, label: str) -> tuple[str, ...]:
    items = _array(value, label)
    if not all(isinstance(item, str) for item in items):
        raise HookConfigurationError(f"{label} must contain only strings")
    return tuple(items)


def _check_revision(rule: HookRule, expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if type(expected_revision) is not int or expected_revision < 1:
        raise HookConfigurationError("expected Hook revision must be positive")
    if rule.revision != expected_revision:
        raise HookConfigurationError(
            f"Hook revision conflict: expected {expected_revision}, found {rule.revision}"
        )


def _validate_scope(scope: object) -> None:
    if scope not in {"user", "project"}:
        raise HookConfigurationError("Hook scope must be user or project")


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise HookConfigurationError(f"Hook configuration path must not be a symlink: {path}")
    if not path.exists():
        return None
    _ensure_safe_existing_chain(path)
    _ensure_safe_path(path, require_file=True)
    try:
        if path.stat().st_size > MAX_HOOK_CONFIGURATION_BYTES:
            raise HookConfigurationError(
                f"Hook configuration exceeds {MAX_HOOK_CONFIGURATION_BYTES} bytes: {path}"
            )
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream, parse_constant=_reject_json_constant)
    except HookConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise HookConfigurationError(
            f"Hook configuration is unreadable or invalid: {path}"
        ) from None
    if not isinstance(data, dict):
        raise HookConfigurationError(f"Hook configuration must be a JSON object: {path}")
    return data


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    parent = path.parent
    _ensure_safe_parent_chain(parent)
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    except OSError:
        raise HookConfigurationError(
            f"could not create Hook configuration directory: {parent}"
        ) from None
    _ensure_safe_path(parent, require_file=False)
    if path.exists() or path.is_symlink():
        _ensure_safe_path(path, require_file=True)
    payload = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_HOOK_CONFIGURATION_BYTES:
        raise HookConfigurationError(
            f"Hook configuration exceeds {MAX_HOOK_CONFIGURATION_BYTES} bytes: {path}"
        )
    descriptor: int | None = None
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=parent, prefix=f".{path.name}.", suffix=".tmp"
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
        raise HookConfigurationError(f"could not write Hook configuration: {path}") from None
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
    raise HookConfigurationError(f"Hook configuration contains invalid JSON constant: {value}")


def _open_lock_file(path: Path):
    _ensure_safe_parent_chain(path.parent)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    except OSError:
        raise HookConfigurationError(
            f"could not create Hook lock directory: {path.parent}"
        ) from None
    _ensure_safe_path(path.parent, require_file=False)
    if path.is_symlink():
        raise HookConfigurationError(f"Hook configuration lock must not be a symlink: {path}")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a+b")
    except OSError:
        raise HookConfigurationError(f"could not open Hook configuration lock: {path}") from None


def _lock_stream(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except OSError:
        raise HookConfigurationError("could not lock Hook configuration") from None


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
            raise HookConfigurationError(
                f"Hook configuration path must not contain a symlink: {current}"
            )


def _ensure_safe_parent_chain(path: Path) -> None:
    _ensure_safe_existing_chain(path)
    current = path
    while not current.exists() and not current.is_symlink():
        if current == current.parent:
            break
        current = current.parent
    if current.is_symlink():
        raise HookConfigurationError(
            f"Hook configuration path must not contain a symlink: {current}"
        )


def _ensure_safe_path(path: Path, *, require_file: bool) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise HookConfigurationError(f"Hook configuration path is unavailable: {path}") from None
    expected = stat.S_ISREG(info.st_mode) if require_file else stat.S_ISDIR(info.st_mode)
    if not expected or stat.S_ISLNK(info.st_mode):
        kind = "regular file" if require_file else "directory"
        raise HookConfigurationError(f"Hook configuration path must be a {kind}: {path}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
