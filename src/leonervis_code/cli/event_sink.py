"""Reusable stream sink for safe ephemeral prompt lifecycle events."""

from __future__ import annotations

from typing import TextIO

from leonervis_code.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextReceived,
    AssistantToolTextStreamCompleted,
)
from leonervis_code.cli.markdown_renderer import (
    DEFAULT_TERMINAL_WIDTH,
    TerminalMarkdownRenderer,
    escape_terminal_controls,
    render_plain_document,
    write_markdown_document,
)
from leonervis_code.cli.presentation import (
    ToolDetailMode,
    render_assistant_prefix,
    render_host_message,
    render_message_separator,
    render_message,
    render_prompt_event,
)


class TerminalEventSink:
    """Render one prompt event as a stable flushed terminal line."""

    def __init__(
        self,
        stream: TextIO,
        *,
        color: bool,
        stream_deltas: bool = True,
        render_markdown: bool = False,
        show_role_markers: bool = False,
        show_waiting: bool = False,
        tool_detail_mode: ToolDetailMode = ToolDetailMode.COMPACT,
        markdown_width: int | None = None,
    ) -> None:
        self._stream = stream
        self._color = color
        self._markdown_width = markdown_width
        self._stream_deltas = stream_deltas
        self._response_parts: list[str] = []
        self._visible_response = False
        self._final_text_was_streamed = False
        if show_role_markers:
            resolved_width = DEFAULT_TERMINAL_WIDTH if markdown_width is None else markdown_width
            self._assistant_prefix = (
                f"\n{render_message_separator(resolved_width, color=color)}\n"
                f"{render_assistant_prefix(color=color)}"
            )
        else:
            self._assistant_prefix = ""
        self._continuation_prefix = "  " if show_role_markers else ""
        self._prefix_width = 2 if show_role_markers else 0
        self._markdown = (
            TerminalMarkdownRenderer(
                stream,
                color=color,
                width=markdown_width,
                first_prefix=self._assistant_prefix,
                continuation_prefix=self._continuation_prefix,
                prefix_width=self._prefix_width,
            )
            if render_markdown
            else None
        )
        self._show_role_markers = show_role_markers
        self._show_waiting = show_waiting
        self._waiting_visible = False
        self._assistant_output_active = False
        if type(tool_detail_mode) is not ToolDetailMode:
            raise ValueError("tool detail mode is invalid")
        self._tool_detail_mode = tool_detail_mode

    @property
    def final_text_was_streamed(self) -> bool:
        return self._final_text_was_streamed

    def start_waiting(self) -> None:
        """Show one ephemeral pre-event status without changing runtime behavior."""
        if not self._show_waiting or self._waiting_visible:
            return
        try:
            message = "Working..."
            self._stream.write(
                render_host_message(message, "info", color=self._color)
                if self._show_role_markers
                else render_message(message, "info", color=self._color)
            )
            self._stream.flush()
        except Exception:
            return
        self._waiting_visible = True

    def begin_final_output(self) -> None:
        """Resolve waiting and mark a non-streamed final assistant response."""
        self._clear_waiting()
        if self._markdown is None:
            self._begin_assistant_output()

    def write_final_text(self, text: str) -> None:
        """Render one complete final response through the same role boundary."""
        self._clear_waiting()
        self._write_complete_assistant(text)

    def __call__(self, event: object) -> None:
        if isinstance(event, AssistantToolTextReceived):
            self._write_complete_assistant(event.text)
            self._assistant_output_active = False
            return
        if isinstance(event, AssistantResponseTextDeltaReceived):
            if self._markdown is None:
                self._begin_assistant_output()
            else:
                self._clear_waiting()
            self._response_parts.append(event.text)
            if self._stream_deltas:
                if self._markdown is None:
                    self._stream.write(escape_terminal_controls(event.text))
                    self._stream.flush()
                    self._visible_response = True
                elif self._markdown.push(event.text):
                    self._visible_response = True
            return
        if isinstance(event, AssistantToolTextStreamCompleted):
            self._resolve_stream(event.text, companion=True)
            return
        if isinstance(event, AssistantFinalTextStreamCommitted):
            self._resolve_stream(event.text, companion=False)
            self._final_text_was_streamed = True
            return
        self._clear_waiting()
        message, kind = render_prompt_event(event, tool_detail_mode=self._tool_detail_mode)
        self._stream.write(
            render_host_message(message, kind, color=self._color)
            if self._show_role_markers
            else render_message(message, kind, color=self._color)
        )
        if not message.endswith("\n"):
            self._stream.write("\n")
        self._stream.flush()

    def abort_stream(self) -> bool:
        """Discard hidden deltas or terminate a visible partial response line."""
        self._clear_waiting()
        had_partial = bool(self._response_parts)
        if self._visible_response and self._response_parts:
            if self._markdown is None and not "".join(self._response_parts).endswith("\n"):
                self._stream.write("\n")
            self._stream.flush()
        if self._markdown is not None:
            self._markdown.abort()
        self._response_parts.clear()
        self._visible_response = False
        self._assistant_output_active = False
        return had_partial

    def _resolve_stream(self, text: str, *, companion: bool) -> None:
        collected = "".join(self._response_parts)
        if collected != text:
            raise ValueError("terminal stream text does not match the completed response")
        if companion and not self._stream_deltas:
            self._write_complete_assistant(text)
            if self._markdown is not None:
                self._markdown.reset()
        elif self._stream_deltas:
            if self._markdown is None:
                if not text.endswith("\n"):
                    self._stream.write("\n")
                    self._stream.flush()
            else:
                self._markdown.flush()
                self._markdown.reset()
        self._response_parts.clear()
        self._visible_response = False
        self._assistant_output_active = False

    def _begin_assistant_output(self) -> None:
        self._clear_waiting()
        if not self._show_role_markers or self._assistant_output_active:
            return
        self._stream.write(self._assistant_prefix)
        self._stream.flush()
        self._assistant_output_active = True

    def _write_complete_assistant(self, text: str) -> None:
        self._clear_waiting()
        if self._markdown is not None:
            write_markdown_document(
                self._stream,
                text,
                color=self._color,
                width=self._markdown_width,
                first_prefix=self._assistant_prefix,
                continuation_prefix=self._continuation_prefix,
                prefix_width=self._prefix_width,
            )
            return
        if self._show_role_markers:
            self._stream.write(
                render_plain_document(
                    text,
                    width=(
                        DEFAULT_TERMINAL_WIDTH
                        if self._markdown_width is None
                        else self._markdown_width
                    ),
                    first_prefix=self._assistant_prefix,
                    continuation_prefix=self._continuation_prefix,
                    prefix_width=self._prefix_width,
                )
            )
            self._stream.flush()
            return
        safe_text = escape_terminal_controls(text)
        self._stream.write(safe_text if safe_text.endswith("\n") else f"{safe_text}\n")
        self._stream.flush()

    def _clear_waiting(self) -> None:
        if not self._waiting_visible:
            return
        self._stream.write("\r\x1b[2K")
        self._stream.flush()
        self._waiting_visible = False
