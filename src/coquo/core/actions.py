"""Exact Host action identities for permission and approval boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Mapping
from uuid import UUID

from coquo.core.contracts import ToolArguments
from coquo.core.permissions import PermissionAction

ACTION_IDENTITY_VERSION = 1
CURRENT_ACTION_IDENTITY_VERSION = 2
_ACTION_IDENTITY_DOMAIN = b"coquo-action-identity-v1\0"
_ACTION_IDENTITY_V2_DOMAIN = b"coquo-action-identity-v2\0"
_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
MAX_ACTION_TEXT_LENGTH = 4096


class ActionPreconditionKind(StrEnum):
    """Closed kinds of trusted execution precondition identity."""

    NONE = "none"
    PATH_ABSENT = "path-absent"
    EXPECTED_STATE_SHA256 = "expected-state-sha256"
    EXPECTED_CONFIGURATION_SHA256 = "expected-configuration-sha256"
    WORKTREE_INTEGRATION = "worktree-integration"


@dataclass(frozen=True)
class ActionPrecondition:
    """One trusted execution precondition included in exact action identity."""

    kind: ActionPreconditionKind
    fingerprint: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ActionPreconditionKind:
            raise ValueError("action precondition kind is invalid")
        if self.kind in {
            ActionPreconditionKind.EXPECTED_STATE_SHA256,
            ActionPreconditionKind.EXPECTED_CONFIGURATION_SHA256,
            ActionPreconditionKind.WORKTREE_INTEGRATION,
        }:
            if type(self.fingerprint) is not str or _SHA256.fullmatch(self.fingerprint) is None:
                raise ValueError("SHA-256 precondition requires a lowercase SHA-256 digest")
        elif self.fingerprint is not None:
            raise ValueError("action precondition fingerprint must be null for this kind")

    @classmethod
    def none(cls) -> ActionPrecondition:
        return cls(ActionPreconditionKind.NONE)

    @classmethod
    def path_absent(cls) -> ActionPrecondition:
        return cls(ActionPreconditionKind.PATH_ABSENT)

    @classmethod
    def expected_state(cls, fingerprint: str) -> ActionPrecondition:
        return cls(ActionPreconditionKind.EXPECTED_STATE_SHA256, fingerprint)

    @classmethod
    def expected_configuration(cls, fingerprint: str) -> ActionPrecondition:
        return cls(ActionPreconditionKind.EXPECTED_CONFIGURATION_SHA256, fingerprint)

    @classmethod
    def worktree_integration(cls, fingerprint: str) -> ActionPrecondition:
        return cls(ActionPreconditionKind.WORKTREE_INTEGRATION, fingerprint)

    def as_mapping(self) -> dict[str, object]:
        return {"fingerprint": self.fingerprint, "kind": self.kind.value}

    @classmethod
    def from_mapping(cls, value: object) -> ActionPrecondition:
        mapping = _closed_mapping(value, {"fingerprint", "kind"}, "action precondition")
        try:
            kind = ActionPreconditionKind(mapping["kind"])
        except (TypeError, ValueError):
            raise ValueError("action precondition kind is invalid") from None
        return cls(kind=kind, fingerprint=mapping["fingerprint"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class ActionLease:
    """One non-recreatable prepared-turn lease bound to Session and runtime state."""

    session_id: str
    lease_id: str
    runtime_generation: int
    context_id: str

    def __post_init__(self) -> None:
        canonical_uuid4(self.session_id, "action lease session ID")
        canonical_uuid4(self.lease_id, "action lease ID")
        if type(self.runtime_generation) is not int or self.runtime_generation < 0:
            raise ValueError("action lease runtime generation must be non-negative")
        if type(self.context_id) is not str or _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("action lease context ID is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "context_id": self.context_id,
            "lease_id": self.lease_id,
            "runtime_generation": self.runtime_generation,
            "session_id": self.session_id,
        }

    @classmethod
    def from_mapping(cls, value: object) -> ActionLease:
        mapping = _closed_mapping(
            value,
            {"context_id", "lease_id", "runtime_generation", "session_id"},
            "action lease",
        )
        return cls(
            session_id=mapping["session_id"],  # type: ignore[arg-type]
            lease_id=mapping["lease_id"],  # type: ignore[arg-type]
            runtime_generation=mapping["runtime_generation"],  # type: ignore[arg-type]
            context_id=mapping["context_id"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class ActionIdentity:
    """Canonical identity of one exact model-requested Host action."""

    request_id: str
    tool_use_id: str
    tool_name: str
    arguments: ToolArguments
    action: PermissionAction
    workspace_fingerprint: str
    lease: ActionLease
    precondition: ActionPrecondition
    execution_scope: str = "authority-workspace"
    execution_root_fingerprint: str | None = None
    worktree_id: str | None = None
    version: int = ACTION_IDENTITY_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in {
            ACTION_IDENTITY_VERSION,
            CURRENT_ACTION_IDENTITY_VERSION,
        }:
            raise ValueError("unsupported action identity version")
        canonical_uuid4(self.request_id, "action request ID")
        _bounded_text(self.tool_use_id, "action tool_use ID")
        if type(self.tool_name) is not str or _TOOL_NAME.fullmatch(self.tool_name) is None:
            raise ValueError("action tool name is invalid")
        if type(self.arguments) is not ToolArguments:
            raise ValueError("action arguments are invalid")
        if type(self.action) is not PermissionAction:
            raise ValueError("permission action is invalid")
        if (
            type(self.workspace_fingerprint) is not str
            or _WORKSPACE_FINGERPRINT.fullmatch(self.workspace_fingerprint) is None
        ):
            raise ValueError("action workspace fingerprint is invalid")
        if type(self.lease) is not ActionLease:
            raise ValueError("action lease is invalid")
        if type(self.precondition) is not ActionPrecondition:
            raise ValueError("action precondition is invalid")
        if self.execution_scope not in {"authority-workspace", "team-worktree"}:
            raise ValueError("action execution scope is invalid")
        if self.version == ACTION_IDENTITY_VERSION and (
            self.execution_scope != "authority-workspace"
            or self.execution_root_fingerprint is not None
            or self.worktree_id is not None
        ):
            raise ValueError("legacy action identity cannot carry execution scope")
        if (
            self.execution_root_fingerprint is not None
            and _WORKSPACE_FINGERPRINT.fullmatch(self.execution_root_fingerprint) is None
        ):
            raise ValueError("action execution root fingerprint is invalid")
        if self.version == CURRENT_ACTION_IDENTITY_VERSION and (
            self.execution_scope == "authority-workspace"
            and self.execution_root_fingerprint is None
        ):
            raise ValueError("v2 authority action requires an execution root fingerprint")
        if self.execution_scope == "team-worktree":
            if self.version != CURRENT_ACTION_IDENTITY_VERSION:
                raise ValueError("team worktree scope requires action identity v2")
            if self.execution_root_fingerprint is None or self.worktree_id is None:
                raise ValueError("team worktree scope requires root and worktree identity")
            canonical_uuid4(self.worktree_id, "action worktree ID")
        elif self.worktree_id is not None:
            raise ValueError("authority action cannot carry a worktree ID")

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.as_mapping(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def digest(self) -> str:
        payload = self.canonical_json.encode("utf-8")
        domain = (
            _ACTION_IDENTITY_V2_DOMAIN
            if self.version == CURRENT_ACTION_IDENTITY_VERSION
            else _ACTION_IDENTITY_DOMAIN
        )
        return f"act-v{self.version}-{hashlib.sha256(domain + payload).hexdigest()}"

    def as_mapping(self) -> dict[str, object]:
        value = {
            "action": self.action.value,
            "arguments": self.arguments.as_mapping(),
            "arguments_version": self.arguments.version,
            "lease": self.lease.as_mapping(),
            "precondition": self.precondition.as_mapping(),
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "tool_use_id": self.tool_use_id,
            "version": self.version,
            "workspace_fingerprint": self.workspace_fingerprint,
        }
        if self.version == CURRENT_ACTION_IDENTITY_VERSION:
            value.update(
                {
                    "execution_root_fingerprint": self.execution_root_fingerprint,
                    "execution_scope": self.execution_scope,
                    "worktree_id": self.worktree_id,
                }
            )
        return value

    @classmethod
    def from_mapping(cls, value: object) -> ActionIdentity:
        if not isinstance(value, dict):
            raise ValueError("action identity must be a JSON object")
        version = value.get("version")
        legacy_fields = {
            "action",
            "arguments",
            "arguments_version",
            "lease",
            "precondition",
            "request_id",
            "tool_name",
            "tool_use_id",
            "version",
            "workspace_fingerprint",
        }
        v2_fields = legacy_fields | {
            "execution_root_fingerprint",
            "execution_scope",
            "worktree_id",
        }
        mapping = _closed_mapping(
            value,
            v2_fields if version == CURRENT_ACTION_IDENTITY_VERSION else legacy_fields,
            "action identity",
        )
        raw_arguments = mapping["arguments"]
        if not isinstance(raw_arguments, dict):
            raise ValueError("action arguments must be a JSON object")
        arguments_version = mapping["arguments_version"]
        if type(arguments_version) is not int:
            raise ValueError("action arguments version must be an integer")
        try:
            arguments = ToolArguments.from_mapping(
                raw_arguments,
                version=arguments_version,
            )
        except ValueError as error:
            raise ValueError(str(error)) from None
        try:
            action = PermissionAction(mapping["action"])
        except (TypeError, ValueError):
            raise ValueError("permission action is invalid") from None
        return cls(
            request_id=mapping["request_id"],  # type: ignore[arg-type]
            tool_use_id=mapping["tool_use_id"],  # type: ignore[arg-type]
            tool_name=mapping["tool_name"],  # type: ignore[arg-type]
            arguments=arguments,
            action=action,
            workspace_fingerprint=mapping["workspace_fingerprint"],  # type: ignore[arg-type]
            lease=ActionLease.from_mapping(mapping["lease"]),
            precondition=ActionPrecondition.from_mapping(mapping["precondition"]),
            version=mapping["version"],  # type: ignore[arg-type]
            execution_scope=mapping.get("execution_scope", "authority-workspace"),  # type: ignore[arg-type]
            execution_root_fingerprint=mapping.get("execution_root_fingerprint"),  # type: ignore[arg-type]
            worktree_id=mapping.get("worktree_id"),  # type: ignore[arg-type]
        )


def canonical_uuid4(value: object, label: str) -> str:
    """Validate one canonical lowercase UUID4 used by Host action contracts."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"{label} must be a canonical UUID4") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{label} must be a canonical UUID4")
    return value


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ACTION_TEXT_LENGTH:
        raise ValueError(f"{label} must be non-empty bounded text")
    return value


def _closed_mapping(value: object, fields: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    if set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value
