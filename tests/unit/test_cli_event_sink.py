from __future__ import annotations

import io

from leonervis_code.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextStreamCompleted,
    AssistantToolTextReceived,
    ToolEventStatus,
    ToolRequestFinished,
)
from leonervis_code.cli.event_sink import TerminalEventSink
from leonervis_code.cli.presentation import GREEN, RESET


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
