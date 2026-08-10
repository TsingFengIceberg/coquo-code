"""Deterministic bounded recursive workspace tree inspection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools._workspace_paths import (
    WorkspacePathFailure,
    open_directory_path,
    validate_workspace_path,
)

LIST_TREE_TOOL_NAME = "list_tree"
MAX_LIST_TREE_DEPTH = 16
MAX_LIST_TREE_SCANNED_ENTRIES = 10_000
MAX_LIST_TREE_SCANNED_DIRECTORIES = 1_000
MAX_LIST_TREE_RESULTS = 500
MAX_LIST_TREE_OUTPUT_BYTES = 32 * 1024
LIST_TREE_TRUNCATION_SENTINEL = '{"truncated":true}\n'


class _ListTreeFailure(RuntimeError):
    pass


def list_tree_model_definition() -> dict[str, object]:
    return {
        "name": LIST_TREE_TOOL_NAME,
        "description": (
            "Recursively list a bounded workspace directory tree through a requested depth. "
            "Use '.' for the workspace root. This read-only deterministic tool includes hidden "
            "entries and types but never follows symbolic links or reads file content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative directory, or '.' for root.",
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIST_TREE_DEPTH,
                    "description": "Maximum descendant depth to enumerate.",
                },
            },
            "required": ["path", "max_depth"],
            "additionalProperties": False,
        },
    }


def list_tree_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(list_tree_model_definition())


class ListTreeTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def execute(self, request: ToolUse) -> ToolResult:
        descriptor: int | None = None
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"max_depth", "path"}:
                raise _ListTreeFailure("list_tree input is malformed")
            raw_path = arguments["path"]
            max_depth = arguments["max_depth"]
            if (
                not isinstance(raw_path, str)
                or type(max_depth) is not int
                or not 1 <= max_depth <= MAX_LIST_TREE_DEPTH
            ):
                raise _ListTreeFailure("list_tree input is malformed")
            parts = validate_workspace_path(
                raw_path,
                tool_name=LIST_TREE_TOOL_NAME,
                allow_root=True,
            )
            descriptor = open_directory_path(
                self._workspace,
                parts,
                tool_name=LIST_TREE_TOOL_NAME,
            )
            entries = _scan_tree(descriptor, parts, max_depth)
            content, truncated = _format_entries(entries)
        except (AttributeError, ValueError):
            return self._error(request, "list_tree input is malformed")
        except (WorkspacePathFailure, _ListTreeFailure) as error:
            return self._error(request, str(error))
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return ToolResult(request.tool_use_id, content, truncated=truncated)

    @staticmethod
    def _error(request: ToolUse, message: str) -> ToolResult:
        return ToolResult(request.tool_use_id, message, is_error=True)


def _scan_tree(
    root_descriptor: int,
    root_parts: tuple[str, ...],
    max_depth: int,
) -> list[tuple[str, str, int]]:
    results: list[tuple[str, str, int]] = []
    scanned_entries = 0
    scanned_directories = 1

    def walk(descriptor: int, relative: tuple[str, ...], depth: int) -> None:
        nonlocal scanned_entries, scanned_directories
        try:
            with os.scandir(descriptor) as iterator:
                entries = list(iterator)
        except PermissionError:
            raise _ListTreeFailure("list_tree encountered an inaccessible directory") from None
        except OSError:
            raise _ListTreeFailure("list_tree could not scan a directory") from None
        try:
            entries.sort(key=lambda entry: entry.name.encode("utf-8"))
        except UnicodeEncodeError:
            raise _ListTreeFailure("list_tree encountered a path that is not valid UTF-8") from None

        for entry in entries:
            scanned_entries += 1
            if scanned_entries > MAX_LIST_TREE_SCANNED_ENTRIES:
                raise _ListTreeFailure(
                    "list_tree entry scan limit reached; choose a narrower path or depth"
                )
            try:
                entry.name.encode("utf-8")
                info = entry.stat(follow_symlinks=False)
            except UnicodeEncodeError:
                raise _ListTreeFailure(
                    "list_tree encountered a path that is not valid UTF-8"
                ) from None
            except FileNotFoundError:
                raise _ListTreeFailure("list_tree tree changed while being listed") from None
            except PermissionError:
                raise _ListTreeFailure("list_tree encountered an inaccessible entry") from None
            except OSError:
                raise _ListTreeFailure("list_tree could not inspect an entry") from None
            child_relative = relative + (entry.name,)
            results.append(("/".join(child_relative), _entry_type(info.st_mode), depth))
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or depth >= max_depth:
                continue
            scanned_directories += 1
            if scanned_directories > MAX_LIST_TREE_SCANNED_DIRECTORIES:
                raise _ListTreeFailure(
                    "list_tree directory scan limit reached; choose a narrower path or depth"
                )
            try:
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            except (FileNotFoundError, PermissionError, OSError):
                raise _ListTreeFailure("list_tree tree changed while being listed") from None
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise _ListTreeFailure("list_tree tree changed while being listed")
                walk(child_fd, child_relative, depth + 1)
            finally:
                os.close(child_fd)

    walk(root_descriptor, root_parts, 1)
    results.sort(key=lambda item: item[0].encode("utf-8"))
    return results


def _entry_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _format_entries(entries: list[tuple[str, str, int]]) -> tuple[str, bool]:
    records = [
        json.dumps(
            {"depth": depth, "path": path, "type": entry_type},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for path, entry_type, depth in entries[:MAX_LIST_TREE_RESULTS]
    ]
    count_truncated = len(entries) > MAX_LIST_TREE_RESULTS
    complete = "".join(records)
    if not count_truncated and len(complete.encode("utf-8")) <= MAX_LIST_TREE_OUTPUT_BYTES:
        return complete, False
    sentinel_bytes = len(LIST_TREE_TRUNCATION_SENTINEL.encode("utf-8"))
    selected: list[str] = []
    output_bytes = 0
    for record in records:
        record_bytes = len(record.encode("utf-8"))
        if output_bytes + record_bytes + sentinel_bytes > MAX_LIST_TREE_OUTPUT_BYTES:
            break
        selected.append(record)
        output_bytes += record_bytes
    selected.append(LIST_TREE_TRUNCATION_SENTINEL)
    return "".join(selected), True
