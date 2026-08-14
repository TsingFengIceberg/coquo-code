from __future__ import annotations

from threading import Barrier, Event, Lock
from time import monotonic

import pytest

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunStore
from coquo.child_supervisor import ChildRunSupervisor, ChildSupervisorError
from coquo.cli.presentation import render_child_supervisor_notification
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.session import ProjectSession


class BlockingExecutor:
    def __init__(self, barrier: Barrier, release: Event, calls: list[str], lock: Lock) -> None:
        self.barrier = barrier
        self.release = release
        self.calls = calls
        self.lock = lock

    def run(self, child_run_id: str):
        with self.lock:
            self.calls.append(child_run_id)
        self.barrier.wait(timeout=2)
        self.release.wait(timeout=2)


def prepared_runs(tmp_path, count: int):
    sessions = SessionStore(tmp_path)
    parent_writer = sessions.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = sessions.inspect(parent_id)
    parent_writer.release()
    store = ChildRunStore(tmp_path)
    runs = []
    for index in range(count):
        info = store.create(f"Inspect {index}", parent_session=parent_id)
        from coquo.child_runtime import build_child_runtime_spec_from_binding

        spec = build_child_runtime_spec_from_binding(
            child_run_id=info.child_run_id,
            parent_session_id=parent_id,
            child_session_id=f"{index + 1:08d}-1234-4234-9234-123456789abc",
            objective=info.objective,
            binding=parent.binding,
        )
        store.prepare(
            info.child_run_id, runtime_spec=spec, session_store=sessions, binding=parent.binding
        )
        runs.append(info.child_run_id)
    return store, runs


def test_supervisor_starts_four_workers_and_queues_fifth(tmp_path) -> None:
    store, ids = prepared_runs(tmp_path, 5)
    barrier = Barrier(4)
    release = Event()
    calls: list[str] = []
    lock = Lock()

    def executor(_child_id: str):
        return BlockingExecutor(barrier, release, calls, lock)

    supervisor = ChildRunSupervisor(tmp_path, executor_factory=executor)
    for child_id in ids[:4]:
        supervisor.submit(child_id)
    deadline = monotonic() + 2
    while monotonic() < deadline and len(calls) < 4:
        pass
    assert len(calls) == 4
    supervisor.submit(ids[4])
    assert supervisor.queued_count == 1
    with pytest.raises(ChildSupervisorError, match="already submitted"):
        supervisor.submit(ids[4])
    release.set()
    deadline = monotonic() + 2
    while monotonic() < deadline and supervisor.queued_count:
        pass
    supervisor.close()
    assert store.inspect(ids[0]).status in {ChildRunStatus.READY, ChildRunStatus.CANCELLED}


def test_supervisor_rejects_unprepared_run_and_reports_failure(tmp_path) -> None:
    sessions = SessionStore(tmp_path)
    parent = sessions.create(BindingSnapshot.fake())
    parent_id = parent.session_id
    parent.release()
    info = ChildRunStore(tmp_path).create("not ready", parent_session=parent_id)
    supervisor = ChildRunSupervisor(tmp_path)
    with pytest.raises(ChildSupervisorError, match="not ready"):
        supervisor.submit(info.child_run_id)
    supervisor.close()


class FailingExecutor:
    def __init__(self, calls: list[str], lock: Lock) -> None:
        self.calls = calls
        self.lock = lock

    def run(self, child_run_id: str):
        with self.lock:
            self.calls.append(child_run_id)
        raise RuntimeError("planned worker failure")


def test_worker_failure_isolated_and_notification_is_bounded(tmp_path) -> None:
    store, ids = prepared_runs(tmp_path, 2)
    calls: list[str] = []
    lock = Lock()

    supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda _child_id: FailingExecutor(calls, lock),
        worker_count=1,
    )
    supervisor.submit(ids[0])
    supervisor.submit(ids[1])
    deadline = monotonic() + 2
    while monotonic() < deadline and len(calls) < 2:
        pass
    supervisor.close()

    assert calls == ids
    notifications = supervisor.drain_notifications()
    assert [item.child_run_id for item in notifications] == ids
    assert all(item.status is ChildRunStatus.READY for item in notifications)
    assert all(item.message == "planned worker failure" for item in notifications)
    assert all(
        store.inspect(child_id).status in {ChildRunStatus.READY, ChildRunStatus.CANCELLED}
        for child_id in ids
    )


def test_parent_session_can_commit_while_child_worker_is_active(tmp_path) -> None:
    parent = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    info = parent.create_child_run("inspect in background")
    parent.prepare_child_run(info.child_run_id)
    barrier = Barrier(1)
    release = Event()

    class ParentOverlapExecutor:
        def run(self, _child_run_id: str):
            barrier.wait(timeout=2)
            release.wait(timeout=2)

    supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda _child_id: ParentOverlapExecutor(),
        worker_count=1,
        parent_session_id=parent.session_id,
    )
    parent._child_supervisor = supervisor
    parent.start_child_run(info.child_run_id)
    barrier.wait(timeout=2)
    assert parent.prompt("parent remains usable") == "Fake response: parent remains usable"
    assert parent.history[-1].text == "Fake response: parent remains usable"
    release.set()
    parent.close()


def test_supervisor_rejects_child_owned_by_another_parent(tmp_path) -> None:
    store, ids = prepared_runs(tmp_path, 1)
    sessions = SessionStore(tmp_path)
    other = sessions.create(BindingSnapshot.fake())
    other_id = other.session_id
    other.release()
    supervisor = ChildRunSupervisor(tmp_path, parent_session_id=other_id)
    with pytest.raises(ChildSupervisorError, match="another parent"):
        supervisor.submit(ids[0])
    supervisor.close()


def test_supervisor_rejects_queue_overflow(tmp_path) -> None:
    _store, ids = prepared_runs(tmp_path, 3)
    started = Event()
    release = Event()

    class QueueingExecutor:
        def run(self, _child_run_id: str):
            started.set()
            release.wait(timeout=2)

    supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda _child_id: QueueingExecutor(),
        worker_count=1,
        queue_capacity=1,
    )
    supervisor.submit(ids[0])
    assert started.wait(timeout=2)
    supervisor.submit(ids[1])
    with pytest.raises(ChildSupervisorError, match="queue is full"):
        supervisor.submit(ids[2])
    release.set()
    supervisor.close()


def test_supervisor_wait_timeout_and_cancel_active_worker(tmp_path) -> None:
    _store, ids = prepared_runs(tmp_path, 1)
    started = Event()
    release = Event()

    class CancellableExecutor:
        def run(self, _child_run_id: str, *, cancellation=None):
            started.set()
            while not release.is_set():
                if cancellation is not None and cancellation.requested:
                    return
                release.wait(0.01)

    supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda _child_id: CancellableExecutor(),
        worker_count=1,
    )
    supervisor.submit(ids[0])
    assert started.wait(timeout=2)
    assert supervisor.wait(ids[0], 0).status is ChildRunStatus.READY
    cancelling = supervisor.cancel(ids[0], "stop")
    assert cancelling.status is ChildRunStatus.CANCELLED
    release.set()
    assert supervisor.wait(ids[0], 2).status is ChildRunStatus.CANCELLED
    supervisor.close()


def test_supervisor_notification_rendering_is_bounded() -> None:
    from coquo.child_supervisor import ChildSupervisorNotification

    notification = ChildSupervisorNotification(
        "12345678-1234-4234-9234-123456789abc",
        ChildRunStatus.COMPLETED,
        "done\nwith controls",
    )
    rendered = render_child_supervisor_notification(notification)
    assert rendered == (
        "Child Run 12345678-1234-4234-9234-123456789abc: completed: done\\nwith controls"
    )
