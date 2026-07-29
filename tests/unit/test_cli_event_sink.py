from __future__ import annotations

import io

from leonervis_code.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextStreamCompleted,
    AssistantToolTextReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
    ToolResultDetails,
)
from leonervis_code.cli.event_sink import TerminalEventSink
from leonervis_code.cli.markdown_renderer import write_markdown_document
from leonervis_code.cli.presentation import GREEN, RESET, ToolDetailMode


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
        "• Working...\r\x1b[2K• I will inspect.\n[tool 1/6] succeeded code=ok\n• Done.\n"
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
    assert rendered.startswith("• Working...\r\x1b[2K• Done")
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
    sink.begin_final_output()
    write_markdown_document(stream, "**HISTORY_TEST_OK**", color=False)

    assert stream.getvalue() == "• Working...\r\x1b[2K• HISTORY_TEST_OK\n"


def test_default_sink_contract_has_no_waiting_or_role_marker() -> None:
    stream = FlushingStream()
    sink = TerminalEventSink(stream, color=False)

    sink.start_waiting()
    sink(AssistantToolTextReceived("Inspecting."))
    sink.begin_final_output()
    stream.write("Done.\n")

    assert stream.getvalue() == "Inspecting.\nDone.\n"
