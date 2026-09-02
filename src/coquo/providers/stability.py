"""Deterministic Provider soak and stream-latency classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


MAX_SOAK_SAMPLES = 128
MAX_FAILURES = 128


class StreamLatencyClass(StrEnum):
    NOT_STREAMED = "not-streamed"
    UPSTREAM_FIRST_DELTA_WAIT = "upstream-first-delta-wait"
    UPSTREAM_INTER_DELTA_GAP = "upstream-inter-delta-gap"
    HEALTHY = "healthy"


@dataclass(frozen=True)
class StreamSample:
    """Content-free Host measurements for one Provider invocation."""

    elapsed_milliseconds: int | None
    delta_count: int
    first_delta_milliseconds: int | None
    max_delta_gap_milliseconds: int | None
    retry_count: int = 0
    outcome: str = "final-text"

    def __post_init__(self) -> None:
        for value, label in (
            (self.elapsed_milliseconds, "elapsed"),
            (self.first_delta_milliseconds, "first delta"),
            (self.max_delta_gap_milliseconds, "delta gap"),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"Provider stability {label} is invalid")
        if type(self.delta_count) is not int or not 0 <= self.delta_count <= 100_000:
            raise ValueError("Provider stability delta count is invalid")
        if self.delta_count == 0 and (
            self.first_delta_milliseconds is not None or self.max_delta_gap_milliseconds is not None
        ):
            raise ValueError("Provider stability metrics require at least one delta")
        if self.delta_count == 1 and self.max_delta_gap_milliseconds is not None:
            raise ValueError("one-delta Provider sample cannot have a gap")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 2:
            raise ValueError("Provider stability retry count is invalid")
        if not isinstance(self.outcome, str) or not self.outcome:
            raise ValueError("Provider stability outcome is invalid")


def classify_stream(
    sample: StreamSample,
    *,
    first_delta_threshold_milliseconds: int = 5_000,
    inter_delta_threshold_milliseconds: int = 5_000,
) -> StreamLatencyClass:
    """Classify an observed delay without claiming where the delay originated."""
    if not isinstance(sample, StreamSample):
        raise ValueError("Provider stability sample is invalid")
    if (
        type(first_delta_threshold_milliseconds) is not int
        or first_delta_threshold_milliseconds < 0
        or type(inter_delta_threshold_milliseconds) is not int
        or inter_delta_threshold_milliseconds < 0
    ):
        raise ValueError("Provider stability thresholds are invalid")
    if sample.delta_count == 0:
        return StreamLatencyClass.NOT_STREAMED
    if (
        sample.first_delta_milliseconds is not None
        and sample.first_delta_milliseconds >= first_delta_threshold_milliseconds
    ):
        return StreamLatencyClass.UPSTREAM_FIRST_DELTA_WAIT
    if (
        sample.max_delta_gap_milliseconds is not None
        and sample.max_delta_gap_milliseconds >= inter_delta_threshold_milliseconds
    ):
        return StreamLatencyClass.UPSTREAM_INTER_DELTA_GAP
    return StreamLatencyClass.HEALTHY


@dataclass(frozen=True)
class ProviderSoakReport:
    """Bounded aggregate for repeated real-provider or deterministic runs."""

    total: int
    succeeded: int
    failed: int
    timeouts: int
    samples: tuple[StreamSample, ...]
    classifications: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.total) is not int or not 1 <= self.total <= MAX_SOAK_SAMPLES:
            raise ValueError("Provider soak total is invalid")
        if any(
            type(value) is not int or value < 0
            for value in (self.succeeded, self.failed, self.timeouts)
        ):
            raise ValueError("Provider soak counts are invalid")
        if self.succeeded + self.failed + self.timeouts != self.total:
            raise ValueError("Provider soak counts do not add up")
        if len(self.samples) > self.total:
            raise ValueError("Provider soak samples exceed total")
        if sum(count for _, count in self.classifications) != len(self.samples):
            raise ValueError("Provider soak classifications do not add up")

    @property
    def pass_rate(self) -> float:
        return self.succeeded / self.total

    def as_mapping(self) -> dict[str, object]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "timeouts": self.timeouts,
            "pass_rate": self.pass_rate,
            "sample_count": len(self.samples),
            "classifications": dict(self.classifications),
        }


def aggregate_soak(
    outcomes: tuple[str, ...], samples: tuple[StreamSample, ...], *, max_failures: int = 0
) -> ProviderSoakReport:
    """Aggregate bounded status labels and stream facts for a soak run."""
    if not isinstance(outcomes, tuple) or not outcomes or len(outcomes) > MAX_SOAK_SAMPLES:
        raise ValueError("Provider soak outcomes are invalid")
    if not isinstance(samples, tuple) or len(samples) > len(outcomes):
        raise ValueError("Provider soak samples are invalid")
    if type(max_failures) is not int or not 0 <= max_failures <= MAX_FAILURES:
        raise ValueError("Provider soak failure limit is invalid")
    succeeded = sum(item == "passed" for item in outcomes)
    timeouts = sum(item == "timeout" for item in outcomes)
    failed = len(outcomes) - succeeded - timeouts
    if failed + timeouts > max_failures:
        # The report remains truthful; callers can apply this as a gate.
        pass
    counts: dict[str, int] = {}
    for sample in samples:
        key = classify_stream(sample).value
        counts[key] = counts.get(key, 0) + 1
    return ProviderSoakReport(
        len(outcomes),
        succeeded,
        failed,
        timeouts,
        samples,
        tuple(sorted(counts.items())),
    )


__all__ = [
    "MAX_FAILURES",
    "MAX_SOAK_SAMPLES",
    "ProviderSoakReport",
    "StreamLatencyClass",
    "StreamSample",
    "aggregate_soak",
    "classify_stream",
]
