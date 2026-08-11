"""Read-only bounded ZIP and uncompressed TAR inventory."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import tarfile
import zipfile

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools._workspace_files import read_workspace_regular_file
from coquo.tools._workspace_paths import WorkspacePathFailure

ARCHIVE_LIST_TOOL_NAME = "archive_list"
MAX_ARCHIVE_SOURCE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_ARCHIVE_OUTPUT_BYTES = 32 * 1024
ARCHIVE_TRUNCATION_SENTINEL = '{"truncated":true}\n'


def archive_list_model_definition() -> dict[str, object]:
    return {
        "name": ARCHIVE_LIST_TOOL_NAME,
        "description": (
            "List bounded metadata for an existing workspace ZIP or uncompressed TAR regular "
            "file without extracting entries. Reports unsafe paths, links, sizes, and entry types."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative archive."}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


def archive_list_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(archive_list_model_definition())


class ArchiveListTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != ARCHIVE_LIST_TOOL_NAME or set(arguments) != {"path"}:
                raise ValueError
            path = arguments["path"]
            if not isinstance(path, str):
                raise ValueError
            snapshot = read_workspace_regular_file(
                self._workspace,
                path,
                tool_name=ARCHIVE_LIST_TOOL_NAME,
                max_bytes=MAX_ARCHIVE_SOURCE_BYTES,
            )
            entries = _read_entries(snapshot.data)
            content, truncated = _format_entries(entries)
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "archive_list input is malformed", is_error=True)
        except WorkspacePathFailure as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, content, truncated=truncated)


def _read_entries(data: bytes) -> list[dict[str, object]]:
    stream = BytesIO(data)
    if zipfile.is_zipfile(stream):
        return _zip_entries(data)
    if data.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00")):
        raise WorkspacePathFailure("archive_list compressed TAR formats are not supported")
    try:
        with tarfile.open(fileobj=BytesIO(data), mode="r:") as archive:
            members = archive.getmembers()
    except (tarfile.TarError, OSError):
        raise WorkspacePathFailure(
            "archive_list target is not a supported ZIP or TAR archive"
        ) from None
    if len(members) > MAX_ARCHIVE_ENTRIES:
        raise WorkspacePathFailure(f"archive_list archive exceeds {MAX_ARCHIVE_ENTRIES} entries")
    entries = []
    for member in members:
        kind = (
            "file"
            if member.isfile()
            else "directory"
            if member.isdir()
            else "link"
            if (member.issym() or member.islnk())
            else "other"
        )
        entries.append(
            {
                "encrypted": False,
                "path": member.name,
                "safe_path": _safe_archive_path(member.name),
                "size": member.size,
                "type": kind,
            }
        )
    return entries


def _zip_entries(data: bytes) -> list[dict[str, object]]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        raise WorkspacePathFailure("archive_list ZIP metadata is invalid") from None
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise WorkspacePathFailure(f"archive_list archive exceeds {MAX_ARCHIVE_ENTRIES} entries")
    entries = []
    for info in infos:
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = "directory" if info.is_dir() else "link" if stat.S_ISLNK(mode) else "file"
        entries.append(
            {
                "compressed_size": info.compress_size,
                "encrypted": bool(info.flag_bits & 0x1),
                "path": info.filename,
                "safe_path": _safe_archive_path(info.filename),
                "size": info.file_size,
                "type": kind,
            }
        )
    return entries


def _safe_archive_path(value: str) -> bool:
    candidate = value[:-1] if value.endswith("/") else value
    pure = PurePosixPath(candidate)
    return bool(
        candidate
        and "\x00" not in candidate
        and "\\" not in candidate
        and not pure.is_absolute()
        and not PureWindowsPath(candidate).drive
        and all(part not in {"", ".", ".."} for part in pure.parts)
    )


def _format_entries(entries: list[dict[str, object]]) -> tuple[str, bool]:
    entries.sort(key=lambda item: str(item["path"]).encode("utf-8"))
    output: list[str] = []
    size = 0
    sentinel_size = len(ARCHIVE_TRUNCATION_SENTINEL.encode("ascii"))
    for entry in entries:
        record = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        record_size = len(record.encode("utf-8"))
        if record_size + sentinel_size > MAX_ARCHIVE_OUTPUT_BYTES:
            raise WorkspacePathFailure("archive_list one entry exceeds the output limit")
        if size + record_size + sentinel_size > MAX_ARCHIVE_OUTPUT_BYTES:
            output.append(ARCHIVE_TRUNCATION_SENTINEL)
            return "".join(output), True
        output.append(record)
        size += record_size
    return "".join(output), False
