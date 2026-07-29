from __future__ import annotations

import io
from pathlib import Path
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from leonervis_code.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    ProviderInvocationPreflighted,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
)
from leonervis_code.cli.approval import TerminalApprovalBroker
from leonervis_code.cli.frontend import (
    ApprovalPending,
    CancellationRequested,
    FrontendEventQueue,
    PromptActivity,
    TerminalPhase,
    TerminalViewState,
    TurnFinished,
    TurnSubmitted,
    reduce_terminal_state,
)
from leonervis_code.cli.terminal_app import TerminalApplication
from leonervis_code.core.action_coordinator import (
    ApprovalResolution,
    HumanApprovalRequest,
)
from leonervis_code.core.approval_preview import build_file_change_preview
from leonervis_code.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled
from leonervis_code.core.contracts import ToolArguments
from leonervis_code.core.permissions import (
    PermissionAction,
    PermissionDecision,
    PermissionReason,
    PermissionResult,
)
from leonervis_code.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from leonervis_code.session import TurnCommitStarted


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
        PromptActivity(1, ProviderInvocationPreflighted(1, 24, report)),
    )
    assert state.status == "Preparing provider request"

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


def test_frontend_queue_coalesces_only_consecutive_text_deltas() -> None:
    queue = FrontendEventQueue(capacity=2)
    assert queue.put(PromptActivity(1, AssistantResponseTextDeltaReceived("a")))
    assert not queue.put(PromptActivity(1, AssistantResponseTextDeltaReceived("b")))
    queue.put(TurnFinished(1, "ab"))

    events = queue.drain()
    assert events == (
        PromptActivity(1, AssistantResponseTextDeltaReceived("ab")),
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


def test_persistent_application_approval_temporarily_owns_input_and_restores_draft(
    tmp_path: Path,
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
            color=False,
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
    assert "\n› do work\n\n  │ [tool 1/32] write_file" in rendered
    assert "  │ [tool 1/32] write_file path='note.txt' content_bytes=6" in rendered
    assert "  │ Approval required" in rendered
    assert "  │ Prepared candidate (6 bytes):" in rendered
    assert "  │ --- a/note.txt" in rendered
    assert "  │ [tool 1/32] succeeded code=overwritten" in rendered
    assert "\n• approval complete\n" in rendered
    assert rendered.count(f"  {'─' * 24}") == 1
    assert rendered.index("[tool 1/32] write_file") < rendered.index("• approval complete")
    assert rendered.index("• approval complete") < rendered.index(f"  {'─' * 24}")


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
