from __future__ import annotations

import pytest

from leonervis_code.core.contracts import (
    AssistantText,
    ConversationRequest,
    ToolArguments,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled
from leonervis_code.providers.streaming import (
    ProviderTextDelta,
    respond_with_streaming,
)
from leonervis_code.system_prompt import build_system_prompt


def request() -> ConversationRequest:
    return ConversationRequest(build_system_prompt(), (UserMessage("hello"),))


class StreamingProvider:
    def __init__(self, response, deltas=("hel", "lo")) -> None:
        self.response = response
        self.deltas = deltas
        self.respond_calls = 0

    def respond(self, _request):
        self.respond_calls += 1
        return self.response

    def respond_stream(self, _request, *, event_sink):
        for text in self.deltas:
            event_sink(ProviderTextDelta(text))
        return self.response


def test_streaming_helper_preserves_exact_text_and_falls_back_explicitly() -> None:
    provider = StreamingProvider(AssistantText("hello"))
    events = []

    streamed = respond_with_streaming(
        provider,
        request(),
        event_sink=events.append,
        prefer_stream=True,
    )
    fallback = respond_with_streaming(
        provider,
        request(),
        event_sink=events.append,
        prefer_stream=False,
    )

    assert streamed.response == AssistantText("hello")
    assert streamed.text_was_streamed is True
    assert events == [ProviderTextDelta("hel"), ProviderTextDelta("lo")]
    assert fallback.response == AssistantText("hello")
    assert fallback.text_was_streamed is False
    assert provider.respond_calls == 1


def test_streaming_helper_validates_completed_text_and_event_shape() -> None:
    mismatch = StreamingProvider(AssistantText("different"))
    with pytest.raises(ValueError, match="does not match"):
        respond_with_streaming(
            mismatch,
            request(),
            event_sink=lambda _event: None,
            prefer_stream=True,
        )

    class InvalidEventProvider(StreamingProvider):
        def respond_stream(self, _request, *, event_sink):
            event_sink("bad")
            return AssistantText("bad")

    with pytest.raises(ValueError, match="invalid event"):
        respond_with_streaming(
            InvalidEventProvider(AssistantText("bad")),
            request(),
            event_sink=lambda _event: None,
            prefer_stream=True,
        )


def test_streaming_helper_matches_atomic_tool_companion_text() -> None:
    response = ToolUse(
        "call-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will read.",
    )
    provider = StreamingProvider(response, ("I will ", "read."))

    outcome = respond_with_streaming(
        provider,
        request(),
        event_sink=lambda _event: None,
        prefer_stream=True,
    )

    assert outcome.response == response
    assert outcome.text_was_streamed is True


def test_streaming_helper_rejects_aggregate_text_over_bound(monkeypatch) -> None:
    monkeypatch.setattr("leonervis_code.providers.streaming.MAX_PROVIDER_STREAM_TEXT_CHARACTERS", 5)
    monkeypatch.setattr("leonervis_code.providers.streaming.MAX_PROVIDER_STREAM_TEXT_BYTES", 5)
    provider = StreamingProvider(AssistantText("abcdef"), ("abc", "def"))

    with pytest.raises(ValueError, match="exceeds"):
        respond_with_streaming(
            provider,
            request(),
            event_sink=lambda _event: None,
            prefer_stream=True,
        )


def test_streaming_helper_observes_cancellation_between_deltas() -> None:
    cancellation = TurnCancellation()
    provider = StreamingProvider(AssistantText("hello"))

    with pytest.raises(TurnCancelled):
        respond_with_streaming(
            provider,
            request(),
            event_sink=lambda _event: cancellation.request(),
            prefer_stream=True,
            cancellation=cancellation,
        )


def test_nonstreaming_helper_observes_cancellation_after_blocking_response() -> None:
    cancellation = TurnCancellation()

    class CancellingProvider:
        def respond(self, _request):
            cancellation.request()
            return AssistantText("not committed")

    with pytest.raises(TurnCancelled):
        respond_with_streaming(
            CancellingProvider(),
            request(),
            event_sink=lambda _event: None,
            prefer_stream=False,
            cancellation=cancellation,
        )


@pytest.mark.parametrize("value", ["", "bad\x00text", "\ud800"])
def test_provider_text_delta_rejects_noncanonical_text(value: str) -> None:
    with pytest.raises(ValueError):
        ProviderTextDelta(value)
