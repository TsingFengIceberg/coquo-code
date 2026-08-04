"""Bounded unified comparison of two workspace UTF-8 files."""

from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools._workspace_files import read_workspace_regular_file
from leonervis_code.tools._workspace_paths import WorkspacePathFailure

COMPARE_FILES_TOOL_NAME = "compare_files"
MAX_COMPARE_SOURCE_BYTES = 1024 * 1024
MAX_COMPARE_OUTPUT_BYTES = 64 * 1024
COMPARE_TRUNCATION_MARKER = "\n[truncated]\n"


def compare_files_model_definition() -> dict[str, object]:
    return {
        "name": COMPARE_FILES_TOOL_NAME,
        "description": (
            "Compare two existing workspace-relative UTF-8 regular files and return one bounded "
            "deterministic unified diff. This read-only tool does not use Git or follow symlinks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "left": {"type": "string", "description": "First workspace-relative file."},
                "right": {"type": "string", "description": "Second workspace-relative file."},
            },
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    }


def compare_files_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(compare_files_model_definition())


class CompareFilesTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != COMPARE_FILES_TOOL_NAME or set(arguments) != {"left", "right"}:
                raise ValueError
            left = arguments["left"]
            right = arguments["right"]
            if not isinstance(left, str) or not isinstance(right, str) or left == right:
                raise ValueError
            left_file = read_workspace_regular_file(
                self._workspace,
                left,
                tool_name=COMPARE_FILES_TOOL_NAME,
                max_bytes=MAX_COMPARE_SOURCE_BYTES,
            )
            right_file = read_workspace_regular_file(
                self._workspace,
                right,
                tool_name=COMPARE_FILES_TOOL_NAME,
                max_bytes=MAX_COMPARE_SOURCE_BYTES,
            )
            left_text = _decode(left_file.data)
            right_text = _decode(right_file.data)
            content, truncated = _bounded_diff(
                left_file.relative_path, right_file.relative_path, left_text, right_text
            )
        except (AttributeError, ValueError):
            return ToolResult(
                request.tool_use_id, "compare_files input is malformed", is_error=True
            )
        except WorkspacePathFailure as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, content, truncated=truncated)


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkspacePathFailure("compare_files content is not valid UTF-8") from None
    if "\x00" in text:
        raise WorkspacePathFailure("compare_files content contains NUL")
    return text


def _bounded_diff(left: str, right: str, left_text: str, right_text: str) -> tuple[str, bool]:
    fragments = unified_diff(
        left_text.splitlines(keepends=True),
        right_text.splitlines(keepends=True),
        fromfile=f"a/{left}",
        tofile=f"b/{right}",
        lineterm="\n",
        n=3,
    )
    marker_bytes = len(COMPARE_TRUNCATION_MARKER.encode("utf-8"))
    output: list[str] = []
    size = 0
    for fragment in fragments:
        encoded = fragment.encode("utf-8")
        if size + len(encoded) + marker_bytes > MAX_COMPARE_OUTPUT_BYTES:
            output.append(COMPARE_TRUNCATION_MARKER)
            return "".join(output), True
        output.append(fragment)
        size += len(encoded)
    return "".join(output), False
