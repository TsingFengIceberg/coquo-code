"""Controlled no-overwrite movement of one workspace directory tree."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import StrEnum
import errno
import hashlib
import json
import os
from pathlib import Path
import stat

from leonervis_code.core.actions import ActionPrecondition
from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.core.permissions import PermissionAction
from leonervis_code.tools._workspace_paths import (
    WorkspacePathFailure,
    open_parent_directory,
    validate_workspace_path,
)

MOVE_DIRECTORY_TOOL_NAME = "move_directory"
_RENAME_NOREPLACE = 1


@dataclass(frozen=True)
class _DirectoryState:
    device: int
    inode: int
    mode: int
    modified_ns: int
    changed_ns: int
    link_count: int


@dataclass(frozen=True)
class _ParentState:
    device: int
    inode: int


@dataclass(frozen=True)
class PreparedMoveDirectory:
    request: ToolUse
    source_path: str
    destination_path: str
    source_parent: _ParentState
    destination_parent: _ParentState
    source_state: _DirectoryState
    action: PermissionAction
    precondition: ActionPrecondition


class MoveDirectoryPreparationError(ValueError):
    pass


class MoveDirectoryOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class MoveDirectoryExecutionResult:
    tool_result: ToolResult
    outcome: MoveDirectoryOutcome
    result_code: str
    audit_message: str


def move_directory_model_definition() -> dict[str, object]:
    return {
        "name": MOVE_DIRECTORY_TOOL_NAME,
        "description": (
            "Move one existing workspace directory tree to one missing same-filesystem "
            "destination. The Host rejects symlinked parents, descendant destinations, "
            "replacement, stale state, and platforms without atomic no-replace rename support."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Existing workspace-relative directory.",
                },
                "destination": {
                    "type": "string",
                    "description": "Missing workspace-relative destination.",
                },
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
    }


def move_directory_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(move_directory_model_definition())


class MoveDirectoryTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def prepare(self, request: ToolUse) -> PreparedMoveDirectory:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != MOVE_DIRECTORY_TOOL_NAME or set(arguments) != {
                "destination",
                "source",
            }:
                raise ValueError
            source = arguments["source"]
            destination = arguments["destination"]
            if not isinstance(source, str) or not isinstance(destination, str):
                raise ValueError
            source_parts = validate_workspace_path(
                source, tool_name=MOVE_DIRECTORY_TOOL_NAME, allow_root=False
            )
            destination_parts = validate_workspace_path(
                destination, tool_name=MOVE_DIRECTORY_TOOL_NAME, allow_root=False
            )
        except (AttributeError, ValueError, WorkspacePathFailure):
            raise MoveDirectoryPreparationError("move_directory input is malformed") from None
        if source_parts == destination_parts:
            raise MoveDirectoryPreparationError("move_directory source and destination must differ")
        if destination_parts[: len(source_parts)] == source_parts:
            raise MoveDirectoryPreparationError(
                "move_directory destination must not be inside source"
            )
        observed = self._observe(source_parts, destination_parts)
        precondition = ActionPrecondition.expected_state(
            _state_digest(source_parts, destination_parts, *observed)
        )
        return PreparedMoveDirectory(
            request,
            "/".join(source_parts),
            "/".join(destination_parts),
            observed[0],
            observed[1],
            observed[2],
            PermissionAction.WORKSPACE_MOVE,
            precondition,
        )

    def refresh_precondition(self, prepared: PreparedMoveDirectory) -> ActionPrecondition:
        try:
            source = validate_workspace_path(
                prepared.source_path, tool_name=MOVE_DIRECTORY_TOOL_NAME, allow_root=False
            )
            destination = validate_workspace_path(
                prepared.destination_path, tool_name=MOVE_DIRECTORY_TOOL_NAME, allow_root=False
            )
            observed = self._observe(source, destination)
            digest = _state_digest(source, destination, *observed)
        except (WorkspacePathFailure, MoveDirectoryPreparationError) as error:
            digest = hashlib.sha256(f"invalid:{error}".encode()).hexdigest()
        return ActionPrecondition.expected_state(digest)

    def execute_detailed(self, prepared: PreparedMoveDirectory) -> MoveDirectoryExecutionResult:
        request = prepared.request
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(request, "move_directory conflict: source or destination changed")
        source_parts = tuple(prepared.source_path.split("/"))
        destination_parts = tuple(prepared.destination_path.split("/"))
        source_fd: int | None = None
        destination_fd: int | None = None
        renamed = False
        try:
            source_fd, source_name = open_parent_directory(
                self._workspace, source_parts, tool_name=MOVE_DIRECTORY_TOOL_NAME
            )
            destination_fd, destination_name = open_parent_directory(
                self._workspace, destination_parts, tool_name=MOVE_DIRECTORY_TOOL_NAME
            )
            _assert_parent(source_fd, prepared.source_parent)
            _assert_parent(destination_fd, prepared.destination_parent)
            if _directory_at(source_fd, source_name) != prepared.source_state:
                raise MoveDirectoryPreparationError("move_directory conflict: source changed")
            if _entry_at(destination_fd, destination_name) is not None:
                raise MoveDirectoryPreparationError(
                    "move_directory conflict: destination is no longer absent"
                )
            _rename_noreplace(source_fd, source_name, destination_fd, destination_name)
            renamed = True
            try:
                os.fsync(destination_fd)
                if (prepared.source_parent.device, prepared.source_parent.inode) != (
                    prepared.destination_parent.device,
                    prepared.destination_parent.inode,
                ):
                    os.fsync(source_fd)
            except OSError:
                return MoveDirectoryExecutionResult(
                    ToolResult(
                        request.tool_use_id,
                        "move_directory moved the directory but durability is unknown; inspect both paths and do not retry automatically",
                        is_error=True,
                    ),
                    MoveDirectoryOutcome.PARTIAL,
                    "directory_moved_durability_unknown",
                    "move_directory moved the directory but parent durability is unknown",
                )
        except (WorkspacePathFailure, MoveDirectoryPreparationError) as error:
            return self._failed(request, str(error))
        except OSError as error:
            if renamed:
                return MoveDirectoryExecutionResult(
                    ToolResult(
                        request.tool_use_id,
                        "move_directory may have moved the directory; inspect both paths and do not retry automatically",
                        is_error=True,
                    ),
                    MoveDirectoryOutcome.PARTIAL,
                    "directory_move_state_unknown",
                    "move_directory state is unknown after rename",
                )
            if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                return self._failed(
                    request, "move_directory conflict: destination is no longer absent"
                )
            if error.errno == errno.EXDEV:
                return self._failed(
                    request, "move_directory source and destination must share a filesystem"
                )
            if error.errno in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
                return self._failed(
                    request, "move_directory atomic no-replace rename is unavailable"
                )
            return self._failed(request, "move_directory could not move the directory")
        finally:
            if destination_fd is not None:
                os.close(destination_fd)
            if source_fd is not None:
                os.close(source_fd)
        content = (
            json.dumps(
                {
                    "destination": prepared.destination_path,
                    "operation": "moved",
                    "source": prepared.source_path,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return MoveDirectoryExecutionResult(
            ToolResult(request.tool_use_id, content),
            MoveDirectoryOutcome.SUCCEEDED,
            "directory_moved",
            f"move_directory moved {prepared.source_path} to {prepared.destination_path}",
        )

    def _observe(
        self,
        source: tuple[str, ...],
        destination: tuple[str, ...],
    ) -> tuple[_ParentState, _ParentState, _DirectoryState]:
        source_fd: int | None = None
        destination_fd: int | None = None
        try:
            source_fd, source_name = open_parent_directory(
                self._workspace, source, tool_name=MOVE_DIRECTORY_TOOL_NAME
            )
            destination_fd, destination_name = open_parent_directory(
                self._workspace, destination, tool_name=MOVE_DIRECTORY_TOOL_NAME
            )
            source_parent = _parent_state(source_fd)
            destination_parent = _parent_state(destination_fd)
            source_state = _directory_at(source_fd, source_name)
            if source_state is None:
                raise MoveDirectoryPreparationError("move_directory source does not exist")
            if _entry_at(destination_fd, destination_name) is not None:
                raise MoveDirectoryPreparationError("move_directory destination already exists")
            if source_state.device != destination_parent.device:
                raise MoveDirectoryPreparationError(
                    "move_directory source and destination must share a filesystem"
                )
            return source_parent, destination_parent, source_state
        finally:
            if destination_fd is not None:
                os.close(destination_fd)
            if source_fd is not None:
                os.close(source_fd)

    @staticmethod
    def _failed(request: ToolUse, message: str) -> MoveDirectoryExecutionResult:
        return MoveDirectoryExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            MoveDirectoryOutcome.FAILED,
            "directory_not_moved",
            message,
        )


def _parent_state(descriptor: int) -> _ParentState:
    info = os.fstat(descriptor)
    return _ParentState(info.st_dev, info.st_ino)


def _assert_parent(descriptor: int, expected: _ParentState) -> None:
    if _parent_state(descriptor) != expected:
        raise MoveDirectoryPreparationError("move_directory parent changed")


def _entry_at(descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _directory_at(descriptor: int, name: str) -> _DirectoryState | None:
    info = _entry_at(descriptor, name)
    if info is None:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MoveDirectoryPreparationError("move_directory source must be a real directory")
    return _DirectoryState(
        info.st_dev,
        info.st_ino,
        stat.S_IMODE(info.st_mode),
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _state_digest(
    source: tuple[str, ...],
    destination: tuple[str, ...],
    source_parent: _ParentState,
    destination_parent: _ParentState,
    source_state: _DirectoryState,
) -> str:
    payload = json.dumps(
        {
            "destination": "/".join(destination),
            "destination_absent": True,
            "destination_parent": destination_parent.__dict__,
            "source": "/".join(source),
            "source_parent": source_parent.__dict__,
            "source_state": source_state.__dict__,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _rename_noreplace(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        source_fd,
        os.fsencode(source),
        destination_fd,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
