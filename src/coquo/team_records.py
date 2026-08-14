"""Closed, versioned records for durable workspace Teams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import TypeAlias
from uuid import UUID

from coquo.session_records import canonical_session_id, workspace_fingerprint

TEAM_HEADER_SCHEMA_VERSION = 1
TEAM_CLOSED_SCHEMA_VERSION = 1
TEAM_MEMBER_JOINED_SCHEMA_VERSION = 1
TEAM_MEMBER_DISABLED_SCHEMA_VERSION = 1
TEAM_MEMBER_ENABLED_SCHEMA_VERSION = 1
TEAM_MEMBER_LEFT_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_CHILD_BOUND_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_OBSERVED_SCHEMA_VERSION = 1
MAX_TEAM_RECORD_BYTES = 64 * 1024
MAX_TEAM_RECORDS = 10_000
MAX_TEAM_NAME_CHARACTERS = 80
MAX_TEAM_NAME_BYTES = 256
MAX_TEAM_REASON_CHARACTERS = 4096
MAX_TEAM_REASON_BYTES = 16 * 1024
MAX_TEAM_MEMBERS = 64
TEAM_MEMBER_ROLE_CONTRACT = "read-only-investigator-v1"
MAX_TEAM_ASSIGNMENT_OBJECTIVE_CHARACTERS = 4096
MAX_TEAM_ASSIGNMENT_OBJECTIVE_BYTES = 16 * 1024

_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")


class TeamRecordError(ValueError):
    """Raised when a Team record or replay chain is invalid."""


class TeamStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class TeamMemberStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    LEFT = "left"


class TeamAssignmentPhase(StrEnum):
    PENDING_CHILD = "pending_child"
    CHILD_BOUND = "child_bound"
    TERMINAL_OBSERVED = "terminal_observed"


@dataclass(frozen=True)
class TeamHeader:
    sequence: int
    team_id: str
    workspace: str
    workspace_fingerprint: str
    owner_session_id: str
    name: str
    created_at: str
    record_type: str = "team_header"
    schema_version: int = TEAM_HEADER_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamClosed:
    sequence: int
    team_id: str
    closed_at: str
    record_type: str = "team_closed"
    schema_version: int = TEAM_CLOSED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMemberJoined:
    sequence: int
    team_id: str
    member_id: str
    name: str
    role_contract: str
    joined_at: str
    record_type: str = "team_member_joined"
    schema_version: int = TEAM_MEMBER_JOINED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMemberDisabled:
    sequence: int
    team_id: str
    member_id: str
    reason: str
    disabled_at: str
    record_type: str = "team_member_disabled"
    schema_version: int = TEAM_MEMBER_DISABLED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMemberEnabled:
    sequence: int
    team_id: str
    member_id: str
    enabled_at: str
    record_type: str = "team_member_enabled"
    schema_version: int = TEAM_MEMBER_ENABLED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMemberLeft:
    sequence: int
    team_id: str
    member_id: str
    reason: str
    left_at: str
    record_type: str = "team_member_left"
    schema_version: int = TEAM_MEMBER_LEFT_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamAssignmentCreated:
    sequence: int
    team_id: str
    assignment_id: str
    member_id: str
    child_run_id: str
    objective: str
    objective_sha256: str
    created_at: str
    record_type: str = "team_assignment_created"
    schema_version: int = TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamAssignmentChildBound:
    sequence: int
    team_id: str
    assignment_id: str
    child_run_id: str
    child_header_sequence: int
    child_origin_sequence: int
    bound_at: str
    record_type: str = "team_assignment_child_bound"
    schema_version: int = TEAM_ASSIGNMENT_CHILD_BOUND_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamAssignmentObserved:
    sequence: int
    team_id: str
    assignment_id: str
    child_run_id: str
    child_session_id: str | None
    child_outcome: str
    child_terminal_sequence: int
    handoff_sha256: str
    observed_at: str
    record_type: str = "team_assignment_observed"
    schema_version: int = TEAM_ASSIGNMENT_OBSERVED_SCHEMA_VERSION


TeamRecord: TypeAlias = (
    TeamHeader
    | TeamClosed
    | TeamMemberJoined
    | TeamMemberDisabled
    | TeamMemberEnabled
    | TeamMemberLeft
    | TeamAssignmentCreated
    | TeamAssignmentChildBound
    | TeamAssignmentObserved
)


@dataclass(frozen=True)
class TeamMemberState:
    member_id: str
    name: str
    role_contract: str
    status: TeamMemberStatus
    joined_at: str
    disabled_at: str | None = None
    left_at: str | None = None


@dataclass(frozen=True)
class TeamAssignmentState:
    assignment_id: str
    member_id: str
    child_run_id: str
    objective: str
    objective_sha256: str
    created_at: str
    phase: TeamAssignmentPhase
    child_header_sequence: int | None = None
    child_origin_sequence: int | None = None
    bound_at: str | None = None
    child_session_id: str | None = None
    child_outcome: str | None = None
    child_terminal_sequence: int | None = None
    handoff_sha256: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True)
class TeamReplayState:
    header: TeamHeader
    records: tuple[TeamRecord, ...]
    closed: TeamClosed | None = None
    members: tuple[TeamMemberState, ...] = ()
    assignments: tuple[TeamAssignmentState, ...] = ()

    @property
    def status(self) -> TeamStatus:
        return TeamStatus.CLOSED if self.closed is not None else TeamStatus.OPEN

    @property
    def next_sequence(self) -> int:
        return len(self.records)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_team_id(value: object) -> str:
    return _canonical_uuid4(value, "Team ID")


def canonical_team_name(value: object) -> str:
    return _bounded_text(
        value,
        "Team name",
        max_characters=MAX_TEAM_NAME_CHARACTERS,
        max_bytes=MAX_TEAM_NAME_BYTES,
    )


def canonical_team_assignment_objective(value: object) -> str:
    return _bounded_text(
        value,
        "Team assignment objective",
        max_characters=MAX_TEAM_ASSIGNMENT_OBJECTIVE_CHARACTERS,
        max_bytes=MAX_TEAM_ASSIGNMENT_OBJECTIVE_BYTES,
    )


def team_assignment_objective_sha256(objective: str) -> str:
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


def canonical_team_timestamp(value: object, label: str = "Team timestamp") -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise TeamRecordError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise TeamRecordError(f"{label} is invalid") from None
    if parsed.tzinfo != timezone.utc:
        raise TeamRecordError(f"{label} is invalid")
    return value


def encode_team_record(record: TeamRecord) -> bytes:
    _validate_record(record)
    if isinstance(record, TeamHeader):
        value = {
            "created_at": record.created_at,
            "name": record.name,
            "owner_session_id": record.owner_session_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
            "workspace": record.workspace,
            "workspace_fingerprint": record.workspace_fingerprint,
        }
    elif isinstance(record, TeamClosed):
        value = {
            "closed_at": record.closed_at,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamMemberJoined):
        value = {
            "joined_at": record.joined_at,
            "member_id": record.member_id,
            "name": record.name,
            "record_type": record.record_type,
            "role_contract": record.role_contract,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamMemberDisabled):
        value = {
            "disabled_at": record.disabled_at,
            "member_id": record.member_id,
            "reason": record.reason,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamMemberEnabled):
        value = {
            "enabled_at": record.enabled_at,
            "member_id": record.member_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamAssignmentCreated):
        value = {
            "assignment_id": record.assignment_id,
            "child_run_id": record.child_run_id,
            "created_at": record.created_at,
            "member_id": record.member_id,
            "objective": record.objective,
            "objective_sha256": record.objective_sha256,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamAssignmentChildBound):
        value = {
            "assignment_id": record.assignment_id,
            "bound_at": record.bound_at,
            "child_header_sequence": record.child_header_sequence,
            "child_origin_sequence": record.child_origin_sequence,
            "child_run_id": record.child_run_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamAssignmentObserved):
        value = {
            "assignment_id": record.assignment_id,
            "child_outcome": record.child_outcome,
            "child_run_id": record.child_run_id,
            "child_session_id": record.child_session_id,
            "child_terminal_sequence": record.child_terminal_sequence,
            "handoff_sha256": record.handoff_sha256,
            "observed_at": record.observed_at,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    else:
        value = {
            "left_at": record.left_at,
            "member_id": record.member_id,
            "reason": record.reason,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    try:
        payload = (
            json.dumps(
                value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise TeamRecordError("Team record is not JSON encodable") from None
    if len(payload) > MAX_TEAM_RECORD_BYTES:
        raise TeamRecordError(f"Team record exceeds {MAX_TEAM_RECORD_BYTES} bytes")
    return payload


def decode_team_record(payload: bytes) -> TeamRecord:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_TEAM_RECORD_BYTES:
        raise TeamRecordError("Team record payload is empty or oversized")
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or b"\n" in payload or b"\r" in payload:
        raise TeamRecordError("Team record must occupy exactly one JSONL line")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise TeamRecordError("Team record is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise TeamRecordError("Team record must be a JSON object")
    record_type = value.get("record_type")
    if record_type == "team_header":
        _require_fields(
            value,
            "team_header",
            "created_at",
            "name",
            "owner_session_id",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
            "workspace",
            "workspace_fingerprint",
        )
        record: TeamRecord = TeamHeader(
            sequence=value["sequence"],
            team_id=value["team_id"],
            workspace=value["workspace"],
            workspace_fingerprint=value["workspace_fingerprint"],
            owner_session_id=value["owner_session_id"],
            name=value["name"],
            created_at=value["created_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_closed":
        _require_fields(
            value,
            "team_closed",
            "closed_at",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamClosed(
            sequence=value["sequence"],
            team_id=value["team_id"],
            closed_at=value["closed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_member_joined":
        _require_fields(
            value,
            "team_member_joined",
            "joined_at",
            "member_id",
            "name",
            "record_type",
            "role_contract",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamMemberJoined(
            sequence=value["sequence"],
            team_id=value["team_id"],
            member_id=value["member_id"],
            name=value["name"],
            role_contract=value["role_contract"],
            joined_at=value["joined_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_member_disabled":
        _require_fields(
            value,
            "team_member_disabled",
            "disabled_at",
            "member_id",
            "reason",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamMemberDisabled(
            sequence=value["sequence"],
            team_id=value["team_id"],
            member_id=value["member_id"],
            reason=value["reason"],
            disabled_at=value["disabled_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_member_enabled":
        _require_fields(
            value,
            "team_member_enabled",
            "enabled_at",
            "member_id",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamMemberEnabled(
            sequence=value["sequence"],
            team_id=value["team_id"],
            member_id=value["member_id"],
            enabled_at=value["enabled_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_member_left":
        _require_fields(
            value,
            "team_member_left",
            "left_at",
            "member_id",
            "reason",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamMemberLeft(
            sequence=value["sequence"],
            team_id=value["team_id"],
            member_id=value["member_id"],
            reason=value["reason"],
            left_at=value["left_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_assignment_created":
        _require_fields(
            value,
            "team_assignment_created",
            "assignment_id",
            "child_run_id",
            "created_at",
            "member_id",
            "objective",
            "objective_sha256",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamAssignmentCreated(
            sequence=value["sequence"],
            team_id=value["team_id"],
            assignment_id=value["assignment_id"],
            member_id=value["member_id"],
            child_run_id=value["child_run_id"],
            objective=value["objective"],
            objective_sha256=value["objective_sha256"],
            created_at=value["created_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_assignment_child_bound":
        _require_fields(
            value,
            "team_assignment_child_bound",
            "assignment_id",
            "bound_at",
            "child_header_sequence",
            "child_origin_sequence",
            "child_run_id",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamAssignmentChildBound(
            sequence=value["sequence"],
            team_id=value["team_id"],
            assignment_id=value["assignment_id"],
            child_run_id=value["child_run_id"],
            child_header_sequence=value["child_header_sequence"],
            child_origin_sequence=value["child_origin_sequence"],
            bound_at=value["bound_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_assignment_observed":
        _require_fields(
            value,
            "team_assignment_observed",
            "assignment_id",
            "child_outcome",
            "child_run_id",
            "child_session_id",
            "child_terminal_sequence",
            "handoff_sha256",
            "observed_at",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamAssignmentObserved(
            sequence=value["sequence"],
            team_id=value["team_id"],
            assignment_id=value["assignment_id"],
            child_run_id=value["child_run_id"],
            child_session_id=value["child_session_id"],
            child_outcome=value["child_outcome"],
            child_terminal_sequence=value["child_terminal_sequence"],
            handoff_sha256=value["handoff_sha256"],
            observed_at=value["observed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    else:
        raise TeamRecordError("unknown Team record type")
    _validate_record(record)
    return record


def replay_team_records(
    records: list[TeamRecord] | tuple[TeamRecord, ...],
    *,
    expected_workspace: str,
    expected_workspace_fingerprint: str,
    expected_team_id: str,
    expected_file_name: str,
) -> TeamReplayState:
    if not records:
        raise TeamRecordError("Team transcript is missing its header")
    if len(records) > MAX_TEAM_RECORDS:
        raise TeamRecordError(f"Team transcript exceeds {MAX_TEAM_RECORDS} records")
    header = records[0]
    if not isinstance(header, TeamHeader):
        raise TeamRecordError("team_header must be the first record")
    _validate_record(header)
    expected_id = canonical_team_id(expected_team_id)
    if expected_file_name != f"{expected_id}.jsonl":
        raise TeamRecordError("Team transcript filename does not match its ID")
    if header.team_id != expected_id:
        raise TeamRecordError("Team header ID does not match its transcript")
    if header.workspace != expected_workspace:
        raise TeamRecordError("Team workspace does not match the current workspace")
    if header.workspace_fingerprint != expected_workspace_fingerprint:
        raise TeamRecordError("Team workspace fingerprint does not match the current workspace")

    closed: TeamClosed | None = None
    members: dict[str, TeamMemberState] = {}
    member_names: set[str] = set()
    assignments: dict[str, TeamAssignmentState] = {}
    assignment_children: set[str] = set()
    active_member_assignments: set[str] = set()
    previous_timestamp = header.created_at
    for expected_sequence, record in enumerate(records[1:], start=1):
        if type(record) is TeamHeader:
            raise TeamRecordError("team_header may appear only once")
        if record.sequence != expected_sequence:
            raise TeamRecordError(
                f"Team record sequence must be {expected_sequence}, got {record.sequence}"
            )
        _validate_record(record)
        if record.team_id != header.team_id:
            raise TeamRecordError("Team record ID does not match its header")
        timestamp = _record_timestamp(record)
        if timestamp < previous_timestamp:
            raise TeamRecordError("Team lifecycle timestamps must be nondecreasing")
        previous_timestamp = timestamp
        if closed is not None:
            raise TeamRecordError("Team lifecycle record appears after team_closed")
        if isinstance(record, TeamClosed):
            closed = record
            continue
        if isinstance(record, TeamMemberJoined):
            if record.member_id in members:
                raise TeamRecordError("Team member ID is duplicated")
            if len(members) >= MAX_TEAM_MEMBERS:
                raise TeamRecordError(f"Team exceeds {MAX_TEAM_MEMBERS} members")
            folded = record.name.casefold()
            if folded in member_names:
                raise TeamRecordError("Team member name is duplicated")
            member_names.add(folded)
            members[record.member_id] = TeamMemberState(
                member_id=record.member_id,
                name=record.name,
                role_contract=record.role_contract,
                status=TeamMemberStatus.ACTIVE,
                joined_at=record.joined_at,
            )
            continue
        if isinstance(record, TeamAssignmentCreated):
            if record.assignment_id in assignments:
                raise TeamRecordError("Team assignment ID is duplicated")
            if record.child_run_id in assignment_children:
                raise TeamRecordError("Team assignment Child Run ID is duplicated")
            member = members.get(record.member_id)
            if member is None:
                raise TeamRecordError("Team assignment references an unknown member")
            if member.status is not TeamMemberStatus.ACTIVE:
                raise TeamRecordError("Team assignment requires an active member")
            if record.member_id in active_member_assignments:
                raise TeamRecordError("Team member already has a pending assignment")
            assignments[record.assignment_id] = TeamAssignmentState(
                assignment_id=record.assignment_id,
                member_id=record.member_id,
                child_run_id=record.child_run_id,
                objective=record.objective,
                objective_sha256=record.objective_sha256,
                created_at=record.created_at,
                phase=TeamAssignmentPhase.PENDING_CHILD,
            )
            assignment_children.add(record.child_run_id)
            active_member_assignments.add(record.member_id)
            continue
        if isinstance(record, TeamAssignmentChildBound):
            assignment = assignments.get(record.assignment_id)
            if assignment is None or assignment.phase is not TeamAssignmentPhase.PENDING_CHILD:
                raise TeamRecordError("Team assignment Child binding is invalid")
            if record.child_run_id != assignment.child_run_id:
                raise TeamRecordError("Team assignment Child ID does not match creation")
            assignments[record.assignment_id] = TeamAssignmentState(
                **{
                    **assignment.__dict__,
                    "phase": TeamAssignmentPhase.CHILD_BOUND,
                    "child_header_sequence": record.child_header_sequence,
                    "child_origin_sequence": record.child_origin_sequence,
                    "bound_at": record.bound_at,
                }
            )
            continue
        if isinstance(record, TeamAssignmentObserved):
            assignment = assignments.get(record.assignment_id)
            if assignment is None or assignment.phase is not TeamAssignmentPhase.CHILD_BOUND:
                raise TeamRecordError("Team assignment observation is invalid")
            if record.child_run_id != assignment.child_run_id:
                raise TeamRecordError("Team assignment observed Child ID does not match")
            assignments[record.assignment_id] = TeamAssignmentState(
                **{
                    **assignment.__dict__,
                    "phase": TeamAssignmentPhase.TERMINAL_OBSERVED,
                    "child_session_id": record.child_session_id,
                    "child_outcome": record.child_outcome,
                    "child_terminal_sequence": record.child_terminal_sequence,
                    "handoff_sha256": record.handoff_sha256,
                    "observed_at": record.observed_at,
                }
            )
            active_member_assignments.discard(assignment.member_id)
            continue
        if record.member_id not in members:
            raise TeamRecordError("Team member lifecycle references an unknown member")
        current = members[record.member_id]
        if isinstance(record, TeamMemberDisabled):
            if current.status is not TeamMemberStatus.ACTIVE:
                raise TeamRecordError("Team member can be disabled only when active")
            members[record.member_id] = TeamMemberState(
                **{
                    **current.__dict__,
                    "status": TeamMemberStatus.DISABLED,
                    "disabled_at": record.disabled_at,
                }
            )
        elif isinstance(record, TeamMemberEnabled):
            if current.status is not TeamMemberStatus.DISABLED:
                raise TeamRecordError("Team member can be enabled only when disabled")
            members[record.member_id] = TeamMemberState(
                **{**current.__dict__, "status": TeamMemberStatus.ACTIVE}
            )
        elif isinstance(record, TeamMemberLeft):
            if current.status is TeamMemberStatus.LEFT:
                raise TeamRecordError("Team member has already left")
            if record.member_id in active_member_assignments:
                raise TeamRecordError("Team member cannot leave with pending assignment")
            members[record.member_id] = TeamMemberState(
                **{**current.__dict__, "status": TeamMemberStatus.LEFT, "left_at": record.left_at}
            )
    return TeamReplayState(
        header=header,
        records=tuple(records),
        closed=closed,
        members=tuple(members.values()),
        assignments=tuple(assignments.values()),
    )


def _validate_record(record: TeamRecord) -> None:
    if isinstance(record, TeamHeader):
        if (
            record.record_type != "team_header"
            or record.schema_version != TEAM_HEADER_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team header schema")
        if type(record.sequence) is not int or record.sequence != 0:
            raise TeamRecordError("Team header sequence must be 0")
        canonical_team_id(record.team_id)
        _validate_workspace(record.workspace)
        _validate_fingerprint(record.workspace_fingerprint)
        if record.workspace_fingerprint != workspace_fingerprint(Path(record.workspace)):
            raise TeamRecordError("Team workspace fingerprint does not match its workspace")
        try:
            canonical_session_id(record.owner_session_id)
        except Exception as error:
            raise TeamRecordError(f"Team owner Session ID is invalid: {error}") from None
        canonical_team_name(record.name)
        canonical_team_timestamp(record.created_at, "Team created_at")
        return
    if isinstance(record, TeamClosed):
        if (
            record.record_type != "team_closed"
            or record.schema_version != TEAM_CLOSED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team closed schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team closed sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_timestamp(record.closed_at, "Team closed_at")
        return
    if isinstance(record, TeamMemberJoined):
        if (
            record.record_type != "team_member_joined"
            or record.schema_version != TEAM_MEMBER_JOINED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team member-joined schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team member-joined sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.member_id)
        canonical_team_name(record.name)
        if record.role_contract != TEAM_MEMBER_ROLE_CONTRACT:
            raise TeamRecordError("Team member role contract is invalid")
        canonical_team_timestamp(record.joined_at, "Team member joined_at")
        return
    if isinstance(record, TeamMemberDisabled):
        if (
            record.record_type != "team_member_disabled"
            or record.schema_version != TEAM_MEMBER_DISABLED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team member-disabled schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team member-disabled sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.member_id)
        canonical_team_reason(record.reason)
        canonical_team_timestamp(record.disabled_at, "Team member disabled_at")
        return
    if isinstance(record, TeamMemberEnabled):
        if (
            record.record_type != "team_member_enabled"
            or record.schema_version != TEAM_MEMBER_ENABLED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team member-enabled schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team member-enabled sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.member_id)
        canonical_team_timestamp(record.enabled_at, "Team member enabled_at")
        return
    if isinstance(record, TeamMemberLeft):
        if (
            record.record_type != "team_member_left"
            or record.schema_version != TEAM_MEMBER_LEFT_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team member-left schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team member-left sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.member_id)
        canonical_team_reason(record.reason)
        canonical_team_timestamp(record.left_at, "Team member left_at")
        return
    if isinstance(record, TeamAssignmentCreated):
        if (
            record.record_type != "team_assignment_created"
            or record.schema_version != TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team assignment-created schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team assignment-created sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.assignment_id)
        canonical_team_id(record.member_id)
        canonical_team_id(record.child_run_id)
        objective = canonical_team_assignment_objective(record.objective)
        if record.objective_sha256 != team_assignment_objective_sha256(objective):
            raise TeamRecordError("Team assignment objective digest does not match objective")
        canonical_team_timestamp(record.created_at, "Team assignment created_at")
        return
    if isinstance(record, TeamAssignmentChildBound):
        if (
            record.record_type != "team_assignment_child_bound"
            or record.schema_version != TEAM_ASSIGNMENT_CHILD_BOUND_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team assignment-bound schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team assignment-bound sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.assignment_id)
        canonical_team_id(record.child_run_id)
        if type(record.child_header_sequence) is not int or record.child_header_sequence != 0:
            raise TeamRecordError("Team assignment Child header sequence is invalid")
        if type(record.child_origin_sequence) is not int or record.child_origin_sequence != 1:
            raise TeamRecordError("Team assignment Child origin sequence is invalid")
        canonical_team_timestamp(record.bound_at, "Team assignment bound_at")
        return
    if isinstance(record, TeamAssignmentObserved):
        if (
            record.record_type != "team_assignment_observed"
            or record.schema_version != TEAM_ASSIGNMENT_OBSERVED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team assignment-observed schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team assignment-observed sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.assignment_id)
        canonical_team_id(record.child_run_id)
        if record.child_session_id is not None:
            canonical_team_id(record.child_session_id)
        if record.child_outcome not in {"completed", "failed", "cancelled", "interrupted"}:
            raise TeamRecordError("Team assignment Child outcome is invalid")
        if type(record.child_terminal_sequence) is not int or record.child_terminal_sequence < 1:
            raise TeamRecordError("Team assignment terminal sequence is invalid")
        if (
            not isinstance(record.handoff_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", record.handoff_sha256) is None
        ):
            raise TeamRecordError("Team assignment handoff digest is invalid")
        canonical_team_timestamp(record.observed_at, "Team assignment observed_at")
        return
    raise TeamRecordError("unsupported Team record")


def _canonical_uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TeamRecordError(f"{label} must be text")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise TeamRecordError(f"{label} must be a canonical UUID4") from None
    if parsed.version != 4 or str(parsed) != value:
        raise TeamRecordError(f"{label} must be a canonical UUID4")
    return value


def _bounded_text(value: object, label: str, *, max_characters: int, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TeamRecordError(f"{label} must be nonblank text")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise TeamRecordError(f"{label} must not contain control characters")
    if len(value) > max_characters or len(value.encode("utf-8")) > max_bytes:
        raise TeamRecordError(f"{label} exceeds its bound")
    return value


def canonical_team_reason(value: object) -> str:
    return _bounded_text(
        value,
        "Team member reason",
        max_characters=MAX_TEAM_REASON_CHARACTERS,
        max_bytes=MAX_TEAM_REASON_BYTES,
    )


def _record_timestamp(record: TeamRecord) -> str:
    if isinstance(record, TeamClosed):
        return record.closed_at
    if isinstance(record, TeamMemberJoined):
        return record.joined_at
    if isinstance(record, TeamMemberDisabled):
        return record.disabled_at
    if isinstance(record, TeamMemberEnabled):
        return record.enabled_at
    if isinstance(record, TeamMemberLeft):
        return record.left_at
    if isinstance(record, TeamAssignmentCreated):
        return record.created_at
    if isinstance(record, TeamAssignmentChildBound):
        return record.bound_at
    if isinstance(record, TeamAssignmentObserved):
        return record.observed_at
    raise TeamRecordError("Team record has no lifecycle timestamp")


def _validate_workspace(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or not Path(value).is_absolute()
        or "\n" in value
        or "\r" in value
    ):
        raise TeamRecordError("Team workspace is invalid")


def _validate_fingerprint(value: object) -> None:
    if not isinstance(value, str) or _WORKSPACE_FINGERPRINT.fullmatch(value) is None:
        raise TeamRecordError("Team workspace fingerprint is invalid")


def _require_fields(value: dict[str, object], label: str, *fields: str) -> None:
    expected = set(fields)
    if set(value) != expected:
        raise TeamRecordError(f"{label} has unknown or missing fields")
