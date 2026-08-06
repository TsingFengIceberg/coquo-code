"""TTY-only Markdown rendering for untrusted assistant text."""

from __future__ import annotations

import io
import os
import re
from typing import TextIO
import unicodedata

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

DEFAULT_TERMINAL_WIDTH = 100
MIN_TERMINAL_WIDTH = 40
MAX_TERMINAL_WIDTH = 240
MAX_AUTOMATIC_CONTENT_WIDTH = 100
TERMINAL_RIGHT_MARGIN = 1
_TRAILING_RENDER_PADDING = re.compile(r"[ \t]+((?:\x1b\[[0-?]*[ -/]*[@-~])*)(\r?\n)")


class TerminalMarkdownRenderer:
    """Render complete Markdown prefixes while retaining an incomplete stream suffix."""

    def __init__(
        self,
        stream: TextIO,
        *,
        color: bool,
        width: int | None = None,
        first_prefix: str = "",
        continuation_prefix: str = "",
        prefix_width: int = 0,
    ) -> None:
        self._stream = stream
        self._color = color
        self._width = (
            terminal_stream_content_width(stream) if width is None else _validate_width(width)
        )
        self._first_prefix, self._continuation_prefix, self._prefix_width = _validate_prefixes(
            first_prefix,
            continuation_prefix,
            prefix_width,
            self._width,
        )
        self._pending = ""
        self._started = False

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

    def resize(self, width: int) -> None:
        """Apply the latest terminal width without discarding buffered stream text."""
        selected_width = _validate_width(width)
        _validate_prefixes(
            self._first_prefix,
            self._continuation_prefix,
            self._prefix_width,
            selected_width,
        )
        self._width = selected_width

    def abort(self) -> None:
        """Discard an incomplete suffix without presenting it as completed Markdown."""
        self._pending = ""
        self._started = False

    def reset(self) -> None:
        """Start a new assistant document after one stream completes."""
        self._pending = ""
        self._started = False

    def _render(self, markdown: str) -> bool:
        if not markdown:
            return False
        safe_markdown = escape_terminal_controls(markdown)
        rendered = _render_to_ansi(
            safe_markdown,
            color=self._color,
            width=self._width - self._prefix_width,
        )
        if not rendered:
            rendered = safe_markdown
            if not rendered.endswith("\n"):
                rendered += "\n"
        first_prefix = self._continuation_prefix if self._started else self._first_prefix
        self._stream.write(_apply_hanging_prefix(rendered, first_prefix, self._continuation_prefix))
        self._stream.flush()
        self._started = True
        return True


def write_markdown_document(
    stream: TextIO,
    markdown: str,
    *,
    color: bool,
    width: int | None = None,
    first_prefix: str = "",
    continuation_prefix: str = "",
    prefix_width: int = 0,
) -> None:
    """Render one complete Markdown document and terminate its terminal line."""
    selected_width = (
        terminal_stream_content_width(stream) if width is None else _validate_width(width)
    )
    stream.write(
        render_markdown_document(
            markdown,
            color=color,
            width=selected_width,
            first_prefix=first_prefix,
            continuation_prefix=continuation_prefix,
            prefix_width=prefix_width,
        )
    )
    stream.flush()


def render_markdown_document(
    markdown: str,
    *,
    color: bool,
    width: int | None = None,
    first_prefix: str = "",
    continuation_prefix: str = "",
    prefix_width: int = 0,
) -> str:
    """Purely render one complete untrusted Markdown document to safe terminal text."""
    selected_width = DEFAULT_TERMINAL_WIDTH if width is None else _validate_width(width)
    first_prefix, continuation_prefix, prefix_width = _validate_prefixes(
        first_prefix,
        continuation_prefix,
        prefix_width,
        selected_width,
    )
    safe_markdown = escape_terminal_controls(markdown)
    rendered = _render_to_ansi(
        safe_markdown,
        color=color,
        width=selected_width - prefix_width,
    )
    if not rendered:
        rendered = safe_markdown if safe_markdown.endswith("\n") else f"{safe_markdown}\n"
    return _apply_hanging_prefix(rendered, first_prefix, continuation_prefix)


def render_plain_document(
    text: str,
    *,
    width: int,
    first_prefix: str = "",
    continuation_prefix: str = "",
    prefix_width: int = 0,
) -> str:
    """Render escaped plain text with display-width wrapping and a hanging prefix."""
    selected_width = _validate_width(width)
    first_prefix, continuation_prefix, prefix_width = _validate_prefixes(
        first_prefix,
        continuation_prefix,
        prefix_width,
        selected_width,
    )
    body_width = selected_width - prefix_width
    safe_text = escape_terminal_controls(text)
    logical_lines = safe_text.split("\n")
    if logical_lines and logical_lines[-1] == "":
        logical_lines.pop()
    if not logical_lines:
        logical_lines = [""]
    console = Console(
        width=body_width,
        height=25,
        markup=False,
        emoji=False,
        highlight=False,
    )
    visual_lines: list[str] = []
    for logical_line in logical_lines:
        wrapped = Text(logical_line).wrap(console, body_width, overflow="fold", no_wrap=False)
        visual_lines.extend(line.plain for line in wrapped or [Text("")])
    rendered = "\n".join(visual_lines) + "\n"
    return _apply_hanging_prefix(rendered, first_prefix, continuation_prefix)


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
        height=25,
        markup=False,
        emoji=False,
        highlight=False,
    )
    console.print(
        Markdown(markdown, code_theme="monokai", hyperlinks=False),
        soft_wrap=False,
        overflow="fold",
    )
    return _TRAILING_RENDER_PADDING.sub(r"\1\2", output.getvalue())


def terminal_stream_content_width(stream: TextIO) -> int:
    """Return the bounded persistent-output width for one terminal stream."""
    try:
        columns = os.get_terminal_size(stream.fileno()).columns
    except (AttributeError, OSError, ValueError):
        columns = DEFAULT_TERMINAL_WIDTH + TERMINAL_RIGHT_MARGIN
    return terminal_content_width(columns)


def terminal_content_width(columns: int) -> int:
    """Choose a readable hard-wrap width below the physical terminal edge."""
    if type(columns) is not int or columns <= 0:
        raise ValueError("physical terminal width is invalid")
    return max(
        MIN_TERMINAL_WIDTH,
        min(columns - TERMINAL_RIGHT_MARGIN, MAX_AUTOMATIC_CONTENT_WIDTH),
    )


def _validate_width(width: int) -> int:
    if type(width) is not int or not MIN_TERMINAL_WIDTH <= width <= MAX_TERMINAL_WIDTH:
        raise ValueError("terminal Markdown width is out of range")
    return width


def _validate_prefixes(
    first_prefix: str,
    continuation_prefix: str,
    prefix_width: int,
    width: int,
) -> tuple[str, str, int]:
    if not isinstance(first_prefix, str) or not isinstance(continuation_prefix, str):
        raise ValueError("terminal prefixes must be strings")
    if type(prefix_width) is not int or prefix_width < 0 or prefix_width >= width:
        raise ValueError("terminal prefix width is invalid")
    return first_prefix, continuation_prefix, prefix_width


def _apply_hanging_prefix(
    rendered: str,
    first_prefix: str,
    continuation_prefix: str,
) -> str:
    if not rendered:
        return ""
    result: list[str] = []
    for index, line in enumerate(rendered.splitlines(keepends=True)):
        result.append(first_prefix if index == 0 else continuation_prefix)
        result.append(line)
    return "".join(result)


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
