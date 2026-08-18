"""Host-only bridge between durable Tasks and Child/Team execution ledgers.

The bridge owns no execution state.  Task, Child, Team assignment, and Team
schedule ledgers remain authoritative for their own state; this module only
binds exact identities, verifies terminal evidence, and records one normalized
Task Stage outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from coquo.child_handoff import ChildHandoff, ChildHandoffError, publish_child_handoff
from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunInfo, ChildRunStore, ChildRunStoreError
from coquo.session_store import SessionStore, SessionStoreError
from coquo.task_records import (
    StageExecutionTarget,
    StageFailureReason,
)
from coquo.task_store import TaskInfo, TaskStore, TaskStoreError, TaskStageInfo
from coquo.team_records import (
    TeamAssignmentPhase,
    TeamScheduleFinished,
    TeamScheduleOutcome,
    TeamScheduleState,
)
from coquo.team_schedule import TeamScheduleError, TeamScheduleRun, TeamScheduleService
from coquo.team_service import (
    TeamAssignmentError,
    TeamAssignmentInfo,
    TeamAssignmentService,
)
from coquo.team_store import TeamInfo, TeamStore, TeamStoreError


class TaskBridgeError(RuntimeError):
    """Raised when a Task-to-external execution bridge cannot advance safely."""


class TaskBridgeOutcome(StrEnum):
    """Normalized bridge outcome values returned to Host callers."""

    PENDING = "pending"
    COMMITTED = "committed"
    FAILED = "failed"
    RECOVERY_REQUIRED = "recovery-required"


@dataclass(frozen=True)
class TaskBridgeResult:
    """One bounded projection of a Task Stage and its external evidence."""

    task: TaskInfo
    stage: TaskStageInfo
    outcome: str
    target: StageExecutionTarget
    child: ChildRunInfo | None = None
    assignment: TeamAssignmentInfo | None = None
    schedule: TeamScheduleState | None = None
    handoffs: tuple[ChildHandoff, ...] = ()
    result_code: str | None = None
    diagnostic: str | None = None


class TaskOrchestrationService:
    """Coordinate Task Stage admission and exact Child/Team convergence."""

    def __init__(
        self,
        workspace: Path,
        *,
        task_store: TaskStore | None = None,
        child_store: ChildRunStore | None = None,
        team_store: TeamStore | None = None,
        team_service: TeamAssignmentService | None = None,
        schedule_service: TeamScheduleService | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.tasks = task_store or TaskStore(self.workspace)
        self.children = child_store or ChildRunStore(self.workspace)
        self.teams = team_store or TeamStore(self.workspace)
        self.team_assignments = team_service or TeamAssignmentService(self.workspace)
        self.schedules = schedule_service or TeamScheduleService(self.workspace)
        self._clock = clock

    # ------------------------------------------------------------------
    # Admission: Task -> Child
    # ------------------------------------------------------------------
    def start_child_stage(
        self,
        task_id: str,
        objective: str,
        *,
        permission_mode: str = "read-only",
        approval_mode: str = "ask",
    ) -> TaskBridgeResult:
        """Start and durably bind one Task Stage to a Child Run.

        This method admits metadata only.  A caller with a live ProjectSession
        may subsequently call :meth:`run_child_stage`; no provider or worker is
        invoked here.
        """

        task = self._task(task_id)
        self._ensure_owner_session(task)
        child: ChildRunInfo | None = None
        with self.tasks.open(task.task_id) as writer:
            stage = writer.start_stage(objective)
            try:
                child = self.children.create(
                    stage.objective,
                    parent_session=task.owner_session_id,
                )
            except (ChildRunStoreError, SessionStoreError, OSError) as error:
                raise TaskBridgeError(
                    "Task Stage started but Child creation failed; recovery is required: "
                    + str(error)
                ) from None
            try:
                writer.delegate_stage(
                    target=StageExecutionTarget.CHILD,
                    child_run_id=child.child_run_id,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                )
            except TaskStoreError as error:
                raise TaskBridgeError(
                    "Child Run exists but Task delegation was not durably recorded; "
                    "recovery is required: " + str(error)
                ) from None
        return self._result_for_child(self._task(task.task_id), child)

    def run_child_stage(
        self,
        task_id: str,
        child_run_id: str,
        session: Any,
        *,
        background: bool = True,
    ) -> TaskBridgeResult:
        """Prepare and execute an admitted Child through a live parent Session."""

        task, stage = self._delegated_stage(task_id, StageExecutionTarget.CHILD)
        expected = stage.child_run_id
        if expected != child_run_id:
            raise TaskBridgeError("Child Run ID does not match the Task Stage delegation")
        self._ensure_session_owner(session, task.owner_session_id)
        child = self._validate_child(task, stage)
        if child.status is ChildRunStatus.QUEUED:
            session.prepare_child_run(child.child_run_id)
            child = self.children.inspect(child.child_run_id)
        if child.status in {ChildRunStatus.READY, ChildRunStatus.ADMITTED}:
            if background:
                child = session.start_child_run(child.child_run_id)
            else:
                child = session.run_child_run(child.child_run_id)
        elif child.status is ChildRunStatus.RUNNING and not background:
            raise TaskBridgeError(
                "Child Run is already running; observe it or use background execution"
            )
        return self.observe_child_stage(task.task_id, child_run_id)

    def observe_child_stage(
        self,
        task_id: str,
        child_run_id: str | None = None,
    ) -> TaskBridgeResult:
        """Collect one terminal Child handoff and converge the Task Stage."""

        task, stage = self._delegated_stage(task_id, StageExecutionTarget.CHILD)
        expected = stage.child_run_id
        if expected is None or (child_run_id is not None and child_run_id != expected):
            raise TaskBridgeError("Child Run ID does not match the Task Stage delegation")
        child = self._validate_child(task, stage)
        if stage.outcome == "committed":
            handoff = self._published_child_handoff(child.child_run_id)
            return self._result_for_child(self._task(task.task_id), child, handoff=handoff)
        if stage.outcome == "failed":
            return self._result_for_child(self._task(task.task_id), child)
        if child.status not in {
            ChildRunStatus.COMPLETED,
            ChildRunStatus.FAILED,
            ChildRunStatus.CANCELLED,
            ChildRunStatus.INTERRUPTED,
        }:
            return self._result_for_child(self._task(task.task_id), child)
        try:
            handoff = publish_child_handoff(self.workspace, child.child_run_id)
        except (ChildHandoffError, ChildRunStoreError, OSError) as error:
            return self._fail_external(
                task,
                stage,
                target=StageExecutionTarget.CHILD,
                reason=StageFailureReason.HOST_ERROR,
                result_code="child-handoff-unavailable",
                diagnostic=str(error),
                child=child,
            )
        if handoff.outcome == ChildRunStatus.COMPLETED.value:
            return self._commit_external(
                task,
                stage,
                target=StageExecutionTarget.CHILD,
                evidence_sha256=handoff.handoff_sha256,
                terminal_record_sequence=handoff.terminal_record_sequence,
                child=child,
                handoff=handoff,
            )
        return self._fail_external(
            task,
            stage,
            target=StageExecutionTarget.CHILD,
            reason=_child_failure_reason(handoff),
            result_code=handoff.result_code,
            child=child,
            handoff=handoff,
        )

    # ------------------------------------------------------------------
    # Admission: Task -> Team assignment
    # ------------------------------------------------------------------
    def start_team_assignment_stage(
        self,
        task_id: str,
        team_id: str,
        member_id: str,
        *,
        objective: str | None = None,
        work_item_id: str | None = None,
        permission_mode: str = "read-only",
        approval_mode: str = "ask",
    ) -> TaskBridgeResult:
        """Start and bind one Task Stage to one durable Team assignment."""

        task = self._task(task_id)
        self._ensure_owner_session(task)
        team = self._team(team_id)
        self._ensure_team_owner(task, team)
        assignment: TeamAssignmentInfo | None = None
        with self.tasks.open(task.task_id) as writer:
            stage = writer.start_stage(objective or task.objective)
            try:
                assignment = self.team_assignments.create(
                    team.team_id,
                    member_id,
                    stage.objective,
                    work_item_id=work_item_id,
                    parent_permission_mode=permission_mode,
                )
            except (TeamAssignmentError, TeamStoreError, OSError) as error:
                raise TaskBridgeError(
                    "Task Stage started but Team assignment creation failed; "
                    "recovery is required: " + str(error)
                ) from None
            try:
                writer.delegate_stage(
                    target=StageExecutionTarget.TEAM_ASSIGNMENT,
                    child_run_id=assignment.assignment.child_run_id,
                    team_id=team.team_id,
                    assignment_id=assignment.assignment.assignment_id,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                )
            except TaskStoreError as error:
                raise TaskBridgeError(
                    "Team assignment exists but Task delegation was not durably recorded; "
                    "recovery is required: " + str(error)
                ) from None
        return self._result_for_assignment(self._task(task.task_id), assignment)

    def run_team_assignment_stage(
        self,
        task_id: str,
        assignment_id: str,
        session: Any,
        *,
        background: bool = True,
    ) -> TaskBridgeResult:
        """Prepare and execute an admitted Team assignment."""

        task, stage = self._delegated_stage(task_id, StageExecutionTarget.TEAM_ASSIGNMENT)
        if stage.assignment_id != assignment_id or stage.team_id is None:
            raise TaskBridgeError("Team assignment identity does not match Task delegation")
        self._ensure_session_owner(session, task.owner_session_id)
        assignment = self._validate_assignment(task, stage)
        if assignment.child is None:
            raise TaskBridgeError(assignment.child_error or "Team assignment Child is unavailable")
        if assignment.child.status is ChildRunStatus.QUEUED:
            session.prepare_team_assignment(stage.team_id, assignment_id)
            assignment = self.team_assignments.inspect(stage.team_id, assignment_id)
        if assignment.child is not None and assignment.child.status in {
            ChildRunStatus.READY,
            ChildRunStatus.ADMITTED,
        }:
            if background:
                session.start_team_assignment(stage.team_id, assignment_id)
            else:
                session.run_team_assignment(stage.team_id, assignment_id)
        elif assignment.child.status is ChildRunStatus.RUNNING and not background:
            raise TaskBridgeError(
                "Team assignment Child is already running; observe it or use background execution"
            )
        return self.observe_team_assignment_stage(task.task_id, assignment_id)

    def observe_team_assignment_stage(
        self,
        task_id: str,
        assignment_id: str | None = None,
    ) -> TaskBridgeResult:
        """Observe exact Team/Child handoff evidence and converge the Task Stage."""

        task, stage = self._delegated_stage(task_id, StageExecutionTarget.TEAM_ASSIGNMENT)
        if stage.team_id is None or stage.assignment_id is None:
            raise TaskBridgeError("Team assignment identity is incomplete")
        if assignment_id is not None and assignment_id != stage.assignment_id:
            raise TaskBridgeError("Team assignment ID does not match Task delegation")
        assignment = self._validate_assignment(task, stage)
        if assignment.child is None:
            return self._fail_external(
                task,
                stage,
                target=StageExecutionTarget.TEAM_ASSIGNMENT,
                reason=StageFailureReason.HOST_ERROR,
                result_code="team-child-unavailable",
                diagnostic=assignment.child_error,
                assignment=assignment,
            )
        if stage.outcome == "committed":
            handoff = self._published_child_handoff(assignment.assignment.child_run_id)
            return self._result_for_assignment(
                self._task(task.task_id), assignment, handoff=handoff
            )
        if stage.outcome == "failed":
            return self._result_for_assignment(self._task(task.task_id), assignment)
        if assignment.child.status not in {
            ChildRunStatus.COMPLETED,
            ChildRunStatus.FAILED,
            ChildRunStatus.CANCELLED,
            ChildRunStatus.INTERRUPTED,
        }:
            return self._result_for_assignment(self._task(task.task_id), assignment)
        try:
            handoff = self.team_assignments.observe_terminal(
                stage.team_id,
                stage.assignment_id,
            )
            assignment = self._validate_assignment(task, stage)
        except (TeamAssignmentError, TeamStoreError, ChildHandoffError, OSError) as error:
            return self._fail_external(
                task,
                stage,
                target=StageExecutionTarget.TEAM_ASSIGNMENT,
                reason=StageFailureReason.HOST_ERROR,
                result_code="team-observation-failed",
                diagnostic=str(error),
                assignment=assignment,
            )
        if handoff.outcome == ChildRunStatus.COMPLETED.value:
            return self._commit_external(
                task,
                stage,
                target=StageExecutionTarget.TEAM_ASSIGNMENT,
                evidence_sha256=handoff.handoff_sha256,
                terminal_record_sequence=handoff.terminal_record_sequence,
                assignment=assignment,
                handoff=handoff,
            )
        return self._fail_external(
            task,
            stage,
            target=StageExecutionTarget.TEAM_ASSIGNMENT,
            reason=_child_failure_reason(handoff),
            result_code=handoff.result_code,
            assignment=assignment,
            handoff=handoff,
        )

    # ------------------------------------------------------------------
    # Admission and execution: Task -> Team schedule
    # ------------------------------------------------------------------
    def start_team_schedule_stage(
        self,
        task_id: str,
        team_id: str,
        *,
        max_assignments: int = 32,
        max_parallel: int = 4,
        permission_mode: str = "read-only",
        approval_mode: str = "ask",
    ) -> TaskBridgeResult:
        """Start and bind a schedule identity without dispatching workers.

        The schedule's assignment roster is intentionally empty at this point:
        the scheduler admits assignments lazily.  Observation resolves the
        exact roster from the Team ledger before Task terminal commit.
        """

        task = self._task(task_id)
        self._ensure_owner_session(task)
        team = self._team(team_id)
        self._ensure_team_owner(task, team)
        schedule: TeamScheduleState | None = None
        run: TeamScheduleRun | None = None
        with self.tasks.open(task.task_id) as writer:
            writer.start_stage(task.objective)
            try:
                run = self.schedules.start(
                    team.team_id,
                    max_assignments=max_assignments,
                    max_parallel=max_parallel,
                    parent_permission_mode=permission_mode,
                )
                schedule = run.state
                writer.delegate_stage(
                    target=StageExecutionTarget.TEAM_SCHEDULE,
                    team_id=team.team_id,
                    schedule_run_id=schedule.schedule_run_id,
                    assignment_ids=(),
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                )
            except (TeamScheduleError, TeamStoreError, TaskStoreError, OSError) as error:
                raise TaskBridgeError(
                    "Task/schedule admission did not reach a durable common boundary; "
                    "recovery is required: " + str(error)
                ) from None
            finally:
                if run is not None:
                    run.close()
        if schedule is None:
            raise TaskBridgeError("Team schedule admission returned no schedule state")
        return self._result_for_schedule(self._task(task.task_id), team, schedule)

    def run_team_schedule_stage(
        self,
        task_id: str,
        schedule_run_id: str,
        session: Any,
    ) -> TaskBridgeResult:
        """Drive one admitted bounded schedule through the existing Team runner."""

        task, stage = self._delegated_stage(task_id, StageExecutionTarget.TEAM_SCHEDULE)
        if stage.team_id is None or stage.schedule_run_id != schedule_run_id:
            raise TaskBridgeError("Team schedule identity does not match Task delegation")
        self._ensure_session_owner(session, task.owner_session_id)
        schedule = self._schedule(stage.team_id, schedule_run_id)
        if not schedule.status.terminal:
            run = self.schedules.resume(stage.team_id, schedule_run_id)
            try:
                schedule = self.schedules.run_started(run, session)
            finally:
                run.close()
        return self.observe_team_schedule_stage(task.task_id, schedule_run_id)

    def observe_team_schedule_stage(
        self,
        task_id: str,
        schedule_run_id: str | None = None,
    ) -> TaskBridgeResult:
        """Aggregate exact assignment handoffs and converge one schedule Stage."""

        task, stage = self._delegated_stage(task_id, StageExecutionTarget.TEAM_SCHEDULE)
        if stage.team_id is None or stage.schedule_run_id is None:
            raise TaskBridgeError("Team schedule identity is incomplete")
        if schedule_run_id is not None and schedule_run_id != stage.schedule_run_id:
            raise TaskBridgeError("Team schedule ID does not match Task delegation")
        team = self._team(stage.team_id)
        self._ensure_team_owner(task, team)
        schedule = self._schedule(stage.team_id, stage.schedule_run_id)
        assignment_service = self.team_assignments
        assignments: list[TeamAssignmentInfo] = []
        handoffs: list[ChildHandoff] = []
        for assignment_id in schedule.assignment_ids:
            try:
                assignment = assignment_service.inspect(stage.team_id, assignment_id)
            except (TeamAssignmentError, TeamStoreError) as error:
                return self._fail_external(
                    task,
                    stage,
                    target=StageExecutionTarget.TEAM_SCHEDULE,
                    reason=StageFailureReason.HOST_ERROR,
                    result_code="schedule-assignment-unavailable",
                    diagnostic=str(error),
                    schedule=schedule,
                )
            assignments.append(assignment)
            if assignment.child is None:
                return self._fail_external(
                    task,
                    stage,
                    target=StageExecutionTarget.TEAM_SCHEDULE,
                    reason=StageFailureReason.HOST_ERROR,
                    result_code="schedule-child-unavailable",
                    diagnostic=assignment.child_error,
                    schedule=schedule,
                )
            if assignment.assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED:
                if assignment.child.status not in {
                    ChildRunStatus.COMPLETED,
                    ChildRunStatus.FAILED,
                    ChildRunStatus.CANCELLED,
                    ChildRunStatus.INTERRUPTED,
                }:
                    continue
                try:
                    handoff = assignment_service.observe_terminal(
                        stage.team_id, assignment.assignment.assignment_id
                    )
                except (TeamAssignmentError, TeamStoreError, ChildHandoffError, OSError) as error:
                    return self._fail_external(
                        task,
                        stage,
                        target=StageExecutionTarget.TEAM_SCHEDULE,
                        reason=StageFailureReason.HOST_ERROR,
                        result_code="schedule-observation-failed",
                        diagnostic=str(error),
                        schedule=schedule,
                    )
                handoffs.append(handoff)
            else:
                try:
                    handoffs.append(
                        self._published_child_handoff(assignment.assignment.child_run_id)
                    )
                except (ChildHandoffError, ChildRunStoreError, OSError) as error:
                    return self._fail_external(
                        task,
                        stage,
                        target=StageExecutionTarget.TEAM_SCHEDULE,
                        reason=StageFailureReason.HOST_ERROR,
                        result_code="schedule-handoff-unavailable",
                        diagnostic=str(error),
                        schedule=schedule,
                    )
        schedule = self._schedule(stage.team_id, stage.schedule_run_id)
        if stage.outcome == "committed":
            return self._result_for_schedule(
                self._task(task.task_id), team, schedule, handoffs=tuple(handoffs)
            )
        if stage.outcome == "failed":
            return self._result_for_schedule(
                self._task(task.task_id), team, schedule, handoffs=tuple(handoffs)
            )
        if not schedule.status.terminal:
            return self._result_for_schedule(
                self._task(task.task_id), team, schedule, handoffs=tuple(handoffs)
            )
        if any(
            assignment.assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
            for assignment in assignments
        ):
            return self._fail_external(
                task,
                stage,
                target=StageExecutionTarget.TEAM_SCHEDULE,
                reason=StageFailureReason.INTERRUPTED,
                result_code="schedule-terminal-with-live-assignment",
                schedule=schedule,
                handoffs=tuple(handoffs),
            )
        if schedule.outcome in {
            TeamScheduleOutcome.FAILED,
            TeamScheduleOutcome.CANCELLED,
            TeamScheduleOutcome.INTERRUPTED,
        }:
            return self._fail_external(
                task,
                stage,
                target=StageExecutionTarget.TEAM_SCHEDULE,
                reason=_schedule_failure_reason(schedule),
                result_code=schedule.result_code or schedule.outcome.value,
                schedule=schedule,
                handoffs=tuple(handoffs),
            )
        if any(handoff.outcome != ChildRunStatus.COMPLETED.value for handoff in handoffs):
            failed = next(
                handoff for handoff in handoffs if handoff.outcome != ChildRunStatus.COMPLETED.value
            )
            return self._fail_external(
                task,
                stage,
                target=StageExecutionTarget.TEAM_SCHEDULE,
                reason=_child_failure_reason(failed),
                result_code=failed.result_code,
                schedule=schedule,
                handoffs=tuple(handoffs),
            )
        evidence = _schedule_evidence_digest(schedule, handoffs)
        terminal_sequence = _schedule_terminal_sequence(
            self.teams.replay_state(stage.team_id), schedule
        )
        return self._commit_external(
            task,
            stage,
            target=StageExecutionTarget.TEAM_SCHEDULE,
            evidence_sha256=evidence,
            terminal_record_sequence=terminal_sequence,
            schedule=schedule,
            handoffs=tuple(handoffs),
        )

    # ------------------------------------------------------------------
    # Shared validation and projections
    # ------------------------------------------------------------------
    def _task(self, task_id: str) -> TaskInfo:
        try:
            return self.tasks.inspect(task_id)
        except TaskStoreError as error:
            raise TaskBridgeError(str(error)) from None

    def _team(self, team_id: str) -> TeamInfo:
        try:
            return self.teams.inspect(team_id)
        except TeamStoreError as error:
            raise TaskBridgeError(str(error)) from None

    def _schedule(self, team_id: str, schedule_run_id: str) -> TeamScheduleState:
        try:
            schedule = self.schedules.status(team_id, schedule_run_id)
        except TeamScheduleError as error:
            raise TaskBridgeError(str(error)) from None
        if schedule is None:
            raise TaskBridgeError("Team schedule run was not found")
        return schedule

    @staticmethod
    def _ensure_owner_session(task: TaskInfo) -> None:
        try:
            SessionStore(Path(task.workspace)).inspect(task.owner_session_id)
        except SessionStoreError as error:
            raise TaskBridgeError(f"Task owner Session is unavailable: {error}") from None

    @staticmethod
    def _ensure_session_owner(session: Any, expected: str) -> None:
        actual = getattr(getattr(session, "_writer", None), "session_id", None)
        if actual != expected:
            raise TaskBridgeError("live parent Session does not own the Task")

    @staticmethod
    def _ensure_team_owner(task: TaskInfo, team: TeamInfo) -> None:
        if team.owner_session_id != task.owner_session_id:
            raise TaskBridgeError("Team owner Session does not match Task owner Session")

    def _delegated_stage(
        self,
        task_id: str,
        target: StageExecutionTarget,
    ) -> tuple[TaskInfo, TaskStageInfo]:
        task = self._task(task_id)
        if not task.stages:
            raise TaskBridgeError("Task has no Stage")
        stage = task.stages[-1]
        if stage.delegation_target is not target:
            raise TaskBridgeError("latest Task Stage is not delegated to the requested target")
        return task, stage

    def _validate_child(self, task: TaskInfo, stage: TaskStageInfo) -> ChildRunInfo:
        if stage.child_run_id is None:
            raise TaskBridgeError("Task Child delegation has no Child Run ID")
        try:
            child = self.children.inspect(stage.child_run_id)
        except ChildRunStoreError as error:
            raise TaskBridgeError(str(error)) from None
        if child.workspace_fingerprint != task.workspace_fingerprint:
            raise TaskBridgeError("Child Run workspace does not match Task")
        if child.parent_session_id != task.owner_session_id:
            raise TaskBridgeError("Child Run parent Session does not match Task owner")
        if child.objective != stage.objective:
            raise TaskBridgeError("Child Run objective does not match Task Stage")
        return child

    def _validate_assignment(self, task: TaskInfo, stage: TaskStageInfo) -> TeamAssignmentInfo:
        if stage.team_id is None or stage.assignment_id is None or stage.child_run_id is None:
            raise TaskBridgeError("Task Team assignment delegation identity is incomplete")
        try:
            assignment = self.team_assignments.inspect(stage.team_id, stage.assignment_id)
        except (TeamAssignmentError, TeamStoreError) as error:
            raise TaskBridgeError(str(error)) from None
        self._ensure_team_owner(task, assignment.team)
        if assignment.assignment.child_run_id != stage.child_run_id:
            raise TaskBridgeError("Team assignment Child does not match Task delegation")
        if assignment.assignment.objective != stage.objective:
            raise TaskBridgeError("Team assignment objective does not match Task Stage")
        if assignment.child is not None:
            if assignment.child.parent_session_id != task.owner_session_id:
                raise TaskBridgeError("Team assignment Child parent does not match Task owner")
            if assignment.child.workspace_fingerprint != task.workspace_fingerprint:
                raise TaskBridgeError("Team assignment Child workspace does not match Task")
        return assignment

    def _commit_external(
        self,
        task: TaskInfo,
        stage: TaskStageInfo,
        *,
        target: StageExecutionTarget,
        evidence_sha256: str,
        terminal_record_sequence: int,
        child: ChildRunInfo | None = None,
        assignment: TeamAssignmentInfo | None = None,
        schedule: TeamScheduleState | None = None,
        handoff: ChildHandoff | None = None,
        handoffs: tuple[ChildHandoff, ...] = (),
    ) -> TaskBridgeResult:
        try:
            with self.tasks.open(task.task_id) as writer:
                writer.commit_external_stage(
                    evidence_sha256,
                    terminal_record_sequence,
                    target=target,
                )
        except TaskStoreError as error:
            raise TaskBridgeError(
                "external evidence is verified but Task terminal commit requires recovery: "
                + str(error)
            ) from None
        all_handoffs = handoffs or ((handoff,) if handoff is not None else ())
        return self._make_result(
            self._task(task.task_id),
            target,
            TaskBridgeOutcome.COMMITTED,
            child=child,
            assignment=assignment,
            schedule=schedule,
            handoffs=all_handoffs,
        )

    def _fail_external(
        self,
        task: TaskInfo,
        stage: TaskStageInfo,
        *,
        target: StageExecutionTarget,
        reason: StageFailureReason,
        result_code: str,
        diagnostic: str | None = None,
        child: ChildRunInfo | None = None,
        assignment: TeamAssignmentInfo | None = None,
        schedule: TeamScheduleState | None = None,
        handoff: ChildHandoff | None = None,
        handoffs: tuple[ChildHandoff, ...] = (),
    ) -> TaskBridgeResult:
        try:
            with self.tasks.open(task.task_id) as writer:
                writer.fail_external_stage(reason, result_code, target=target)
        except TaskStoreError as error:
            raise TaskBridgeError(
                "external failure is known but Task failure commit requires recovery: " + str(error)
            ) from None
        all_handoffs = handoffs or ((handoff,) if handoff is not None else ())
        return self._make_result(
            self._task(task.task_id),
            target,
            TaskBridgeOutcome.FAILED,
            child=child,
            assignment=assignment,
            schedule=schedule,
            handoffs=all_handoffs,
            result_code=result_code,
            diagnostic=diagnostic,
        )

    def _published_child_handoff(self, child_run_id: str) -> ChildHandoff:
        try:
            return publish_child_handoff(self.workspace, child_run_id)
        except (ChildHandoffError, ChildRunStoreError, OSError) as error:
            raise TaskBridgeError(str(error)) from None

    def _result_for_child(
        self,
        task: TaskInfo,
        child: ChildRunInfo,
        *,
        handoff: ChildHandoff | None = None,
    ) -> TaskBridgeResult:
        stage = task.stages[-1]
        outcome = (
            TaskBridgeOutcome.COMMITTED
            if stage.outcome == "committed"
            else TaskBridgeOutcome.FAILED
            if stage.outcome == "failed"
            else TaskBridgeOutcome.PENDING
        )
        return self._make_result(
            task,
            StageExecutionTarget.CHILD,
            outcome,
            child=child,
            handoffs=((handoff,) if handoff is not None else ()),
        )

    def _result_for_assignment(
        self,
        task: TaskInfo,
        assignment: TeamAssignmentInfo,
        *,
        handoff: ChildHandoff | None = None,
    ) -> TaskBridgeResult:
        stage = task.stages[-1]
        outcome = (
            TaskBridgeOutcome.COMMITTED
            if stage.outcome == "committed"
            else TaskBridgeOutcome.FAILED
            if stage.outcome == "failed"
            else TaskBridgeOutcome.PENDING
        )
        return self._make_result(
            task,
            StageExecutionTarget.TEAM_ASSIGNMENT,
            outcome,
            assignment=assignment,
            handoffs=((handoff,) if handoff is not None else ()),
        )

    def _result_for_schedule(
        self,
        task: TaskInfo,
        team: TeamInfo,
        schedule: TeamScheduleState,
        *,
        handoffs: tuple[ChildHandoff, ...] = (),
    ) -> TaskBridgeResult:
        stage = task.stages[-1]
        outcome = (
            TaskBridgeOutcome.COMMITTED
            if stage.outcome == "committed"
            else TaskBridgeOutcome.FAILED
            if stage.outcome == "failed"
            else TaskBridgeOutcome.PENDING
        )
        return self._make_result(
            task,
            StageExecutionTarget.TEAM_SCHEDULE,
            outcome,
            schedule=schedule,
            handoffs=handoffs,
        )

    def _make_result(
        self,
        task: TaskInfo,
        target: StageExecutionTarget,
        outcome: str,
        *,
        child: ChildRunInfo | None = None,
        assignment: TeamAssignmentInfo | None = None,
        schedule: TeamScheduleState | None = None,
        handoffs: tuple[ChildHandoff, ...] = (),
        result_code: str | None = None,
        diagnostic: str | None = None,
    ) -> TaskBridgeResult:
        return TaskBridgeResult(
            task=task,
            stage=task.stages[-1],
            outcome=outcome,
            target=target,
            child=child,
            assignment=assignment,
            schedule=schedule,
            handoffs=handoffs,
            result_code=result_code,
            diagnostic=diagnostic,
        )


def _child_failure_reason(handoff: ChildHandoff) -> StageFailureReason:
    if handoff.outcome == ChildRunStatus.CANCELLED.value:
        return StageFailureReason.CANCELLED
    if handoff.outcome == ChildRunStatus.INTERRUPTED.value:
        return StageFailureReason.INTERRUPTED
    if handoff.terminal_record_type == "child_run_preparation_failed":
        return StageFailureReason.HOST_ERROR
    return StageFailureReason.PROVIDER_ERROR


def _schedule_failure_reason(schedule: TeamScheduleState) -> StageFailureReason:
    if schedule.outcome is TeamScheduleOutcome.CANCELLED:
        return StageFailureReason.CANCELLED
    if schedule.outcome is TeamScheduleOutcome.INTERRUPTED:
        return StageFailureReason.INTERRUPTED
    return StageFailureReason.HOST_ERROR


def _schedule_evidence_digest(
    schedule: TeamScheduleState,
    handoffs: tuple[ChildHandoff, ...],
) -> str:
    manifest = {
        "assignment_ids": list(schedule.assignment_ids),
        "handoff_sha256": [item.handoff_sha256 for item in handoffs],
        "outcome": schedule.outcome.value if schedule.outcome is not None else None,
        "result_code": schedule.result_code,
        "schedule_run_id": schedule.schedule_run_id,
        "status": schedule.status.value,
    }
    return hashlib.sha256(
        (
            "coquo-task-team-schedule-evidence-v1\0"
            + json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        ).encode("utf-8")
    ).hexdigest()


def _schedule_terminal_sequence(state, schedule: TeamScheduleState) -> int:
    matches = [
        record.sequence
        for record in state.records
        if isinstance(record, TeamScheduleFinished)
        and record.schedule_run_id == schedule.schedule_run_id
    ]
    if not matches:
        raise TaskBridgeError("terminal Team schedule has no durable finish record")
    return matches[-1]


__all__ = [
    "TaskBridgeError",
    "TaskBridgeOutcome",
    "TaskBridgeResult",
    "TaskOrchestrationService",
]
