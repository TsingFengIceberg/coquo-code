"""Host-owned durable Team work-board projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from coquo.team_records import (
    TeamRecordError,
    TeamStatus,
    TeamWorkItemState,
    TeamWorkStatus,
    canonical_team_id,
)
from coquo.team_store import TeamInfo, TeamStore, TeamStoreError


class TeamWorkError(RuntimeError):
    """Raised when a Team work-board operation cannot advance safely."""


@dataclass(frozen=True)
class TeamWorkList:
    team: TeamInfo
    items: tuple[TeamWorkItemState, ...]


class TeamWorkService:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.teams = TeamStore(self.workspace)

    def create(
        self,
        team_id: str,
        title: str,
        objective: str,
        dependency_ids: tuple[str, ...] = (),
    ) -> TeamWorkItemState:
        team = self._team(team_id)
        if team.status is not TeamStatus.OPEN:
            raise TeamWorkError("Team is closed")
        try:
            dependencies = tuple(canonical_team_id(item) for item in dependency_ids)
            if len(dependencies) != len(set(dependencies)):
                raise TeamRecordError("Team work item dependencies must be unique")
            item_id = str(uuid4())
            return self.teams.create_work_item(
                team.team_id, item_id, title, objective, dependencies
            )
        except (TeamRecordError, TeamStoreError) as error:
            raise TeamWorkError(str(error)) from None

    def list(
        self,
        team_id: str,
        *,
        limit: int = 100,
        status: TeamWorkStatus | None = None,
    ) -> TeamWorkList:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TeamWorkError("work list limit must be between 1 and 100")
        team = self._team(team_id)
        items = tuple(item for item in team.work_items if status is None or item.status is status)
        return TeamWorkList(team, items[:limit])

    def show(self, team_id: str, work_item_id: str) -> TeamWorkItemState:
        try:
            return self.teams.work_item(team_id, work_item_id)
        except TeamStoreError as error:
            raise TeamWorkError(str(error)) from None

    def cancel(self, team_id: str, work_item_id: str, reason: str) -> TeamWorkItemState:
        try:
            return self.teams.cancel_work_item(team_id, work_item_id, reason)
        except TeamStoreError as error:
            raise TeamWorkError(str(error)) from None

    def assign(self, team_id: str, work_item_id: str, member_id: str):
        item = self.show(team_id, work_item_id)
        if item.status is not TeamWorkStatus.READY:
            raise TeamWorkError("Team work item is not ready")
        from coquo.team_service import TeamAssignmentError, TeamAssignmentService

        try:
            return TeamAssignmentService(self.workspace).create(
                team_id, member_id, item.objective, work_item_id=item.work_item_id
            )
        except TeamAssignmentError as error:
            raise TeamWorkError(str(error)) from None

    def release(self, team_id: str, work_item_id: str, reason: str) -> TeamWorkItemState:
        item = self.show(team_id, work_item_id)
        if item.status is not TeamWorkStatus.REVIEW or item.current_assignment_id is None:
            raise TeamWorkError("Team work item is not in review")
        try:
            with self.teams.open(team_id) as writer:
                writer.release_work_item(item.work_item_id, item.current_assignment_id, reason)
            return self.show(team_id, item.work_item_id)
        except TeamStoreError as error:
            raise TeamWorkError(str(error)) from None

    def complete(self, team_id: str, work_item_id: str, evidence: str) -> TeamWorkItemState:
        item = self.show(team_id, work_item_id)
        if item.status is not TeamWorkStatus.REVIEW or item.current_assignment_id is None:
            raise TeamWorkError("Team work item is not in review")
        from coquo.team_service import TeamAssignmentError, TeamAssignmentService

        try:
            assignment_service = TeamAssignmentService(self.workspace)
            handoff = assignment_service.observe_terminal(team_id, item.current_assignment_id)
            info = assignment_service.inspect(team_id, item.current_assignment_id)
            if handoff.outcome != "completed" or info.assignment.handoff_sha256 is None:
                raise TeamWorkError("Team work item requires a completed Child handoff")
            with self.teams.open(team_id) as writer:
                writer.complete_work_item(
                    item.work_item_id,
                    item.current_assignment_id,
                    handoff.handoff_sha256,
                    evidence,
                )
            return self.show(team_id, item.work_item_id)
        except (TeamStoreError, TeamAssignmentError) as error:
            raise TeamWorkError(str(error)) from None

    def _team(self, team_id: str) -> TeamInfo:
        try:
            return self.teams.inspect(team_id)
        except TeamStoreError as error:
            raise TeamWorkError(str(error)) from None
