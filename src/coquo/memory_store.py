"""Durable local store for explicit long-term semantic-memory records."""

from __future__ import annotations

from contextlib import contextmanager
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
    MEMORY_MAX_SEARCH_RESULTS,
    MemoryError,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
)

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class MemoryStoreError(MemoryError):
    """Raised when the local memory log cannot be safely read or written."""


class MemoryStore:
    """Append-only, replayable memory event log under the workspace ``.coquo``."""

    def __init__(self, workspace: Path) -> None:
        original = Path(workspace)
        resolved = original.resolve(strict=True)
        if original.is_symlink() or not resolved.is_dir():
            raise MemoryStoreError("workspace must be an existing non-symlink directory")
        self.workspace = resolved
        self.root = resolved / ".coquo" / "memory"
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / ".memory.lock"
        self._thread_lock = RLock()

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
            self._append(record, "created", records)
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
        return updated

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
    ) -> MemoryRecord:
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
            self._append(updated, "updated", records)
        return updated

    def transition(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        if status not in {
            MemoryStatus.STALE,
            MemoryStatus.DELETED,
            MemoryStatus.EVICTED,
            MemoryStatus.CONFIRMED,
        }:
            raise MemoryStoreError("unsupported memory transition")
        with self._transaction():
            records = self._read_records()
            current = self._require(records, memory_id)
            try:
                updated = current.transition(status)
            except MemoryError as error:
                raise MemoryStoreError(str(error)) from None
            self._append(updated, status.value, records)
        return updated

    def search(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = MEMORY_MAX_SEARCH_RESULTS,
    ) -> tuple[MemoryRecord, ...]:
        if not isinstance(query, str) or not query.strip():
            raise MemoryStoreError("memory search query must not be blank")
        if len(query) > 512 or "\x00" in query:
            raise MemoryStoreError("memory search query is invalid")
        if not isinstance(limit, int) or not 1 <= limit <= MEMORY_MAX_SEARCH_RESULTS:
            raise MemoryStoreError("memory search limit is out of bounds")
        records = self._read_records()
        matches = [
            record
            for record in records.values()
            if record.status in {MemoryStatus.CANDIDATE, MemoryStatus.CONFIRMED, MemoryStatus.STALE}
            and (scope is None or record.scope is scope)
            and (scope_id is None or record.scope_id == scope_id)
            and query.casefold() in record.content.casefold()
        ]
        matches.sort(key=lambda item: (item.created_at, item.memory_id), reverse=True)
        matches = matches[:limit]
        if matches:
            with self._transaction():
                records = self._read_records()
                for record in matches:
                    current = records.get(record.memory_id)
                    if current is not None and current.status not in {
                        MemoryStatus.DELETED,
                        MemoryStatus.EVICTED,
                    }:
                        updated = current.recalled()
                        self._append(updated, "recalled", records)
        return tuple(matches)

    def _read_records(self) -> dict[str, MemoryRecord]:
        if not self.events_path.exists():
            return {}
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
        records: dict[str, MemoryRecord] = {}
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
            if not isinstance(event, dict) or set(event) != {"event", "record", "version"}:
                raise MemoryStoreError("memory event schema is invalid")
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
        return records

    def _append(
        self,
        record: MemoryRecord,
        event: str,
        records: dict[str, MemoryRecord],
    ) -> None:
        if len(records) >= MEMORY_MAX_EVENTS and record.memory_id not in records:
            raise MemoryStoreError("memory record limit reached")
        payload = (
            json.dumps(
                {"event": event, "record": record.to_mapping(), "version": 1},
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

    def _ensure_root(self) -> None:
        parent = self.root.parent
        if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
            raise MemoryStoreError(".coquo must be a regular directory")
        if not parent.exists():
            parent.mkdir(mode=0o700)
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise MemoryStoreError("memory directory must be a regular directory")
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
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
