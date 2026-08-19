"""Host-owned, fail-closed orchestration for a bounded multi-agent workflow.

This module is intentionally a thin coordinator.  Task, Child, Team, and
verification ledgers remain authoritative for execution facts; this layer
stores only the workflow phase, immutable packet, and explicitly supplied
untrusted evidence.  It never invokes a Provider and never performs Git
integration, commit, push, or workspace mutation on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from uuid import UUID, uuid4

from coquo.core.permissions import (
    ApprovalMode,
    PermissionAction,
    PermissionDecision,
    PermissionGate,
    PermissionMode,
    PermissionRequest,
)
from coquo.task_bridge import TaskBridgeError, TaskBridgeResult, TaskOrchestrationService
from coquo.task_records import StageExecutionTarget
from coquo.task_store import TaskStore, TaskStoreError
from coquo.worktree_integration import (
    IntegrationPreflight,
    IntegrationResult,
    WorktreeIntegrationError,
    WorktreeIntegrationService,
)
from coquo.worktree_records import WorktreeState


class WorkflowError(RuntimeError):
    """Raised when a workflow request would violate its Host state machine."""


class WorkflowRole(StrEnum):
    ARCHITECT = "architect"
    EXPLORER = "explorer"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    INTEGRATOR = "integrator"


class WorkflowPhase(StrEnum):
    ARCHITECTURE = "architecture"
    EXPLORATION = "exploration"
    EXECUTION = "execution"
    REVIEW = "review"
    INTEGRATION = "integration"
    COMPLETED = "completed"
    NEEDS_REWORK = "needs-rework"
    BLOCKED = "blocked"
    RECOVERY_REQUIRED = "recovery-required"


class WorkflowVerdict(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


_TERMINAL_PHASES = {
    WorkflowPhase.COMPLETED,
    WorkflowPhase.BLOCKED,
    WorkflowPhase.RECOVERY_REQUIRED,
}
_NEXT_PHASE = {
    WorkflowPhase.ARCHITECTURE: WorkflowPhase.EXPLORATION,
    WorkflowPhase.EXPLORATION: WorkflowPhase.EXECUTION,
    WorkflowPhase.EXECUTION: WorkflowPhase.REVIEW,
    WorkflowPhase.REVIEW: WorkflowPhase.INTEGRATION,
}
_PHASE_ROLE = {
    WorkflowPhase.ARCHITECTURE: WorkflowRole.ARCHITECT,
    WorkflowPhase.EXPLORATION: WorkflowRole.EXPLORER,
    WorkflowPhase.EXECUTION: WorkflowRole.EXECUTOR,
    WorkflowPhase.REVIEW: WorkflowRole.REVIEWER,
    WorkflowPhase.INTEGRATION: WorkflowRole.INTEGRATOR,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: object, label: str, *, max_chars: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise WorkflowError(f"workflow {label} is invalid")
    value = value.strip()
    if len(value) > max_chars:
        raise WorkflowError(f"workflow {label} is oversized")
    return value


def _tuple_text(values: object, label: str, *, max_items: int = 32) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        try:
            values = tuple(values)  # type: ignore[arg-type]
        except TypeError:
            raise WorkflowError(f"workflow {label} is invalid") from None
    if len(values) > max_items:
        raise WorkflowError(f"workflow {label} has too many entries")
    result = tuple(_text(value, f"{label} entry", max_chars=2048) for value in values)
    if len(set(result)) != len(result):
        raise WorkflowError(f"workflow {label} contains duplicates")
    return result


@dataclass(frozen=True)
class WorkflowPacket:
    """Immutable contract handed from one Host-owned phase to the next."""

    objective: str
    scope: tuple[str, ...]
    resources: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    permission_profile: str
    recovery_policy: str
    verification_plan: tuple[str, ...]
    provider_provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        _text(self.objective, "objective")
        _tuple_text(self.scope, "scope")
        _tuple_text(self.resources, "resources")
        if not self.acceptance_criteria:
            raise WorkflowError("workflow acceptance criteria cannot be empty")
        _tuple_text(self.acceptance_criteria, "acceptance criteria")
        _text(self.permission_profile, "permission profile", max_chars=256)
        _text(self.recovery_policy, "recovery policy", max_chars=2048)
        _tuple_text(self.verification_plan, "verification plan")
        if not isinstance(self.provider_provenance, Mapping):
            raise WorkflowError("workflow provider provenance is invalid")
        for key, value in self.provider_provenance.items():
            _text(key, "provider provenance key", max_chars=128)
            _text(value, "provider provenance value", max_chars=512)


@dataclass(frozen=True)
class WorkflowEvidence:
    role: WorkflowRole
    source_id: str
    status: str
    summary: str
    untrusted: bool = True
    evidence_sha256: str | None = None
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if type(self.role) is not WorkflowRole:
            raise WorkflowError("workflow evidence role is invalid")
        _text(self.source_id, "evidence source ID", max_chars=256)
        _text(self.status, "evidence status", max_chars=128)
        _text(self.summary, "evidence summary", max_chars=8192)
        if self.untrusted is not True:
            raise WorkflowError("workflow evidence must remain untrusted")
        if self.evidence_sha256 is not None and (
            not isinstance(self.evidence_sha256, str)
            or len(self.evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_sha256)
        ):
            raise WorkflowError("workflow evidence digest is invalid")


@dataclass(frozen=True)
class WorkflowStage:
    """Durable identity projection for one Host-started external stage.

    Task, Child, and Team ledgers remain authoritative.  A workflow stores only
    the exact identity needed to inspect or recover that stage and a bounded
    status projection, never a duplicate worker or Provider state machine.
    """

    role: WorkflowRole
    task_id: str
    target: StageExecutionTarget
    source_id: str
    status: str
    child_run_id: str | None = None
    team_id: str | None = None
    assignment_id: str | None = None
    schedule_run_id: str | None = None
    assignment_ids: tuple[str, ...] = ()
    handoff_received: bool = False
    untrusted: bool = True
    result_code: str | None = None
    diagnostic: str | None = None
    evidence_sha256: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if type(self.role) is not WorkflowRole:
            raise WorkflowError("workflow stage role is invalid")
        try:
            UUID(self.task_id)
        except (ValueError, AttributeError):
            raise WorkflowError("workflow stage Task ID is invalid") from None
        if type(self.target) is not StageExecutionTarget:
            raise WorkflowError("workflow stage target is invalid")
        _text(self.source_id, "stage source ID", max_chars=256)
        _text(self.status, "stage status", max_chars=128)
        if not isinstance(self.assignment_ids, tuple):
            raise WorkflowError("workflow stage assignment IDs are invalid")
        for value in self.assignment_ids:
            _text(value, "stage assignment ID", max_chars=256)
        if self.untrusted is not True:
            raise WorkflowError("workflow stage evidence must remain untrusted")
        if self.evidence_sha256 is not None and (
            not isinstance(self.evidence_sha256, str)
            or len(self.evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_sha256)
        ):
            raise WorkflowError("workflow stage evidence digest is invalid")


@dataclass(frozen=True)
class WorkflowIntegration:
    """Durable Host projection of one exact worktree integration decision.

    The worktree ledger remains authoritative for Git facts.  This projection
    records only the identity and result needed to prevent an unreviewed or
    ambiguous integration from being accepted after a reload.
    """

    team_id: str
    assignment_id: str
    worktree_id: str
    patch_sha256: str | None
    manifest_sha256: str | None
    precondition_sha256: str | None
    target_ref: str | None
    target_head: str | None
    base_commit: str | None
    changed_paths: int
    status: str
    result_code: str
    diagnostic: str
    permission_decision: str
    permission_reason: str
    host_accepted: bool = False
    recorded_at: str = ""
    host_accepted_at: str | None = None

    def __post_init__(self) -> None:
        _text(self.team_id, "integration Team ID", max_chars=256)
        _text(self.assignment_id, "integration assignment ID", max_chars=256)
        _text(self.worktree_id, "integration worktree ID", max_chars=256)
        for value, label in (
            (self.patch_sha256, "integration patch digest"),
            (self.manifest_sha256, "integration manifest digest"),
            (self.precondition_sha256, "integration precondition digest"),
        ):
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise WorkflowError(f"{label} is invalid")
        for value, label in (
            (self.target_ref, "integration target ref"),
            (self.target_head, "integration target head"),
            (self.base_commit, "integration base commit"),
        ):
            if value is not None:
                _text(value, label, max_chars=256)
        if type(self.changed_paths) is not int or self.changed_paths < 0:
            raise WorkflowError("integration changed path count is invalid")
        _text(self.status, "integration status", max_chars=64)
        _text(self.result_code, "integration result code", max_chars=128)
        _text(self.diagnostic, "integration diagnostic", max_chars=4096)
        _text(self.permission_decision, "integration permission decision", max_chars=32)
        _text(self.permission_reason, "integration permission reason", max_chars=128)
        if type(self.host_accepted) is not bool:
            raise WorkflowError("integration host acceptance is invalid")


@dataclass(frozen=True)
class WorkflowState:
    workflow_id: str
    task_id: str
    phase: WorkflowPhase
    packet: WorkflowPacket
    evidence: tuple[WorkflowEvidence, ...] = ()
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""
    stages: tuple[WorkflowStage, ...] = ()
    integration: WorkflowIntegration | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.workflow_id)
            UUID(self.task_id)
        except (ValueError, AttributeError):
            raise WorkflowError("workflow or Task ID is invalid") from None
        if type(self.phase) is not WorkflowPhase:
            raise WorkflowError("workflow phase is invalid")
        if type(self.revision) is not int or self.revision < 0:
            raise WorkflowError("workflow revision is invalid")
        if any(type(item) is not WorkflowEvidence for item in self.evidence):
            raise WorkflowError("workflow evidence is invalid")
        if any(type(item) is not WorkflowStage for item in self.stages):
            raise WorkflowError("workflow stages are invalid")
        if self.integration is not None and type(self.integration) is not WorkflowIntegration:
            raise WorkflowError("workflow integration projection is invalid")


class WorkflowOrchestrator:
    """Advance one Host-owned workflow without autonomous side effects."""

    def __init__(
        self,
        workspace: Path,
        *,
        task_store: TaskStore | None = None,
        bridge_service: TaskOrchestrationService | None = None,
        integration_service: WorktreeIntegrationService | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.tasks = task_store or TaskStore(self.workspace)
        self.bridge = bridge_service or TaskOrchestrationService(
            self.workspace, task_store=self.tasks
        )
        self.integrations = integration_service or WorktreeIntegrationService(self.workspace)
        self.permissions = PermissionGate()
        self.root = self.workspace / ".coquo" / "workflows"
        self._states: dict[str, WorkflowState] = {}

    def start(
        self,
        objective: str,
        *,
        owner_session: str = "latest",
        task_id: str | None = None,
        scope: tuple[str, ...] = ("workspace",),
        resources: tuple[str, ...] = (),
        acceptance_criteria: tuple[str, ...] = (),
        permission_profile: str = "read-only-explorer-then-explicit-executor",
        recovery_policy: str = "unknown-or-lost-result requires Host recovery; never auto-retry",
        verification_plan: tuple[str, ...] = ("Host checks", "independent read-only review"),
        provider_provenance: Mapping[str, str] = (),
    ) -> WorkflowState:
        objective = _text(objective, "objective")
        criteria = _tuple_text(acceptance_criteria, "acceptance criteria")
        if not criteria:
            raise WorkflowError("workflow acceptance criteria cannot be empty")
        if task_id is None:
            try:
                task = self.tasks.create(
                    objective,
                    owner_session=owner_session,
                    acceptance_criteria=criteria,
                )
            except (TaskStoreError, OSError) as error:
                raise WorkflowError(f"Task creation failed: {error}") from None
            task_id = task.task_id
        else:
            try:
                task = self.tasks.inspect(task_id)
            except TaskStoreError as error:
                raise WorkflowError(f"Task inspection failed: {error}") from None
            if task.objective != objective:
                raise WorkflowError("workflow objective does not match the existing Task")
        packet = WorkflowPacket(
            objective=objective,
            scope=_tuple_text(scope, "scope"),
            resources=_tuple_text(resources, "resources"),
            acceptance_criteria=criteria,
            permission_profile=_text(permission_profile, "permission profile", max_chars=256),
            recovery_policy=_text(recovery_policy, "recovery policy", max_chars=2048),
            verification_plan=_tuple_text(verification_plan, "verification plan"),
            provider_provenance=dict(provider_provenance),
        )
        now = _now()
        state = WorkflowState(
            str(uuid4()), task_id, WorkflowPhase.ARCHITECTURE, packet, (), 0, now, now
        )
        self._save(state)
        return state

    def inspect(self, workflow_id: str) -> WorkflowState:
        if workflow_id in self._states:
            return self._states[workflow_id]
        path = self._path(workflow_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowError(f"workflow state is unavailable: {error}") from None
        state = self._decode(data)
        self._states[state.workflow_id] = state
        return state

    def advance(
        self, workflow_id: str, *, expected_phase: WorkflowPhase | None = None
    ) -> WorkflowState:
        state = self.inspect(workflow_id)
        self._expect_phase(state, expected_phase)
        try:
            next_phase = _NEXT_PHASE[state.phase]
        except KeyError:
            raise WorkflowError(f"workflow phase {state.phase.value} cannot advance") from None
        return self._replace(state, phase=next_phase)

    def record_exploration(
        self, workflow_id: str, *, source_id: str, status: str, summary: str
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.EXPLORATION)
        evidence = WorkflowEvidence(
            WorkflowRole.EXPLORER, source_id, status, summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.EXECUTION, evidence=state.evidence + (evidence,)
        )

    # ------------------------------------------------------------------
    # Host-owned Task–Child–Team execution bridge
    # ------------------------------------------------------------------
    def start_exploration_stage(
        self,
        workflow_id: str,
        *,
        permission_mode: str = "read-only",
        approval_mode: str = "ask",
    ) -> TaskBridgeResult:
        """Admit the Explorer phase through the existing Task→Child bridge."""
        state = self._phase(workflow_id, WorkflowPhase.EXPLORATION)
        existing = self._latest_stage(state, WorkflowRole.EXPLORER)
        if existing is not None:
            if existing.target is not StageExecutionTarget.CHILD:
                raise WorkflowError("Explorer stage is bound to a non-Child target")
            return self.bridge.observe_child_stage(existing.task_id, existing.child_run_id)
        try:
            result = self.bridge.start_child_stage(
                state.task_id,
                state.packet.objective,
                permission_mode=permission_mode,
                approval_mode=approval_mode,
            )
        except TaskBridgeError as error:
            raise WorkflowError(f"Explorer admission failed: {error}") from None
        self._replace_stage(workflow_id, WorkflowRole.EXPLORER, result)
        return result

    def run_exploration_stage(
        self,
        workflow_id: str,
        session: Any,
        *,
        background: bool = True,
    ) -> TaskBridgeResult:
        """Start/await the admitted Explorer without duplicating Child execution."""
        state = self._phase(workflow_id, WorkflowPhase.EXPLORATION)
        stage = self._require_stage(state, WorkflowRole.EXPLORER)
        if stage.child_run_id is None:
            raise WorkflowError("Explorer Child identity is missing")
        try:
            result = self.bridge.run_child_stage(
                state.task_id, stage.child_run_id, session, background=background
            )
        except TaskBridgeError as error:
            raise WorkflowError(f"Explorer execution failed: {error}") from None
        self._record_bridge_result(workflow_id, WorkflowRole.EXPLORER, result)
        return result

    def start_execution_stage(
        self,
        workflow_id: str,
        *,
        target: StageExecutionTarget = StageExecutionTarget.CHILD,
        team_id: str | None = None,
        member_id: str | None = None,
        work_item_id: str | None = None,
        max_assignments: int = 32,
        max_parallel: int = 4,
        permission_mode: str = "read-only",
        approval_mode: str = "ask",
    ) -> TaskBridgeResult:
        """Admit one Executor stage using Child, Team assignment, or Team schedule."""
        state = self._phase(workflow_id, WorkflowPhase.EXECUTION)
        existing = self._latest_stage(state, WorkflowRole.EXECUTOR)
        if existing is not None:
            return self._observe_existing_stage(state, existing)
        try:
            stage_task = self.tasks.derive(
                state.task_id,
                state.packet.objective,
                owner_session=self.tasks.inspect(state.task_id).owner_session_id,
                acceptance_criteria=state.packet.acceptance_criteria,
                name=f"workflow-executor-{workflow_id[:8]}",
            )
            if target is StageExecutionTarget.CHILD:
                result = self.bridge.start_child_stage(
                    stage_task.task_id,
                    state.packet.objective,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                )
            elif target is StageExecutionTarget.TEAM_ASSIGNMENT:
                if team_id is None or member_id is None:
                    raise WorkflowError("Team assignment execution requires team_id and member_id")
                result = self.bridge.start_team_assignment_stage(
                    stage_task.task_id,
                    team_id,
                    member_id,
                    objective=state.packet.objective,
                    work_item_id=work_item_id,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                )
            elif target is StageExecutionTarget.TEAM_SCHEDULE:
                if team_id is None:
                    raise WorkflowError("Team schedule execution requires team_id")
                result = self.bridge.start_team_schedule_stage(
                    stage_task.task_id,
                    team_id,
                    max_assignments=max_assignments,
                    max_parallel=max_parallel,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                )
            else:
                raise WorkflowError("unsupported workflow execution target")
        except (TaskBridgeError, ValueError) as error:
            raise WorkflowError(f"Executor admission failed: {error}") from None
        self._replace_stage(workflow_id, WorkflowRole.EXECUTOR, result)
        return result

    def run_execution_stage(
        self,
        workflow_id: str,
        session: Any,
        *,
        background: bool = True,
    ) -> TaskBridgeResult:
        """Start/await the admitted Executor through the existing bridge."""
        state = self._phase(workflow_id, WorkflowPhase.EXECUTION)
        stage = self._require_stage(state, WorkflowRole.EXECUTOR)
        try:
            if stage.target is StageExecutionTarget.CHILD:
                if stage.child_run_id is None:
                    raise WorkflowError("Executor Child identity is missing")
                result = self.bridge.run_child_stage(
                    stage.task_id, stage.child_run_id, session, background=background
                )
            elif stage.target is StageExecutionTarget.TEAM_ASSIGNMENT:
                if stage.assignment_id is None:
                    raise WorkflowError("Executor Team assignment identity is missing")
                result = self.bridge.run_team_assignment_stage(
                    stage.task_id, stage.assignment_id, session, background=background
                )
            elif stage.target is StageExecutionTarget.TEAM_SCHEDULE:
                if stage.schedule_run_id is None:
                    raise WorkflowError("Executor Team schedule identity is missing")
                result = self.bridge.run_team_schedule_stage(
                    stage.task_id, stage.schedule_run_id, session
                )
            else:
                raise WorkflowError("unsupported workflow execution target")
        except (TaskBridgeError, ValueError) as error:
            raise WorkflowError(f"Executor execution failed: {error}") from None
        self._record_bridge_result(workflow_id, WorkflowRole.EXECUTOR, result)
        return result

    def recover_stage(self, workflow_id: str, role: WorkflowRole) -> TaskBridgeResult:
        """Re-observe one exact stage; never retries or creates a replacement."""
        state = self.inspect(workflow_id)
        stage = self._require_stage(state, role)
        try:
            result = self._observe_existing_stage(state, stage)
        except (TaskBridgeError, ValueError) as error:
            raise WorkflowError(f"workflow stage recovery failed: {error}") from None
        self._record_bridge_result(workflow_id, role, result, recovery=True)
        return result

    def _observe_existing_stage(
        self, state: WorkflowState, stage: WorkflowStage
    ) -> TaskBridgeResult:
        if stage.target is StageExecutionTarget.CHILD:
            return self.bridge.observe_child_stage(stage.task_id, stage.child_run_id)
        if stage.target is StageExecutionTarget.TEAM_ASSIGNMENT:
            return self.bridge.observe_team_assignment_stage(stage.task_id, stage.assignment_id)
        if stage.target is StageExecutionTarget.TEAM_SCHEDULE:
            return self.bridge.observe_team_schedule_stage(stage.task_id, stage.schedule_run_id)
        raise WorkflowError("unsupported workflow stage target")

    @staticmethod
    def _latest_stage(state: WorkflowState, role: WorkflowRole) -> WorkflowStage | None:
        for stage in reversed(state.stages):
            if stage.role is role:
                return stage
        return None

    def _require_stage(self, state: WorkflowState, role: WorkflowRole) -> WorkflowStage:
        stage = self._latest_stage(state, role)
        if stage is None:
            raise WorkflowError(f"workflow has no {role.value} stage")
        return stage

    def _replace_stage(
        self, workflow_id: str, role: WorkflowRole, result: TaskBridgeResult
    ) -> WorkflowState:
        state = self.inspect(workflow_id)
        stages = tuple(item for item in state.stages if item.role is not role)
        stage = self._stage_from_result(role, result)
        return self._replace(state, stages=stages + (stage,))

    def _record_bridge_result(
        self,
        workflow_id: str,
        role: WorkflowRole,
        result: TaskBridgeResult,
        *,
        recovery: bool = False,
    ) -> WorkflowState:
        state = self.inspect(workflow_id)
        prior = self._latest_stage(state, role)
        expected_task_id = prior.task_id if prior is not None else state.task_id
        if result.task.task_id != expected_task_id:
            raise WorkflowError("bridge Task does not match workflow stage")
        stages = tuple(item for item in state.stages if item.role is not role)
        stages += (self._stage_from_result(role, result),)
        if result.outcome == "committed":
            if role is WorkflowRole.EXPLORER and state.phase is WorkflowPhase.EXPLORATION:
                evidence = WorkflowEvidence(
                    role,
                    self._stage_from_result(role, result).source_id,
                    "committed",
                    "Explorer handoff committed through Host bridge",
                    recorded_at=_now(),
                )
                return self._replace(
                    state,
                    phase=WorkflowPhase.EXECUTION,
                    stages=stages,
                    evidence=state.evidence + (evidence,),
                )
            if role is WorkflowRole.EXECUTOR and state.phase is WorkflowPhase.EXECUTION:
                evidence = WorkflowEvidence(
                    role,
                    self._stage_from_result(role, result).source_id,
                    "committed",
                    "Executor evidence committed through Host bridge",
                    recorded_at=_now(),
                )
                return self._replace(
                    state,
                    phase=WorkflowPhase.REVIEW,
                    stages=stages,
                    evidence=state.evidence + (evidence,),
                )
            return self._replace(state, stages=stages)
        if result.outcome in {"failed", "recovery-required"} or recovery:
            evidence = WorkflowEvidence(
                role,
                self._stage_from_result(role, result).source_id,
                result.outcome,
                result.diagnostic or "External stage requires Host recovery",
                recorded_at=_now(),
            )
            return self._replace(
                state,
                phase=WorkflowPhase.RECOVERY_REQUIRED,
                stages=stages,
                evidence=state.evidence + (evidence,),
            )
        return self._replace(state, stages=stages)

    @staticmethod
    def _stage_from_result(role: WorkflowRole, result: TaskBridgeResult) -> WorkflowStage:
        task_stage = result.stage
        target = result.target
        source_id = task_stage.stage_id
        if target is StageExecutionTarget.CHILD:
            source_id = task_stage.child_run_id or source_id
        elif target is StageExecutionTarget.TEAM_ASSIGNMENT:
            source_id = task_stage.assignment_id or source_id
        elif target is StageExecutionTarget.TEAM_SCHEDULE:
            source_id = task_stage.schedule_run_id or source_id
        handoff_received = bool(result.handoffs)
        return WorkflowStage(
            role=role,
            task_id=result.task.task_id,
            target=target,
            source_id=source_id,
            status=result.outcome,
            child_run_id=task_stage.child_run_id,
            team_id=task_stage.team_id,
            assignment_id=task_stage.assignment_id,
            schedule_run_id=task_stage.schedule_run_id,
            assignment_ids=(
                result.schedule.assignment_ids
                if result.schedule is not None
                else task_stage.assignment_ids
            ),
            handoff_received=handoff_received,
            result_code=result.result_code,
            diagnostic=result.diagnostic,
            evidence_sha256=(
                task_stage.external_evidence_sha256
                or (result.handoffs[0].handoff_sha256 if result.handoffs else None)
            ),
            created_at=task_stage.started_at,
            updated_at=_now(),
        )

    def record_execution(
        self,
        workflow_id: str,
        *,
        source_id: str,
        status: str,
        summary: str,
        bridge_result: TaskBridgeResult | None = None,
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.EXECUTION)
        if bridge_result is not None:
            if bridge_result.task.task_id != state.task_id:
                raise WorkflowError("execution bridge Task does not match workflow")
            if bridge_result.outcome not in {"committed", "pending", "failed", "recovery-required"}:
                raise WorkflowError("execution bridge outcome is invalid")
            status = bridge_result.outcome
        evidence = WorkflowEvidence(
            WorkflowRole.EXECUTOR, source_id, status, summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.REVIEW, evidence=state.evidence + (evidence,)
        )

    def record_review(
        self,
        workflow_id: str,
        *,
        source_id: str,
        verdict: WorkflowVerdict,
        summary: str,
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.REVIEW)
        if type(verdict) is not WorkflowVerdict:
            raise WorkflowError("review verdict is invalid")
        evidence = WorkflowEvidence(
            WorkflowRole.REVIEWER, source_id, verdict.value, summary, recorded_at=_now()
        )
        target = {
            WorkflowVerdict.PASSED: WorkflowPhase.INTEGRATION,
            WorkflowVerdict.REJECTED: WorkflowPhase.NEEDS_REWORK,
            WorkflowVerdict.UNKNOWN: WorkflowPhase.RECOVERY_REQUIRED,
        }[verdict]
        return self._replace(state, phase=target, evidence=state.evidence + (evidence,))

    # ------------------------------------------------------------------
    # Host-owned Reviewer -> Integrator boundary
    # ------------------------------------------------------------------
    def prepare_integration(
        self,
        workflow_id: str,
        *,
        expected_patch_sha256: str | None = None,
        permission_mode: PermissionMode | str = PermissionMode.WORKSPACE_WRITE,
        approval_mode: ApprovalMode | str = ApprovalMode.AUTO,
    ) -> IntegrationPreflight | None:
        """Run one non-mutating, exact integration preflight.

        A read-only Child executor has no worktree to integrate; in that case
        the workflow records an explicit ``not-required`` projection.  A
        writable Team assignment must provide its sealed patch digest and pass
        all worktree/authority checks before the Host may call :meth:`integrate`.
        """
        state = self._phase(workflow_id, WorkflowPhase.INTEGRATION)
        self._require_passed_review(state)
        stage = self._require_stage(state, WorkflowRole.EXECUTOR)
        if stage.target is not StageExecutionTarget.TEAM_ASSIGNMENT:
            projection = self._not_required_integration(stage)
            self._replace(state, integration=projection)
            return None
        if stage.team_id is None or stage.assignment_id is None:
            raise WorkflowError("Executor Team assignment identity is incomplete")
        try:
            assignment = self.bridge.team_assignments.inspect(stage.team_id, stage.assignment_id)
        except Exception as error:
            raise WorkflowError(f"integration assignment inspection failed: {error}") from None
        worktree_id = assignment.assignment.worktree_id
        if worktree_id is None:
            projection = self._not_required_integration(stage)
            self._replace(state, integration=projection)
            return None
        if expected_patch_sha256 is None:
            raise WorkflowError("writable integration requires the sealed patch digest")
        self._check_integration_permission(permission_mode, approval_mode)
        try:
            prepared = self.integrations.prepare(
                stage.team_id, stage.assignment_id, expected_patch_sha256
            )
        except (WorktreeIntegrationError, ValueError) as error:
            projection = WorkflowIntegration(
                team_id=stage.team_id,
                assignment_id=stage.assignment_id,
                worktree_id=worktree_id,
                patch_sha256=expected_patch_sha256,
                manifest_sha256=None,
                precondition_sha256=None,
                target_ref=None,
                target_head=None,
                base_commit=None,
                changed_paths=0,
                status="preflight-failed",
                result_code="integration_preflight_failed",
                diagnostic=str(error),
                permission_decision=PermissionDecision.ALLOW.value,
                permission_reason="explicit_host_integration_preflight",
                recorded_at=_now(),
            )
            self._replace(state, integration=projection)
            raise WorkflowError(f"integration preflight failed: {error}") from None
        projection = self._projection_from_preflight(
            prepared,
            status="preflighted",
            result_code="integration_preflight_ok",
            diagnostic="Host integration preflight passed; no workspace mutation occurred",
            permission_mode=permission_mode,
            approval_mode=approval_mode,
        )
        self._replace(state, integration=projection)
        return prepared

    def integrate(
        self,
        workflow_id: str,
        *,
        action_digest: str,
        permission_mode: PermissionMode | str = PermissionMode.WORKSPACE_WRITE,
        approval_mode: ApprovalMode | str = ApprovalMode.AUTO,
    ) -> IntegrationResult | None:
        """Apply one prepared sealed patch, never commit/push/retry it."""
        state = self._phase(workflow_id, WorkflowPhase.INTEGRATION)
        self._require_passed_review(state)
        projection = state.integration
        if projection is None:
            raise WorkflowError("integration requires an explicit preflight")
        if projection.status == "not-required":
            raise WorkflowError("integration is not required for this workflow stage")
        if projection.status != "preflighted":
            raise WorkflowError("integration preflight is not ready")
        if (
            not isinstance(action_digest, str)
            or len(action_digest) != 64
            or any(char not in "0123456789abcdef" for char in action_digest)
        ):
            raise WorkflowError("integration action digest is invalid")
        self._check_integration_permission(permission_mode, approval_mode)
        prepared = IntegrationPreflight(
            team_id=projection.team_id,
            assignment_id=projection.assignment_id,
            worktree_id=projection.worktree_id,
            patch_sha256=projection.patch_sha256 or "",
            manifest_sha256=projection.manifest_sha256 or "",
            target_ref=projection.target_ref or "",
            target_head=projection.target_head or "",
            base_commit=projection.base_commit or "",
            changed_paths=projection.changed_paths,
            patch_bytes=0,
            precondition_sha256=projection.precondition_sha256 or "",
        )
        try:
            result = self.integrations.integrate(prepared, action_digest=action_digest)
        except (WorktreeIntegrationError, ValueError) as error:
            unknown = self._integration_is_unknown(projection.worktree_id)
            updated = replace(
                projection,
                status="recovery-required" if unknown else "failed",
                result_code="integration_unknown" if unknown else "integration_failed",
                diagnostic=str(error),
                recorded_at=_now(),
            )
            self._replace(
                state,
                integration=updated,
                phase=WorkflowPhase.RECOVERY_REQUIRED if unknown else WorkflowPhase.INTEGRATION,
            )
            raise WorkflowError(
                "integration outcome is unknown; recovery is required"
                if unknown
                else f"integration failed: {error}"
            ) from None
        updated = replace(
            projection,
            status=result.status,
            result_code=result.result_code,
            diagnostic=result.message,
            target_head=result.target_head,
            host_accepted=False,
            recorded_at=_now(),
        )
        self._replace(state, integration=updated)
        return result

    def recover_integration(self, workflow_id: str) -> WorkflowIntegration:
        """Re-observe one exact worktree; never applies a second patch."""
        state = self.inspect(workflow_id)
        projection = state.integration
        if projection is None:
            raise WorkflowError("workflow has no integration projection")
        try:
            info = self.integrations.worktrees.store.inspect(projection.worktree_id)
        except Exception as error:
            raise WorkflowError(f"integration recovery inspection failed: {error}") from None
        state_value = info.state
        if state_value is WorktreeState.APPLIED:
            status, code, phase = "applied", "applied", WorkflowPhase.INTEGRATION
        elif state_value is WorktreeState.INTEGRATION_UNKNOWN:
            status, code, phase = (
                "recovery-required",
                "integration_unknown",
                WorkflowPhase.RECOVERY_REQUIRED,
            )
        elif state_value is WorktreeState.INTEGRATION_FAILED:
            status, code, phase = "failed", "integration_failed", WorkflowPhase.INTEGRATION
        else:
            status, code, phase = (
                "recovery-required",
                "integration_state_unresolved",
                WorkflowPhase.RECOVERY_REQUIRED,
            )
        updated = replace(
            projection,
            status=status,
            result_code=code,
            diagnostic=f"Host re-observed worktree state: {state_value.value}",
            recorded_at=_now(),
        )
        self._replace(state, integration=updated, phase=phase)
        return updated

    def accept(
        self, workflow_id: str, *, summary: str = "Host accepted reviewed workflow"
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.INTEGRATION)
        self._require_passed_review(state)
        if self._requires_integration(state):
            if state.integration is None:
                raise WorkflowError("Host acceptance requires an explicit integration preflight")
            if state.integration.status != "applied":
                raise WorkflowError(
                    "Host acceptance requires applied integration evidence; "
                    f"got {state.integration.status}"
                )
        evidence = WorkflowEvidence(
            WorkflowRole.INTEGRATOR, "host", "accepted", summary, recorded_at=_now()
        )
        integration = state.integration
        if integration is not None:
            integration = replace(integration, host_accepted=True, host_accepted_at=_now())
        return self._replace(
            state,
            phase=WorkflowPhase.COMPLETED,
            integration=integration,
            evidence=state.evidence + (evidence,),
        )

    def rework(self, workflow_id: str, *, summary: str) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.NEEDS_REWORK)
        evidence = WorkflowEvidence(
            WorkflowRole.ARCHITECT, "host", "rework", summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.EXECUTION, evidence=state.evidence + (evidence,)
        )

    def block(self, workflow_id: str, *, summary: str) -> WorkflowState:
        state = self.inspect(workflow_id)
        if state.phase in _TERMINAL_PHASES:
            raise WorkflowError("terminal workflow cannot be blocked again")
        evidence = WorkflowEvidence(
            _PHASE_ROLE.get(state.phase, WorkflowRole.ARCHITECT),
            "host",
            "blocked",
            summary,
            recorded_at=_now(),
        )
        return self._replace(
            state, phase=WorkflowPhase.BLOCKED, evidence=state.evidence + (evidence,)
        )

    def _require_passed_review(self, state: WorkflowState) -> None:
        review = next(
            (item for item in reversed(state.evidence) if item.role is WorkflowRole.REVIEWER),
            None,
        )
        if review is None or review.status != WorkflowVerdict.PASSED.value:
            raise WorkflowError("Host integration requires a passed Reviewer evidence")

    def _requires_integration(self, state: WorkflowState) -> bool:
        stage = self._latest_stage(state, WorkflowRole.EXECUTOR)
        if stage is None or stage.target is not StageExecutionTarget.TEAM_ASSIGNMENT:
            return False
        if stage.team_id is None or stage.assignment_id is None:
            raise WorkflowError("Executor Team assignment identity is incomplete")
        try:
            assignment = self.bridge.team_assignments.inspect(stage.team_id, stage.assignment_id)
        except Exception as error:
            raise WorkflowError(f"integration assignment inspection failed: {error}") from None
        return assignment.assignment.worktree_id is not None

    def _not_required_integration(self, stage: WorkflowStage) -> WorkflowIntegration:
        return WorkflowIntegration(
            team_id=stage.team_id or "not-applicable",
            assignment_id=stage.assignment_id or "not-applicable",
            worktree_id="not-applicable",
            patch_sha256=None,
            manifest_sha256=None,
            precondition_sha256=None,
            target_ref=None,
            target_head=None,
            base_commit=None,
            changed_paths=0,
            status="not-required",
            result_code="no-writable-worktree",
            diagnostic="Executor result has no writable Team worktree; Host acceptance remains explicit",
            permission_decision=PermissionDecision.ALLOW.value,
            permission_reason="integration_not_required",
            recorded_at=_now(),
        )

    def _check_integration_permission(
        self,
        permission_mode: PermissionMode | str,
        approval_mode: ApprovalMode | str,
    ) -> None:
        try:
            mode = (
                permission_mode
                if type(permission_mode) is PermissionMode
                else PermissionMode(permission_mode)
            )
            approval = (
                approval_mode
                if type(approval_mode) is ApprovalMode
                else ApprovalMode(approval_mode)
            )
        except ValueError:
            raise WorkflowError("integration permission or approval mode is invalid") from None
        result = self.permissions.evaluate(
            PermissionRequest(mode, approval, PermissionAction.WORKSPACE_OVERWRITE)
        )
        if result.decision is PermissionDecision.DENY:
            raise WorkflowError(f"integration permission denied: {result.reason.value}")
        if result.decision is PermissionDecision.ASK:
            raise WorkflowError(f"integration approval required: {result.reason.value}")

    def _projection_from_preflight(
        self,
        prepared: IntegrationPreflight,
        *,
        status: str,
        result_code: str,
        diagnostic: str,
        permission_mode: PermissionMode | str,
        approval_mode: ApprovalMode | str,
    ) -> WorkflowIntegration:
        mode = (
            permission_mode
            if type(permission_mode) is PermissionMode
            else PermissionMode(permission_mode)
        )
        approval = (
            approval_mode if type(approval_mode) is ApprovalMode else ApprovalMode(approval_mode)
        )
        permission = self.permissions.evaluate(
            PermissionRequest(mode, approval, PermissionAction.WORKSPACE_OVERWRITE)
        )
        return WorkflowIntegration(
            team_id=prepared.team_id,
            assignment_id=prepared.assignment_id,
            worktree_id=prepared.worktree_id,
            patch_sha256=prepared.patch_sha256,
            manifest_sha256=prepared.manifest_sha256,
            precondition_sha256=prepared.precondition_sha256,
            target_ref=prepared.target_ref,
            target_head=prepared.target_head,
            base_commit=prepared.base_commit,
            changed_paths=prepared.changed_paths,
            status=status,
            result_code=result_code,
            diagnostic=diagnostic,
            permission_decision=permission.decision.value,
            permission_reason=permission.reason.value,
            recorded_at=_now(),
        )

    def _integration_is_unknown(self, worktree_id: str) -> bool:
        try:
            state = self.integrations.worktrees.store.inspect(worktree_id).state
        except Exception:
            return True
        return state is WorktreeState.INTEGRATION_UNKNOWN

    def _phase(self, workflow_id: str, expected: WorkflowPhase) -> WorkflowState:
        state = self.inspect(workflow_id)
        if state.phase is not expected:
            raise WorkflowError(
                f"workflow requires phase {expected.value}, got {state.phase.value}"
            )
        return state

    def _expect_phase(self, state: WorkflowState, expected: WorkflowPhase | None) -> None:
        if expected is not None and state.phase is not expected:
            raise WorkflowError(
                f"workflow phase changed: expected {expected.value}, got {state.phase.value}"
            )

    def _replace(self, state: WorkflowState, **changes: Any) -> WorkflowState:
        next_state = WorkflowState(
            workflow_id=changes.get("workflow_id", state.workflow_id),
            task_id=changes.get("task_id", state.task_id),
            phase=changes.get("phase", state.phase),
            packet=changes.get("packet", state.packet),
            evidence=changes.get("evidence", state.evidence),
            revision=state.revision + 1,
            created_at=state.created_at,
            updated_at=_now(),
            stages=changes.get("stages", state.stages),
            integration=changes.get("integration", state.integration),
        )
        self._save(next_state)
        return next_state

    def _path(self, workflow_id: str) -> Path:
        try:
            value = str(UUID(workflow_id))
        except (ValueError, AttributeError):
            raise WorkflowError("workflow ID is invalid") from None
        return self.root / f"{value}.json"

    def _save(self, state: WorkflowState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.workflow_id)
        payload = json.dumps(
            self._encode(state), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            temp = Path(handle.name)
        temp.replace(path)
        self._states[state.workflow_id] = state

    @staticmethod
    def _encode(state: WorkflowState) -> dict[str, object]:
        return {
            "workflow_id": state.workflow_id,
            "task_id": state.task_id,
            "phase": state.phase.value,
            "packet": {
                "objective": state.packet.objective,
                "scope": state.packet.scope,
                "resources": state.packet.resources,
                "acceptance_criteria": state.packet.acceptance_criteria,
                "permission_profile": state.packet.permission_profile,
                "recovery_policy": state.packet.recovery_policy,
                "verification_plan": state.packet.verification_plan,
                "provider_provenance": dict(state.packet.provider_provenance),
            },
            "evidence": [
                {
                    "role": item.role.value,
                    "source_id": item.source_id,
                    "status": item.status,
                    "summary": item.summary,
                    "untrusted": item.untrusted,
                    "evidence_sha256": item.evidence_sha256,
                    "recorded_at": item.recorded_at,
                }
                for item in state.evidence
            ],
            "stages": [
                {
                    "role": item.role.value,
                    "task_id": item.task_id,
                    "target": item.target.value,
                    "source_id": item.source_id,
                    "status": item.status,
                    "child_run_id": item.child_run_id,
                    "team_id": item.team_id,
                    "assignment_id": item.assignment_id,
                    "schedule_run_id": item.schedule_run_id,
                    "assignment_ids": item.assignment_ids,
                    "handoff_received": item.handoff_received,
                    "untrusted": item.untrusted,
                    "result_code": item.result_code,
                    "diagnostic": item.diagnostic,
                    "evidence_sha256": item.evidence_sha256,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in state.stages
            ],
            "integration": (
                None
                if state.integration is None
                else {
                    "team_id": state.integration.team_id,
                    "assignment_id": state.integration.assignment_id,
                    "worktree_id": state.integration.worktree_id,
                    "patch_sha256": state.integration.patch_sha256,
                    "manifest_sha256": state.integration.manifest_sha256,
                    "precondition_sha256": state.integration.precondition_sha256,
                    "target_ref": state.integration.target_ref,
                    "target_head": state.integration.target_head,
                    "base_commit": state.integration.base_commit,
                    "changed_paths": state.integration.changed_paths,
                    "status": state.integration.status,
                    "result_code": state.integration.result_code,
                    "diagnostic": state.integration.diagnostic,
                    "permission_decision": state.integration.permission_decision,
                    "permission_reason": state.integration.permission_reason,
                    "host_accepted": state.integration.host_accepted,
                    "recorded_at": state.integration.recorded_at,
                    "host_accepted_at": state.integration.host_accepted_at,
                }
            ),
            "revision": state.revision,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    @staticmethod
    def _decode(value: object) -> WorkflowState:
        if not isinstance(value, dict):
            raise WorkflowError("workflow state JSON is invalid")
        try:
            packet = value["packet"]
            assert isinstance(packet, dict)
            raw_evidence = value["evidence"]
            assert isinstance(raw_evidence, list)
            evidence = tuple(
                WorkflowEvidence(
                    WorkflowRole(item["role"]),
                    item["source_id"],
                    item["status"],
                    item["summary"],
                    item["untrusted"],
                    item.get("evidence_sha256"),
                    item.get("recorded_at", ""),
                )
                for item in raw_evidence
            )
            return WorkflowState(
                workflow_id=value["workflow_id"],
                task_id=value["task_id"],
                phase=WorkflowPhase(value["phase"]),
                packet=WorkflowPacket(
                    packet["objective"],
                    tuple(packet["scope"]),
                    tuple(packet["resources"]),
                    tuple(packet["acceptance_criteria"]),
                    packet["permission_profile"],
                    packet["recovery_policy"],
                    tuple(packet["verification_plan"]),
                    dict(packet["provider_provenance"]),
                ),
                evidence=evidence,
                revision=value["revision"],
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                stages=tuple(
                    WorkflowStage(
                        role=WorkflowRole(item["role"]),
                        task_id=item.get("task_id", value["task_id"]),
                        target=StageExecutionTarget(item["target"]),
                        source_id=item["source_id"],
                        status=item["status"],
                        child_run_id=item.get("child_run_id"),
                        team_id=item.get("team_id"),
                        assignment_id=item.get("assignment_id"),
                        schedule_run_id=item.get("schedule_run_id"),
                        assignment_ids=tuple(item.get("assignment_ids", ())),
                        handoff_received=item.get("handoff_received", False),
                        untrusted=item.get("untrusted", True),
                        result_code=item.get("result_code"),
                        diagnostic=item.get("diagnostic"),
                        evidence_sha256=item.get("evidence_sha256"),
                        created_at=item.get("created_at", ""),
                        updated_at=item.get("updated_at", ""),
                    )
                    for item in value.get("stages", ())
                ),
                integration=(
                    None
                    if value.get("integration") is None
                    else WorkflowIntegration(
                        team_id=value["integration"]["team_id"],
                        assignment_id=value["integration"]["assignment_id"],
                        worktree_id=value["integration"]["worktree_id"],
                        patch_sha256=value["integration"].get("patch_sha256"),
                        manifest_sha256=value["integration"].get("manifest_sha256"),
                        precondition_sha256=value["integration"].get("precondition_sha256"),
                        target_ref=value["integration"].get("target_ref"),
                        target_head=value["integration"].get("target_head"),
                        base_commit=value["integration"].get("base_commit"),
                        changed_paths=value["integration"]["changed_paths"],
                        status=value["integration"]["status"],
                        result_code=value["integration"]["result_code"],
                        diagnostic=value["integration"]["diagnostic"],
                        permission_decision=value["integration"].get(
                            "permission_decision", PermissionDecision.ALLOW.value
                        ),
                        permission_reason=value["integration"].get(
                            "permission_reason", "legacy_workflow_projection"
                        ),
                        host_accepted=value["integration"].get("host_accepted", False),
                        recorded_at=value["integration"].get("recorded_at", ""),
                        host_accepted_at=value["integration"].get("host_accepted_at"),
                    )
                ),
            )
        except (AssertionError, KeyError, TypeError, ValueError, WorkflowError) as error:
            if isinstance(error, WorkflowError):
                raise
            raise WorkflowError("workflow state JSON is invalid") from None


__all__ = [
    "WorkflowError",
    "WorkflowRole",
    "WorkflowPhase",
    "WorkflowVerdict",
    "WorkflowPacket",
    "WorkflowEvidence",
    "WorkflowStage",
    "WorkflowIntegration",
    "WorkflowState",
    "WorkflowOrchestrator",
]
