"""Controlled no-overwrite movement of one workspace regular file."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import errno
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat

from coquo.core.actions import ActionPrecondition, ActionPreconditionKind
from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.permissions import PermissionAction

MOVE_FILE_TOOL_NAME = "move_file"
MAX_MOVE_PATH_CHARACTERS = 4096
MAX_MOVE_PATH_BYTES = 4096
MAX_MOVE_PATH_COMPONENTS = 64
MAX_MOVE_COMPONENT_BYTES = 255


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
class PreparedMoveFile:
    """One immutable source identity and absent destination prepared without mutation."""

    request: ToolUse
    source_path: str
    destination_path: str
    source_parent: _ObservedParent
    destination_parent: _ObservedParent
    source_state: _ObservedFile
    action: PermissionAction
    precondition: ActionPrecondition


class MoveFilePreparationError(ValueError):
    """A hard-bound rejection before a move is permission-eligible."""


class MoveFileOutcome(StrEnum):
    """Known move outcomes including visible partial effects."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class MoveFileExecutionResult:
    """One truthful model result plus stable Host audit attribution."""

    tool_result: ToolResult
    outcome: MoveFileOutcome
    result_code: str
    audit_message: str


class _MoveFilePartialEffect(RuntimeError):
    def __init__(self, result_code: str, message: str) -> None:
        self.result_code = result_code
        super().__init__(message)


class MoveFileTool:
    """Move one regular file without following symlinks or replacing a destination."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def prepare(self, request: ToolUse) -> PreparedMoveFile:
        """Validate both paths and bind exact source state plus destination absence."""
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"destination", "source"}:
                raise ValueError
            raw_source = arguments["source"]
            raw_destination = arguments["destination"]
            if not isinstance(raw_source, str) or not isinstance(raw_destination, str):
                raise ValueError
        except (AttributeError, ValueError):
            raise MoveFilePreparationError("move_file input is malformed") from None

        source_path, source_parent_path, source_name, source_parent = self._resolve_path(
            raw_source, "source"
        )
        destination_path, destination_parent_path, destination_name, destination_parent = (
            self._resolve_path(raw_destination, "destination")
        )
        if source_path == destination_path:
            raise MoveFilePreparationError("move_file source and destination must differ")

        source = self._observe_source(source_parent_path / source_name)
        if self._observe_destination(destination_parent_path / destination_name) is not None:
            raise MoveFilePreparationError("move_file destination already exists")
        if source.device != destination_parent.device:
            raise MoveFilePreparationError(
                "move_file source and destination must share a filesystem"
            )

        precondition = self._precondition(
            source_path,
            destination_path,
            source_parent,
            destination_parent,
            source,
            destination_absent=True,
        )
        return PreparedMoveFile(
            request=request,
            source_path=source_path,
            destination_path=destination_path,
            source_parent=source_parent,
            destination_parent=destination_parent,
            source_state=source,
            action=PermissionAction.WORKSPACE_MOVE,
            precondition=precondition,
        )

    def refresh_precondition(self, prepared: PreparedMoveFile) -> ActionPrecondition:
        """Re-observe both paths for exact approval and stale-state checks."""
        if type(prepared) is not PreparedMoveFile:
            raise ValueError("prepared move_file is invalid")
        try:
            source_path, source_parent_path, source_name, source_parent = self._resolve_path(
                prepared.source_path, "source"
            )
            destination_path, destination_parent_path, destination_name, destination_parent = (
                self._resolve_path(prepared.destination_path, "destination")
            )
            source = self._observe_source(source_parent_path / source_name)
            destination_absent = (
                self._observe_destination(destination_parent_path / destination_name) is None
            )
        except MoveFilePreparationError as error:
            return ActionPrecondition.expected_state(
                hashlib.sha256(f"invalid:{error}".encode("utf-8")).hexdigest()
            )
        return self._precondition(
            source_path,
            destination_path,
            source_parent,
            destination_parent,
            source,
            destination_absent=destination_absent,
        )

    def execute(self, prepared: PreparedMoveFile) -> ToolResult:
        """Apply one prepared move and return its model-visible result."""
        return self.execute_detailed(prepared).tool_result

    def execute_detailed(self, prepared: PreparedMoveFile) -> MoveFileExecutionResult:
        """Link destination exclusively, durably install it, then remove the source."""
        if type(prepared) is not PreparedMoveFile:
            raise ValueError("prepared move_file is invalid")
        request = prepared.request
        if prepared.precondition.kind != ActionPreconditionKind.EXPECTED_STATE_SHA256:
            return self._failed(request, "move_file precondition is invalid")
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(request, "move_file conflict: source or destination changed")

        source_parent_path = self._workspace / Path(prepared.source_path).parent
        destination_parent_path = self._workspace / Path(prepared.destination_path).parent
        source_name = Path(prepared.source_path).name
        destination_name = Path(prepared.destination_path).name
        source_fd: int | None = None
        destination_fd: int | None = None
        destination_linked = False
        source_removed = False
        try:
            source_fd = _open_directory(source_parent_path)
            destination_fd = _open_directory(destination_parent_path)
            _assert_parent_identity(source_fd, prepared.source_parent, "source")
            _assert_parent_identity(destination_fd, prepared.destination_parent, "destination")
            current_source = _observe_source_at(source_fd, source_name)
            if current_source != prepared.source_state:
                raise MoveFilePreparationError("move_file conflict: source changed")
            if _observe_at(destination_fd, destination_name) is not None:
                raise MoveFilePreparationError(
                    "move_file conflict: destination is no longer absent"
                )

            try:
                os.link(
                    source_name,
                    destination_name,
                    src_dir_fd=source_fd,
                    dst_dir_fd=destination_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise MoveFilePreparationError(
                    "move_file conflict: destination is no longer absent"
                ) from None
            destination_linked = True

            try:
                os.fsync(destination_fd)
            except OSError:
                raise _MoveFilePartialEffect(
                    "destination_linked_source_retained_durability_unknown",
                    "move_file created the destination link but retained the source because destination durability is unknown; inspect both paths and do not retry automatically",
                ) from None

            try:
                os.unlink(source_name, dir_fd=source_fd)
            except OSError:
                raise _MoveFilePartialEffect(
                    "destination_linked_source_retained",
                    "move_file created the destination link but could not remove the source; inspect both paths and do not retry automatically",
                ) from None
            source_removed = True

            try:
                os.fsync(source_fd)
            except OSError:
                raise _MoveFilePartialEffect(
                    "file_moved_durability_unknown",
                    "move_file moved the file, but source removal durability is unknown; inspect both paths and do not retry automatically",
                ) from None
        except _MoveFilePartialEffect as error:
            return MoveFileExecutionResult(
                ToolResult(request.tool_use_id, str(error), is_error=True),
                MoveFileOutcome.PARTIAL,
                error.result_code,
                str(error),
            )
        except MoveFilePreparationError as error:
            return self._failed(request, str(error))
        except PermissionError:
            if destination_linked:
                return self._partial_from_state(request, source_removed)
            return self._failed(request, "move_file source or destination is not accessible")
        except OSError as error:
            if destination_linked:
                return self._partial_from_state(request, source_removed)
            if error.errno == errno.EXDEV:
                return self._failed(
                    request, "move_file source and destination must share a filesystem"
                )
            return self._failed(request, "move_file could not move the file")
        finally:
            if destination_fd is not None:
                os.close(destination_fd)
            if source_fd is not None:
                os.close(source_fd)

        payload = json.dumps(
            {
                "destination": prepared.destination_path,
                "operation": "moved",
                "source": prepared.source_path,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return MoveFileExecutionResult(
            ToolResult(request.tool_use_id, f"{payload}\n"),
            MoveFileOutcome.SUCCEEDED,
            "file_moved",
            f"move_file moved {prepared.source_path} to {prepared.destination_path}",
        )

    def _partial_from_state(
        self, request: ToolUse, source_removed: bool
    ) -> MoveFileExecutionResult:
        if source_removed:
            code = "file_moved_durability_unknown"
            message = (
                "move_file moved the file, but durability is unknown; inspect both paths and "
                "do not retry automatically"
            )
        else:
            code = "destination_linked_source_retained"
            message = (
                "move_file may have created the destination link while retaining the source; "
                "inspect both paths and do not retry automatically"
            )
        return MoveFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            MoveFileOutcome.PARTIAL,
            code,
            message,
        )

    @staticmethod
    def _failed(request: ToolUse, message: str) -> MoveFileExecutionResult:
        return MoveFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            MoveFileOutcome.FAILED,
            "file_not_moved",
            message,
        )

    def _resolve_path(self, raw_path: str, label: str) -> tuple[str, Path, str, _ObservedParent]:
        parts = _validate_path(raw_path, label)
        current = self._workspace
        for component in parts[:-1]:
            current /= component
            try:
                info = current.lstat()
            except FileNotFoundError:
                raise MoveFilePreparationError(
                    f"move_file {label} parent directory does not exist"
                ) from None
            except PermissionError:
                raise MoveFilePreparationError(
                    f"move_file {label} parent directory is not accessible"
                ) from None
            except OSError:
                raise MoveFilePreparationError(
                    f"move_file could not inspect {label} parent directory"
                ) from None
            if stat.S_ISLNK(info.st_mode):
                raise MoveFilePreparationError(f"move_file {label} path contains a symbolic link")
            if not stat.S_ISDIR(info.st_mode):
                raise MoveFilePreparationError(f"move_file {label} parent path is not a directory")
        parent_info = current.lstat()
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise MoveFilePreparationError(f"move_file {label} parent path is not a directory")
        return (
            "/".join(parts),
            current,
            parts[-1],
            _ObservedParent(parent_info.st_dev, parent_info.st_ino),
        )

    @staticmethod
    def _observe_source(target: Path) -> _ObservedFile:
        try:
            info = target.lstat()
        except FileNotFoundError:
            raise MoveFilePreparationError("move_file source does not exist") from None
        except PermissionError:
            raise MoveFilePreparationError("move_file source is not accessible") from None
        except OSError:
            raise MoveFilePreparationError("move_file could not inspect source") from None
        return _source_from_stat(info)

    @staticmethod
    def _observe_destination(target: Path) -> os.stat_result | None:
        try:
            return target.lstat()
        except FileNotFoundError:
            return None
        except PermissionError:
            raise MoveFilePreparationError("move_file destination is not accessible") from None
        except OSError:
            raise MoveFilePreparationError("move_file could not inspect destination") from None

    @staticmethod
    def _precondition(
        source_path: str,
        destination_path: str,
        source_parent: _ObservedParent,
        destination_parent: _ObservedParent,
        source: _ObservedFile,
        *,
        destination_absent: bool,
    ) -> ActionPrecondition:
        payload = json.dumps(
            {
                "destination_absent": destination_absent,
                "destination_parent": [
                    destination_parent.device,
                    destination_parent.inode,
                ],
                "destination_path": destination_path,
                "source_parent": [source_parent.device, source_parent.inode],
                "source_path": source_path,
                "source_state": source.fingerprint,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return ActionPrecondition.expected_state(hashlib.sha256(payload).hexdigest())


def move_file_model_definition() -> dict[str, object]:
    """Return the exact provider-neutral controlled file-move definition."""
    return {
        "name": MOVE_FILE_TOOL_NAME,
        "description": (
            "Move one existing workspace-relative regular file to one missing workspace-relative "
            "destination. The Host applies workspace-move permission and approval policy, rejects "
            "symlinks, stale paths, directory sources, cross-filesystem moves, and destination "
            "replacement. Destination parents must already exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Portable workspace-relative path of the existing source file.",
                },
                "destination": {
                    "type": "string",
                    "description": "Portable workspace-relative missing destination path.",
                },
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
    }


def move_file_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(move_file_model_definition())


def _validate_path(raw_path: str, label: str) -> tuple[str, ...]:
    try:
        encoded = raw_path.encode("utf-8")
    except UnicodeEncodeError:
        raise MoveFilePreparationError(f"move_file {label} path must be valid UTF-8") from None
    if (
        not raw_path
        or not raw_path.strip()
        or len(raw_path) > MAX_MOVE_PATH_CHARACTERS
        or len(encoded) > MAX_MOVE_PATH_BYTES
        or "\x00" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).drive
    ):
        raise MoveFilePreparationError(
            f"move_file {label} must be a portable workspace-relative file path"
        )
    parts = tuple(raw_path.split("/"))
    if (
        not parts
        or len(parts) > MAX_MOVE_PATH_COMPONENTS
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise MoveFilePreparationError(
            f"move_file {label} must be a portable workspace-relative file path"
        )
    for part in parts:
        try:
            part_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            raise MoveFilePreparationError(f"move_file {label} path must be valid UTF-8") from None
        if len(part_bytes) > MAX_MOVE_COMPONENT_BYTES:
            raise MoveFilePreparationError(
                f"move_file {label} path component exceeds {MAX_MOVE_COMPONENT_BYTES} bytes"
            )
    return parts


def _source_from_stat(info: os.stat_result) -> _ObservedFile:
    if stat.S_ISLNK(info.st_mode):
        raise MoveFilePreparationError("move_file source must not be a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise MoveFilePreparationError("move_file source must be a regular file")
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


def _assert_parent_identity(descriptor: int, expected: _ObservedParent, label: str) -> None:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
        expected.device,
        expected.inode,
    ):
        raise MoveFilePreparationError(f"move_file conflict: {label} parent changed")


def _observe_at(descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _observe_source_at(descriptor: int, name: str) -> _ObservedFile:
    observed = _observe_at(descriptor, name)
    if observed is None:
        raise MoveFilePreparationError("move_file conflict: source no longer exists")
    return _source_from_stat(observed)
