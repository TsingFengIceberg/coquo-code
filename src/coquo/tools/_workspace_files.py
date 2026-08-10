"""Shared no-follow reads for bounded workspace regular-file tools."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from coquo.tools._workspace_paths import (
    WorkspacePathFailure,
    open_parent_directory,
    validate_workspace_path,
)


@dataclass(frozen=True)
class WorkspaceFileSnapshot:
    relative_path: str
    size: int
    mode: int
    data: bytes


def read_workspace_regular_file(
    workspace: Path,
    raw_path: str,
    *,
    tool_name: str,
    max_bytes: int,
) -> WorkspaceFileSnapshot:
    """Read one stable regular file through parent and target no-follow descriptors."""
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("workspace file byte limit is invalid")
    parts = validate_workspace_path(raw_path, tool_name=tool_name, allow_root=False)
    parent_fd, name = open_parent_directory(workspace, parts, tool_name=tool_name)
    descriptor: int | None = None
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise WorkspacePathFailure(f"{tool_name} target does not exist") from None
        except PermissionError:
            raise WorkspacePathFailure(f"{tool_name} target is not accessible") from None
        except OSError:
            raise WorkspacePathFailure(f"{tool_name} could not inspect target") from None
        if stat.S_ISLNK(before.st_mode):
            raise WorkspacePathFailure(f"{tool_name} path must not contain symbolic links")
        if not stat.S_ISREG(before.st_mode):
            raise WorkspacePathFailure(f"{tool_name} target is not a regular file")
        if before.st_size > max_bytes:
            raise WorkspacePathFailure(f"{tool_name} source exceeds {max_bytes} bytes")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except PermissionError:
            raise WorkspacePathFailure(f"{tool_name} target is not accessible") from None
        except OSError:
            raise WorkspacePathFailure(f"{tool_name} target changed before reading") from None
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
            raise WorkspacePathFailure(f"{tool_name} target changed before reading")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise WorkspacePathFailure(f"{tool_name} source exceeds {max_bytes} bytes")
        after = os.fstat(descriptor)
        if _identity(opened) != _identity(after):
            raise WorkspacePathFailure(f"{tool_name} target changed while reading")
        return WorkspaceFileSnapshot(
            relative_path="/".join(parts),
            size=after.st_size,
            mode=stat.S_IMODE(after.st_mode),
            data=b"".join(chunks),
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def sha256_workspace_regular_file(
    workspace: Path,
    raw_path: str,
    *,
    tool_name: str,
    max_bytes: int,
) -> tuple[str, int, str]:
    """Hash one stable bounded regular file without retaining its bytes."""
    parts = validate_workspace_path(raw_path, tool_name=tool_name, allow_root=False)
    parent_fd, name = open_parent_directory(workspace, parts, tool_name=tool_name)
    descriptor: int | None = None
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise WorkspacePathFailure(f"{tool_name} path must not contain symbolic links")
        if not stat.S_ISREG(before.st_mode):
            raise WorkspacePathFailure(f"{tool_name} target is not a regular file")
        if before.st_size > max_bytes:
            raise WorkspacePathFailure(f"{tool_name} source exceeds {max_bytes} bytes")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if _identity(before) != _identity(opened):
            raise WorkspacePathFailure(f"{tool_name} target changed before reading")
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, 64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise WorkspacePathFailure(f"{tool_name} source exceeds {max_bytes} bytes")
            digest.update(chunk)
        if _identity(opened) != _identity(os.fstat(descriptor)):
            raise WorkspacePathFailure(f"{tool_name} target changed while reading")
        return "/".join(parts), total, digest.hexdigest()
    except FileNotFoundError:
        raise WorkspacePathFailure(f"{tool_name} target does not exist") from None
    except PermissionError:
        raise WorkspacePathFailure(f"{tool_name} target is not accessible") from None
    except WorkspacePathFailure:
        raise
    except OSError:
        raise WorkspacePathFailure(f"{tool_name} could not read target") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )
