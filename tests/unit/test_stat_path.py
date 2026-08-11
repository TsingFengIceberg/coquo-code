from __future__ import annotations

import json
from pathlib import Path

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.stat_path import StatPathTool


def request(path: object, **extra: object) -> ToolUse:
    return ToolUse(
        "stat-1",
        "stat_path",
        ToolArguments.from_mapping({"path": path, **extra}),
    )


def test_reports_root_directory_and_regular_file_metadata(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    target.chmod(0o640)
    tool = StatPathTool(tmp_path)

    root = json.loads(tool.execute(request(".")).content)
    file = json.loads(tool.execute(request("note.txt")).content)

    assert root["path"] == "."
    assert root["type"] == "directory"
    assert "size" not in root
    assert file == {
        "mode": "0640",
        "modified_ns": target.stat().st_mtime_ns,
        "path": "note.txt",
        "size": 5,
        "type": "file",
    }


def test_reports_final_symlink_without_following_it(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-stat.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(outside)

    payload = json.loads(StatPathTool(tmp_path).execute(request("link")).content)

    assert payload["type"] == "symlink"
    assert payload["path"] == "link"
    assert "size" not in payload


def test_rejects_symlink_parent_missing_and_nonportable_paths(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    tool = StatPathTool(tmp_path)

    assert "symbolic links" in tool.execute(request("alias/child")).content
    assert "does not exist" in tool.execute(request("missing")).content
    for path in ("", "../x", "a//b", "a\\b", "/x"):
        assert "portable workspace-relative path" in tool.execute(request(path)).content


@pytest.mark.parametrize("call", [request(1), request(".", extra=1)])
def test_rejects_malformed_input(tmp_path: Path, call: ToolUse) -> None:
    result = StatPathTool(tmp_path).execute(call)

    assert result.is_error
    assert result.content == "stat_path input is malformed"
