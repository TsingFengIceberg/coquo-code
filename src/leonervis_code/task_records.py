"""Closed versioned records for durable Leonervis Code tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
import re
from typing import TypeAlias
from uuid import UUID

from leonervis_code.session_records import (
    SessionRecordError,
    canonical_session_id,
    workspace_fingerprint,
)

TASK_HEADER_SCHEMA_VERSION = 1
STAGE_STARTED_SCHEMA_VERSION = 1
STAGE_COMMITTED_SCHEMA_VERSION = 1
STAGE_FAILED_SCHEMA_VERSION = 1
MAX_TASK_RECORD_BYTES = 64 * 1024
MAX_TASK_RECORDS = 10_000
MAX_TASK_OBJECTIVE_CHARACTERS = 4096
MAX_TASK_OBJECTIVE_BYTES = 16 * 1024
MAX_ACCEPTANCE_CRITERIA = 16
MAX_ACCEPTANCE_CRITERION_CHARACTERS = 1024
MAX_ACCEPTANCE_CRITERION_BYTES = 4096
MAX_TASK_TEXT_BYTES = 32 * 1024

_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_CANONICAL_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class TaskRecordError(ValueError):
    """Raised when a Task record or replay chain is invalid."""


class TaskScope(StrEnum):
    """Closed first-version scope for one durable Task."""

    WORKSPACE = "workspace"


class TaskStatus(StrEnum):
    """Derived durable Task lifecycle status."""

    READY = "ready"
    STAGE_IN_PROGRESS = "stage-in-progress"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"
    BLOCKED = "blocked"


class StageFailureReason(StrEnum):
    """Closed first-version reasons for a Stage without a committed Turn."""

    CANCELLED = "cancelled"
    PROVIDER_ERROR = "provider-error"
    TURN_NOT_COMMITTED = "turn-not-committed"
    HOST_ERROR = "host-error"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class TaskHeader:
    """Immutable identity and acceptance contract for one durable Task."""

    sequence: int
    task_id: str
    workspace: str
    workspace_fingerprint: str
    owner_session_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    created_at: str
    scope: TaskScope = TaskScope.WORKSPACE
    record_type: str = "task_header"
    schema_version: int = TASK_HEADER_SCHEMA_VERSION


@dataclass(frozen=True)
class StageStarted:
    """Durable barrier declaring one bounded Stage attempt."""

    sequence: int
    stage_id: str
    stage_number: int
    session_id: str
    objective: str
    started_at: str
    record_type: str = "stage_started"
    schema_version: int = STAGE_STARTED_SCHEMA_VERSION


@dataclass(frozen=True)
class StageCommitted:
    """Terminal Stage fact linked to one exact committed Session Turn record."""

    sequence: int
    stage_id: str
    stage_number: int
    session_id: str
    turn_number: int
    turn_record_sequence: int
    turn_record_sha256: str
    committed_at: str
    record_type: str = "stage_committed"
    schema_version: int = STAGE_COMMITTED_SCHEMA_VERSION


@dataclass(frozen=True)
class StageFailed:
    """Terminal Stage fact proving that no committed Turn advanced the Task."""

    sequence: int
    stage_id: str
    stage_number: int
    reason: StageFailureReason
    failed_at: str
    record_type: str = "stage_failed"
    schema_version: int = STAGE_FAILED_SCHEMA_VERSION


StageTerminal: TypeAlias = StageCommitted | StageFailed
TaskRecord: TypeAlias = TaskHeader | StageStarted | StageCommitted | StageFailed


@dataclass(frozen=True)
class TaskStageState:
    """One strictly replayed Stage attempt and its optional terminal record."""

    started: StageStarted
    terminal: StageTerminal | None


@dataclass(frozen=True)
class TaskReplayState:
    """Strictly replayed current Task state."""

    header: TaskHeader
    records: tuple[TaskRecord, ...]
    stages: tuple[TaskStageState, ...] = ()

    @property
    def task_id(self) -> str:
        return self.header.task_id

    @property
    def status(self) -> TaskStatus:
        if not self.stages:
            return TaskStatus.READY
        terminal = self.stages[-1].terminal
        if terminal is None:
            return TaskStatus.INTERRUPTED
        if isinstance(terminal, StageCommitted):
            return TaskStatus.PAUSED
        return TaskStatus.BLOCKED

    @property
    def active_stage(self) -> StageStarted | None:
        if self.stages and self.stages[-1].terminal is None:
            return self.stages[-1].started
        return None

    @property
    def next_stage_number(self) -> int:
        return len(self.stages) + 1

    @property
    def next_sequence(self) -> int:
        return len(self.records)


def canonical_task_id(value: object) -> str:
    """Return one exact lowercase canonical UUID4 Task identity."""
    if not isinstance(value, str):
        raise TaskRecordError("task ID must be a canonical UUID4 string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise TaskRecordError("task ID must be a canonical UUID4 string") from None
    if parsed.version != 4 or str(parsed) != value:
        raise TaskRecordError("task ID must be a canonical lowercase UUID4 string")
    return value


def canonical_stage_id(value: object) -> str:
    """Return one exact lowercase canonical UUID4 Stage identity."""
    if not isinstance(value, str):
        raise TaskRecordError("stage ID must be a canonical UUID4 string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise TaskRecordError("stage ID must be a canonical UUID4 string") from None
    if parsed.version != 4 or str(parsed) != value:
        raise TaskRecordError("stage ID must be a canonical lowercase UUID4 string")
    return value


def canonical_task_objective(value: object) -> str:
    """Validate one exact bounded nonblank Task objective."""
    return _bounded_text(
        value,
        "task objective",
        max_characters=MAX_TASK_OBJECTIVE_CHARACTERS,
        max_bytes=MAX_TASK_OBJECTIVE_BYTES,
    )


def canonical_stage_objective(value: object) -> str:
    """Validate one exact bounded nonblank Stage objective."""
    return _bounded_text(
        value,
        "stage objective",
        max_characters=MAX_TASK_OBJECTIVE_CHARACTERS,
        max_bytes=MAX_TASK_OBJECTIVE_BYTES,
    )


def canonical_acceptance_criteria(values: object) -> tuple[str, ...]:
    """Validate a bounded ordered set of exact acceptance criteria."""
    if not isinstance(values, (tuple, list)):
        raise TaskRecordError("acceptance criteria must be an array")
    if len(values) > MAX_ACCEPTANCE_CRITERIA:
        raise TaskRecordError(
            f"acceptance criteria exceed the {MAX_ACCEPTANCE_CRITERIA}-item limit"
        )
    criteria = tuple(
        _bounded_text(
            value,
            "acceptance criterion",
            max_characters=MAX_ACCEPTANCE_CRITERION_CHARACTERS,
            max_bytes=MAX_ACCEPTANCE_CRITERION_BYTES,
        )
        for value in values
    )
    if len(set(criteria)) != len(criteria):
        raise TaskRecordError("acceptance criteria must not contain duplicates")
    total_bytes = sum(len(value.encode("utf-8")) for value in criteria)
    if total_bytes > MAX_TASK_TEXT_BYTES:
        raise TaskRecordError(f"acceptance criteria exceed {MAX_TASK_TEXT_BYTES} total UTF-8 bytes")
    return criteria


def encode_task_record(record: TaskRecord) -> bytes:
    """Encode one validated Task record as canonical newline-terminated JSON."""
    value = _record_to_dict(record)
    payload = (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_TASK_RECORD_BYTES:
        raise TaskRecordError(f"task record exceeds {MAX_TASK_RECORD_BYTES} UTF-8 bytes")
    return payload


def decode_task_record(payload: bytes) -> TaskRecord:
    """Decode one non-newline JSON payload using the closed Task schema."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_TASK_RECORD_BYTES:
        raise TaskRecordError("task record payload is empty or oversized")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise TaskRecordError("task record is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise TaskRecordError("task record must be a JSON object")
    record_type = value.get("record_type")
    if record_type == "stage_started":
        return _decode_stage_started(value)
    if record_type == "stage_committed":
        return _decode_stage_committed(value)
    if record_type == "stage_failed":
        return _decode_stage_failed(value)
    if record_type != "task_header":
        raise TaskRecordError("unknown task record type")
    _require_fields(
        value,
        {
            "acceptance_criteria",
            "created_at",
            "objective",
            "owner_session_id",
            "record_type",
            "schema_version",
            "scope",
            "sequence",
            "task_id",
            "workspace",
            "workspace_fingerprint",
        },
        "task_header",
    )
    if value.get("schema_version") != TASK_HEADER_SCHEMA_VERSION:
        raise TaskRecordError("unsupported task_header schema version")
    try:
        scope = TaskScope(value.get("scope"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported task scope") from None
    acceptance = value.get("acceptance_criteria")
    if not isinstance(acceptance, list):
        raise TaskRecordError("acceptance criteria must be an array")
    record = TaskHeader(
        sequence=value.get("sequence"),
        task_id=value.get("task_id"),
        workspace=value.get("workspace"),
        workspace_fingerprint=value.get("workspace_fingerprint"),
        owner_session_id=value.get("owner_session_id"),
        objective=value.get("objective"),
        acceptance_criteria=tuple(acceptance),
        created_at=value.get("created_at"),
        scope=scope,
    )
    _validate_header(record)
    return record


def replay_task_records(
    records: list[TaskRecord] | tuple[TaskRecord, ...],
    *,
    expected_workspace: str,
    expected_workspace_fingerprint: str,
    expected_task_id: str,
    expected_file_name: str,
) -> TaskReplayState:
    """Strictly replay one complete Task transcript."""
    if not records:
        raise TaskRecordError("task transcript is empty")
    if len(records) > MAX_TASK_RECORDS:
        raise TaskRecordError(f"task transcript exceeds {MAX_TASK_RECORDS} records")
    if not isinstance(records[0], TaskHeader):
        raise TaskRecordError("task_header must be the first task record")
    header = records[0]
    _validate_header(header)
    canonical_expected = canonical_task_id(expected_task_id)
    if expected_file_name != f"{canonical_expected}.jsonl":
        raise TaskRecordError("task transcript file name does not match its task ID")
    if header.task_id != canonical_expected:
        raise TaskRecordError("task header ID does not match its transcript")
    if header.workspace != expected_workspace:
        raise TaskRecordError("task workspace does not match the current workspace")
    if header.workspace_fingerprint != expected_workspace_fingerprint:
        raise TaskRecordError("task workspace fingerprint does not match the current workspace")
    stages: list[TaskStageState] = []
    active: StageStarted | None = None
    previous_timestamp = header.created_at
    seen_stage_ids: set[str] = set()
    for expected_sequence, record in enumerate(records[1:], start=1):
        if record.sequence != expected_sequence:
            raise TaskRecordError(
                f"task record sequence must be {expected_sequence}, got {record.sequence}"
            )
        if isinstance(record, TaskHeader):
            raise TaskRecordError("task_header may appear only once")
        if isinstance(record, StageStarted):
            _validate_stage_started(record)
            if active is not None:
                raise TaskRecordError("a new Stage cannot start before the active Stage terminates")
            if record.stage_number != len(stages) + 1:
                raise TaskRecordError("Stage numbers must be contiguous and 1-based")
            if record.stage_id in seen_stage_ids:
                raise TaskRecordError("Stage IDs must be unique within one Task")
            if record.session_id != header.owner_session_id:
                raise TaskRecordError("Stage Session must match the Task owner Session")
            _require_timestamp_order(previous_timestamp, record.started_at)
            previous_timestamp = record.started_at
            seen_stage_ids.add(record.stage_id)
            active = record
            stages.append(TaskStageState(record, None))
            continue
        if active is None:
            raise TaskRecordError("Stage terminal record has no active Stage")
        if isinstance(record, StageCommitted):
            _validate_stage_committed(record)
            _validate_stage_terminal_identity(active, record)
            if record.session_id != header.owner_session_id:
                raise TaskRecordError("committed Stage Session must match the Task owner Session")
            terminal_at = record.committed_at
        elif isinstance(record, StageFailed):
            _validate_stage_failed(record)
            _validate_stage_terminal_identity(active, record)
            terminal_at = record.failed_at
        else:
            raise TaskRecordError("unsupported task lifecycle record")
        _require_timestamp_order(previous_timestamp, terminal_at)
        previous_timestamp = terminal_at
        stages[-1] = TaskStageState(active, record)
        active = None
    return TaskReplayState(header=header, records=tuple(records), stages=tuple(stages))


def _record_to_dict(record: TaskRecord) -> dict[str, object]:
    if isinstance(record, TaskHeader):
        _validate_header(record)
        return {
            "acceptance_criteria": list(record.acceptance_criteria),
            "created_at": record.created_at,
            "objective": record.objective,
            "owner_session_id": record.owner_session_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "scope": record.scope.value,
            "sequence": record.sequence,
            "task_id": record.task_id,
            "workspace": record.workspace,
            "workspace_fingerprint": record.workspace_fingerprint,
        }
    if isinstance(record, StageStarted):
        _validate_stage_started(record)
        return {
            "objective": record.objective,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "session_id": record.session_id,
            "stage_id": record.stage_id,
            "stage_number": record.stage_number,
            "started_at": record.started_at,
        }
    if isinstance(record, StageCommitted):
        _validate_stage_committed(record)
        return {
            "committed_at": record.committed_at,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "session_id": record.session_id,
            "stage_id": record.stage_id,
            "stage_number": record.stage_number,
            "turn_number": record.turn_number,
            "turn_record_sequence": record.turn_record_sequence,
            "turn_record_sha256": record.turn_record_sha256,
        }
    if isinstance(record, StageFailed):
        _validate_stage_failed(record)
        return {
            "failed_at": record.failed_at,
            "reason": record.reason.value,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "stage_id": record.stage_id,
            "stage_number": record.stage_number,
        }
    raise TaskRecordError("unsupported task record")


def _decode_stage_started(value: dict[str, object]) -> StageStarted:
    _require_fields(
        value,
        {
            "objective",
            "record_type",
            "schema_version",
            "sequence",
            "session_id",
            "stage_id",
            "stage_number",
            "started_at",
        },
        "stage_started",
    )
    if value.get("schema_version") != STAGE_STARTED_SCHEMA_VERSION:
        raise TaskRecordError("unsupported stage_started schema version")
    record = StageStarted(
        sequence=value.get("sequence"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        session_id=value.get("session_id"),
        objective=value.get("objective"),
        started_at=value.get("started_at"),
    )
    _validate_stage_started(record)
    return record


def _decode_stage_committed(value: dict[str, object]) -> StageCommitted:
    _require_fields(
        value,
        {
            "committed_at",
            "record_type",
            "schema_version",
            "sequence",
            "session_id",
            "stage_id",
            "stage_number",
            "turn_number",
            "turn_record_sequence",
            "turn_record_sha256",
        },
        "stage_committed",
    )
    if value.get("schema_version") != STAGE_COMMITTED_SCHEMA_VERSION:
        raise TaskRecordError("unsupported stage_committed schema version")
    record = StageCommitted(
        sequence=value.get("sequence"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        session_id=value.get("session_id"),
        turn_number=value.get("turn_number"),
        turn_record_sequence=value.get("turn_record_sequence"),
        turn_record_sha256=value.get("turn_record_sha256"),
        committed_at=value.get("committed_at"),
    )
    _validate_stage_committed(record)
    return record


def _decode_stage_failed(value: dict[str, object]) -> StageFailed:
    _require_fields(
        value,
        {
            "failed_at",
            "reason",
            "record_type",
            "schema_version",
            "sequence",
            "stage_id",
            "stage_number",
        },
        "stage_failed",
    )
    if value.get("schema_version") != STAGE_FAILED_SCHEMA_VERSION:
        raise TaskRecordError("unsupported stage_failed schema version")
    try:
        reason = StageFailureReason(value.get("reason"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported Stage failure reason") from None
    record = StageFailed(
        sequence=value.get("sequence"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        reason=reason,
        failed_at=value.get("failed_at"),
    )
    _validate_stage_failed(record)
    return record


def _require_fields(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise TaskRecordError(f"{label} has unknown or missing fields")


def _validate_stage_started(record: object) -> None:
    if not isinstance(record, StageStarted):
        raise TaskRecordError("unsupported stage_started record")
    _validate_positive_sequence(record.sequence, "stage_started sequence")
    if record.record_type != "stage_started":
        raise TaskRecordError("stage_started record type is invalid")
    if record.schema_version != STAGE_STARTED_SCHEMA_VERSION:
        raise TaskRecordError("unsupported stage_started schema version")
    canonical_stage_id(record.stage_id)
    _validate_stage_number(record.stage_number)
    _validate_session_id(record.session_id, "Stage Session ID")
    canonical_stage_objective(record.objective)
    _validate_timestamp(record.started_at, "Stage started_at")


def _validate_stage_committed(record: object) -> None:
    if not isinstance(record, StageCommitted):
        raise TaskRecordError("unsupported stage_committed record")
    _validate_positive_sequence(record.sequence, "stage_committed sequence")
    if record.record_type != "stage_committed":
        raise TaskRecordError("stage_committed record type is invalid")
    if record.schema_version != STAGE_COMMITTED_SCHEMA_VERSION:
        raise TaskRecordError("unsupported stage_committed schema version")
    canonical_stage_id(record.stage_id)
    _validate_stage_number(record.stage_number)
    _validate_session_id(record.session_id, "committed Stage Session ID")
    _validate_positive_integer(record.turn_number, "Session Turn number")
    _validate_positive_integer(record.turn_record_sequence, "Session Turn record sequence")
    if (
        not isinstance(record.turn_record_sha256, str)
        or _SHA256.fullmatch(record.turn_record_sha256) is None
    ):
        raise TaskRecordError("Session Turn record SHA-256 is invalid")
    _validate_timestamp(record.committed_at, "Stage committed_at")


def _validate_stage_failed(record: object) -> None:
    if not isinstance(record, StageFailed):
        raise TaskRecordError("unsupported stage_failed record")
    _validate_positive_sequence(record.sequence, "stage_failed sequence")
    if record.record_type != "stage_failed":
        raise TaskRecordError("stage_failed record type is invalid")
    if record.schema_version != STAGE_FAILED_SCHEMA_VERSION:
        raise TaskRecordError("unsupported stage_failed schema version")
    canonical_stage_id(record.stage_id)
    _validate_stage_number(record.stage_number)
    if type(record.reason) is not StageFailureReason:
        raise TaskRecordError("unsupported Stage failure reason")
    _validate_timestamp(record.failed_at, "Stage failed_at")


def _validate_stage_terminal_identity(
    started: StageStarted,
    terminal: StageCommitted | StageFailed,
) -> None:
    if terminal.stage_id != started.stage_id:
        raise TaskRecordError("Stage terminal ID does not match the active Stage")
    if terminal.stage_number != started.stage_number:
        raise TaskRecordError("Stage terminal number does not match the active Stage")


def _validate_positive_sequence(value: object, label: str) -> None:
    if type(value) is not int or value < 1:
        raise TaskRecordError(f"{label} must be a positive integer")


def _validate_stage_number(value: object) -> None:
    _validate_positive_integer(value, "Stage number")


def _validate_positive_integer(value: object, label: str) -> None:
    if type(value) is not int or value < 1:
        raise TaskRecordError(f"{label} must be a positive integer")


def _validate_session_id(value: object, label: str) -> None:
    try:
        canonical_session_id(value)
    except SessionRecordError as error:
        raise TaskRecordError(f"invalid {label}: {error}") from None


def _validate_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or _CANONICAL_TIMESTAMP.fullmatch(value) is None:
        raise TaskRecordError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise TaskRecordError(f"{label} must be a canonical UTC timestamp") from None
    if parsed.tzinfo != timezone.utc:
        raise TaskRecordError(f"{label} must be a canonical UTC timestamp")


def _require_timestamp_order(previous: str, current: str) -> None:
    if current < previous:
        raise TaskRecordError("Task lifecycle timestamps must be nondecreasing")


def _validate_header(record: object) -> None:
    if not isinstance(record, TaskHeader):
        raise TaskRecordError("unsupported task record")
    if type(record.sequence) is not int or record.sequence != 0:
        raise TaskRecordError("task_header sequence must be 0")
    if record.record_type != "task_header":
        raise TaskRecordError("task_header record type is invalid")
    if record.schema_version != TASK_HEADER_SCHEMA_VERSION:
        raise TaskRecordError("unsupported task_header schema version")
    canonical_task_id(record.task_id)
    try:
        canonical_session_id(record.owner_session_id)
    except SessionRecordError as error:
        raise TaskRecordError(f"invalid owner Session ID: {error}") from None
    if not isinstance(record.workspace, str) or not record.workspace:
        raise TaskRecordError("task workspace must be a non-empty string")
    if not Path(record.workspace).is_absolute():
        raise TaskRecordError("task workspace must be absolute")
    if (
        not isinstance(record.workspace_fingerprint, str)
        or _WORKSPACE_FINGERPRINT.fullmatch(record.workspace_fingerprint) is None
    ):
        raise TaskRecordError("task workspace fingerprint is invalid")
    if record.workspace_fingerprint != workspace_fingerprint(Path(record.workspace)):
        raise TaskRecordError("task workspace fingerprint does not match its workspace")
    if record.scope is not TaskScope.WORKSPACE:
        raise TaskRecordError("unsupported task scope")
    canonical_task_objective(record.objective)
    canonical_acceptance_criteria(record.acceptance_criteria)
    if (
        not isinstance(record.created_at, str)
        or _CANONICAL_TIMESTAMP.fullmatch(record.created_at) is None
    ):
        raise TaskRecordError("task created_at must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(record.created_at[:-1] + "+00:00")
    except ValueError:
        raise TaskRecordError("task created_at must be a canonical UTC timestamp") from None
    if parsed.tzinfo != timezone.utc:
        raise TaskRecordError("task created_at must be a canonical UTC timestamp")


def _bounded_text(value: object, label: str, *, max_characters: int, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise TaskRecordError(f"{label} must be nonblank text without NUL")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise TaskRecordError(f"{label} must be valid UTF-8") from None
    if len(value) > max_characters or len(encoded) > max_bytes:
        raise TaskRecordError(
            f"{label} exceeds {max_characters} characters or {max_bytes} UTF-8 bytes"
        )
    return value
