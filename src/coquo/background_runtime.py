"""Durable, restartable Child background runtime.

The ordinary :class:`ChildRunSupervisor` is intentionally process-local and is
still useful for tests and for callers that need an in-process executor.  This
module adds the durable control-plane seam used by the CLI and by a restarted
Host: a workspace-bound append-only queue, one OS worker lease, bounded worker
concurrency, heartbeat state, and fail-closed orphan reconciliation.

The queue is not a second source of Child execution truth.  The Child Run JSONL
ledger remains authoritative for admission, execution and terminal outcomes;
this ledger records only background submission ownership and observation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from threading import Lock
import time
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import (
    ChildRunInfo,
    ChildRunStore,
    ChildRunExecutionLeaseError,
)
from coquo.session_records import workspace_fingerprint

BACKGROUND_QUEUE_SCHEMA_VERSION = 2
BACKGROUND_QUEUE_SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
BACKGROUND_WORKER_STATE_SCHEMA_VERSION = 1
MAX_BACKGROUND_QUEUE_RECORDS = 20_000
MAX_BACKGROUND_QUEUE_BYTES = 8 * 1024 * 1024
MAX_BACKGROUND_QUEUE_CAPACITY = 32
MAX_BACKGROUND_WORKERS = 4
MAX_BACKGROUND_FLEET_WORKERS = 8
BACKGROUND_HEARTBEAT_SECONDS = 0.5
BACKGROUND_IDLE_SECONDS = 2.0
BACKGROUND_HEARTBEAT_STALE_SECONDS = 5.0
BACKGROUND_RECORD_TYPES = {"background_queue_event_v1"}
BACKGROUND_EVENTS = {"queued", "claimed", "heartbeat", "requeued", "terminal"}
BACKGROUND_TERMINAL_STATUSES = frozenset(item.value for item in ChildRunStatus)
BACKGROUND_EFFECT_STATES = frozenset({"not-started", "in-flight", "confirmed", "unknown"})


class BackgroundRuntimeError(RuntimeError):
    """Raised when durable background scheduling cannot proceed safely."""


class BackgroundWorkerAlreadyRunning(BackgroundRuntimeError):
    """Raised when another worker owns the workspace worker lease."""


@dataclass(frozen=True)
class BackgroundQueueItem:
    submission_id: str
    child_run_id: str
    state: str
    queued_at: str
    worker_id: str | None = None
    lease_id: str | None = None
    claimed_at: str | None = None
    heartbeat_at: str | None = None
    terminal_child_status: str | None = None
    message: str | None = None
    effect_state: str = "confirmed"

    @property
    def pending(self) -> bool:
        return self.state in {"queued", "claimed"}

    @property
    def outcome_unknown(self) -> bool:
        """Whether a terminal item may have crossed an external side-effect boundary."""
        return self.state == "terminal" and self.effect_state == "unknown"


@dataclass(frozen=True)
class BackgroundWorkerState:
    worker_id: str
    pid: int
    state: str
    started_at: str
    heartbeat_at: str
    active_submission_ids: tuple[str, ...] = ()
    active_child_run_ids: tuple[str, ...] = ()
    last_error: str | None = None


@dataclass(frozen=True)
class BackgroundRuntimeStatus:
    worker_running: bool
    worker: BackgroundWorkerState | None
    queue: tuple[BackgroundQueueItem, ...]
    orphaned_submission_ids: tuple[str, ...]
    diagnostic: str | None = None

    @property
    def pending_count(self) -> int:
        return sum(item.pending for item in self.queue)

    @property
    def active_count(self) -> int:
        return sum(item.state == "claimed" for item in self.queue)


@dataclass(frozen=True)
class BackgroundSubmission:
    item: BackgroundQueueItem
    worker_pid: int | None
    worker_started: bool
    launch_error: str | None = None


@dataclass(frozen=True)
class BackgroundWorkerResult:
    worker_id: str
    outcome: str
    processed_child_run_ids: tuple[str, ...] = ()
    recovered_child_run_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackgroundFleetResult:
    """Aggregate result for one bounded local worker fleet run."""

    outcome: str
    worker_results: tuple[BackgroundWorkerResult, ...]

    @property
    def processed_child_run_ids(self) -> tuple[str, ...]:
        return tuple(
            child_id
            for result in self.worker_results
            for child_id in result.processed_child_run_ids
        )

    @property
    def recovered_child_run_ids(self) -> tuple[str, ...]:
        return tuple(
            child_id
            for result in self.worker_results
            for child_id in result.recovered_child_run_ids
        )

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(message for result in self.worker_results for message in result.diagnostics)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise BackgroundRuntimeError(f"{label} must be text")
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BackgroundRuntimeError(f"{label} must be a canonical UUID") from None
    if parsed.version != 4 or str(parsed) != value:
        raise BackgroundRuntimeError(f"{label} must be a canonical UUID4")
    return value


def _safe_text(value: object, label: str, limit: int = 1024) -> str:
    if not isinstance(value, str):
        raise BackgroundRuntimeError(f"{label} must be text")
    text = value.replace("\r", " ").replace("\n", " ").strip()
    if not text or len(text) > limit:
        raise BackgroundRuntimeError(f"{label} is invalid")
    return text


def _safe_effect_state(value: object) -> str:
    state = _safe_text(value, "background effect state", 32)
    if state not in BACKGROUND_EFFECT_STATES:
        raise BackgroundRuntimeError("background effect state is invalid")
    return state


def _safe_error(error: BaseException) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    return text[:1024] or error.__class__.__name__


class _FileLease:
    def __init__(self, path: Path, descriptor: int, *, protocol: bytes) -> None:
        self.path = path
        self._descriptor = descriptor
        self._protocol = protocol
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _unlock(self._descriptor)
        finally:
            os.close(self._descriptor)

    def __enter__(self) -> "_FileLease":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class BackgroundQueueStore:
    """Append-only queue ledger and workspace worker lease."""

    def __init__(self, workspace: Path, *, clock: Callable[[], str] = utc_now) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise BackgroundRuntimeError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise BackgroundRuntimeError("workspace is inaccessible") from None
        if not resolved.is_dir():
            raise BackgroundRuntimeError("workspace is not a directory")
        self.workspace = resolved
        self.root = (
            resolved / ".coquo" / "child-runs" / workspace_fingerprint(resolved) / "background"
        )
        self.queue_path = self.root / "queue.jsonl"
        self.queue_lock_path = self.root / "queue.lock"
        self.worker_lock_path = self.root / "worker.lock"
        self.worker_state_path = self.root / "worker-state.json"
        self._clock = clock
        self._guard = Lock()

    def enqueue(
        self,
        child_run_id: str,
        *,
        parent_session_id: str | None = None,
        capacity: int = MAX_BACKGROUND_QUEUE_CAPACITY,
    ) -> BackgroundQueueItem:
        child_id = _canonical_uuid(child_run_id, "Child Run ID")
        if type(capacity) is not int or not 1 <= capacity <= MAX_BACKGROUND_QUEUE_CAPACITY:
            raise ValueError("background queue capacity is invalid")
        info = ChildRunStore(self.workspace).inspect(child_id)
        if info.status is not ChildRunStatus.READY:
            raise BackgroundRuntimeError("Child Run is not ready for background execution")
        if parent_session_id is not None and info.parent_session_id != parent_session_id:
            raise BackgroundRuntimeError("Child Run belongs to another parent Session")
        with self._queue_lock():
            items = self._replay()
            current = next(
                (item for item in items if item.child_run_id == child_id and item.pending), None
            )
            if current is not None:
                raise BackgroundRuntimeError("Child Run is already queued for background execution")
            pending = sum(item.pending for item in items)
            if pending >= capacity:
                raise BackgroundRuntimeError("background queue is full")
            item = BackgroundQueueItem(
                submission_id=str(uuid4()),
                child_run_id=child_id,
                state="queued",
                queued_at=self._clock(),
                effect_state="not-started",
            )
            self._append_locked(self._record("queued", item))
            return item

    def claim(self, worker_id: str) -> BackgroundQueueItem | None:
        worker = _canonical_uuid(worker_id, "worker ID")
        with self._queue_lock():
            items = self._replay()
            candidate = next((item for item in items if item.state == "queued"), None)
            if candidate is None:
                return None
            claimed = BackgroundQueueItem(
                submission_id=candidate.submission_id,
                child_run_id=candidate.child_run_id,
                state="claimed",
                queued_at=candidate.queued_at,
                worker_id=worker,
                lease_id=str(uuid4()),
                claimed_at=self._clock(),
                heartbeat_at=self._clock(),
                effect_state="in-flight",
            )
            self._append_locked(self._record("claimed", claimed))
            return claimed

    def heartbeat(
        self, item: BackgroundQueueItem, *, message: str | None = None
    ) -> BackgroundQueueItem:
        current = self._find(item.submission_id)
        if current.state != "claimed" or current.lease_id != item.lease_id:
            return current
        updated = BackgroundQueueItem(
            submission_id=current.submission_id,
            child_run_id=current.child_run_id,
            state="claimed",
            queued_at=current.queued_at,
            worker_id=current.worker_id,
            lease_id=current.lease_id,
            claimed_at=current.claimed_at,
            heartbeat_at=self._clock(),
            message=message or current.message,
            effect_state=current.effect_state,
        )
        with self._queue_lock():
            current = self._find_locked(item.submission_id)
            if current.state == "claimed" and current.lease_id == item.lease_id:
                self._append_locked(self._record("heartbeat", updated))
                return updated
            return current

    def requeue(self, item: BackgroundQueueItem, *, reason: str) -> BackgroundQueueItem:
        reason_text = _safe_text(reason, "requeue reason")
        with self._queue_lock():
            current = self._find_locked(item.submission_id)
            if current.state != "claimed" or current.lease_id != item.lease_id:
                return current
            updated = BackgroundQueueItem(
                submission_id=current.submission_id,
                child_run_id=current.child_run_id,
                state="queued",
                queued_at=current.queued_at,
                message=reason_text,
                effect_state="not-started",
            )
            self._append_locked(self._record("requeued", updated, message=reason_text))
            return updated

    def terminal(
        self,
        item: BackgroundQueueItem,
        *,
        child_status: str,
        message: str | None = None,
        effect_state: str = "confirmed",
    ) -> BackgroundQueueItem:
        status = _safe_text(child_status, "Child terminal status", 32)
        if status not in BACKGROUND_TERMINAL_STATUSES:
            raise BackgroundRuntimeError("Child terminal status is invalid")
        effect = _safe_effect_state(effect_state)
        with self._queue_lock():
            current = self._find_locked(item.submission_id)
            if current.state == "terminal":
                if current.terminal_child_status != status or current.effect_state != effect:
                    raise BackgroundRuntimeError(
                        "background terminal observation conflicts with the durable result"
                    )
                return current
            if current.state != "claimed" or current.lease_id != item.lease_id:
                raise BackgroundRuntimeError("background queue lease does not match")
            terminal = BackgroundQueueItem(
                submission_id=current.submission_id,
                child_run_id=current.child_run_id,
                state="terminal",
                queued_at=current.queued_at,
                worker_id=current.worker_id,
                lease_id=current.lease_id,
                claimed_at=current.claimed_at,
                heartbeat_at=current.heartbeat_at,
                terminal_child_status=status,
                message=None if message is None else _safe_text(message, "terminal message"),
                effect_state=effect,
            )
            self._append_locked(self._record("terminal", terminal))
            return terminal

    def snapshot(self) -> tuple[BackgroundQueueItem, ...]:
        if not self.root.exists():
            return ()
        return self._replay()

    def inspect(self, submission_id: str) -> BackgroundQueueItem:
        return self._find(_canonical_uuid(submission_id, "submission ID"))

    def worker_is_running(self, *, slot: int | None = None) -> bool:
        slots = range(MAX_BACKGROUND_FLEET_WORKERS) if slot is None else (slot,)
        for selected in slots:
            if type(selected) is not int or not 0 <= selected < MAX_BACKGROUND_FLEET_WORKERS:
                raise ValueError("background worker slot is invalid")
            lock_path = self._worker_lock_path(selected)
            if not lock_path.exists():
                continue
            try:
                descriptor = _open_file(lock_path, writable=True)
                try:
                    _lock(descriptor, nonblocking=True)
                except BackgroundWorkerAlreadyRunning:
                    return True
                _unlock(descriptor)
                os.close(descriptor)
            except OSError:
                continue
        return False

    def acquire_worker(self, *, slot: int = 0) -> _FileLease:
        if type(slot) is not int or not 0 <= slot < MAX_BACKGROUND_FLEET_WORKERS:
            raise ValueError("background worker slot is invalid")
        self._ensure_root()
        lock_path = self._worker_lock_path(slot)
        descriptor = _open_file(lock_path, writable=True)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"coquo-background-worker-v1\n")
                os.fsync(descriptor)
            else:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.read(descriptor, 128) != b"coquo-background-worker-v1\n":
                    raise BackgroundRuntimeError("background worker lease header is invalid")
            _lock(descriptor, nonblocking=True)
            return _FileLease(lock_path, descriptor, protocol=b"coquo-background-worker-v1\n")
        except BaseException:
            os.close(descriptor)
            raise

    def write_worker_state(self, state: BackgroundWorkerState | None, *, slot: int = 0) -> None:
        if type(slot) is not int or not 0 <= slot < MAX_BACKGROUND_FLEET_WORKERS:
            raise ValueError("background worker slot is invalid")
        self._ensure_root()
        state_path = self._worker_state_path(slot)
        if state is None:
            try:
                state_path.unlink()
            except FileNotFoundError:
                return
            except OSError as error:
                raise BackgroundRuntimeError("could not remove background worker state") from error
            return
        payload = {
            "schema_version": BACKGROUND_WORKER_STATE_SCHEMA_VERSION,
            "worker_id": state.worker_id,
            "pid": state.pid,
            "state": state.state,
            "started_at": state.started_at,
            "heartbeat_at": state.heartbeat_at,
            "active_submission_ids": list(state.active_submission_ids),
            "active_child_run_ids": list(state.active_child_run_ids),
            "last_error": state.last_error,
        }
        _atomic_write_json(state_path, payload, self.root)

    def read_worker_state(self, *, slot: int = 0) -> BackgroundWorkerState | None:
        if type(slot) is not int or not 0 <= slot < MAX_BACKGROUND_FLEET_WORKERS:
            raise ValueError("background worker slot is invalid")
        state_path = self._worker_state_path(slot)
        if not state_path.exists():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if data.get("schema_version") != BACKGROUND_WORKER_STATE_SCHEMA_VERSION:
                raise BackgroundRuntimeError("background worker state schema is unsupported")
            return BackgroundWorkerState(
                worker_id=_canonical_uuid(data.get("worker_id"), "worker ID"),
                pid=data["pid"],
                state=_safe_text(data["state"], "worker state", 32),
                started_at=_safe_text(data["started_at"], "worker started_at", 64),
                heartbeat_at=_safe_text(data["heartbeat_at"], "worker heartbeat_at", 64),
                active_submission_ids=tuple(
                    _canonical_uuid(value, "submission ID")
                    for value in data.get("active_submission_ids", [])
                ),
                active_child_run_ids=tuple(
                    _canonical_uuid(value, "Child Run ID")
                    for value in data.get("active_child_run_ids", [])
                ),
                last_error=data.get("last_error"),
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            BackgroundRuntimeError,
        ) as error:
            raise BackgroundRuntimeError("background worker state is invalid") from error

    @contextmanager
    def _queue_lock(self) -> Iterator[None]:
        self._ensure_root()
        descriptor = _open_file(self.queue_lock_path, writable=True)
        try:
            _lock(descriptor, nonblocking=False)
            yield
        finally:
            _unlock(descriptor)
            os.close(descriptor)

    def _find(self, submission_id: str) -> BackgroundQueueItem:
        with self._queue_lock():
            return self._find_locked(submission_id)

    def _find_locked(self, submission_id: str) -> BackgroundQueueItem:
        for item in self._replay():
            if item.submission_id == submission_id:
                return item
        raise BackgroundRuntimeError("background submission was not found")

    def _record(
        self,
        event: str,
        item: BackgroundQueueItem,
        *,
        message: str | None = None,
    ) -> dict[str, object]:
        if event not in BACKGROUND_EVENTS:
            raise BackgroundRuntimeError("background queue event is invalid")
        return {
            "record_type": "background_queue_event_v1",
            "schema_version": BACKGROUND_QUEUE_SCHEMA_VERSION,
            "sequence": None,
            "event": event,
            "submission_id": item.submission_id,
            "child_run_id": item.child_run_id,
            "worker_id": item.worker_id,
            "lease_id": item.lease_id,
            "queued_at": item.queued_at,
            "claimed_at": item.claimed_at,
            "heartbeat_at": item.heartbeat_at,
            "terminal_child_status": item.terminal_child_status,
            "effect_state": item.effect_state,
            "message": message if message is not None else item.message,
            "recorded_at": self._clock(),
        }

    def _append_locked(self, record: dict[str, object]) -> None:
        self._ensure_root()
        records = self._read_records()
        if len(records) >= MAX_BACKGROUND_QUEUE_RECORDS:
            raise BackgroundRuntimeError("background queue ledger record limit exceeded")
        record["sequence"] = len(records)
        line = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(line) > 16 * 1024:
            raise BackgroundRuntimeError("background queue record is oversized")
        descriptor = _open_file(self.queue_path, writable=True)
        try:
            os.lseek(descriptor, 0, os.SEEK_END)
            _write_all(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read_records(self) -> list[dict[str, object]]:
        if not self.queue_path.exists():
            return []
        try:
            data = self.queue_path.read_bytes()
        except OSError as error:
            raise BackgroundRuntimeError("background queue is inaccessible") from error
        if len(data) > MAX_BACKGROUND_QUEUE_BYTES or (data and not data.endswith(b"\n")):
            raise BackgroundRuntimeError("background queue is not at a durable record boundary")
        records: list[dict[str, object]] = []
        for index, line in enumerate(data.splitlines()):
            if index >= MAX_BACKGROUND_QUEUE_RECORDS:
                raise BackgroundRuntimeError("background queue record limit exceeded")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise BackgroundRuntimeError("background queue contains invalid JSON") from error
            self._validate_record(record, index)
            records.append(record)
        return records

    def _replay(self) -> tuple[BackgroundQueueItem, ...]:
        records = self._read_records()
        items: dict[str, BackgroundQueueItem] = {}
        for record in records:
            event = record["event"]
            submission_id = record["submission_id"]
            current = items.get(submission_id)
            event_effect = record.get("effect_state")
            if event_effect is None:
                event_effect = (
                    "not-started"
                    if event in {"queued", "requeued"}
                    else "in-flight"
                    if event in {"claimed", "heartbeat"}
                    else "confirmed"
                )
            candidate = BackgroundQueueItem(
                submission_id=submission_id,
                child_run_id=record["child_run_id"],
                state="queued"
                if event in {"queued", "requeued"}
                else "claimed"
                if event in {"claimed", "heartbeat"}
                else "terminal",
                queued_at=record["queued_at"],
                worker_id=record.get("worker_id") or (current.worker_id if current else None),
                lease_id=record.get("lease_id") or (current.lease_id if current else None),
                claimed_at=record.get("claimed_at") or (current.claimed_at if current else None),
                heartbeat_at=record.get("heartbeat_at")
                or (current.heartbeat_at if current else None),
                terminal_child_status=record.get("terminal_child_status")
                or (current.terminal_child_status if current else None),
                message=record.get("message") or (current.message if current else None),
                effect_state=_safe_effect_state(event_effect),
            )
            items[submission_id] = candidate
        return tuple(sorted(items.values(), key=lambda item: (item.queued_at, item.submission_id)))

    @staticmethod
    def _validate_record(record: object, expected_sequence: int) -> None:
        if not isinstance(record, dict) or record.get("record_type") not in BACKGROUND_RECORD_TYPES:
            raise BackgroundRuntimeError("background queue record type is invalid")
        if record.get("schema_version") not in BACKGROUND_QUEUE_SUPPORTED_SCHEMA_VERSIONS:
            raise BackgroundRuntimeError("background queue schema is unsupported")
        if record.get("sequence") != expected_sequence:
            raise BackgroundRuntimeError("background queue sequence is not contiguous")
        if record.get("event") not in BACKGROUND_EVENTS:
            raise BackgroundRuntimeError("background queue event is invalid")
        _canonical_uuid(record.get("submission_id"), "submission ID")
        _canonical_uuid(record.get("child_run_id"), "Child Run ID")
        for key in ("worker_id", "lease_id"):
            if record.get(key) is not None:
                _canonical_uuid(record[key], key)
        for key in ("queued_at", "claimed_at", "heartbeat_at", "recorded_at"):
            if record.get(key) is not None:
                _safe_text(record[key], key, 64)
        if (
            record.get("terminal_child_status") is not None
            and record["terminal_child_status"] not in BACKGROUND_TERMINAL_STATUSES
        ):
            raise BackgroundRuntimeError("background terminal status is invalid")
        if record.get("message") is not None:
            _safe_text(record["message"], "background message")
        if record.get("effect_state") is not None:
            _safe_effect_state(record["effect_state"])

    def _ensure_root(self) -> None:
        for path in (
            self.workspace / ".coquo",
            self.workspace / ".coquo" / "child-runs",
            self.root.parent,
            self.root,
        ):
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_dir():
                    raise BackgroundRuntimeError("background runtime path is unsafe")
            else:
                path.mkdir(mode=0o700)
        for path in (
            self.queue_path,
            self.queue_lock_path,
            self.worker_lock_path,
            *(self._worker_lock_path(slot) for slot in range(1, MAX_BACKGROUND_FLEET_WORKERS)),
        ):
            if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise BackgroundRuntimeError("background runtime file mode is invalid")

    def _worker_lock_path(self, slot: int) -> Path:
        return self.worker_lock_path if slot == 0 else self.root / f"worker-{slot}.lock"

    def _worker_state_path(self, slot: int) -> Path:
        return self.worker_state_path if slot == 0 else self.root / f"worker-state-{slot}.json"


class PersistentChildRunRuntime:
    """Host-facing durable submission, observation and recovery API."""

    def __init__(
        self,
        workspace: Path,
        *,
        parent_session_id: str | None = None,
        worker_count: int = MAX_BACKGROUND_WORKERS,
        queue_capacity: int = MAX_BACKGROUND_QUEUE_CAPACITY,
        worker_launcher: Callable[[list[str]], object] | None = None,
    ) -> None:
        if type(worker_count) is not int or not 1 <= worker_count <= MAX_BACKGROUND_WORKERS:
            raise ValueError("background worker count is invalid")
        self.workspace = Path(workspace)
        self.parent_session_id = parent_session_id
        self.worker_count = worker_count
        self.queue_capacity = queue_capacity
        self.store = BackgroundQueueStore(workspace)
        self._worker_launcher = worker_launcher

    def start(self, child_run_id: str) -> BackgroundSubmission:
        item = self.store.enqueue(
            child_run_id,
            parent_session_id=self.parent_session_id,
            capacity=self.queue_capacity,
        )
        try:
            pid = self.ensure_worker()
            return BackgroundSubmission(item, pid, pid is not None)
        except BaseException as error:
            return BackgroundSubmission(item, None, False, _safe_error(error))

    def ensure_worker(self) -> int | None:
        if self.store.worker_is_running():
            return None
        command = [
            sys.executable,
            "-m",
            "coquo.background_worker",
            "--workspace",
            str(self.store.workspace),
            "--worker-count",
            str(self.worker_count),
        ]
        if self._worker_launcher is not None:
            launched = self._worker_launcher(command)
            return getattr(launched, "pid", None)
        process = subprocess.Popen(
            command,
            cwd=str(self.store.workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return process.pid

    def wait(self, child_run_id: str, timeout_seconds: float) -> ChildRunInfo:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("Child wait timeout is invalid")
        if not 0 <= timeout_seconds <= 30:
            raise ValueError("Child wait timeout must be between 0 and 30 seconds")
        store = ChildRunStore(self.workspace)
        deadline = time.monotonic() + timeout_seconds
        while True:
            info = store.inspect(child_run_id)
            if info.status in {
                ChildRunStatus.CANCELLED,
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.INTERRUPTED,
            }:
                return info
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return info
            time.sleep(min(0.1, remaining))

    def status(self) -> BackgroundRuntimeStatus:
        queue = self.store.snapshot()
        worker = self.store.read_worker_state()
        running = self.store.worker_is_running()
        orphaned = tuple(
            item.submission_id for item in queue if item.state == "claimed" and not running
        )
        return BackgroundRuntimeStatus(running, worker, queue, orphaned)

    def recover_orphans(self) -> BackgroundWorkerResult:
        worker = PersistentChildWorker(
            self.workspace,
            worker_count=self.worker_count,
            executor_factory=None,
        )
        return worker.recover_orphans()


class PersistentChildWorker:
    """Restartable worker process; one process owns bounded Child threads."""

    def __init__(
        self,
        workspace: Path,
        *,
        worker_count: int = MAX_BACKGROUND_WORKERS,
        executor_factory: Callable[[str], object] | None = None,
        heartbeat_seconds: float = BACKGROUND_HEARTBEAT_SECONDS,
        idle_seconds: float = BACKGROUND_IDLE_SECONDS,
        slot: int = 0,
    ) -> None:
        if type(worker_count) is not int or not 1 <= worker_count <= MAX_BACKGROUND_WORKERS:
            raise ValueError("background worker count is invalid")
        if type(slot) is not int or not 0 <= slot < MAX_BACKGROUND_FLEET_WORKERS:
            raise ValueError("background worker slot is invalid")
        if heartbeat_seconds <= 0 or idle_seconds < 0:
            raise ValueError("background worker timing is invalid")
        self.workspace = Path(workspace)
        self.store = BackgroundQueueStore(workspace)
        self.worker_count = worker_count
        self.executor_factory = executor_factory
        self.heartbeat_seconds = heartbeat_seconds
        self.idle_seconds = idle_seconds
        self.slot = slot

    def run(self, *, max_items: int | None = None) -> BackgroundWorkerResult:
        worker_id = str(uuid4())
        try:
            lease = self.store.acquire_worker(slot=self.slot)
        except BackgroundWorkerAlreadyRunning:
            return BackgroundWorkerResult(worker_id, "already_running")
        processed: list[str] = []
        diagnostics: list[str] = []
        recovered: list[str] = []
        active: dict[Future[tuple[str, str, str | None, str]], BackgroundQueueItem] = {}
        last_activity = time.monotonic()
        processed_count = 0
        with lease:
            try:
                recovered, reconcile_diagnostics = self._reconcile_orphans(worker_id)
                diagnostics.extend(reconcile_diagnostics)
                self._write_state(worker_id, "running", active)
                with ThreadPoolExecutor(
                    max_workers=self.worker_count, thread_name_prefix="coquo-child"
                ) as pool:
                    while True:
                        now = time.monotonic()
                        for future, item in tuple(active.items()):
                            if future.done():
                                del active[future]
                                child_id, status, error, effect_state = future.result()
                                processed.append(child_id)
                                processed_count += 1
                                last_activity = now
                                if error:
                                    diagnostics.append(error)
                                if status in BACKGROUND_TERMINAL_STATUSES:
                                    try:
                                        self.store.terminal(
                                            item,
                                            child_status=status,
                                            message=error,
                                            effect_state=effect_state,
                                        )
                                    except BaseException as terminal_error:
                                        diagnostics.append(
                                            f"{child_id}: queue terminal append failed: {_safe_error(terminal_error)}"
                                        )
                                else:
                                    diagnostics.append(
                                        f"{child_id}: executor returned non-terminal Child state {status}; queue left claimed"
                                    )
                        if max_items is None or processed_count < max_items:
                            while len(active) < self.worker_count:
                                item = self.store.claim(worker_id)
                                if item is None:
                                    break
                                active[pool.submit(self._execute, item)] = item
                                last_activity = now
                        self._heartbeat(worker_id, active)
                        if not active:
                            if not any(item.state == "queued" for item in self.store.snapshot()):
                                if (
                                    max_items is not None
                                    or now - last_activity >= self.idle_seconds
                                ):
                                    break
                        time.sleep(min(self.heartbeat_seconds, 0.1))
                self._write_state(worker_id, "stopped", {})
            except BaseException as error:
                diagnostics.append(_safe_error(error))
                try:
                    self._write_state(worker_id, "failed", {}, last_error=_safe_error(error))
                except BaseException:
                    pass
                return BackgroundWorkerResult(
                    worker_id, "failed", tuple(processed), tuple(recovered), tuple(diagnostics)
                )
        return BackgroundWorkerResult(
            worker_id, "completed", tuple(processed), tuple(recovered), tuple(diagnostics)
        )

    def run_forever(
        self,
        *,
        stop_event=None,
        max_runtime_seconds: float | None = None,
    ) -> BackgroundWorkerResult:
        """Keep this worker alive until stopped, recovering abandoned work each cycle.

        The queue remains the durable source of truth; each cycle may acquire
        the slot lease independently, so a process restart cannot duplicate a
        Child execution.  ``max_runtime_seconds`` exists for supervised tests
        and bounded service managers and is never persisted as task state.
        """
        from threading import Event

        token = stop_event or Event()
        if not hasattr(token, "is_set") or not hasattr(token, "wait"):
            raise ValueError("stop_event must provide is_set() and wait()")
        started = time.monotonic()
        processed: list[str] = []
        recovered: list[str] = []
        diagnostics: list[str] = []
        worker_id = str(uuid4())
        while not token.is_set():
            if max_runtime_seconds is not None and (
                not isinstance(max_runtime_seconds, (int, float)) or max_runtime_seconds < 0
            ):
                raise ValueError("max_runtime_seconds is invalid")
            if (
                max_runtime_seconds is not None
                and time.monotonic() - started >= max_runtime_seconds
            ):
                break
            result = self.run()
            worker_id = result.worker_id
            processed.extend(result.processed_child_run_ids)
            recovered.extend(result.recovered_child_run_ids)
            diagnostics.extend(result.diagnostics)
            if result.outcome == "already_running":
                token.wait(min(self.idle_seconds, 0.5) if self.idle_seconds else 0.1)
            elif result.outcome == "failed":
                token.wait(min(self.idle_seconds or 0.1, 1.0))
            else:
                token.wait(min(self.idle_seconds, 0.5) if self.idle_seconds else 0.1)
        return BackgroundWorkerResult(
            worker_id,
            "stopped",
            tuple(processed),
            tuple(recovered),
            tuple(diagnostics),
        )

    def recover_orphans(self) -> BackgroundWorkerResult:
        worker_id = str(uuid4())
        try:
            lease = self.store.acquire_worker(slot=self.slot)
        except BackgroundWorkerAlreadyRunning:
            return BackgroundWorkerResult(worker_id, "already_running")
        with lease:
            recovered, diagnostics = self._reconcile_orphans(worker_id)
        return BackgroundWorkerResult(
            worker_id, "recovered", (), tuple(recovered), tuple(diagnostics)
        )

    def _reconcile_orphans(self, worker_id: str) -> tuple[list[str], list[str]]:
        recovered: list[str] = []
        diagnostics: list[str] = []
        for item in self.store.snapshot():
            if item.state != "claimed" or item.worker_id == worker_id:
                continue
            try:
                info = ChildRunStore(self.workspace).inspect(item.child_run_id)
                if info.status in {
                    ChildRunStatus.COMPLETED,
                    ChildRunStatus.CANCELLED,
                    ChildRunStatus.FAILED,
                    ChildRunStatus.INTERRUPTED,
                }:
                    self.store.terminal(item, child_status=info.status.value)
                    recovered.append(item.child_run_id)
                    continue
                if info.status is ChildRunStatus.READY:
                    # Requeue only after proving no executor still owns the Child.
                    execution_lease = None
                    try:
                        execution_lease = ChildRunStore(self.workspace).acquire_execution(
                            item.child_run_id
                        )
                    except ChildRunExecutionLeaseError as error:
                        diagnostics.append(
                            f"{item.child_run_id}: execution lease still owned: {_safe_error(error)}"
                        )
                        continue
                    finally:
                        if execution_lease is not None:
                            execution_lease.close()
                    self.store.requeue(
                        item, reason="previous background worker abandoned before Child start"
                    )
                    recovered.append(item.child_run_id)
                    continue
                if info.status in {ChildRunStatus.RUNNING, ChildRunStatus.CANCELLING}:
                    try:
                        recovery_lease = ChildRunStore(self.workspace).acquire_recovery_lease(
                            item.child_run_id
                        )
                    except BaseException as error:
                        diagnostics.append(
                            f"{item.child_run_id}: recovery refused: {_safe_error(error)}"
                        )
                        continue
                    try:
                        interrupted = ChildRunStore(self.workspace).finish_interrupted(
                            item.child_run_id
                        )
                    finally:
                        recovery_lease.close()
                    self.store.terminal(
                        item,
                        child_status=interrupted.status.value,
                        message="orphaned worker execution",
                    )
                    recovered.append(item.child_run_id)
                    continue
                diagnostics.append(
                    f"{item.child_run_id}: unexpected Child state {info.status.value}; queue left claimed"
                )
            except BaseException as error:
                diagnostics.append(
                    f"{item.child_run_id}: orphan reconciliation failed: {_safe_error(error)}"
                )
        return recovered, diagnostics

    def _execute(self, item: BackgroundQueueItem) -> tuple[str, str, str | None, str]:
        error_text: str | None = None
        try:
            executor = (
                self.executor_factory(item.child_run_id)
                if self.executor_factory is not None
                else self._default_executor(item.child_run_id)
            )
            result = executor.run(item.child_run_id)
            del result
        except BaseException as error:
            error_text = _safe_error(error)
            try:
                current = ChildRunStore(self.workspace).inspect(item.child_run_id)
                if current.status is ChildRunStatus.READY:
                    current = ChildRunStore(self.workspace).finish_failed(
                        item.child_run_id,
                        execution_id=None,
                        phase="background_worker",
                        result_code="background_execution_failed",
                        message=error_text,
                    )
                return item.child_run_id, current.status.value, error_text, "not-started"
            except BaseException as terminal_error:
                return (
                    item.child_run_id,
                    "failed",
                    f"{error_text}; terminalization failed: {_safe_error(terminal_error)}",
                    "unknown",
                )
        try:
            current = ChildRunStore(self.workspace).inspect(item.child_run_id)
            if current.status not in {
                ChildRunStatus.COMPLETED,
                ChildRunStatus.CANCELLED,
                ChildRunStatus.FAILED,
                ChildRunStatus.INTERRUPTED,
            }:
                current = ChildRunStore(self.workspace).finish_failed(
                    item.child_run_id,
                    execution_id=current.execution_id,
                    phase="background_worker",
                    result_code="worker_missing_terminal_state",
                    message="background executor returned without a durable terminal Child state",
                )
            return item.child_run_id, current.status.value, None, "confirmed"
        except BaseException as error:
            return (
                item.child_run_id,
                "failed",
                f"terminal inspection failed: {_safe_error(error)}",
                "unknown",
            )

    def _default_executor(self, child_run_id: str):
        del child_run_id
        from coquo.child_runtime import ChildRunExecutor

        return ChildRunExecutor(self.workspace)

    def _write_state(
        self,
        worker_id: str,
        state: str,
        active: Mapping[Future[tuple[str, str, str | None, str]], BackgroundQueueItem],
        *,
        last_error: str | None = None,
    ) -> None:
        items = tuple(active.values())
        self.store.write_worker_state(
            BackgroundWorkerState(
                worker_id=worker_id,
                pid=os.getpid(),
                state=state,
                started_at=self.store.read_worker_state().started_at
                if self.store.read_worker_state()
                else utc_now(),
                heartbeat_at=utc_now(),
                active_submission_ids=tuple(item.submission_id for item in items),
                active_child_run_ids=tuple(item.child_run_id for item in items),
                last_error=last_error,
            ),
            slot=self.slot,
        )

    def _heartbeat(
        self,
        worker_id: str,
        active: Mapping[Future[tuple[str, str, str | None, str]], BackgroundQueueItem],
    ) -> None:
        for item in active.values():
            try:
                self.store.heartbeat(item)
            except BaseException:
                pass
        try:
            self._write_state(worker_id, "running", active)
        except BaseException:
            pass


class BackgroundWorkerFleet:
    """Run a bounded set of independently leased workers in one supervisor.

    Each slot has its own OS lease and state file.  This models a small local
    worker fleet while retaining the same queue claim protocol used by
    separately launched processes; no distributed exactly-once guarantee is
    implied.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        fleet_size: int = 2,
        worker_count: int = MAX_BACKGROUND_WORKERS,
        executor_factory: Callable[[str], object] | None = None,
        heartbeat_seconds: float = BACKGROUND_HEARTBEAT_SECONDS,
        idle_seconds: float = BACKGROUND_IDLE_SECONDS,
    ) -> None:
        if type(fleet_size) is not int or not 1 <= fleet_size <= MAX_BACKGROUND_FLEET_WORKERS:
            raise ValueError("background fleet size is invalid")
        self.workspace = Path(workspace)
        self.fleet_size = fleet_size
        self.worker_count = worker_count
        self.executor_factory = executor_factory
        self.heartbeat_seconds = heartbeat_seconds
        self.idle_seconds = idle_seconds

    def run(self, *, max_items: int | None = None) -> BackgroundFleetResult:
        from concurrent.futures import ThreadPoolExecutor

        workers = tuple(
            PersistentChildWorker(
                self.workspace,
                worker_count=self.worker_count,
                executor_factory=self.executor_factory,
                heartbeat_seconds=self.heartbeat_seconds,
                idle_seconds=self.idle_seconds,
                slot=slot,
            )
            for slot in range(self.fleet_size)
        )
        with ThreadPoolExecutor(
            max_workers=self.fleet_size, thread_name_prefix="coquo-fleet"
        ) as pool:
            futures = tuple(pool.submit(worker.run, max_items=max_items) for worker in workers)
            results = tuple(future.result() for future in futures)
        outcome = "failed" if any(item.outcome == "failed" for item in results) else "completed"
        return BackgroundFleetResult(outcome, results)


def _open_file(path: Path, *, writable: bool) -> int:
    flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | (os.O_CREAT if writable else 0), 0o600)
    except OSError as error:
        raise BackgroundRuntimeError(
            f"could not open background runtime file: {path.name}"
        ) from error
    try:
        descriptor_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_IMODE(descriptor_stat.st_mode) != 0o600
        ):
            raise BackgroundRuntimeError("background runtime file is not a private regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _lock(descriptor: int, *, nonblocking: bool) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK if nonblocking else msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    except OSError as error:
        if nonblocking:
            raise BackgroundWorkerAlreadyRunning("background worker lease is owned") from error
        raise BackgroundRuntimeError("background queue lock is unavailable") from error


def _unlock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(descriptor, view)
        if count <= 0:
            raise BackgroundRuntimeError("background runtime append made no progress")
        view = view[count:]


def _atomic_write_json(path: Path, payload: dict[str, object], parent: Path) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        try:
            directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
