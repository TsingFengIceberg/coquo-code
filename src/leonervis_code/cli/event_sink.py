"""Reusable stream sink for safe ephemeral prompt lifecycle events."""

from __future__ import annotations

from typing import TextIO

from leonervis_code.cli.presentation import render_message, render_prompt_event


class TerminalEventSink:
    """Render one prompt event as a stable flushed terminal line."""

    def __init__(self, stream: TextIO, *, color: bool) -> None:
        self._stream = stream
        self._color = color

    def __call__(self, event: object) -> None:
        message, kind = render_prompt_event(event)
        self._stream.write(f"{render_message(message, kind, color=self._color)}\n")
        self._stream.flush()
