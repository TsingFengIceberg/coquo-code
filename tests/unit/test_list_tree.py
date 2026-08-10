from __future__ import annotations

import json
from pathlib import Path

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.list_tree import (
    MAX_LIST_TREE_DEPTH,
    MAX_LIST_TREE_OUTPUT_BYTES,
    MAX_LIST_TREE_RESULTS,
    ListTreeTool,
)


def request(path: object = ".", max_depth: object = 2, **extra: object) -> ToolUse:
    return ToolUse(
        "tree-1",
        "list_tree",
        ToolArguments.from_mapping({"path": path, "max_depth": max_depth, **extra}),
    )


def records(content: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in content.splitlines()]


def test_lists_deterministic_recursive_tree_with_hidden_and_symlink_entries(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "z.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / ".hidden").write_text("", encoding="utf-8")
    (tmp_path / "src" / "deep" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "alias").symlink_to(tmp_path / "src", target_is_directory=True)

    result = ListTreeTool(tmp_path).execute(request(".", 2))

    assert not result.is_error
    assert records(result.content) == [
        {"depth": 1, "path": "alias", "type": "symlink"},
        {"depth": 1, "path": "src", "type": "directory"},
        {"depth": 2, "path": "src/.hidden", "type": "file"},
        {"depth": 2, "path": "src/deep", "type": "directory"},
        {"depth": 2, "path": "src/z.py", "type": "file"},
    ]


def test_subtree_paths_remain_workspace_relative_and_depth_restarts_at_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text("", encoding="utf-8")

    result = ListTreeTool(tmp_path).execute(request("src", 1))

    assert records(result.content) == [{"depth": 1, "path": "src/pkg", "type": "directory"}]


@pytest.mark.parametrize(
    "call",
    [
        request(1),
        request(".", True),
        request(".", 0),
        request(".", MAX_LIST_TREE_DEPTH + 1),
        request(".", 1, extra=1),
    ],
)
def test_rejects_malformed_input(tmp_path: Path, call: ToolUse) -> None:
    result = ListTreeTool(tmp_path).execute(call)

    assert result.is_error
    assert result.content == "list_tree input is malformed"


def test_rejects_nonportable_symlink_and_file_targets(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    (tmp_path / "file").write_text("", encoding="utf-8")
    (tmp_path / "alias").symlink_to(tmp_path / "dir", target_is_directory=True)
    tool = ListTreeTool(tmp_path)

    for path in ("", "../x", "a//b", "a\\b", "/x"):
        assert "portable workspace-relative path" in tool.execute(request(path)).content
    assert "symbolic links" in tool.execute(request("alias")).content
    assert "target is not a directory" in tool.execute(request("file")).content


def test_truncates_at_count_and_byte_boundaries(tmp_path: Path) -> None:
    for index in range(MAX_LIST_TREE_RESULTS + 10):
        (tmp_path / f"entry-{index:04d}-{'x' * 120}").write_text("", encoding="utf-8")

    result = ListTreeTool(tmp_path).execute(request(".", 1))

    assert result.truncated
    assert result.content.endswith('{"truncated":true}\n')
    assert len(result.content.encode("utf-8")) <= MAX_LIST_TREE_OUTPUT_BYTES
    assert records(result.content)[-1] == {"truncated": True}
