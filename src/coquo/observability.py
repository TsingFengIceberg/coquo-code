"""Host-owned, read-only observation contracts and durable-record projections.

The projection deliberately reads the existing Session, Task, Child Run, and
Team ledgers.  It does not add a second append-only log or copy conversation,
tool, or handoff bodies into an observation event.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Condition, RLock
import time
from collections.abc import Callable
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ObservationSource(StrEnum):
    SESSION = "session"
    TASK = "task"
    CHILD = "child"
    TEAM = "team"
    BACKGROUND = "background"


class ObservationPhase(StrEnum):
    CREATED = "created"
    REQUESTED = "requested"
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"
    RECOVERED = "recovered"
    OBSERVED = "observed"
    COMMITTED = "committed"
    CHANGED = "changed"


class ObservationEvidence(StrEnum):
    HOST_VERIFIED = "host-verified"
    HOST_OBSERVED = "host-observed"
    UNTRUSTED = "untrusted"


class ObservationError(ValueError):
    """Raised for an invalid Host observation query."""


OBSERVATION_EVENT_SCHEMA_VERSION = 1
MAX_OBSERVATION_SUBSCRIBER_PENDING = 1024
MAX_OBSERVATION_READ_LIMIT = 256
MAX_OBSERVATION_WAIT_SECONDS = 30
MAX_PERSISTENT_OBSERVATION_BYTES = 16 * 1024 * 1024
MAX_PERSISTENT_OBSERVATION_EVENTS = 50_000


@dataclass(frozen=True)
class ObservationRetentionPolicy:
    """Bounded process-local retention; it never deletes authoritative ledgers."""

    max_events: int = 512
    max_age_seconds: int | None = None

    def __post_init__(self) -> None:
        if type(self.max_events) is not int or not 1 <= self.max_events <= 10_000:
            raise ValueError("observation retention max_events must be between 1 and 10000")
        if self.max_age_seconds is not None and (
            type(self.max_age_seconds) is not int or not 1 <= self.max_age_seconds <= 86_400
        ):
            raise ValueError("observation retention max_age_seconds is invalid")


@dataclass(frozen=True)
class ObservationContext:
    """Correlation IDs carried by one Host operation and its descendants."""

    trace_id: str
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    stage_id: str | None = None
    child_run_id: str | None = None
    team_id: str | None = None
    parent_event_id: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.trace_id, "trace ID")
        for name in (
            "session_id",
            "turn_id",
            "task_id",
            "stage_id",
            "child_run_id",
            "team_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _uuid(value, name.replace("_", " "))
        if self.parent_event_id is not None:
            _event_id(self.parent_event_id, "parent event ID")

    @classmethod
    def new(cls, *, session_id: str | None = None, **ids: str | None) -> "ObservationContext":
        """Create a fresh Host trace for a prompt or orchestration operation."""
        return cls(str(uuid4()), session_id=session_id, **ids)

    def child(self, **updates: str | None) -> "ObservationContext":
        """Return a descendant context while retaining the same trace ID."""
        values = {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "stage_id": self.stage_id,
            "child_run_id": self.child_run_id,
            "team_id": self.team_id,
            "parent_event_id": self.parent_event_id,
        }
        values.update(updates)
        return ObservationContext(**values)


@dataclass(frozen=True)
class ObservationEvent:
    """One bounded, serializable fact in a Host observation timeline."""

    event_id: str
    trace_id: str
    source: ObservationSource
    source_id: str
    sequence: int
    occurred_at: str
    record_type: str
    phase: ObservationPhase
    status: str
    evidence: ObservationEvidence
    summary: str
    parent_event_id: str | None = None
    related_ids: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _event_id(self.event_id, "event ID")
        _uuid(self.trace_id, "trace ID")
        _uuid(self.source_id, "source ID")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("observation sequence must be a non-negative integer")
        _text(self.occurred_at, "observation timestamp", 64)
        _text(self.record_type, "observation record type", 96)
        _text(self.status, "observation status", 64)
        _text(self.summary, "observation summary", 512)
        if self.parent_event_id is not None:
            _event_id(self.parent_event_id, "parent event ID")
        if tuple(sorted(self.related_ids)) != self.related_ids:
            raise ValueError("observation related IDs must be sorted")
        for key, value in self.related_ids:
            _text(key, "related ID key", 64)
            _text(value, "related ID value", 128)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": OBSERVATION_EVENT_SCHEMA_VERSION,
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "source": self.source.value,
            "source_id": self.source_id,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "record_type": self.record_type,
            "phase": self.phase.value,
            "status": self.status,
            "evidence": self.evidence.value,
            "summary": self.summary,
            "parent_event_id": self.parent_event_id,
            "related_ids": dict(self.related_ids),
        }


@dataclass(frozen=True)
class ObservationBatch:
    """A cursor-aware bounded read from one live observation stream.

    ``gap`` is true when the requested cursor predates retained events or a
    subscriber had to drop events because its bounded queue was full.  A gap
    never implies that authoritative Session/Task/Child/Team records were
    deleted; consumers must re-query the durable source when they need a full
    history.
    """

    events: tuple[ObservationEvent, ...]
    next_sequence: int
    oldest_sequence: int | None
    latest_sequence: int | None
    stream_epoch: int
    gap: bool = False
    dropped_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or len(self.events) > MAX_OBSERVATION_READ_LIMIT:
            raise ValueError("observation batch events are invalid")
        if type(self.next_sequence) is not int or self.next_sequence < 0:
            raise ValueError("observation batch next sequence is invalid")
        for value, label in (
            (self.oldest_sequence, "oldest sequence"),
            (self.latest_sequence, "latest sequence"),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"observation batch {label} is invalid")
        if self.latest_sequence is not None and self.oldest_sequence is None:
            raise ValueError("observation batch latest sequence requires oldest sequence")
        if type(self.stream_epoch) is not int or self.stream_epoch < 0:
            raise ValueError("observation batch stream epoch is invalid")
        if type(self.gap) is not bool:
            raise ValueError("observation batch gap flag is invalid")
        if type(self.dropped_count) is not int or self.dropped_count < 0:
            raise ValueError("observation batch dropped count is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "events": [event.to_mapping() for event in self.events],
            "next_sequence": self.next_sequence,
            "oldest_sequence": self.oldest_sequence,
            "latest_sequence": self.latest_sequence,
            "stream_epoch": self.stream_epoch,
            "gap": self.gap,
            "dropped_count": self.dropped_count,
        }


def _decode_observation_event(value: object) -> ObservationEvent:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "event_id",
        "trace_id",
        "source",
        "source_id",
        "sequence",
        "occurred_at",
        "record_type",
        "phase",
        "status",
        "evidence",
        "summary",
        "parent_event_id",
        "related_ids",
    }:
        raise ValueError
    if value["schema_version"] != OBSERVATION_EVENT_SCHEMA_VERSION:
        raise ValueError
    related = value["related_ids"]
    if not isinstance(related, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in related.items()
    ):
        raise ValueError
    try:
        return ObservationEvent(
            event_id=value["event_id"],
            trace_id=value["trace_id"],
            source=ObservationSource(value["source"]),
            source_id=value["source_id"],
            sequence=value["sequence"],
            occurred_at=value["occurred_at"],
            record_type=value["record_type"],
            phase=ObservationPhase(value["phase"]),
            status=value["status"],
            evidence=ObservationEvidence(value["evidence"]),
            summary=value["summary"],
            parent_event_id=value["parent_event_id"],
            related_ids=tuple(sorted(related.items())),
        )
    except (TypeError, ValueError):
        raise ValueError from None


class PersistentObservationStore:
    """Small append-only cross-process observation projection.

    Durable Session/Task/Child/Team ledgers remain authoritative.  This store
    contains only the already-redacted ``ObservationEvent`` projection and is
    therefore safe to rebuild or truncate after an operator-visible gap.  A
    file lock, append+fsync, and a monotonic cursor make independent Host
    processes share one replayable observation rail without a database.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_events: int = MAX_PERSISTENT_OBSERVATION_EVENTS,
        max_bytes: int = MAX_PERSISTENT_OBSERVATION_BYTES,
    ) -> None:
        if type(max_events) is not int or not 1 <= max_events <= MAX_PERSISTENT_OBSERVATION_EVENTS:
            raise ValueError("persistent observation event limit is invalid")
        if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_PERSISTENT_OBSERVATION_BYTES:
            raise ValueError("persistent observation byte limit is invalid")
        requested = Path(path)
        if requested.is_symlink():
            raise ValueError("persistent observation path must not be a symlink")
        self.path = requested
        self.lock_path = requested.with_suffix(requested.suffix + ".lock")
        self.max_events = max_events
        self.max_bytes = max_bytes
        self._guard = RLock()

    def append(self, event: ObservationEvent) -> ObservationEvent:
        if not isinstance(event, ObservationEvent):
            raise TypeError("persistent observation requires an ObservationEvent")
        with self._guard, self._locked():
            events = self._read_unlocked()
            if any(item.event_id == event.event_id for item in events):
                return next(item for item in events if item.event_id == event.event_id)
            cursor = events[-1].sequence + 1 if events else 0
            # The process-local stream sequence is not the durable cursor.  A
            # persistent event gets a fresh global sequence and identity.
            persisted = replace(
                event,
                sequence=cursor,
                event_id="obs-v1-"
                + hashlib.sha256(
                    f"{event.trace_id}\0persistent\0{event.source_id}\0{cursor}\0{event.record_type}".encode()
                ).hexdigest(),
            )
            encoded = (
                json.dumps(persisted.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            if len(encoded) > self.max_bytes:
                raise ObservationError("persistent observation event exceeds byte limit")
            existing_bytes = self.path.stat().st_size if self.path.exists() else 0
            while events and (
                len(events) >= self.max_events or existing_bytes + len(encoded) > self.max_bytes
            ):
                # Retention is explicit: drop the oldest projection and expose
                # the resulting cursor gap to readers rather than rewriting a
                # source ledger or pretending history is complete.
                events = events[1:]
                self._rewrite_unlocked(events)
                existing_bytes = self.path.stat().st_size if self.path.exists() else 0
            self._ensure_parent()
            with self.path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            return persisted

    def read(
        self,
        *,
        after: int = -1,
        limit: int = MAX_OBSERVATION_READ_LIMIT,
        trace_id: str | None = None,
        source: ObservationSource | None = None,
        source_id: str | None = None,
    ) -> ObservationBatch:
        _validate_observation_cursor(after, limit, 0.0)
        with self._guard, self._locked():
            events = self._read_unlocked()
        if trace_id is not None:
            events = tuple(item for item in events if item.trace_id == trace_id)
        if source is not None:
            events = tuple(item for item in events if item.source is source)
        if source_id is not None:
            _uuid(source_id, "observation source ID")
            events = tuple(item for item in events if item.source_id == source_id)
        selected = tuple(item for item in events if item.sequence > after)[:limit]
        oldest = events[0].sequence if events else None
        latest = events[-1].sequence if events else None
        return ObservationBatch(
            selected,
            selected[-1].sequence + 1 if selected else max(after + 1, 0),
            oldest,
            latest,
            self.epoch,
            bool(oldest is not None and after + 1 < oldest),
        )

    @property
    def epoch(self) -> int:
        with self._guard, self._locked():
            return self._read_epoch_unlocked()

    def bump_epoch(self) -> int:
        with self._guard, self._locked():
            self._ensure_parent()
            events = self._read_unlocked()
            epoch = self._read_epoch_unlocked() + 1
            self._write_epoch_unlocked(epoch)
            # Epoch boundaries intentionally do not delete events; they tell a
            # subscriber that its old cursor belongs to a different runtime.
            del events
            return epoch

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _read_unlocked(self) -> tuple[ObservationEvent, ...]:
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
            if len(raw) > self.max_bytes:
                raise ValueError
            result: list[ObservationEvent] = []
            for line in raw.splitlines():
                if not line:
                    continue
                mapping = json.loads(line.decode("utf-8"))
                result.append(_decode_observation_event(mapping))
            if len(result) > self.max_events:
                raise ValueError
            for expected, event in enumerate(result):
                if event.sequence != (result[0].sequence + expected if result else 0):
                    raise ValueError
            return tuple(result)
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            raise ObservationError("persistent observation store is invalid") from None

    def _read_epoch_unlocked(self) -> int:
        epoch_path = self.path.with_suffix(self.path.suffix + ".epoch")
        if not epoch_path.exists():
            return 0
        try:
            value = int(epoch_path.read_text(encoding="ascii"))
        except (OSError, ValueError):
            raise ObservationError("persistent observation epoch is invalid") from None
        if value < 0:
            raise ObservationError("persistent observation epoch is invalid")
        return value

    def _write_epoch_unlocked(self, epoch: int) -> None:
        epoch_path = self.path.with_suffix(self.path.suffix + ".epoch")
        temporary = epoch_path.with_suffix(epoch_path.suffix + ".tmp")
        temporary.write_text(str(epoch), encoding="ascii")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(epoch_path)

    def _rewrite_unlocked(self, events: tuple[ObservationEvent, ...]) -> None:
        self._ensure_parent()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        data = b"".join(
            (json.dumps(item.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n").encode()
            for item in events
        )
        temporary.write_bytes(data)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    @contextmanager
    def _locked(self):
        self._ensure_parent()
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


class ObservationSubscription:
    """Bounded, non-blocking publisher subscription for local consumers."""

    def __init__(
        self, stream: "ObservationStream", subscriber_id: int, max_pending: int, stream_epoch: int
    ) -> None:
        self._stream = stream
        self._subscriber_id = subscriber_id
        self._max_pending = max_pending
        self._pending: deque[ObservationEvent] = deque()
        self._condition = Condition(RLock())
        self._closed = False
        self._gap = False
        self._dropped_count = 0
        self._stream_epoch = stream_epoch

    def _offer(self, event: ObservationEvent, stream_epoch: int) -> None:
        with self._condition:
            if self._closed:
                return
            if stream_epoch != self._stream_epoch:
                self._pending.clear()
                self._gap = False
                self._dropped_count = 0
                self._stream_epoch = stream_epoch
            if len(self._pending) >= self._max_pending:
                self._pending.popleft()
                self._gap = True
                self._dropped_count += 1
            self._pending.append(event)
            self._condition.notify_all()

    def _reset_epoch(self, stream_epoch: int) -> None:
        """Discard queued events when the stream switches Session context."""
        with self._condition:
            if self._closed:
                return
            self._pending.clear()
            self._gap = False
            self._dropped_count = 0
            self._stream_epoch = stream_epoch
            self._condition.notify_all()

    def read(
        self, *, after: int = -1, limit: int = MAX_OBSERVATION_READ_LIMIT, timeout: float = 0.0
    ) -> ObservationBatch:
        """Read queued events, optionally waiting for the next event."""
        _validate_observation_cursor(after, limit, timeout)
        deadline = time.monotonic() + float(timeout)
        with self._condition:
            while not self._closed and not any(event.sequence > after for event in self._pending):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            selected = tuple(event for event in self._pending if event.sequence > after)[:limit]
            if selected:
                next_sequence = selected[-1].sequence + 1
                oldest = self._pending[0].sequence if self._pending else None
                latest = self._pending[-1].sequence if self._pending else None
            else:
                oldest = self._pending[0].sequence if self._pending else None
                latest = self._pending[-1].sequence if self._pending else None
                next_sequence = after + 1
            batch = ObservationBatch(
                selected,
                next_sequence,
                oldest,
                latest,
                self._stream_epoch,
                self._gap or (oldest is not None and after + 1 < oldest),
                self._dropped_count,
            )
            if selected:
                consumed_until = selected[-1].sequence
                self._pending = deque(
                    event for event in self._pending if event.sequence > consumed_until
                )
                self._gap = False
                self._dropped_count = 0
            return batch

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._stream._remove_queue_subscriber(self._subscriber_id, self)

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed


class ObservationStream:
    """Bounded in-memory live event stream for one Host runtime."""

    def __init__(
        self,
        *,
        source_id: str,
        context: ObservationContext,
        retention: ObservationRetentionPolicy = ObservationRetentionPolicy(),
        store: PersistentObservationStore | None = None,
    ) -> None:
        _uuid(source_id, "observation stream source ID")
        self.source_id = source_id
        self.context = context
        self.retention = retention
        self.store = store
        self._events: deque[ObservationEvent] = deque(maxlen=retention.max_events)
        if store is not None:
            persisted = store.read(limit=MAX_OBSERVATION_READ_LIMIT, source_id=source_id).events
            self._events.extend(persisted)
            self._sequence = (persisted[-1].sequence + 1) if persisted else 0
            self._stream_epoch = store.epoch
        else:
            self._sequence = 0
            self._stream_epoch = 0
        self._lock = RLock()
        self._subscribers: dict[int, Callable[[ObservationEvent], None]] = {}
        self._queue_subscribers: dict[int, ObservationSubscription] = {}
        self._next_subscriber_id = 1

    @property
    def stream_epoch(self) -> int:
        with self._lock:
            return self._stream_epoch

    def publish_prompt(self, event: object) -> ObservationEvent:
        """Publish a content-free projection of an existing live Host event."""
        record_type = _live_record_type(event)
        status = _live_status(event)
        return self.publish(
            record_type=record_type,
            status=status,
            summary=_live_summary(event, record_type),
            related_ids=_related_ids(event),
        )

    def set_context(self, context: ObservationContext) -> None:
        """Attach the current volatile turn context to subsequent events."""
        if not isinstance(context, ObservationContext):
            raise TypeError("observation stream context is invalid")
        with self._lock:
            subscriptions: tuple[ObservationSubscription, ...] = ()
            stream_epoch = self._stream_epoch
            if context.session_id is not None and context.session_id != self.source_id:
                # A ProjectSession can switch its durable Session in place. Do not
                # let live events from the previous Session bleed into the new one.
                self.source_id = context.session_id
                self._events.clear()
                self._sequence = 0
                self._stream_epoch = (
                    self.store.bump_epoch() if self.store is not None else self._stream_epoch + 1
                )
                subscriptions = tuple(self._queue_subscribers.values())
                stream_epoch = self._stream_epoch
            self.context = context
        for subscription in subscriptions:
            subscription._reset_epoch(stream_epoch)

    def subscribe(self, callback: Callable[[ObservationEvent], None]) -> Callable[[], None]:
        """Subscribe to live events and return an idempotent unsubscribe callback.

        Subscribers are process-local presentation hooks. Callback failures are
        isolated from the Agent loop, and callbacks never run while the stream
        lock is held.
        """
        if not callable(callback):
            raise TypeError("observation subscriber must be callable")
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = callback
        removed = False

        def unsubscribe() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            with self._lock:
                self._subscribers.pop(subscriber_id, None)

        return unsubscribe

    def subscribe_queue(
        self, *, max_pending: int = MAX_OBSERVATION_SUBSCRIBER_PENDING
    ) -> ObservationSubscription:
        """Subscribe through a bounded queue without blocking ``publish``."""
        if (
            type(max_pending) is not int
            or not 1 <= max_pending <= MAX_OBSERVATION_SUBSCRIBER_PENDING
        ):
            raise ValueError("observation subscriber queue limit is invalid")
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            subscription = ObservationSubscription(
                self, subscriber_id, max_pending, self._stream_epoch
            )
            self._queue_subscribers[subscriber_id] = subscription
        return subscription

    def _remove_queue_subscriber(
        self, subscriber_id: int, subscription: ObservationSubscription
    ) -> None:
        with self._lock:
            if self._queue_subscribers.get(subscriber_id) is subscription:
                self._queue_subscribers.pop(subscriber_id, None)

    def publish(
        self,
        *,
        record_type: str,
        status: str,
        summary: str,
        related_ids: tuple[tuple[str, str], ...] = (),
        occurred_at: str | None = None,
    ) -> ObservationEvent:
        _text(record_type, "live observation record type", 96)
        _text(status, "live observation status", 64)
        _text(summary, "live observation summary", 512)
        with self._lock:
            event = ObservationEvent(
                event_id="obs-v1-"
                + hashlib.sha256(
                    f"{self.context.trace_id}\0live\0{self.source_id}\0{self._sequence}\0{record_type}".encode()
                ).hexdigest(),
                trace_id=self.context.trace_id,
                source=ObservationSource.SESSION,
                source_id=self.source_id,
                sequence=self._sequence,
                occurred_at=occurred_at or _now(),
                record_type=record_type,
                phase=ObservationPhase.OBSERVED,
                status=status,
                evidence=ObservationEvidence.HOST_OBSERVED,
                summary=summary,
                parent_event_id=self._events[-1].event_id
                if self._events
                else self.context.parent_event_id,
                related_ids=related_ids,
            )
            if self.store is not None:
                event = self.store.append(event)
            self._events.append(event)
            self._sequence = event.sequence + 1
            self._expire()
            subscribers = tuple(self._subscribers.values())
            queue_subscribers = tuple(self._queue_subscribers.values())
            stream_epoch = self._stream_epoch
        for subscription in queue_subscribers:
            subscription._offer(event, stream_epoch)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # A diagnostics consumer must never change Agent causality.
                continue
        return event

    def snapshot(self) -> tuple[ObservationEvent, ...]:
        if self.store is not None:
            return self.store.read(
                limit=MAX_OBSERVATION_READ_LIMIT, source_id=self.source_id
            ).events
        with self._lock:
            self._expire()
            return tuple(self._events)

    def read(
        self,
        *,
        after: int = -1,
        limit: int = MAX_OBSERVATION_READ_LIMIT,
    ) -> ObservationBatch:
        """Read retained events with explicit stale-cursor/gap semantics."""
        _validate_observation_cursor(after, limit, 0.0)
        if self.store is not None:
            return self.store.read(after=after, limit=limit, source_id=self.source_id)
        with self._lock:
            self._expire()
            values = tuple(self._events)
            selected = tuple(event for event in values if event.sequence > after)[:limit]
            oldest = values[0].sequence if values else None
            latest = values[-1].sequence if values else None
            next_sequence = selected[-1].sequence + 1 if selected else after + 1
            gap = oldest is not None and after + 1 < oldest
            return ObservationBatch(
                selected,
                next_sequence,
                oldest,
                latest,
                self._stream_epoch,
                gap,
            )

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._stream_epoch += 1

    def _expire(self) -> None:
        if self.retention.max_age_seconds is None or not self._events:
            return
        cutoff = datetime.now(timezone.utc).timestamp() - self.retention.max_age_seconds
        while self._events and _timestamp_epoch(self._events[0].occurred_at) < cutoff:
            self._events.popleft()


def _validate_observation_cursor(after: int, limit: int, timeout: float) -> None:
    if type(after) is not int or after < -1:
        raise ObservationError("observation cursor is invalid")
    if type(limit) is not int or not 1 <= limit <= MAX_OBSERVATION_READ_LIMIT:
        raise ObservationError("observation read limit is invalid")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 <= timeout <= MAX_OBSERVATION_WAIT_SECONDS
    ):
        raise ObservationError("observation wait timeout is invalid")


def retain_observation_events(
    events: Iterable[ObservationEvent],
    *,
    policy: ObservationRetentionPolicy = ObservationRetentionPolicy(),
) -> tuple[ObservationEvent, ...]:
    """Apply bounded inspection retention without mutating any ledger."""
    values = tuple(events)
    if policy.max_age_seconds is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - policy.max_age_seconds
        values = tuple(event for event in values if _timestamp_epoch(event.occurred_at) >= cutoff)
    if len(values) <= policy.max_events:
        return values
    return values[-policy.max_events :]


@dataclass(frozen=True)
class ObservationDiagnostic:
    """Read-only diagnosis; it never applies recovery or mutates a ledger."""

    code: str
    severity: str
    message: str
    recovery: str
    event_id: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "recovery": self.recovery,
            "event_id": self.event_id,
        }


def filter_observation_events(
    events: Iterable[ObservationEvent],
    *,
    trace_id: str | None = None,
    status: str | None = None,
    evidence: ObservationEvidence | str | None = None,
    record_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> tuple[ObservationEvent, ...]:
    """Apply bounded, deterministic filters to already Host-observed events."""
    if trace_id is not None:
        _uuid(trace_id, "trace ID")
    if evidence is not None:
        try:
            evidence = ObservationEvidence(evidence)
        except ValueError:
            raise ObservationError("observation evidence is invalid") from None
    for value, label in ((status, "status"), (record_type, "record type")):
        if value is not None:
            _text(value, f"observation {label}", 96)
    since_epoch = _parse_filter_timestamp(since, "since")
    until_epoch = _parse_filter_timestamp(until, "until")
    if since_epoch is not None and until_epoch is not None and since_epoch > until_epoch:
        raise ObservationError("observation since must not be later than until")
    selected = []
    for event in events:
        if trace_id is not None and event.trace_id != trace_id:
            continue
        if status is not None and event.status != status:
            continue
        if evidence is not None and event.evidence is not evidence:
            continue
        if record_type is not None and event.record_type != record_type:
            continue
        timestamp = _timestamp_epoch(event.occurred_at)
        if since_epoch is not None and timestamp < since_epoch:
            continue
        if until_epoch is not None and timestamp > until_epoch:
            continue
        selected.append(event)
    return tuple(selected)


def diagnose_observation_events(
    events: Iterable[ObservationEvent],
    *,
    stale_after_seconds: int = 300,
) -> tuple[ObservationDiagnostic, ...]:
    """Report actionable inconsistencies without claiming a recovery occurred."""
    if type(stale_after_seconds) is not int or not 1 <= stale_after_seconds <= 86_400:
        raise ObservationError("diagnostic stale threshold is invalid")
    values = tuple(events)
    diagnostics: list[ObservationDiagnostic] = []
    event_ids = {event.event_id for event in values}
    for event in values:
        if event.parent_event_id is not None and event.parent_event_id not in event_ids:
            diagnostics.append(
                ObservationDiagnostic(
                    "missing-parent-link",
                    "warning",
                    f"{event.record_type} references an unavailable parent observation",
                    "re-run the read-only timeline query and inspect the source ledger",
                    event.event_id,
                )
            )
        if event.evidence is ObservationEvidence.UNTRUSTED:
            diagnostics.append(
                ObservationDiagnostic(
                    "untrusted-evidence",
                    "info",
                    f"{event.record_type} is not Host-verified evidence",
                    "re-observe the durable source before treating the result as a fact",
                    event.event_id,
                )
            )
        if event.status in {"failed", "error", "unknown", "outcome-unknown"}:
            diagnostics.append(
                ObservationDiagnostic(
                    "failed-or-unknown",
                    "warning",
                    f"{event.record_type} ended with status {event.status}",
                    "inspect the exact source record; do not retry automatically",
                    event.event_id,
                )
            )
        if event.source is ObservationSource.BACKGROUND and event.status == "claimed":
            age = datetime.now(timezone.utc).timestamp() - _timestamp_epoch(event.occurred_at)
            if age >= stale_after_seconds:
                diagnostics.append(
                    ObservationDiagnostic(
                        "stale-background-lease",
                        "warning",
                        "background submission has not reported a recent heartbeat",
                        "run the exact Child recovery/observation command; do not retry blindly",
                        event.event_id,
                    )
                )
    return tuple(diagnostics)


def project_background_items(
    items: Iterable[object],
    *,
    trace_id: str,
) -> tuple[ObservationEvent, ...]:
    """Project current durable queue snapshots as bounded Host observations."""
    _uuid(trace_id, "trace ID")
    events: list[ObservationEvent] = []
    for sequence, item in enumerate(items):
        submission_id = getattr(item, "submission_id", None)
        child_run_id = getattr(item, "child_run_id", None)
        if not isinstance(submission_id, str) or not isinstance(child_run_id, str):
            continue
        _uuid(submission_id, "submission ID")
        _uuid(child_run_id, "Child Run ID")
        state = getattr(item, "state", "unknown")
        status = getattr(item, "terminal_child_status", None) or state
        occurred_at = (
            getattr(item, "heartbeat_at", None)
            or getattr(item, "claimed_at", None)
            or getattr(item, "queued_at", None)
        )
        if not isinstance(occurred_at, str):
            continue
        record_type = f"background_{state}"
        phase, _ = _phase_status(record_type)
        event_id = (
            "obs-v1-"
            + hashlib.sha256(
                f"{trace_id}\0background\0{submission_id}\0{sequence}\0{record_type}".encode()
            ).hexdigest()
        )
        related = {
            "child_run_id": child_run_id,
            "submission_id": submission_id,
        }
        for name in ("worker_id", "lease_id"):
            value = getattr(item, name, None)
            if isinstance(value, str):
                related[name] = value
        events.append(
            ObservationEvent(
                event_id=event_id,
                trace_id=trace_id,
                source=ObservationSource.BACKGROUND,
                source_id=submission_id,
                sequence=sequence,
                occurred_at=occurred_at,
                record_type=record_type,
                phase=phase,
                status=str(status),
                evidence=ObservationEvidence.HOST_OBSERVED,
                summary=(
                    f"background submission {state} "
                    f"effect={getattr(item, 'effect_state', 'confirmed')}"
                ),
                related_ids=tuple(sorted(related.items())),
            )
        )
    return tuple(events)


def observation_event_json(event: ObservationEvent) -> str:
    return json.dumps(event.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def project_session_records(
    session_id: str,
    records: Iterable[object],
    *,
    context: ObservationContext | None = None,
) -> tuple[ObservationEvent, ...]:
    return _project(
        ObservationSource.SESSION,
        session_id,
        records,
        context=context,
        untrusted_types=frozenset({"child_handoff_delivered"}),
    )


def project_task_records(
    task_id: str,
    records: Iterable[object],
    *,
    context: ObservationContext | None = None,
) -> tuple[ObservationEvent, ...]:
    return _project(ObservationSource.TASK, task_id, records, context=context)


def project_child_records(
    child_run_id: str,
    records: Iterable[object],
    *,
    context: ObservationContext | None = None,
) -> tuple[ObservationEvent, ...]:
    return _project(
        ObservationSource.CHILD,
        child_run_id,
        records,
        context=context,
        untrusted_types=frozenset({"child_run_handoff_published"}),
    )


def project_team_records(
    team_id: str,
    records: Iterable[object],
    *,
    context: ObservationContext | None = None,
) -> tuple[ObservationEvent, ...]:
    return _project(ObservationSource.TEAM, team_id, records, context=context)


def merge_observation_events(
    event_groups: Iterable[Iterable[ObservationEvent]],
) -> tuple[ObservationEvent, ...]:
    """Merge projections and link ledger roots through existing durable IDs."""
    groups = tuple(tuple(group) for group in event_groups)
    events = [event for group in groups for event in group]
    anchors = _cross_source_anchors(events)
    correlated: list[ObservationEvent] = []
    for group in groups:
        if not group:
            continue
        root = min(group, key=lambda item: item.sequence)
        anchor = anchors.get((root.source, root.source_id))
        for event in group:
            correlated.append(
                replace(event, parent_event_id=anchor)
                if event.event_id == root.event_id and anchor is not None
                else event
            )
    events = sorted(
        correlated,
        key=lambda item: (item.occurred_at, item.sequence, item.source.value, item.event_id),
    )
    return tuple(events)


def _project(
    source: ObservationSource,
    source_id: str,
    records: Iterable[object],
    *,
    context: ObservationContext | None,
    untrusted_types: frozenset[str] = frozenset(),
) -> tuple[ObservationEvent, ...]:
    _uuid(source_id, f"{source.value} ID")
    ctx = context or ObservationContext(trace_id=source_id)
    if ctx.trace_id != source_id and ctx.session_id is None:
        ctx = ctx.child(session_id=source_id if source is ObservationSource.SESSION else None)
    events: list[ObservationEvent] = []
    previous: str | None = ctx.parent_event_id
    for record in records:
        record_type = _safe_record_type(record)
        sequence = getattr(record, "sequence", None)
        occurred_at = _timestamp(record)
        if type(sequence) is not int or sequence < 0 or occurred_at is None:
            continue
        phase, status = _phase_status(record_type)
        evidence = (
            ObservationEvidence.UNTRUSTED
            if record_type in untrusted_types
            else ObservationEvidence.HOST_VERIFIED
            if source in {ObservationSource.SESSION, ObservationSource.TASK, ObservationSource.TEAM}
            else ObservationEvidence.HOST_OBSERVED
        )
        event_id = (
            "obs-v1-"
            + hashlib.sha256(
                f"{ctx.trace_id}\0{source.value}\0{source_id}\0{sequence}\0{record_type}".encode()
            ).hexdigest()
        )
        event = ObservationEvent(
            event_id=event_id,
            trace_id=ctx.trace_id,
            source=source,
            source_id=source_id,
            sequence=sequence,
            occurred_at=occurred_at,
            record_type=record_type,
            phase=phase,
            status=status,
            evidence=evidence,
            summary=_summary(record_type, phase, status),
            parent_event_id=previous,
            related_ids=_related_ids(record),
        )
        events.append(event)
        previous = event_id
    return tuple(sorted(events, key=lambda item: (item.occurred_at, item.sequence, item.event_id)))


def _safe_record_type(record: object) -> str:
    value = getattr(record, "record_type", type(record).__name__)
    return value if isinstance(value, str) and value and len(value) <= 96 else type(record).__name__


def _timestamp(record: object) -> str | None:
    for name in (
        "occurred_at",
        "created_at",
        "committed_at",
        "started_at",
        "delegated_at",
        "admitted_at",
        "completed_at",
        "failed_at",
        "assigned_at",
        "configured_at",
        "joined_at",
        "disabled_at",
        "enabled_at",
        "left_at",
        "bound_at",
        "observed_at",
        "published_at",
        "requested_at",
        "cancelled_at",
        "interrupted_at",
        "closed_at",
        "finished_at",
        "read_at",
        "sent_at",
        "released_at",
        "verified_at",
        "checked_at",
        "delivered_at",
        "accepted_at",
        "proposed_at",
        "recorded_at",
    ):
        value = getattr(record, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _phase_status(record_type: str) -> tuple[ObservationPhase, str]:
    lowered = record_type.lower()
    if lowered.endswith("_header") or "created" in lowered:
        return ObservationPhase.CREATED, "created"
    if "failed" in lowered or "error" in lowered:
        return ObservationPhase.FAILED, "failed"
    if "recovered" in lowered or "recovery" in lowered or "resumed" in lowered:
        return ObservationPhase.RECOVERED, "recovered"
    if "started" in lowered or "requested" in lowered:
        return (
            ObservationPhase.STARTED if "started" in lowered else ObservationPhase.REQUESTED,
            "in-progress" if "started" in lowered else "requested",
        )
    if "committed" in lowered:
        return ObservationPhase.COMMITTED, "committed"
    if "completed" in lowered or "finished" in lowered:
        return ObservationPhase.FINISHED, "completed"
    if "handoff" in lowered or "observed" in lowered or "delivered" in lowered:
        return ObservationPhase.OBSERVED, "observed"
    if "closed" in lowered or "cancelled" in lowered or "terminated" in lowered:
        return ObservationPhase.FINISHED, "terminal"
    return ObservationPhase.CHANGED, "recorded"


def _summary(record_type: str, phase: ObservationPhase, status: str) -> str:
    return f"{record_type}: {phase.value} ({status})"


def _related_ids(record: object) -> tuple[tuple[str, str], ...]:
    names = (
        "session_id",
        "owner_session_id",
        "parent_session_id",
        "source_session_id",
        "child_session_id",
        "task_id",
        "stage_id",
        "child_run_id",
        "parent_child_run_id",
        "root_child_run_id",
        "team_id",
        "member_id",
        "assignment_id",
        "schedule_run_id",
        "work_item_id",
        "message_id",
        "action_request_id",
        "admission_id",
        "plan_id",
        "completion_stage_id",
        "proposal_tool_use_id",
        "tool_use_id",
        "parent_tool_use_id",
        "context_id",
        "parent_context_id",
        "target_team_id",
        "delivery_id",
        "reply_message_id",
        "execution_id",
        "turn_id",
    )
    values = {
        name: value
        for name in names
        if isinstance(value := getattr(record, name, None), str) and value
    }
    return tuple(sorted(values.items()))


def _uuid(value: object, label: str) -> None:
    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"{label} must be a UUID4") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{label} must be a canonical UUID4")


def _event_id(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("obs-v1-")
        or len(value) != 71
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} is invalid")


def _cross_source_anchors(
    events: Iterable[ObservationEvent],
) -> dict[tuple[ObservationSource, str], str]:
    """Choose the strongest existing causal record for each foreign ledger root."""
    all_events = tuple(events)
    session_task: dict[str, str] = {}
    session_child_tool: dict[tuple[str, str], str] = {}
    session_team: dict[str, str] = {}
    task_child: dict[str, str] = {}
    task_team: dict[str, str] = {}
    team_child: dict[str, str] = {}

    for event in all_events:
        related = dict(event.related_ids)
        if event.source is ObservationSource.SESSION:
            task_id = related.get("task_id")
            if event.record_type == "task_admission_resolved" and task_id:
                session_task[task_id] = event.event_id
            if event.record_type == "child_delegation_decided":
                session_id = related.get("parent_session_id")
                tool_use_id = related.get("tool_use_id")
                if session_id and tool_use_id:
                    session_child_tool[(session_id, tool_use_id)] = event.event_id
            team_id = related.get("target_team_id")
            if event.record_type == "team_control_decided" and team_id:
                session_team[team_id] = event.event_id
        elif event.source is ObservationSource.TASK and event.record_type == "stage_delegated":
            child_id = related.get("child_run_id")
            team_id = related.get("team_id")
            if child_id:
                task_child[child_id] = event.event_id
            if team_id:
                task_team[team_id] = event.event_id
        elif (
            event.source is ObservationSource.TEAM
            and event.record_type == "team_assignment_created"
        ):
            child_id = related.get("child_run_id")
            if child_id:
                team_child[child_id] = event.event_id

    anchors: dict[tuple[ObservationSource, str], str] = {}
    by_ledger: dict[tuple[ObservationSource, str], list[ObservationEvent]] = {}
    for event in all_events:
        by_ledger.setdefault((event.source, event.source_id), []).append(event)
    for key, ledger in by_ledger.items():
        source, source_id = key
        related = {}
        for event in ledger:
            related.update(event.related_ids)
        anchor = None
        if source is ObservationSource.TASK:
            anchor = session_task.get(source_id)
        elif source is ObservationSource.TEAM:
            anchor = task_team.get(source_id) or session_team.get(source_id)
        elif source is ObservationSource.CHILD:
            parent_session = related.get("parent_session_id")
            parent_tool = related.get("parent_tool_use_id")
            anchor = task_child.get(source_id) or team_child.get(source_id)
            if anchor is None and parent_session and parent_tool:
                anchor = session_child_tool.get((parent_session, parent_tool))
        if anchor is not None:
            anchors[key] = anchor
    return anchors


def _text(value: object, label: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum or not value.isprintable():
        raise ValueError(f"{label} is invalid")


def _parse_filter_timestamp(value: str | None, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ObservationError(f"observation {label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ObservationError(f"observation {label} timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise ObservationError(f"observation {label} timestamp must include a timezone")
    return parsed.timestamp()


def _timestamp_epoch(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _live_record_type(event: object) -> str:
    """Normalize a live PromptEvent class without retaining its payload."""
    name = type(event).__name__
    if name.endswith("Event"):
        name = name[:-5]
    return "live_" + "_".join(part.lower() for part in _camel_parts(name) if part)


def _live_status(event: object) -> str:
    for name in ("status", "outcome", "result"):
        value = getattr(event, name, None)
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, str) and value:
            return value[:64]
    name = type(event).__name__.lower()
    if "failed" in name or "error" in name or "notapplied" in name:
        return "failed"
    if "started" in name or "received" in name or "proposed" in name:
        return "started"
    if "committed" in name or "prepared" in name or "completed" in name:
        return "completed"
    return "observed"


def _live_summary(event: object, record_type: str) -> str:
    """Describe stream delivery without retaining response content."""
    text = getattr(event, "text", None)
    if record_type == "live_assistant_response_text_delta_received" and isinstance(text, str):
        return f"{record_type} chars={len(text)} bytes={len(text.encode('utf-8'))}"
    if record_type == "live_provider_invocation_finished":
        elapsed = getattr(event, "elapsed_milliseconds", None)
        delta_count = getattr(event, "delta_count", 0)
        first_delta = getattr(event, "first_delta_milliseconds", None)
        max_gap = getattr(event, "max_delta_gap_milliseconds", None)
        retry_count = getattr(event, "retry_count", 0)
        return (
            f"{record_type} elapsed_ms={elapsed if elapsed is not None else 'none'} "
            f"delta_count={delta_count} "
            f"first_delta_ms={first_delta if first_delta is not None else 'none'} "
            f"max_delta_gap_ms={max_gap if max_gap is not None else 'none'} "
            f"retry_count={retry_count}"
        )
    return record_type


def _camel_parts(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    current = ""
    for character in value:
        if character.isupper() and current:
            parts.append(current)
            current = character
        else:
            current += character
    if current:
        parts.append(current)
    return tuple(parts)
