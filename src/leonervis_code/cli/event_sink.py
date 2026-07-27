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
from leonervis_code.cli.presentation import render_message, render_prompt_event


class TerminalEventSink:
    """Render one prompt event as a stable flushed terminal line."""

    def __init__(
        self,
        stream: TextIO,
        *,
        color: bool,
        stream_deltas: bool = True,
        render_markdown: bool = False,
    ) -> None:
        self._stream = stream
        self._color = color
        self._stream_deltas = stream_deltas
        self._response_parts: list[str] = []
        self._visible_response = False
        self._final_text_was_streamed = False
        self._markdown = TerminalMarkdownRenderer(stream, color=color) if render_markdown else None

    @property
    def final_text_was_streamed(self) -> bool:
        return self._final_text_was_streamed

    def __call__(self, event: object) -> None:
        if isinstance(event, AssistantToolTextReceived) and self._markdown is not None:
            write_markdown_document(self._stream, event.text, color=self._color)
            return
        if isinstance(event, AssistantResponseTextDeltaReceived):
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
        message, kind = render_prompt_event(event)
        self._stream.write(render_message(message, kind, color=self._color))
        if not message.endswith("\n"):
            self._stream.write("\n")
        self._stream.flush()

    def abort_stream(self) -> bool:
        """Discard hidden deltas or terminate a visible partial response line."""
        had_partial = bool(self._response_parts)
        if self._visible_response and self._response_parts:
            if self._markdown is None and not "".join(self._response_parts).endswith("\n"):
                self._stream.write("\n")
            self._stream.flush()
        if self._markdown is not None:
            self._markdown.abort()
        self._response_parts.clear()
        self._visible_response = False
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
