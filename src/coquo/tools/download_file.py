"""Bounded public download with exact-state atomic workspace installation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from coquo.core.actions import ActionPrecondition
from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.permissions import PermissionAction
from coquo.tools._workspace_paths import (
    WorkspacePathFailure,
    open_parent_directory,
    validate_workspace_path,
)
from coquo.tools.web_transport import (
    PinnedWebGetTransport,
    WebGetTransport,
    WebTransportError,
    canonical_public_web_url,
)

DOWNLOAD_FILE_TOOL_NAME = "download_file"
DOWNLOAD_FILE_TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_FILE_BYTES = 16 * 1024 * 1024
MAX_DOWNLOAD_EXISTING_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class _TargetState:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    digest: str


@dataclass(frozen=True)
class _ParentState:
    device: int
    inode: int


@dataclass(frozen=True)
class PreparedDownloadFile:
    request: ToolUse
    url: str
    relative_path: str
    parent: _ParentState
    target_state: _TargetState | None
    action: PermissionAction
    precondition: ActionPrecondition


class DownloadFilePreparationError(ValueError):
    pass


class DownloadFileOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class DownloadFileExecutionResult:
    tool_result: ToolResult
    outcome: DownloadFileOutcome
    result_code: str
    audit_message: str


class _DownloadPartial(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def download_file_model_definition() -> dict[str, object]:
    return {
        "name": DOWNLOAD_FILE_TOOL_NAME,
        "description": (
            "Download one bounded public HTTP or HTTPS resource and atomically create or replace "
            "one workspace regular file. The Host applies public-address and redirect checks, "
            "binds approval to exact URL and target state, preserves overwrite mode, and never "
            "follows symlinks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Public HTTP or HTTPS URL.",
                },
                "path": {"type": "string", "description": "Workspace-relative destination file."},
            },
            "required": ["url", "path"],
            "additionalProperties": False,
        },
    }


def download_file_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(download_file_model_definition())


class DownloadFileTool:
    def __init__(self, workspace: Path, transport: WebGetTransport | None = None) -> None:
        self._workspace = workspace.resolve()
        self._transport = transport or PinnedWebGetTransport()

    def prepare(self, request: ToolUse) -> PreparedDownloadFile:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != DOWNLOAD_FILE_TOOL_NAME or set(arguments) != {"path", "url"}:
                raise ValueError
            path = arguments["path"]
            url = arguments["url"]
            if not isinstance(path, str) or not isinstance(url, str):
                raise ValueError
            parts = validate_workspace_path(
                path, tool_name=DOWNLOAD_FILE_TOOL_NAME, allow_root=False
            )
            canonical_url = canonical_public_web_url(url)
            parent, state = self._observe(parts)
        except (AttributeError, ValueError, WorkspacePathFailure):
            raise DownloadFilePreparationError("download_file input is malformed") from None
        except WebTransportError as error:
            raise DownloadFilePreparationError(str(error)) from None
        precondition = ActionPrecondition.expected_state(
            _download_state_digest(canonical_url, parts, parent, state)
        )
        return PreparedDownloadFile(
            request,
            canonical_url,
            "/".join(parts),
            parent,
            state,
            PermissionAction.NETWORK_WRITE,
            precondition,
        )

    def refresh_precondition(self, prepared: PreparedDownloadFile) -> ActionPrecondition:
        try:
            parts = validate_workspace_path(
                prepared.relative_path, tool_name=DOWNLOAD_FILE_TOOL_NAME, allow_root=False
            )
            parent, state = self._observe(parts)
            digest = _download_state_digest(prepared.url, parts, parent, state)
        except (WorkspacePathFailure, DownloadFilePreparationError) as error:
            digest = hashlib.sha256(f"invalid:{error}".encode()).hexdigest()
        return ActionPrecondition.expected_state(digest)

    def execute_detailed(self, prepared: PreparedDownloadFile) -> DownloadFileExecutionResult:
        request = prepared.request
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(request, "download_file conflict: destination changed")
        try:
            response = self._transport.fetch(
                prepared.url,
                timeout_seconds=DOWNLOAD_FILE_TIMEOUT_SECONDS,
                max_response_bytes=MAX_DOWNLOAD_FILE_BYTES,
            )
        except WebTransportError as error:
            return DownloadFileExecutionResult(
                ToolResult(request.tool_use_id, str(error), is_error=True),
                DownloadFileOutcome.PARTIAL
                if error.delivery_unknown
                else DownloadFileOutcome.FAILED,
                error.result_code,
                str(error),
            )
        if not 200 <= response.status_code < 300:
            return self._failed(request, f"download_file received HTTP {response.status_code}")
        if self.refresh_precondition(prepared) != prepared.precondition:
            return self._failed(
                request, "download_file conflict: destination changed during download"
            )
        try:
            operation = self._install(prepared, response.body)
        except DownloadFilePreparationError as error:
            return self._failed(request, str(error))
        except _DownloadPartial as error:
            return DownloadFileExecutionResult(
                ToolResult(request.tool_use_id, str(error), is_error=True),
                DownloadFileOutcome.PARTIAL,
                error.code,
                str(error),
            )
        digest = hashlib.sha256(response.body).hexdigest()
        content = (
            json.dumps(
                {
                    "bytes_written": len(response.body),
                    "operation": operation,
                    "path": prepared.relative_path,
                    "sha256": digest,
                    "url": response.final_url,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return DownloadFileExecutionResult(
            ToolResult(request.tool_use_id, content),
            DownloadFileOutcome.SUCCEEDED,
            f"file_{operation}",
            f"download_file {operation} {prepared.relative_path} with {len(response.body)} bytes",
        )

    def _observe(self, parts: tuple[str, ...]) -> tuple[_ParentState, _TargetState | None]:
        parent_fd, name = open_parent_directory(
            self._workspace, parts, tool_name=DOWNLOAD_FILE_TOOL_NAME
        )
        descriptor: int | None = None
        try:
            parent_info = os.fstat(parent_fd)
            parent = _ParentState(parent_info.st_dev, parent_info.st_ino)
            try:
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return parent, None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise DownloadFilePreparationError(
                    "download_file destination must be a regular file or absent"
                )
            if info.st_size > MAX_DOWNLOAD_EXISTING_BYTES:
                raise DownloadFilePreparationError(
                    f"download_file existing file exceeds {MAX_DOWNLOAD_EXISTING_BYTES} bytes"
                )
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise DownloadFilePreparationError(
                    "download_file destination changed during inspection"
                )
            digest = hashlib.sha256()
            total = 0
            while chunk := os.read(descriptor, 64 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_EXISTING_BYTES:
                    raise DownloadFilePreparationError(
                        f"download_file existing file exceeds {MAX_DOWNLOAD_EXISTING_BYTES} bytes"
                    )
                digest.update(chunk)
            after = os.fstat(descriptor)
            if _file_identity(opened) != _file_identity(after):
                raise DownloadFilePreparationError(
                    "download_file destination changed during inspection"
                )
            return parent, _TargetState(
                after.st_dev,
                after.st_ino,
                stat.S_IMODE(after.st_mode),
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                digest.hexdigest(),
            )
        except PermissionError:
            raise DownloadFilePreparationError(
                "download_file destination is not accessible"
            ) from None
        except DownloadFilePreparationError:
            raise
        except OSError:
            raise DownloadFilePreparationError(
                "download_file could not inspect destination"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    def _install(self, prepared: PreparedDownloadFile, content: bytes) -> str:
        parts = tuple(prepared.relative_path.split("/"))
        parent_fd, name = open_parent_directory(
            self._workspace, parts, tool_name=DOWNLOAD_FILE_TOOL_NAME
        )
        temporary = f".{name}.coquo-{uuid4().hex}.tmp"
        descriptor: int | None = None
        installed = False
        try:
            parent_info = os.fstat(parent_fd)
            if _ParentState(parent_info.st_dev, parent_info.st_ino) != prepared.parent:
                raise DownloadFilePreparationError("download_file destination parent changed")
            mode = 0o666 if prepared.target_state is None else prepared.target_state.mode
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                mode,
                dir_fd=parent_fd,
            )
            if prepared.target_state is not None:
                os.fchmod(descriptor, mode)
            _write_all(descriptor, content)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            current_parent, current = self._observe(parts)
            if current_parent != prepared.parent or current != prepared.target_state:
                raise DownloadFilePreparationError("download_file conflict: destination changed")
            if prepared.target_state is None:
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    raise DownloadFilePreparationError(
                        "download_file conflict: destination changed"
                    ) from None
                installed = True
                operation = "created"
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    raise _DownloadPartial(
                        "download_created_cleanup_unknown",
                        "download_file created the target but temporary cleanup failed; inspect the workspace and do not retry automatically",
                    ) from None
            else:
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                installed = True
                operation = "overwritten"
            try:
                os.fsync(parent_fd)
            except OSError:
                raise _DownloadPartial(
                    f"download_{operation}_durability_unknown",
                    f"download_file {operation} the target but directory durability is unknown; inspect the workspace and do not retry automatically",
                ) from None
            return operation
        except (DownloadFilePreparationError, _DownloadPartial):
            raise
        except OSError:
            if installed:
                raise _DownloadPartial(
                    "download_install_state_unknown",
                    "download_file may have installed the target; inspect the workspace and do not retry automatically",
                ) from None
            raise DownloadFilePreparationError(
                "download_file could not install destination"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not installed:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass
            os.close(parent_fd)

    @staticmethod
    def _failed(request: ToolUse, message: str) -> DownloadFileExecutionResult:
        return DownloadFileExecutionResult(
            ToolResult(request.tool_use_id, message, is_error=True),
            DownloadFileOutcome.FAILED,
            "file_not_downloaded",
            message,
        )


def _download_state_digest(
    url: str,
    parts: tuple[str, ...],
    parent: _ParentState,
    target: _TargetState | None,
) -> str:
    payload = json.dumps(
        {
            "max_bytes": MAX_DOWNLOAD_FILE_BYTES,
            "parent": parent.__dict__,
            "path": "/".join(parts),
            "target": None if target is None else target.__dict__,
            "timeout_seconds": DOWNLOAD_FILE_TIMEOUT_SECONDS,
            "url": url,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]
