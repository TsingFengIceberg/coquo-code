from __future__ import annotations

import io
import re

import pytest
from rich.cells import cell_len

from coquo.cli.markdown_renderer import (
    TerminalMarkdownRenderer,
    escape_terminal_controls,
    render_markdown_document,
    render_plain_document,
    terminal_content_width,
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


def test_role_documents_wrap_with_hanging_body_indentation() -> None:
    user = render_plain_document(
        "0123456789 0123456789 0123456789 0123456789\nsecond line",
        width=40,
        first_prefix="› ",
        continuation_prefix="  ",
        prefix_width=2,
    )
    assert user.splitlines() == [
        "› 0123456789 0123456789 0123456789 ",
        "  0123456789",
        "  second line",
    ]

    assistant = render_markdown_document(
        "A long assistant response that must wrap inside the role body column.",
        color=False,
        width=40,
        first_prefix="• ",
        continuation_prefix="  ",
        prefix_width=2,
    )
    lines = assistant.splitlines()
    assert lines[0].startswith("• ")
    assert all(line.startswith(("• ", "  ")) for line in lines)
    assert all(len(line) <= 40 for line in lines)


def test_terminal_content_width_reserves_the_edge_and_caps_readable_lines() -> None:
    assert terminal_content_width(80) == 79
    assert terminal_content_width(101) == 100
    assert terminal_content_width(120) == 100
    assert terminal_content_width(241) == 100
    assert terminal_content_width(40) == 40

    with pytest.raises(ValueError, match="physical terminal width"):
        terminal_content_width(0)


def test_terminal_margin_keeps_cjk_markdown_and_trace_continuations_off_left_edge() -> None:
    physical_width = 60
    content_width = terminal_content_width(physical_width)
    assistant = render_markdown_document(
        "这是一个包含中文说明和 OpenAI Responses API 名称的较长段落，用来验证终端自动换行不会越过预渲染边界。\n\n"
        "1. c23e36d - feat: complete remote MCP transports and extended capabilities\n"
        "2. 4705b1d - feat: add bounded MCP notifications and exact local trust policy",
        color=False,
        width=content_width,
        first_prefix="• ",
        continuation_prefix="  ",
        prefix_width=2,
    )
    trace = render_plain_document(
        "Profile usage: 369.8k in / 9.4k out - known=24 unknown=0 - compaction 0 in / 0 out",
        width=content_width,
        first_prefix="  │ ",
        continuation_prefix="  │ ",
        prefix_width=4,
    )

    assistant_lines = assistant.splitlines()
    trace_lines = trace.splitlines()
    assert all(line.startswith(("• ", "  ")) for line in assistant_lines)
    assert all(line.startswith("  │ ") for line in trace_lines)
    assert all(cell_len(line) < physical_width for line in (*assistant_lines, *trace_lines))


def test_streaming_markdown_resize_preserves_pending_text_and_uses_latest_width() -> None:
    stream = FlushingStream()
    renderer = TerminalMarkdownRenderer(
        stream,
        color=False,
        width=79,
        first_prefix="• ",
        continuation_prefix="  ",
        prefix_width=2,
    )

    assert renderer.push("尚未完成的流式段落会在窗口缩小以后继续使用新的宽度进行包装") is False
    renderer.resize(40)
    assert renderer.flush() is True

    lines = stream.getvalue().splitlines()
    assert len(lines) >= 2
    assert lines[0].startswith("• ")
    assert all(line.startswith(("• ", "  ")) for line in lines)
    assert all(cell_len(line) <= 40 for line in lines)

    with pytest.raises(ValueError, match="width"):
        renderer.resize(39)


@pytest.mark.parametrize("width", [39, 241, True])
def test_terminal_markdown_rejects_invalid_width(width: int) -> None:
    with pytest.raises(ValueError, match="width"):
        TerminalMarkdownRenderer(io.StringIO(), color=False, width=width)
