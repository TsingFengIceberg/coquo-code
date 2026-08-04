import hashlib
from pathlib import Path

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.checksum_file import CHECKSUM_FILE_TOOL_NAME, ChecksumFileTool


def test_checksum_file_hashes_binary_content(tmp_path: Path) -> None:
    data = b"\x00binary\xff"
    (tmp_path / "payload.bin").write_bytes(data)
    request = ToolUse(
        "toolu_checksum",
        CHECKSUM_FILE_TOOL_NAME,
        ToolArguments.from_mapping({"path": "payload.bin"}),
    )

    result = ChecksumFileTool(tmp_path).execute(request)

    assert not result.is_error
    assert hashlib.sha256(data).hexdigest() in result.content
    assert '"bytes":8' in result.content
