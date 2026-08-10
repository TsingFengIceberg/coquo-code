"""Deterministic bounded listing of one workspace directory."""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import stat

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition

LIST_DIRECTORY_TOOL_NAME = "list_directory"
MAX_LIST_DIRECTORY_PATH_CHARACTERS = 4096
MAX_LIST_DIRECTORY_PATH_BYTES = 4096
MAX_LIST_DIRECTORY_PATH_COMPONENTS = 64
MAX_LIST_DIRECTORY_COMPONENT_BYTES = 255
MAX_LIST_DIRECTORY_SCANNED_ENTRIES = 10_000
MAX_LIST_DIRECTORY_RESULTS = 200
MAX_LIST_DIRECTORY_OUTPUT_BYTES = 32 * 1024
LIST_DIRECTORY_TRUNCATION_MARKER = '{"truncated":true}\n'


class _ListDirectoryFailure(RuntimeError):
    """One stable model-visible directory-listing failure."""


def list_directory_model_definition() -> dict[str, object]:
    """Return a fresh provider-neutral definition of the bounded listing tool."""
    return {
        "name": LIST_DIRECTORY_TOOL_NAME,
        "description": (
            "List the direct children of one workspace-relative directory when directory "
            "structure or entry types are needed. Use '.' for the workspace root. This tool "
            "is read-only, bounded, deterministic, non-recursive, includes hidden entries, "
            "and reports regular files, directories, symbolic links, and other entries without "
            "following symbolic links. Output is JSON Lines and may be truncated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Portable workspace-relative directory path, or '.' for the workspace root."
                    ),
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def list_directory_tool_snapshot() -> CanonicalToolDefinition:
    """Freeze the canonical directory-listing definition for context identity."""
    return CanonicalToolDefinition.from_mapping(list_directory_model_definition())


class ListDirectoryTool:
    """List one directory without escaping, mutating, recursing, or following links."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def execute(self, request: ToolUse) -> ToolResult:
        """Return stable bounded JSONL entries for one requested directory."""
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"path"} or not isinstance(arguments["path"], str):
                raise _ListDirectoryFailure("list_directory input is malformed")
            parts = _validate_path(arguments["path"])
            descriptor = self._open_target(parts)
            try:
                entries = _scan_entries(descriptor, parts)
            finally:
                os.close(descriptor)
            content, truncated = _format_entries(entries)
        except (AttributeError, ValueError):
            return self._error(request, "list_directory input is malformed")
        except _ListDirectoryFailure as error:
            return self._error(request, str(error))
        return ToolResult(request.tool_use_id, content, truncated=truncated)

    def _open_target(self, parts: tuple[str, ...]) -> int:
        try:
            descriptor = _open_directory(self._workspace)
        except PermissionError:
            raise _ListDirectoryFailure("list_directory target is not accessible") from None
        except OSError:
            raise _ListDirectoryFailure("list_directory could not open the workspace") from None

        try:
            for index, component in enumerate(parts):
                try:
                    info = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    raise _ListDirectoryFailure("list_directory target does not exist") from None
                except PermissionError:
                    raise _ListDirectoryFailure("list_directory target is not accessible") from None
                except OSError:
                    raise _ListDirectoryFailure("list_directory could not inspect target") from None
                if stat.S_ISLNK(info.st_mode):
                    raise _ListDirectoryFailure(
                        "list_directory path must not contain symbolic links"
                    )
                if not stat.S_ISDIR(info.st_mode):
                    message = (
                        "list_directory target is not a directory"
                        if index == len(parts) - 1
                        else "list_directory parent path is not a directory"
                    )
                    raise _ListDirectoryFailure(message)
                try:
                    child = _open_directory_at(descriptor, component)
                except FileNotFoundError:
                    raise _ListDirectoryFailure(
                        "list_directory target changed while being opened"
                    ) from None
                except PermissionError:
                    raise _ListDirectoryFailure("list_directory target is not accessible") from None
                except OSError:
                    raise _ListDirectoryFailure(
                        "list_directory target changed while being opened"
                    ) from None
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _error(request: ToolUse, content: str) -> ToolResult:
        return ToolResult(request.tool_use_id, content, is_error=True)


def _validate_path(raw_path: str) -> tuple[str, ...]:
    try:
        encoded = raw_path.encode("utf-8")
    except UnicodeEncodeError:
        raise _ListDirectoryFailure("list_directory path must be valid UTF-8") from None
    if (
        not raw_path
        or not raw_path.strip()
        or len(raw_path) > MAX_LIST_DIRECTORY_PATH_CHARACTERS
        or len(encoded) > MAX_LIST_DIRECTORY_PATH_BYTES
        or "\x00" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).drive
    ):
        raise _ListDirectoryFailure(
            "list_directory path must be a portable workspace-relative directory path or '.'"
        )
    if raw_path == ".":
        return ()
    parts = tuple(raw_path.split("/"))
    if (
        not parts
        or len(parts) > MAX_LIST_DIRECTORY_PATH_COMPONENTS
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise _ListDirectoryFailure(
            "list_directory path must be a portable workspace-relative directory path or '.'"
        )
    for part in parts:
        try:
            part_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            raise _ListDirectoryFailure("list_directory path must be valid UTF-8") from None
        if len(part_bytes) > MAX_LIST_DIRECTORY_COMPONENT_BYTES:
            raise _ListDirectoryFailure(
                f"list_directory path component exceeds {MAX_LIST_DIRECTORY_COMPONENT_BYTES} bytes"
            )
    return parts


def _scan_entries(descriptor: int, parts: tuple[str, ...]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    try:
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_LIST_DIRECTORY_SCANNED_ENTRIES:
                    raise _ListDirectoryFailure(
                        "list_directory entry scan limit reached; choose a narrower directory"
                    )
                try:
                    entry.name.encode("utf-8")
                except UnicodeEncodeError:
                    raise _ListDirectoryFailure(
                        "list_directory encountered a path that is not valid UTF-8"
                    ) from None
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    raise _ListDirectoryFailure(
                        "list_directory directory changed while being listed"
                    ) from None
                except PermissionError:
                    raise _ListDirectoryFailure(
                        "list_directory encountered an inaccessible entry"
                    ) from None
                except OSError:
                    raise _ListDirectoryFailure(
                        "list_directory could not inspect an entry"
                    ) from None
                entry_type = _entry_type(info.st_mode)
                relative_path = "/".join(parts + (entry.name,))
                entries.append((relative_path, entry_type))
    except _ListDirectoryFailure:
        raise
    except PermissionError:
        raise _ListDirectoryFailure("list_directory target is not accessible") from None
    except OSError:
        raise _ListDirectoryFailure("list_directory could not scan target") from None
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    return entries


def _entry_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _format_entries(entries: list[tuple[str, str]]) -> tuple[str, bool]:
    lines = [
        json.dumps(
            {"path": path, "type": entry_type},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for path, entry_type in entries[:MAX_LIST_DIRECTORY_RESULTS]
    ]
    count_truncated = len(entries) > MAX_LIST_DIRECTORY_RESULTS
    complete = "".join(lines)
    if not count_truncated and len(complete.encode("utf-8")) <= MAX_LIST_DIRECTORY_OUTPUT_BYTES:
        return complete, False

    marker_bytes = len(LIST_DIRECTORY_TRUNCATION_MARKER.encode("utf-8"))
    selected: list[str] = []
    output_bytes = 0
    for line in lines:
        line_bytes = len(line.encode("utf-8"))
        if output_bytes + line_bytes + marker_bytes > MAX_LIST_DIRECTORY_OUTPUT_BYTES:
            break
        selected.append(line)
        output_bytes += line_bytes
    selected.append(LIST_DIRECTORY_TRUNCATION_MARKER)
    return "".join(selected), True


def _open_directory(path: Path) -> int:
    return os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )


def _open_directory_at(parent_descriptor: int, name: str) -> int:
    return os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )
