"""Closed, replayable records for one isolated Team worktree.

The worktree ledger is intentionally independent from the Team and Session ledgers.
It records identity and side-effect boundaries; filesystem/Git inspection lives in
``worktree_store`` and ``git_worktree``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import TypeAlias

from coquo.session_records import canonical_session_id

WORKTREE_SCHEMA_VERSION = 1
MAX_WORKTREE_RECORD_BYTES = 64 * 1024
MAX_WORKTREE_RECORDS = 2048
MAX_WORKTREE_TRANSCRIPT_BYTES = 2 * 1024 * 1024
MAX_WORKTREE_PATCH_BYTES = 4 * 1024 * 1024
MAX_WORKTREE_CHANGED_PATHS = 512

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_REF = re.compile(r"refs/heads/[A-Za-z0-9._/-]{1,200}\Z")
_BRANCH = re.compile(r"coquo/team/[0-9a-f-]{36}/[0-9a-f-]{36}\Z")
_RELATIVE = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")


class WorktreeRecordError(ValueError):
    """Raised for malformed records or an invalid replay transition."""


class WorktreeOperation(StrEnum):
    PROVISION = "provision"
    SEAL = "seal"
    INTEGRATE = "integrate"
    RETIRE = "retire"


class WorktreeOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    OUTCOME_UNKNOWN = "outcome_unknown"


class WorktreeState(StrEnum):
    DECLARED = "declared"
    PROVISIONING = "provisioning"
    READY = "ready"
    PROVISION_FAILED = "provision_failed"
    PROVISION_UNKNOWN = "provision_unknown"
    SEALING = "sealing"
    SEALED_EMPTY = "sealed_empty"
    SEALED_CHANGES = "sealed_changes"
    SEAL_FAILED = "seal_failed"
    SEAL_UNKNOWN = "seal_unknown"
    INTEGRATING = "integrating"
    APPLIED = "applied"
    INTEGRATION_FAILED = "integration_failed"
    INTEGRATION_UNKNOWN = "integration_unknown"
    RETIRING = "retiring"
    RETIRED = "retired"
    RETIRE_FAILED = "retire_failed"
    RETIRE_UNKNOWN = "retire_unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise WorktreeRecordError(f"{label} is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise WorktreeRecordError(f"{label} is not valid UTF-8") from None
    return value


def _id(value: object, label: str) -> str:
    try:
        return canonical_session_id(value)
    except Exception as error:
        raise WorktreeRecordError(f"{label} is invalid") from error


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise WorktreeRecordError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise WorktreeRecordError(f"{label} is invalid")
    return value


def canonical_relative_path(value: object) -> str:
    path = _text(value, "worktree relative path", maximum=240)
    if not path.isascii() or not _RELATIVE.fullmatch(path):
        raise WorktreeRecordError("worktree relative path is invalid")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise WorktreeRecordError("worktree relative path must be confined")
    return str(parsed)


@dataclass(frozen=True)
class WorktreeHeader:
    sequence: int
    worktree_id: str
    authority_workspace: str
    authority_workspace_fingerprint: str
    team_id: str
    assignment_id: str
    child_run_id: str
    member_id: str
    role_contract: str
    target_ref: str
    base_commit: str
    branch: str
    relative_path: str
    created_at: str
    record_type: str = "worktree_header"
    schema_version: int = WORKTREE_SCHEMA_VERSION


@dataclass(frozen=True)
class WorktreeOperationStarted:
    sequence: int
    operation_id: str
    worktree_id: str
    operation: WorktreeOperation
    started_at: str
    record_type: str = "worktree_operation_started"
    schema_version: int = WORKTREE_SCHEMA_VERSION


@dataclass(frozen=True)
class WorktreeOperationFinished:
    sequence: int
    operation_id: str
    worktree_id: str
    operation: WorktreeOperation
    outcome: WorktreeOutcome
    result_code: str
    message: str
    finished_at: str
    record_type: str = "worktree_operation_finished"
    schema_version: int = WORKTREE_SCHEMA_VERSION


@dataclass(frozen=True)
class WorktreeSealed:
    sequence: int
    operation_id: str
    worktree_id: str
    patch_sha256: str
    patch_bytes: int
    changed_paths: int
    manifest_sha256: str
    sealed_at: str
    record_type: str = "worktree_sealed"
    schema_version: int = WORKTREE_SCHEMA_VERSION


WorktreeRecord: TypeAlias = (
    WorktreeHeader | WorktreeOperationStarted | WorktreeOperationFinished | WorktreeSealed
)


@dataclass(frozen=True)
class WorktreeReplayState:
    header: WorktreeHeader
    records: tuple[WorktreeRecord, ...]
    state: WorktreeState
    sealed: WorktreeSealed | None = None
    live_operation_id: str | None = None


def validate_worktree_record(record: WorktreeRecord) -> None:
    if type(record.sequence) is not int or record.sequence < 0:
        raise WorktreeRecordError("worktree sequence is invalid")
    if type(record.record_type) is not str or record.schema_version != WORKTREE_SCHEMA_VERSION:
        raise WorktreeRecordError("unsupported worktree record schema")
    if isinstance(record, WorktreeHeader):
        if record.sequence != 0 or record.record_type != "worktree_header":
            raise WorktreeRecordError("worktree header identity is invalid")
        _id(record.worktree_id, "worktree ID")
        _text(record.authority_workspace, "authority workspace", maximum=4096)
        if _WORKSPACE_FINGERPRINT.fullmatch(record.authority_workspace_fingerprint) is None:
            raise WorktreeRecordError("authority workspace fingerprint is invalid")
        for value, label in (
            (record.team_id, "Team ID"),
            (record.assignment_id, "assignment ID"),
            (record.child_run_id, "Child Run ID"),
            (record.member_id, "member ID"),
        ):
            _id(value, label)
        _text(record.role_contract, "role contract", maximum=256)
        if _REF.fullmatch(record.target_ref) is None:
            raise WorktreeRecordError("target ref is invalid")
        if _COMMIT.fullmatch(record.base_commit) is None:
            raise WorktreeRecordError("base commit is invalid")
        if _BRANCH.fullmatch(record.branch) is None:
            raise WorktreeRecordError("generated worktree branch is invalid")
        canonical_relative_path(record.relative_path)
        _timestamp(record.created_at, "worktree created_at")
        return
    if isinstance(record, WorktreeOperationStarted):
        if record.sequence < 1 or record.record_type != "worktree_operation_started":
            raise WorktreeRecordError("worktree operation start is invalid")
        _id(record.operation_id, "operation ID")
        _id(record.worktree_id, "worktree ID")
        if type(record.operation) is not WorktreeOperation:
            raise WorktreeRecordError("worktree operation is invalid")
        _timestamp(record.started_at, "operation started_at")
        return
    if isinstance(record, WorktreeOperationFinished):
        if record.sequence < 1 or record.record_type != "worktree_operation_finished":
            raise WorktreeRecordError("worktree operation finish is invalid")
        _id(record.operation_id, "operation ID")
        _id(record.worktree_id, "worktree ID")
        if type(record.operation) is not WorktreeOperation:
            raise WorktreeRecordError("worktree operation is invalid")
        if type(record.outcome) is not WorktreeOutcome:
            raise WorktreeRecordError("worktree outcome is invalid")
        _text(record.result_code, "operation result code", maximum=256)
        _text(record.message, "operation message", maximum=4096)
        _timestamp(record.finished_at, "operation finished_at")
        return
    if isinstance(record, WorktreeSealed):
        if record.sequence < 1 or record.record_type != "worktree_sealed":
            raise WorktreeRecordError("worktree seal is invalid")
        _id(record.operation_id, "seal operation ID")
        _id(record.worktree_id, "worktree ID")
        _sha(record.patch_sha256, "patch digest")
        _sha(record.manifest_sha256, "manifest digest")
        if (
            type(record.patch_bytes) is not int
            or not 0 <= record.patch_bytes <= MAX_WORKTREE_PATCH_BYTES
        ):
            raise WorktreeRecordError("patch byte bound is invalid")
        if (
            type(record.changed_paths) is not int
            or not 0 <= record.changed_paths <= MAX_WORKTREE_CHANGED_PATHS
        ):
            raise WorktreeRecordError("changed path bound is invalid")
        _timestamp(record.sealed_at, "sealed_at")
        return
    raise WorktreeRecordError("unknown worktree record type")


def replay_worktree_records(
    records: list[WorktreeRecord] | tuple[WorktreeRecord, ...],
) -> WorktreeReplayState:
    if not records or len(records) > MAX_WORKTREE_RECORDS:
        raise WorktreeRecordError("worktree record count is invalid")
    for index, record in enumerate(records):
        validate_worktree_record(record)
        if record.sequence != index:
            raise WorktreeRecordError("worktree sequence is not contiguous")
    header = records[0]
    if not isinstance(header, WorktreeHeader):
        raise WorktreeRecordError("worktree transcript must start with a header")
    state = WorktreeState.DECLARED
    live: WorktreeOperationStarted | None = None
    sealed: WorktreeSealed | None = None
    for record in records[1:]:
        if isinstance(record, WorktreeOperationStarted):
            if record.worktree_id != header.worktree_id or live is not None:
                raise WorktreeRecordError("worktree operation start is out of order")
            allowed = {
                WorktreeOperation.PROVISION: WorktreeState.DECLARED,
                WorktreeOperation.SEAL: WorktreeState.READY,
                WorktreeOperation.INTEGRATE: WorktreeState.SEALED_CHANGES,
                WorktreeOperation.RETIRE: {
                    WorktreeState.SEALED_EMPTY,
                    WorktreeState.APPLIED,
                    WorktreeState.PROVISION_FAILED,
                    WorktreeState.SEAL_FAILED,
                    WorktreeState.INTEGRATION_FAILED,
                },
            }[record.operation]
            if isinstance(allowed, set):
                if state not in allowed:
                    raise WorktreeRecordError("worktree operation cannot start in this state")
            elif state is not allowed:
                raise WorktreeRecordError("worktree operation cannot start in this state")
            live = record
            state = {
                WorktreeOperation.PROVISION: WorktreeState.PROVISIONING,
                WorktreeOperation.SEAL: WorktreeState.SEALING,
                WorktreeOperation.INTEGRATE: WorktreeState.INTEGRATING,
                WorktreeOperation.RETIRE: WorktreeState.RETIRING,
            }[record.operation]
        elif isinstance(record, WorktreeOperationFinished):
            if (
                live is None
                or record.worktree_id != header.worktree_id
                or record.operation_id != live.operation_id
                or record.operation is not live.operation
            ):
                raise WorktreeRecordError("worktree operation finish does not match start")
            state = _finished_state(record.operation, record.outcome)
            if record.outcome is WorktreeOutcome.SUCCEEDED:
                if record.operation is WorktreeOperation.PROVISION:
                    state = WorktreeState.READY
                elif record.operation is WorktreeOperation.SEAL:
                    if sealed is None:
                        raise WorktreeRecordError("successful seal requires worktree_sealed")
                    state = (
                        WorktreeState.SEALED_EMPTY
                        if sealed.changed_paths == 0
                        else WorktreeState.SEALED_CHANGES
                    )
                elif record.operation is WorktreeOperation.INTEGRATE:
                    state = WorktreeState.APPLIED
                else:
                    state = WorktreeState.RETIRED
            live = None
        elif isinstance(record, WorktreeSealed):
            if (
                live is None
                or live.operation is not WorktreeOperation.SEAL
                or record.operation_id != live.operation_id
                or record.worktree_id != header.worktree_id
            ):
                raise WorktreeRecordError("worktree seal does not match live operation")
            sealed = record
            state = (
                WorktreeState.SEALED_EMPTY
                if record.changed_paths == 0
                else WorktreeState.SEALED_CHANGES
            )
        else:
            raise WorktreeRecordError("unknown worktree record in replay")
    return WorktreeReplayState(
        header=header,
        records=tuple(records),
        state=state,
        sealed=sealed,
        live_operation_id=live.operation_id if live is not None else None,
    )


def _finished_state(operation: WorktreeOperation, outcome: WorktreeOutcome) -> WorktreeState:
    if outcome is WorktreeOutcome.OUTCOME_UNKNOWN:
        return {
            WorktreeOperation.PROVISION: WorktreeState.PROVISION_UNKNOWN,
            WorktreeOperation.SEAL: WorktreeState.SEAL_UNKNOWN,
            WorktreeOperation.INTEGRATE: WorktreeState.INTEGRATION_UNKNOWN,
            WorktreeOperation.RETIRE: WorktreeState.RETIRE_UNKNOWN,
        }[operation]
    if outcome is WorktreeOutcome.FAILED:
        return {
            WorktreeOperation.PROVISION: WorktreeState.PROVISION_FAILED,
            WorktreeOperation.SEAL: WorktreeState.SEAL_FAILED,
            WorktreeOperation.INTEGRATE: WorktreeState.INTEGRATION_FAILED,
            WorktreeOperation.RETIRE: WorktreeState.RETIRE_FAILED,
        }[operation]
    if outcome is WorktreeOutcome.PARTIAL:
        return {
            WorktreeOperation.PROVISION: WorktreeState.PROVISION_UNKNOWN,
            WorktreeOperation.SEAL: WorktreeState.SEAL_UNKNOWN,
            WorktreeOperation.INTEGRATE: WorktreeState.INTEGRATION_UNKNOWN,
            WorktreeOperation.RETIRE: WorktreeState.RETIRE_UNKNOWN,
        }[operation]
    return WorktreeState.DECLARED


def _mapping(record: WorktreeRecord) -> dict[str, object]:
    value = {"record_type": record.record_type, "schema_version": record.schema_version}
    for name, item in record.__dict__.items():
        if name not in {"record_type", "schema_version"}:
            value[name] = item.value if isinstance(item, StrEnum) else item
    return value


def encode_worktree_record(record: WorktreeRecord) -> bytes:
    validate_worktree_record(record)
    payload = (
        json.dumps(
            _mapping(record),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    if len(payload) > MAX_WORKTREE_RECORD_BYTES:
        raise WorktreeRecordError("worktree record exceeds size bound")
    return payload


def _closed(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorktreeRecordError("worktree record fields are not closed")
    return value


def decode_worktree_record(payload: bytes) -> WorktreeRecord:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_WORKTREE_RECORD_BYTES:
        raise WorktreeRecordError("worktree record payload is invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WorktreeRecordError("worktree record is not canonical JSON") from None
    if not isinstance(value, dict):
        raise WorktreeRecordError("worktree record must be an object")
    record_type = value.get("record_type")
    if record_type == "worktree_header":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "worktree_id",
            "authority_workspace",
            "authority_workspace_fingerprint",
            "team_id",
            "assignment_id",
            "child_run_id",
            "member_id",
            "role_contract",
            "target_ref",
            "base_commit",
            "branch",
            "relative_path",
            "created_at",
        }
        value = _closed(value, fields)
        record: WorktreeRecord = WorktreeHeader(**value)  # type: ignore[arg-type]
    elif record_type == "worktree_operation_started":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "operation_id",
            "worktree_id",
            "operation",
            "started_at",
        }
        value = _closed(value, fields)
        record = WorktreeOperationStarted(
            operation=WorktreeOperation(value["operation"]),
            **{k: v for k, v in value.items() if k != "operation"},
        )  # type: ignore[arg-type]
    elif record_type == "worktree_operation_finished":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "operation_id",
            "worktree_id",
            "operation",
            "outcome",
            "result_code",
            "message",
            "finished_at",
        }
        value = _closed(value, fields)
        record = WorktreeOperationFinished(
            operation=WorktreeOperation(value["operation"]),
            outcome=WorktreeOutcome(value["outcome"]),
            **{k: v for k, v in value.items() if k not in {"operation", "outcome"}},
        )  # type: ignore[arg-type]
    elif record_type == "worktree_sealed":
        fields = {
            "record_type",
            "schema_version",
            "sequence",
            "operation_id",
            "worktree_id",
            "patch_sha256",
            "patch_bytes",
            "changed_paths",
            "manifest_sha256",
            "sealed_at",
        }
        value = _closed(value, fields)
        record = WorktreeSealed(**value)  # type: ignore[arg-type]
    else:
        raise WorktreeRecordError("unknown worktree record type")
    validate_worktree_record(record)
    if json.loads(encode_worktree_record(record)) != json.loads(payload):
        raise WorktreeRecordError("worktree record is not canonical")
    return record


def worktree_record_digest(record: WorktreeRecord) -> str:
    return hashlib.sha256(encode_worktree_record(record)).hexdigest()
