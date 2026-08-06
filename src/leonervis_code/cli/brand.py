"""Leonervis Code terminal-brand rendering."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from typing import TextIO

from leonervis_code.cli.markdown_renderer import render_plain_document

RESET = "\x1b[0m"
TAIL = (166, 90, 24)
BODY = (230, 154, 43)
HEAD = (255, 224, 154)

# The three equal 5×5 modules agreed for the Leonervis LEO mark.
L_GLYPH = ("█    ", "█    ", "█    ", "█    ", "█████")
E_GLYPH = ("█████", "█    ", "█████", "█    ", "█████")
O_GLYPH = (" ███ ", "█   █", "█   █", "█   █", " ███ ")


def color_enabled(stream: TextIO, environment: Mapping[str, str] | None = None) -> bool:
    """Return whether terminal color should be emitted for ``stream``."""
    env = os.environ if environment is None else environment
    return stream.isatty() and "NO_COLOR" not in env


def rgb(red: int, green: int, blue: int) -> str:
    """Return an ANSI truecolor foreground escape sequence."""
    return f"\x1b[38;2;{red};{green};{blue}m"


def paint(text: str, color: tuple[int, int, int], *, enabled: bool) -> str:
    """Apply a foreground color to non-space characters in ``text``."""
    if not enabled:
        return text
    return "".join(
        f"{rgb(*color)}{character}{RESET}" if character != " " else " " for character in text
    )


def render_mark(*, color: bool) -> tuple[str, ...]:
    """Render the five-row LEO mark using the established three-color palette."""
    return tuple(
        f"{paint(L_GLYPH[row], TAIL, enabled=color)}"
        f"{paint(E_GLYPH[row], BODY, enabled=color)} "
        f"{paint(O_GLYPH[row], HEAD, enabled=color)}"
        for row in range(len(L_GLYPH))
    )


def display_path(path: Path) -> str:
    """Format a path relative to the user home directory when possible."""
    resolved_path = path.resolve()
    home = Path.home().resolve()
    if resolved_path == home:
        return "~"
    if resolved_path.is_relative_to(home):
        return f"~/{resolved_path.relative_to(home)}"
    return str(resolved_path)


def render_banner(*, version: str, cwd: Path, color: bool, width: int | None = None) -> str:
    """Render the Foundation 3D banner with a bounded narrow-terminal fallback."""
    mark = render_mark(color=color)
    details = (
        f"LEONERVIS CODE v{version}",
        "Foundation 3D · durable workspace Sessions",
        display_path(cwd),
    )
    plain_mark_width = 2 + len(L_GLYPH[0]) + len(E_GLYPH[0]) + 1 + len(O_GLYPH[0])
    if width is not None and any(plain_mark_width + 4 + len(detail) > width for detail in details):
        detail_block = render_plain_document(
            "\n".join(details),
            width=width,
            first_prefix="  ",
            continuation_prefix="  ",
            prefix_width=2,
        ).removesuffix("\n")
        return "\n".join((*[f"  {row}".rstrip() for row in mark], "", detail_block))
    lines = [f"  {mark[row]}    {details[row]}".rstrip() for row in range(len(details))]
    lines.extend(f"  {row}".rstrip() for row in mark[len(details) :])
    return "\n".join(lines)
