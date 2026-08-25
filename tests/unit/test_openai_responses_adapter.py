from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import ANY

import openai
import pytest

from coquo.core.compaction import (
    CompactSummaryRequest,
    EffectiveContextSummary,
    build_compact_prompt,
)
from coquo.core.contracts import (
    AssistantText,
    AssistantToolBatch,
    ConversationRequest,
    MemoryEvidence,
    ProviderOwnedItem,
    ProviderResponseEnvelope,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.core.orchestration import ProviderFailureKind
from coquo.providers.errors import ProviderAdapterError
from coquo.providers.definitions import ReasoningEffort, ReasoningProfile
from coquo.providers.native_search import (
    NativeSearchContextSize,
    NativeSearchMode,
    NativeSearchRuntimeOptions,
)
from coquo.providers.openai_responses import (
    OpenAIResponsesConversationProvider,
    build_input_projection,
    build_request,
    parse_response,
    parse_response_stream,
    create_openai_responses_provider,
    serialize_history,
)
from coquo.providers.resolver import resolve_runtime_route
from coquo.providers.streaming import (
    ProviderSearchActivity,
    ProviderSearchObservation,
    ProviderSearchPhase,
    ProviderTextDelta,
)
from coquo.providers.usage import ProviderTokenUsage
from coquo.system_prompt import build_system_prompt


class RecordingResponsesClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        return self.outcomes.pop(0)


def route():
    return resolve_runtime_route("deepseek/deepseek-v4-flash", environment={})


def request(*history, allow_tools: bool = True) -> ConversationRequest:
    return ConversationRequest(
        build_system_prompt(),
        tuple(history),
        allow_tools=allow_tools,
        enabled_tool_names=("read_file",) if allow_tools else None,
    )


def message(text: str, *, annotations: list[dict[str, object]] | None = None):
    return {
        "type": "message",
        "id": "msg_1",
        "status": "completed",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": annotations or [],
            }
        ],
    }


def response(*output: dict[str, object], status: str = "completed"):
    return {
        "id": "resp_1",
        "object": "response",
        "status": status,
        "output": list(output),
        "usage": {"input_tokens": 12, "output_tokens": 4},
        "incomplete_details": None,
    }


def test_projection_combines_host_functions_with_provider_web_search() -> None:
    snapshot = request(UserMessage("search and inspect"))

    counted = build_input_projection(route(), snapshot)
    created = build_request(route(), snapshot)

    assert counted["input"] == [{"role": "user", "content": "search and inspect"}]
    assert counted["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": ANY,
            "parameters": ANY,
            "strict": False,
        },
        {"type": "web_search"},
    ]
    assert counted["tools"] == created["tools"]
    assert created["store"] is False
    assert created["max_output_tokens"] == route().max_output_tokens
    assert "parallel_tool_calls" not in created


def test_request_preserves_max_reasoning_effort_without_profile_mapping() -> None:
    effort_route = route()
    effort_route = replace(effort_route, reasoning_effort=ReasoningEffort.MAX)

    created = build_request(effort_route, request(UserMessage("reason")))

    assert created["reasoning"] == {"effort": "max"}


def test_request_maps_host_effort_through_profile_native_name() -> None:
    effort_route = replace(
        route(),
        reasoning_effort=ReasoningEffort.XHIGH,
        reasoning_profile=ReasoningProfile.from_mapping(
            {
                "native_kind": "effort",
                "native_levels": ["standard", "ultra"],
                "mapping": {"xhigh": "ultra"},
            }
        ),
    )

    created = build_request(effort_route, request(UserMessage("reason")))

    assert created["reasoning"] == {"effort": "ultra"}


def test_responses_effort_matrix_uses_profile_mapping_and_rejects_gaps() -> None:
    levels = tuple(effort.value for effort in ReasoningEffort)
    profile = ReasoningProfile.from_mapping(
        {
            "native_kind": "effort",
            "native_levels": [f"wire-{level}" for level in levels],
            "mapping": {level: f"wire-{level}" for level in levels},
        }
    )
    mapped = replace(
        route(),
        reasoning_effort=ReasoningEffort.XHIGH,
        reasoning_profile=profile,
    )
    assert build_request(mapped, request(UserMessage("reason")))["reasoning"] == {
        "effort": "wire-xhigh"
    }

    unmapped = replace(
        mapped,
        reasoning_effort=ReasoningEffort.LOW,
        reasoning_profile=ReasoningProfile.from_mapping(
            {
                "native_kind": "effort",
                "native_levels": ["wire-high"],
                "mapping": {"high": "wire-high"},
            }
        ),
    )
    with pytest.raises(ValueError, match="not mapped"):
        build_request(unmapped, request(UserMessage("reason")))


def test_text_only_projection_omits_all_tools_including_native_search() -> None:
    projection = build_input_projection(route(), request(UserMessage("title"), allow_tools=False))

    assert "tools" not in projection
    assert "tool_choice" not in projection


def test_memory_evidence_projection_is_exact_ordered_and_count_create_equivalent() -> None:
    summary = EffectiveContextSummary("old state")
    evidence = (
        MemoryEvidence("memory-1", "workspace", "first fact", "fact", 0.8),
        MemoryEvidence("memory-2", "task", "second fact", "policy", 0.9),
    )
    snapshot = ConversationRequest(
        build_system_prompt(),
        (UserMessage("recent"),),
        effective_summary=summary,
        allow_tools=False,
        memory_evidence=evidence,
    )

    counted = build_input_projection(route(), snapshot)
    created = build_request(route(), snapshot)

    assert counted["input"] == created["input"]
    assert [item["role"] for item in counted["input"]] == [
        "user",
        "assistant",
        "user",
        "user",
        "user",
    ]
    assert counted["input"][0]["content"] == summary.user_text
    assert counted["input"][2]["content"] == evidence[0].rendered
    assert counted["input"][3]["content"] == evidence[1].rendered
    assert counted["input"][4]["content"] == "recent"


def test_native_search_projection_applies_required_domains_and_context() -> None:
    projection = build_input_projection(
        route(),
        request(UserMessage("official sources")),
        native_search_options=NativeSearchRuntimeOptions(
            mode=NativeSearchMode.REQUIRED,
            allowed_domains=("openai.com", "platform.openai.com"),
            context_size=NativeSearchContextSize.HIGH,
        ),
    )

    assert projection["tool_choice"] == {"type": "web_search"}
    assert projection["tools"][-1] == {
        "type": "web_search",
        "filters": {"allowed_domains": ["openai.com", "platform.openai.com"]},
        "search_context_size": "high",
    }


def test_provider_owned_search_and_reasoning_round_trip_unchanged_in_history() -> None:
    reasoning = ProviderOwnedItem.from_mapping(
        {"type": "reasoning", "id": "rs_1", "status": "completed", "summary": []}
    )
    search = ProviderOwnedItem.from_mapping(
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "Coquo"},
        }
    )

    projected = serialize_history(
        (UserMessage("search"), reasoning, search, AssistantText("done")),
        route=route(),
        committed_context=True,
    )

    assert projected[1] == reasoning.as_mapping()
    assert projected[2] == search.as_mapping()
    assert projected[3] == {"role": "assistant", "content": "done"}


def test_function_call_and_output_use_responses_call_id_pairing() -> None:
    parsed = parse_response(
        response(
            {
                "type": "function_call",
                "id": "fc_1",
                "status": "completed",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
            }
        ),
        route=route(),
    )

    assert isinstance(parsed, ToolUse)
    projected = serialize_history(
        (
            UserMessage("read"),
            parsed,
            ToolResult("call_1", "notes"),
            AssistantText("done"),
        ),
        route=route(),
        committed_context=True,
    )
    assert projected[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_file",
        "arguments": '{"path":"README.md"}',
    }
    assert projected[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "notes",
    }


def test_response_normalizes_search_history_citations_and_final_text() -> None:
    raw_search = {
        "type": "web_search_call",
        "id": "ws_1",
        "status": "completed",
        "action": {"type": "search", "query": "current Python"},
    }
    parsed = parse_response(
        response(
            raw_search,
            message(
                "Python result",
                annotations=[
                    {
                        "type": "url_citation",
                        "url": "https://docs.python.org/3/",
                        "title": "Python docs",
                    }
                ],
            ),
        ),
        route=route(),
    )

    assert isinstance(parsed, ProviderResponseEnvelope)
    assert parsed.provider_items[0].as_mapping() == raw_search
    assert parsed.response == AssistantText(
        "Python result\n\nSources:\n- [Python docs](https://docs.python.org/3/)"
    )


def test_response_accepts_failed_search_item_when_terminal_response_completed() -> None:
    failed_search = {
        "type": "web_search_call",
        "id": "ws_failed",
        "status": "failed",
        "action": {"type": "open_page", "url": "https://example.com/docs"},
        "error": {"code": "SSRF_BLOCKED"},
    }

    parsed = parse_response(
        response(failed_search, message("Search was partially available.")),
        route=route(),
    )

    assert isinstance(parsed, ProviderResponseEnvelope)
    assert parsed.provider_items[0].as_mapping() == failed_search
    assert parsed.response == AssistantText("Search was partially available.")


def test_response_tolerates_null_and_nested_compatible_citations() -> None:
    null_annotations = message("First.")
    null_annotations["content"][0]["annotations"] = None
    nested_annotations = message("Second.")
    nested_annotations["id"] = "msg_2"
    nested_annotations["content"][0]["annotations"] = {
        "type": "url_citation",
        "url_citation": {
            "url": "https://example.com/reference",
            "title": "Reference",
        },
    }

    parsed = parse_response(
        response(null_annotations, nested_annotations),
        route=route(),
    )

    assert parsed == AssistantText(
        "First.\nSecond.\n\nSources:\n- [Reference](https://example.com/reference)"
    )


def test_response_discards_malformed_optional_citations_without_losing_text() -> None:
    malformed = message("Still valid.")
    malformed["content"][0]["annotations"] = [
        "not-an-object",
        {
            "type": "url_citation",
            "url": "https://user:password@example.com/private",
            "title": "Unsafe credentials",
        },
        {
            "type": "url_citation",
            "url": "https://example.com/\nspoofed",
            "title": "Unsafe control",
        },
    ]

    assert parse_response(response(malformed), route=route()) == AssistantText("Still valid.")


def test_multiple_function_calls_form_one_host_batch_with_companion_text() -> None:
    parsed = parse_response(
        response(
            message("I will inspect both."),
            {
                "type": "function_call",
                "id": "fc_1",
                "status": "completed",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": '{"path":"a.txt"}',
            },
            {
                "type": "function_call",
                "id": "fc_2",
                "status": "completed",
                "call_id": "call_2",
                "name": "read_file",
                "arguments": '{"path":"b.txt"}',
            },
        ),
        route=route(),
    )

    assert isinstance(parsed, AssistantToolBatch)
    assert parsed.assistant_text == "I will inspect both."
    assert [call.tool_use_id for call in parsed.tool_uses] == ["call_1", "call_2"]


def test_incomplete_max_output_is_typed_and_retains_usage() -> None:
    raw = response(message("partial"), status="incomplete")
    raw["incomplete_details"] = {"reason": "max_output_tokens"}

    with pytest.raises(ProviderAdapterError) as caught:
        parse_response(
            raw,
            route=route(),
            usage=ProviderTokenUsage(12, 64),
        )

    assert caught.value.failure.kind == ProviderFailureKind.OUTPUT_LIMIT
    assert caught.value.usage == ProviderTokenUsage(12, 64)
    assert caught.value.partial_response_observed is True


def test_unknown_hosted_output_item_fails_closed() -> None:
    with pytest.raises(ProviderAdapterError, match="unsupported") as caught:
        parse_response(
            response({"type": "code_interpreter_call", "id": "ci_1"}),
            route=route(),
        )

    assert caught.value.failure.kind == ProviderFailureKind.RESPONSE_INVALID


def test_stream_uses_terminal_response_as_truth_and_emits_citation_suffix() -> None:
    final = response(
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "Python"},
        },
        message(
            "done",
            annotations=[
                {
                    "type": "url_citation",
                    "url": "https://example.com/result",
                    "title": "Result",
                }
            ],
        ),
    )
    events = [
        SimpleNamespace(type="response.created", sequence_number=0),
        SimpleNamespace(type="response.web_search_call.searching", sequence_number=1),
        SimpleNamespace(type="response.web_search_call.completed", sequence_number=2),
        SimpleNamespace(type="response.output_text.delta", sequence_number=3, delta="done"),
        SimpleNamespace(type="response.completed", sequence_number=4, response=final),
    ]
    deltas: list[ProviderTextDelta | ProviderSearchActivity] = []

    parsed, usage, streamed, observation = parse_response_stream(
        events,
        route=route(),
        event_sink=deltas.append,
    )

    assert isinstance(parsed, ProviderResponseEnvelope)
    assert usage == ProviderTokenUsage(12, 4)
    assert streamed is True
    assert observation == ProviderSearchObservation(1, 0, ("search",), 0, 1)
    assert deltas[:2] == [
        ProviderSearchActivity(ProviderSearchPhase.SEARCHING),
        ProviderSearchActivity(ProviderSearchPhase.COMPLETED),
    ]
    assert "".join(delta.text for delta in deltas if isinstance(delta, ProviderTextDelta)) == (
        parsed.response.text
    )


def test_provider_invocation_reports_usage_and_exact_request() -> None:
    client = RecordingResponsesClient([response(message("done"))])
    provider = OpenAIResponsesConversationProvider(route(), client)

    outcome = provider.respond_outcome(request(UserMessage("hello")))

    assert outcome.response == AssistantText("done")
    assert outcome.usage == ProviderTokenUsage(12, 4)
    assert client.requests[0]["model"] == "deepseek-v4-flash"
    assert client.requests[0]["tools"][-1] == {"type": "web_search"}


def test_compaction_accepts_reasoning_but_does_not_return_it_as_history() -> None:
    raw_reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "status": "completed",
        "content": [{"type": "reasoning_text", "text": "private reasoning"}],
        "summary": [],
    }
    client = RecordingResponsesClient([response(raw_reasoning, message("short summary"))])
    provider = OpenAIResponsesConversationProvider(route(), client)

    outcome = provider.summarize_compact_outcome(
        CompactSummaryRequest(build_compact_prompt(), "long source", 128)
    )

    assert outcome.response == AssistantText("short summary")
    assert "tools" not in client.requests[0]
    assert client.requests[0]["instructions"] == build_compact_prompt().text


def test_production_client_uses_responses_resource_and_disables_redirects(monkeypatch) -> None:
    captured = {}
    responses = RecordingResponsesClient([])

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.responses = responses

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(openai, "OpenAI", FakeClient)

    provider = create_openai_responses_provider(route(), api_key="secret")

    assert isinstance(provider, OpenAIResponsesConversationProvider)
    assert captured["base_url"] == "https://api.deepseek.com/v1"
    assert captured["max_retries"] == 0
    assert captured["http_client"].follow_redirects is False
    provider.close()
    assert captured["closed"] is True
