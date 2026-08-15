"""Durable Team schedule identity and deterministic dispatch policy.

This module deliberately stops at the schedule/run boundary in B5.1.  Child
execution and background supervision are layered on top in ``team_supervisor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from coquo.team_records import (
    MAX_TEAM_SCHEDULE_ASSIGNMENTS,
    MAX_TEAM_SCHEDULE_PARALLEL,
    TeamAssignmentPhase,
    TeamMemberState,
    TeamMemberStatus,
    TeamScheduleOutcome,
    TeamScheduleSource,
    TeamScheduleState,
    TeamStatus,
    TeamWorkItemState,
    TeamWorkStatus,
    TeamRecordError,
    canonical_team_id,
    utc_now,
)
from coquo.team_store import TeamInfo, TeamScheduleLease, TeamStore, TeamStoreError
from coquo.team_service import TeamAssignmentError, TeamAssignmentService


class TeamScheduleError(RuntimeError):
    """Raised when a Team schedule cannot safely advance."""


def _schedule_session_call(session, unlocked_name: str, locked_name: str):
    """Use the scheduler-only Session seam when available.

    Parent model waits can hold the ProjectSession lock while the coordinator
    continues independently; the fallback preserves compatibility with small
    test/session doubles that only expose the public Host wrapper.
    """
    return getattr(session, unlocked_name, getattr(session, locked_name))


@dataclass(frozen=True)
class TeamScheduleSelection:
    work_item: TeamWorkItemState
    member: TeamMemberState


@dataclass(frozen=True)
class TeamScheduleResult:
    team: TeamInfo
    schedule: TeamScheduleState


def select_ready_work(team: TeamInfo) -> tuple[TeamWorkItemState, ...]:
    """Return ready work in durable Team order, without mutating the ledger."""
    if team.status is not TeamStatus.OPEN:
        return ()
    return tuple(item for item in team.work_items if item.status is TeamWorkStatus.READY)


def select_member(team: TeamInfo, work_item: TeamWorkItemState) -> TeamMemberState | None:
    """Choose the least-used active/free member with Team-order tie breaks."""
    if work_item.status is not TeamWorkStatus.READY:
        return None
    busy = {
        assignment.member_id
        for assignment in team.assignments
        if assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
    }
    candidates = [
        (index, member)
        for index, member in enumerate(team.members)
        if member.status is TeamMemberStatus.ACTIVE and member.member_id not in busy
    ]
    if not candidates:
        return None
    counts = {
        member.member_id: sum(
            1
            for assignment in team.assignments
            if assignment.member_id == member.member_id and assignment.schedule_run_id is not None
        )
        for _, member in candidates
    }
    _, selected = min(candidates, key=lambda pair: (counts[pair[1].member_id], pair[0]))
    return selected


def select_next(team: TeamInfo) -> TeamScheduleSelection | None:
    for item in select_ready_work(team):
        member = select_member(team, item)
        if member is not None:
            return TeamScheduleSelection(item, member)
    return None


class TeamScheduleRun:
    """A started schedule's durable identity and held lease."""

    def __init__(
        self,
        service: TeamScheduleService,
        team_id: str,
        state: TeamScheduleState,
        lease: TeamScheduleLease,
    ) -> None:
        self._service = service
        self.team_id = team_id
        self.state = state
        self._lease = lease
        self._closed = False

    @property
    def schedule_run_id(self) -> str:
        return self.state.schedule_run_id

    def cancel(
        self, reason: str, *, source: TeamScheduleSource | str = TeamScheduleSource.HOST
    ) -> TeamScheduleState:
        self.state = self._service.cancel(self.team_id, self.schedule_run_id, reason, source=source)
        return self.state

    def finish(
        self,
        *,
        outcome: TeamScheduleOutcome | str,
        result_code: str = "ok",
        message: str = "",
    ) -> TeamScheduleState:
        latest = self._service.status(self.team_id, self.schedule_run_id)
        assignment_count = (
            len(latest.assignment_ids) if latest is not None else len(self.state.assignment_ids)
        )
        self.state = self._service.finish(
            self.team_id,
            self.schedule_run_id,
            outcome=outcome,
            assignment_count=assignment_count,
            result_code=result_code,
            message=message,
        )
        self.close()
        return self.state

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._lease.close()

    def __enter__(self) -> TeamScheduleRun:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TeamScheduleService:
    """Own schedule records and lease acquisition, but not Child execution."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.teams = TeamStore(self.workspace, uuid_factory=uuid_factory, clock=clock)
        self._uuid_factory = uuid_factory
        self._clock = clock

    def start(
        self,
        team_id: str,
        *,
        source: TeamScheduleSource | str = TeamScheduleSource.HOST,
        max_assignments: int = MAX_TEAM_SCHEDULE_ASSIGNMENTS,
        max_parallel: int = MAX_TEAM_SCHEDULE_PARALLEL,
        schedule_run_id: str | None = None,
    ) -> TeamScheduleRun:
        if (
            type(max_assignments) is not int
            or not 1 <= max_assignments <= MAX_TEAM_SCHEDULE_ASSIGNMENTS
        ):
            raise TeamScheduleError("schedule assignment limit must be between 1 and 32")
        if type(max_parallel) is not int or not 1 <= max_parallel <= MAX_TEAM_SCHEDULE_PARALLEL:
            raise TeamScheduleError("schedule parallel limit must be between 1 and 4")
        try:
            team = self.teams.inspect(team_id)
            if team.status is not TeamStatus.OPEN:
                raise TeamScheduleError("Team is closed")
            run_id = canonical_team_id(schedule_run_id) if schedule_run_id else self._new_id()
            lease = self.teams.acquire_schedule(team.team_id)
            try:
                state = self.teams.start_schedule(
                    team.team_id,
                    run_id,
                    source=source,
                    max_assignments=max_assignments,
                    max_parallel=max_parallel,
                )
            except BaseException:
                lease.close()
                raise
            return TeamScheduleRun(self, team.team_id, state, lease)
        except TeamStoreError as error:
            raise TeamScheduleError(str(error)) from None
        except TeamRecordError as error:
            raise TeamScheduleError(str(error)) from None

    def status(self, team_id: str, schedule_run_id: str | None = None) -> TeamScheduleState | None:
        try:
            team = self.teams.inspect(team_id)
        except TeamStoreError as error:
            raise TeamScheduleError(str(error)) from None
        if schedule_run_id is None:
            return team.schedules[-1] if team.schedules else None
        canonical = canonical_team_id(schedule_run_id)
        return next((item for item in team.schedules if item.schedule_run_id == canonical), None)

    def run(
        self,
        team_id: str,
        session,
        *,
        source: TeamScheduleSource | str = TeamScheduleSource.HOST,
        max_assignments: int = MAX_TEAM_SCHEDULE_ASSIGNMENTS,
        max_parallel: int = MAX_TEAM_SCHEDULE_PARALLEL,
    ) -> TeamScheduleState:
        """Start and synchronously execute one bounded schedule wave."""
        run = self.start(
            team_id,
            source=source,
            max_assignments=max_assignments,
            max_parallel=max_parallel,
        )
        return self.run_started(run, session)

    def run_started(self, run: TeamScheduleRun, session) -> TeamScheduleState:
        """Drive an already-started run through existing Team/Child services."""
        assignment_service = TeamAssignmentService(self.workspace)
        active: list[str] = []
        try:
            while True:
                team = self.teams.inspect(run.team_id)
                schedule = next(
                    item for item in team.schedules if item.schedule_run_id == run.schedule_run_id
                )
                if schedule.cancel_requested_at is not None:
                    for assignment_id in tuple(schedule.assignment_ids):
                        info = assignment_service.inspect(run.team_id, assignment_id)
                        if info.assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED:
                            continue
                        try:
                            _schedule_session_call(
                                session,
                                "_cancel_team_assignment_unlocked",
                                "cancel_team_assignment",
                            )(run.team_id, assignment_id, schedule.cancel_reason or "cancelled")
                        except Exception:
                            pass
                    self._observe_active(run.team_id, schedule.assignment_ids, session)
                    return run.finish(outcome=TeamScheduleOutcome.CANCELLED)

                # First reconcile work already admitted by this exact run.
                for assignment_id in tuple(schedule.assignment_ids):
                    info = assignment_service.inspect(run.team_id, assignment_id)
                    if info.assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED:
                        continue
                    if assignment_id not in active:
                        active.append(assignment_id)

                while (
                    len(active) < schedule.max_parallel
                    and len(schedule.assignment_ids) < schedule.max_assignments
                ):
                    current = self.teams.inspect(run.team_id)
                    selection = select_next(current)
                    if selection is None:
                        break
                    try:
                        info = assignment_service.create(
                            run.team_id,
                            selection.member.member_id,
                            selection.work_item.objective,
                            work_item_id=selection.work_item.work_item_id,
                            schedule_run_id=run.schedule_run_id,
                        )
                        _schedule_session_call(
                            session, "_prepare_team_assignment_unlocked", "prepare_team_assignment"
                        )(run.team_id, info.assignment.assignment_id)
                        _schedule_session_call(
                            session, "_start_team_assignment_unlocked", "start_team_assignment"
                        )(run.team_id, info.assignment.assignment_id)
                        active.append(info.assignment.assignment_id)
                    except (TeamAssignmentError, Exception) as error:
                        # A known assignment error is recorded as a bounded failed run;
                        # the durable assignment identity, if any, is left for recovery.
                        return run.finish(
                            outcome=TeamScheduleOutcome.FAILED,
                            result_code="dispatch_failed",
                            message=str(error)[:4096] or "dispatch failed",
                        )
                    schedule = next(
                        item
                        for item in self.teams.inspect(run.team_id).schedules
                        if item.schedule_run_id == run.schedule_run_id
                    )

                if active:
                    completed: list[str] = []
                    for assignment_id in tuple(active):
                        try:
                            _schedule_session_call(
                                session, "_wait_team_assignment_unlocked", "wait_team_assignment"
                            )(run.team_id, assignment_id, 30)
                        except Exception:
                            # Leave the exact assignment for the next recovery pass.
                            continue
                        latest = assignment_service.inspect(run.team_id, assignment_id)
                        if latest.assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED:
                            completed.append(assignment_id)
                    active = [
                        assignment_id for assignment_id in active if assignment_id not in completed
                    ]
                    if active:
                        return run.finish(
                            outcome=TeamScheduleOutcome.FAILED,
                            result_code="observation_pending",
                            message="owned assignment did not reach terminal observation",
                        )
                    continue

                current = self.teams.inspect(run.team_id)
                schedule = next(
                    item
                    for item in current.schedules
                    if item.schedule_run_id == run.schedule_run_id
                )
                if len(schedule.assignment_ids) >= schedule.max_assignments:
                    return run.finish(outcome=TeamScheduleOutcome.LIMIT_REACHED)
                if select_next(current) is None:
                    return run.finish(outcome=TeamScheduleOutcome.IDLE)
        except Exception as error:
            try:
                return run.finish(
                    outcome=TeamScheduleOutcome.FAILED,
                    result_code="scheduler_failed",
                    message=str(error)[:4096] or "scheduler failed",
                )
            except Exception:
                run.close()
                raise

    @staticmethod
    def _observe_active(team_id: str, assignment_ids: tuple[str, ...], session) -> None:
        for assignment_id in assignment_ids:
            try:
                _schedule_session_call(
                    session, "_wait_team_assignment_unlocked", "wait_team_assignment"
                )(team_id, assignment_id, 0)
            except Exception:
                continue

    def recover(self, team_id: str, schedule_run_id: str | None = None) -> TeamScheduleState:
        """Reconcile exact durable IDs without selecting work or invoking a Provider."""
        team = self.teams.inspect(team_id)
        schedule = (
            self.status(team.team_id, schedule_run_id)
            if schedule_run_id is not None
            else next((item for item in team.schedules if not item.status.terminal), None)
        )
        if schedule is None:
            raise TeamScheduleError("Team schedule run was not found")
        if schedule.status.terminal:
            return schedule
        lease = self.teams.acquire_schedule(team.team_id)
        try:
            latest = self.teams.inspect(team.team_id)
            current = next(
                item
                for item in latest.schedules
                if item.schedule_run_id == schedule.schedule_run_id
            )
            assignment_service = TeamAssignmentService(self.workspace)
            for assignment_id in current.assignment_ids:
                try:
                    assignment_service.recover(team.team_id, assignment_id, limit=1)
                except Exception:
                    # Recovery remains exact-ID and fail-closed; the schedule is not
                    # allowed to claim a terminal outcome while one assignment is live.
                    pass
            latest = self.teams.inspect(team.team_id)
            current = next(
                item
                for item in latest.schedules
                if item.schedule_run_id == schedule.schedule_run_id
            )
            if any(
                assignment_id
                not in {
                    item.assignment_id
                    for item in latest.assignments
                    if item.phase is TeamAssignmentPhase.TERMINAL_OBSERVED
                }
                for assignment_id in current.assignment_ids
            ):
                raise TeamScheduleError("Team schedule recovery found a still-owned assignment")
            if current.cancel_requested_at is not None:
                self.teams.finish_schedule(
                    team.team_id,
                    current.schedule_run_id,
                    outcome=TeamScheduleOutcome.CANCELLED,
                    assignment_count=len(current.assignment_ids),
                    result_code="recovered_cancel",
                    message="cancelled schedule recovered without new dispatch",
                )
            else:
                self.teams.finish_schedule(
                    team.team_id,
                    current.schedule_run_id,
                    outcome=TeamScheduleOutcome.INTERRUPTED,
                    assignment_count=len(current.assignment_ids),
                    result_code="process_lost",
                    message="schedule coordinator was not running",
                )
            return self.teams.inspect(team.team_id).schedules[-1]
        finally:
            lease.close()

    def cancel(
        self,
        team_id: str,
        schedule_run_id: str,
        reason: str,
        *,
        source: TeamScheduleSource | str = TeamScheduleSource.HOST,
    ) -> TeamScheduleState:
        try:
            return self.teams.cancel_schedule(team_id, schedule_run_id, reason, source=source)
        except TeamStoreError as error:
            raise TeamScheduleError(str(error)) from None

    def finish(
        self,
        team_id: str,
        schedule_run_id: str,
        *,
        outcome: TeamScheduleOutcome | str,
        assignment_count: int,
        result_code: str,
        message: str,
    ) -> TeamScheduleState:
        try:
            return self.teams.finish_schedule(
                team_id,
                schedule_run_id,
                outcome=outcome,
                assignment_count=assignment_count,
                result_code=result_code,
                message=message,
            )
        except TeamStoreError as error:
            raise TeamScheduleError(str(error)) from None

    def _new_id(self) -> str:
        value = self._uuid_factory()
        return canonical_team_id(str(value))


__all__ = [
    "TeamScheduleError",
    "TeamScheduleResult",
    "TeamScheduleRun",
    "TeamScheduleSelection",
    "TeamScheduleService",
    "select_member",
    "select_next",
    "select_ready_work",
]
