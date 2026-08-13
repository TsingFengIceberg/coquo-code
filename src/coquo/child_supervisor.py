"""Process-local bounded supervisor for prepared Child Runs."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Thread
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

    def close(self, *, join_timeout: float = 0.25) -> None:
        if join_timeout < 0:
            raise ValueError("join timeout is invalid")
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(join_timeout)

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
                self._executor_factory(child_run_id).run(child_run_id)
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
                    self._submitted.discard(child_run_id)
                    self._notifications.append(
                        ChildSupervisorNotification(child_run_id, status, message)
                    )

    def _default_executor(self, child_run_id: str):
        del child_run_id
        from coquo.child_runtime import ChildRunExecutor

        return ChildRunExecutor(self.workspace)
