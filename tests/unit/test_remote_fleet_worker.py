from __future__ import annotations

from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

from coquo.remote_fleet import FleetAssignment, FleetTask, FleetTaskStatus
from coquo.remote_fleet_worker import RemoteFleetWorkerLoop
from coquo.remote_workers import RemoteLease, RemoteResult


def _assignment() -> FleetAssignment:
    task_id = str(uuid4())
    lease = RemoteLease(str(uuid4()), task_id, "worker-a", 4_000_000_000.0)
    task = FleetTask(
        task_id,
        "tenant-a",
        "workspace-v1",
        "inspect",
        "read-only",
        (),
        FleetTaskStatus.ASSIGNED,
        "worker-a",
        lease.lease_id,
        lease.expires_at,
    )
    return FleetAssignment(task, lease)


class FakeTransport:
    def __init__(self, assignments: list[FleetAssignment]) -> None:
        self.assignments = list(assignments)
        self.heartbeats = 0
        self.completed: list[RemoteResult] = []

    def dispatch(self, _worker_id, *, capabilities, lease_seconds):
        del capabilities, lease_seconds
        return self.assignments.pop(0) if self.assignments else None

    def heartbeat_lease(self, lease, *, lease_seconds):
        del lease_seconds
        self.heartbeats += 1
        return FleetAssignment(
            replace(
                _assignment().task,
                task_id=lease.task_id,
                lease_id=lease.lease_id,
                lease_expires_at=lease.expires_at,
            ),
            lease,
        )

    def complete(self, lease, result):
        assert result.task_id == lease.task_id
        self.completed.append(result)
        return replace(_assignment().task, task_id=result.task_id, status=FleetTaskStatus.COMPLETED)


def test_worker_loop_executes_and_transfers_bounded_result() -> None:
    transport = FakeTransport([_assignment()])
    report = RemoteFleetWorkerLoop(
        transport,
        worker_id="worker-a",
        handler=lambda task: f"handled: {task.objective}",
        poll_interval_seconds=0,
    ).run(max_tasks=1)

    assert report.claimed == 1
    assert report.completed == 1
    assert report.failed == report.cancelled == report.unknown == 0
    assert transport.completed[0].result_payload == "handled: inspect"
    assert transport.completed[0].result_sha256 is not None


def test_worker_loop_reports_handler_failure_without_retry() -> None:
    transport = FakeTransport([_assignment()])

    def fail(_task):
        raise RuntimeError("handler failed")

    report = RemoteFleetWorkerLoop(transport, worker_id="worker-a", handler=fail).run(max_tasks=1)

    assert report.failed == 1
    assert len(transport.completed) == 1
    assert transport.completed[0].status == "failed"
    assert "handler failed" in (transport.completed[0].diagnostic or "")


def test_worker_loop_stop_is_cooperative_and_does_not_claim_new_work() -> None:
    transport = FakeTransport([_assignment()])
    stopped = Event()

    def stop(_task):
        stopped.set()
        loop.request_stop()
        return "done"

    loop = RemoteFleetWorkerLoop(transport, worker_id="worker-a", handler=stop)
    report = loop.run(max_tasks=2, max_idle_polls=0)

    assert stopped.is_set()
    assert report.claimed == 1
    assert len(transport.completed) == 1


def test_worker_loop_rejects_oversized_handler_result() -> None:
    transport = FakeTransport([_assignment()])
    report = RemoteFleetWorkerLoop(
        transport,
        worker_id="worker-a",
        handler=lambda _task: "x" * (64 * 1024 + 1),
    ).run(max_tasks=1)

    assert report.failed == 1
    assert transport.completed[0].result_payload is None
    assert "size limit" in (transport.completed[0].diagnostic or "")


def test_worker_loop_validates_bounds() -> None:
    with pytest.raises(ValueError, match="heartbeat"):
        RemoteFleetWorkerLoop(
            FakeTransport([]),
            worker_id="worker-a",
            handler=lambda _task: "ok",
            lease_seconds=1,
            heartbeat_interval_seconds=1,
        )
