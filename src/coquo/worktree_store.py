"""Durable storage and leases for isolated Team worktrees."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from coquo.session_records import workspace_fingerprint
from coquo.worktree_records import (
    MAX_WORKTREE_PATCH_BYTES,
    WorktreeHeader,
    WorktreeRecord,
    WorktreeRecordError,
    WorktreeSealed,
    decode_worktree_record,
    encode_worktree_record,
    replay_worktree_records,
    utc_now,
)


class WorktreeStoreError(RuntimeError):
    """Raised when local worktree state cannot be trusted or persisted."""


class WorktreeAppendCommitError(WorktreeStoreError):
    def __init__(self, message: str, *, record_may_be_visible: bool) -> None:
        self.record_may_be_visible = record_may_be_visible
        super().__init__(message)


class WorktreeLeaseError(WorktreeStoreError):
    """Raised when another process owns the worktree lifecycle lease."""


@dataclass(frozen=True)
class WorktreeInfo:
    worktree_id: str
    path: Path
    state: str
    header: WorktreeHeader
    sealed: WorktreeSealed | None
    record_count: int


_ACTIVE_WRITERS: set[str] = set()
_ACTIVE_WRITERS_GUARD = Lock()
_ACTIVE_LEASES: set[str] = set()
_ACTIVE_LEASES_GUARD = Lock()
_LEASE_HEADER = b"coquo-worktree-v1\n"


class WorktreeLease:
    def __init__(self, descriptor: int, path: Path, key: str) -> None:
        self._descriptor = descriptor
        self.path = path
        self._key = key
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _unlock(self._descriptor)
        finally:
            with _ACTIVE_LEASES_GUARD:
                _ACTIVE_LEASES.discard(self._key)
            os.close(self._descriptor)

    def __enter__(self) -> WorktreeLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class WorktreeStore:
    def __init__(self, workspace: Path, *, clock: Callable[[], str] = utc_now) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise WorktreeStoreError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise WorktreeStoreError("workspace does not exist or is inaccessible") from None
        if not resolved.is_dir():
            raise WorktreeStoreError("workspace must be a directory")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".coquo" / "worktrees" / self.workspace_fingerprint
        self._clock = clock

    def declare(
        self,
        *,
        worktree_id: str,
        team_id: str,
        assignment_id: str,
        child_run_id: str,
        member_id: str,
        role_contract: str,
        target_ref: str,
        base_commit: str,
        branch: str,
        relative_path: str,
        created_at: str | None = None,
    ) -> WorktreeInfo:
        header = WorktreeHeader(
            sequence=0,
            worktree_id=worktree_id,
            authority_workspace=str(self.workspace),
            authority_workspace_fingerprint=self.workspace_fingerprint,
            team_id=team_id,
            assignment_id=assignment_id,
            child_run_id=child_run_id,
            member_id=member_id,
            role_contract=role_contract,
            target_ref=target_ref,
            base_commit=base_commit,
            branch=branch,
            relative_path=relative_path,
            created_at=created_at or self._clock(),
        )
        self._ensure_root()
        path = self.transcript_path(worktree_id)
        payload = encode_worktree_record(header)
        try:
            descriptor, temporary = tempfile.mkstemp(
                dir=path.parent, prefix=".worktree.", suffix=".tmp"
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path, follow_symlinks=False)
            _fsync_directory(path.parent)
            os.unlink(temporary)
            _fsync_directory(path.parent)
        except FileExistsError:
            raise WorktreeStoreError("worktree ID already exists") from None
        except OSError:
            raise WorktreeStoreError("could not durably create worktree ledger") from None
        finally:
            if "temporary" in locals():
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            if "descriptor" in locals() and descriptor not in {-1, None}:
                os.close(descriptor)
        return self.inspect(worktree_id)

    def transcript_path(self, worktree_id: str) -> Path:
        return self.root / f"{worktree_id}.jsonl"

    def artifact_path(self, worktree_id: str) -> Path:
        return self.root / f"{worktree_id}.sealed.patch"

    def lease_path(self, worktree_id: str) -> Path:
        return self.root / f"{worktree_id}.lifecycle-v1.lock"

    def inspect(self, worktree_id: str) -> WorktreeInfo:
        path = self.transcript_path(worktree_id)
        data = _read_path(path, self.workspace)
        if len(data) > 2 * 1024 * 1024 or not data.endswith(b"\n"):
            raise WorktreeStoreError("worktree ledger is oversized or incomplete")
        try:
            records = [decode_worktree_record(line) for line in data.splitlines()]
            state = replay_worktree_records(records)
        except WorktreeRecordError as error:
            raise WorktreeStoreError(f"invalid worktree ledger: {error}") from None
        if (
            state.header.authority_workspace != str(self.workspace)
            or state.header.authority_workspace_fingerprint != self.workspace_fingerprint
            or state.header.worktree_id != worktree_id
        ):
            raise WorktreeStoreError("worktree ledger belongs to another workspace or ID")
        return WorktreeInfo(
            worktree_id, path, state.state.value, state.header, state.sealed, len(records)
        )

    def append(self, worktree_id: str, record: WorktreeRecord) -> WorktreeInfo:
        path = self.transcript_path(worktree_id)
        key = str(path)
        with _writer_claim(key):
            descriptor = _open_rw_append(path)
            try:
                data = _read_descriptor(descriptor, path)
                records = [decode_worktree_record(line) for line in data.splitlines()]
                if not records or records[0].worktree_id != worktree_id:
                    raise WorktreeStoreError("worktree ledger identity is invalid")
                if record.sequence != len(records):
                    raise WorktreeStoreError("worktree record sequence is stale")
                candidate = replay_worktree_records([*records, record])
                del candidate
                payload = encode_worktree_record(record)
                if len(data) + len(payload) > 2 * 1024 * 1024:
                    raise WorktreeStoreError("worktree ledger exceeds size bound")
                os.lseek(descriptor, 0, os.SEEK_END)
                wrote = False
                view = memoryview(payload)
                while view:
                    wrote = True
                    count = os.write(descriptor, view)
                    if count <= 0:
                        raise OSError("worktree ledger append made no progress")
                    view = view[count:]
                os.fsync(descriptor)
            except WorktreeStoreError:
                raise
            except OSError:
                raise WorktreeAppendCommitError(
                    "worktree append durability is uncertain",
                    record_may_be_visible=wrote if "wrote" in locals() else False,
                ) from None
            finally:
                os.close(descriptor)
        return self.inspect(worktree_id)

    def acquire_lease(self, worktree_id: str) -> WorktreeLease:
        self._ensure_root()
        path = self.lease_path(worktree_id)
        if path.is_symlink():
            raise WorktreeLeaseError("worktree lease path must not be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError:
            raise WorktreeLeaseError("worktree lease is inaccessible") from None
        key = str(path)
        claimed = False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise WorktreeLeaseError("worktree lease must be a 0600 regular file")
            os.lseek(descriptor, 0, os.SEEK_SET)
            current = os.read(descriptor, len(_LEASE_HEADER) + 1)
            if current not in {b"", _LEASE_HEADER}:
                raise WorktreeLeaseError("worktree lease header is invalid")
            if current == b"":
                os.write(descriptor, _LEASE_HEADER)
                os.fsync(descriptor)
            with _ACTIVE_LEASES_GUARD:
                if key in _ACTIVE_LEASES:
                    raise WorktreeLeaseError("worktree already has an active lease")
                _ACTIVE_LEASES.add(key)
                claimed = True
            _lock_nonblocking(descriptor)
            return WorktreeLease(descriptor, path, key)
        except BaseException:
            if claimed:
                with _ACTIVE_LEASES_GUARD:
                    _ACTIVE_LEASES.discard(key)
            os.close(descriptor)
            raise

    def write_patch_artifact(self, worktree_id: str, payload: bytes) -> Path:
        if not isinstance(payload, bytes) or len(payload) > MAX_WORKTREE_PATCH_BYTES:
            raise WorktreeStoreError("patch artifact exceeds bound")
        self._ensure_root()
        target = self.artifact_path(worktree_id)
        if target.exists() or target.is_symlink():
            existing = target.read_bytes()
            if existing != payload:
                raise WorktreeStoreError("sealed patch artifact already differs")
            return target
        temporary = self.root / f".{worktree_id}.patch.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.link(temporary, target, follow_symlinks=False)
            _fsync_directory(self.root)
        except FileExistsError:
            if not target.exists() or target.read_bytes() != payload:
                raise WorktreeStoreError("sealed patch artifact collision") from None
        except OSError:
            raise WorktreeStoreError("could not install sealed patch artifact") from None
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
        return target

    def _ensure_root(self) -> None:
        _ensure_directory(self.workspace / ".coquo", self.workspace)
        _ensure_directory(self.workspace / ".coquo" / "worktrees", self.workspace)
        _ensure_directory(self.root, self.workspace)


class _writer_claim:
    def __init__(self, key: str) -> None:
        self.key = key

    def __enter__(self):
        with _ACTIVE_WRITERS_GUARD:
            if self.key in _ACTIVE_WRITERS:
                raise WorktreeStoreError("worktree ledger already has an active writer")
            _ACTIVE_WRITERS.add(self.key)

    def __exit__(self, *_: object) -> None:
        with _ACTIVE_WRITERS_GUARD:
            _ACTIVE_WRITERS.discard(self.key)


def _open_rw_append(path: Path) -> int:
    if path.is_symlink():
        raise WorktreeStoreError("worktree ledger must not be a symlink")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
        _lock_exclusive(descriptor)
        return descriptor
    except OSError:
        raise WorktreeStoreError("worktree ledger is inaccessible") from None


def _read_path(path: Path, boundary: Path) -> bytes:
    if boundary not in path.parents or path.is_symlink():
        raise WorktreeStoreError("worktree ledger path escapes or is symlinked")
    descriptor = _open_rw_append(path)
    try:
        return _read_descriptor(descriptor, path)
    finally:
        _unlock(descriptor)
        os.close(descriptor)


def _read_descriptor(descriptor: int, path: Path) -> bytes:
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WorktreeStoreError("worktree ledger must be a regular file")
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(data) != before.st_size or (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise WorktreeStoreError("worktree ledger changed during read")
        pathname = path.lstat()
        if (pathname.st_dev, pathname.st_ino) != (before.st_dev, before.st_ino):
            raise WorktreeStoreError("worktree ledger path changed during read")
        return data
    except WorktreeStoreError:
        raise
    except OSError:
        raise WorktreeStoreError("worktree ledger could not be read") from None


def _ensure_directory(path: Path, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise WorktreeStoreError("worktree storage path escapes workspace")
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
            raise WorktreeStoreError("could not create worktree storage directory") from None
    except OSError:
        raise WorktreeStoreError("worktree storage directory is inaccessible") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise WorktreeStoreError("worktree storage path must be a real directory")


def _fsync_directory(path: Path) -> None:
    descriptor = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        os.fsync(descriptor)
    except OSError:
        raise WorktreeStoreError("could not confirm worktree directory durability") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _lock_exclusive(descriptor: int) -> None:
    if os.name == "nt":
        try:
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        except OSError:
            os.close(descriptor)
            raise WorktreeStoreError("worktree ledger lock failed") from None
    else:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            os.close(descriptor)
            raise WorktreeStoreError("worktree ledger lock failed") from None


def _lock_nonblocking(descriptor: int) -> None:
    try:
        if os.name == "nt":
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise WorktreeLeaseError("worktree lease is already held") from None


def _unlock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
