from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from coquo.core.actions import ActionPrecondition
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import PermissionAction
from coquo.tools.delete_directory import (
    MAX_DELETE_DIRECTORY_COMPONENT_BYTES,
    MAX_DELETE_DIRECTORY_PATH_BYTES,
    MAX_DELETE_DIRECTORY_PATH_CHARACTERS,
    MAX_DELETE_DIRECTORY_PATH_COMPONENTS,
    DeleteDirectoryOutcome,
    DeleteDirectoryPreparationError,
    DeleteDirectoryTool,
    delete_directory_model_definition,
)


def request(path: object = "empty-dir", *, tool_use_id: str = "rmdir-1") -> ToolUse:
    return ToolUse(tool_use_id, "delete_directory", ToolArguments.from_mapping({"path": path}))


def test_prepare_is_side_effect_free_immutable_and_binds_empty_target(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    prepared = DeleteDirectoryTool(tmp_path).prepare(request())

    assert prepared.path == "empty-dir"
    assert prepared.action == PermissionAction.WORKSPACE_DELETE
    assert prepared.precondition.kind.value == "expected-state-sha256"
    assert target.is_dir()
    with pytest.raises(FrozenInstanceError):
        prepared.path = "other"  # type: ignore[misc]


@pytest.mark.parametrize("arguments", [{}, {"path": "a", "extra": "x"}, {"path": 1}])
def test_prepare_rejects_malformed_arguments(tmp_path: Path, arguments: dict[str, object]) -> None:
    call = ToolUse("rmdir-1", "delete_directory", ToolArguments.from_mapping(arguments))
    with pytest.raises(DeleteDirectoryPreparationError, match="input is malformed"):
        DeleteDirectoryTool(tmp_path).prepare(call)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "/absolute",
        "../escape",
        "a/../b",
        "a/./b",
        "a//b",
        "a/",
        "a\\b",
        "C:/b",
        "nul\x00x",
    ],
)
def test_prepare_rejects_nonportable_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(DeleteDirectoryPreparationError, match="portable workspace-relative"):
        DeleteDirectoryTool(tmp_path).prepare(request(path))


def test_prepare_enforces_path_bounds(tmp_path: Path) -> None:
    tool = DeleteDirectoryTool(tmp_path)
    invalid = [
        "a" * (MAX_DELETE_DIRECTORY_PATH_CHARACTERS + 1),
        "é" * (MAX_DELETE_DIRECTORY_PATH_BYTES // 2 + 1),
        "/".join("a" for _ in range(MAX_DELETE_DIRECTORY_PATH_COMPONENTS + 1)),
    ]
    for path in invalid:
        with pytest.raises(DeleteDirectoryPreparationError):
            tool.prepare(request(path))
    with pytest.raises(DeleteDirectoryPreparationError, match="component exceeds"):
        tool.prepare(request("é" * (MAX_DELETE_DIRECTORY_COMPONENT_BYTES // 2 + 1)))


def test_prepare_rejects_missing_file_symlink_and_non_empty_targets(tmp_path: Path) -> None:
    tool = DeleteDirectoryTool(tmp_path)
    with pytest.raises(DeleteDirectoryPreparationError, match="does not exist"):
        tool.prepare(request())
    (tmp_path / "empty-dir").write_text("keep", encoding="utf-8")
    with pytest.raises(DeleteDirectoryPreparationError, match="must be a directory"):
        tool.prepare(request())
    (tmp_path / "empty-dir").unlink()
    (tmp_path / "real").mkdir()
    (tmp_path / "empty-dir").symlink_to("real", target_is_directory=True)
    with pytest.raises(DeleteDirectoryPreparationError, match="symbolic link"):
        tool.prepare(request())
    (tmp_path / "empty-dir").unlink()
    (tmp_path / "empty-dir").mkdir()
    (tmp_path / "empty-dir" / "child.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(DeleteDirectoryPreparationError, match="must be empty"):
        tool.prepare(request())


def test_prepare_requires_existing_real_directory_parent(tmp_path: Path) -> None:
    tool = DeleteDirectoryTool(tmp_path)
    with pytest.raises(DeleteDirectoryPreparationError, match="parent directory does not exist"):
        tool.prepare(request("missing/empty"))
    (tmp_path / "parent-file").write_text("x", encoding="utf-8")
    with pytest.raises(DeleteDirectoryPreparationError, match="not a directory"):
        tool.prepare(request("parent-file/empty"))
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "empty").mkdir()
    (tmp_path / "link").symlink_to("real", target_is_directory=True)
    with pytest.raises(DeleteDirectoryPreparationError, match="symbolic link"):
        tool.prepare(request("link/empty"))


def test_execute_deletes_empty_directory_and_returns_closed_json(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    result = tool.execute_detailed(tool.prepare(request()))

    assert result.outcome == DeleteDirectoryOutcome.SUCCEEDED
    assert result.result_code == "directory_deleted"
    assert result.tool_result.content == '{"operation":"deleted","path":"empty-dir"}\n'
    assert not target.exists()


def test_execute_rejects_stale_target_without_deleting_replacement(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    prepared = tool.prepare(request())
    target.rmdir()
    target.mkdir()

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteDirectoryOutcome.FAILED
    assert result.result_code == "directory_not_deleted"
    assert target.is_dir()


def test_execute_rejects_changed_parent(tmp_path: Path) -> None:
    parent = tmp_path / "old"
    parent.mkdir()
    (parent / "empty").mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    prepared = tool.prepare(request("old/empty"))
    parent.rename(tmp_path / "moved")
    parent.mkdir()
    replacement = parent / "empty"
    replacement.mkdir()

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteDirectoryOutcome.FAILED
    assert replacement.is_dir()


def test_execute_rejects_child_created_after_prepare(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    prepared = tool.prepare(request())
    (target / "child.txt").write_text("keep", encoding="utf-8")

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteDirectoryOutcome.FAILED
    assert result.result_code == "directory_not_deleted"
    assert (target / "child.txt").read_text(encoding="utf-8") == "keep"


def test_rmdir_failure_reports_failed_and_keeps_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.delete_directory.os.rmdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError()),
    )

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteDirectoryOutcome.FAILED
    assert result.result_code == "directory_not_deleted"
    assert target.is_dir()


def test_parent_fsync_failure_reports_deleted_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.delete_directory._fsync_directory",
        lambda _fd: (_ for _ in ()).throw(OSError()),
    )

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteDirectoryOutcome.PARTIAL
    assert result.result_code == "directory_deleted_durability_unknown"
    assert "do not retry automatically" in result.tool_result.content
    assert not target.exists()


def test_execute_rejects_invalid_precondition(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()
    tool = DeleteDirectoryTool(tmp_path)
    prepared = replace(tool.prepare(request()), precondition=ActionPrecondition.none())
    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteDirectoryOutcome.FAILED
    assert result.tool_result.content == "delete_directory precondition is invalid"


def test_model_definition_is_closed_and_exact() -> None:
    assert delete_directory_model_definition() == {
        "name": "delete_directory",
        "description": (
            "Permanently delete one existing empty workspace-relative directory. The Host "
            "applies workspace-delete permission and approval policy, rejects symlinks, stale "
            "paths, non-empty directories, and the workspace root, and requires the parent "
            "directory to already exist. This does not provide recursive deletion or recovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of the empty directory to delete.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }
