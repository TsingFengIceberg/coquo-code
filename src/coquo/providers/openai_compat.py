"""OpenAI-compatible chat-completions adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlsplit

import openai

from coquo.core.compaction import CompactSummaryRequest, EffectiveContextSummary
from coquo.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    ConversationItem,
    ConversationRequest,
    ProviderResponse,
    MemoryEvidence,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.orchestration import ProviderFailureKind
from coquo.core.project_instructions import render_project_instructions
from coquo.core.session_title import SessionTitleRequest
from coquo.providers.definitions import RuntimeProviderRoute, wire_reasoning_effort
from coquo.providers.native_search import (
    NativeSearchAdapterId,
    NativeSearchCitationFormat,
    NativeSearchConfiguration,
    NativeSearchRuntimeOptions,
    validate_native_search_runtime_options,
)
from coquo.providers.errors import (
    ProviderAdapterError,
    adapter_error,
    extract_upstream_error_metadata,
    output_limit_error,
)
from coquo.providers.request_context import (
    RequestTokenCount,
    estimate_serialized_input_tokens,
)
from coquo.providers.streaming import (
    MAX_PROVIDER_STREAM_ARGUMENT_BYTES,
    MAX_PROVIDER_STREAM_EVENTS,
    MAX_PROVIDER_STREAM_IDENTIFIER_BYTES,
    MAX_PROVIDER_STREAM_TEXT_BYTES,
    MAX_PROVIDER_STREAM_TEXT_CHARACTERS,
    ProviderTextDelta,
    ProviderTextDeltaSink,
    ProviderResponseOutcome,
    ProviderSearchActivity,
    ProviderSearchObservation,
    ProviderSearchPhase,
)
from coquo.providers.usage import ProviderTokenUsage, parse_provider_usage
from coquo.tools.catalog import (
    MAX_TOOL_CALLS_PER_RESPONSE,
    model_tool_definitions,
    tool_input_for_provider_history,
    tool_use_from_provider_input,
)


class ChatCompletionsClient(Protocol):
    """The narrow synchronous SDK operation used by the compatible adapter."""

    def create(self, **kwargs: object) -> object:
        """Create one non-streaming chat completion."""


class OpenAICompatibleConversationProvider:
    """Translate neutral causal history through an OpenAI-compatible endpoint."""

    def __init__(
        self,
        route: RuntimeProviderRoute,
        client: ChatCompletionsClient,
        *,
        owner: object | None = None,
    ) -> None:
        self._route = route
        self._client = client
        self._owner = owner
        self._native_search_enabled = route.native_search.default_enabled
        self._native_search_options = NativeSearchRuntimeOptions()

    def set_native_search_enabled(self, enabled: bool) -> None:
        """Toggle one process-local provider capability between serialized turns."""
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
        """Close the production SDK owner when this adapter constructed it."""
        close = getattr(self._owner, "close", None)
        if callable(close):
            close()

    def count_input_tokens(self, request_snapshot: ConversationRequest) -> RequestTokenCount:
        """Estimate the exact native input-bearing chat projection locally."""
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
        """Estimate the exact no-tools compact-summary input projection."""
        return estimate_serialized_input_tokens(
            build_compact_summary_input_projection(self._route, request_snapshot)
        )

    def summarize_compact(self, request_snapshot: CompactSummaryRequest) -> AssistantText:
        """Generate one text-only summary without exposing compatible tools."""
        return self.summarize_compact_outcome(request_snapshot).response  # type: ignore[return-value]

    def summarize_compact_outcome(
        self, request_snapshot: CompactSummaryRequest
    ) -> ProviderResponseOutcome:
        """Generate one summary and retain actual provider usage outside history."""
        request = build_compact_summary_request(self._route, request_snapshot)
        try:
            response = self._client.create(**request)
        except openai.APIError as error:
            raise normalize_sdk_error(error, route=self._route) from None
        usage = _parse_compatible_usage(getattr(response, "usage", None))
        return ProviderResponseOutcome(
            parse_compact_summary_response(
                response,
                route=self._route,
                requested_output_tokens=request_snapshot.max_output_tokens,
                usage=usage,
            ),
            False,
            usage,
        )

    def count_session_title_input_tokens(
        self, request_snapshot: SessionTitleRequest
    ) -> RequestTokenCount:
        """Estimate the exact no-tools Session-title projection."""
        route = replace(self._route, max_output_tokens=request_snapshot.max_output_tokens)
        return estimate_serialized_input_tokens(
            build_input_projection(
                route,
                request_snapshot.conversation_request,
                committed_context=True,
            )
        )

    def generate_session_title_outcome(
        self, request_snapshot: SessionTitleRequest
    ) -> ProviderResponseOutcome:
        """Generate one no-tools Session title and retain provider usage."""
        route = replace(self._route, max_output_tokens=request_snapshot.max_output_tokens)
        request = build_request(
            route,
            request_snapshot.conversation_request,
            native_search_enabled=False,
        )
        try:
            response = self._client.create(**request)
        except openai.APIError as error:
            raise normalize_sdk_error(error, route=route) from None
        usage = _parse_compatible_usage(getattr(response, "usage", None))
        return ProviderResponseOutcome(
            parse_session_title_response(response, route=route, usage=usage),
            False,
            usage,
        )

    def respond(self, request_snapshot: ConversationRequest) -> ProviderResponse:
        """Make one non-streaming compatible request through the injected seam."""
        return self.respond_outcome(request_snapshot).response

    def respond_outcome(self, request_snapshot: ConversationRequest) -> ProviderResponseOutcome:
        """Return one response with Host-only actual token usage."""
        request = build_request(
            self._route,
            request_snapshot,
            native_search_enabled=self._native_search_enabled,
            native_search_options=self._native_search_options,
        )
        try:
            response = self._client.create(**request)
        except openai.APIError as error:
            raise normalize_sdk_error(error, route=self._route) from None
        usage = _parse_compatible_usage(getattr(response, "usage", None))
        parsed = parse_response(response, route=self._route, usage=usage)
        return ProviderResponseOutcome(
            parsed,
            False,
            usage,
            search_observation=(
                _buffered_search_observation(parsed) if self._native_search_enabled else None
            ),
        )

    def respond_stream(
        self,
        request_snapshot: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponse:
        """Consume one compatible response stream into the same neutral contract."""
        return self.respond_stream_outcome(request_snapshot, event_sink=event_sink).response

    def respond_stream_outcome(
        self,
        request_snapshot: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponseOutcome:
        """Consume one stream and retain final compatible usage metadata."""
        if self._native_search_enabled:
            event_sink(ProviderSearchActivity(ProviderSearchPhase.SEARCHING))
            try:
                outcome = self.respond_outcome(request_snapshot)
            except BaseException:
                event_sink(ProviderSearchActivity(ProviderSearchPhase.FAILED))
                raise
            event_sink(ProviderSearchActivity(ProviderSearchPhase.COMPLETED))
            if isinstance(outcome.response, AssistantText):
                event_sink(ProviderTextDelta(outcome.response.text))
                return ProviderResponseOutcome(
                    outcome.response,
                    True,
                    outcome.usage,
                    search_observation=outcome.search_observation,
                )
            return outcome
        request = build_request(self._route, request_snapshot, native_search_enabled=False)
        request["stream"] = True
        request["stream_options"] = {"include_usage": True}
        _validate_request_size(self._route, request)
        stream = None
        captured_usage: list[ProviderTokenUsage] = []
        try:
            stream = self._client.create(**request)
            response = parse_response_stream(
                stream,
                route=self._route,
                event_sink=event_sink,
                usage_sink=captured_usage.append,
            )
            return ProviderResponseOutcome(
                response,
                True,
                captured_usage[0] if captured_usage else None,
            )
        except openai.APIError as error:
            raise normalize_sdk_error(error, route=self._route) from None
        finally:
            _close_stream(stream)


def create_openai_compatible_provider(
    route: RuntimeProviderRoute,
    *,
    api_key: str | None,
) -> OpenAICompatibleConversationProvider:
    """Construct the official OpenAI SDK at the selected credential boundary."""
    definition = route.definition
    if definition.credential_required and not (api_key and api_key.strip()):
        raise adapter_error(
            provider_id=definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.AUTHENTICATION,
            code="missing_api_key",
            message=f"{definition.credential_env} is not configured",
        )
    client = openai.OpenAI(
        api_key=api_key or "local-no-auth",
        base_url=route.base_url,
        max_retries=0,
        http_client=openai.DefaultHttpxClient(follow_redirects=False),
    )
    return OpenAICompatibleConversationProvider(route, client.chat.completions, owner=client)


def _compatible_tool_definition(definition: dict[str, object]) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["input_schema"],
        },
    }


def read_file_tool_definition() -> dict[str, object]:
    """Retain the tested compatible wrapper for the canonical read contract."""
    return _compatible_tool_definition(model_tool_definitions()[0])


def glob_tool_definition() -> dict[str, object]:
    """Wrap the canonical glob contract as one compatible function tool."""
    return _compatible_tool_definition(model_tool_definitions()[1])


def grep_tool_definition() -> dict[str, object]:
    """Wrap the canonical grep contract as one compatible function tool."""
    return _compatible_tool_definition(model_tool_definitions()[2])


def write_file_tool_definition() -> dict[str, object]:
    """Wrap the canonical controlled write contract as one compatible function tool."""
    return _compatible_tool_definition(model_tool_definitions()[3])


def edit_file_tool_definition() -> dict[str, object]:
    """Wrap the canonical controlled exact-edit contract as one compatible function tool."""
    return _compatible_tool_definition(model_tool_definitions()[4])


def run_command_tool_definition() -> dict[str, object]:
    """Wrap the canonical controlled command contract as one compatible function tool."""
    return _compatible_tool_definition(model_tool_definitions()[5])


def mkdir_tool_definition() -> dict[str, object]:
    """Wrap the canonical controlled directory contract as one compatible function tool."""
    return _compatible_tool_definition(model_tool_definitions()[6])


def move_file_tool_definition() -> dict[str, object]:
    """Return the canonical move_file definition in OpenAI tool shape."""
    return _compatible_tool_definition(model_tool_definitions()[7])


def delete_file_tool_definition() -> dict[str, object]:
    """Return the canonical delete_file definition in OpenAI tool shape."""
    return _compatible_tool_definition(model_tool_definitions()[8])


def delete_directory_tool_definition() -> dict[str, object]:
    """Return the canonical delete_directory definition in OpenAI tool shape."""
    return _compatible_tool_definition(model_tool_definitions()[9])


def list_directory_tool_definition() -> dict[str, object]:
    """Return the canonical list_directory definition in OpenAI tool shape."""
    return _compatible_tool_definition(model_tool_definitions()[10])


def copy_file_tool_definition() -> dict[str, object]:
    """Return the canonical copy_file definition in OpenAI tool shape."""
    return _compatible_tool_definition(model_tool_definitions()[11])


def read_file_lines_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[12])


def stat_path_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[13])


def list_tree_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[14])


def grep_regex_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[15])


def patch_file_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[16])


def git_status_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[17])


def git_diff_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[18])


def git_log_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[19])


def git_show_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[20])


def web_search_tool_definition() -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions()[21])


def _named_compatible_tool_definition(name: str) -> dict[str, object]:
    return _compatible_tool_definition(model_tool_definitions((name,))[0])


def web_fetch_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("web_fetch")


def compare_files_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("compare_files")


def git_blame_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("git_blame")


def git_refs_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("git_refs")


def json_query_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("json_query")


def checksum_file_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("checksum_file")


def archive_list_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("archive_list")


def move_directory_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("move_directory")


def download_file_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("download_file")


def task_propose_plan_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_propose_plan")


def task_report_reflection_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_report_reflection")


def task_report_blocker_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_report_blocker")


def task_propose_completion_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_propose_completion")


def task_propose_start_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_propose_start")


def task_accept_admission_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_accept_admission")


def task_accept_plan_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_accept_plan")


def task_confirm_completion_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("task_confirm_completion")


def child_spawn_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("child_spawn")


def child_status_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("child_status")


def child_wait_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("child_wait")


def child_cancel_tool_definition() -> dict[str, object]:
    return _named_compatible_tool_definition("child_cancel")


def model_tool_definitions_for_openai(
    enabled_tool_names: tuple[str, ...] | None = None,
    *,
    definitions: tuple[CanonicalToolDefinition, ...] | None = None,
) -> tuple[dict[str, object], ...]:
    """Wrap every canonical tool in its fixed provider-visible order."""
    return tuple(
        _compatible_tool_definition(item)
        for item in model_tool_definitions(
            enabled_tool_names,
            definitions=definitions,
        )
    )


def _apply_native_search_projection(
    projection: dict[str, object],
    configuration: NativeSearchConfiguration,
    options: NativeSearchRuntimeOptions = NativeSearchRuntimeOptions(),
) -> None:
    validate_native_search_runtime_options(configuration, options)
    adapter = configuration.adapter_id
    if adapter is None:
        return
    if adapter is NativeSearchAdapterId.OPENAI_CHAT_WEB_SEARCH_OPTIONS_V1:
        web_search_options: dict[str, object] = {}
        if options.context_size is not None:
            web_search_options["search_context_size"] = options.context_size.value
        projection["web_search_options"] = web_search_options
        return
    if adapter is NativeSearchAdapterId.DASHSCOPE_ENABLE_SEARCH_V1:
        projection["extra_body"] = {"enable_search": True}
        return
    if adapter is NativeSearchAdapterId.OPENROUTER_WEB_SEARCH_V1:
        tools = projection.setdefault("tools", [])
        assert isinstance(tools, list)
        tools.append({"type": "openrouter:web_search"})
        return
    if adapter is NativeSearchAdapterId.CUSTOM_MANIFEST_V1:
        manifest = configuration.manifest
        assert manifest is not None
        if manifest.extra_body:
            projection["extra_body"] = dict(manifest.extra_body)
        if manifest.server_tool is not None:
            tools = projection.setdefault("tools", [])
            assert isinstance(tools, list)
            tools.append(dict(manifest.server_tool))
        return
    raise ValueError(f"native-search adapter is incompatible with OpenAI Chat: {adapter.value}")


def _append_native_search_citations(
    text: str,
    message: object,
    configuration: NativeSearchConfiguration,
) -> str:
    citation_format = _citation_format(configuration)
    if citation_format is NativeSearchCitationFormat.NONE:
        return text
    citations: list[tuple[str, str]] = []
    if citation_format is NativeSearchCitationFormat.OPENAI_URL_ANNOTATIONS:
        for annotation in _object_list(_field(message, "annotations")):
            if _field(annotation, "type") != "url_citation":
                continue
            citation = _field(annotation, "url_citation")
            url = _field(citation, "url")
            title = _field(citation, "title")
            _add_citation(citations, url, title)
    elif citation_format is NativeSearchCitationFormat.DASHSCOPE_SEARCH_INFO:
        search_info = _field(message, "search_info")
        for result in _object_list(_field(search_info, "search_results")):
            _add_citation(citations, _field(result, "url"), _field(result, "title"))
    if not citations:
        return text
    rendered = ["", "", "Sources:"]
    rendered.extend(f"- [{_escape_link_title(title)}]({url})" for url, title in citations)
    return text + "\n".join(rendered)


def _citation_format(configuration: NativeSearchConfiguration) -> NativeSearchCitationFormat:
    adapter = configuration.adapter_id
    if adapter in {
        NativeSearchAdapterId.OPENAI_CHAT_WEB_SEARCH_OPTIONS_V1,
        NativeSearchAdapterId.OPENROUTER_WEB_SEARCH_V1,
    }:
        return NativeSearchCitationFormat.OPENAI_URL_ANNOTATIONS
    if adapter is NativeSearchAdapterId.DASHSCOPE_ENABLE_SEARCH_V1:
        return NativeSearchCitationFormat.DASHSCOPE_SEARCH_INFO
    if adapter is NativeSearchAdapterId.CUSTOM_MANIFEST_V1:
        assert configuration.manifest is not None
        return configuration.manifest.citation_format
    return NativeSearchCitationFormat.NONE


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) and len(value) <= 100 else []


def _add_citation(citations: list[tuple[str, str]], url: object, title: object) -> None:
    if len(citations) >= 20 or not isinstance(url, str) or len(url) > 4096:
        return
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        return
    safe_title = title if isinstance(title, str) and title.strip() else parsed.netloc
    safe_title = " ".join(safe_title.split())[:512]
    if not safe_title or any(character in safe_title for character in ("\x00", "\r", "\n")):
        return
    if all(existing_url != url for existing_url, _existing_title in citations):
        citations.append((url, safe_title))


def _escape_link_title(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _buffered_search_observation(response: ProviderResponse) -> ProviderSearchObservation:
    text = response.text if isinstance(response, AssistantText) else response.assistant_text or ""
    citation_count = min(20, text.count("\n- [") if "\n\nSources:\n" in text else 0)
    return ProviderSearchObservation(1, 0, ("search",), citation_count, citation_count)


def build_input_projection(
    route: RuntimeProviderRoute,
    request_snapshot: ConversationRequest,
    *,
    committed_context: bool = False,
    native_search_enabled: bool = True,
    native_search_options: NativeSearchRuntimeOptions = NativeSearchRuntimeOptions(),
) -> dict[str, object]:
    """Build the native fields that contribute provider input tokens."""
    system_messages = [{"role": "system", "content": request_snapshot.system_prompt.text}]
    if request_snapshot.project_instructions is not None:
        system_messages.append(
            {
                "role": "system",
                "content": render_project_instructions(request_snapshot.project_instructions),
            }
        )
    projection: dict[str, object] = {
        "model": route.wire_model,
        "messages": [
            *system_messages,
            *_serialize_effective_summary(request_snapshot.effective_summary),
            *serialize_history(
                request_snapshot.history,
                route=route,
                committed_context=committed_context,
                memory_evidence=request_snapshot.memory_evidence,
            ),
        ],
    }
    if request_snapshot.allow_tools:
        projection["tools"] = list(
            model_tool_definitions_for_openai(
                request_snapshot.enabled_tool_names,
                definitions=request_snapshot.tool_definitions,
            )
        )
        projection["parallel_tool_calls"] = False
        if native_search_enabled:
            _apply_native_search_projection(projection, route.native_search, native_search_options)
    return projection


def build_request(
    route: RuntimeProviderRoute,
    request_snapshot: ConversationRequest,
    *,
    native_search_enabled: bool = True,
    native_search_options: NativeSearchRuntimeOptions = NativeSearchRuntimeOptions(),
) -> dict[str, object]:
    """Build a complete provider-native request with deterministic compatibility rules."""
    request: dict[str, object] = {
        **build_input_projection(
            route,
            request_snapshot,
            native_search_enabled=native_search_enabled,
            native_search_options=native_search_options,
        ),
        "stream": False,
    }
    token_field = token_limit_field(route.wire_model)
    request[token_field] = route.max_output_tokens
    if route.reasoning_effort is not None:
        request["reasoning_effort"] = wire_reasoning_effort(
            route.reasoning_effort, route.reasoning_profile
        )
    if route.temperature is not None and not fixed_sampling_model(route.wire_model):
        request["temperature"] = route.temperature
    _validate_request_size(route, request)
    return request


def build_compact_summary_input_projection(
    route: RuntimeProviderRoute,
    request_snapshot: CompactSummaryRequest,
) -> dict[str, object]:
    """Build the no-tools compatible input projection for controlled summary."""
    return {
        "model": route.wire_model,
        "messages": [
            {"role": "system", "content": request_snapshot.prompt.text},
            {"role": "user", "content": request_snapshot.source_text},
        ],
    }


def build_compact_summary_request(
    route: RuntimeProviderRoute,
    request_snapshot: CompactSummaryRequest,
) -> dict[str, object]:
    """Build one complete compatible summary request without tool fields."""
    request: dict[str, object] = {
        **build_compact_summary_input_projection(route, request_snapshot),
        "stream": False,
    }
    request[token_limit_field(route.wire_model)] = request_snapshot.max_output_tokens
    if route.temperature is not None and not fixed_sampling_model(route.wire_model):
        request["temperature"] = route.temperature
    _validate_request_size(route, request)
    return request


def serialize_history(
    history: tuple[ConversationItem, ...],
    *,
    route: RuntimeProviderRoute,
    committed_context: bool = False,
    memory_evidence: tuple[MemoryEvidence, ...] = (),
) -> list[dict[str, object]]:
    """Translate neutral history for invocation or committed-context counting."""
    if not history:
        if committed_context:
            return []
        raise _invalid_history(route, "conversation history must not be empty")
    messages: list[dict[str, object]] = []
    messages.extend({"role": "user", "content": item.rendered} for item in memory_evidence)
    expected = "user"
    pending_tool_use_ids: tuple[str, ...] = ()

    for item in history:
        if isinstance(item, UserMessage):
            if expected != "user":
                raise _invalid_history(route, "user message is out of causal order")
            messages.append({"role": "user", "content": item.text})
            expected = "assistant"
            continue
        if isinstance(item, AssistantText):
            if expected != "assistant":
                raise _invalid_history(route, "assistant text is out of causal order")
            messages.append({"role": "assistant", "content": item.text})
            expected = "user"
            continue
        if isinstance(item, ToolUse):
            if expected != "assistant":
                raise _invalid_history(route, "tool use is out of causal order")
            try:
                tool_input = tool_input_for_provider_history(item)
            except ValueError:
                raise _invalid_history(route, f"unsupported tool in history: {item.name}") from None
            if not item.tool_use_id:
                raise _invalid_history(route, "tool use ID must not be blank")
            if item.assistant_text is not None:
                try:
                    ToolUse(
                        item.tool_use_id,
                        item.name,
                        item.arguments,
                        assistant_text=item.assistant_text,
                    )
                except ValueError:
                    raise _invalid_history(route, "assistant tool text is malformed") from None
            messages.append(
                {
                    "role": "assistant",
                    "content": item.assistant_text,
                    "tool_calls": [
                        {
                            "id": item.tool_use_id,
                            "type": "function",
                            "function": {
                                "name": item.name,
                                "arguments": json.dumps(
                                    tool_input,
                                    separators=(",", ":"),
                                    ensure_ascii=False,
                                    sort_keys=True,
                                ),
                            },
                        }
                    ],
                }
            )
            pending_tool_use_ids = (item.tool_use_id,)
            expected = "tool_result"
            continue
        if isinstance(item, AssistantToolBatch):
            if expected != "assistant":
                raise _invalid_history(route, "tool batch is out of causal order")
            tool_calls: list[dict[str, object]] = []
            for request in item.tool_uses:
                try:
                    tool_input = tool_input_for_provider_history(request)
                except ValueError:
                    raise _invalid_history(
                        route, f"unsupported tool in history: {request.name}"
                    ) from None
                tool_calls.append(
                    {
                        "id": request.tool_use_id,
                        "type": "function",
                        "function": {
                            "name": request.name,
                            "arguments": json.dumps(
                                tool_input,
                                separators=(",", ":"),
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        },
                    }
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": item.assistant_text,
                    "tool_calls": tool_calls,
                }
            )
            pending_tool_use_ids = tuple(request.tool_use_id for request in item.tool_uses)
            expected = "tool_result"
            continue
        if isinstance(item, ToolResult):
            if expected != "tool_result" or not pending_tool_use_ids:
                raise _invalid_history(route, "tool result does not match the pending tool use")
            if item.tool_use_id != pending_tool_use_ids[0]:
                raise _invalid_history(route, "tool result does not match the pending tool use")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_use_id,
                    "content": item.content,
                }
            )
            pending_tool_use_ids = pending_tool_use_ids[1:]
            expected = "tool_result" if pending_tool_use_ids else "assistant"
            continue
        raise _invalid_history(route, "conversation history contains an unknown item")

    valid_terminal_states = {"assistant"}
    if committed_context:
        valid_terminal_states.add("user")
    if expected not in valid_terminal_states:
        message = (
            "committed conversation history must end with assistant text"
            if committed_context
            else "conversation history must end before an assistant response"
        )
        raise _invalid_history(route, message)
    return messages


def _serialize_effective_summary(
    summary: EffectiveContextSummary | None,
) -> list[dict[str, object]]:
    if summary is None:
        return []
    return [
        {"role": "user", "content": summary.user_text},
        {"role": "assistant", "content": summary.assistant_acknowledgement},
    ]


def parse_compact_summary_response(
    response: object,
    *,
    route: RuntimeProviderRoute,
    requested_output_tokens: int | None = None,
    usage: ProviderTokenUsage | None = None,
) -> AssistantText:
    """Decode only one normally completed text-only compact summary."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise _invalid_response(route, "compact summary response must contain exactly one choice")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason in {"length", "max_tokens"}:
        raise _output_limit_response(
            route,
            "compact summary reached the configured output-token limit",
            requested_output_tokens=(
                requested_output_tokens
                if requested_output_tokens is not None
                else route.max_output_tokens
            ),
            usage=usage,
            partial_response_observed=_choice_has_partial_response(choice),
        )
    if finish_reason in {"content_filter", "refusal"}:
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused or filtered the compact summary request",
        )
    if finish_reason != "stop":
        raise _invalid_response(route, "compact summary used an unsupported finish reason")
    message = getattr(choice, "message", None)
    if message is None:
        raise _invalid_response(route, "compact summary choice contained no message")
    if getattr(message, "refusal", None):
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused the compact summary request",
        )
    if getattr(message, "tool_calls", None):
        raise _invalid_response(route, "compact summary unexpectedly contained tool calls")
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise _invalid_response(route, "compact summary text was empty or malformed")
    return AssistantText(content.strip())


def parse_session_title_response(
    response: object,
    *,
    route: RuntimeProviderRoute,
    usage: ProviderTokenUsage | None = None,
) -> AssistantText:
    """Decode only one normally completed text-only Session title."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise _invalid_response(route, "Session title response must contain exactly one choice")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason in {"length", "max_tokens"}:
        raise _output_limit_response(
            route,
            "Session title reached the configured output-token limit",
            requested_output_tokens=route.max_output_tokens,
            usage=usage,
            partial_response_observed=_choice_has_partial_response(choice),
        )
    if finish_reason in {"content_filter", "refusal"}:
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused or filtered the Session title request",
        )
    if finish_reason != "stop":
        raise _invalid_response(route, "Session title used an unsupported finish reason")
    message = getattr(choice, "message", None)
    if message is None:
        raise _invalid_response(route, "Session title choice contained no message")
    if getattr(message, "refusal", None):
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused the Session title request",
        )
    if getattr(message, "tool_calls", None):
        raise _invalid_response(route, "Session title unexpectedly contained tool calls")
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise _invalid_response(route, "Session title text was empty or malformed")
    return AssistantText(content.strip())


def parse_response(
    response: object,
    *,
    route: RuntimeProviderRoute,
    usage: ProviderTokenUsage | None = None,
) -> ProviderResponse:
    """Decode complete text or one bounded ordered function-call batch."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise _invalid_response(route, "provider response must contain exactly one choice")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason in {"length", "max_tokens"}:
        raise _output_limit_response(
            route,
            "provider response reached the configured output-token limit",
            requested_output_tokens=route.max_output_tokens,
            usage=usage,
            partial_response_observed=_choice_has_partial_response(choice),
        )
    if finish_reason in {"content_filter", "refusal"}:
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused or filtered the request",
        )
    if finish_reason not in {"stop", "tool_calls"}:
        raise _invalid_response(route, "provider response used an unsupported finish reason")

    message = getattr(choice, "message", None)
    if message is None:
        raise _invalid_response(route, "provider response choice contained no message")
    content = getattr(message, "content", None)
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused the request",
        )
    tool_calls = getattr(message, "tool_calls", None) or []
    if finish_reason == "stop":
        if not isinstance(content, str) or not content:
            raise _invalid_response(route, "provider text response was empty or malformed")
        if tool_calls:
            raise _invalid_response(route, "text response unexpectedly contained tool calls")
        return AssistantText(
            text=_append_native_search_citations(content, message, route.native_search)
        )
    if not isinstance(tool_calls, list) or not tool_calls:
        raise _invalid_response(route, "tool response contained no tool calls")
    if len(tool_calls) > MAX_TOOL_CALLS_PER_RESPONSE:
        raise _invalid_response(route, "tool response exceeded the per-response call limit")
    if content is None or content == "":
        assistant_text = None
    elif isinstance(content, str):
        assistant_text = content
    else:
        raise _invalid_response(route, "provider assistant tool text was malformed")

    requests: list[ToolUse] = []
    seen_ids: set[str] = set()
    for call in tool_calls:
        if getattr(call, "type", "function") != "function":
            raise _invalid_response(route, "provider tool call type was unsupported")
        tool_use_id = getattr(call, "id", None)
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if not isinstance(tool_use_id, str) or not tool_use_id:
            raise _invalid_response(route, "provider tool call ID was malformed")
        if tool_use_id in seen_ids:
            raise _invalid_response(route, "provider tool call ID was duplicated")
        seen_ids.add(tool_use_id)
        if not isinstance(name, str):
            raise _invalid_response(route, "provider requested an unsupported tool")
        if not isinstance(arguments, str):
            raise _invalid_response(route, "provider tool arguments were not JSON text")
        try:
            tool_input = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            raise _invalid_response(route, "provider tool arguments were invalid JSON") from None
        if not isinstance(tool_input, dict):
            raise _invalid_response(route, f"provider {name} arguments were malformed")
        try:
            requests.append(tool_use_from_provider_input(tool_use_id, name, tool_input))
        except ValueError:
            raise _invalid_response(route, f"provider {name} arguments were malformed") from None
    if len(requests) == 1:
        request = requests[0]
        if assistant_text is None:
            return request
        try:
            return ToolUse(
                request.tool_use_id,
                request.name,
                request.arguments,
                assistant_text=assistant_text,
            )
        except ValueError:
            raise _invalid_response(route, "provider assistant tool text was malformed") from None
    try:
        return AssistantToolBatch(tuple(requests), assistant_text)
    except ValueError:
        raise _invalid_response(route, "provider tool batch was malformed") from None


@dataclass
class _CompatibleStreamToolCall:
    tool_use_id: str | None = None
    name_parts: list[str] = field(default_factory=list)
    argument_parts: list[str] = field(default_factory=list)
    name_bytes: int = 0
    argument_bytes: int = 0


def parse_response_stream(
    chunks: object,
    *,
    route: RuntimeProviderRoute,
    event_sink: ProviderTextDeltaSink,
    usage_sink: Callable[[ProviderTokenUsage], None] | None = None,
) -> ProviderResponse:
    """Assemble one strict sequential response from compatible chat chunks."""
    try:
        iterator = iter(chunks)  # type: ignore[arg-type]
    except TypeError:
        raise _invalid_response(route, "provider stream was not iterable") from None

    text_parts: list[str] = []
    tool_states: dict[int, _CompatibleStreamToolCall] = {}
    total_argument_bytes = 0
    finish_reason: str | None = None
    saw_chunk = False
    text_characters = 0
    text_bytes = 0
    stream_usage: ProviderTokenUsage | None = None
    saw_usage_metadata = False
    saw_empty_prefix_chunk = False

    for chunk_index, chunk in enumerate(iterator, start=1):
        if chunk_index > MAX_PROVIDER_STREAM_EVENTS:
            raise _invalid_response(route, "provider stream contained too many chunks")
        choices = getattr(chunk, "choices", None)
        raw_usage = getattr(chunk, "usage", None)
        chunk_usage = _parse_compatible_usage(raw_usage)
        if choices == [] and raw_usage is None and not saw_chunk and finish_reason is None:
            if saw_empty_prefix_chunk:
                raise _invalid_response(route, "provider stream repeated empty prefix chunk")
            saw_empty_prefix_chunk = True
            continue
        if choices == [] and finish_reason is not None and raw_usage is not None:
            if saw_usage_metadata:
                raise _invalid_response(route, "provider stream repeated usage metadata")
            saw_usage_metadata = True
            stream_usage = chunk_usage
            continue
        if finish_reason is not None:
            raise _invalid_response(route, "provider stream continued after its finish reason")
        if not isinstance(choices, list) or len(choices) != 1:
            raise _invalid_response(route, "provider stream chunk must contain one choice")
        if raw_usage is not None:
            if saw_usage_metadata:
                raise _invalid_response(route, "provider stream repeated usage metadata")
            saw_usage_metadata = True
            stream_usage = chunk_usage
        choice = choices[0]
        choice_index = getattr(choice, "index", None)
        if type(choice_index) is not int or choice_index != 0:
            raise _invalid_response(route, "provider stream choice index was invalid")
        delta = getattr(choice, "delta", None)
        if delta is None:
            raise _invalid_response(route, "provider stream choice contained no delta")
        saw_chunk = True

        refusal = getattr(delta, "refusal", None)
        if refusal:
            raise adapter_error(
                provider_id=route.definition.provider_id,
                model_id=route.selected_model,
                kind=ProviderFailureKind.CONTENT_REFUSAL,
                code="content_refusal",
                message="provider refused or filtered the request",
            )
        content = getattr(delta, "content", None)
        if content is not None:
            if not isinstance(content, str):
                raise _invalid_response(route, "provider stream text delta was malformed")
            if content:
                try:
                    encoded_content = content.encode("utf-8")
                except UnicodeEncodeError:
                    raise _invalid_response(
                        route, "provider stream text delta was malformed"
                    ) from None
                text_characters += len(content)
                text_bytes += len(encoded_content)
                if (
                    text_characters > MAX_PROVIDER_STREAM_TEXT_CHARACTERS
                    or text_bytes > MAX_PROVIDER_STREAM_TEXT_BYTES
                ):
                    raise _invalid_response(route, "provider stream text was too large")
                try:
                    event = ProviderTextDelta(content)
                except ValueError:
                    raise _invalid_response(
                        route, "provider stream text delta was malformed"
                    ) from None
                text_parts.append(content)
                event_sink(event)

        tool_calls = getattr(delta, "tool_calls", None) or []
        if not isinstance(tool_calls, list):
            raise _invalid_response(route, "provider stream tool-call delta was malformed")
        if len(tool_calls) > MAX_TOOL_CALLS_PER_RESPONSE:
            raise _invalid_response(route, "provider stream tool-call delta was too large")
        for call in tool_calls:
            call_index = getattr(call, "index", None)
            if type(call_index) is not int or call_index < 0:
                raise _invalid_response(route, "provider stream tool-call index was invalid")
            if call_index >= MAX_TOOL_CALLS_PER_RESPONSE:
                raise _invalid_response(
                    route, "provider stream exceeded the per-response call limit"
                )
            state = tool_states.setdefault(call_index, _CompatibleStreamToolCall())
            call_id = getattr(call, "id", None)
            if call_id is not None:
                if not isinstance(call_id, str) or not call_id:
                    raise _invalid_response(route, "provider stream tool-call ID was malformed")
                try:
                    encoded_call_id = call_id.encode("utf-8")
                except UnicodeEncodeError:
                    raise _invalid_response(
                        route, "provider stream tool-call ID was malformed"
                    ) from None
                if len(encoded_call_id) > MAX_PROVIDER_STREAM_IDENTIFIER_BYTES:
                    raise _invalid_response(route, "provider stream tool-call ID was too large")
                if state.tool_use_id is not None and call_id != state.tool_use_id:
                    raise _invalid_response(route, "provider stream changed its tool-call ID")
                state.tool_use_id = call_id
            call_type = getattr(call, "type", None)
            if call_type not in {None, "function"}:
                raise _invalid_response(route, "provider stream tool-call type was unsupported")
            function = getattr(call, "function", None)
            if function is None:
                raise _invalid_response(route, "provider stream tool function was missing")
            name = getattr(function, "name", None)
            if name is not None:
                if not isinstance(name, str):
                    raise _invalid_response(route, "provider stream tool name was malformed")
                try:
                    encoded_name = name.encode("utf-8")
                except UnicodeEncodeError:
                    raise _invalid_response(
                        route, "provider stream tool name was malformed"
                    ) from None
                state.name_bytes += len(encoded_name)
                if state.name_bytes > MAX_PROVIDER_STREAM_IDENTIFIER_BYTES:
                    raise _invalid_response(route, "provider stream tool name was too large")
                state.name_parts.append(name)
            arguments = getattr(function, "arguments", None)
            if arguments is not None:
                if not isinstance(arguments, str):
                    raise _invalid_response(route, "provider stream tool arguments were malformed")
                try:
                    encoded_arguments = arguments.encode("utf-8")
                except UnicodeEncodeError:
                    raise _invalid_response(
                        route, "provider stream tool arguments were malformed"
                    ) from None
                state.argument_bytes += len(encoded_arguments)
                total_argument_bytes += len(encoded_arguments)
                if (
                    state.argument_bytes > MAX_PROVIDER_STREAM_ARGUMENT_BYTES
                    or total_argument_bytes > MAX_PROVIDER_STREAM_ARGUMENT_BYTES
                ):
                    raise _invalid_response(route, "provider stream tool arguments were too large")
                state.argument_parts.append(arguments)

        current_finish = getattr(choice, "finish_reason", None)
        if current_finish is not None:
            if not isinstance(current_finish, str):
                raise _invalid_response(route, "provider stream finish reason was malformed")
            finish_reason = current_finish

    if not saw_chunk or finish_reason is None:
        raise _invalid_response(route, "provider stream ended before a finish reason")
    if finish_reason in {"length", "max_tokens"}:
        raise _output_limit_response(
            route,
            "provider response reached the configured output-token limit",
            requested_output_tokens=route.max_output_tokens,
            usage=stream_usage,
            partial_response_observed=bool(text_parts or tool_states),
        )
    if finish_reason in {"content_filter", "refusal"}:
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="provider refused or filtered the request",
        )
    if usage_sink is not None and stream_usage is not None:
        usage_sink(stream_usage)

    text = "".join(text_parts)
    has_tool_fragments = bool(tool_states)
    if finish_reason == "stop":
        if has_tool_fragments:
            raise _invalid_response(route, "text stream unexpectedly contained a tool call")
        if not text:
            raise _invalid_response(route, "provider text response was empty or malformed")
        return AssistantText(text)
    if finish_reason != "tool_calls" or not has_tool_fragments:
        raise _invalid_response(route, "provider stream used an unsupported finish reason")
    expected_indexes = list(range(len(tool_states)))
    if sorted(tool_states) != expected_indexes:
        raise _invalid_response(route, "provider stream tool-call indexes were not contiguous")
    requests: list[ToolUse] = []
    seen_ids: set[str] = set()
    for call_index in expected_indexes:
        state = tool_states[call_index]
        if state.tool_use_id is None:
            raise _invalid_response(route, "provider stream tool-call ID was missing")
        if state.tool_use_id in seen_ids:
            raise _invalid_response(route, "provider stream tool-call ID was duplicated")
        seen_ids.add(state.tool_use_id)
        name = "".join(state.name_parts)
        if not name:
            raise _invalid_response(route, "provider stream tool name was missing")
        try:
            tool_input = json.loads("".join(state.argument_parts))
        except json.JSONDecodeError:
            raise _invalid_response(route, f"provider {name} arguments were malformed") from None
        if not isinstance(tool_input, dict):
            raise _invalid_response(route, f"provider {name} arguments were malformed")
        try:
            requests.append(tool_use_from_provider_input(state.tool_use_id, name, tool_input))
        except ValueError:
            raise _invalid_response(route, f"provider {name} arguments were malformed") from None
    if len(requests) == 1:
        request = requests[0]
        if not text:
            return request
        try:
            return ToolUse(
                request.tool_use_id,
                request.name,
                request.arguments,
                assistant_text=text,
            )
        except ValueError:
            raise _invalid_response(route, "provider assistant tool text was malformed") from None
    try:
        return AssistantToolBatch(tuple(requests), text or None)
    except ValueError:
        raise _invalid_response(route, "provider tool batch was malformed") from None


def _close_stream(stream: object | None) -> None:
    if stream is None:
        return
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _parse_compatible_usage(usage: object) -> ProviderTokenUsage | None:
    return parse_provider_usage(
        usage,
        input_field="prompt_tokens",
        output_field="completion_tokens",
    )


def token_limit_field(model: str) -> str:
    """Select the documented token-limit field for compatible reasoning families."""
    base = model.rsplit("/", 1)[-1].lower()
    return "max_completion_tokens" if base.startswith("gpt-5") else "max_tokens"


def fixed_sampling_model(model: str) -> bool:
    """Return whether known reasoning families reject tuning controls."""
    base = model.rsplit("/", 1)[-1].lower()
    return base.startswith(("o1", "o3", "o4", "gpt-5")) or "thinking" in base


def normalize_sdk_error(
    error: openai.APIError,
    *,
    route: RuntimeProviderRoute,
) -> ProviderAdapterError:
    """Map official SDK failures without retaining raw response or credential data."""
    provider_id = route.definition.provider_id
    model_id = route.selected_model
    if isinstance(error, openai.APITimeoutError):
        return adapter_error(
            provider_id=provider_id,
            model_id=model_id,
            kind=ProviderFailureKind.TIMEOUT,
            code="request_timeout",
            message=f"{provider_id} request timed out",
            retryable=True,
        )
    if isinstance(error, openai.APIConnectionError):
        return adapter_error(
            provider_id=provider_id,
            model_id=model_id,
            kind=ProviderFailureKind.TRANSPORT,
            code="connection_failed",
            message=f"could not connect to {provider_id}",
            retryable=True,
        )
    upstream = extract_upstream_error_metadata(error)
    status = upstream.http_status_code
    request_id = upstream.request_id
    retry_after = upstream.retry_after_seconds
    if isinstance(error, openai.AuthenticationError) or status == 401:
        kind, code, message, retryable = (
            ProviderFailureKind.AUTHENTICATION,
            "authentication_failed",
            f"{provider_id} rejected the API credential",
            False,
        )
    elif isinstance(error, openai.PermissionDeniedError) or status == 403:
        kind, code, message, retryable = (
            ProviderFailureKind.AUTHORIZATION,
            "permission_denied",
            f"{provider_id} denied access to the requested resource",
            False,
        )
    elif isinstance(error, openai.NotFoundError) or status == 404:
        kind, code, message, retryable = (
            ProviderFailureKind.MODEL_UNAVAILABLE,
            "model_unavailable",
            f"the requested {provider_id} model is unavailable",
            False,
        )
    elif isinstance(error, openai.RateLimitError) or status == 429:
        kind, code, message, retryable = (
            ProviderFailureKind.RATE_LIMITED,
            "rate_limited",
            f"{provider_id} rate-limited the request",
            True,
        )
    elif isinstance(error, openai.BadRequestError) or status in {400, 413, 422}:
        kind, code, message, retryable = (
            ProviderFailureKind.INVALID_REQUEST,
            "invalid_request",
            f"{provider_id} rejected the request as invalid",
            False,
        )
    elif isinstance(error, openai.InternalServerError) or (
        isinstance(status, int) and status >= 500
    ):
        kind, code, message, retryable = (
            ProviderFailureKind.PROVIDER_UNAVAILABLE,
            "provider_unavailable",
            f"{provider_id} is temporarily unavailable",
            True,
        )
    else:
        kind, code, message, retryable = (
            ProviderFailureKind.TRANSPORT,
            "sdk_failure",
            f"the {provider_id} SDK could not complete the request",
            False,
        )
    return adapter_error(
        provider_id=provider_id,
        model_id=model_id,
        kind=kind,
        code=code,
        message=message,
        retryable=retryable,
        retry_after_seconds=retry_after,
        request_id=request_id,
        http_status_code=upstream.http_status_code,
        upstream_error_code=upstream.upstream_error_code,
        upstream_error_type=upstream.upstream_error_type,
        upstream_message=upstream.upstream_message,
    )


def _validate_request_size(route: RuntimeProviderRoute, request: dict[str, object]) -> None:
    encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > route.definition.request_body_limit:
        raise adapter_error(
            provider_id=route.definition.provider_id,
            model_id=route.selected_model,
            kind=ProviderFailureKind.INVALID_REQUEST,
            code="request_body_too_large",
            message="provider request exceeds the configured body-size limit",
        )


def _invalid_history(route: RuntimeProviderRoute, message: str) -> ProviderAdapterError:
    return adapter_error(
        provider_id=route.definition.provider_id,
        model_id=route.selected_model,
        kind=ProviderFailureKind.INVALID_REQUEST,
        code="invalid_history",
        message=message,
    )


def _invalid_response(route: RuntimeProviderRoute, message: str) -> ProviderAdapterError:
    return adapter_error(
        provider_id=route.definition.provider_id,
        model_id=route.selected_model,
        kind=ProviderFailureKind.RESPONSE_INVALID,
        code="response_invalid",
        message=message,
    )


def _output_limit_response(
    route: RuntimeProviderRoute,
    message: str,
    *,
    requested_output_tokens: int,
    usage: ProviderTokenUsage | None,
    partial_response_observed: bool,
) -> ProviderAdapterError:
    return output_limit_error(
        provider_id=route.definition.provider_id,
        model_id=route.selected_model,
        message=message,
        requested_output_tokens=requested_output_tokens,
        usage=usage,
        partial_response_observed=partial_response_observed,
    )


def _choice_has_partial_response(choice: object) -> bool:
    message = getattr(choice, "message", None)
    if message is None:
        return False
    content = getattr(message, "content", None)
    tool_calls = getattr(message, "tool_calls", None)
    return bool(isinstance(content, str) and content) or bool(tool_calls)
