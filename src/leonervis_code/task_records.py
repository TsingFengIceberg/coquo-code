"""Closed versioned records for durable Leonervis Code tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path, PureWindowsPath
import re
from typing import TypeAlias
from uuid import UUID

from leonervis_code.core.hook_contracts import (
    HookAuditLedger,
    HookEvent,
    hook_audit_ledger_from_mapping,
    hook_audit_ledger_to_mapping,
)
from leonervis_code.core.task_admission import canonical_task_admission_id
from leonervis_code.session_records import (
    SessionRecordError,
    canonical_session_id,
    workspace_fingerprint,
)

TASK_HEADER_SCHEMA_VERSION = 1
TASK_CONFIGURATION_SCHEMA_VERSION = 1
TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION = 1
STAGE_STARTED_SCHEMA_VERSION = 3
STAGE_COMMITTED_SCHEMA_VERSION = 3
STAGE_FAILED_SCHEMA_VERSION = 3
TASK_PLAN_PROPOSED_SCHEMA_VERSION = 3
TASK_PLAN_ACCEPTED_SCHEMA_VERSION = 1
TASK_COMPLETION_PROPOSED_SCHEMA_VERSION = 2
TASK_ACCEPTANCE_VERIFIED_SCHEMA_VERSION = 1
TASK_ACCEPTANCE_CHECKED_SCHEMA_VERSION = 1
TASK_TERMINATED_SCHEMA_VERSION = 2
TASK_RENAMED_SCHEMA_VERSION = 1
TASK_ARCHIVED_SCHEMA_VERSION = 1
TASK_REFLECTION_RECORDED_SCHEMA_VERSION = 2
TASK_BLOCKER_RECORDED_SCHEMA_VERSION = 2
TASK_PAUSE_CHANGED_SCHEMA_VERSION = 1
TASK_CONTEXT_CHECKPOINT_SCHEMA_VERSION = 1
TASK_ADMISSION_ORIGIN_SCHEMA_VERSION = 1
MAX_TASK_RECORD_BYTES = 64 * 1024
MAX_TASK_RECORDS = 10_000
MAX_TASK_OBJECTIVE_CHARACTERS = 4096
MAX_TASK_OBJECTIVE_BYTES = 16 * 1024
MAX_ACCEPTANCE_CRITERIA = 16
MAX_ACCEPTANCE_CRITERION_CHARACTERS = 1024
MAX_ACCEPTANCE_CRITERION_BYTES = 4096
MAX_TASK_TEXT_BYTES = 32 * 1024
MAX_ACCEPTANCE_COMMAND_ARGUMENTS = 64
MAX_ACCEPTANCE_REVIEW_PATHS = 32
MAX_TASK_NAME_CHARACTERS = 80
MAX_TASK_NAME_BYTES = 256
MAX_PLAN_STEPS = 32
MAX_TASK_STAGES = 256
MAX_TASK_PROVIDER_INVOCATIONS = 100_000
MAX_TASK_TOOL_REQUESTS = 100_000
MAX_TASK_TOKEN_BUDGET = 1_000_000_000
DEFAULT_TASK_MAX_STAGES = 32
DEFAULT_TASK_MAX_PROVIDER_INVOCATIONS = 32 * 24
DEFAULT_TASK_MAX_TOOL_REQUESTS = 32 * 32

_WORKSPACE_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")
_CANONICAL_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")


class TaskRecordError(ValueError):
    """Raised when a Task record or replay chain is invalid."""


class TaskScope(StrEnum):
    WORKSPACE = "workspace"


class TaskStatus(StrEnum):
    READY = "ready"
    STAGE_IN_PROGRESS = "stage-in-progress"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETION_PROPOSED = "completion-proposed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageFailureReason(StrEnum):
    CANCELLED = "cancelled"
    PROVIDER_ERROR = "provider-error"
    TURN_NOT_COMMITTED = "turn-not-committed"
    HOST_ERROR = "host-error"
    INTERRUPTED = "interrupted"


class StageKind(StrEnum):
    EXECUTION = "execution"
    PLANNING = "planning"
    REFLECTION = "reflection"
    CORRECTION = "correction"


class ReflectionRecommendation(StrEnum):
    CONTINUE = "continue"
    CORRECTION = "correction"
    REVISE_PLAN = "revise-plan"
    NEEDS_HUMAN = "needs-human"
    FAIL = "fail"


class TaskBlockerCategory(StrEnum):
    INFORMATION = "information"
    PERMISSION = "permission"
    HUMAN_EVIDENCE = "human-evidence"
    EXTERNAL_CONDITION = "external-condition"
    OTHER = "other"


class TaskTerminalOutcome(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CompletionProposalSource(StrEnum):
    MODEL = "model"


class AcceptanceVerificationSource(StrEnum):
    USER = "user"
    HOST_CHECK = "host-check"
    INDEPENDENT_REVIEWER = "independent-reviewer"


class AcceptanceCriterionKind(StrEnum):
    HUMAN = "human"
    PATH_EXISTS = "path-exists"
    PATH_UNCHANGED = "path-unchanged"
    COMMAND_SUCCEEDS = "command-succeeds"
    ACTION_AUDIT_CERTAIN = "action-audit-certain"
    INDEPENDENT_REVIEWER = "independent-reviewer"


class AcceptancePathType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class TaskCompletionPolicy(StrEnum):
    MANUAL = "manual"
    AUTO_VERIFIED = "auto-verified"


class AcceptanceCheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_HUMAN = "needs-human"
    ERROR = "error"


@dataclass(frozen=True)
class TaskAcceptanceCriterion:
    kind: AcceptanceCriterionKind
    description: str
    path: str | None = None
    path_type: AcceptancePathType | None = None
    expected_sha256: str | None = None
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    timeout_seconds: int | None = None
    review_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskBudget:
    max_stages: int = DEFAULT_TASK_MAX_STAGES
    max_provider_invocations: int = DEFAULT_TASK_MAX_PROVIDER_INVOCATIONS
    max_tool_requests: int = DEFAULT_TASK_MAX_TOOL_REQUESTS
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class StageUsage:
    provider_invocations: int
    input_tokens: int
    output_tokens: int
    known_token_invocations: int
    unknown_token_invocations: int
    tool_requests: int
    tool_admitted: int
    tool_dispatched: int
    tool_succeeded: int
    tool_unsuccessful: int


@dataclass(frozen=True)
class TaskHeader:
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
class TaskConfiguration:
    sequence: int
    name: str
    budget: TaskBudget
    configured_at: str
    parent_task_id: str | None = None
    record_type: str = "task_configuration"
    schema_version: int = TASK_CONFIGURATION_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskAcceptanceContract:
    sequence: int
    criteria: tuple[TaskAcceptanceCriterion, ...]
    completion_policy: TaskCompletionPolicy
    configured_at: str
    record_type: str = "task_acceptance_contract"
    schema_version: int = TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskAdmissionOrigin:
    sequence: int
    admission_id: str
    proposal_sha256: str
    configuration_sha256: str
    confirmation_sha256: str
    source_session_id: str
    source_turn_record_sequence: int
    proposal_tool_use_id: str
    source_context_id: str
    recorded_at: str
    record_type: str = "task_admission_origin"
    schema_version: int = TASK_ADMISSION_ORIGIN_SCHEMA_VERSION


@dataclass(frozen=True)
class StageStarted:
    sequence: int
    stage_id: str
    stage_number: int
    session_id: str
    objective: str
    started_at: str
    kind: StageKind = StageKind.EXECUTION
    session_record_sequence_before: int | None = None
    session_turn_count_before: int | None = None
    prompt_sha256: str | None = None
    hook_audit: HookAuditLedger = HookAuditLedger()
    record_type: str = "stage_started"
    schema_version: int = STAGE_STARTED_SCHEMA_VERSION


@dataclass(frozen=True)
class StageCommitted:
    sequence: int
    stage_id: str
    stage_number: int
    session_id: str
    turn_number: int
    turn_record_sequence: int
    turn_record_sha256: str
    committed_at: str
    usage: StageUsage | None = None
    hook_audit: HookAuditLedger = HookAuditLedger()
    record_type: str = "stage_committed"
    schema_version: int = STAGE_COMMITTED_SCHEMA_VERSION


@dataclass(frozen=True)
class StageFailed:
    sequence: int
    stage_id: str
    stage_number: int
    reason: StageFailureReason
    failed_at: str
    usage: StageUsage | None = None
    hook_audit: HookAuditLedger = HookAuditLedger()
    record_type: str = "stage_failed"
    schema_version: int = STAGE_FAILED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskPlanProposed:
    sequence: int
    plan_id: str
    stage_id: str
    stage_number: int
    steps: tuple[str, ...]
    proposed_at: str
    predecessor_plan_id: str | None = None
    revision_reason: str | None = None
    reflection_id: str | None = None
    proposal_tool_use_id: str | None = None
    record_type: str = "task_plan_proposed"
    schema_version: int = TASK_PLAN_PROPOSED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskPlanAccepted:
    sequence: int
    plan_id: str
    accepted_at: str
    record_type: str = "task_plan_accepted"
    schema_version: int = TASK_PLAN_ACCEPTED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskCompletionProposed:
    sequence: int
    stage_id: str
    stage_number: int
    source: CompletionProposalSource
    proposed_at: str
    proposal_tool_use_id: str | None = None
    record_type: str = "task_completion_proposed"
    schema_version: int = TASK_COMPLETION_PROPOSED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskAcceptanceVerified:
    sequence: int
    completion_stage_id: str
    criterion_index: int
    evidence: str
    source: AcceptanceVerificationSource
    verified_at: str
    record_type: str = "task_acceptance_verified"
    schema_version: int = TASK_ACCEPTANCE_VERIFIED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskAcceptanceChecked:
    sequence: int
    completion_stage_id: str
    criterion_index: int
    source: AcceptanceVerificationSource
    outcome: AcceptanceCheckOutcome
    evidence: str
    checked_at: str
    record_type: str = "task_acceptance_checked"
    schema_version: int = TASK_ACCEPTANCE_CHECKED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskTerminated:
    sequence: int
    outcome: TaskTerminalOutcome
    reason: str | None
    terminated_at: str
    hook_audit: HookAuditLedger = HookAuditLedger()
    record_type: str = "task_terminated"
    schema_version: int = TASK_TERMINATED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskRenamed:
    sequence: int
    name: str
    renamed_at: str
    record_type: str = "task_renamed"
    schema_version: int = TASK_RENAMED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskArchived:
    sequence: int
    archived: bool
    changed_at: str
    record_type: str = "task_archived"
    schema_version: int = TASK_ARCHIVED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskReflectionRecorded:
    sequence: int
    reflection_id: str
    stage_id: str
    stage_number: int
    recommendation: ReflectionRecommendation
    summary: str
    next_objective: str | None
    recorded_at: str
    proposal_tool_use_id: str | None = None
    record_type: str = "task_reflection_recorded"
    schema_version: int = TASK_REFLECTION_RECORDED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskBlockerRecorded:
    sequence: int
    stage_id: str
    stage_number: int
    category: TaskBlockerCategory
    summary: str
    proposal_tool_use_id: str
    recorded_at: str
    hook_audit: HookAuditLedger = HookAuditLedger()
    record_type: str = "task_blocker_recorded"
    schema_version: int = TASK_BLOCKER_RECORDED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskPauseChanged:
    sequence: int
    paused: bool
    reason: str | None
    changed_at: str
    record_type: str = "task_pause_changed"
    schema_version: int = TASK_PAUSE_CHANGED_SCHEMA_VERSION


@dataclass(frozen=True)
class TaskContextCheckpoint:
    sequence: int
    checkpoint_id: str
    source_sequence: int
    prior_checkpoint_id: str | None
    accepted_plan_id: str | None
    completed_plan_steps: int
    completion_stage_id: str | None
    unresolved_criterion_indices: tuple[int, ...]
    latest_reflection_id: str | None
    created_at: str
    record_type: str = "task_context_checkpoint"
    schema_version: int = TASK_CONTEXT_CHECKPOINT_SCHEMA_VERSION


StageTerminal: TypeAlias = StageCommitted | StageFailed
TaskRecord: TypeAlias = (
    TaskHeader
    | TaskConfiguration
    | TaskAcceptanceContract
    | TaskAdmissionOrigin
    | StageStarted
    | StageCommitted
    | StageFailed
    | TaskPlanProposed
    | TaskPlanAccepted
    | TaskCompletionProposed
    | TaskAcceptanceVerified
    | TaskAcceptanceChecked
    | TaskTerminated
    | TaskRenamed
    | TaskArchived
    | TaskReflectionRecorded
    | TaskBlockerRecorded
    | TaskPauseChanged
    | TaskContextCheckpoint
)


@dataclass(frozen=True)
class TaskStageState:
    started: StageStarted
    terminal: StageTerminal | None


@dataclass(frozen=True)
class TaskReplayState:
    header: TaskHeader
    records: tuple[TaskRecord, ...]
    stages: tuple[TaskStageState, ...] = ()
    configuration: TaskConfiguration | None = None
    acceptance_contract: TaskAcceptanceContract | None = None
    admission_origin: TaskAdmissionOrigin | None = None
    plan_proposals: tuple[TaskPlanProposed, ...] = ()
    accepted_plan_id: str | None = None
    accepted_plan_sequence: int | None = None
    completion_proposals: tuple[TaskCompletionProposed, ...] = ()
    acceptance_verifications: tuple[TaskAcceptanceVerified, ...] = ()
    acceptance_checks: tuple[TaskAcceptanceChecked, ...] = ()
    terminal: TaskTerminated | None = None
    renamed: tuple[TaskRenamed, ...] = ()
    archived_events: tuple[TaskArchived, ...] = ()
    reflections: tuple[TaskReflectionRecorded, ...] = ()
    blockers: tuple[TaskBlockerRecorded, ...] = ()
    pause_events: tuple[TaskPauseChanged, ...] = ()
    context_checkpoints: tuple[TaskContextCheckpoint, ...] = ()

    @property
    def task_id(self) -> str:
        return self.header.task_id

    @property
    def name(self) -> str:
        if self.renamed:
            return self.renamed[-1].name
        if self.configuration is not None:
            return self.configuration.name
        return default_task_name(self.header.objective)

    @property
    def budget(self) -> TaskBudget:
        return self.configuration.budget if self.configuration is not None else TaskBudget()

    @property
    def criteria(self) -> tuple[TaskAcceptanceCriterion, ...]:
        if self.acceptance_contract is not None:
            return self.acceptance_contract.criteria
        return tuple(
            TaskAcceptanceCriterion(AcceptanceCriterionKind.HUMAN, description)
            for description in self.header.acceptance_criteria
        )

    @property
    def completion_policy(self) -> TaskCompletionPolicy:
        if self.acceptance_contract is None:
            return TaskCompletionPolicy.MANUAL
        return self.acceptance_contract.completion_policy

    @property
    def parent_task_id(self) -> str | None:
        return self.configuration.parent_task_id if self.configuration is not None else None

    @property
    def archived(self) -> bool:
        return self.archived_events[-1].archived if self.archived_events else False

    @property
    def driver_paused(self) -> bool:
        return self.pause_events[-1].paused if self.pause_events else False

    @property
    def latest_reflection(self) -> TaskReflectionRecorded | None:
        return self.reflections[-1] if self.reflections else None

    @property
    def latest_blocker(self) -> TaskBlockerRecorded | None:
        return self.blockers[-1] if self.blockers else None

    @property
    def latest_checkpoint(self) -> TaskContextCheckpoint | None:
        return self.context_checkpoints[-1] if self.context_checkpoints else None

    @property
    def status(self) -> TaskStatus:
        if self.terminal is not None:
            return TaskStatus(self.terminal.outcome.value)
        if not self.stages:
            return TaskStatus.READY
        terminal = self.stages[-1].terminal
        if terminal is None:
            return TaskStatus.INTERRUPTED
        if self.current_completion_proposal is not None:
            return TaskStatus.COMPLETION_PROPOSED
        if isinstance(terminal, StageCommitted):
            if (
                self.latest_blocker is not None
                and self.latest_blocker.stage_id == self.stages[-1].started.stage_id
            ):
                return TaskStatus.BLOCKED
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

    @property
    def latest_plan(self) -> TaskPlanProposed | None:
        return self.plan_proposals[-1] if self.plan_proposals else None

    @property
    def accepted_plan(self) -> TaskPlanProposed | None:
        latest = self.latest_plan
        if latest is not None and latest.plan_id == self.accepted_plan_id:
            return latest
        return None

    @property
    def current_completion_proposal(self) -> TaskCompletionProposed | None:
        if not self.completion_proposals or not self.stages:
            return None
        proposal = self.completion_proposals[-1]
        latest = self.stages[-1]
        if not isinstance(latest.terminal, StageCommitted):
            return None
        if proposal.stage_id != latest.started.stage_id:
            return None
        return proposal

    @property
    def verified_criteria(self) -> dict[int, TaskAcceptanceVerified]:
        proposal = self.current_completion_proposal
        if proposal is None:
            return {}
        return {
            record.criterion_index: record
            for record in self.acceptance_verifications
            if record.completion_stage_id == proposal.stage_id
        }


def canonical_task_id(value: object) -> str:
    return _canonical_uuid4(value, "task ID")


def canonical_stage_id(value: object) -> str:
    return _canonical_uuid4(value, "stage ID")


def canonical_plan_id(value: object) -> str:
    return _canonical_uuid4(value, "plan ID")


def canonical_reflection_id(value: object) -> str:
    return _canonical_uuid4(value, "reflection ID")


def canonical_checkpoint_id(value: object) -> str:
    return _canonical_uuid4(value, "Task checkpoint ID")


def canonical_task_proposal_tool_use_id(value: object) -> str:
    return _bounded_text(
        value,
        "Task proposal tool-use ID",
        max_characters=4096,
        max_bytes=4096,
    )


def canonical_task_objective(value: object) -> str:
    return _bounded_text(
        value,
        "task objective",
        max_characters=MAX_TASK_OBJECTIVE_CHARACTERS,
        max_bytes=MAX_TASK_OBJECTIVE_BYTES,
    )


def canonical_stage_objective(value: object) -> str:
    return _bounded_text(
        value,
        "stage objective",
        max_characters=MAX_TASK_OBJECTIVE_CHARACTERS,
        max_bytes=MAX_TASK_OBJECTIVE_BYTES,
    )


def canonical_task_name(value: object) -> str:
    return _bounded_text(
        value,
        "task name",
        max_characters=MAX_TASK_NAME_CHARACTERS,
        max_bytes=MAX_TASK_NAME_BYTES,
    )


def default_task_name(objective: str) -> str:
    canonical_task_objective(objective)
    candidate = " ".join(objective.split()) or "New task"
    candidate = candidate[:MAX_TASK_NAME_CHARACTERS]
    while len(candidate.encode("utf-8")) > MAX_TASK_NAME_BYTES:
        candidate = candidate[:-1]
    return candidate


def canonical_acceptance_criteria(values: object) -> tuple[str, ...]:
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
    if sum(len(value.encode("utf-8")) for value in criteria) > MAX_TASK_TEXT_BYTES:
        raise TaskRecordError(f"acceptance criteria exceed {MAX_TASK_TEXT_BYTES} total UTF-8 bytes")
    return criteria


def canonical_task_acceptance_contract(
    criteria: object,
    completion_policy: object,
) -> tuple[TaskAcceptanceCriterion, ...]:
    if not isinstance(criteria, (tuple, list)):
        raise TaskRecordError("structured acceptance criteria must be an array")
    if len(criteria) > MAX_ACCEPTANCE_CRITERIA:
        raise TaskRecordError(
            f"structured acceptance criteria exceed the {MAX_ACCEPTANCE_CRITERIA}-item limit"
        )
    if type(completion_policy) is not TaskCompletionPolicy:
        raise TaskRecordError("Task completion policy is invalid")
    canonical = tuple(canonical_acceptance_criterion(value) for value in criteria)
    descriptions = canonical_acceptance_criteria(
        tuple(criterion.description for criterion in canonical)
    )
    if len(descriptions) != len(canonical):
        raise TaskRecordError("structured acceptance criteria are invalid")
    return canonical


def canonical_acceptance_criterion(value: object) -> TaskAcceptanceCriterion:
    if not isinstance(value, TaskAcceptanceCriterion):
        raise TaskRecordError("structured acceptance criterion is invalid")
    if type(value.kind) is not AcceptanceCriterionKind:
        raise TaskRecordError("acceptance criterion kind is invalid")
    _bounded_text(
        value.description,
        "acceptance criterion description",
        max_characters=MAX_ACCEPTANCE_CRITERION_CHARACTERS,
        max_bytes=MAX_ACCEPTANCE_CRITERION_BYTES,
    )
    empty_command = not value.argv and value.cwd is None and value.timeout_seconds is None
    empty_path = value.path is None and value.path_type is None and value.expected_sha256 is None
    if value.kind is AcceptanceCriterionKind.HUMAN:
        if not empty_command or not empty_path or value.review_paths:
            raise TaskRecordError("human acceptance criterion contains unsupported fields")
    elif value.kind is AcceptanceCriterionKind.PATH_EXISTS:
        _criterion_path(value.path, "acceptance path")
        if type(value.path_type) is not AcceptancePathType:
            raise TaskRecordError("path-exists criterion requires a path type")
        if value.expected_sha256 is not None or not empty_command or value.review_paths:
            raise TaskRecordError("path-exists acceptance criterion contains unsupported fields")
    elif value.kind is AcceptanceCriterionKind.PATH_UNCHANGED:
        _criterion_path(value.path, "protected acceptance path")
        if value.path_type is not AcceptancePathType.FILE:
            raise TaskRecordError("path-unchanged criterion requires a regular file")
        _required_sha256(value.expected_sha256, "protected acceptance path SHA-256")
        if not empty_command or value.review_paths:
            raise TaskRecordError("path-unchanged acceptance criterion contains unsupported fields")
    elif value.kind is AcceptanceCriterionKind.COMMAND_SUCCEEDS:
        if not empty_path or value.review_paths:
            raise TaskRecordError("command acceptance criterion contains unsupported fields")
        _criterion_command(value.argv, value.cwd, value.timeout_seconds)
    elif value.kind is AcceptanceCriterionKind.ACTION_AUDIT_CERTAIN:
        if not empty_command or not empty_path or value.review_paths:
            raise TaskRecordError("Action Audit criterion contains unsupported fields")
    elif value.kind is AcceptanceCriterionKind.INDEPENDENT_REVIEWER:
        if not empty_command or not empty_path:
            raise TaskRecordError("reviewer acceptance criterion contains unsupported fields")
        if len(value.review_paths) > MAX_ACCEPTANCE_REVIEW_PATHS:
            raise TaskRecordError(
                f"reviewer acceptance paths exceed the {MAX_ACCEPTANCE_REVIEW_PATHS}-item limit"
            )
        for path in value.review_paths:
            _criterion_path(path, "reviewer acceptance path")
        if len(set(value.review_paths)) != len(value.review_paths):
            raise TaskRecordError("reviewer acceptance paths must not contain duplicates")
    return value


def _criterion_command(argv: object, cwd: object, timeout_seconds: object) -> None:
    if not isinstance(argv, tuple) or not 1 <= len(argv) <= MAX_ACCEPTANCE_COMMAND_ARGUMENTS:
        raise TaskRecordError(
            f"acceptance command argv must contain 1 to {MAX_ACCEPTANCE_COMMAND_ARGUMENTS} items"
        )
    total_bytes = 0
    for index, argument in enumerate(argv):
        if not isinstance(argument, str) or "\x00" in argument:
            raise TaskRecordError(f"acceptance command argv[{index}] is invalid")
        try:
            encoded = argument.encode("utf-8")
        except UnicodeEncodeError:
            raise TaskRecordError(f"acceptance command argv[{index}] is invalid") from None
        if len(encoded) > 1024:
            raise TaskRecordError(f"acceptance command argv[{index}] exceeds 1024 bytes")
        total_bytes += len(encoded)
    if not argv[0].strip() or total_bytes > 8192:
        raise TaskRecordError("acceptance command argv is invalid or oversized")
    if not isinstance(cwd, str):
        raise TaskRecordError("acceptance command cwd is invalid")
    _criterion_path(cwd, "acceptance command cwd", allow_root=True)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise TaskRecordError("acceptance command timeout must be from 1 to 300 seconds")


def _criterion_path(value: object, label: str, *, allow_root: bool = False) -> str:
    if not isinstance(value, str):
        raise TaskRecordError(f"{label} must be a portable workspace-relative path")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise TaskRecordError(f"{label} must be valid UTF-8") from None
    if allow_root and value == ".":
        return value
    parts = value.split("/")
    if (
        not value
        or value != value.strip()
        or "\x00" in value
        or "\\" in value
        or Path(value).is_absolute()
        or PureWindowsPath(value).drive
        or len(value) > 4096
        or len(encoded) > 4096
        or len(parts) > 64
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part.encode("utf-8")) > 255 for part in parts)
    ):
        raise TaskRecordError(f"{label} must be a portable workspace-relative path")
    return value


def canonical_plan_steps(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values:
        raise TaskRecordError("task plan steps must be a non-empty array")
    if len(values) > MAX_PLAN_STEPS:
        raise TaskRecordError(f"task plan exceeds the {MAX_PLAN_STEPS}-step limit")
    steps = tuple(canonical_stage_objective(value) for value in values)
    if len(set(steps)) != len(steps):
        raise TaskRecordError("task plan steps must not contain duplicates")
    if sum(len(value.encode("utf-8")) for value in steps) > MAX_TASK_TEXT_BYTES:
        raise TaskRecordError(f"task plan exceeds {MAX_TASK_TEXT_BYTES} total UTF-8 bytes")
    return steps


def canonical_task_budget(value: object) -> TaskBudget:
    if not isinstance(value, TaskBudget):
        raise TaskRecordError("task budget is invalid")
    _bounded_positive(value.max_stages, MAX_TASK_STAGES, "Task Stage budget")
    _bounded_positive(
        value.max_provider_invocations,
        MAX_TASK_PROVIDER_INVOCATIONS,
        "Task provider-invocation budget",
    )
    _bounded_positive(value.max_tool_requests, MAX_TASK_TOOL_REQUESTS, "Task tool-request budget")
    for number, label in (
        (value.max_input_tokens, "Task input-token budget"),
        (value.max_output_tokens, "Task output-token budget"),
    ):
        if number is not None:
            _bounded_positive(number, MAX_TASK_TOKEN_BUDGET, label)
    return value


def encode_task_record(record: TaskRecord) -> bytes:
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
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_TASK_RECORD_BYTES:
        raise TaskRecordError("task record payload is empty or oversized")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise TaskRecordError("task record is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise TaskRecordError("task record must be a JSON object")
    decoder = _DECODERS.get(value.get("record_type"))
    if decoder is None:
        raise TaskRecordError("unknown task record type")
    return decoder(value)


def replay_task_records(
    records: list[TaskRecord] | tuple[TaskRecord, ...],
    *,
    expected_workspace: str,
    expected_workspace_fingerprint: str,
    expected_task_id: str,
    expected_file_name: str,
) -> TaskReplayState:
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

    configuration: TaskConfiguration | None = None
    acceptance_contract: TaskAcceptanceContract | None = None
    admission_origin: TaskAdmissionOrigin | None = None
    stages: list[TaskStageState] = []
    active: StageStarted | None = None
    plan_proposals: list[TaskPlanProposed] = []
    accepted_plan_id: str | None = None
    completion_proposals: list[TaskCompletionProposed] = []
    verifications: list[TaskAcceptanceVerified] = []
    checks: list[TaskAcceptanceChecked] = []
    terminal: TaskTerminated | None = None
    renamed: list[TaskRenamed] = []
    archived: list[TaskArchived] = []
    reflections: list[TaskReflectionRecorded] = []
    blockers: list[TaskBlockerRecorded] = []
    pause_events: list[TaskPauseChanged] = []
    checkpoints: list[TaskContextCheckpoint] = []
    previous_timestamp = header.created_at
    seen_stage_ids: set[str] = set()
    seen_plan_ids: set[str] = set()
    seen_proposal_tool_use_ids: set[str] = set()
    verified_keys: set[tuple[str, int]] = set()

    for expected_sequence, record in enumerate(records[1:], start=1):
        if record.sequence != expected_sequence:
            raise TaskRecordError(
                f"task record sequence must be {expected_sequence}, got {record.sequence}"
            )
        if isinstance(record, TaskHeader):
            raise TaskRecordError("task_header may appear only once")
        _validate_record(record)
        timestamp = _record_timestamp(record)
        _require_timestamp_order(previous_timestamp, timestamp)
        previous_timestamp = timestamp

        if isinstance(record, TaskConfiguration):
            expected_configuration_sequence = 2 if admission_origin is not None else 1
            if (
                configuration is not None
                or expected_sequence != expected_configuration_sequence
                or stages
            ):
                raise TaskRecordError(
                    "task_configuration must appear once immediately after header"
                )
            if record.parent_task_id == header.task_id:
                raise TaskRecordError("Task cannot derive from itself")
            configuration = record
            continue
        if isinstance(record, TaskAdmissionOrigin):
            if admission_origin is not None or expected_sequence != 1:
                raise TaskRecordError(
                    "task_admission_origin must appear once immediately after header"
                )
            if record.source_session_id != header.owner_session_id:
                raise TaskRecordError("Task admission source must match the owner Session")
            admission_origin = record
            continue
        if isinstance(record, TaskAcceptanceContract):
            if acceptance_contract is not None or stages:
                raise TaskRecordError(
                    "task_acceptance_contract must appear once before the first Stage"
                )
            if tuple(item.description for item in record.criteria) != header.acceptance_criteria:
                raise TaskRecordError(
                    "structured acceptance contract must match task_header descriptions"
                )
            acceptance_contract = record
            continue
        if isinstance(record, (TaskRenamed, TaskArchived)):
            if active is not None:
                raise TaskRecordError("Task metadata cannot advance while a Stage is active")
            (renamed if isinstance(record, TaskRenamed) else archived).append(record)
            continue
        if terminal is not None:
            raise TaskRecordError("Task lifecycle cannot advance after termination")
        if isinstance(record, StageStarted):
            if active is not None:
                raise TaskRecordError("a new Stage cannot start before the active Stage terminates")
            if record.stage_number != len(stages) + 1:
                raise TaskRecordError("Stage numbers must be contiguous and 1-based")
            if record.stage_id in seen_stage_ids:
                raise TaskRecordError("Stage IDs must be unique within one Task")
            if record.session_id != header.owner_session_id:
                raise TaskRecordError("Stage Session must match the Task owner Session")
            if len(stages) >= (
                configuration.budget.max_stages if configuration else MAX_TASK_STAGES
            ):
                raise TaskRecordError("Task Stage budget is exhausted")
            seen_stage_ids.add(record.stage_id)
            active = record
            stages.append(TaskStageState(record, None))
            continue
        if isinstance(record, (StageCommitted, StageFailed)):
            if active is None:
                raise TaskRecordError("Stage terminal record has no active Stage")
            _validate_stage_terminal_identity(active, record)
            if isinstance(record, StageCommitted) and record.session_id != header.owner_session_id:
                raise TaskRecordError("committed Stage Session must match the Task owner Session")
            stages[-1] = TaskStageState(active, record)
            active = None
            continue
        if active is not None:
            raise TaskRecordError("Task metadata cannot advance while a Stage is active")
        if isinstance(record, TaskPlanProposed):
            latest = stages[-1] if stages else None
            if (
                latest is None
                or not isinstance(latest.terminal, StageCommitted)
                or latest.started.kind is not StageKind.PLANNING
                or latest.started.stage_id != record.stage_id
                or latest.started.stage_number != record.stage_number
            ):
                raise TaskRecordError(
                    "Task plan proposal must reference the latest committed planning Stage"
                )
            if any(proposal.stage_id == record.stage_id for proposal in plan_proposals):
                raise TaskRecordError("Task planning Stage may propose a plan only once")
            if any(blocker.stage_id == record.stage_id for blocker in blockers):
                raise TaskRecordError("blocked Task Stage cannot also propose a plan")
            remaining_stages = (
                configuration.budget.max_stages
                if configuration is not None
                else TaskBudget().max_stages
            ) - len(stages)
            if len(record.steps) > remaining_stages:
                raise TaskRecordError("Task plan exceeds the remaining cumulative Stage budget")
            if record.plan_id in seen_plan_ids:
                raise TaskRecordError("Task plan IDs must be unique")
            predecessor = plan_proposals[-1].plan_id if plan_proposals else None
            if record.schema_version >= 2:
                if record.predecessor_plan_id != predecessor:
                    raise TaskRecordError(
                        "Task plan revision must reference the immediately preceding plan"
                    )
                if predecessor is None and (
                    record.revision_reason is not None or record.reflection_id is not None
                ):
                    raise TaskRecordError("initial Task plan cannot contain revision provenance")
                if predecessor is not None and record.revision_reason is None:
                    raise TaskRecordError("Task plan revision requires a bounded reason")
                if record.reflection_id is not None and (
                    not reflections
                    or reflections[-1].reflection_id != record.reflection_id
                    or reflections[-1].stage_number != record.stage_number - 1
                ):
                    raise TaskRecordError("Task plan revision reflection is not current")
            seen_plan_ids.add(record.plan_id)
            _claim_proposal_tool_use_id(record, seen_proposal_tool_use_ids)
            plan_proposals.append(record)
            continue
        if isinstance(record, TaskPlanAccepted):
            if not plan_proposals or record.plan_id != plan_proposals[-1].plan_id:
                raise TaskRecordError("Task plan acceptance must reference the latest proposal")
            if accepted_plan_id == record.plan_id:
                raise TaskRecordError("latest Task plan is already accepted")
            accepted_plan_id = record.plan_id
            accepted_plan_sequence = record.sequence
            continue
        if isinstance(record, TaskCompletionProposed):
            latest = stages[-1] if stages else None
            if (
                latest is None
                or not isinstance(latest.terminal, StageCommitted)
                or latest.started.kind not in {StageKind.EXECUTION, StageKind.CORRECTION}
                or latest.started.stage_id != record.stage_id
                or latest.started.stage_number != record.stage_number
            ):
                raise TaskRecordError(
                    "Task completion proposal must reference the latest committed execution Stage"
                )
            if completion_proposals and completion_proposals[-1].stage_id == record.stage_id:
                raise TaskRecordError("Task Stage may propose completion only once")
            if any(blocker.stage_id == record.stage_id for blocker in blockers):
                raise TaskRecordError("blocked Task Stage cannot also propose completion")
            _claim_proposal_tool_use_id(record, seen_proposal_tool_use_ids)
            completion_proposals.append(record)
            continue
        if isinstance(record, TaskReflectionRecorded):
            latest = stages[-1] if stages else None
            if (
                latest is None
                or not isinstance(latest.terminal, StageCommitted)
                or latest.started.kind is not StageKind.REFLECTION
                or latest.started.stage_id != record.stage_id
                or latest.started.stage_number != record.stage_number
            ):
                raise TaskRecordError(
                    "Task reflection must reference the latest committed reflection Stage"
                )
            if any(item.stage_id == record.stage_id for item in reflections):
                raise TaskRecordError("Task reflection Stage may be recorded only once")
            if any(blocker.stage_id == record.stage_id for blocker in blockers):
                raise TaskRecordError("blocked Task Stage cannot also record reflection")
            _claim_proposal_tool_use_id(record, seen_proposal_tool_use_ids)
            reflections.append(record)
            continue
        if isinstance(record, TaskBlockerRecorded):
            latest = stages[-1] if stages else None
            if (
                latest is None
                or not isinstance(latest.terminal, StageCommitted)
                or latest.started.stage_id != record.stage_id
                or latest.started.stage_number != record.stage_number
            ):
                raise TaskRecordError("Task blocker must reference the latest committed Stage")
            if any(item.stage_id == record.stage_id for item in blockers):
                raise TaskRecordError("Task Stage may report a blocker only once")
            if any(
                item.stage_id == record.stage_id
                for item in (*plan_proposals, *completion_proposals, *reflections)
            ):
                raise TaskRecordError("Task Stage cannot report a blocker after another proposal")
            _claim_proposal_tool_use_id(record, seen_proposal_tool_use_ids)
            blockers.append(record)
            continue
        if isinstance(record, (TaskAcceptanceVerified, TaskAcceptanceChecked)):
            if not 1 <= record.criterion_index <= len(header.acceptance_criteria):
                raise TaskRecordError("Task acceptance verification index is outside the contract")
            current_proposal = completion_proposals[-1] if completion_proposals else None
            latest_stage = stages[-1] if stages else None
            if (
                current_proposal is None
                or latest_stage is None
                or current_proposal.stage_id != latest_stage.started.stage_id
                or record.completion_stage_id != current_proposal.stage_id
            ):
                raise TaskRecordError(
                    "Task acceptance evidence requires the current completion proposal"
                )
            if isinstance(record, TaskAcceptanceChecked):
                checks.append(record)
                continue
            criteria = (
                acceptance_contract.criteria
                if acceptance_contract is not None
                else tuple(
                    TaskAcceptanceCriterion(AcceptanceCriterionKind.HUMAN, description)
                    for description in header.acceptance_criteria
                )
            )
            expected_source = _criterion_verification_source(criteria[record.criterion_index - 1])
            if record.source is not expected_source:
                raise TaskRecordError(
                    "Task acceptance verification source does not match criterion"
                )
            key = (record.completion_stage_id, record.criterion_index)
            if key in verified_keys:
                raise TaskRecordError(
                    "Task acceptance criterion may be verified only once per completion proposal"
                )
            verified_keys.add(key)
            verifications.append(record)
            continue
        if isinstance(record, TaskPauseChanged):
            if pause_events and pause_events[-1].paused is record.paused:
                raise TaskRecordError("Task pause state must change")
            pause_events.append(record)
            continue
        if isinstance(record, TaskContextCheckpoint):
            prior = checkpoints[-1].checkpoint_id if checkpoints else None
            current_proposal = completion_proposals[-1] if completion_proposals else None
            latest_stage = stages[-1] if stages else None
            current_completion_stage_id = (
                current_proposal.stage_id
                if current_proposal is not None
                and latest_stage is not None
                and current_proposal.stage_id == latest_stage.started.stage_id
                else None
            )
            current_accepted = (
                plan_proposals[-1].plan_id
                if plan_proposals and plan_proposals[-1].plan_id == accepted_plan_id
                else None
            )
            if record.source_sequence != record.sequence - 1:
                raise TaskRecordError("Task checkpoint source sequence is stale")
            if record.prior_checkpoint_id != prior:
                raise TaskRecordError("Task checkpoint chain is invalid")
            if record.accepted_plan_id != current_accepted:
                raise TaskRecordError("Task checkpoint accepted plan is stale")
            completed_plan_steps = 0
            if current_accepted is not None and accepted_plan_sequence is not None:
                plan = plan_proposals[-1]
                for stage in stages:
                    if (
                        completed_plan_steps == len(plan.steps)
                        or stage.started.sequence <= accepted_plan_sequence
                        or stage.started.kind is not StageKind.EXECUTION
                        or not isinstance(stage.terminal, StageCommitted)
                        or stage.started.objective != plan.steps[completed_plan_steps]
                    ):
                        continue
                    completed_plan_steps += 1
            if record.completed_plan_steps != completed_plan_steps:
                raise TaskRecordError("Task checkpoint plan progress is stale")
            if record.completion_stage_id != current_completion_stage_id:
                raise TaskRecordError("Task checkpoint completion proposal is stale")
            if record.latest_reflection_id != (
                reflections[-1].reflection_id if reflections else None
            ):
                raise TaskRecordError("Task checkpoint reflection is stale")
            verified = {
                item.criterion_index
                for item in verifications
                if current_completion_stage_id is not None
                and item.completion_stage_id == current_completion_stage_id
            }
            unresolved = tuple(
                index
                for index in range(1, len(header.acceptance_criteria) + 1)
                if index not in verified
            )
            if record.unresolved_criterion_indices != unresolved:
                raise TaskRecordError("Task checkpoint unresolved criteria are stale")
            checkpoints.append(record)
            continue
        if isinstance(record, TaskTerminated):
            if record.outcome is TaskTerminalOutcome.COMPLETED:
                latest_proposal = completion_proposals[-1] if completion_proposals else None
                latest_stage = stages[-1] if stages else None
                if (
                    latest_proposal is None
                    or latest_stage is None
                    or latest_proposal.stage_id != latest_stage.started.stage_id
                ):
                    raise TaskRecordError("completed Task requires a current completion proposal")
                current_verified = {
                    verification.criterion_index
                    for verification in verifications
                    if verification.completion_stage_id == latest_proposal.stage_id
                }
                if len(current_verified) != len(header.acceptance_criteria):
                    raise TaskRecordError(
                        "completed Task requires all acceptance criteria verified"
                    )
            terminal = record
            continue
        raise TaskRecordError("unsupported task lifecycle record")

    return TaskReplayState(
        header=header,
        records=tuple(records),
        stages=tuple(stages),
        configuration=configuration,
        acceptance_contract=acceptance_contract,
        admission_origin=admission_origin,
        plan_proposals=tuple(plan_proposals),
        accepted_plan_id=accepted_plan_id,
        completion_proposals=tuple(completion_proposals),
        acceptance_verifications=tuple(verifications),
        acceptance_checks=tuple(checks),
        terminal=terminal,
        renamed=tuple(renamed),
        archived_events=tuple(archived),
        reflections=tuple(reflections),
        blockers=tuple(blockers),
        pause_events=tuple(pause_events),
        context_checkpoints=tuple(checkpoints),
    )


def _claim_proposal_tool_use_id(
    record: TaskPlanProposed
    | TaskCompletionProposed
    | TaskReflectionRecorded
    | TaskBlockerRecorded,
    seen: set[str],
) -> None:
    tool_use_id = record.proposal_tool_use_id
    if tool_use_id is None:
        return
    if tool_use_id in seen:
        raise TaskRecordError("Task proposal tool-use IDs must be unique")
    seen.add(tool_use_id)


def _record_to_dict(record: TaskRecord) -> dict[str, object]:
    _validate_record(record)
    if isinstance(record, TaskHeader):
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
    if isinstance(record, TaskConfiguration):
        return {
            "budget": _budget_to_dict(record.budget),
            "configured_at": record.configured_at,
            "name": record.name,
            "parent_task_id": record.parent_task_id,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
        }
    if isinstance(record, TaskAcceptanceContract):
        return {
            "completion_policy": record.completion_policy.value,
            "configured_at": record.configured_at,
            "criteria": [_criterion_to_dict(item) for item in record.criteria],
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
        }
    if isinstance(record, TaskAdmissionOrigin):
        return {
            "admission_id": record.admission_id,
            "configuration_sha256": record.configuration_sha256,
            "confirmation_sha256": record.confirmation_sha256,
            "proposal_sha256": record.proposal_sha256,
            "proposal_tool_use_id": record.proposal_tool_use_id,
            "record_type": record.record_type,
            "recorded_at": record.recorded_at,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "source_context_id": record.source_context_id,
            "source_session_id": record.source_session_id,
            "source_turn_record_sequence": record.source_turn_record_sequence,
        }
    if isinstance(record, StageStarted):
        common = {
            "objective": record.objective,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "session_id": record.session_id,
            "stage_id": record.stage_id,
            "stage_number": record.stage_number,
            "started_at": record.started_at,
        }
        if record.schema_version >= 2:
            common.update(
                {
                    "kind": record.kind.value,
                    "prompt_sha256": record.prompt_sha256,
                    "session_record_sequence_before": record.session_record_sequence_before,
                    "session_turn_count_before": record.session_turn_count_before,
                }
            )
        if record.schema_version >= 3:
            common["hook_audit"] = _hook_audit_to_value(record.hook_audit, "stage_started")
        return common
    if isinstance(record, StageCommitted):
        common = {
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
        if record.schema_version >= 2:
            common["usage"] = _usage_to_dict(record.usage) if record.usage is not None else None
        if record.schema_version >= 3:
            common["hook_audit"] = _hook_audit_to_value(record.hook_audit, "stage_committed")
        return common
    if isinstance(record, StageFailed):
        common = {
            "failed_at": record.failed_at,
            "reason": record.reason.value,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "stage_id": record.stage_id,
            "stage_number": record.stage_number,
        }
        if record.schema_version >= 2:
            common["usage"] = _usage_to_dict(record.usage) if record.usage is not None else None
        if record.schema_version >= 3:
            common["hook_audit"] = _hook_audit_to_value(record.hook_audit, "stage_failed")
        return common
    if isinstance(record, TaskPlanProposed):
        common = {
            "plan_id": record.plan_id,
            "proposed_at": record.proposed_at,
            "record_type": record.record_type,
            "schema_version": record.schema_version,
            "sequence": record.sequence,
            "stage_id": record.stage_id,
            "stage_number": record.stage_number,
            "steps": list(record.steps),
        }
        if record.schema_version >= 2:
            common.update(
                {
                    "predecessor_plan_id": record.predecessor_plan_id,
                    "reflection_id": record.reflection_id,
                    "revision_reason": record.revision_reason,
                }
            )
        if record.schema_version >= 3:
            common["proposal_tool_use_id"] = record.proposal_tool_use_id
        return common
    if isinstance(record, TaskPlanAccepted):
        return _simple_record(record, accepted_at=record.accepted_at, plan_id=record.plan_id)
    if isinstance(record, TaskCompletionProposed):
        common = _simple_record(
            record,
            proposed_at=record.proposed_at,
            source=record.source.value,
            stage_id=record.stage_id,
            stage_number=record.stage_number,
        )
        if record.schema_version >= 2:
            common["proposal_tool_use_id"] = record.proposal_tool_use_id
        return common
    if isinstance(record, TaskAcceptanceVerified):
        return _simple_record(
            record,
            completion_stage_id=record.completion_stage_id,
            criterion_index=record.criterion_index,
            evidence=record.evidence,
            source=record.source.value,
            verified_at=record.verified_at,
        )
    if isinstance(record, TaskAcceptanceChecked):
        return _simple_record(
            record,
            checked_at=record.checked_at,
            completion_stage_id=record.completion_stage_id,
            criterion_index=record.criterion_index,
            evidence=record.evidence,
            outcome=record.outcome.value,
            source=record.source.value,
        )
    if isinstance(record, TaskTerminated):
        common = _simple_record(
            record,
            outcome=record.outcome.value,
            reason=record.reason,
            terminated_at=record.terminated_at,
        )
        if record.schema_version >= 2:
            common["hook_audit"] = _hook_audit_to_value(record.hook_audit, "task_terminated")
        return common
    if isinstance(record, TaskRenamed):
        return _simple_record(record, name=record.name, renamed_at=record.renamed_at)
    if isinstance(record, TaskArchived):
        return _simple_record(record, archived=record.archived, changed_at=record.changed_at)
    if isinstance(record, TaskReflectionRecorded):
        common = _simple_record(
            record,
            next_objective=record.next_objective,
            recommendation=record.recommendation.value,
            recorded_at=record.recorded_at,
            reflection_id=record.reflection_id,
            stage_id=record.stage_id,
            stage_number=record.stage_number,
            summary=record.summary,
        )
        if record.schema_version >= 2:
            common["proposal_tool_use_id"] = record.proposal_tool_use_id
        return common
    if isinstance(record, TaskBlockerRecorded):
        common = _simple_record(
            record,
            category=record.category.value,
            proposal_tool_use_id=record.proposal_tool_use_id,
            recorded_at=record.recorded_at,
            stage_id=record.stage_id,
            stage_number=record.stage_number,
            summary=record.summary,
        )
        if record.schema_version >= 2:
            common["hook_audit"] = _hook_audit_to_value(record.hook_audit, "task_blocker_recorded")
        return common
    if isinstance(record, TaskPauseChanged):
        return _simple_record(
            record,
            changed_at=record.changed_at,
            paused=record.paused,
            reason=record.reason,
        )
    if isinstance(record, TaskContextCheckpoint):
        return _simple_record(
            record,
            accepted_plan_id=record.accepted_plan_id,
            checkpoint_id=record.checkpoint_id,
            completed_plan_steps=record.completed_plan_steps,
            completion_stage_id=record.completion_stage_id,
            created_at=record.created_at,
            latest_reflection_id=record.latest_reflection_id,
            prior_checkpoint_id=record.prior_checkpoint_id,
            source_sequence=record.source_sequence,
            unresolved_criterion_indices=list(record.unresolved_criterion_indices),
        )
    raise TaskRecordError("unsupported task record")


def _simple_record(record: TaskRecord, **fields: object) -> dict[str, object]:
    return {
        **fields,
        "record_type": record.record_type,
        "schema_version": record.schema_version,
        "sequence": record.sequence,
    }


def _decode_header(value: dict[str, object]) -> TaskHeader:
    _fields(
        value,
        "task_header",
        "acceptance_criteria",
        "created_at",
        "objective",
        "owner_session_id",
        "scope",
        "task_id",
        "workspace",
        "workspace_fingerprint",
    )
    _version(value, TASK_HEADER_SCHEMA_VERSION, "task_header")
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


def _decode_configuration(value: dict[str, object]) -> TaskConfiguration:
    _fields(value, "task_configuration", "budget", "configured_at", "name", "parent_task_id")
    _version(value, TASK_CONFIGURATION_SCHEMA_VERSION, "task_configuration")
    record = TaskConfiguration(
        sequence=value.get("sequence"),
        name=value.get("name"),
        budget=_budget_from_value(value.get("budget")),
        configured_at=value.get("configured_at"),
        parent_task_id=value.get("parent_task_id"),
    )
    _validate_configuration(record)
    return record


def _decode_acceptance_contract(value: dict[str, object]) -> TaskAcceptanceContract:
    _fields(
        value,
        "task_acceptance_contract",
        "completion_policy",
        "configured_at",
        "criteria",
    )
    _version(value, TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION, "task_acceptance_contract")
    raw_criteria = value.get("criteria")
    if not isinstance(raw_criteria, list):
        raise TaskRecordError("structured acceptance criteria must be an array")
    try:
        policy = TaskCompletionPolicy(value.get("completion_policy"))
    except (TypeError, ValueError):
        raise TaskRecordError("Task completion policy is invalid") from None
    record = TaskAcceptanceContract(
        sequence=value.get("sequence"),
        criteria=tuple(_criterion_from_value(item) for item in raw_criteria),
        completion_policy=policy,
        configured_at=value.get("configured_at"),
    )
    _validate_acceptance_contract(record)
    return record


def _decode_admission_origin(value: dict[str, object]) -> TaskAdmissionOrigin:
    _fields(
        value,
        "task_admission_origin",
        "admission_id",
        "configuration_sha256",
        "confirmation_sha256",
        "proposal_sha256",
        "proposal_tool_use_id",
        "recorded_at",
        "source_context_id",
        "source_session_id",
        "source_turn_record_sequence",
    )
    _version(value, TASK_ADMISSION_ORIGIN_SCHEMA_VERSION, "task_admission_origin")
    record = TaskAdmissionOrigin(
        sequence=value.get("sequence"),
        admission_id=value.get("admission_id"),
        proposal_sha256=value.get("proposal_sha256"),
        configuration_sha256=value.get("configuration_sha256"),
        confirmation_sha256=value.get("confirmation_sha256"),
        source_session_id=value.get("source_session_id"),
        source_turn_record_sequence=value.get("source_turn_record_sequence"),
        proposal_tool_use_id=value.get("proposal_tool_use_id"),
        source_context_id=value.get("source_context_id"),
        recorded_at=value.get("recorded_at"),
    )
    _validate_admission_origin(record)
    return record


def _decode_stage_started(value: dict[str, object]) -> StageStarted:
    version = value.get("schema_version")
    base = (
        "objective",
        "session_id",
        "stage_id",
        "stage_number",
        "started_at",
    )
    if version == 1:
        _fields(value, "stage_started", *base)
        record = StageStarted(
            sequence=value.get("sequence"),
            stage_id=value.get("stage_id"),
            stage_number=value.get("stage_number"),
            session_id=value.get("session_id"),
            objective=value.get("objective"),
            started_at=value.get("started_at"),
            schema_version=1,
        )
    elif version in {2, 3}:
        _fields(
            value,
            "stage_started",
            *base,
            "kind",
            "prompt_sha256",
            "session_record_sequence_before",
            "session_turn_count_before",
            *(("hook_audit",) if version >= 3 else ()),
        )
        try:
            kind = StageKind(value.get("kind"))
        except (TypeError, ValueError):
            raise TaskRecordError("unsupported Stage kind") from None
        record = StageStarted(
            sequence=value.get("sequence"),
            stage_id=value.get("stage_id"),
            stage_number=value.get("stage_number"),
            session_id=value.get("session_id"),
            objective=value.get("objective"),
            started_at=value.get("started_at"),
            kind=kind,
            session_record_sequence_before=value.get("session_record_sequence_before"),
            session_turn_count_before=value.get("session_turn_count_before"),
            prompt_sha256=value.get("prompt_sha256"),
            hook_audit=(
                _hook_audit_from_value(value.get("hook_audit"), "stage_started")
                if version >= 3
                else HookAuditLedger()
            ),
            schema_version=version,
        )
    else:
        raise TaskRecordError("unsupported stage_started schema version")
    _validate_stage_started(record)
    return record


def _decode_stage_committed(value: dict[str, object]) -> StageCommitted:
    version = value.get("schema_version")
    base = (
        "committed_at",
        "session_id",
        "stage_id",
        "stage_number",
        "turn_number",
        "turn_record_sequence",
        "turn_record_sha256",
    )
    if version == 1:
        _fields(value, "stage_committed", *base)
        usage = None
    elif version in {2, 3}:
        _fields(
            value,
            "stage_committed",
            *base,
            "usage",
            *(("hook_audit",) if version >= 3 else ()),
        )
        usage = None if value.get("usage") is None else _usage_from_value(value.get("usage"))
    else:
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
        usage=usage,
        hook_audit=(
            _hook_audit_from_value(value.get("hook_audit"), "stage_committed")
            if version >= 3
            else HookAuditLedger()
        ),
        schema_version=version,
    )
    _validate_stage_committed(record)
    return record


def _decode_stage_failed(value: dict[str, object]) -> StageFailed:
    version = value.get("schema_version")
    fields = ("failed_at", "reason", "stage_id", "stage_number")
    if version == 1:
        _fields(value, "stage_failed", *fields)
        usage = None
    elif version in {2, 3}:
        _fields(
            value,
            "stage_failed",
            *fields,
            "usage",
            *(("hook_audit",) if version >= 3 else ()),
        )
        usage = None if value.get("usage") is None else _usage_from_value(value.get("usage"))
    else:
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
        usage=usage,
        hook_audit=(
            _hook_audit_from_value(value.get("hook_audit"), "stage_failed")
            if version >= 3
            else HookAuditLedger()
        ),
        schema_version=version,
    )
    _validate_stage_failed(record)
    return record


def _decode_plan_proposed(value: dict[str, object]) -> TaskPlanProposed:
    version = value.get("schema_version")
    base = (
        "plan_id",
        "proposed_at",
        "stage_id",
        "stage_number",
        "steps",
    )
    if version == 1:
        _fields(value, "task_plan_proposed", *base)
    elif version in {2, 3}:
        _fields(
            value,
            "task_plan_proposed",
            *base,
            "predecessor_plan_id",
            "reflection_id",
            "revision_reason",
            *(() if version == 2 else ("proposal_tool_use_id",)),
        )
    else:
        raise TaskRecordError("unsupported task_plan_proposed schema version")
    steps = value.get("steps")
    if not isinstance(steps, list):
        raise TaskRecordError("task plan steps must be an array")
    record = TaskPlanProposed(
        sequence=value.get("sequence"),
        plan_id=value.get("plan_id"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        steps=tuple(steps),
        proposed_at=value.get("proposed_at"),
        predecessor_plan_id=value.get("predecessor_plan_id"),
        revision_reason=value.get("revision_reason"),
        reflection_id=value.get("reflection_id"),
        proposal_tool_use_id=value.get("proposal_tool_use_id"),
        schema_version=version,
    )
    _validate_plan_proposed(record)
    return record


def _decode_plan_accepted(value: dict[str, object]) -> TaskPlanAccepted:
    _fields(value, "task_plan_accepted", "accepted_at", "plan_id")
    _version(value, TASK_PLAN_ACCEPTED_SCHEMA_VERSION, "task_plan_accepted")
    record = TaskPlanAccepted(value.get("sequence"), value.get("plan_id"), value.get("accepted_at"))
    _validate_plan_accepted(record)
    return record


def _decode_completion_proposed(value: dict[str, object]) -> TaskCompletionProposed:
    version = value.get("schema_version")
    _fields(
        value,
        "task_completion_proposed",
        "proposed_at",
        "source",
        "stage_id",
        "stage_number",
        *(() if version == 1 else ("proposal_tool_use_id",)),
    )
    if version not in {1, TASK_COMPLETION_PROPOSED_SCHEMA_VERSION}:
        raise TaskRecordError("unsupported task_completion_proposed schema version")
    try:
        source = CompletionProposalSource(value.get("source"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported completion proposal source") from None
    record = TaskCompletionProposed(
        sequence=value.get("sequence"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        source=source,
        proposed_at=value.get("proposed_at"),
        proposal_tool_use_id=value.get("proposal_tool_use_id"),
        schema_version=version,
    )
    _validate_completion_proposed(record)
    return record


def _decode_acceptance_verified(value: dict[str, object]) -> TaskAcceptanceVerified:
    _fields(
        value,
        "task_acceptance_verified",
        "completion_stage_id",
        "criterion_index",
        "evidence",
        "source",
        "verified_at",
    )
    _version(value, TASK_ACCEPTANCE_VERIFIED_SCHEMA_VERSION, "task_acceptance_verified")
    try:
        source = AcceptanceVerificationSource(value.get("source"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported acceptance verification source") from None
    record = TaskAcceptanceVerified(
        value.get("sequence"),
        value.get("completion_stage_id"),
        value.get("criterion_index"),
        value.get("evidence"),
        source,
        value.get("verified_at"),
    )
    _validate_acceptance_verified(record)
    return record


def _decode_acceptance_checked(value: dict[str, object]) -> TaskAcceptanceChecked:
    _fields(
        value,
        "task_acceptance_checked",
        "checked_at",
        "completion_stage_id",
        "criterion_index",
        "evidence",
        "outcome",
        "source",
    )
    _version(value, TASK_ACCEPTANCE_CHECKED_SCHEMA_VERSION, "task_acceptance_checked")
    try:
        source = AcceptanceVerificationSource(value.get("source"))
        outcome = AcceptanceCheckOutcome(value.get("outcome"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported acceptance check source or outcome") from None
    record = TaskAcceptanceChecked(
        sequence=value.get("sequence"),
        completion_stage_id=value.get("completion_stage_id"),
        criterion_index=value.get("criterion_index"),
        source=source,
        outcome=outcome,
        evidence=value.get("evidence"),
        checked_at=value.get("checked_at"),
    )
    _validate_acceptance_checked(record)
    return record


def _decode_terminated(value: dict[str, object]) -> TaskTerminated:
    version = value.get("schema_version")
    if version not in {1, TASK_TERMINATED_SCHEMA_VERSION}:
        raise TaskRecordError("unsupported task_terminated schema version")
    _fields(
        value,
        "task_terminated",
        "outcome",
        "reason",
        "terminated_at",
        *(("hook_audit",) if version >= 2 else ()),
    )
    try:
        outcome = TaskTerminalOutcome(value.get("outcome"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported Task terminal outcome") from None
    record = TaskTerminated(
        value.get("sequence"),
        outcome,
        value.get("reason"),
        value.get("terminated_at"),
        hook_audit=(
            _hook_audit_from_value(value.get("hook_audit"), "task_terminated")
            if version >= 2
            else HookAuditLedger()
        ),
        schema_version=version,
    )
    _validate_terminated(record)
    return record


def _decode_renamed(value: dict[str, object]) -> TaskRenamed:
    _fields(value, "task_renamed", "name", "renamed_at")
    _version(value, TASK_RENAMED_SCHEMA_VERSION, "task_renamed")
    record = TaskRenamed(value.get("sequence"), value.get("name"), value.get("renamed_at"))
    _validate_renamed(record)
    return record


def _decode_archived(value: dict[str, object]) -> TaskArchived:
    _fields(value, "task_archived", "archived", "changed_at")
    _version(value, TASK_ARCHIVED_SCHEMA_VERSION, "task_archived")
    record = TaskArchived(value.get("sequence"), value.get("archived"), value.get("changed_at"))
    _validate_archived(record)
    return record


def _decode_reflection_recorded(value: dict[str, object]) -> TaskReflectionRecorded:
    version = value.get("schema_version")
    _fields(
        value,
        "task_reflection_recorded",
        "next_objective",
        "recommendation",
        "recorded_at",
        "reflection_id",
        "stage_id",
        "stage_number",
        "summary",
        *(() if version == 1 else ("proposal_tool_use_id",)),
    )
    if version not in {1, TASK_REFLECTION_RECORDED_SCHEMA_VERSION}:
        raise TaskRecordError("unsupported task_reflection_recorded schema version")
    try:
        recommendation = ReflectionRecommendation(value.get("recommendation"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported Task reflection recommendation") from None
    record = TaskReflectionRecorded(
        sequence=value.get("sequence"),
        reflection_id=value.get("reflection_id"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        recommendation=recommendation,
        summary=value.get("summary"),
        next_objective=value.get("next_objective"),
        recorded_at=value.get("recorded_at"),
        proposal_tool_use_id=value.get("proposal_tool_use_id"),
        schema_version=version,
    )
    _validate_reflection_recorded(record)
    return record


def _decode_blocker_recorded(value: dict[str, object]) -> TaskBlockerRecorded:
    version = value.get("schema_version")
    if version not in {1, TASK_BLOCKER_RECORDED_SCHEMA_VERSION}:
        raise TaskRecordError("unsupported task_blocker_recorded schema version")
    _fields(
        value,
        "task_blocker_recorded",
        "category",
        "proposal_tool_use_id",
        "recorded_at",
        "stage_id",
        "stage_number",
        "summary",
        *(("hook_audit",) if version >= 2 else ()),
    )
    try:
        category = TaskBlockerCategory(value.get("category"))
    except (TypeError, ValueError):
        raise TaskRecordError("unsupported Task blocker category") from None
    record = TaskBlockerRecorded(
        sequence=value.get("sequence"),
        stage_id=value.get("stage_id"),
        stage_number=value.get("stage_number"),
        category=category,
        summary=value.get("summary"),
        proposal_tool_use_id=value.get("proposal_tool_use_id"),
        recorded_at=value.get("recorded_at"),
        hook_audit=(
            _hook_audit_from_value(value.get("hook_audit"), "task_blocker_recorded")
            if version >= 2
            else HookAuditLedger()
        ),
        schema_version=version,
    )
    _validate_blocker_recorded(record)
    return record


def _decode_pause_changed(value: dict[str, object]) -> TaskPauseChanged:
    _fields(value, "task_pause_changed", "changed_at", "paused", "reason")
    _version(value, TASK_PAUSE_CHANGED_SCHEMA_VERSION, "task_pause_changed")
    record = TaskPauseChanged(
        value.get("sequence"),
        value.get("paused"),
        value.get("reason"),
        value.get("changed_at"),
    )
    _validate_pause_changed(record)
    return record


def _decode_context_checkpoint(value: dict[str, object]) -> TaskContextCheckpoint:
    _fields(
        value,
        "task_context_checkpoint",
        "accepted_plan_id",
        "checkpoint_id",
        "completed_plan_steps",
        "completion_stage_id",
        "created_at",
        "latest_reflection_id",
        "prior_checkpoint_id",
        "source_sequence",
        "unresolved_criterion_indices",
    )
    _version(value, TASK_CONTEXT_CHECKPOINT_SCHEMA_VERSION, "task_context_checkpoint")
    unresolved = value.get("unresolved_criterion_indices")
    if not isinstance(unresolved, list):
        raise TaskRecordError("Task checkpoint unresolved criteria must be an array")
    record = TaskContextCheckpoint(
        sequence=value.get("sequence"),
        checkpoint_id=value.get("checkpoint_id"),
        source_sequence=value.get("source_sequence"),
        prior_checkpoint_id=value.get("prior_checkpoint_id"),
        accepted_plan_id=value.get("accepted_plan_id"),
        completed_plan_steps=value.get("completed_plan_steps"),
        completion_stage_id=value.get("completion_stage_id"),
        unresolved_criterion_indices=tuple(unresolved),
        latest_reflection_id=value.get("latest_reflection_id"),
        created_at=value.get("created_at"),
    )
    _validate_context_checkpoint(record)
    return record


_DECODERS = {
    "task_header": _decode_header,
    "task_configuration": _decode_configuration,
    "task_acceptance_contract": _decode_acceptance_contract,
    "task_admission_origin": _decode_admission_origin,
    "stage_started": _decode_stage_started,
    "stage_committed": _decode_stage_committed,
    "stage_failed": _decode_stage_failed,
    "task_plan_proposed": _decode_plan_proposed,
    "task_plan_accepted": _decode_plan_accepted,
    "task_completion_proposed": _decode_completion_proposed,
    "task_acceptance_verified": _decode_acceptance_verified,
    "task_acceptance_checked": _decode_acceptance_checked,
    "task_terminated": _decode_terminated,
    "task_renamed": _decode_renamed,
    "task_archived": _decode_archived,
    "task_reflection_recorded": _decode_reflection_recorded,
    "task_blocker_recorded": _decode_blocker_recorded,
    "task_pause_changed": _decode_pause_changed,
    "task_context_checkpoint": _decode_context_checkpoint,
}


def _validate_record(record: object) -> None:
    validators = {
        TaskHeader: _validate_header,
        TaskConfiguration: _validate_configuration,
        TaskAcceptanceContract: _validate_acceptance_contract,
        TaskAdmissionOrigin: _validate_admission_origin,
        StageStarted: _validate_stage_started,
        StageCommitted: _validate_stage_committed,
        StageFailed: _validate_stage_failed,
        TaskPlanProposed: _validate_plan_proposed,
        TaskPlanAccepted: _validate_plan_accepted,
        TaskCompletionProposed: _validate_completion_proposed,
        TaskAcceptanceVerified: _validate_acceptance_verified,
        TaskAcceptanceChecked: _validate_acceptance_checked,
        TaskTerminated: _validate_terminated,
        TaskRenamed: _validate_renamed,
        TaskArchived: _validate_archived,
        TaskReflectionRecorded: _validate_reflection_recorded,
        TaskBlockerRecorded: _validate_blocker_recorded,
        TaskPauseChanged: _validate_pause_changed,
        TaskContextCheckpoint: _validate_context_checkpoint,
    }
    validator = validators.get(type(record))
    if validator is None:
        raise TaskRecordError("unsupported task record")
    validator(record)


def _validate_header(record: object) -> None:
    if not isinstance(record, TaskHeader):
        raise TaskRecordError("unsupported task record")
    if type(record.sequence) is not int or record.sequence != 0:
        raise TaskRecordError("task_header sequence must be 0")
    _record_identity(record, "task_header", TASK_HEADER_SCHEMA_VERSION)
    canonical_task_id(record.task_id)
    _validate_session_id(record.owner_session_id, "owner Session ID")
    if (
        not isinstance(record.workspace, str)
        or not record.workspace
        or not Path(record.workspace).is_absolute()
    ):
        raise TaskRecordError("task workspace must be a non-empty absolute path")
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
    _validate_timestamp(record.created_at, "task created_at")


def _validate_configuration(record: object) -> None:
    if not isinstance(record, TaskConfiguration):
        raise TaskRecordError("unsupported task_configuration record")
    _positive_sequence(record.sequence, "task_configuration sequence")
    _record_identity(record, "task_configuration", TASK_CONFIGURATION_SCHEMA_VERSION)
    canonical_task_name(record.name)
    canonical_task_budget(record.budget)
    if record.parent_task_id is not None:
        canonical_task_id(record.parent_task_id)
    _validate_timestamp(record.configured_at, "Task configured_at")


def _validate_acceptance_contract(record: object) -> None:
    if not isinstance(record, TaskAcceptanceContract):
        raise TaskRecordError("unsupported task_acceptance_contract record")
    _positive_sequence(record.sequence, "task_acceptance_contract sequence")
    _record_identity(
        record,
        "task_acceptance_contract",
        TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION,
    )
    canonical_task_acceptance_contract(record.criteria, record.completion_policy)
    _validate_timestamp(record.configured_at, "Task acceptance contract configured_at")


def _validate_admission_origin(record: object) -> None:
    if not isinstance(record, TaskAdmissionOrigin):
        raise TaskRecordError("unsupported task_admission_origin record")
    _positive_sequence(record.sequence, "task_admission_origin sequence")
    _record_identity(record, "task_admission_origin", TASK_ADMISSION_ORIGIN_SCHEMA_VERSION)
    try:
        canonical_task_admission_id(record.admission_id)
    except ValueError as error:
        raise TaskRecordError(str(error)) from None
    if (
        not isinstance(record.proposal_sha256, str)
        or _SHA256.fullmatch(record.proposal_sha256) is None
    ):
        raise TaskRecordError("Task admission proposal SHA-256 is invalid")
    for digest, label in (
        (record.configuration_sha256, "Task admission configuration SHA-256"),
        (record.confirmation_sha256, "Task admission confirmation SHA-256"),
    ):
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise TaskRecordError(f"{label} is invalid")
    _validate_session_id(record.source_session_id, "Task admission source Session ID")
    _positive(record.source_turn_record_sequence, "Task admission source Turn record sequence")
    canonical_task_proposal_tool_use_id(record.proposal_tool_use_id)
    if (
        not isinstance(record.source_context_id, str)
        or _CONTEXT_ID.fullmatch(record.source_context_id) is None
    ):
        raise TaskRecordError("Task admission source context identity is invalid")
    _validate_timestamp(record.recorded_at, "Task admission origin recorded_at")


def _validate_stage_started(record: object) -> None:
    if not isinstance(record, StageStarted):
        raise TaskRecordError("unsupported stage_started record")
    _positive_sequence(record.sequence, "stage_started sequence")
    if record.record_type != "stage_started" or record.schema_version not in {1, 2, 3}:
        raise TaskRecordError("unsupported stage_started schema or record type")
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "Stage number")
    _validate_session_id(record.session_id, "Stage Session ID")
    canonical_stage_objective(record.objective)
    _validate_timestamp(record.started_at, "Stage started_at")
    if record.schema_version == 1:
        if (
            record.kind is not StageKind.EXECUTION
            or record.session_record_sequence_before is not None
            or record.session_turn_count_before is not None
            or record.prompt_sha256 is not None
            or record.hook_audit.entries
        ):
            raise TaskRecordError("stage_started v1 cannot contain recovery metadata")
        return
    if type(record.kind) is not StageKind:
        raise TaskRecordError("unsupported Stage kind")
    for value, label in (
        (record.session_record_sequence_before, "Session record baseline"),
        (record.session_turn_count_before, "Session Turn baseline"),
    ):
        if value is not None and (type(value) is not int or value < 0):
            raise TaskRecordError(f"{label} must be a nonnegative integer")
    if (record.session_record_sequence_before is None) != (
        record.session_turn_count_before is None
    ):
        raise TaskRecordError("Stage recovery baselines must be provided together")
    _optional_sha256(record.prompt_sha256, "Stage prompt SHA-256")
    _validate_hook_audit(
        record.hook_audit,
        supported=record.schema_version >= 3,
        label="stage_started",
        expected_event=HookEvent.TASK_STAGE_STARTED,
    )


def _validate_stage_committed(record: object) -> None:
    if not isinstance(record, StageCommitted):
        raise TaskRecordError("unsupported stage_committed record")
    _positive_sequence(record.sequence, "stage_committed sequence")
    if record.record_type != "stage_committed" or record.schema_version not in {1, 2, 3}:
        raise TaskRecordError("unsupported stage_committed schema or record type")
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "Stage number")
    _validate_session_id(record.session_id, "committed Stage Session ID")
    _positive(record.turn_number, "Session Turn number")
    _positive(record.turn_record_sequence, "Session Turn record sequence")
    _required_sha256(record.turn_record_sha256, "Session Turn record SHA-256")
    _validate_timestamp(record.committed_at, "Stage committed_at")
    if record.schema_version == 1 and record.usage is not None:
        raise TaskRecordError("stage_committed v1 cannot contain usage")
    if record.usage is not None:
        _validate_stage_usage(record.usage)
    _validate_hook_audit(
        record.hook_audit,
        supported=record.schema_version >= 3,
        label="stage_committed",
        expected_event=HookEvent.TASK_STAGE_COMMITTED,
    )


def _validate_stage_failed(record: object) -> None:
    if not isinstance(record, StageFailed):
        raise TaskRecordError("unsupported stage_failed record")
    _positive_sequence(record.sequence, "stage_failed sequence")
    if record.record_type != "stage_failed" or record.schema_version not in {1, 2, 3}:
        raise TaskRecordError("unsupported stage_failed schema or record type")
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "Stage number")
    if type(record.reason) is not StageFailureReason:
        raise TaskRecordError("unsupported Stage failure reason")
    _validate_timestamp(record.failed_at, "Stage failed_at")
    if record.schema_version == 1 and record.usage is not None:
        raise TaskRecordError("stage_failed v1 cannot contain usage")
    if record.usage is not None:
        _validate_stage_usage(record.usage)
    _validate_hook_audit(
        record.hook_audit,
        supported=record.schema_version >= 3,
        label="stage_failed",
        expected_event=HookEvent.TASK_STAGE_FAILED,
    )


def _validate_plan_proposed(record: object) -> None:
    if not isinstance(record, TaskPlanProposed):
        raise TaskRecordError("unsupported task_plan_proposed record")
    _positive_sequence(record.sequence, "task_plan_proposed sequence")
    if record.record_type != "task_plan_proposed" or record.schema_version not in {1, 2, 3}:
        raise TaskRecordError("unsupported task_plan_proposed schema or record type")
    canonical_plan_id(record.plan_id)
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "Stage number")
    canonical_plan_steps(record.steps)
    _validate_timestamp(record.proposed_at, "Task plan proposed_at")
    if record.schema_version == 1:
        if any(
            value is not None
            for value in (
                record.predecessor_plan_id,
                record.revision_reason,
                record.reflection_id,
            )
        ):
            raise TaskRecordError("task_plan_proposed v1 cannot contain revision provenance")
        if record.proposal_tool_use_id is not None:
            raise TaskRecordError("task_plan_proposed v1 cannot contain a proposal tool ID")
        return
    if record.predecessor_plan_id is not None:
        canonical_plan_id(record.predecessor_plan_id)
    if record.revision_reason is not None:
        _bounded_text(
            record.revision_reason,
            "Task plan revision reason",
            max_characters=1024,
            max_bytes=4096,
        )
    if record.reflection_id is not None:
        canonical_reflection_id(record.reflection_id)
    if record.schema_version == 2 and record.proposal_tool_use_id is not None:
        raise TaskRecordError("task_plan_proposed v2 cannot contain a proposal tool ID")
    if record.schema_version >= 3 and record.proposal_tool_use_id is not None:
        canonical_task_proposal_tool_use_id(record.proposal_tool_use_id)


def _validate_plan_accepted(record: object) -> None:
    if not isinstance(record, TaskPlanAccepted):
        raise TaskRecordError("unsupported task_plan_accepted record")
    _positive_sequence(record.sequence, "task_plan_accepted sequence")
    _record_identity(record, "task_plan_accepted", TASK_PLAN_ACCEPTED_SCHEMA_VERSION)
    canonical_plan_id(record.plan_id)
    _validate_timestamp(record.accepted_at, "Task plan accepted_at")


def _validate_completion_proposed(record: object) -> None:
    if not isinstance(record, TaskCompletionProposed):
        raise TaskRecordError("unsupported task_completion_proposed record")
    _positive_sequence(record.sequence, "task_completion_proposed sequence")
    if record.record_type != "task_completion_proposed" or record.schema_version not in {1, 2}:
        raise TaskRecordError("unsupported task_completion_proposed schema or record type")
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "Stage number")
    if type(record.source) is not CompletionProposalSource:
        raise TaskRecordError("unsupported completion proposal source")
    _validate_timestamp(record.proposed_at, "Task completion proposed_at")
    if record.schema_version == 1 and record.proposal_tool_use_id is not None:
        raise TaskRecordError("task_completion_proposed v1 cannot contain a proposal tool ID")
    if record.schema_version >= 2 and record.proposal_tool_use_id is not None:
        canonical_task_proposal_tool_use_id(record.proposal_tool_use_id)


def _validate_acceptance_verified(record: object) -> None:
    if not isinstance(record, TaskAcceptanceVerified):
        raise TaskRecordError("unsupported task_acceptance_verified record")
    _positive_sequence(record.sequence, "task_acceptance_verified sequence")
    _record_identity(record, "task_acceptance_verified", TASK_ACCEPTANCE_VERIFIED_SCHEMA_VERSION)
    canonical_stage_id(record.completion_stage_id)
    _positive(record.criterion_index, "acceptance criterion index")
    _bounded_text(record.evidence, "acceptance evidence", max_characters=1024, max_bytes=4096)
    if type(record.source) is not AcceptanceVerificationSource:
        raise TaskRecordError("unsupported acceptance verification source")
    _validate_timestamp(record.verified_at, "Task acceptance verified_at")


def _validate_acceptance_checked(record: object) -> None:
    if not isinstance(record, TaskAcceptanceChecked):
        raise TaskRecordError("unsupported task_acceptance_checked record")
    _positive_sequence(record.sequence, "task_acceptance_checked sequence")
    _record_identity(record, "task_acceptance_checked", TASK_ACCEPTANCE_CHECKED_SCHEMA_VERSION)
    canonical_stage_id(record.completion_stage_id)
    _positive(record.criterion_index, "acceptance criterion index")
    if record.source not in {
        AcceptanceVerificationSource.HOST_CHECK,
        AcceptanceVerificationSource.INDEPENDENT_REVIEWER,
    }:
        raise TaskRecordError("acceptance checks require a Host or reviewer source")
    if type(record.outcome) is not AcceptanceCheckOutcome:
        raise TaskRecordError("acceptance check outcome is invalid")
    _bounded_text(record.evidence, "acceptance check evidence", max_characters=1024, max_bytes=4096)
    _validate_timestamp(record.checked_at, "Task acceptance checked_at")


def _validate_terminated(record: object) -> None:
    if not isinstance(record, TaskTerminated):
        raise TaskRecordError("unsupported task_terminated record")
    _positive_sequence(record.sequence, "task_terminated sequence")
    if record.record_type != "task_terminated" or record.schema_version not in {1, 2}:
        raise TaskRecordError("unsupported task_terminated schema or record type")
    if type(record.outcome) is not TaskTerminalOutcome:
        raise TaskRecordError("unsupported Task terminal outcome")
    if record.outcome is TaskTerminalOutcome.COMPLETED:
        if record.reason is not None:
            raise TaskRecordError("completed Task cannot contain a failure reason")
    else:
        _bounded_text(record.reason, "Task terminal reason", max_characters=1024, max_bytes=4096)
    _validate_timestamp(record.terminated_at, "Task terminated_at")
    _validate_hook_audit(
        record.hook_audit,
        supported=record.schema_version >= 2,
        label="task_terminated",
        expected_event=HookEvent.TASK_TERMINATED,
    )


def _validate_renamed(record: object) -> None:
    if not isinstance(record, TaskRenamed):
        raise TaskRecordError("unsupported task_renamed record")
    _positive_sequence(record.sequence, "task_renamed sequence")
    _record_identity(record, "task_renamed", TASK_RENAMED_SCHEMA_VERSION)
    canonical_task_name(record.name)
    _validate_timestamp(record.renamed_at, "Task renamed_at")


def _validate_archived(record: object) -> None:
    if not isinstance(record, TaskArchived):
        raise TaskRecordError("unsupported task_archived record")
    _positive_sequence(record.sequence, "task_archived sequence")
    _record_identity(record, "task_archived", TASK_ARCHIVED_SCHEMA_VERSION)
    if type(record.archived) is not bool:
        raise TaskRecordError("Task archived flag must be boolean")
    _validate_timestamp(record.changed_at, "Task archive changed_at")


def _validate_reflection_recorded(record: object) -> None:
    if not isinstance(record, TaskReflectionRecorded):
        raise TaskRecordError("unsupported task_reflection_recorded record")
    _positive_sequence(record.sequence, "task_reflection_recorded sequence")
    if record.record_type != "task_reflection_recorded" or record.schema_version not in {1, 2}:
        raise TaskRecordError("unsupported task_reflection_recorded schema or record type")
    canonical_reflection_id(record.reflection_id)
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "reflection Stage number")
    if type(record.recommendation) is not ReflectionRecommendation:
        raise TaskRecordError("unsupported Task reflection recommendation")
    _bounded_text(record.summary, "Task reflection summary", max_characters=1024, max_bytes=4096)
    if record.next_objective is not None:
        canonical_stage_objective(record.next_objective)
    if (
        record.recommendation
        in {
            ReflectionRecommendation.CONTINUE,
            ReflectionRecommendation.CORRECTION,
            ReflectionRecommendation.REVISE_PLAN,
        }
        and record.next_objective is None
    ):
        raise TaskRecordError("actionable Task reflection requires a next objective")
    _validate_timestamp(record.recorded_at, "Task reflection recorded_at")
    if record.schema_version == 1 and record.proposal_tool_use_id is not None:
        raise TaskRecordError("task_reflection_recorded v1 cannot contain a proposal tool ID")
    if record.schema_version >= 2 and record.proposal_tool_use_id is not None:
        canonical_task_proposal_tool_use_id(record.proposal_tool_use_id)


def _validate_blocker_recorded(record: object) -> None:
    if not isinstance(record, TaskBlockerRecorded):
        raise TaskRecordError("unsupported task_blocker_recorded record")
    _positive_sequence(record.sequence, "task_blocker_recorded sequence")
    if record.record_type != "task_blocker_recorded" or record.schema_version not in {1, 2}:
        raise TaskRecordError("unsupported task_blocker_recorded schema or record type")
    canonical_stage_id(record.stage_id)
    _positive(record.stage_number, "blocker Stage number")
    if type(record.category) is not TaskBlockerCategory:
        raise TaskRecordError("unsupported Task blocker category")
    _bounded_text(record.summary, "Task blocker summary", max_characters=1024, max_bytes=4096)
    canonical_task_proposal_tool_use_id(record.proposal_tool_use_id)
    _validate_timestamp(record.recorded_at, "Task blocker recorded_at")
    _validate_hook_audit(
        record.hook_audit,
        supported=record.schema_version >= 2,
        label="task_blocker_recorded",
        expected_event=HookEvent.TASK_BLOCKED,
    )


def _validate_pause_changed(record: object) -> None:
    if not isinstance(record, TaskPauseChanged):
        raise TaskRecordError("unsupported task_pause_changed record")
    _positive_sequence(record.sequence, "task_pause_changed sequence")
    _record_identity(record, "task_pause_changed", TASK_PAUSE_CHANGED_SCHEMA_VERSION)
    if type(record.paused) is not bool:
        raise TaskRecordError("Task pause state must be boolean")
    if record.reason is not None:
        _bounded_text(record.reason, "Task pause reason", max_characters=1024, max_bytes=4096)
    _validate_timestamp(record.changed_at, "Task pause changed_at")


def _validate_context_checkpoint(record: object) -> None:
    if not isinstance(record, TaskContextCheckpoint):
        raise TaskRecordError("unsupported task_context_checkpoint record")
    _positive_sequence(record.sequence, "task_context_checkpoint sequence")
    _record_identity(
        record,
        "task_context_checkpoint",
        TASK_CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    )
    canonical_checkpoint_id(record.checkpoint_id)
    if type(record.source_sequence) is not int or record.source_sequence < 0:
        raise TaskRecordError("Task checkpoint source sequence must be nonnegative")
    if record.prior_checkpoint_id is not None:
        canonical_checkpoint_id(record.prior_checkpoint_id)
    if record.accepted_plan_id is not None:
        canonical_plan_id(record.accepted_plan_id)
    if type(record.completed_plan_steps) is not int or record.completed_plan_steps < 0:
        raise TaskRecordError("Task checkpoint completed steps must be nonnegative")
    if record.completion_stage_id is not None:
        canonical_stage_id(record.completion_stage_id)
    if (
        not isinstance(record.unresolved_criterion_indices, tuple)
        or tuple(sorted(set(record.unresolved_criterion_indices)))
        != record.unresolved_criterion_indices
        or any(type(index) is not int or index < 1 for index in record.unresolved_criterion_indices)
    ):
        raise TaskRecordError("Task checkpoint unresolved criteria are invalid")
    if record.latest_reflection_id is not None:
        canonical_reflection_id(record.latest_reflection_id)
    _validate_timestamp(record.created_at, "Task checkpoint created_at")


def _validate_stage_usage(usage: object) -> None:
    if not isinstance(usage, StageUsage):
        raise TaskRecordError("Stage usage is invalid")
    for field, value in usage.__dict__.items():
        if type(value) is not int or value < 0:
            raise TaskRecordError(f"Stage usage {field} must be a nonnegative integer")
    if (
        usage.known_token_invocations + usage.unknown_token_invocations
        != usage.provider_invocations
    ):
        raise TaskRecordError("Stage provider usage counts are inconsistent")
    if not (
        usage.tool_succeeded + usage.tool_unsuccessful
        == usage.tool_dispatched
        <= usage.tool_admitted
        <= usage.tool_requests
    ):
        raise TaskRecordError("Stage tool usage counts are inconsistent")


def _record_timestamp(record: TaskRecord) -> str:
    for name in (
        "configured_at",
        "checked_at",
        "started_at",
        "committed_at",
        "failed_at",
        "proposed_at",
        "accepted_at",
        "verified_at",
        "terminated_at",
        "renamed_at",
        "changed_at",
        "recorded_at",
        "created_at",
    ):
        value = getattr(record, name, None)
        if isinstance(value, str):
            return value
    raise TaskRecordError("task record has no lifecycle timestamp")


def _validate_stage_terminal_identity(started: StageStarted, terminal: StageTerminal) -> None:
    if terminal.stage_id != started.stage_id:
        raise TaskRecordError("Stage terminal ID does not match the active Stage")
    if terminal.stage_number != started.stage_number:
        raise TaskRecordError("Stage terminal number does not match the active Stage")


def _record_identity(record: object, record_type: str, version: int) -> None:
    if getattr(record, "record_type", None) != record_type:
        raise TaskRecordError(f"{record_type} record type is invalid")
    if getattr(record, "schema_version", None) != version:
        raise TaskRecordError(f"unsupported {record_type} schema version")


def _fields(value: dict[str, object], label: str, *specific: str) -> None:
    expected = {"record_type", "schema_version", "sequence", *specific}
    if set(value) != expected:
        raise TaskRecordError(f"{label} has unknown or missing fields")


def _version(value: dict[str, object], expected: int, label: str) -> None:
    if value.get("schema_version") != expected:
        raise TaskRecordError(f"unsupported {label} schema version")


def _criterion_to_dict(criterion: TaskAcceptanceCriterion) -> dict[str, object]:
    canonical_acceptance_criterion(criterion)
    return {
        "argv": list(criterion.argv),
        "cwd": criterion.cwd,
        "description": criterion.description,
        "expected_sha256": criterion.expected_sha256,
        "kind": criterion.kind.value,
        "path": criterion.path,
        "path_type": criterion.path_type.value if criterion.path_type is not None else None,
        "review_paths": list(criterion.review_paths),
        "timeout_seconds": criterion.timeout_seconds,
    }


def _criterion_from_value(value: object) -> TaskAcceptanceCriterion:
    fields = {
        "argv",
        "cwd",
        "description",
        "expected_sha256",
        "kind",
        "path",
        "path_type",
        "review_paths",
        "timeout_seconds",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise TaskRecordError("structured acceptance criterion has unknown or missing fields")
    try:
        kind = AcceptanceCriterionKind(value.get("kind"))
        raw_path_type = value.get("path_type")
        path_type = None if raw_path_type is None else AcceptancePathType(raw_path_type)
    except (TypeError, ValueError):
        raise TaskRecordError("structured acceptance criterion enum is invalid") from None
    argv = value.get("argv")
    review_paths = value.get("review_paths")
    if not isinstance(argv, list) or not isinstance(review_paths, list):
        raise TaskRecordError("structured acceptance criterion arrays are invalid")
    record = TaskAcceptanceCriterion(
        kind=kind,
        description=value.get("description"),
        path=value.get("path"),
        path_type=path_type,
        expected_sha256=value.get("expected_sha256"),
        argv=tuple(argv),
        cwd=value.get("cwd"),
        timeout_seconds=value.get("timeout_seconds"),
        review_paths=tuple(review_paths),
    )
    return canonical_acceptance_criterion(record)


def _criterion_verification_source(
    criterion: TaskAcceptanceCriterion,
) -> AcceptanceVerificationSource:
    if criterion.kind is AcceptanceCriterionKind.HUMAN:
        return AcceptanceVerificationSource.USER
    if criterion.kind is AcceptanceCriterionKind.INDEPENDENT_REVIEWER:
        return AcceptanceVerificationSource.INDEPENDENT_REVIEWER
    return AcceptanceVerificationSource.HOST_CHECK


def _budget_to_dict(budget: TaskBudget) -> dict[str, object]:
    canonical_task_budget(budget)
    return {
        "max_input_tokens": budget.max_input_tokens,
        "max_output_tokens": budget.max_output_tokens,
        "max_provider_invocations": budget.max_provider_invocations,
        "max_stages": budget.max_stages,
        "max_tool_requests": budget.max_tool_requests,
    }


def _budget_from_value(value: object) -> TaskBudget:
    if not isinstance(value, dict) or set(value) != {
        "max_input_tokens",
        "max_output_tokens",
        "max_provider_invocations",
        "max_stages",
        "max_tool_requests",
    }:
        raise TaskRecordError("task budget has unknown or missing fields")
    budget = TaskBudget(
        max_stages=value.get("max_stages"),
        max_provider_invocations=value.get("max_provider_invocations"),
        max_tool_requests=value.get("max_tool_requests"),
        max_input_tokens=value.get("max_input_tokens"),
        max_output_tokens=value.get("max_output_tokens"),
    )
    return canonical_task_budget(budget)


def _usage_to_dict(usage: StageUsage) -> dict[str, int]:
    _validate_stage_usage(usage)
    return dict(usage.__dict__)


def _hook_audit_to_value(ledger: HookAuditLedger, label: str) -> dict[str, object]:
    try:
        return hook_audit_ledger_to_mapping(ledger)
    except ValueError as error:
        raise TaskRecordError(f"invalid {label} Hook audit: {error}") from None


def _hook_audit_from_value(value: object, label: str) -> HookAuditLedger:
    try:
        return hook_audit_ledger_from_mapping(value)
    except ValueError as error:
        raise TaskRecordError(f"invalid {label} Hook audit: {error}") from None


def _validate_hook_audit(
    ledger: HookAuditLedger,
    *,
    supported: bool,
    label: str,
    expected_event: HookEvent,
) -> None:
    if type(ledger) is not HookAuditLedger:
        raise TaskRecordError(f"{label} Hook audit is invalid")
    try:
        ledger.__post_init__()
    except ValueError as error:
        raise TaskRecordError(f"invalid {label} Hook audit: {error}") from None
    if not supported and ledger.entries:
        raise TaskRecordError(f"legacy {label} cannot contain Hook audit")
    if supported and any(entry.event is not expected_event for entry in ledger.entries):
        raise TaskRecordError(f"{label} Hook audit event is invalid")


def _usage_from_value(value: object) -> StageUsage:
    fields = set(StageUsage.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != fields:
        raise TaskRecordError("Stage usage has unknown or missing fields")
    usage = StageUsage(**value)
    _validate_stage_usage(usage)
    return usage


def _simple_record_fields(record: object) -> tuple[int, str, int]:
    return (record.sequence, record.record_type, record.schema_version)


def _canonical_uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TaskRecordError(f"{label} must be a canonical UUID4 string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise TaskRecordError(f"{label} must be a canonical UUID4 string") from None
    if parsed.version != 4 or str(parsed) != value:
        raise TaskRecordError(f"{label} must be a canonical lowercase UUID4 string")
    return value


def _validate_session_id(value: object, label: str) -> None:
    try:
        canonical_session_id(value)
    except SessionRecordError as error:
        raise TaskRecordError(f"invalid {label}: {error}") from None


def _positive_sequence(value: object, label: str) -> None:
    _positive(value, label)


def _positive(value: object, label: str) -> None:
    if type(value) is not int or value < 1:
        raise TaskRecordError(f"{label} must be a positive integer")


def _bounded_positive(value: object, maximum: int, label: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise TaskRecordError(f"{label} must be between 1 and {maximum}")


def _required_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TaskRecordError(f"{label} is invalid")


def _optional_sha256(value: object, label: str) -> None:
    if value is not None:
        _required_sha256(value, label)


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
