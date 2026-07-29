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
    TerminalMarkdownRenderer,
    write_markdown_document,
)
from leonervis_code.cli.presentation import ToolDetailMode, render_message, render_prompt_event


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
    ) -> None:
        self._stream = stream
        self._color = color
        self._stream_deltas = stream_deltas
        self._response_parts: list[str] = []
        self._visible_response = False
        self._final_text_was_streamed = False
        self._markdown = TerminalMarkdownRenderer(stream, color=color) if render_markdown else None
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
            self._stream.write(render_message("• Working...", "info", color=self._color))
            self._stream.flush()
        except Exception:
            return
        self._waiting_visible = True

    def begin_final_output(self) -> None:
        """Resolve waiting and mark a non-streamed final assistant response."""
        self._begin_assistant_output()

    def __call__(self, event: object) -> None:
        if isinstance(event, AssistantToolTextReceived):
            self._begin_assistant_output()
            if self._markdown is None:
                self._stream.write(event.text)
                if not event.text.endswith("\n"):
                    self._stream.write("\n")
                self._stream.flush()
            else:
                write_markdown_document(self._stream, event.text, color=self._color)
            self._assistant_output_active = False
            return
        if isinstance(event, AssistantResponseTextDeltaReceived):
            self._begin_assistant_output()
            self._response_parts.append(event.text)
            if self._stream_deltas:
                if self._markdown is None:
                    self._stream.write(event.text)
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
        self._stream.write(render_message(message, kind, color=self._color))
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
            if self._markdown is None:
                self._stream.write(text)
                if not text.endswith("\n"):
                    self._stream.write("\n")
                self._stream.flush()
            else:
                write_markdown_document(self._stream, text, color=self._color)
        elif self._stream_deltas:
            if self._markdown is None:
                if not text.endswith("\n"):
                    self._stream.write("\n")
                    self._stream.flush()
            else:
                self._markdown.flush()
        self._response_parts.clear()
        self._visible_response = False
        self._assistant_output_active = False

    def _begin_assistant_output(self) -> None:
        self._clear_waiting()
        if not self._show_role_markers or self._assistant_output_active:
            return
        marker = render_message("•", "success", color=self._color)
        self._stream.write(f"{marker} ")
        self._stream.flush()
        self._assistant_output_active = True

    def _clear_waiting(self) -> None:
        if not self._waiting_visible:
            return
        self._stream.write("\r\x1b[2K")
        self._stream.flush()
        self._waiting_visible = False
