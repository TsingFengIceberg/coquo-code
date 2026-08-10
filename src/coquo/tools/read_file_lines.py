"""Bounded line-addressable reads of one workspace UTF-8 file."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools._workspace_paths import (
    WorkspacePathFailure,
    open_parent_directory,
    validate_workspace_path,
)

READ_FILE_LINES_TOOL_NAME = "read_file_lines"
MAX_READ_FILE_LINES_SOURCE_BYTES = 1024 * 1024
MAX_READ_FILE_LINES_START = 1_000_000
MAX_READ_FILE_LINES_COUNT = 200
MAX_READ_FILE_LINES_OUTPUT_BYTES = 32 * 1024
READ_FILE_LINES_TRUNCATION_SENTINEL = '{"truncated":true}\n'


class _ReadFileLinesFailure(RuntimeError):
    pass


def read_file_lines_model_definition() -> dict[str, object]:
    return {
        "name": READ_FILE_LINES_TOOL_NAME,
        "description": (
            "Read a bounded range of logical lines from one existing workspace-relative UTF-8 "
            "regular file. Use this read-only tool for later sections of files that read_file "
            "would truncate. Output is deterministic JSON Lines containing line and text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of one UTF-8 text file.",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_READ_FILE_LINES_START,
                    "description": "One-based first logical line to return.",
                },
                "line_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_READ_FILE_LINES_COUNT,
                    "description": "Maximum number of complete logical lines to return.",
                },
            },
            "required": ["path", "start_line", "line_count"],
            "additionalProperties": False,
        },
    }


def read_file_lines_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(read_file_lines_model_definition())


class ReadFileLinesTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"line_count", "path", "start_line"}:
                raise _ReadFileLinesFailure("read_file_lines input is malformed")
            raw_path = arguments["path"]
            start_line = arguments["start_line"]
            line_count = arguments["line_count"]
            if (
                not isinstance(raw_path, str)
                or type(start_line) is not int
                or type(line_count) is not int
                or not 1 <= start_line <= MAX_READ_FILE_LINES_START
                or not 1 <= line_count <= MAX_READ_FILE_LINES_COUNT
            ):
                raise _ReadFileLinesFailure("read_file_lines input is malformed")
            parts = validate_workspace_path(
                raw_path,
                tool_name=READ_FILE_LINES_TOOL_NAME,
                allow_root=False,
            )
            data = self._read(parts)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise _ReadFileLinesFailure("read_file_lines content is not valid UTF-8") from None
            if "\x00" in text:
                raise _ReadFileLinesFailure("read_file_lines content contains NUL")
            content, truncated = _format_range(text, start_line, line_count)
        except (AttributeError, ValueError):
            return self._error(request, "read_file_lines input is malformed")
        except (WorkspacePathFailure, _ReadFileLinesFailure) as error:
            return self._error(request, str(error))
        return ToolResult(request.tool_use_id, content, truncated=truncated)

    def _read(self, parts: tuple[str, ...]) -> bytes:
        parent_fd, name = open_parent_directory(
            self._workspace,
            parts,
            tool_name=READ_FILE_LINES_TOOL_NAME,
        )
        descriptor: int | None = None
        try:
            try:
                before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise _ReadFileLinesFailure("read_file_lines target does not exist") from None
            except PermissionError:
                raise _ReadFileLinesFailure("read_file_lines target is not accessible") from None
            except OSError:
                raise _ReadFileLinesFailure("read_file_lines could not inspect target") from None
            if stat.S_ISLNK(before.st_mode):
                raise _ReadFileLinesFailure("read_file_lines path must not contain symbolic links")
            if not stat.S_ISREG(before.st_mode):
                raise _ReadFileLinesFailure("read_file_lines target is not a regular file")
            if before.st_size > MAX_READ_FILE_LINES_SOURCE_BYTES:
                raise _ReadFileLinesFailure(
                    f"read_file_lines source exceeds {MAX_READ_FILE_LINES_SOURCE_BYTES} bytes"
                )
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise _ReadFileLinesFailure("read_file_lines target changed before reading")
            data = _read_bounded(descriptor, MAX_READ_FILE_LINES_SOURCE_BYTES)
            after = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise _ReadFileLinesFailure("read_file_lines target changed while reading")
            return data
        except _ReadFileLinesFailure:
            raise
        except PermissionError:
            raise _ReadFileLinesFailure("read_file_lines target is not accessible") from None
        except OSError:
            raise _ReadFileLinesFailure("read_file_lines could not read target") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    @staticmethod
    def _error(request: ToolUse, message: str) -> ToolResult:
        return ToolResult(request.tool_use_id, message, is_error=True)


def _read_bounded(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise _ReadFileLinesFailure(f"read_file_lines source exceeds {limit} bytes")
    return data


def _logical_lines(text: str) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] not in {"\r", "\n"}:
            index += 1
            continue
        lines.append(text[start:index])
        if text[index] == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            index += 1
        index += 1
        start = index
    if start < len(text):
        lines.append(text[start:])
    return lines


def _format_range(text: str, start_line: int, line_count: int) -> tuple[str, bool]:
    lines = _logical_lines(text)
    selected = lines[start_line - 1 : start_line - 1 + line_count]
    records = [
        json.dumps(
            {"line": start_line + index, "text": line},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for index, line in enumerate(selected)
    ]
    sentinel_bytes = len(READ_FILE_LINES_TRUNCATION_SENTINEL.encode("utf-8"))
    output: list[str] = []
    output_bytes = 0
    for index, record in enumerate(records):
        record_bytes = len(record.encode("utf-8"))
        has_later_record = index + 1 < len(records)
        reserve = sentinel_bytes if has_later_record else 0
        if record_bytes + reserve > MAX_READ_FILE_LINES_OUTPUT_BYTES and not output:
            raise _ReadFileLinesFailure("read_file_lines selected line exceeds the output limit")
        if output_bytes + record_bytes > MAX_READ_FILE_LINES_OUTPUT_BYTES:
            output.append(READ_FILE_LINES_TRUNCATION_SENTINEL)
            return "".join(output), True
        if output_bytes + record_bytes + reserve > MAX_READ_FILE_LINES_OUTPUT_BYTES:
            output.append(READ_FILE_LINES_TRUNCATION_SENTINEL)
            return "".join(output), True
        output.append(record)
        output_bytes += record_bytes
    return "".join(output), False
