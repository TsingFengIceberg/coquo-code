"""Workspace-bound append-only storage for durable Leonervis Code tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from leonervis_code.session_records import workspace_fingerprint
from leonervis_code.session_store import SessionStore, SessionStoreError, SessionTurnEvidence
from leonervis_code.task_records import (
    MAX_TASK_RECORDS,
    StageCommitted,
    StageFailed,
    StageFailureReason,
    StageStarted,
    TaskHeader,
    TaskRecord,
    TaskRecordError,
    TaskReplayState,
    TaskScope,
    TaskStatus,
    canonical_acceptance_criteria,
    canonical_stage_id,
    canonical_stage_objective,
    canonical_task_id,
    canonical_task_objective,
    decode_task_record,
    encode_task_record,
    replay_task_records,
)

MAX_TASK_TRANSCRIPT_BYTES = 1024 * 1024
MAX_TASK_DIRECTORY_ENTRIES = 10_000


class TaskStoreError(RuntimeError):
    """Raised when durable Task persistence cannot proceed safely."""


class TaskCreateCommitError(TaskStoreError):
    """Report whether a failed create made the final Task name visible."""

    def __init__(self, message: str, *, task_visible: bool) -> None:
        self.task_visible = task_visible
        super().__init__(message)


class TaskAppendCommitError(TaskStoreError):
    """Report that a Stage record may be visible with uncertain durability."""

    def __init__(self, message: str, *, record_may_be_visible: bool) -> None:
        self.record_may_be_visible = record_may_be_visible
        super().__init__(message)


@dataclass(frozen=True)
class TaskStageInfo:
    """Terminal-safe metadata for one strictly replayed Stage."""

    stage_id: str
    stage_number: int
    objective: str
    started_at: str
    outcome: str
    terminal_at: str | None
    turn_number: int | None
    turn_record_sequence: int | None
    turn_record_sha256: str | None
    failure_reason: StageFailureReason | None


@dataclass(frozen=True)
class TaskInfo:
    """Validated metadata and objective for one durable Task."""

    task_id: str
    path: Path
    workspace: str
    workspace_fingerprint: str
    owner_session_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    created_at: str
    scope: TaskScope
    status: TaskStatus
    record_count: int
    stages: tuple[TaskStageInfo, ...]


def utc_now() -> str:
    """Return the canonical Task timestamp representation."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class TaskStore:
    """Create and strictly inspect workspace-bound Task transcripts."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        stage_uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise TaskStoreError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise TaskStoreError(
                f"workspace does not exist or is inaccessible: {requested}"
            ) from None
        if not resolved.is_dir():
            raise TaskStoreError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".leonervis-code" / "tasks" / self.workspace_fingerprint
        self._uuid_factory = uuid_factory
        self._stage_uuid_factory = stage_uuid_factory
        self._clock = clock

    def create(
        self,
        objective: str,
        *,
        owner_session: str = "latest",
        acceptance_criteria: tuple[str, ...] = (),
    ) -> TaskInfo:
        """Atomically create one ready Task owned by an existing Session."""
        try:
            canonical_objective = canonical_task_objective(objective)
            canonical_criteria = canonical_acceptance_criteria(acceptance_criteria)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        try:
            owner = SessionStore(self.workspace).inspect(owner_session)
        except SessionStoreError as error:
            raise TaskStoreError(f"owner Session is invalid or unavailable: {error}") from None
        task_id = _factory_task_id(self._uuid_factory)
        header = TaskHeader(
            sequence=0,
            task_id=task_id,
            workspace=str(self.workspace),
            workspace_fingerprint=self.workspace_fingerprint,
            owner_session_id=owner.session_id,
            objective=canonical_objective,
            acceptance_criteria=canonical_criteria,
            created_at=self._clock(),
        )
        try:
            payload = encode_task_record(header)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        self._ensure_root()
        path = self.root / f"{task_id}.jsonl"
        _install_task_transcript(path, payload)
        state = self._replay(path, [header])
        return _task_info(path, state)

    def inspect(self, task_id: str) -> TaskInfo:
        """Strictly replay one exact Task ID without creating or repairing state."""
        canonical = _store_task_id(task_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        state = self._load_state(path)
        return _task_info(
            path,
            state,
            active_stage=state.active_stage is not None and _task_writer_is_active(path),
        )

    def open(self, task_id: str) -> TaskWriter:
        """Take one exclusive foreground writer lease for a durable Task."""
        canonical = _store_task_id(task_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        descriptor = _open_task_transcript(path, writable=True)
        key = str(path)
        claimed = False
        locked = False
        try:
            _claim_active_writer(key)
            claimed = True
            _lock_descriptor(descriptor)
            locked = True
            data = _read_task_descriptor(descriptor, path)
            state = self._decode_state(path, data)
            return TaskWriter(self, path, descriptor, state, key)
        except BaseException:
            if locked:
                _unlock_descriptor(descriptor)
            if claimed:
                _release_active_writer(key)
            os.close(descriptor)
            raise

    def list(self) -> tuple[TaskInfo, ...]:
        """Strictly list bounded Task transcripts without creating local state."""
        if not self.root.exists() and not self.root.is_symlink():
            _validate_optional_parent_chain(self.workspace, self.root.parent)
            return ()
        self._validate_existing_root()
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            raise TaskStoreError("task directory is inaccessible") from None
        if len(entries) > MAX_TASK_DIRECTORY_ENTRIES:
            raise TaskStoreError(f"task directory exceeds {MAX_TASK_DIRECTORY_ENTRIES} entries")
        paths: list[Path] = []
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            path = self.root / entry.name
            _task_id_from_path(path)
            paths.append(path)
        tasks = tuple(self._inspect_path(path) for path in paths)
        return tuple(sorted(tasks, key=lambda task: (task.created_at, task.task_id), reverse=True))

    def _inspect_path(self, path: Path) -> TaskInfo:
        state = self._load_state(path)
        return _task_info(
            path,
            state,
            active_stage=state.active_stage is not None and _task_writer_is_active(path),
        )

    def _load_state(self, path: Path) -> TaskReplayState:
        _task_id_from_path(path)
        data = _read_task_transcript(path)
        return self._decode_state(path, data)

    def _decode_state(self, path: Path, data: bytes) -> TaskReplayState:
        if not data.endswith(b"\n"):
            raise TaskStoreError("task transcript does not end at a durable record boundary")
        lines = data.splitlines()
        if len(lines) > MAX_TASK_RECORDS:
            raise TaskStoreError(f"task transcript exceeds {MAX_TASK_RECORDS} records")
        try:
            records = [decode_task_record(line) for line in lines]
        except TaskRecordError as error:
            raise TaskStoreError(f"invalid task transcript {path}: {error}") from None
        return self._replay(path, records)

    def _replay(self, path: Path, records: list[TaskRecord]) -> TaskReplayState:
        try:
            return replay_task_records(
                records,
                expected_workspace=str(self.workspace),
                expected_workspace_fingerprint=self.workspace_fingerprint,
                expected_task_id=_task_id_from_path(path),
                expected_file_name=path.name,
            )
        except TaskRecordError as error:
            raise TaskStoreError(f"invalid task transcript {path}: {error}") from None

    def _ensure_root(self) -> None:
        _ensure_directory(self.workspace / ".leonervis-code", boundary=self.workspace)
        _ensure_directory(self.workspace / ".leonervis-code" / "tasks", boundary=self.workspace)
        _ensure_directory(self.root, boundary=self.workspace)

    def _validate_existing_root(self) -> None:
        _validate_directory(self.workspace / ".leonervis-code", self.workspace)
        _validate_directory(self.workspace / ".leonervis-code" / "tasks", self.workspace)
        _validate_directory(self.root, self.workspace)


_ACTIVE_TASK_WRITERS: set[str] = set()
_ACTIVE_TASK_WRITERS_GUARD = Lock()


class TaskWriter:
    """Exclusive append-only writer for one foreground Task Stage."""

    def __init__(
        self,
        store: TaskStore,
        path: Path,
        descriptor: int,
        state: TaskReplayState,
        active_key: str,
    ) -> None:
        self._store = store
        self.path = path
        self._descriptor = descriptor
        self._state = state
        self._active_key = active_key
        self._released = False
        self._uncertain = False

    @property
    def state(self) -> TaskReplayState:
        return self._state

    @property
    def info(self) -> TaskInfo:
        return _task_info(
            self.path,
            self._state,
            active_stage=self._state.active_stage is not None,
        )

    def start_stage(self, objective: str) -> StageStarted:
        """Durably start one bounded Stage before any provider work begins."""
        self._ensure_writable()
        if self._state.active_stage is not None:
            raise TaskStoreError("Task already has an unresolved Stage")
        try:
            canonical_objective = canonical_stage_objective(objective)
        except TaskRecordError as error:
            raise TaskStoreError(str(error)) from None
        stage_id = _factory_stage_id(self._store._stage_uuid_factory)
        record = StageStarted(
            sequence=self._state.next_sequence,
            stage_id=stage_id,
            stage_number=self._state.next_stage_number,
            session_id=self._state.header.owner_session_id,
            objective=canonical_objective,
            started_at=self._store._clock(),
        )
        self._append(record)
        return record

    def commit_stage(self, turn_record_sequence: int) -> StageCommitted:
        """Link the active Stage to one independently verified committed Session Turn."""
        self._ensure_writable()
        active = self._require_active_stage()
        try:
            evidence = SessionStore(self._store.workspace).turn_evidence(
                active.session_id,
                turn_record_sequence,
            )
        except SessionStoreError as error:
            raise TaskStoreError(f"Session Turn evidence is invalid: {error}") from None
        record = _committed_stage_record(
            self._state.next_sequence,
            active,
            evidence,
            self._store._clock(),
        )
        self._append(record)
        return record

    def fail_stage(self, reason: StageFailureReason) -> StageFailed:
        """Durably terminate the active Stage without claiming a committed Turn."""
        self._ensure_writable()
        active = self._require_active_stage()
        if type(reason) is not StageFailureReason:
            raise TaskStoreError("Stage failure reason is invalid")
        record = StageFailed(
            sequence=self._state.next_sequence,
            stage_id=active.stage_id,
            stage_number=active.stage_number,
            reason=reason,
            failed_at=self._store._clock(),
        )
        self._append(record)
        return record

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            _unlock_descriptor(self._descriptor)
        finally:
            os.close(self._descriptor)
            _release_active_writer(self._active_key)

    def __enter__(self) -> TaskWriter:
        self._ensure_writable()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def _append(self, record: TaskRecord) -> None:
        try:
            candidate = self._store._replay(self.path, [*self._state.records, record])
            _append_task_record_descriptor(self._descriptor, self.path, record)
        except TaskAppendCommitError:
            self._uncertain = True
            raise
        self._state = candidate

    def _require_active_stage(self) -> StageStarted:
        active = self._state.active_stage
        if active is None:
            raise TaskStoreError("Task has no active Stage")
        return active

    def _ensure_writable(self) -> None:
        if self._released:
            raise TaskStoreError("Task writer is released")
        if self._uncertain:
            raise TaskStoreError(
                "Task writer durability is uncertain; release and inspect before continuing"
            )


def _factory_task_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_task_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"task ID factory returned an invalid value: {error}") from None


def _factory_stage_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_stage_id(candidate)
    except TaskRecordError as error:
        raise TaskStoreError(f"Stage ID factory returned an invalid value: {error}") from None


def _store_task_id(value: object) -> str:
    try:
        return canonical_task_id(value)
    except TaskRecordError as error:
        raise TaskStoreError(str(error)) from None


def _task_id_from_path(path: Path) -> str:
    if path.suffix != ".jsonl":
        raise TaskStoreError("task transcript file name must end in .jsonl")
    try:
        return canonical_task_id(path.stem)
    except TaskRecordError as error:
        raise TaskStoreError(f"invalid task transcript file name: {error}") from None


def _task_info(
    path: Path,
    state: TaskReplayState,
    *,
    active_stage: bool = False,
) -> TaskInfo:
    header = state.header
    stages: list[TaskStageInfo] = []
    for stage in state.stages:
        terminal = stage.terminal
        if terminal is None:
            outcome = (
                "stage-in-progress" if active_stage and stage is state.stages[-1] else "interrupted"
            )
            terminal_at = None
            turn_number = None
            turn_record_sequence = None
            turn_record_sha256 = None
            failure_reason = None
        elif isinstance(terminal, StageCommitted):
            outcome = "committed"
            terminal_at = terminal.committed_at
            turn_number = terminal.turn_number
            turn_record_sequence = terminal.turn_record_sequence
            turn_record_sha256 = terminal.turn_record_sha256
            failure_reason = None
        else:
            outcome = "failed"
            terminal_at = terminal.failed_at
            turn_number = None
            turn_record_sequence = None
            turn_record_sha256 = None
            failure_reason = terminal.reason
        stages.append(
            TaskStageInfo(
                stage_id=stage.started.stage_id,
                stage_number=stage.started.stage_number,
                objective=stage.started.objective,
                started_at=stage.started.started_at,
                outcome=outcome,
                terminal_at=terminal_at,
                turn_number=turn_number,
                turn_record_sequence=turn_record_sequence,
                turn_record_sha256=turn_record_sha256,
                failure_reason=failure_reason,
            )
        )
    status = TaskStatus.STAGE_IN_PROGRESS if active_stage else state.status
    return TaskInfo(
        task_id=header.task_id,
        path=path,
        workspace=header.workspace,
        workspace_fingerprint=header.workspace_fingerprint,
        owner_session_id=header.owner_session_id,
        objective=header.objective,
        acceptance_criteria=header.acceptance_criteria,
        created_at=header.created_at,
        scope=header.scope,
        status=status,
        record_count=len(state.records),
        stages=tuple(stages),
    )


def _committed_stage_record(
    sequence: int,
    active: StageStarted,
    evidence: SessionTurnEvidence,
    committed_at: str,
) -> StageCommitted:
    if evidence.session_id != active.session_id:
        raise TaskStoreError("Session Turn evidence belongs to a different Session")
    if evidence.committed_at < active.started_at:
        raise TaskStoreError("Session Turn evidence predates the active Stage")
    if committed_at < evidence.committed_at:
        raise TaskStoreError("Stage commit timestamp predates its Session Turn evidence")
    return StageCommitted(
        sequence=sequence,
        stage_id=active.stage_id,
        stage_number=active.stage_number,
        session_id=evidence.session_id,
        turn_number=evidence.turn_number,
        turn_record_sequence=evidence.record_sequence,
        turn_record_sha256=evidence.record_sha256,
        committed_at=committed_at,
    )


def _claim_active_writer(key: str) -> None:
    with _ACTIVE_TASK_WRITERS_GUARD:
        if key in _ACTIVE_TASK_WRITERS:
            raise TaskStoreError("Task already has an active writer")
        _ACTIVE_TASK_WRITERS.add(key)


def _release_active_writer(key: str) -> None:
    with _ACTIVE_TASK_WRITERS_GUARD:
        _ACTIVE_TASK_WRITERS.discard(key)


def _task_writer_is_active(path: Path) -> bool:
    with _ACTIVE_TASK_WRITERS_GUARD:
        if str(path) in _ACTIVE_TASK_WRITERS:
            return True
    descriptor = _open_task_transcript(path, writable=True)
    try:
        try:
            _lock_descriptor(descriptor)
        except TaskStoreError:
            return True
        _unlock_descriptor(descriptor)
        return False
    finally:
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise TaskStoreError("Task already has an active writer") from None


def _unlock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _open_task_transcript(path: Path, *, writable: bool) -> int:
    if path.parent.is_symlink() or path.is_symlink():
        raise TaskStoreError("task transcript path must not contain a symlink")
    flags = (os.O_RDWR | os.O_APPEND) if writable else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise TaskStoreError(f"task transcript is inaccessible: {path}") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskStoreError("task transcript must be a regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_task_descriptor(descriptor: int, path: Path) -> bytes:
    try:
        before = os.fstat(descriptor)
        if before.st_size > MAX_TASK_TRANSCRIPT_BYTES:
            raise TaskStoreError(f"task transcript exceeds {MAX_TASK_TRANSCRIPT_BYTES} bytes")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size)
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != identity:
            raise TaskStoreError("task transcript changed while it was being read")
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise TaskStoreError("task transcript path changed while it was being read")
        return data
    except TaskStoreError:
        raise
    except OSError:
        raise TaskStoreError(f"task transcript is inaccessible: {path}") from None


def _append_task_record_descriptor(
    descriptor: int,
    path: Path,
    record: TaskRecord,
) -> None:
    try:
        payload = encode_task_record(record)
    except TaskRecordError as error:
        raise TaskStoreError(str(error)) from None
    write_started = False
    try:
        info = os.fstat(descriptor)
        pathname = path.lstat()
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise TaskStoreError("task transcript path no longer matches its writer")
        if info.st_size + len(payload) > MAX_TASK_TRANSCRIPT_BYTES:
            raise TaskStoreError(f"task transcript would exceed {MAX_TASK_TRANSCRIPT_BYTES} bytes")
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(payload)
        while view:
            write_started = True
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("task transcript append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except TaskStoreError:
        raise
    except OSError:
        raise TaskAppendCommitError(
            "could not durably append task transcript; inspect before retrying",
            record_may_be_visible=write_started,
        ) from None


def _install_task_transcript(path: Path, payload: bytes) -> None:
    temporary: str | None = None
    descriptor: int | None = None
    linked = False
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".task.", suffix=".tmp")
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
        raise TaskStoreError(f"task ID collision: {path.stem}") from None
    except (OSError, TaskStoreError):
        raise TaskCreateCommitError(
            "could not durably create task transcript",
            task_visible=linked,
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_task_transcript(path: Path) -> bytes:
    if path.parent.is_symlink() or path.is_symlink():
        raise TaskStoreError("task transcript path must not contain a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise TaskStoreError("task transcript must be a regular file")
        if before.st_size > MAX_TASK_TRANSCRIPT_BYTES:
            raise TaskStoreError(f"task transcript exceeds {MAX_TASK_TRANSCRIPT_BYTES} bytes")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size)
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != identity:
            raise TaskStoreError("task transcript changed while it was being read")
        if (pathname.st_dev, pathname.st_ino) != (before.st_dev, before.st_ino):
            raise TaskStoreError("task transcript path changed while it was being read")
        return data
    except TaskStoreError:
        raise
    except OSError:
        raise TaskStoreError(f"task transcript is inaccessible: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _ensure_directory(path: Path, *, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise TaskStoreError("task storage path escapes the workspace")
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
            raise TaskStoreError(f"could not create task storage directory: {path}") from None
    except OSError:
        raise TaskStoreError(f"task storage directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TaskStoreError(f"task storage path must be a real directory: {path}")


def _validate_directory(path: Path, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise TaskStoreError("task storage path escapes the workspace")
    try:
        info = path.lstat()
    except OSError:
        raise TaskStoreError(f"task storage directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TaskStoreError(f"task storage path must be a real directory: {path}")


def _validate_optional_parent_chain(workspace: Path, parent: Path) -> None:
    current = workspace / ".leonervis-code"
    stop = parent
    while current == stop or current in stop.parents:
        if current.exists() or current.is_symlink():
            _validate_directory(current, workspace)
        else:
            return
        if current == stop:
            return
        relative = stop.relative_to(current)
        current = current / relative.parts[0]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise TaskStoreError(f"could not confirm task directory durability: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    (StageCommitted,)
    (StageFailed,)
    (StageFailureReason,)
    (StageStarted,)
    (canonical_stage_objective,)
