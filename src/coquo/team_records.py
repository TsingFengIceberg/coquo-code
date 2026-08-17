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
TEAM_MEMBER_JOINED_V2_SCHEMA_VERSION = 2
TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_CREATED_V2_SCHEMA_VERSION = 2
TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION = 3
TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION = 4
TEAM_SCHEDULE_STARTED_SCHEMA_VERSION = 1
TEAM_SCHEDULE_STARTED_V2_SCHEMA_VERSION = 2
TEAM_SCHEDULE_CANCEL_REQUESTED_SCHEMA_VERSION = 1
TEAM_SCHEDULE_FINISHED_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_CHILD_BOUND_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_OBSERVED_SCHEMA_VERSION = 1
TEAM_MESSAGE_SENT_SCHEMA_VERSION = 1
TEAM_MESSAGE_READ_SCHEMA_VERSION = 1
TEAM_MESSAGE_CANCELLED_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_MAILBOX_BOUND_SCHEMA_VERSION = 1
TEAM_ASSIGNMENT_MAILBOX_OBSERVED_SCHEMA_VERSION = 1
TEAM_WORK_ITEM_CREATED_SCHEMA_VERSION = 1
TEAM_WORK_ITEM_RELEASED_SCHEMA_VERSION = 1
TEAM_WORK_ITEM_COMPLETED_SCHEMA_VERSION = 1
TEAM_WORK_ITEM_CANCELLED_SCHEMA_VERSION = 1
MAX_TEAM_RECORD_BYTES = 64 * 1024
MAX_TEAM_RECORDS = 10_000
MAX_TEAM_NAME_CHARACTERS = 80
MAX_TEAM_NAME_BYTES = 256
MAX_TEAM_REASON_CHARACTERS = 4096
MAX_TEAM_REASON_BYTES = 16 * 1024
MAX_TEAM_MEMBERS = 64
TEAM_MEMBER_ROLE_CONTRACT = "read-only-investigator-v1"
TEAM_MEMBER_ROLE_CONTRACTS = (
    TEAM_MEMBER_ROLE_CONTRACT,
    "isolated-workspace-writer-v1",
    "isolated-coder-v1",
)
MAX_TEAM_ASSIGNMENT_OBJECTIVE_CHARACTERS = 4096
MAX_TEAM_ASSIGNMENT_OBJECTIVE_BYTES = 16 * 1024
MAX_TEAM_MESSAGE_BODY_CHARACTERS = 32 * 1024
MAX_TEAM_MESSAGE_BODY_BYTES = 32 * 1024
MAX_TEAM_OWNER_MESSAGE_CHARACTERS = 4096
MAX_TEAM_OWNER_MESSAGE_BYTES = 8 * 1024
MAX_TEAM_WORK_ITEMS = 1024
MAX_TEAM_WORK_DEPENDENCIES = 16
MAX_TEAM_SCHEDULE_ASSIGNMENTS = 32
MAX_TEAM_SCHEDULE_PARALLEL = 4
MAX_TEAM_SCHEDULE_REASON_CHARACTERS = 4096
MAX_TEAM_SCHEDULE_REASON_BYTES = 16 * 1024

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


class TeamMessageStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    UNREAD = "unread"
    READ = "read"


class TeamWorkStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    ASSIGNED = "assigned"
    REVIEW = "review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TeamScheduleSource(StrEnum):
    HOST = "host"
    MODEL = "model"
    SHUTDOWN = "shutdown"


class TeamScheduleOutcome(StrEnum):
    IDLE = "idle"
    LIMIT_REACHED = "limit_reached"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TeamScheduleStatus(StrEnum):
    RUNNING = "running"
    CANCELLING = "cancelling"
    IDLE = "idle"
    LIMIT_REACHED = "limit_reached"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            TeamScheduleStatus.IDLE,
            TeamScheduleStatus.LIMIT_REACHED,
            TeamScheduleStatus.CANCELLED,
            TeamScheduleStatus.FAILED,
            TeamScheduleStatus.INTERRUPTED,
        }


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
    work_item_id: str | None = None
    schedule_run_id: str | None = None
    member_role_contract: str = TEAM_MEMBER_ROLE_CONTRACT
    worktree_id: str | None = None
    base_commit: str | None = None
    target_ref: str | None = None
    record_type: str = "team_assignment_created"
    schema_version: int = TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamScheduleStarted:
    sequence: int
    team_id: str
    schedule_run_id: str
    source: str
    max_assignments: int
    max_parallel: int
    started_at: str
    capability_snapshot_sha256: str | None = None
    eligible_members: tuple[dict[str, object], ...] = ()
    parent_permission_mode: str | None = None
    record_type: str = "team_schedule_started"
    schema_version: int = TEAM_SCHEDULE_STARTED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamScheduleCancelRequested:
    sequence: int
    team_id: str
    schedule_run_id: str
    reason: str
    source: str
    requested_at: str
    record_type: str = "team_schedule_cancel_requested"
    schema_version: int = TEAM_SCHEDULE_CANCEL_REQUESTED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamScheduleFinished:
    sequence: int
    team_id: str
    schedule_run_id: str
    outcome: str
    assignment_count: int
    result_code: str
    message: str
    finished_at: str
    record_type: str = "team_schedule_finished"
    schema_version: int = TEAM_SCHEDULE_FINISHED_SCHEMA_VERSION


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


@dataclass(frozen=True)
class TeamAssignmentMailboxBound:
    sequence: int
    team_id: str
    assignment_id: str
    child_run_id: str
    member_id: str
    delivery_id: str
    inbox_message_ids: tuple[str, ...]
    reply_message_id: str
    bound_at: str
    record_type: str = "team_assignment_mailbox_bound"
    schema_version: int = TEAM_ASSIGNMENT_MAILBOX_BOUND_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamAssignmentMailboxObserved:
    sequence: int
    team_id: str
    assignment_id: str
    delivery_id: str
    child_session_id: str
    child_turn_record_sequence: int
    child_user_message_sha256: str
    observed_at: str
    record_type: str = "team_assignment_mailbox_observed"
    schema_version: int = TEAM_ASSIGNMENT_MAILBOX_OBSERVED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamWorkItemCreated:
    sequence: int
    team_id: str
    work_item_id: str
    title: str
    objective: str
    dependency_ids: tuple[str, ...]
    created_at: str
    record_type: str = "team_work_item_created"
    schema_version: int = TEAM_WORK_ITEM_CREATED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamWorkItemReleased:
    sequence: int
    team_id: str
    work_item_id: str
    assignment_id: str
    reason: str
    released_at: str
    record_type: str = "team_work_item_released"
    schema_version: int = TEAM_WORK_ITEM_RELEASED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamWorkItemCompleted:
    sequence: int
    team_id: str
    work_item_id: str
    assignment_id: str
    handoff_sha256: str
    evidence: str
    completed_at: str
    record_type: str = "team_work_item_completed"
    schema_version: int = TEAM_WORK_ITEM_COMPLETED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamWorkItemCancelled:
    sequence: int
    team_id: str
    work_item_id: str
    reason: str
    cancelled_at: str
    record_type: str = "team_work_item_cancelled"
    schema_version: int = TEAM_WORK_ITEM_CANCELLED_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMessageSent:
    sequence: int
    team_id: str
    message_id: str
    sender_member_id: str | None
    recipient_member_id: str | None
    body: str
    body_sha256: str
    source_assignment_id: str | None
    source_child_session_id: str | None
    source_turn_record_sequence: int | None
    source_handoff_sha256: str | None
    sent_at: str
    record_type: str = "team_message_sent"
    schema_version: int = TEAM_MESSAGE_SENT_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMessageRead:
    sequence: int
    team_id: str
    message_id: str
    read_at: str
    record_type: str = "team_message_read"
    schema_version: int = TEAM_MESSAGE_READ_SCHEMA_VERSION


@dataclass(frozen=True)
class TeamMessageCancelled:
    sequence: int
    team_id: str
    message_id: str
    reason: str
    cancelled_at: str
    record_type: str = "team_message_cancelled"
    schema_version: int = TEAM_MESSAGE_CANCELLED_SCHEMA_VERSION


TeamRecord: TypeAlias = (
    TeamHeader
    | TeamClosed
    | TeamMemberJoined
    | TeamMemberDisabled
    | TeamMemberEnabled
    | TeamMemberLeft
    | TeamAssignmentCreated
    | TeamScheduleStarted
    | TeamScheduleCancelRequested
    | TeamScheduleFinished
    | TeamAssignmentChildBound
    | TeamAssignmentObserved
    | TeamAssignmentMailboxBound
    | TeamAssignmentMailboxObserved
    | TeamWorkItemCreated
    | TeamWorkItemReleased
    | TeamWorkItemCompleted
    | TeamWorkItemCancelled
    | TeamMessageSent
    | TeamMessageRead
    | TeamMessageCancelled
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
    work_item_id: str | None = None
    delivery_id: str | None = None
    inbox_message_ids: tuple[str, ...] = ()
    reply_message_id: str | None = None
    schedule_run_id: str | None = None
    mailbox_bound_at: str | None = None
    mailbox_observed_at: str | None = None
    child_user_message_sha256: str | None = None
    member_role_contract: str = TEAM_MEMBER_ROLE_CONTRACT
    worktree_id: str | None = None
    base_commit: str | None = None
    target_ref: str | None = None


@dataclass(frozen=True)
class TeamWorkItemState:
    work_item_id: str
    title: str
    objective: str
    dependency_ids: tuple[str, ...]
    status: TeamWorkStatus
    blocked_dependency_ids: tuple[str, ...] = ()
    assignment_ids: tuple[str, ...] = ()
    current_assignment_id: str | None = None
    handoff_sha256: str | None = None
    completion_evidence: str | None = None
    terminal_reason: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class TeamMessageState:
    message_id: str
    sender_member_id: str | None
    recipient_member_id: str | None
    body: str
    body_sha256: str
    source_assignment_id: str | None
    source_child_session_id: str | None
    source_turn_record_sequence: int | None
    source_handoff_sha256: str | None
    sent_at: str
    status: TeamMessageStatus
    read_at: str | None = None
    cancelled_at: str | None = None


@dataclass(frozen=True)
class TeamScheduleState:
    schedule_run_id: str
    source: TeamScheduleSource
    max_assignments: int
    max_parallel: int
    started_at: str
    status: TeamScheduleStatus
    cancel_reason: str | None = None
    cancel_source: TeamScheduleSource | None = None
    cancel_requested_at: str | None = None
    outcome: TeamScheduleOutcome | None = None
    assignment_count: int = 0
    result_code: str | None = None
    message: str | None = None
    finished_at: str | None = None
    assignment_ids: tuple[str, ...] = ()
    capability_snapshot_sha256: str | None = None
    eligible_members: tuple[dict[str, object], ...] = ()
    parent_permission_mode: str | None = None


@dataclass(frozen=True)
class TeamReplayState:
    header: TeamHeader
    records: tuple[TeamRecord, ...]
    closed: TeamClosed | None = None
    members: tuple[TeamMemberState, ...] = ()
    assignments: tuple[TeamAssignmentState, ...] = ()
    messages: tuple[TeamMessageState, ...] = ()
    work_items: tuple[TeamWorkItemState, ...] = ()
    schedules: tuple[TeamScheduleState, ...] = ()

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


def team_message_body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


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
    elif isinstance(record, TeamScheduleStarted):
        value = {
            "max_assignments": record.max_assignments,
            "max_parallel": record.max_parallel,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "schedule_run_id": record.schedule_run_id,
            "source": record.source,
            "started_at": record.started_at,
            "team_id": record.team_id,
        }
        if record.schema_version == TEAM_SCHEDULE_STARTED_V2_SCHEMA_VERSION:
            value["capability_snapshot_sha256"] = record.capability_snapshot_sha256
            value["eligible_members"] = [dict(item) for item in record.eligible_members]
            value["parent_permission_mode"] = record.parent_permission_mode
    elif isinstance(record, TeamScheduleCancelRequested):
        value = {
            "reason": record.reason,
            "record_type": record.record_type,
            "requested_at": record.requested_at,
            "schedule_run_id": record.schedule_run_id,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "source": record.source,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamScheduleFinished):
        value = {
            "assignment_count": record.assignment_count,
            "finished_at": record.finished_at,
            "message": record.message,
            "outcome": record.outcome,
            "record_type": record.record_type,
            "result_code": record.result_code,
            "schema_version": record.schema_version,
            "schedule_run_id": record.schedule_run_id,
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
        if record.schema_version == TEAM_ASSIGNMENT_CREATED_V2_SCHEMA_VERSION:
            value["work_item_id"] = record.work_item_id
        if record.schema_version == TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION:
            value["schedule_run_id"] = record.schedule_run_id
            value["work_item_id"] = record.work_item_id
        if record.schema_version == TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION:
            value.update(
                {
                    "base_commit": record.base_commit,
                    "member_role_contract": record.member_role_contract,
                    "schedule_run_id": record.schedule_run_id,
                    "target_ref": record.target_ref,
                    "work_item_id": record.work_item_id,
                    "worktree_id": record.worktree_id,
                }
            )
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
    elif isinstance(record, TeamAssignmentMailboxBound):
        value = {
            "assignment_id": record.assignment_id,
            "bound_at": record.bound_at,
            "child_run_id": record.child_run_id,
            "delivery_id": record.delivery_id,
            "inbox_message_ids": list(record.inbox_message_ids),
            "member_id": record.member_id,
            "record_type": record.record_type,
            "reply_message_id": record.reply_message_id,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamAssignmentMailboxObserved):
        value = {
            "assignment_id": record.assignment_id,
            "child_session_id": record.child_session_id,
            "child_turn_record_sequence": record.child_turn_record_sequence,
            "child_user_message_sha256": record.child_user_message_sha256,
            "delivery_id": record.delivery_id,
            "observed_at": record.observed_at,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamWorkItemCreated):
        value = {
            "created_at": record.created_at,
            "dependency_ids": list(record.dependency_ids),
            "objective": record.objective,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
            "title": record.title,
            "work_item_id": record.work_item_id,
        }
    elif isinstance(record, TeamWorkItemReleased):
        value = {
            "assignment_id": record.assignment_id,
            "reason": record.reason,
            "record_type": record.record_type,
            "released_at": record.released_at,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
            "work_item_id": record.work_item_id,
        }
    elif isinstance(record, TeamWorkItemCompleted):
        value = {
            "assignment_id": record.assignment_id,
            "completed_at": record.completed_at,
            "evidence": record.evidence,
            "handoff_sha256": record.handoff_sha256,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
            "work_item_id": record.work_item_id,
        }
    elif isinstance(record, TeamWorkItemCancelled):
        value = {
            "cancelled_at": record.cancelled_at,
            "reason": record.reason,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
            "work_item_id": record.work_item_id,
        }
    elif isinstance(record, TeamMessageSent):
        value = {
            "body": record.body,
            "body_sha256": record.body_sha256,
            "message_id": record.message_id,
            "recipient_member_id": record.recipient_member_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sender_member_id": record.sender_member_id,
            "sent_at": record.sent_at,
            "sequence": record.sequence,
            "source_assignment_id": record.source_assignment_id,
            "source_child_session_id": record.source_child_session_id,
            "source_handoff_sha256": record.source_handoff_sha256,
            "source_turn_record_sequence": record.source_turn_record_sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamMessageRead):
        value = {
            "message_id": record.message_id,
            "read_at": record.read_at,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "team_id": record.team_id,
        }
    elif isinstance(record, TeamMessageCancelled):
        value = {
            "cancelled_at": record.cancelled_at,
            "message_id": record.message_id,
            "reason": record.reason,
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
    elif record_type == "team_schedule_started":
        schema_version = value.get("schema_version")
        expected = {
            "max_assignments",
            "max_parallel",
            "record_type",
            "schema_version",
            "sequence",
            "schedule_run_id",
            "source",
            "started_at",
            "team_id",
        }
        if schema_version == TEAM_SCHEDULE_STARTED_V2_SCHEMA_VERSION:
            expected.update(
                {"capability_snapshot_sha256", "eligible_members", "parent_permission_mode"}
            )
        if set(value) != expected:
            raise TeamRecordError("team_schedule_started has unknown or missing fields")
        record = TeamScheduleStarted(
            sequence=value["sequence"],
            team_id=value["team_id"],
            schedule_run_id=value["schedule_run_id"],
            source=value["source"],
            max_assignments=value["max_assignments"],
            max_parallel=value["max_parallel"],
            started_at=value["started_at"],
            capability_snapshot_sha256=value.get("capability_snapshot_sha256"),
            eligible_members=tuple(value.get("eligible_members", ())),
            parent_permission_mode=value.get("parent_permission_mode"),
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_schedule_cancel_requested":
        _require_fields(
            value,
            "team_schedule_cancel_requested",
            "reason",
            "record_type",
            "requested_at",
            "schedule_run_id",
            "schema_version",
            "sequence",
            "source",
            "team_id",
        )
        record = TeamScheduleCancelRequested(
            sequence=value["sequence"],
            team_id=value["team_id"],
            schedule_run_id=value["schedule_run_id"],
            reason=value["reason"],
            source=value["source"],
            requested_at=value["requested_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_schedule_finished":
        _require_fields(
            value,
            "team_schedule_finished",
            "assignment_count",
            "finished_at",
            "message",
            "outcome",
            "record_type",
            "result_code",
            "schema_version",
            "schedule_run_id",
            "sequence",
            "team_id",
        )
        record = TeamScheduleFinished(
            sequence=value["sequence"],
            team_id=value["team_id"],
            schedule_run_id=value["schedule_run_id"],
            outcome=value["outcome"],
            assignment_count=value["assignment_count"],
            result_code=value["result_code"],
            message=value["message"],
            finished_at=value["finished_at"],
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
        schema_version = value.get("schema_version")
        expected = {
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
        }
        if schema_version == TEAM_ASSIGNMENT_CREATED_V2_SCHEMA_VERSION:
            expected.add("work_item_id")
        elif schema_version == TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION:
            expected.update({"work_item_id", "schedule_run_id"})
        elif schema_version == TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION:
            expected.update(
                {
                    "base_commit",
                    "member_role_contract",
                    "schedule_run_id",
                    "target_ref",
                    "work_item_id",
                    "worktree_id",
                }
            )
        if set(value) != expected:
            raise TeamRecordError("team_assignment_created has unknown or missing fields")
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
            work_item_id=value.get("work_item_id"),
            schedule_run_id=value.get("schedule_run_id"),
            member_role_contract=value.get("member_role_contract", TEAM_MEMBER_ROLE_CONTRACT),
            worktree_id=value.get("worktree_id"),
            base_commit=value.get("base_commit"),
            target_ref=value.get("target_ref"),
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
    elif record_type == "team_assignment_mailbox_bound":
        _require_fields(
            value,
            "team_assignment_mailbox_bound",
            "assignment_id",
            "bound_at",
            "child_run_id",
            "delivery_id",
            "inbox_message_ids",
            "member_id",
            "record_type",
            "reply_message_id",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamAssignmentMailboxBound(
            sequence=value["sequence"],
            team_id=value["team_id"],
            assignment_id=value["assignment_id"],
            child_run_id=value["child_run_id"],
            member_id=value["member_id"],
            delivery_id=value["delivery_id"],
            inbox_message_ids=tuple(value["inbox_message_ids"]),
            reply_message_id=value["reply_message_id"],
            bound_at=value["bound_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_assignment_mailbox_observed":
        _require_fields(
            value,
            "team_assignment_mailbox_observed",
            "assignment_id",
            "child_session_id",
            "child_turn_record_sequence",
            "child_user_message_sha256",
            "delivery_id",
            "observed_at",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamAssignmentMailboxObserved(
            sequence=value["sequence"],
            team_id=value["team_id"],
            assignment_id=value["assignment_id"],
            delivery_id=value["delivery_id"],
            child_session_id=value["child_session_id"],
            child_turn_record_sequence=value["child_turn_record_sequence"],
            child_user_message_sha256=value["child_user_message_sha256"],
            observed_at=value["observed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_work_item_created":
        _require_fields(
            value,
            "team_work_item_created",
            "created_at",
            "dependency_ids",
            "objective",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
            "title",
            "work_item_id",
        )
        record = TeamWorkItemCreated(
            sequence=value["sequence"],
            team_id=value["team_id"],
            work_item_id=value["work_item_id"],
            title=value["title"],
            objective=value["objective"],
            dependency_ids=tuple(value["dependency_ids"]),
            created_at=value["created_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_work_item_released":
        _require_fields(
            value,
            "team_work_item_released",
            "assignment_id",
            "reason",
            "record_type",
            "released_at",
            "schema_version",
            "sequence",
            "team_id",
            "work_item_id",
        )
        record = TeamWorkItemReleased(
            sequence=value["sequence"],
            team_id=value["team_id"],
            work_item_id=value["work_item_id"],
            assignment_id=value["assignment_id"],
            reason=value["reason"],
            released_at=value["released_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_work_item_completed":
        _require_fields(
            value,
            "team_work_item_completed",
            "assignment_id",
            "completed_at",
            "evidence",
            "handoff_sha256",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
            "work_item_id",
        )
        record = TeamWorkItemCompleted(
            sequence=value["sequence"],
            team_id=value["team_id"],
            work_item_id=value["work_item_id"],
            assignment_id=value["assignment_id"],
            handoff_sha256=value["handoff_sha256"],
            evidence=value["evidence"],
            completed_at=value["completed_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_work_item_cancelled":
        _require_fields(
            value,
            "team_work_item_cancelled",
            "cancelled_at",
            "reason",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
            "work_item_id",
        )
        record = TeamWorkItemCancelled(
            sequence=value["sequence"],
            team_id=value["team_id"],
            work_item_id=value["work_item_id"],
            reason=value["reason"],
            cancelled_at=value["cancelled_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_message_sent":
        _require_fields(
            value,
            "team_message_sent",
            "body",
            "body_sha256",
            "message_id",
            "recipient_member_id",
            "record_type",
            "schema_version",
            "sender_member_id",
            "sent_at",
            "sequence",
            "source_assignment_id",
            "source_child_session_id",
            "source_handoff_sha256",
            "source_turn_record_sequence",
            "team_id",
        )
        record = TeamMessageSent(
            sequence=value["sequence"],
            team_id=value["team_id"],
            message_id=value["message_id"],
            sender_member_id=value["sender_member_id"],
            recipient_member_id=value["recipient_member_id"],
            body=value["body"],
            body_sha256=value["body_sha256"],
            source_assignment_id=value["source_assignment_id"],
            source_child_session_id=value["source_child_session_id"],
            source_turn_record_sequence=value["source_turn_record_sequence"],
            source_handoff_sha256=value["source_handoff_sha256"],
            sent_at=value["sent_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_message_read":
        _require_fields(
            value,
            "team_message_read",
            "message_id",
            "read_at",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamMessageRead(
            sequence=value["sequence"],
            team_id=value["team_id"],
            message_id=value["message_id"],
            read_at=value["read_at"],
            record_type=value["record_type"],
            schema_version=value["schema_version"],
        )
    elif record_type == "team_message_cancelled":
        _require_fields(
            value,
            "team_message_cancelled",
            "cancelled_at",
            "message_id",
            "reason",
            "record_type",
            "schema_version",
            "sequence",
            "team_id",
        )
        record = TeamMessageCancelled(
            sequence=value["sequence"],
            team_id=value["team_id"],
            message_id=value["message_id"],
            reason=value["reason"],
            cancelled_at=value["cancelled_at"],
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
    work_items: dict[str, TeamWorkItemState] = {}
    assignment_children: set[str] = set()
    active_member_assignments: set[str] = set()
    messages: dict[str, TeamMessageState] = {}
    schedules: dict[str, TeamScheduleState] = {}
    delivery_ids: set[str] = set()
    reply_ids: set[str] = set()
    bound_message_ids: set[str] = set()
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
        if isinstance(record, TeamScheduleStarted):
            if record.schedule_run_id in schedules:
                raise TeamRecordError("Team schedule run ID is duplicated")
            if any(not schedule.status.terminal for schedule in schedules.values()):
                raise TeamRecordError("Team already has a nonterminal schedule")
            schedules[record.schedule_run_id] = TeamScheduleState(
                schedule_run_id=record.schedule_run_id,
                source=TeamScheduleSource(record.source),
                max_assignments=record.max_assignments,
                max_parallel=record.max_parallel,
                started_at=record.started_at,
                status=TeamScheduleStatus.RUNNING,
                capability_snapshot_sha256=record.capability_snapshot_sha256,
                eligible_members=record.eligible_members,
                parent_permission_mode=record.parent_permission_mode,
            )
            continue
        if isinstance(record, TeamScheduleCancelRequested):
            schedule = schedules.get(record.schedule_run_id)
            if schedule is None or schedule.status.terminal:
                raise TeamRecordError("Team schedule cancellation is invalid")
            if schedule.cancel_requested_at is not None:
                raise TeamRecordError("Team schedule cancellation is duplicated")
            schedules[record.schedule_run_id] = TeamScheduleState(
                **{
                    **schedule.__dict__,
                    "status": TeamScheduleStatus.CANCELLING,
                    "cancel_reason": record.reason,
                    "cancel_source": TeamScheduleSource(record.source),
                    "cancel_requested_at": record.requested_at,
                }
            )
            continue
        if isinstance(record, TeamScheduleFinished):
            schedule = schedules.get(record.schedule_run_id)
            if schedule is None or schedule.status.terminal:
                raise TeamRecordError("Team schedule finish is invalid")
            if record.assignment_count != len(schedule.assignment_ids):
                raise TeamRecordError("Team schedule assignment count is invalid")
            if any(
                assignments[assignment_id].phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
                for assignment_id in schedule.assignment_ids
                if assignment_id in assignments
            ):
                raise TeamRecordError("Team schedule cannot finish with pending assignments")
            outcome = TeamScheduleOutcome(record.outcome)
            if outcome is TeamScheduleOutcome.CANCELLED and schedule.cancel_requested_at is None:
                raise TeamRecordError("Cancelled Team schedule requires a cancel request")
            if (
                outcome is not TeamScheduleOutcome.CANCELLED
                and schedule.cancel_requested_at is not None
            ):
                raise TeamRecordError("Cancelling Team schedule must finish as cancelled")
            status = TeamScheduleStatus(record.outcome)
            schedules[record.schedule_run_id] = TeamScheduleState(
                **{
                    **schedule.__dict__,
                    "status": status,
                    "outcome": outcome,
                    "assignment_count": record.assignment_count,
                    "result_code": record.result_code,
                    "message": record.message,
                    "finished_at": record.finished_at,
                }
            )
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
            schedule = schedules.get(record.schedule_run_id) if record.schedule_run_id else None
            if (
                record.schema_version
                in {
                    TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION,
                    TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION,
                }
                and record.schedule_run_id is not None
            ):
                if schedule is None or schedule.status.terminal:
                    raise TeamRecordError("Team assignment references an invalid schedule run")
                if len(schedule.assignment_ids) >= schedule.max_assignments:
                    raise TeamRecordError("Team schedule assignment limit was exceeded")
            work_item = (
                work_items.get(record.work_item_id) if record.work_item_id is not None else None
            )
            if record.work_item_id is not None:
                if work_item is None or work_item.status is not TeamWorkStatus.READY:
                    raise TeamRecordError("Team assignment work item is not ready")
            if record.schema_version == TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION:
                if record.member_role_contract != member.role_contract:
                    raise TeamRecordError("Team assignment role does not match its member")
            assignments[record.assignment_id] = TeamAssignmentState(
                assignment_id=record.assignment_id,
                member_id=record.member_id,
                child_run_id=record.child_run_id,
                objective=record.objective,
                objective_sha256=record.objective_sha256,
                created_at=record.created_at,
                phase=TeamAssignmentPhase.PENDING_CHILD,
                work_item_id=record.work_item_id,
                schedule_run_id=record.schedule_run_id,
                member_role_contract=record.member_role_contract,
                worktree_id=record.worktree_id,
                base_commit=record.base_commit,
                target_ref=record.target_ref,
            )
            assignment_children.add(record.child_run_id)
            active_member_assignments.add(record.member_id)
            if work_item is not None:
                work_items[work_item.work_item_id] = TeamWorkItemState(
                    **{
                        **work_item.__dict__,
                        "status": TeamWorkStatus.ASSIGNED,
                        "assignment_ids": (*work_item.assignment_ids, record.assignment_id),
                        "current_assignment_id": record.assignment_id,
                    }
                )
            if schedule is not None:
                schedules[record.schedule_run_id] = TeamScheduleState(
                    **{
                        **schedule.__dict__,
                        "assignment_ids": (*schedule.assignment_ids, record.assignment_id),
                    }
                )
            continue
        if isinstance(record, TeamWorkItemCreated):
            if record.work_item_id in work_items:
                raise TeamRecordError("Team work item ID is duplicated")
            if len(work_items) >= MAX_TEAM_WORK_ITEMS:
                raise TeamRecordError(f"Team exceeds {MAX_TEAM_WORK_ITEMS} work items")
            if len(record.dependency_ids) > MAX_TEAM_WORK_DEPENDENCIES or len(
                set(record.dependency_ids)
            ) != len(record.dependency_ids):
                raise TeamRecordError("Team work item dependencies are invalid")
            for dependency_id in record.dependency_ids:
                if dependency_id not in work_items:
                    raise TeamRecordError(
                        "Team work item dependency must reference an earlier item"
                    )
            work_items[record.work_item_id] = TeamWorkItemState(
                work_item_id=record.work_item_id,
                title=record.title,
                objective=record.objective,
                dependency_ids=record.dependency_ids,
                status=TeamWorkStatus.READY,
                created_at=record.created_at,
            )
            continue
        if isinstance(record, TeamWorkItemCancelled):
            item = work_items.get(record.work_item_id)
            if item is None or item.status in {TeamWorkStatus.COMPLETED, TeamWorkStatus.CANCELLED}:
                raise TeamRecordError("Team work item cancellation is invalid")
            if item.status is TeamWorkStatus.ASSIGNED:
                raise TeamRecordError("Team work item cannot be cancelled while assigned")
            work_items[record.work_item_id] = TeamWorkItemState(
                **{
                    **item.__dict__,
                    "status": TeamWorkStatus.CANCELLED,
                    "terminal_reason": record.reason,
                }
            )
            continue
        if isinstance(record, TeamWorkItemReleased):
            item = work_items.get(record.work_item_id)
            if (
                item is None
                or item.status is not TeamWorkStatus.REVIEW
                or item.current_assignment_id != record.assignment_id
            ):
                raise TeamRecordError("Team work item release is invalid")
            work_items[record.work_item_id] = TeamWorkItemState(
                **{
                    **item.__dict__,
                    "status": TeamWorkStatus.READY,
                    "current_assignment_id": None,
                    "terminal_reason": record.reason,
                }
            )
            continue
        if isinstance(record, TeamWorkItemCompleted):
            item = work_items.get(record.work_item_id)
            if (
                item is None
                or item.status is not TeamWorkStatus.REVIEW
                or item.current_assignment_id != record.assignment_id
            ):
                raise TeamRecordError("Team work item completion is invalid")
            work_items[record.work_item_id] = TeamWorkItemState(
                **{
                    **item.__dict__,
                    "status": TeamWorkStatus.COMPLETED,
                    "handoff_sha256": record.handoff_sha256,
                    "completion_evidence": record.evidence,
                }
            )
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
            if record.child_outcome != "completed":
                bound_message_ids.difference_update(assignment.inbox_message_ids)
            if assignment.work_item_id is not None:
                work_item = work_items[assignment.work_item_id]
                work_items[assignment.work_item_id] = TeamWorkItemState(
                    **{**work_item.__dict__, "status": TeamWorkStatus.REVIEW}
                )
            continue
        if isinstance(record, TeamAssignmentMailboxBound):
            assignment = assignments.get(record.assignment_id)
            if assignment is None or assignment.phase is not TeamAssignmentPhase.CHILD_BOUND:
                raise TeamRecordError("Team mailbox binding requires a Child-bound assignment")
            if (
                record.child_run_id != assignment.child_run_id
                or record.member_id != assignment.member_id
            ):
                raise TeamRecordError("Team mailbox binding provenance does not match assignment")
            if assignment.delivery_id is not None:
                raise TeamRecordError("Team assignment mailbox is already bound")
            if record.delivery_id in delivery_ids or record.reply_message_id in reply_ids:
                raise TeamRecordError("Team mailbox delivery or reply ID is duplicated")
            if len(record.inbox_message_ids) > 8 or len(set(record.inbox_message_ids)) != len(
                record.inbox_message_ids
            ):
                raise TeamRecordError("Team mailbox inbox IDs are invalid")
            previous_sequence = -1
            for message_id in record.inbox_message_ids:
                message = messages.get(message_id)
                if (
                    message is None
                    or message.sender_member_id is not None
                    or message.recipient_member_id != record.member_id
                ):
                    raise TeamRecordError("Team mailbox inbox message is invalid")
                if message.status is not TeamMessageStatus.PENDING:
                    raise TeamRecordError("Team mailbox inbox message is not pending")
                if message_id in bound_message_ids:
                    raise TeamRecordError("Team mailbox message is already bound")
                message_sequence = next(
                    item.sequence
                    for item in records
                    if isinstance(item, TeamMessageSent) and item.message_id == message_id
                )
                if message_sequence <= previous_sequence:
                    raise TeamRecordError("Team mailbox inbox order is invalid")
                previous_sequence = message_sequence
            delivery_ids.add(record.delivery_id)
            reply_ids.add(record.reply_message_id)
            bound_message_ids.update(record.inbox_message_ids)
            assignments[record.assignment_id] = TeamAssignmentState(
                **{
                    **assignment.__dict__,
                    "delivery_id": record.delivery_id,
                    "inbox_message_ids": record.inbox_message_ids,
                    "reply_message_id": record.reply_message_id,
                    "mailbox_bound_at": record.bound_at,
                }
            )
            continue
        if isinstance(record, TeamAssignmentMailboxObserved):
            assignment = assignments.get(record.assignment_id)
            if assignment is None or assignment.delivery_id != record.delivery_id:
                raise TeamRecordError("Team mailbox observation is invalid")
            if assignment.mailbox_observed_at is not None:
                raise TeamRecordError("Team assignment mailbox is already observed")
            if (
                assignment.child_session_id is not None
                and assignment.child_session_id != record.child_session_id
            ):
                raise TeamRecordError("Team mailbox Child Session does not match")
            if (
                type(record.child_turn_record_sequence) is not int
                or record.child_turn_record_sequence < 1
            ):
                raise TeamRecordError("Team mailbox Child Turn sequence is invalid")
            if (
                not isinstance(record.child_user_message_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", record.child_user_message_sha256) is None
            ):
                raise TeamRecordError("Team mailbox user-message digest is invalid")
            for message_id in assignment.inbox_message_ids:
                message = messages[message_id]
                if message.status is not TeamMessageStatus.PENDING:
                    raise TeamRecordError("Team mailbox message is not pending at delivery")
                messages[message_id] = TeamMessageState(
                    **{**message.__dict__, "status": TeamMessageStatus.DELIVERED}
                )
            assignments[record.assignment_id] = TeamAssignmentState(
                **{
                    **assignment.__dict__,
                    "mailbox_observed_at": record.observed_at,
                    "child_session_id": record.child_session_id,
                    "child_user_message_sha256": record.child_user_message_sha256,
                }
            )
            continue
        if isinstance(record, TeamMessageSent):
            if record.message_id in messages:
                raise TeamRecordError("Team message ID is duplicated")
            recipient = (
                members.get(record.recipient_member_id)
                if record.recipient_member_id is not None
                else None
            )
            sender = (
                members.get(record.sender_member_id)
                if record.sender_member_id is not None
                else None
            )
            if record.sender_member_id is None:
                if record.recipient_member_id is None or recipient is None:
                    raise TeamRecordError("Owner message recipient is invalid")
                if recipient.status is TeamMemberStatus.LEFT:
                    raise TeamRecordError("Owner message targets a left member")
                if any(
                    value is not None
                    for value in (
                        record.source_assignment_id,
                        record.source_child_session_id,
                        record.source_turn_record_sequence,
                        record.source_handoff_sha256,
                    )
                ):
                    raise TeamRecordError("Owner message has member provenance")
                status = TeamMessageStatus.PENDING
            else:
                if sender is None or record.recipient_member_id is not None:
                    raise TeamRecordError("Member message endpoints are invalid")
                assignment = (
                    assignments.get(record.source_assignment_id)
                    if record.source_assignment_id is not None
                    else None
                )
                if (
                    assignment is None
                    or assignment.member_id != record.sender_member_id
                    or record.source_child_session_id is None
                    or record.source_turn_record_sequence is None
                    or record.source_handoff_sha256 is None
                ):
                    raise TeamRecordError("Member message provenance is invalid")
                status = TeamMessageStatus.UNREAD
            messages[record.message_id] = TeamMessageState(
                message_id=record.message_id,
                sender_member_id=record.sender_member_id,
                recipient_member_id=record.recipient_member_id,
                body=record.body,
                body_sha256=record.body_sha256,
                source_assignment_id=record.source_assignment_id,
                source_child_session_id=record.source_child_session_id,
                source_turn_record_sequence=record.source_turn_record_sequence,
                source_handoff_sha256=record.source_handoff_sha256,
                sent_at=record.sent_at,
                status=status,
            )
            continue
        if isinstance(record, TeamMessageRead):
            message = messages.get(record.message_id)
            if message is None or message.sender_member_id is None:
                raise TeamRecordError("Only member messages can be marked read")
            if message.status is not TeamMessageStatus.UNREAD:
                raise TeamRecordError("Team message is not unread")
            messages[record.message_id] = TeamMessageState(
                **{**message.__dict__, "status": TeamMessageStatus.READ, "read_at": record.read_at}
            )
            continue
        if isinstance(record, TeamMessageCancelled):
            message = messages.get(record.message_id)
            if message is None or message.sender_member_id is not None:
                raise TeamRecordError("Only owner messages can be cancelled")
            if message.status is not TeamMessageStatus.PENDING:
                raise TeamRecordError("Team message is not pending")
            messages[record.message_id] = TeamMessageState(
                **{
                    **message.__dict__,
                    "status": TeamMessageStatus.CANCELLED,
                    "cancelled_at": record.cancelled_at,
                }
            )
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
        messages=tuple(messages.values()),
        work_items=tuple(_project_work_items(work_items)),
        schedules=tuple(schedules.values()),
    )


def _project_work_items(items: dict[str, TeamWorkItemState]) -> tuple[TeamWorkItemState, ...]:
    projected: list[TeamWorkItemState] = []
    for item in items.values():
        if item.status in {
            TeamWorkStatus.COMPLETED,
            TeamWorkStatus.CANCELLED,
            TeamWorkStatus.ASSIGNED,
            TeamWorkStatus.REVIEW,
        }:
            projected.append(item)
            continue
        blocked = tuple(
            dependency_id
            for dependency_id in item.dependency_ids
            if items[dependency_id].status is not TeamWorkStatus.COMPLETED
        )
        projected.append(
            TeamWorkItemState(
                **{
                    **item.__dict__,
                    "status": TeamWorkStatus.BLOCKED if blocked else TeamWorkStatus.READY,
                    "blocked_dependency_ids": blocked,
                }
            )
        )
    return tuple(projected)


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
    if isinstance(record, TeamScheduleStarted):
        if record.record_type != "team_schedule_started" or record.schema_version not in {
            TEAM_SCHEDULE_STARTED_SCHEMA_VERSION,
            TEAM_SCHEDULE_STARTED_V2_SCHEMA_VERSION,
        }:
            raise TeamRecordError("unsupported Team schedule-started schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team schedule-started sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.schedule_run_id)
        if record.source not in {TeamScheduleSource.HOST.value, TeamScheduleSource.MODEL.value}:
            raise TeamRecordError("Team schedule source is invalid")
        if (
            type(record.max_assignments) is not int
            or not 1 <= record.max_assignments <= MAX_TEAM_SCHEDULE_ASSIGNMENTS
        ):
            raise TeamRecordError("Team schedule assignment limit is invalid")
        if (
            type(record.max_parallel) is not int
            or not 1 <= record.max_parallel <= MAX_TEAM_SCHEDULE_PARALLEL
        ):
            raise TeamRecordError("Team schedule parallel limit is invalid")
        if record.schema_version == TEAM_SCHEDULE_STARTED_SCHEMA_VERSION:
            if record.capability_snapshot_sha256 is not None or record.eligible_members:
                raise TeamRecordError("legacy Team schedule cannot carry capability snapshot")
        else:
            if (
                not isinstance(record.capability_snapshot_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", record.capability_snapshot_sha256) is None
                or not isinstance(record.eligible_members, tuple)
                or len(record.eligible_members) > MAX_TEAM_MEMBERS
                or any(not isinstance(item, dict) for item in record.eligible_members)
                or record.parent_permission_mode
                not in {"read-only", "workspace-write", "danger-full-access"}
            ):
                raise TeamRecordError("Team schedule capability snapshot is invalid")
        canonical_team_timestamp(record.started_at, "Team schedule started_at")
        return
    if isinstance(record, TeamScheduleCancelRequested):
        if (
            record.record_type != "team_schedule_cancel_requested"
            or record.schema_version != TEAM_SCHEDULE_CANCEL_REQUESTED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team schedule-cancel schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team schedule-cancel sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.schedule_run_id)
        if record.source not in {item.value for item in TeamScheduleSource}:
            raise TeamRecordError("Team schedule cancellation source is invalid")
        canonical_team_schedule_reason(record.reason)
        canonical_team_timestamp(record.requested_at, "Team schedule requested_at")
        return
    if isinstance(record, TeamScheduleFinished):
        if (
            record.record_type != "team_schedule_finished"
            or record.schema_version != TEAM_SCHEDULE_FINISHED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team schedule-finished schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team schedule-finished sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.schedule_run_id)
        if record.outcome not in {item.value for item in TeamScheduleOutcome}:
            raise TeamRecordError("Team schedule outcome is invalid")
        if (
            type(record.assignment_count) is not int
            or not 0 <= record.assignment_count <= MAX_TEAM_SCHEDULE_ASSIGNMENTS
        ):
            raise TeamRecordError("Team schedule assignment count is invalid")
        canonical_team_reason(record.result_code)
        canonical_team_schedule_message(record.message)
        canonical_team_timestamp(record.finished_at, "Team schedule finished_at")
        return
    if isinstance(record, TeamMemberJoined):
        if record.record_type != "team_member_joined" or record.schema_version not in {
            TEAM_MEMBER_JOINED_SCHEMA_VERSION,
            TEAM_MEMBER_JOINED_V2_SCHEMA_VERSION,
        }:
            raise TeamRecordError("unsupported Team member-joined schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team member-joined sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.member_id)
        canonical_team_name(record.name)
        if record.role_contract not in {
            "read-only-investigator-v1",
            "isolated-workspace-writer-v1",
            "isolated-coder-v1",
        }:
            raise TeamRecordError("Team member role contract is invalid")
        if (
            record.schema_version == TEAM_MEMBER_JOINED_SCHEMA_VERSION
            and record.role_contract != TEAM_MEMBER_ROLE_CONTRACT
        ):
            raise TeamRecordError("legacy Team member cannot carry a writable role")
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
        if record.record_type != "team_assignment_created" or record.schema_version not in {
            TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION,
            TEAM_ASSIGNMENT_CREATED_V2_SCHEMA_VERSION,
            TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION,
            TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION,
        }:
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
        if (
            record.schema_version == TEAM_ASSIGNMENT_CREATED_SCHEMA_VERSION
            and record.work_item_id is not None
        ):
            raise TeamRecordError("legacy Team assignment cannot carry a work item")
        if (
            record.schema_version
            not in {
                TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION,
                TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION,
            }
            and record.schedule_run_id is not None
        ):
            raise TeamRecordError("non-v3 Team assignment cannot carry a schedule run")
        if record.work_item_id is not None:
            canonical_team_id(record.work_item_id)
        if record.schedule_run_id is not None:
            canonical_team_id(record.schedule_run_id)
        if record.schema_version != TEAM_ASSIGNMENT_CREATED_V4_SCHEMA_VERSION:
            if record.member_role_contract != TEAM_MEMBER_ROLE_CONTRACT or any(
                value is not None
                for value in (record.worktree_id, record.base_commit, record.target_ref)
            ):
                raise TeamRecordError("legacy Team assignment cannot carry writable provenance")
        else:
            if record.member_role_contract not in {
                "read-only-investigator-v1",
                "isolated-workspace-writer-v1",
                "isolated-coder-v1",
            }:
                raise TeamRecordError("Team assignment role contract is invalid")
            writable = record.member_role_contract != TEAM_MEMBER_ROLE_CONTRACT
            if writable != all(
                value is not None
                for value in (record.worktree_id, record.base_commit, record.target_ref)
            ):
                raise TeamRecordError("Team assignment worktree provenance is incomplete")
            if record.worktree_id is not None:
                canonical_team_id(record.worktree_id)
            for value, label in (
                (record.base_commit, "Team assignment base commit"),
                (record.target_ref, "Team assignment target ref"),
            ):
                if value is not None:
                    _bounded_text(value, label, max_characters=256, max_bytes=1024)
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
    if isinstance(record, TeamAssignmentMailboxBound):
        if (
            record.record_type != "team_assignment_mailbox_bound"
            or record.schema_version != TEAM_ASSIGNMENT_MAILBOX_BOUND_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team mailbox-bound schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team mailbox-bound sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.assignment_id)
        canonical_team_id(record.child_run_id)
        canonical_team_id(record.member_id)
        canonical_team_id(record.delivery_id)
        canonical_team_id(record.reply_message_id)
        if (
            not isinstance(record.inbox_message_ids, tuple)
            or len(record.inbox_message_ids) > 8
            or len(set(record.inbox_message_ids)) != len(record.inbox_message_ids)
        ):
            raise TeamRecordError("Team mailbox inbox IDs are invalid")
        for message_id in record.inbox_message_ids:
            canonical_team_id(message_id)
        canonical_team_timestamp(record.bound_at, "Team mailbox bound_at")
        return
    if isinstance(record, TeamAssignmentMailboxObserved):
        if (
            record.record_type != "team_assignment_mailbox_observed"
            or record.schema_version != TEAM_ASSIGNMENT_MAILBOX_OBSERVED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team mailbox-observed schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team mailbox-observed sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.assignment_id)
        canonical_team_id(record.delivery_id)
        canonical_team_id(record.child_session_id)
        if (
            type(record.child_turn_record_sequence) is not int
            or record.child_turn_record_sequence < 1
        ):
            raise TeamRecordError("Team mailbox Child Turn sequence is invalid")
        if (
            not isinstance(record.child_user_message_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", record.child_user_message_sha256) is None
        ):
            raise TeamRecordError("Team mailbox user-message digest is invalid")
        canonical_team_timestamp(record.observed_at, "Team mailbox observed_at")
        return
    if isinstance(record, TeamWorkItemCreated):
        if (
            record.record_type != "team_work_item_created"
            or record.schema_version != TEAM_WORK_ITEM_CREATED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team work-created schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team work-created sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.work_item_id)
        canonical_team_name(record.title)
        canonical_team_assignment_objective(record.objective)
        if (
            not isinstance(record.dependency_ids, tuple)
            or len(record.dependency_ids) > MAX_TEAM_WORK_DEPENDENCIES
            or len(set(record.dependency_ids)) != len(record.dependency_ids)
        ):
            raise TeamRecordError("Team work dependencies are invalid")
        for dependency_id in record.dependency_ids:
            canonical_team_id(dependency_id)
        canonical_team_timestamp(record.created_at, "Team work created_at")
        return
    if isinstance(record, TeamWorkItemReleased):
        if (
            record.record_type != "team_work_item_released"
            or record.schema_version != TEAM_WORK_ITEM_RELEASED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team work-released schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team work-released sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.work_item_id)
        canonical_team_id(record.assignment_id)
        canonical_team_reason(record.reason)
        canonical_team_timestamp(record.released_at, "Team work released_at")
        return
    if isinstance(record, TeamWorkItemCompleted):
        if (
            record.record_type != "team_work_item_completed"
            or record.schema_version != TEAM_WORK_ITEM_COMPLETED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team work-completed schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team work-completed sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.work_item_id)
        canonical_team_id(record.assignment_id)
        if (
            not isinstance(record.handoff_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", record.handoff_sha256) is None
        ):
            raise TeamRecordError("Team work handoff digest is invalid")
        canonical_team_reason(record.evidence)
        canonical_team_timestamp(record.completed_at, "Team work completed_at")
        return
    if isinstance(record, TeamWorkItemCancelled):
        if (
            record.record_type != "team_work_item_cancelled"
            or record.schema_version != TEAM_WORK_ITEM_CANCELLED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team work-cancelled schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team work-cancelled sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.work_item_id)
        canonical_team_reason(record.reason)
        canonical_team_timestamp(record.cancelled_at, "Team work cancelled_at")
        return
    if isinstance(record, TeamMessageSent):
        if (
            record.record_type != "team_message_sent"
            or record.schema_version != TEAM_MESSAGE_SENT_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team message-sent schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team message-sent sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.message_id)
        if (record.sender_member_id is None) == (record.recipient_member_id is None):
            raise TeamRecordError("Team message must have exactly one member endpoint")
        if record.sender_member_id is not None:
            canonical_team_id(record.sender_member_id)
        if record.recipient_member_id is not None:
            canonical_team_id(record.recipient_member_id)
        body = _canonical_message_body(record.body)
        if record.sender_member_id is None and (
            len(body) > MAX_TEAM_OWNER_MESSAGE_CHARACTERS
            or len(body.encode("utf-8")) > MAX_TEAM_OWNER_MESSAGE_BYTES
        ):
            raise TeamRecordError("Owner Team message exceeds its bound")
        if record.body_sha256 != team_message_body_sha256(body):
            raise TeamRecordError("Team message body digest does not match body")
        source_values = (
            record.source_assignment_id,
            record.source_child_session_id,
            record.source_turn_record_sequence,
            record.source_handoff_sha256,
        )
        if record.sender_member_id is None:
            if any(value is not None for value in source_values):
                raise TeamRecordError("Owner Team message has source provenance")
        else:
            if any(value is None for value in source_values):
                raise TeamRecordError("Member Team message source provenance is incomplete")
            canonical_team_id(record.source_assignment_id)
            canonical_team_id(record.source_child_session_id)
            if (
                type(record.source_turn_record_sequence) is not int
                or record.source_turn_record_sequence < 1
            ):
                raise TeamRecordError("Member message source Turn sequence is invalid")
            if (
                not isinstance(record.source_handoff_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", record.source_handoff_sha256) is None
            ):
                raise TeamRecordError("Member message source handoff digest is invalid")
        canonical_team_timestamp(record.sent_at, "Team message sent_at")
        return
    if isinstance(record, TeamMessageRead):
        if (
            record.record_type != "team_message_read"
            or record.schema_version != TEAM_MESSAGE_READ_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team message-read schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team message-read sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.message_id)
        canonical_team_timestamp(record.read_at, "Team message read_at")
        return
    if isinstance(record, TeamMessageCancelled):
        if (
            record.record_type != "team_message_cancelled"
            or record.schema_version != TEAM_MESSAGE_CANCELLED_SCHEMA_VERSION
        ):
            raise TeamRecordError("unsupported Team message-cancelled schema")
        if type(record.sequence) is not int or record.sequence < 1:
            raise TeamRecordError("Team message-cancelled sequence must be positive")
        canonical_team_id(record.team_id)
        canonical_team_id(record.message_id)
        canonical_team_reason(record.reason)
        canonical_team_timestamp(record.cancelled_at, "Team message cancelled_at")
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


def _canonical_message_body(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TeamRecordError("Team message body must be nonblank text")
    if any(
        unicodedata.category(char).startswith("C") and char not in {"\n", "\t"} for char in value
    ):
        raise TeamRecordError("Team message body must not contain control characters")
    if (
        len(value) > MAX_TEAM_MESSAGE_BODY_CHARACTERS
        or len(value.encode("utf-8")) > MAX_TEAM_MESSAGE_BODY_BYTES
    ):
        raise TeamRecordError("Team message body exceeds its bound")
    return value


def canonical_team_reason(value: object) -> str:
    return _bounded_text(
        value,
        "Team member reason",
        max_characters=MAX_TEAM_REASON_CHARACTERS,
        max_bytes=MAX_TEAM_REASON_BYTES,
    )


def canonical_team_schedule_reason(value: object) -> str:
    return _bounded_text(
        value,
        "Team schedule reason",
        max_characters=MAX_TEAM_SCHEDULE_REASON_CHARACTERS,
        max_bytes=MAX_TEAM_SCHEDULE_REASON_BYTES,
    )


def canonical_team_schedule_message(value: object) -> str:
    if not isinstance(value, str):
        raise TeamRecordError("Team schedule message must be text")
    if any(ord(char) < 32 and char not in {"\n", "\t"} for char in value):
        raise TeamRecordError("Team schedule message must not contain control characters")
    if (
        len(value) > MAX_TEAM_SCHEDULE_REASON_CHARACTERS
        or len(value.encode("utf-8")) > MAX_TEAM_SCHEDULE_REASON_BYTES
    ):
        raise TeamRecordError("Team schedule message exceeds its bound")
    return value


def _record_timestamp(record: TeamRecord) -> str:
    if isinstance(record, TeamClosed):
        return record.closed_at
    if isinstance(record, TeamScheduleStarted):
        return record.started_at
    if isinstance(record, TeamScheduleCancelRequested):
        return record.requested_at
    if isinstance(record, TeamScheduleFinished):
        return record.finished_at
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
    if isinstance(record, TeamAssignmentMailboxBound):
        return record.bound_at
    if isinstance(record, TeamAssignmentMailboxObserved):
        return record.observed_at
    if isinstance(record, TeamWorkItemCreated):
        return record.created_at
    if isinstance(record, TeamWorkItemReleased):
        return record.released_at
    if isinstance(record, TeamWorkItemCompleted):
        return record.completed_at
    if isinstance(record, TeamWorkItemCancelled):
        return record.cancelled_at
    if isinstance(record, TeamMessageSent):
        return record.sent_at
    if isinstance(record, TeamMessageRead):
        return record.read_at
    if isinstance(record, TeamMessageCancelled):
        return record.cancelled_at
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
