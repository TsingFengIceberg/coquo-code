"""OpenAI Responses adapter with stateless history and provider-owned search replay."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import openai

from coquo.core.compaction import CompactSummaryRequest
from coquo.core.contracts import (
    AssistantText,
    AssistantToolBatch,
    ConversationItem,
    ConversationRequest,
    ProviderOwnedItem,
    ProviderResponse,
    ProviderResponseEnvelope,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.orchestration import ProviderFailureKind
from coquo.core.project_instructions import render_project_instructions
from coquo.core.session_title import SessionTitleRequest
from coquo.providers.definitions import RuntimeProviderRoute
from coquo.providers.errors import adapter_error, output_limit_error
from coquo.providers.native_search import (
    NativeSearchAdapterId,
    NativeSearchMode,
    NativeSearchRuntimeOptions,
    validate_native_search_runtime_options,
)
from coquo.providers.openai_compat import fixed_sampling_model, normalize_sdk_error
from coquo.providers.request_context import (
    RequestTokenCount,
    estimate_serialized_input_tokens,
)
from coquo.providers.streaming import (
    MAX_PROVIDER_STREAM_EVENTS,
    ProviderResponseOutcome,
    ProviderSearchActivity,
    ProviderSearchObservation,
    ProviderSearchPhase,
    ProviderTextDelta,
    ProviderTextDeltaSink,
)
from coquo.providers.usage import ProviderTokenUsage, parse_provider_usage
from coquo.tools.catalog import (
    MAX_TOOL_CALLS_PER_RESPONSE,
    model_tool_definitions,
    tool_input_for_provider_history,
    tool_use_from_provider_input,
)


class ResponsesClient(Protocol):
    def create(self, **kwargs: object) -> object:
        """Create one synchronous Responses API invocation or stream."""


class OpenAIResponsesConversationProvider:
    """Project neutral full history through a stateless Responses endpoint."""

    def __init__(
        self,
        route: RuntimeProviderRoute,
        client: ResponsesClient,
        *,
        owner: object | None = None,
    ) -> None:
        self._route = route
        self._client = client
        self._owner = owner
        self._native_search_enabled = route.native_search.default_enabled
        self._native_search_options = NativeSearchRuntimeOptions()

    def set_native_search_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("provider native-search state must be boolean")
        if enabled and not self._route.native_search.available:
            raise ValueError("provider native search is unavailable")
        self._native_search_enabled = enabled

    def set_native_search_options(self, options: NativeSearchRuntimeOptions) -> None:
        if type(options) is not NativeSearchRuntimeOptions:
            raise ValueError("provider native-search options are invalid")
        validate_native_search_runtime_options(self._route.native_search, options)
        self._native_search_options = options

    def close(self) -> None:
        close = getattr(self._owner, "close", None)
        if callable(close):
            close()

    def count_input_tokens(self, request_snapshot: ConversationRequest) -> RequestTokenCount:
        return estimate_serialized_input_tokens(
            build_input_projection(
                self._route,
                request_snapshot,
                committed_context=True,
                native_search_enabled=self._native_search_enabled,
                native_search_options=self._native_search_options,
            )
        )

    def count_compact_summary_input_tokens(
        self, request_snapshot: CompactSummaryRequest
    ) -> RequestTokenCount:
        return estimate_serialized_input_tokens(
            build_compact_summary_input_projection(self._route, request_snapshot)
        )

    def summarize_compact_outcome(
        self, request_snapshot: CompactSummaryRequest
    ) -> ProviderResponseOutcome:
        request = build_compact_summary_request(self._route, request_snapshot)
        response = self._create(request)
        usage = _parse_usage(response)
        result = parse_response(
            response,
            route=self._route,
            requested_output_tokens=request_snapshot.max_output_tokens,
            usage=usage,
            allow_tools=False,
        )
        text = _standalone_text_response(result, self._route)
        return ProviderResponseOutcome(text, False, usage)

    def summarize_compact(self, request_snapshot: CompactSummaryRequest) -> AssistantText:
        return self.summarize_compact_outcome(request_snapshot).response  # type: ignore[return-value]

    def count_session_title_input_tokens(
        self, request_snapshot: SessionTitleRequest
    ) -> RequestTokenCount:
        return estimate_serialized_input_tokens(
            build_input_projection(
                self._route,
                request_snapshot.conversation_request,
                committed_context=True,
                native_search_enabled=False,
                native_search_options=NativeSearchRuntimeOptions(),
            )
        )

    def generate_session_title_outcome(
        self, request_snapshot: SessionTitleRequest
    ) -> ProviderResponseOutcome:
        request = build_request(
            self._route,
            request_snapshot.conversation_request,
            native_search_enabled=False,
            native_search_options=NativeSearchRuntimeOptions(),
        )
        request["max_output_tokens"] = request_snapshot.max_output_tokens
        response = self._create(request)
        usage = _parse_usage(response)
        result = parse_response(
            response,
            route=self._route,
            requested_output_tokens=request_snapshot.max_output_tokens,
            usage=usage,
            allow_tools=False,
        )
        text = _standalone_text_response(result, self._route)
        return ProviderResponseOutcome(text, False, usage)

    def respond(self, request_snapshot: ConversationRequest) -> ProviderResponse:
        return self.respond_outcome(request_snapshot).response

    def respond_outcome(self, request_snapshot: ConversationRequest) -> ProviderResponseOutcome:
        response = self._create(
            build_request(
                self._route,
                request_snapshot,
                native_search_enabled=self._native_search_enabled,
                native_search_options=self._native_search_options,
            )
        )
        usage = _parse_usage(response)
        parsed = parse_response(response, route=self._route, usage=usage)
        return ProviderResponseOutcome(
            parsed,
            False,
            usage,
            search_observation=_provider_search_observation(response, self._route),
        )

    def respond_stream(
        self,
        request_snapshot: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponse:
        return self.respond_stream_outcome(request_snapshot, event_sink=event_sink).response

    def respond_stream_outcome(
        self,
        request_snapshot: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponseOutcome:
        request = build_request(
            self._route,
            request_snapshot,
            native_search_enabled=self._native_search_enabled,
            native_search_options=self._native_search_options,
        )
        request["stream"] = True
        _validate_request_size(self._route, request)
        stream = None
        try:
            stream = self._client.create(**request)
            response, usage, streamed, search_observation = parse_response_stream(
                stream,
                route=self._route,
                event_sink=event_sink,
            )
            return ProviderResponseOutcome(
                response,
                streamed,
                usage,
                search_observation=search_observation,
            )
        except openai.APIError as error:
            raise normalize_sdk_error(error, route=self._route) from None
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def _create(self, request: dict[str, object]) -> object:
        _validate_request_size(self._route, request)
        try:
            return self._client.create(**request)
        except openai.APIError as error:
            raise normalize_sdk_error(error, route=self._route) from None


def create_openai_responses_provider(
    route: RuntimeProviderRoute,
    *,
    api_key: str,
) -> OpenAIResponsesConversationProvider:
    http_client = httpx.Client(follow_redirects=False, trust_env=False)
    try:
        owner = openai.OpenAI(
            api_key=api_key,
            base_url=route.base_url,
            max_retries=0,
            http_client=http_client,
        )
    except BaseException:
        http_client.close()
        raise
    return OpenAIResponsesConversationProvider(route, owner.responses, owner=owner)


def build_input_projection(
    route: RuntimeProviderRoute,
    request_snapshot: ConversationRequest,
    *,
    committed_context: bool = False,
    native_search_enabled: bool = True,
    native_search_options: NativeSearchRuntimeOptions = NativeSearchRuntimeOptions(),
) -> dict[str, object]:
    validate_native_search_runtime_options(route.native_search, native_search_options)
    instructions = request_snapshot.system_prompt.text
    if request_snapshot.project_instructions is not None:
        instructions += "\n\n" + render_project_instructions(request_snapshot.project_instructions)
    projection: dict[str, object] = {
        "model": route.wire_model,
        "instructions": instructions,
        "input": serialize_history(
            request_snapshot.history,
            route=route,
            committed_context=committed_context,
            effective_summary=request_snapshot.effective_summary,
        ),
    }
    if request_snapshot.allow_tools:
        tools = list(
            responses_tool_definitions(
                request_snapshot.enabled_tool_names,
                definitions=request_snapshot.tool_definitions,
            )
        )
        if native_search_enabled:
            _add_native_search_tool(route, tools, native_search_options)
        projection["tools"] = tools
        projection["tool_choice"] = (
            {"type": "web_search"}
            if native_search_enabled and native_search_options.mode is NativeSearchMode.REQUIRED
            else "auto"
        )
    return projection


def build_request(
    route: RuntimeProviderRoute,
    request_snapshot: ConversationRequest,
    *,
    native_search_enabled: bool = True,
    native_search_options: NativeSearchRuntimeOptions = NativeSearchRuntimeOptions(),
) -> dict[str, object]:
    request = {
        **build_input_projection(
            route,
            request_snapshot,
            native_search_enabled=native_search_enabled,
            native_search_options=native_search_options,
        ),
        "max_output_tokens": route.max_output_tokens,
        "store": False,
        "stream": False,
    }
    if route.temperature is not None and not fixed_sampling_model(route.wire_model):
        request["temperature"] = route.temperature
    _validate_request_size(route, request)
    return request


def build_compact_summary_input_projection(
    route: RuntimeProviderRoute,
    request_snapshot: CompactSummaryRequest,
) -> dict[str, object]:
    return {
        "model": route.wire_model,
        "instructions": request_snapshot.prompt.text,
        "input": [{"role": "user", "content": request_snapshot.source_text}],
    }


def build_compact_summary_request(
    route: RuntimeProviderRoute,
    request_snapshot: CompactSummaryRequest,
) -> dict[str, object]:
    request: dict[str, object] = {
        **build_compact_summary_input_projection(route, request_snapshot),
        "max_output_tokens": request_snapshot.max_output_tokens,
        "store": False,
        "stream": False,
    }
    if route.temperature is not None and not fixed_sampling_model(route.wire_model):
        request["temperature"] = route.temperature
    _validate_request_size(route, request)
    return request


def responses_tool_definitions(
    enabled_tool_names: tuple[str, ...] | None = None,
    *,
    definitions: tuple[CanonicalToolDefinition, ...] | None = None,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "type": "function",
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["input_schema"],
            "strict": False,
        }
        for definition in model_tool_definitions(
            enabled_tool_names,
            definitions=definitions,
        )
    )


def serialize_history(
    history: tuple[ConversationItem, ...],
    *,
    route: RuntimeProviderRoute,
    committed_context: bool = False,
    effective_summary: object | None = None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if effective_summary is not None:
        user_text = getattr(effective_summary, "user_text", None)
        acknowledgement = getattr(effective_summary, "assistant_acknowledgement", None)
        if not isinstance(user_text, str) or not isinstance(acknowledgement, str):
            raise _invalid_history(route, "effective summary was malformed")
        items.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": acknowledgement},
            ]
        )
    expected = "user"
    pending_ids: tuple[str, ...] = ()
    for item in history:
        if isinstance(item, UserMessage):
            if expected != "user":
                raise _invalid_history(route, "user message is out of causal order")
            items.append({"role": "user", "content": item.text})
            expected = "assistant"
            continue
        if isinstance(item, ProviderOwnedItem):
            if expected != "assistant" or item.protocol != "openai_responses":
                raise _invalid_history(route, "provider-owned item is out of causal order")
            items.append(item.as_mapping())
            continue
        if isinstance(item, AssistantText):
            if expected != "assistant":
                raise _invalid_history(route, "assistant text is out of causal order")
            items.append({"role": "assistant", "content": item.text})
            expected = "user"
            continue
        if isinstance(item, (ToolUse, AssistantToolBatch)):
            if expected != "assistant":
                raise _invalid_history(route, "function call is out of causal order")
            requests = item.tool_uses if isinstance(item, AssistantToolBatch) else (item,)
            assistant_text = item.assistant_text
            if assistant_text is not None:
                items.append({"role": "assistant", "content": assistant_text})
            for request in requests:
                try:
                    arguments = tool_input_for_provider_history(request)
                except ValueError:
                    raise _invalid_history(
                        route, f"unsupported tool in history: {request.name}"
                    ) from None
                items.append(
                    {
                        "type": "function_call",
                        "call_id": request.tool_use_id,
                        "name": request.name,
                        "arguments": json.dumps(
                            arguments,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    }
                )
            pending_ids = tuple(request.tool_use_id for request in requests)
            expected = "tool_result"
            continue
        if isinstance(item, ToolResult):
            if expected != "tool_result" or not pending_ids or item.tool_use_id != pending_ids[0]:
                raise _invalid_history(route, "function output does not match its call")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": item.tool_use_id,
                    "output": item.content,
                }
            )
            pending_ids = pending_ids[1:]
            expected = "tool_result" if pending_ids else "assistant"
            continue
        raise _invalid_history(route, "conversation history contains an unknown item")
    terminal = {"user", "assistant"} if committed_context else {"assistant"}
    if expected not in terminal:
        raise _invalid_history(route, "conversation history ended in an incomplete state")
    return items


def parse_response(
    response: object,
    *,
    route: RuntimeProviderRoute,
    requested_output_tokens: int | None = None,
    usage: ProviderTokenUsage | None = None,
    allow_tools: bool = True,
    allow_provider_items: bool = True,
) -> ProviderResponse:
    value = _as_mapping(response, label="provider response", route=route)
    status = value.get("status")
    requested = requested_output_tokens or route.max_output_tokens
    if status == "incomplete":
        details = value.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        if reason in {"max_output_tokens", "max_tokens"}:
            raise output_limit_error(
                provider_id=route.definition.provider_id,
                model_id=route.selected_model,
                message="provider response reached the configured output-token limit",
                requested_output_tokens=requested,
                usage=usage,
                partial_response_observed=bool(value.get("output")),
            )
        raise _invalid_response(route, "provider response was incomplete")
    if status == "failed":
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.PROVIDER_UNAVAILABLE,
            code="response_failed",
            message="provider reported a failed response",
        )
    if status != "completed":
        raise _invalid_response(route, "provider response status was unsupported")
    output = value.get("output")
    if not isinstance(output, list) or not output:
        raise _invalid_response(route, "provider response output was empty or malformed")

    provider_items: list[ProviderOwnedItem] = []
    calls: list[ToolUse] = []
    message_texts: list[str] = []
    citations: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for raw_item in output:
        if not isinstance(raw_item, dict):
            raise _invalid_response(route, "provider output item was malformed")
        item_type = raw_item.get("type")
        item_id = raw_item.get("id")
        if isinstance(item_id, str):
            if item_id in seen_ids:
                raise _invalid_response(route, "provider output item ID was duplicated")
            seen_ids.add(item_id)
        if item_type in {"reasoning", "web_search_call"}:
            if not allow_provider_items:
                raise _invalid_response(route, "text-only response contained provider-owned items")
            allowed_statuses = (
                {None, "completed", "failed"}
                if item_type == "web_search_call"
                else {None, "completed"}
            )
            if raw_item.get("status") not in allowed_statuses:
                raise _invalid_response(route, "provider-owned item did not complete")
            try:
                provider_items.append(ProviderOwnedItem.from_mapping(raw_item))
            except ValueError:
                raise _invalid_response(route, "provider-owned item was malformed") from None
            continue
        if item_type == "message":
            if raw_item.get("role") != "assistant" or raw_item.get("status") not in {
                None,
                "completed",
            }:
                raise _invalid_response(route, "provider message item was malformed")
            text, item_citations = _parse_message_content(raw_item, route)
            if text:
                message_texts.append(text)
            for citation in item_citations:
                if citation not in citations and len(citations) < 20:
                    citations.append(citation)
            continue
        if item_type == "function_call":
            if not allow_tools:
                raise _invalid_response(route, "text-only response contained function calls")
            call_id = raw_item.get("call_id")
            name = raw_item.get("name")
            arguments = raw_item.get("arguments")
            if raw_item.get("status") not in {None, "completed"}:
                raise _invalid_response(route, "provider function call did not complete")
            if not all(isinstance(field, str) and field for field in (call_id, name, arguments)):
                raise _invalid_response(route, "provider function call was malformed")
            if any(existing.tool_use_id == call_id for existing in calls):
                raise _invalid_response(route, "provider function call ID was duplicated")
            try:
                decoded = json.loads(arguments)
            except json.JSONDecodeError:
                raise _invalid_response(
                    route, "provider function arguments were invalid JSON"
                ) from None
            if not isinstance(decoded, dict):
                raise _invalid_response(route, f"provider {name} arguments were malformed")
            try:
                calls.append(tool_use_from_provider_input(call_id, name, decoded))
            except ValueError:
                raise _invalid_response(
                    route, f"provider {name} arguments were malformed"
                ) from None
            continue
        raise _invalid_response(route, f"provider output item type was unsupported: {item_type}")

    assistant_text = "\n".join(message_texts) if message_texts else None
    if assistant_text is not None and citations:
        assistant_text += _render_sources(citations)
    if calls:
        if len(calls) > MAX_TOOL_CALLS_PER_RESPONSE:
            raise _invalid_response(route, "provider function calls exceeded the response limit")
        core: ProviderResponse
        if len(calls) == 1:
            call = calls[0]
            core = ToolUse(call.tool_use_id, call.name, call.arguments, assistant_text)
        else:
            core = AssistantToolBatch(tuple(calls), assistant_text)
    else:
        if not assistant_text:
            raise _invalid_response(route, "provider response contained no assistant text")
        core = AssistantText(assistant_text)
    if not provider_items:
        return core
    return ProviderResponseEnvelope(tuple(provider_items), core)  # type: ignore[arg-type]


def parse_response_stream(
    events: object,
    *,
    route: RuntimeProviderRoute,
    event_sink: ProviderTextDeltaSink,
) -> tuple[
    ProviderResponse,
    ProviderTokenUsage | None,
    bool,
    ProviderSearchObservation | None,
]:
    try:
        iterator = iter(events)  # type: ignore[arg-type]
    except TypeError:
        raise _invalid_response(route, "provider response stream was not iterable") from None
    terminal: object | None = None
    terminal_type: str | None = None
    text_parts: list[str] = []
    previous_sequence = -1
    last_search_phase: ProviderSearchPhase | None = None
    for index, event in enumerate(iterator, start=1):
        if index > MAX_PROVIDER_STREAM_EVENTS:
            raise _invalid_response(route, "provider response stream contained too many events")
        event_type = getattr(event, "type", None)
        sequence = getattr(event, "sequence_number", None)
        if not isinstance(event_type, str) or not event_type.startswith("response."):
            raise _invalid_response(route, "provider response stream event type was malformed")
        if sequence is not None:
            if type(sequence) is not int or sequence <= previous_sequence:
                raise _invalid_response(route, "provider response stream sequence was invalid")
            previous_sequence = sequence
        if terminal is not None:
            raise _invalid_response(route, "provider response stream continued after completion")
        if event_type in {
            "response.web_search_call.in_progress",
            "response.web_search_call.searching",
        }:
            phase = ProviderSearchPhase.SEARCHING
            if phase is not last_search_phase:
                event_sink(ProviderSearchActivity(phase))
                last_search_phase = phase
        elif event_type == "response.web_search_call.completed":
            phase = ProviderSearchPhase.COMPLETED
            if phase is not last_search_phase:
                event_sink(ProviderSearchActivity(phase))
                last_search_phase = phase
        elif event_type == "response.web_search_call.failed":
            phase = ProviderSearchPhase.FAILED
            if phase is not last_search_phase:
                event_sink(ProviderSearchActivity(phase))
                last_search_phase = phase
        elif event_type == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            try:
                event_delta = ProviderTextDelta(delta)
            except ValueError:
                raise _invalid_response(
                    route, "provider response text delta was malformed"
                ) from None
            text_parts.append(delta)
            event_sink(event_delta)
        elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
            terminal = getattr(event, "response", None)
            terminal_type = event_type
    if terminal is None or terminal_type is None:
        raise _invalid_response(route, "provider response stream ended without a terminal event")
    usage = _parse_usage(terminal)
    response = parse_response(terminal, route=route, usage=usage)
    visible = response.response if isinstance(response, ProviderResponseEnvelope) else response
    visible_text = (
        visible.text if isinstance(visible, AssistantText) else visible.assistant_text or ""
    )
    streamed = "".join(text_parts)
    if not visible_text.startswith(streamed):
        raise _invalid_response(route, "provider stream text did not match its completed response")
    suffix = visible_text[len(streamed) :]
    if suffix:
        event_sink(ProviderTextDelta(suffix))
    return (
        response,
        usage,
        bool(visible_text),
        _provider_search_observation(terminal, route),
    )


def _add_native_search_tool(
    route: RuntimeProviderRoute,
    tools: list[dict[str, object]],
    options: NativeSearchRuntimeOptions,
) -> None:
    if route.native_search.adapter_id is not NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1:
        if route.native_search.available:
            raise ValueError("Responses route uses an incompatible native-search adapter")
        return
    tool: dict[str, object] = {"type": "web_search"}
    if options.allowed_domains:
        tool["filters"] = {"allowed_domains": list(options.allowed_domains)}
    if options.context_size is not None:
        tool["search_context_size"] = options.context_size.value
    tools.append(tool)


def _standalone_text_response(
    response: ProviderResponse,
    route: RuntimeProviderRoute,
) -> AssistantText:
    if isinstance(response, ProviderResponseEnvelope):
        if any(item.item_type != "reasoning" for item in response.provider_items):
            raise _invalid_response(
                route, "standalone text response contained a provider tool call"
            )
        response = response.response
    if not isinstance(response, AssistantText):
        raise _invalid_response(route, "standalone response did not contain final text")
    return response


def _parse_message_content(
    item: dict[str, object], route: RuntimeProviderRoute
) -> tuple[str, list[tuple[str, str]]]:
    content = item.get("content")
    if not isinstance(content, list) or not content:
        raise _invalid_response(route, "provider message content was malformed")
    texts: list[str] = []
    citations: list[tuple[str, str]] = []
    for part in content:
        if not isinstance(part, dict):
            raise _invalid_response(route, "provider message content part was malformed")
        part_type = part.get("type")
        if part_type == "refusal":
            raise adapter_error(
                provider_id=route.definition.provider_id,
                model_id=route.selected_model,
                kind=ProviderFailureKind.CONTENT_REFUSAL,
                code="content_refusal",
                message="provider refused the request",
            )
        if part_type != "output_text" or not isinstance(part.get("text"), str):
            raise _invalid_response(route, "provider message content type was unsupported")
        texts.append(part["text"])
        for citation in _citations_from_annotations(part.get("annotations")):
            if citation not in citations:
                citations.append(citation)
    return "".join(texts), citations


def _citations_from_annotations(value: object) -> list[tuple[str, str]]:
    return _citation_details(value)[0]


def _citation_details(value: object) -> tuple[list[tuple[str, str]], int]:
    malformed_container = 0
    if value is None:
        annotations: list[object] = []
    elif isinstance(value, dict):
        annotations = [value]
    elif isinstance(value, list):
        annotations = value[:100]
    else:
        annotations = []
        malformed_container = 1
    citations: list[tuple[str, str]] = []
    discarded = malformed_container
    for annotation in annotations:
        if not isinstance(annotation, dict):
            discarded += 1
            continue
        if annotation.get("type") != "url_citation":
            continue
        nested = annotation.get("url_citation")
        citation_value = nested if isinstance(nested, dict) else annotation
        citation = _safe_citation(citation_value.get("url"), citation_value.get("title"))
        if citation is None:
            discarded += 1
        elif citation not in citations:
            citations.append(citation)
    return citations, discarded


def _provider_search_observation(
    response: object,
    route: RuntimeProviderRoute,
) -> ProviderSearchObservation | None:
    value = _as_mapping(response, label="provider response", route=route)
    output = value.get("output")
    if not isinstance(output, list):
        return None
    calls = 0
    failed = 0
    action_types: list[str] = []
    sources = 0
    citations: list[tuple[str, str]] = []
    discarded_citations = 0
    for item in output[:100]:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            calls += 1
            failed += int(item.get("status") == "failed")
            action = item.get("action")
            action_type = action.get("type") if isinstance(action, dict) else None
            safe_action = (
                action_type if action_type in {"search", "open_page", "find_in_page"} else "unknown"
            )
            if safe_action not in action_types and len(action_types) < 8:
                action_types.append(safe_action)
            raw_sources = action.get("sources") if isinstance(action, dict) else None
            if isinstance(raw_sources, list):
                sources = min(1000, sources + len(raw_sources[:100]))
        if item.get("type") == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content[:100]:
                if not isinstance(part, dict):
                    continue
                item_citations, item_discarded = _citation_details(part.get("annotations"))
                discarded_citations += item_discarded
                for citation in item_citations:
                    if citation not in citations and len(citations) < 20:
                        citations.append(citation)
    if not calls:
        return None
    return ProviderSearchObservation(
        calls,
        failed,
        tuple(action_types),
        sources,
        len(citations),
        discarded_citations,
    )


def _safe_citation(url: object, title: object) -> tuple[str, str] | None:
    if not isinstance(url, str) or len(url) > 4096:
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        return None
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    safe_title = title if isinstance(title, str) and title.strip() else parsed.netloc
    safe_title = " ".join(safe_title.split())[:512]
    if not safe_title or any(character in safe_title for character in ("\x00", "\r", "\n")):
        return None
    return url, safe_title


def _render_sources(citations: list[tuple[str, str]]) -> str:
    lines = ["\n\nSources:"]
    for url, title in citations:
        safe_title = title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [{safe_title}]({url})")
    return "\n".join(lines)


def _as_mapping(
    value: object,
    *,
    label: str,
    route: RuntimeProviderRoute,
) -> dict[str, object]:
    if isinstance(value, dict):
        mapping = value
    else:
        dump = getattr(value, "model_dump", None)
        mapping = dump(mode="json", exclude_none=False) if callable(dump) else None
    if not isinstance(mapping, dict):
        raise _invalid_response(route, f"{label} was malformed")
    try:
        encoded = json.dumps(mapping, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _invalid_response(route, f"{label} was malformed") from None
    if len(encoded) > route.definition.request_body_limit:
        raise _invalid_response(route, f"{label} exceeded the supported size")
    return mapping


def _parse_usage(response: object) -> ProviderTokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if isinstance(usage, dict):
        try:
            return ProviderTokenUsage(usage.get("input_tokens"), usage.get("output_tokens"))
        except ValueError:
            return None
    return parse_provider_usage(usage, input_field="input_tokens", output_field="output_tokens")


def _validate_request_size(route: RuntimeProviderRoute, request: dict[str, object]) -> None:
    try:
        payload = json.dumps(
            request,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _invalid_history(route, "Responses request was not JSON serializable") from None
    if len(payload) > route.definition.request_body_limit:
        raise _invalid_history(route, "Responses request exceeded the provider body limit")


def _invalid_history(route: RuntimeProviderRoute, message: str):
    return adapter_error(
        provider_id=route.definition.provider_id,
        model_id=route.selected_model,
        kind=ProviderFailureKind.INVALID_REQUEST,
        code="invalid_history",
        message=message,
    )


def _invalid_response(route: RuntimeProviderRoute, message: str):
    return adapter_error(
        provider_id=route.definition.provider_id,
        model_id=route.selected_model,
        kind=ProviderFailureKind.RESPONSE_INVALID,
        code="response_invalid",
        message=message,
    )
