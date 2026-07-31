"""Explicit Anthropic Messages adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from collections.abc import Callable
from typing import Protocol

import anthropic

from leonervis_code.core.compaction import CompactSummaryRequest, EffectiveContextSummary
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    ConversationItem,
    ConversationRequest,
    ProviderResponse,
    ToolResult,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.core.project_instructions import render_project_instructions
from leonervis_code.core.session_title import SessionTitleRequest
from leonervis_code.providers.errors import (
    ProviderAdapterError,
    adapter_error,
    output_limit_error,
    safe_request_id,
    safe_retry_after,
)
from leonervis_code.providers.model_context import (
    OFFICIAL_ANTHROPIC_BASE_URL,
    ModelContextDiscovery,
)
from leonervis_code.providers.request_context import (
    MAX_REQUEST_INPUT_TOKENS,
    RequestTokenCount,
    RequestTokenCountMethod,
    estimate_serialized_input_tokens,
)
from leonervis_code.providers.streaming import (
    MAX_PROVIDER_STREAM_ARGUMENT_BYTES,
    MAX_PROVIDER_STREAM_EVENTS,
    MAX_PROVIDER_STREAM_IDENTIFIER_BYTES,
    MAX_PROVIDER_STREAM_TEXT_BYTES,
    MAX_PROVIDER_STREAM_TEXT_CHARACTERS,
    ProviderTextDelta,
    ProviderTextDeltaSink,
    ProviderResponseOutcome,
)
from leonervis_code.providers.usage import (
    MAX_PROVIDER_USAGE_TOKENS,
    ProviderTokenUsage,
    parse_provider_usage,
)
from leonervis_code.tools.catalog import (
    MAX_TOOL_CALLS_PER_RESPONSE,
    model_tool_definitions,
    tool_input_from_use,
    tool_use_from_input,
)

PROVIDER_ID = "anthropic"
DEFAULT_MAX_OUTPUT_TOKENS = 1024


@dataclass(frozen=True)
class AnthropicProviderConfig:
    """Non-secret invocation settings for one explicit Anthropic adapter."""

    model_id: str
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    base_url: str = "https://api.anthropic.com"
    temperature: float | None = None

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("Anthropic model ID must not be blank")
        if self.max_output_tokens < 1:
            raise ValueError("Anthropic max output tokens must be at least 1")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValueError("Anthropic temperature must be between 0.0 and 2.0")


class AnthropicModelsClient(Protocol):
    """The narrow synchronous Models API operation used for discovery."""

    def retrieve(self, model_id: str, **kwargs: object) -> object:
        """Retrieve metadata for one exact Anthropic model."""


class AnthropicMessagesClient(Protocol):
    """The narrow synchronous SDK operation used by the adapter."""

    def count_tokens(self, **kwargs: object) -> object:
        """Count input tokens for one Anthropic Messages projection."""

    def create(self, **kwargs: object) -> object:
        """Create one non-streaming Anthropic message."""


def create_anthropic_provider(
    config: AnthropicProviderConfig,
    *,
    api_key: str,
) -> AnthropicConversationProvider:
    """Construct the official synchronous SDK client at the credential boundary."""
    if not api_key.strip():
        raise _adapter_error(
            config,
            kind=ProviderFailureKind.AUTHENTICATION,
            code="missing_api_key",
            message="ANTHROPIC_API_KEY is not configured",
        )
    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=config.base_url,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(follow_redirects=False),
    )
    return AnthropicConversationProvider(
        config,
        client.messages,
        models_client=getattr(client, "models", None),
        owner=client,
    )


class AnthropicConversationProvider:
    """Serialize neutral causal history and decode one Anthropic response."""

    def __init__(
        self,
        config: AnthropicProviderConfig,
        client: AnthropicMessagesClient,
        *,
        models_client: AnthropicModelsClient | None = None,
        owner: object | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._models_client = models_client
        self._owner = owner

    def close(self) -> None:
        """Close the production SDK owner when this adapter constructed it."""
        close = getattr(self._owner, "close", None)
        if callable(close):
            close()

    def count_input_tokens(self, request_snapshot: ConversationRequest) -> RequestTokenCount:
        """Count official Anthropic input exactly, falling back to a safe estimate."""
        projection = build_input_projection(
            self._config,
            request_snapshot,
            committed_context=True,
        )
        if self._config.base_url.rstrip("/") != OFFICIAL_ANTHROPIC_BASE_URL:
            return estimate_serialized_input_tokens(projection)
        try:
            result = self._client.count_tokens(**projection)
            input_tokens = getattr(result, "input_tokens", None)
            if type(input_tokens) is not int or not (0 <= input_tokens <= MAX_REQUEST_INPUT_TOKENS):
                raise ValueError
            return RequestTokenCount(input_tokens, RequestTokenCountMethod.EXACT)
        except Exception:
            estimated = estimate_serialized_input_tokens(projection)
            return RequestTokenCount(
                estimated.input_tokens,
                RequestTokenCountMethod.ESTIMATED,
                "Anthropic token counting failed safely; used serialized estimate",
            )

    def count_compact_summary_input_tokens(
        self, request_snapshot: CompactSummaryRequest
    ) -> RequestTokenCount:
        """Count the exact no-tools compact-summary input projection."""
        projection = build_compact_summary_input_projection(self._config, request_snapshot)
        if self._config.base_url.rstrip("/") != OFFICIAL_ANTHROPIC_BASE_URL:
            return estimate_serialized_input_tokens(projection)
        try:
            result = self._client.count_tokens(**projection)
            input_tokens = getattr(result, "input_tokens", None)
            if type(input_tokens) is not int or not (0 <= input_tokens <= MAX_REQUEST_INPUT_TOKENS):
                raise ValueError
            return RequestTokenCount(input_tokens, RequestTokenCountMethod.EXACT)
        except Exception:
            estimated = estimate_serialized_input_tokens(projection)
            return RequestTokenCount(
                estimated.input_tokens,
                RequestTokenCountMethod.ESTIMATED,
                "Anthropic compact token counting failed safely; used serialized estimate",
            )

    def summarize_compact(self, request_snapshot: CompactSummaryRequest) -> AssistantText:
        """Generate one text-only summary without exposing workspace tools."""
        return self.summarize_compact_outcome(request_snapshot).response  # type: ignore[return-value]

    def summarize_compact_outcome(
        self, request_snapshot: CompactSummaryRequest
    ) -> ProviderResponseOutcome:
        """Generate one summary and retain actual provider usage outside history."""
        request = build_compact_summary_request(self._config, request_snapshot)
        try:
            response = self._client.create(**request)
        except anthropic.APIError as error:
            raise normalize_sdk_error(error, config=self._config) from None
        usage = _parse_anthropic_usage(getattr(response, "usage", None))
        return ProviderResponseOutcome(
            parse_compact_summary_response(
                response,
                config=self._config,
                requested_output_tokens=request_snapshot.max_output_tokens,
                usage=usage,
            ),
            False,
            usage,
        )

    def count_session_title_input_tokens(
        self, request_snapshot: SessionTitleRequest
    ) -> RequestTokenCount:
        """Count the exact no-tools Session-title projection."""
        config = replace(self._config, max_output_tokens=request_snapshot.max_output_tokens)
        projection = build_input_projection(
            config,
            request_snapshot.conversation_request,
            committed_context=True,
        )
        if config.base_url.rstrip("/") != OFFICIAL_ANTHROPIC_BASE_URL:
            return estimate_serialized_input_tokens(projection)
        try:
            result = self._client.count_tokens(**projection)
            input_tokens = getattr(result, "input_tokens", None)
            if type(input_tokens) is not int or not (0 <= input_tokens <= MAX_REQUEST_INPUT_TOKENS):
                raise ValueError
            return RequestTokenCount(input_tokens, RequestTokenCountMethod.EXACT)
        except Exception:
            estimated = estimate_serialized_input_tokens(projection)
            return RequestTokenCount(
                estimated.input_tokens,
                RequestTokenCountMethod.ESTIMATED,
                "Anthropic Session-title token counting failed safely; used serialized estimate",
            )

    def generate_session_title_outcome(
        self, request_snapshot: SessionTitleRequest
    ) -> ProviderResponseOutcome:
        """Generate one no-tools Session title and retain provider usage."""
        config = replace(self._config, max_output_tokens=request_snapshot.max_output_tokens)
        request = build_request(config, request_snapshot.conversation_request)
        try:
            response = self._client.create(**request)
        except anthropic.APIError as error:
            raise normalize_sdk_error(error, config=config) from None
        usage = _parse_anthropic_usage(getattr(response, "usage", None))
        return ProviderResponseOutcome(
            parse_session_title_response(response, config=config, usage=usage),
            False,
            usage,
        )

    def respond(self, request_snapshot: ConversationRequest) -> ProviderResponse:
        """Make one non-streaming request through the injected SDK seam."""
        return self.respond_outcome(request_snapshot).response

    def respond_outcome(self, request_snapshot: ConversationRequest) -> ProviderResponseOutcome:
        """Return one response with Host-only actual token usage."""
        request = build_request(self._config, request_snapshot)
        try:
            response = self._client.create(**request)
        except anthropic.APIError as error:
            raise normalize_sdk_error(error, config=self._config) from None
        usage = _parse_anthropic_usage(getattr(response, "usage", None))
        return ProviderResponseOutcome(
            parse_response(response, config=self._config, usage=usage),
            False,
            usage,
        )

    def respond_stream(
        self,
        request_snapshot: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponse:
        """Consume one Anthropic event stream into the same neutral contract."""
        return self.respond_stream_outcome(request_snapshot, event_sink=event_sink).response

    def respond_stream_outcome(
        self,
        request_snapshot: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponseOutcome:
        """Consume one stream and retain its final Host-only usage metadata."""
        request = build_request(self._config, request_snapshot)
        request["stream"] = True
        stream = None
        captured_usage: list[ProviderTokenUsage] = []
        try:
            stream = self._client.create(**request)
            response = parse_response_stream(
                stream,
                config=self._config,
                event_sink=event_sink,
                usage_sink=captured_usage.append,
            )
            return ProviderResponseOutcome(
                response,
                True,
                captured_usage[0] if captured_usage else None,
            )
        except anthropic.APIError as error:
            raise normalize_sdk_error(error, config=self._config) from None
        finally:
            _close_stream(stream)

    def discover_model_context(self) -> ModelContextDiscovery:
        """Discover one official Anthropic model's maximum input context."""
        if (
            self._models_client is None
            or self._config.base_url.rstrip("/") != OFFICIAL_ANTHROPIC_BASE_URL
        ):
            return ModelContextDiscovery(None, "live context discovery is unsupported")
        try:
            model = self._models_client.retrieve(self._config.model_id)
        except anthropic.APIError:
            return ModelContextDiscovery(None, "Anthropic model discovery failed safely")
        model_id = getattr(model, "id", None)
        max_input_tokens = getattr(model, "max_input_tokens", None)
        max_tokens = getattr(model, "max_tokens", None)
        if model_id != self._config.model_id:
            return ModelContextDiscovery(
                None, "Anthropic model discovery returned a different model ID"
            )
        context_value = (
            max_input_tokens if type(max_input_tokens) is int and max_input_tokens > 0 else None
        )
        output_value = max_tokens if type(max_tokens) is int and max_tokens > 0 else None
        diagnostic = None
        if context_value is None or output_value is None:
            diagnostic = "Anthropic model discovery returned an incomplete limit set"
        return ModelContextDiscovery(context_value, diagnostic, output_value)


def build_input_projection(
    config: AnthropicProviderConfig,
    request_snapshot: ConversationRequest,
    *,
    committed_context: bool = False,
) -> dict[str, object]:
    """Build the Anthropic fields that contribute provider input tokens."""
    system: object = request_snapshot.system_prompt.text
    if request_snapshot.project_instructions is not None:
        system = [
            {"type": "text", "text": request_snapshot.system_prompt.text},
            {
                "type": "text",
                "text": render_project_instructions(request_snapshot.project_instructions),
            },
        ]
    projection: dict[str, object] = {
        "model": config.model_id,
        "system": system,
        "messages": [
            *_serialize_effective_summary(request_snapshot.effective_summary),
            *serialize_history(
                request_snapshot.history,
                config=config,
                committed_context=committed_context,
            ),
        ],
    }
    if request_snapshot.allow_tools:
        projection["tools"] = list(model_tool_definitions(request_snapshot.enabled_tool_names))
        projection["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    return projection


def build_request(
    config: AnthropicProviderConfig,
    request_snapshot: ConversationRequest,
) -> dict[str, object]:
    """Build one complete Anthropic Messages request deterministically."""
    request: dict[str, object] = {
        **build_input_projection(config, request_snapshot),
        "max_tokens": config.max_output_tokens,
        "stream": False,
    }
    if config.temperature is not None:
        request["temperature"] = config.temperature
    return request


def build_compact_summary_input_projection(
    config: AnthropicProviderConfig,
    request_snapshot: CompactSummaryRequest,
) -> dict[str, object]:
    """Build the no-tools Anthropic input projection for controlled summary."""
    return {
        "model": config.model_id,
        "system": request_snapshot.prompt.text,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": request_snapshot.source_text}],
            }
        ],
    }


def build_compact_summary_request(
    config: AnthropicProviderConfig,
    request_snapshot: CompactSummaryRequest,
) -> dict[str, object]:
    """Build one complete no-tools Anthropic summary request."""
    request: dict[str, object] = {
        **build_compact_summary_input_projection(config, request_snapshot),
        "max_tokens": request_snapshot.max_output_tokens,
        "stream": False,
    }
    if config.temperature is not None:
        request["temperature"] = config.temperature
    return request


def read_file_tool_definition() -> dict[str, object]:
    """Retain the tested Anthropic wrapper for the canonical read contract."""
    return model_tool_definitions()[0]


def glob_tool_definition() -> dict[str, object]:
    """Return the canonical Anthropic glob contract."""
    return model_tool_definitions()[1]


def grep_tool_definition() -> dict[str, object]:
    """Return the canonical Anthropic grep contract."""
    return model_tool_definitions()[2]


def write_file_tool_definition() -> dict[str, object]:
    """Return the canonical Anthropic controlled write contract."""
    return model_tool_definitions()[3]


def edit_file_tool_definition() -> dict[str, object]:
    """Return the canonical Anthropic controlled exact-edit contract."""
    return model_tool_definitions()[4]


def run_command_tool_definition() -> dict[str, object]:
    """Return the canonical Anthropic controlled command contract."""
    return model_tool_definitions()[5]


def mkdir_tool_definition() -> dict[str, object]:
    """Return the canonical Anthropic controlled directory contract."""
    return model_tool_definitions()[6]


def move_file_tool_definition() -> dict[str, object]:
    """Return the canonical move_file definition used by Anthropic requests."""
    return model_tool_definitions()[7]


def delete_file_tool_definition() -> dict[str, object]:
    """Return the canonical delete_file definition used by Anthropic requests."""
    return model_tool_definitions()[8]


def delete_directory_tool_definition() -> dict[str, object]:
    """Return the canonical delete_directory definition used by Anthropic requests."""
    return model_tool_definitions()[9]


def list_directory_tool_definition() -> dict[str, object]:
    """Return the canonical list_directory definition used by Anthropic requests."""
    return model_tool_definitions()[10]


def copy_file_tool_definition() -> dict[str, object]:
    """Return the canonical copy_file definition used by Anthropic requests."""
    return model_tool_definitions()[11]


def read_file_lines_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[12]


def stat_path_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[13]


def list_tree_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[14]


def grep_regex_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[15]


def patch_file_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[16]


def git_status_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[17]


def git_diff_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[18]


def git_log_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[19]


def git_show_tool_definition() -> dict[str, object]:
    return model_tool_definitions()[20]


def serialize_history(
    history: tuple[ConversationItem, ...],
    *,
    config: AnthropicProviderConfig,
    committed_context: bool = False,
) -> list[dict[str, object]]:
    """Convert neutral causal history for invocation or committed-context counting."""
    if not history:
        if committed_context:
            return []
        raise _invalid_history(config, "conversation history must not be empty")

    messages: list[dict[str, object]] = []
    expected = "user"
    pending_tool_use_ids: tuple[str, ...] = ()
    pending_result_blocks: list[dict[str, object]] = []

    for item in history:
        if isinstance(item, UserMessage):
            if expected != "user" or not isinstance(item.text, str):
                raise _invalid_history(config, "user message is out of causal order")
            messages.append({"role": "user", "content": [{"type": "text", "text": item.text}]})
            expected = "assistant"
            continue

        if isinstance(item, AssistantText):
            if expected != "assistant" or not isinstance(item.text, str):
                raise _invalid_history(config, "assistant text is out of causal order")
            messages.append({"role": "assistant", "content": [{"type": "text", "text": item.text}]})
            expected = "user"
            continue

        if isinstance(item, ToolUse):
            if expected != "assistant":
                raise _invalid_history(config, "tool use is out of causal order")
            try:
                tool_input = tool_input_from_use(item)
            except ValueError:
                raise _invalid_history(
                    config, f"unsupported tool in history: {item.name}"
                ) from None
            if not isinstance(item.tool_use_id, str) or not item.tool_use_id:
                raise _invalid_history(config, "tool use ID must not be blank")
            content: list[dict[str, object]] = []
            if item.assistant_text is not None:
                try:
                    ToolUse(
                        item.tool_use_id,
                        item.name,
                        item.arguments,
                        assistant_text=item.assistant_text,
                    )
                except ValueError:
                    raise _invalid_history(config, "assistant tool text is malformed") from None
                content.append({"type": "text", "text": item.assistant_text})
            content.append(
                {
                    "type": "tool_use",
                    "id": item.tool_use_id,
                    "name": item.name,
                    "input": tool_input,
                }
            )
            messages.append({"role": "assistant", "content": content})
            pending_tool_use_ids = (item.tool_use_id,)
            expected = "tool_result"
            continue

        if isinstance(item, AssistantToolBatch):
            if expected != "assistant":
                raise _invalid_history(config, "tool batch is out of causal order")
            content: list[dict[str, object]] = []
            if item.assistant_text is not None:
                content.append({"type": "text", "text": item.assistant_text})
            for request in item.tool_uses:
                try:
                    tool_input = tool_input_from_use(request)
                except ValueError:
                    raise _invalid_history(
                        config, f"unsupported tool in history: {request.name}"
                    ) from None
                content.append(
                    {
                        "type": "tool_use",
                        "id": request.tool_use_id,
                        "name": request.name,
                        "input": tool_input,
                    }
                )
            messages.append({"role": "assistant", "content": content})
            pending_tool_use_ids = tuple(request.tool_use_id for request in item.tool_uses)
            pending_result_blocks = []
            expected = "tool_result"
            continue

        if isinstance(item, ToolResult):
            if expected != "tool_result" or not pending_tool_use_ids:
                raise _invalid_history(config, "tool result does not match the pending tool use")
            if item.tool_use_id != pending_tool_use_ids[0]:
                raise _invalid_history(config, "tool result does not match the pending tool use")
            if not isinstance(item.content, str):
                raise _invalid_history(config, "tool result content must be text")
            pending_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": item.tool_use_id,
                    "content": item.content,
                    "is_error": item.is_error,
                }
            )
            pending_tool_use_ids = pending_tool_use_ids[1:]
            if pending_tool_use_ids:
                expected = "tool_result"
            else:
                messages.append({"role": "user", "content": pending_result_blocks})
                pending_result_blocks = []
                expected = "assistant"
            continue

        raise _invalid_history(config, "conversation history contains an unknown item")

    valid_terminal_states = {"assistant"}
    if committed_context:
        valid_terminal_states.add("user")
    if expected not in valid_terminal_states:
        message = (
            "committed conversation history must end with assistant text"
            if committed_context
            else "conversation history must end before an assistant response"
        )
        raise _invalid_history(config, message)
    return messages


def _serialize_effective_summary(
    summary: EffectiveContextSummary | None,
) -> list[dict[str, object]]:
    if summary is None:
        return []
    return [
        {"role": "user", "content": [{"type": "text", "text": summary.user_text}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": summary.assistant_acknowledgement}],
        },
    ]


def parse_compact_summary_response(
    response: object,
    *,
    config: AnthropicProviderConfig,
    requested_output_tokens: int | None = None,
    usage: ProviderTokenUsage | None = None,
) -> AssistantText:
    """Decode only a normally completed text-only compact summary."""
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise _adapter_error(
            config,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="Anthropic refused the compact summary request",
        )
    if stop_reason == "max_tokens":
        raise _output_limit_response(
            config,
            "Anthropic compact summary reached the configured output-token limit",
            requested_output_tokens=(
                requested_output_tokens
                if requested_output_tokens is not None
                else config.max_output_tokens
            ),
            usage=usage,
            partial_response_observed=_message_has_partial_response(response),
        )
    if stop_reason != "end_turn":
        raise _invalid_response(config, "Anthropic compact summary used an unsupported stop reason")
    content = getattr(response, "content", None)
    if not isinstance(content, list) or not content:
        raise _invalid_response(config, "Anthropic compact summary contained no content blocks")
    text_parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "text":
            raise _invalid_response(config, "Anthropic compact summary contained a non-text block")
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            raise _invalid_response(config, "Anthropic compact summary text was malformed")
        text_parts.append(text)
    text = "".join(text_parts).strip()
    if not text:
        raise _invalid_response(config, "Anthropic compact summary was empty")
    return AssistantText(text)


def parse_session_title_response(
    response: object,
    *,
    config: AnthropicProviderConfig,
    usage: ProviderTokenUsage | None = None,
) -> AssistantText:
    """Decode only one normally completed text-only Session title."""
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise _adapter_error(
            config,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="Anthropic refused the Session title request",
        )
    if stop_reason == "max_tokens":
        raise _output_limit_response(
            config,
            "Anthropic Session title reached the configured output-token limit",
            requested_output_tokens=config.max_output_tokens,
            usage=usage,
            partial_response_observed=_message_has_partial_response(response),
        )
    if stop_reason != "end_turn":
        raise _invalid_response(config, "Anthropic Session title used an unsupported stop reason")
    content = getattr(response, "content", None)
    if not isinstance(content, list) or not content:
        raise _invalid_response(config, "Anthropic Session title contained no content blocks")
    text_parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "text":
            raise _invalid_response(config, "Anthropic Session title contained a non-text block")
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            raise _invalid_response(config, "Anthropic Session title text was malformed")
        text_parts.append(text)
    text = "".join(text_parts).strip()
    if not text:
        raise _invalid_response(config, "Anthropic Session title was empty")
    return AssistantText(text)


def parse_response(
    response: object,
    *,
    config: AnthropicProviderConfig,
    usage: ProviderTokenUsage | None = None,
) -> ProviderResponse:
    """Decode complete text or one bounded ordered tool-use batch."""
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise _adapter_error(
            config,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="Anthropic refused the request",
        )
    if stop_reason == "max_tokens":
        raise _output_limit_response(
            config,
            "Anthropic response reached the configured output-token limit",
            requested_output_tokens=config.max_output_tokens,
            usage=usage,
            partial_response_observed=_message_has_partial_response(response),
        )
    if stop_reason not in {"end_turn", "tool_use"}:
        raise _invalid_response(config, "Anthropic response used an unsupported stop reason")

    content = getattr(response, "content", None)
    if not isinstance(content, list) or not content:
        raise _invalid_response(config, "Anthropic response contained no content blocks")

    text_parts: list[str] = []
    tool_blocks: list[object] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", None)
            if not isinstance(text, str):
                raise _invalid_response(config, "Anthropic text block was malformed")
            text_parts.append(text)
        elif block_type == "tool_use":
            tool_blocks.append(block)
        else:
            raise _invalid_response(config, "Anthropic response contained an unsupported block")

    if not tool_blocks:
        if stop_reason != "end_turn":
            raise _invalid_response(config, "text response did not end with end_turn")
        return AssistantText(text="".join(text_parts))
    if stop_reason != "tool_use":
        raise _invalid_response(config, "tool response did not end with tool_use")
    if len(tool_blocks) > MAX_TOOL_CALLS_PER_RESPONSE:
        raise _invalid_response(config, "Anthropic response exceeded the per-response call limit")

    requests: list[ToolUse] = []
    seen_ids: set[str] = set()
    for block in tool_blocks:
        tool_use_id = getattr(block, "id", None)
        name = getattr(block, "name", None)
        tool_input = getattr(block, "input", None)
        if not isinstance(tool_use_id, str) or not tool_use_id:
            raise _invalid_response(config, "Anthropic tool use ID was malformed")
        if tool_use_id in seen_ids:
            raise _invalid_response(config, "Anthropic tool use ID was duplicated")
        seen_ids.add(tool_use_id)
        if not isinstance(name, str):
            raise _invalid_response(config, "Anthropic requested an unsupported tool")
        if not isinstance(tool_input, dict):
            raise _invalid_response(config, f"Anthropic {name} input was malformed")
        try:
            requests.append(tool_use_from_input(tool_use_id, name, tool_input))
        except ValueError:
            raise _invalid_response(config, f"Anthropic {name} input was malformed") from None
    joined_text = "".join(text_parts)
    if text_parts and not joined_text:
        raise _invalid_response(config, "Anthropic assistant tool text was malformed")
    text = joined_text or None
    if len(requests) > 1:
        try:
            return AssistantToolBatch(tuple(requests), text)
        except ValueError:
            raise _invalid_response(config, "Anthropic tool batch was malformed") from None
    request = requests[0]
    if text is None:
        return request
    try:
        return ToolUse(
            request.tool_use_id,
            request.name,
            request.arguments,
            assistant_text=text,
        )
    except ValueError:
        raise _invalid_response(config, "Anthropic assistant tool text was malformed") from None


@dataclass
class _AnthropicStreamBlock:
    block_type: str
    text_parts: list[str]
    tool_use_id: str | None = None
    tool_name: str | None = None
    initial_input: dict[str, object] | None = None
    input_parts: list[str] | None = None


def parse_response_stream(
    events: object,
    *,
    config: AnthropicProviderConfig,
    event_sink: ProviderTextDeltaSink,
    usage_sink: Callable[[ProviderTokenUsage], None] | None = None,
) -> ProviderResponse:
    """Assemble one strict sequential response from Anthropic message events."""
    try:
        iterator = iter(events)  # type: ignore[arg-type]
    except TypeError:
        raise _invalid_response(config, "Anthropic response stream was not iterable") from None

    blocks: dict[int, _AnthropicStreamBlock] = {}
    completed_blocks: list[_AnthropicStreamBlock] = []
    expected_index = 0
    started = False
    stopped = False
    stop_reason: str | None = None
    argument_bytes = 0
    text_characters = 0
    text_bytes = 0
    input_tokens: int | None = None
    output_tokens: int | None = None

    def emit_text(text: str) -> None:
        nonlocal text_characters, text_bytes
        try:
            encoded_text = text.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid_response(config, "Anthropic stream text delta was malformed") from None
        text_characters += len(text)
        text_bytes += len(encoded_text)
        if (
            text_characters > MAX_PROVIDER_STREAM_TEXT_CHARACTERS
            or text_bytes > MAX_PROVIDER_STREAM_TEXT_BYTES
        ):
            raise _invalid_response(config, "Anthropic stream text was too large")
        try:
            event = ProviderTextDelta(text)
        except ValueError:
            raise _invalid_response(config, "Anthropic stream text delta was malformed") from None
        event_sink(event)

    for event_index, event in enumerate(iterator, start=1):
        if event_index > MAX_PROVIDER_STREAM_EVENTS:
            raise _invalid_response(config, "Anthropic stream contained too many events")
        if stopped:
            raise _invalid_response(config, "Anthropic stream continued after message_stop")
        event_type = getattr(event, "type", None)
        if event_type == "message_start":
            if started:
                raise _invalid_response(config, "Anthropic stream repeated message_start")
            message = getattr(event, "message", None)
            if message is None or getattr(message, "role", None) != "assistant":
                raise _invalid_response(config, "Anthropic stream message_start was malformed")
            input_tokens = _usage_value(
                getattr(getattr(message, "usage", None), "input_tokens", None)
            )
            started = True
            continue
        if not started:
            raise _invalid_response(config, "Anthropic stream event preceded message_start")
        if stop_reason is not None and event_type != "message_stop":
            raise _invalid_response(config, "Anthropic stream continued after its stop reason")
        if event_type == "content_block_start":
            if blocks:
                raise _invalid_response(config, "Anthropic stream content blocks overlapped")
            index = getattr(event, "index", None)
            if type(index) is not int or index != expected_index or index in blocks:
                raise _invalid_response(config, "Anthropic stream content-block index was invalid")
            block = getattr(event, "content_block", None)
            block_type = getattr(block, "type", None)
            if block_type == "text":
                initial_text = getattr(block, "text", "")
                if not isinstance(initial_text, str):
                    raise _invalid_response(config, "Anthropic stream text block was malformed")
                state = _AnthropicStreamBlock("text", [])
                if initial_text:
                    state.text_parts.append(initial_text)
                    emit_text(initial_text)
            elif block_type == "tool_use":
                tool_use_id = getattr(block, "id", None)
                tool_name = getattr(block, "name", None)
                initial_input = getattr(block, "input", None)
                if (
                    not isinstance(tool_use_id, str)
                    or not tool_use_id
                    or not isinstance(tool_name, str)
                    or not tool_name
                    or not isinstance(initial_input, dict)
                ):
                    raise _invalid_response(config, "Anthropic stream tool block was malformed")
                try:
                    encoded_tool_id = tool_use_id.encode("utf-8")
                    encoded_tool_name = tool_name.encode("utf-8")
                except UnicodeEncodeError:
                    raise _invalid_response(
                        config, "Anthropic stream tool block was malformed"
                    ) from None
                if (
                    len(encoded_tool_id) > MAX_PROVIDER_STREAM_IDENTIFIER_BYTES
                    or len(encoded_tool_name) > MAX_PROVIDER_STREAM_IDENTIFIER_BYTES
                ):
                    raise _invalid_response(config, "Anthropic stream tool block was too large")
                state = _AnthropicStreamBlock(
                    "tool_use",
                    [],
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    initial_input=initial_input,
                    input_parts=[],
                )
            else:
                raise _invalid_response(config, "Anthropic stream block type was unsupported")
            blocks[index] = state
            expected_index += 1
            continue
        if event_type == "content_block_delta":
            index = getattr(event, "index", None)
            state = blocks.get(index)
            if state is None:
                raise _invalid_response(config, "Anthropic stream delta had no active block")
            delta = getattr(event, "delta", None)
            delta_type = getattr(delta, "type", None)
            if delta_type == "text_delta" and state.block_type == "text":
                text = getattr(delta, "text", None)
                if not isinstance(text, str):
                    raise _invalid_response(config, "Anthropic stream text delta was malformed")
                if text:
                    state.text_parts.append(text)
                    emit_text(text)
            elif delta_type == "input_json_delta" and state.block_type == "tool_use":
                partial_json = getattr(delta, "partial_json", None)
                if not isinstance(partial_json, str):
                    raise _invalid_response(config, "Anthropic stream input delta was malformed")
                try:
                    encoded_json = partial_json.encode("utf-8")
                except UnicodeEncodeError:
                    raise _invalid_response(
                        config, "Anthropic stream input delta was malformed"
                    ) from None
                argument_bytes += len(encoded_json)
                if argument_bytes > MAX_PROVIDER_STREAM_ARGUMENT_BYTES:
                    raise _invalid_response(config, "Anthropic stream tool input was too large")
                assert state.input_parts is not None
                state.input_parts.append(partial_json)
            else:
                raise _invalid_response(config, "Anthropic stream delta type was unsupported")
            continue
        if event_type == "content_block_stop":
            index = getattr(event, "index", None)
            state = blocks.pop(index, None)
            if state is None:
                raise _invalid_response(config, "Anthropic stream stopped an unknown block")
            completed_blocks.append(state)
            continue
        if event_type == "message_delta":
            if blocks:
                raise _invalid_response(config, "Anthropic stream ended before a block stopped")
            delta = getattr(event, "delta", None)
            value = getattr(delta, "stop_reason", None)
            if not isinstance(value, str) or stop_reason is not None:
                raise _invalid_response(config, "Anthropic stream stop reason was malformed")
            output_tokens = _usage_value(
                getattr(getattr(event, "usage", None), "output_tokens", None)
            )
            stop_reason = value
            continue
        if event_type == "message_stop":
            if blocks or stop_reason is None:
                raise _invalid_response(config, "Anthropic stream stopped before completion")
            stopped = True
            continue
        raise _invalid_response(config, "Anthropic stream event type was unsupported")

    if not started or not stopped or stop_reason is None:
        raise _invalid_response(config, "Anthropic stream ended before message_stop")
    if stop_reason == "max_tokens":
        usage = (
            ProviderTokenUsage(input_tokens, output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None
        )
        raise _output_limit_response(
            config,
            "Anthropic response reached the configured output-token limit",
            requested_output_tokens=config.max_output_tokens,
            usage=usage,
            partial_response_observed=(
                text_characters > 0
                or any(block.block_type == "tool_use" for block in completed_blocks)
            ),
        )
    if stop_reason == "refusal":
        raise _adapter_error(
            config,
            kind=ProviderFailureKind.CONTENT_REFUSAL,
            code="content_refusal",
            message="Anthropic refused the request",
        )
    if usage_sink is not None and input_tokens is not None and output_tokens is not None:
        usage_sink(ProviderTokenUsage(input_tokens, output_tokens))

    text = "".join(
        part
        for block in completed_blocks
        if block.block_type == "text"
        for part in block.text_parts
    )
    tools = [block for block in completed_blocks if block.block_type == "tool_use"]
    if not tools:
        if stop_reason != "end_turn" or not text:
            raise _invalid_response(config, "Anthropic text stream did not end correctly")
        return AssistantText(text)
    if stop_reason != "tool_use":
        raise _invalid_response(config, "Anthropic tool stream did not end correctly")
    if len(tools) > MAX_TOOL_CALLS_PER_RESPONSE:
        raise _invalid_response(config, "Anthropic stream exceeded the per-response call limit")

    requests: list[ToolUse] = []
    seen_ids: set[str] = set()
    for tool in tools:
        assert tool.tool_use_id is not None and tool.tool_name is not None
        assert tool.initial_input is not None and tool.input_parts is not None
        if tool.tool_use_id in seen_ids:
            raise _invalid_response(config, "Anthropic stream tool use ID was duplicated")
        seen_ids.add(tool.tool_use_id)
        if tool.input_parts:
            if tool.initial_input:
                raise _invalid_response(config, "Anthropic stream tool input was ambiguous")
            try:
                tool_input = json.loads("".join(tool.input_parts))
            except json.JSONDecodeError:
                raise _invalid_response(
                    config, "Anthropic stream tool input was malformed"
                ) from None
        else:
            tool_input = tool.initial_input
        if not isinstance(tool_input, dict):
            raise _invalid_response(config, "Anthropic stream tool input was malformed")
        try:
            requests.append(tool_use_from_input(tool.tool_use_id, tool.tool_name, tool_input))
        except ValueError:
            raise _invalid_response(
                config,
                f"Anthropic {tool.tool_name} input was malformed",
            ) from None
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
            raise _invalid_response(config, "Anthropic assistant tool text was malformed") from None
    try:
        return AssistantToolBatch(tuple(requests), text or None)
    except ValueError:
        raise _invalid_response(config, "Anthropic stream tool batch was malformed") from None


def _close_stream(stream: object | None) -> None:
    if stream is None:
        return
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _parse_anthropic_usage(usage: object) -> ProviderTokenUsage | None:
    return parse_provider_usage(
        usage,
        input_field="input_tokens",
        output_field="output_tokens",
    )


def _usage_value(value: object) -> int | None:
    if type(value) is int and 0 <= value <= MAX_PROVIDER_USAGE_TOKENS:
        return value
    return None


def normalize_sdk_error(
    error: anthropic.APIError,
    *,
    config: AnthropicProviderConfig,
) -> ProviderAdapterError:
    """Map official SDK exceptions to stable failures without raw provider data."""
    if isinstance(error, anthropic.APIResponseValidationError):
        return _adapter_error(
            config,
            kind=ProviderFailureKind.RESPONSE_INVALID,
            code="sdk_response_invalid",
            message="Anthropic returned a response the SDK could not validate",
        )
    if isinstance(error, anthropic.APITimeoutError):
        return _adapter_error(
            config,
            kind=ProviderFailureKind.TIMEOUT,
            code="request_timeout",
            message="Anthropic request timed out",
            retryable=True,
        )
    if isinstance(error, anthropic.APIConnectionError):
        return _adapter_error(
            config,
            kind=ProviderFailureKind.TRANSPORT,
            code="connection_failed",
            message="could not connect to Anthropic",
            retryable=True,
        )

    status = getattr(error, "status_code", None)
    request_id = safe_request_id(getattr(error, "request_id", None))
    retry_after = safe_retry_after(getattr(getattr(error, "response", None), "headers", None))
    if isinstance(error, anthropic.AuthenticationError) or status == 401:
        kind = ProviderFailureKind.AUTHENTICATION
        code = "authentication_failed"
        message = "Anthropic rejected the API credential"
        retryable = False
    elif isinstance(error, anthropic.PermissionDeniedError) or status == 403:
        kind = ProviderFailureKind.AUTHORIZATION
        code = "permission_denied"
        message = "Anthropic denied access to the requested resource"
        retryable = False
    elif isinstance(error, anthropic.NotFoundError) or status == 404:
        kind = ProviderFailureKind.MODEL_UNAVAILABLE
        code = "model_unavailable"
        message = "the requested Anthropic model is unavailable"
        retryable = False
    elif isinstance(error, anthropic.RateLimitError) or status == 429:
        kind = ProviderFailureKind.RATE_LIMITED
        code = "rate_limited"
        message = "Anthropic rate-limited the request"
        retryable = True
    elif isinstance(error, anthropic.BadRequestError) or status in {400, 413, 422}:
        kind = ProviderFailureKind.INVALID_REQUEST
        code = "invalid_request"
        message = "Anthropic rejected the request as invalid"
        retryable = False
    elif isinstance(error, anthropic.InternalServerError) or (
        isinstance(status, int) and status >= 500
    ):
        kind = ProviderFailureKind.PROVIDER_UNAVAILABLE
        code = "provider_unavailable"
        message = "Anthropic is temporarily unavailable"
        retryable = True
    else:
        kind = ProviderFailureKind.TRANSPORT
        code = "sdk_failure"
        message = "the Anthropic SDK could not complete the request"
        retryable = False

    return _adapter_error(
        config,
        kind=kind,
        code=code,
        message=message,
        retryable=retryable,
        retry_after_seconds=retry_after,
        request_id=request_id,
    )


def _invalid_history(config: AnthropicProviderConfig, message: str) -> ProviderAdapterError:
    return _adapter_error(
        config,
        kind=ProviderFailureKind.INVALID_REQUEST,
        code="invalid_history",
        message=message,
    )


def _invalid_response(config: AnthropicProviderConfig, message: str) -> ProviderAdapterError:
    return _adapter_error(
        config,
        kind=ProviderFailureKind.RESPONSE_INVALID,
        code="response_invalid",
        message=message,
    )


def _output_limit_response(
    config: AnthropicProviderConfig,
    message: str,
    *,
    requested_output_tokens: int,
    usage: ProviderTokenUsage | None,
    partial_response_observed: bool,
) -> ProviderAdapterError:
    return output_limit_error(
        provider_id=PROVIDER_ID,
        model_id=config.model_id,
        message=message,
        requested_output_tokens=requested_output_tokens,
        usage=usage,
        partial_response_observed=partial_response_observed,
    )


def _message_has_partial_response(response: object) -> bool:
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return False
    return any(
        (getattr(block, "type", None) == "text" and bool(getattr(block, "text", None)))
        or getattr(block, "type", None) == "tool_use"
        for block in content
    )


def _adapter_error(
    config: AnthropicProviderConfig,
    *,
    kind: ProviderFailureKind,
    code: str,
    message: str,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    request_id: str | None = None,
) -> ProviderAdapterError:
    return adapter_error(
        provider_id=PROVIDER_ID,
        model_id=config.model_id,
        kind=kind,
        code=code,
        message=message,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        request_id=request_id,
    )
