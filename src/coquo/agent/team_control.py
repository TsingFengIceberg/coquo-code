"""Volatile per-Turn accounting for parent-owned Team controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from coquo.agent.tool_events import ToolDispatchResult
from coquo.core.contracts import ToolUse
from coquo.core.team_approval import TeamControlApprovalIdentity
from coquo.tools.team_control import (
    MAX_TEAM_CREATES_PER_TURN,
    MAX_TEAM_MEMBER_ADDS_PER_TURN,
    MAX_TEAM_MESSAGE_SHOWS_PER_TURN,
    MAX_TEAM_MUTATIONS_PER_TURN,
    MAX_TEAM_SCHEDULE_STARTS_PER_TURN,
    MAX_TEAM_WAIT_SECONDS_PER_TURN,
)


@dataclass
class TeamControlState:
    """Counters and pending approval identity that never enter Session history."""

    mutation_count: int = 0
    create_count: int = 0
    member_add_count: int = 0
    schedule_start_count: int = 0
    message_show_count: int = 0
    requested_wait_seconds: int = 0
    pending_approval_identity: TeamControlApprovalIdentity | None = None

    def reserve_mutation(self) -> int:
        if self.mutation_count >= MAX_TEAM_MUTATIONS_PER_TURN:
            raise ValueError("Team mutation limit of 8 per Turn is exhausted")
        self.mutation_count += 1
        return self.mutation_count

    def reserve_create(self) -> int:
        if (
            self.create_count >= MAX_TEAM_CREATES_PER_TURN
            or self.mutation_count >= MAX_TEAM_MUTATIONS_PER_TURN
        ):
            raise ValueError("Team create limit of 1 per Turn is exhausted")
        self.create_count += 1
        self.mutation_count += 1
        return self.create_count

    def reserve_member_add(self) -> int:
        if (
            self.member_add_count >= MAX_TEAM_MEMBER_ADDS_PER_TURN
            or self.mutation_count >= MAX_TEAM_MUTATIONS_PER_TURN
        ):
            raise ValueError("Team member-add limit of 4 per Turn is exhausted")
        self.member_add_count += 1
        self.mutation_count += 1
        return self.member_add_count

    def reserve_schedule_start(self) -> int:
        if (
            self.schedule_start_count >= MAX_TEAM_SCHEDULE_STARTS_PER_TURN
            or self.mutation_count >= MAX_TEAM_MUTATIONS_PER_TURN
        ):
            raise ValueError("Team schedule-start limit of 1 per Turn is exhausted")
        self.schedule_start_count += 1
        self.mutation_count += 1
        return self.schedule_start_count

    def reserve_message_show(self) -> int:
        if self.message_show_count >= MAX_TEAM_MESSAGE_SHOWS_PER_TURN:
            raise ValueError("Team message-show limit of 4 per Turn is exhausted")
        self.message_show_count += 1
        return self.message_show_count

    def reserve_wait(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 0:
            raise ValueError("Team wait seconds must be a non-negative integer")
        if self.requested_wait_seconds + seconds > MAX_TEAM_WAIT_SECONDS_PER_TURN:
            raise ValueError("Team wait limit of 60 requested seconds per Turn is exhausted")
        self.requested_wait_seconds += seconds

    def clear(self) -> None:
        self.mutation_count = 0
        self.create_count = 0
        self.member_add_count = 0
        self.schedule_start_count = 0
        self.message_show_count = 0
        self.requested_wait_seconds = 0
        self.pending_approval_identity = None


@dataclass(frozen=True)
class TeamControlDispatchResult:
    dispatch: ToolDispatchResult

    def __post_init__(self) -> None:
        if type(self.dispatch) is not ToolDispatchResult:
            raise ValueError("Team control dispatch result is invalid")


TeamControlDispatcher = Callable[[ToolUse, str], TeamControlDispatchResult]
