"""Process-local bounded supervisor for prepared Child Runs."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Thread
from time import monotonic
from typing import Callable

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunInfo, ChildRunStore

MAX_CHILD_WORKERS = 4
MAX_CHILD_QUEUE = 32
MAX_CHILD_NOTIFICATIONS = 128


@dataclass(frozen=True)
class ChildSupervisorNotification:
    child_run_id: str
    status: ChildRunStatus | None
    message: str | None = None


class ChildSupervisorError(RuntimeError):
    """Raised when local Child scheduling cannot accept a request."""


class ChildRunSupervisor:
    """Own bounded daemon workers; durable Child state remains authoritative."""

    def __init__(
        self,
        workspace,
        *,
        executor_factory: Callable[[str], object] | None = None,
        worker_count: int = MAX_CHILD_WORKERS,
        queue_capacity: int = MAX_CHILD_QUEUE,
        parent_session_id: str | None = None,
    ) -> None:
        if type(worker_count) is not int or not 1 <= worker_count <= MAX_CHILD_WORKERS:
            raise ValueError("Child worker count is invalid")
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= MAX_CHILD_QUEUE:
            raise ValueError("Child queue capacity is invalid")
        self.workspace = workspace
        self.store = ChildRunStore(workspace)
        self._executor_factory = executor_factory or self._default_executor
        self._worker_count = worker_count
        self._queue_capacity = queue_capacity
        self._parent_session_id = parent_session_id
        self._condition = Condition()
        self._queue: deque[str] = deque()
        self._submitted: set[str] = set()
        self._notifications: deque[ChildSupervisorNotification] = deque(
            maxlen=MAX_CHILD_NOTIFICATIONS
        )
        self._workers: list[Thread] = []
        self._closing = False
        self._active: dict[str, object] = {}

    def submit(self, child_run_id: str) -> ChildRunInfo:
        info = self.store.inspect(child_run_id)
        if info.status is not ChildRunStatus.READY:
            raise ChildSupervisorError("Child Run is not ready for background execution")
        if (
            self._parent_session_id is not None
            and info.parent_session_id != self._parent_session_id
        ):
            raise ChildSupervisorError("Child Run belongs to another parent Session")
        with self._condition:
            if self._closing:
                raise ChildSupervisorError("Child supervisor is closing")
            if child_run_id in self._submitted:
                raise ChildSupervisorError("Child Run is already submitted")
            if len(self._queue) >= self._queue_capacity:
                raise ChildSupervisorError("Child supervisor queue is full")
            self._submitted.add(child_run_id)
            self._queue.append(child_run_id)
            self._ensure_workers_locked()
            self._condition.notify()
        return info

    def cancel(self, child_run_id: str, reason: str, *, source: str = "host") -> ChildRunInfo:
        self.store.request_cancel(child_run_id, reason=reason, source=source)
        with self._condition:
            token = self._active.get(child_run_id)
            if token is not None and hasattr(token, "request"):
                token.request()
            self._condition.notify_all()
        return self.store.inspect(child_run_id)

    def wait(self, child_run_id: str, timeout_seconds: float) -> ChildRunInfo:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("Child wait timeout is invalid")
        if not 0 <= timeout_seconds <= 30:
            raise ValueError("Child wait timeout must be between 0 and 30 seconds")
        deadline = monotonic() + timeout_seconds
        while True:
            info = self.store.inspect(child_run_id)
            if info.status in {
                ChildRunStatus.CANCELLED,
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.INTERRUPTED,
            }:
                return info
            remaining = deadline - monotonic()
            if remaining <= 0:
                return info
            with self._condition:
                self._condition.wait(min(0.1, remaining))

    def inspect(self, child_run_id: str) -> ChildRunInfo:
        return self.store.inspect(child_run_id)

    def drain_notifications(self, limit: int = MAX_CHILD_NOTIFICATIONS):
        if type(limit) is not int or not 1 <= limit <= MAX_CHILD_NOTIFICATIONS:
            raise ValueError("notification limit is invalid")
        with self._condition:
            items = []
            while self._notifications and len(items) < limit:
                items.append(self._notifications.popleft())
            return tuple(items)

    def close(self, *, join_timeout: float = 1.0) -> None:
        if join_timeout < 0:
            raise ValueError("join timeout is invalid")
        with self._condition:
            self._closing = True
            queued = tuple(self._queue)
            self._queue.clear()
            self._condition.notify_all()
            workers = tuple(self._workers)
            active = tuple(self._active)
        for child_run_id in queued:
            self.store.request_cancel(child_run_id, reason="supervisor shutdown", source="shutdown")
        for child_run_id in active:
            current = self.store.inspect(child_run_id)
            if current.status in {
                ChildRunStatus.CANCELLED,
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.INTERRUPTED,
            }:
                continue
            if current.status is not ChildRunStatus.CANCELLING:
                self.cancel(child_run_id, "supervisor shutdown", source="shutdown")
            else:
                with self._condition:
                    token = self._active.get(child_run_id)
                    if token is not None and hasattr(token, "request"):
                        token.request()
        deadline = monotonic() + join_timeout
        for worker in workers:
            worker.join(max(0.0, deadline - monotonic()))

    @property
    def active_worker_count(self) -> int:
        with self._condition:
            return sum(worker.is_alive() for worker in self._workers)

    @property
    def queued_count(self) -> int:
        with self._condition:
            return len(self._queue)

    def _ensure_workers_locked(self) -> None:
        if self._workers:
            return
        self._workers = [
            Thread(target=self._worker, name=f"coquo-child-{index + 1}", daemon=True)
            for index in range(self._worker_count)
        ]
        for worker in self._workers:
            worker.start()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closing:
                    self._condition.wait()
                if not self._queue:
                    return
                child_run_id = self._queue.popleft()
            status: ChildRunStatus | None = None
            try:
                from coquo.core.cancellation import TurnCancellation

                token = TurnCancellation()
                with self._condition:
                    self._active[child_run_id] = token
                executor = self._executor_factory(child_run_id)
                try:
                    executor.run(child_run_id, cancellation=token)
                except TypeError as error:
                    if "cancellation" not in str(error):
                        raise
                    executor.run(child_run_id)
                status = self.store.inspect(child_run_id).status
                message = None
            except BaseException as error:
                try:
                    status = self.store.inspect(child_run_id).status
                except BaseException as inspect_error:
                    message = f"{error}; durable status unavailable: {inspect_error}"
                else:
                    message = str(error)
                message = message.replace("\n", " ").replace("\r", " ")[:512]
                if not message:
                    message = error.__class__.__name__
            finally:
                with self._condition:
                    self._active.pop(child_run_id, None)
                    self._submitted.discard(child_run_id)
                    self._notifications.append(
                        ChildSupervisorNotification(child_run_id, status, message)
                    )
                    self._condition.notify_all()

    def _default_executor(self, child_run_id: str):
        del child_run_id
        from coquo.child_runtime import ChildRunExecutor

        return ChildRunExecutor(self.workspace)
