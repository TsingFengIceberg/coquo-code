from __future__ import annotations

import pytest

from leonervis_code.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from leonervis_code.providers.usage import (
    ProviderInvocationKind,
    ProviderTokenUsage,
    ProviderUsageTotals,
    RuntimeUsageTracker,
    parse_provider_usage,
)


def fit_report() -> ContextFitReport:
    return ContextFitReport(
        target=None,
        input_count=RequestTokenCount(72_400, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=8_000,
        context_window_limit=128_000,
        model_output_limit=16_000,
        decision=ContextFitDecision.FITS,
    )


def test_usage_tracker_separates_turn_compaction_unknown_and_reset() -> None:
    tracker = RuntimeUsageTracker(3)
    cursor = tracker.turn_cursor()
    tracker.record_context(fit_report())
    tracker.record(ProviderInvocationKind.TURN, ProviderTokenUsage(100, 20))
    tracker.record(ProviderInvocationKind.COMPACTION, ProviderTokenUsage(80, 10))
    tracker.record(ProviderInvocationKind.REVIEW, ProviderTokenUsage(40, 5))
    tracker.record(ProviderInvocationKind.TURN, None)

    snapshot = tracker.finish_turn(cursor)

    assert snapshot.latest_context == fit_report()
    assert snapshot.turn_totals.input_tokens == 100
    assert snapshot.turn_totals.output_tokens == 20
    assert snapshot.turn_totals.known_invocations == 1
    assert snapshot.turn_totals.unknown_invocations == 1
    assert snapshot.profile_compaction_totals.input_tokens == 80
    assert snapshot.profile_review_totals == ProviderUsageTotals(40, 5, 1, 0)
    assert snapshot.latest_compaction is not None
    assert snapshot.latest_compaction.usage == ProviderTokenUsage(80, 10)
    assert snapshot.latest_review is not None
    assert snapshot.latest_review.usage == ProviderTokenUsage(40, 5)
    assert [record.kind for record in snapshot.latest_turn] == [
        ProviderInvocationKind.TURN,
        ProviderInvocationKind.TURN,
    ]
    compact_suffix = tracker.records_since(
        cursor,
        kind=ProviderInvocationKind.COMPACTION,
    )
    assert len(compact_suffix) == 1
    assert compact_suffix[0].sequence == 1
    assert compact_suffix[0].usage == ProviderTokenUsage(80, 10)
    with pytest.raises(ValueError, match="cursor"):
        tracker.records_since(99)

    tracker.reset(4)
    reset = tracker.snapshot()
    assert reset.runtime_generation == 4
    assert reset.latest_invocation is None
    assert reset.latest_compaction is None
    assert reset.latest_review is None
    assert reset.latest_context is None


def test_provider_usage_is_strict_and_malformed_metadata_is_unknown() -> None:
    class Usage:
        prompt_tokens = 12
        completion_tokens = 3

    assert parse_provider_usage(
        Usage(), input_field="prompt_tokens", output_field="completion_tokens"
    ) == ProviderTokenUsage(12, 3)
    assert (
        parse_provider_usage(
            object(), input_field="prompt_tokens", output_field="completion_tokens"
        )
        is None
    )
    with pytest.raises(ValueError):
        ProviderTokenUsage(-1, 0)
