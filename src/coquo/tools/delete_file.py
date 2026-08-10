"""Controlled deletion of one workspace regular file."""

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

DELETE_FILE_TOOL_NAME = "delete_file"
MAX_DELETE_PATH_CHARACTERS = 4096
MAX_DELETE_PATH_BYTES = 4096
MAX_DELETE_PATH_COMPONENTS = 64
MAX_DELETE_COMPONENT_BYTES = 255


@dataclass(frozen=True)
class _ObservedParent:
    device: int
    inode: int


@dataclass(frozen=True)
class _ObservedFile:
    device: int
    inode: int
    mode: int
    size: int
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
                "size": self.size,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PreparedDeleteFile:
    """One immutable regular-file identity prepared without mutation."""

    request: ToolUse
    path: str
    parent: _ObservedParent
    target_state: _ObservedFile
    action: PermissionAction
    precondition: ActionPrecondition


class DeleteFilePreparationError(ValueError):
    """A hard-bound rejection before deletion is permission-eligible."""


class DeleteFileOutcome(StrEnum):
    """Known deletion outcomes including durability uncertainty."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class DeleteFileExecutionResult:
    """One truthful model result plus stable Host audit attribution."""

    tool_result: ToolResult
    outcome: DeleteFileOutcome
    result_code: str
    audit_message: str


class DeleteFileTool:
    """Delete one regular file without following symlinks or deleting directories."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def prepare(self, request: ToolUse) -> PreparedDeleteFile:
        """Validate the path and bind the exact target and parent identities."""
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"path"} or not isinstance(arguments["path"], str):
                raise ValueError
            raw_path = arguments["path"]
        except (AttributeError, ValueError):
            raise DeleteFilePreparationError("delete_file input is malformed") from None

        path, parent_path, name, parent = self._resolve_path(raw_path)
        target = self._observe_target(parent_path / name)
        return PreparedDeleteFile(
            request=request,
            path=path,
            parent=parent,
            target_state=target,
            action=PermissionAction.WORKSPACE_DELETE,
            precondition=self._precondition(path, parent, target),
        )

    def refresh_precondition(self, prepared: PreparedDeleteFile) -> ActionPrecondition:
        """Re-observe the exact target for approval and stale-state checks."""
        if type(prepared) is not PreparedDeleteFile:
            raise ValueError("prepared delete_file is invalid")
        try:
            path, parent_path, name, parent = self._resolve_path(prepared.path)
            target = self._observe_target(parent_path / name)
        except DeleteFilePreparationError as error:
            return ActionPrecondition.expected_state(
                hashlib.sha256(f"invalid:{error}".encode("utf-8")).hexdigest()
            )
        return self._precondition(path, parent, target)

    def execute(self, prepared: PreparedDeleteFile) -> ToolResult:
        """Apply one prepared deletion and return its model-visible result."""
        return self.execute_detailed(prepared).tool_result

    def execute_detailed(self, prepared: PreparedDeleteFile) -> DeleteFileExecutionResult:
        """Revalidate through the real parent, unlink the name, and fsync the parent."""
        if type(prepared) is not PreparedDeleteFile:
            raise ValueError("prepared delete_file is invalid")
        request = prepared.request
        if prepared.precondition.kind != ActionPreconditionKind.EXPECTED_STATE_SHA256:
            return self._failed(request, "delete_file precondition is invalid")
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(request, "delete_file conflict: target changed")

        parent_path = self._workspace / Path(prepared.path).parent
        name = Path(prepared.path).name
        parent_fd: int | None = None
        deleted = False
        try:
            parent_fd = _open_directory(parent_path)
            _assert_parent_identity(parent_fd, prepared.parent)
            current = _observe_target_at(parent_fd, name)
            if current != prepared.target_state:
                raise DeleteFilePreparationError("delete_file conflict: target changed")
            os.unlink(name, dir_fd=parent_fd)
            deleted = True
            try:
                _fsync_directory(parent_fd)
            except OSError:
                message = (
                    "delete_file removed the file, but deletion durability is unknown; "
                    "inspect the path and do not retry automatically"
                )
                return DeleteFileExecutionResult(
                    ToolResult(request.tool_use_id, message, is_error=True),
                    DeleteFileOutcome.PARTIAL,
                    "file_deleted_durability_unknown",
                    message,
                )
        except DeleteFilePreparationError as error:
            return self._failed(request, str(error))
        except FileNotFoundError:
            return self._failed(request, "delete_file conflict: target no longer exists")
        except PermissionError:
            if deleted:
                return self._durability_unknown(request)
            return self._failed(request, "delete_file target is not accessible")
        except OSError:
            if deleted:
                return self._durability_unknown(request)
            return self._failed(request, "delete_file could not delete the file")
        finally:
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
        return DeleteFileExecutionResult(
            ToolResult(request.tool_use_id, payload),
            DeleteFileOutcome.SUCCEEDED,
            "file_deleted",
            f"delete_file deleted {prepared.path}",
        )

    @staticmethod
    def _failed(request: ToolUse, message: str) -> DeleteFileExecutionResult:
        return DeleteFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            DeleteFileOutcome.FAILED,
            "file_not_deleted",
            message,
        )

    @staticmethod
    def _durability_unknown(request: ToolUse) -> DeleteFileExecutionResult:
        message = (
            "delete_file removed the file, but deletion durability is unknown; "
            "inspect the path and do not retry automatically"
        )
        return DeleteFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            DeleteFileOutcome.PARTIAL,
            "file_deleted_durability_unknown",
            message,
        )

    def _resolve_path(self, raw_path: str) -> tuple[str, Path, str, _ObservedParent]:
        parts = _validate_path(raw_path)
        current = self._workspace
        for component in parts[:-1]:
            current /= component
            try:
                info = current.lstat()
            except FileNotFoundError:
                raise DeleteFilePreparationError(
                    "delete_file parent directory does not exist"
                ) from None
            except PermissionError:
                raise DeleteFilePreparationError(
                    "delete_file parent directory is not accessible"
                ) from None
            except OSError:
                raise DeleteFilePreparationError(
                    "delete_file could not inspect parent directory"
                ) from None
            if stat.S_ISLNK(info.st_mode):
                raise DeleteFilePreparationError("delete_file path contains a symbolic link")
            if not stat.S_ISDIR(info.st_mode):
                raise DeleteFilePreparationError("delete_file parent path is not a directory")
        try:
            parent_info = current.lstat()
        except (FileNotFoundError, PermissionError, OSError):
            raise DeleteFilePreparationError(
                "delete_file parent directory is not accessible"
            ) from None
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
            raise DeleteFilePreparationError("delete_file parent path is not a directory")
        return (
            "/".join(parts),
            current,
            parts[-1],
            _ObservedParent(parent_info.st_dev, parent_info.st_ino),
        )

    @staticmethod
    def _observe_target(target: Path) -> _ObservedFile:
        try:
            info = target.lstat()
        except FileNotFoundError:
            raise DeleteFilePreparationError("delete_file target does not exist") from None
        except PermissionError:
            raise DeleteFilePreparationError("delete_file target is not accessible") from None
        except OSError:
            raise DeleteFilePreparationError("delete_file could not inspect target") from None
        return _target_from_stat(info)

    @staticmethod
    def _precondition(
        path: str, parent: _ObservedParent, target: _ObservedFile
    ) -> ActionPrecondition:
        payload = json.dumps(
            {
                "parent": [parent.device, parent.inode],
                "path": path,
                "target_state": target.fingerprint,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return ActionPrecondition.expected_state(hashlib.sha256(payload).hexdigest())


def delete_file_model_definition() -> dict[str, object]:
    """Return the exact provider-neutral controlled file-deletion definition."""
    return {
        "name": DELETE_FILE_TOOL_NAME,
        "description": (
            "Permanently delete one existing workspace-relative regular file. The Host applies "
            "workspace-delete permission and approval policy, rejects symlinks, stale paths, and "
            "directories, and requires the parent directory to already exist. This does not "
            "provide recursive deletion or recovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of the regular file to delete.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def delete_file_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(delete_file_model_definition())


def _validate_path(raw_path: str) -> tuple[str, ...]:
    try:
        encoded = raw_path.encode("utf-8")
    except UnicodeEncodeError:
        raise DeleteFilePreparationError("delete_file path must be valid UTF-8") from None
    if (
        not raw_path
        or not raw_path.strip()
        or len(raw_path) > MAX_DELETE_PATH_CHARACTERS
        or len(encoded) > MAX_DELETE_PATH_BYTES
        or "\x00" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).drive
    ):
        raise DeleteFilePreparationError(
            "delete_file path must be a portable workspace-relative file path"
        )
    parts = tuple(raw_path.split("/"))
    if (
        not parts
        or len(parts) > MAX_DELETE_PATH_COMPONENTS
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise DeleteFilePreparationError(
            "delete_file path must be a portable workspace-relative file path"
        )
    for part in parts:
        try:
            part_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            raise DeleteFilePreparationError("delete_file path must be valid UTF-8") from None
        if len(part_bytes) > MAX_DELETE_COMPONENT_BYTES:
            raise DeleteFilePreparationError(
                f"delete_file path component exceeds {MAX_DELETE_COMPONENT_BYTES} bytes"
            )
    return parts


def _target_from_stat(info: os.stat_result) -> _ObservedFile:
    if stat.S_ISLNK(info.st_mode):
        raise DeleteFilePreparationError("delete_file target must not be a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise DeleteFilePreparationError("delete_file target must be a regular file")
    return _ObservedFile(
        device=info.st_dev,
        inode=info.st_ino,
        mode=info.st_mode,
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
        changed_ns=info.st_ctime_ns,
        link_count=info.st_nlink,
    )


def _open_directory(path: Path) -> int:
    return os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )


def _assert_parent_identity(descriptor: int, expected: _ObservedParent) -> None:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
        expected.device,
        expected.inode,
    ):
        raise DeleteFilePreparationError("delete_file conflict: parent changed")


def _observe_target_at(descriptor: int, name: str) -> _ObservedFile:
    try:
        observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        raise DeleteFilePreparationError("delete_file conflict: target no longer exists") from None
    return _target_from_stat(observed)


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)
