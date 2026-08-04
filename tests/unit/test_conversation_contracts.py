from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from leonervis_code.core.contracts import (
    AssistantText,
    MAX_ASSISTANT_TOOL_TEXT_BYTES,
    MAX_ASSISTANT_TOOL_TEXT_CHARACTERS,
    MAX_TOOL_OUTCOME_ENTRIES,
    ProviderOwnedItem,
    ProviderResponseEnvelope,
    ConversationRequest,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolTurnLedger,
    ToolUse,
)
from leonervis_code.system_prompt import build_system_prompt


def tool_use(*, assistant_text: str | None = None) -> ToolUse:
    return ToolUse(
        "tool-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text=assistant_text,
    )


def test_tool_use_canonically_binds_optional_assistant_text() -> None:
    pure = tool_use()
    mixed = tool_use(assistant_text="I will inspect the file first.")
    whitespace = tool_use(assistant_text="  \n")

    assert pure.assistant_text is None
    assert mixed.assistant_text == "I will inspect the file first."
    assert whitespace.assistant_text == "  \n"
    assert mixed != pure
    with pytest.raises(FrozenInstanceError):
        mixed.assistant_text = "changed"  # type: ignore[misc]


def test_conversation_request_rejects_invalid_project_instruction_value() -> None:
    with pytest.raises(ValueError, match="project instructions"):
        ConversationRequest(
            build_system_prompt(),
            (),
            project_instructions="AGENTS.md",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("value", ["", 1])
def test_tool_use_rejects_noncanonical_assistant_text(value: object) -> None:
    with pytest.raises(ValueError, match="non-empty text or null"):
        tool_use(assistant_text=value)  # type: ignore[arg-type]


def test_tool_use_accepts_exact_text_limit_and_rejects_character_or_byte_overflow() -> None:
    assert tool_use(assistant_text="a" * MAX_ASSISTANT_TOOL_TEXT_CHARACTERS).assistant_text

    with pytest.raises(ValueError, match="supported size"):
        tool_use(assistant_text="a" * (MAX_ASSISTANT_TOOL_TEXT_CHARACTERS + 1))
    assert MAX_ASSISTANT_TOOL_TEXT_BYTES == MAX_ASSISTANT_TOOL_TEXT_CHARACTERS
    with pytest.raises(ValueError, match="supported size"):
        tool_use(assistant_text="界" * (MAX_ASSISTANT_TOOL_TEXT_BYTES // 3 + 1))


def test_tool_use_rejects_non_utf8_assistant_text() -> None:
    with pytest.raises(ValueError, match="valid UTF-8"):
        tool_use(assistant_text="\ud800")


def test_tool_use_rejects_nul_in_assistant_text() -> None:
    with pytest.raises(ValueError, match="NUL"):
        tool_use(assistant_text="before\x00after")


def test_tool_turn_ledger_derives_authoritative_counts_from_ordered_entries() -> None:
    ledger = ToolTurnLedger(
        (
            ToolOutcomeEntry("one", "mkdir", 1, ToolRequestOutcome.ERROR, "invalid_request"),
            ToolOutcomeEntry(
                "two",
                "write_file",
                2,
                ToolRequestOutcome.SKIPPED_AFTER_FAILURE,
                "prior_batch_action_not_succeeded",
            ),
            ToolOutcomeEntry(
                "three",
                "write_file",
                3,
                ToolRequestOutcome.REJECTED_OVER_BUDGET,
                "batch_exceeds_remaining_budget",
            ),
        )
    )

    assert ledger.requested == 3
    assert ledger.admitted == 2
    assert ledger.dispatched == 1
    assert ledger.count(ToolRequestOutcome.ERROR) == 1
    assert ledger.count(ToolRequestOutcome.SKIPPED_AFTER_FAILURE) == 1
    assert ledger.count(ToolRequestOutcome.REJECTED_OVER_BUDGET) == 1


def test_tool_turn_ledger_rejects_gaps_duplicates_and_unbound_synthetic_codes() -> None:
    with pytest.raises(ValueError, match="continuous"):
        ToolTurnLedger((ToolOutcomeEntry("one", "read_file", 2, ToolRequestOutcome.SUCCEEDED),))
    duplicate = ToolOutcomeEntry("one", "read_file", 1, ToolRequestOutcome.SUCCEEDED)
    with pytest.raises(ValueError, match="duplicate"):
        ToolTurnLedger(
            (duplicate, ToolOutcomeEntry("one", "read_file", 2, ToolRequestOutcome.ERROR))
        )
    with pytest.raises(ValueError, match="canonical result code"):
        ToolOutcomeEntry(
            "one",
            "read_file",
            1,
            ToolRequestOutcome.REJECTED_OVER_BUDGET,
            "wrong",
        )
    oversized = tuple(
        ToolOutcomeEntry(
            f"tool-{index}",
            "read_file",
            index,
            ToolRequestOutcome.SUCCEEDED,
        )
        for index in range(1, MAX_TOOL_OUTCOME_ENTRIES + 2)
    )
    with pytest.raises(ValueError, match="entry limit"):
        ToolTurnLedger(oversized)


def test_provider_owned_item_is_canonical_bounded_and_enveloped() -> None:
    item = ProviderOwnedItem.from_mapping(
        {
            "status": "completed",
            "type": "web_search_call",
            "id": "ws_1",
            "action": {"query": "Python", "type": "search"},
        }
    )

    assert item.canonical_json.startswith('{"action"')
    assert item.as_mapping()["id"] == "ws_1"
    envelope = ProviderResponseEnvelope((item,), AssistantText("done"))
    assert envelope.response == AssistantText("done")

    with pytest.raises(ValueError, match="unsupported"):
        ProviderOwnedItem.from_mapping({"type": "code_interpreter_call", "id": "ci_1"})
    with pytest.raises(ValueError, match="duplicate"):
        ProviderResponseEnvelope((item, item), AssistantText("done"))
