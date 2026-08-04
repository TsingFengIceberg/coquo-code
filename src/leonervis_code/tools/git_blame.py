"""Bounded current-HEAD Git blame observation for one literal path range."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools.git_diff import validate_git_literal_path
from leonervis_code.tools.git_repository import (
    GIT_OBJECT_ID_PATTERN,
    GitObservationError,
    GitRepository,
)

GIT_BLAME_TOOL_NAME = "git_blame"
MAX_GIT_BLAME_START_LINE = 1_000_000
MAX_GIT_BLAME_LINE_COUNT = 200
MAX_GIT_BLAME_RAW_OUTPUT_BYTES = 1024 * 1024
MAX_GIT_BLAME_OUTPUT_BYTES = 32 * 1024
GIT_BLAME_TRUNCATION_SENTINEL = '{"truncated":true}\n'
_HEADER = re.compile(rb"([0-9a-f]{40}|[0-9a-f]{64}) ([1-9][0-9]*) ([1-9][0-9]*)(?: [1-9][0-9]*)?\Z")
_TIMEZONE = re.compile(r"([+-])(\d{2})(\d{2})\Z")


@dataclass(frozen=True)
class GitBlameLine:
    commit_id: str
    original_line: int
    line: int
    author: str
    authored_at: str
    text: str


def git_blame_model_definition() -> dict[str, object]:
    return {
        "name": GIT_BLAME_TOOL_NAME,
        "description": (
            "Read bounded current-HEAD Git blame attribution for one literal tracked "
            "workspace-relative file range. Returns deterministic JSON Lines and accepts no "
            "arbitrary revision or Git arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Literal tracked file path."},
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_GIT_BLAME_START_LINE,
                },
                "line_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_GIT_BLAME_LINE_COUNT,
                },
            },
            "required": ["path", "start_line", "line_count"],
            "additionalProperties": False,
        },
    }


def git_blame_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(git_blame_model_definition())


class GitBlameTool:
    def __init__(self, workspace: Path, repository: GitRepository | None = None) -> None:
        self._repository = repository or GitRepository(workspace)

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != GIT_BLAME_TOOL_NAME or set(arguments) != {
                "line_count",
                "path",
                "start_line",
            }:
                raise ValueError
            path = arguments["path"]
            start = arguments["start_line"]
            count = arguments["line_count"]
            if (
                not isinstance(path, str)
                or type(start) is not int
                or type(count) is not int
                or not 1 <= start <= MAX_GIT_BLAME_START_LINE
                or not 1 <= count <= MAX_GIT_BLAME_LINE_COUNT
            ):
                raise ValueError
            canonical = validate_git_literal_path(path, tool_name=GIT_BLAME_TOOL_NAME)
            if canonical == ".":
                raise GitObservationError("git_blame path must identify one tracked file")
            result = self._repository.history(
                (
                    "blame",
                    "--line-porcelain",
                    "--root",
                    "--no-progress",
                    f"-L{start},+{count}",
                    "HEAD",
                    "--",
                    canonical,
                ),
                stdout_limit=MAX_GIT_BLAME_RAW_OUTPUT_BYTES,
            )
            if result.truncated:
                raise GitObservationError("git blame exceeded the raw observation limit")
            lines = _parse_blame(result.stdout)
            content, truncated = _format_blame(lines)
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "git_blame input is malformed", is_error=True)
        except GitObservationError as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, content, truncated=truncated)


def _parse_blame(data: bytes) -> list[GitBlameLine]:
    source = data.splitlines()
    output: list[GitBlameLine] = []
    index = 0
    while index < len(source):
        match = _HEADER.fullmatch(source[index])
        if match is None:
            raise GitObservationError("git blame returned malformed data")
        commit_id = match.group(1).decode("ascii")
        original_line = int(match.group(2))
        final_line = int(match.group(3))
        if GIT_OBJECT_ID_PATTERN.fullmatch(commit_id) is None:
            raise GitObservationError("git blame returned an invalid commit ID")
        author: str | None = None
        author_time: int | None = None
        author_tz: str | None = None
        index += 1
        while index < len(source) and not source[index].startswith(b"\t"):
            key, separator, value = source[index].partition(b" ")
            if separator:
                if key == b"author":
                    author = _decode_utf8(value, "author")
                elif key == b"author-time":
                    try:
                        author_time = int(value.decode("ascii"))
                    except ValueError:
                        raise GitObservationError(
                            "git blame returned an invalid author time"
                        ) from None
                elif key == b"author-tz":
                    try:
                        author_tz = value.decode("ascii", errors="strict")
                    except UnicodeDecodeError:
                        raise GitObservationError(
                            "git blame returned a non-ASCII author timezone"
                        ) from None
            index += 1
        if index >= len(source) or author is None or author_time is None or author_tz is None:
            raise GitObservationError("git blame returned incomplete line metadata")
        text = _decode_utf8(source[index][1:], "line text")
        output.append(
            GitBlameLine(
                commit_id,
                original_line,
                final_line,
                author,
                _timestamp(author_time, author_tz),
                text,
            )
        )
        index += 1
        if len(output) > MAX_GIT_BLAME_LINE_COUNT:
            raise GitObservationError("git blame returned too many lines")
    return output


def _timestamp(seconds: int, zone: str) -> str:
    match = _TIMEZONE.fullmatch(zone)
    if match is None:
        raise GitObservationError("git blame returned an invalid author timezone")
    offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    if match.group(1) == "-":
        offset = -offset
    try:
        return datetime.fromtimestamp(seconds, timezone(offset)).isoformat()
    except (OverflowError, OSError, ValueError):
        raise GitObservationError("git blame returned an invalid author time") from None


def _decode_utf8(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise GitObservationError(f"git blame returned a {label} that is not valid UTF-8") from None


def _format_blame(lines: list[GitBlameLine]) -> tuple[str, bool]:
    output: list[str] = []
    size = 0
    sentinel_size = len(GIT_BLAME_TRUNCATION_SENTINEL.encode("ascii"))
    for line in lines:
        record = (
            json.dumps(
                {
                    "author": line.author,
                    "authored_at": line.authored_at,
                    "commit_id": line.commit_id,
                    "line": line.line,
                    "original_line": line.original_line,
                    "text": line.text,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        record_size = len(record.encode("utf-8"))
        if record_size + sentinel_size > MAX_GIT_BLAME_OUTPUT_BYTES:
            raise GitObservationError("one git blame line exceeds the output limit")
        if size + record_size + sentinel_size > MAX_GIT_BLAME_OUTPUT_BYTES:
            output.append(GIT_BLAME_TRUNCATION_SENTINEL)
            return "".join(output), True
        output.append(record)
        size += record_size
    return "".join(output), False
