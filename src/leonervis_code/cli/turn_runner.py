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
from leonervis_code.cli.presentation import render_provider_adapter_error
from leonervis_code.core.action_coordinator import ActionIdentityChangedError
from leonervis_code.core.approvals import ApprovalGrantError
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.manager import RuntimeProviderStateError
from leonervis_code.providers.profile import ProviderProfileError
from leonervis_code.providers.request_context import ContextPreflightError
from leonervis_code.session_store import SessionStoreError


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
            self._queue.put(TurnFailed(turn_id, _turn_failure_message(error)))
            self._queue.put(TurnFinished(turn_id, ""))
        finally:
            self._approval_broker.deactivate(turn_id)
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None
                    self._cancellation = None


def _turn_failure_message(error: BaseException) -> str:
    if isinstance(error, ContextPreflightError):
        return f"Context preflight error: {error}"
    if isinstance(error, ProviderAdapterError):
        message = render_provider_adapter_error(error, prefix="Provider error")
        if error.failure.kind == ProviderFailureKind.OUTPUT_LIMIT:
            message += "\nNo turn was committed. Tool side effects completed earlier remain in Action Audit."
        return message
    if isinstance(error, (ProviderProfileError, RuntimeProviderStateError)):
        return f"Runtime error: {error}"
    if isinstance(error, (ApprovalGrantError, ActionIdentityChangedError)):
        return f"Action authorization error: {error}"
    if isinstance(error, SessionStoreError):
        return f"Session error: {error}"
    return f"Turn failed: {type(error).__name__}: {error}"
