"""Bounded, privacy-preserving Host observations for semantic memory."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from coquo.memory import utc_now

MAX_MEMORY_OBSERVATIONS = 256


@dataclass(frozen=True)
class MemoryObservation:
    """One content-free memory lifecycle fact suitable for local inspection."""

    sequence: int
    occurred_at: str
    operation: str
    outcome: str
    actor: str
    scope_kinds: tuple[str, ...] = ()
    record_count: int = 0
    degraded: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("memory observation sequence is invalid")
        if not self.operation or not self.operation.isascii() or len(self.operation) > 64:
            raise ValueError("memory observation operation is invalid")
        if not self.outcome or not self.outcome.isascii() or len(self.outcome) > 64:
            raise ValueError("memory observation outcome is invalid")
        if not self.actor or not self.actor.isascii() or len(self.actor) > 128:
            raise ValueError("memory observation actor is invalid")
        if len(self.scope_kinds) > 8 or any(
            not isinstance(value, str) or not value.isascii() or len(value) > 32
            for value in self.scope_kinds
        ):
            raise ValueError("memory observation scopes are invalid")
        if type(self.record_count) is not int or not 0 <= self.record_count <= 1000:
            raise ValueError("memory observation record count is invalid")
        if type(self.degraded) is not bool:
            raise ValueError("memory observation degraded flag is invalid")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.isascii() or len(self.reason) > 256
        ):
            raise ValueError("memory observation reason is invalid")


class MemoryObservationLedger:
    """Bounded process-local observation store; it never persists memory content."""

    def __init__(self, *, limit: int = MAX_MEMORY_OBSERVATIONS) -> None:
        if type(limit) is not int or not 1 <= limit <= MAX_MEMORY_OBSERVATIONS:
            raise ValueError("memory observation limit is invalid")
        self._limit = limit
        self._sequence = 0
        self._items: list[MemoryObservation] = []
        self._lock = RLock()

    def record(
        self,
        operation: str,
        outcome: str,
        *,
        actor: str,
        scope_kinds: tuple[str, ...] = (),
        record_count: int = 0,
        degraded: bool = False,
        reason: str | None = None,
    ) -> MemoryObservation:
        with self._lock:
            self._sequence += 1
            item = MemoryObservation(
                self._sequence,
                utc_now(),
                operation,
                outcome,
                actor,
                tuple(scope_kinds),
                record_count,
                degraded,
                reason,
            )
            self._items.append(item)
            if len(self._items) > self._limit:
                del self._items[: len(self._items) - self._limit]
            return item

    def snapshot(self, limit: int = MAX_MEMORY_OBSERVATIONS) -> tuple[MemoryObservation, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_MEMORY_OBSERVATIONS:
            raise ValueError("memory observation snapshot limit is invalid")
        with self._lock:
            return tuple(self._items[-limit:])
