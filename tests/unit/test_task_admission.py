from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest

from leonervis_code.core.contracts import (
    AssistantText,
    AssistantToolBatch,
    CommittedTurn,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.task_admission import (
    TASK_PROPOSE_START_TOOL_NAME,
    TaskAdmissionProposal,
    recover_task_admission_proposal,
    task_admission_receipt,
)

CONTEXT_ID = "ctx-v5-" + "a" * 64


def admission_request() -> ToolUse:
    return ToolUse(
        "admission-1",
        TASK_PROPOSE_START_TOOL_NAME,
        ToolArguments.from_mapping(
            {
                "objective": "Implement and verify the feature",
                "reason": "The request needs multiple bounded stages.",
                "acceptance_criteria": ["Tests pass", "Documentation is synchronized"],
            }
        ),
    )


def test_task_admission_proposal_has_stable_identity_and_is_immutable() -> None:
    first = TaskAdmissionProposal.from_request(admission_request(), CONTEXT_ID)
    second = TaskAdmissionProposal.from_request(admission_request(), CONTEXT_ID)

    assert first == second
    assert first.admission_id.startswith("tap-v1-")
    assert len(first.proposal_sha256) == 64
    with pytest.raises(FrozenInstanceError):
        first.reason = "changed"  # type: ignore[misc]


def test_task_admission_recovery_requires_exact_receipt_and_success_ledger() -> None:
    request = admission_request()
    proposal = TaskAdmissionProposal.from_request(request, CONTEXT_ID)
    turn = CommittedTurn(
        (
            UserMessage("large task"),
            request,
            ToolResult("admission-1", task_admission_receipt(proposal)),
            AssistantText("Proposed."),
        ),
        UserMessage("large task"),
        AssistantText("Proposed."),
        ToolTurnLedger(
            (
                ToolOutcomeEntry(
                    "admission-1",
                    TASK_PROPOSE_START_TOOL_NAME,
                    1,
                    ToolRequestOutcome.SUCCEEDED,
                ),
            )
        ),
    )

    assert recover_task_admission_proposal(turn) == proposal
    receipt = json.loads(task_admission_receipt(proposal))
    receipt["context_id"] = "ctx-v5-" + "b" * 64
    invalid = CommittedTurn(
        (turn.items[0], request, ToolResult("admission-1", json.dumps(receipt)), turn.items[-1]),
        turn.user,
        turn.assistant,
        turn.tool_ledger,
    )
    with pytest.raises(ValueError, match="does not match"):
        recover_task_admission_proposal(invalid)


def test_task_admission_recovery_allows_prior_completed_tools_but_remains_terminal() -> None:
    request = admission_request()
    proposal = TaskAdmissionProposal.from_request(request, CONTEXT_ID)
    prior = ToolUse("list-1", "list_directory", ToolArguments.from_mapping({"path": "."}))
    ledger = ToolTurnLedger(
        (
            ToolOutcomeEntry("list-1", "list_directory", 1, ToolRequestOutcome.SUCCEEDED),
            ToolOutcomeEntry(
                "admission-1",
                TASK_PROPOSE_START_TOOL_NAME,
                2,
                ToolRequestOutcome.SUCCEEDED,
            ),
        )
    )
    items = (
        UserMessage("inspect then propose"),
        prior,
        ToolResult("list-1", "{}"),
        request,
        ToolResult("admission-1", task_admission_receipt(proposal)),
        AssistantText("Proposed."),
    )
    turn = CommittedTurn(
        items,
        UserMessage("inspect then propose"),
        AssistantText("Proposed."),
        ledger,
    )

    assert recover_task_admission_proposal(turn) == proposal

    sibling = CommittedTurn(
        (
            items[0],
            AssistantToolBatch((request, prior)),
            ToolResult("admission-1", task_admission_receipt(proposal)),
            ToolResult("list-1", "{}"),
            items[-1],
        ),
        turn.user,
        turn.assistant,
        ledger,
    )
    with pytest.raises(ValueError, match="sibling"):
        recover_task_admission_proposal(sibling)

    nonterminal = CommittedTurn(
        (*items[:-1], prior, ToolResult("list-1", "{}"), items[-1]),
        turn.user,
        turn.assistant,
        ledger,
    )
    with pytest.raises(ValueError, match="not terminal"):
        recover_task_admission_proposal(nonterminal)
