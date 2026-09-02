from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
from pathlib import Path
import re
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Size
from rich.cells import cell_len

from coquo.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    ProviderInvocationFinished,
    ProviderInvocationOutcome,
    ProviderInvocationPurpose,
    ProviderInvocationPreflighted,
    ProviderInvocationStarted,
    TaskLifecycleCommitted,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
)
from coquo.cli.approval import TerminalApprovalBroker
from coquo.cli.frontend import (
    ApprovalPending,
    CancellationRequested,
    FrontendEventQueue,
    PromptActivity,
    TerminalPhase,
    TerminalViewState,
    TurnFinished,
    TurnFailed,
    TurnSubmitted,
    reduce_terminal_state,
)
from coquo.cli.terminal_app import TerminalApplication, _QueuedPromptRenderer
from coquo.cli.presentation import CLEAR_SCREEN
from coquo.core.action_coordinator import (
    ApprovalResolution,
    HumanApprovalRequest,
)
from coquo.core.approval_preview import build_file_change_preview
from coquo.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from coquo.core.cancellation import TurnCancellation, TurnCancelled
from coquo.core.contracts import ToolArguments
from coquo.core.permissions import (
    PermissionAction,
    PermissionDecision,
    PermissionReason,
    PermissionResult,
)
from coquo.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from coquo.session import SessionTitlePrepared, TurnCommitStarted
from coquo.session_records import SessionNameSource

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class _SizedDummyOutput(DummyOutput):
    def __init__(self, columns: int) -> None:
        self._columns = columns

    def get_size(self) -> Size:
        return Size(rows=24, columns=self._columns)


@dataclass(frozen=True)
class _TitleInfo:
    name: str
    name_source: SessionNameSource
    title_fallback_reason: object | None = None


def test_terminal_reducer_accepts_only_one_matching_active_turn() -> None:
    state = reduce_terminal_state(TerminalViewState(), TurnSubmitted(1))
    assert state.phase == TerminalPhase.GENERATING
    assert state.status == "Preparing turn"
    assert state.active_turn == 1

    state = reduce_terminal_state(
        state,
        PromptActivity(1, AssistantResponseTextDeltaReceived("hello")),
    )
    assert state.status == "Responding"

    with pytest.raises(ValueError, match="active turn"):
        reduce_terminal_state(state, TurnFinished(2, "wrong"))
    with pytest.raises(ValueError, match="submission"):
        reduce_terminal_state(state, TurnSubmitted(2))

    state = reduce_terminal_state(state, CancellationRequested(1, exit_after_turn=True))
    assert state.phase == TerminalPhase.CANCELLING
    assert state.exit_after_turn is True
    state = reduce_terminal_state(state, TurnFinished(1, ""))
    assert state.phase == TerminalPhase.IDLE
    assert state.exit_after_turn is True


def test_terminal_reducer_preserves_specific_initial_activity_status() -> None:
    state = reduce_terminal_state(
        TerminalViewState(),
        TurnSubmitted(1, "Preparing Task Stage"),
    )

    assert state.phase == TerminalPhase.GENERATING
    assert state.status == "Preparing Task Stage"

    with pytest.raises(ValueError, match="turn ID"):
        TurnSubmitted(0)
    with pytest.raises(ValueError, match="status"):
        TurnSubmitted(1, " ")


def test_terminal_reducer_exposes_provider_tool_and_result_phases() -> None:
    state = reduce_terminal_state(TerminalViewState(), TurnSubmitted(1))
    report = ContextFitReport(
        None,
        RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED),
        20,
        100,
        None,
        ContextFitDecision.FITS,
    )
    state = reduce_terminal_state(
        state,
        PromptActivity(1, ProviderInvocationStarted(1, 24)),
    )
    assert state.status == "Model round 1/24: starting"

    state = reduce_terminal_state(
        state,
        PromptActivity(1, ProviderInvocationPreflighted(1, 24, report)),
    )
    assert state.status == "Model round 1/24: waiting for provider"

    state = reduce_terminal_state(
        state,
        PromptActivity(
            1,
            ProviderInvocationFinished(
                1,
                24,
                ProviderInvocationOutcome.TOOL_REQUEST,
                1,
            ),
        ),
    )
    assert state.status == "Model round 1/24: tool-request received"

    state = reduce_terminal_state(
        state,
        PromptActivity(
            1,
            ProviderInvocationStarted(2, 24, ProviderInvocationPurpose.SESSION_TITLE),
        ),
    )
    assert state.status == "Model round 2/24: naming Session"

    state = reduce_terminal_state(
        state,
        PromptActivity(1, ToolRequestStarted("read_file", 1, 32, "path='README.md'")),
    )
    assert state.phase == TerminalPhase.TOOL
    assert state.status == "Running read_file"

    state = reduce_terminal_state(
        state,
        PromptActivity(
            1,
            ToolRequestFinished("read_file", 1, 32, ToolEventStatus.SUCCEEDED, "ok"),
        ),
    )
    assert state.phase == TerminalPhase.GENERATING
    assert state.status == "Processing tool result"

    state = reduce_terminal_state(state, PromptActivity(1, TurnCommitStarted()))
    assert state.status == "Saving Session"
    state = reduce_terminal_state(state, TurnFinished(1, ""))
    assert state.phase == TerminalPhase.IDLE
    assert state.exit_after_turn is False


def test_frontend_queue_preserves_each_text_delta_in_arrival_order() -> None:
    queue = FrontendEventQueue(capacity=4)
    assert queue.put(PromptActivity(1, AssistantResponseTextDeltaReceived("a")))
    assert queue.put(PromptActivity(1, AssistantResponseTextDeltaReceived("b")))
    queue.put(TurnFinished(1, "ab"))

    events = queue.drain()
    assert events == (
        PromptActivity(1, AssistantResponseTextDeltaReceived("a")),
        PromptActivity(1, AssistantResponseTextDeltaReceived("b")),
        TurnFinished(1, "ab"),
    )


def test_frontend_queue_close_releases_a_blocked_critical_event_producer() -> None:
    queue = FrontendEventQueue(capacity=2)
    queue.put(TurnSubmitted(1))
    queue.put(TurnFinished(1, "done"))
    result = []
    producer = Thread(target=lambda: result.append(queue.put(TurnFinished(1, "late"))))

    producer.start()
    time.sleep(0.05)
    assert producer.is_alive()
    queue.close()
    producer.join(1)

    assert not producer.is_alive()
    assert result == [False]


def test_frontend_queue_metrics_expose_backpressure_without_event_content() -> None:
    queue = FrontendEventQueue(capacity=2)
    queue.put(TurnSubmitted(1))
    queue.put(TurnFinished(1, "done"))
    result = []
    producer = Thread(target=lambda: result.append(queue.put(TurnFinished(1, "late"))))
    producer.start()
    time.sleep(0.05)
    before = queue.metrics()
    assert before["blocked_puts"] == 1
    assert before["depth"] == 2
    queue.drain()
    producer.join(1)
    after = queue.metrics()
    assert result == [True]
    assert after["high_watermark"] == 2
    assert after["enqueued"] == 3
    assert after["drained"] == 2
    assert "late" not in str(after)


@pytest.mark.parametrize("render_markdown", [False, True])
def test_queued_renderer_fallback_keeps_assistant_hanging_indent(
    render_markdown: bool,
) -> None:
    renderer = _QueuedPromptRenderer(
        color=False,
        render_markdown=render_markdown,
        width=40,
    )
    renderer.render(AssistantResponseTextDeltaReceived("partial"))

    rendered = renderer.render(
        AssistantFinalTextStreamCommitted(
            "A corrected complete Task response that wraps safely.\nSecond logical line."
        )
    )

    lines = rendered.splitlines()
    assert any(line.startswith("• A corrected") for line in lines)
    assert "Second logical line." in " ".join(line.strip() for line in lines)
    assert all(line.startswith(("• ", "  ")) for line in lines if line)


def test_persistent_application_reserves_the_terminal_final_column(tmp_path: Path) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=True,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=_SizedDummyOutput(80),
        )

    assert terminal._current_width() == 79


def test_persistent_application_hard_wraps_wide_viewports_at_readable_width(
    tmp_path: Path,
) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=True,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=_SizedDummyOutput(240),
        )

    assert terminal._current_width() == 100


def test_persistent_application_refreshes_renderer_width_for_each_activity(
    tmp_path: Path,
) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    output = _SizedDummyOutput(80)
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=True,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=output,
        )
        terminal._state = reduce_terminal_state(terminal._state, TurnSubmitted(1))
        asyncio.run(
            terminal._handle_event(
                PromptActivity(
                    1,
                    AssistantResponseTextDeltaReceived(
                        "尚未完成的流式段落会在窗口缩小以后继续使用新的宽度进行包装"
                    ),
                )
            )
        )
        output._columns = 41
        asyncio.run(
            terminal._handle_event(
                PromptActivity(
                    1,
                    AssistantFinalTextStreamCommitted(
                        "尚未完成的流式段落会在窗口缩小以后继续使用新的宽度进行包装"
                    ),
                )
            )
        )

    rendered = terminal._stdout.getvalue()
    lines = [line for line in rendered.splitlines() if line]
    assert len(lines) >= 2
    assert lines[0].startswith("• ")
    assert all(line.startswith(("• ", "  ")) for line in lines)
    assert all(cell_len(ANSI_ESCAPE.sub("", line)) <= 40 for line in lines)


def test_persistent_application_reverts_transient_title_when_first_turn_fails(
    tmp_path: Path,
) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=True,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        original = _TitleInfo("New session 1", SessionNameSource.DEFAULT)
        terminal._session_info = original
        terminal._state = reduce_terminal_state(terminal._state, TurnSubmitted(1))
        asyncio.run(
            terminal._handle_event(
                PromptActivity(
                    1,
                    SessionTitlePrepared("Immediate title", SessionNameSource.MODEL),
                )
            )
        )
        assert terminal._session_info.name == "Immediate title"

        asyncio.run(terminal._handle_event(TurnFailed(1, "provider failed")))

    assert terminal._session_info == original


def test_turn_cancellation_is_idempotent_and_checked() -> None:
    cancellation = TurnCancellation()
    assert cancellation.requested is False
    assert cancellation.request() is True
    assert cancellation.request() is False
    with pytest.raises(TurnCancelled):
        cancellation.check()


class _InteractiveSession:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.prompts: list[str] = []

    @property
    def turns(self):
        return ()

    def prompt(self, text, *, event_sink, include_tool_details, cancellation):
        del include_tool_details
        self.prompts.append(text)
        self.started.set()
        while not self.release.wait(0.01):
            cancellation.check()
        event_sink(AssistantResponseTextDeltaReceived("reply"))
        event_sink(AssistantFinalTextStreamCommitted("reply"))
        return "reply"


class _CommitEventSession:
    @property
    def turns(self):
        return ()

    def prompt(self, _text, *, event_sink, include_tool_details, cancellation):
        del include_tool_details, cancellation
        event_sink(AssistantResponseTextDeltaReceived("saved reply"))
        event_sink(TurnCommitStarted())
        event_sink(AssistantFinalTextStreamCommitted("saved reply"))
        return "saved reply"


class _NamedSession:
    def __init__(self) -> None:
        self.name = "New session 1"

    @property
    def turns(self):
        return ()

    def session_info(self):
        return SimpleNamespace(name=self.name)

    def rename_session(self, name=None):
        self.name = name or "Automatic title"
        return SimpleNamespace(
            name=self.name,
            name_source=SimpleNamespace(value="manual" if name is not None else "auto"),
        )

    def prompt(self, text, *, event_sink, include_tool_details, cancellation):
        del include_tool_details, cancellation
        self.name = text
        event_sink(AssistantResponseTextDeltaReceived("named reply"))
        event_sink(AssistantFinalTextStreamCommitted("named reply"))
        return "named reply"


class _FailingSession:
    @property
    def turns(self):
        return ()

    def prompt(self, _text, *, event_sink, include_tool_details, cancellation):
        del event_sink, include_tool_details, cancellation
        raise RuntimeError("provider unavailable")


class _TaskTerminalSession:
    def __init__(self) -> None:
        self.requests = []

    @property
    def turns(self):
        return ()

    def continue_task(
        self,
        task_id,
        stage_objective,
        *,
        event_sink,
        include_tool_details,
        cancellation,
    ):
        del include_tool_details
        cancellation.check()
        self.requests.append(("continue", task_id, stage_objective))
        event_sink(AssistantResponseTextDeltaReceived("Task Stage complete"))
        event_sink(AssistantFinalTextStreamCommitted("Task Stage complete"))
        return SimpleNamespace(response="Task Stage complete")

    def run_task(
        self,
        task_id,
        *,
        max_stages,
        event_sink,
        include_tool_details,
        cancellation,
    ):
        del event_sink, include_tool_details
        cancellation.check()
        self.requests.append(("run", task_id, max_stages))
        return SimpleNamespace(
            stages=(
                SimpleNamespace(response="First Stage complete"),
                SimpleNamespace(response="Second Stage complete"),
            )
        )


class _NaturalTaskSession:
    def __init__(self) -> None:
        self.requests = []

    @property
    def turns(self):
        return ()

    def prompt(self, text, *, event_sink, include_tool_details, cancellation):
        del include_tool_details
        cancellation.check()
        self.requests.append(("prompt", text))
        event_sink(AssistantResponseTextDeltaReceived("Accepted naturally."))
        event_sink(
            TaskLifecycleCommitted(
                "accept-plan",
                "42345678-1234-4234-9234-123456789abc",
                4,
            )
        )
        event_sink(AssistantFinalTextStreamCommitted("Accepted naturally."))
        return "Accepted naturally."

    def drive_task(
        self,
        task_id,
        *,
        max_stages,
        event_sink,
        include_tool_details,
        cancellation,
    ):
        del include_tool_details
        cancellation.check()
        self.requests.append(("drive", task_id, max_stages))
        response = "Automatic Task Stage complete.\nSecond Task line."
        event_sink(AssistantResponseTextDeltaReceived(response))
        event_sink(AssistantFinalTextStreamCommitted(response))
        return SimpleNamespace(stages=(SimpleNamespace(response=response),))


class _HistorySession:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._turns = (
            SimpleNamespace(user=SimpleNamespace(text="older diagnostic prompt")),
            SimpleNamespace(user=SimpleNamespace(text="newer implementation prompt")),
        )

    @property
    def turns(self):
        return self._turns

    def prompt(self, text, *, event_sink, include_tool_details, cancellation):
        del include_tool_details, cancellation
        self.prompts.append(text)
        event_sink(AssistantResponseTextDeltaReceived("history reply"))
        event_sink(AssistantFinalTextStreamCommitted("history reply"))
        return "history reply"


def test_persistent_application_exposes_ephemeral_animated_activity_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )

        assert terminal._activity_visible() is False
        terminal._turn_starting = True
        terminal._state = TerminalViewState(status="Preparing Task Stage")
        assert terminal._activity_visible() is True
        assert terminal._activity_line().value == "  Preparing Task Stage..."

        terminal._turn_starting = False
        terminal._state = reduce_terminal_state(
            TerminalViewState(),
            TurnSubmitted(1, "Preparing turn"),
        )
        assert terminal._activity_visible() is True
        monkeypatch.setattr("coquo.cli.terminal_app.time.monotonic", lambda: 100.0)
        asyncio.run(terminal._handle_event(PromptActivity(1, ProviderInvocationStarted(1, 24))))
        monkeypatch.setattr("coquo.cli.terminal_app.time.monotonic", lambda: 101.25)
        assert terminal._activity_line().value == ("  - Model round 1/24: starting · 1.2s...")
        asyncio.run(
            terminal._handle_event(
                PromptActivity(
                    1,
                    ProviderInvocationFinished(
                        1,
                        24,
                        ProviderInvocationOutcome.FINAL_TEXT,
                    ),
                )
            )
        )
        assert terminal._provider_invocation_started_at is None
        terminal._state = reduce_terminal_state(terminal._state, TurnFinished(1, "done"))
    assert terminal._activity_visible() is False


def test_persistent_application_persists_provider_round_boundaries_immediately(
    tmp_path: Path,
) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=True,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        asyncio.run(terminal._handle_event(TurnSubmitted(1)))
        asyncio.run(terminal._handle_event(PromptActivity(1, ProviderInvocationStarted(1, 24))))
        assert "Model round [1/24]: started" in stdout.getvalue()
        asyncio.run(
            terminal._handle_event(
                PromptActivity(
                    1,
                    ProviderInvocationFinished(
                        1,
                        24,
                        ProviderInvocationOutcome.TOOL_REQUEST,
                        1,
                        32_500,
                    ),
                )
            )
        )

    assert "Model round [1/24]: 1 tool request received (32.5s)" in stdout.getvalue()


def test_persistent_application_emits_provider_wait_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _HistorySession(),
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        monkeypatch.setattr("coquo.cli.terminal_app.time.monotonic", lambda: 100.0)
        asyncio.run(terminal._handle_event(TurnSubmitted(1)))
        asyncio.run(terminal._handle_event(PromptActivity(1, ProviderInvocationStarted(1, 24))))
        monkeypatch.setattr("coquo.cli.terminal_app.time.monotonic", lambda: 105.1)
        asyncio.run(terminal._emit_provider_wait_heartbeat())

    assert "Provider still waiting: model round [1/24] (5.1s)" in stdout.getvalue()


def test_persistent_application_commit_status_does_not_split_or_duplicate_stream(
    tmp_path: Path,
) -> None:
    session = _CommitEventSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("save\r")
        _wait_until(lambda: not terminal.state.busy and "saved reply" in stdout.getvalue())
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert stdout.getvalue().count("saved reply") == 1
    assert "\n• saved reply\n" in stdout.getvalue()


def test_persistent_application_refreshes_session_name_after_turn_and_rename(
    tmp_path: Path,
) -> None:
    session = _NamedSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        assert "New session 1" in terminal._toolbar().value
        thread = Thread(target=terminal.run)
        thread.start()

        pipe.send_text("Review provider adapters\r")
        _wait_until(lambda: not terminal.state.busy and session.name == "Review provider adapters")
        _wait_until(lambda: terminal._session_info.name == "Review provider adapters")
        assert "Review provider adapters" in terminal._toolbar().value

        pipe.send_text("/session rename Release review\r")
        _wait_until(lambda: terminal._session_info.name == "Release review")
        _wait_until(lambda: "Session name: Release review (manual)" in stdout.getvalue())
        assert "Release review" in terminal._toolbar().value
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    rendered = stdout.getvalue()
    slash_input = rendered.index("› /session rename Release review")
    slash_result = rendered.index("  Session name: Release review (manual)")
    slash_separator = rendered.index(f"  {'─' * 24}", slash_result)
    assert slash_input < slash_result < slash_separator
    assert rendered[slash_input:slash_separator].count("› /session rename") == 1


def test_persistent_application_keeps_failure_inside_turn_trace(tmp_path: Path) -> None:
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            _FailingSession(),
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("fail\r")
        _wait_until(lambda: f"  {'─' * 24}" in stdout.getvalue())
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    rendered = stdout.getvalue()
    assert "  │ Turn failed: RuntimeError: provider unavailable" in rendered
    assert rendered.index("  │ Turn failed") < rendered.index(f"  {'─' * 24}")


def test_persistent_application_runs_task_stage_through_shared_background_worker(
    tmp_path: Path,
) -> None:
    session = _TaskTerminalSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    task_id = "42345678-1234-4234-9234-123456789abc"
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text(f"/task continue {task_id} Implement one Stage\r")
        _wait_until(lambda: not terminal.state.busy and "Task Stage complete" in stdout.getvalue())
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert session.requests == [("continue", task_id, "Implement one Stage")]
    rendered = stdout.getvalue()
    assert f"› /task continue {task_id} Implement one Stage" in rendered
    assert rendered.count("Task Stage complete") == 1
    assert "\n• Task Stage complete\n" in rendered
    assert rendered.count(f"  {'─' * 24}") == 1


def test_persistent_task_run_keeps_all_nonstreamed_stage_responses(tmp_path: Path) -> None:
    session = _TaskTerminalSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    task_id = "42345678-1234-4234-9234-123456789abc"
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text(f"/task run {task_id} 2\r")
        _wait_until(
            lambda: not terminal.state.busy and "Second Stage complete" in stdout.getvalue()
        )
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert session.requests == [("run", task_id, 2)]
    rendered = stdout.getvalue()
    assert rendered.count("First Stage complete") == 1
    assert rendered.count("Second Stage complete") == 1
    assert rendered.index("First Stage complete") < rendered.index("Second Stage complete")


def test_persistent_application_natural_lifecycle_automatically_starts_foreground_driver(
    tmp_path: Path,
) -> None:
    session = _NaturalTaskSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    task_id = "42345678-1234-4234-9234-123456789abc"
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("计划没问题，继续\r")
        _wait_until(
            lambda: (
                not terminal.state.busy
                and not terminal._runner.busy
                and ("drive", task_id, 4) in session.requests
                and "Automatic Task Stage complete" in stdout.getvalue()
            )
        )
        time.sleep(0.1)
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert session.requests == [
        ("prompt", "计划没问题，继续"),
        ("drive", task_id, 4),
    ]
    rendered = stdout.getvalue()
    assert "Task plan accepted and committed" in rendered
    assert "Automatic Task Stage complete" in rendered
    assert "\n  Second Task line.\n" in rendered
    assert "/task" not in rendered


def test_persistent_application_clear_writes_terminal_reset_without_session_mutation(
    tmp_path: Path,
) -> None:
    session = _HistorySession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("/clear\r")
        _wait_until(lambda: CLEAR_SCREEN in stdout.getvalue())
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert session.prompts == []


def test_persistent_application_ctrl_r_searches_current_session_prompt_history(
    tmp_path: Path,
) -> None:
    session = _HistorySession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("\x12older")
        _wait_until(lambda: terminal._search_toolbar.control.buffer.text == "older")
        pipe.send_text("\r")
        _wait_until(lambda: terminal.draft == "older diagnostic prompt")
        pipe.send_text("\r")
        _wait_until(lambda: session.prompts == ["older diagnostic prompt"])
        _wait_until(lambda: not terminal.state.busy)
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()


def test_persistent_application_up_arrow_recalls_process_local_slash_command(
    tmp_path: Path,
) -> None:
    session = _HistorySession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("/help\r")
        _wait_until(lambda: "Host command groups:" in stdout.getvalue())
        pipe.send_text("\x1b[A")
        _wait_until(lambda: terminal.draft == "/help")
        pipe.send_text("\x03\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert session.prompts == []


def test_persistent_application_keeps_busy_draft_and_returns_to_idle(tmp_path: Path) -> None:
    session = _InteractiveSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("hello\r")
        assert session.started.wait(1)
        _wait_until(lambda: terminal.state.busy)

        pipe.send_text("next draft")
        _wait_until(lambda: terminal.draft == "next draft")
        pipe.send_text("\r")
        time.sleep(0.05)
        assert terminal.draft == "next draft"
        assert session.prompts == ["hello"]

        session.release.set()
        _wait_until(lambda: not terminal.state.busy)
        assert terminal.draft == "next draft"
        pipe.send_text("\r")
        _wait_until(lambda: session.prompts == ["hello", "next draft"])
        _wait_until(lambda: stdout.getvalue().count(f"  {'─' * 24}") == 2)
        assert not terminal.state.busy
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()
    assert "› hello" in stdout.getvalue()
    assert "› next draft" in stdout.getvalue()
    assert stdout.getvalue().count(f"  {'─' * 24}") == 2
    assert stdout.getvalue().count("• reply") == 2
    assert "\n• reply" in stdout.getvalue()
    assert stdout.getvalue().index("› hello") < stdout.getvalue().index("reply")


def test_persistent_application_ctrl_d_waits_for_turn_cancellation(tmp_path: Path) -> None:
    session = _InteractiveSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=io.StringIO(),
            cwd=tmp_path,
            color=False,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("cancel me\r")
        assert session.started.wait(1)
        _wait_until(lambda: terminal.state.busy)
        pipe.send_text("\x04")
        thread.join(2)

    assert not thread.is_alive()


class _ApprovalSession:
    def __init__(self) -> None:
        self.started = Event()
        self.request_approval = Event()
        self.broker = None
        self.resolution = None

    @property
    def turns(self):
        return ()

    def prompt(self, _text, *, event_sink, include_tool_details, cancellation):
        del include_tool_details, cancellation
        self.started.set()
        self.request_approval.wait(1)
        event_sink(ToolRequestStarted("write_file", 1, 32, "path='note.txt' content_bytes=6"))
        self.resolution = self.broker(_approval_request())
        event_sink(
            ToolRequestFinished(
                "write_file",
                1,
                32,
                ToolEventStatus.SUCCEEDED,
                "overwritten",
            )
        )
        event_sink(AssistantResponseTextDeltaReceived("approval complete"))
        event_sink(AssistantFinalTextStreamCommitted("approval complete"))
        return "approval complete"


@pytest.mark.parametrize("color", [False, True])
def test_persistent_application_approval_temporarily_owns_input_and_restores_draft(
    tmp_path: Path, color: bool
) -> None:
    session = _ApprovalSession()
    queue = FrontendEventQueue()
    broker = TerminalApprovalBroker(
        lambda turn_id, request: queue.put(ApprovalPending(turn_id, request))
    )
    session.broker = broker
    stdout = io.StringIO()
    with create_pipe_input() as pipe:
        terminal = TerminalApplication(
            session,
            stdout=stdout,
            cwd=tmp_path,
            color=color,
            render_markdown=False,
            queue=queue,
            approval_broker=broker,
            input=pipe,
            output=DummyOutput(),
        )
        thread = Thread(target=terminal.run)
        thread.start()
        pipe.send_text("do work\r")
        assert session.started.wait(1)
        pipe.send_text("keep this draft")
        _wait_until(lambda: terminal.draft == "keep this draft")
        session.request_approval.set()
        _wait_until(lambda: terminal.state.phase == TerminalPhase.APPROVAL)
        assert terminal.draft == ""

        pipe.send_text("y\r")
        _wait_until(lambda: not terminal.state.busy)
        assert session.resolution == ApprovalResolution.ACCEPT
        assert terminal.draft == "keep this draft"
        pipe.send_text("\x03\x04")
        thread.join(2)

    assert not thread.is_alive()
    rendered = stdout.getvalue()
    assert r"\x1b" not in rendered
    plain = ANSI_ESCAPE.sub("", rendered)
    assert "\n› do work\n\n  │ Turn 1: started\n  │ [tool 1/32] write_file" in plain
    assert "  │ Turn 1: committed" in plain
    assert "  │ [tool 1/32] write_file path='note.txt' content_bytes=6" in plain
    assert "  │ Approval required" in plain
    assert "  │ Prepared candidate (6 bytes):" in plain
    assert "  │ --- a/note.txt" in plain
    assert "  │ [tool 1/32] succeeded code=overwritten" in plain
    assert "\n• approval complete\n" in plain
    assert plain.count(f"  {'─' * 24}") == 1
    assert plain.index("[tool 1/32] write_file") < plain.index("• approval complete")
    assert plain.index("• approval complete") < plain.index(f"  {'─' * 24}")


def _approval_request() -> HumanApprovalRequest:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="write-1",
        tool_name="write_file",
        arguments=ToolArguments.from_mapping({"path": "note.txt", "content": "value\n"}),
        action=PermissionAction.WORKSPACE_OVERWRITE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.expected_state("3" * 64),
    )
    preview = build_file_change_preview(
        action_digest=identity.digest,
        path="note.txt",
        before=b"old\n",
        after=b"value\n",
    )
    return HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_OVERWRITE,
        ),
        preview,
    )


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")
