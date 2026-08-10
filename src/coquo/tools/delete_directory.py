"""Controlled deletion of one empty workspace directory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat

from coquo.core.actions import ActionPrecondition, ActionPreconditionKind
from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.permissions import PermissionAction

DELETE_DIRECTORY_TOOL_NAME = "delete_directory"
MAX_DELETE_DIRECTORY_PATH_CHARACTERS = 4096
MAX_DELETE_DIRECTORY_PATH_BYTES = 4096
MAX_DELETE_DIRECTORY_PATH_COMPONENTS = 64
MAX_DELETE_DIRECTORY_COMPONENT_BYTES = 255


@dataclass(frozen=True)
class _ObservedParent:
    device: int
    inode: int


@dataclass(frozen=True)
class _ObservedDirectory:
    device: int
    inode: int
    mode: int
    modified_ns: int
    changed_ns: int
    link_count: int

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "changed_ns": self.changed_ns,
                "device": self.device,
                "inode": self.inode,
                "link_count": self.link_count,
                "mode": self.mode,
                "modified_ns": self.modified_ns,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PreparedDeleteDirectory:
    """One immutable empty-directory identity prepared without mutation."""

    request: ToolUse
    path: str
    parent: _ObservedParent
    target_state: _ObservedDirectory
    action: PermissionAction
    precondition: ActionPrecondition


class DeleteDirectoryPreparationError(ValueError):
    """A hard-bound rejection before directory deletion is permission-eligible."""


class DeleteDirectoryOutcome(StrEnum):
    """Known directory-deletion outcomes including durability uncertainty."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class DeleteDirectoryExecutionResult:
    """One truthful model result plus stable Host audit attribution."""

    tool_result: ToolResult
    outcome: DeleteDirectoryOutcome
    result_code: str
    audit_message: str


class DeleteDirectoryTool:
    """Delete one empty directory without following symlinks or recursing."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def prepare(self, request: ToolUse) -> PreparedDeleteDirectory:
        """Validate the path and bind the exact empty target and parent identities."""
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"path"} or not isinstance(arguments["path"], str):
                raise ValueError
            raw_path = arguments["path"]
        except (AttributeError, ValueError):
            raise DeleteDirectoryPreparationError("delete_directory input is malformed") from None

        path, parent_path, name, parent = self._resolve_path(raw_path)
        target = self._observe_target(parent_path / name)
        self._assert_empty(parent_path / name)
        return PreparedDeleteDirectory(
            request=request,
            path=path,
            parent=parent,
            target_state=target,
            action=PermissionAction.WORKSPACE_DELETE,
            precondition=self._precondition(path, parent, target),
        )

    def refresh_precondition(self, prepared: PreparedDeleteDirectory) -> ActionPrecondition:
        """Re-observe the exact empty target for approval and stale-state checks."""
        if type(prepared) is not PreparedDeleteDirectory:
            raise ValueError("prepared delete_directory is invalid")
        try:
            path, parent_path, name, parent = self._resolve_path(prepared.path)
            target = self._observe_target(parent_path / name)
            self._assert_empty(parent_path / name)
        except DeleteDirectoryPreparationError as error:
            return ActionPrecondition.expected_state(
                hashlib.sha256(f"invalid:{error}".encode("utf-8")).hexdigest()
            )
        return self._precondition(path, parent, target)

    def execute(self, prepared: PreparedDeleteDirectory) -> ToolResult:
        """Apply one prepared directory deletion and return its model-visible result."""
        return self.execute_detailed(prepared).tool_result

    def execute_detailed(self, prepared: PreparedDeleteDirectory) -> DeleteDirectoryExecutionResult:
        """Revalidate through real descriptors, rmdir the name, and fsync the parent."""
        if type(prepared) is not PreparedDeleteDirectory:
            raise ValueError("prepared delete_directory is invalid")
        request = prepared.request
        if prepared.precondition.kind != ActionPreconditionKind.EXPECTED_STATE_SHA256:
            return self._failed(request, "delete_directory precondition is invalid")
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(request, "delete_directory conflict: target changed")

        parent_path = self._workspace / Path(prepared.path).parent
        name = Path(prepared.path).name
        parent_fd: int | None = None
        target_fd: int | None = None
        deleted = False
        try:
            parent_fd = _open_directory(parent_path)
            _assert_parent_identity(parent_fd, prepared.parent)
            current = _observe_target_at(parent_fd, name)
            if current != prepared.target_state:
                raise DeleteDirectoryPreparationError("delete_directory conflict: target changed")
            target_fd = _open_directory_at(parent_fd, name)
            _assert_target_identity(target_fd, prepared.target_state)
            _assert_empty_at(target_fd)
            os.close(target_fd)
            target_fd = None
            os.rmdir(name, dir_fd=parent_fd)
            deleted = True
            try:
                _fsync_directory(parent_fd)
            except OSError:
                return self._durability_unknown(request)
        except DeleteDirectoryPreparationError as error:
            return self._failed(request, str(error))
        except FileNotFoundError:
            return self._failed(request, "delete_directory conflict: target no longer exists")
        except PermissionError:
            if deleted:
                return self._durability_unknown(request)
            return self._failed(request, "delete_directory target is not accessible")
        except OSError:
            if deleted:
                return self._durability_unknown(request)
            return self._failed(request, "delete_directory could not delete the directory")
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if parent_fd is not None:
                os.close(parent_fd)

        payload = (
            json.dumps(
                {"operation": "deleted", "path": prepared.path},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return DeleteDirectoryExecutionResult(
            ToolResult(request.tool_use_id, payload),
            DeleteDirectoryOutcome.SUCCEEDED,
            "directory_deleted",
            f"delete_directory deleted {prepared.path}",
        )

    @staticmethod
    def _failed(request: ToolUse, message: str) -> DeleteDirectoryExecutionResult:
        return DeleteDirectoryExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            DeleteDirectoryOutcome.FAILED,
            "directory_not_deleted",
            message,
        )

    @staticmethod
    def _durability_unknown(request: ToolUse) -> DeleteDirectoryExecutionResult:
        message = (
            "delete_directory removed the directory, but deletion durability is unknown; "
            "inspect the path and do not retry automatically"
        )
        return DeleteDirectoryExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            DeleteDirectoryOutcome.PARTIAL,
            "directory_deleted_durability_unknown",
            message,
        )

    def _resolve_path(self, raw_path: str) -> tuple[str, Path, str, _ObservedParent]:
        parts = _validate_path(raw_path)
        current = self._workspace
        for component in parts[:-1]:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                raise DeleteDirectoryPreparationError(
                    "delete_directory parent directory does not exist"
                ) from None
            except PermissionError:
                raise DeleteDirectoryPreparationError(
                    "delete_directory parent directory is not accessible"
                ) from None
            except OSError:
                raise DeleteDirectoryPreparationError(
                    "delete_directory could not inspect parent directory"
                ) from None
            if stat.S_ISLNK(info.st_mode):
                raise DeleteDirectoryPreparationError(
                    "delete_directory path contains a symbolic link"
                )
            if not stat.S_ISDIR(info.st_mode):
                raise DeleteDirectoryPreparationError(
                    "delete_directory parent path is not a directory"
                )
        try:
            parent_info = current.lstat()
        except PermissionError:
            raise DeleteDirectoryPreparationError(
                "delete_directory parent directory is not accessible"
            ) from None
        if not stat.S_ISDIR(parent_info.st_mode):
            raise DeleteDirectoryPreparationError("delete_directory parent path is not a directory")
        path = "/".join(parts)
        return (
            path,
            current,
            parts[-1],
            _ObservedParent(parent_info.st_dev, parent_info.st_ino),
        )

    @staticmethod
    def _observe_target(target: Path) -> _ObservedDirectory:
        try:
            info = target.lstat()
        except FileNotFoundError:
            raise DeleteDirectoryPreparationError(
                "delete_directory target does not exist"
            ) from None
        except PermissionError:
            raise DeleteDirectoryPreparationError(
                "delete_directory target is not accessible"
            ) from None
        except OSError:
            raise DeleteDirectoryPreparationError(
                "delete_directory could not inspect target"
            ) from None
        return _target_from_stat(info)

    @staticmethod
    def _assert_empty(target: Path) -> None:
        try:
            with os.scandir(target) as entries:
                if next(entries, None) is not None:
                    raise DeleteDirectoryPreparationError("delete_directory target must be empty")
        except DeleteDirectoryPreparationError:
            raise
        except PermissionError:
            raise DeleteDirectoryPreparationError(
                "delete_directory target is not accessible"
            ) from None
        except OSError:
            raise DeleteDirectoryPreparationError(
                "delete_directory could not inspect target"
            ) from None

    @staticmethod
    def _precondition(
        path: str, parent: _ObservedParent, target: _ObservedDirectory
    ) -> ActionPrecondition:
        payload = json.dumps(
            {
                "empty": True,
                "parent": [parent.device, parent.inode],
                "path": path,
                "target_state": target.fingerprint,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return ActionPrecondition.expected_state(hashlib.sha256(payload).hexdigest())


def delete_directory_model_definition() -> dict[str, object]:
    """Return the exact provider-neutral controlled empty-directory definition."""
    return {
        "name": DELETE_DIRECTORY_TOOL_NAME,
        "description": (
            "Permanently delete one existing empty workspace-relative directory. The Host "
            "applies workspace-delete permission and approval policy, rejects symlinks, stale "
            "paths, non-empty directories, and the workspace root, and requires the parent "
            "directory to already exist. This does not provide recursive deletion or recovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of the empty directory to delete.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def delete_directory_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(delete_directory_model_definition())


def _validate_path(raw_path: str) -> tuple[str, ...]:
    try:
        encoded = raw_path.encode("utf-8")
    except UnicodeEncodeError:
        raise DeleteDirectoryPreparationError("delete_directory path must be valid UTF-8") from None
    if (
        not raw_path
        or not raw_path.strip()
        or len(raw_path) > MAX_DELETE_DIRECTORY_PATH_CHARACTERS
        or len(encoded) > MAX_DELETE_DIRECTORY_PATH_BYTES
        or "\x00" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).drive
    ):
        raise DeleteDirectoryPreparationError(
            "delete_directory path must be a portable workspace-relative directory path"
        )
    parts = tuple(raw_path.split("/"))
    if (
        not parts
        or len(parts) > MAX_DELETE_DIRECTORY_PATH_COMPONENTS
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise DeleteDirectoryPreparationError(
            "delete_directory path must be a portable workspace-relative directory path"
        )
    for part in parts:
        try:
            part_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            raise DeleteDirectoryPreparationError(
                "delete_directory path must be valid UTF-8"
            ) from None
        if len(part_bytes) > MAX_DELETE_DIRECTORY_COMPONENT_BYTES:
            raise DeleteDirectoryPreparationError(
                "delete_directory path component exceeds "
                f"{MAX_DELETE_DIRECTORY_COMPONENT_BYTES} bytes"
            )
    return parts


def _target_from_stat(info: os.stat_result) -> _ObservedDirectory:
    if stat.S_ISLNK(info.st_mode):
        raise DeleteDirectoryPreparationError("delete_directory target must not be a symbolic link")
    if not stat.S_ISDIR(info.st_mode):
        raise DeleteDirectoryPreparationError("delete_directory target must be a directory")
    return _ObservedDirectory(
        device=info.st_dev,
        inode=info.st_ino,
        mode=info.st_mode,
        modified_ns=info.st_mtime_ns,
        changed_ns=info.st_ctime_ns,
        link_count=info.st_nlink,
    )


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


def _assert_parent_identity(descriptor: int, expected: _ObservedParent) -> None:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
        expected.device,
        expected.inode,
    ):
        raise DeleteDirectoryPreparationError("delete_directory conflict: parent changed")


def _assert_target_identity(descriptor: int, expected: _ObservedDirectory) -> None:
    observed = _target_from_stat(os.fstat(descriptor))
    if observed != expected:
        raise DeleteDirectoryPreparationError("delete_directory conflict: target changed")


def _observe_target_at(descriptor: int, name: str) -> _ObservedDirectory:
    try:
        observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        raise DeleteDirectoryPreparationError(
            "delete_directory conflict: target no longer exists"
        ) from None
    return _target_from_stat(observed)


def _assert_empty_at(descriptor: int) -> None:
    try:
        with os.scandir(descriptor) as entries:
            if next(entries, None) is not None:
                raise DeleteDirectoryPreparationError(
                    "delete_directory conflict: target is no longer empty"
                )
    except DeleteDirectoryPreparationError:
        raise
    except PermissionError:
        raise DeleteDirectoryPreparationError("delete_directory target is not accessible") from None
    except OSError:
        raise DeleteDirectoryPreparationError("delete_directory could not inspect target") from None


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)
