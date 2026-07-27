from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from leonervis_code.core.contracts import (
    MAX_ASSISTANT_TOOL_TEXT_BYTES,
    MAX_ASSISTANT_TOOL_TEXT_CHARACTERS,
    ToolArguments,
    ToolUse,
)


def tool_use(*, assistant_text: str | None = None) -> ToolUse:
    return ToolUse(
        "tool-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text=assistant_text,
    )


def test_tool_use_canonically_binds_optional_assistant_text() -> None:
    pure = tool_use()
    mixed = tool_use(assistant_text="I will inspect the file first.")
    whitespace = tool_use(assistant_text="  \n")

    assert pure.assistant_text is None
    assert mixed.assistant_text == "I will inspect the file first."
    assert whitespace.assistant_text == "  \n"
    assert mixed != pure
    with pytest.raises(FrozenInstanceError):
        mixed.assistant_text = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("value", ["", 1])
def test_tool_use_rejects_noncanonical_assistant_text(value: object) -> None:
    with pytest.raises(ValueError, match="non-empty text or null"):
        tool_use(assistant_text=value)  # type: ignore[arg-type]


def test_tool_use_accepts_exact_text_limit_and_rejects_character_or_byte_overflow() -> None:
    assert tool_use(assistant_text="a" * MAX_ASSISTANT_TOOL_TEXT_CHARACTERS).assistant_text

    with pytest.raises(ValueError, match="supported size"):
        tool_use(assistant_text="a" * (MAX_ASSISTANT_TOOL_TEXT_CHARACTERS + 1))
    assert MAX_ASSISTANT_TOOL_TEXT_BYTES == MAX_ASSISTANT_TOOL_TEXT_CHARACTERS
    with pytest.raises(ValueError, match="supported size"):
        tool_use(assistant_text="界" * (MAX_ASSISTANT_TOOL_TEXT_BYTES // 3 + 1))


def test_tool_use_rejects_non_utf8_assistant_text() -> None:
    with pytest.raises(ValueError, match="valid UTF-8"):
        tool_use(assistant_text="\ud800")


def test_tool_use_rejects_nul_in_assistant_text() -> None:
    with pytest.raises(ValueError, match="NUL"):
        tool_use(assistant_text="before\x00after")
