"""Controlled bounded copying of one workspace regular file."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
from uuid import uuid4

from coquo.core.actions import ActionPrecondition, ActionPreconditionKind
from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.permissions import PermissionAction

COPY_FILE_TOOL_NAME = "copy_file"
MAX_COPY_PATH_CHARACTERS = 4096
MAX_COPY_PATH_BYTES = 4096
MAX_COPY_PATH_COMPONENTS = 64
MAX_COPY_COMPONENT_BYTES = 255
MAX_COPY_SOURCE_BYTES = 1024 * 1024


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
    digest: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "changed_ns": self.changed_ns,
                "device": self.device,
                "digest": self.digest,
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
class PreparedCopyFile:
    """One immutable source snapshot and absent destination prepared without mutation."""

    request: ToolUse
    source_path: str
    destination_path: str
    source_parent: _ObservedParent
    destination_parent: _ObservedParent
    source_state: _ObservedFile
    content: bytes
    action: PermissionAction
    precondition: ActionPrecondition


class CopyFilePreparationError(ValueError):
    """A hard-bound rejection before a copy is permission-eligible."""


class CopyFileOutcome(StrEnum):
    """Known copy outcomes including visible partial effects."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class CopyFileExecutionResult:
    """One truthful model result plus stable Host audit attribution."""

    tool_result: ToolResult
    outcome: CopyFileOutcome
    result_code: str
    audit_message: str


class _CopyFilePartialEffect(RuntimeError):
    def __init__(self, result_code: str, message: str) -> None:
        self.result_code = result_code
        super().__init__(message)


class CopyFileTool:
    """Copy one bounded regular file without following symlinks or replacing a target."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def prepare(self, request: ToolUse) -> PreparedCopyFile:
        """Validate both paths and bind exact source content plus destination absence."""
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"destination", "source"}:
                raise ValueError
            raw_source = arguments["source"]
            raw_destination = arguments["destination"]
            if not isinstance(raw_source, str) or not isinstance(raw_destination, str):
                raise ValueError
        except (AttributeError, ValueError):
            raise CopyFilePreparationError("copy_file input is malformed") from None

        source_path, source_parent_path, source_name, source_parent = self._resolve_path(
            raw_source, "source"
        )
        destination_path, destination_parent_path, destination_name, destination_parent = (
            self._resolve_path(raw_destination, "destination")
        )
        if source_path == destination_path:
            raise CopyFilePreparationError("copy_file source and destination must differ")

        source, content = self._read_source(source_parent_path / source_name)
        if self._observe_destination(destination_parent_path / destination_name) is not None:
            raise CopyFilePreparationError("copy_file destination already exists")
        precondition = self._precondition(
            source_path,
            destination_path,
            source_parent,
            destination_parent,
            source,
            destination_absent=True,
        )
        return PreparedCopyFile(
            request=request,
            source_path=source_path,
            destination_path=destination_path,
            source_parent=source_parent,
            destination_parent=destination_parent,
            source_state=source,
            content=content,
            action=PermissionAction.WORKSPACE_CREATE,
            precondition=precondition,
        )

    def refresh_precondition(self, prepared: PreparedCopyFile) -> ActionPrecondition:
        """Re-observe both paths for exact approval and stale-state checks."""
        if type(prepared) is not PreparedCopyFile:
            raise ValueError("prepared copy_file is invalid")
        try:
            source_path, source_parent_path, source_name, source_parent = self._resolve_path(
                prepared.source_path, "source"
            )
            destination_path, destination_parent_path, destination_name, destination_parent = (
                self._resolve_path(prepared.destination_path, "destination")
            )
            source, _ = self._read_source(source_parent_path / source_name)
            destination_absent = (
                self._observe_destination(destination_parent_path / destination_name) is None
            )
        except CopyFilePreparationError as error:
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

    def execute(self, prepared: PreparedCopyFile) -> ToolResult:
        """Apply one prepared copy and return its model-visible result."""
        return self.execute_detailed(prepared).tool_result

    def execute_detailed(self, prepared: PreparedCopyFile) -> CopyFileExecutionResult:
        """Write a durable temporary snapshot and install the destination exclusively."""
        if type(prepared) is not PreparedCopyFile:
            raise ValueError("prepared copy_file is invalid")
        request = prepared.request
        if prepared.precondition.kind != ActionPreconditionKind.EXPECTED_STATE_SHA256:
            return self._failed(request, "copy_file precondition is invalid")
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(request, "copy_file conflict: source or destination changed")

        source_parent_path = self._workspace / Path(prepared.source_path).parent
        destination_parent_path = self._workspace / Path(prepared.destination_path).parent
        source_name = Path(prepared.source_path).name
        destination_name = Path(prepared.destination_path).name
        temporary_name = f".{destination_name}.coquo-{uuid4().hex}.tmp"
        source_parent_fd: int | None = None
        destination_parent_fd: int | None = None
        temporary_fd: int | None = None
        temporary_created = False
        temporary_removed = False
        destination_linked = False

        def cleanup_unlinked_temporary() -> bool:
            nonlocal temporary_fd, temporary_removed
            if temporary_fd is not None:
                os.close(temporary_fd)
                temporary_fd = None
            if temporary_created and not temporary_removed and destination_parent_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=destination_parent_fd)
                except OSError:
                    return False
                temporary_removed = True
            return True

        try:
            source_parent_fd = _open_directory(source_parent_path)
            destination_parent_fd = _open_directory(destination_parent_path)
            _assert_parent_identity(source_parent_fd, prepared.source_parent, "source")
            _assert_parent_identity(
                destination_parent_fd, prepared.destination_parent, "destination"
            )
            current_source, content = _read_source_at(source_parent_fd, source_name)
            if current_source != prepared.source_state or content != prepared.content:
                raise CopyFilePreparationError("copy_file conflict: source changed")
            if _observe_at(destination_parent_fd, destination_name) is not None:
                raise CopyFilePreparationError(
                    "copy_file conflict: destination is no longer absent"
                )

            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                _copy_mode(prepared.source_state.mode),
                dir_fd=destination_parent_fd,
            )
            temporary_created = True
            os.fchmod(temporary_fd, _copy_mode(prepared.source_state.mode))
            _write_all(temporary_fd, prepared.content)
            _fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None

            try:
                os.link(
                    temporary_name,
                    destination_name,
                    src_dir_fd=destination_parent_fd,
                    dst_dir_fd=destination_parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise CopyFilePreparationError(
                    "copy_file conflict: destination is no longer absent"
                ) from None
            destination_linked = True

            try:
                os.unlink(temporary_name, dir_fd=destination_parent_fd)
            except OSError:
                try:
                    _fsync(destination_parent_fd)
                except OSError:
                    raise _CopyFilePartialEffect(
                        "copied_cleanup_and_durability_unknown",
                        "copy_file created the destination, but temporary cleanup failed and directory durability is unknown; inspect the workspace and do not retry automatically",
                    ) from None
                raise _CopyFilePartialEffect(
                    "copied_with_temporary_cleanup_failure",
                    "copy_file created the destination durably, but temporary cleanup failed; inspect the workspace and do not retry automatically",
                ) from None
            temporary_removed = True

            try:
                _fsync(destination_parent_fd)
            except OSError:
                raise _CopyFilePartialEffect(
                    "file_copied_durability_unknown",
                    "copy_file created the destination, but directory durability is unknown; inspect the workspace and do not retry automatically",
                ) from None
        except _CopyFilePartialEffect as error:
            return CopyFileExecutionResult(
                ToolResult(request.tool_use_id, str(error), is_error=True),
                CopyFileOutcome.PARTIAL,
                error.result_code,
                str(error),
            )
        except CopyFilePreparationError as error:
            if not destination_linked and not cleanup_unlinked_temporary():
                return self._temporary_cleanup_partial(request)
            return self._failed(request, str(error))
        except PermissionError:
            if destination_linked:
                return self._partial_from_state(request)
            if not cleanup_unlinked_temporary():
                return self._temporary_cleanup_partial(request)
            return self._failed(request, "copy_file source or destination is not accessible")
        except OSError:
            if destination_linked:
                return self._partial_from_state(request)
            if not cleanup_unlinked_temporary():
                return self._temporary_cleanup_partial(request)
            return self._failed(request, "copy_file could not copy the file")
        finally:
            if not destination_linked:
                cleanup_unlinked_temporary()
            if destination_parent_fd is not None:
                os.close(destination_parent_fd)
            if source_parent_fd is not None:
                os.close(source_parent_fd)

        payload = json.dumps(
            {
                "bytes_copied": len(prepared.content),
                "destination": prepared.destination_path,
                "operation": "copied",
                "source": prepared.source_path,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return CopyFileExecutionResult(
            ToolResult(request.tool_use_id, f"{payload}\n"),
            CopyFileOutcome.SUCCEEDED,
            "file_copied",
            f"copy_file copied {prepared.source_path} to {prepared.destination_path}",
        )

    @staticmethod
    def _partial_from_state(request: ToolUse) -> CopyFileExecutionResult:
        message = (
            "copy_file may have created the destination, but its exact state or durability is "
            "unknown; inspect the workspace and do not retry automatically"
        )
        return CopyFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            CopyFileOutcome.PARTIAL,
            "file_copy_state_unknown",
            message,
        )

    @staticmethod
    def _temporary_cleanup_partial(request: ToolUse) -> CopyFileExecutionResult:
        message = (
            "copy_file did not create the destination, but temporary cleanup failed; inspect the "
            "workspace and do not retry automatically"
        )
        return CopyFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            CopyFileOutcome.PARTIAL,
            "temporary_cleanup_failed_destination_absent",
            message,
        )

    @staticmethod
    def _failed(request: ToolUse, message: str) -> CopyFileExecutionResult:
        return CopyFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            CopyFileOutcome.FAILED,
            "file_not_copied",
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
                raise CopyFilePreparationError(
                    f"copy_file {label} parent directory does not exist"
                ) from None
            except PermissionError:
                raise CopyFilePreparationError(
                    f"copy_file {label} parent directory is not accessible"
                ) from None
            except OSError:
                raise CopyFilePreparationError(
                    f"copy_file could not inspect {label} parent directory"
                ) from None
            if stat.S_ISLNK(info.st_mode):
                raise CopyFilePreparationError(f"copy_file {label} path contains a symbolic link")
            if not stat.S_ISDIR(info.st_mode):
                raise CopyFilePreparationError(f"copy_file {label} parent path is not a directory")
        try:
            parent_info = current.lstat()
        except PermissionError:
            raise CopyFilePreparationError(
                f"copy_file {label} parent directory is not accessible"
            ) from None
        except OSError:
            raise CopyFilePreparationError(
                f"copy_file could not inspect {label} parent directory"
            ) from None
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise CopyFilePreparationError(f"copy_file {label} parent path is not a directory")
        return (
            "/".join(parts),
            current,
            parts[-1],
            _ObservedParent(parent_info.st_dev, parent_info.st_ino),
        )

    @staticmethod
    def _read_source(target: Path) -> tuple[_ObservedFile, bytes]:
        try:
            descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            raise CopyFilePreparationError("copy_file source does not exist") from None
        except PermissionError:
            raise CopyFilePreparationError("copy_file source is not accessible") from None
        except OSError:
            try:
                info = target.lstat()
            except OSError:
                raise CopyFilePreparationError("copy_file could not inspect source") from None
            _validate_source_stat(info)
            raise CopyFilePreparationError("copy_file could not read source") from None
        try:
            return _read_open_source(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _observe_destination(target: Path) -> os.stat_result | None:
        try:
            return target.lstat()
        except FileNotFoundError:
            return None
        except PermissionError:
            raise CopyFilePreparationError("copy_file destination is not accessible") from None
        except OSError:
            raise CopyFilePreparationError("copy_file could not inspect destination") from None

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


def copy_file_model_definition() -> dict[str, object]:
    """Return the exact provider-neutral controlled file-copy definition."""
    return {
        "name": COPY_FILE_TOOL_NAME,
        "description": (
            "Copy one existing bounded workspace-relative regular file to one missing "
            "workspace-relative destination. The Host applies workspace-create permission and "
            "approval policy, rejects symlinks, stale paths, directory sources, oversized source "
            "files, and destination replacement. Destination parents must already exist."
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


def copy_file_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(copy_file_model_definition())


def _validate_path(raw_path: str, label: str) -> tuple[str, ...]:
    try:
        encoded = raw_path.encode("utf-8")
    except UnicodeEncodeError:
        raise CopyFilePreparationError(f"copy_file {label} path must be valid UTF-8") from None
    if (
        not raw_path
        or not raw_path.strip()
        or len(raw_path) > MAX_COPY_PATH_CHARACTERS
        or len(encoded) > MAX_COPY_PATH_BYTES
        or "\x00" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).drive
    ):
        raise CopyFilePreparationError(
            f"copy_file {label} must be a portable workspace-relative file path"
        )
    parts = tuple(raw_path.split("/"))
    if (
        not parts
        or len(parts) > MAX_COPY_PATH_COMPONENTS
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise CopyFilePreparationError(
            f"copy_file {label} must be a portable workspace-relative file path"
        )
    for part in parts:
        try:
            part_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            raise CopyFilePreparationError(f"copy_file {label} path must be valid UTF-8") from None
        if len(part_bytes) > MAX_COPY_COMPONENT_BYTES:
            raise CopyFilePreparationError(
                f"copy_file {label} path component exceeds {MAX_COPY_COMPONENT_BYTES} bytes"
            )
    return parts


def _validate_source_stat(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode):
        raise CopyFilePreparationError("copy_file source must not be a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise CopyFilePreparationError("copy_file source must be a regular file")
    if info.st_size > MAX_COPY_SOURCE_BYTES:
        raise CopyFilePreparationError(f"copy_file source exceeds {MAX_COPY_SOURCE_BYTES} bytes")


def _read_open_source(descriptor: int) -> tuple[_ObservedFile, bytes]:
    before = os.fstat(descriptor)
    _validate_source_stat(before)
    content = _read_bounded(descriptor, MAX_COPY_SOURCE_BYTES)
    after = os.fstat(descriptor)
    _validate_source_stat(after)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_nlink,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_nlink,
    )
    if before_identity != after_identity:
        raise CopyFilePreparationError("copy_file source changed during inspection")
    return (
        _ObservedFile(
            device=after.st_dev,
            inode=after.st_ino,
            mode=after.st_mode,
            size=after.st_size,
            modified_ns=after.st_mtime_ns,
            changed_ns=after.st_ctime_ns,
            link_count=after.st_nlink,
            digest=hashlib.sha256(content).hexdigest(),
        ),
        content,
    )


def _read_source_at(descriptor: int, name: str) -> tuple[_ObservedFile, bytes]:
    try:
        source_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
    except FileNotFoundError:
        raise CopyFilePreparationError("copy_file conflict: source no longer exists") from None
    except OSError:
        raise CopyFilePreparationError("copy_file conflict: source is not readable") from None
    try:
        return _read_open_source(source_fd)
    finally:
        os.close(source_fd)


def _read_bounded(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > limit:
        raise CopyFilePreparationError(f"copy_file source exceeds {limit} bytes")
    return content


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _copy_mode(source_mode: int) -> int:
    return stat.S_IMODE(source_mode) & 0o777


def _fsync(descriptor: int) -> None:
    os.fsync(descriptor)


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
        raise CopyFilePreparationError(f"copy_file conflict: {label} parent changed")


def _observe_at(descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
