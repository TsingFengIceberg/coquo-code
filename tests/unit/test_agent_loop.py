from __future__ import annotations

from dataclasses import replace

import pytest

from coquo.agent.loop import (
    AgentLoop,
    TaskControlProtocolError,
    ToolLoopLimitError,
)
from coquo.agent.task_control import (
    TaskControlDispatchResult,
    TaskControlProposal,
    TaskProposalKind,
)
from coquo.agent.child_control import ChildControlDispatchResult
from coquo.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextStreamCompleted,
    AssistantToolTextReceived,
    ToolDispatchResult,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestSkipped,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
)
from coquo.core.compaction import EffectiveContextSummary
from coquo.core.contracts import (
    AssistantToolBatch,
    ToolArguments,
    AssistantText,
    CommittedTurn,
    ConversationTurn,
    ProviderOwnedItem,
    ProviderResponseEnvelope,
    ToolRequestOutcome,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.providers.fake import ScriptedFakeProvider
from coquo.core.project_instructions import ProjectInstructionsLoader
from coquo.core.extensions import ToolExposure, ToolRegistrySnapshot
from coquo.core.permissions import PermissionAction
from coquo.providers.streaming import ProviderTextDelta
from coquo.tools.glob import GlobTool
from coquo.tools.grep import GrepTool
from coquo.tools.list_directory import ListDirectoryTool
from coquo.tools.read_file import ReadFileTool
from coquo.tools.catalog import (
    MAX_PROVIDER_INVOCATIONS_PER_TURN,
    MAX_TOOL_REQUESTS_PER_TURN,
    TOOL_REGISTRY_SNAPSHOT,
)


def _registry_with_child_controls() -> ToolRegistrySnapshot:
    return TOOL_REGISTRY_SNAPSHOT


_TASK_ID = "11111111-1111-4111-8111-111111111111"
_STAGE_ID = "22222222-2222-4222-8222-222222222222"


def test_loop_commits_provider_owned_items_without_dispatching_them(tmp_path) -> None:
    provider_item = ProviderOwnedItem.from_mapping(
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "current docs"},
        }
    )
    provider = ScriptedFakeProvider(
        [ProviderResponseEnvelope((provider_item,), AssistantText("found"))]
    )
    committed: list[CommittedTurn] = []
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=committed.append,
    )

    assert loop.run("search") == "found"
    assert committed[0].items == (
        UserMessage("search"),
        provider_item,
        AssistantText("found"),
    )
    assert committed[0].tool_ledger.entries == ()


def test_loop_commits_glob_grep_and_read_causality(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "src/*.py"})),
            ToolUse(
                "grep-1",
                "grep",
                ToolArguments.from_mapping({"query": "print", "include": "src/*.py"}),
            ),
            ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "src/app.py"})),
            AssistantText("found and read"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("find code") == "found and read"
    grep_result = '{"path":"src/app.py","line":1,"text":"print(\'ok\')"}\n'
    assert loop.history == (
        UserMessage("find code"),
        ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "src/*.py"})),
        ToolResult("glob-1", "src/app.py\n"),
        ToolUse(
            "grep-1",
            "grep",
            ToolArguments.from_mapping({"query": "print", "include": "src/*.py"}),
        ),
        ToolResult("grep-1", grep_result),
        ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "src/app.py"})),
        ToolResult("read-1", "print('ok')\n"),
        AssistantText("found and read"),
    )
    assert [
        definition.name for definition in loop.effective_context_snapshot().tool_definitions
    ] == [
        "read_file",
        "glob",
        "grep",
        "write_file",
        "edit_file",
        "run_command",
        "mkdir",
        "move_file",
        "delete_file",
        "delete_directory",
        "list_directory",
        "copy_file",
        "read_file_lines",
        "stat_path",
        "list_tree",
        "grep_regex",
        "patch_file",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "web_search",
        "web_fetch",
        "compare_files",
        "git_blame",
        "git_refs",
        "json_query",
        "checksum_file",
        "archive_list",
        "move_directory",
        "download_file",
        "tool_search",
        "tool_promote",
        "skill_search",
        "skill_load",
        "skill_read_resource",
        "task_propose_plan",
        "task_report_reflection",
        "task_report_blocker",
        "task_propose_completion",
        "task_propose_start",
        "task_accept_admission",
        "task_accept_plan",
        "task_confirm_completion",
        "skill_propose_create",
        "skill_accept_create",
        "child_spawn",
        "child_status",
        "child_wait",
        "child_cancel",
        "team_create",
        "team_add_member",
        "team_status",
        "team_message_send",
        "team_message_show",
        "team_message_read",
        "team_work_create",
        "team_schedule_start",
        "team_schedule_wait",
        "team_work_review",
        "team_close",
    ]
    assert provider.received_requests[1].history[-1] == ToolResult("glob-1", "src/app.py\n")
    assert provider.received_requests[2].history[-1] == ToolResult("grep-1", grep_result)
    assert provider.received_requests[3].history[-1] == ToolResult("read-1", "print('ok')\n")


def test_loop_executes_list_directory_and_returns_exact_causal_result(tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "note.txt").write_text("note\n", encoding="utf-8")
    call = ToolUse(
        "list-1",
        "list_directory",
        ToolArguments.from_mapping({"path": "."}),
    )
    provider = ScriptedFakeProvider([call, AssistantText("listed")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("inspect root") == "listed"
    result = ToolResult(
        "list-1",
        '{"path":"empty","type":"directory"}\n{"path":"note.txt","type":"file"}\n',
    )
    assert provider.received_requests[1].history[-2:] == (call, result)
    assert loop.history == (
        UserMessage("inspect root"),
        call,
        result,
        AssistantText("listed"),
    )


def test_loop_executes_one_tool_batch_sequentially_and_returns_all_results(tmp_path) -> None:
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    batch = AssistantToolBatch(
        (
            ToolUse("glob-batch", "glob", ToolArguments.from_mapping({"pattern": "*.py"})),
            ToolUse("read-batch", "read_file", ToolArguments.from_mapping({"path": "a.py"})),
        ),
        "Inspecting both.",
    )
    provider = ScriptedFakeProvider([batch, AssistantText("done")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    events = []

    assert loop.run("inspect", event_sink=events.append) == "done"
    assert loop.history == (
        UserMessage("inspect"),
        batch,
        ToolResult("glob-batch", "a.py\n"),
        ToolResult("read-batch", "a\n"),
        AssistantText("done"),
    )
    assert provider.received_requests[1].history[-3:] == loop.history[1:4]
    assert events[:-1] == [
        AssistantToolTextReceived("Inspecting both."),
        ToolRequestStarted("glob", 1, MAX_TOOL_REQUESTS_PER_TURN, "pattern='*.py'"),
        ToolRequestFinished("glob", 1, MAX_TOOL_REQUESTS_PER_TURN, ToolEventStatus.SUCCEEDED),
        ToolRequestStarted("read_file", 2, MAX_TOOL_REQUESTS_PER_TURN, "path='a.py'"),
        ToolRequestFinished("read_file", 2, MAX_TOOL_REQUESTS_PER_TURN, ToolEventStatus.SUCCEEDED),
    ]
    assert isinstance(events[-1], ToolTurnSummaryCommitted)
    assert events[-1].ledger.requested == events[-1].ledger.dispatched == 2


def test_loop_skips_remaining_batch_calls_after_first_non_success(tmp_path) -> None:
    batch = AssistantToolBatch(
        (
            ToolUse(
                "read-missing",
                "read_file",
                ToolArguments.from_mapping({"path": "missing.txt"}),
            ),
            ToolUse("glob-skipped", "glob", ToolArguments.from_mapping({"pattern": "*.py"})),
        )
    )
    provider = ScriptedFakeProvider([batch, AssistantText("replanned")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("inspect") == "replanned"
    assert loop.history[2:4] == (
        ToolResult("read-missing", "read_file path does not exist", is_error=True),
        ToolResult(
            "glob-skipped",
            "tool was not executed because an earlier action in the same batch did not succeed",
            is_error=True,
        ),
    )


def test_loop_rejects_oversized_provider_batch_before_any_dispatch(tmp_path) -> None:
    batch = AssistantToolBatch(
        tuple(
            ToolUse(
                f"read-{index}",
                "read_file",
                ToolArguments.from_mapping({"path": "missing.txt"}),
            )
            for index in range(1, 10)
        )
    )
    provider = ScriptedFakeProvider([batch])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    with pytest.raises(ToolLoopLimitError, match="per-response"):
        loop.run("inspect")

    assert loop.history == ()


def test_twenty_fourth_provider_invocation_is_text_only(tmp_path) -> None:
    calls = [
        ToolUse(
            f"read-{index}",
            "read_file",
            ToolArguments.from_mapping({"path": "missing.txt"}),
        )
        for index in range(1, MAX_PROVIDER_INVOCATIONS_PER_TURN)
    ]
    provider = ScriptedFakeProvider([*calls, AssistantText("bounded")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("inspect") == "bounded"
    assert len(provider.received_requests) == MAX_PROVIDER_INVOCATIONS_PER_TURN
    assert all(request.allow_tools for request in provider.received_requests[:-1])
    assert provider.received_requests[-1].allow_tools is False
    final_result = provider.received_requests[-1].history[-1]
    assert isinstance(final_result, ToolResult)
    assert "Host tool ledger: requested=23 admitted=23 dispatched=23" in final_result.content
    assert "error=23" in final_result.content


def test_first_provider_response_hook_runs_before_tools_and_consumes_shared_budget(
    tmp_path,
) -> None:
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                "read-1",
                "read_file",
                ToolArguments.from_mapping({"path": "missing.txt"}),
            ),
            AssistantText("done"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    timeline: list[object] = []

    def prepare_title() -> int:
        timeline.append("title")
        return 22

    assert (
        loop.run(
            "inspect",
            event_sink=timeline.append,
            first_provider_response_hook=prepare_title,
        )
        == "done"
    )

    title_index = timeline.index("title")
    tool_index = next(
        index for index, event in enumerate(timeline) if isinstance(event, ToolRequestStarted)
    )
    assert title_index < tool_index
    assert provider.received_requests[-1].allow_tools is False


def test_loop_counts_glob_and_read_against_one_shared_budget(tmp_path) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "*.py"})),
            ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "a.py"})),
            ToolUse("glob-2", "glob", ToolArguments.from_mapping({"pattern": "*.py"})),
            ToolUse("read-2", "read_file", ToolArguments.from_mapping({"path": "a.py"})),
            ToolUse("glob-3", "glob", ToolArguments.from_mapping({"pattern": "*.py"})),
            ToolUse("read-3", "read_file", ToolArguments.from_mapping({"path": "a.py"})),
            ToolUse("glob-4", "glob", ToolArguments.from_mapping({"pattern": "*.py"})),
            AssistantText("bounded"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("inspect") == "bounded"
    results = [item for item in loop.history if isinstance(item, ToolResult)]
    assert [result.tool_use_id for result in results] == [
        "glob-1",
        "read-1",
        "glob-2",
        "read-2",
        "glob-3",
        "read-3",
        "glob-4",
    ]
    assert results[-1] == ToolResult("glob-4", "a.py\n")


def test_loop_commits_structured_tool_causality_after_final_text(tmp_path) -> None:
    (tmp_path / "README.md").write_text("Project notes\n", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                tool_use_id="read-1",
                name="read_file",
                arguments=ToolArguments.from_mapping({"path": "README.md"}),
            ),
            AssistantText(text="I read the project notes."),
            AssistantText(text="Second reply"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("Read README") == "I read the project notes."
    assert loop.history == (
        UserMessage(text="Read README"),
        ToolUse(
            tool_use_id="read-1",
            name="read_file",
            arguments=ToolArguments.from_mapping({"path": "README.md"}),
        ),
        ToolResult(tool_use_id="read-1", content="Project notes\n"),
        AssistantText(text="I read the project notes."),
    )
    assert loop.turns == (
        ConversationTurn(
            user=UserMessage(text="Read README"),
            assistant=AssistantText(text="I read the project notes."),
        ),
    )

    assert loop.run("Continue") == "Second reply"
    assert provider.received_requests[-1].history == loop.history[:-1]


def test_prepared_turn_is_read_only_and_rebases_the_same_pending_user(tmp_path) -> None:
    provider = ScriptedFakeProvider([AssistantText("done")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    prepared = loop.prepare_turn("pending")

    assert loop.history == ()
    assert prepared.initial_request.history == (prepared.user,)
    assert prepared.initial_request.tool_definitions == prepared.tool_set_snapshot.definitions
    assert prepared.initial_request.tool_set_id == prepared.tool_set_snapshot.snapshot_id
    summary = EffectiveContextSummary("earlier")
    loop.install_compaction(summary=summary, retained_history=())
    rebased = prepared.rebase(loop.effective_context_snapshot())
    assert rebased.user is prepared.user
    assert rebased.pending_items is prepared.pending_items
    assert rebased.tool_set_snapshot is prepared.tool_set_snapshot
    assert rebased.context.tool_set_id == prepared.context.tool_set_id
    assert rebased.initial_request.history == (prepared.user,)
    assert rebased.initial_request.effective_summary == summary

    assert loop.run_prepared(rebased) == "done"
    assert provider.received_requests[0].history == (prepared.user,)
    assert loop.history == (prepared.user, AssistantText("done"))

    provider = ScriptedFakeProvider(
        [
            ToolUse(
                tool_use_id="unknown-1",
                name="search",
                arguments=ToolArguments.from_mapping({"path": "README.md"}),
            ),
            AssistantText(text="The requested tool is unavailable."),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    with pytest.raises(ValueError, match="outside the prepared tool set"):
        loop.run("Search")
    assert loop.history == ()
    assert len(provider.received_requests) == 1


def test_pinned_tool_set_rebuild_does_not_reopen_the_registry(tmp_path) -> None:
    loop = AgentLoop(
        ScriptedFakeProvider([AssistantText("done")]),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    prepared = loop.prepare_turn("inspect", enabled_tool_names=("read_file",))

    def fail_registry_lookup():
        raise AssertionError("a pinned ToolSet must not reopen the registry")

    loop._tool_registry_factory = fail_registry_lookup
    rebuilt = loop.effective_context_snapshot_with_project_instructions(
        prepared.context.project_instructions,
        tool_set_snapshot=prepared.tool_set_snapshot,
    )

    assert rebuilt.context_id == prepared.context.context_id
    assert rebuilt.tool_definitions == prepared.tool_set_snapshot.definitions
    assert rebuilt.tool_set_id == prepared.tool_set_snapshot.snapshot_id


def test_prepared_turn_advances_only_through_an_explicit_later_tool_set_epoch(
    tmp_path,
) -> None:
    read_contract = TOOL_REGISTRY_SNAPSHOT.contract("read_file")
    grep_contract = replace(
        TOOL_REGISTRY_SNAPSHOT.contract("grep"),
        exposure=ToolExposure.DEFERRED,
    )
    registry = ToolRegistrySnapshot(2, (read_contract, grep_contract))
    loop = AgentLoop(
        ScriptedFakeProvider([AssistantText("done")]),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        tool_registry_factory=lambda: registry,
    )
    prepared = loop.prepare_turn("inspect")

    assert prepared.tool_set_snapshot.epoch == 0
    assert prepared.tool_set_snapshot.names == ("read_file",)
    promoted = prepared.tool_set_snapshot.promote(registry, ("grep",))
    advanced = prepared.advance_tool_set(promoted)

    assert advanced.tool_set_snapshot.epoch == 1
    assert advanced.tool_set_snapshot.names == ("read_file", "grep")
    assert advanced.context.context_id != prepared.context.context_id
    assert advanced.initial_request.enabled_tool_names == ("read_file", "grep")
    assert advanced.initial_request.tool_definitions == promoted.definitions
    assert advanced.initial_request.tool_set_id == promoted.snapshot_id


def test_discovery_search_promotes_exact_candidate_for_later_invocation_without_execution(
    tmp_path,
) -> None:
    from coquo.core.effective_context import CanonicalToolDefinition
    from coquo.core.extensions import (
        ExtensionSource,
        ExtensionSourceKind,
        ExtensionToolContract,
        ToolExecutionKind,
    )

    remote = ExtensionToolContract(
        CanonicalToolDefinition.from_mapping(
            {
                "name": "mcp_fixture_find_widgets_1234567890",
                "description": "Untrusted MCP tool for widget lookup.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        ),
        ExtensionSource(ExtensionSourceKind.MCP, "mcp.project.fixture.2025-06-18", 1),
        ToolExecutionKind.MCP_REMOTE,
        ToolExposure.DEFERRED,
        (PermissionAction.DANGEROUS,),
    )
    registry = ToolRegistrySnapshot(
        2,
        (*TOOL_REGISTRY_SNAPSHOT.contracts, remote),
    )
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                "search-1",
                "tool_search",
                ToolArguments.from_mapping({"query": "widget", "max_results": 4}),
            ),
            ToolUse(
                "promote-1",
                "tool_promote",
                ToolArguments.from_mapping({"names": [remote.name]}),
            ),
            ToolUse(
                "remote-1",
                remote.name,
                ToolArguments.from_mapping({"query": "blue"}),
            ),
            AssistantText("execution remained unavailable"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        tool_registry_factory=lambda: registry,
    )

    assert loop.run("find widgets") == "execution remained unavailable"
    assert remote.name not in tuple(
        definition.name for definition in provider.received_requests[0].tool_definitions
    )
    assert remote.name not in tuple(
        definition.name for definition in provider.received_requests[1].tool_definitions
    )
    assert remote.name in provider.received_requests[2].enabled_tool_names
    assert provider.received_requests[2].tool_set_id != provider.received_requests[1].tool_set_id
    remote_result = next(
        item
        for item in loop.history
        if isinstance(item, ToolResult) and item.tool_use_id == "remote-1"
    )
    assert remote_result.is_error
    assert remote_result.content == "MCP tool execution requires a ProjectSession action boundary"


def test_loop_does_not_commit_candidate_when_provider_fails_after_a_tool(tmp_path) -> None:
    (tmp_path / "README.md").write_text("contents", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                tool_use_id="read-1",
                name="read_file",
                arguments=ToolArguments.from_mapping({"path": "README.md"}),
            ),
            RuntimeError("provider failed"),
            AssistantText(text="retry reply"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        loop.run("failed prompt")

    assert loop.history == ()
    assert loop.effective_history == ()
    assert loop.turns == ()
    assert loop.run("retry prompt") == "retry reply"
    assert provider.received_requests[-1].history == (UserMessage(text="retry prompt"),)


def test_loop_bounds_tool_requests_and_returns_budget_error_before_final_text(tmp_path) -> None:
    (tmp_path / "README.md").write_text("contents", encoding="utf-8")
    requests = [
        ToolUse(
            tool_use_id=f"read-{number}",
            name="read_file",
            arguments=ToolArguments.from_mapping({"path": "README.md"}),
        )
        for number in range(1, MAX_TOOL_REQUESTS_PER_TURN + 2)
    ]
    batches = [
        AssistantToolBatch(tuple(requests[start : start + 8]))
        for start in range(0, MAX_TOOL_REQUESTS_PER_TURN, 8)
    ]
    provider = ScriptedFakeProvider(
        [*batches, requests[-1], AssistantText(text="Finished after the limit.")]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("Read repeatedly") == "Finished after the limit."
    results = [item for item in loop.history if isinstance(item, ToolResult)]
    assert len(results) == MAX_TOOL_REQUESTS_PER_TURN + 1
    assert results[-1].tool_use_id == f"read-{MAX_TOOL_REQUESTS_PER_TURN + 1}"
    assert results[-1].is_error is True
    assert "Host tool ledger: requested=33 admitted=32 dispatched=32" in results[-1].content
    assert "rejected_over_budget=1 unused_admission_slots=0" in results[-1].content
    assert "tool_requests_closed=true" in results[-1].content
    assert provider.received_requests[-1].allow_tools is False


def test_loop_rejects_another_tool_after_the_limit_without_committing(tmp_path) -> None:
    (tmp_path / "README.md").write_text("contents", encoding="utf-8")
    requests = [
        ToolUse(
            tool_use_id=f"read-{number}",
            name="read_file",
            arguments=ToolArguments.from_mapping({"path": "README.md"}),
        )
        for number in range(1, MAX_TOOL_REQUESTS_PER_TURN + 3)
    ]
    batches = [
        AssistantToolBatch(tuple(requests[start : start + 8]))
        for start in range(0, MAX_TOOL_REQUESTS_PER_TURN, 8)
    ]
    provider = ScriptedFakeProvider([*batches, requests[-2], requests[-1]])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    with pytest.raises(ToolLoopLimitError, match="final text-only"):
        loop.run("Read repeatedly")

    assert loop.history == ()
    assert loop.effective_history == ()
    assert loop.turns == ()


def test_tool_budget_resets_for_each_user_turn(tmp_path) -> None:
    (tmp_path / "README.md").write_text("contents", encoding="utf-8")
    first_turn = [
        ToolUse(
            tool_use_id=f"first-{number}",
            name="read_file",
            arguments=ToolArguments.from_mapping({"path": "README.md"}),
        )
        for number in range(1, 7)
    ]
    second_turn = [
        ToolUse(
            tool_use_id=f"second-{number}",
            name="read_file",
            arguments=ToolArguments.from_mapping({"path": "README.md"}),
        )
        for number in range(1, 7)
    ]
    provider = ScriptedFakeProvider(
        [*first_turn, AssistantText("first done"), *second_turn, AssistantText("second done")]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert loop.run("first") == "first done"
    assert loop.run("second") == "second done"
    results = [item for item in loop.history if isinstance(item, ToolResult)]
    assert len(results) == 12
    assert all(not result.is_error for result in results)


def test_loop_persists_complete_turn_before_memory_commit(tmp_path) -> None:
    committed: list[CommittedTurn] = []
    provider = ScriptedFakeProvider([AssistantText(text="saved")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=committed.append,
    )

    assert loop.run("persist") == "saved"
    assert len(committed) == 1
    assert committed[0].items == (UserMessage("persist"), AssistantText("saved"))
    assert committed[0].user == UserMessage("persist")
    assert committed[0].assistant == AssistantText("saved")
    assert committed[0].hook_audit.entries[-1].event.value == "turn_committed"
    assert loop.history == committed[0].items


def test_loop_does_not_commit_memory_when_durable_commit_fails(tmp_path) -> None:
    (tmp_path / "README.md").write_text("notes\n", encoding="utf-8")
    call = ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will inspect first.",
    )
    provider = ScriptedFakeProvider([call, AssistantText(text="not durable")])

    def fail(_: CommittedTurn) -> None:
        raise OSError("disk full")

    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=fail,
    )
    events = []

    with pytest.raises(OSError, match="disk full"):
        loop.run("persist", event_sink=events.append)

    assert loop.history == ()
    assert loop.effective_history == ()
    assert loop.turns == ()
    assert not any(isinstance(event, ToolTurnSummaryCommitted) for event in events)
    assert provider.received_requests[1].history[-2:] == (
        call,
        ToolResult("read-1", "notes\n"),
    )


def test_loop_restores_validated_history_and_rejects_broken_causality(tmp_path) -> None:
    restored = (
        UserMessage("read"),
        ToolUse("call-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
        ToolResult("call-1", "notes"),
        AssistantText("done"),
    )
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        initial_history=restored,
    )

    assert loop.history == restored
    assert loop.effective_history == restored
    assert loop.turns == (ConversationTurn(UserMessage("read"), AssistantText("done")),)

    with pytest.raises(ValueError, match="does not match"):
        AgentLoop(
            None,
            ReadFileTool(tmp_path),
            GlobTool(tmp_path),
            GrepTool(tmp_path),
            ListDirectoryTool(tmp_path),
            initial_history=(
                UserMessage("read"),
                ToolUse("call-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
                ToolResult("other", "notes"),
                AssistantText("done"),
            ),
        )


def test_history_snapshots_cannot_be_mutated_by_later_turns(tmp_path) -> None:
    provider = ScriptedFakeProvider(
        [AssistantText(text="first reply"), AssistantText(text="second reply")]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    loop.run("first prompt")
    first_request = provider.received_requests[0].history

    loop.run("second prompt")

    assert first_request == (UserMessage(text="first prompt"),)
    assert loop.history is not first_request


def test_committed_context_snapshot_is_exact_read_only_and_independent(tmp_path) -> None:
    history = (
        UserMessage("read"),
        ToolUse("call-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
        ToolResult("call-1", "notes"),
        AssistantText("done"),
    )
    snapshots = []

    def build_snapshot():
        from coquo.system_prompt import build_system_prompt

        snapshot = build_system_prompt()
        snapshots.append(snapshot)
        return snapshot

    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        initial_history=history,
        system_prompt_factory=build_snapshot,
    )

    request = loop.committed_context_request()

    assert request.history == history
    assert request.history is loop.history
    assert isinstance(request.history[-1], AssistantText)
    assert loop.history == history
    assert loop.turns == (ConversationTurn(UserMessage("read"), AssistantText("done")),)
    assert len(snapshots) == 1


def test_empty_committed_context_has_no_synthetic_user_message(tmp_path) -> None:
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    request = loop.committed_context_request()

    assert request.history == ()
    assert loop.history == ()
    assert loop.effective_history == ()
    assert loop.turns == ()


def test_loop_pins_one_system_prompt_snapshot_across_tool_continuations(tmp_path) -> None:
    (tmp_path / "README.md").write_text("notes\n", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse("call-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
            AssistantText("done"),
        ]
    )
    snapshots = []

    def build_snapshot():
        from coquo.system_prompt import build_system_prompt

        snapshot = build_system_prompt()
        snapshots.append(snapshot)
        return snapshot

    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        system_prompt_factory=build_snapshot,
    )

    assert loop.run("read") == "done"
    assert len(snapshots) == 1
    assert [request.system_prompt for request in provider.received_requests] == [
        snapshots[0],
        snapshots[0],
    ]
    assert (
        provider.received_requests[0].system_prompt is provider.received_requests[1].system_prompt
    )
    assert all(snapshots[0].text not in repr(item) for item in loop.history)


def _action_lease_for(prepared, *, lease_id="12345678-1234-4234-9234-123456789abc"):
    from coquo.core.actions import ActionLease

    return ActionLease(
        session_id="22345678-1234-4234-9234-123456789abc",
        lease_id=lease_id,
        runtime_generation=0,
        context_id=prepared.context.context_id,
    )


def test_prepared_turn_binds_one_action_lease_and_cannot_rebase_after_binding(tmp_path) -> None:
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    prepared = loop.prepare_turn("hello")
    lease = _action_lease_for(prepared)

    leased = prepared.with_action_lease(lease)

    assert leased.action_lease == lease
    with pytest.raises(ValueError, match="already has"):
        leased.with_action_lease(lease)
    with pytest.raises(ValueError, match="cannot be rebased"):
        leased.rebase(loop.effective_context_snapshot())
    with pytest.raises(ValueError, match="context does not match"):
        prepared.with_action_lease(replace(lease, context_id=f"ctx-v1-{'0' * 64}"))


def test_project_instructions_are_pinned_across_continuations_and_reload_next_turn(
    tmp_path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("first guidance\n", encoding="utf-8")
    (tmp_path / "note.txt").write_text("note", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "note.txt"})),
            AssistantText("first done"),
            AssistantText("second done"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        project_instructions_factory=ProjectInstructionsLoader(tmp_path).load,
    )

    prepared = loop.prepare_turn("first")
    (tmp_path / "AGENTS.md").write_text("second guidance\n", encoding="utf-8")
    assert loop.run_prepared(prepared) == "first done"
    assert loop.run("second") == "second done"

    first, continuation, second = provider.received_requests
    assert first.project_instructions is continuation.project_instructions
    assert first.project_instructions is not None
    assert first.project_instructions.text == "first guidance\n"
    assert second.project_instructions is not None
    assert second.project_instructions.text == "second guidance\n"


def test_invalid_project_instructions_block_provider_invocation(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"\xff")
    provider = ScriptedFakeProvider([AssistantText("must not run")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        project_instructions_factory=ProjectInstructionsLoader(tmp_path).load,
    )

    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        loop.run("blocked")
    assert provider.received_requests == ()


def test_action_dispatcher_receives_the_same_lease_across_tool_continuations(tmp_path) -> None:
    first = ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "a.txt"}))
    second = ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "*.txt"}))
    provider = ScriptedFakeProvider([first, second, AssistantText("done")])
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    prepared = loop.prepare_turn("inspect")
    lease = _action_lease_for(prepared)
    received = []

    def dispatch(request, current_lease):
        received.append((request, current_lease))
        return ToolResult(request.tool_use_id, f"resolved {request.name}")

    loop.install_action_dispatcher(dispatch)

    assert loop.run_prepared(prepared.with_action_lease(lease), provider=provider) == "done"
    assert received == [(first, lease), (second, lease)]
    assert provider.received_requests[1].history[-1] == ToolResult("read-1", "resolved read_file")
    assert provider.received_requests[2].history[-1] == ToolResult("glob-1", "resolved glob")


def test_over_budget_batch_gets_results_without_entering_action_dispatch(tmp_path) -> None:
    calls = [
        ToolUse(
            f"read-{index}",
            "read_file",
            ToolArguments.from_mapping({"path": f"{index}.txt"}),
        )
        for index in range(1, MAX_TOOL_REQUESTS_PER_TURN + 2)
    ]
    batches = [
        AssistantToolBatch(tuple(calls[start : start + 8]))
        for start in range(0, MAX_TOOL_REQUESTS_PER_TURN, 8)
    ]
    provider = ScriptedFakeProvider([*batches, calls[-1], AssistantText("stopped")])
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    prepared = loop.prepare_turn("inspect")
    lease = _action_lease_for(prepared)
    dispatched = []
    events = []

    def dispatch(request, _lease):
        dispatched.append(request.tool_use_id)
        return ToolResult(request.tool_use_id, "ok")

    loop.install_action_dispatcher(dispatch)

    assert (
        loop.run_prepared(
            prepared.with_action_lease(lease), provider=provider, event_sink=events.append
        )
        == "stopped"
    )
    assert dispatched == [f"read-{index}" for index in range(1, 33)]
    final_result = provider.received_requests[-1].history[-1]
    assert isinstance(final_result, ToolResult)
    assert final_result.tool_use_id == "read-33"
    assert "rejected_over_budget=1 unused_admission_slots=0" in final_result.content
    assert "tool_requests_closed=true" in final_result.content
    skipped = next(event for event in events if isinstance(event, ToolRequestSkipped))
    assert skipped.call_index == 33
    assert skipped.call_limit == MAX_TOOL_REQUESTS_PER_TURN
    assert isinstance(events[-1], ToolTurnSummaryCommitted)
    assert events[-1].ledger.count(ToolRequestOutcome.REJECTED_OVER_BUDGET) == 1


def test_host_ledger_accounts_for_failed_batch_skips_successes_and_over_budget_calls(
    tmp_path,
) -> None:
    calls = [
        ToolUse(
            f"call-{index}",
            "write_file" if index != 1 else "mkdir",
            ToolArguments.from_mapping(
                {"path": f"file-{index:02}.txt", "content": f"{index:02}\n"}
                if index != 1
                else {"path": "budget-33"}
            ),
        )
        for index in range(1, 41)
    ]
    batches = [AssistantToolBatch(tuple(calls[start : start + 8])) for start in range(0, 40, 8)]
    provider = ScriptedFakeProvider([*batches, AssistantText("reported arithmetic")])
    committed = []
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=committed.append,
    )
    prepared = loop.prepare_turn("create many")
    lease = _action_lease_for(prepared)

    def dispatch(request, _lease):
        if request.tool_use_id == "call-1":
            return ToolDispatchResult(
                ToolResult(request.tool_use_id, "already exists", is_error=True),
                ToolEventStatus.ERROR,
                "invalid_request",
            )
        return ToolDispatchResult(
            ToolResult(request.tool_use_id, "created"),
            ToolEventStatus.SUCCEEDED,
            "created",
        )

    loop.install_action_dispatcher(dispatch)
    events = []
    usage = []

    assert (
        loop.run_prepared(
            prepared.with_action_lease(lease),
            provider=provider,
            event_sink=events.append,
            tool_usage_sink=usage.append,
        )
        == "reported arithmetic"
    )
    ledger = committed[0].tool_ledger
    assert (ledger.requested, ledger.admitted, ledger.dispatched) == (40, 32, 25)
    assert ledger.count(ToolRequestOutcome.SUCCEEDED) == 24
    assert ledger.count(ToolRequestOutcome.ERROR) == 1
    assert ledger.count(ToolRequestOutcome.SKIPPED_AFTER_FAILURE) == 7
    assert ledger.count(ToolRequestOutcome.REJECTED_OVER_BUDGET) == 8
    assert [entry.request_index for entry in ledger.entries] == list(range(1, 41))
    final_result = provider.received_requests[-1].history[-1]
    assert isinstance(final_result, ToolResult)
    assert "requested=40 admitted=32 dispatched=25 succeeded=24 error=1" in final_result.content
    assert "skipped_after_failure=7 rejected_over_budget=8 unused_admission_slots=0" in (
        final_result.content
    )
    assert "tool_requests_closed=true" in final_result.content
    assert isinstance(events[-1], ToolTurnSummaryCommitted)
    assert events[-1].ledger == ledger
    assert (
        usage[-1].requested,
        usage[-1].admitted,
        usage[-1].dispatched,
        usage[-1].succeeded,
        usage[-1].unsuccessful,
    ) == (40, 32, 25, 24, 1)


def test_forced_finalization_closes_tools_despite_one_unused_admission_slot(tmp_path) -> None:
    calls = [
        ToolUse(
            f"call-{index}",
            "write_file" if index != 1 else "mkdir",
            ToolArguments.from_mapping(
                {"path": f"file-{index:02}.txt", "content": f"{index:02}\n"}
                if index != 1
                else {"path": "already-exists"}
            ),
        )
        for index in range(1, 40)
    ]
    batches = (
        AssistantToolBatch(tuple(calls[0:8])),
        AssistantToolBatch(tuple(calls[8:15])),
        AssistantToolBatch(tuple(calls[15:23])),
        AssistantToolBatch(tuple(calls[23:31])),
        AssistantToolBatch(tuple(calls[31:39])),
    )
    provider = ScriptedFakeProvider([*batches, AssistantText("reported closed tools")])
    committed = []
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=committed.append,
    )
    prepared = loop.prepare_turn("create many")
    lease = _action_lease_for(prepared)

    def dispatch(request, _lease):
        if request.tool_use_id == "call-1":
            return ToolDispatchResult(
                ToolResult(request.tool_use_id, "already exists", is_error=True),
                ToolEventStatus.ERROR,
                "invalid_request",
            )
        return ToolDispatchResult(
            ToolResult(request.tool_use_id, "created"),
            ToolEventStatus.SUCCEEDED,
            "created",
        )

    loop.install_action_dispatcher(dispatch)

    assert loop.run_prepared(prepared.with_action_lease(lease), provider=provider) == (
        "reported closed tools"
    )
    ledger = committed[0].tool_ledger
    assert (ledger.requested, ledger.admitted, ledger.dispatched) == (39, 31, 24)
    assert ledger.count(ToolRequestOutcome.SUCCEEDED) == 23
    assert ledger.count(ToolRequestOutcome.ERROR) == 1
    assert ledger.count(ToolRequestOutcome.SKIPPED_AFTER_FAILURE) == 7
    assert ledger.count(ToolRequestOutcome.REJECTED_OVER_BUDGET) == 8
    final_request = provider.received_requests[-1]
    assert final_request.allow_tools is False
    final_result = final_request.history[-1]
    assert isinstance(final_result, ToolResult)
    assert "requested=39 admitted=31 dispatched=24 succeeded=23 error=1" in (final_result.content)
    assert "skipped_after_failure=7 rejected_over_budget=8" in final_result.content
    assert "unused_admission_slots=1 tool_requests_closed=true" in final_result.content
    assert "remaining_budget" not in final_result.content


def test_tool_events_preserve_sequential_order_status_code_and_truncation(tmp_path) -> None:
    first = ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "a.txt"}))
    second = ToolUse(
        "grep-1",
        "grep",
        ToolArguments.from_mapping({"query": "secret query", "include": "*.txt"}),
    )
    provider = ScriptedFakeProvider([first, second, AssistantText("done")])
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    prepared = loop.prepare_turn("inspect")
    lease = _action_lease_for(prepared)

    def dispatch(request, _lease):
        if request.name == "read_file":
            return ToolDispatchResult(
                ToolResult(request.tool_use_id, "bounded", truncated=True),
                ToolEventStatus.SUCCEEDED,
                "ok",
            )
        return ToolDispatchResult(
            ToolResult(request.tool_use_id, "denied", is_error=True),
            ToolEventStatus.DENIED,
            "denied_read_only_mode",
        )

    loop.install_action_dispatcher(dispatch)
    events = []
    usage = []

    assert (
        loop.run_prepared(
            prepared.with_action_lease(lease),
            provider=provider,
            event_sink=events.append,
            tool_usage_sink=usage.append,
        )
        == "done"
    )
    assert events[:-1] == [
        ToolRequestStarted("read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, "path='a.txt'"),
        ToolRequestFinished(
            "read_file",
            1,
            MAX_TOOL_REQUESTS_PER_TURN,
            ToolEventStatus.SUCCEEDED,
            "ok",
            truncated=True,
        ),
        ToolRequestStarted("grep", 2, MAX_TOOL_REQUESTS_PER_TURN, "include='*.txt' query_bytes=12"),
        ToolRequestFinished(
            "grep",
            2,
            MAX_TOOL_REQUESTS_PER_TURN,
            ToolEventStatus.DENIED,
            "denied_read_only_mode",
        ),
    ]
    assert isinstance(events[-1], ToolTurnSummaryCommitted)
    assert events[-1].ledger.count(ToolRequestOutcome.SUCCEEDED) == 1
    assert events[-1].ledger.count(ToolRequestOutcome.DENIED) == 1
    assert "secret query" not in repr(events)


def test_tool_event_sink_failure_does_not_change_execution_or_commit(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("ok\n", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "a.txt"})),
            AssistantText("done"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    def broken_sink(_event):
        raise OSError("terminal closed")

    assert loop.run("inspect", event_sink=broken_sink) == "done"
    assert loop.turns[-1].assistant == AssistantText("done")


def test_dispatch_exception_emits_outcome_unknown_without_committing(tmp_path) -> None:
    call = ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "a.txt"}))
    provider = ScriptedFakeProvider([call])
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    prepared = loop.prepare_turn("inspect")
    lease = _action_lease_for(prepared)
    loop.install_action_dispatcher(
        lambda _request, _lease: (_ for _ in ()).throw(RuntimeError("audit failed"))
    )
    events = []
    usage = []

    with pytest.raises(RuntimeError, match="audit failed"):
        loop.run_prepared(
            prepared.with_action_lease(lease),
            provider=provider,
            event_sink=events.append,
            tool_usage_sink=usage.append,
        )

    assert events == [
        ToolRequestStarted("read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, "path='a.txt'"),
        ToolRequestFinished(
            "read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, ToolEventStatus.OUTCOME_UNKNOWN
        ),
    ]
    assert usage[-1].requested == usage[-1].admitted == usage[-1].dispatched == 1
    assert usage[-1].succeeded == 0
    assert usage[-1].unsuccessful == 1
    assert loop.history == ()


def test_assistant_tool_text_is_displayed_executed_continued_and_committed(tmp_path) -> None:
    (tmp_path / "README.md").write_text("notes\n", encoding="utf-8")
    call = ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will read the file.",
    )
    provider = ScriptedFakeProvider([call, AssistantText("The file contains notes.")])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    events = []

    assert loop.run("inspect", event_sink=events.append) == "The file contains notes."
    result = ToolResult("read-1", "notes\n")
    assert events[:-1] == [
        AssistantToolTextReceived("I will read the file."),
        ToolRequestStarted("read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, "path='README.md'"),
        ToolRequestFinished("read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, ToolEventStatus.SUCCEEDED),
    ]
    assert isinstance(events[-1], ToolTurnSummaryCommitted)
    assert provider.received_requests[1].history[-2:] == (call, result)
    assert loop.history == (
        UserMessage("inspect"),
        call,
        result,
        AssistantText("The file contains notes."),
    )


def test_streaming_loop_orders_complete_tool_text_execution_and_durable_final_commit(
    tmp_path,
) -> None:
    (tmp_path / "README.md").write_text("notes\n", encoding="utf-8")
    call = ToolUse(
        "read-stream",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will read.",
    )

    class StreamingProvider:
        def __init__(self) -> None:
            self.outcomes = [(call, ("I will ", "read.")), (AssistantText("Done."), ("Do", "ne."))]
            self.requests = []

        def respond(self, _request):
            raise AssertionError("streaming path expected")

        def respond_stream(self, request, *, event_sink):
            self.requests.append(request)
            response, parts = self.outcomes.pop(0)
            for part in parts:
                event_sink(ProviderTextDelta(part))
            return response

    provider = StreamingProvider()
    committed = []
    events = []
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=committed.append,
    )

    assert loop.run("inspect", event_sink=events.append) == "Done."
    assert events[:-1] == [
        AssistantResponseTextDeltaReceived("I will "),
        AssistantResponseTextDeltaReceived("read."),
        AssistantToolTextStreamCompleted("I will read."),
        ToolRequestStarted("read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, "path='README.md'"),
        ToolRequestFinished("read_file", 1, MAX_TOOL_REQUESTS_PER_TURN, ToolEventStatus.SUCCEEDED),
        AssistantResponseTextDeltaReceived("Do"),
        AssistantResponseTextDeltaReceived("ne."),
        AssistantFinalTextStreamCommitted("Done."),
    ]
    assert isinstance(events[-1], ToolTurnSummaryCommitted)
    assert committed[0].items[-1] == AssistantText("Done.")
    assert committed[0].tool_ledger == events[-1].ledger
    assert provider.requests[1].history[-2:] == (call, ToolResult("read-stream", "notes\n"))


def test_streaming_loop_does_not_confirm_or_commit_final_text_when_durability_fails(
    tmp_path,
) -> None:
    class StreamingProvider:
        def respond(self, _request):
            raise AssertionError("streaming path expected")

        def respond_stream(self, _request, *, event_sink):
            event_sink(ProviderTextDelta("not "))
            event_sink(ProviderTextDelta("durable"))
            return AssistantText("not durable")

    def fail(_turn) -> None:
        raise OSError("disk full")

    events = []
    loop = AgentLoop(
        StreamingProvider(),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=fail,
    )

    with pytest.raises(OSError, match="disk full"):
        loop.run("persist", event_sink=events.append)

    assert events == [
        AssistantResponseTextDeltaReceived("not "),
        AssistantResponseTextDeltaReceived("durable"),
    ]
    assert loop.history == ()


def test_streaming_event_sink_failure_cannot_change_execution_or_commit(tmp_path) -> None:
    class StreamingProvider:
        def respond(self, _request):
            raise AssertionError("streaming path expected")

        def respond_stream(self, _request, *, event_sink):
            event_sink(ProviderTextDelta("done"))
            return AssistantText("done")

    loop = AgentLoop(
        StreamingProvider(),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    assert (
        loop.run("inspect", event_sink=lambda _event: (_ for _ in ()).throw(OSError("closed")))
        == "done"
    )
    assert loop.history == (UserMessage("inspect"), AssistantText("done"))


def test_tool_request_during_final_text_only_invocation_cannot_extend_budget(tmp_path) -> None:
    calls = [
        ToolUse(
            f"read-{index}",
            "read_file",
            ToolArguments.from_mapping({"path": "missing.txt"}),
        )
        for index in range(1, MAX_TOOL_REQUESTS_PER_TURN + 3)
    ]
    batches = [
        AssistantToolBatch(tuple(calls[start : start + 8]))
        for start in range(0, MAX_TOOL_REQUESTS_PER_TURN, 8)
    ]
    provider = ScriptedFakeProvider([*batches, calls[-2], calls[-1]])
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    with pytest.raises(ToolLoopLimitError, match="final text-only"):
        loop.run("inspect")

    assert loop.history == ()
    assert provider.received_requests[-1].allow_tools is False


def test_prepared_tool_subset_rejects_unexposed_provider_call_before_dispatch(tmp_path) -> None:
    call = ToolUse(
        "read-hidden",
        "read_file",
        ToolArguments.from_mapping({"path": "missing.txt"}),
    )
    provider = ScriptedFakeProvider([call])
    dispatched = []
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        action_dispatcher=lambda request, _lease: dispatched.append(request),
    )
    prepared = loop.prepare_turn("inspect", enabled_tool_names=("glob",))

    with pytest.raises(ValueError, match="outside the prepared tool set"):
        loop.run_prepared(prepared)

    assert dispatched == []
    assert loop.history == ()
    assert provider.received_requests[0].enabled_tool_names == ("glob",)
    assert provider.received_requests[0].tool_definitions == prepared.tool_set_snapshot.definitions
    assert provider.received_requests[0].tool_set_id == prepared.tool_set_snapshot.snapshot_id


def test_task_control_proposal_is_terminal_and_published_only_after_turn_commit(tmp_path) -> None:
    call = ToolUse(
        "task-control-1",
        "git_show",
        ToolArguments.from_mapping({"commit_id": "HEAD", "path": "."}),
    )
    provider = ScriptedFakeProvider([call, AssistantText("proposal submitted")])
    order = []
    proposals = []
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=lambda _turn: order.append("commit"),
    )

    def dispatch(request, context_id):
        proposal = TaskControlProposal(
            kind=TaskProposalKind.COMPLETION,
            task_id=_TASK_ID,
            stage_id=_STAGE_ID,
            stage_number=1,
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            payload=ToolArguments.from_mapping({"proposed": True}),
        )
        return TaskControlDispatchResult(
            ToolDispatchResult(
                ToolResult(request.tool_use_id, '{"accepted":true}'),
                ToolEventStatus.SUCCEEDED,
                "proposal_received",
            ),
            proposal,
        )

    loop.install_task_control_dispatcher(("git_show",), dispatch)

    assert (
        loop.run(
            "finish",
            task_proposal_sink=lambda proposal: (
                order.append("proposal"),
                proposals.append(proposal),
            ),
        )
        == "proposal submitted"
    )

    assert order == ["commit", "proposal"]
    assert len(proposals) == 1
    assert proposals[0].context_id.startswith("ctx-v25-")
    assert provider.received_requests[0].allow_tools is True
    assert provider.received_requests[1].allow_tools is False
    assert provider.received_requests[1].enabled_tool_names is None


def test_task_control_proposal_is_not_published_when_turn_commit_fails(tmp_path) -> None:
    call = ToolUse(
        "task-control-1",
        "git_show",
        ToolArguments.from_mapping({"commit_id": "HEAD", "path": "."}),
    )
    provider = ScriptedFakeProvider([call, AssistantText("proposal submitted")])
    proposals = []

    def fail_commit(_turn) -> None:
        raise OSError("disk full")

    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=fail_commit,
    )

    def dispatch(request, context_id):
        return TaskControlDispatchResult(
            ToolDispatchResult(
                ToolResult(request.tool_use_id, '{"accepted":true}'),
                ToolEventStatus.SUCCEEDED,
            ),
            TaskControlProposal(
                TaskProposalKind.COMPLETION,
                _TASK_ID,
                _STAGE_ID,
                1,
                context_id,
                request.tool_use_id,
                ToolArguments.from_mapping({"proposed": True}),
            ),
        )

    loop.install_task_control_dispatcher(("git_show",), dispatch)

    with pytest.raises(OSError, match="disk full"):
        loop.run("finish", task_proposal_sink=proposals.append)

    assert proposals == []
    assert loop.history == ()


def test_task_control_call_must_be_the_only_call_in_its_response(tmp_path) -> None:
    control = ToolUse(
        "task-control-1",
        "git_show",
        ToolArguments.from_mapping({"commit_id": "HEAD", "path": "."}),
    )
    action = ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "missing.txt"}),
    )
    provider = ScriptedFakeProvider([AssistantToolBatch((control, action))])
    dispatched = []
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    loop.install_task_control_dispatcher(
        ("git_show",),
        lambda request, context_id: dispatched.append((request, context_id)),
    )

    with pytest.raises(TaskControlProtocolError, match="only call"):
        loop.run("finish", task_proposal_sink=lambda _proposal: None)

    assert dispatched == []
    assert loop.history == ()


def test_child_control_uses_normal_ledger_and_does_not_force_final(tmp_path) -> None:
    first = ToolUse(
        "child-control-1",
        "child_status",
        ToolArguments.from_mapping({"child_run_id": "42345678-1234-4234-9234-123456789abc"}),
    )
    second = ToolUse(
        "child-control-2",
        "child_status",
        ToolArguments.from_mapping({"child_run_id": "52345678-1234-4234-9234-123456789abc"}),
    )
    provider = ScriptedFakeProvider([first, second, AssistantText("both observed")])
    committed: list[CommittedTurn] = []
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        commit_turn=committed.append,
        tool_registry_factory=_registry_with_child_controls,
    )
    calls: list[str] = []

    def dispatch(request, _context_id):
        calls.append(request.tool_use_id)
        return ChildControlDispatchResult(
            ToolDispatchResult(
                ToolResult(request.tool_use_id, '{"status":"running"}'),
                ToolEventStatus.SUCCEEDED,
                "child_observed",
            )
        )

    loop.install_child_control_dispatcher(("child_status",), dispatch)
    prepared = loop.prepare_turn("observe", enabled_tool_names=("child_status",))
    assert loop.run_prepared(prepared, provider=provider) == "both observed"
    assert calls == ["child-control-1", "child-control-2"]
    assert len(provider.received_requests) == 3
    assert [entry.tool_use_id for entry in committed[0].tool_ledger.entries] == calls


def test_child_control_call_must_be_isolated(tmp_path) -> None:
    control = ToolUse(
        "child-control-1",
        "child_status",
        ToolArguments.from_mapping({"child_run_id": "42345678-1234-4234-9234-123456789abc"}),
    )
    read = ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "x"}))
    loop = AgentLoop(
        ScriptedFakeProvider([AssistantToolBatch((control, read))]),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        tool_registry_factory=_registry_with_child_controls,
    )
    loop.install_child_control_dispatcher(
        ("child_status",),
        lambda _request, _context_id: (_ for _ in ()).throw(AssertionError()),
    )
    prepared = loop.prepare_turn("observe", enabled_tool_names=("read_file", "child_status"))
    with pytest.raises(TaskControlProtocolError, match="only call"):
        loop.run_prepared(prepared, provider=loop._provider)
