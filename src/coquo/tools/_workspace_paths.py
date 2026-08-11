"""Shared strict path handling for new bounded workspace tools."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import stat

MAX_WORKSPACE_PATH_CHARACTERS = 4096
MAX_WORKSPACE_PATH_BYTES = 4096
MAX_WORKSPACE_PATH_COMPONENTS = 64
MAX_WORKSPACE_COMPONENT_BYTES = 255


class WorkspacePathFailure(RuntimeError):
    """One safe tool-specific workspace path diagnostic."""


def validate_workspace_path(
    raw_path: str,
    *,
    tool_name: str,
    allow_root: bool,
) -> tuple[str, ...]:
    """Validate one strict portable path and return POSIX components."""
    try:
        encoded = raw_path.encode("utf-8")
    except UnicodeEncodeError:
        raise WorkspacePathFailure(f"{tool_name} path must be valid UTF-8") from None
    if (
        not raw_path
        or not raw_path.strip()
        or len(raw_path) > MAX_WORKSPACE_PATH_CHARACTERS
        or len(encoded) > MAX_WORKSPACE_PATH_BYTES
        or "\x00" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).drive
    ):
        raise WorkspacePathFailure(f"{tool_name} path must be a portable workspace-relative path")
    if allow_root and raw_path == ".":
        return ()
    parts = tuple(raw_path.split("/"))
    if (
        not parts
        or len(parts) > MAX_WORKSPACE_PATH_COMPONENTS
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise WorkspacePathFailure(f"{tool_name} path must be a portable workspace-relative path")
    for part in parts:
        try:
            part_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            raise WorkspacePathFailure(f"{tool_name} path must be valid UTF-8") from None
        if len(part_bytes) > MAX_WORKSPACE_COMPONENT_BYTES:
            raise WorkspacePathFailure(
                f"{tool_name} path component exceeds {MAX_WORKSPACE_COMPONENT_BYTES} bytes"
            )
    return parts


def open_workspace_directory(workspace: Path, *, tool_name: str) -> int:
    try:
        return os.open(
            workspace,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except PermissionError:
        raise WorkspacePathFailure(f"{tool_name} workspace is not accessible") from None
    except OSError:
        raise WorkspacePathFailure(f"{tool_name} could not open the workspace") from None


def open_directory_path(
    workspace: Path,
    parts: tuple[str, ...],
    *,
    tool_name: str,
) -> int:
    """Open one real directory path component by component without following links."""
    descriptor = open_workspace_directory(workspace, tool_name=tool_name)
    try:
        for index, component in enumerate(parts):
            try:
                info = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                raise WorkspacePathFailure(f"{tool_name} target does not exist") from None
            except PermissionError:
                raise WorkspacePathFailure(f"{tool_name} target is not accessible") from None
            except OSError:
                raise WorkspacePathFailure(f"{tool_name} could not inspect target") from None
            if stat.S_ISLNK(info.st_mode):
                raise WorkspacePathFailure(f"{tool_name} path must not contain symbolic links")
            if not stat.S_ISDIR(info.st_mode):
                label = "target" if index == len(parts) - 1 else "parent path"
                raise WorkspacePathFailure(f"{tool_name} {label} is not a directory")
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                raise WorkspacePathFailure(
                    f"{tool_name} target changed while being opened"
                ) from None
            except PermissionError:
                raise WorkspacePathFailure(f"{tool_name} target is not accessible") from None
            except OSError:
                raise WorkspacePathFailure(
                    f"{tool_name} target changed while being opened"
                ) from None
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_parent_directory(
    workspace: Path,
    parts: tuple[str, ...],
    *,
    tool_name: str,
) -> tuple[int, str]:
    if not parts:
        raise ValueError("file path must contain one component")
    return open_directory_path(workspace, parts[:-1], tool_name=tool_name), parts[-1]
