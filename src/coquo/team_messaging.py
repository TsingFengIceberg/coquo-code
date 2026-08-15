"""Host-owned durable Team mailbox operations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from uuid import uuid4

from coquo.team_records import (
    TeamMessageState,
    TeamMessageStatus,
    TeamMemberStatus,
    TeamRecordError,
    TeamStatus,
    canonical_team_id,
    TeamAssignmentPhase,
)
from coquo.team_store import TeamInfo, TeamStore, TeamStoreError


class TeamMessageError(RuntimeError):
    """Raised when a Host mailbox operation cannot advance safely."""

    def __init__(
        self,
        message: str,
        *,
        team_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        self.team_id = team_id
        self.message_id = message_id
        super().__init__(message)


@dataclass(frozen=True)
class TeamMessageList:
    team: TeamInfo
    messages: tuple[TeamMessageState, ...]


class TeamMessagingService:
    """Provide strict owner mailbox mutations without invoking a Provider."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.teams = TeamStore(self.workspace)

    def send_owner(self, team_id: str, member_id: str, body: str) -> TeamMessageState:
        team = self._team(team_id)
        if team.status is not TeamStatus.OPEN:
            raise TeamMessageError("Team is closed", team_id=team.team_id)
        member = self._member(team, member_id)
        if member.status is TeamMemberStatus.LEFT:
            raise TeamMessageError("Team member has left", team_id=team.team_id)
        try:
            return self.teams.send_message(team.team_id, member.member_id, body)
        except TeamStoreError as error:
            raise TeamMessageError(
                str(error),
                team_id=team.team_id,
            ) from None

    def list(
        self,
        team_id: str,
        *,
        limit: int = 100,
        member_id: str | None = None,
        status: TeamMessageStatus | None = None,
    ) -> TeamMessageList:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TeamMessageError("message list limit must be between 1 and 100")
        team = self._team(team_id)
        canonical_member: str | None = None
        if member_id is not None:
            canonical_member = self._canonical_id(member_id, "member ID", team.team_id)
        messages = tuple(
            message
            for message in team.messages
            if (
                canonical_member is None
                or message.sender_member_id == canonical_member
                or message.recipient_member_id == canonical_member
            )
            and (status is None or message.status is status)
        )
        return TeamMessageList(team, messages[:limit])

    def show(self, team_id: str, message_id: str) -> TeamMessageState:
        team = self._team(team_id)
        canonical = self._canonical_id(message_id, "message ID", team.team_id)
        try:
            return self.teams.message(team.team_id, canonical)
        except TeamStoreError as error:
            raise TeamMessageError(str(error), team_id=team.team_id, message_id=canonical) from None

    def read(self, team_id: str, message_id: str) -> TeamMessageState:
        message = self.show(team_id, message_id)
        if message.sender_member_id is None:
            raise TeamMessageError(
                "Owner-to-member message cannot be marked read",
                team_id=team_id,
                message_id=message.message_id,
            )
        try:
            return self.teams.read_message(team_id, message.message_id)
        except TeamStoreError as error:
            raise TeamMessageError(
                str(error), team_id=team_id, message_id=message.message_id
            ) from None

    def cancel(self, team_id: str, message_id: str, reason: str) -> TeamMessageState:
        message = self.show(team_id, message_id)
        if message.sender_member_id is not None:
            raise TeamMessageError(
                "Member-to-owner message cannot be cancelled",
                team_id=team_id,
                message_id=message.message_id,
            )
        if message.status is not TeamMessageStatus.PENDING:
            raise TeamMessageError(
                "Owner-to-member message is no longer pending",
                team_id=team_id,
                message_id=message.message_id,
            )
        for assignment in self._team(team_id).assignments:
            if (
                message.message_id in assignment.inbox_message_ids
                and assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
            ):
                raise TeamMessageError(
                    f"Owner-to-member message is bound to active assignment {assignment.assignment_id}",
                    team_id=team_id,
                    message_id=message.message_id,
                )
        try:
            return self.teams.cancel_message(team_id, message.message_id, reason)
        except TeamStoreError as error:
            raise TeamMessageError(
                str(error), team_id=team_id, message_id=message.message_id
            ) from None

    def close_blockers(self, team_id: str) -> tuple[str, ...]:
        """Return exact mailbox identities that prevent Team closure."""
        team = self._team(team_id)
        blockers: list[str] = []
        for message in team.messages:
            if message.sender_member_id is None and message.status is TeamMessageStatus.PENDING:
                blockers.append(f"message {message.message_id} is still pending")
            elif (
                message.sender_member_id is not None and message.status is TeamMessageStatus.UNREAD
            ):
                blockers.append(f"reply {message.message_id} is unread")
        for assignment in team.assignments:
            if (
                assignment.delivery_id is not None
                and assignment.phase is TeamAssignmentPhase.TERMINAL_OBSERVED
                and assignment.child_outcome == "completed"
                and assignment.mailbox_observed_at is None
            ):
                blockers.append(
                    f"assignment {assignment.assignment_id} mailbox delivery is unobserved"
                )
        return tuple(blockers)

    def leave_blockers(self, team_id: str, member_id: str) -> tuple[str, ...]:
        """Return exact inbound mailbox identities that prevent member leave."""
        team = self._team(team_id)
        member = self._member(team, member_id)
        blockers = [
            f"message {message.message_id} is still pending"
            for message in team.messages
            if (
                message.sender_member_id is None
                and message.recipient_member_id == member.member_id
                and message.status is TeamMessageStatus.PENDING
            )
        ]
        return tuple(blockers)

    def bind_assignment(self, team_id: str, assignment_id: str):
        """Freeze the oldest pending inbox and preallocate delivery/reply identities."""
        team = self._team(team_id)
        canonical_assignment = self._canonical_id(assignment_id, "assignment ID", team.team_id)
        assignment = next(
            (item for item in team.assignments if item.assignment_id == canonical_assignment), None
        )
        if assignment is None:
            raise TeamMessageError("Team assignment was not found", team_id=team.team_id)
        if assignment.phase is not TeamAssignmentPhase.CHILD_BOUND:
            raise TeamMessageError(
                "Team assignment is not ready for mailbox binding", team_id=team.team_id
            )
        if assignment.delivery_id is not None:
            return assignment
        bound = {
            message_id
            for item in team.assignments
            if (
                item.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
                or item.child_outcome == "completed"
            )
            for message_id in item.inbox_message_ids
        }
        selected = []
        total_bytes = 0
        for message in team.messages:
            if (
                message.sender_member_id is not None
                or message.recipient_member_id != assignment.member_id
                or message.status is not TeamMessageStatus.PENDING
                or message.message_id in bound
            ):
                continue
            body_bytes = len(message.body.encode("utf-8"))
            if len(selected) >= 8 or total_bytes + body_bytes > 12 * 1024:
                if not selected:
                    raise TeamMessageError(
                        "oldest pending Team message does not fit the inbox bound",
                        team_id=team.team_id,
                    )
                break
            selected.append(message.message_id)
            total_bytes += body_bytes
        try:
            return self.teams.bind_assignment_mailbox(
                team.team_id,
                assignment.assignment_id,
                child_run_id=assignment.child_run_id,
                member_id=assignment.member_id,
                delivery_id=str(uuid4()),
                inbox_message_ids=tuple(selected),
                reply_message_id=str(uuid4()),
            )
        except TeamStoreError as error:
            raise TeamMessageError(str(error), team_id=team.team_id) from None

    def team_prompt(self, team_id: str, assignment_id: str) -> str:
        team = self._team(team_id)
        assignment = next(
            (
                item
                for item in team.assignments
                if item.assignment_id
                == self._canonical_id(assignment_id, "assignment ID", team.team_id)
            ),
            None,
        )
        if assignment is None or assignment.delivery_id is None:
            raise TeamMessageError("Team assignment has no mailbox binding", team_id=team.team_id)
        inbox = tuple(
            {
                "body": message.body,
                "message_id": message.message_id,
                "sent_at": message.sent_at,
            }
            for message in team.messages
            if message.message_id in assignment.inbox_message_ids
        )
        from coquo.child_runtime import (
            build_team_child_role_prompt,
            build_writable_child_role_prompt,
        )

        member = next(item for item in team.members if item.member_id == assignment.member_id)
        if member.role_contract != "read-only-investigator-v1":
            if (
                assignment.worktree_id is None
                or assignment.base_commit is None
                or assignment.target_ref is None
            ):
                raise TeamMessageError("writable Team assignment has incomplete worktree identity")
            from coquo.session_records import workspace_fingerprint
            from coquo.worktree_service import WorktreeService

            binding = WorktreeService(self.workspace).inspect_binding(assignment.worktree_id)
            return build_writable_child_role_prompt(
                objective=assignment.objective,
                child_run_id=assignment.child_run_id,
                role_contract=member.role_contract,
                worktree_id=assignment.worktree_id,
                execution_root_fingerprint=workspace_fingerprint(binding.worktree_root),
                base_commit=assignment.base_commit,
                target_ref=assignment.target_ref,
                inbox=inbox,
            )

        return build_team_child_role_prompt(
            objective=assignment.objective,
            child_run_id=assignment.child_run_id,
            team_id=team.team_id,
            member_id=assignment.member_id,
            assignment_id=assignment.assignment_id,
            delivery_id=assignment.delivery_id,
            inbox=inbox,
        )

    def publish_reply_and_delivery(self, team_id: str, assignment_id: str, handoff) -> None:
        team = self._team(team_id)
        assignment = next(
            (
                item
                for item in team.assignments
                if item.assignment_id
                == self._canonical_id(assignment_id, "assignment ID", team.team_id)
            ),
            None,
        )
        if (
            assignment is None
            or assignment.delivery_id is None
            or assignment.reply_message_id is None
        ):
            raise TeamMessageError("Team assignment has no mailbox binding", team_id=team.team_id)
        if (
            handoff.outcome != "completed"
            or handoff.child_session_id is None
            or handoff.child_turn_record_sequence is None
        ):
            return
        prompt = self.team_prompt(team.team_id, assignment.assignment_id)
        from coquo.session_store import SessionStore

        evidence = SessionStore(self.workspace).turn_evidence(
            handoff.child_session_id, handoff.child_turn_record_sequence
        )
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if evidence.user_message_sha256 != prompt_digest:
            raise TeamMessageError(
                "Team Child user prompt does not match mailbox binding", team_id=team.team_id
            )
        existing = next(
            (
                message
                for message in team.messages
                if message.message_id == assignment.reply_message_id
            ),
            None,
        )
        if existing is None:
            try:
                self.teams.send_member_message(
                    team.team_id,
                    message_id=assignment.reply_message_id,
                    member_id=assignment.member_id,
                    body=handoff.body,
                    source_assignment_id=assignment.assignment_id,
                    source_child_session_id=handoff.child_session_id,
                    source_turn_record_sequence=handoff.child_turn_record_sequence,
                    source_handoff_sha256=handoff.handoff_sha256,
                )
            except TeamStoreError as error:
                raise TeamMessageError(str(error), team_id=team.team_id) from None
        elif (
            existing.body != handoff.body
            or existing.source_handoff_sha256 != handoff.handoff_sha256
            or existing.source_child_session_id != handoff.child_session_id
        ):
            raise TeamMessageError(
                "Team member reply disagrees with published handoff", team_id=team.team_id
            )
        latest = self._team(team.team_id)
        updated = next(
            item for item in latest.assignments if item.assignment_id == assignment.assignment_id
        )
        if updated.mailbox_observed_at is None:
            try:
                self.teams.observe_assignment_mailbox(
                    team.team_id,
                    assignment.assignment_id,
                    delivery_id=assignment.delivery_id,
                    child_session_id=handoff.child_session_id,
                    child_turn_record_sequence=handoff.child_turn_record_sequence,
                    child_user_message_sha256=prompt_digest,
                )
            except TeamStoreError as error:
                raise TeamMessageError(str(error), team_id=team.team_id) from None

    def _team(self, team_id: str) -> TeamInfo:
        try:
            return self.teams.inspect(team_id)
        except TeamStoreError as error:
            raise TeamMessageError(str(error), team_id=str(team_id)) from None

    @staticmethod
    def _member(team: TeamInfo, member_id: str):
        canonical = TeamMessagingService._canonical_id(member_id, "member ID", team.team_id)
        for member in team.members:
            if member.member_id == canonical:
                return member
        raise TeamMessageError("Team member was not found", team_id=team.team_id)

    @staticmethod
    def _canonical_id(value: str, label: str, team_id: str) -> str:
        try:
            return canonical_team_id(value)
        except TeamRecordError as error:
            raise TeamMessageError(f"{label} is invalid: {error}", team_id=team_id) from None
