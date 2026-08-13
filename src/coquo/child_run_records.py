"""Closed, versioned records for the durable Child Run control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
import re
import unicodedata
from uuid import UUID

from coquo.session_records import workspace_fingerprint

CHILD_RUN_HEADER_SCHEMA_VERSION = 1
CHILD_RUN_CANCELLED_SCHEMA_VERSION = 1
CHILD_RUN_ADMITTED_SCHEMA_VERSION = 1
CHILD_SESSION_BOUND_SCHEMA_VERSION = 1
CHILD_RUN_PREPARATION_FAILED_SCHEMA_VERSION = 1
CHILD_RUN_STARTED_SCHEMA_VERSION = 1
CHILD_RUN_COMPLETED_SCHEMA_VERSION = 1
CHILD_RUN_FAILED_SCHEMA_VERSION = 1
MAX_CHILD_RUN_RECORD_BYTES = 64 * 1024
MAX_CHILD_RUN_RECORDS = 10_000
MAX_CHILD_RUN_TRANSCRIPT_BYTES = 1024 * 1024
MAX_CHILD_RUN_OBJECTIVE_CHARACTERS = 4096
MAX_CHILD_RUN_OBJECTIVE_BYTES = 16 * 1024
MAX_CHILD_RUN_REASON_CHARACTERS = 4096
MAX_CHILD_RUN_REASON_BYTES = 16 * 1024

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


ChildRunRecord = (
    ChildRunHeader
    | ChildRunCancelled
    | ChildRunAdmitted
    | ChildSessionBound
    | ChildRunPreparationFailed
    | ChildRunStarted
    | ChildRunCompleted
    | ChildRunFailed
)


@dataclass(frozen=True)
class ChildRunReplayState:
    header: ChildRunHeader
    cancelled: ChildRunCancelled | None
    admitted: ChildRunAdmitted | None
    session_bound: ChildSessionBound | None
    preparation_failed: ChildRunPreparationFailed | None
    started: ChildRunStarted | None
    completed: ChildRunCompleted | None
    failed: ChildRunFailed | None
    records: tuple[ChildRunRecord, ...]

    @property
    def status(self) -> ChildRunStatus:
        if self.cancelled is not None:
            return ChildRunStatus.CANCELLED
        if self.completed is not None:
            return ChildRunStatus.COMPLETED
        if self.failed is not None or self.preparation_failed is not None:
            return ChildRunStatus.FAILED
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


def _record_identity(record: ChildRunRecord) -> None:
    if type(record) is ChildRunHeader:
        if record.record_type != "child_run_header" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run header schema")
    elif type(record) is ChildRunCancelled:
        if record.record_type != "child_run_cancelled" or record.schema_version != 1:
            raise ChildRunRecordError("unsupported Child Run cancellation schema")
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
        if record.sequence != 1:
            raise ChildRunRecordError("Child Run cancellation sequence is invalid")
        canonical_child_run_reason(record.reason)
        canonical_child_run_timestamp(record.cancelled_at, "Child Run cancelled_at")
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
                ChildRunAdmitted,
                ChildSessionBound,
                ChildRunPreparationFailed,
                ChildRunStarted,
                ChildRunCompleted,
                ChildRunFailed,
            ),
        ):
            raise ChildRunRecordError("unknown Child Run record type")
        validate_child_run_record(record)
        _sequence(record.sequence, index)
    header = normalized[0]
    if not isinstance(header, ChildRunHeader):
        raise ChildRunRecordError("Child Run transcript must begin with a header")
    cancelled = admitted = session_bound = preparation_failed = None
    started = completed = failed = None
    for record in normalized[1:]:
        if record.child_run_id != header.child_run_id:
            raise ChildRunRecordError("Child Run record ID does not match header")
        if isinstance(record, ChildRunCancelled):
            if len(normalized) != 2 or record.cancelled_at < header.created_at:
                raise ChildRunRecordError("Child Run cancellation is out of order")
            cancelled = record
        elif isinstance(record, ChildRunAdmitted):
            if (
                admitted is not None
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
        else:
            raise ChildRunRecordError("Child Run transcript contains an unsupported record")
    if cancelled is not None and len(normalized) != 2:
        raise ChildRunRecordError("Child Run cancellation must be terminal")
    if preparation_failed is not None and len(normalized) not in {2, 3}:
        raise ChildRunRecordError("Child Run preparation failure must be terminal")
    if completed is not None and failed is not None:
        raise ChildRunRecordError("Child Run has multiple terminal outcomes")
    if session_bound is not None and admitted is None:
        raise ChildRunRecordError("Child Session binding requires admission")
    return ChildRunReplayState(
        header,
        cancelled,
        admitted,
        session_bound,
        preparation_failed,
        started,
        completed,
        failed,
        normalized,
    )


def child_run_workspace_fingerprint(workspace: Path) -> str:
    return workspace_fingerprint(workspace)
