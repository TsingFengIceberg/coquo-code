"""Single background turn worker for the persistent terminal frontend."""

from __future__ import annotations

from threading import Lock, Thread, current_thread

from leonervis_code.cli.approval import TerminalApprovalBroker
from leonervis_code.cli.frontend import (
    FrontendEventQueue,
    PromptActivity,
    TurnCompleting,
    TurnFailed,
    TurnFinished,
    TurnSubmitted,
)
from leonervis_code.cli.failure_guidance import render_turn_failure
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled


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
        with self._lock:
            if self._thread is not None:
                return None
            turn_id = self._next_turn_id
            self._next_turn_id += 1
            cancellation = TurnCancellation()
            thread = Thread(
                target=self._run,
                args=(turn_id, prompt, include_tool_details, cancellation),
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
            response = prompt_method(
                prompt,
                event_sink=lambda event: self._queue.put(PromptActivity(turn_id, event)),
                include_tool_details=include_tool_details,
                cancellation=cancellation,
            )
            self._queue.put(TurnCompleting(turn_id))
            self._queue.put(TurnFinished(turn_id, response))
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
