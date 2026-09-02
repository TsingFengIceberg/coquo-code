from __future__ import annotations

import pytest

from coquo.providers.stability import (
    ProviderSoakReport,
    StreamLatencyClass,
    StreamSample,
    aggregate_soak,
    classify_stream,
)


def test_stream_classification_distinguishes_no_stream_first_wait_gap_and_healthy():
    assert classify_stream(StreamSample(100, 0, None, None)) is StreamLatencyClass.NOT_STREAMED
    assert (
        classify_stream(StreamSample(7_000, 2, 5_000, 100))
        is StreamLatencyClass.UPSTREAM_FIRST_DELTA_WAIT
    )
    assert (
        classify_stream(StreamSample(8_000, 3, 100, 6_000))
        is StreamLatencyClass.UPSTREAM_INTER_DELTA_GAP
    )
    assert classify_stream(StreamSample(100, 2, 10, 20)) is StreamLatencyClass.HEALTHY


def test_soak_aggregation_is_bounded_and_content_free():
    report = aggregate_soak(
        ("passed", "failed", "timeout", "passed"),
        (
            StreamSample(100, 1, 10, None),
            StreamSample(8_000, 2, 7_000, 10),
        ),
        max_failures=2,
    )
    assert isinstance(report, ProviderSoakReport)
    assert report.succeeded == 2
    assert report.failed == 1
    assert report.timeouts == 1
    assert report.as_mapping()["sample_count"] == 2
    assert report.as_mapping()["classifications"]["healthy"] == 1


def test_stability_rejects_invalid_sample_and_outcome_bounds():
    with pytest.raises(ValueError, match="at least one delta"):
        StreamSample(10, 0, 1, None)
    with pytest.raises(ValueError, match="outcomes"):
        aggregate_soak((), ())
