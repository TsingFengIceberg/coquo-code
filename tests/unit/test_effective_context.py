from __future__ import annotations

from dataclasses import replace

import pytest

from leonervis_code.core.compaction import EffectiveContextSummary
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    SystemPromptSnapshot,
    ToolArguments,
    ToolResult,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
    CanonicalToolDefinition,
    EffectiveContextSnapshot,
    validate_complete_history,
)
from leonervis_code.system_prompt import build_system_prompt
from leonervis_code.core.project_instructions import ProjectInstructionsLoader
from leonervis_code.tools.catalog import TOOL_CATALOG, TOOL_REGISTRY_SNAPSHOT
from leonervis_code.tools.read_file import read_file_model_definition


def snapshot(*history) -> EffectiveContextSnapshot:
    items = tuple(history)
    return EffectiveContextSnapshot(
        representation_version=EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
        source=EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
        system_prompt=build_system_prompt(),
        tool_definitions=TOOL_CATALOG,
        tool_set_id=TOOL_REGISTRY_SNAPSHOT.select().snapshot_id,
        full_history=items,
        effective_history=items,
    )


def test_empty_effective_context_is_stable_and_has_no_synthetic_user() -> None:
    first = snapshot()
    second = snapshot()

    assert first.context_id == second.context_id
    assert (
        first.context_id
        == "ctx-v9-8e257b8889c2794ab1deef575bf96a22a9394cdac71e54234cb769adeaafadc7"
    )
    assert first.full_turn_count == first.effective_turn_count == 0
    assert first.full_item_count == first.effective_item_count == 0
    assert first.to_conversation_request().history == ()


def test_effective_context_projects_exact_frozen_tool_set_and_identity() -> None:
    context = snapshot()
    tool_set = TOOL_REGISTRY_SNAPSHOT.select(("read_file", "grep"))
    selected = replace(
        context,
        tool_definitions=tool_set.definitions,
        tool_set_id=tool_set.snapshot_id,
    )
    request = selected.to_conversation_request(enabled_tool_names=tool_set.names)

    assert selected.context_id != context.context_id
    assert request.enabled_tool_names == tool_set.names
    assert request.tool_definitions == tool_set.definitions
    assert request.tool_set_id == tool_set.snapshot_id
    with pytest.raises(ValueError, match="disabled-tool"):
        selected.to_conversation_request(
            allow_tools=False,
            enabled_tool_names=("read_file",),
        )


def test_complete_tool_turn_is_atomic_and_identity_covers_flags() -> None:
    history = (
        UserMessage("read"),
        ToolUse("call-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
        ToolResult("call-1", "notes", is_error=False, truncated=False),
        AssistantText("done"),
    )
    context = snapshot(*history)
    changed = snapshot(
        history[0],
        history[1],
        replace(history[2], truncated=True),
        history[3],
    )

    assert context.full_turn_count == 1
    assert context.full_item_count == 4
    assert context.effective_turns[0].items == history
    assert context.context_id != changed.context_id
    changed_arguments = snapshot(
        history[0],
        ToolUse("call-1", "read_file", ToolArguments.from_mapping({"path": "other.md"})),
        history[2],
        history[3],
    )
    assert context.context_id != changed_arguments.context_id


def test_complete_tool_batch_is_atomic_and_identity_preserves_order() -> None:
    first = ToolUse("mkdir-src", "mkdir", ToolArguments.from_mapping({"path": "src"}))
    second = ToolUse("mkdir-tests", "mkdir", ToolArguments.from_mapping({"path": "tests"}))
    history = (
        UserMessage("create"),
        AssistantToolBatch((first, second), "Creating directories."),
        ToolResult("mkdir-src", "directory_created"),
        ToolResult("mkdir-tests", "directory_created"),
        AssistantText("done"),
    )

    context = snapshot(*history)

    assert context.full_turn_count == 1
    assert (
        context.context_id
        != snapshot(
            history[0],
            AssistantToolBatch((second, first), "Creating directories."),
            history[3],
            history[2],
            history[4],
        ).context_id
    )
    with pytest.raises(ValueError, match="does not match"):
        snapshot(history[0], history[1], history[3], history[2], history[4])


def test_assistant_text_and_tool_use_are_one_atomic_history_item_and_identity_input() -> None:
    call = ToolUse(
        "call-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will inspect the file.",
    )
    history = (
        UserMessage("read"),
        call,
        ToolResult("call-1", "notes"),
        AssistantText("done"),
    )

    validated = validate_complete_history(history)
    mixed = snapshot(*history)
    pure = snapshot(*history[:1], replace(call, assistant_text=None), *history[2:])

    assert validated.complete_turns[0].items[1] is call
    assert mixed.context_id != pure.context_id


@pytest.mark.parametrize(
    "history, message",
    [
        ((AssistantText("bad"),), "start with a user"),
        ((UserMessage("bad"),), "end with assistant"),
        (
            (
                UserMessage("x"),
                ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "x"})),
                AssistantText("bad"),
            ),
            "does not match",
        ),
        (
            (
                UserMessage("x"),
                ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "x"})),
                ToolUse("two", "read_file", ToolArguments.from_mapping({"path": "y"})),
                ToolResult("two", "y"),
                ToolResult("one", "x"),
                AssistantText("bad"),
            ),
            "does not match",
        ),
        (
            (
                UserMessage("x"),
                ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "x"})),
                ToolResult("one", "x"),
                AssistantText("one"),
                UserMessage("y"),
                ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "y"})),
                ToolResult("one", "y"),
                AssistantText("two"),
            ),
            "duplicate tool use ID",
        ),
    ],
)
def test_complete_history_fails_closed_on_invalid_causality(history, message) -> None:
    with pytest.raises(ValueError, match=message):
        validate_complete_history(history)


def test_context_identity_includes_prompt_and_tool_contract() -> None:
    context = snapshot(UserMessage("hello"), AssistantText("reply"))
    with pytest.raises(ValueError, match="fingerprint"):
        replace(
            context,
            system_prompt=SystemPromptSnapshot(
                version=1,
                text="different\n",
                fingerprint="v1-invalid",
            ),
        )

    tool = read_file_model_definition()
    tool["description"] = "different"
    altered_tool = replace(
        context,
        tool_definitions=(
            CanonicalToolDefinition.from_mapping(tool),
            *TOOL_CATALOG[1:],
        ),
    )
    assert altered_tool.context_id != context.context_id
    assert (
        replace(context, tool_definitions=tuple(reversed(TOOL_CATALOG))).context_id
        != context.context_id
    )
    with pytest.raises(ValueError, match="duplicate"):
        replace(context, tool_definitions=(TOOL_CATALOG[0], TOOL_CATALOG[0]))


def test_context_identity_includes_exact_project_instructions(tmp_path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("first\n", encoding="utf-8")
    first = replace(
        snapshot(),
        project_instructions=ProjectInstructionsLoader(tmp_path).load(),
    )
    target.write_text("second\n", encoding="utf-8")
    second = replace(
        snapshot(),
        project_instructions=ProjectInstructionsLoader(tmp_path).load(),
    )

    assert first.context_id != snapshot().context_id
    assert first.context_id != second.context_id
    assert first.to_conversation_request().project_instructions == first.project_instructions


def test_full_history_source_requires_transcript_and_effective_equality() -> None:
    full = (UserMessage("one"), AssistantText("reply"))
    with pytest.raises(ValueError, match="must equal"):
        EffectiveContextSnapshot(
            representation_version=1,
            source=EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
            system_prompt=build_system_prompt(),
            tool_definitions=TOOL_CATALOG,
            full_history=full,
            effective_history=(),
        )


def test_compacted_context_identity_covers_summary_and_retained_suffix() -> None:
    full = (
        UserMessage("one"),
        AssistantText("a"),
        UserMessage("two"),
        AssistantText("b"),
        UserMessage("three"),
        AssistantText("c"),
    )
    summary = EffectiveContextSummary("Earlier: one")
    context = EffectiveContextSnapshot(
        representation_version=COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
        source=EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
        system_prompt=build_system_prompt(),
        tool_definitions=TOOL_CATALOG,
        tool_set_id=TOOL_REGISTRY_SNAPSHOT.select().snapshot_id,
        full_history=full,
        effective_history=full[-4:],
        effective_summary=summary,
    )

    assert context.context_id.startswith("ctx-v10-")
    assert context.full_turn_count == 3
    assert context.effective_turn_count == 2
    assert context.to_conversation_request().effective_summary == summary
    assert (
        context.context_id
        != replace(context, effective_summary=EffectiveContextSummary("Different")).context_id
    )
    with pytest.raises(ValueError, match="suffix"):
        replace(context, effective_history=full[:4])
