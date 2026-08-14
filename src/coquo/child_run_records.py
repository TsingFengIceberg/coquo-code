"""Closed, versioned records for the durable Child Run control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import UUID

from coquo.session_records import workspace_fingerprint

CHILD_RUN_HEADER_SCHEMA_VERSION = 1
CHILD_RUN_CANCELLED_SCHEMA_VERSION = 1
CHILD_RUN_DELEGATED_SCHEMA_VERSION = 1
CHILD_RUN_ADMITTED_SCHEMA_VERSION = 1
CHILD_SESSION_BOUND_SCHEMA_VERSION = 1
CHILD_RUN_PREPARATION_FAILED_SCHEMA_VERSION = 1
CHILD_RUN_STARTED_SCHEMA_VERSION = 1
CHILD_RUN_COMPLETED_SCHEMA_VERSION = 1
CHILD_RUN_FAILED_SCHEMA_VERSION = 1
CHILD_RUN_CANCEL_REQUESTED_SCHEMA_VERSION = 1
CHILD_RUN_CANCELLED_TERMINAL_SCHEMA_VERSION = 1
CHILD_RUN_INTERRUPTED_SCHEMA_VERSION = 1
CHILD_RUN_HANDOFF_PUBLISHED_SCHEMA_VERSION = 1
MAX_CHILD_RUN_RECORD_BYTES = 64 * 1024
MAX_CHILD_RUN_RECORDS = 10_000
MAX_CHILD_RUN_TRANSCRIPT_BYTES = 1024 * 1024
MAX_CHILD_RUN_OBJECTIVE_CHARACTERS = 4096
MAX_CHILD_RUN_OBJECTIVE_BYTES = 16 * 1024
MAX_CHILD_RUN_REASON_CHARACTERS = 4096
MAX_CHILD_RUN_REASON_BYTES = 16 * 1024
MAX_CHILD_HANDOFF_BODY_CHARACTERS = 32 * 1024
MAX_CHILD_HANDOFF_BODY_BYTES = 32 * 1024

_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")


class ChildRunRecordError(ValueError):
    """Raised when a Child Run record or replay chain is invalid."""


class ChildRunStatus(StrEnum):
    QUEUED = "queued"
    CANCELLED = "cancelled"
    ADMITTED = "admitted"
    READY = "ready"
    FAILED = "failed"
    RUNNING = "running"
    CANCELLING = "cancelling"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ChildRunHeader:
    sequence: int
    child_run_id: str
    workspace: str
    workspace_fingerprint: str
    parent_session_id: str
    objective: str
    created_at: str
    record_type: str = "child_run_header"
    schema_version: int = CHILD_RUN_HEADER_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunCancelled:
    sequence: int
    child_run_id: str
    reason: str
    cancelled_at: str
    record_type: str = "child_run_cancelled"
    schema_version: int = CHILD_RUN_CANCELLED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunDelegated:
    sequence: int
    child_run_id: str
    parent_session_id: str
    parent_context_id: str
    parent_tool_use_id: str
    decision_record_sequence: int
    decision_sha256: str
    depth: int
    source: str
    delegated_at: str
    record_type: str = "child_run_delegated"
    schema_version: int = CHILD_RUN_DELEGATED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunAdmitted:
    sequence: int
    child_run_id: str
    parent_session_id: str
    child_session_id: str
    permission_mode: str
    approval_mode: str
    provider_binding: dict[str, object]
    tool_registry_id: str
    tool_registry_generation: int
    tool_set_id: str
    tool_names: tuple[str, ...]
    role_contract_version: int
    role_prompt_fingerprint: str
    max_provider_invocations: int
    max_tool_requests: int
    max_output_tokens: int
    deadline_seconds: int
    admitted_at: str
    record_type: str = "child_run_admitted"
    schema_version: int = CHILD_RUN_ADMITTED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildSessionBound:
    sequence: int
    child_run_id: str
    child_session_id: str
    session_header_sequence: int
    session_path: str
    bound_at: str
    record_type: str = "child_session_bound"
    schema_version: int = CHILD_SESSION_BOUND_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunPreparationFailed:
    sequence: int
    child_run_id: str
    phase: str
    result_code: str
    message: str
    failed_at: str
    record_type: str = "child_run_preparation_failed"
    schema_version: int = CHILD_RUN_PREPARATION_FAILED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunStarted:
    sequence: int
    child_run_id: str
    child_session_id: str
    execution_id: str
    started_at: str
    record_type: str = "child_run_started"
    schema_version: int = CHILD_RUN_STARTED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunCompleted:
    sequence: int
    child_run_id: str
    execution_id: str
    session_record_sequence: int
    assistant_text_sha256: str
    completed_at: str
    record_type: str = "child_run_completed"
    schema_version: int = CHILD_RUN_COMPLETED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunFailed:
    sequence: int
    child_run_id: str
    execution_id: str | None
    phase: str
    result_code: str
    message: str
    failed_at: str
    record_type: str = "child_run_failed"
    schema_version: int = CHILD_RUN_FAILED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunCancelRequested:
    sequence: int
    child_run_id: str
    execution_id: str | None
    reason: str
    source: str
    requested_at: str
    record_type: str = "child_run_cancel_requested"
    schema_version: int = CHILD_RUN_CANCEL_REQUESTED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunCancelledTerminal:
    sequence: int
    child_run_id: str
    execution_id: str | None
    cancel_request_sequence: int
    result_code: str
    observed_at: str
    record_type: str = "child_run_cancelled_terminal"
    schema_version: int = CHILD_RUN_CANCELLED_TERMINAL_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunInterrupted:
    sequence: int
    child_run_id: str
    execution_id: str
    last_durable_state: str
    lock_protocol: str
    result_code: str
    interrupted_at: str
    record_type: str = "child_run_interrupted"
    schema_version: int = CHILD_RUN_INTERRUPTED_SCHEMA_VERSION


@dataclass(frozen=True)
class ChildRunHandoffPublished:
    sequence: int
    child_run_id: str
    parent_session_id: str
    child_session_id: str | None
    outcome: str
    terminal_record_sequence: int
    terminal_record_type: str
    result_code: str
    source_text_sha256: str
    body: str
    body_sha256: str
    truncated: bool
    child_turn_record_sequence: int | None
    child_turn_record_sha256: str | None
    handoff_sha256: str
    published_at: str
    record_type: str = "child_run_handoff_published"
    schema_version: int = CHILD_RUN_HANDOFF_PUBLISHED_SCHEMA_VERSION


ChildRunRecord = (
    ChildRunHeader
    | ChildRunCancelled
    | ChildRunDelegated
    | ChildRunAdmitted
    | ChildSessionBound
    | ChildRunPreparationFailed
    | ChildRunStarted
    | ChildRunCompleted
    | ChildRunFailed
    | ChildRunCancelRequested
    | ChildRunCancelledTerminal
    | ChildRunInterrupted
    | ChildRunHandoffPublished
)


@dataclass(frozen=True)
class ChildRunReplayState:
    header: ChildRunHeader
    cancelled: ChildRunCancelled | None
    delegated: ChildRunDelegated | None
    admitted: ChildRunAdmitted | None
    session_bound: ChildSessionBound | None
    preparation_failed: ChildRunPreparationFailed | None
    started: ChildRunStarted | None
    completed: ChildRunCompleted | None
    failed: ChildRunFailed | None
    cancel_requested: ChildRunCancelRequested | None
    cancelled_terminal: ChildRunCancelledTerminal | None
    interrupted: ChildRunInterrupted | None
    handoff: ChildRunHandoffPublished | None
    records: tuple[ChildRunRecord, ...]

    @property
    def status(self) -> ChildRunStatus:
        if self.cancelled is not None:
            return ChildRunStatus.CANCELLED
        if self.completed is not None:
            return ChildRunStatus.COMPLETED
        if self.failed is not None or self.preparation_failed is not None:
            return ChildRunStatus.FAILED
        if self.cancelled_terminal is not None:
            return ChildRunStatus.CANCELLED
        if self.interrupted is not None:
            return ChildRunStatus.INTERRUPTED
        if self.cancel_requested is not None:
            return ChildRunStatus.CANCELLING
        if self.started is not None:
            return ChildRunStatus.RUNNING
        if self.session_bound is not None:
            return ChildRunStatus.READY
        if self.admitted is not None:
            return ChildRunStatus.ADMITTED
        return ChildRunStatus.QUEUED


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_child_run_id(value: object) -> str:
    if not isinstance(value, str):
        raise ChildRunRecordError("Child Run ID must be text")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ChildRunRecordError("Child Run ID must be a canonical UUID4") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ChildRunRecordError("Child Run ID must be a canonical UUID4")
    return value


def _canonical_text(value: object, label: str, max_characters: int, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise ChildRunRecordError(f"{label} must be text")
    if not value.strip():
        raise ChildRunRecordError(f"{label} must not be blank")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ChildRunRecordError(f"{label} must not contain control characters")
    if "\r" in value or "\n" in value:
        raise ChildRunRecordError(f"{label} must be one line")
    if len(value) > max_characters:
        raise ChildRunRecordError(f"{label} exceeds {max_characters} characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise ChildRunRecordError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def canonical_child_run_objective(value: object) -> str:
    return _canonical_text(
        value,
        "Child Run objective",
        MAX_CHILD_RUN_OBJECTIVE_CHARACTERS,
        MAX_CHILD_RUN_OBJECTIVE_BYTES,
    )


def canonical_child_run_reason(value: object) -> str:
    return _canonical_text(
        value,
        "Child Run cancellation reason",
        MAX_CHILD_RUN_REASON_CHARACTERS,
        MAX_CHILD_RUN_REASON_BYTES,
    )


def canonical_child_run_timestamp(value: object, label: str = "Child Run timestamp") -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise ChildRunRecordError(f"{label} is invalid")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ChildRunRecordError(f"{label} is invalid") from None
    return value


def _workspace(value: object) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ChildRunRecordError("Child Run workspace is invalid")
    return value


def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or _WORKSPACE_FINGERPRINT.fullmatch(value) is None:
        raise ChildRunRecordError("Child Run workspace fingerprint is invalid")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ChildRunRecordError(f"{label} is invalid")
    return value


def _sequence(value: object, expected: int) -> None:
    if type(value) is not int or value != expected:
        raise ChildRunRecordError("Child Run record sequence is invalid")


def _handoff_manifest_digest(record: ChildRunHandoffPublished) -> str:
    manifest = {
        "body_sha256": record.body_sha256,
        "child_run_id": record.child_run_id,
        "child_session_id": record.child_session_id,
        "child_turn_record_sequence": record.child_turn_record_sequence,
        "child_turn_record_sha256": record.child_turn_record_sha256,
        "outcome": record.outcome,
        "parent_session_id": record.parent_session_id,
        "result_code": record.result_code,
        "source_text_sha256": record.source_text_sha256,
        "terminal_record_sequence": record.terminal_record_sequence,
        "terminal_record_type": record.terminal_record_type,
        "truncated": record.truncated,
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _record_identity(record: ChildRunRecord) -> None:
    if type(record) is ChildRunHeader:
        if record.record_type != "child_run_header" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run header schema")
    elif type(record) is ChildRunCancelled:
        if record.record_type != "child_run_cancelled" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run cancellation schema")
    elif type(record) is ChildRunDelegated:
        if record.record_type != "child_run_delegated" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run delegation schema")
    elif type(record) is ChildRunAdmitted:
        if record.record_type != "child_run_admitted" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run admission schema")
    elif type(record) is ChildSessionBound:
        if record.record_type != "child_session_bound" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Session binding schema")
    elif type(record) is ChildRunPreparationFailed:
        if record.record_type != "child_run_preparation_failed" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run preparation schema")
    elif type(record) is ChildRunStarted:
        if record.record_type != "child_run_started" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run started schema")
    elif type(record) is ChildRunCompleted:
        if record.record_type != "child_run_completed" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run completed schema")
    elif type(record) is ChildRunFailed:
        if record.record_type != "child_run_failed" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run failed schema")
    elif type(record) is ChildRunCancelRequested:
        if record.record_type != "child_run_cancel_requested" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run cancellation request schema")
    elif type(record) is ChildRunCancelledTerminal:
        if record.record_type != "child_run_cancelled_terminal" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run terminal cancellation schema")
    elif type(record) is ChildRunInterrupted:
        if record.record_type != "child_run_interrupted" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run interruption schema")
    elif type(record) is ChildRunHandoffPublished:
        if record.record_type != "child_run_handoff_published" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run handoff schema")
    else:
        raise ChildRunRecordError("unknown Child Run record type")


def validate_child_run_record(record: ChildRunRecord) -> None:
    _record_identity(record)
    if type(record.sequence) is not int or record.sequence < 0:
        raise ChildRunRecordError("Child Run record sequence is invalid")
    canonical_child_run_id(record.child_run_id)
    if isinstance(record, ChildRunHeader):
        if record.sequence != 0:
            raise ChildRunRecordError("Child Run header sequence is invalid")
        _workspace(record.workspace)
        _fingerprint(record.workspace_fingerprint)
        canonical_child_run_id(record.parent_session_id)
        canonical_child_run_objective(record.objective)
        canonical_child_run_timestamp(record.created_at, "Child Run created_at")
    elif isinstance(record, ChildRunCancelled):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run cancellation sequence is invalid")
        canonical_child_run_reason(record.reason)
        canonical_child_run_timestamp(record.cancelled_at, "Child Run cancelled_at")
    elif isinstance(record, ChildRunDelegated):
        if record.sequence != 1:
            raise ChildRunRecordError("Child Run delegation sequence is invalid")
        canonical_child_run_id(record.parent_session_id)
        if re.fullmatch(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}", record.parent_context_id) is None:
            raise ChildRunRecordError("Child Run parent context identity is invalid")
        _canonical_text(record.parent_tool_use_id, "Child Run parent ToolUse ID", 4096, 16384)
        if type(record.decision_record_sequence) is not int or record.decision_record_sequence < 1:
            raise ChildRunRecordError("Child Run decision record sequence is invalid")
        if re.fullmatch(r"[0-9a-f]{64}", record.decision_sha256) is None:
            raise ChildRunRecordError("Child Run decision digest is invalid")
        if record.depth != 1 or record.source != "model":
            raise ChildRunRecordError("Child Run delegation source or depth is invalid")
        canonical_child_run_timestamp(record.delegated_at, "Child Run delegated_at")
    if isinstance(record, ChildRunAdmitted):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run admission sequence is invalid")
        canonical_child_run_id(record.parent_session_id)
        canonical_child_run_id(record.child_session_id)
        _canonical_text(record.permission_mode, "Child Run permission mode", 64, 256)
        _canonical_text(record.approval_mode, "Child Run approval mode", 64, 256)
        if not isinstance(record.provider_binding, dict):
            raise ChildRunRecordError("Child Run provider binding is invalid")
        if not record.provider_binding or any(
            not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None)))
            for key, value in record.provider_binding.items()
        ):
            raise ChildRunRecordError("Child Run provider binding is invalid")
        _canonical_text(record.tool_registry_id, "Child Run tool registry ID", 256, 1024)
        if type(record.tool_registry_generation) is not int or record.tool_registry_generation < 1:
            raise ChildRunRecordError("Child Run tool registry generation is invalid")
        _canonical_text(record.tool_set_id, "Child Run tool set ID", 256, 1024)
        if (
            not isinstance(record.tool_names, tuple)
            or not record.tool_names
            or len(set(record.tool_names)) != len(record.tool_names)
            or any(
                not isinstance(name, str) or not name.isascii() or not name
                for name in record.tool_names
            )
        ):
            raise ChildRunRecordError("Child Run tool names are invalid")
        if type(record.role_contract_version) is not int or record.role_contract_version < 1:
            raise ChildRunRecordError("Child Run role contract version is invalid")
        _canonical_text(
            record.role_prompt_fingerprint, "Child Run role prompt fingerprint", 128, 256
        )
        for value, label in (
            (record.max_provider_invocations, "provider invocation budget"),
            (record.max_tool_requests, "tool request budget"),
            (record.max_output_tokens, "output token budget"),
            (record.deadline_seconds, "deadline"),
        ):
            if type(value) is not int or value < 1:
                raise ChildRunRecordError(f"Child Run {label} is invalid")
        canonical_child_run_timestamp(record.admitted_at, "Child Run admitted_at")
    if isinstance(record, ChildSessionBound):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Session binding sequence is invalid")
        canonical_child_run_id(record.child_session_id)
        if type(record.session_header_sequence) is not int or record.session_header_sequence != 0:
            raise ChildRunRecordError("Child Session header sequence is invalid")
        _canonical_text(record.session_path, "Child Session path", 4096, 16 * 1024)
        canonical_child_run_timestamp(record.bound_at, "Child Session bound_at")
    if isinstance(record, ChildRunPreparationFailed):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run preparation failure sequence is invalid")
        _canonical_text(record.phase, "Child Run failure phase", 64, 256)
        _canonical_text(record.result_code, "Child Run failure result code", 128, 512)
        _canonical_text(record.message, "Child Run failure message", 1024, 4096)
        canonical_child_run_timestamp(record.failed_at, "Child Run failed_at")
    if isinstance(record, ChildRunStarted):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run started sequence is invalid")
        canonical_child_run_id(record.child_session_id)
        canonical_child_run_id(record.execution_id)
        canonical_child_run_timestamp(record.started_at, "Child Run started_at")
    if isinstance(record, ChildRunCompleted):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run completed sequence is invalid")
        canonical_child_run_id(record.execution_id)
        if type(record.session_record_sequence) is not int or record.session_record_sequence < 1:
            raise ChildRunRecordError("Child Run Session record sequence is invalid")
        _sha256(record.assistant_text_sha256, "Child Run assistant text digest")
        canonical_child_run_timestamp(record.completed_at, "Child Run completed_at")
    if isinstance(record, ChildRunFailed):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run failed sequence is invalid")
        if record.execution_id is not None:
            canonical_child_run_id(record.execution_id)
        _canonical_text(record.phase, "Child Run failure phase", 64, 256)
        _canonical_text(record.result_code, "Child Run failure result code", 128, 512)
        _canonical_text(record.message, "Child Run failure message", 1024, 4096)
        canonical_child_run_timestamp(record.failed_at, "Child Run failed_at")
    if isinstance(record, ChildRunCancelRequested):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run cancellation request sequence is invalid")
        if record.execution_id is not None:
            canonical_child_run_id(record.execution_id)
        canonical_child_run_reason(record.reason)
        _canonical_text(record.source, "Child Run cancellation source", 32, 128)
        if record.source not in {"host", "shutdown", "deadline", "model"}:
            raise ChildRunRecordError("Child Run cancellation source is invalid")
        canonical_child_run_timestamp(record.requested_at, "Child Run cancellation requested_at")
    if isinstance(record, ChildRunCancelledTerminal):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run terminal cancellation sequence is invalid")
        if record.execution_id is not None:
            canonical_child_run_id(record.execution_id)
        if type(record.cancel_request_sequence) is not int or record.cancel_request_sequence < 1:
            raise ChildRunRecordError("Child Run cancellation request sequence is invalid")
        _canonical_text(record.result_code, "Child Run cancellation result code", 128, 512)
        canonical_child_run_timestamp(record.observed_at, "Child Run cancellation observed_at")
    if isinstance(record, ChildRunInterrupted):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run interruption sequence is invalid")
        canonical_child_run_id(record.execution_id)
        _canonical_text(record.last_durable_state, "Child Run last durable state", 32, 128)
        if record.last_durable_state not in {"running", "cancelling"}:
            raise ChildRunRecordError("Child Run last durable state is invalid")
        if record.lock_protocol != "v2":
            raise ChildRunRecordError("Child Run interruption lock protocol is invalid")
        _canonical_text(record.result_code, "Child Run interruption result code", 128, 512)
        canonical_child_run_timestamp(record.interrupted_at, "Child Run interrupted_at")
    if isinstance(record, ChildRunHandoffPublished):
        if record.sequence < 1:
            raise ChildRunRecordError("Child Run handoff sequence is invalid")
        canonical_child_run_id(record.parent_session_id)
        if record.child_session_id is not None:
            canonical_child_run_id(record.child_session_id)
        _canonical_text(record.outcome, "Child Run handoff outcome", 32, 128)
        if record.outcome not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ChildRunRecordError("Child Run handoff outcome is invalid")
        if type(record.terminal_record_sequence) is not int or record.terminal_record_sequence < 1:
            raise ChildRunRecordError("Child Run handoff terminal sequence is invalid")
        _canonical_text(record.terminal_record_type, "Child Run handoff terminal type", 64, 256)
        _canonical_text(record.result_code, "Child Run handoff result code", 128, 512)
        _sha256(record.source_text_sha256, "Child Run handoff source digest")
        if not isinstance(record.body, str) or len(record.body) > MAX_CHILD_HANDOFF_BODY_CHARACTERS:
            raise ChildRunRecordError("Child Run handoff body exceeds its character bound")
        try:
            body_bytes = record.body.encode("utf-8")
        except UnicodeEncodeError:
            raise ChildRunRecordError("Child Run handoff body is not valid UTF-8") from None
        if len(body_bytes) > MAX_CHILD_HANDOFF_BODY_BYTES or "\x00" in record.body:
            raise ChildRunRecordError("Child Run handoff body exceeds its UTF-8 bound")
        _sha256(record.body_sha256, "Child Run handoff body digest")
        if type(record.truncated) is not bool:
            raise ChildRunRecordError("Child Run handoff truncated flag is invalid")
        if record.child_turn_record_sequence is not None and (
            type(record.child_turn_record_sequence) is not int
            or record.child_turn_record_sequence < 1
        ):
            raise ChildRunRecordError("Child Run handoff Child Turn sequence is invalid")
        if (record.child_turn_record_sequence is None) != (record.child_turn_record_sha256 is None):
            raise ChildRunRecordError("Child Run handoff Child Turn evidence is incomplete")
        if record.child_turn_record_sha256 is not None:
            _sha256(record.child_turn_record_sha256, "Child Run handoff Child Turn digest")
        _sha256(record.handoff_sha256, "Child Run handoff digest")
        if record.handoff_sha256 != _handoff_manifest_digest(record):
            raise ChildRunRecordError("Child Run handoff digest does not match its manifest")
        canonical_child_run_timestamp(record.published_at, "Child Run handoff published_at")


def _mapping(record: ChildRunRecord) -> dict[str, object]:
    validate_child_run_record(record)
    if isinstance(record, ChildRunHeader):
        return {
            "child_run_id": record.child_run_id,
            "created_at": record.created_at,
            "objective": record.objective,
            "parent_session_id": record.parent_session_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "workspace": record.workspace,
            "workspace_fingerprint": record.workspace_fingerprint,
        }
    if isinstance(record, ChildRunCancelled):
        return {
            "cancelled_at": record.cancelled_at,
            "child_run_id": record.child_run_id,
            "reason": record.reason,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
        }
    if isinstance(record, ChildRunDelegated):
        return {
            "child_run_id": record.child_run_id,
            "decision_record_sequence": record.decision_record_sequence,
            "decision_sha256": record.decision_sha256,
            "delegated_at": record.delegated_at,
            "depth": record.depth,
            "parent_context_id": record.parent_context_id,
            "parent_session_id": record.parent_session_id,
            "parent_tool_use_id": record.parent_tool_use_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "source": record.source,
        }
    if isinstance(record, ChildRunAdmitted):
        return {
            "approval_mode": record.approval_mode,
            "admitted_at": record.admitted_at,
            "child_run_id": record.child_run_id,
            "child_session_id": record.child_session_id,
            "deadline_seconds": record.deadline_seconds,
            "max_output_tokens": record.max_output_tokens,
            "max_provider_invocations": record.max_provider_invocations,
            "max_tool_requests": record.max_tool_requests,
            "parent_session_id": record.parent_session_id,
            "permission_mode": record.permission_mode,
            "provider_binding": record.provider_binding,
            "record_type": record.record_type,
            "role_contract_version": record.role_contract_version,
            "role_prompt_fingerprint": record.role_prompt_fingerprint,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "tool_names": list(record.tool_names),
            "tool_registry_generation": record.tool_registry_generation,
            "tool_registry_id": record.tool_registry_id,
            "tool_set_id": record.tool_set_id,
        }
    if isinstance(record, ChildSessionBound):
        return {
            "bound_at": record.bound_at,
            "child_run_id": record.child_run_id,
            "child_session_id": record.child_session_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "session_header_sequence": record.session_header_sequence,
            "session_path": record.session_path,
        }
    if isinstance(record, ChildRunStarted):
        return {
            "child_run_id": record.child_run_id,
            "child_session_id": record.child_session_id,
            "execution_id": record.execution_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "started_at": record.started_at,
        }
    if isinstance(record, ChildRunCompleted):
        return {
            "assistant_text_sha256": record.assistant_text_sha256,
            "child_run_id": record.child_run_id,
            "completed_at": record.completed_at,
            "execution_id": record.execution_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "session_record_sequence": record.session_record_sequence,
        }
    if isinstance(record, ChildRunFailed):
        return {
            "child_run_id": record.child_run_id,
            "execution_id": record.execution_id,
            "failed_at": record.failed_at,
            "message": record.message,
            "phase": record.phase,
            "record_type": record.record_type,
            "result_code": record.result_code,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
        }
    if isinstance(record, ChildRunCancelRequested):
        return {
            "child_run_id": record.child_run_id,
            "execution_id": record.execution_id,
            "reason": record.reason,
            "record_type": record.record_type,
            "requested_at": record.requested_at,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "source": record.source,
        }
    if isinstance(record, ChildRunCancelledTerminal):
        return {
            "cancel_request_sequence": record.cancel_request_sequence,
            "child_run_id": record.child_run_id,
            "execution_id": record.execution_id,
            "observed_at": record.observed_at,
            "record_type": record.record_type,
            "result_code": record.result_code,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
        }
    if isinstance(record, ChildRunInterrupted):
        return {
            "child_run_id": record.child_run_id,
            "execution_id": record.execution_id,
            "interrupted_at": record.interrupted_at,
            "last_durable_state": record.last_durable_state,
            "lock_protocol": record.lock_protocol,
            "record_type": record.record_type,
            "result_code": record.result_code,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
        }
    if isinstance(record, ChildRunHandoffPublished):
        return {
            "body": record.body,
            "body_sha256": record.body_sha256,
            "child_run_id": record.child_run_id,
            "child_session_id": record.child_session_id,
            "child_turn_record_sequence": record.child_turn_record_sequence,
            "child_turn_record_sha256": record.child_turn_record_sha256,
            "handoff_sha256": record.handoff_sha256,
            "outcome": record.outcome,
            "parent_session_id": record.parent_session_id,
            "published_at": record.published_at,
            "record_type": record.record_type,
            "result_code": record.result_code,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "source_text_sha256": record.source_text_sha256,
            "terminal_record_sequence": record.terminal_record_sequence,
            "terminal_record_type": record.terminal_record_type,
            "truncated": record.truncated,
        }
    return {
        "child_run_id": record.child_run_id,
        "failed_at": record.failed_at,
        "message": record.message,
        "phase": record.phase,
        "record_type": record.record_type,
        "result_code": record.result_code,
        "schema_version": record.schema_version,
        "sequence": record.sequence,
    }


def encode_child_run_record(record: ChildRunRecord) -> bytes:
    try:
        payload = (
            json.dumps(
                _mapping(record),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except ChildRunRecordError:
        raise
    except (TypeError, ValueError):
        raise ChildRunRecordError("Child Run record is not JSON encodable") from None
    if len(payload) > MAX_CHILD_RUN_RECORD_BYTES:
        raise ChildRunRecordError(f"Child Run record exceeds {MAX_CHILD_RUN_RECORD_BYTES} bytes")
    return payload


def decode_child_run_record(payload: bytes) -> ChildRunRecord:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CHILD_RUN_RECORD_BYTES:
        raise ChildRunRecordError("Child Run record is empty or oversized")
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or b"\n" in payload or b"\r" in payload:
        raise ChildRunRecordError("Child Run record must occupy one JSONL line")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ChildRunRecordError("Child Run record is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise ChildRunRecordError("Child Run record must be a JSON object")
    record_type = value.get("record_type")
    if record_type == "child_run_header":
        expected = {
            "child_run_id",
            "created_at",
            "objective",
            "parent_session_id",
            "record_type",
            "schema_version",
            "sequence",
            "workspace",
            "workspace_fingerprint",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run header has unknown or missing fields")
        record: ChildRunRecord = ChildRunHeader(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            workspace=value["workspace"],
            workspace_fingerprint=value["workspace_fingerprint"],
            parent_session_id=value["parent_session_id"],
            objective=value["objective"],
            created_at=value["created_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_cancelled":
        expected = {
            "cancelled_at",
            "child_run_id",
            "reason",
            "record_type",
            "schema_version",
            "sequence",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run cancellation has unknown or missing fields")
        record = ChildRunCancelled(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            reason=value["reason"],
            cancelled_at=value["cancelled_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_admitted":
        expected = {
            "approval_mode",
            "admitted_at",
            "child_run_id",
            "child_session_id",
            "deadline_seconds",
            "max_output_tokens",
            "max_provider_invocations",
            "max_tool_requests",
            "parent_session_id",
            "permission_mode",
            "provider_binding",
            "record_type",
            "role_contract_version",
            "role_prompt_fingerprint",
            "schema_version",
            "sequence",
            "tool_names",
            "tool_registry_generation",
            "tool_registry_id",
            "tool_set_id",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run admission has unknown or missing fields")
        record = ChildRunAdmitted(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            parent_session_id=value["parent_session_id"],
            child_session_id=value["child_session_id"],
            permission_mode=value["permission_mode"],
            approval_mode=value["approval_mode"],
            provider_binding=value["provider_binding"],
            tool_registry_id=value["tool_registry_id"],
            tool_registry_generation=value["tool_registry_generation"],
            tool_set_id=value["tool_set_id"],
            tool_names=tuple(value["tool_names"]),
            role_contract_version=value["role_contract_version"],
            role_prompt_fingerprint=value["role_prompt_fingerprint"],
            max_provider_invocations=value["max_provider_invocations"],
            max_tool_requests=value["max_tool_requests"],
            max_output_tokens=value["max_output_tokens"],
            deadline_seconds=value["deadline_seconds"],
            admitted_at=value["admitted_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_delegated":
        expected = {
            "child_run_id",
            "decision_record_sequence",
            "decision_sha256",
            "delegated_at",
            "depth",
            "parent_context_id",
            "parent_session_id",
            "parent_tool_use_id",
            "record_type",
            "schema_version",
            "sequence",
            "source",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run delegation has unknown or missing fields")
        record = ChildRunDelegated(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            parent_session_id=value["parent_session_id"],
            parent_context_id=value["parent_context_id"],
            parent_tool_use_id=value["parent_tool_use_id"],
            decision_record_sequence=value["decision_record_sequence"],
            decision_sha256=value["decision_sha256"],
            depth=value["depth"],
            source=value["source"],
            delegated_at=value["delegated_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_session_bound":
        expected = {
            "bound_at",
            "child_run_id",
            "child_session_id",
            "record_type",
            "schema_version",
            "sequence",
            "session_header_sequence",
            "session_path",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Session binding has unknown or missing fields")
        record = ChildSessionBound(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            child_session_id=value["child_session_id"],
            session_header_sequence=value["session_header_sequence"],
            session_path=value["session_path"],
            bound_at=value["bound_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_preparation_failed":
        expected = {
            "child_run_id",
            "failed_at",
            "message",
            "phase",
            "record_type",
            "result_code",
            "schema_version",
            "sequence",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run preparation failure has unknown or missing fields")
        record = ChildRunPreparationFailed(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            phase=value["phase"],
            result_code=value["result_code"],
            message=value["message"],
            failed_at=value["failed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_started":
        expected = {
            "child_run_id",
            "child_session_id",
            "execution_id",
            "record_type",
            "schema_version",
            "sequence",
            "started_at",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run started record has unknown or missing fields")
        record = ChildRunStarted(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            child_session_id=value["child_session_id"],
            execution_id=value["execution_id"],
            started_at=value["started_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_completed":
        expected = {
            "assistant_text_sha256",
            "child_run_id",
            "completed_at",
            "execution_id",
            "record_type",
            "schema_version",
            "sequence",
            "session_record_sequence",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run completed record has unknown or missing fields")
        record = ChildRunCompleted(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            execution_id=value["execution_id"],
            session_record_sequence=value["session_record_sequence"],
            assistant_text_sha256=value["assistant_text_sha256"],
            completed_at=value["completed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_failed":
        expected = {
            "child_run_id",
            "execution_id",
            "failed_at",
            "message",
            "phase",
            "record_type",
            "result_code",
            "schema_version",
            "sequence",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run failed record has unknown or missing fields")
        record = ChildRunFailed(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            execution_id=value["execution_id"],
            phase=value["phase"],
            result_code=value["result_code"],
            message=value["message"],
            failed_at=value["failed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_cancel_requested":
        expected = {
            "child_run_id",
            "execution_id",
            "reason",
            "record_type",
            "requested_at",
            "schema_version",
            "sequence",
            "source",
        }
        if set(value) != expected:
            raise ChildRunRecordError(
                "Child Run cancellation request has unknown or missing fields"
            )
        record = ChildRunCancelRequested(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            execution_id=value["execution_id"],
            reason=value["reason"],
            source=value["source"],
            requested_at=value["requested_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_cancelled_terminal":
        expected = {
            "cancel_request_sequence",
            "child_run_id",
            "execution_id",
            "observed_at",
            "record_type",
            "result_code",
            "schema_version",
            "sequence",
        }
        if set(value) != expected:
            raise ChildRunRecordError(
                "Child Run terminal cancellation has unknown or missing fields"
            )
        record = ChildRunCancelledTerminal(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            execution_id=value["execution_id"],
            cancel_request_sequence=value["cancel_request_sequence"],
            result_code=value["result_code"],
            observed_at=value["observed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_interrupted":
        expected = {
            "child_run_id",
            "execution_id",
            "interrupted_at",
            "last_durable_state",
            "lock_protocol",
            "record_type",
            "result_code",
            "schema_version",
            "sequence",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run interruption has unknown or missing fields")
        record = ChildRunInterrupted(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            execution_id=value["execution_id"],
            last_durable_state=value["last_durable_state"],
            lock_protocol=value["lock_protocol"],
            result_code=value["result_code"],
            interrupted_at=value["interrupted_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "child_run_handoff_published":
        expected = {
            "body",
            "body_sha256",
            "child_run_id",
            "child_session_id",
            "child_turn_record_sequence",
            "child_turn_record_sha256",
            "handoff_sha256",
            "outcome",
            "parent_session_id",
            "published_at",
            "record_type",
            "result_code",
            "schema_version",
            "sequence",
            "source_text_sha256",
            "terminal_record_sequence",
            "terminal_record_type",
            "truncated",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run handoff has unknown or missing fields")
        record = ChildRunHandoffPublished(
            sequence=value["sequence"],
            child_run_id=value["child_run_id"],
            parent_session_id=value["parent_session_id"],
            child_session_id=value["child_session_id"],
            outcome=value["outcome"],
            terminal_record_sequence=value["terminal_record_sequence"],
            terminal_record_type=value["terminal_record_type"],
            result_code=value["result_code"],
            source_text_sha256=value["source_text_sha256"],
            body=value["body"],
            body_sha256=value["body_sha256"],
            truncated=value["truncated"],
            child_turn_record_sequence=value["child_turn_record_sequence"],
            child_turn_record_sha256=value["child_turn_record_sha256"],
            handoff_sha256=value["handoff_sha256"],
            published_at=value["published_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    else:
        raise ChildRunRecordError("unknown Child Run record type")
    validate_child_run_record(record)
    return record


def replay_child_run_records(
    records: list[ChildRunRecord] | tuple[ChildRunRecord, ...],
) -> ChildRunReplayState:
    if not isinstance(records, (list, tuple)) or not records:
        raise ChildRunRecordError("Child Run transcript must contain a header")
    if len(records) > MAX_CHILD_RUN_RECORDS:
        raise ChildRunRecordError(f"Child Run transcript exceeds {MAX_CHILD_RUN_RECORDS} records")
    normalized = tuple(records)
    for index, record in enumerate(normalized):
        if not isinstance(
            record,
            (
                ChildRunHeader,
                ChildRunCancelled,
                ChildRunDelegated,
                ChildRunAdmitted,
                ChildSessionBound,
                ChildRunPreparationFailed,
                ChildRunStarted,
                ChildRunCompleted,
                ChildRunFailed,
                ChildRunCancelRequested,
                ChildRunCancelledTerminal,
                ChildRunInterrupted,
                ChildRunHandoffPublished,
            ),
        ):
            raise ChildRunRecordError("unknown Child Run record type")
        validate_child_run_record(record)
        _sequence(record.sequence, index)
    header = normalized[0]
    if not isinstance(header, ChildRunHeader):
        raise ChildRunRecordError("Child Run transcript must begin with a header")
    cancelled = delegated = admitted = session_bound = preparation_failed = None
    started = completed = failed = None
    cancel_requested = cancelled_terminal = interrupted = handoff = None
    for record in normalized[1:]:
        if record.child_run_id != header.child_run_id:
            raise ChildRunRecordError("Child Run record ID does not match header")
        if isinstance(record, ChildRunCancelled):
            if (
                cancelled is not None
                or admitted is not None
                or record.cancelled_at < header.created_at
            ):
                raise ChildRunRecordError("Child Run cancellation is out of order")
            cancelled = record
        elif isinstance(record, ChildRunDelegated):
            if delegated is not None or admitted is not None or record is not normalized[1]:
                raise ChildRunRecordError("Child Run delegation is out of order")
            if record.parent_session_id != header.parent_session_id:
                raise ChildRunRecordError("Child Run delegation owner does not match header")
            delegated = record
        elif isinstance(record, ChildRunAdmitted):
            if (
                admitted is not None
                or cancelled is not None
                or len(normalized) < 2
                or record.parent_session_id != header.parent_session_id
            ):
                raise ChildRunRecordError("Child Run admission is invalid")
            admitted = record
        elif isinstance(record, ChildSessionBound):
            if (
                admitted is None
                or session_bound is not None
                or record.child_session_id != admitted.child_session_id
            ):
                raise ChildRunRecordError("Child Session binding is invalid")
            session_bound = record
        elif isinstance(record, ChildRunPreparationFailed):
            if preparation_failed is not None or session_bound is not None or started is not None:
                raise ChildRunRecordError("Child Run preparation failure is invalid")
            preparation_failed = record
        elif isinstance(record, ChildRunStarted):
            if (
                started is not None
                or admitted is None
                or session_bound is None
                or record.child_session_id != admitted.child_session_id
            ):
                raise ChildRunRecordError("Child Run started record is invalid")
            started = record
        elif isinstance(record, ChildRunCompleted):
            if (
                completed is not None
                or started is None
                or record.execution_id != started.execution_id
            ):
                raise ChildRunRecordError("Child Run completed record is invalid")
            completed = record
        elif isinstance(record, ChildRunFailed):
            if failed is not None or completed is not None or preparation_failed is not None:
                raise ChildRunRecordError("Child Run failed record is invalid")
            if started is not None and record.execution_id != started.execution_id:
                raise ChildRunRecordError("Child Run failed execution ID is invalid")
            failed = record
        elif isinstance(record, ChildRunCancelRequested):
            if (
                cancel_requested is not None
                or completed is not None
                or failed is not None
                or (started is None and session_bound is None)
                or (started is not None and record.execution_id != started.execution_id)
                or (started is None and record.execution_id is not None)
            ):
                raise ChildRunRecordError("Child Run cancellation request is invalid")
            cancel_requested = record
        elif isinstance(record, ChildRunCancelledTerminal):
            if (
                cancelled_terminal is not None
                or cancel_requested is None
                or completed is not None
                or failed is not None
                or record.execution_id != cancel_requested.execution_id
                or record.cancel_request_sequence != cancel_requested.sequence
            ):
                raise ChildRunRecordError("Child Run terminal cancellation is invalid")
            cancelled_terminal = record
        elif isinstance(record, ChildRunInterrupted):
            if (
                interrupted is not None
                or started is None
                or completed is not None
                or failed is not None
                or record.execution_id != started.execution_id
                or record.last_durable_state
                not in {ChildRunStatus.RUNNING.value, ChildRunStatus.CANCELLING.value}
            ):
                raise ChildRunRecordError("Child Run interruption is invalid")
            if (
                record.last_durable_state == ChildRunStatus.CANCELLING.value
                and cancel_requested is None
            ):
                raise ChildRunRecordError("Child Run cancelling interruption lacks request")
            interrupted = record
        elif isinstance(record, ChildRunHandoffPublished):
            terminal = (
                completed
                or failed
                or preparation_failed
                or cancelled_terminal
                or interrupted
                or cancelled
            )
            terminal_result_code = (
                "completed"
                if completed is not None
                else failed.result_code
                if failed is not None
                else preparation_failed.result_code
                if preparation_failed is not None
                else cancelled_terminal.result_code
                if cancelled_terminal is not None
                else interrupted.result_code
                if interrupted is not None
                else "cancelled_before_start"
            )
            if (
                handoff is not None
                or terminal is None
                or record.parent_session_id != header.parent_session_id
                or record.child_session_id != (admitted.child_session_id if admitted else None)
                or record.terminal_record_sequence != terminal.sequence
                or record.terminal_record_type != terminal.record_type
                or record.result_code != terminal_result_code
                or record.outcome
                != (
                    ChildRunStatus.COMPLETED.value
                    if completed is not None
                    else ChildRunStatus.FAILED.value
                    if failed is not None or preparation_failed is not None
                    else ChildRunStatus.INTERRUPTED.value
                    if interrupted is not None
                    else ChildRunStatus.CANCELLED.value
                )
                or record.body_sha256 != hashlib.sha256(record.body.encode("utf-8")).hexdigest()
                or (completed is not None) != (record.child_turn_record_sequence is not None)
                or (
                    completed is not None
                    and (
                        record.source_text_sha256 != completed.assistant_text_sha256
                        or record.child_turn_record_sequence != completed.session_record_sequence
                    )
                )
                or (
                    completed is None
                    and (
                        record.body
                        != f"Child Run ended with outcome {record.outcome} ({record.result_code})."
                        or record.source_text_sha256 != record.body_sha256
                        or record.truncated
                    )
                )
            ):
                raise ChildRunRecordError("Child Run handoff publication is invalid")
            handoff = record
        else:
            raise ChildRunRecordError("Child Run transcript contains an unsupported record")
    if cancelled is not None and normalized[-1] not in {cancelled, handoff}:
        raise ChildRunRecordError("Child Run cancellation must be terminal")
    if preparation_failed is not None and len(normalized) not in {2, 3}:
        raise ChildRunRecordError("Child Run preparation failure must be terminal")
    if completed is not None and failed is not None:
        raise ChildRunRecordError("Child Run has multiple terminal outcomes")
    if cancelled_terminal is not None and (completed is not None or failed is not None):
        raise ChildRunRecordError("Child Run has multiple terminal outcomes")
    if interrupted is not None and (completed is not None or failed is not None):
        raise ChildRunRecordError("Child Run has multiple terminal outcomes")
    if (
        cancelled_terminal is not None
        and cancelled_terminal.execution_id is None
        and started is not None
    ):
        raise ChildRunRecordError("Child Run ready cancellation has an execution ID")
    if session_bound is not None and admitted is None:
        raise ChildRunRecordError("Child Session binding requires admission")
    return ChildRunReplayState(
        header,
        cancelled,
        delegated,
        admitted,
        session_bound,
        preparation_failed,
        started,
        completed,
        failed,
        cancel_requested,
        cancelled_terminal,
        interrupted,
        handoff,
        normalized,
    )


def child_run_workspace_fingerprint(workspace: Path) -> str:
    return workspace_fingerprint(workspace)
