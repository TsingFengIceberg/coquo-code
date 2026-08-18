from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from coquo.background_runtime import (
    BackgroundRuntimeError,
    BackgroundWorkerAlreadyRunning,
    BackgroundQueueStore,
    PersistentChildRunRuntime,
    PersistentChildWorker,
)
from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunStore
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore


def prepared_child(tmp_path: Path):
    sessions = SessionStore(tmp_path)
    parent_writer = sessions.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent_writer.release()
    parent = sessions.inspect(parent_id)
    store = ChildRunStore(tmp_path)
    info = store.create("background objective", parent_session=parent_id)
    from coquo.child_runtime import build_child_runtime_spec_from_binding

    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="82345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    store.prepare(
        info.child_run_id, runtime_spec=spec, session_store=sessions, binding=parent.binding
    )
    return store, info.child_run_id


def test_background_queue_is_durable_and_deduplicated(tmp_path: Path) -> None:
    child_store, child_id = prepared_child(tmp_path)
    queue = BackgroundQueueStore(tmp_path)
    item = queue.enqueue(child_id)
    assert BackgroundQueueStore(tmp_path).snapshot() == (item,)
    with pytest.raises(BackgroundRuntimeError, match="already queued"):
        queue.enqueue(child_id)
    claimed = queue.claim("92345678-1234-4234-9234-123456789abc")
    assert claimed is not None
    assert claimed.state == "claimed"
    assert queue.inspect(item.submission_id).lease_id == claimed.lease_id
    queue.requeue(claimed, reason="worker exited before start")
    assert queue.inspect(item.submission_id).state == "queued"
    assert child_store.inspect(child_id).status is ChildRunStatus.READY


def test_background_worker_completes_child_and_records_terminal_queue_event(tmp_path: Path) -> None:
    child_store, child_id = prepared_child(tmp_path)

    class FakeExecutor:
        def run(self, run_id: str):
            child_store.start_execution(
                run_id,
                child_session_id=child_store.inspect(run_id).child_session_id or "",
                execution_id="92345678-1234-4234-9234-123456789abc",
            )
            child_store.finish_completed(
                run_id,
                execution_id="92345678-1234-4234-9234-123456789abc",
                session_record_sequence=1,
                assistant_text_sha256=sha256(b"done").hexdigest(),
            )
            return "done"

    class Launched:
        pid = 1234

    runtime = PersistentChildRunRuntime(tmp_path, worker_launcher=lambda _command: Launched())
    submission = runtime.start(child_id)
    assert submission.worker_started is True
    result = PersistentChildWorker(
        tmp_path,
        executor_factory=lambda _run_id: FakeExecutor(),
        idle_seconds=0,
    ).run(max_items=1)
    assert result.outcome == "completed"
    assert result.processed_child_run_ids == (child_id,)
    assert child_store.inspect(child_id).status is ChildRunStatus.COMPLETED
    item = BackgroundQueueStore(tmp_path).inspect(submission.item.submission_id)
    assert item.state == "terminal"
    assert item.terminal_child_status == "completed"


def test_worker_reconciles_ready_orphan_without_reexecuting_it(tmp_path: Path) -> None:
    child_store, child_id = prepared_child(tmp_path)
    queue = BackgroundQueueStore(tmp_path)
    item = queue.enqueue(child_id)
    claimed = queue.claim("92345678-1234-4234-9234-123456789abc")
    assert claimed is not None
    result = PersistentChildWorker(tmp_path).recover_orphans()
    assert result.recovered_child_run_ids == (child_id,)
    assert queue.inspect(item.submission_id).state == "queued"
    assert child_store.inspect(child_id).status is ChildRunStatus.READY


def test_worker_reconciles_running_orphan_as_interrupted(tmp_path: Path) -> None:
    child_store, child_id = prepared_child(tmp_path)
    info = child_store.inspect(child_id)
    queue = BackgroundQueueStore(tmp_path)
    item = queue.enqueue(child_id)
    claimed = queue.claim("92345678-1234-4234-9234-123456789abc")
    assert claimed is not None
    execution_lease = child_store.acquire_execution(child_id)
    child_store.start_execution(
        child_id,
        child_session_id=info.child_session_id or "",
        execution_id="92345678-1234-4234-9234-123456789abc",
    )
    execution_lease.close()
    result = PersistentChildWorker(tmp_path).recover_orphans()
    assert result.recovered_child_run_ids == (child_id,)
    assert child_store.inspect(child_id).status is ChildRunStatus.INTERRUPTED
    terminal = queue.inspect(item.submission_id)
    assert terminal.state == "terminal"
    assert terminal.terminal_child_status == "interrupted"


def test_worker_lease_is_exclusive(tmp_path: Path) -> None:
    worker = PersistentChildWorker(tmp_path)
    first = worker.store.acquire_worker()
    try:
        with pytest.raises(BackgroundWorkerAlreadyRunning):
            worker.store.acquire_worker()
    finally:
        first.close()
