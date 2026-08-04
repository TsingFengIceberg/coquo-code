"""Bounded SHA-256 observation for one workspace regular file."""

from __future__ import annotations

import json
from pathlib import Path

from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools._workspace_files import sha256_workspace_regular_file
from leonervis_code.tools._workspace_paths import WorkspacePathFailure

CHECKSUM_FILE_TOOL_NAME = "checksum_file"
MAX_CHECKSUM_FILE_BYTES = 256 * 1024 * 1024


def checksum_file_model_definition() -> dict[str, object]:
    return {
        "name": CHECKSUM_FILE_TOOL_NAME,
        "description": (
            "Compute SHA-256 for one existing bounded workspace-relative regular file without "
            "following symlinks. Returns path, byte count, algorithm, and lowercase digest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative file."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def checksum_file_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(checksum_file_model_definition())


class ChecksumFileTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != CHECKSUM_FILE_TOOL_NAME or set(arguments) != {"path"}:
                raise ValueError
            path = arguments["path"]
            if not isinstance(path, str):
                raise ValueError
            canonical, size, digest = sha256_workspace_regular_file(
                self._workspace,
                path,
                tool_name=CHECKSUM_FILE_TOOL_NAME,
                max_bytes=MAX_CHECKSUM_FILE_BYTES,
            )
        except (AttributeError, ValueError):
            return ToolResult(
                request.tool_use_id, "checksum_file input is malformed", is_error=True
            )
        except WorkspacePathFailure as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        content = (
            json.dumps(
                {"algorithm": "sha256", "bytes": size, "digest": digest, "path": canonical},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return ToolResult(request.tool_use_id, content)
