"""Bounded reachable-commit metadata and patch observation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools.git_diff import _decode_patch, validate_git_literal_path
from coquo.tools.git_log import _decode_bounded_utf8
from coquo.tools.git_repository import (
    GIT_OBJECT_ID_PATTERN,
    GitObservationError,
    GitRepository,
)

GIT_SHOW_TOOL_NAME = "git_show"
MAX_GIT_SHOW_OUTPUT_BYTES = 64 * 1024
MAX_GIT_SHOW_METADATA_BYTES = 4096
MAX_GIT_SHOW_MESSAGE_BYTES = 8 * 1024
GIT_SHOW_TRUNCATION_MARKER = "\n[truncated]\n"


@dataclass(frozen=True)
class GitShowSnapshot:
    commit_id: str
    parent_ids: tuple[str, ...]
    committed_at: str
    path: str
    message: str
    message_truncated: bool
    patch: str
    patch_truncated: bool
    content: str

    @property
    def truncated(self) -> bool:
        return self.message_truncated or self.patch_truncated


def git_show_model_definition() -> dict[str, object]:
    return {
        "name": GIT_SHOW_TOOL_NAME,
        "description": (
            "Read bounded metadata, commit message, and tracked patch for one complete commit ID "
            "that is reachable from the workspace repository's current HEAD, optionally limited "
            "to one literal workspace-relative path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commit_id": {
                    "type": "string",
                    "description": "One complete lowercase 40- or 64-hex commit object ID.",
                },
                "path": {
                    "type": "string",
                    "description": "'.' or one literal portable workspace-relative path.",
                },
            },
            "required": ["commit_id", "path"],
            "additionalProperties": False,
        },
    }


def git_show_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(git_show_model_definition())


class GitShowTool:
    def __init__(self, workspace: Path, repository: GitRepository | None = None) -> None:
        self._repository = repository or GitRepository(workspace)

    def observe(self, commit_id: str, path: str = ".") -> GitShowSnapshot:
        if not isinstance(commit_id, str) or not GIT_OBJECT_ID_PATTERN.fullmatch(commit_id):
            raise GitObservationError(
                "git_show commit_id must be a complete lowercase 40- or 64-hex object ID"
            )
        relative_path = validate_git_literal_path(path, tool_name=GIT_SHOW_TOOL_NAME)
        if not self._repository.commit_is_reachable(commit_id):
            raise GitObservationError("git_show commit_id is not reachable from current HEAD")

        metadata_result = self._repository.history(
            (
                "show",
                "--no-patch",
                "--no-show-signature",
                "--format=%H%x00%P%x00%cI",
                commit_id,
            ),
            stdout_limit=MAX_GIT_SHOW_METADATA_BYTES,
        )
        if metadata_result.truncated:
            raise GitObservationError("git show metadata exceeded the observation limit")
        resolved_id, parent_ids, committed_at = _parse_metadata(metadata_result.stdout)
        if resolved_id != commit_id:
            raise GitObservationError("git show resolved an unexpected commit ID")

        message_result = self._repository.history(
            (
                "show",
                "--no-patch",
                "--no-show-signature",
                "--format=format:%B",
                commit_id,
            ),
            stdout_limit=MAX_GIT_SHOW_MESSAGE_BYTES,
        )
        message, message_truncated = _decode_bounded_utf8(
            message_result.stdout,
            MAX_GIT_SHOW_MESSAGE_BYTES,
            "git show message",
            source_truncated=message_result.truncated,
        )
        header = (
            json.dumps(
                {
                    "commit_id": commit_id,
                    "parent_ids": list(parent_ids),
                    "committed_at": committed_at,
                    "message": message,
                    "message_truncated": message_truncated,
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        header_size = len(header.encode("utf-8"))
        marker_size = len(GIT_SHOW_TRUNCATION_MARKER.encode("utf-8"))
        patch_limit = MAX_GIT_SHOW_OUTPUT_BYTES - header_size - marker_size
        if patch_limit <= 0:
            raise GitObservationError("git show metadata exceeds the output limit")

        path_arguments = () if relative_path == "." else ("--", f":(literal){relative_path}")
        patch_result = self._repository.history(
            (
                "show",
                "--format=",
                "--no-show-signature",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--ignore-submodules=all",
                "--no-color",
                "--patch",
                "--unified=3",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                commit_id,
                *path_arguments,
            ),
            stdout_limit=patch_limit,
        )
        patch = _decode_patch(patch_result.stdout)
        if patch_result.truncated:
            patch += GIT_SHOW_TRUNCATION_MARKER
        return GitShowSnapshot(
            commit_id,
            parent_ids,
            committed_at,
            relative_path,
            message,
            message_truncated,
            patch,
            patch_result.truncated,
            header + patch,
        )

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != GIT_SHOW_TOOL_NAME or set(arguments) != {"commit_id", "path"}:
                raise ValueError
            commit_id = arguments["commit_id"]
            path = arguments["path"]
            if not isinstance(commit_id, str) or not isinstance(path, str):
                raise ValueError
            snapshot = self.observe(commit_id, path)
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "git_show input is malformed", is_error=True)
        except GitObservationError as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, snapshot.content, truncated=snapshot.truncated)


def _parse_metadata(data: bytes) -> tuple[str, tuple[str, ...], str]:
    try:
        text = data.decode("ascii").rstrip("\n")
    except UnicodeDecodeError:
        raise GitObservationError("git show returned non-ASCII metadata") from None
    fields = text.split("\0")
    if len(fields) != 3 or not GIT_OBJECT_ID_PATTERN.fullmatch(fields[0]):
        raise GitObservationError("git show returned malformed metadata")
    parent_ids = tuple(fields[1].split()) if fields[1] else ()
    if any(not GIT_OBJECT_ID_PATTERN.fullmatch(parent) for parent in parent_ids):
        raise GitObservationError("git show returned an invalid parent ID")
    try:
        datetime.fromisoformat(fields[2])
    except ValueError:
        raise GitObservationError("git show returned an invalid commit timestamp") from None
    return fields[0], parent_ids, fields[2]
