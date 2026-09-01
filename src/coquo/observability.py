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
from datetime import datetime, timezone
from threading import RLock
from collections.abc import Callable
from uuid import UUID, uuid4


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


class ObservationStream:
    """Bounded in-memory live event stream for one Host runtime."""

    def __init__(
        self,
        *,
        source_id: str,
        context: ObservationContext,
        retention: ObservationRetentionPolicy = ObservationRetentionPolicy(),
    ) -> None:
        _uuid(source_id, "observation stream source ID")
        self.source_id = source_id
        self.context = context
        self.retention = retention
        self._events: deque[ObservationEvent] = deque(maxlen=retention.max_events)
        self._sequence = 0
        self._lock = RLock()
        self._subscribers: dict[int, Callable[[ObservationEvent], None]] = {}
        self._next_subscriber_id = 1

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
            if context.session_id is not None and context.session_id != self.source_id:
                # A ProjectSession can switch its durable Session in place. Do not
                # let live events from the previous Session bleed into the new one.
                self.source_id = context.session_id
                self._events.clear()
                self._sequence = 0
            self.context = context

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
            self._events.append(event)
            self._sequence += 1
            self._expire()
            subscribers = tuple(self._subscribers.values())
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # A diagnostics consumer must never change Agent causality.
                continue
        return event

    def snapshot(self) -> tuple[ObservationEvent, ...]:
        with self._lock:
            self._expire()
            return tuple(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def _expire(self) -> None:
        if self.retention.max_age_seconds is None or not self._events:
            return
        cutoff = datetime.now(timezone.utc).timestamp() - self.retention.max_age_seconds
        while self._events and _timestamp_epoch(self._events[0].occurred_at) < cutoff:
            self._events.popleft()


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
