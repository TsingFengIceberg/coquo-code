"""Bounded, cancellation-aware Provider retry and usage-budget policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

from coquo.core.cancellation import TurnCancellation
from coquo.core.orchestration import ProviderFailureKind
from coquo.providers.errors import ProviderAdapterError
from coquo.providers.request_context import ContextFitReport
from coquo.providers.streaming import ProviderResponseOutcome
from coquo.providers.usage import ProviderTokenUsage

MAX_PROVIDER_RELIABILITY_ATTEMPTS = 3
MAX_PROVIDER_RELIABILITY_DELAY_SECONDS = 30.0
MAX_PROVIDER_RELIABILITY_ELAPSED_SECONDS = 300.0
MAX_PROVIDER_RELIABILITY_TOKENS = 100_000_000


@dataclass(frozen=True)
class ProviderReliabilityPolicy:
    """Host-owned limits for one logical Provider invocation.

    The default is deliberately one attempt.  Callers must opt into retries;
    that prevents a transport ambiguity from silently duplicating a billed
    request or a request whose external Provider-side effect is unknown.
    """

    max_attempts: int = 1
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    max_elapsed_seconds: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    require_known_usage: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= MAX_PROVIDER_RELIABILITY_ATTEMPTS
        ):
            raise ValueError("provider reliability attempts must be between 1 and 3")
        for value, label in (
            (self.base_delay_seconds, "provider reliability base delay"),
            (self.max_delay_seconds, "provider reliability maximum delay"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label} must be a finite nonnegative number")
            if value < 0 or value > MAX_PROVIDER_RELIABILITY_DELAY_SECONDS:
                raise ValueError(f"{label} is outside the supported range")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("provider reliability maximum delay must not be below base delay")
        if self.max_elapsed_seconds is not None and (
            isinstance(self.max_elapsed_seconds, bool)
            or not isinstance(self.max_elapsed_seconds, (int, float))
            or self.max_elapsed_seconds <= 0
            or self.max_elapsed_seconds > MAX_PROVIDER_RELIABILITY_ELAPSED_SECONDS
        ):
            raise ValueError("provider reliability elapsed limit is outside the supported range")
        for value, label in (
            (self.max_input_tokens, "provider reliability input-token limit"),
            (self.max_output_tokens, "provider reliability output-token limit"),
        ):
            if value is not None and (
                type(value) is not int or not 1 <= value <= MAX_PROVIDER_RELIABILITY_TOKENS
            ):
                raise ValueError(f"{label} is outside the supported range")
        if type(self.require_known_usage) is not bool:
            raise ValueError("provider reliability known-usage flag is invalid")


class ProviderReliabilityBudgetError(RuntimeError):
    """A local fail-closed budget rejection with no raw Provider payload."""

    def __init__(self, code: str, message: str, *, attempts: int = 0) -> None:
        if not isinstance(code, str) or not code or not code.isascii():
            raise ValueError("provider reliability error code is invalid")
        if not isinstance(message, str) or not message or not message.isprintable():
            raise ValueError("provider reliability error message is invalid")
        if type(attempts) is not int or attempts < 0:
            raise ValueError("provider reliability attempt count is invalid")
        super().__init__(message)
        self.code = code
        self.attempts = attempts


@dataclass(frozen=True)
class ProviderReliabilityResult:
    """Result of one bounded logical invocation and its attempt accounting."""

    outcome: ProviderResponseOutcome
    attempts: int
    input_tokens: int
    output_tokens: int
    unknown_usage: bool
    retry_delays_seconds: tuple[float, ...] = ()


def invoke_with_reliability(
    invoke: Callable[[Callable[[object], None]], tuple[ProviderResponseOutcome, bool]],
    *,
    report: ContextFitReport,
    policy: ProviderReliabilityPolicy,
    cancellation: TurnCancellation | None,
    event_sink: Callable[[object], None],
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    usage_sink: Callable[[ProviderTokenUsage | None], None] | None = None,
    retry_sink: Callable[[int, float, ProviderFailureKind], None] | None = None,
) -> ProviderReliabilityResult:
    """Invoke a Provider, retrying only before any response delta is observed.

    ``invoke`` receives an attempt-local event sink and returns its outcome plus
    a boolean indicating whether any text delta was emitted.  A failed stream
    after a delta is never retried because replaying it would duplicate visible
    output.  Events are forwarded as they arrive, preserving interactive
    streaming; the helper itself owns no Provider, Session, or Action state.
    """
    if not isinstance(report, ContextFitReport):
        raise ValueError("provider reliability context report is invalid")
    if not callable(event_sink):
        raise ValueError("provider reliability event sink is invalid")
    input_count = report.input_count.input_tokens
    if policy.max_input_tokens is not None:
        if input_count is None:
            raise ProviderReliabilityBudgetError(
                "provider_input_budget_unknown",
                "provider input-token budget cannot be enforced because input usage is unknown",
            )
        if input_count > policy.max_input_tokens:
            raise ProviderReliabilityBudgetError(
                "provider_input_budget_exceeded",
                f"provider input-token budget exceeded: {input_count} > {policy.max_input_tokens}",
            )

    started = monotonic()
    attempts = 0
    input_tokens = 0
    output_tokens = 0
    unknown_usage = False
    delays: list[float] = []

    while True:
        if cancellation is not None:
            cancellation.check()
        if (
            policy.max_elapsed_seconds is not None
            and monotonic() - started >= policy.max_elapsed_seconds
        ):
            raise ProviderReliabilityBudgetError(
                "provider_elapsed_budget_exceeded",
                "provider reliability elapsed-time budget exceeded before the next attempt",
                attempts=attempts,
            )
        attempts += 1
        saw_delta = False

        def receive(event: object) -> None:
            nonlocal saw_delta
            # ProviderTextDelta is intentionally detected by its public text
            # attribute, keeping this helper independent of stream internals.
            if hasattr(event, "text"):
                saw_delta = True
            event_sink(event)

        try:
            outcome, provider_saw_delta = invoke(receive)
            if not isinstance(outcome, ProviderResponseOutcome):
                raise ValueError("provider reliability invocation returned an invalid outcome")
            saw_delta = saw_delta or provider_saw_delta
            usage = outcome.usage
            if usage_sink is not None:
                usage_sink(usage)
            if usage is None:
                unknown_usage = True
                if policy.require_known_usage:
                    raise ProviderReliabilityBudgetError(
                        "provider_usage_unknown",
                        "provider usage is unavailable while known usage is required",
                        attempts=attempts,
                    )
            else:
                input_tokens += usage.input_tokens
                output_tokens += usage.output_tokens
            if policy.max_input_tokens is not None and input_tokens > policy.max_input_tokens:
                raise ProviderReliabilityBudgetError(
                    "provider_input_budget_exceeded",
                    f"provider input-token budget exceeded: {input_tokens} > {policy.max_input_tokens}",
                    attempts=attempts,
                )
            if policy.max_output_tokens is not None and output_tokens > policy.max_output_tokens:
                raise ProviderReliabilityBudgetError(
                    "provider_output_budget_exceeded",
                    f"provider output-token budget exceeded: {output_tokens} > {policy.max_output_tokens}",
                    attempts=attempts,
                )
            elapsed = monotonic() - started
            if policy.max_elapsed_seconds is not None and elapsed > policy.max_elapsed_seconds:
                raise ProviderReliabilityBudgetError(
                    "provider_elapsed_budget_exceeded",
                    "provider reliability elapsed-time budget exceeded after the response",
                    attempts=attempts,
                )
            return ProviderReliabilityResult(
                outcome,
                attempts,
                input_tokens,
                output_tokens,
                unknown_usage,
                tuple(delays),
            )
        except ProviderReliabilityBudgetError:
            raise
        except ProviderAdapterError as error:
            if usage_sink is not None:
                usage_sink(error.usage)
            failure = error.failure
            can_retry = (
                attempts < policy.max_attempts
                and not saw_delta
                and failure.retryable
                and failure.kind
                in {
                    ProviderFailureKind.RATE_LIMITED,
                    ProviderFailureKind.TIMEOUT,
                    ProviderFailureKind.TRANSPORT,
                    ProviderFailureKind.PROVIDER_UNAVAILABLE,
                }
            )
            if not can_retry:
                raise
            delay = min(
                policy.max_delay_seconds,
                policy.base_delay_seconds * (2 ** (attempts - 1)),
            )
            if failure.retry_after_seconds is not None:
                delay = min(policy.max_delay_seconds, max(delay, failure.retry_after_seconds))
            if (
                policy.max_elapsed_seconds is not None
                and monotonic() - started + delay >= policy.max_elapsed_seconds
            ):
                raise ProviderReliabilityBudgetError(
                    "provider_elapsed_budget_exceeded",
                    "provider reliability elapsed-time budget leaves no room for another attempt",
                    attempts=attempts,
                ) from error
            delays.append(float(delay))
            if retry_sink is not None:
                retry_sink(attempts, float(delay), failure.kind)
            if cancellation is not None:
                cancellation.wait(delay)
                cancellation.check()
            else:
                sleep(delay)
