"""Single background turn worker for the persistent terminal frontend."""

from __future__ import annotations

from threading import Lock, Thread, current_thread
from dataclasses import dataclass

from leonervis_code.cli.approval import TerminalApprovalBroker
from leonervis_code.cli.frontend import (
    ForegroundTaskHandoff,
    FrontendEventQueue,
    PromptActivity,
    TurnCompleting,
    TurnFailed,
    TurnFinished,
    TurnSubmitted,
)
from leonervis_code.agent.tool_events import TaskLifecycleCommitted
from leonervis_code.cli.failure_guidance import render_turn_failure
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled


@dataclass(frozen=True)
class TaskTurnRequest:
    """One parsed foreground Task operation for the shared terminal worker."""

    operation: str
    task_id: str
    stage_objective: str | None = None
    max_stages: int = 16


class TurnRunner:
    """Own exactly one worker and one cooperative cancellation token at a time."""

    def __init__(
        self,
        session: object,
        queue: FrontendEventQueue,
        approval_broker: TerminalApprovalBroker,
    ) -> None:
        self._session = session
        self._queue = queue
        self._approval_broker = approval_broker
        self._lock = Lock()
        self._thread: Thread | None = None
        self._cancellation: TurnCancellation | None = None
        self._next_turn_id = 1

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None

    def start(self, prompt: str, *, include_tool_details: bool = False) -> int | None:
        return self._start_worker(
            self._run,
            (prompt, include_tool_details),
        )

    def start_task(
        self,
        request: TaskTurnRequest,
        *,
        include_tool_details: bool = False,
    ) -> int | None:
        if not isinstance(request, TaskTurnRequest):
            raise ValueError("Task turn request is invalid")
        return self._start_worker(
            self._run_task,
            (request, include_tool_details),
        )

    def _start_worker(self, target, arguments: tuple[object, ...]) -> int | None:
        with self._lock:
            if self._thread is not None:
                return None
            turn_id = self._next_turn_id
            self._next_turn_id += 1
            cancellation = TurnCancellation()
            thread = Thread(
                target=target,
                args=(turn_id, *arguments, cancellation),
                name=f"leonervis-turn-{turn_id}",
                daemon=False,
            )
            self._thread = thread
            self._cancellation = cancellation
            self._approval_broker.activate(turn_id, cancellation)
            self._queue.put(TurnSubmitted(turn_id))
            thread.start()
            return turn_id

    def cancel(self) -> bool:
        with self._lock:
            cancellation = self._cancellation
        return cancellation.request() if cancellation is not None else False

    def join(self, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _run(
        self,
        turn_id: int,
        prompt: str,
        include_tool_details: bool,
        cancellation: TurnCancellation,
    ) -> None:
        try:
            prompt_method = getattr(self._session, "prompt")
            task_handoff: ForegroundTaskHandoff | None = None

            def observe_event(event: object) -> None:
                nonlocal task_handoff
                self._queue.put(PromptActivity(turn_id, event))
                if (
                    isinstance(event, TaskLifecycleCommitted)
                    and event.foreground_max_stages is not None
                ):
                    if task_handoff is not None:
                        raise RuntimeError("turn emitted more than one foreground Task handoff")
                    task_handoff = ForegroundTaskHandoff(
                        event.task_id,
                        event.foreground_max_stages,
                    )

            response = prompt_method(
                prompt,
                event_sink=observe_event,
                include_tool_details=include_tool_details,
                cancellation=cancellation,
            )
            self._queue.put(TurnCompleting(turn_id))
            self._queue.put(TurnFinished(turn_id, response, task_handoff))
        except TurnCancelled:
            self._queue.put(
                TurnFailed(
                    turn_id,
                    "Generation cancelled; partial assistant text was not committed.",
                    cancelled=True,
                )
            )
            self._queue.put(TurnFinished(turn_id, ""))
        except BaseException as error:
            self._queue.put(TurnFailed(turn_id, render_turn_failure(error)))
            self._queue.put(TurnFinished(turn_id, ""))
        finally:
            self._approval_broker.deactivate(turn_id)
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None
                    self._cancellation = None

    def _run_task(
        self,
        turn_id: int,
        request: TaskTurnRequest,
        include_tool_details: bool,
        cancellation: TurnCancellation,
    ) -> None:
        try:
            common = {
                "event_sink": lambda event: self._queue.put(PromptActivity(turn_id, event)),
                "include_tool_details": include_tool_details,
                "cancellation": cancellation,
            }
            if request.operation == "continue":
                assert request.stage_objective is not None
                result = getattr(self._session, "continue_task")(
                    request.task_id,
                    request.stage_objective,
                    **common,
                )
                response = result.response
            elif request.operation == "plan":
                result = getattr(self._session, "plan_task")(request.task_id, **common)
                response = result.response
            elif request.operation == "run":
                result = getattr(self._session, "run_task")(
                    request.task_id,
                    max_stages=request.max_stages,
                    **common,
                )
                response = "\n\n".join(stage.response for stage in result.stages if stage.response)
            elif request.operation == "reflect":
                result = getattr(self._session, "reflect_task")(request.task_id, **common)
                response = result.response
            elif request.operation == "correct":
                result = getattr(self._session, "correct_task")(
                    request.task_id,
                    request.stage_objective,
                    **common,
                )
                response = result.response
            elif request.operation == "revise":
                result = getattr(self._session, "revise_task_plan")(request.task_id, **common)
                response = result.response
            elif request.operation == "drive":
                result = getattr(self._session, "drive_task")(
                    request.task_id,
                    max_stages=request.max_stages,
                    **common,
                )
                response = "\n\n".join(stage.response for stage in result.stages if stage.response)
            else:
                raise ValueError("unsupported Task turn operation")
            self._queue.put(TurnCompleting(turn_id))
            self._queue.put(TurnFinished(turn_id, response))
        except TurnCancelled:
            self._queue.put(
                TurnFailed(
                    turn_id,
                    "Task execution cancelled; inspect the durable Stage outcome before continuing.",
                    cancelled=True,
                )
            )
            self._queue.put(TurnFinished(turn_id, ""))
        except BaseException as error:
            self._queue.put(TurnFailed(turn_id, render_turn_failure(error)))
            self._queue.put(TurnFinished(turn_id, ""))
        finally:
            self._approval_broker.deactivate(turn_id)
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None
                    self._cancellation = None
