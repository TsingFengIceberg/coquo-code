"""Bounded Host-owned contracts for future model Task coordination requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import TypeAlias

from coquo.agent.tool_events import ToolDispatchResult, ToolEventStatus
from coquo.core.contracts import (
    AssistantToolBatch,
    CommittedTurn,
    ToolArguments,
    ToolRequestOutcome,
    ToolResult,
    ToolUse,
)
from coquo.core.skill_authoring import SkillAuthoringControl
from coquo.core.task_admission import (
    TaskAdmissionProposal,
    canonical_task_admission_id,
)
from coquo.task_records import canonical_plan_id, canonical_stage_id, canonical_task_id
from coquo.tools.task_coordination import (
    TASK_ACCEPT_ADMISSION_TOOL_NAME,
    TASK_ACCEPT_PLAN_TOOL_NAME,
    TASK_CONFIRM_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_PLAN_TOOL_NAME,
    TASK_REPORT_BLOCKER_TOOL_NAME,
    TASK_REPORT_REFLECTION_TOOL_NAME,
)

_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")


class TaskProposalKind(StrEnum):
    """Closed proposal kinds that may later be exposed through dedicated model tools."""

    PLAN = "plan"
    REFLECTION = "reflection"
    BLOCKER = "blocker"
    COMPLETION = "completion"


class TaskLifecycleKind(StrEnum):
    """Closed natural-language lifecycle transitions requested from an ordinary Prompt."""

    ACCEPT_ADMISSION = "accept-admission"
    ACCEPT_PLAN = "accept-plan"
    CONFIRM_COMPLETION = "confirm-completion"


@dataclass(frozen=True)
class TaskControlProposal:
    """One validated model proposal bound to an exact Task Stage and context snapshot."""

    kind: TaskProposalKind
    task_id: str
    stage_id: str
    stage_number: int
    context_id: str
    tool_use_id: str
    payload: ToolArguments

    def __post_init__(self) -> None:
        if type(self.kind) is not TaskProposalKind:
            raise ValueError("Task proposal kind is invalid")
        canonical_task_id(self.task_id)
        canonical_stage_id(self.stage_id)
        if type(self.stage_number) is not int or self.stage_number <= 0:
            raise ValueError("Task proposal Stage number must be positive")
        if not isinstance(self.context_id, str) or _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("Task proposal context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("Task proposal tool-use ID is invalid")
        if type(self.payload) is not ToolArguments:
            raise ValueError("Task proposal payload is invalid")


@dataclass(frozen=True)
class TaskLifecycleRequest:
    """One commit-coupled lifecycle request translated from direct user language."""

    kind: TaskLifecycleKind
    context_id: str
    tool_use_id: str
    subject_id: str
    expected_identity: str

    def __post_init__(self) -> None:
        if type(self.kind) is not TaskLifecycleKind:
            raise ValueError("Task lifecycle kind is invalid")
        if not isinstance(self.context_id, str) or _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("Task lifecycle context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("Task lifecycle tool-use ID is invalid")
        if self.kind is TaskLifecycleKind.ACCEPT_ADMISSION:
            canonical_task_admission_id(self.subject_id)
            if re.fullmatch(r"[0-9a-f]{64}", self.expected_identity) is None:
                raise ValueError("Task lifecycle admission identity is invalid")
        else:
            canonical_task_id(self.subject_id)
            if self.kind is TaskLifecycleKind.ACCEPT_PLAN:
                canonical_plan_id(self.expected_identity)
            else:
                canonical_stage_id(self.expected_identity)

    @property
    def request_sha256(self) -> str:
        """Bind the Host-only request to its exact immutable lifecycle fields."""
        payload = "\0".join(
            (
                self.kind.value,
                self.context_id,
                self.tool_use_id,
                self.subject_id,
                self.expected_identity,
            )
        ).encode("utf-8")
        return hashlib.sha256(b"coquo-task-lifecycle-request-v1\0" + payload).hexdigest()


@dataclass(frozen=True)
class TaskControlDispatchResult:
    """Model-visible receipt plus an optional Host-only pending proposal."""

    dispatch: ToolDispatchResult
    proposal: TaskProposal | None = None

    def __post_init__(self) -> None:
        if type(self.dispatch) is not ToolDispatchResult:
            raise ValueError("Task control dispatch result is invalid")
        succeeded = self.dispatch.status is ToolEventStatus.SUCCEEDED
        if succeeded != (self.proposal is not None):
            raise ValueError("only a successful Task control dispatch may carry a proposal")
        if self.proposal is not None and self.proposal.tool_use_id != (
            self.dispatch.tool_result.tool_use_id
        ):
            raise ValueError("Task proposal does not match its tool result")


TaskProposal: TypeAlias = (
    TaskControlProposal | TaskAdmissionProposal | TaskLifecycleRequest | SkillAuthoringControl
)
TaskControlDispatcher = Callable[[ToolUse, str], TaskControlDispatchResult]
TaskProposalSink = Callable[[TaskProposal], None]

TASK_PROPOSAL_KIND_BY_TOOL = {
    TASK_PROPOSE_PLAN_TOOL_NAME: TaskProposalKind.PLAN,
    TASK_REPORT_REFLECTION_TOOL_NAME: TaskProposalKind.REFLECTION,
    TASK_REPORT_BLOCKER_TOOL_NAME: TaskProposalKind.BLOCKER,
    TASK_PROPOSE_COMPLETION_TOOL_NAME: TaskProposalKind.COMPLETION,
}

TASK_LIFECYCLE_KIND_BY_TOOL = {
    TASK_ACCEPT_ADMISSION_TOOL_NAME: TaskLifecycleKind.ACCEPT_ADMISSION,
    TASK_ACCEPT_PLAN_TOOL_NAME: TaskLifecycleKind.ACCEPT_PLAN,
    TASK_CONFIRM_COMPLETION_TOOL_NAME: TaskLifecycleKind.CONFIRM_COMPLETION,
}


def recover_task_control_proposal(
    turn: CommittedTurn,
    *,
    tool_name: str,
    kind: TaskProposalKind,
    task_id: str,
    stage_id: str,
    stage_number: int,
    context_id: str,
) -> TaskControlProposal:
    """Recover one proposal only from an exact committed call and Host-success ledger fact."""
    request = recover_task_control_request(turn, tool_name=tool_name)
    return TaskControlProposal(
        kind=kind,
        task_id=task_id,
        stage_id=stage_id,
        stage_number=stage_number,
        context_id=context_id,
        tool_use_id=request.tool_use_id,
        payload=request.arguments,
    )


def recover_task_control_request(turn: CommittedTurn, *, tool_name: str) -> ToolUse:
    """Recover one terminal successful Host-ledger-backed control request."""
    if type(turn) is not CommittedTurn:
        raise ValueError("Task proposal recovery requires a committed Turn")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("Task proposal recovery tool name is invalid")
    matches: list[tuple[ToolUse, ToolResult]] = []
    for index, item in enumerate(turn.items):
        requests = (
            item.tool_uses
            if isinstance(item, AssistantToolBatch)
            else (item,)
            if isinstance(item, ToolUse)
            else ()
        )
        for offset, request in enumerate(requests, start=1):
            if request.name != tool_name:
                continue
            if isinstance(item, AssistantToolBatch) and len(item.tool_uses) != 1:
                raise ValueError("Task control call was mixed with sibling calls")
            result_index = index + offset
            if result_index >= len(turn.items) or not isinstance(
                turn.items[result_index], ToolResult
            ):
                raise ValueError("Task control call has no matching committed result")
            result = turn.items[result_index]
            if result.tool_use_id != request.tool_use_id:
                raise ValueError("Task control result does not match its request")
            if any(
                isinstance(later, (ToolUse, AssistantToolBatch))
                for later in turn.items[result_index + 1 :]
            ):
                raise ValueError("Task control call was not terminal in its committed Turn")
            matches.append((request, result))
    if len(matches) != 1:
        raise ValueError("committed Turn must contain exactly one matching Task control call")
    request, result = matches[0]
    ledger_matches = tuple(
        entry for entry in turn.tool_ledger.entries if entry.tool_use_id == request.tool_use_id
    )
    if (
        result.is_error
        or len(ledger_matches) != 1
        or ledger_matches[0].tool_name != tool_name
        or ledger_matches[0].outcome is not ToolRequestOutcome.SUCCEEDED
    ):
        raise ValueError("Task control call was not durably recorded as successful")
    return request
