from __future__ import annotations

import json
from pathlib import Path

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.read_file_lines import (
    MAX_READ_FILE_LINES_COUNT,
    MAX_READ_FILE_LINES_OUTPUT_BYTES,
    MAX_READ_FILE_LINES_SOURCE_BYTES,
    ReadFileLinesTool,
)


def request(path: object, start_line: object = 1, line_count: object = 20) -> ToolUse:
    return ToolUse(
        "lines-1",
        "read_file_lines",
        ToolArguments.from_mapping(
            {"path": path, "start_line": start_line, "line_count": line_count}
        ),
    )


def records(content: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in content.splitlines()]


def test_reads_one_based_bounded_range_with_exact_logical_line_spelling(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_bytes("zero\r\none\rtwo\n三".encode())

    result = ReadFileLinesTool(tmp_path).execute(request("note.txt", 2, 3))

    assert not result.is_error
    assert not result.truncated
    assert records(result.content) == [
        {"line": 2, "text": "one"},
        {"line": 3, "text": "two"},
        {"line": 4, "text": "三"},
    ]


def test_range_beyond_eof_and_empty_file_return_empty_success(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    (tmp_path / "short.txt").write_text("one\n", encoding="utf-8")
    tool = ReadFileLinesTool(tmp_path)

    assert tool.execute(request("empty.txt")).content == ""
    result = tool.execute(request("short.txt", 20, 2))
    assert result.content == ""
    assert not result.is_error


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "a", "start_line": 1},
        {"path": "a", "start_line": 1, "line_count": 1, "extra": 1},
        {"path": 1, "start_line": 1, "line_count": 1},
        {"path": "a", "start_line": True, "line_count": 1},
        {"path": "a", "start_line": 1, "line_count": MAX_READ_FILE_LINES_COUNT + 1},
    ],
)
def test_rejects_malformed_input(tmp_path: Path, arguments: dict[str, object]) -> None:
    malformed = ToolUse("lines-1", "read_file_lines", ToolArguments.from_mapping(arguments))

    result = ReadFileLinesTool(tmp_path).execute(malformed)

    assert result.is_error
    assert result.content == "read_file_lines input is malformed"


@pytest.mark.parametrize("path", ["", ".", "../x", "a/../x", "a//b", "a\\b", "/x"])
def test_rejects_nonportable_file_paths(tmp_path: Path, path: str) -> None:
    result = ReadFileLinesTool(tmp_path).execute(request(path))

    assert result.is_error
    assert "portable workspace-relative path" in result.content


def test_rejects_symlinks_nonfiles_binary_and_large_sources(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-lines.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    (tmp_path / "dir").mkdir()
    (tmp_path / "binary").write_bytes(b"\xff")
    (tmp_path / "large").write_bytes(b"x" * (MAX_READ_FILE_LINES_SOURCE_BYTES + 1))
    tool = ReadFileLinesTool(tmp_path)

    assert "symbolic links" in tool.execute(request("link.txt")).content
    assert "regular file" in tool.execute(request("dir")).content
    assert "not valid UTF-8" in tool.execute(request("binary")).content
    assert "source exceeds" in tool.execute(request("large")).content


def test_truncates_only_at_complete_record_boundary(tmp_path: Path) -> None:
    line = "x" * 1000
    (tmp_path / "many.txt").write_text("\n".join([line] * 100), encoding="utf-8")

    result = ReadFileLinesTool(tmp_path).execute(request("many.txt", 1, 100))

    assert result.truncated
    assert result.content.endswith('{"truncated":true}\n')
    assert len(result.content.encode("utf-8")) <= MAX_READ_FILE_LINES_OUTPUT_BYTES
    assert records(result.content)[-1] == {"truncated": True}


def test_rejects_one_selected_line_that_cannot_fit(tmp_path: Path) -> None:
    (tmp_path / "wide.txt").write_text(
        "x" * MAX_READ_FILE_LINES_OUTPUT_BYTES,
        encoding="utf-8",
    )

    result = ReadFileLinesTool(tmp_path).execute(request("wide.txt", 1, 1))

    assert result.is_error
    assert "selected line exceeds" in result.content
