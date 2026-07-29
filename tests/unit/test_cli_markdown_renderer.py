from __future__ import annotations

import io
import re

import pytest

from leonervis_code.cli.markdown_renderer import (
    TerminalMarkdownRenderer,
    escape_terminal_controls,
    write_markdown_document,
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class FlushingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def test_complete_markdown_renders_structure_without_source_markers() -> None:
    stream = FlushingStream()

    write_markdown_document(
        stream,
        "# Heading\n\nThis is **bold** and `code`.\n\n- first\n- second\n\n"
        "```python\nprint('ok')\n```",
        color=False,
        width=60,
    )

    rendered = stream.getvalue()
    assert "Heading" in rendered
    assert "This is bold and code." in rendered
    assert " • first" in rendered
    assert "print('ok')" in rendered
    assert "# Heading" not in rendered
    assert "**bold**" not in rendered
    assert "```" not in rendered
    assert "\x1b" not in rendered
    assert stream.flush_count == 1


def test_color_rendering_uses_ansi_but_escapes_provider_control_characters() -> None:
    stream = io.StringIO()

    write_markdown_document(
        stream,
        "**safe** \x1b[31mprovider-color\x1b[0m\rreturn",
        color=True,
        width=60,
    )

    rendered = stream.getvalue()
    plain = ANSI_ESCAPE.sub("", rendered)
    assert "\x1b" in rendered
    assert "safe" in plain
    assert r"\x1b[31mprovider-color\x1b[0m\x0dreturn" in plain
    assert "\x1b[31mprovider-color" not in plain


def test_streaming_waits_for_blank_line_or_closed_fence() -> None:
    stream = FlushingStream()
    renderer = TerminalMarkdownRenderer(stream, color=False, width=60)

    assert renderer.push("# Heading\n") is False
    assert stream.getvalue() == ""
    assert renderer.push("\n") is True
    assert "Heading" in stream.getvalue()

    before_code = stream.getvalue()
    assert renderer.push("```python\nprint('ok')\n") is False
    assert stream.getvalue() == before_code
    assert renderer.push("```\n") is True
    assert "print('ok')" in stream.getvalue()


def test_abort_discards_incomplete_markdown_suffix() -> None:
    stream = FlushingStream()
    renderer = TerminalMarkdownRenderer(stream, color=False, width=60)

    assert renderer.push("unfinished **bold") is False
    renderer.abort()
    assert renderer.flush() is False
    assert stream.getvalue() == ""


def test_terminal_control_escaping_preserves_unicode_newlines_and_tabs() -> None:
    assert escape_terminal_controls("中文\n\ttext\x00\x1b\r\u202e\u2028") == (
        r"中文" "\n\ttext" r"\x00\x1b\x0d\u202e\u2028"
    )


@pytest.mark.parametrize("width", [39, 241, True])
def test_terminal_markdown_rejects_invalid_width(width: int) -> None:
    with pytest.raises(ValueError, match="width"):
        TerminalMarkdownRenderer(io.StringIO(), color=False, width=width)
