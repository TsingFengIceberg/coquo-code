"""Provider-neutral synchronous response-stream contract and compatibility helper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from leonervis_code.core.contracts import (
    AssistantText,
    ConversationProvider,
    ConversationRequest,
    ProviderResponse,
    ToolUse,
)

MAX_PROVIDER_STREAM_TEXT_CHARACTERS = 1024 * 1024
MAX_PROVIDER_STREAM_TEXT_BYTES = 1024 * 1024
MAX_PROVIDER_STREAM_ARGUMENT_BYTES = 64 * 1024
MAX_PROVIDER_STREAM_IDENTIFIER_BYTES = 4 * 1024
MAX_PROVIDER_STREAM_EVENTS = 100_000


@dataclass(frozen=True)
class ProviderTextDelta:
    """One exact non-empty provider text fragment in wire order."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("provider text delta must be non-empty text")
        try:
            encoded = self.text.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("provider text delta must be valid UTF-8") from None
        if "\x00" in self.text:
            raise ValueError("provider text delta must not contain NUL")
        if (
            len(self.text) > MAX_PROVIDER_STREAM_TEXT_CHARACTERS
            or len(encoded) > MAX_PROVIDER_STREAM_TEXT_BYTES
        ):
            raise ValueError("provider text delta exceeds the supported size")


ProviderTextDeltaSink = Callable[[ProviderTextDelta], None]


class StreamingConversationProvider(Protocol):
    """Produce one complete neutral response while reporting exact text deltas."""

    def respond_stream(
        self,
        request: ConversationRequest,
        *,
        event_sink: ProviderTextDeltaSink,
    ) -> ProviderResponse:
        """Return one validated response after synchronously consuming its stream."""


@dataclass(frozen=True)
class ProviderResponseOutcome:
    """One complete response plus whether its exact text arrived as deltas."""

    response: ProviderResponse
    text_was_streamed: bool


def respond_with_streaming(
    provider: ConversationProvider,
    request: ConversationRequest,
    *,
    event_sink: ProviderTextDeltaSink,
    prefer_stream: bool,
) -> ProviderResponseOutcome:
    """Use a provider stream when requested, otherwise preserve ``respond`` compatibility."""
    stream_method = getattr(provider, "respond_stream", None)
    streaming_supported = getattr(provider, "streaming_supported", callable(stream_method))
    if not prefer_stream or not callable(stream_method) or streaming_supported is not True:
        return ProviderResponseOutcome(provider.respond(request), False)

    text_parts: list[str] = []
    character_count = 0
    byte_count = 0

    def receive(delta: ProviderTextDelta) -> None:
        nonlocal character_count, byte_count
        if not isinstance(delta, ProviderTextDelta):
            raise ValueError("provider stream emitted an invalid event")
        character_count += len(delta.text)
        byte_count += len(delta.text.encode("utf-8"))
        if (
            character_count > MAX_PROVIDER_STREAM_TEXT_CHARACTERS
            or byte_count > MAX_PROVIDER_STREAM_TEXT_BYTES
        ):
            raise ValueError("provider stream text exceeds the supported size")
        text_parts.append(delta.text)
        event_sink(delta)

    response = stream_method(request, event_sink=receive)
    if not isinstance(response, (AssistantText, ToolUse)):
        raise ValueError("provider stream returned an invalid response")
    streamed_text = "".join(text_parts)
    response_text = (
        response.text if isinstance(response, AssistantText) else response.assistant_text or ""
    )
    if streamed_text != response_text:
        raise ValueError("provider stream text does not match its completed response")
    return ProviderResponseOutcome(response, bool(text_parts))
