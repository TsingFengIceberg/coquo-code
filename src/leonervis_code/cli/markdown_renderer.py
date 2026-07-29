"""TTY-only Markdown rendering for untrusted assistant text."""

from __future__ import annotations

import io
import os
from typing import TextIO
import unicodedata

from rich.console import Console
from rich.markdown import Markdown

DEFAULT_TERMINAL_WIDTH = 100
MIN_TERMINAL_WIDTH = 40
MAX_TERMINAL_WIDTH = 240


class TerminalMarkdownRenderer:
    """Render complete Markdown prefixes while retaining an incomplete stream suffix."""

    def __init__(
        self,
        stream: TextIO,
        *,
        color: bool,
        width: int | None = None,
    ) -> None:
        self._stream = stream
        self._color = color
        self._width = _terminal_width(stream) if width is None else _validate_width(width)
        self._pending = ""

    def push(self, delta: str) -> bool:
        """Buffer one exact delta and render its largest stream-safe prefix."""
        self._pending += delta
        boundary = _stream_safe_boundary(self._pending)
        if boundary is None:
            return False
        ready = self._pending[:boundary]
        self._pending = self._pending[boundary:]
        return self._render(ready)

    def flush(self) -> bool:
        """Render the remaining suffix after its response is known to be complete."""
        if not self._pending:
            return False
        pending = self._pending
        self._pending = ""
        return self._render(pending)

    def render_complete(self, markdown: str) -> bool:
        """Render one complete document without splitting its block relationships."""
        if self._pending:
            raise ValueError("cannot render a complete document while a stream is pending")
        return self._render(markdown)

    def abort(self) -> None:
        """Discard an incomplete suffix without presenting it as completed Markdown."""
        self._pending = ""

    def _render(self, markdown: str) -> bool:
        if not markdown:
            return False
        safe_markdown = escape_terminal_controls(markdown)
        rendered = _render_to_ansi(
            safe_markdown,
            color=self._color,
            width=self._width,
        )
        if not rendered:
            rendered = safe_markdown
            if not rendered.endswith("\n"):
                rendered += "\n"
        self._stream.write(rendered)
        self._stream.flush()
        return True


def write_markdown_document(
    stream: TextIO,
    markdown: str,
    *,
    color: bool,
    width: int | None = None,
) -> None:
    """Render one complete Markdown document and terminate its terminal line."""
    selected_width = _terminal_width(stream) if width is None else _validate_width(width)
    stream.write(render_markdown_document(markdown, color=color, width=selected_width))
    stream.flush()


def render_markdown_document(
    markdown: str,
    *,
    color: bool,
    width: int | None = None,
) -> str:
    """Purely render one complete untrusted Markdown document to safe terminal text."""
    selected_width = DEFAULT_TERMINAL_WIDTH if width is None else _validate_width(width)
    safe_markdown = escape_terminal_controls(markdown)
    rendered = _render_to_ansi(safe_markdown, color=color, width=selected_width)
    if rendered:
        return rendered
    return safe_markdown if safe_markdown.endswith("\n") else f"{safe_markdown}\n"


def escape_terminal_controls(text: str) -> str:
    """Make untrusted terminal controls visible while preserving Markdown and Unicode."""
    escaped: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character in {"\n", "\t"}:
            escaped.append(character)
        elif unicodedata.category(character) not in {"Cc", "Cf", "Zl", "Zp"}:
            escaped.append(character)
        elif codepoint <= 0xFF:
            escaped.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(f"\\U{codepoint:08x}")
    return "".join(escaped)


def _render_to_ansi(markdown: str, *, color: bool, width: int) -> str:
    output = io.StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        color_system="standard" if color else None,
        no_color=not color,
        width=width,
        markup=False,
        emoji=False,
        highlight=False,
    )
    console.print(
        Markdown(markdown, code_theme="monokai", hyperlinks=False),
        soft_wrap=True,
    )
    return output.getvalue()


def _terminal_width(stream: TextIO) -> int:
    try:
        width = os.get_terminal_size(stream.fileno()).columns
    except (AttributeError, OSError, ValueError):
        width = DEFAULT_TERMINAL_WIDTH
    return max(MIN_TERMINAL_WIDTH, min(width, MAX_TERMINAL_WIDTH))


def _validate_width(width: int) -> int:
    if type(width) is not int or not MIN_TERMINAL_WIDTH <= width <= MAX_TERMINAL_WIDTH:
        raise ValueError("terminal Markdown width is out of range")
    return width


def _stream_safe_boundary(markdown: str) -> int | None:
    """Return the last complete blank-line or fenced-block boundary."""
    open_fence: tuple[str, int] | None = None
    last_boundary: int | None = None
    cursor = 0

    for line in markdown.splitlines(keepends=True):
        cursor += len(line)
        if not line.endswith(("\n", "\r")):
            continue
        content = line.rstrip("\r\n")
        marker = _fence_marker(content)
        if open_fence is not None:
            if marker is not None and marker[0] == open_fence[0] and marker[1] >= open_fence[1]:
                if marker[2].strip() == "":
                    open_fence = None
                    last_boundary = cursor
            continue
        if marker is not None:
            open_fence = (marker[0], marker[1])
            continue
        if not content.strip():
            last_boundary = cursor

    return last_boundary


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    content = line[indent:]
    if not content or content[0] not in {"`", "~"}:
        return None
    character = content[0]
    length = len(content) - len(content.lstrip(character))
    if length < 3:
        return None
    remainder = content[length:]
    if character == "`" and "`" in remainder:
        return None
    return character, length, remainder
