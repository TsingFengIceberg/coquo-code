"""Typed local frontend state, events, reducer, and bounded worker queue."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Condition

from coquo.agent.tool_events import AssistantResponseTextDeltaReceived
from coquo.core.action_coordinator import ApprovalPromptRequest


class TerminalPhase(StrEnum):
    IDLE = "idle"
    GENERATING = "generating"
    TOOL = "tool"
    APPROVAL = "approval"
    CANCELLING = "cancelling"
    COMPLETING = "completing"
    FAILED = "failed"


@dataclass(frozen=True)
class TerminalViewState:
    phase: TerminalPhase = TerminalPhase.IDLE
    status: str = "Ready"
    active_turn: int | None = None
    approval_request: ApprovalPromptRequest | None = None
    exit_after_turn: bool = False

    @property
    def busy(self) -> bool:
        return self.phase != TerminalPhase.IDLE


@dataclass(frozen=True)
class TurnSubmitted:
    turn_id: int
    status: str = "Preparing turn"

    def __post_init__(self) -> None:
        if type(self.turn_id) is not int or self.turn_id < 1:
            raise ValueError("submitted turn ID is invalid")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("submitted turn status is invalid")


@dataclass(frozen=True)
class PromptActivity:
    turn_id: int
    event: object


@dataclass(frozen=True)
class ApprovalPending:
    turn_id: int
    request: ApprovalPromptRequest


@dataclass(frozen=True)
class ApprovalResolved:
    turn_id: int


@dataclass(frozen=True)
class CancellationRequested:
    turn_id: int
    exit_after_turn: bool = False


@dataclass(frozen=True)
class TurnCompleting:
    turn_id: int


@dataclass(frozen=True)
class ForegroundTaskHandoff:
    task_id: str
    max_stages: int

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("foreground Task handoff ID is invalid")
        if type(self.max_stages) is not int or not 1 <= self.max_stages <= 16:
            raise ValueError("foreground Task handoff Stage limit is invalid")


@dataclass(frozen=True)
class TurnFinished:
    turn_id: int
    response: str
    task_handoff: ForegroundTaskHandoff | None = None

    def __post_init__(self) -> None:
        if type(self.turn_id) is not int or self.turn_id < 1:
            raise ValueError("finished turn ID is invalid")
        if not isinstance(self.response, str):
            raise ValueError("finished turn response is invalid")
        if self.task_handoff is not None and type(self.task_handoff) is not ForegroundTaskHandoff:
            raise ValueError("finished turn Task handoff is invalid")


@dataclass(frozen=True)
class TurnFailed:
    turn_id: int
    message: str
    cancelled: bool = False


FrontendEvent = (
    TurnSubmitted
    | PromptActivity
    | ApprovalPending
    | ApprovalResolved
    | CancellationRequested
    | TurnCompleting
    | TurnFinished
    | TurnFailed
)


def reduce_terminal_state(state: TerminalViewState, event: FrontendEvent) -> TerminalViewState:
    """Apply one legal frontend transition without I/O or Session mutation."""
    if isinstance(event, TurnSubmitted):
        if state.busy:
            raise ValueError("turn submission is invalid for the current terminal state")
        return TerminalViewState(TerminalPhase.GENERATING, event.status, event.turn_id)

    if state.active_turn != event.turn_id:
        raise ValueError("frontend event does not match the active turn")

    if isinstance(event, PromptActivity):
        if state.phase in {TerminalPhase.COMPLETING, TerminalPhase.FAILED}:
            raise ValueError("prompt activity arrived after terminal completion")
        if state.phase == TerminalPhase.CANCELLING:
            return state
        activity = event.event
        name = type(activity).__name__
        phase = state.phase
        if (
            state.phase
            not in {
                TerminalPhase.CANCELLING,
                TerminalPhase.APPROVAL,
            }
            and name == "ToolRequestStarted"
        ):
            phase = TerminalPhase.TOOL
        elif name in {
            "ToolRequestFinished",
            "ToolRequestLimited",
            "ToolRequestSkipped",
            "ToolTurnSummaryCommitted",
        }:
            phase = TerminalPhase.GENERATING
        return replace(state, phase=phase, status=_activity_status(activity))
    if isinstance(event, ApprovalPending):
        if state.phase in {TerminalPhase.COMPLETING, TerminalPhase.FAILED}:
            raise ValueError("approval arrived after terminal completion")
        if state.phase == TerminalPhase.CANCELLING:
            return state
        return replace(
            state,
            phase=TerminalPhase.APPROVAL,
            status="Approval required",
            approval_request=event.request,
        )
    if isinstance(event, ApprovalResolved):
        if state.phase != TerminalPhase.APPROVAL:
            raise ValueError("approval resolution requires a pending approval")
        return replace(
            state,
            phase=TerminalPhase.GENERATING,
            status="Continuing turn",
            approval_request=None,
        )
    if isinstance(event, CancellationRequested):
        if state.phase in {TerminalPhase.COMPLETING, TerminalPhase.FAILED}:
            return replace(state, exit_after_turn=state.exit_after_turn or event.exit_after_turn)
        return replace(
            state,
            phase=TerminalPhase.CANCELLING,
            status="Cancelling",
            approval_request=None,
            exit_after_turn=state.exit_after_turn or event.exit_after_turn,
        )
    if isinstance(event, TurnCompleting):
        return replace(
            state,
            phase=TerminalPhase.COMPLETING,
            status="Finalizing turn",
            approval_request=None,
        )
    if isinstance(event, TurnFailed):
        return replace(
            state,
            phase=TerminalPhase.FAILED,
            status="Cancelled" if event.cancelled else "Failed",
            approval_request=None,
        )
    if isinstance(event, TurnFinished):
        return TerminalViewState(exit_after_turn=state.exit_after_turn)
    raise TypeError("unsupported frontend event")


def _activity_status(event: object) -> str:
    name = type(event).__name__
    if name == "AssistantResponseTextDeltaReceived":
        return "Responding"
    if name in {"AssistantToolTextReceived", "AssistantToolTextStreamCompleted"}:
        return "Planning actions"
    if name == "ProviderInvocationPreflighted":
        return "Preparing provider request"
    if name == "ProviderInvocationUsageReceived":
        return "Processing provider response"
    if name == "ProviderSearchActivityReceived":
        return f"Provider search {getattr(event, 'phase').value}"
    if name == "ProviderSearchSummaryReceived":
        return "Recording provider search"
    if name == "McpNotificationActivityReceived":
        return f"MCP {getattr(event, 'kind').value}"
    if name == "ToolRequestStarted":
        tool_name = getattr(event, "tool_name", "tool")
        return f"Running {tool_name}"
    if name in {"ToolRequestFinished", "ToolRequestLimited", "ToolRequestSkipped"}:
        return "Processing tool result"
    if name == "ToolTurnSummaryCommitted":
        return "Recording tool outcomes"
    if name == "AutoCompactionStarted":
        return "Compacting context"
    if name == "AutoCompactionCommitted":
        return "Saving compacted context"
    if name == "AutoCompactionNotApplied":
        return "Continuing without compaction"
    if name == "TurnUsageCompleted":
        return "Recording provider usage"
    if name == "SessionTitleGenerationStarted":
        return "Naming Session"
    if name == "SessionTitlePrepared":
        return "Session title prepared"
    if name == "SessionTitleFallbackApplied":
        return "Session named with Host fallback"
    if name == "TurnCommitStarted":
        return "Saving Session"
    if name == "TaskAdmissionProposed":
        return "Recording Task proposal"
    if name == "TaskLifecycleCommitted":
        return "Updating Task lifecycle"
    if name == "HookLifecycleObserved":
        return "Observing Hook lifecycle"
    if name == "SkillCandidateCommitted":
        return "Recording Skill candidate"
    if name == "SkillCandidateInstalled":
        return "Installing Skill candidate"
    if name == "TaskRunStopped":
        return "Finishing Task run"
    return "Working"


class FrontendEventQueue:
    """Bounded local queue that only coalesces consecutive assistant text deltas."""

    def __init__(self, capacity: int = 256) -> None:
        if type(capacity) is not int or capacity < 2:
            raise ValueError("frontend queue capacity must be at least two")
        self._capacity = capacity
        self._items: deque[FrontendEvent] = deque()
        self._condition = Condition()
        self._closed = False

    def put(self, event: FrontendEvent) -> bool:
        """Enqueue one event; return false only when a text delta was coalesced."""
        with self._condition:
            if self._closed:
                return False
            if self._coalesce_delta(event):
                self._condition.notify()
                return False
            while len(self._items) >= self._capacity and not self._closed:
                self._condition.wait()
            if self._closed:
                return False
            self._items.append(event)
            self._condition.notify()
            return True

    def drain(self, limit: int = 64) -> tuple[FrontendEvent, ...]:
        if type(limit) is not int or limit < 1:
            raise ValueError("frontend drain limit must be positive")
        with self._condition:
            drained: list[FrontendEvent] = []
            while self._items and len(drained) < limit:
                drained.append(self._items.popleft())
            if drained:
                self._condition.notify_all()
            return tuple(drained)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _coalesce_delta(self, event: FrontendEvent) -> bool:
        if not self._items or not _is_delta(event):
            return False
        previous = self._items[-1]
        if not _is_delta(previous) or previous.turn_id != event.turn_id:
            return False
        assert isinstance(previous, PromptActivity) and isinstance(event, PromptActivity)
        before = previous.event
        after = event.event
        assert isinstance(before, AssistantResponseTextDeltaReceived)
        assert isinstance(after, AssistantResponseTextDeltaReceived)
        self._items[-1] = PromptActivity(
            event.turn_id,
            AssistantResponseTextDeltaReceived(before.text + after.text),
        )
        return True


def _is_delta(event: FrontendEvent) -> bool:
    return isinstance(event, PromptActivity) and isinstance(
        event.event, AssistantResponseTextDeltaReceived
    )
