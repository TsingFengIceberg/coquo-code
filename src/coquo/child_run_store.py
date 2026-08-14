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
    ChildRunDelegated,
    ChildRunAdmitted,
    ChildRunHeader,
    ChildSessionBound,
    ChildRunPreparationFailed,
    ChildRunStarted,
    ChildRunCompleted,
    ChildRunFailed,
    ChildRunCancelRequested,
    ChildRunCancelledTerminal,
    ChildRunInterrupted,
    ChildRunHandoffPublished,
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
from coquo.session_records import BindingSnapshot
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


class ChildRunExecutionLeaseError(ChildRunStoreError):
    """Raised when another process or thread owns one Child execution lease."""


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
    child_session_id: str | None = None
    tool_set_id: str | None = None
    tool_names: tuple[str, ...] = ()
    provider_binding: dict[str, object] | None = None
    preparation_failure: str | None = None
    execution_id: str | None = None
    assistant_text_sha256: str | None = None
    session_record_sequence: int | None = None
    failure_result_code: str | None = None
    cancellation_request_reason: str | None = None
    cancellation_request_source: str | None = None
    cancellation_request_sequence: int | None = None
    interrupted_result_code: str | None = None
    handoff: ChildRunHandoffPublished | None = None
    delegated: ChildRunDelegated | None = None


_ACTIVE_WRITERS: set[str] = set()
_ACTIVE_WRITERS_GUARD = Lock()
_ACTIVE_EXECUTION_LEASES: set[str] = set()
_ACTIVE_EXECUTION_GUARD = Lock()


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
            raise ChildRunStoreError(
                f"workspace does not exist or is inaccessible: {requested}"
            ) from None
        if not resolved.is_dir():
            raise ChildRunStoreError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".coquo" / "child-runs" / self.workspace_fingerprint
        self._uuid_factory = uuid_factory
        self._clock = clock

    def create(
        self,
        objective: str,
        *,
        parent_session: str = "latest",
        delegation: ChildRunDelegated | None = None,
    ) -> ChildRunInfo:
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
        records: list[ChildRunRecord] = [header]
        if delegation is not None:
            records.append(
                ChildRunDelegated(
                    sequence=1,
                    child_run_id=child_run_id,
                    parent_session_id=parent_session_id,
                    parent_context_id=delegation.parent_context_id,
                    parent_tool_use_id=delegation.parent_tool_use_id,
                    decision_record_sequence=delegation.decision_record_sequence,
                    decision_sha256=delegation.decision_sha256,
                    depth=delegation.depth,
                    source=delegation.source,
                    delegated_at=delegation.delegated_at,
                )
            )
        state = self._replay(self.root / f"{child_run_id}.jsonl", records)
        payload = b"".join(encode_child_run_record(record) for record in records)
        self._ensure_root()
        path = self.root / f"{child_run_id}.jsonl"
        _install_child_run_transcript(path, payload)
        return _child_run_info(path, state)

    def inspect(self, child_run_id: str) -> ChildRunInfo:
        canonical = _store_child_run_id(child_run_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        return _child_run_info(path, self._load_state(path))

    def replay_state(self, child_run_id: str) -> ChildRunReplayState:
        """Return one strict Child replay state for Host-owned projections."""
        canonical = _store_child_run_id(child_run_id)
        self._validate_existing_root()
        return self._load_state(self.root / f"{canonical}.jsonl")

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
        return tuple(
            sorted(runs, key=lambda item: (item.created_at, item.child_run_id), reverse=True)
        )

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

    def prepare(
        self,
        child_run_id: str,
        *,
        runtime_spec,
        session_store: SessionStore,
        binding,
    ) -> ChildRunInfo:
        """Durably admit one Child and bind one detached Session without latest mutation."""
        from coquo.child_runtime import ChildRuntimeSpec
        from coquo.session_store import SessionCreationRequest

        if type(runtime_spec) is not ChildRuntimeSpec:
            raise ChildRunStoreError("Child runtime specification is invalid")
        if type(binding) is not BindingSnapshot:
            raise ChildRunStoreError("Child Session binding is invalid")
        with self.open(child_run_id) as writer:
            state = writer.state
            if state.header.child_run_id != runtime_spec.child_run_id:
                raise ChildRunStoreError("Child Run runtime specification ID does not match header")
            if state.header.parent_session_id != runtime_spec.parent_session_id:
                raise ChildRunStoreError(
                    "Child Run runtime specification owner does not match header"
                )
            if state.status is ChildRunStatus.QUEUED:
                admitted = ChildRunAdmitted(
                    sequence=len(state.records),
                    child_run_id=runtime_spec.child_run_id,
                    parent_session_id=runtime_spec.parent_session_id,
                    child_session_id=runtime_spec.child_session_id,
                    permission_mode=runtime_spec.permission_mode,
                    approval_mode=runtime_spec.approval_mode,
                    provider_binding=dict(runtime_spec.provider_binding),
                    tool_registry_id=runtime_spec.tool_registry_id,
                    tool_registry_generation=runtime_spec.tool_registry_generation,
                    tool_set_id=runtime_spec.tool_set_id,
                    tool_names=runtime_spec.tool_names,
                    role_contract_version=runtime_spec.role_contract_version,
                    role_prompt_fingerprint=runtime_spec.role_prompt_fingerprint,
                    max_provider_invocations=runtime_spec.max_provider_invocations,
                    max_tool_requests=runtime_spec.max_tool_requests,
                    max_output_tokens=runtime_spec.max_output_tokens,
                    deadline_seconds=runtime_spec.deadline_seconds,
                    admitted_at=self._clock(),
                )
                writer._append_transition(admitted)
            elif state.status is ChildRunStatus.ADMITTED:
                admitted = state.admitted
                assert admitted is not None
                if (
                    admitted.child_session_id != runtime_spec.child_session_id
                    or admitted.tool_set_id != runtime_spec.tool_set_id
                    or admitted.provider_binding != dict(runtime_spec.provider_binding)
                ):
                    raise ChildRunStoreError(
                        "Child Run admission does not match the requested runtime"
                    )
            elif state.status is ChildRunStatus.READY:
                admitted = state.admitted
                bound = state.session_bound
                assert admitted is not None and bound is not None
                if (
                    admitted.child_session_id != runtime_spec.child_session_id
                    or admitted.tool_set_id != runtime_spec.tool_set_id
                    or admitted.provider_binding != dict(runtime_spec.provider_binding)
                    or bound.child_session_id != runtime_spec.child_session_id
                ):
                    raise ChildRunStoreError(
                        "Child Run binding does not match the requested runtime"
                    )
                return writer.info
            else:
                raise ChildRunStoreError("Child Run is not queued or partially admitted")
            child_writer = None
            try:
                if writer.state.session_bound is not None:
                    bound = writer.state.session_bound
                    if bound.child_session_id != runtime_spec.child_session_id:
                        raise ChildRunStoreError("Child Session binding does not match admission")
                    return writer.info
                try:
                    child_info = session_store.inspect(runtime_spec.child_session_id)
                except SessionStoreError:
                    child_path = session_store.root / f"{runtime_spec.child_session_id}.jsonl"
                    try:
                        child_path.lstat()
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        raise ChildRunStoreError(
                            "existing Child Session is inaccessible"
                        ) from error
                    else:
                        raise ChildRunStoreError(
                            "existing Child Session is unsafe or invalid"
                        ) from None
                    child_writer = session_store.create(
                        binding,
                        creation=SessionCreationRequest(
                            session_id=runtime_spec.child_session_id,
                            publish_latest=False,
                            name=f"Child {runtime_spec.child_run_id[:8]}",
                        ),
                    )
                    child_info = child_writer.info
                    child_writer.release()
                    child_writer = None
                if child_info.binding != binding:
                    raise ChildRunStoreError(
                        "existing Child Session binding does not match admission"
                    )
                bound = ChildSessionBound(
                    sequence=len(writer.state.records),
                    child_run_id=runtime_spec.child_run_id,
                    child_session_id=runtime_spec.child_session_id,
                    session_header_sequence=0,
                    session_path=str(child_info.path),
                    bound_at=self._clock(),
                )
                writer._append_transition(bound)
            except BaseException as error:
                if child_writer is not None:
                    child_writer.release()
                if isinstance(error, (ChildRunAppendCommitError, ChildRunCreateCommitError)):
                    raise
                failure = ChildRunPreparationFailed(
                    sequence=len(writer.state.records),
                    child_run_id=runtime_spec.child_run_id,
                    phase="child_session",
                    result_code="session_create_failed",
                    message=str(error)[:1024] or "Child Session creation failed",
                    failed_at=self._clock(),
                )
                writer._append_transition(failure)
                raise ChildRunStoreError(str(error)) from None
            return writer.info

    def acquire_execution(self, child_run_id: str):
        """Acquire a persistent v2 OS lifetime lease for one Child Run."""
        info = self.inspect(child_run_id)
        if info.status is not ChildRunStatus.READY:
            raise ChildRunStoreError("Child Run is not ready for execution")
        return self._acquire_v2_lease(info.child_run_id)

    def acquire_recovery_lease(self, child_run_id: str):
        """Acquire a v2 lease only to prove a nonterminal executor is abandoned."""
        info = self.inspect(child_run_id)
        if info.status not in {ChildRunStatus.RUNNING, ChildRunStatus.CANCELLING}:
            raise ChildRunStoreError("Child Run is not recoverable")
        return self._acquire_v2_lease(info.child_run_id)

    def _acquire_v2_lease(self, child_run_id: str):
        info = self.inspect(child_run_id)
        legacy_path = self.root / f"{info.child_run_id}.execution.lock"
        if legacy_path.exists() or legacy_path.is_symlink():
            raise ChildRunExecutionLeaseError(f"legacy_lease_ambiguous: {legacy_path}")
        path = self.root / f"{info.child_run_id}.execution-v2.lock"
        self._ensure_root()
        descriptor: int | None = None
        lease_created = False
        key = str(path)
        with _ACTIVE_EXECUTION_GUARD:
            if key in _ACTIVE_EXECUTION_LEASES:
                raise ChildRunExecutionLeaseError("Child Run already has an active execution lease")
            _ACTIVE_EXECUTION_LEASES.add(key)
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            descriptor_stat = os.fstat(descriptor)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise ChildRunStoreError("Child Run execution lease must be a regular file")
            if stat.S_IMODE(descriptor_stat.st_mode) != 0o600:
                raise ChildRunStoreError("Child Run execution lease mode is invalid")
            if descriptor_stat.st_size == 0:
                os.write(descriptor, b"child-execution-lease-v2\n")
                os.fsync(descriptor)
            else:
                os.lseek(descriptor, 0, os.SEEK_SET)
                header = os.read(descriptor, 128)
                if header != b"child-execution-lease-v2\n":
                    raise ChildRunExecutionLeaseError(
                        "Child Run v2 execution lease header is invalid"
                    )
            _lock_execution_descriptor(descriptor)
            lease = ChildRunExecutionLease(path, descriptor, key)
            lease_created = True
            return lease
        except ChildRunExecutionLeaseError:
            raise
        except FileExistsError:
            raise ChildRunExecutionLeaseError(
                "Child Run already has an active execution lease"
            ) from None
        except OSError as error:
            raise ChildRunStoreError("could not acquire Child Run execution lease") from error
        finally:
            if not lease_created:
                if descriptor is not None:
                    os.close(descriptor)
                with _ACTIVE_EXECUTION_GUARD:
                    _ACTIVE_EXECUTION_LEASES.discard(key)

    def request_cancel(
        self,
        child_run_id: str,
        *,
        reason: str,
        source: str = "host",
    ) -> ChildRunInfo:
        canonical = _store_child_run_id(child_run_id)
        with self.open(canonical) as writer:
            state = writer.state
            if state.status is ChildRunStatus.QUEUED:
                writer.cancel(reason)
                return writer.info
            if state.cancel_requested is not None:
                if state.cancel_requested.reason != canonical_child_run_reason(reason):
                    raise ChildRunStoreError(
                        "Child Run cancellation reason conflicts with existing request"
                    )
                return writer.info
            if state.status in {
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.CANCELLED,
                ChildRunStatus.INTERRUPTED,
            }:
                return writer.info
            execution_id = state.started.execution_id if state.started is not None else None
            writer.request_cancel(reason=reason, source=source, execution_id=execution_id)
            if state.started is None:
                writer.finish_cancelled(result_code="cancelled_before_start")
            return writer.info

    def finish_cancelled(
        self, child_run_id: str, *, result_code: str = "cancelled"
    ) -> ChildRunInfo:
        with self.open(child_run_id) as writer:
            writer.finish_cancelled(result_code=result_code)
            return writer.info

    def finish_interrupted(
        self,
        child_run_id: str,
        *,
        result_code: str = "execution_abandoned",
    ) -> ChildRunInfo:
        with self.open(child_run_id) as writer:
            writer.finish_interrupted(result_code=result_code)
            return writer.info

    def publish_handoff(self, child_run_id: str, handoff: ChildRunHandoffPublished) -> ChildRunInfo:
        with self.open(child_run_id) as writer:
            writer.publish_handoff(handoff)
            return writer.info

    def start_execution(
        self, child_run_id: str, *, child_session_id: str, execution_id: str
    ) -> ChildRunInfo:
        with self.open(child_run_id) as writer:
            writer.start(child_session_id=child_session_id, execution_id=execution_id)
            return writer.info

    def finish_completed(
        self,
        child_run_id: str,
        *,
        execution_id: str,
        session_record_sequence: int,
        assistant_text_sha256: str,
    ) -> ChildRunInfo:
        with self.open(child_run_id) as writer:
            writer.complete(
                execution_id=execution_id,
                session_record_sequence=session_record_sequence,
                assistant_text_sha256=assistant_text_sha256,
            )
            return writer.info

    def finish_failed(
        self,
        child_run_id: str,
        *,
        execution_id: str | None,
        phase: str,
        result_code: str,
        message: str,
    ) -> ChildRunInfo:
        with self.open(child_run_id) as writer:
            writer.fail(
                execution_id=execution_id,
                phase=phase,
                result_code=result_code,
                message=message,
            )
            return writer.info

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
            raise ChildRunStoreError(
                "Child Run transcript does not end at a durable record boundary"
            )
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
        if (
            state.header.workspace != str(self.workspace)
            or state.header.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ChildRunStoreError("Child Run transcript belongs to another workspace")
        return state


class ChildRunWriter:
    """Exclusive append-only writer for one queued Child Run."""

    def __init__(
        self,
        store: ChildRunStore,
        path: Path,
        descriptor: int,
        state: ChildRunReplayState,
        key: str,
    ) -> None:
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
            sequence=len(self._state.records),
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

    def start(self, *, child_session_id: str, execution_id: str) -> ChildRunStarted:
        if self._closed or self._poisoned:
            raise ChildRunStoreError("Child Run writer is not writable")
        if self._state.status is not ChildRunStatus.READY:
            raise ChildRunStoreError("Child Run is not ready for execution")
        record = ChildRunStarted(
            sequence=len(self._state.records),
            child_run_id=self._state.header.child_run_id,
            child_session_id=child_session_id,
            execution_id=execution_id,
            started_at=self._store._clock(),
        )
        self._append_transition(record)
        return record

    def complete(
        self,
        *,
        execution_id: str,
        session_record_sequence: int,
        assistant_text_sha256: str,
    ) -> ChildRunCompleted:
        if self._state.status not in {ChildRunStatus.RUNNING, ChildRunStatus.CANCELLING}:
            raise ChildRunStoreError("Child Run is not running")
        record = ChildRunCompleted(
            sequence=len(self._state.records),
            child_run_id=self._state.header.child_run_id,
            execution_id=execution_id,
            session_record_sequence=session_record_sequence,
            assistant_text_sha256=assistant_text_sha256,
            completed_at=self._store._clock(),
        )
        self._append_transition(record)
        return record

    def fail(
        self,
        *,
        execution_id: str | None,
        phase: str,
        result_code: str,
        message: str,
    ) -> ChildRunFailed:
        if self._state.status not in {
            ChildRunStatus.READY,
            ChildRunStatus.RUNNING,
            ChildRunStatus.CANCELLING,
        }:
            raise ChildRunStoreError("Child Run is already terminal")
        record = ChildRunFailed(
            sequence=len(self._state.records),
            child_run_id=self._state.header.child_run_id,
            execution_id=execution_id,
            phase=phase,
            result_code=result_code,
            message=message,
            failed_at=self._store._clock(),
        )
        self._append_transition(record)
        return record

    def request_cancel(
        self,
        *,
        reason: str,
        source: str,
        execution_id: str | None,
    ) -> ChildRunCancelRequested:
        if self._state.cancel_requested is not None:
            existing = self._state.cancel_requested
            if existing.reason != canonical_child_run_reason(reason):
                raise ChildRunStoreError(
                    "Child Run cancellation reason conflicts with existing request"
                )
            return existing
        if self._state.status in {
            ChildRunStatus.COMPLETED,
            ChildRunStatus.FAILED,
            ChildRunStatus.CANCELLED,
            ChildRunStatus.INTERRUPTED,
        }:
            raise ChildRunStoreError("Child Run is already terminal")
        record = ChildRunCancelRequested(
            sequence=len(self._state.records),
            child_run_id=self._state.header.child_run_id,
            execution_id=execution_id,
            reason=canonical_child_run_reason(reason),
            source=source,
            requested_at=self._store._clock(),
        )
        self._append_transition(record)
        return record

    def finish_cancelled(self, *, result_code: str) -> ChildRunCancelledTerminal:
        if self._state.cancelled_terminal is not None:
            return self._state.cancelled_terminal
        request = self._state.cancel_requested
        if request is None:
            raise ChildRunStoreError("Child Run has no durable cancellation request")
        record = ChildRunCancelledTerminal(
            sequence=len(self._state.records),
            child_run_id=self._state.header.child_run_id,
            execution_id=request.execution_id,
            cancel_request_sequence=request.sequence,
            result_code=result_code,
            observed_at=self._store._clock(),
        )
        self._append_transition(record)
        return record

    def finish_interrupted(self, *, result_code: str) -> ChildRunInterrupted:
        if self._state.interrupted is not None:
            return self._state.interrupted
        started = self._state.started
        if started is None:
            raise ChildRunStoreError("Child Run has no started execution")
        record = ChildRunInterrupted(
            sequence=len(self._state.records),
            child_run_id=self._state.header.child_run_id,
            execution_id=started.execution_id,
            last_durable_state=(
                ChildRunStatus.CANCELLING.value
                if self._state.cancel_requested is not None
                else ChildRunStatus.RUNNING.value
            ),
            lock_protocol="v2",
            result_code=result_code,
            interrupted_at=self._store._clock(),
        )
        self._append_transition(record)
        return record

    def publish_handoff(self, handoff: ChildRunHandoffPublished) -> ChildRunHandoffPublished:
        if not isinstance(handoff, ChildRunHandoffPublished):
            raise ChildRunStoreError("Child Run handoff is invalid")
        if handoff.child_run_id != self._state.header.child_run_id:
            raise ChildRunStoreError("Child Run handoff ID does not match transcript")
        if self._state.handoff is not None:
            if self._state.handoff == handoff:
                return self._state.handoff
            raise ChildRunStoreError("Child Run handoff is already published differently")
        if self._state.status not in {
            ChildRunStatus.COMPLETED,
            ChildRunStatus.FAILED,
            ChildRunStatus.CANCELLED,
            ChildRunStatus.INTERRUPTED,
        }:
            raise ChildRunStoreError("Child Run is not terminal")
        self._append_transition(handoff)
        return handoff

    def _append_transition(self, record: ChildRunRecord) -> ChildRunRecord:
        if self._closed:
            raise ChildRunStoreError("Child Run writer is closed")
        if self._poisoned:
            raise ChildRunStoreError("Child Run writer durability is uncertain")
        candidate = list(self._state.records) + [record]
        state = self._store._replay(self._path, candidate)
        try:
            _append_record(self._descriptor, self._path, record)
        except ChildRunAppendCommitError:
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
    admitted = state.admitted
    preparation_failed = state.preparation_failed
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
        child_session_id=(
            state.session_bound.child_session_id
            if state.session_bound is not None
            else (admitted.child_session_id if admitted is not None else None)
        ),
        tool_set_id=admitted.tool_set_id if admitted is not None else None,
        tool_names=admitted.tool_names if admitted is not None else (),
        provider_binding=dict(admitted.provider_binding) if admitted is not None else None,
        preparation_failure=(
            f"{preparation_failed.result_code}: {preparation_failed.message}"
            if preparation_failed is not None
            else None
        ),
        execution_id=(
            state.started.execution_id
            if state.started is not None
            else (
                state.completed.execution_id
                if state.completed is not None
                else state.failed.execution_id
                if state.failed is not None
                else None
            )
        ),
        assistant_text_sha256=state.completed.assistant_text_sha256
        if state.completed is not None
        else None,
        session_record_sequence=state.completed.session_record_sequence
        if state.completed is not None
        else None,
        failure_result_code=state.failed.result_code if state.failed is not None else None,
        cancellation_request_reason=(
            state.cancel_requested.reason if state.cancel_requested is not None else None
        ),
        cancellation_request_source=(
            state.cancel_requested.source if state.cancel_requested is not None else None
        ),
        cancellation_request_sequence=(
            state.cancel_requested.sequence if state.cancel_requested is not None else None
        ),
        interrupted_result_code=(
            state.interrupted.result_code if state.interrupted is not None else None
        ),
        handoff=state.handoff,
        delegated=state.delegated,
    )


class ChildRunExecutionLease:
    """Lifetime lock distinct from the append writer lock."""

    def __init__(self, path: Path, descriptor: int, key: str) -> None:
        self.path = path
        self._descriptor = descriptor
        self._key = key
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _unlock_execution_descriptor(self._descriptor)
        finally:
            try:
                os.close(self._descriptor)
            finally:
                with _ACTIVE_EXECUTION_GUARD:
                    _ACTIVE_EXECUTION_LEASES.discard(self._key)

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


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


def _lock_execution_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise ChildRunExecutionLeaseError(
            "Child Run already has an active execution lease"
        ) from None


def _unlock_execution_descriptor(descriptor: int) -> None:
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
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise ChildRunStoreError("Child Run transcript changed while it was being read")
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
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
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=".child-run.", suffix=".tmp"
        )
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
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        os.fsync(descriptor)
    except OSError:
        raise ChildRunStoreError(
            f"could not confirm Child Run directory durability: {path}"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
