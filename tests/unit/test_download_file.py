from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.download_file import (
    DOWNLOAD_FILE_TOOL_NAME,
    DownloadFileOutcome,
    DownloadFileTool,
)
from coquo.tools.web_transport import WebHttpResponse, WebTransportError


@dataclass
class FakeTransport:
    body: bytes
    mutate: Callable[[], None] | None = None
    error: WebTransportError | None = None

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> WebHttpResponse:
        assert url == "https://example.com/file.bin"
        assert timeout_seconds == 30
        assert max_response_bytes == 16 * 1024 * 1024
        if self.mutate is not None:
            self.mutate()
        if self.error is not None:
            raise self.error
        return WebHttpResponse(200, "application/octet-stream", "", self.body, url, 0)


def request(path: str = "file.bin") -> ToolUse:
    return ToolUse(
        "toolu_download",
        DOWNLOAD_FILE_TOOL_NAME,
        ToolArguments.from_mapping({"path": path, "url": "https://example.com/file.bin"}),
    )


def test_download_file_creates_and_overwrites_binary_atomically(tmp_path: Path) -> None:
    tool = DownloadFileTool(tmp_path, FakeTransport(b"first\x00"))
    created = tool.execute_detailed(tool.prepare(request()))
    assert created.outcome is DownloadFileOutcome.SUCCEEDED
    assert (tmp_path / "file.bin").read_bytes() == b"first\x00"
    assert '"operation":"created"' in created.tool_result.content

    (tmp_path / "file.bin").chmod(0o640)
    overwrite = DownloadFileTool(tmp_path, FakeTransport(b"second"))
    overwritten = overwrite.execute_detailed(overwrite.prepare(request()))
    assert overwritten.outcome is DownloadFileOutcome.SUCCEEDED
    assert (tmp_path / "file.bin").read_bytes() == b"second"
    assert (tmp_path / "file.bin").stat().st_mode & 0o777 == 0o640


def test_download_file_rechecks_target_after_network_and_preserves_unknown_transport(
    tmp_path: Path,
) -> None:
    prepared_tool = DownloadFileTool(
        tmp_path,
        FakeTransport(b"remote", mutate=lambda: (tmp_path / "file.bin").write_bytes(b"other")),
    )
    prepared = prepared_tool.prepare(request())
    conflict = prepared_tool.execute_detailed(prepared)
    assert conflict.outcome is DownloadFileOutcome.FAILED
    assert (tmp_path / "file.bin").read_bytes() == b"other"

    failed_tool = DownloadFileTool(
        tmp_path,
        FakeTransport(
            b"",
            error=WebTransportError("web_timed_out", "timed out", delivery_unknown=True),
        ),
    )
    partial = failed_tool.execute_detailed(failed_tool.prepare(request("other.bin")))
    assert partial.outcome is DownloadFileOutcome.PARTIAL
    assert not (tmp_path / "other.bin").exists()
