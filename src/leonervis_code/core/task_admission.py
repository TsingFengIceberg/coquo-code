"""Immutable Host-owned contracts for model-proposed durable Task admission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re

from leonervis_code.core.contracts import (
    AssistantToolBatch,
    CommittedTurn,
    ToolArguments,
    ToolRequestOutcome,
    ToolResult,
    ToolUse,
)

TASK_ADMISSION_ID_VERSION = "tap-v1"
TASK_PROPOSE_START_TOOL_NAME = "task_propose_start"
MAX_TASK_ADMISSION_REASON_CHARACTERS = 1024
MAX_TASK_ADMISSION_REASON_BYTES = 4096
MAX_TASK_ADMISSION_CRITERIA = 16

_ADMISSION_ID = re.compile(r"tap-v1-[0-9a-f]{64}\Z")
_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")


class TaskAdmissionOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TaskAdmissionProposal:
    """One bounded model proposal that has not created or authorized a Task."""

    admission_id: str
    context_id: str
    tool_use_id: str
    objective: str
    reason: str
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        canonical_task_admission_id(self.admission_id)
        if not isinstance(self.context_id, str) or _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("Task admission context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("Task admission tool-use ID is invalid")
        _bounded_text(self.objective, "Task admission objective", 4096, 16 * 1024)
        _bounded_text(
            self.reason,
            "Task admission reason",
            MAX_TASK_ADMISSION_REASON_CHARACTERS,
            MAX_TASK_ADMISSION_REASON_BYTES,
        )
        if (
            not isinstance(self.acceptance_criteria, tuple)
            or not 1 <= len(self.acceptance_criteria) <= MAX_TASK_ADMISSION_CRITERIA
        ):
            raise ValueError("Task admission acceptance criteria are invalid")
        for criterion in self.acceptance_criteria:
            _bounded_text(criterion, "Task admission acceptance criterion", 1024, 4096)
        expected = task_admission_id(
            context_id=self.context_id,
            tool_use_id=self.tool_use_id,
            objective=self.objective,
            reason=self.reason,
            acceptance_criteria=self.acceptance_criteria,
        )
        if self.admission_id != expected:
            raise ValueError("Task admission ID does not match its proposal")

    @classmethod
    def from_request(cls, request: ToolUse, context_id: str) -> TaskAdmissionProposal:
        if type(request) is not ToolUse or request.name != TASK_PROPOSE_START_TOOL_NAME:
            raise ValueError("Task admission request is invalid")
        arguments = request.arguments.as_mapping()
        if set(arguments) != {"objective", "reason", "acceptance_criteria"}:
            raise ValueError("Task admission arguments are invalid")
        criteria = arguments["acceptance_criteria"]
        if not isinstance(criteria, list) or not all(isinstance(item, str) for item in criteria):
            raise ValueError("Task admission acceptance criteria are invalid")
        proposal_id = task_admission_id(
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            objective=arguments["objective"],
            reason=arguments["reason"],
            acceptance_criteria=tuple(criteria),
        )
        return cls(
            proposal_id,
            context_id,
            request.tool_use_id,
            arguments["objective"],
            arguments["reason"],
            tuple(criteria),
        )

    @property
    def arguments(self) -> ToolArguments:
        return ToolArguments.from_mapping(
            {
                "objective": self.objective,
                "reason": self.reason,
                "acceptance_criteria": list(self.acceptance_criteria),
            }
        )

    @property
    def proposal_sha256(self) -> str:
        return hashlib.sha256(_canonical_proposal_payload(self)).hexdigest()


def canonical_task_admission_id(value: object) -> str:
    if not isinstance(value, str) or _ADMISSION_ID.fullmatch(value) is None:
        raise ValueError("Task admission ID is invalid")
    return value


def task_admission_id(
    *,
    context_id: object,
    tool_use_id: object,
    objective: object,
    reason: object,
    acceptance_criteria: object,
) -> str:
    if not isinstance(context_id, str) or _CONTEXT_ID.fullmatch(context_id) is None:
        raise ValueError("Task admission context identity is invalid")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        raise ValueError("Task admission tool-use ID is invalid")
    _bounded_text(objective, "Task admission objective", 4096, 16 * 1024)
    _bounded_text(
        reason,
        "Task admission reason",
        MAX_TASK_ADMISSION_REASON_CHARACTERS,
        MAX_TASK_ADMISSION_REASON_BYTES,
    )
    if (
        not isinstance(acceptance_criteria, tuple)
        or not 1 <= len(acceptance_criteria) <= MAX_TASK_ADMISSION_CRITERIA
    ):
        raise ValueError("Task admission acceptance criteria are invalid")
    for criterion in acceptance_criteria:
        _bounded_text(criterion, "Task admission acceptance criterion", 1024, 4096)
    payload = json.dumps(
        {
            "acceptance_criteria": list(acceptance_criteria),
            "context_id": context_id,
            "objective": objective,
            "reason": reason,
            "tool_use_id": tool_use_id,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(b"leonervis-task-admission-v1\0" + payload).hexdigest()
    return f"{TASK_ADMISSION_ID_VERSION}-{digest}"


def task_admission_receipt(proposal: TaskAdmissionProposal) -> str:
    if type(proposal) is not TaskAdmissionProposal:
        raise ValueError("Task admission proposal is invalid")
    return json.dumps(
        {
            "admission_id": proposal.admission_id,
            "context_id": proposal.context_id,
            "proposal": "received",
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def recover_task_admission_proposal(turn: CommittedTurn) -> TaskAdmissionProposal | None:
    """Recover one exact proposal from committed causality and Host ledger evidence."""
    if type(turn) is not CommittedTurn:
        raise ValueError("Task admission recovery requires a committed Turn")
    matches: list[tuple[int, ToolUse]] = []
    for index, item in enumerate(turn.items):
        requests = (
            item.tool_uses
            if isinstance(item, AssistantToolBatch)
            else (item,)
            if isinstance(item, ToolUse)
            else ()
        )
        admissions = tuple(
            request for request in requests if request.name == TASK_PROPOSE_START_TOOL_NAME
        )
        if admissions and len(requests) != 1:
            raise ValueError("Task admission call was mixed with a sibling tool request")
        matches.extend((index, request) for request in admissions)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("Task admission Turn contained multiple admission calls")
    request_index, request = matches[0]
    result_index = request_index + 1
    if result_index >= len(turn.items) or not isinstance(turn.items[result_index], ToolResult):
        raise ValueError("Task admission call has no immediate matching result")
    result = turn.items[result_index]
    if result.tool_use_id != request.tool_use_id:
        raise ValueError("Task admission result does not match its request")
    if any(
        isinstance(item, (ToolUse, AssistantToolBatch)) for item in turn.items[result_index + 1 :]
    ):
        raise ValueError("Task admission call was not terminal in its Turn")
    ledger = tuple(
        entry for entry in turn.tool_ledger.entries if entry.tool_use_id == request.tool_use_id
    )
    if (
        result.is_error
        or result.truncated
        or len(ledger) != 1
        or ledger[0].tool_name != TASK_PROPOSE_START_TOOL_NAME
        or ledger[0].outcome is not ToolRequestOutcome.SUCCEEDED
    ):
        raise ValueError("Task admission was not durably recorded as successful")
    receipt_text, marker, ledger_text = result.content.partition("\n\nHost tool ledger:")
    if marker and not ledger_text.endswith("Treat these counts as authoritative."):
        raise ValueError("Task admission receipt is invalid")
    try:
        receipt = json.loads(receipt_text)
    except json.JSONDecodeError:
        raise ValueError("Task admission receipt is invalid") from None
    if not isinstance(receipt, dict) or set(receipt) != {
        "admission_id",
        "context_id",
        "proposal",
    }:
        raise ValueError("Task admission receipt is invalid")
    proposal = TaskAdmissionProposal.from_request(request, receipt.get("context_id"))
    if receipt != json.loads(task_admission_receipt(proposal)):
        raise ValueError("Task admission receipt does not match its committed proposal")
    return proposal


def _canonical_proposal_payload(proposal: TaskAdmissionProposal) -> bytes:
    return json.dumps(
        proposal.arguments.as_mapping(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _bounded_text(value: object, label: str, max_characters: int, max_bytes: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    if len(value) > max_characters:
        raise ValueError(f"{label} exceeds {max_characters} characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
