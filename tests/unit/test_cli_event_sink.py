from __future__ import annotations

import io

from coquo.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextStreamCompleted,
    AssistantToolTextReceived,
    ProviderInvocationFinished,
    ProviderInvocationOutcome,
    ProviderInvocationStarted,
    TaskAdmissionProposed,
    TaskLifecycleCommitted,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
    ToolResultDetails,
)
from coquo.cli.event_sink import TerminalEventSink
from coquo.cli.presentation import DIM, GREEN, RESET, ToolDetailMode


class FlushingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def test_terminal_event_sink_writes_and_flushes_one_stable_line() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False)

    sink(ToolRequestFinished("read_file", 1, 6, ToolEventStatus.SUCCEEDED, "ok"))

    assert stream.getvalue() == "[tool 1/6] succeeded code=ok\n"
    assert stream.flush_count == 1


def test_terminal_event_sink_flushes_provider_round_boundaries_immediately() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False)

    sink(ProviderInvocationStarted(1, 24))
    sink(
        ProviderInvocationFinished(
            1,
            24,
            ProviderInvocationOutcome.TOOL_REQUEST,
            1,
        )
    )

    assert stream.getvalue() == (
        "Model round [1/24]: started\nModel round [1/24]: 1 tool request received\n"
    )
    assert stream.flush_count == 2


def test_terminal_event_sink_can_keep_non_tty_provider_rounds_quiet() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False, show_provider_rounds=False)

    sink(ProviderInvocationStarted(1, 24))
    sink(
        ProviderInvocationFinished(
            1,
            24,
            ProviderInvocationOutcome.FINAL_TEXT,
        )
    )

    assert stream.getvalue() == ""
    assert stream.flush_count == 0


def test_terminal_event_sink_announces_one_committed_task_admission() -> None:
    stream = FlushingStream()

    TerminalEventSink(stream, color=False)(
        TaskAdmissionProposed("tap-v1-" + "a" * 64, "Build a durable feature", 2)
    )

    rendered = stream.getvalue()
    assert "Task admission proposal committed:" in rendered
    assert "Build a durable feature" in rendered
    assert "Reply naturally when you want to accept it" in rendered
    assert "/task proposal accept" not in rendered


def test_terminal_event_sink_announces_natural_task_lifecycle_commit() -> None:
    stream = FlushingStream()

    TerminalEventSink(stream, color=False)(
        TaskLifecycleCommitted(
            "accept-plan",
            "12345678-1234-4234-9234-123456789abc",
            16,
        )
    )

    rendered = stream.getvalue()
    assert "Task plan accepted and committed" in rendered
    assert "Continuing in the foreground" in rendered
    assert "/task" not in rendered


def test_terminal_event_sink_uses_existing_semantic_colors() -> None:
    stream = io.StringIO()

    TerminalEventSink(stream, color=True)(
        ToolRequestFinished("read_file", 1, 6, ToolEventStatus.SUCCEEDED)
    )

    assert stream.getvalue() == f"{GREEN}[tool 1/6] succeeded{RESET}\n"


def test_terminal_event_sink_full_mode_renders_structured_command_details() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False, tool_detail_mode=ToolDetailMode.FULL)

    sink(
        ToolRequestStarted(
            "run_command",
            1,
            32,
            "command='uv' args=2 cwd='.' timeout=30s",
            ('argv: ["uv","run","pytest"]', "cwd: '.'", "timeout_seconds: 30"),
        )
    )

    assert stream.getvalue() == (
        "[tool 1/32] run_command\n"
        '  argv: ["uv","run","pytest"]\n'
        "  cwd: '.'\n"
        "  timeout_seconds: 30\n"
    )
    assert stream.flush_count == 1


def test_terminal_event_sink_full_mode_renders_command_result_metadata_only() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False, tool_detail_mode=ToolDetailMode.FULL)
    details = ToolResultDetails(
        "exit=7 duration=4ms stdout=6B stderr=10B",
        (
            "status: exited",
            "exit_code: 7",
            "duration_ms: 4",
            "stdout: captured=6 total=6 truncated=false",
            "stderr: captured=10 total=10 truncated=false",
            "cleanup_complete: true",
        ),
    )

    sink(
        ToolRequestFinished(
            "run_command",
            1,
            32,
            ToolEventStatus.FAILED,
            "command_exited_nonzero",
            result_details=details,
        )
    )

    assert stream.getvalue() == (
        "[tool 1/32] failed code=command_exited_nonzero\n"
        "  status: exited\n"
        "  exit_code: 7\n"
        "  duration_ms: 4\n"
        "  stdout: captured=6 total=6 truncated=false\n"
        "  stderr: captured=10 total=10 truncated=false\n"
        "  cleanup_complete: true\n"
        "Next: inspect the reported stdout, stderr, and exit code before changing or rerunning "
        "the command.\n"
    )
    assert stream.flush_count == 1


def test_terminal_event_sink_preserves_companion_text_with_one_terminating_newline() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False)

    sink(AssistantToolTextReceived("I will inspect."))
    sink(AssistantToolTextReceived("First line\nSecond line\n"))

    assert stream.getvalue() == "I will inspect.\nFirst line\nSecond line\n"
    assert stream.flush_count == 2


def test_terminal_event_sink_streams_repl_text_and_does_not_duplicate_final() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False)

    sink(AssistantResponseTextDeltaReceived("First\n"))
    sink(AssistantResponseTextDeltaReceived("second"))
    sink(AssistantFinalTextStreamCommitted("First\nsecond"))

    assert stream.getvalue() == "First\nsecond\n"
    assert sink.final_text_was_streamed is True


def test_terminal_event_sink_buffers_one_shot_final_and_flushes_only_companion_text() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False, stream_deltas=False)

    sink(AssistantResponseTextDeltaReceived("I will "))
    sink(AssistantResponseTextDeltaReceived("inspect."))
    sink(AssistantToolTextStreamCompleted("I will inspect."))
    sink(AssistantResponseTextDeltaReceived("final"))
    sink(AssistantFinalTextStreamCommitted("final"))

    assert stream.getvalue() == "I will inspect.\n"
    assert sink.final_text_was_streamed is True


def test_terminal_event_sink_abort_discards_hidden_or_terminates_visible_partial_text() -> None:
    hidden_stream = FlushingStream()
    hidden = TerminalEventSink(hidden_stream, color=False, stream_deltas=False)
    hidden(AssistantResponseTextDeltaReceived("secret partial"))

    visible_stream = FlushingStream()
    visible = TerminalEventSink(visible_stream, color=False)
    visible(AssistantResponseTextDeltaReceived("visible partial"))

    assert hidden.abort_stream() is True
    assert hidden_stream.getvalue() == ""
    assert visible.abort_stream() is True
    assert visible_stream.getvalue() == "visible partial\n"
    assert visible.abort_stream() is False


def test_terminal_event_sink_renders_streamed_markdown_at_safe_boundaries() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False, render_markdown=True)

    sink(AssistantResponseTextDeltaReceived("# Result\n"))
    assert stream.getvalue() == ""
    sink(AssistantResponseTextDeltaReceived("\n- one\n- two"))
    assert "Result" in stream.getvalue()
    sink(AssistantFinalTextStreamCommitted("# Result\n\n- one\n- two"))

    rendered = stream.getvalue()
    assert "# Result" not in rendered
    assert " • one" in rendered
    assert rendered.count("Result") == 1


def test_hidden_stream_renders_only_completed_tool_companion_markdown() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(
        stream,
        color=False,
        stream_deltas=False,
        render_markdown=True,
    )

    sink(AssistantResponseTextDeltaReceived("**Inspecting**"))
    assert stream.getvalue() == ""
    sink(AssistantToolTextStreamCompleted("**Inspecting**"))

    assert "Inspecting" in stream.getvalue()
    assert "**" not in stream.getvalue()


def test_nonstream_tool_companion_uses_the_same_markdown_renderer() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False, render_markdown=True)

    sink(AssistantToolTextReceived("**Inspecting** `app.py`"))

    assert "Inspecting app.py" in stream.getvalue()
    assert "**" not in stream.getvalue()
    assert "`" not in stream.getvalue()


def test_tty_feedback_replaces_waiting_with_plain_assistant_role_markers() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(
        stream,
        color=False,
        show_role_markers=True,
        show_waiting=True,
    )

    sink.start_waiting()
    sink(AssistantToolTextReceived("I will inspect."))
    sink(ToolRequestFinished("read_file", 1, 6, ToolEventStatus.SUCCEEDED, "ok"))
    sink.begin_final_output()
    stream.write("Done.\n")

    assert stream.getvalue() == (
        "  Working...\r\x1b[2K\n• I will inspect.\n  │ [tool 1/6] succeeded code=ok\n\n• Done.\n"
    )


def test_tty_feedback_marks_streamed_markdown_without_rendering_marker_as_a_list() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(
        stream,
        color=False,
        render_markdown=True,
        show_role_markers=True,
        show_waiting=True,
    )

    sink.start_waiting()
    sink(AssistantResponseTextDeltaReceived("**Done**"))
    sink(AssistantFinalTextStreamCommitted("**Done**"))

    rendered = stream.getvalue()
    assert rendered.startswith("  Working...\r\x1b[2K\n• Done")
    assert "Done" in rendered
    assert "**" not in rendered


def test_tty_feedback_keeps_nonstream_markdown_final_on_the_role_marker_line() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(
        stream,
        color=False,
        render_markdown=True,
        show_role_markers=True,
        show_waiting=True,
    )

    sink.start_waiting()
    sink.write_final_text("**HISTORY_TEST_OK**")

    assert stream.getvalue() == "  Working...\r\x1b[2K\n• HISTORY_TEST_OK\n"


def test_tty_markdown_defers_role_marker_until_visible_text_is_ready() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(
        stream,
        color=False,
        render_markdown=True,
        show_role_markers=True,
    )

    sink(AssistantResponseTextDeltaReceived("**Done**"))
    assert stream.getvalue() == ""

    sink(AssistantFinalTextStreamCommitted("**Done**"))
    assert stream.getvalue().startswith("\n• Done")


def test_tty_host_events_are_indented_and_muted_without_weakening_errors() -> None:
    success_stream = io.StringIO()
    TerminalEventSink(success_stream, color=True, show_role_markers=True)(
        ToolRequestFinished("read_file", 1, 6, ToolEventStatus.SUCCEEDED)
    )
    assert success_stream.getvalue() == (f"{DIM}{GREEN}  │ [tool 1/6] succeeded{RESET}\n")

    error_stream = io.StringIO()
    TerminalEventSink(error_stream, color=False, show_role_markers=True)(
        ToolRequestFinished("read_file", 1, 6, ToolEventStatus.FAILED, "failed")
    )
    assert error_stream.getvalue() == "  │ [tool 1/6] failed code=failed\n"


def test_default_sink_contract_has_no_waiting_or_role_marker() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False)

    sink.start_waiting()
    sink(AssistantToolTextReceived("Inspecting."))
    sink.begin_final_output()
    stream.write("Done.\n")

    assert stream.getvalue() == "Inspecting.\nDone.\n"
