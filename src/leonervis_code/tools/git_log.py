"""Bounded current-HEAD Git commit-history observation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools.git_diff import validate_git_literal_path
from leonervis_code.tools.git_repository import (
    GIT_OBJECT_ID_PATTERN,
    GitObservationError,
    GitRepository,
)

GIT_LOG_TOOL_NAME = "git_log"
DEFAULT_GIT_LOG_LIMIT = 10
MAX_GIT_LOG_LIMIT = 50
MAX_GIT_LOG_RAW_OUTPUT_BYTES = 1024 * 1024
MAX_GIT_LOG_OUTPUT_BYTES = 32 * 1024
MAX_GIT_LOG_SUBJECT_BYTES = 1024
GIT_LOG_TRUNCATION_SENTINEL = '{"truncated":true}\n'


@dataclass(frozen=True)
class GitLogEntry:
    commit_id: str
    parent_ids: tuple[str, ...]
    committed_at: str
    subject: str
    subject_truncated: bool


@dataclass(frozen=True)
class GitLogSnapshot:
    entries: tuple[GitLogEntry, ...]
    path: str
    truncated: bool
    content: str


def git_log_model_definition() -> dict[str, object]:
    return {
        "name": GIT_LOG_TOOL_NAME,
        "description": (
            "List a bounded recent history reachable from the workspace repository's current "
            "HEAD, optionally limited to one literal workspace-relative path. Returns "
            "deterministic JSON Lines without reading arbitrary revisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_GIT_LOG_LIMIT,
                    "description": "Maximum number of recent commits to return.",
                },
                "path": {
                    "type": "string",
                    "description": "'.' or one literal portable workspace-relative path.",
                },
            },
            "required": ["limit", "path"],
            "additionalProperties": False,
        },
    }


def git_log_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(git_log_model_definition())


class GitLogTool:
    def __init__(self, workspace: Path, repository: GitRepository | None = None) -> None:
        self._repository = repository or GitRepository(workspace)

    def observe(self, limit: int = DEFAULT_GIT_LOG_LIMIT, path: str = ".") -> GitLogSnapshot:
        if type(limit) is not int or not 1 <= limit <= MAX_GIT_LOG_LIMIT:
            raise GitObservationError(f"git_log limit must be from 1 to {MAX_GIT_LOG_LIMIT}")
        relative_path = validate_git_literal_path(path, tool_name=GIT_LOG_TOOL_NAME)
        path_arguments = () if relative_path == "." else ("--", f":(literal){relative_path}")
        result = self._repository.history(
            (
                "log",
                "-z",
                f"--max-count={limit}",
                "--topo-order",
                "--no-decorate",
                "--no-show-signature",
                "--format=%H%x00%P%x00%cI%x00%s",
                "HEAD",
                *path_arguments,
            ),
            stdout_limit=MAX_GIT_LOG_RAW_OUTPUT_BYTES,
        )
        if result.truncated:
            raise GitObservationError("git log exceeded the raw observation limit")
        entries = _parse_log(result.stdout)
        content, truncated = _format_entries(entries)
        return GitLogSnapshot(tuple(entries), relative_path, truncated, content)

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != GIT_LOG_TOOL_NAME or set(arguments) != {"limit", "path"}:
                raise ValueError
            limit = arguments["limit"]
            path = arguments["path"]
            if type(limit) is not int or not isinstance(path, str):
                raise ValueError
            snapshot = self.observe(limit, path)
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "git_log input is malformed", is_error=True)
        except GitObservationError as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, snapshot.content, truncated=snapshot.truncated)


def _parse_log(data: bytes) -> list[GitLogEntry]:
    if not data:
        return []
    fields = data.split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    if len(fields) % 4:
        raise GitObservationError("git log returned malformed data")
    entries = []
    for offset in range(0, len(fields), 4):
        commit_id = _decode_ascii(fields[offset], "commit ID")
        parent_text = _decode_ascii(fields[offset + 1], "parent IDs")
        committed_at = _decode_ascii(fields[offset + 2], "commit timestamp")
        if not GIT_OBJECT_ID_PATTERN.fullmatch(commit_id):
            raise GitObservationError("git log returned an invalid commit ID")
        parent_ids = tuple(parent_text.split()) if parent_text else ()
        if any(not GIT_OBJECT_ID_PATTERN.fullmatch(parent) for parent in parent_ids):
            raise GitObservationError("git log returned an invalid parent ID")
        try:
            datetime.fromisoformat(committed_at)
        except ValueError:
            raise GitObservationError("git log returned an invalid commit timestamp") from None
        subject, subject_truncated = _decode_bounded_utf8(
            fields[offset + 3], MAX_GIT_LOG_SUBJECT_BYTES, "git log subject"
        )
        entries.append(GitLogEntry(commit_id, parent_ids, committed_at, subject, subject_truncated))
    return entries


def _format_entries(entries: list[GitLogEntry]) -> tuple[str, bool]:
    output: list[str] = []
    size = 0
    sentinel_size = len(GIT_LOG_TRUNCATION_SENTINEL.encode("utf-8"))
    for entry in entries:
        record = (
            json.dumps(
                {
                    "commit_id": entry.commit_id,
                    "parent_ids": list(entry.parent_ids),
                    "committed_at": entry.committed_at,
                    "subject": entry.subject,
                    "subject_truncated": entry.subject_truncated,
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        record_size = len(record.encode("utf-8"))
        if size + record_size + sentinel_size > MAX_GIT_LOG_OUTPUT_BYTES:
            output.append(GIT_LOG_TRUNCATION_SENTINEL)
            return "".join(output), True
        output.append(record)
        size += record_size
    return "".join(output), any(entry.subject_truncated for entry in entries)


def _decode_ascii(data: bytes, label: str) -> str:
    try:
        return data.decode("ascii")
    except UnicodeDecodeError:
        raise GitObservationError(f"git log returned a non-ASCII {label}") from None


def _decode_bounded_utf8(
    data: bytes,
    limit: int,
    label: str,
    *,
    source_truncated: bool = False,
) -> tuple[str, bool]:
    truncated = source_truncated or len(data) > limit
    selected = data[:limit]
    if truncated:
        while selected:
            try:
                return selected.decode("utf-8"), True
            except UnicodeDecodeError as error:
                if error.reason != "unexpected end of data":
                    raise GitObservationError(f"{label} is not valid UTF-8") from None
                selected = selected[: error.start]
    try:
        return selected.decode("utf-8"), truncated
    except UnicodeDecodeError:
        raise GitObservationError(f"{label} is not valid UTF-8") from None
