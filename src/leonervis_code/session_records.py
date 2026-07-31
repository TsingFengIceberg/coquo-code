"""Closed versioned records for durable Leonervis Code sessions.

This module owns only the typed transcript format and replay invariants. Filesystem
safety, locking, and durability live in :mod:`leonervis_code.session_store`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import TypeAlias, TypeVar
import unicodedata
from urllib.parse import urlparse
from uuid import UUID

from leonervis_code.core.actions import ActionIdentity, canonical_uuid4
from leonervis_code.core.compaction import (
    COMPACT_MIN_EFFECTIVE_TURNS,
    COMPACT_PROMPT_VERSION,
    COMPACT_RETAINED_TURNS,
    SUMMARY_CONTINUATION_VERSION,
    CompactionTrigger,
    EffectiveContextSummary,
    build_compact_prompt,
    summary_continuation_fingerprint,
)
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    ConversationItem,
    ConversationTurn,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.permissions import (
    ApprovalMode,
    PermissionDecision,
    PermissionGate,
    PermissionMode,
    PermissionReason,
    PermissionRequest,
    PermissionResult,
)
from leonervis_code.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
    validate_complete_history,
)
from leonervis_code.providers.usage import (
    ProviderInvocationKind,
    ProviderInvocationUsage,
    ProviderTokenUsage,
)

SCHEMA_VERSION = 1
SESSION_HEADER_SCHEMA_VERSION = 2
TURN_COMMITTED_LEGACY_SCHEMA_VERSION = 1
TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION = 2
TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION = 3
TURN_COMMITTED_BATCH_SCHEMA_VERSION = 4
TURN_COMMITTED_LEDGER_SCHEMA_VERSION = 5
TURN_COMMITTED_USAGE_SCHEMA_VERSION = 6
TURN_COMMITTED_NAMING_SCHEMA_VERSION = 7
TURN_COMMITTED_SCHEMA_VERSION = 8
TURN_FAILED_LEGACY_SCHEMA_VERSION = 1
TURN_FAILED_SCHEMA_VERSION = 2
SESSION_RESUMED_LEGACY_SCHEMA_VERSION = 1
SESSION_RESUMED_SCHEMA_VERSION = 2
CONTEXT_COMPACTED_LEGACY_SCHEMA_VERSION = 2
CONTEXT_COMPACTED_TRIGGER_SCHEMA_VERSION = 3
CONTEXT_COMPACTED_SCHEMA_VERSION = 4
WORKSPACE_FINGERPRINT_VERSION = "v1"
MAX_RECORD_BYTES = 1024 * 1024
MAX_RECORDS = 100_000
MAX_TEXT_BYTES = 512 * 1024
MAX_STRING_LENGTH = 4096
MAX_SESSION_NAME_CHARACTERS = 80
MAX_SESSION_NAME_BYTES = 256
MAX_GENERATED_SESSION_NAME_CHARACTERS = 48
MAX_GENERATED_SESSION_NAME_BYTES = 160

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_ACTION_DIGEST = re.compile(r"act-v1-[0-9a-f]{64}\Z")
_EnumT = TypeVar("_EnumT", bound=StrEnum)


class SessionRecordError(ValueError):
    """Raised when a session record or replay chain is invalid."""


class SessionNameSource(StrEnum):
    """Trusted origin of one current Session display name."""

    DEFAULT = "default"
    AUTO = "auto"
    MODEL = "model"
    FALLBACK = "fallback"
    MANUAL = "manual"


class SessionTitleFallbackReason(StrEnum):
    """Bounded Host reason for using a deterministic first-turn title."""

    PROVIDER_OUTPUT_LIMIT = "provider_output_limit"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_CANDIDATE = "invalid_candidate"
    DUPLICATE_TITLE = "duplicate_title"
    INVOCATION_BUDGET = "invocation_budget"


@dataclass(frozen=True)
class BindingSnapshot:
    """Redacted, immutable provider-route binding captured with durable events."""

    profile_id: str | None
    profile_revision: int | None
    profile_name: str | None
    profile_fingerprint: str | None
    provider_id: str
    protocol: str | None
    selected_model: str | None
    wire_model: str | None
    base_url: str | None
    base_url_source: str | None
    source: str
    credential_env: str | None
    max_output_tokens: int | None
    temperature: float | None
    generation: int
    adapter_version: str
    route_fingerprint: str

    def __post_init__(self) -> None:
        _optional_text(self.profile_id, "binding profile_id")
        if self.profile_revision is not None and (
            type(self.profile_revision) is not int or self.profile_revision < 0
        ):
            raise SessionRecordError(
                "binding profile_revision must be a non-negative integer or null"
            )
        _optional_text(self.profile_name, "binding profile_name")
        _optional_sha256(self.profile_fingerprint, "binding profile_fingerprint")
        _required_text(self.provider_id, "binding provider_id")
        _optional_text(self.protocol, "binding protocol")
        _optional_text(self.selected_model, "binding selected_model")
        _optional_text(self.wire_model, "binding wire_model")
        _validate_base_url(self.base_url)
        _optional_text(self.base_url_source, "binding base_url_source")
        _required_text(self.source, "binding source")
        if self.credential_env is not None:
            if (
                not isinstance(self.credential_env, str)
                or _ENVIRONMENT_NAME.fullmatch(self.credential_env) is None
            ):
                raise SessionRecordError(
                    "binding credential_env must be a portable environment name or null"
                )
        if self.max_output_tokens is not None and (
            type(self.max_output_tokens) is not int or self.max_output_tokens < 1
        ):
            raise SessionRecordError("binding max_output_tokens must be a positive integer or null")
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not 0.0 <= float(self.temperature) <= 2.0
        ):
            raise SessionRecordError("binding temperature must be between 0.0 and 2.0 or null")
        if type(self.generation) is not int or self.generation < 0:
            raise SessionRecordError("binding generation must be a non-negative integer")
        _required_text(self.adapter_version, "binding adapter_version")
        _required_sha256(self.route_fingerprint, "binding route_fingerprint")

    @classmethod
    def fake(
        cls,
        *,
        generation: int = 0,
        adapter_version: str = "fake-v1",
        source: str = "default",
    ) -> BindingSnapshot:
        """Build a complete redacted snapshot for the deterministic fake runtime."""
        fingerprint = hashlib.sha256(
            f"fake\0{adapter_version}\0{source}".encode("utf-8")
        ).hexdigest()
        return cls(
            profile_id=None,
            profile_revision=None,
            profile_name=None,
            profile_fingerprint=None,
            provider_id="fake",
            protocol=None,
            selected_model=None,
            wire_model=None,
            base_url=None,
            base_url_source=None,
            source=source,
            credential_env=None,
            max_output_tokens=None,
            temperature=None,
            generation=generation,
            adapter_version=adapter_version,
            route_fingerprint=fingerprint,
        )


@dataclass(frozen=True)
class SessionHeader:
    sequence: int
    session_id: str
    workspace: str
    workspace_fingerprint: str
    created_at: str
    binding: BindingSnapshot
    name: str | None = None
    record_type: str = "session_header"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class TurnCommitted:
    sequence: int
    committed_at: str
    binding: BindingSnapshot
    items: tuple[ConversationItem, ...]
    tool_ledger: ToolTurnLedger = ToolTurnLedger()
    provider_usage: tuple[ProviderInvocationUsage, ...] | None = ()
    session_name: str | None = None
    session_name_source: SessionNameSource | None = None
    session_title_fallback_reason: SessionTitleFallbackReason | None = None
    record_type: str = "turn_committed"
    schema_version: int = TURN_COMMITTED_SCHEMA_VERSION


@dataclass(frozen=True)
class RuntimeChanged:
    sequence: int
    occurred_at: str
    binding: BindingSnapshot
    reason: str
    record_type: str = "runtime_changed"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class SessionNamed:
    sequence: int
    occurred_at: str
    name: str
    source: SessionNameSource
    record_type: str = "session_named"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class SessionArchiveChanged:
    sequence: int
    occurred_at: str
    archived: bool
    record_type: str = "session_archive_changed"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class SessionPinChanged:
    sequence: int
    occurred_at: str
    pinned: bool
    record_type: str = "session_pin_changed"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class SessionForked:
    """Durable provenance for a Session materialized from complete parent turns."""

    sequence: int
    occurred_at: str
    source_session_id: str
    source_turn_count: int
    source_transcript_sha256: str
    record_type: str = "session_forked"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class TurnFailed:
    sequence: int
    occurred_at: str
    binding: BindingSnapshot
    failure_kind: str
    message: str
    provider_usage: tuple[ProviderInvocationUsage, ...] | None = ()
    record_type: str = "turn_failed"
    schema_version: int = TURN_FAILED_SCHEMA_VERSION


class ApprovalAuditOutcome(StrEnum):
    """Durable human resolution of one policy ``ask`` decision."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ActionAuthorization(StrEnum):
    """Durable evidence used to authorize the start of execution."""

    POLICY_ALLOW = "policy-allow"
    APPROVAL_GRANT = "approval-grant"


class ActionExecutionOutcome(StrEnum):
    """Known executor outcome recorded after the external effect returns."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class ActionAuditStatus(StrEnum):
    """Derived replay state for one exact action lifecycle."""

    REQUESTED = "requested"
    AWAITING_APPROVAL = "awaiting-approval"
    AUTHORIZED = "authorized"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    DENIED = "denied"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    OUTCOME_UNKNOWN = "outcome-unknown"


@dataclass(frozen=True)
class ActionRequested:
    sequence: int
    occurred_at: str
    binding: BindingSnapshot
    identity: ActionIdentity
    permission_mode: PermissionMode
    approval_mode: ApprovalMode
    record_type: str = "action_requested"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class PermissionDecided:
    sequence: int
    occurred_at: str
    action_request_id: str
    action_digest: str
    decision: PermissionDecision
    reason: PermissionReason
    record_type: str = "permission_decided"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ApprovalResolved:
    sequence: int
    occurred_at: str
    action_request_id: str
    action_digest: str
    outcome: ApprovalAuditOutcome
    grant_id: str | None
    record_type: str = "approval_resolved"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ActionExecutionStarted:
    sequence: int
    occurred_at: str
    action_request_id: str
    action_digest: str
    authorization: ActionAuthorization
    grant_id: str | None
    record_type: str = "action_execution_started"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ActionExecutionFinished:
    sequence: int
    occurred_at: str
    action_request_id: str
    action_digest: str
    outcome: ActionExecutionOutcome
    result_code: str
    message: str
    record_type: str = "action_execution_finished"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ActionAuditState:
    """One exact lifecycle reconstructed without entering model history."""

    identity: ActionIdentity
    permission_request: PermissionRequest
    status: ActionAuditStatus
    requested_sequence: int
    last_sequence: int
    permission_result: PermissionResult | None = None
    approval_outcome: ApprovalAuditOutcome | None = None
    grant_id: str | None = None
    execution_outcome: ActionExecutionOutcome | None = None
    result_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class SessionResumed:
    sequence: int
    occurred_at: str
    binding: BindingSnapshot | None = None
    record_type: str = "session_resumed"
    schema_version: int = SESSION_RESUMED_SCHEMA_VERSION


@dataclass(frozen=True)
class Recovery:
    sequence: int
    occurred_at: str
    truncated_bytes: int
    record_type: str = "recovery"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ContextCompacted:
    sequence: int
    occurred_at: str
    binding: BindingSnapshot
    source_context_id: str
    result_context_id: str
    source_full_turn_count: int
    source_effective_turn_count: int
    retained_from_full_turn: int
    previous_checkpoint_sequence: int | None
    summary: str
    compact_prompt_version: int
    compact_prompt_fingerprint: str
    continuation_version: int
    continuation_fingerprint: str
    effective_context_representation_version: int
    provider_usage: tuple[ProviderInvocationUsage, ...] | None = ()
    trigger: CompactionTrigger = CompactionTrigger.MANUAL
    high_water_percent: int | None = None
    record_type: str = "context_compacted"
    schema_version: int = CONTEXT_COMPACTED_SCHEMA_VERSION


@dataclass(frozen=True)
class CompactionFailed:
    sequence: int
    occurred_at: str
    binding: BindingSnapshot
    trigger: CompactionTrigger
    failure_kind: str
    message: str
    provider_usage: tuple[ProviderInvocationUsage, ...]
    record_type: str = "compaction_failed"
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class SessionClosed:
    sequence: int
    occurred_at: str
    reason: str
    record_type: str = "session_closed"
    schema_version: int = SCHEMA_VERSION


SessionRecord: TypeAlias = (
    SessionHeader
    | TurnCommitted
    | RuntimeChanged
    | SessionNamed
    | SessionArchiveChanged
    | SessionPinChanged
    | SessionForked
    | TurnFailed
    | ActionRequested
    | PermissionDecided
    | ApprovalResolved
    | ActionExecutionStarted
    | ActionExecutionFinished
    | SessionResumed
    | Recovery
    | ContextCompacted
    | CompactionFailed
    | SessionClosed
)
AuditRecord: TypeAlias = (
    RuntimeChanged
    | SessionNamed
    | SessionArchiveChanged
    | SessionPinChanged
    | SessionForked
    | TurnFailed
    | ActionRequested
    | PermissionDecided
    | ApprovalResolved
    | ActionExecutionStarted
    | ActionExecutionFinished
    | SessionResumed
    | Recovery
    | CompactionFailed
    | SessionClosed
)


@dataclass(frozen=True)
class ReplayState:
    """Validated transcript state; audit records are intentionally absent from history."""

    header: SessionHeader
    records: tuple[SessionRecord, ...]
    history: tuple[ConversationItem, ...]
    effective_history: tuple[ConversationItem, ...]
    effective_summary: EffectiveContextSummary | None
    effective_source: str
    latest_checkpoint: ContextCompacted | None
    action_audits: tuple[ActionAuditState, ...]
    turns: tuple[ConversationTurn, ...]
    latest_name: SessionNamed | None
    archived: bool
    pinned: bool
    forked_from: SessionForked | None
    binding: BindingSnapshot
    next_sequence: int
    closed: bool


def canonical_session_id(value: object) -> str:
    """Return a canonical lowercase UUID string or fail closed."""
    if not isinstance(value, str):
        raise SessionRecordError("session ID must be text")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise SessionRecordError("session ID must be a canonical UUID") from None
    if parsed.version != 4 or str(parsed) != value:
        raise SessionRecordError("session ID must be a canonical UUID4")
    return value


def canonical_session_name(value: object) -> str:
    """Normalize one bounded single-line display name or fail closed."""
    if not isinstance(value, str):
        raise SessionRecordError("session name must be text")
    if any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise SessionRecordError("session name must not contain control or format characters")
    normalized = " ".join(value.split())
    if not normalized:
        raise SessionRecordError("session name must not be empty")
    if len(normalized) > MAX_SESSION_NAME_CHARACTERS:
        raise SessionRecordError(f"session name exceeds {MAX_SESSION_NAME_CHARACTERS} characters")
    if len(normalized.encode("utf-8")) > MAX_SESSION_NAME_BYTES:
        raise SessionRecordError(f"session name exceeds {MAX_SESSION_NAME_BYTES} UTF-8 bytes")
    return normalized


def canonical_generated_session_name(value: object) -> str:
    """Validate the stricter title bound used by model and Host fallback naming."""
    normalized = canonical_session_name(value)
    if len(normalized) > MAX_GENERATED_SESSION_NAME_CHARACTERS:
        raise SessionRecordError(
            f"generated session name exceeds {MAX_GENERATED_SESSION_NAME_CHARACTERS} characters"
        )
    if len(normalized.encode("utf-8")) > MAX_GENERATED_SESSION_NAME_BYTES:
        raise SessionRecordError(
            f"generated session name exceeds {MAX_GENERATED_SESSION_NAME_BYTES} UTF-8 bytes"
        )
    return normalized


def workspace_fingerprint(workspace: Path) -> str:
    """Hash one canonical workspace identity using a domain-separated v1 SHA-256."""
    canonical = os.fsencode(str(Path(workspace).resolve(strict=True)))
    digest = hashlib.sha256(b"leonervis-code-workspace-v1\0" + canonical).hexdigest()
    return f"{WORKSPACE_FINGERPRINT_VERSION}-{digest}"


def encode_record(record: SessionRecord) -> bytes:
    """Encode one record as a compact canonical JSONL line."""
    data = _record_to_dict(record)
    try:
        payload = (
            json.dumps(
                data,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError):
        raise SessionRecordError("session record is not JSON encodable") from None
    if len(payload) > MAX_RECORD_BYTES:
        raise SessionRecordError(f"session record exceeds {MAX_RECORD_BYTES} bytes")
    return payload


def decode_record(payload: bytes) -> SessionRecord:
    """Decode one complete JSON record and reject unknown fields or types."""
    if not isinstance(payload, bytes):
        raise SessionRecordError("session record payload must be bytes")
    if not payload or len(payload) > MAX_RECORD_BYTES:
        raise SessionRecordError("session record is empty or oversized")
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or b"\n" in payload or b"\r" in payload:
        raise SessionRecordError("session record must occupy exactly one JSONL line")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise SessionRecordError("session record is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise SessionRecordError("session record must be a JSON object")
    return _record_from_dict(value)


def replay_records(
    records: tuple[SessionRecord, ...] | list[SessionRecord],
    *,
    expected_workspace: str | None = None,
    expected_workspace_fingerprint: str | None = None,
    expected_session_id: str | None = None,
    expected_file_name: str | None = None,
) -> ReplayState:
    """Validate sequence, conversation causality, and durable action lifecycles."""
    if not records:
        raise SessionRecordError("session transcript is missing its header")
    if len(records) > MAX_RECORDS:
        raise SessionRecordError(f"session transcript exceeds {MAX_RECORDS} records")
    header = records[0]
    if not isinstance(header, SessionHeader):
        raise SessionRecordError("session_header must be the first record")
    _validate_header(header)
    if expected_workspace is not None and header.workspace != expected_workspace:
        raise SessionRecordError("session workspace does not match the current workspace")
    if (
        expected_workspace_fingerprint is not None
        and header.workspace_fingerprint != expected_workspace_fingerprint
    ):
        raise SessionRecordError("session workspace fingerprint does not match")
    if expected_session_id is not None and header.session_id != expected_session_id:
        raise SessionRecordError("session ID does not match the selected transcript")
    if expected_file_name is not None and expected_file_name != f"{header.session_id}.jsonl":
        raise SessionRecordError("session transcript file name does not match its header")

    history: list[ConversationItem] = []
    effective_history: list[ConversationItem] = []
    effective_summary: EffectiveContextSummary | None = None
    effective_source = EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY
    latest_checkpoint: ContextCompacted | None = None
    turns: list[ConversationTurn] = []
    latest_name: SessionNamed | None = None
    archived = False
    pinned = False
    forked_from: SessionForked | None = None
    binding = header.binding
    closed = False
    seen_tool_ids: set[str] = set()
    action_states: dict[str, ActionAuditState] = {}
    action_order: list[str] = []
    grant_ids: set[str] = set()
    live_action_request_id: str | None = None
    validated: list[SessionRecord] = []
    for expected_sequence, record in enumerate(records):
        if record.sequence != expected_sequence:
            raise SessionRecordError(
                f"session sequence mismatch: expected {expected_sequence}, got {record.sequence}"
            )
        _validate_record_version(record)
        if expected_sequence and isinstance(record, SessionHeader):
            raise SessionRecordError("session_header may only be the first record")
        if closed and not isinstance(record, (Recovery, SessionResumed)):
            raise SessionRecordError(
                "session transcript requires session_resumed after session_closed"
            )
        if isinstance(record, TurnCommitted):
            _require_no_live_action(live_action_request_id, "turn_committed")
            _validate_timestamp(record.committed_at, "turn committed_at")
            _validate_turn(record.items, seen_tool_ids)
            _validate_turn_ledger(record)
            _validate_turn_session_name(record)
            if record.session_name is not None and (turns or latest_name is not None):
                raise SessionRecordError(
                    "turn_committed Session name is only valid on an unnamed first turn"
                )
            history.extend(record.items)
            effective_history.extend(record.items)
            turns.append(ConversationTurn(user=record.items[0], assistant=record.items[-1]))  # type: ignore[arg-type]
            binding = record.binding
        elif isinstance(record, RuntimeChanged):
            _require_no_live_action(live_action_request_id, "runtime_changed")
            _validate_timestamp(record.occurred_at, "runtime_changed occurred_at")
            _required_text(record.reason, "runtime_changed reason", allow_empty=True)
            binding = record.binding
        elif isinstance(record, SessionNamed):
            _require_no_live_action(live_action_request_id, "session_named")
            _validate_timestamp(record.occurred_at, "session_named occurred_at")
            if canonical_session_name(record.name) != record.name:
                raise SessionRecordError("session name must use canonical whitespace")
            if record.source not in {
                SessionNameSource.AUTO,
                SessionNameSource.MODEL,
                SessionNameSource.FALLBACK,
                SessionNameSource.MANUAL,
            }:
                raise SessionRecordError("session_named source is invalid")
            latest_name = record
        elif isinstance(record, SessionArchiveChanged):
            _require_no_live_action(live_action_request_id, "session_archive_changed")
            _validate_timestamp(record.occurred_at, "session_archive_changed occurred_at")
            if type(record.archived) is not bool:
                raise SessionRecordError("session_archive_changed archived must be boolean")
            archived = record.archived
        elif isinstance(record, SessionPinChanged):
            _require_no_live_action(live_action_request_id, "session_pin_changed")
            _validate_timestamp(record.occurred_at, "session_pin_changed occurred_at")
            if type(record.pinned) is not bool:
                raise SessionRecordError("session_pin_changed pinned must be boolean")
            pinned = record.pinned
        elif isinstance(record, SessionForked):
            _require_no_live_action(live_action_request_id, "session_forked")
            _validate_session_forked(record, header=header, turns=turns)
            if forked_from is not None:
                raise SessionRecordError("session_forked may appear only once")
            forked_from = record
        elif isinstance(record, TurnFailed):
            _validate_timestamp(record.occurred_at, "turn_failed occurred_at")
            _required_text(record.failure_kind, "turn_failed failure_kind")
            _required_text(record.message, "turn_failed message", allow_empty=True)
            if record.schema_version == TURN_FAILED_SCHEMA_VERSION:
                _validate_provider_usage(
                    record.provider_usage,
                    expected_kind=ProviderInvocationKind.TURN,
                    label="turn_failed",
                )
            elif record.provider_usage not in (None, ()):
                raise SessionRecordError("legacy turn_failed cannot contain provider usage")
            _interrupt_live_action(action_states, live_action_request_id, record.sequence)
            live_action_request_id = None
            binding = record.binding
        elif isinstance(record, ActionRequested):
            _require_no_live_action(live_action_request_id, "action_requested")
            _validate_action_requested(record, header=header, binding=binding)
            request_id = record.identity.request_id
            if request_id in action_states:
                raise SessionRecordError("action request ID is duplicated")
            request = PermissionRequest(
                record.permission_mode,
                record.approval_mode,
                record.identity.action,
            )
            action_states[request_id] = ActionAuditState(
                identity=record.identity,
                permission_request=request,
                status=ActionAuditStatus.REQUESTED,
                requested_sequence=record.sequence,
                last_sequence=record.sequence,
            )
            action_order.append(request_id)
            live_action_request_id = request_id
        elif isinstance(record, PermissionDecided):
            _validate_permission_decided_fields(record)
            state = _referenced_action(record, action_states)
            if state.status != ActionAuditStatus.REQUESTED:
                raise SessionRecordError("permission_decided is out of order")
            result = PermissionResult(record.decision, record.reason)
            if PermissionGate().evaluate(state.permission_request) != result:
                raise SessionRecordError("permission_decided does not match deterministic policy")
            status = {
                PermissionDecision.ALLOW: ActionAuditStatus.AUTHORIZED,
                PermissionDecision.ASK: ActionAuditStatus.AWAITING_APPROVAL,
                PermissionDecision.DENY: ActionAuditStatus.DENIED,
            }[result.decision]
            action_states[record.action_request_id] = replace(
                state,
                status=status,
                permission_result=result,
                last_sequence=record.sequence,
            )
            if status == ActionAuditStatus.DENIED:
                live_action_request_id = None
        elif isinstance(record, ApprovalResolved):
            _validate_approval_resolved_fields(record)
            state = _referenced_action(record, action_states)
            if state.status != ActionAuditStatus.AWAITING_APPROVAL:
                raise SessionRecordError("approval_resolved is out of order")
            if record.outcome == ApprovalAuditOutcome.ACCEPTED:
                assert record.grant_id is not None
                if record.grant_id in grant_ids:
                    raise SessionRecordError("approval grant ID is duplicated")
                grant_ids.add(record.grant_id)
                status = ActionAuditStatus.APPROVED
            elif record.outcome == ApprovalAuditOutcome.REJECTED:
                status = ActionAuditStatus.REJECTED
            else:
                status = ActionAuditStatus.CANCELLED
            action_states[record.action_request_id] = replace(
                state,
                status=status,
                approval_outcome=record.outcome,
                grant_id=record.grant_id,
                last_sequence=record.sequence,
            )
            if status in {ActionAuditStatus.REJECTED, ActionAuditStatus.CANCELLED}:
                live_action_request_id = None
        elif isinstance(record, ActionExecutionStarted):
            _validate_action_execution_started_fields(record)
            state = _referenced_action(record, action_states)
            if state.identity.lease.runtime_generation != binding.generation:
                raise SessionRecordError("action execution lease is stale for the current runtime")
            if state.status == ActionAuditStatus.AUTHORIZED:
                if (
                    record.authorization != ActionAuthorization.POLICY_ALLOW
                    or record.grant_id is not None
                ):
                    raise SessionRecordError("allowed action requires policy authorization")
            elif state.status == ActionAuditStatus.APPROVED:
                if (
                    record.authorization != ActionAuthorization.APPROVAL_GRANT
                    or record.grant_id != state.grant_id
                ):
                    raise SessionRecordError("approved action requires its exact approval grant")
            else:
                raise SessionRecordError("action_execution_started is out of order")
            action_states[record.action_request_id] = replace(
                state,
                status=ActionAuditStatus.EXECUTING,
                last_sequence=record.sequence,
            )
        elif isinstance(record, ActionExecutionFinished):
            _validate_action_execution_finished_fields(record)
            state = _referenced_action(record, action_states)
            if state.status != ActionAuditStatus.EXECUTING:
                raise SessionRecordError("action_execution_finished is out of order")
            status = {
                ActionExecutionOutcome.SUCCEEDED: ActionAuditStatus.SUCCEEDED,
                ActionExecutionOutcome.FAILED: ActionAuditStatus.FAILED,
                ActionExecutionOutcome.PARTIAL: ActionAuditStatus.PARTIAL,
            }[record.outcome]
            action_states[record.action_request_id] = replace(
                state,
                status=status,
                execution_outcome=record.outcome,
                result_code=record.result_code,
                message=record.message,
                last_sequence=record.sequence,
            )
            live_action_request_id = None
        elif isinstance(record, SessionResumed):
            _validate_timestamp(record.occurred_at, "session_resumed occurred_at")
            _interrupt_live_action(action_states, live_action_request_id, record.sequence)
            live_action_request_id = None
            if record.schema_version == SESSION_RESUMED_SCHEMA_VERSION:
                assert record.binding is not None
                binding = record.binding
            closed = False
        elif isinstance(record, Recovery):
            _validate_timestamp(record.occurred_at, "recovery occurred_at")
            if type(record.truncated_bytes) is not int or record.truncated_bytes < 1:
                raise SessionRecordError("recovery truncated_bytes must be a positive integer")
        elif isinstance(record, ContextCompacted):
            _require_no_live_action(live_action_request_id, "context_compacted")
            _validate_context_compacted(
                record,
                full_history=tuple(history),
                effective_history=tuple(effective_history),
                latest_checkpoint=latest_checkpoint,
            )
            full_turns = validate_complete_history(tuple(history)).complete_turns
            retained_turns = full_turns[record.retained_from_full_turn :]
            effective_history = [item for turn in retained_turns for item in turn.items]
            effective_summary = EffectiveContextSummary(
                record.summary,
                continuation_version=record.continuation_version,
                continuation_fingerprint=record.continuation_fingerprint,
            )
            effective_source = EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT
            latest_checkpoint = record
            binding = record.binding
        elif isinstance(record, CompactionFailed):
            _require_no_live_action(live_action_request_id, "compaction_failed")
            _validate_compaction_failed(record)
            if record.binding != binding:
                raise SessionRecordError("compaction_failed binding does not match current runtime")
        elif isinstance(record, SessionClosed):
            _require_no_live_action(live_action_request_id, "session_closed")
            _validate_timestamp(record.occurred_at, "session_closed occurred_at")
            _required_text(record.reason, "session_closed reason", allow_empty=True)
            closed = True
        elif not isinstance(record, SessionHeader):
            raise SessionRecordError("unsupported session record")
        validated.append(record)
    return ReplayState(
        header=header,
        records=tuple(validated),
        history=tuple(history),
        effective_history=tuple(effective_history),
        effective_summary=effective_summary,
        effective_source=effective_source,
        latest_checkpoint=latest_checkpoint,
        action_audits=tuple(action_states[request_id] for request_id in action_order),
        turns=tuple(turns),
        latest_name=latest_name,
        archived=archived,
        pinned=pinned,
        forked_from=forked_from,
        binding=binding,
        next_sequence=len(validated),
        closed=closed,
    )


def _require_no_live_action(
    live_action_request_id: str | None,
    record_type: str,
) -> None:
    if live_action_request_id is not None:
        raise SessionRecordError(f"{record_type} cannot cross an unresolved action lifecycle")


def _interrupt_live_action(
    action_states: dict[str, ActionAuditState],
    live_action_request_id: str | None,
    sequence: int,
) -> None:
    if live_action_request_id is None:
        return
    state = action_states[live_action_request_id]
    status = (
        ActionAuditStatus.OUTCOME_UNKNOWN
        if state.status == ActionAuditStatus.EXECUTING
        else ActionAuditStatus.ABANDONED
    )
    action_states[live_action_request_id] = replace(
        state,
        status=status,
        last_sequence=sequence,
    )


def _validate_action_requested(
    record: ActionRequested,
    *,
    header: SessionHeader,
    binding: BindingSnapshot,
) -> None:
    _validate_action_requested_fields(record)
    if record.binding != binding:
        raise SessionRecordError("action_requested binding does not match current runtime")
    if record.identity.lease.session_id != header.session_id:
        raise SessionRecordError("action identity Session does not match transcript")
    if record.identity.workspace_fingerprint != header.workspace_fingerprint:
        raise SessionRecordError("action identity workspace does not match transcript")
    if record.identity.lease.runtime_generation != binding.generation:
        raise SessionRecordError("action identity runtime generation is stale")


def _validate_action_requested_fields(record: ActionRequested) -> None:
    _validate_timestamp(record.occurred_at, "action_requested occurred_at")
    record.binding.__post_init__()
    if type(record.identity) is not ActionIdentity:
        raise SessionRecordError("action_requested identity is invalid")
    try:
        record.identity.__post_init__()
    except ValueError as error:
        raise SessionRecordError(str(error)) from None
    if type(record.permission_mode) is not PermissionMode:
        raise SessionRecordError("action_requested permission mode is invalid")
    if type(record.approval_mode) is not ApprovalMode:
        raise SessionRecordError("action_requested approval mode is invalid")


def _validate_permission_decided_fields(record: PermissionDecided) -> None:
    _validate_action_reference_fields(record)
    if type(record.decision) is not PermissionDecision:
        raise SessionRecordError("permission_decided decision is invalid")
    if type(record.reason) is not PermissionReason:
        raise SessionRecordError("permission_decided reason is invalid")
    try:
        PermissionResult(record.decision, record.reason)
    except ValueError as error:
        raise SessionRecordError(str(error)) from None


def _validate_approval_resolved_fields(record: ApprovalResolved) -> None:
    _validate_action_reference_fields(record)
    if type(record.outcome) is not ApprovalAuditOutcome:
        raise SessionRecordError("approval_resolved outcome is invalid")
    if record.outcome == ApprovalAuditOutcome.ACCEPTED:
        if record.grant_id is None:
            raise SessionRecordError("accepted approval requires a grant ID")
        _canonical_action_uuid(record.grant_id, "approval grant ID")
    elif record.grant_id is not None:
        raise SessionRecordError("rejected or cancelled approval must not contain a grant ID")


def _validate_action_execution_started_fields(record: ActionExecutionStarted) -> None:
    _validate_action_reference_fields(record)
    if type(record.authorization) is not ActionAuthorization:
        raise SessionRecordError("action execution authorization is invalid")
    if record.grant_id is not None:
        _canonical_action_uuid(record.grant_id, "approval grant ID")


def _validate_action_execution_finished_fields(record: ActionExecutionFinished) -> None:
    _validate_action_reference_fields(record)
    if type(record.outcome) is not ActionExecutionOutcome:
        raise SessionRecordError("action execution outcome is invalid")
    _required_text(record.result_code, "action execution result_code")
    _required_text(record.message, "action execution message", allow_empty=True)


def _validate_action_reference_fields(
    record: PermissionDecided | ApprovalResolved | ActionExecutionStarted | ActionExecutionFinished,
) -> None:
    _validate_timestamp(record.occurred_at, f"{record.record_type} occurred_at")
    _canonical_action_uuid(record.action_request_id, "action request ID")
    if (
        type(record.action_digest) is not str
        or _ACTION_DIGEST.fullmatch(record.action_digest) is None
    ):
        raise SessionRecordError("action digest is invalid")


def _referenced_action(
    record: PermissionDecided | ApprovalResolved | ActionExecutionStarted | ActionExecutionFinished,
    action_states: dict[str, ActionAuditState],
) -> ActionAuditState:
    state = action_states.get(record.action_request_id)
    if state is None:
        raise SessionRecordError(f"{record.record_type} references an unknown action")
    if state.identity.digest != record.action_digest:
        raise SessionRecordError(f"{record.record_type} action digest does not match")
    return state


def _canonical_action_uuid(value: object, label: str) -> str:
    try:
        return canonical_uuid4(value, label)
    except ValueError as error:
        raise SessionRecordError(str(error)) from None


def _record_to_dict(record: SessionRecord) -> dict[str, object]:
    _validate_record_version(record)
    common: dict[str, object] = {
        "record_type": record.record_type,
        "schema_version": record.schema_version,
        "sequence": record.sequence,
    }
    if isinstance(record, SessionHeader):
        _validate_header(record)
        common.update(
            session_id=record.session_id,
            workspace=record.workspace,
            workspace_fingerprint=record.workspace_fingerprint,
            created_at=record.created_at,
            binding=_binding_to_dict(record.binding),
        )
        if record.schema_version == SESSION_HEADER_SCHEMA_VERSION:
            common["name"] = record.name
    elif isinstance(record, TurnCommitted):
        _validate_timestamp(record.committed_at, "turn committed_at")
        _validate_turn(record.items, set())
        _validate_turn_ledger(record)
        _validate_turn_session_name(record)
        common.update(
            committed_at=record.committed_at,
            binding=_binding_to_dict(record.binding),
            items=[
                _item_to_dict(item, schema_version=record.schema_version) for item in record.items
            ],
        )
        if record.schema_version >= TURN_COMMITTED_LEDGER_SCHEMA_VERSION:
            common["tool_ledger"] = _tool_ledger_to_dict(record.tool_ledger)
        if record.schema_version >= TURN_COMMITTED_USAGE_SCHEMA_VERSION:
            common["provider_usage"] = _provider_usage_to_value(
                record.provider_usage,
                expected_kind=ProviderInvocationKind.TURN,
                label="turn_committed",
            )
        if record.schema_version >= TURN_COMMITTED_NAMING_SCHEMA_VERSION:
            common["session_name"] = record.session_name
            common["session_name_source"] = (
                record.session_name_source.value if record.session_name_source is not None else None
            )
        if record.schema_version == TURN_COMMITTED_SCHEMA_VERSION:
            common["session_title_fallback_reason"] = (
                record.session_title_fallback_reason.value
                if record.session_title_fallback_reason is not None
                else None
            )
    elif isinstance(record, RuntimeChanged):
        _validate_timestamp(record.occurred_at, "runtime_changed occurred_at")
        _required_text(record.reason, "runtime_changed reason", allow_empty=True)
        common.update(
            occurred_at=record.occurred_at,
            binding=_binding_to_dict(record.binding),
            reason=record.reason,
        )
    elif isinstance(record, SessionNamed):
        _validate_timestamp(record.occurred_at, "session_named occurred_at")
        if canonical_session_name(record.name) != record.name:
            raise SessionRecordError("session name must use canonical whitespace")
        if record.source not in {
            SessionNameSource.AUTO,
            SessionNameSource.MODEL,
            SessionNameSource.FALLBACK,
            SessionNameSource.MANUAL,
        }:
            raise SessionRecordError("session_named source is invalid")
        common.update(
            occurred_at=record.occurred_at,
            name=record.name,
            source=record.source.value,
        )
    elif isinstance(record, SessionArchiveChanged):
        _validate_timestamp(record.occurred_at, "session_archive_changed occurred_at")
        if type(record.archived) is not bool:
            raise SessionRecordError("session_archive_changed archived must be boolean")
        common.update(occurred_at=record.occurred_at, archived=record.archived)
    elif isinstance(record, SessionPinChanged):
        _validate_timestamp(record.occurred_at, "session_pin_changed occurred_at")
        if type(record.pinned) is not bool:
            raise SessionRecordError("session_pin_changed pinned must be boolean")
        common.update(occurred_at=record.occurred_at, pinned=record.pinned)
    elif isinstance(record, SessionForked):
        _validate_timestamp(record.occurred_at, "session_forked occurred_at")
        canonical_session_id(record.source_session_id)
        if type(record.source_turn_count) is not int or record.source_turn_count < 1:
            raise SessionRecordError("session_forked source_turn_count must be positive")
        if _SHA256.fullmatch(record.source_transcript_sha256) is None:
            raise SessionRecordError("session_forked source transcript digest is invalid")
        common.update(
            occurred_at=record.occurred_at,
            source_session_id=record.source_session_id,
            source_turn_count=record.source_turn_count,
            source_transcript_sha256=record.source_transcript_sha256,
        )
    elif isinstance(record, TurnFailed):
        _validate_timestamp(record.occurred_at, "turn_failed occurred_at")
        _required_text(record.failure_kind, "turn_failed failure_kind")
        _required_text(record.message, "turn_failed message", allow_empty=True)
        common.update(
            occurred_at=record.occurred_at,
            binding=_binding_to_dict(record.binding),
            failure_kind=record.failure_kind,
            message=record.message,
        )
        if record.schema_version == TURN_FAILED_SCHEMA_VERSION:
            common["provider_usage"] = _provider_usage_to_value(
                record.provider_usage,
                expected_kind=ProviderInvocationKind.TURN,
                label="turn_failed",
            )
    elif isinstance(record, ActionRequested):
        _validate_action_requested_fields(record)
        common.update(
            occurred_at=record.occurred_at,
            binding=_binding_to_dict(record.binding),
            identity=record.identity.as_mapping(),
            permission_mode=record.permission_mode.value,
            approval_mode=record.approval_mode.value,
        )
    elif isinstance(record, PermissionDecided):
        _validate_permission_decided_fields(record)
        common.update(
            occurred_at=record.occurred_at,
            action_request_id=record.action_request_id,
            action_digest=record.action_digest,
            decision=record.decision.value,
            reason=record.reason.value,
        )
    elif isinstance(record, ApprovalResolved):
        _validate_approval_resolved_fields(record)
        common.update(
            occurred_at=record.occurred_at,
            action_request_id=record.action_request_id,
            action_digest=record.action_digest,
            outcome=record.outcome.value,
            grant_id=record.grant_id,
        )
    elif isinstance(record, ActionExecutionStarted):
        _validate_action_execution_started_fields(record)
        common.update(
            occurred_at=record.occurred_at,
            action_request_id=record.action_request_id,
            action_digest=record.action_digest,
            authorization=record.authorization.value,
            grant_id=record.grant_id,
        )
    elif isinstance(record, ActionExecutionFinished):
        _validate_action_execution_finished_fields(record)
        common.update(
            occurred_at=record.occurred_at,
            action_request_id=record.action_request_id,
            action_digest=record.action_digest,
            outcome=record.outcome.value,
            result_code=record.result_code,
            message=record.message,
        )
    elif isinstance(record, SessionResumed):
        _validate_timestamp(record.occurred_at, "session_resumed occurred_at")
        common["occurred_at"] = record.occurred_at
        if record.schema_version == SESSION_RESUMED_SCHEMA_VERSION:
            if record.binding is None:
                raise SessionRecordError("current session_resumed requires a runtime binding")
            common["binding"] = _binding_to_dict(record.binding)
    elif isinstance(record, Recovery):
        _validate_timestamp(record.occurred_at, "recovery occurred_at")
        if type(record.truncated_bytes) is not int or record.truncated_bytes < 1:
            raise SessionRecordError("recovery truncated_bytes must be a positive integer")
        common.update(occurred_at=record.occurred_at, truncated_bytes=record.truncated_bytes)
    elif isinstance(record, ContextCompacted):
        _validate_context_compacted_fields(record)
        common.update(
            occurred_at=record.occurred_at,
            binding=_binding_to_dict(record.binding),
            source_context_id=record.source_context_id,
            result_context_id=record.result_context_id,
            source_full_turn_count=record.source_full_turn_count,
            source_effective_turn_count=record.source_effective_turn_count,
            retained_from_full_turn=record.retained_from_full_turn,
            previous_checkpoint_sequence=record.previous_checkpoint_sequence,
            summary=record.summary,
            compact_prompt_version=record.compact_prompt_version,
            compact_prompt_fingerprint=record.compact_prompt_fingerprint,
            continuation_version=record.continuation_version,
            continuation_fingerprint=record.continuation_fingerprint,
            effective_context_representation_version=record.effective_context_representation_version,
        )
        if record.schema_version >= CONTEXT_COMPACTED_TRIGGER_SCHEMA_VERSION:
            common.update(
                trigger=record.trigger.value,
                high_water_percent=record.high_water_percent,
            )
        if record.schema_version == CONTEXT_COMPACTED_SCHEMA_VERSION:
            common["provider_usage"] = _provider_usage_to_value(
                record.provider_usage,
                expected_kind=ProviderInvocationKind.COMPACTION,
                label="context_compacted",
            )
    elif isinstance(record, CompactionFailed):
        _validate_compaction_failed(record)
        common.update(
            occurred_at=record.occurred_at,
            binding=_binding_to_dict(record.binding),
            trigger=record.trigger.value,
            failure_kind=record.failure_kind,
            message=record.message,
            provider_usage=_provider_usage_to_value(
                record.provider_usage,
                expected_kind=ProviderInvocationKind.COMPACTION,
                label="compaction_failed",
            ),
        )
    elif isinstance(record, SessionClosed):
        _validate_timestamp(record.occurred_at, "session_closed occurred_at")
        _required_text(record.reason, "session_closed reason", allow_empty=True)
        common.update(occurred_at=record.occurred_at, reason=record.reason)
    else:
        raise SessionRecordError("unsupported session record")
    return common


def _record_from_dict(value: dict[str, object]) -> SessionRecord:
    record_type = _required_field_text(value, "record_type", "session record")
    version = value.get("schema_version")
    if record_type == "session_header":
        allowed_versions = {SCHEMA_VERSION, SESSION_HEADER_SCHEMA_VERSION}
    elif record_type == "context_compacted":
        allowed_versions = {
            CONTEXT_COMPACTED_LEGACY_SCHEMA_VERSION,
            CONTEXT_COMPACTED_TRIGGER_SCHEMA_VERSION,
            CONTEXT_COMPACTED_SCHEMA_VERSION,
        }
    elif record_type == "turn_committed":
        allowed_versions = {
            TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
            TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
            TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_LEDGER_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }
    elif record_type == "turn_failed":
        allowed_versions = {TURN_FAILED_LEGACY_SCHEMA_VERSION, TURN_FAILED_SCHEMA_VERSION}
    elif record_type == "session_resumed":
        allowed_versions = {
            SESSION_RESUMED_LEGACY_SCHEMA_VERSION,
            SESSION_RESUMED_SCHEMA_VERSION,
        }
    else:
        allowed_versions = {SCHEMA_VERSION}
    if type(version) is not int or version not in allowed_versions:
        raise SessionRecordError("unsupported session record schema version")
    sequence = value.get("sequence")
    if type(sequence) is not int or sequence < 0:
        raise SessionRecordError("session record sequence must be a non-negative integer")

    if record_type == "session_header":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "session_id",
            "workspace",
            "workspace_fingerprint",
            "created_at",
            "binding",
        }
        if version == SESSION_HEADER_SCHEMA_VERSION:
            fields.add("name")
        _closed_fields(
            value,
            fields,
            record_type,
        )
        record = SessionHeader(
            sequence=sequence,
            session_id=_required_field_text(value, "session_id", record_type),
            workspace=_required_field_text(value, "workspace", record_type),
            workspace_fingerprint=_required_field_text(value, "workspace_fingerprint", record_type),
            created_at=_required_field_text(value, "created_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            name=(
                canonical_session_name(value.get("name"))
                if version == SESSION_HEADER_SCHEMA_VERSION
                else None
            ),
            schema_version=version,
        )
        _validate_header(record)
        return record
    if record_type == "turn_committed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "committed_at",
            "binding",
            "items",
        }
        if version >= TURN_COMMITTED_LEDGER_SCHEMA_VERSION:
            fields.add("tool_ledger")
        if version >= TURN_COMMITTED_USAGE_SCHEMA_VERSION:
            fields.add("provider_usage")
        if version >= TURN_COMMITTED_NAMING_SCHEMA_VERSION:
            fields.update({"session_name", "session_name_source"})
        if version == TURN_COMMITTED_SCHEMA_VERSION:
            fields.add("session_title_fallback_reason")
        _closed_fields(
            value,
            fields,
            record_type,
        )
        raw_items = value.get("items")
        if not isinstance(raw_items, list):
            raise SessionRecordError("turn_committed items must be an array")
        items = tuple(_item_from_value(item, schema_version=version) for item in raw_items)
        record = TurnCommitted(
            sequence=sequence,
            committed_at=_required_field_text(value, "committed_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            items=items,
            tool_ledger=(
                _tool_ledger_from_value(value.get("tool_ledger"))
                if version >= TURN_COMMITTED_LEDGER_SCHEMA_VERSION
                else ToolTurnLedger()
            ),
            provider_usage=(
                _provider_usage_from_value(
                    value.get("provider_usage"),
                    expected_kind=ProviderInvocationKind.TURN,
                    label=record_type,
                )
                if version >= TURN_COMMITTED_USAGE_SCHEMA_VERSION
                else ()
            ),
            session_name=(
                _nullable_field_text(value, "session_name", record_type)
                if version >= TURN_COMMITTED_NAMING_SCHEMA_VERSION
                else None
            ),
            session_name_source=(
                _session_name_source_from_value(value.get("session_name_source"))
                if version >= TURN_COMMITTED_NAMING_SCHEMA_VERSION
                else None
            ),
            session_title_fallback_reason=(
                _session_title_fallback_reason_from_value(
                    value.get("session_title_fallback_reason")
                )
                if version == TURN_COMMITTED_SCHEMA_VERSION
                else None
            ),
            schema_version=version,
        )
        _validate_timestamp(record.committed_at, "turn committed_at")
        _validate_turn(record.items, set())
        _validate_turn_ledger(record)
        _validate_turn_session_name(record)
        return record
    if record_type == "runtime_changed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "binding",
            "reason",
        }
        _closed_fields(value, fields, record_type)
        return RuntimeChanged(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            reason=_required_field_text(value, "reason", record_type, allow_empty=True),
        )
    if record_type == "session_named":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "name",
            "source",
        }
        _closed_fields(value, fields, record_type)
        try:
            source = SessionNameSource(_required_field_text(value, "source", record_type))
        except ValueError:
            raise SessionRecordError("session_named source is invalid") from None
        if source not in {
            SessionNameSource.AUTO,
            SessionNameSource.MODEL,
            SessionNameSource.FALLBACK,
            SessionNameSource.MANUAL,
        }:
            raise SessionRecordError("session_named source is invalid")
        return SessionNamed(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            name=canonical_session_name(value.get("name")),
            source=source,
        )
    if record_type == "session_archive_changed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "archived",
        }
        _closed_fields(value, fields, record_type)
        archived = value.get("archived")
        if type(archived) is not bool:
            raise SessionRecordError("session_archive_changed archived must be boolean")
        return SessionArchiveChanged(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            archived=archived,
        )
    if record_type == "session_pin_changed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "pinned",
        }
        _closed_fields(value, fields, record_type)
        pinned = value.get("pinned")
        if type(pinned) is not bool:
            raise SessionRecordError("session_pin_changed pinned must be boolean")
        return SessionPinChanged(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            pinned=pinned,
        )
    if record_type == "session_forked":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "source_session_id",
            "source_turn_count",
            "source_transcript_sha256",
        }
        _closed_fields(value, fields, record_type)
        source_turn_count = value.get("source_turn_count")
        if type(source_turn_count) is not int or source_turn_count < 1:
            raise SessionRecordError("session_forked source_turn_count must be positive")
        source_digest = _required_field_text(value, "source_transcript_sha256", record_type)
        if _SHA256.fullmatch(source_digest) is None:
            raise SessionRecordError("session_forked source transcript digest is invalid")
        return SessionForked(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            source_session_id=canonical_session_id(value.get("source_session_id")),
            source_turn_count=source_turn_count,
            source_transcript_sha256=source_digest,
        )
    if record_type == "turn_failed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "binding",
            "failure_kind",
            "message",
        }
        if version == TURN_FAILED_SCHEMA_VERSION:
            fields.add("provider_usage")
        _closed_fields(value, fields, record_type)
        return TurnFailed(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            failure_kind=_required_field_text(value, "failure_kind", record_type),
            message=_required_field_text(value, "message", record_type, allow_empty=True),
            provider_usage=(
                _provider_usage_from_value(
                    value.get("provider_usage"),
                    expected_kind=ProviderInvocationKind.TURN,
                    label=record_type,
                )
                if version == TURN_FAILED_SCHEMA_VERSION
                else ()
            ),
            schema_version=version,
        )
    if record_type == "action_requested":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "binding",
            "identity",
            "permission_mode",
            "approval_mode",
        }
        _closed_fields(value, fields, record_type)
        try:
            identity = ActionIdentity.from_mapping(value.get("identity"))
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
        record = ActionRequested(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            identity=identity,
            permission_mode=_enum_field(value, "permission_mode", record_type, PermissionMode),
            approval_mode=_enum_field(value, "approval_mode", record_type, ApprovalMode),
        )
        _validate_action_requested_fields(record)
        return record
    if record_type == "permission_decided":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "action_request_id",
            "action_digest",
            "decision",
            "reason",
        }
        _closed_fields(value, fields, record_type)
        record = PermissionDecided(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            action_request_id=_required_field_text(value, "action_request_id", record_type),
            action_digest=_required_field_text(value, "action_digest", record_type),
            decision=_enum_field(value, "decision", record_type, PermissionDecision),
            reason=_enum_field(value, "reason", record_type, PermissionReason),
        )
        _validate_permission_decided_fields(record)
        return record
    if record_type == "approval_resolved":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "action_request_id",
            "action_digest",
            "outcome",
            "grant_id",
        }
        _closed_fields(value, fields, record_type)
        record = ApprovalResolved(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            action_request_id=_required_field_text(value, "action_request_id", record_type),
            action_digest=_required_field_text(value, "action_digest", record_type),
            outcome=_enum_field(value, "outcome", record_type, ApprovalAuditOutcome),
            grant_id=_nullable_field_text(value, "grant_id", record_type),
        )
        _validate_approval_resolved_fields(record)
        return record
    if record_type == "action_execution_started":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "action_request_id",
            "action_digest",
            "authorization",
            "grant_id",
        }
        _closed_fields(value, fields, record_type)
        record = ActionExecutionStarted(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            action_request_id=_required_field_text(value, "action_request_id", record_type),
            action_digest=_required_field_text(value, "action_digest", record_type),
            authorization=_enum_field(value, "authorization", record_type, ActionAuthorization),
            grant_id=_nullable_field_text(value, "grant_id", record_type),
        )
        _validate_action_execution_started_fields(record)
        return record
    if record_type == "action_execution_finished":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "action_request_id",
            "action_digest",
            "outcome",
            "result_code",
            "message",
        }
        _closed_fields(value, fields, record_type)
        record = ActionExecutionFinished(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            action_request_id=_required_field_text(value, "action_request_id", record_type),
            action_digest=_required_field_text(value, "action_digest", record_type),
            outcome=_enum_field(value, "outcome", record_type, ActionExecutionOutcome),
            result_code=_required_field_text(value, "result_code", record_type),
            message=_required_field_text(value, "message", record_type, allow_empty=True),
        )
        _validate_action_execution_finished_fields(record)
        return record
    if record_type == "context_compacted":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "binding",
            "source_context_id",
            "result_context_id",
            "source_full_turn_count",
            "source_effective_turn_count",
            "retained_from_full_turn",
            "previous_checkpoint_sequence",
            "summary",
            "compact_prompt_version",
            "compact_prompt_fingerprint",
            "continuation_version",
            "continuation_fingerprint",
            "effective_context_representation_version",
        }
        if version >= CONTEXT_COMPACTED_TRIGGER_SCHEMA_VERSION:
            fields |= {"trigger", "high_water_percent"}
        if version == CONTEXT_COMPACTED_SCHEMA_VERSION:
            fields.add("provider_usage")
        _closed_fields(value, fields, record_type)
        previous = value.get("previous_checkpoint_sequence")
        if previous is not None and (type(previous) is not int or previous < 0):
            raise SessionRecordError(
                "context_compacted previous_checkpoint_sequence must be non-negative or null"
            )
        if version >= CONTEXT_COMPACTED_TRIGGER_SCHEMA_VERSION:
            try:
                trigger = CompactionTrigger(_required_field_text(value, "trigger", record_type))
            except ValueError:
                raise SessionRecordError("context_compacted trigger is invalid") from None
            high_water_percent = _nullable_field_int(value, "high_water_percent", record_type)
        else:
            trigger = CompactionTrigger.MANUAL
            high_water_percent = None
        record = ContextCompacted(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            source_context_id=_required_field_text(value, "source_context_id", record_type),
            result_context_id=_required_field_text(value, "result_context_id", record_type),
            source_full_turn_count=_required_field_int(
                value, "source_full_turn_count", record_type
            ),
            source_effective_turn_count=_required_field_int(
                value, "source_effective_turn_count", record_type
            ),
            retained_from_full_turn=_required_field_int(
                value, "retained_from_full_turn", record_type
            ),
            previous_checkpoint_sequence=previous,
            summary=_required_field_text(value, "summary", record_type),
            compact_prompt_version=_required_field_int(
                value, "compact_prompt_version", record_type
            ),
            compact_prompt_fingerprint=_required_field_text(
                value, "compact_prompt_fingerprint", record_type
            ),
            continuation_version=_required_field_int(value, "continuation_version", record_type),
            continuation_fingerprint=_required_field_text(
                value, "continuation_fingerprint", record_type
            ),
            effective_context_representation_version=_required_field_int(
                value, "effective_context_representation_version", record_type
            ),
            provider_usage=(
                _provider_usage_from_value(
                    value.get("provider_usage"),
                    expected_kind=ProviderInvocationKind.COMPACTION,
                    label=record_type,
                )
                if version == CONTEXT_COMPACTED_SCHEMA_VERSION
                else ()
            ),
            trigger=trigger,
            high_water_percent=high_water_percent,
            schema_version=version,
        )
        _validate_context_compacted_fields(record)
        return record
    if record_type == "compaction_failed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "occurred_at",
            "binding",
            "trigger",
            "failure_kind",
            "message",
            "provider_usage",
        }
        _closed_fields(value, fields, record_type)
        try:
            trigger = CompactionTrigger(_required_field_text(value, "trigger", record_type))
        except ValueError:
            raise SessionRecordError("compaction_failed trigger is invalid") from None
        record = CompactionFailed(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            binding=_binding_from_value(value.get("binding")),
            trigger=trigger,
            failure_kind=_required_field_text(value, "failure_kind", record_type),
            message=_required_field_text(value, "message", record_type, allow_empty=True),
            provider_usage=_provider_usage_from_value(
                value.get("provider_usage"),
                expected_kind=ProviderInvocationKind.COMPACTION,
                label=record_type,
            ),
        )
        _validate_compaction_failed(record)
        return record
    simple_fields = {"record_type", "schema_version", "sequence", "occurred_at"}
    if record_type == "session_resumed":
        fields = set(simple_fields)
        if version == SESSION_RESUMED_SCHEMA_VERSION:
            fields.add("binding")
        _closed_fields(value, fields, record_type)
        return SessionResumed(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            binding=(
                _binding_from_value(value.get("binding"))
                if version == SESSION_RESUMED_SCHEMA_VERSION
                else None
            ),
            schema_version=version,
        )
    if record_type == "recovery":
        _closed_fields(value, simple_fields | {"truncated_bytes"}, record_type)
        truncated = value.get("truncated_bytes")
        if type(truncated) is not int or truncated < 1:
            raise SessionRecordError("recovery truncated_bytes must be a positive integer")
        return Recovery(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            truncated_bytes=truncated,
        )
    if record_type == "session_closed":
        _closed_fields(value, simple_fields | {"reason"}, record_type)
        return SessionClosed(
            sequence=sequence,
            occurred_at=_required_field_text(value, "occurred_at", record_type),
            reason=_required_field_text(value, "reason", record_type, allow_empty=True),
        )
    raise SessionRecordError(f"unknown session record type: {record_type}")


def _binding_to_dict(binding: BindingSnapshot) -> dict[str, object]:
    binding.__post_init__()
    return {
        "profile_id": binding.profile_id,
        "profile_revision": binding.profile_revision,
        "profile_name": binding.profile_name,
        "profile_fingerprint": binding.profile_fingerprint,
        "provider_id": binding.provider_id,
        "protocol": binding.protocol,
        "selected_model": binding.selected_model,
        "wire_model": binding.wire_model,
        "base_url": binding.base_url,
        "base_url_source": binding.base_url_source,
        "source": binding.source,
        "credential_env": binding.credential_env,
        "max_output_tokens": binding.max_output_tokens,
        "temperature": binding.temperature,
        "generation": binding.generation,
        "adapter_version": binding.adapter_version,
        "route_fingerprint": binding.route_fingerprint,
    }


def _binding_from_value(value: object) -> BindingSnapshot:
    if not isinstance(value, dict):
        raise SessionRecordError("binding must be a JSON object")
    fields = {
        "profile_id",
        "profile_revision",
        "profile_name",
        "profile_fingerprint",
        "provider_id",
        "protocol",
        "selected_model",
        "wire_model",
        "base_url",
        "base_url_source",
        "source",
        "credential_env",
        "max_output_tokens",
        "temperature",
        "generation",
        "adapter_version",
        "route_fingerprint",
    }
    _closed_fields(value, fields, "binding")
    return BindingSnapshot(
        profile_id=_nullable_field_text(value, "profile_id", "binding"),
        profile_revision=_nullable_field_int(value, "profile_revision", "binding"),
        profile_name=_nullable_field_text(value, "profile_name", "binding"),
        profile_fingerprint=_nullable_field_text(value, "profile_fingerprint", "binding"),
        provider_id=_required_field_text(value, "provider_id", "binding"),
        protocol=_nullable_field_text(value, "protocol", "binding"),
        selected_model=_nullable_field_text(value, "selected_model", "binding"),
        wire_model=_nullable_field_text(value, "wire_model", "binding"),
        base_url=_nullable_field_text(value, "base_url", "binding"),
        base_url_source=_nullable_field_text(value, "base_url_source", "binding"),
        source=_required_field_text(value, "source", "binding"),
        credential_env=_nullable_field_text(value, "credential_env", "binding"),
        max_output_tokens=_nullable_field_int(value, "max_output_tokens", "binding"),
        temperature=_nullable_field_number(value, "temperature", "binding"),
        generation=_required_field_int(value, "generation", "binding"),
        adapter_version=_required_field_text(value, "adapter_version", "binding"),
        route_fingerprint=_required_field_text(value, "route_fingerprint", "binding"),
    )


def _item_to_dict(
    item: ConversationItem,
    *,
    schema_version: int = TURN_COMMITTED_SCHEMA_VERSION,
) -> dict[str, object]:
    if schema_version not in {
        TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
        TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
        TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
        TURN_COMMITTED_BATCH_SCHEMA_VERSION,
        TURN_COMMITTED_USAGE_SCHEMA_VERSION,
        TURN_COMMITTED_NAMING_SCHEMA_VERSION,
        TURN_COMMITTED_SCHEMA_VERSION,
    }:
        raise SessionRecordError("unsupported turn_committed schema version")
    if isinstance(item, UserMessage):
        _text_payload(item.text, "user message text")
        return {"item_type": "user_message", "text": item.text}
    if isinstance(item, AssistantText):
        _text_payload(item.text, "assistant text")
        return {"item_type": "assistant_text", "text": item.text}
    if isinstance(item, ToolUse):
        _required_text(item.tool_use_id, "tool_use ID")
        _required_text(item.name, "tool_use name")
        supports_assistant_text = schema_version in {
            TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }
        if item.assistant_text is not None and not supports_assistant_text:
            raise SessionRecordError(
                "assistant tool text requires a newer turn_committed schema version"
            )
        if not isinstance(item.arguments, ToolArguments):
            raise SessionRecordError("tool_use arguments are invalid")
        arguments = item.arguments.as_mapping()
        if schema_version == TURN_COMMITTED_LEGACY_SCHEMA_VERSION:
            if item.name == "read_file" and set(arguments) == {"path"}:
                path = arguments["path"]
            elif item.name == "glob" and set(arguments) == {"pattern"}:
                path = arguments["pattern"]
            elif item.name not in {"read_file", "glob", "grep"} and set(arguments) == {"path"}:
                path = arguments["path"]
            else:
                raise SessionRecordError("tool_use cannot be represented by schema version 1")
            _required_text(path, "tool_use path")
            return {
                "item_type": "tool_use",
                "tool_use_id": item.tool_use_id,
                "name": item.name,
                "path": path,
            }
        payload = {
            "item_type": "tool_use",
            "tool_use_id": item.tool_use_id,
            "name": item.name,
            "arguments_version": item.arguments.version,
            "arguments": arguments,
        }
        if supports_assistant_text:
            if item.assistant_text is not None:
                try:
                    ToolUse(
                        item.tool_use_id,
                        item.name,
                        item.arguments,
                        assistant_text=item.assistant_text,
                    )
                except ValueError as error:
                    raise SessionRecordError(str(error)) from None
                _text_payload(item.assistant_text, "assistant tool text")
            payload["assistant_text"] = item.assistant_text
        return payload
    if isinstance(item, AssistantToolBatch):
        if schema_version not in {
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }:
            raise SessionRecordError(
                "assistant tool batch requires a newer turn_committed schema version"
            )
        try:
            AssistantToolBatch(item.tool_uses, item.assistant_text)
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
        return {
            "item_type": "assistant_tool_batch",
            "assistant_text": item.assistant_text,
            "tool_uses": [
                {
                    "tool_use_id": request.tool_use_id,
                    "name": request.name,
                    "arguments_version": request.arguments.version,
                    "arguments": request.arguments.as_mapping(),
                }
                for request in item.tool_uses
            ],
        }
    if isinstance(item, ToolResult):
        _required_text(item.tool_use_id, "tool_result ID")
        _text_payload(item.content, "tool_result content")
        if type(item.is_error) is not bool or type(item.truncated) is not bool:
            raise SessionRecordError("tool_result flags must be booleans")
        return {
            "item_type": "tool_result",
            "tool_use_id": item.tool_use_id,
            "content": item.content,
            "is_error": item.is_error,
            "truncated": item.truncated,
        }
    raise SessionRecordError("turn contains an unsupported conversation item")


def _item_from_value(value: object, *, schema_version: int) -> ConversationItem:
    if not isinstance(value, dict):
        raise SessionRecordError("turn item must be a JSON object")
    item_type = _required_field_text(value, "item_type", "turn item")
    if item_type in {"user_message", "assistant_text"}:
        _closed_fields(value, {"item_type", "text"}, item_type)
        text = _required_field_payload_text(value, "text", item_type)
        return UserMessage(text) if item_type == "user_message" else AssistantText(text)
    if item_type == "tool_use":
        if schema_version == TURN_COMMITTED_LEGACY_SCHEMA_VERSION:
            _closed_fields(value, {"item_type", "tool_use_id", "name", "path"}, item_type)
            name = _required_field_text(value, "name", item_type)
            path = _required_field_text(value, "path", item_type)
            if name == "glob":
                arguments = {"pattern": path}
            else:
                arguments = {"path": path}
            return ToolUse(
                tool_use_id=_required_field_text(value, "tool_use_id", item_type),
                name=name,
                arguments=ToolArguments.from_mapping(arguments),
            )
        fields = {
            "item_type",
            "tool_use_id",
            "name",
            "arguments_version",
            "arguments",
        }
        if schema_version in {
            TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }:
            fields.add("assistant_text")
        _closed_fields(value, fields, item_type)
        arguments_version = value.get("arguments_version")
        if type(arguments_version) is not int:
            raise SessionRecordError("tool_use arguments_version must be an integer")
        raw_arguments = value.get("arguments")
        if not isinstance(raw_arguments, dict):
            raise SessionRecordError("tool_use arguments must be a JSON object")
        try:
            arguments = ToolArguments.from_mapping(
                raw_arguments,
                version=arguments_version,
            )
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
        assistant_text = None
        if schema_version in {
            TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }:
            raw_assistant_text = value.get("assistant_text")
            if raw_assistant_text is not None:
                if not isinstance(raw_assistant_text, str):
                    raise SessionRecordError("tool_use assistant_text must be text or null")
                assistant_text = raw_assistant_text
        tool_use_id = _required_field_text(value, "tool_use_id", item_type)
        name = _required_field_text(value, "name", item_type)
        try:
            request = ToolUse(
                tool_use_id=tool_use_id,
                name=name,
                arguments=arguments,
                assistant_text=assistant_text,
            )
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
        if assistant_text is not None:
            _text_payload(assistant_text, "tool_use assistant_text")
        return request
    if item_type == "assistant_tool_batch":
        if schema_version not in {
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }:
            raise SessionRecordError(
                "assistant tool batch requires a newer turn_committed schema version"
            )
        _closed_fields(value, {"item_type", "assistant_text", "tool_uses"}, item_type)
        raw_text = value.get("assistant_text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise SessionRecordError("assistant tool batch text must be text or null")
        raw_uses = value.get("tool_uses")
        if not isinstance(raw_uses, list) or not raw_uses:
            raise SessionRecordError("assistant tool batch tool_uses must be a non-empty array")
        requests: list[ToolUse] = []
        for raw_use in raw_uses:
            if not isinstance(raw_use, dict):
                raise SessionRecordError("assistant tool batch tool use must be an object")
            _closed_fields(
                raw_use,
                {"tool_use_id", "name", "arguments_version", "arguments"},
                "assistant tool batch tool use",
            )
            arguments_version = raw_use.get("arguments_version")
            if type(arguments_version) is not int:
                raise SessionRecordError("tool_use arguments_version must be an integer")
            raw_arguments = raw_use.get("arguments")
            if not isinstance(raw_arguments, dict):
                raise SessionRecordError("tool_use arguments must be a JSON object")
            try:
                arguments = ToolArguments.from_mapping(
                    raw_arguments,
                    version=arguments_version,
                )
                requests.append(
                    ToolUse(
                        _required_field_text(
                            raw_use, "tool_use_id", "assistant tool batch tool use"
                        ),
                        _required_field_text(raw_use, "name", "assistant tool batch tool use"),
                        arguments,
                    )
                )
            except ValueError as error:
                raise SessionRecordError(str(error)) from None
        try:
            return AssistantToolBatch(tuple(requests), raw_text)
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
    if item_type == "tool_result":
        _closed_fields(
            value,
            {"item_type", "tool_use_id", "content", "is_error", "truncated"},
            item_type,
        )
        content = _required_field_payload_text(value, "content", item_type)
        is_error = value.get("is_error")
        truncated = value.get("truncated")
        if type(is_error) is not bool or type(truncated) is not bool:
            raise SessionRecordError("tool_result flags must be booleans")
        return ToolResult(
            tool_use_id=_required_field_text(value, "tool_use_id", item_type),
            content=content,
            is_error=is_error,
            truncated=truncated,
        )
    raise SessionRecordError(f"unknown turn item type: {item_type}")


def _tool_ledger_to_dict(ledger: ToolTurnLedger) -> dict[str, object]:
    if type(ledger) is not ToolTurnLedger:
        raise SessionRecordError("turn_committed tool ledger is invalid")
    try:
        ledger.__post_init__()
    except ValueError as error:
        raise SessionRecordError(str(error)) from None
    return {
        "entries": [
            {
                "tool_use_id": entry.tool_use_id,
                "tool_name": entry.tool_name,
                "request_index": entry.request_index,
                "outcome": entry.outcome.value,
                "result_code": entry.result_code,
            }
            for entry in ledger.entries
        ]
    }


def _tool_ledger_from_value(value: object) -> ToolTurnLedger:
    if not isinstance(value, dict):
        raise SessionRecordError("turn_committed tool_ledger must be an object")
    _closed_fields(value, {"entries"}, "turn_committed tool_ledger")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        raise SessionRecordError("turn_committed tool ledger entries must be an array")
    entries: list[ToolOutcomeEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise SessionRecordError("tool ledger entry must be an object")
        _closed_fields(
            raw_entry,
            {"tool_use_id", "tool_name", "request_index", "outcome", "result_code"},
            "tool ledger entry",
        )
        request_index = raw_entry.get("request_index")
        if type(request_index) is not int:
            raise SessionRecordError("tool ledger request_index must be an integer")
        try:
            entries.append(
                ToolOutcomeEntry(
                    tool_use_id=_required_field_text(raw_entry, "tool_use_id", "tool ledger entry"),
                    tool_name=_required_field_text(raw_entry, "tool_name", "tool ledger entry"),
                    request_index=request_index,
                    outcome=_enum_field(
                        raw_entry,
                        "outcome",
                        "tool ledger entry",
                        ToolRequestOutcome,
                    ),
                    result_code=_nullable_field_text(raw_entry, "result_code", "tool ledger entry"),
                )
            )
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
    try:
        return ToolTurnLedger(tuple(entries))
    except ValueError as error:
        raise SessionRecordError(str(error)) from None


def _provider_usage_to_value(
    records: tuple[ProviderInvocationUsage, ...] | None,
    *,
    expected_kind: ProviderInvocationKind,
    label: str,
) -> list[dict[str, int | None]]:
    _validate_provider_usage(records, expected_kind=expected_kind, label=label)
    assert records is not None
    return [
        {
            "index": record.sequence,
            "input_tokens": record.usage.input_tokens if record.usage is not None else None,
            "output_tokens": record.usage.output_tokens if record.usage is not None else None,
        }
        for record in records
    ]


def _provider_usage_from_value(
    value: object,
    *,
    expected_kind: ProviderInvocationKind,
    label: str,
) -> tuple[ProviderInvocationUsage, ...]:
    if not isinstance(value, list):
        raise SessionRecordError(f"{label} provider_usage must be an array")
    records: list[ProviderInvocationUsage] = []
    for item in value:
        if not isinstance(item, dict):
            raise SessionRecordError(f"{label} provider usage entry must be an object")
        _closed_fields(
            item,
            {"index", "input_tokens", "output_tokens"},
            f"{label} provider usage entry",
        )
        index = item.get("index")
        input_tokens = item.get("input_tokens")
        output_tokens = item.get("output_tokens")
        if (input_tokens is None) != (output_tokens is None):
            raise SessionRecordError(
                f"{label} provider usage tokens must both be integers or both null"
            )
        try:
            usage = (
                None if input_tokens is None else ProviderTokenUsage(input_tokens, output_tokens)  # type: ignore[arg-type]
            )
            records.append(ProviderInvocationUsage(index, expected_kind, usage))  # type: ignore[arg-type]
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
    result = tuple(records)
    _validate_provider_usage(result, expected_kind=expected_kind, label=label)
    return result


def _validate_provider_usage(
    records: tuple[ProviderInvocationUsage, ...] | None,
    *,
    expected_kind: ProviderInvocationKind,
    label: str,
) -> None:
    if records is None:
        raise SessionRecordError(f"{label} provider_usage is unavailable in the current schema")
    if type(records) is not tuple or len(records) > 24:
        raise SessionRecordError(f"{label} provider_usage must contain at most 24 entries")
    for index, record in enumerate(records, start=1):
        if type(record) is not ProviderInvocationUsage:
            raise SessionRecordError(f"{label} provider usage entry is invalid")
        try:
            record.__post_init__()
        except ValueError as error:
            raise SessionRecordError(str(error)) from None
        if record.sequence != index or record.kind != expected_kind:
            raise SessionRecordError(
                f"{label} provider usage entries must be contiguous and operation-local"
            )


def _validate_turn_ledger(record: TurnCommitted) -> None:
    if record.schema_version < TURN_COMMITTED_LEDGER_SCHEMA_VERSION:
        if record.tool_ledger.entries:
            raise SessionRecordError("legacy turn_committed cannot contain a tool ledger")
        return
    if type(record.tool_ledger) is not ToolTurnLedger:
        raise SessionRecordError("turn_committed tool ledger is invalid")
    try:
        record.tool_ledger.__post_init__()
    except ValueError as error:
        raise SessionRecordError(str(error)) from None

    requests: list[ToolUse] = []
    results: dict[str, ToolResult] = {}
    for item in record.items:
        if isinstance(item, ToolUse):
            requests.append(item)
        elif isinstance(item, AssistantToolBatch):
            requests.extend(item.tool_uses)
        elif isinstance(item, ToolResult):
            results[item.tool_use_id] = item
    if len(requests) != len(record.tool_ledger.entries):
        raise SessionRecordError("turn_committed tool ledger does not cover every request")
    for request, entry in zip(requests, record.tool_ledger.entries, strict=True):
        if (entry.tool_use_id, entry.tool_name) != (request.tool_use_id, request.name):
            raise SessionRecordError("turn_committed tool ledger identity does not match history")
        result = results[request.tool_use_id]
        if (entry.outcome == ToolRequestOutcome.SUCCEEDED) == result.is_error:
            raise SessionRecordError("turn_committed tool ledger outcome contradicts tool result")
    if record.schema_version >= TURN_COMMITTED_USAGE_SCHEMA_VERSION:
        _validate_provider_usage(
            record.provider_usage,
            expected_kind=ProviderInvocationKind.TURN,
            label="turn_committed",
        )
    elif record.provider_usage not in (None, ()):
        raise SessionRecordError("legacy turn_committed cannot contain provider usage")


def _validate_turn_session_name(record: TurnCommitted) -> None:
    if record.schema_version < TURN_COMMITTED_NAMING_SCHEMA_VERSION:
        if (
            record.session_name is not None
            or record.session_name_source is not None
            or record.session_title_fallback_reason is not None
        ):
            raise SessionRecordError("legacy turn_committed cannot contain a Session name")
        return
    if (
        record.schema_version == TURN_COMMITTED_NAMING_SCHEMA_VERSION
        and record.session_title_fallback_reason is not None
    ):
        raise SessionRecordError("turn_committed v7 cannot contain a title fallback reason")
    if (record.session_name is None) != (record.session_name_source is None):
        raise SessionRecordError("turn_committed Session name fields must both be null or present")
    if record.session_name is None:
        if record.session_title_fallback_reason is not None:
            raise SessionRecordError("unnamed turn_committed cannot contain a fallback reason")
        return
    if canonical_generated_session_name(record.session_name) != record.session_name:
        raise SessionRecordError("generated Session name must use canonical whitespace")
    if record.session_name_source not in {
        SessionNameSource.MODEL,
        SessionNameSource.FALLBACK,
    }:
        raise SessionRecordError("turn_committed Session name source is invalid")
    if record.schema_version == TURN_COMMITTED_SCHEMA_VERSION:
        if (
            record.session_name_source == SessionNameSource.FALLBACK
            and record.session_title_fallback_reason is None
        ):
            raise SessionRecordError("fallback Session name requires a bounded reason")
        if (
            record.session_name_source != SessionNameSource.FALLBACK
            and record.session_title_fallback_reason is not None
        ):
            raise SessionRecordError("model Session name cannot contain a fallback reason")


def _validate_session_forked(
    record: SessionForked,
    *,
    header: SessionHeader,
    turns: list[ConversationTurn],
) -> None:
    _validate_timestamp(record.occurred_at, "session_forked occurred_at")
    if record.sequence != 1 or turns:
        raise SessionRecordError("session_forked must immediately follow session_header")
    canonical_session_id(record.source_session_id)
    if record.source_session_id == header.session_id:
        raise SessionRecordError("session_forked source must differ from the new Session")
    if type(record.source_turn_count) is not int or record.source_turn_count < 1:
        raise SessionRecordError("session_forked source_turn_count must be positive")
    if _SHA256.fullmatch(record.source_transcript_sha256) is None:
        raise SessionRecordError("session_forked source transcript digest is invalid")


def _validate_header(header: SessionHeader) -> None:
    if header.sequence != 0:
        raise SessionRecordError("session_header sequence must be zero")
    canonical_session_id(header.session_id)
    workspace = Path(header.workspace)
    if not workspace.is_absolute() or str(workspace) != header.workspace:
        raise SessionRecordError("session workspace must be a canonical absolute path")
    if _WORKSPACE_FINGERPRINT.fullmatch(header.workspace_fingerprint) is None:
        raise SessionRecordError("session workspace fingerprint is invalid")
    _validate_timestamp(header.created_at, "session created_at")
    header.binding.__post_init__()
    if header.schema_version == SESSION_HEADER_SCHEMA_VERSION:
        if canonical_session_name(header.name) != header.name:
            raise SessionRecordError("session header name must use canonical whitespace")
    elif header.name is not None:
        raise SessionRecordError("legacy session_header cannot contain a name")


def _validate_turn(items: tuple[ConversationItem, ...], seen_tool_ids: set[str]) -> None:
    for item in items:
        _item_to_dict(item, schema_version=TURN_COMMITTED_SCHEMA_VERSION)
    try:
        validated = validate_complete_history(
            items,
            prior_tool_use_ids=frozenset(seen_tool_ids),
        )
    except ValueError as error:
        raise SessionRecordError(f"invalid committed turn: {error}") from None
    if len(validated.complete_turns) != 1:
        raise SessionRecordError("turn_committed must contain exactly one complete turn")
    seen_tool_ids.update(validated.tool_use_ids)


def _validate_record_version(record: SessionRecord) -> None:
    if isinstance(record, SessionHeader):
        if record.schema_version not in {SCHEMA_VERSION, SESSION_HEADER_SCHEMA_VERSION}:
            raise SessionRecordError("unsupported session record schema version")
        return
    if isinstance(record, TurnCommitted):
        if record.schema_version not in {
            TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
            TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
            TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
            TURN_COMMITTED_BATCH_SCHEMA_VERSION,
            TURN_COMMITTED_LEDGER_SCHEMA_VERSION,
            TURN_COMMITTED_USAGE_SCHEMA_VERSION,
            TURN_COMMITTED_NAMING_SCHEMA_VERSION,
            TURN_COMMITTED_SCHEMA_VERSION,
        }:
            raise SessionRecordError("unsupported session record schema version")
        return
    if isinstance(record, ContextCompacted):
        expected = {
            CONTEXT_COMPACTED_LEGACY_SCHEMA_VERSION,
            CONTEXT_COMPACTED_TRIGGER_SCHEMA_VERSION,
            CONTEXT_COMPACTED_SCHEMA_VERSION,
        }
        if record.schema_version not in expected:
            raise SessionRecordError("unsupported session record schema version")
        return
    if isinstance(record, TurnFailed):
        if record.schema_version not in {
            TURN_FAILED_LEGACY_SCHEMA_VERSION,
            TURN_FAILED_SCHEMA_VERSION,
        }:
            raise SessionRecordError("unsupported session record schema version")
        return
    if isinstance(record, SessionResumed):
        if record.schema_version not in {
            SESSION_RESUMED_LEGACY_SCHEMA_VERSION,
            SESSION_RESUMED_SCHEMA_VERSION,
        }:
            raise SessionRecordError("unsupported session record schema version")
        if (
            record.schema_version == SESSION_RESUMED_LEGACY_SCHEMA_VERSION
            and record.binding is not None
        ):
            raise SessionRecordError("legacy session_resumed cannot contain a runtime binding")
        if record.schema_version == SESSION_RESUMED_SCHEMA_VERSION and record.binding is None:
            raise SessionRecordError("current session_resumed requires a runtime binding")
        return
    if record.schema_version != SCHEMA_VERSION:
        raise SessionRecordError("unsupported session record schema version")


def _validate_context_compacted_fields(record: ContextCompacted) -> None:
    _validate_record_version(record)
    if record.schema_version == CONTEXT_COMPACTED_LEGACY_SCHEMA_VERSION:
        if record.trigger != CompactionTrigger.MANUAL or record.high_water_percent is not None:
            raise SessionRecordError(
                "legacy context_compacted provenance must be manual without a threshold"
            )
    elif record.trigger == CompactionTrigger.HIGH_WATER:
        if record.high_water_percent != 80:
            raise SessionRecordError("high-water context_compacted threshold must be 80")
    elif record.trigger in {CompactionTrigger.MANUAL, CompactionTrigger.OVERFLOW}:
        if record.high_water_percent is not None:
            raise SessionRecordError(
                "manual and overflow context_compacted thresholds must be null"
            )
    else:
        raise SessionRecordError("context_compacted trigger is invalid")
    if record.schema_version == CONTEXT_COMPACTED_SCHEMA_VERSION:
        _validate_provider_usage(
            record.provider_usage,
            expected_kind=ProviderInvocationKind.COMPACTION,
            label="context_compacted",
        )
    elif record.provider_usage not in (None, ()):
        raise SessionRecordError("legacy context_compacted cannot contain provider usage")
    _validate_timestamp(record.occurred_at, "context_compacted occurred_at")
    record.binding.__post_init__()
    _context_id(record.source_context_id, "context_compacted source_context_id")
    _context_id(record.result_context_id, "context_compacted result_context_id")
    for value, label in (
        (record.source_full_turn_count, "source_full_turn_count"),
        (record.source_effective_turn_count, "source_effective_turn_count"),
        (record.retained_from_full_turn, "retained_from_full_turn"),
    ):
        if type(value) is not int or value < 0:
            raise SessionRecordError(f"context_compacted {label} must be non-negative")
    if record.previous_checkpoint_sequence is not None and (
        type(record.previous_checkpoint_sequence) is not int
        or record.previous_checkpoint_sequence < 0
    ):
        raise SessionRecordError(
            "context_compacted previous_checkpoint_sequence must be non-negative or null"
        )
    _text_payload(record.summary, "context_compacted summary")
    if not record.summary.strip():
        raise SessionRecordError("context_compacted summary must not be blank")
    prompt = build_compact_prompt()
    if (
        record.compact_prompt_version != COMPACT_PROMPT_VERSION
        or record.compact_prompt_fingerprint != prompt.fingerprint
    ):
        raise SessionRecordError("context_compacted compact prompt provenance is unsupported")
    if (
        record.continuation_version != SUMMARY_CONTINUATION_VERSION
        or record.continuation_fingerprint
        != summary_continuation_fingerprint(SUMMARY_CONTINUATION_VERSION)
    ):
        raise SessionRecordError("context_compacted continuation provenance is unsupported")
    if record.effective_context_representation_version not in {
        2,
        COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    }:
        raise SessionRecordError(
            "context_compacted effective-context representation is unsupported"
        )


def _validate_compaction_failed(record: CompactionFailed) -> None:
    _validate_timestamp(record.occurred_at, "compaction_failed occurred_at")
    record.binding.__post_init__()
    if type(record.trigger) is not CompactionTrigger:
        raise SessionRecordError("compaction_failed trigger is invalid")
    _required_text(record.failure_kind, "compaction_failed failure_kind")
    _required_text(record.message, "compaction_failed message", allow_empty=True)
    _validate_provider_usage(
        record.provider_usage,
        expected_kind=ProviderInvocationKind.COMPACTION,
        label="compaction_failed",
    )


def _validate_context_compacted(
    record: ContextCompacted,
    *,
    full_history: tuple[ConversationItem, ...],
    effective_history: tuple[ConversationItem, ...],
    latest_checkpoint: ContextCompacted | None,
) -> None:
    _validate_context_compacted_fields(record)
    full_turns = validate_complete_history(full_history).complete_turns
    effective_turns = validate_complete_history(effective_history).complete_turns
    if record.source_full_turn_count != len(full_turns):
        raise SessionRecordError("context_compacted full turn count does not match replay state")
    if record.source_effective_turn_count != len(effective_turns):
        raise SessionRecordError(
            "context_compacted effective turn count does not match replay state"
        )
    if len(effective_turns) < COMPACT_MIN_EFFECTIVE_TURNS:
        raise SessionRecordError("context_compacted source has too few effective turns")
    expected_boundary = len(full_turns) - COMPACT_RETAINED_TURNS
    if record.retained_from_full_turn != expected_boundary:
        raise SessionRecordError("context_compacted retained boundary is invalid")
    expected_previous = latest_checkpoint.sequence if latest_checkpoint is not None else None
    if record.previous_checkpoint_sequence != expected_previous:
        raise SessionRecordError(
            "context_compacted previous checkpoint does not match replay state"
        )
    if latest_checkpoint is not None and (
        record.retained_from_full_turn < latest_checkpoint.retained_from_full_turn
    ):
        raise SessionRecordError("context_compacted retained boundary moved backwards")


def _context_id(value: object, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"ctx-v[1-6]-[0-9a-f]{64}", value) is None:
        raise SessionRecordError(f"{label} is invalid")


def _closed_fields(value: dict[str, object], expected: set[str], label: str) -> None:
    unknown = set(value) - expected
    if unknown:
        raise SessionRecordError(f"{label} contains unknown field: {sorted(unknown)[0]}")
    missing = expected - set(value)
    if missing:
        raise SessionRecordError(f"{label} is missing required field: {sorted(missing)[0]}")


def _required_field_payload_text(value: dict[str, object], field: str, label: str) -> str:
    """Decode conversation payload text without the 4096-character metadata cap."""
    result = value.get(field)
    if not isinstance(result, str):
        raise SessionRecordError(f"{label} {field} must be text")
    _text_payload(result, f"{label} {field}")
    return result


def _required_field_text(
    value: dict[str, object], field: str, label: str, *, allow_empty: bool = False
) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise SessionRecordError(f"{label} {field} must be text")
    _required_text(result, f"{label} {field}", allow_empty=allow_empty)
    return result


def _nullable_field_text(value: dict[str, object], field: str, label: str) -> str | None:
    result = value.get(field)
    if result is None:
        return None
    if not isinstance(result, str):
        raise SessionRecordError(f"{label} {field} must be text or null")
    return result


def _required_field_int(value: dict[str, object], field: str, label: str) -> int:
    result = value.get(field)
    if type(result) is not int:
        raise SessionRecordError(f"{label} {field} must be an integer")
    return result


def _nullable_field_int(value: dict[str, object], field: str, label: str) -> int | None:
    result = value.get(field)
    if result is not None and type(result) is not int:
        raise SessionRecordError(f"{label} {field} must be an integer or null")
    return result


def _enum_field(
    value: dict[str, object],
    field: str,
    label: str,
    enum_type: type[_EnumT],
) -> _EnumT:
    raw = _required_field_text(value, field, label)
    try:
        return enum_type(raw)
    except ValueError:
        raise SessionRecordError(f"{label} {field} is invalid") from None


def _session_name_source_from_value(value: object) -> SessionNameSource | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionRecordError("turn_committed session_name_source must be text or null")
    try:
        return SessionNameSource(value)
    except ValueError:
        raise SessionRecordError("turn_committed session_name_source is invalid") from None


def _session_title_fallback_reason_from_value(
    value: object,
) -> SessionTitleFallbackReason | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionRecordError(
            "turn_committed session_title_fallback_reason must be text or null"
        )
    try:
        return SessionTitleFallbackReason(value)
    except ValueError:
        raise SessionRecordError(
            "turn_committed session_title_fallback_reason is invalid"
        ) from None


def _nullable_field_number(value: dict[str, object], field: str, label: str) -> float | None:
    result = value.get(field)
    if result is None:
        return None
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise SessionRecordError(f"{label} {field} must be a number or null")
    return float(result)


def _required_text(value: object, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise SessionRecordError(f"{label} must be text")
    if not allow_empty and not value:
        raise SessionRecordError(f"{label} must not be empty")
    if len(value) > MAX_STRING_LENGTH:
        raise SessionRecordError(f"{label} exceeds {MAX_STRING_LENGTH} characters")
    if "\x00" in value:
        raise SessionRecordError(f"{label} must not contain NUL")


def _optional_text(value: object, label: str) -> None:
    if value is not None:
        _required_text(value, label)


def _text_payload(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise SessionRecordError(f"{label} must be text")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise SessionRecordError(f"{label} exceeds {MAX_TEXT_BYTES} bytes")
    if "\x00" in value:
        raise SessionRecordError(f"{label} must not contain NUL")


def _required_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise SessionRecordError(f"{label} must be a lowercase SHA-256 hex digest")


def _optional_sha256(value: object, label: str) -> None:
    if value is not None:
        _required_sha256(value, label)


def _validate_base_url(value: object) -> None:
    if value is None:
        return
    _required_text(value, "binding base_url")
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SessionRecordError("binding base_url must be an absolute credential-free HTTP(S) URL")


def _validate_timestamp(value: object, label: str) -> None:
    _required_text(value, label)
    assert isinstance(value, str)
    if not value.endswith("Z"):
        raise SessionRecordError(f"{label} must be a UTC RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise SessionRecordError(f"{label} must be a UTC RFC3339 timestamp") from None
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise SessionRecordError(f"{label} must be a UTC RFC3339 timestamp")
