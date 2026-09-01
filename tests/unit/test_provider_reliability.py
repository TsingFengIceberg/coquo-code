from __future__ import annotations

import pytest

from coquo.core.cancellation import TurnCancellation
from coquo.core.contracts import AssistantText
from coquo.core.orchestration import ProviderFailureKind
from coquo.providers.errors import adapter_error
from coquo.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from coquo.providers.reliability import (
    ProviderReliabilityBudgetError,
    ProviderReliabilityPolicy,
    invoke_with_reliability,
)
from coquo.providers.streaming import ProviderResponseOutcome, ProviderTextDelta
from coquo.providers.usage import ProviderTokenUsage


def _report(input_tokens: int | None = 10) -> ContextFitReport:
    count = (
        RequestTokenCount(input_tokens, RequestTokenCountMethod.EXACT)
        if input_tokens is not None
        else RequestTokenCount.unknown("test")
    )
    return ContextFitReport(None, count, 20, None, None, ContextFitDecision.UNKNOWN)


def test_retry_is_bounded_and_only_successful_attempt_events_are_visible() -> None:
    calls = 0
    events: list[object] = []
    sleeps: list[float] = []

    def invoke(sink):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise adapter_error(
                provider_id="test",
                model_id="model",
                kind=ProviderFailureKind.TRANSPORT,
                code="transport",
                message="temporary transport failure",
                retryable=True,
            )
        sink(ProviderTextDelta("done"))
        return ProviderResponseOutcome(AssistantText("done"), True), True

    result = invoke_with_reliability(
        invoke,
        report=_report(),
        policy=ProviderReliabilityPolicy(max_attempts=2, base_delay_seconds=0.5),
        cancellation=None,
        sleep=sleeps.append,
        event_sink=events.append,
    )

    assert calls == 2
    assert result.attempts == 2
    assert result.retry_delays_seconds == (0.5,)
    assert sleeps == [0.5]
    assert [event.text for event in events] == ["done"]


def test_stream_failure_after_delta_is_not_retried() -> None:
    calls = 0
    events: list[object] = []

    def invoke(sink):
        nonlocal calls
        calls += 1
        sink(ProviderTextDelta("partial"))
        raise adapter_error(
            provider_id="test",
            model_id="model",
            kind=ProviderFailureKind.TRANSPORT,
            code="transport",
            message="stream ended unexpectedly",
            retryable=True,
        )

    with pytest.raises(Exception, match="stream ended unexpectedly"):
        invoke_with_reliability(
            invoke,
            report=_report(),
            policy=ProviderReliabilityPolicy(max_attempts=3, base_delay_seconds=0),
            cancellation=None,
            event_sink=events.append,
        )

    assert calls == 1
    assert [event.text for event in events] == ["partial"]


def test_known_input_budget_rejects_before_provider_call() -> None:
    called = False

    def invoke(_sink):
        nonlocal called
        called = True
        return ProviderResponseOutcome(AssistantText("unexpected"), False), False

    with pytest.raises(ProviderReliabilityBudgetError, match="input-token budget"):
        invoke_with_reliability(
            invoke,
            report=_report(50),
            policy=ProviderReliabilityPolicy(max_input_tokens=49),
            cancellation=None,
            event_sink=lambda _event: None,
        )
    assert called is False


def test_output_budget_rejects_after_known_usage_and_cancellation_stops_retry() -> None:
    calls = 0
    cancellation = TurnCancellation()

    def invoke(_sink):
        nonlocal calls
        calls += 1
        cancellation.request()
        raise adapter_error(
            provider_id="test",
            model_id="model",
            kind=ProviderFailureKind.RATE_LIMITED,
            code="rate_limited",
            message="try later",
            retryable=True,
        )

    with pytest.raises(BaseException):
        invoke_with_reliability(
            invoke,
            report=_report(),
            policy=ProviderReliabilityPolicy(max_attempts=3, base_delay_seconds=1),
            cancellation=cancellation,
            sleep=lambda _delay: None,
            event_sink=lambda _event: None,
        )
    assert calls == 1


def test_known_output_budget_is_checked_after_success() -> None:
    def invoke(_sink):
        return (
            ProviderResponseOutcome(AssistantText("done"), False, ProviderTokenUsage(10, 8)),
            False,
        )

    with pytest.raises(ProviderReliabilityBudgetError, match="output-token budget"):
        invoke_with_reliability(
            invoke,
            report=_report(),
            policy=ProviderReliabilityPolicy(max_output_tokens=7),
            cancellation=None,
            event_sink=lambda _event: None,
        )
