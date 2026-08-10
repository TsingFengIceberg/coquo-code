"""Workspace-bound append-only storage for durable Coquo tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from coquo.core.contracts import ToolArguments
from coquo.core.hook_contracts import (
    HookAuditLedger,
    HookAuditObservation,
    bounded_hook_audit_limit,
)
from coquo.core.task_admission import TaskAdmissionProposal, canonical_task_admission_id
from coquo.session_records import workspace_fingerprint
from coquo.session_store import SessionStore, SessionStoreError, SessionTurnEvidence
from coquo.task_records import (
    MAX_TASK_RECORDS,
    MAX_ACCEPTANCE_CRITERIA,
    AcceptanceCheckOutcome,
    AcceptanceCriterionKind,
    AcceptancePathType,
    AcceptanceVerificationSource,
    CompletionProposalSource,
    ReflectionRecommendation,
    StageCommitted,
    StageFailed,
    StageFailureReason,
    StageKind,
    StageStarted,
    StageUsage,
    TaskAcceptanceVerified,
    TaskAcceptanceChecked,
    TaskAcceptanceContract,
    TaskAcceptanceCriterion,
    TaskAdmissionOrigin,
    TaskArchived,
    TaskBudget,
    TaskBlockerCategory,
    TaskBlockerRecorded,
    TaskCompletionProposed,
    TaskConfiguration,
    TaskCompletionPolicy,
    TaskHeader,
    TaskPlanAccepted,
    TaskPlanProposed,
    TaskReflectionRecorded,
    TaskPauseChanged,
    TaskContextCheckpoint,
    TaskRecord,
    TaskRecordError,
    TaskRenamed,
    TaskReplayState,
    TaskScope,
    TaskStatus,
    TaskTerminalOutcome,
    TaskTerminated,
    canonical_acceptance_criteria,
    canonical_task_acceptance_contract,
    canonical_plan_id,
    canonical_reflection_id,
    canonical_checkpoint_id,
    canonical_plan_steps,
    canonical_stage_id,
    canonical_stage_objective,
    canonical_task_budget,
    canonical_task_id,
    canonical_task_name,
    canonical_task_objective,
    default_task_name,
    decode_task_record,
    encode_task_record,
    replay_task_records,
)
from coquo.tools._workspace_paths import (
    WorkspacePathFailure,
    open_parent_directory,
    validate_workspace_path,
)

MAX_TASK_TRANSCRIPT_BYTES = 1024 * 1024
MAX_TASK_DIRECTORY_ENTRIES = 10_000
MAX_PROTECTED_ACCEPTANCE_FILE_BYTES = 1024 * 1024
_EMPTY_STAGE_USAGE = StageUsage(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


class TaskStoreError(RuntimeError):
    """Raised when durable Task persistence cannot proceed safely."""


class TaskCreateCommitError(TaskStoreError):
    """Report whether a failed create made the final Task name visible."""

    def __init__(self, message: str, *, task_visible: bool) -> None:
        self.task_visible = task_visible
        super().__init__(message)


class TaskAppendCommitError(TaskStoreError):
    """Report that a Stage record may be visible with uncertain durability."""

    def __init__(self, message: str, *, record_may_be_visible: bool) -> None:
        self.record_may_be_visible = record_may_be_visible
        super().__init__(message)


@dataclass(frozen=True)
class TaskAdmissionConfiguration:
    """Canonical operator-owned settings for accepting one Task proposal."""

    name: str | None = None
    budget: TaskBudget = TaskBudget()
    completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL
    criteria: tuple[ToolArguments, ...] | None = None

    def __post_init__(self) -> None:
        try:
            if self.name is not None:
                canonical_task_name(self.name)
            canonical_task_budget(self.budget)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        if type(self.completion_policy) is not TaskCompletionPolicy:
            raise TaskStoreError("Task completion policy is invalid")
        if self.criteria is not None:
            if (
                not isinstance(self.criteria, tuple)
                or not 1 <= len(self.criteria) <= MAX_ACCEPTANCE_CRITERIA
                or any(type(item) is not ToolArguments for item in self.criteria)
            ):
                raise TaskStoreError("Task admission criteria are invalid")

    @classmethod
    def from_mapping(cls, value: object | None) -> TaskAdmissionConfiguration:
        if value is None:
            return cls()
        if not isinstance(value, dict) or not set(value) <= {
            "name",
            "budget",
            "completion_policy",
            "criteria",
        }:
            raise TaskStoreError("Task admission configuration has unknown fields")
        name = value.get("name")
        if name is not None and not isinstance(name, str):
            raise TaskStoreError("Task admission name must be text or null")
        raw_budget = value.get("budget", {})
        budget_fields = {
            "max_stages",
            "max_provider_invocations",
            "max_tool_requests",
            "max_input_tokens",
            "max_output_tokens",
        }
        if not isinstance(raw_budget, dict) or not set(raw_budget) <= budget_fields:
            raise TaskStoreError("Task admission budget has unknown fields")
        defaults = TaskBudget()
        budget = TaskBudget(
            max_stages=raw_budget.get("max_stages", defaults.max_stages),
            max_provider_invocations=raw_budget.get(
                "max_provider_invocations", defaults.max_provider_invocations
            ),
            max_tool_requests=raw_budget.get("max_tool_requests", defaults.max_tool_requests),
            max_input_tokens=raw_budget.get("max_input_tokens", defaults.max_input_tokens),
            max_output_tokens=raw_budget.get("max_output_tokens", defaults.max_output_tokens),
        )
        try:
            completion_policy = TaskCompletionPolicy(
                value.get("completion_policy", TaskCompletionPolicy.MANUAL.value)
            )
        except (TypeError, ValueError):
            raise TaskStoreError("Task admission completion policy is invalid") from None
        raw_criteria = value.get("criteria")
        if raw_criteria is None:
            criteria = None
        elif not isinstance(raw_criteria, list):
            raise TaskStoreError("Task admission criteria must be an array or null")
        else:
            try:
                criteria = tuple(ToolArguments.from_mapping(item) for item in raw_criteria)
            except (TypeError, ValueError):
                raise TaskStoreError("Task admission criteria must contain JSON objects") from None
        return cls(name, budget, completion_policy, criteria)

    def as_mapping(self) -> dict[str, object]:
        return {
            "budget": {
                "max_input_tokens": self.budget.max_input_tokens,
                "max_output_tokens": self.budget.max_output_tokens,
                "max_provider_invocations": self.budget.max_provider_invocations,
                "max_stages": self.budget.max_stages,
                "max_tool_requests": self.budget.max_tool_requests,
            },
            "completion_policy": self.completion_policy.value,
            "criteria": (
                None if self.criteria is None else [item.as_mapping() for item in self.criteria]
            ),
            "name": self.name,
        }

    @property
    def sha256(self) -> str:
        return _canonical_digest(b"coquo-task-admission-configuration-v1\0", self.as_mapping())


@dataclass(frozen=True)
class TaskAdmissionAcceptancePreview:
    """One exact no-write Task candidate requiring an explicit confirmation digest."""

    proposal: TaskAdmissionProposal
    name: str
    budget: TaskBudget
    completion_policy: TaskCompletionPolicy
    criteria: tuple[TaskAcceptanceCriterion, ...]
    configuration_sha256: str
    confirmation_sha256: str

    def __post_init__(self) -> None:
        if type(self.proposal) is not TaskAdmissionProposal:
            raise TaskStoreError("Task admission preview proposal is invalid")
        canonical_task_name(self.name)
        canonical_task_budget(self.budget)
        canonical_task_acceptance_contract(self.criteria, self.completion_policy)
        for digest in (self.configuration_sha256, self.confirmation_sha256):
            if not isinstance(digest, str) or len(digest) != 64:
                raise TaskStoreError("Task admission preview digest is invalid")


@dataclass(frozen=True)
class TaskStageInfo:
    """Terminal-safe metadata for one strictly replayed Stage."""

    stage_id: str
    stage_number: int
    objective: str
    started_at: str
    outcome: str
    terminal_at: str | None
    turn_number: int | None
    turn_record_sequence: int | None
    turn_record_sha256: str | None
    failure_reason: StageFailureReason | None
    kind: StageKind = StageKind.EXECUTION
    usage: StageUsage | None = None


@dataclass(frozen=True)
class TaskUsageInfo:
    """Cumulative bounded accounting copied from committed Session Turn evidence."""

    committed_stages: int
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
    unavailable_stages: int


@dataclass(frozen=True)
class TaskPlanInfo:
    plan_id: str
    steps: tuple[str, ...]
    proposed_at: str
    accepted: bool
    completed_steps: int
    predecessor_plan_id: str | None = None
    revision_reason: str | None = None
    reflection_id: str | None = None
    proposal_tool_use_id: str | None = None


@dataclass(frozen=True)
class TaskReflectionInfo:
    reflection_id: str
    stage_id: str
    stage_number: int
    recommendation: ReflectionRecommendation
    summary: str
    next_objective: str | None
    recorded_at: str
    proposal_tool_use_id: str | None = None


@dataclass(frozen=True)
class TaskBlockerInfo:
    stage_id: str
    stage_number: int
    category: TaskBlockerCategory
    summary: str
    proposal_tool_use_id: str
    recorded_at: str


@dataclass(frozen=True)
class TaskCheckpointInfo:
    checkpoint_id: str
    source_sequence: int
    accepted_plan_id: str | None
    completed_plan_steps: int
    completion_stage_id: str | None
    unresolved_criterion_indices: tuple[int, ...]
    latest_reflection_id: str | None
    created_at: str


@dataclass(frozen=True)
class TaskAcceptanceInfo:
    completion_stage_id: str
    criterion_index: int
    evidence: str
    verified_at: str
    source: AcceptanceVerificationSource = AcceptanceVerificationSource.USER


@dataclass(frozen=True)
class TaskAcceptanceCheckInfo:
    completion_stage_id: str
    criterion_index: int
    source: AcceptanceVerificationSource
    outcome: AcceptanceCheckOutcome
    evidence: str
    checked_at: str


@dataclass(frozen=True)
class TaskInfo:
    """Validated metadata and objective for one durable Task."""

    task_id: str
    path: Path
    workspace: str
    workspace_fingerprint: str
    owner_session_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    created_at: str
    scope: TaskScope
    status: TaskStatus
    record_count: int
    stages: tuple[TaskStageInfo, ...]
    name: str = ""
    archived: bool = False
    parent_task_id: str | None = None
    budget: TaskBudget = TaskBudget()
    usage: TaskUsageInfo = TaskUsageInfo(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    budget_exhausted: tuple[str, ...] = ()
    latest_plan: TaskPlanInfo | None = None
    acceptance_verifications: tuple[TaskAcceptanceInfo, ...] = ()
    criteria: tuple[TaskAcceptanceCriterion, ...] = ()
    completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL
    acceptance_checks: tuple[TaskAcceptanceCheckInfo, ...] = ()
    terminal_outcome: TaskTerminalOutcome | None = None
    terminal_reason: str | None = None
    driver_paused: bool = False
    latest_reflection: TaskReflectionInfo | None = None
    latest_checkpoint: TaskCheckpointInfo | None = None
    latest_blocker: TaskBlockerInfo | None = None
    admission_origin: TaskAdmissionOrigin | None = None


def utc_now() -> str:
    """Return the canonical Task timestamp representation."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class TaskStore:
    """Create and strictly inspect workspace-bound Task transcripts."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        stage_uuid_factory: Callable[[], UUID | str] = uuid4,
        plan_uuid_factory: Callable[[], UUID | str] = uuid4,
        reflection_uuid_factory: Callable[[], UUID | str] = uuid4,
        checkpoint_uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise TaskStoreError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise TaskStoreError(
                f"workspace does not exist or is inaccessible: {requested}"
            ) from None
        if not resolved.is_dir():
            raise TaskStoreError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".coquo" / "tasks" / self.workspace_fingerprint
        self._uuid_factory = uuid_factory
        self._stage_uuid_factory = stage_uuid_factory
        self._plan_uuid_factory = plan_uuid_factory
        self._reflection_uuid_factory = reflection_uuid_factory
        self._checkpoint_uuid_factory = checkpoint_uuid_factory
        self._clock = clock

    def create(
        self,
        objective: str,
        *,
        owner_session: str = "latest",
        acceptance_criteria: tuple[str, ...] = (),
        structured_criteria: tuple[dict[str, object], ...] = (),
        completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL,
        name: str | None = None,
        budget: TaskBudget = TaskBudget(),
        parent_task_id: str | None = None,
        _admission_proposal: TaskAdmissionProposal | None = None,
        _admission_turn_record_sequence: int | None = None,
        _admission_configuration_sha256: str | None = None,
        _admission_confirmation_sha256: str | None = None,
    ) -> TaskInfo:
        """Atomically create one ready Task owned by an existing Session."""
        try:
            canonical_objective = canonical_task_objective(objective)
            canonical_human_criteria = canonical_acceptance_criteria(acceptance_criteria)
            contract_criteria = _prepare_acceptance_criteria(
                self.workspace,
                canonical_human_criteria,
                structured_criteria,
                completion_policy,
            )
            canonical_criteria = tuple(item.description for item in contract_criteria)
            canonical_name = canonical_task_name(name or default_task_name(canonical_objective))
            canonical_budget = canonical_task_budget(budget)
            canonical_parent = (
                canonical_task_id(parent_task_id) if parent_task_id is not None else None
            )
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        if canonical_parent is not None:
            self.inspect(canonical_parent)
        try:
            owner = SessionStore(self.workspace).inspect(owner_session)
        except SessionStoreError as error:
            raise TaskStoreError(f"owner Session is invalid or unavailable: {error}") from None
        admission_values = (
            _admission_proposal,
            _admission_turn_record_sequence,
            _admission_configuration_sha256,
            _admission_confirmation_sha256,
        )
        if any(value is not None for value in admission_values) and not all(
            value is not None for value in admission_values
        ):
            raise TaskStoreError("Task admission provenance is incomplete")
        if _admission_turn_record_sequence is not None and (
            type(_admission_turn_record_sequence) is not int or _admission_turn_record_sequence < 1
        ):
            raise TaskStoreError("Task admission source Turn record sequence is invalid")
        task_id = _factory_task_id(self._uuid_factory)
        timestamp = self._clock()
        header = TaskHeader(
            sequence=0,
            task_id=task_id,
            workspace=str(self.workspace),
            workspace_fingerprint=self.workspace_fingerprint,
            owner_session_id=owner.session_id,
            objective=canonical_objective,
            acceptance_criteria=canonical_criteria,
            created_at=timestamp,
        )
        records: list[TaskRecord] = [header]
        if _admission_proposal is not None:
            records.append(
                TaskAdmissionOrigin(
                    sequence=len(records),
                    admission_id=_admission_proposal.admission_id,
                    proposal_sha256=_admission_proposal.proposal_sha256,
                    configuration_sha256=_admission_configuration_sha256,
                    confirmation_sha256=_admission_confirmation_sha256,
                    source_session_id=owner.session_id,
                    source_turn_record_sequence=_admission_turn_record_sequence,
                    proposal_tool_use_id=_admission_proposal.tool_use_id,
                    source_context_id=_admission_proposal.context_id,
                    recorded_at=timestamp,
                )
            )
        configuration = (
            TaskConfiguration(
                sequence=len(records),
                name=canonical_name,
                budget=canonical_budget,
                configured_at=timestamp,
                parent_task_id=canonical_parent,
            )
            if name is not None or canonical_budget != TaskBudget() or canonical_parent is not None
            else None
        )
        if configuration is not None:
            records.append(configuration)
        contract = (
            TaskAcceptanceContract(
                sequence=len(records),
                criteria=contract_criteria,
                completion_policy=completion_policy,
                configured_at=timestamp,
            )
            if structured_criteria or completion_policy is not TaskCompletionPolicy.MANUAL
            else None
        )
        if contract is not None:
            records.append(contract)
        try:
            payload = b"".join(encode_task_record(record) for record in records)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        self._ensure_root()
        path = self.root / f"{task_id}.jsonl"
        _install_task_transcript(path, payload)
        state = self._replay(path, records)
        return _task_info(path, state)

    def create_from_admission(
        self,
        proposal: TaskAdmissionProposal,
        *,
        owner_session: str,
        source_turn_record_sequence: int,
        configuration: TaskAdmissionConfiguration = TaskAdmissionConfiguration(),
        confirmation_sha256: str,
    ) -> TaskInfo:
        """Create one Task carrying immutable provenance from an accepted proposal."""
        preview = self.prepare_admission_acceptance(
            proposal,
            owner_session=owner_session,
            source_turn_record_sequence=source_turn_record_sequence,
            configuration=configuration,
        )
        if confirmation_sha256 != preview.confirmation_sha256:
            raise TaskStoreError("Task admission confirmation does not match the current candidate")
        human_criteria = proposal.acceptance_criteria if configuration.criteria is None else ()
        structured_criteria = (
            ()
            if configuration.criteria is None
            else tuple(item.as_mapping() for item in configuration.criteria)
        )
        return self.create(
            proposal.objective,
            owner_session=owner_session,
            acceptance_criteria=human_criteria,
            structured_criteria=structured_criteria,
            completion_policy=configuration.completion_policy,
            name=configuration.name,
            budget=configuration.budget,
            _admission_proposal=proposal,
            _admission_turn_record_sequence=source_turn_record_sequence,
            _admission_configuration_sha256=configuration.sha256,
            _admission_confirmation_sha256=preview.confirmation_sha256,
        )

    def prepare_admission_acceptance(
        self,
        proposal: TaskAdmissionProposal,
        *,
        owner_session: str,
        source_turn_record_sequence: int,
        configuration: TaskAdmissionConfiguration = TaskAdmissionConfiguration(),
    ) -> TaskAdmissionAcceptancePreview:
        """Validate and derive one exact no-write Task admission candidate."""
        if type(proposal) is not TaskAdmissionProposal:
            raise TaskStoreError("Task admission proposal is invalid")
        if type(configuration) is not TaskAdmissionConfiguration:
            raise TaskStoreError("Task admission configuration is invalid")
        self._validate_admission_source(
            proposal,
            owner_session=owner_session,
            source_turn_record_sequence=source_turn_record_sequence,
        )
        try:
            name = canonical_task_name(configuration.name or default_task_name(proposal.objective))
            budget = canonical_task_budget(configuration.budget)
            human_criteria = proposal.acceptance_criteria if configuration.criteria is None else ()
            criteria = _prepare_acceptance_criteria(
                self.workspace,
                canonical_acceptance_criteria(human_criteria),
                (
                    ()
                    if configuration.criteria is None
                    else tuple(item.as_mapping() for item in configuration.criteria)
                ),
                configuration.completion_policy,
            )
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        candidate = {
            "admission_id": proposal.admission_id,
            "budget": configuration.as_mapping()["budget"],
            "completion_policy": configuration.completion_policy.value,
            "criteria": [_acceptance_criterion_mapping(item) for item in criteria],
            "name": name,
            "objective": proposal.objective,
            "owner_session_id": owner_session,
            "source_turn_record_sequence": source_turn_record_sequence,
        }
        return TaskAdmissionAcceptancePreview(
            proposal,
            name,
            budget,
            configuration.completion_policy,
            criteria,
            configuration.sha256,
            _canonical_digest(b"coquo-task-admission-confirmation-v1\0", candidate),
        )

    def validate_existing_admission_acceptance(
        self,
        task: TaskInfo,
        configuration: TaskAdmissionConfiguration,
        confirmation_sha256: str,
    ) -> None:
        """Require a retry to match the exact configuration stored on the sourced Task."""
        origin = task.admission_origin
        if origin is None or origin.configuration_sha256 != configuration.sha256:
            raise TaskStoreError("existing Task admission configuration does not match this retry")
        if origin.confirmation_sha256 != confirmation_sha256:
            raise TaskStoreError("existing Task admission confirmation does not match this retry")

    def _validate_admission_source(
        self,
        proposal: TaskAdmissionProposal,
        *,
        owner_session: str,
        source_turn_record_sequence: int,
    ) -> None:
        try:
            sources = SessionStore(self.workspace).task_admissions(owner_session)
        except SessionStoreError as error:
            raise TaskStoreError(f"Task admission source Session is unavailable: {error}") from None
        source = next(
            (item for item in sources if item.proposal.admission_id == proposal.admission_id),
            None,
        )
        if (
            source is None
            or source.proposal != proposal
            or source.turn_record_sequence != source_turn_record_sequence
        ):
            raise TaskStoreError("Task admission provenance does not match its source Session Turn")

    def find_by_admission(self, admission_id: str) -> TaskInfo | None:
        """Find at most one Task created from an exact admission proposal."""
        try:
            canonical = canonical_task_admission_id(admission_id)
        except ValueError as error:
            raise TaskStoreError(str(error)) from None
        matches = tuple(
            task
            for task in self.list()
            if task.admission_origin is not None and task.admission_origin.admission_id == canonical
        )
        if len(matches) > 1:
            raise TaskStoreError("Task admission provenance is duplicated")
        return matches[0] if matches else None

    def derive(
        self,
        parent_task_id: str,
        objective: str,
        *,
        owner_session: str = "latest",
        acceptance_criteria: tuple[str, ...] = (),
        structured_criteria: tuple[dict[str, object], ...] = (),
        completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL,
        name: str | None = None,
        budget: TaskBudget = TaskBudget(),
    ) -> TaskInfo:
        """Create a new independent Task with one immutable parent provenance link."""
        parent = self.inspect(parent_task_id)
        return self.create(
            objective,
            owner_session=owner_session,
            acceptance_criteria=acceptance_criteria,
            structured_criteria=structured_criteria,
            completion_policy=completion_policy,
            name=name,
            budget=budget,
            parent_task_id=parent.task_id,
        )

    def inspect(self, task_id: str) -> TaskInfo:
        """Strictly replay one exact Task ID without creating or repairing state."""
        canonical = _store_task_id(task_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        state = self._load_state(path)
        return _task_info(
            path,
            state,
            active_stage=state.active_stage is not None and _task_writer_is_active(path),
        )

    def hook_evaluations(
        self,
        task_id: str,
        limit: int = 20,
    ) -> tuple[HookAuditObservation, ...]:
        """Project recent content-free Hook evaluations from one strict Task replay."""
        try:
            bounded_hook_audit_limit(limit)
        except ValueError as error:
            raise TaskStoreError(str(error)) from None
        canonical = _store_task_id(task_id)
        self._validate_existing_root()
        state = self._load_state(self.root / f"{canonical}.jsonl")
        observations = tuple(
            HookAuditObservation(record.record_type, record.sequence, entry)
            for record in state.records
            if isinstance(
                record,
                (
                    StageStarted,
                    StageCommitted,
                    StageFailed,
                    TaskBlockerRecorded,
                    TaskTerminated,
                ),
            )
            for entry in record.hook_audit.entries
        )
        return observations[-limit:]

    def open(self, task_id: str) -> TaskWriter:
        """Take one exclusive foreground writer lease for a durable Task."""
        canonical = _store_task_id(task_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        descriptor = _open_task_transcript(path, writable=True)
        key = str(path)
        claimed = False
        locked = False
        try:
            _claim_active_writer(key)
            claimed = True
            _lock_descriptor(descriptor)
            locked = True
            data = _read_task_descriptor(descriptor, path)
            state = self._decode_state(path, data)
            return TaskWriter(self, path, descriptor, state, key)
        except BaseException:
            if locked:
                _unlock_descriptor(descriptor)
            if claimed:
                _release_active_writer(key)
            os.close(descriptor)
            raise

    def list(self) -> tuple[TaskInfo, ...]:
        """Strictly list bounded Task transcripts without creating local state."""
        if not self.root.exists() and not self.root.is_symlink():
            _validate_optional_parent_chain(self.workspace, self.root.parent)
            return ()
        self._validate_existing_root()
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            raise TaskStoreError("task directory is inaccessible") from None
        if len(entries) > MAX_TASK_DIRECTORY_ENTRIES:
            raise TaskStoreError(f"task directory exceeds {MAX_TASK_DIRECTORY_ENTRIES} entries")
        paths: list[Path] = []
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            path = self.root / entry.name
            _task_id_from_path(path)
            paths.append(path)
        tasks = tuple(self._inspect_path(path) for path in paths)
        return tuple(sorted(tasks, key=lambda task: (task.created_at, task.task_id), reverse=True))

    def _inspect_path(self, path: Path) -> TaskInfo:
        state = self._load_state(path)
        return _task_info(
            path,
            state,
            active_stage=state.active_stage is not None and _task_writer_is_active(path),
        )

    def _load_state(self, path: Path) -> TaskReplayState:
        _task_id_from_path(path)
        data = _read_task_transcript(path)
        return self._decode_state(path, data)

    def _decode_state(self, path: Path, data: bytes) -> TaskReplayState:
        if not data.endswith(b"\n"):
            raise TaskStoreError("task transcript does not end at a durable record boundary")
        lines = data.splitlines()
        if len(lines) > MAX_TASK_RECORDS:
            raise TaskStoreError(f"task transcript exceeds {MAX_TASK_RECORDS} records")
        try:
            records = [decode_task_record(line) for line in lines]
        except TaskRecordError as error:
            raise TaskStoreError(f"invalid task transcript {path}: {error}") from None
        return self._replay(path, records)

    def _replay(self, path: Path, records: list[TaskRecord]) -> TaskReplayState:
        try:
            return replay_task_records(
                records,
                expected_workspace=str(self.workspace),
                expected_workspace_fingerprint=self.workspace_fingerprint,
                expected_task_id=_task_id_from_path(path),
                expected_file_name=path.name,
            )
        except TaskRecordError as error:
            raise TaskStoreError(f"invalid task transcript {path}: {error}") from None

    def _ensure_root(self) -> None:
        _ensure_directory(self.workspace / ".coquo", boundary=self.workspace)
        _ensure_directory(self.workspace / ".coquo" / "tasks", boundary=self.workspace)
        _ensure_directory(self.root, boundary=self.workspace)

    def _validate_existing_root(self) -> None:
        _validate_directory(self.workspace / ".coquo", self.workspace)
        _validate_directory(self.workspace / ".coquo" / "tasks", self.workspace)
        _validate_directory(self.root, self.workspace)


_ACTIVE_TASK_WRITERS: set[str] = set()
_ACTIVE_TASK_WRITERS_GUARD = Lock()


class TaskWriter:
    """Exclusive append-only writer for one foreground Task Stage."""

    def __init__(
        self,
        store: TaskStore,
        path: Path,
        descriptor: int,
        state: TaskReplayState,
        active_key: str,
    ) -> None:
        self._store = store
        self.path = path
        self._descriptor = descriptor
        self._state = state
        self._active_key = active_key
        self._released = False
        self._uncertain = False

    @property
    def state(self) -> TaskReplayState:
        return self._state

    @property
    def info(self) -> TaskInfo:
        return _task_info(
            self.path,
            self._state,
            active_stage=self._state.active_stage is not None,
        )

    def start_stage(
        self,
        objective: str,
        *,
        kind: StageKind = StageKind.EXECUTION,
        session_record_sequence_before: int | None = None,
        session_turn_count_before: int | None = None,
        prompt_sha256: str | None = None,
        hook_audit: HookAuditLedger = HookAuditLedger(),
    ) -> StageStarted:
        """Durably start one bounded Stage before any provider work begins."""
        self._ensure_writable()
        if self._state.active_stage is not None:
            raise TaskStoreError("Task already has an unresolved Stage")
        if self._state.terminal is not None:
            raise TaskStoreError("Task is terminal and cannot start another Stage")
        exhausted = _budget_exhaustion(self._state)
        if exhausted:
            raise TaskStoreError("Task cumulative budget is exhausted: " + ", ".join(exhausted))
        try:
            canonical_objective = canonical_stage_objective(objective)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        stage_id = _factory_stage_id(self._store._stage_uuid_factory)
        record = StageStarted(
            sequence=self._state.next_sequence,
            stage_id=stage_id,
            stage_number=self._state.next_stage_number,
            session_id=self._state.header.owner_session_id,
            objective=canonical_objective,
            started_at=self._store._clock(),
            kind=kind,
            session_record_sequence_before=session_record_sequence_before,
            session_turn_count_before=session_turn_count_before,
            prompt_sha256=prompt_sha256,
            hook_audit=hook_audit,
        )
        self._append(record)
        return record

    def commit_stage(
        self,
        turn_record_sequence: int,
        *,
        hook_audit: HookAuditLedger = HookAuditLedger(),
    ) -> StageCommitted:
        """Link the active Stage to one independently verified committed Session Turn."""
        self._ensure_writable()
        active = self._require_active_stage()
        try:
            evidence = SessionStore(self._store.workspace).turn_evidence(
                active.session_id,
                turn_record_sequence,
            )
        except SessionStoreError as error:
            raise TaskStoreError(f"Session Turn evidence is invalid: {error}") from None
        record = _committed_stage_record(
            self._state.next_sequence,
            active,
            evidence,
            self._store._clock(),
            hook_audit,
        )
        self._append(record)
        return record

    def recover_stage(
        self,
        *,
        committed_hook_audit: HookAuditLedger = HookAuditLedger(),
        failed_hook_audit: HookAuditLedger = HookAuditLedger(),
    ) -> StageCommitted | StageFailed:
        """Reconcile one interrupted Stage from exact Session baseline and prompt evidence."""
        self._ensure_writable()
        active = self._require_active_stage()
        if active.session_record_sequence_before is None or active.prompt_sha256 is None:
            return self.fail_stage(
                StageFailureReason.INTERRUPTED,
                usage=None,
                hook_audit=failed_hook_audit,
            )
        try:
            matches = SessionStore(self._store.workspace).find_turn_evidence(
                active.session_id,
                after_record_sequence=active.session_record_sequence_before,
                user_message_sha256=active.prompt_sha256,
            )
        except SessionStoreError as error:
            raise TaskStoreError(f"Session Turn recovery evidence is invalid: {error}") from None
        if not matches:
            return self.fail_stage(
                StageFailureReason.INTERRUPTED,
                usage=None,
                hook_audit=failed_hook_audit,
            )
        if len(matches) != 1:
            raise TaskStoreError("interrupted Stage has ambiguous committed Turn evidence")
        return self.commit_stage(
            matches[0].record_sequence,
            hook_audit=committed_hook_audit,
        )

    def fail_stage(
        self,
        reason: StageFailureReason,
        *,
        usage: StageUsage | None = _EMPTY_STAGE_USAGE,
        hook_audit: HookAuditLedger = HookAuditLedger(),
    ) -> StageFailed:
        """Durably terminate the active Stage without claiming a committed Turn."""
        self._ensure_writable()
        active = self._require_active_stage()
        if type(reason) is not StageFailureReason:
            raise TaskStoreError("Stage failure reason is invalid")
        record = StageFailed(
            sequence=self._state.next_sequence,
            stage_id=active.stage_id,
            stage_number=active.stage_number,
            reason=reason,
            failed_at=self._store._clock(),
            usage=usage,
            hook_audit=hook_audit,
        )
        self._append(record)
        return record

    def propose_plan(
        self,
        steps: tuple[str, ...],
        *,
        revision_reason: str | None = None,
        reflection_id: str | None = None,
        proposal_tool_use_id: str | None = None,
    ) -> TaskPlanProposed:
        """Persist a parsed model plan only after its planning Stage committed."""
        self._ensure_writable()
        latest = self._state.stages[-1] if self._state.stages else None
        if (
            latest is None
            or not isinstance(latest.terminal, StageCommitted)
            or latest.started.kind is not StageKind.PLANNING
        ):
            raise TaskStoreError("Task plan requires a latest committed planning Stage")
        if self._state.latest_plan is not None and (
            self._state.latest_plan.stage_id == latest.started.stage_id
        ):
            if (
                proposal_tool_use_id is not None
                and self._state.latest_plan.proposal_tool_use_id == proposal_tool_use_id
            ):
                try:
                    canonical_steps = canonical_plan_steps(steps)
                except TaskRecordError as error:
                    raise TaskStoreError(str(error)) from None
                previous_plan = (
                    self._state.plan_proposals[-2] if len(self._state.plan_proposals) >= 2 else None
                )
                expected_reason = (
                    revision_reason or "user-requested-replan"
                    if previous_plan is not None
                    else None
                )
                if (
                    self._state.latest_plan.steps != canonical_steps
                    or self._state.latest_plan.revision_reason != expected_reason
                    or self._state.latest_plan.reflection_id != reflection_id
                ):
                    raise TaskStoreError("replayed Task plan proposal does not match its tool call")
                return self._state.latest_plan
            raise TaskStoreError("latest planning Stage already has a plan proposal")
        try:
            canonical_steps = canonical_plan_steps(steps)
            plan_id = _factory_plan_id(self._store._plan_uuid_factory)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        remaining_stages = self._state.budget.max_stages - len(self._state.stages)
        if len(canonical_steps) > remaining_stages:
            raise TaskStoreError(
                "Task plan exceeds the remaining cumulative Stage budget: "
                f"{len(canonical_steps)} steps proposed, {remaining_stages} available"
            )
        record = TaskPlanProposed(
            sequence=self._state.next_sequence,
            plan_id=plan_id,
            stage_id=latest.started.stage_id,
            stage_number=latest.started.stage_number,
            steps=canonical_steps,
            proposed_at=self._store._clock(),
            predecessor_plan_id=(
                self._state.latest_plan.plan_id if self._state.latest_plan is not None else None
            ),
            revision_reason=(revision_reason if self._state.latest_plan is not None else None)
            or ("user-requested-replan" if self._state.latest_plan is not None else None),
            reflection_id=reflection_id,
            proposal_tool_use_id=proposal_tool_use_id,
        )
        self._append(record)
        return record

    def accept_plan(self) -> TaskPlanAccepted:
        self._ensure_writable()
        plan = self._state.latest_plan
        if plan is None:
            raise TaskStoreError("Task has no plan proposal to accept")
        if self._state.accepted_plan_id == plan.plan_id:
            raise TaskStoreError("latest Task plan is already accepted")
        record = TaskPlanAccepted(
            sequence=self._state.next_sequence,
            plan_id=plan.plan_id,
            accepted_at=self._store._clock(),
        )
        self._append(record)
        return record

    def propose_completion(
        self, *, proposal_tool_use_id: str | None = None
    ) -> TaskCompletionProposed:
        self._ensure_writable()
        latest = self._state.stages[-1] if self._state.stages else None
        if (
            latest is None
            or not isinstance(latest.terminal, StageCommitted)
            or latest.started.kind not in {StageKind.EXECUTION, StageKind.CORRECTION}
        ):
            raise TaskStoreError("Task completion requires a committed execution Stage")
        current = self._state.current_completion_proposal
        if current is not None and current.stage_id == latest.started.stage_id:
            if (
                proposal_tool_use_id is not None
                and current.proposal_tool_use_id == proposal_tool_use_id
            ):
                return current
            raise TaskStoreError("latest execution Stage already proposed completion")
        record = TaskCompletionProposed(
            sequence=self._state.next_sequence,
            stage_id=latest.started.stage_id,
            stage_number=latest.started.stage_number,
            source=CompletionProposalSource.MODEL,
            proposed_at=self._store._clock(),
            proposal_tool_use_id=proposal_tool_use_id,
        )
        self._append(record)
        return record

    def record_reflection(
        self,
        recommendation: ReflectionRecommendation,
        summary: str,
        next_objective: str | None,
        *,
        proposal_tool_use_id: str | None = None,
    ) -> TaskReflectionRecorded:
        """Persist one parsed recommendation after its no-tools reflection Stage commits."""
        self._ensure_writable()
        latest = self._state.stages[-1] if self._state.stages else None
        if (
            latest is None
            or not isinstance(latest.terminal, StageCommitted)
            or latest.started.kind is not StageKind.REFLECTION
        ):
            raise TaskStoreError("Task reflection requires a committed reflection Stage")
        if (
            self._state.reflections
            and self._state.reflections[-1].stage_id == latest.started.stage_id
        ):
            if (
                proposal_tool_use_id is not None
                and self._state.reflections[-1].proposal_tool_use_id == proposal_tool_use_id
            ):
                current = self._state.reflections[-1]
                if (
                    current.recommendation is not recommendation
                    or current.summary != summary
                    or current.next_objective != next_objective
                ):
                    raise TaskStoreError("replayed Task reflection does not match its tool call")
                return self._state.reflections[-1]
            raise TaskStoreError("latest reflection Stage is already recorded")
        record = TaskReflectionRecorded(
            sequence=self._state.next_sequence,
            reflection_id=_factory_reflection_id(self._store._reflection_uuid_factory),
            stage_id=latest.started.stage_id,
            stage_number=latest.started.stage_number,
            recommendation=recommendation,
            summary=summary,
            next_objective=next_objective,
            recorded_at=self._store._clock(),
            proposal_tool_use_id=proposal_tool_use_id,
        )
        self._append(record)
        return record

    def record_blocker(
        self,
        category: TaskBlockerCategory,
        summary: str,
        *,
        proposal_tool_use_id: str,
        hook_audit: HookAuditLedger = HookAuditLedger(),
    ) -> TaskBlockerRecorded:
        """Persist one model blocker after its owning Stage committed."""
        self._ensure_writable()
        latest = self._state.stages[-1] if self._state.stages else None
        if latest is None or not isinstance(latest.terminal, StageCommitted):
            raise TaskStoreError("Task blocker requires a committed Stage")
        if self._state.latest_blocker is not None and (
            self._state.latest_blocker.stage_id == latest.started.stage_id
        ):
            if self._state.latest_blocker.proposal_tool_use_id == proposal_tool_use_id:
                current = self._state.latest_blocker
                if current.category is not category or current.summary != summary:
                    raise TaskStoreError("replayed Task blocker does not match its tool call")
                return self._state.latest_blocker
            raise TaskStoreError("latest Task Stage already has a blocker")
        record = TaskBlockerRecorded(
            sequence=self._state.next_sequence,
            stage_id=latest.started.stage_id,
            stage_number=latest.started.stage_number,
            category=category,
            summary=summary,
            proposal_tool_use_id=proposal_tool_use_id,
            recorded_at=self._store._clock(),
            hook_audit=hook_audit,
        )
        self._append(record)
        return record

    def set_paused(self, paused: bool, reason: str | None = None) -> TaskPauseChanged:
        """Durably change only automatic foreground-driver admission."""
        self._ensure_writable()
        if self._state.active_stage is not None:
            raise TaskStoreError("Task driver pause cannot change while a Stage is active")
        if self._state.terminal is not None:
            raise TaskStoreError("terminal Task driver pause cannot change")
        if type(paused) is not bool or paused is self._state.driver_paused:
            raise TaskStoreError("Task driver pause state is unchanged")
        record = TaskPauseChanged(
            sequence=self._state.next_sequence,
            paused=paused,
            reason=reason,
            changed_at=self._store._clock(),
        )
        self._append(record)
        return record

    def create_context_checkpoint(self) -> TaskContextCheckpoint:
        """Append one deterministic bounded snapshot of current derived Task state."""
        self._ensure_writable()
        if self._state.active_stage is not None:
            raise TaskStoreError("Task checkpoint cannot be created while a Stage is active")
        if (
            self._state.latest_checkpoint is not None
            and self._state.latest_checkpoint.sequence == self._state.next_sequence - 1
        ):
            raise TaskStoreError("Task context has not advanced since the latest checkpoint")
        plan = _task_plan_info(self._state)
        proposal = self._state.current_completion_proposal
        unresolved = tuple(
            index
            for index in range(1, len(self._state.criteria) + 1)
            if index not in self._state.verified_criteria
        )
        record = TaskContextCheckpoint(
            sequence=self._state.next_sequence,
            checkpoint_id=_factory_checkpoint_id(self._store._checkpoint_uuid_factory),
            source_sequence=self._state.next_sequence - 1,
            prior_checkpoint_id=(
                self._state.latest_checkpoint.checkpoint_id
                if self._state.latest_checkpoint is not None
                else None
            ),
            accepted_plan_id=(plan.plan_id if plan is not None and plan.accepted else None),
            completed_plan_steps=(
                plan.completed_steps if plan is not None and plan.accepted else 0
            ),
            completion_stage_id=proposal.stage_id if proposal is not None else None,
            unresolved_criterion_indices=unresolved,
            latest_reflection_id=(
                self._state.latest_reflection.reflection_id
                if self._state.latest_reflection is not None
                else None
            ),
            created_at=self._store._clock(),
        )
        self._append(record)
        return record

    def verify_acceptance(
        self,
        criterion_index: int,
        evidence: str,
        *,
        source: AcceptanceVerificationSource = AcceptanceVerificationSource.USER,
    ) -> TaskAcceptanceVerified:
        self._ensure_writable()
        proposal = self._state.current_completion_proposal
        if proposal is None:
            raise TaskStoreError(
                "Task acceptance verification requires the current completion proposal"
            )
        if not 1 <= criterion_index <= len(self._state.criteria):
            raise TaskStoreError("Task acceptance verification index is outside the contract")
        criterion = self._state.criteria[criterion_index - 1]
        expected_source = _criterion_source(criterion)
        if source is not expected_source:
            raise TaskStoreError(
                f"Task acceptance criterion requires {expected_source.value} verification"
            )
        record = TaskAcceptanceVerified(
            sequence=self._state.next_sequence,
            completion_stage_id=proposal.stage_id,
            criterion_index=criterion_index,
            evidence=evidence,
            source=source,
            verified_at=self._store._clock(),
        )
        self._append(record)
        return record

    def record_acceptance_check(
        self,
        criterion_index: int,
        source: AcceptanceVerificationSource,
        outcome: AcceptanceCheckOutcome,
        evidence: str,
    ) -> TaskAcceptanceChecked:
        self._ensure_writable()
        proposal = self._state.current_completion_proposal
        if proposal is None:
            raise TaskStoreError("Task acceptance check requires the current completion proposal")
        if not 1 <= criterion_index <= len(self._state.criteria):
            raise TaskStoreError("Task acceptance check index is outside the contract")
        criterion = self._state.criteria[criterion_index - 1]
        if (
            source is not _criterion_source(criterion)
            or source is AcceptanceVerificationSource.USER
        ):
            raise TaskStoreError("Task acceptance check source does not match criterion")
        record = TaskAcceptanceChecked(
            sequence=self._state.next_sequence,
            completion_stage_id=proposal.stage_id,
            criterion_index=criterion_index,
            source=source,
            outcome=outcome,
            evidence=evidence,
            checked_at=self._store._clock(),
        )
        self._append(record)
        return record

    def terminate(
        self,
        outcome: TaskTerminalOutcome,
        reason: str | None = None,
        *,
        hook_audit: HookAuditLedger = HookAuditLedger(),
        active_stage_hook_audit: HookAuditLedger = HookAuditLedger(),
    ) -> TaskTerminated:
        self._ensure_writable()
        if self._state.active_stage is not None:
            if outcome is TaskTerminalOutcome.CANCELLED:
                self.fail_stage(
                    StageFailureReason.CANCELLED,
                    usage=None,
                    hook_audit=active_stage_hook_audit,
                )
            elif outcome is TaskTerminalOutcome.FAILED:
                self.fail_stage(
                    StageFailureReason.INTERRUPTED,
                    usage=None,
                    hook_audit=active_stage_hook_audit,
                )
            else:
                raise TaskStoreError("active Stage must terminate before the Task")
        record = TaskTerminated(
            sequence=self._state.next_sequence,
            outcome=outcome,
            reason=reason,
            terminated_at=self._store._clock(),
            hook_audit=hook_audit,
        )
        self._append(record)
        return record

    def rename(self, name: str) -> TaskRenamed:
        self._ensure_writable()
        record = TaskRenamed(
            sequence=self._state.next_sequence,
            name=name,
            renamed_at=self._store._clock(),
        )
        self._append(record)
        return record

    def set_archived(self, archived: bool) -> TaskArchived:
        self._ensure_writable()
        if type(archived) is not bool:
            raise TaskStoreError("Task archived flag is invalid")
        record = TaskArchived(
            sequence=self._state.next_sequence,
            archived=archived,
            changed_at=self._store._clock(),
        )
        self._append(record)
        return record

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            _unlock_descriptor(self._descriptor)
        finally:
            os.close(self._descriptor)
            _release_active_writer(self._active_key)

    def __enter__(self) -> TaskWriter:
        self._ensure_writable()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def _append(self, record: TaskRecord) -> None:
        try:
            candidate = self._store._replay(self.path, [*self._state.records, record])
            _append_task_record_descriptor(self._descriptor, self.path, record)
        except TaskAppendCommitError:
            self._uncertain = True
            raise
        self._state = candidate

    def _require_active_stage(self) -> StageStarted:
        active = self._state.active_stage
        if active is None:
            raise TaskStoreError("Task has no active Stage")
        return active

    def _ensure_writable(self) -> None:
        if self._released:
            raise TaskStoreError("Task writer is released")
        if self._uncertain:
            raise TaskStoreError(
                "Task writer durability is uncertain; release and inspect before continuing"
            )


def _prepare_acceptance_criteria(
    workspace: Path,
    human_criteria: tuple[str, ...],
    structured_specs: tuple[dict[str, object], ...],
    completion_policy: TaskCompletionPolicy,
) -> tuple[TaskAcceptanceCriterion, ...]:
    if not isinstance(structured_specs, tuple) or any(
        not isinstance(spec, dict) for spec in structured_specs
    ):
        raise TaskStoreError("structured acceptance criteria must be JSON objects")
    if type(completion_policy) is not TaskCompletionPolicy:
        raise TaskStoreError("Task completion policy is invalid")
    criteria = [
        TaskAcceptanceCriterion(AcceptanceCriterionKind.HUMAN, description)
        for description in human_criteria
    ]
    try:
        for spec in structured_specs:
            criteria.append(_prepare_acceptance_criterion(workspace, spec))
        return canonical_task_acceptance_contract(tuple(criteria), completion_policy)
    except (TaskRecordError, WorkspacePathFailure) as error:
        raise TaskStoreError(str(error)) from None


def _prepare_acceptance_criterion(
    workspace: Path,
    spec: dict[str, object],
) -> TaskAcceptanceCriterion:
    kind_value = spec.get("kind")
    description = spec.get("description")
    try:
        kind = AcceptanceCriterionKind(kind_value)
    except (TypeError, ValueError):
        raise TaskRecordError("structured acceptance criterion kind is invalid") from None
    if kind is AcceptanceCriterionKind.HUMAN:
        _criterion_spec_fields(spec, {"kind", "description"})
        return TaskAcceptanceCriterion(kind, description)
    if kind is AcceptanceCriterionKind.PATH_EXISTS:
        _criterion_spec_fields(spec, {"kind", "description", "path", "path_type"})
        try:
            path_type = AcceptancePathType(spec.get("path_type"))
        except (TypeError, ValueError):
            raise TaskRecordError("path-exists criterion path_type is invalid") from None
        return TaskAcceptanceCriterion(
            kind,
            description,
            path=spec.get("path"),
            path_type=path_type,
        )
    if kind is AcceptanceCriterionKind.PATH_UNCHANGED:
        _criterion_spec_fields(spec, {"kind", "description", "path"})
        path = spec.get("path")
        if not isinstance(path, str):
            raise TaskRecordError("path-unchanged criterion path is invalid")
        digest = _protected_file_sha256(workspace, path)
        return TaskAcceptanceCriterion(
            kind,
            description,
            path=path,
            path_type=AcceptancePathType.FILE,
            expected_sha256=digest,
        )
    if kind is AcceptanceCriterionKind.COMMAND_SUCCEEDS:
        _criterion_spec_fields(
            spec,
            {"kind", "description", "argv", "cwd", "timeout_seconds"},
        )
        argv = spec.get("argv")
        if not isinstance(argv, list):
            raise TaskRecordError("command-succeeds criterion argv must be an array")
        return TaskAcceptanceCriterion(
            kind,
            description,
            argv=tuple(argv),
            cwd=spec.get("cwd"),
            timeout_seconds=spec.get("timeout_seconds"),
        )
    if kind is AcceptanceCriterionKind.ACTION_AUDIT_CERTAIN:
        _criterion_spec_fields(spec, {"kind", "description"})
        return TaskAcceptanceCriterion(kind, description)
    _criterion_spec_fields(spec, {"kind", "description", "paths"})
    paths = spec.get("paths")
    if not isinstance(paths, list):
        raise TaskRecordError("independent-reviewer criterion paths must be an array")
    return TaskAcceptanceCriterion(kind, description, review_paths=tuple(paths))


def _criterion_spec_fields(spec: dict[str, object], expected: set[str]) -> None:
    if set(spec) != expected:
        raise TaskRecordError("structured acceptance criterion has unknown or missing fields")


def _acceptance_criterion_mapping(criterion: TaskAcceptanceCriterion) -> dict[str, object]:
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


def _canonical_digest(prefix: bytes, value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(prefix + payload).hexdigest()


def _protected_file_sha256(workspace: Path, relative_path: str) -> str:
    parts = validate_workspace_path(
        relative_path,
        tool_name="task_acceptance",
        allow_root=False,
    )
    parent, name = open_parent_directory(
        workspace,
        parts,
        tool_name="task_acceptance",
    )
    descriptor: int | None = None
    try:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError:
            raise TaskRecordError("protected acceptance path is unavailable") from None
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise TaskRecordError("protected acceptance path must be a regular file")
        if info.st_size > MAX_PROTECTED_ACCEPTANCE_FILE_BYTES:
            raise TaskRecordError(
                f"protected acceptance file exceeds {MAX_PROTECTED_ACCEPTANCE_FILE_BYTES} bytes"
            )
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent,
            )
        except OSError:
            raise TaskRecordError("protected acceptance path changed while being opened") from None
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != info.st_dev
            or opened.st_ino != info.st_ino
        ):
            raise TaskRecordError("protected acceptance path changed while being opened")
        content = bytearray()
        while len(content) <= MAX_PROTECTED_ACCEPTANCE_FILE_BYTES:
            chunk = os.read(
                descriptor, min(64 * 1024, MAX_PROTECTED_ACCEPTANCE_FILE_BYTES + 1 - len(content))
            )
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > MAX_PROTECTED_ACCEPTANCE_FILE_BYTES:
            raise TaskRecordError(
                f"protected acceptance file exceeds {MAX_PROTECTED_ACCEPTANCE_FILE_BYTES} bytes"
            )
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise TaskRecordError("protected acceptance path changed while being read")
        visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if visible.st_dev != opened.st_dev or visible.st_ino != opened.st_ino:
            raise TaskRecordError("protected acceptance path changed while being read")
        return hashlib.sha256(content).hexdigest()
    except OSError:
        raise TaskRecordError("protected acceptance path became unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _criterion_source(
    criterion: TaskAcceptanceCriterion,
) -> AcceptanceVerificationSource:
    if criterion.kind is AcceptanceCriterionKind.HUMAN:
        return AcceptanceVerificationSource.USER
    if criterion.kind is AcceptanceCriterionKind.INDEPENDENT_REVIEWER:
        return AcceptanceVerificationSource.INDEPENDENT_REVIEWER
    return AcceptanceVerificationSource.HOST_CHECK


def _factory_task_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_task_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"task ID factory returned an invalid value: {error}") from None


def _factory_stage_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_stage_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"Stage ID factory returned an invalid value: {error}") from None


def _factory_plan_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_plan_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"plan ID factory returned an invalid value: {error}") from None


def _factory_reflection_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_reflection_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"reflection ID factory returned an invalid value: {error}") from None


def _factory_checkpoint_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_checkpoint_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"checkpoint ID factory returned an invalid value: {error}") from None


def _store_task_id(value: object) -> str:
    try:
        return canonical_task_id(value)
    except TaskRecordError as error:
        raise TaskStoreError(str(error)) from None


def _task_id_from_path(path: Path) -> str:
    if path.suffix != ".jsonl":
        raise TaskStoreError("task transcript file name must end in .jsonl")
    try:
        return canonical_task_id(path.stem)
    except TaskRecordError as error:
        raise TaskStoreError(f"invalid task transcript file name: {error}") from None


def _task_info(
    path: Path,
    state: TaskReplayState,
    *,
    active_stage: bool = False,
) -> TaskInfo:
    header = state.header
    stages: list[TaskStageInfo] = []
    for stage in state.stages:
        terminal = stage.terminal
        if terminal is None:
            outcome = (
                "stage-in-progress" if active_stage and stage is state.stages[-1] else "interrupted"
            )
            terminal_at = None
            turn_number = None
            turn_record_sequence = None
            turn_record_sha256 = None
            failure_reason = None
        elif isinstance(terminal, StageCommitted):
            outcome = "committed"
            terminal_at = terminal.committed_at
            turn_number = terminal.turn_number
            turn_record_sequence = terminal.turn_record_sequence
            turn_record_sha256 = terminal.turn_record_sha256
            failure_reason = None
        else:
            outcome = "failed"
            terminal_at = terminal.failed_at
            turn_number = None
            turn_record_sequence = None
            turn_record_sha256 = None
            failure_reason = terminal.reason
        stages.append(
            TaskStageInfo(
                stage_id=stage.started.stage_id,
                stage_number=stage.started.stage_number,
                objective=stage.started.objective,
                started_at=stage.started.started_at,
                outcome=outcome,
                terminal_at=terminal_at,
                turn_number=turn_number,
                turn_record_sequence=turn_record_sequence,
                turn_record_sha256=turn_record_sha256,
                failure_reason=failure_reason,
                kind=stage.started.kind,
                usage=(
                    terminal.usage if isinstance(terminal, (StageCommitted, StageFailed)) else None
                ),
            )
        )
    status = TaskStatus.STAGE_IN_PROGRESS if active_stage else state.status
    usage = _task_usage(state)
    plan = _task_plan_info(state)
    terminal = state.terminal
    current_checks: dict[int, TaskAcceptanceChecked] = {}
    if state.current_completion_proposal is not None:
        for record in state.acceptance_checks:
            if record.completion_stage_id == state.current_completion_proposal.stage_id:
                current_checks[record.criterion_index] = record
    return TaskInfo(
        task_id=header.task_id,
        path=path,
        workspace=header.workspace,
        workspace_fingerprint=header.workspace_fingerprint,
        owner_session_id=header.owner_session_id,
        objective=header.objective,
        acceptance_criteria=header.acceptance_criteria,
        created_at=header.created_at,
        scope=header.scope,
        status=status,
        record_count=len(state.records),
        stages=tuple(stages),
        name=state.name,
        archived=state.archived,
        parent_task_id=state.parent_task_id,
        budget=state.budget,
        usage=usage,
        budget_exhausted=_budget_exhaustion(state),
        latest_plan=plan,
        acceptance_verifications=tuple(
            TaskAcceptanceInfo(
                record.completion_stage_id,
                record.criterion_index,
                record.evidence,
                record.verified_at,
                record.source,
            )
            for record in state.verified_criteria.values()
        ),
        criteria=state.criteria,
        completion_policy=state.completion_policy,
        acceptance_checks=tuple(
            TaskAcceptanceCheckInfo(
                record.completion_stage_id,
                record.criterion_index,
                record.source,
                record.outcome,
                record.evidence,
                record.checked_at,
            )
            for record in (current_checks[index] for index in sorted(current_checks))
        ),
        terminal_outcome=terminal.outcome if terminal is not None else None,
        terminal_reason=terminal.reason if terminal is not None else None,
        driver_paused=state.driver_paused,
        latest_reflection=(
            TaskReflectionInfo(
                state.latest_reflection.reflection_id,
                state.latest_reflection.stage_id,
                state.latest_reflection.stage_number,
                state.latest_reflection.recommendation,
                state.latest_reflection.summary,
                state.latest_reflection.next_objective,
                state.latest_reflection.recorded_at,
                state.latest_reflection.proposal_tool_use_id,
            )
            if state.latest_reflection is not None
            else None
        ),
        latest_blocker=(
            TaskBlockerInfo(
                state.latest_blocker.stage_id,
                state.latest_blocker.stage_number,
                state.latest_blocker.category,
                state.latest_blocker.summary,
                state.latest_blocker.proposal_tool_use_id,
                state.latest_blocker.recorded_at,
            )
            if state.latest_blocker is not None
            else None
        ),
        latest_checkpoint=(
            TaskCheckpointInfo(
                state.latest_checkpoint.checkpoint_id,
                state.latest_checkpoint.source_sequence,
                state.latest_checkpoint.accepted_plan_id,
                state.latest_checkpoint.completed_plan_steps,
                state.latest_checkpoint.completion_stage_id,
                state.latest_checkpoint.unresolved_criterion_indices,
                state.latest_checkpoint.latest_reflection_id,
                state.latest_checkpoint.created_at,
            )
            if state.latest_checkpoint is not None
            else None
        ),
        admission_origin=state.admission_origin,
    )


def _task_usage(state: TaskReplayState) -> TaskUsageInfo:
    values = {
        "provider_invocations": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "known_token_invocations": 0,
        "unknown_token_invocations": 0,
        "tool_requests": 0,
        "tool_admitted": 0,
        "tool_dispatched": 0,
        "tool_succeeded": 0,
        "tool_unsuccessful": 0,
    }
    committed = 0
    unavailable = 0
    for stage in state.stages:
        terminal = stage.terminal
        if terminal is None:
            continue
        if isinstance(terminal, StageCommitted):
            committed += 1
        if terminal.usage is None:
            unavailable += 1
            continue
        for field in values:
            values[field] += getattr(terminal.usage, field)
    return TaskUsageInfo(
        committed_stages=committed,
        unavailable_stages=unavailable,
        **values,
    )


def _budget_exhaustion(state: TaskReplayState) -> tuple[str, ...]:
    budget = state.budget
    usage = _task_usage(state)
    reasons: list[str] = []
    if len(state.stages) >= budget.max_stages:
        reasons.append("stage-limit")
    if usage.unavailable_stages:
        reasons.append("provider-invocation-accounting-unknown")
    elif usage.provider_invocations >= budget.max_provider_invocations:
        reasons.append("provider-invocation-limit")
    if usage.unavailable_stages:
        reasons.append("tool-request-accounting-unknown")
    elif usage.tool_requests >= budget.max_tool_requests:
        reasons.append("tool-request-limit")
    if budget.max_input_tokens is not None:
        if usage.unavailable_stages or usage.unknown_token_invocations:
            reasons.append("input-token-accounting-unknown")
        elif usage.input_tokens >= budget.max_input_tokens:
            reasons.append("input-token-limit")
    if budget.max_output_tokens is not None:
        if usage.unavailable_stages or usage.unknown_token_invocations:
            reasons.append("output-token-accounting-unknown")
        elif usage.output_tokens >= budget.max_output_tokens:
            reasons.append("output-token-limit")
    return tuple(reasons)


def _task_plan_info(state: TaskReplayState) -> TaskPlanInfo | None:
    plan = state.latest_plan
    if plan is None:
        return None
    accepted_record = next(
        (
            record
            for record in reversed(state.records)
            if isinstance(record, TaskPlanAccepted) and record.plan_id == plan.plan_id
        ),
        None,
    )
    completed = 0
    if accepted_record is not None:
        for stage in state.stages:
            if (
                completed == len(plan.steps)
                or stage.started.sequence <= accepted_record.sequence
                or stage.started.kind is not StageKind.EXECUTION
                or not isinstance(stage.terminal, StageCommitted)
                or stage.started.objective != plan.steps[completed]
            ):
                continue
            completed += 1
    return TaskPlanInfo(
        plan_id=plan.plan_id,
        steps=plan.steps,
        proposed_at=plan.proposed_at,
        accepted=accepted_record is not None,
        completed_steps=min(completed, len(plan.steps)),
        predecessor_plan_id=plan.predecessor_plan_id,
        revision_reason=plan.revision_reason,
        reflection_id=plan.reflection_id,
        proposal_tool_use_id=plan.proposal_tool_use_id,
    )


def _committed_stage_record(
    sequence: int,
    active: StageStarted,
    evidence: SessionTurnEvidence,
    committed_at: str,
    hook_audit: HookAuditLedger = HookAuditLedger(),
) -> StageCommitted:
    if evidence.session_id != active.session_id:
        raise TaskStoreError("Session Turn evidence belongs to a different Session")
    if evidence.committed_at < active.started_at:
        raise TaskStoreError("Session Turn evidence predates the active Stage")
    if (
        active.session_record_sequence_before is not None
        and evidence.record_sequence <= active.session_record_sequence_before
    ):
        raise TaskStoreError("Session Turn evidence does not follow the Stage baseline")
    if (
        active.session_turn_count_before is not None
        and evidence.turn_number != active.session_turn_count_before + 1
    ):
        raise TaskStoreError("Session Turn evidence is not the Stage's next committed Turn")
    if active.prompt_sha256 is not None and evidence.user_message_sha256 != active.prompt_sha256:
        raise TaskStoreError("Session Turn evidence does not match the Stage prompt")
    if committed_at < evidence.committed_at:
        raise TaskStoreError("Stage commit timestamp predates its Session Turn evidence")
    usage = None
    if evidence.provider_usage_available and evidence.tool_usage_available:
        usage = StageUsage(
            provider_invocations=evidence.provider_invocations,
            input_tokens=evidence.input_tokens,
            output_tokens=evidence.output_tokens,
            known_token_invocations=evidence.known_token_invocations,
            unknown_token_invocations=evidence.unknown_token_invocations,
            tool_requests=evidence.tool_requests,
            tool_admitted=evidence.tool_admitted,
            tool_dispatched=evidence.tool_dispatched,
            tool_succeeded=evidence.tool_succeeded,
            tool_unsuccessful=evidence.tool_unsuccessful,
        )
    return StageCommitted(
        sequence=sequence,
        stage_id=active.stage_id,
        stage_number=active.stage_number,
        session_id=evidence.session_id,
        turn_number=evidence.turn_number,
        turn_record_sequence=evidence.record_sequence,
        turn_record_sha256=evidence.record_sha256,
        committed_at=committed_at,
        usage=usage,
        hook_audit=hook_audit,
    )


def _claim_active_writer(key: str) -> None:
    with _ACTIVE_TASK_WRITERS_GUARD:
        if key in _ACTIVE_TASK_WRITERS:
            raise TaskStoreError("Task already has an active writer")
        _ACTIVE_TASK_WRITERS.add(key)


def _release_active_writer(key: str) -> None:
    with _ACTIVE_TASK_WRITERS_GUARD:
        _ACTIVE_TASK_WRITERS.discard(key)


def _task_writer_is_active(path: Path) -> bool:
    with _ACTIVE_TASK_WRITERS_GUARD:
        if str(path) in _ACTIVE_TASK_WRITERS:
            return True
    descriptor = _open_task_transcript(path, writable=True)
    try:
        try:
            _lock_descriptor(descriptor)
        except TaskStoreError:
            return True
        _unlock_descriptor(descriptor)
        return False
    finally:
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise TaskStoreError("Task already has an active writer") from None


def _unlock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _open_task_transcript(path: Path, *, writable: bool) -> int:
    if path.parent.is_symlink() or path.is_symlink():
        raise TaskStoreError("task transcript path must not contain a symlink")
    flags = (os.O_RDWR | os.O_APPEND) if writable else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise TaskStoreError(f"task transcript is inaccessible: {path}") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskStoreError("task transcript must be a regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_task_descriptor(descriptor: int, path: Path) -> bytes:
    try:
        before = os.fstat(descriptor)
        if before.st_size > MAX_TASK_TRANSCRIPT_BYTES:
            raise TaskStoreError(f"task transcript exceeds {MAX_TASK_TRANSCRIPT_BYTES} bytes")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size)
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != identity:
            raise TaskStoreError("task transcript changed while it was being read")
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise TaskStoreError("task transcript path changed while it was being read")
        return data
    except TaskStoreError:
        raise
    except OSError:
        raise TaskStoreError(f"task transcript is inaccessible: {path}") from None


def _append_task_record_descriptor(
    descriptor: int,
    path: Path,
    record: TaskRecord,
) -> None:
    try:
        payload = encode_task_record(record)
    except TaskRecordError as error:
        raise TaskStoreError(str(error)) from None
    write_started = False
    try:
        info = os.fstat(descriptor)
        pathname = path.lstat()
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise TaskStoreError("task transcript path no longer matches its writer")
        if info.st_size + len(payload) > MAX_TASK_TRANSCRIPT_BYTES:
            raise TaskStoreError(f"task transcript would exceed {MAX_TASK_TRANSCRIPT_BYTES} bytes")
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(payload)
        while view:
            write_started = True
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("task transcript append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except TaskStoreError:
        raise
    except OSError:
        raise TaskAppendCommitError(
            "could not durably append task transcript; inspect before retrying",
            record_may_be_visible=write_started,
        ) from None


def _install_task_transcript(path: Path, payload: bytes) -> None:
    temporary: str | None = None
    descriptor: int | None = None
    linked = False
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".task.", suffix=".tmp")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        linked = True
        _fsync_directory(path.parent)
        os.unlink(temporary)
        temporary = None
        _fsync_directory(path.parent)
    except FileExistsError:
        raise TaskStoreError(f"task ID collision: {path.stem}") from None
    except (OSError, TaskStoreError):
        raise TaskCreateCommitError(
            "could not durably create task transcript",
            task_visible=linked,
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_task_transcript(path: Path) -> bytes:
    if path.parent.is_symlink() or path.is_symlink():
        raise TaskStoreError("task transcript path must not contain a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise TaskStoreError("task transcript must be a regular file")
        if before.st_size > MAX_TASK_TRANSCRIPT_BYTES:
            raise TaskStoreError(f"task transcript exceeds {MAX_TASK_TRANSCRIPT_BYTES} bytes")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size)
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != identity:
            raise TaskStoreError("task transcript changed while it was being read")
        if (pathname.st_dev, pathname.st_ino) != (before.st_dev, before.st_ino):
            raise TaskStoreError("task transcript path changed while it was being read")
        return data
    except TaskStoreError:
        raise
    except OSError:
        raise TaskStoreError(f"task transcript is inaccessible: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _ensure_directory(path: Path, *, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise TaskStoreError("task storage path escapes the workspace")
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
            _fsync_directory(path.parent)
            return
        except FileExistsError:
            info = path.lstat()
        except OSError:
            raise TaskStoreError(f"could not create task storage directory: {path}") from None
    except OSError:
        raise TaskStoreError(f"task storage directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TaskStoreError(f"task storage path must be a real directory: {path}")


def _validate_directory(path: Path, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise TaskStoreError("task storage path escapes the workspace")
    try:
        info = path.lstat()
    except OSError:
        raise TaskStoreError(f"task storage directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TaskStoreError(f"task storage path must be a real directory: {path}")


def _validate_optional_parent_chain(workspace: Path, parent: Path) -> None:
    current = workspace / ".coquo"
    stop = parent
    while current == stop or current in stop.parents:
        if current.exists() or current.is_symlink():
            _validate_directory(current, workspace)
        else:
            return
        if current == stop:
            return
        relative = stop.relative_to(current)
        current = current / relative.parts[0]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise TaskStoreError(f"could not confirm task directory durability: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    (StageCommitted,)
    (StageFailed,)
    (StageFailureReason,)
    (StageStarted,)
    (canonical_stage_objective,)
