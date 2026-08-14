"""Cross-ledger Team assignment coordination without owning Child execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from coquo.child_run_store import ChildRunInfo, ChildRunStore, ChildRunStoreError
from coquo.child_run_records import ChildRunStatus
from coquo.team_records import (
    TeamAssignmentPhase,
    TeamAssignmentState,
    TeamMemberState,
    TeamMemberStatus,
    TeamRecordError,
    TeamStatus,
    TeamWorkStatus,
    canonical_team_assignment_objective,
    canonical_team_id,
    utc_now,
)
from coquo.team_store import TeamInfo, TeamStore, TeamStoreError
from coquo.team_messaging import TeamMessageError, TeamMessagingService


class TeamAssignmentError(RuntimeError):
    """Raised when an assignment saga cannot safely advance."""

    def __init__(
        self,
        message: str,
        *,
        team_id: str | None = None,
        assignment_id: str | None = None,
        child_run_id: str | None = None,
    ) -> None:
        self.team_id = team_id
        self.assignment_id = assignment_id
        self.child_run_id = child_run_id
        super().__init__(message)


@dataclass(frozen=True)
class TeamAssignmentInfo:
    team: TeamInfo
    member: TeamMemberState
    assignment: TeamAssignmentState
    child: ChildRunInfo | None
    child_error: str | None = None

    @property
    def phase(self) -> TeamAssignmentPhase:
        return self.assignment.phase


@dataclass(frozen=True)
class TeamRecoveryDiagnostic:
    assignment_id: str | None
    outcome: str
    message: str


@dataclass(frozen=True)
class TeamRecoveryResult:
    recovered: tuple[TeamAssignmentInfo, ...]
    diagnostics: tuple[TeamRecoveryDiagnostic, ...]


class TeamAssignmentService:
    """Coordinate durable Team intent and exact Child provenance."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self._uuid_factory = uuid_factory
        self._clock = clock
        self.teams = TeamStore(self.workspace, clock=clock)
        self.children = ChildRunStore(self.workspace, clock=clock)
        self.messaging = TeamMessagingService(self.workspace)

    def create(
        self,
        team_id: str,
        member_id: str,
        objective: str,
        *,
        work_item_id: str | None = None,
    ) -> TeamAssignmentInfo:
        team = self._inspect_team(team_id)
        canonical_team = team.team_id
        canonical_member = self._canonical_id(member_id, "member ID")
        try:
            objective = canonical_team_assignment_objective(objective)
        except TeamRecordError as error:
            raise TeamAssignmentError(str(error), team_id=canonical_team) from None
        member = self._member(team, canonical_member)
        if team.status is not TeamStatus.OPEN:
            raise TeamAssignmentError("Team is closed", team_id=canonical_team)
        if member.status is not TeamMemberStatus.ACTIVE:
            raise TeamAssignmentError("Team member is not active", team_id=canonical_team)
        if any(
            assignment.member_id == canonical_member
            and assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
            for assignment in team.assignments
        ):
            raise TeamAssignmentError(
                "Team member already has a pending assignment", team_id=canonical_team
            )
        canonical_work: str | None = None
        if work_item_id is not None:
            canonical_work = self._canonical_id(work_item_id, "work item ID")
            work = next(
                (item for item in team.work_items if item.work_item_id == canonical_work), None
            )
            if work is None or work.status.value != "ready":
                raise TeamAssignmentError("Team work item is not ready", team_id=canonical_team)
        assignment_id = self._new_id("assignment ID")
        child_run_id = self._new_id("Child Run ID")
        try:
            created = self.teams.create_assignment(
                canonical_team,
                assignment_id,
                canonical_member,
                child_run_id,
                objective,
                work_item_id=canonical_work,
            )
        except TeamStoreError as error:
            raise TeamAssignmentError(
                str(error),
                team_id=canonical_team,
                assignment_id=assignment_id,
                child_run_id=child_run_id,
            ) from None
        try:
            self.children.create_for_team(
                objective,
                child_run_id=child_run_id,
                parent_session=team.owner_session_id,
                team_id=canonical_team,
                member_id=canonical_member,
                assignment_id=assignment_id,
                assigned_at=created.created_at,
            )
        except (ChildRunStoreError, OSError) as error:
            raise TeamAssignmentError(
                f"Child Run creation requires recovery: {error}",
                team_id=canonical_team,
                assignment_id=assignment_id,
                child_run_id=child_run_id,
            ) from None
        try:
            self.teams.bind_assignment(canonical_team, assignment_id, child_run_id)
        except TeamStoreError as error:
            raise TeamAssignmentError(
                f"Team Child binding requires recovery: {error}",
                team_id=canonical_team,
                assignment_id=assignment_id,
                child_run_id=child_run_id,
            ) from None
        return self.inspect(canonical_team, assignment_id)

    def inspect(self, team_id: str, assignment_id: str) -> TeamAssignmentInfo:
        team = self._inspect_team(team_id)
        canonical_assignment = self._canonical_id(assignment_id, "assignment ID")
        assignment = next(
            (item for item in team.assignments if item.assignment_id == canonical_assignment),
            None,
        )
        if assignment is None:
            raise TeamAssignmentError(
                "Team assignment was not found",
                team_id=team.team_id,
                assignment_id=canonical_assignment,
            )
        member = self._member(team, assignment.member_id)
        child, child_error = self._child_projection(team, assignment)
        return TeamAssignmentInfo(team, member, assignment, child, child_error)

    def list(self, team_id: str, *, limit: int = 100) -> tuple[TeamAssignmentInfo, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TeamAssignmentError("assignment list limit must be between 1 and 100")
        team = self._inspect_team(team_id)
        return tuple(
            self.inspect(team.team_id, assignment.assignment_id)
            for assignment in team.assignments[:limit]
        )

    def recover(
        self,
        team_id: str,
        assignment_id: str | None = None,
        *,
        limit: int = 100,
    ) -> TeamRecoveryResult:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TeamAssignmentError("assignment recovery limit must be between 1 and 100")
        team = self._inspect_team(team_id)
        selected = team.assignments
        canonical_assignment: str | None = None
        if assignment_id is not None:
            canonical_assignment = self._canonical_id(assignment_id, "assignment ID")
            selected = tuple(
                assignment
                for assignment in selected
                if assignment.assignment_id == canonical_assignment
            )
            if not selected:
                raise TeamAssignmentError(
                    "Team assignment was not found",
                    team_id=team.team_id,
                    assignment_id=canonical_assignment,
                )
        recovered: list[TeamAssignmentInfo] = []
        diagnostics: list[TeamRecoveryDiagnostic] = []
        for assignment in selected[:limit]:
            if assignment.phase is TeamAssignmentPhase.PENDING_CHILD:
                try:
                    self.children.create_for_team(
                        assignment.objective,
                        child_run_id=assignment.child_run_id,
                        parent_session=team.owner_session_id,
                        team_id=team.team_id,
                        member_id=assignment.member_id,
                        assignment_id=assignment.assignment_id,
                        assigned_at=assignment.created_at,
                    )
                    self.teams.bind_assignment(
                        team.team_id, assignment.assignment_id, assignment.child_run_id
                    )
                    recovered.append(self.inspect(team.team_id, assignment.assignment_id))
                except (TeamStoreError, ChildRunStoreError, OSError) as error:
                    diagnostics.append(
                        TeamRecoveryDiagnostic(
                            assignment.assignment_id,
                            "blocked",
                            str(error),
                        )
                    )
            elif assignment.phase is TeamAssignmentPhase.CHILD_BOUND:
                try:
                    info = self.inspect(team.team_id, assignment.assignment_id)
                    if info.child is None:
                        raise TeamAssignmentError(
                            info.child_error or "Child Run transcript is unavailable",
                            team_id=team.team_id,
                            assignment_id=assignment.assignment_id,
                        )
                    if info.child.status in {
                        ChildRunStatus.RUNNING,
                        ChildRunStatus.CANCELLING,
                    }:
                        from coquo.child_recovery import ChildRunRecoveryService

                        ChildRunRecoveryService(self.workspace).recover(
                            parent_session_id=team.owner_session_id,
                            child_run_id=assignment.child_run_id,
                            limit=1,
                        )
                    latest = self.inspect(team.team_id, assignment.assignment_id)
                    if latest.child is not None and latest.child.status in {
                        ChildRunStatus.COMPLETED,
                        ChildRunStatus.FAILED,
                        ChildRunStatus.CANCELLED,
                        ChildRunStatus.INTERRUPTED,
                    }:
                        self.observe_terminal(team.team_id, assignment.assignment_id)
                        recovered.append(self.inspect(team.team_id, assignment.assignment_id))
                    else:
                        diagnostics.append(
                            TeamRecoveryDiagnostic(
                                assignment.assignment_id,
                                "child_bound",
                                f"Child Run remains {latest.child.status.value if latest.child else 'unavailable'}",
                            )
                        )
                except (TeamAssignmentError, TeamStoreError, ChildRunStoreError, OSError) as error:
                    diagnostics.append(
                        TeamRecoveryDiagnostic(assignment.assignment_id, "blocked", str(error))
                    )
            else:
                try:
                    info = self.inspect(team.team_id, assignment.assignment_id)
                    if (
                        info.assignment.delivery_id is not None
                        and info.assignment.child_outcome == "completed"
                        and info.assignment.mailbox_observed_at is None
                    ):
                        self.observe_terminal(team.team_id, assignment.assignment_id)
                        info = self.inspect(team.team_id, assignment.assignment_id)
                    recovered.append(info)
                except (TeamAssignmentError, TeamMessageError, TeamStoreError, OSError) as error:
                    diagnostics.append(
                        TeamRecoveryDiagnostic(assignment.assignment_id, "blocked", str(error))
                    )
        return TeamRecoveryResult(tuple(recovered), tuple(diagnostics))

    def close(self, team_id: str) -> TeamInfo:
        """Observe every terminal assignment before closing the Team ledger."""
        team = self._inspect_team(team_id)
        for assignment in team.assignments:
            if assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED:
                if (
                    assignment.delivery_id is not None
                    and assignment.child_outcome == "completed"
                    and assignment.mailbox_observed_at is None
                ):
                    self.observe_terminal(team.team_id, assignment.assignment_id)
                continue
            info = self.inspect(team.team_id, assignment.assignment_id)
            if info.child is None or info.child.status not in {
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.CANCELLED,
                ChildRunStatus.INTERRUPTED,
            }:
                raise TeamAssignmentError(
                    "Team cannot close while an assignment Child is nonterminal",
                    team_id=team.team_id,
                    assignment_id=assignment.assignment_id,
                )
            self.observe_terminal(team.team_id, assignment.assignment_id)
        team = self._inspect_team(team_id)
        work_blockers = [
            f"work item {item.work_item_id} is {item.status.value}"
            for item in team.work_items
            if item.status not in {TeamWorkStatus.COMPLETED, TeamWorkStatus.CANCELLED}
        ]
        mailbox_blockers = list(self.messaging.close_blockers(team.team_id))
        if work_blockers or mailbox_blockers:
            details = "; ".join((*work_blockers, *mailbox_blockers))
            raise TeamAssignmentError(
                f"Team cannot close while coordination items are pending: {details}",
                team_id=team.team_id,
            )
        try:
            return self.teams.close(team.team_id)
        except TeamStoreError as error:
            raise TeamAssignmentError(str(error), team_id=team.team_id) from None

    def leave_member(self, team_id: str, member_id: str, reason: str) -> TeamMemberState:
        """Require exact terminal observation before removing a member identity."""
        team = self._inspect_team(team_id)
        member = self._member(team, self._canonical_id(member_id, "member ID"))
        for assignment in team.assignments:
            if assignment.member_id != member.member_id:
                continue
            if assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED:
                if (
                    assignment.delivery_id is not None
                    and assignment.child_outcome == "completed"
                    and assignment.mailbox_observed_at is None
                ):
                    self.observe_terminal(team.team_id, assignment.assignment_id)
                continue
            info = self.inspect(team.team_id, assignment.assignment_id)
            if info.child is None or info.child.status not in {
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.CANCELLED,
                ChildRunStatus.INTERRUPTED,
            }:
                raise TeamAssignmentError(
                    "Team member cannot leave while an assignment Child is nonterminal",
                    team_id=team.team_id,
                    assignment_id=assignment.assignment_id,
                )
            self.observe_terminal(team.team_id, assignment.assignment_id)
        blockers = self.messaging.leave_blockers(team.team_id, member.member_id)
        if blockers:
            raise TeamAssignmentError(
                "Team member cannot leave while mailbox messages are pending: "
                + "; ".join(blockers),
                team_id=team.team_id,
            )
        try:
            return self.teams.leave_member(team.team_id, member.member_id, reason)
        except TeamStoreError as error:
            raise TeamAssignmentError(str(error), team_id=team.team_id) from None

    def observe_terminal(self, team_id: str, assignment_id: str):
        """Publish exact Child handoff evidence, then append a content-free Team observation."""
        info = self.inspect(team_id, assignment_id)
        if info.child is None:
            raise TeamAssignmentError(
                info.child_error or "Child Run transcript is unavailable",
                team_id=info.team.team_id,
                assignment_id=info.assignment.assignment_id,
                child_run_id=info.assignment.child_run_id,
            )
        from coquo.child_handoff import publish_child_handoff

        try:
            handoff = publish_child_handoff(self.workspace, info.assignment.child_run_id)
        except Exception as error:
            raise TeamAssignmentError(
                f"Child handoff evidence is unavailable: {error}",
                team_id=info.team.team_id,
                assignment_id=info.assignment.assignment_id,
                child_run_id=info.assignment.child_run_id,
            ) from None
        if handoff.parent_session_id != info.team.owner_session_id:
            raise TeamAssignmentError(
                "Child Run owner does not match Team owner",
                team_id=info.team.team_id,
                assignment_id=info.assignment.assignment_id,
                child_run_id=info.assignment.child_run_id,
            )
        if info.assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED:
            observed = info.assignment
            if (
                observed.child_outcome != handoff.outcome
                or observed.child_terminal_sequence != handoff.terminal_record_sequence
                or observed.handoff_sha256 != handoff.handoff_sha256
            ):
                raise TeamAssignmentError(
                    "Team terminal observation disagrees with Child handoff",
                    team_id=info.team.team_id,
                    assignment_id=info.assignment.assignment_id,
                    child_run_id=info.assignment.child_run_id,
                )
            if (
                info.assignment.delivery_id is not None
                and handoff.outcome == "completed"
                and info.assignment.mailbox_observed_at is None
            ):
                try:
                    self.messaging.publish_reply_and_delivery(
                        info.team.team_id, info.assignment.assignment_id, handoff
                    )
                except TeamMessageError as error:
                    raise TeamAssignmentError(
                        f"Team mailbox observation requires recovery: {error}",
                        team_id=info.team.team_id,
                        assignment_id=info.assignment.assignment_id,
                        child_run_id=info.assignment.child_run_id,
                    ) from None
            return handoff
        if info.assignment.delivery_id is not None and handoff.outcome == "completed":
            try:
                self.messaging.publish_reply_and_delivery(
                    info.team.team_id, info.assignment.assignment_id, handoff
                )
            except TeamMessageError as error:
                raise TeamAssignmentError(
                    f"Team mailbox observation requires recovery: {error}",
                    team_id=info.team.team_id,
                    assignment_id=info.assignment.assignment_id,
                    child_run_id=info.assignment.child_run_id,
                ) from None
        try:
            self.teams.observe_assignment(
                info.team.team_id,
                info.assignment.assignment_id,
                child_run_id=info.assignment.child_run_id,
                child_session_id=handoff.child_session_id,
                child_outcome=handoff.outcome,
                child_terminal_sequence=handoff.terminal_record_sequence,
                handoff_sha256=handoff.handoff_sha256,
            )
        except TeamStoreError as error:
            raise TeamAssignmentError(
                f"Team terminal observation requires recovery: {error}",
                team_id=info.team.team_id,
                assignment_id=info.assignment.assignment_id,
                child_run_id=info.assignment.child_run_id,
            ) from None
        return handoff

    def _inspect_team(self, team_id: str) -> TeamInfo:
        try:
            return self.teams.inspect(team_id)
        except TeamStoreError as error:
            raise TeamAssignmentError(str(error), team_id=str(team_id)) from None

    @staticmethod
    def _member(team: TeamInfo, member_id: str) -> TeamMemberState:
        for member in team.members:
            if member.member_id == member_id:
                return member
        raise TeamAssignmentError("Team member was not found", team_id=team.team_id)

    def _child_projection(
        self, team: TeamInfo, assignment: TeamAssignmentState
    ) -> tuple[ChildRunInfo | None, str | None]:
        path = self.children.root / f"{assignment.child_run_id}.jsonl"
        if not path.exists() and not path.is_symlink():
            return None, "Child Run transcript is missing"
        try:
            info = self.children.inspect(assignment.child_run_id)
            origin = info.team_assignment
            if origin is None:
                return None, "Child Run has no Team assignment provenance"
            if (
                origin.child_run_id != assignment.child_run_id
                or origin.parent_session_id != team.owner_session_id
                or origin.team_id != team.team_id
                or origin.member_id != assignment.member_id
                or origin.assignment_id != assignment.assignment_id
                or origin.objective_sha256 != assignment.objective_sha256
                or info.objective != assignment.objective
                or (
                    assignment.child_origin_sequence is not None
                    and origin.sequence != assignment.child_origin_sequence
                )
            ):
                return None, "Child Run Team provenance does not match assignment"
            return info, None
        except ChildRunStoreError as error:
            return None, str(error)

    @staticmethod
    def _canonical_id(value: str, label: str) -> str:
        try:
            return canonical_team_id(value)
        except TeamRecordError as error:
            raise TeamAssignmentError(f"{label} is invalid: {error}") from None

    def _new_id(self, label: str) -> str:
        try:
            value = self._uuid_factory()
            if isinstance(value, UUID):
                value = str(value)
            return canonical_team_id(value)
        except (TeamRecordError, ValueError, TypeError) as error:
            raise TeamAssignmentError(f"{label} is invalid: {error}") from None
