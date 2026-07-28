"""Bounded staged or unstaged Git patch observation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath

from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools.git_repository import GitObservationError, GitRepository

GIT_DIFF_TOOL_NAME = "git_diff"
MAX_GIT_DIFF_OUTPUT_BYTES = 64 * 1024
GIT_DIFF_TRUNCATION_MARKER = "\n[truncated]\n"
MAX_GIT_DIFF_PATH_CHARACTERS = 4096
MAX_GIT_DIFF_PATH_BYTES = 4096
MAX_GIT_DIFF_PATH_COMPONENTS = 64


class GitDiffScope(StrEnum):
    UNSTAGED = "unstaged"
    STAGED = "staged"


@dataclass(frozen=True)
class GitDiffSnapshot:
    scope: GitDiffScope
    path: str
    content: str
    truncated: bool


def git_diff_model_definition() -> dict[str, object]:
    return {
        "name": GIT_DIFF_TOOL_NAME,
        "description": (
            "Read one bounded Git patch for staged or unstaged tracked changes under a literal "
            "workspace-relative path. It disables external diff and text conversion and does "
            "not include untracked file content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": [GitDiffScope.UNSTAGED.value, GitDiffScope.STAGED.value],
                    "description": "Compare worktree to index, or index to HEAD.",
                },
                "path": {
                    "type": "string",
                    "description": "'.' or one literal portable workspace-relative path.",
                },
            },
            "required": ["scope", "path"],
            "additionalProperties": False,
        },
    }


def git_diff_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(git_diff_model_definition())


class GitDiffTool:
    def __init__(self, workspace: Path, repository: GitRepository | None = None) -> None:
        self._repository = repository or GitRepository(workspace)

    def observe(self, scope: GitDiffScope | str, path: str = ".") -> GitDiffSnapshot:
        try:
            selected_scope = GitDiffScope(scope)
        except ValueError:
            raise GitObservationError("git_diff scope must be staged or unstaged") from None
        relative_path = _validate_path(path)
        common = (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--ignore-submodules=all",
            "--no-color",
            "--patch",
            "--unified=3",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        )
        cached = ("--cached",) if selected_scope == GitDiffScope.STAGED else ()
        pathspec = ":(literal)." if relative_path == "." else f":(literal){relative_path}"
        marker_bytes = len(GIT_DIFF_TRUNCATION_MARKER.encode("utf-8"))
        result = self._repository.diff(
            (*common, *cached, "--", pathspec),
            stdout_limit=MAX_GIT_DIFF_OUTPUT_BYTES - marker_bytes,
        )
        content = _decode_patch(result.stdout)
        if result.truncated:
            content += GIT_DIFF_TRUNCATION_MARKER
        return GitDiffSnapshot(selected_scope, relative_path, content, result.truncated)

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != GIT_DIFF_TOOL_NAME or set(arguments) != {"scope", "path"}:
                raise ValueError
            scope = arguments["scope"]
            path = arguments["path"]
            if not isinstance(scope, str) or not isinstance(path, str):
                raise ValueError
            snapshot = self.observe(scope, path)
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "git_diff input is malformed", is_error=True)
        except GitObservationError as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(
            request.tool_use_id,
            snapshot.content,
            truncated=snapshot.truncated,
        )


def _validate_path(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise GitObservationError("git_diff path must be valid UTF-8") from None
    parts = value.split("/")
    if (
        not value
        or not value.strip()
        or value != value.strip()
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or PureWindowsPath(value).drive
        or value.endswith("/")
        or (value != "." and any(part in {"", ".", ".."} for part in parts))
        or len(parts) > MAX_GIT_DIFF_PATH_COMPONENTS
        or len(value) > MAX_GIT_DIFF_PATH_CHARACTERS
        or len(encoded) > MAX_GIT_DIFF_PATH_BYTES
    ):
        raise GitObservationError(
            "git_diff path must be '.' or a portable workspace-relative literal path"
        )
    return value


def _decode_patch(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.reason != "unexpected end of data":
            raise GitObservationError("git diff output is not valid UTF-8") from None
        try:
            return data[: error.start].decode("utf-8")
        except UnicodeDecodeError:
            raise GitObservationError("git diff output is not valid UTF-8") from None
