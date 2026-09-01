"""Provider-neutral synchronous response-stream contract and compatibility helper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from coquo.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    ConversationProvider,
    ConversationRequest,
    ProviderResponse,
    ProviderResponseEnvelope,
    ToolUse,
)
from coquo.providers.usage import ProviderTokenUsage
from coquo.providers.request_context import ContextFitReport
from coquo.core.cancellation import TurnCancellation

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


class ProviderSearchPhase(StrEnum):
    SEARCHING = "searching"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ProviderSearchActivity:
    """One content-free ephemeral Provider-owned search lifecycle event."""

    phase: ProviderSearchPhase

    def __post_init__(self) -> None:
        if type(self.phase) is not ProviderSearchPhase:
            raise ValueError("provider search activity phase is invalid")


@dataclass(frozen=True)
class ProviderSearchObservation:
    """Bounded content-free facts derived from one terminal Provider response."""

    call_count: int
    failed_count: int
    action_types: tuple[str, ...]
    source_count: int
    citation_count: int
    discarded_citation_count: int = 0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.call_count,
                self.failed_count,
                self.source_count,
                self.citation_count,
                self.discarded_citation_count,
            )
        ):
            raise ValueError("provider search observation counts are invalid")
        if self.failed_count > self.call_count:
            raise ValueError("provider search failed count exceeds call count")
        allowed_actions = {"search", "open_page", "find_in_page", "unknown"}
        if (
            not isinstance(self.action_types, tuple)
            or len(self.action_types) > 8
            or len(set(self.action_types)) != len(self.action_types)
            or any(action not in allowed_actions for action in self.action_types)
        ):
            raise ValueError("provider search action types are invalid")


ProviderStreamEvent = ProviderTextDelta | ProviderSearchActivity
ProviderTextDeltaSink = Callable[[ProviderStreamEvent], None]


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
    usage: ProviderTokenUsage | None = None
    context_report: ContextFitReport | None = None
    search_observation: ProviderSearchObservation | None = None
    attempts: int = 1
    retry_delays_seconds: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if type(self.attempts) is not int or self.attempts < 1 or self.attempts > 3:
            raise ValueError("provider response attempt count is invalid")
        if not isinstance(self.retry_delays_seconds, tuple):
            raise ValueError("provider response retry delays are invalid")
        if len(self.retry_delays_seconds) != self.attempts - 1:
            raise ValueError("provider response retry delays do not match attempts")
        for delay in self.retry_delays_seconds:
            if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0:
                raise ValueError("provider response retry delay is invalid")


def respond_with_streaming(
    provider: ConversationProvider,
    request: ConversationRequest,
    *,
    event_sink: ProviderTextDeltaSink,
    prefer_stream: bool,
    preflight_sink: Callable[[ContextFitReport], None] | None = None,
    cancellation: TurnCancellation | None = None,
) -> ProviderResponseOutcome:
    """Use a provider stream when requested, otherwise preserve ``respond`` compatibility."""
    if cancellation is not None:
        cancellation.check()

    def checked_sink(event: ProviderStreamEvent) -> None:
        if cancellation is not None:
            cancellation.check()
        event_sink(event)

    observed_method = getattr(provider, "respond_with_observation", None)
    if callable(observed_method):
        try:
            outcome = observed_method(
                request,
                event_sink=checked_sink,
                prefer_stream=prefer_stream,
                preflight_sink=preflight_sink,
                cancellation=cancellation,
            )
        except TypeError as error:
            # Preserve compatibility with third-party observed providers that
            # implement the pre-cancellation callback shape.
            if "cancellation" not in str(error):
                raise
            outcome = observed_method(
                request,
                event_sink=checked_sink,
                prefer_stream=prefer_stream,
                preflight_sink=preflight_sink,
            )
        if cancellation is not None:
            cancellation.check()
        return outcome

    stream_method = getattr(provider, "respond_stream", None)
    stream_outcome_method = getattr(provider, "respond_stream_outcome", None)
    streaming_supported = getattr(provider, "streaming_supported", callable(stream_method))
    if not prefer_stream or not callable(stream_method) or streaming_supported is not True:
        outcome_method = getattr(provider, "respond_outcome", None)
        if callable(outcome_method):
            outcome = outcome_method(request)
            if not isinstance(outcome, ProviderResponseOutcome):
                raise ValueError("provider returned an invalid response outcome")
            if cancellation is not None:
                cancellation.check()
            return outcome
        response = provider.respond(request)
        if cancellation is not None:
            cancellation.check()
        return ProviderResponseOutcome(response, False)

    text_parts: list[str] = []
    character_count = 0
    byte_count = 0

    def receive(event: ProviderStreamEvent) -> None:
        nonlocal character_count, byte_count
        if isinstance(event, ProviderSearchActivity):
            checked_sink(event)
            return
        if not isinstance(event, ProviderTextDelta):
            raise ValueError("provider stream emitted an invalid event")
        character_count += len(event.text)
        byte_count += len(event.text.encode("utf-8"))
        if (
            character_count > MAX_PROVIDER_STREAM_TEXT_CHARACTERS
            or byte_count > MAX_PROVIDER_STREAM_TEXT_BYTES
        ):
            raise ValueError("provider stream text exceeds the supported size")
        text_parts.append(event.text)
        checked_sink(event)

    if callable(stream_outcome_method):
        outcome = stream_outcome_method(request, event_sink=receive)
        if not isinstance(outcome, ProviderResponseOutcome):
            raise ValueError("provider stream returned an invalid response outcome")
        response = outcome.response
        usage = outcome.usage
        search_observation = outcome.search_observation
    else:
        response = stream_method(request, event_sink=receive)
        usage = None
        search_observation = None
    if not isinstance(
        response, (AssistantText, ToolUse, AssistantToolBatch, ProviderResponseEnvelope)
    ):
        raise ValueError("provider stream returned an invalid response")
    streamed_text = "".join(text_parts)
    visible_response = (
        response.response if isinstance(response, ProviderResponseEnvelope) else response
    )
    response_text = (
        visible_response.text
        if isinstance(visible_response, AssistantText)
        else visible_response.assistant_text or ""
    )
    if streamed_text != response_text:
        raise ValueError("provider stream text does not match its completed response")
    if cancellation is not None:
        cancellation.check()
    return ProviderResponseOutcome(
        response,
        bool(text_parts),
        usage,
        search_observation=search_observation,
    )
