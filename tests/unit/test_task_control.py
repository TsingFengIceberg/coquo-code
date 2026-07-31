from __future__ import annotations

import pytest

from leonervis_code.agent.task_control import (
    TaskControlDispatchResult,
    TaskControlProposal,
    TaskProposalKind,
    recover_task_control_proposal,
)
from leonervis_code.agent.tool_events import ToolDispatchResult, ToolEventStatus
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    CommittedTurn,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)

TASK_ID = "11111111-1111-4111-8111-111111111111"
STAGE_ID = "22222222-2222-4222-8222-222222222222"
CONTEXT_ID = "ctx-v5-" + "a" * 64


def proposal(*, tool_use_id: str = "proposal-1") -> TaskControlProposal:
    return TaskControlProposal(
        TaskProposalKind.PLAN,
        TASK_ID,
        STAGE_ID,
        1,
        CONTEXT_ID,
        tool_use_id,
        ToolArguments.from_mapping({"steps": ["inspect"]}),
    )


def test_task_control_proposal_is_bound_and_immutable() -> None:
    value = proposal()

    assert value.kind is TaskProposalKind.PLAN
    assert value.task_id == TASK_ID
    assert value.stage_id == STAGE_ID
    assert value.context_id == CONTEXT_ID


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_id", "not-a-task", "task ID"),
        ("stage_id", "not-a-stage", "stage ID"),
        ("stage_number", 0, "Stage number"),
        ("context_id", "ctx-v5-bad", "context identity"),
        ("tool_use_id", "", "tool-use ID"),
    ],
)
def test_task_control_proposal_rejects_invalid_binding(field, value, message) -> None:
    values = proposal().__dict__ | {field: value}

    with pytest.raises(ValueError, match=message):
        TaskControlProposal(**values)


def test_task_control_dispatch_requires_success_and_matching_proposal() -> None:
    success = ToolDispatchResult(
        ToolResult("proposal-1", "accepted"),
        ToolEventStatus.SUCCEEDED,
    )
    assert TaskControlDispatchResult(success, proposal()).proposal == proposal()

    with pytest.raises(ValueError, match="successful"):
        TaskControlDispatchResult(success)
    with pytest.raises(ValueError, match="match"):
        TaskControlDispatchResult(success, proposal(tool_use_id="other"))


def committed_control_turn(*, outcome: ToolRequestOutcome = ToolRequestOutcome.SUCCEEDED):
    request = ToolUse(
        "proposal-1",
        "git_show",
        ToolArguments.from_mapping({"proposal": "value"}),
    )
    result = ToolResult(
        "proposal-1", "accepted", is_error=outcome is not ToolRequestOutcome.SUCCEEDED
    )
    return CommittedTurn(
        items=(UserMessage("task"), request, result, AssistantText("done")),
        user=UserMessage("task"),
        assistant=AssistantText("done"),
        tool_ledger=ToolTurnLedger((ToolOutcomeEntry("proposal-1", "git_show", 1, outcome),)),
    )


def test_recovery_rebuilds_proposal_from_committed_call_and_host_ledger() -> None:
    recovered = recover_task_control_proposal(
        committed_control_turn(),
        tool_name="git_show",
        kind=TaskProposalKind.COMPLETION,
        task_id=TASK_ID,
        stage_id=STAGE_ID,
        stage_number=3,
        context_id=CONTEXT_ID,
    )

    assert recovered.kind is TaskProposalKind.COMPLETION
    assert recovered.stage_number == 3
    assert recovered.payload.as_mapping() == {"proposal": "value"}


def test_recovery_rejects_unsuccessful_host_ledger_fact() -> None:
    with pytest.raises(ValueError, match="not durably recorded as successful"):
        recover_task_control_proposal(
            committed_control_turn(outcome=ToolRequestOutcome.REJECTED),
            tool_name="git_show",
            kind=TaskProposalKind.COMPLETION,
            task_id=TASK_ID,
            stage_id=STAGE_ID,
            stage_number=3,
            context_id=CONTEXT_ID,
        )


def test_recovery_rejects_control_call_mixed_with_a_sibling() -> None:
    control = ToolUse(
        "proposal-1",
        "git_show",
        ToolArguments.from_mapping({"proposal": "value"}),
    )
    sibling = ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
    )
    turn = CommittedTurn(
        items=(
            UserMessage("task"),
            AssistantToolBatch((control, sibling)),
            ToolResult("proposal-1", "accepted"),
            ToolResult("read-1", "content"),
            AssistantText("done"),
        ),
        user=UserMessage("task"),
        assistant=AssistantText("done"),
        tool_ledger=ToolTurnLedger(
            (
                ToolOutcomeEntry("proposal-1", "git_show", 1, ToolRequestOutcome.SUCCEEDED),
                ToolOutcomeEntry("read-1", "read_file", 2, ToolRequestOutcome.SUCCEEDED),
            )
        ),
    )

    with pytest.raises(ValueError, match="mixed"):
        recover_task_control_proposal(
            turn,
            tool_name="git_show",
            kind=TaskProposalKind.COMPLETION,
            task_id=TASK_ID,
            stage_id=STAGE_ID,
            stage_number=3,
            context_id=CONTEXT_ID,
        )
