"""Explicit bounded Worker loop for the RemoteWorkerFleet control plane."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread

from coquo.remote_fleet import FleetAssignment, FleetTask, RemoteFleetClient
from coquo.remote_workers import MAX_REMOTE_RESULT_PAYLOAD_BYTES, RemoteResult


@dataclass(frozen=True)
class RemoteFleetWorkerReport:
    """Content-free facts about one bounded worker-loop invocation."""

    claimed: int
    completed: int
    failed: int
    cancelled: int
    unknown: int
    idle_polls: int


class RemoteFleetWorkerLoop:
    """Poll, execute, heartbeat, and complete explicitly assigned Fleet work.

    The Fleet service remains a scheduler.  This loop is the separate worker
    process boundary: callers inject a handler that invokes the existing
    Host/Child runtime for the assigned task.  No retry or daemon behavior is
    implicit; ``run`` returns after the requested bounded work or idle polls.
    """

    def __init__(
        self,
        transport: RemoteFleetClient,
        *,
        worker_id: str,
        handler: Callable[[FleetTask], str | bytes],
        lease_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if not isinstance(transport, RemoteFleetClient) and not all(
            callable(getattr(transport, name, None))
            for name in ("dispatch", "heartbeat_lease", "complete")
        ):
            raise ValueError("remote fleet worker transport is invalid")
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("remote fleet worker ID is required")
        if not callable(handler):
            raise ValueError("remote fleet worker handler is required")
        if isinstance(lease_seconds, bool) or not 0 < lease_seconds <= 300:
            raise ValueError("remote fleet worker lease duration is invalid")
        if isinstance(poll_interval_seconds, bool) or not 0 <= poll_interval_seconds <= 30:
            raise ValueError("remote fleet worker poll interval is invalid")
        heartbeat = (
            lease_seconds / 3 if heartbeat_interval_seconds is None else heartbeat_interval_seconds
        )
        if isinstance(heartbeat, bool) or not 0 < heartbeat < lease_seconds:
            raise ValueError("remote fleet worker heartbeat interval is invalid")
        self.transport = transport
        self.worker_id = worker_id
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.heartbeat_interval_seconds = heartbeat
        self._stop = Event()

    def request_stop(self) -> None:
        """Ask a currently idle or executing loop to stop at its next boundary."""
        self._stop.set()

    def run(self, *, max_tasks: int = 1, max_idle_polls: int = 1) -> RemoteFleetWorkerReport:
        if type(max_tasks) is not int or not 1 <= max_tasks <= 256:
            raise ValueError("remote fleet worker task limit is invalid")
        if type(max_idle_polls) is not int or not 0 <= max_idle_polls <= 256:
            raise ValueError("remote fleet worker idle-poll limit is invalid")
        claimed = completed = failed = cancelled = unknown = idle_polls = 0
        while claimed < max_tasks and not self._stop.is_set():
            assignment = self.transport.dispatch(
                self.worker_id,
                capabilities=(),
                lease_seconds=self.lease_seconds,
            )
            if assignment is None:
                idle_polls += 1
                if idle_polls > max_idle_polls:
                    break
                self._stop.wait(self.poll_interval_seconds)
                continue
            idle_polls = 0
            claimed += 1
            status = self._execute(assignment)
            if status == "completed":
                completed += 1
            elif status == "failed":
                failed += 1
            elif status == "cancelled":
                cancelled += 1
            else:
                unknown += 1
        return RemoteFleetWorkerReport(claimed, completed, failed, cancelled, unknown, idle_polls)

    def _execute(self, assignment: FleetAssignment) -> str:
        heartbeat_stop = Event()
        heartbeat_error: list[BaseException] = []
        lease_guard = Lock()
        current_lease = [assignment.lease]

        def heartbeat() -> None:
            while not heartbeat_stop.wait(self.heartbeat_interval_seconds):
                try:
                    with lease_guard:
                        current_lease[0] = self.transport.heartbeat_lease(
                            current_lease[0], lease_seconds=self.lease_seconds
                        ).lease
                except BaseException as error:
                    heartbeat_error.append(error)
                    return

        heartbeat_thread = Thread(
            target=heartbeat,
            name=f"coquo-remote-fleet-heartbeat-{self.worker_id}",
            daemon=False,
        )
        heartbeat_thread.start()
        result_status = "completed"
        diagnostic: str | None = None
        result_payload: str | None = None
        try:
            if self._stop.is_set():
                result_status = "cancelled"
                diagnostic = "worker stop requested before execution"
            else:
                value = self.handler(assignment.task)
                if isinstance(value, bytes):
                    result_payload = value.decode("utf-8", errors="strict")
                elif isinstance(value, str):
                    result_payload = value
                else:
                    raise TypeError("remote worker handler must return UTF-8 text or bytes")
                if len(result_payload.encode("utf-8")) > MAX_REMOTE_RESULT_PAYLOAD_BYTES:
                    raise ValueError("remote worker result exceeds size limit")
        except BaseException as error:
            result_status = "cancelled" if self._stop.is_set() else "failed"
            diagnostic = _bounded_diagnostic(error)
            result_payload = None
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()

        if heartbeat_error:
            result_status = "unknown"
            diagnostic = "remote lease heartbeat failed; worker outcome is unknown"
            result_payload = None
        with lease_guard:
            lease = current_lease[0]
        try:
            digest = None
            if result_payload is not None:
                import hashlib

                digest = hashlib.sha256(result_payload.encode("utf-8")).hexdigest()
            self.transport.complete(
                lease,
                RemoteResult(
                    lease.task_id,
                    lease.lease_id,
                    result_status,
                    digest,
                    diagnostic,
                    result_status == "unknown",
                    result_payload,
                ),
            )
        except BaseException:
            return "unknown"
        return result_status


def _bounded_diagnostic(error: BaseException) -> str:
    message = str(error).replace("\x00", "")
    value = f"{type(error).__name__}: {message}".strip()
    return value[:512] or type(error).__name__


__all__ = ["RemoteFleetWorkerLoop", "RemoteFleetWorkerReport"]
