"""Per-Turn state and Host dispatch contracts for model Child controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from coquo.agent.tool_events import ToolDispatchResult
from coquo.core.contracts import ToolUse
from coquo.core.delegation_approval import DelegationApprovalIdentity
from coquo.tools.child_control import (
    MAX_CHILD_SPAWNS_PER_TURN,
    MAX_CHILD_WAIT_SECONDS_PER_TURN,
)


@dataclass
class ChildControlState:
    """Volatile counters and pending identity for one ordinary parent Turn."""

    spawned_ids: list[str] = field(default_factory=list)
    requested_wait_seconds: int = 0
    depth: int = 0
    parent_child_run_id: str | None = None
    root_child_run_id: str | None = None
    delegation_allowed: bool = False
    pending_approval_identity: DelegationApprovalIdentity | None = None

    def reserve_spawn(self) -> int:
        if self.depth >= 2 or not self.delegation_allowed and self.depth != 0:
            raise ValueError("Child delegation depth limit is exhausted")
        number = len(self.spawned_ids) + 1
        if number > MAX_CHILD_SPAWNS_PER_TURN:
            raise ValueError("Child spawn limit of 4 per Turn is exhausted")
        return number

    def record_spawn(self, child_run_id: str) -> None:
        if len(self.spawned_ids) >= MAX_CHILD_SPAWNS_PER_TURN:
            raise ValueError("Child spawn limit of 4 per Turn is exhausted")
        self.spawned_ids.append(child_run_id)

    def reserve_wait(self, seconds: int) -> None:
        if self.requested_wait_seconds + seconds > MAX_CHILD_WAIT_SECONDS_PER_TURN:
            raise ValueError("Child wait limit of 60 requested seconds per Turn is exhausted")
        self.requested_wait_seconds += seconds


@dataclass(frozen=True)
class ChildControlDispatchResult:
    dispatch: ToolDispatchResult

    def __post_init__(self) -> None:
        if type(self.dispatch) is not ToolDispatchResult:
            raise ValueError("Child control dispatch result is invalid")


ChildControlDispatcher = Callable[[ToolUse, str], ChildControlDispatchResult]
