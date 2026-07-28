"""Provider-neutral actual token usage and process-local runtime accounting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from leonervis_code.providers.request_context import ContextFitReport

MAX_PROVIDER_USAGE_TOKENS = 100_000_000


@dataclass(frozen=True)
class ProviderTokenUsage:
    """Actual input/output tokens reported by one provider generation."""

    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.input_tokens, "input"),
            (self.output_tokens, "output"),
        ):
            if type(value) is not int or not 0 <= value <= MAX_PROVIDER_USAGE_TOKENS:
                raise ValueError(f"provider {label} token usage is outside the supported range")


class ProviderInvocationKind(StrEnum):
    TURN = "turn"
    COMPACTION = "compaction"


@dataclass(frozen=True)
class ProviderInvocationUsage:
    sequence: int
    kind: ProviderInvocationKind
    usage: ProviderTokenUsage | None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("provider invocation usage sequence must be positive")
        if type(self.kind) is not ProviderInvocationKind:
            raise ValueError("provider invocation usage kind is invalid")
        if self.usage is not None and type(self.usage) is not ProviderTokenUsage:
            raise ValueError("provider invocation usage payload is invalid")


@dataclass(frozen=True)
class ProviderUsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    known_invocations: int = 0
    unknown_invocations: int = 0

    def add(self, usage: ProviderTokenUsage | None) -> ProviderUsageTotals:
        if usage is None:
            return ProviderUsageTotals(
                self.input_tokens,
                self.output_tokens,
                self.known_invocations,
                self.unknown_invocations + 1,
            )
        return ProviderUsageTotals(
            self.input_tokens + usage.input_tokens,
            self.output_tokens + usage.output_tokens,
            self.known_invocations + 1,
            self.unknown_invocations,
        )


@dataclass(frozen=True)
class RuntimeUsageSnapshot:
    """Process-local usage since the current runtime target was selected."""

    runtime_generation: int
    latest_context: ContextFitReport | None
    latest_invocation: ProviderInvocationUsage | None
    latest_compaction: ProviderInvocationUsage | None
    latest_turn: tuple[ProviderInvocationUsage, ...]
    turn_totals: ProviderUsageTotals
    profile_turn_totals: ProviderUsageTotals
    profile_compaction_totals: ProviderUsageTotals


class RuntimeUsageTracker:
    """Thread-safe process-local accounting outside conversation and Session state."""

    def __init__(self, runtime_generation: int = 0) -> None:
        self._lock = RLock()
        self._generation = runtime_generation
        self._records: list[ProviderInvocationUsage] = []
        self._latest_turn: tuple[ProviderInvocationUsage, ...] = ()
        self._latest_context: ContextFitReport | None = None

    def reset(self, runtime_generation: int) -> None:
        with self._lock:
            self._generation = runtime_generation
            self._records.clear()
            self._latest_turn = ()
            self._latest_context = None

    def retarget(self, runtime_generation: int) -> None:
        """Advance route generation without discarding current-profile usage totals."""
        with self._lock:
            self._generation = runtime_generation
            self._latest_context = None

    def record_context(self, report: ContextFitReport) -> None:
        if type(report) is not ContextFitReport:
            raise ValueError("provider context report is invalid")
        with self._lock:
            self._latest_context = report

    def turn_cursor(self) -> int:
        with self._lock:
            return len(self._records)

    def records_since(
        self,
        cursor: int,
        *,
        kind: ProviderInvocationKind | None = None,
    ) -> tuple[ProviderInvocationUsage, ...]:
        """Return a stable operation-local suffix without mutating latest-turn state."""
        with self._lock:
            if type(cursor) is not int or not 0 <= cursor <= len(self._records):
                raise ValueError("provider usage cursor is invalid")
            if kind is not None and type(kind) is not ProviderInvocationKind:
                raise ValueError("provider usage kind is invalid")
            selected = (
                record for record in self._records[cursor:] if kind is None or record.kind == kind
            )
            return tuple(
                ProviderInvocationUsage(index, record.kind, record.usage)
                for index, record in enumerate(selected, start=1)
            )

    def finish_turn(self, cursor: int) -> RuntimeUsageSnapshot:
        with self._lock:
            if type(cursor) is not int or not 0 <= cursor <= len(self._records):
                raise ValueError("provider usage turn cursor is invalid")
            self._latest_turn = tuple(
                record
                for record in self._records[cursor:]
                if record.kind == ProviderInvocationKind.TURN
            )
            return self._snapshot_locked()

    def record(
        self,
        kind: ProviderInvocationKind,
        usage: ProviderTokenUsage | None,
    ) -> ProviderInvocationUsage:
        with self._lock:
            record = ProviderInvocationUsage(len(self._records) + 1, kind, usage)
            self._records.append(record)
            return record

    def snapshot(self) -> RuntimeUsageSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> RuntimeUsageSnapshot:
        turn_profile = ProviderUsageTotals()
        compaction_profile = ProviderUsageTotals()
        for record in self._records:
            if record.kind == ProviderInvocationKind.TURN:
                turn_profile = turn_profile.add(record.usage)
            else:
                compaction_profile = compaction_profile.add(record.usage)
        latest_turn_totals = ProviderUsageTotals()
        for record in self._latest_turn:
            latest_turn_totals = latest_turn_totals.add(record.usage)
        latest_compaction = next(
            (
                record
                for record in reversed(self._records)
                if record.kind == ProviderInvocationKind.COMPACTION
            ),
            None,
        )
        return RuntimeUsageSnapshot(
            runtime_generation=self._generation,
            latest_context=self._latest_context,
            latest_invocation=self._records[-1] if self._records else None,
            latest_compaction=latest_compaction,
            latest_turn=self._latest_turn,
            turn_totals=latest_turn_totals,
            profile_turn_totals=turn_profile,
            profile_compaction_totals=compaction_profile,
        )


def parse_provider_usage(
    usage: object,
    *,
    input_field: str,
    output_field: str,
) -> ProviderTokenUsage | None:
    """Read one strict provider usage pair; malformed or absent metadata is unknown."""
    if usage is None:
        return None
    input_tokens = getattr(usage, input_field, None)
    output_tokens = getattr(usage, output_field, None)
    try:
        return ProviderTokenUsage(input_tokens, output_tokens)
    except ValueError:
        return None
