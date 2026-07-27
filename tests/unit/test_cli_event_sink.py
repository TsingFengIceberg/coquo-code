from __future__ import annotations

import io

from leonervis_code.agent.tool_events import (
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
