"""Bounded process-local Team schedule coordination."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Thread
from time import monotonic

from coquo.team_records import TeamScheduleState
from coquo.team_schedule import TeamScheduleRun, TeamScheduleService

MAX_TEAM_SCHEDULE_QUEUE = 8
MAX_TEAM_SCHEDULE_NOTIFICATIONS = 32


@dataclass(frozen=True)
class TeamScheduleNotification:
    team_id: str
    schedule_run_id: str
    state: TeamScheduleState | None
    message: str | None = None


class TeamScheduleSupervisorError(RuntimeError):
    """Raised when the process-local schedule queue cannot accept work."""


class TeamScheduleSupervisor:
    """One coordinator thread over bounded schedule identities."""

    def __init__(
        self, workspace, session, *, queue_capacity: int = MAX_TEAM_SCHEDULE_QUEUE
    ) -> None:
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= MAX_TEAM_SCHEDULE_QUEUE:
            raise ValueError("Team schedule queue capacity is invalid")
        self.workspace = workspace
        self.session = session
        self.service = TeamScheduleService(workspace)
        self._queue_capacity = queue_capacity
        self._condition = Condition()
        self._queue: deque[TeamScheduleRun] = deque()
        self._submitted: set[str] = set()
        self._notifications: deque[TeamScheduleNotification] = deque(
            maxlen=MAX_TEAM_SCHEDULE_NOTIFICATIONS
        )
        self._worker: Thread | None = None
        self._closing = False

    def submit(self, run: TeamScheduleRun) -> TeamScheduleState:
        with self._condition:
            if self._closing:
                raise TeamScheduleSupervisorError("Team schedule supervisor is closing")
            if run.schedule_run_id in self._submitted:
                raise TeamScheduleSupervisorError("Team schedule run is already submitted")
            if len(self._queue) >= self._queue_capacity:
                raise TeamScheduleSupervisorError("Team schedule queue is full")
            self._submitted.add(run.schedule_run_id)
            self._queue.append(run)
            if self._worker is None or not self._worker.is_alive():
                self._worker = Thread(
                    target=self._worker_main, name="coquo-team-schedule", daemon=True
                )
                self._worker.start()
            self._condition.notify_all()
        return run.state

    def wait(self, schedule_run_id: str, timeout_seconds: float) -> TeamScheduleNotification | None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("Team schedule wait timeout is invalid")
        if not 0 <= timeout_seconds <= 30:
            raise ValueError("Team schedule wait timeout must be between 0 and 30 seconds")
        deadline = monotonic() + timeout_seconds
        with self._condition:
            while True:
                for notification in self._notifications:
                    if notification.schedule_run_id == schedule_run_id:
                        return notification
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(min(0.1, remaining))

    def drain_notifications(self, limit: int = MAX_TEAM_SCHEDULE_NOTIFICATIONS):
        if type(limit) is not int or not 1 <= limit <= MAX_TEAM_SCHEDULE_NOTIFICATIONS:
            raise ValueError("Team schedule notification limit is invalid")
        with self._condition:
            items = []
            while self._notifications and len(items) < limit:
                items.append(self._notifications.popleft())
            return tuple(items)

    @property
    def queued_count(self) -> int:
        with self._condition:
            return len(self._queue)

    def close(self, *, join_timeout: float = 1.0) -> None:
        if join_timeout < 0:
            raise ValueError("join timeout is invalid")
        with self._condition:
            self._closing = True
            queued = tuple(self._queue)
            self._queue.clear()
            self._condition.notify_all()
            worker = self._worker
        for run in queued:
            try:
                run.cancel("schedule supervisor shutdown", source="shutdown")
                run.finish(
                    outcome="cancelled",
                    result_code="shutdown",
                    message="schedule was queued during supervisor shutdown",
                )
            except Exception:
                run.close()
        if worker is not None:
            worker.join(join_timeout)

    def _worker_main(self) -> None:
        while True:
            with self._condition:
                if not self._queue:
                    return
                run = self._queue.popleft()
            try:
                state = self.service.run_started(run, self.session)
                notification = TeamScheduleNotification(run.team_id, run.schedule_run_id, state)
            except BaseException as error:
                notification = TeamScheduleNotification(
                    run.team_id,
                    run.schedule_run_id,
                    None,
                    str(error).replace("\n", " ").replace("\r", " ")[:512],
                )
                run.close()
            with self._condition:
                self._submitted.discard(run.schedule_run_id)
                self._notifications.append(notification)
                self._condition.notify_all()


__all__ = [
    "MAX_TEAM_SCHEDULE_NOTIFICATIONS",
    "MAX_TEAM_SCHEDULE_QUEUE",
    "TeamScheduleNotification",
    "TeamScheduleSupervisor",
    "TeamScheduleSupervisorError",
]
