from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from coquo.agent.loop import AgentLoop
from coquo.core.compaction import (
    CompactSummaryRequest,
    EffectiveContextSummary,
    build_compact_prompt,
)
from coquo.core.contracts import (
    AssistantToolBatch,
    ToolArguments,
    AssistantText,
    ConversationRequest,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.core.orchestration import ProviderFailureKind
from coquo.core.project_instructions import ProjectInstructionsLoader
from coquo.core.session_title import build_session_title_request
from coquo.providers.anthropic import (
    AnthropicConversationProvider,
    AnthropicProviderConfig,
    archive_list_tool_definition,
    checksum_file_tool_definition,
    compare_files_tool_definition,
    create_anthropic_provider,
    copy_file_tool_definition,
    delete_directory_tool_definition,
    delete_file_tool_definition,
    download_file_tool_definition,
    edit_file_tool_definition,
    glob_tool_definition,
    grep_tool_definition,
    grep_regex_tool_definition,
    git_diff_tool_definition,
    git_blame_tool_definition,
    git_log_tool_definition,
    git_show_tool_definition,
    git_refs_tool_definition,
    web_search_tool_definition,
    git_status_tool_definition,
    list_directory_tool_definition,
    list_tree_tool_definition,
    json_query_tool_definition,
    mkdir_tool_definition,
    move_file_tool_definition,
    move_directory_tool_definition,
    patch_file_tool_definition,
    normalize_sdk_error,
    parse_compact_summary_response,
    parse_response,
    parse_response_stream,
    read_file_tool_definition,
    read_file_lines_tool_definition,
    run_command_tool_definition,
    serialize_history,
    write_file_tool_definition,
    web_fetch_tool_definition,
    stat_path_tool_definition,
    task_accept_admission_tool_definition,
    task_accept_plan_tool_definition,
    task_confirm_completion_tool_definition,
    task_propose_completion_tool_definition,
    task_propose_start_tool_definition,
    task_propose_plan_tool_definition,
    task_report_blocker_tool_definition,
    task_report_reflection_tool_definition,
    tool_promote_tool_definition,
    tool_search_tool_definition,
    skill_load_tool_definition,
    skill_read_resource_tool_definition,
    skill_search_tool_definition,
    skill_propose_create_tool_definition,
    skill_accept_create_tool_definition,
)
from coquo.providers.errors import ProviderAdapterError
from coquo.providers.request_context import RequestTokenCountMethod
from coquo.providers.streaming import ProviderTextDelta
from coquo.providers.usage import ProviderTokenUsage
from coquo.system_prompt import build_system_prompt
from coquo.tools.glob import GlobTool
from coquo.tools.grep import GrepTool
from coquo.tools.list_directory import ListDirectoryTool
from coquo.tools.read_file import ReadFileTool


class RecordingModelsClient:
    def __init__(self, outcomes: list[object | Exception]) -> None:
        self.outcomes = outcomes
        self.model_ids: list[str] = []

    def retrieve(self, model_id: str, **kwargs: object) -> object:
        self.model_ids.append(model_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingMessagesClient:
    def __init__(
        self,
        outcomes: list[object | Exception],
        *,
        counts: list[object | Exception] | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.counts = counts or []
        self.requests: list[dict[str, object]] = []
        self.count_requests: list[dict[str, object]] = []

    def count_tokens(self, **kwargs: object) -> object:
        self.count_requests.append(kwargs)
        outcome = self.counts.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ClosableStream(list):
    def __init__(self, values) -> None:
        super().__init__(values)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def anthropic_event(event_type: str, **fields: object):
    return SimpleNamespace(type=event_type, **fields)


def message(
    *blocks: TextBlock | ToolUseBlock,
    stop_reason: str | None = None,
) -> Message:
    resolved_stop_reason = stop_reason
    if resolved_stop_reason is None:
        resolved_stop_reason = (
            "tool_use" if any(block.type == "tool_use" for block in blocks) else "end_turn"
        )
    return Message(
        id="msg_test",
        content=list(blocks),
        model="claude-opus-4-8",
        role="assistant",
        stop_reason=resolved_stop_reason,
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def config() -> AnthropicProviderConfig:
    return AnthropicProviderConfig(model_id="claude-opus-4-8", max_output_tokens=64)


def request(*history, allow_tools: bool = True) -> ConversationRequest:
    return ConversationRequest(
        system_prompt=build_system_prompt(),
        history=tuple(history),
        allow_tools=allow_tools,
    )


def test_text_only_count_and_create_projections_omit_tool_fields() -> None:
    client = RecordingMessagesClient(
        [message(TextBlock(text="done", type="text"))],
        counts=[SimpleNamespace(input_tokens=5)],
    )
    provider = AnthropicConversationProvider(config(), client)
    snapshot = request(UserMessage("finish"), allow_tools=False)

    provider.count_input_tokens(snapshot)
    assert provider.respond(snapshot) == AssistantText("done")

    assert "tools" not in client.count_requests[0]
    assert "tool_choice" not in client.count_requests[0]
    assert "tools" not in client.requests[0]
    assert "tool_choice" not in client.requests[0]
    assert client.count_requests[0]["messages"] == client.requests[0]["messages"]


def test_count_and_create_project_the_same_exact_tool_subset() -> None:
    client = RecordingMessagesClient(
        [message(TextBlock(text="done", type="text"))],
        counts=[SimpleNamespace(input_tokens=5)],
    )
    provider = AnthropicConversationProvider(config(), client)
    snapshot = ConversationRequest(
        system_prompt=build_system_prompt(),
        history=(UserMessage("inspect"),),
        enabled_tool_names=("grep", "git_show"),
    )

    provider.count_input_tokens(snapshot)
    assert provider.respond(snapshot) == AssistantText("done")

    assert [tool["name"] for tool in client.count_requests[0]["tools"]] == [
        "grep",
        "git_show",
    ]
    assert client.count_requests[0]["tools"] == client.requests[0]["tools"]


def test_project_instructions_use_the_same_dedicated_count_and_create_block(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use exact tests.\n", encoding="utf-8")
    instructions = ProjectInstructionsLoader(tmp_path).load()
    client = RecordingMessagesClient(
        [message(TextBlock(text="done", type="text"))],
        counts=[SimpleNamespace(input_tokens=5)],
    )
    provider = AnthropicConversationProvider(config(), client)
    snapshot = ConversationRequest(
        system_prompt=build_system_prompt(),
        history=(UserMessage("finish"),),
        project_instructions=instructions,
        allow_tools=False,
    )

    provider.count_input_tokens(snapshot)
    provider.respond(snapshot)

    counted = client.count_requests[0]["system"]
    created = client.requests[0]["system"]
    assert counted == created
    assert isinstance(created, list) and len(created) == 2
    assert created[1]["text"].endswith("Use exact tests.\n")


def test_anthropic_response_outcome_retains_actual_usage_outside_response() -> None:
    client = RecordingMessagesClient([message(TextBlock(text="done", type="text"))])
    outcome = AnthropicConversationProvider(config(), client).respond_outcome(
        request(UserMessage("hello"))
    )

    assert outcome.response == AssistantText("done")
    assert outcome.usage == ProviderTokenUsage(1, 1)


def test_anthropic_output_limit_is_typed_and_retains_nonstream_usage() -> None:
    truncated = message(TextBlock(text="partial", type="text"), stop_reason="max_tokens")
    provider = AnthropicConversationProvider(config(), RecordingMessagesClient([truncated]))

    with pytest.raises(ProviderAdapterError) as caught:
        provider.respond_outcome(request(UserMessage("hello")))

    error = caught.value
    assert error.failure.kind == ProviderFailureKind.OUTPUT_LIMIT
    assert error.failure.diagnostic_code == "output_token_limit"
    assert error.requested_output_tokens == 64
    assert error.usage == ProviderTokenUsage(1, 1)
    assert error.partial_response_observed is True


def test_anthropic_stream_normalizes_start_and_delta_usage() -> None:
    stream = ClosableStream(
        [
            anthropic_event(
                "message_start",
                message=SimpleNamespace(
                    role="assistant",
                    usage=SimpleNamespace(input_tokens=22),
                ),
            ),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text=""),
            ),
            anthropic_event(
                "content_block_delta",
                index=0,
                delta=SimpleNamespace(type="text_delta", text="done"),
            ),
            anthropic_event("content_block_stop", index=0),
            anthropic_event(
                "message_delta",
                delta=SimpleNamespace(stop_reason="end_turn"),
                usage=SimpleNamespace(output_tokens=4),
            ),
            anthropic_event("message_stop"),
        ]
    )
    client = RecordingMessagesClient([stream])
    outcome = AnthropicConversationProvider(config(), client).respond_stream_outcome(
        request(UserMessage("hello")), event_sink=lambda _event: None
    )

    assert outcome.response == AssistantText("done")
    assert outcome.usage == ProviderTokenUsage(22, 4)
    assert stream.closed is True


def test_anthropic_stream_output_limit_retains_usage_and_partial_observation() -> None:
    stream = ClosableStream(
        [
            anthropic_event(
                "message_start",
                message=SimpleNamespace(
                    role="assistant",
                    usage=SimpleNamespace(input_tokens=22),
                ),
            ),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text="partial"),
            ),
            anthropic_event("content_block_stop", index=0),
            anthropic_event(
                "message_delta",
                delta=SimpleNamespace(stop_reason="max_tokens"),
                usage=SimpleNamespace(output_tokens=64),
            ),
            anthropic_event("message_stop"),
        ]
    )
    events = []
    provider = AnthropicConversationProvider(config(), RecordingMessagesClient([stream]))

    with pytest.raises(ProviderAdapterError) as caught:
        provider.respond_stream_outcome(request(UserMessage("hello")), event_sink=events.append)

    error = caught.value
    assert error.failure.kind == ProviderFailureKind.OUTPUT_LIMIT
    assert error.usage == ProviderTokenUsage(22, 64)
    assert error.partial_response_observed is True
    assert events == [ProviderTextDelta("partial")]
    assert stream.closed is True


def test_official_token_count_uses_shared_input_projection_and_safe_fallback() -> None:
    client = RecordingMessagesClient(
        [],
        counts=[SimpleNamespace(input_tokens=321), RuntimeError("secret raw count failure")],
    )
    provider = AnthropicConversationProvider(config(), client)
    snapshot = request(UserMessage("hello"))

    exact = provider.count_input_tokens(snapshot)
    estimated = provider.count_input_tokens(snapshot)

    assert exact.input_tokens == 321
    assert exact.method == RequestTokenCountMethod.EXACT
    assert estimated.method == RequestTokenCountMethod.ESTIMATED
    assert "secret" not in (estimated.diagnostic or "")
    assert set(client.count_requests[0]) == {
        "model",
        "system",
        "messages",
        "tools",
        "tool_choice",
    }


def test_counter_accepts_empty_and_complete_committed_history_without_weakening_send() -> None:
    client = RecordingMessagesClient(
        [message(TextBlock(text="unused", type="text"))],
        counts=[SimpleNamespace(input_tokens=7), SimpleNamespace(input_tokens=9)],
    )
    provider = AnthropicConversationProvider(config(), client)

    empty = provider.count_input_tokens(request())
    complete = provider.count_input_tokens(request(UserMessage("hello"), AssistantText("reply")))

    assert empty.input_tokens == 7
    assert client.count_requests[0]["messages"] == []
    assert complete.input_tokens == 9
    assert client.count_requests[1]["messages"][-1]["role"] == "assistant"
    with pytest.raises(ProviderAdapterError, match="before an assistant response"):
        provider.respond(request(UserMessage("hello"), AssistantText("reply")))
    assert client.requests == []


def test_production_client_uses_explicit_route_and_disables_redirects(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.messages = RecordingMessagesClient([])
            self.models = RecordingModelsClient([])

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ambient-untrusted.example")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    provider = create_anthropic_provider(
        AnthropicProviderConfig(
            model_id="claude-opus-4-8",
            base_url="https://route-owned.example",
        ),
        api_key="secret",
    )

    assert isinstance(provider, AnthropicConversationProvider)
    assert captured["base_url"] == "https://route-owned.example"
    assert captured["max_retries"] == 0
    assert captured["http_client"].follow_redirects is False
    captured["http_client"].close()


def test_serializer_preserves_every_current_causal_item_and_tool_id() -> None:
    history = (
        UserMessage(text="Read the file"),
        ToolUse(
            tool_use_id="toolu_1",
            name="read_file",
            arguments=ToolArguments.from_mapping({"path": "README.md"}),
        ),
        ToolResult(tool_use_id="toolu_1", content="notes\n", is_error=False),
        AssistantText(text="Done"),
        UserMessage(text="Continue"),
    )

    assert serialize_history(history, config=config()) == [
        {"role": "user", "content": [{"type": "text", "text": "Read the file"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "notes\n",
                    "is_error": False,
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
        {"role": "user", "content": [{"type": "text", "text": "Continue"}]},
    ]


def test_serializer_projects_one_batch_and_grouped_ordered_results() -> None:
    batch = AssistantToolBatch(
        (
            ToolUse("tool-src", "mkdir", ToolArguments.from_mapping({"path": "src"})),
            ToolUse("tool-tests", "mkdir", ToolArguments.from_mapping({"path": "tests"})),
        ),
        "Creating directories.",
    )

    assert serialize_history(
        (
            UserMessage("Create them"),
            batch,
            ToolResult("tool-src", "directory_created"),
            ToolResult("tool-tests", "directory_created"),
        ),
        config=config(),
    )[1:] == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Creating directories."},
                {"type": "tool_use", "id": "tool-src", "name": "mkdir", "input": {"path": "src"}},
                {
                    "type": "tool_use",
                    "id": "tool-tests",
                    "name": "mkdir",
                    "input": {"path": "tests"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-src",
                    "content": "directory_created",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-tests",
                    "content": "directory_created",
                    "is_error": False,
                },
            ],
        },
    ]


def test_serializer_preserves_glob_operand_with_its_native_key() -> None:
    history = (
        UserMessage("Find Python"),
        ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "src/**/*.py"})),
        ToolResult("glob-1", "src/app.py\n"),
    )

    serialized = serialize_history(history, config=config())

    assert serialized[1]["content"][0] == {
        "type": "tool_use",
        "id": "glob-1",
        "name": "glob",
        "input": {"pattern": "src/**/*.py"},
    }


def test_serializer_rejects_unknown_tools_and_broken_causality() -> None:
    with pytest.raises(ProviderAdapterError) as unknown:
        serialize_history(
            (
                UserMessage(text="Search"),
                ToolUse(
                    tool_use_id="toolu_1",
                    name="search",
                    arguments=ToolArguments.from_mapping({"path": "README.md"}),
                ),
                ToolResult(tool_use_id="toolu_1", content="result"),
            ),
            config=config(),
        )
    assert unknown.value.failure.kind == ProviderFailureKind.INVALID_REQUEST

    with pytest.raises(ProviderAdapterError, match="does not match"):
        serialize_history(
            (
                UserMessage(text="Read"),
                ToolUse(
                    tool_use_id="toolu_1",
                    name="read_file",
                    arguments=ToolArguments.from_mapping({"path": "README.md"}),
                ),
                ToolResult(tool_use_id="other", content="result"),
            ),
            config=config(),
        )


def test_serializer_projects_assistant_text_before_its_atomic_tool_use() -> None:
    serialized = serialize_history(
        (
            UserMessage("Read"),
            ToolUse(
                "toolu_1",
                "read_file",
                ToolArguments.from_mapping({"path": "README.md"}),
                assistant_text="I will read it.",
            ),
            ToolResult("toolu_1", "notes"),
        ),
        config=config(),
    )

    assert serialized[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will read it."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "read_file",
                "input": {"path": "README.md"},
            },
        ],
    }


def test_read_file_schema_is_exact_and_closed() -> None:
    assert read_file_tool_definition() == {
        "name": "read_file",
        "description": (
            "Read one workspace-relative UTF-8 text file when its contents are needed to "
            "answer the user. This tool is read-only and its bounded output may be truncated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to one UTF-8 text file in the workspace.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def test_glob_schema_is_exact_and_parser_maps_pattern_to_neutral_operand() -> None:
    definition = glob_tool_definition()
    assert definition["name"] == "glob"
    assert definition["input_schema"]["required"] == ["pattern"]
    assert definition["input_schema"]["additionalProperties"] is False
    assert parse_response(
        message(
            ToolUseBlock(
                id="glob-provider",
                name="glob",
                input={"pattern": "src/**/*.py"},
                type="tool_use",
            )
        ),
        config=config(),
    ) == ToolUse("glob-provider", "glob", ToolArguments.from_mapping({"pattern": "src/**/*.py"}))


def test_grep_schema_is_exact_and_parser_preserves_two_arguments() -> None:
    definition = grep_tool_definition()
    assert definition["name"] == "grep"
    assert definition["input_schema"]["required"] == ["query", "include"]
    assert definition["input_schema"]["additionalProperties"] is False
    assert parse_response(
        message(
            ToolUseBlock(
                id="grep-provider",
                name="grep",
                input={"include": "src/**/*.py", "query": "ToolUse("},
                type="tool_use",
            )
        ),
        config=config(),
    ) == ToolUse(
        "grep-provider",
        "grep",
        ToolArguments.from_mapping({"query": "ToolUse(", "include": "src/**/*.py"}),
    )


def test_write_file_schema_is_exact_and_parser_preserves_path_and_content() -> None:
    assert write_file_tool_definition() == {
        "name": "write_file",
        "description": (
            "Write bounded UTF-8 text to one workspace-relative file. The Host detects whether "
            "the action creates or overwrites, applies permission and approval policy, rejects "
            "symlinks, and uses exact target-state conflict checks before atomic installation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative destination file path.",
                },
                "content": {
                    "type": "string",
                    "maxLength": 4096,
                    "description": "Complete UTF-8 file content, at most 4096 bytes.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    }
    assert parse_response(
        message(
            ToolUseBlock(
                id="write-provider",
                name="write_file",
                input={"content": "hello\n", "path": "notes.txt"},
                type="tool_use",
            )
        ),
        config=config(),
    ) == ToolUse(
        "write-provider",
        "write_file",
        ToolArguments.from_mapping({"path": "notes.txt", "content": "hello\n"}),
    )


def test_parser_preserves_bounded_schema_invalid_write_for_host_rejection() -> None:
    content = "x" * 4097
    block = ToolUseBlock(
        id="write-provider",
        name="write_file",
        input={"content": content, "path": "large.txt"},
        type="tool_use",
    )

    assert parse_response(message(block, stop_reason="tool_use"), config=config()) == ToolUse(
        "write-provider",
        "write_file",
        ToolArguments.from_mapping({"path": "large.txt", "content": content}),
    )


def test_edit_file_schema_is_exact_and_parser_preserves_all_arguments() -> None:
    assert edit_file_tool_definition() == {
        "name": "edit_file",
        "description": (
            "Replace one uniquely matching exact text fragment in one existing bounded UTF-8 "
            "workspace file. The Host applies overwrite permission and approval policy, rejects "
            "zero or multiple matches and symlinks, and rechecks the exact source state before "
            "atomic replacement."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of an existing text file.",
                },
                "old_text": {
                    "type": "string",
                    "description": (
                        "Non-empty exact UTF-8 text that must occur exactly once, at most "
                        "4096 bytes."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": (
                        "Exact replacement UTF-8 text, which may be empty, at most 4096 bytes."
                    ),
                },
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    }
    assert parse_response(
        message(
            ToolUseBlock(
                id="edit-provider",
                name="edit_file",
                input={"new_text": "after", "path": "notes.txt", "old_text": "before"},
                type="tool_use",
            )
        ),
        config=config(),
    ) == ToolUse(
        "edit-provider",
        "edit_file",
        ToolArguments.from_mapping(
            {"path": "notes.txt", "old_text": "before", "new_text": "after"}
        ),
    )


def test_parser_concatenates_text_and_preserves_valid_tool_use() -> None:
    assert parse_response(
        message(TextBlock(text="one", type="text"), TextBlock(text=" two", type="text")),
        config=config(),
    ) == AssistantText(text="one two")
    assert parse_response(
        message(
            ToolUseBlock(
                id="toolu_provider",
                name="read_file",
                input={"path": "README.md"},
                type="tool_use",
            )
        ),
        config=config(),
    ) == ToolUse(
        tool_use_id="toolu_provider",
        name="read_file",
        arguments=ToolArguments.from_mapping({"path": "README.md"}),
    )
    assert parse_response(
        message(
            TextBlock(text="I will inspect", type="text"),
            ToolUseBlock(
                id="toolu_mixed",
                name="read_file",
                input={"path": "README.md"},
                type="tool_use",
            ),
            TextBlock(text=" first.\n", type="text"),
        ),
        config=config(),
    ) == ToolUse(
        tool_use_id="toolu_mixed",
        name="read_file",
        arguments=ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will inspect first.\n",
    )


def test_adapter_normalizes_native_mixed_response_to_neutral_tool_use() -> None:
    client = RecordingMessagesClient(
        [
            message(
                TextBlock(text="I will inspect first.", type="text"),
                ToolUseBlock(
                    id="toolu_mixed",
                    name="read_file",
                    input={"path": "README.md"},
                    type="tool_use",
                ),
            )
        ]
    )

    assert AnthropicConversationProvider(config(), client).respond(
        request(UserMessage("Inspect"))
    ) == ToolUse(
        "toolu_mixed",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will inspect first.",
    )


def test_anthropic_stream_parser_emits_text_and_assembles_fragmented_tool_input() -> None:
    events = []
    stream = [
        anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
        anthropic_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        anthropic_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="I will inspect "),
        ),
        anthropic_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="first."),
        ),
        anthropic_event("content_block_stop", index=0),
        anthropic_event(
            "content_block_start",
            index=1,
            content_block=SimpleNamespace(
                type="tool_use", id="tool-stream", name="read_file", input={}
            ),
        ),
        anthropic_event(
            "content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"'),
        ),
        anthropic_event(
            "content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='README.md"}'),
        ),
        anthropic_event("content_block_stop", index=1),
        anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
        anthropic_event("message_stop"),
    ]

    response = parse_response_stream(stream, config=config(), event_sink=events.append)

    assert events == [ProviderTextDelta("I will inspect "), ProviderTextDelta("first.")]
    assert response == ToolUse(
        "tool-stream",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will inspect first.",
    )


def test_anthropic_stream_preserves_schema_invalid_write_for_host_rejection() -> None:
    content = "x" * 4097
    stream = [
        anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
        anthropic_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use", id="write-stream", name="write_file", input={}
            ),
        ),
        anthropic_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(
                type="input_json_delta",
                partial_json=json.dumps({"content": content, "path": "large.txt"}),
            ),
        ),
        anthropic_event("content_block_stop", index=0),
        anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
        anthropic_event("message_stop"),
    ]

    assert parse_response_stream(
        stream, config=config(), event_sink=lambda _event: None
    ) == ToolUse(
        "write-stream",
        "write_file",
        ToolArguments.from_mapping({"path": "large.txt", "content": content}),
    )


def test_anthropic_nonstream_parser_preserves_ordered_tool_batch() -> None:
    response = message(
        TextBlock(text="I will create both directories.", type="text"),
        ToolUseBlock(id="tool-src", name="mkdir", input={"path": "src"}, type="tool_use"),
        ToolUseBlock(id="tool-tests", name="mkdir", input={"path": "tests"}, type="tool_use"),
    )

    assert parse_response(response, config=config()) == AssistantToolBatch(
        (
            ToolUse("tool-src", "mkdir", ToolArguments.from_mapping({"path": "src"})),
            ToolUse("tool-tests", "mkdir", ToolArguments.from_mapping({"path": "tests"})),
        ),
        "I will create both directories.",
    )


def test_anthropic_stream_parser_assembles_multiple_tool_blocks() -> None:
    stream = [
        anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
        anthropic_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use", id="tool-src", name="mkdir", input={"path": "src"}
            ),
        ),
        anthropic_event("content_block_stop", index=0),
        anthropic_event(
            "content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tool-tests", name="mkdir", input={}),
        ),
        anthropic_event(
            "content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"tests"}'),
        ),
        anthropic_event("content_block_stop", index=1),
        anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
        anthropic_event("message_stop"),
    ]

    assert parse_response_stream(
        stream, config=config(), event_sink=lambda _event: None
    ) == AssistantToolBatch(
        (
            ToolUse("tool-src", "mkdir", ToolArguments.from_mapping({"path": "src"})),
            ToolUse("tool-tests", "mkdir", ToolArguments.from_mapping({"path": "tests"})),
        )
    )


@pytest.mark.parametrize(
    "blocks, message_text",
    [
        (
            [
                ToolUseBlock(
                    id="duplicate",
                    name="mkdir",
                    input={"path": "src"},
                    type="tool_use",
                ),
                ToolUseBlock(
                    id="duplicate",
                    name="mkdir",
                    input={"path": "tests"},
                    type="tool_use",
                ),
            ],
            "duplicated",
        ),
        (
            [
                ToolUseBlock(
                    id=f"tool-{index}",
                    name="mkdir",
                    input={"path": str(index)},
                    type="tool_use",
                )
                for index in range(9)
            ],
            "per-response",
        ),
    ],
)
def test_anthropic_nonstream_parser_rejects_invalid_batch_identity_and_bounds(
    blocks, message_text
) -> None:
    with pytest.raises(ProviderAdapterError, match=message_text):
        parse_response(message(*blocks), config=config())


@pytest.mark.parametrize(
    "tool_ids, message_text",
    [
        (["duplicate", "duplicate"], "duplicated"),
        ([f"tool-{index}" for index in range(9)], "per-response"),
    ],
)
def test_anthropic_stream_parser_rejects_invalid_batch_identity_and_bounds(
    tool_ids, message_text
) -> None:
    events = [anthropic_event("message_start", message=SimpleNamespace(role="assistant"))]
    for index, tool_id in enumerate(tool_ids):
        events.extend(
            [
                anthropic_event(
                    "content_block_start",
                    index=index,
                    content_block=SimpleNamespace(
                        type="tool_use",
                        id=tool_id,
                        name="mkdir",
                        input={"path": str(index)},
                    ),
                ),
                anthropic_event("content_block_stop", index=index),
            ]
        )
    events.extend(
        [
            anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
            anthropic_event("message_stop"),
        ]
    )

    with pytest.raises(ProviderAdapterError, match=message_text):
        parse_response_stream(events, config=config(), event_sink=lambda _event: None)


def test_anthropic_stream_request_sets_stream_and_closes_resource() -> None:
    stream = ClosableStream(
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text="Hello"),
            ),
            anthropic_event("content_block_stop", index=0),
            anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
            anthropic_event("message_stop"),
        ]
    )
    client = RecordingMessagesClient([stream])
    provider = AnthropicConversationProvider(config(), client)
    events = []

    assert provider.respond_stream(
        request(UserMessage("hello")), event_sink=events.append
    ) == AssistantText("Hello")
    assert client.requests[0]["stream"] is True
    assert stream.closed is True
    assert events == [ProviderTextDelta("Hello")]


@pytest.mark.parametrize(
    "events",
    [
        [anthropic_event("message_start", message=SimpleNamespace(role="assistant"))],
        [
            anthropic_event("message_start", message=SimpleNamespace()),
            anthropic_event("message_stop"),
        ],
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text="first"),
            ),
            anthropic_event(
                "content_block_start",
                index=1,
                content_block=SimpleNamespace(type="text", text="overlap"),
            ),
        ],
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=1,
                content_block=SimpleNamespace(type="text", text="bad index"),
            ),
        ],
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(
                    type="tool_use", id="tool-1", name="read_file", input={}
                ),
            ),
            anthropic_event(
                "content_block_delta",
                index=0,
                delta=SimpleNamespace(type="input_json_delta", partial_json="{"),
            ),
            anthropic_event("content_block_stop", index=0),
            anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
            anthropic_event("message_stop"),
        ],
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="max_tokens")),
            anthropic_event("message_stop"),
        ],
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text="\ud800"),
            ),
        ],
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(
                    type="tool_use", id="tool-1", name="read_file", input={}
                ),
            ),
            anthropic_event(
                "content_block_delta",
                index=0,
                delta=SimpleNamespace(type="input_json_delta", partial_json="x" * (64 * 1024 + 1)),
            ),
        ],
    ],
)
def test_anthropic_stream_parser_fails_closed_on_incomplete_or_malformed_events(
    events,
) -> None:
    with pytest.raises(ProviderAdapterError):
        parse_response_stream(events, config=config(), event_sink=lambda _event: None)


def test_anthropic_stream_ignores_cleanup_failure_after_success() -> None:
    class FailingCloseStream(ClosableStream):
        def close(self) -> None:
            self.closed = True
            raise OSError("cleanup failed")

    stream = FailingCloseStream(
        [
            anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
            anthropic_event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text="Hello"),
            ),
            anthropic_event("content_block_stop", index=0),
            anthropic_event("message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
            anthropic_event("message_stop"),
        ]
    )
    provider = AnthropicConversationProvider(config(), RecordingMessagesClient([stream]))

    assert provider.respond_stream(
        request(UserMessage("hello")), event_sink=lambda _event: None
    ) == AssistantText("Hello")
    assert stream.closed is True


def test_anthropic_stream_enforces_event_and_identifier_bounds(monkeypatch) -> None:
    monkeypatch.setattr("coquo.providers.anthropic.MAX_PROVIDER_STREAM_EVENTS", 1)
    with pytest.raises(ProviderAdapterError, match="too many events"):
        parse_response_stream(
            [
                anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
                anthropic_event("message_stop"),
            ],
            config=config(),
            event_sink=lambda _event: None,
        )

    monkeypatch.setattr("coquo.providers.anthropic.MAX_PROVIDER_STREAM_EVENTS", 100_000)
    oversized_name = "x" * (4 * 1024 + 1)
    with pytest.raises(ProviderAdapterError, match="tool block was too large"):
        parse_response_stream(
            [
                anthropic_event("message_start", message=SimpleNamespace(role="assistant")),
                anthropic_event(
                    "content_block_start",
                    index=0,
                    content_block=SimpleNamespace(
                        type="tool_use", id="tool-1", name=oversized_name, input={}
                    ),
                ),
            ],
            config=config(),
            event_sink=lambda _event: None,
        )


@pytest.mark.parametrize(
    "response",
    [
        message(),
        message(
            TextBlock(text="", type="text"),
            ToolUseBlock(
                id="toolu_1", name="read_file", input={"path": "README.md"}, type="tool_use"
            ),
        ),
        message(
            TextBlock(text="preface", type="text"),
            ToolUseBlock(
                id="toolu_1", name="read_file", input={"path": "README.md"}, type="tool_use"
            ),
            stop_reason="end_turn",
        ),
        message(
            TextBlock(text="x" * (32 * 1024 + 1), type="text"),
            ToolUseBlock(
                id="toolu_1", name="read_file", input={"path": "README.md"}, type="tool_use"
            ),
        ),
        message(ToolUseBlock(id="toolu_1", name="search", input={"path": "x"}, type="tool_use")),
    ],
)
def test_parser_rejects_response_shapes_the_loop_cannot_represent(response: Message) -> None:
    with pytest.raises(ProviderAdapterError) as caught:
        parse_response(response, config=config())
    assert caught.value.failure.kind == ProviderFailureKind.RESPONSE_INVALID


def test_parser_classifies_refusal_and_rejects_truncated_text() -> None:
    refused = message(TextBlock(text="I cannot help", type="text"), stop_reason="refusal")
    with pytest.raises(ProviderAdapterError) as refusal:
        parse_response(refused, config=config())
    assert refusal.value.failure.kind == ProviderFailureKind.CONTENT_REFUSAL

    truncated = message(TextBlock(text="partial", type="text"), stop_reason="max_tokens")
    with pytest.raises(ProviderAdapterError) as output_limit:
        parse_response(truncated, config=config())
    assert output_limit.value.failure.kind == ProviderFailureKind.OUTPUT_LIMIT
    assert output_limit.value.requested_output_tokens == 64
    assert output_limit.value.partial_response_observed is True


def test_adapter_sends_explicit_temperature_when_configured() -> None:
    client = RecordingMessagesClient([message(TextBlock(text="Hello", type="text"))])
    configured = AnthropicProviderConfig(
        model_id="claude-opus-4-8",
        max_output_tokens=64,
        temperature=0.2,
    )
    provider = AnthropicConversationProvider(configured, client)

    provider.respond(request(UserMessage(text="Hello")))

    assert client.requests[0]["temperature"] == 0.2


def test_adapter_sends_only_explicit_native_request_fields() -> None:
    client = RecordingMessagesClient([message(TextBlock(text="Hello", type="text"))])
    provider = AnthropicConversationProvider(config(), client)

    assert provider.respond(request(UserMessage(text="Hello"))) == AssistantText(text="Hello")
    assert client.requests == [
        {
            "model": "claude-opus-4-8",
            "max_tokens": 64,
            "system": build_system_prompt().text,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
            "tools": [
                read_file_tool_definition(),
                glob_tool_definition(),
                grep_tool_definition(),
                write_file_tool_definition(),
                edit_file_tool_definition(),
                run_command_tool_definition(),
                mkdir_tool_definition(),
                move_file_tool_definition(),
                delete_file_tool_definition(),
                delete_directory_tool_definition(),
                list_directory_tool_definition(),
                copy_file_tool_definition(),
                read_file_lines_tool_definition(),
                stat_path_tool_definition(),
                list_tree_tool_definition(),
                grep_regex_tool_definition(),
                patch_file_tool_definition(),
                git_status_tool_definition(),
                git_diff_tool_definition(),
                git_log_tool_definition(),
                git_show_tool_definition(),
                web_search_tool_definition(),
                web_fetch_tool_definition(),
                compare_files_tool_definition(),
                git_blame_tool_definition(),
                git_refs_tool_definition(),
                json_query_tool_definition(),
                checksum_file_tool_definition(),
                archive_list_tool_definition(),
                move_directory_tool_definition(),
                download_file_tool_definition(),
                tool_search_tool_definition(),
                tool_promote_tool_definition(),
                skill_search_tool_definition(),
                skill_load_tool_definition(),
                skill_read_resource_tool_definition(),
                task_propose_plan_tool_definition(),
                task_report_reflection_tool_definition(),
                task_report_blocker_tool_definition(),
                task_propose_completion_tool_definition(),
                task_propose_start_tool_definition(),
                task_accept_admission_tool_definition(),
                task_accept_plan_tool_definition(),
                task_confirm_completion_tool_definition(),
                skill_propose_create_tool_definition(),
                skill_accept_create_tool_definition(),
            ],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "stream": False,
        }
    ]


def compact_request() -> CompactSummaryRequest:
    return CompactSummaryRequest(build_compact_prompt(), '{"turns":[]}', 32)


def test_compact_summary_count_and_create_omit_tools_and_parse_text_only() -> None:
    client = RecordingMessagesClient(
        [message(TextBlock(text=" summary ", type="text"))],
        counts=[SimpleNamespace(input_tokens=12)],
    )
    provider = AnthropicConversationProvider(config(), client)

    counted = provider.count_compact_summary_input_tokens(compact_request())
    result = provider.summarize_compact(compact_request())

    assert counted.input_tokens == 12
    assert set(client.count_requests[0]) == {"model", "system", "messages"}
    assert result == AssistantText("summary")
    assert "tools" not in client.requests[0]
    assert client.requests[0]["max_tokens"] == 32


def test_session_title_count_and_create_use_no_tools_and_512_token_reserve() -> None:
    client = RecordingMessagesClient(
        [message(TextBlock(text=" Adapter review ", type="text"))],
        counts=[SimpleNamespace(input_tokens=9)],
    )
    provider = AnthropicConversationProvider(config(), client)
    title_request = build_session_title_request("Review adapters")

    counted = provider.count_session_title_input_tokens(title_request)
    outcome = provider.generate_session_title_outcome(title_request)

    assert counted.input_tokens == 9
    assert set(client.count_requests[0]) == {"model", "system", "messages"}
    assert "tools" not in client.requests[0]
    assert client.requests[0]["max_tokens"] == 512
    assert outcome.response == AssistantText("Adapter review")
    assert outcome.usage == ProviderTokenUsage(1, 1)


def test_session_title_adapter_rejects_tool_response() -> None:
    client = RecordingMessagesClient(
        [
            message(
                ToolUseBlock(
                    id="toolu_1",
                    name="read_file",
                    input={"path": "README.md"},
                    type="tool_use",
                )
            )
        ]
    )
    provider = AnthropicConversationProvider(config(), client)

    with pytest.raises(ProviderAdapterError, match="unsupported stop reason"):
        provider.generate_session_title_outcome(build_session_title_request("Review adapters"))


def test_compact_summary_parser_rejects_tools_refusal_and_truncation() -> None:
    with pytest.raises(ProviderAdapterError):
        parse_compact_summary_response(
            message(
                ToolUseBlock(
                    id="toolu_1",
                    name="read_file",
                    input={"path": "README.md"},
                    type="tool_use",
                )
            ),
            config=config(),
        )
    for stop_reason in ("refusal", "max_tokens"):
        with pytest.raises(ProviderAdapterError):
            parse_compact_summary_response(
                message(TextBlock(text="partial", type="text"), stop_reason=stop_reason),
                config=config(),
            )


def test_effective_summary_is_projected_before_retained_history() -> None:
    summary = EffectiveContextSummary("old state")
    snapshot = ConversationRequest(
        build_system_prompt(),
        (UserMessage("recent"),),
        effective_summary=summary,
    )
    client = RecordingMessagesClient([message(TextBlock(text="done", type="text"))])

    AnthropicConversationProvider(config(), client).respond(snapshot)

    messages = client.requests[0]["messages"]
    assert messages[0]["content"][0]["text"] == summary.user_text
    assert messages[1]["content"][0]["text"] == summary.assistant_acknowledgement
    assert messages[2]["content"][0]["text"] == "recent"


def test_anthropic_models_discovery_is_exact_and_safe() -> None:
    models = RecordingModelsClient(
        [
            SimpleNamespace(
                id="claude-opus-4-8",
                max_input_tokens=1_000_000,
                max_tokens=128_000,
            )
        ]
    )
    provider = AnthropicConversationProvider(
        config(), RecordingMessagesClient([]), models_client=models
    )

    discovered = provider.discover_model_context()

    assert discovered.context_window_tokens == 1_000_000
    assert discovered.model_max_output_tokens == 128_000
    assert discovered.diagnostic is None
    assert models.model_ids == ["claude-opus-4-8"]

    mismatched = AnthropicConversationProvider(
        config(),
        RecordingMessagesClient([]),
        models_client=RecordingModelsClient(
            [SimpleNamespace(id="other", max_input_tokens=1_000_000)]
        ),
    ).discover_model_context()
    assert mismatched.context_window_tokens is None
    assert "different model ID" in mismatched.diagnostic

    missing = AnthropicConversationProvider(
        config(),
        RecordingMessagesClient([]),
        models_client=RecordingModelsClient(
            [SimpleNamespace(id="claude-opus-4-8", max_input_tokens=None)]
        ),
    ).discover_model_context()
    assert missing.context_window_tokens is None
    assert "incomplete limit set" in missing.diagnostic


@dataclass
class ErrorCase:
    error: anthropic.APIError
    kind: ProviderFailureKind
    retryable: bool


def status_error(
    error_type: type[anthropic.APIStatusError], status: int, *, retry_after: str | None = None
) -> anthropic.APIStatusError:
    headers = {"request-id": "req_safe"}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    response = httpx.Response(
        status,
        headers=headers,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return error_type("raw provider body sk-ant-secret", response=response, body={"secret": "x"})


@pytest.mark.parametrize(
    "case",
    [
        ErrorCase(
            status_error(anthropic.AuthenticationError, 401),
            ProviderFailureKind.AUTHENTICATION,
            False,
        ),
        ErrorCase(
            status_error(anthropic.PermissionDeniedError, 403),
            ProviderFailureKind.AUTHORIZATION,
            False,
        ),
        ErrorCase(
            status_error(anthropic.BadRequestError, 400), ProviderFailureKind.INVALID_REQUEST, False
        ),
        ErrorCase(
            status_error(anthropic.NotFoundError, 404), ProviderFailureKind.MODEL_UNAVAILABLE, False
        ),
        ErrorCase(
            status_error(anthropic.RateLimitError, 429, retry_after="3"),
            ProviderFailureKind.RATE_LIMITED,
            True,
        ),
        ErrorCase(
            status_error(anthropic.InternalServerError, 503),
            ProviderFailureKind.PROVIDER_UNAVAILABLE,
            True,
        ),
        ErrorCase(
            anthropic.APIResponseValidationError(
                httpx.Response(
                    200,
                    request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                ),
                {"secret": "sk-ant-secret"},
                message="raw invalid response",
            ),
            ProviderFailureKind.RESPONSE_INVALID,
            False,
        ),
        ErrorCase(
            anthropic.APITimeoutError(httpx.Request("POST", "https://api.anthropic.com")),
            ProviderFailureKind.TIMEOUT,
            True,
        ),
        ErrorCase(
            anthropic.APIConnectionError(
                message="raw secret sk-ant-secret",
                request=httpx.Request("POST", "https://api.anthropic.com"),
            ),
            ProviderFailureKind.TRANSPORT,
            True,
        ),
    ],
)
def test_sdk_errors_are_safely_normalized(case: ErrorCase) -> None:
    normalized = normalize_sdk_error(case.error, config=config())

    assert normalized.failure.kind == case.kind
    assert normalized.failure.retryable is case.retryable
    assert "sk-ant-secret" not in normalized.failure.message
    assert "raw provider body" not in normalized.failure.message
    if case.kind == ProviderFailureKind.RATE_LIMITED:
        assert normalized.failure.retry_after_seconds == 3
        assert normalized.failure.request_id == "req_safe"


def test_adapter_backed_loop_preserves_atomic_commit_after_failure(tmp_path) -> None:
    (tmp_path / "README.md").write_text("workspace notes\n", encoding="utf-8")
    failure = status_error(anthropic.InternalServerError, 503)
    client = RecordingMessagesClient(
        [
            message(
                TextBlock(text="I will read it first.", type="text"),
                ToolUseBlock(
                    id="toolu_read",
                    name="read_file",
                    input={"path": "README.md"},
                    type="tool_use",
                ),
            ),
            failure,
        ]
    )
    loop = AgentLoop(
        AnthropicConversationProvider(config(), client),
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )

    with pytest.raises(ProviderAdapterError):
        loop.run("Read README")

    assert loop.history == ()
    assert loop.turns == ()
    assert client.requests[1]["messages"][-2] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will read it first."},
            {
                "type": "tool_use",
                "id": "toolu_read",
                "name": "read_file",
                "input": {"path": "README.md"},
            },
        ],
    }
    assert client.requests[1]["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_read",
                "content": "workspace notes\n",
                "is_error": False,
            }
        ],
    }


def test_run_command_schema_and_parser_preserve_array_and_integer_arguments() -> None:
    definition = run_command_tool_definition()
    assert definition["name"] == "run_command"
    assert definition["input_schema"]["required"] == ["argv", "cwd", "timeout_seconds"]
    block = ToolUseBlock(
        id="command-provider",
        name="run_command",
        input={"argv": ["uv", "run", "pytest"], "cwd": ".", "timeout_seconds": 60},
        type="tool_use",
    )
    assert parse_response(message(block, stop_reason="tool_use"), config=config()) == ToolUse(
        "command-provider",
        "run_command",
        ToolArguments.from_mapping(
            {"argv": ["uv", "run", "pytest"], "cwd": ".", "timeout_seconds": 60}
        ),
    )


def test_mkdir_schema_and_parser_preserve_exact_path_argument() -> None:
    definition = mkdir_tool_definition()
    assert definition["name"] == "mkdir"
    assert definition["input_schema"]["required"] == ["path"]
    assert definition["input_schema"]["additionalProperties"] is False
    block = ToolUseBlock(
        id="mkdir-provider",
        name="mkdir",
        input={"path": "src/pkg"},
        type="tool_use",
    )
    assert parse_response(message(block, stop_reason="tool_use"), config=config()) == ToolUse(
        "mkdir-provider",
        "mkdir",
        ToolArguments.from_mapping({"path": "src/pkg"}),
    )


def test_move_file_tool_definition_is_canonical_and_closed() -> None:
    definition = move_file_tool_definition()
    assert definition["name"] == "move_file"
    assert definition["input_schema"]["required"] == ["source", "destination"]
    assert definition["input_schema"]["additionalProperties"] is False


def test_delete_file_tool_definition_is_canonical_and_closed() -> None:
    definition = delete_file_tool_definition()
    assert definition["name"] == "delete_file"
    assert definition["input_schema"]["required"] == ["path"]
    assert definition["input_schema"]["additionalProperties"] is False


def test_delete_directory_tool_definition_is_canonical_and_closed() -> None:
    definition = delete_directory_tool_definition()
    assert definition["name"] == "delete_directory"
    assert definition["input_schema"]["required"] == ["path"]
    assert definition["input_schema"]["additionalProperties"] is False


def test_list_directory_schema_and_parser_preserve_exact_path_argument() -> None:
    definition = list_directory_tool_definition()
    assert definition["name"] == "list_directory"
    assert definition["input_schema"]["required"] == ["path"]
    assert definition["input_schema"]["additionalProperties"] is False
    block = ToolUseBlock(
        id="list-provider",
        name="list_directory",
        input={"path": "."},
        type="tool_use",
    )
    assert parse_response(message(block, stop_reason="tool_use"), config=config()) == ToolUse(
        "list-provider",
        "list_directory",
        ToolArguments.from_mapping({"path": "."}),
    )


def test_copy_file_schema_and_parser_preserve_exact_paths() -> None:
    definition = copy_file_tool_definition()
    assert definition["name"] == "copy_file"
    assert definition["input_schema"]["required"] == ["source", "destination"]
    assert definition["input_schema"]["additionalProperties"] is False
    block = ToolUseBlock(
        id="copy-provider",
        name="copy_file",
        input={"source": "src/a.bin", "destination": "dst/b.bin"},
        type="tool_use",
    )
    assert parse_response(message(block, stop_reason="tool_use"), config=config()) == ToolUse(
        "copy-provider",
        "copy_file",
        ToolArguments.from_mapping({"source": "src/a.bin", "destination": "dst/b.bin"}),
    )


@pytest.mark.parametrize(
    ("definition_factory", "name", "tool_input"),
    [
        (
            read_file_lines_tool_definition,
            "read_file_lines",
            {"path": "src/app.py", "start_line": 20, "line_count": 10},
        ),
        (stat_path_tool_definition, "stat_path", {"path": "."}),
        (list_tree_tool_definition, "list_tree", {"path": "src", "max_depth": 3}),
        (
            grep_regex_tool_definition,
            "grep_regex",
            {"pattern": r"test_\d+", "include": "**/*.py"},
        ),
        (
            patch_file_tool_definition,
            "patch_file",
            {
                "path": "src/app.py",
                "edits": [{"old_text": "before", "new_text": "after"}],
            },
        ),
        (git_status_tool_definition, "git_status", {}),
        (
            git_diff_tool_definition,
            "git_diff",
            {"scope": "unstaged", "path": "."},
        ),
        (git_log_tool_definition, "git_log", {"limit": 10, "path": "."}),
        (
            git_show_tool_definition,
            "git_show",
            {"commit_id": "a" * 40, "path": "src/app.py"},
        ),
        (
            web_search_tool_definition,
            "web_search",
            {"query": "Python documentation", "max_results": 5},
        ),
        (
            web_fetch_tool_definition,
            "web_fetch",
            {"url": "https://example.com/docs", "format": "markdown"},
        ),
        (
            compare_files_tool_definition,
            "compare_files",
            {"left": "a.txt", "right": "b.txt"},
        ),
        (
            git_blame_tool_definition,
            "git_blame",
            {"path": "src/app.py", "start_line": 1, "line_count": 20},
        ),
        (git_refs_tool_definition, "git_refs", {}),
        (
            json_query_tool_definition,
            "json_query",
            {"path": "data.json", "pointer": "/items/0"},
        ),
        (checksum_file_tool_definition, "checksum_file", {"path": "artifact.bin"}),
        (archive_list_tool_definition, "archive_list", {"path": "bundle.zip"}),
        (
            move_directory_tool_definition,
            "move_directory",
            {"source": "old", "destination": "new"},
        ),
        (
            download_file_tool_definition,
            "download_file",
            {"url": "https://example.com/file.bin", "path": "file.bin"},
        ),
        (
            task_accept_admission_tool_definition,
            "task_accept_admission",
            {"admission_id": "tap-v1-" + "a" * 64},
        ),
        (
            task_accept_plan_tool_definition,
            "task_accept_plan",
            {"task_id": "12345678-1234-4234-9234-123456789abc"},
        ),
        (
            task_confirm_completion_tool_definition,
            "task_confirm_completion",
            {"task_id": "12345678-1234-4234-9234-123456789abc"},
        ),
        (
            skill_propose_create_tool_definition,
            "skill_propose_create",
            {
                "allowed_tools": ["read_file"],
                "description": "Reusable workflow",
                "instructions": "Inspect and verify.",
                "name": "reusable-workflow",
                "scope": "project",
            },
        ),
        (
            skill_accept_create_tool_definition,
            "skill_accept_create",
            {"candidate_id": "skc-v1-" + "a" * 64},
        ),
    ],
)
def test_new_tool_schemas_and_parser_preserve_structured_arguments(
    definition_factory,
    name: str,
    tool_input: dict[str, object],
) -> None:
    definition = definition_factory()
    assert definition["name"] == name
    assert definition["input_schema"]["additionalProperties"] is False
    block = ToolUseBlock(
        id="new-provider",
        name=name,
        input=tool_input,
        type="tool_use",
    )
    assert parse_response(message(block, stop_reason="tool_use"), config=config()) == ToolUse(
        "new-provider",
        name,
        ToolArguments.from_mapping(tool_input),
    )
