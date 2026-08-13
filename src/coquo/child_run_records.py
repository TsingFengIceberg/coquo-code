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


ChildRunRecord = ChildRunHeader | ChildRunCancelled


@dataclass(frozen=True)
class ChildRunReplayState:
    header: ChildRunHeader
    cancelled: ChildRunCancelled | None
    records: tuple[ChildRunRecord, ...]

    @property
    def status(self) -> ChildRunStatus:
        return ChildRunStatus.CANCELLED if self.cancelled is not None else ChildRunStatus.QUEUED


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
    else:
        if record.sequence != 1:
            raise ChildRunRecordError("Child Run cancellation sequence is invalid")
        canonical_child_run_reason(record.reason)
        canonical_child_run_timestamp(record.cancelled_at, "Child Run cancelled_at")


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
    return {
        "cancelled_at": record.cancelled_at,
        "child_run_id": record.child_run_id,
        "reason": record.reason,
        "record_type": record.record_type,
        "schema_version": record.schema_version,
        "sequence": record.sequence,
    }


def encode_child_run_record(record: ChildRunRecord) -> bytes:
    try:
        payload = json.dumps(
            _mapping(record), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8") + b"\n"
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
            "child_run_id", "created_at", "objective", "parent_session_id", "record_type",
            "schema_version", "sequence", "workspace", "workspace_fingerprint",
        }
        if set(value) != expected:
            raise ChildRunRecordError("Child Run header has unknown or missing fields")
        record: ChildRunRecord = ChildRunHeader(
            sequence=value["sequence"], child_run_id=value["child_run_id"], workspace=value["workspace"],
            workspace_fingerprint=value["workspace_fingerprint"], parent_session_id=value["parent_session_id"],
            objective=value["objective"], created_at=value["created_at"],
            record_type=value["record_type"], schema_version=value["schema_version"],
        )
    elif record_type == "child_run_cancelled":
        expected = {"cancelled_at", "child_run_id", "reason", "record_type", "schema_version", "sequence"}
        if set(value) != expected:
            raise ChildRunRecordError("Child Run cancellation has unknown or missing fields")
        record = ChildRunCancelled(
            sequence=value["sequence"], child_run_id=value["child_run_id"], reason=value["reason"],
            cancelled_at=value["cancelled_at"], record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    else:
        raise ChildRunRecordError("unknown Child Run record type")
    validate_child_run_record(record)
    return record


def replay_child_run_records(records: list[ChildRunRecord] | tuple[ChildRunRecord, ...]) -> ChildRunReplayState:
    if not isinstance(records, (list, tuple)) or not records:
        raise ChildRunRecordError("Child Run transcript must contain a header")
    if len(records) > MAX_CHILD_RUN_RECORDS:
        raise ChildRunRecordError(f"Child Run transcript exceeds {MAX_CHILD_RUN_RECORDS} records")
    normalized = tuple(records)
    for index, record in enumerate(normalized):
        if not isinstance(record, (ChildRunHeader, ChildRunCancelled)):
            raise ChildRunRecordError("unknown Child Run record type")
        validate_child_run_record(record)
        _sequence(record.sequence, index)
    header = normalized[0]
    if not isinstance(header, ChildRunHeader):
        raise ChildRunRecordError("Child Run transcript must begin with a header")
    cancelled = None
    if len(normalized) == 2:
        cancelled = normalized[1]
        if not isinstance(cancelled, ChildRunCancelled):
            raise ChildRunRecordError("Child Run transcript terminal record is invalid")
        if cancelled.child_run_id != header.child_run_id:
            raise ChildRunRecordError("Child Run cancellation ID does not match header")
        if cancelled.cancelled_at < header.created_at:
            raise ChildRunRecordError("Child Run cancellation precedes creation")
    elif len(normalized) > 2:
        raise ChildRunRecordError("Child Run transcript contains an unsupported record")
    return ChildRunReplayState(header, cancelled, normalized)


def child_run_workspace_fingerprint(workspace: Path) -> str:
    return workspace_fingerprint(workspace)
