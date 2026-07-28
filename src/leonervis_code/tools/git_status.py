"""Structured bounded Git worktree-status observation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools.git_repository import GitObservationError, GitRepository

GIT_STATUS_TOOL_NAME = "git_status"
MAX_GIT_STATUS_ENTRIES = 200
MAX_GIT_STATUS_PARSED_ENTRIES = 10_000
MAX_GIT_STATUS_OUTPUT_BYTES = 32 * 1024
GIT_STATUS_TRUNCATION_SENTINEL = '{"truncated":true}\n'


@dataclass(frozen=True)
class GitStatusEntry:
    path: str
    index: str
    worktree: str
    original_path: str | None = None


@dataclass(frozen=True)
class GitStatusSnapshot:
    entries: tuple[GitStatusEntry, ...]
    truncated: bool
    content: str


def git_status_model_definition() -> dict[str, object]:
    return {
        "name": GIT_STATUS_TOOL_NAME,
        "description": (
            "Observe bounded staged, unstaged, and untracked Git status for the workspace "
            "repository. Returns deterministic JSON Lines and never reads untracked file content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }


def git_status_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(git_status_model_definition())


class GitStatusTool:
    def __init__(self, workspace: Path, repository: GitRepository | None = None) -> None:
        self._repository = repository or GitRepository(workspace)

    def observe(self) -> GitStatusSnapshot:
        entries = _parse_porcelain_v2(self._repository.status_porcelain())
        content, truncated = _format_entries(entries)
        return GitStatusSnapshot(tuple(entries), truncated, content)

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != GIT_STATUS_TOOL_NAME or arguments:
                raise ValueError
            snapshot = self.observe()
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "git_status input is malformed", is_error=True)
        except GitObservationError as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(
            request.tool_use_id,
            snapshot.content,
            truncated=snapshot.truncated,
        )


def _parse_porcelain_v2(data: bytes) -> list[GitStatusEntry]:
    records = data.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    entries: list[GitStatusEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(entries) >= MAX_GIT_STATUS_PARSED_ENTRIES:
            raise GitObservationError("git status entry limit reached")
        if record.startswith(b"1 "):
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                raise GitObservationError("git status returned malformed data")
            entries.append(_entry(fields[8], fields[1]))
        elif record.startswith(b"2 "):
            fields = record.split(b" ", 9)
            if len(fields) != 10 or index + 1 >= len(records):
                raise GitObservationError("git status returned malformed rename data")
            index += 1
            entries.append(_entry(fields[9], fields[1], original_path=_decode_path(records[index])))
        elif record.startswith(b"u "):
            fields = record.split(b" ", 10)
            if len(fields) != 11:
                raise GitObservationError("git status returned malformed conflict data")
            entries.append(_entry(fields[10], fields[1]))
        elif record.startswith(b"? "):
            path = _decode_path(record[2:])
            entries.append(GitStatusEntry(path, "untracked", "untracked"))
        else:
            raise GitObservationError("git status returned an unsupported record")
        index += 1
    entries.sort(key=lambda item: (item.path.encode("utf-8"), item.original_path or ""))
    return entries


def _entry(path_bytes: bytes, xy: bytes, *, original_path: str | None = None) -> GitStatusEntry:
    if len(xy) != 2:
        raise GitObservationError("git status returned an invalid state")
    return GitStatusEntry(
        _decode_path(path_bytes),
        _decode_state(xy[0]),
        _decode_state(xy[1]),
        original_path,
    )


def _decode_state(value: int) -> str:
    states = {
        ord("."): "clean",
        ord("M"): "modified",
        ord("T"): "type-changed",
        ord("A"): "added",
        ord("D"): "deleted",
        ord("R"): "renamed",
        ord("C"): "copied",
        ord("U"): "unmerged",
    }
    try:
        return states[value]
    except KeyError:
        raise GitObservationError("git status returned an unsupported state") from None


def _decode_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError:
        raise GitObservationError("git status encountered a path that is not valid UTF-8") from None
    pure = PurePosixPath(path)
    if (
        not path
        or "\x00" in path
        or pure.is_absolute()
        or PureWindowsPath(path).is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise GitObservationError("git status returned an unsafe path")
    return path


def _format_entries(entries: list[GitStatusEntry]) -> tuple[str, bool]:
    output: list[str] = []
    output_bytes = 0
    sentinel_bytes = len(GIT_STATUS_TRUNCATION_SENTINEL.encode("utf-8"))
    for position, entry in enumerate(entries):
        value: dict[str, str] = {
            "path": entry.path,
            "index": entry.index,
            "worktree": entry.worktree,
        }
        if entry.original_path is not None:
            value["original_path"] = entry.original_path
        record = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        record_bytes = len(record.encode("utf-8"))
        if record_bytes + sentinel_bytes > MAX_GIT_STATUS_OUTPUT_BYTES:
            raise GitObservationError("one git status entry exceeds the output limit")
        if (
            position >= MAX_GIT_STATUS_ENTRIES
            or output_bytes + record_bytes + sentinel_bytes > MAX_GIT_STATUS_OUTPUT_BYTES
        ):
            output.append(GIT_STATUS_TRUNCATION_SENTINEL)
            return "".join(output), True
        output.append(record)
        output_bytes += record_bytes
    return "".join(output), False
