#!/usr/bin/env python3
"""Render a temporary ANSI-color Coquo startup-banner sample."""

from __future__ import annotations

import argparse
import os

from coquo.cli.brand import C_GLYPH, DEEP, LIGHT, O_GLYPH, Q_GLYPH, WARM, paint

RESET = "\x1b[0m"
BOLD_WHITE = "\x1b[1;97m"
DIM = "\x1b[2m"


def build_mark(*, color: bool) -> list[str]:
    """Render COQ with a one-cell gap before Q."""
    return [
        f"{paint(C_GLYPH[row], DEEP, enabled=color)}"
        f"{paint(O_GLYPH[row], WARM, enabled=color)} "
        f"{paint(Q_GLYPH[row], LIGHT, enabled=color)}"
        for row in range(5)
    ]


def main() -> int:
    """Print a sample banner; it is not part of the Coquo CLI yet."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="render a monochrome fallback instead of ANSI truecolor",
    )
    arguments = parser.parse_args()
    color = not arguments.no_color and not os.environ.get("NO_COLOR")

    mark = build_mark(color=color)
    title = f"{BOLD_WHITE}COQUO{RESET}" if color else "COQUO"
    details = [
        f"{title} v0.1.0",
        "sample startup banner · color study",
        "/root/Projects/coquo",
        "temporary preview — not part of the CLI",
    ]

    print()
    for row, icon_row in enumerate(mark):
        suffix = details[row] if row < len(details) else ""
        if color and row > 0:
            suffix = f"{DIM}{suffix}{RESET}"
        print(f"  {icon_row}    {suffix}".rstrip())
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
