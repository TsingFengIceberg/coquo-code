"""No-follow metadata inspection for one bounded workspace path."""

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
    open_workspace_directory,
    validate_workspace_path,
)

STAT_PATH_TOOL_NAME = "stat_path"


def stat_path_model_definition() -> dict[str, object]:
    return {
        "name": STAT_PATH_TOOL_NAME,
        "description": (
            "Inspect no-follow metadata for one workspace-relative path, or '.' for the "
            "workspace root. This read-only tool reports type, basic rwx mode, modification "
            "time, and regular-file size without reading content or symbolic-link targets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path, or '.' for the root.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def stat_path_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(stat_path_model_definition())


class StatPathTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def execute(self, request: ToolUse) -> ToolResult:
        parent_fd: int | None = None
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"path"} or not isinstance(arguments["path"], str):
                raise ValueError
            raw_path = arguments["path"]
            parts = validate_workspace_path(
                raw_path,
                tool_name=STAT_PATH_TOOL_NAME,
                allow_root=True,
            )
            if parts:
                parent_fd, name = open_parent_directory(
                    self._workspace,
                    parts,
                    tool_name=STAT_PATH_TOOL_NAME,
                )
                try:
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    raise WorkspacePathFailure("stat_path target does not exist") from None
                except PermissionError:
                    raise WorkspacePathFailure("stat_path target is not accessible") from None
                except OSError:
                    raise WorkspacePathFailure("stat_path could not inspect target") from None
                canonical_path = "/".join(parts)
            else:
                descriptor = open_workspace_directory(
                    self._workspace,
                    tool_name=STAT_PATH_TOOL_NAME,
                )
                try:
                    info = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                canonical_path = "."
            payload: dict[str, object] = {
                "mode": f"{stat.S_IMODE(info.st_mode) & 0o777:04o}",
                "modified_ns": info.st_mtime_ns,
                "path": canonical_path,
                "type": _entry_type(info.st_mode),
            }
            if stat.S_ISREG(info.st_mode):
                payload["size"] = info.st_size
            content = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "stat_path input is malformed", is_error=True)
        except WorkspacePathFailure as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
        return ToolResult(request.tool_use_id, f"{content}\n")


def _entry_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"
