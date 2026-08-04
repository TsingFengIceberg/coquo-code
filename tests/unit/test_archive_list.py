from io import BytesIO
from pathlib import Path
import tarfile
import zipfile

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.archive_list import ARCHIVE_LIST_TOOL_NAME, ArchiveListTool


def request(path: str) -> ToolUse:
    return ToolUse(
        "toolu_archive", ARCHIVE_LIST_TOOL_NAME, ToolArguments.from_mapping({"path": path})
    )


def test_archive_list_reports_zip_entries_and_unsafe_paths(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "items.zip", "w") as archive:
        archive.writestr("safe/file.txt", "ok")
        archive.writestr("../escape.txt", "bad")

    result = ArchiveListTool(tmp_path).execute(request("items.zip"))

    assert not result.is_error
    assert '"path":"safe/file.txt","safe_path":true' in result.content
    assert '"path":"../escape.txt","safe_path":false' in result.content


def test_archive_list_treats_normal_directory_entries_as_safe(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "directories.zip", "w") as archive:
        archive.writestr("nested/", b"")

    result = ArchiveListTool(tmp_path).execute(request("directories.zip"))

    assert not result.is_error
    assert '"path":"nested/","safe_path":true' in result.content


def test_archive_list_supports_uncompressed_tar_and_rejects_gzip(tmp_path: Path) -> None:
    payload = b"hello"
    with tarfile.open(tmp_path / "items.tar", "w") as archive:
        info = tarfile.TarInfo("file.txt")
        info.size = len(payload)
        archive.addfile(info, BytesIO(payload))
    result = ArchiveListTool(tmp_path).execute(request("items.tar"))
    assert not result.is_error
    assert '"path":"file.txt"' in result.content

    with tarfile.open(tmp_path / "items.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("file.txt")
        info.size = len(payload)
        archive.addfile(info, BytesIO(payload))
    rejected = ArchiveListTool(tmp_path).execute(request("items.tar.gz"))
    assert rejected.is_error
    assert "compressed TAR" in rejected.content
