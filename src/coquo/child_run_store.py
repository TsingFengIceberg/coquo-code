"""Workspace-bound durable storage for queued/cancelled Child Runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
import stat
import tempfile
from threading import Lock
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from coquo.child_run_records import (
    MAX_CHILD_RUN_RECORDS,
    MAX_CHILD_RUN_TRANSCRIPT_BYTES,
    ChildRunCancelled,
    ChildRunHeader,
    ChildRunRecord,
    ChildRunRecordError,
    ChildRunReplayState,
    ChildRunStatus,
    canonical_child_run_id,
    canonical_child_run_objective,
    canonical_child_run_reason,
    decode_child_run_record,
    encode_child_run_record,
    replay_child_run_records,
    utc_now,
)
from coquo.session_records import canonical_session_id, workspace_fingerprint
from coquo.session_store import SessionStore, SessionStoreError

MAX_CHILD_RUN_DIRECTORY_ENTRIES = 10_000


class ChildRunStoreError(RuntimeError):
    """Raised when Child Run persistence cannot proceed safely."""


class ChildRunCreateCommitError(ChildRunStoreError):
    def __init__(self, message: str, *, child_run_visible: bool) -> None:
        self.child_run_visible = child_run_visible
        super().__init__(message)


class ChildRunAppendCommitError(ChildRunStoreError):
    def __init__(self, message: str, *, record_may_be_visible: bool) -> None:
        self.record_may_be_visible = record_may_be_visible
        super().__init__(message)


@dataclass(frozen=True)
class ChildRunInfo:
    child_run_id: str
    path: Path
    workspace: str
    workspace_fingerprint: str
    parent_session_id: str
    objective: str
    created_at: str
    status: ChildRunStatus
    record_count: int
    cancelled_at: str | None = None
    cancellation_reason: str | None = None


_ACTIVE_WRITERS: set[str] = set()
_ACTIVE_WRITERS_GUARD = Lock()


class ChildRunStore:
    """Create and strictly inspect workspace-bound Child Run transcripts."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise ChildRunStoreError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise ChildRunStoreError(f"workspace does not exist or is inaccessible: {requested}") from None
        if not resolved.is_dir():
            raise ChildRunStoreError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".coquo" / "child-runs" / self.workspace_fingerprint
        self._uuid_factory = uuid_factory
        self._clock = clock

    def create(self, objective: str, *, parent_session: str = "latest") -> ChildRunInfo:
        try:
            objective = canonical_child_run_objective(objective)
            owner = SessionStore(self.workspace).inspect(parent_session)
            parent_session_id = canonical_session_id(owner.session_id)
        except (ChildRunRecordError, SessionStoreError) as error:
            raise ChildRunStoreError(str(error)) from None
        child_run_id = _factory_uuid(self._uuid_factory, "Child Run ID")
        created_at = self._clock()
        header = ChildRunHeader(
            sequence=0,
            child_run_id=child_run_id,
            workspace=str(self.workspace),
            workspace_fingerprint=self.workspace_fingerprint,
            parent_session_id=parent_session_id,
            objective=objective,
            created_at=created_at,
        )
        payload = encode_child_run_record(header)
        self._ensure_root()
        path = self.root / f"{child_run_id}.jsonl"
        _install_child_run_transcript(path, payload)
        state = self._replay(path, [header])
        return _child_run_info(path, state)

    def inspect(self, child_run_id: str) -> ChildRunInfo:
        canonical = _store_child_run_id(child_run_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        return _child_run_info(path, self._load_state(path))

    def list(self, *, status: ChildRunStatus | None = None) -> tuple[ChildRunInfo, ...]:
        if not self.root.exists() and not self.root.is_symlink():
            return ()
        self._validate_existing_root()
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            raise ChildRunStoreError("Child Run directory is inaccessible") from None
        if len(entries) > MAX_CHILD_RUN_DIRECTORY_ENTRIES:
            raise ChildRunStoreError(
                f"Child Run directory exceeds {MAX_CHILD_RUN_DIRECTORY_ENTRIES} entries"
            )
        runs = []
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            path = self.root / entry.name
            _store_child_run_id(path.stem)
            info = _child_run_info(path, self._load_state(path))
            if status is None or info.status is status:
                runs.append(info)
        return tuple(sorted(runs, key=lambda item: (item.created_at, item.child_run_id), reverse=True))

    def open(self, child_run_id: str) -> ChildRunWriter:
        canonical = _store_child_run_id(child_run_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        descriptor = _open_transcript(path, writable=True)
        key = str(path)
        claimed = False
        locked = False
        try:
            _claim_writer(key)
            claimed = True
            _lock_descriptor(descriptor)
            locked = True
            state = self._decode_state(path, _read_descriptor(descriptor, path))
            return ChildRunWriter(self, path, descriptor, state, key)
        except BaseException:
            if locked:
                _unlock_descriptor(descriptor)
            if claimed:
                _release_writer(key)
            os.close(descriptor)
            raise

    def _ensure_root(self) -> None:
        _ensure_directory(self.workspace / ".coquo", self.workspace)
        _ensure_directory(self.workspace / ".coquo" / "child-runs", self.workspace)
        _ensure_directory(self.root, self.workspace)

    def _validate_existing_root(self) -> None:
        _validate_directory(self.root, self.workspace)

    def _load_state(self, path: Path) -> ChildRunReplayState:
        _path_child_run_id(path)
        return self._decode_state(path, _read_transcript(path))

    def _decode_state(self, path: Path, data: bytes) -> ChildRunReplayState:
        if len(data) > MAX_CHILD_RUN_TRANSCRIPT_BYTES:
            raise ChildRunStoreError("Child Run transcript is oversized")
        if not data.endswith(b"\n"):
            raise ChildRunStoreError("Child Run transcript does not end at a durable record boundary")
        lines = data.splitlines()
        if len(lines) > MAX_CHILD_RUN_RECORDS:
            raise ChildRunStoreError("Child Run transcript exceeds record limit")
        try:
            records = [decode_child_run_record(line) for line in lines]
            return self._replay(path, records)
        except ChildRunRecordError as error:
            raise ChildRunStoreError(f"invalid Child Run transcript {path}: {error}") from None

    def _replay(self, path: Path, records: list[ChildRunRecord]) -> ChildRunReplayState:
        try:
            state = replay_child_run_records(records)
        except ChildRunRecordError as error:
            raise ChildRunStoreError(f"invalid Child Run transcript {path}: {error}") from None
        if state.header.child_run_id != path.stem:
            raise ChildRunStoreError("Child Run filename does not match its record ID")
        if state.header.workspace != str(self.workspace) or state.header.workspace_fingerprint != self.workspace_fingerprint:
            raise ChildRunStoreError("Child Run transcript belongs to another workspace")
        return state


class ChildRunWriter:
    """Exclusive append-only writer for one queued Child Run."""

    def __init__(self, store: ChildRunStore, path: Path, descriptor: int, state: ChildRunReplayState, key: str) -> None:
        self._store = store
        self._path = path
        self._descriptor = descriptor
        self._state = state
        self._key = key
        self._closed = False
        self._poisoned = False

    @property
    def state(self) -> ChildRunReplayState:
        return self._state

    @property
    def info(self) -> ChildRunInfo:
        return _child_run_info(self._path, self._state)

    def cancel(self, reason: str) -> ChildRunCancelled:
        if self._closed:
            raise ChildRunStoreError("Child Run writer is closed")
        if self._poisoned:
            raise ChildRunStoreError("Child Run writer durability is uncertain")
        if self._state.status is not ChildRunStatus.QUEUED:
            raise ChildRunStoreError("Child Run is already cancelled")
        record = ChildRunCancelled(
            sequence=1,
            child_run_id=self._state.header.child_run_id,
            reason=canonical_child_run_reason(reason),
            cancelled_at=self._store._clock(),
        )
        candidate = list(self._state.records) + [record]
        state = self._store._replay(self._path, candidate)
        try:
            _append_record(self._descriptor, self._path, record)
        except ChildRunAppendCommitError:
            # The append may have reached the filesystem without a durable
            # commit.  This writer must never issue a second append blindly.
            self._poisoned = True
            raise
        self._state = state
        return record

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _unlock_descriptor(self._descriptor)
        finally:
            _release_writer(self._key)
            os.close(self._descriptor)

    def __enter__(self) -> ChildRunWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _factory_uuid(factory: Callable[[], UUID | str], label: str) -> str:
    try:
        value = factory()
        if isinstance(value, UUID):
            value = str(value)
        return canonical_child_run_id(value)
    except (ChildRunRecordError, ValueError, TypeError) as error:
        raise ChildRunStoreError(f"{label} is invalid: {error}") from None


def _store_child_run_id(value: object) -> str:
    try:
        return canonical_child_run_id(value)
    except ChildRunRecordError as error:
        raise ChildRunStoreError(str(error)) from None


def _path_child_run_id(path: Path) -> str:
    return _store_child_run_id(path.stem)


def _child_run_info(path: Path, state: ChildRunReplayState) -> ChildRunInfo:
    cancelled = state.cancelled
    return ChildRunInfo(
        child_run_id=state.header.child_run_id,
        path=path,
        workspace=state.header.workspace,
        workspace_fingerprint=state.header.workspace_fingerprint,
        parent_session_id=state.header.parent_session_id,
        objective=state.header.objective,
        created_at=state.header.created_at,
        status=state.status,
        record_count=len(state.records),
        cancelled_at=cancelled.cancelled_at if cancelled else None,
        cancellation_reason=cancelled.reason if cancelled else None,
    )


def _claim_writer(key: str) -> None:
    with _ACTIVE_WRITERS_GUARD:
        if key in _ACTIVE_WRITERS:
            raise ChildRunStoreError("Child Run already has an active writer")
        _ACTIVE_WRITERS.add(key)


def _release_writer(key: str) -> None:
    with _ACTIVE_WRITERS_GUARD:
        _ACTIVE_WRITERS.discard(key)


def _lock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise ChildRunStoreError("Child Run already has an active writer") from None


def _unlock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _open_transcript(path: Path, *, writable: bool) -> int:
    if path.parent.is_symlink() or path.is_symlink():
        raise ChildRunStoreError("Child Run transcript path must not contain a symlink")
    flags = (os.O_RDWR | os.O_APPEND) if writable else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ChildRunStoreError(f"Child Run transcript is inaccessible: {path}") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ChildRunStoreError("Child Run transcript must be a regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_descriptor(descriptor: int, path: Path) -> bytes:
    try:
        before = os.fstat(descriptor)
        if before.st_size > MAX_CHILD_RUN_TRANSCRIPT_BYTES:
            raise ChildRunStoreError("Child Run transcript is oversized")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise ChildRunStoreError("Child Run transcript ended during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = path.lstat()
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise ChildRunStoreError("Child Run transcript changed while it was being read")
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (before.st_dev, before.st_ino):
            raise ChildRunStoreError("Child Run transcript path changed while it was being read")
        return data
    except ChildRunStoreError:
        raise
    except OSError:
        raise ChildRunStoreError(f"Child Run transcript is inaccessible: {path}") from None


def _read_transcript(path: Path) -> bytes:
    descriptor = _open_transcript(path, writable=False)
    try:
        return _read_descriptor(descriptor, path)
    finally:
        os.close(descriptor)


def _append_record(descriptor: int, path: Path, record: ChildRunRecord) -> None:
    payload = encode_child_run_record(record)
    write_started = False
    try:
        info = os.fstat(descriptor)
        pathname = path.lstat()
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (info.st_dev, info.st_ino):
            raise ChildRunStoreError("Child Run transcript path no longer matches its writer")
        if info.st_size + len(payload) > MAX_CHILD_RUN_TRANSCRIPT_BYTES:
            raise ChildRunStoreError("Child Run transcript would exceed its bound")
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(payload)
        while view:
            write_started = True
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Child Run append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except ChildRunStoreError:
        raise
    except OSError:
        raise ChildRunAppendCommitError(
            "could not durably append Child Run; inspect before retrying",
            record_may_be_visible=write_started,
        ) from None


def _install_child_run_transcript(path: Path, payload: bytes) -> None:
    temporary: str | None = None
    descriptor: int | None = None
    linked = False
    try:
        _ensure_directory(path.parent, path.parent.parent.parent.parent)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".child-run.", suffix=".tmp")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        linked = True
        _fsync_directory(path.parent)
        os.unlink(temporary)
        temporary = None
        _fsync_directory(path.parent)
    except FileExistsError:
        raise ChildRunStoreError(f"Child Run ID collision: {path.stem}") from None
    except (OSError, ChildRunStoreError):
        raise ChildRunCreateCommitError(
            "could not durably create Child Run transcript",
            child_run_visible=linked,
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _ensure_directory(path: Path, boundary: Path) -> None:
    if boundary not in path.parents and path != boundary:
        raise ChildRunStoreError("Child Run storage path escapes workspace")
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
            _fsync_directory(path.parent)
            return
        except FileExistsError:
            info = path.lstat()
        except OSError:
            raise ChildRunStoreError(f"could not create Child Run directory: {path}") from None
    except OSError:
        raise ChildRunStoreError(f"Child Run directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ChildRunStoreError(f"Child Run path must be a real directory: {path}")


def _validate_directory(path: Path, boundary: Path) -> None:
    if boundary not in path.parents and path != boundary:
        raise ChildRunStoreError("Child Run storage path escapes workspace")
    try:
        info = path.lstat()
    except OSError:
        raise ChildRunStoreError(f"Child Run directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ChildRunStoreError(f"Child Run path must be a real directory: {path}")


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        os.fsync(descriptor)
    except OSError:
        raise ChildRunStoreError(f"could not confirm Child Run directory durability: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
