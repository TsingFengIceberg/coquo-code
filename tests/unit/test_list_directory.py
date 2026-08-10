from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools import list_directory as list_directory_module
from coquo.tools.list_directory import (
    LIST_DIRECTORY_TRUNCATION_MARKER,
    ListDirectoryTool,
    list_directory_model_definition,
)


def request(path: object = ".", *, arguments: dict[str, object] | None = None) -> ToolUse:
    return ToolUse(
        "list-1",
        "list_directory",
        ToolArguments.from_mapping({"path": path} if arguments is None else arguments),
    )


def records(content: str) -> list[dict[str, str]]:
    return [json.loads(line) for line in content.splitlines() if line != '{"truncated":true}']


def test_lists_root_direct_children_types_hidden_entries_and_stable_order(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "a-dir").mkdir()
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
    (tmp_path / "link").symlink_to("z.txt")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    result = ListDirectoryTool(tmp_path).execute(request())

    assert not result.is_error
    assert not result.truncated
    assert records(result.content) == [
        {"path": ".hidden", "type": "file"},
        {"path": "a-dir", "type": "directory"},
        {"path": "link", "type": "symlink"},
        {"path": "pipe", "type": "other"},
        {"path": "z.txt", "type": "file"},
    ]


def test_lists_only_one_requested_level_with_workspace_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "src" / "nested" / "deep.py").write_text("deep", encoding="utf-8")

    result = ListDirectoryTool(tmp_path).execute(request("src"))

    assert records(result.content) == [
        {"path": "src/a.py", "type": "file"},
        {"path": "src/nested", "type": "directory"},
    ]


def test_empty_directory_returns_complete_empty_output(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    result = ListDirectoryTool(tmp_path).execute(request("empty"))
    assert result.content == ""
    assert not result.is_error
    assert not result.truncated


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("", "portable workspace-relative"),
        ("   ", "portable workspace-relative"),
        ("/tmp", "portable workspace-relative"),
        ("C:/tmp", "portable workspace-relative"),
        ("src\\pkg", "portable workspace-relative"),
        ("./src", "portable workspace-relative"),
        ("src/.", "portable workspace-relative"),
        ("src/..", "portable workspace-relative"),
        ("src//pkg", "portable workspace-relative"),
        ("bad\x00path", "portable workspace-relative"),
    ],
)
def test_rejects_invalid_or_nonportable_paths(tmp_path: Path, path: str, message: str) -> None:
    result = ListDirectoryTool(tmp_path).execute(request(path))
    assert result.is_error
    assert message in result.content


def test_rejects_path_and_component_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_PATH_CHARACTERS", 4)
    assert (
        "portable workspace-relative"
        in ListDirectoryTool(tmp_path).execute(request("abcde")).content
    )
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_PATH_CHARACTERS", 4096)
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_PATH_COMPONENTS", 1)
    assert (
        "portable workspace-relative" in ListDirectoryTool(tmp_path).execute(request("a/b")).content
    )
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_PATH_COMPONENTS", 64)
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_COMPONENT_BYTES", 3)
    assert "component exceeds" in ListDirectoryTool(tmp_path).execute(request("abcd")).content


def test_rejects_malformed_arguments(tmp_path: Path) -> None:
    tool = ListDirectoryTool(tmp_path)
    assert tool.execute(request(arguments={})).content == "list_directory input is malformed"
    assert tool.execute(request(arguments={"path": ".", "extra": "x"})).is_error
    assert tool.execute(request(1)).is_error


def test_rejects_missing_file_and_symlink_targets(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real", target_is_directory=True)
    tool = ListDirectoryTool(tmp_path)

    assert tool.execute(request("missing")).content == "list_directory target does not exist"
    assert tool.execute(request("file.txt")).content == "list_directory target is not a directory"
    assert "symbolic links" in tool.execute(request("link")).content


def test_rejects_symlink_and_non_directory_parent_components(tmp_path: Path) -> None:
    (tmp_path / "real" / "child").mkdir(parents=True)
    (tmp_path / "link").symlink_to("real", target_is_directory=True)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    tool = ListDirectoryTool(tmp_path)

    assert "symbolic links" in tool.execute(request("link/child")).content
    assert tool.execute(request("file/child")).content == (
        "list_directory parent path is not a directory"
    )


def test_count_limit_returns_sorted_prefix_and_explicit_truncation(tmp_path: Path) -> None:
    for index in range(list_directory_module.MAX_LIST_DIRECTORY_RESULTS + 1):
        (tmp_path / f"{index:03}.txt").write_text("x", encoding="utf-8")

    result = ListDirectoryTool(tmp_path).execute(request())

    assert result.truncated and not result.is_error
    assert result.content.endswith(LIST_DIRECTORY_TRUNCATION_MARKER)
    assert len(records(result.content)) == list_directory_module.MAX_LIST_DIRECTORY_RESULTS
    assert records(result.content)[0]["path"] == "000.txt"
    assert records(result.content)[-1]["path"] == "199.txt"


def test_output_byte_limit_is_exact_and_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_OUTPUT_BYTES", 60)

    result = ListDirectoryTool(tmp_path).execute(request())

    assert result.truncated
    assert result.content.endswith(LIST_DIRECTORY_TRUNCATION_MARKER)
    assert len(result.content.encode("utf-8")) <= 60
    assert records(result.content) == [{"path": "a.txt", "type": "file"}]


def test_scan_limit_fails_without_partial_listing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    monkeypatch.setattr(list_directory_module, "MAX_LIST_DIRECTORY_SCANNED_ENTRIES", 1)

    result = ListDirectoryTool(tmp_path).execute(request())

    assert result.is_error
    assert result.content == "list_directory entry scan limit reached; choose a narrower directory"
    assert not result.truncated


def test_model_definition_is_closed_and_exact() -> None:
    assert list_directory_model_definition() == {
        "name": "list_directory",
        "description": (
            "List the direct children of one workspace-relative directory when directory "
            "structure or entry types are needed. Use '.' for the workspace root. This tool "
            "is read-only, bounded, deterministic, non-recursive, includes hidden entries, "
            "and reports regular files, directories, symbolic links, and other entries without "
            "following symbolic links. Output is JSON Lines and may be truncated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Portable workspace-relative directory path, or '.' for the workspace root."
                    ),
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }
