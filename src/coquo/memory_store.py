"""Durable local store for explicit long-term semantic-memory records."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import stat
from threading import RLock
from typing import Iterator, Mapping

from coquo.memory import (
    MEMORY_MAX_EVENTS,
    MEMORY_MAX_EVENT_BYTES,
    MEMORY_MAX_EVENT_LOG_BYTES,
    MEMORY_MAX_ACTIVE_RECORDS,
    MEMORY_MAX_SEARCH_RESULTS,
    MemoryError,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
)
from coquo.memory_observability import MemoryObservationLedger

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class MemoryStoreError(MemoryError):
    """Raised when the local memory log cannot be safely read or written."""


class _ReplayState(dict[str, MemoryRecord]):
    """Current records plus the independent append-only event count."""

    def __init__(self, *args, event_count: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.event_count = event_count


class MemoryStore:
    """Append-only, replayable memory event log under the workspace ``.coquo``."""

    def __init__(
        self,
        workspace: Path,
        *,
        observation_ledger: MemoryObservationLedger | None = None,
    ) -> None:
        original = Path(workspace)
        resolved = original.resolve(strict=True)
        if original.is_symlink() or not resolved.is_dir():
            raise MemoryStoreError("workspace must be an existing non-symlink directory")
        self.workspace = resolved
        self.root = resolved / ".coquo" / "memory"
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / ".memory.lock"
        self._thread_lock = RLock()
        self._observations = observation_ledger or MemoryObservationLedger()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._thread_lock:
            self._ensure_root()
            stream = _open_lock(self.lock_path)
            try:
                _lock(stream)
                yield
            finally:
                try:
                    _unlock(stream)
                finally:
                    stream.close()

    def list(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        status: MemoryStatus | None = None,
        limit: int = MEMORY_MAX_SEARCH_RESULTS,
    ) -> tuple[MemoryRecord, ...]:
        if not isinstance(limit, int) or not 1 <= limit <= MEMORY_MAX_SEARCH_RESULTS:
            raise MemoryStoreError("memory list limit is out of bounds")
        records = self._read_records()
        result = [
            record
            for record in records.values()
            if (scope is None or record.scope is scope)
            and (scope_id is None or record.scope_id == scope_id)
            and (status is None or record.status is status)
        ]
        result.sort(key=lambda item: (item.created_at, item.memory_id), reverse=True)
        return tuple(result[:limit])

    def count(self) -> int:
        """Return the number of replayed records, including terminal records."""

        return len(self._read_records())

    def get(self, memory_id: str) -> MemoryRecord:
        records = self._read_records()
        try:
            return records[memory_id]
        except KeyError:
            raise MemoryStoreError(f"memory record does not exist: {memory_id}") from None

    def create_candidate(
        self,
        content: str,
        *,
        scope: MemoryScope,
        scope_id: str,
        category: str = "fact",
        confidence: float = 0.5,
        source_session_id: str | None = None,
        source_turn: int | None = None,
    ) -> MemoryRecord:
        try:
            record = MemoryRecord.candidate(
                content,
                scope=scope,
                scope_id=scope_id,
                category=category,
                confidence=confidence,
                source_session_id=source_session_id,
                source_turn=source_turn,
            )
        except MemoryError as error:
            raise MemoryStoreError(str(error)) from None
        with self._transaction():
            records = self._read_records()
            self._evict_for_capacity(records)
            self._append(record, "created", records)
        self._observe("create", "candidate", record)
        return record

    def confirm(self, memory_id: str) -> MemoryRecord:
        with self._transaction():
            records = self._read_records()
            current = self._require(records, memory_id)
            try:
                updated = current.confirm()
            except MemoryError as error:
                raise MemoryStoreError(str(error)) from None
            self._append(updated, "confirmed", records)
        self._observe("confirm", "completed", updated)
        return updated

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        reason: str | None = None,
    ) -> MemoryRecord:
        _validate_reason(reason)
        with self._transaction():
            records = self._read_records()
            current = self._require(records, memory_id)
            try:
                updated = current.update_fields(
                    content=content,
                    category=category,
                    confidence=confidence,
                )
            except MemoryError as error:
                raise MemoryStoreError(str(error)) from None
            self._append(updated, "updated", records, reason=reason)
        self._observe("update", "completed", updated)
        return updated

    def transition(
        self, memory_id: str, status: MemoryStatus, *, reason: str | None = None
    ) -> MemoryRecord:
        if status not in {
            MemoryStatus.STALE,
            MemoryStatus.DELETED,
            MemoryStatus.EVICTED,
            MemoryStatus.CONFIRMED,
        }:
            raise MemoryStoreError("unsupported memory transition")
        _validate_reason(reason)
        with self._transaction():
            records = self._read_records()
            current = self._require(records, memory_id)
            try:
                updated = current.transition(status)
            except MemoryError as error:
                raise MemoryStoreError(str(error)) from None
            self._append(updated, status.value, records, reason=reason)
        self._observe(status.value, "completed", updated)
        return updated

    def search(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = MEMORY_MAX_SEARCH_RESULTS,
        statuses: frozenset[MemoryStatus] | None = None,
        touch: bool = True,
    ) -> tuple[MemoryRecord, ...]:
        if not isinstance(query, str) or not query.strip():
            raise MemoryStoreError("memory search query must not be blank")
        if len(query) > 512 or "\x00" in query:
            raise MemoryStoreError("memory search query is invalid")
        if not isinstance(limit, int) or not 1 <= limit <= MEMORY_MAX_SEARCH_RESULTS:
            raise MemoryStoreError("memory search limit is out of bounds")
        selected_statuses = (
            statuses
            if statuses is not None
            else frozenset({MemoryStatus.CANDIDATE, MemoryStatus.CONFIRMED, MemoryStatus.STALE})
        )
        if (
            not isinstance(selected_statuses, frozenset)
            or not selected_statuses
            or any(not isinstance(status, MemoryStatus) for status in selected_statuses)
            or any(
                status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}
                for status in selected_statuses
            )
        ):
            raise MemoryStoreError("memory search statuses are invalid")
        if type(touch) is not bool:
            raise MemoryStoreError("memory search touch flag is invalid")
        records = self._read_records()
        matches = [
            record
            for record in records.values()
            if record.status in selected_statuses
            and (scope is None or record.scope is scope)
            and (scope_id is None or record.scope_id == scope_id)
            and query.casefold() in record.content.casefold()
        ]
        matches.sort(key=lambda item: (item.created_at, item.memory_id), reverse=True)
        matches = matches[:limit]
        if matches and touch:
            self.mark_recalled(tuple(record.memory_id for record in matches))
        return tuple(matches)

    def mark_recalled(self, memory_ids: tuple[str, ...]) -> tuple[MemoryRecord, ...]:
        """Touch each named active record once after final recall selection."""
        if (
            not isinstance(memory_ids, tuple)
            or len(set(memory_ids)) != len(memory_ids)
            or any(not isinstance(memory_id, str) or not memory_id for memory_id in memory_ids)
        ):
            raise MemoryStoreError("memory recall IDs are invalid")
        if not memory_ids:
            return ()
        with self._transaction():
            records = self._read_records()
            updated_records: list[MemoryRecord] = []
            for memory_id in memory_ids:
                current = self._require(records, memory_id)
                if current.status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}:
                    raise MemoryStoreError("terminal memory cannot be recalled")
                updated = current.recalled()
                self._append(updated, "recalled", records)
                updated_records.append(updated)
        for record in updated_records:
            self._observe("recall", "completed", record)
        return tuple(updated_records)

    def find_exact(
        self,
        content: str,
        *,
        scope: MemoryScope,
        scope_id: str,
        category: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return active records with the same normalized content and scope."""
        normalized = _normalize_memory_text(content)
        records = self._read_records()
        result = [
            record
            for record in records.values()
            if record.status in {MemoryStatus.CANDIDATE, MemoryStatus.CONFIRMED, MemoryStatus.STALE}
            and record.scope is scope
            and record.scope_id == scope_id
            and (category is None or record.category == category)
            and _normalize_memory_text(record.content) == normalized
        ]
        result.sort(key=lambda item: (item.created_at, item.memory_id), reverse=True)
        return tuple(result[:MEMORY_MAX_SEARCH_RESULTS])

    def possible_conflicts(self, memory_id: str, *, limit: int = 20) -> tuple[MemoryRecord, ...]:
        """Enumerate same-scope, same-category facts for explicit Host review."""
        if not 1 <= limit <= MEMORY_MAX_SEARCH_RESULTS:
            raise MemoryStoreError("memory conflict limit is out of bounds")
        records = self._read_records()
        current = self._require(records, memory_id)
        result = [
            record
            for record in records.values()
            if record.memory_id != current.memory_id
            and record.status is MemoryStatus.CONFIRMED
            and record.scope is current.scope
            and record.scope_id == current.scope_id
            and record.category == current.category
            and _normalize_memory_text(record.content) != _normalize_memory_text(current.content)
        ]
        result.sort(key=lambda item: (item.updated_at, item.memory_id), reverse=True)
        return tuple(result[:limit])

    def consolidate(
        self,
        memory_id: str,
        *,
        content: str,
        duplicate_ids: tuple[str, ...] = (),
        reason: str = "host_consolidation",
    ) -> MemoryRecord:
        """Update one candidate and stale only explicitly named candidate duplicates."""
        if not isinstance(duplicate_ids, tuple) or len(set(duplicate_ids)) != len(duplicate_ids):
            raise MemoryStoreError("memory consolidation duplicate IDs are invalid")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
            raise MemoryStoreError("memory consolidation reason is invalid")
        with self._transaction():
            records = self._read_records()
            current = self._require(records, memory_id)
            if current.status is not MemoryStatus.CANDIDATE:
                raise MemoryStoreError("only a candidate can be consolidated")
            try:
                updated = current.update_fields(content=content)
            except MemoryError as error:
                raise MemoryStoreError(str(error)) from None
            duplicates: list[MemoryRecord] = []
            for duplicate_id in duplicate_ids:
                duplicate = self._require(records, duplicate_id)
                if duplicate.memory_id == current.memory_id:
                    raise MemoryStoreError("memory consolidation cannot duplicate its target")
                if duplicate.scope is not current.scope or duplicate.scope_id != current.scope_id:
                    raise MemoryStoreError("memory consolidation scope mismatch")
                if duplicate.status is not MemoryStatus.CANDIDATE:
                    raise MemoryStoreError("only candidate duplicates can be consolidated")
                duplicates.append(duplicate)
            required_events = 1 + len(duplicates)
            if records.event_count + required_events > MEMORY_MAX_EVENTS:
                raise MemoryStoreError("memory consolidation would exceed the event limit")
            appended = 0
            attempted = 0
            try:
                attempted += 1
                self._append(updated, "updated", records, reason=reason)
                appended += 1
                for duplicate in duplicates:
                    stale = duplicate.transition(MemoryStatus.STALE)
                    attempted += 1
                    self._append(stale, "stale", records, reason=reason)
                    appended += 1
            except MemoryStoreError:
                if attempted:
                    self._observations.record(
                        "consolidate",
                        "partial",
                        actor="host",
                        scope_kinds=(current.scope.value,),
                        record_count=appended,
                        reason="append_outcome_unknown",
                    )
                    raise MemoryStoreError(
                        "memory consolidation may be partially committed; inspect before retrying"
                    ) from None
                raise
        self._observations.record(
            "consolidate",
            "completed",
            actor="host",
            scope_kinds=(current.scope.value,),
            record_count=required_events,
            reason=reason,
        )
        return updated

    def reinforce(self, memory_id: str, *, confidence_delta: float = 0.05) -> MemoryRecord:
        """Increase confidence within bounds and record a durable reinforcement event."""
        if isinstance(confidence_delta, bool) or not isinstance(confidence_delta, (int, float)):
            raise MemoryStoreError("memory reinforcement delta is invalid")
        if not 0.0 < float(confidence_delta) <= 1.0:
            raise MemoryStoreError("memory reinforcement delta is out of bounds")
        with self._transaction():
            records = self._read_records()
            current = self._require(records, memory_id)
            if current.status is not MemoryStatus.CONFIRMED:
                raise MemoryStoreError("only confirmed memory can be reinforced")
            updated = current.update_fields(
                confidence=min(1.0, current.confidence + float(confidence_delta))
            )
            self._append(updated, "updated", records, reason="reinforced")
        self._observe("reinforce", "completed", updated, reason="reinforced")
        return updated

    def review_stale(
        self, before: str, *, limit: int = MEMORY_MAX_SEARCH_RESULTS
    ) -> tuple[MemoryRecord, ...]:
        """Mark old confirmed facts stale in deterministic update order."""
        if not isinstance(before, str):
            raise MemoryStoreError("memory stale review boundary is invalid")
        try:
            boundary = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError:
            raise MemoryStoreError("memory stale review boundary is invalid") from None
        if boundary.tzinfo is None or not 1 <= limit <= MEMORY_MAX_SEARCH_RESULTS:
            raise MemoryStoreError("memory stale review arguments are invalid")
        with self._transaction():
            records = self._read_records()
            candidates = sorted(
                (
                    record
                    for record in records.values()
                    if record.status is MemoryStatus.CONFIRMED
                    and datetime.fromisoformat(record.updated_at.replace("Z", "+00:00")) < boundary
                ),
                key=lambda item: (item.updated_at, item.memory_id),
            )[:limit]
            updated: list[MemoryRecord] = []
            for record in candidates:
                stale = record.transition(MemoryStatus.STALE)
                self._append(stale, "stale", records, reason="stale_review")
                updated.append(stale)
        for record in updated:
            self._observe("stale_review", "completed", record, reason="stale_review")
        return tuple(updated)

    def evict_oldest(self, *, limit: int = 1) -> tuple[MemoryRecord, ...]:
        """Evict the oldest active records without removing their event history."""
        if not 1 <= limit <= MEMORY_MAX_SEARCH_RESULTS:
            raise MemoryStoreError("memory eviction limit is out of bounds")
        with self._transaction():
            records = self._read_records()
            candidates = sorted(
                (
                    record
                    for record in records.values()
                    if record.status
                    in {MemoryStatus.CANDIDATE, MemoryStatus.CONFIRMED, MemoryStatus.STALE}
                ),
                key=lambda item: (item.updated_at, item.memory_id),
            )[:limit]
            updated: list[MemoryRecord] = []
            for record in candidates:
                evicted = record.transition(MemoryStatus.EVICTED)
                self._append(evicted, "evicted", records, reason="capacity_eviction")
                updated.append(evicted)
        for record in updated:
            self._observe("eviction", "completed", record, reason="capacity_eviction")
        return tuple(updated)

    def observations(self, limit: int = 256) -> tuple:
        return self._observations.snapshot(limit)

    def _observe(
        self,
        operation: str,
        outcome: str,
        record: MemoryRecord,
        *,
        reason: str | None = None,
    ) -> None:
        self._observations.record(
            operation,
            outcome,
            actor="host",
            scope_kinds=(record.scope.value,),
            record_count=1,
            reason=reason,
        )

    def _read_records(self) -> _ReplayState:
        if not self.events_path.exists():
            return _ReplayState()
        if self.events_path.is_symlink() or not self.events_path.is_file():
            raise MemoryStoreError("memory event log must be a regular file")
        try:
            descriptor = os.open(
                self.events_path,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            raise MemoryStoreError("memory event log is not readable") from None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MEMORY_MAX_EVENT_LOG_BYTES:
                raise MemoryStoreError("memory event log exceeds its size limit")
            raw = os.read(descriptor, MEMORY_MAX_EVENT_LOG_BYTES + 1)
            after = os.fstat(descriptor)
            if (
                len(raw) != after.st_size
                or len(raw) > MEMORY_MAX_EVENT_LOG_BYTES
                or (before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise MemoryStoreError("memory event log changed while reading")
        except OSError:
            raise MemoryStoreError("memory event log could not be read") from None
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise MemoryStoreError("memory event log ends with an incomplete record")
        records = _ReplayState()
        lines = raw.splitlines()
        if len(lines) > MEMORY_MAX_EVENTS:
            raise MemoryStoreError("memory event log contains too many events")
        for line in lines:
            if len(line) > MEMORY_MAX_EVENT_BYTES:
                raise MemoryStoreError("memory event exceeds its size limit")
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise MemoryStoreError("memory event log contains invalid JSON") from None
            if not isinstance(event, dict) or set(event) not in (
                {"event", "record", "version"},
                {"event", "record", "version", "reason"},
            ):
                raise MemoryStoreError("memory event schema is invalid")
            if "reason" in event and (
                not isinstance(event["reason"], str)
                or not event["reason"].strip()
                or len(event["reason"]) > 256
            ):
                raise MemoryStoreError("memory event reason is invalid")
            if event.get("version") != 1 or not isinstance(event.get("event"), str):
                raise MemoryStoreError("memory event version is invalid")
            record = MemoryRecord.from_mapping(event.get("record"))
            previous = records.get(record.memory_id)
            kind = event["event"]
            if previous is None:
                if kind != "created":
                    raise MemoryStoreError("memory event log must begin with created")
            elif kind == "created":
                raise MemoryStoreError("memory record has duplicate created event")
            _validate_transition(previous, record, kind)
            records[record.memory_id] = record
        records.event_count = len(lines)
        return records

    def _append(
        self,
        record: MemoryRecord,
        event: str,
        records: _ReplayState,
        *,
        reason: str | None = None,
    ) -> None:
        if records.event_count >= MEMORY_MAX_EVENTS:
            raise MemoryStoreError("memory event limit reached")
        event_mapping: dict[str, object] = {
            "event": event,
            "record": record.to_mapping(),
            "version": 1,
        }
        if reason is not None:
            event_mapping["reason"] = reason
        payload = (
            json.dumps(
                event_mapping,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > MEMORY_MAX_EVENT_BYTES:
            raise MemoryStoreError("memory event exceeds its size limit")
        current_size = self.events_path.stat().st_size if self.events_path.exists() else 0
        if current_size + len(payload) > MEMORY_MAX_EVENT_LOG_BYTES:
            raise MemoryStoreError("memory event log would exceed its size limit")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.events_path, flags, 0o600)
        except OSError:
            raise MemoryStoreError("memory event log could not be opened for append") from None
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("memory event append was incomplete")
            os.fsync(descriptor)
        except OSError:
            raise MemoryStoreError("memory event append failed; inspect before retrying") from None
        finally:
            os.close(descriptor)
        _fsync_directory(self.root)
        records[record.memory_id] = record
        records.event_count += 1

    def _evict_for_capacity(self, records: _ReplayState) -> None:
        active = [
            record
            for record in records.values()
            if record.status in {MemoryStatus.CANDIDATE, MemoryStatus.CONFIRMED, MemoryStatus.STALE}
        ]
        if len(active) < MEMORY_MAX_ACTIVE_RECORDS:
            return
        oldest = min(active, key=lambda item: (item.updated_at, item.memory_id))
        evicted = oldest.transition(MemoryStatus.EVICTED)
        self._append(evicted, "evicted", records, reason="capacity_eviction")
        self._observe("eviction", "completed", evicted, reason="capacity_eviction")

    def _ensure_root(self) -> None:
        parent = self.root.parent
        try:
            parent.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise MemoryStoreError(".coquo could not be created") from None
        if parent.is_symlink() or not parent.is_dir():
            raise MemoryStoreError(".coquo must be a regular directory")
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise MemoryStoreError("memory directory must be a regular directory")
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise MemoryStoreError("memory directory could not be created") from None
        if self.root.is_symlink() or not self.root.is_dir():
            raise MemoryStoreError("memory directory must be a regular directory")
        try:
            os.chmod(parent, stat.S_IRWXU)
            os.chmod(self.root, stat.S_IRWXU)
        except OSError:
            raise MemoryStoreError("memory directory permissions could not be secured") from None

    @staticmethod
    def _require(records: Mapping[str, MemoryRecord], memory_id: str) -> MemoryRecord:
        try:
            return records[memory_id]
        except KeyError:
            raise MemoryStoreError(f"memory record does not exist: {memory_id}") from None


def _validate_reason(reason: str | None) -> None:
    if reason is not None and (
        not isinstance(reason, str) or not reason.strip() or len(reason) > 256
    ):
        raise MemoryStoreError("memory event reason is invalid")


def _validate_transition(
    previous: MemoryRecord | None,
    current: MemoryRecord,
    event: str,
) -> None:
    if previous is None:
        if event != "created" or current.status is not MemoryStatus.CANDIDATE:
            raise MemoryStoreError("memory created event must contain a candidate")
        return
    if previous.memory_id != current.memory_id:
        raise MemoryStoreError("memory event identity changed")
    if previous.status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}:
        raise MemoryStoreError("memory event follows a terminal state")
    if event == "confirmed" and current.status is not MemoryStatus.CONFIRMED:
        raise MemoryStoreError("confirmed event has the wrong status")
    if event == "updated" and current.status is not previous.status:
        raise MemoryStoreError("updated event changed memory status")
    if event == "recalled" and current.status is not previous.status:
        raise MemoryStoreError("recalled event changed memory status")
    if event in {"stale", "deleted", "evicted"} and current.status.value != event:
        raise MemoryStoreError("memory status event has the wrong status")
    if event not in {"confirmed", "updated", "recalled", "stale", "deleted", "evicted"}:
        raise MemoryStoreError("unknown memory event")


def _normalize_memory_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _open_lock(path: Path):
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise MemoryStoreError("memory lock could not be opened") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MemoryStoreError("memory lock must be a regular file")
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "a+b")
        descriptor = -1
        return stream
    except OSError:
        raise MemoryStoreError("memory lock is invalid") from None
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _lock(stream) -> None:
    try:
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except OSError:
        raise MemoryStoreError("memory lock could not be acquired") from None


def _unlock(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        raise MemoryStoreError("memory lock could not be released") from None


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise MemoryStoreError("memory directory could not be opened") from None
    try:
        os.fsync(descriptor)
    except OSError:
        raise MemoryStoreError("memory directory could not be synchronized") from None
    finally:
        os.close(descriptor)
