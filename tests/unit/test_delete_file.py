from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from coquo.core.actions import ActionPrecondition
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import PermissionAction
from coquo.tools.delete_file import (
    MAX_DELETE_COMPONENT_BYTES,
    MAX_DELETE_PATH_BYTES,
    MAX_DELETE_PATH_CHARACTERS,
    MAX_DELETE_PATH_COMPONENTS,
    DeleteFileOutcome,
    DeleteFilePreparationError,
    DeleteFileTool,
    delete_file_model_definition,
)


def request(path: object = "obsolete.txt", *, tool_use_id: str = "delete-1") -> ToolUse:
    return ToolUse(tool_use_id, "delete_file", ToolArguments.from_mapping({"path": path}))


def test_prepare_is_side_effect_free_immutable_and_binds_exact_target(tmp_path: Path) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_bytes(b"\x00binary\xff")
    prepared = DeleteFileTool(tmp_path).prepare(request())

    assert prepared.path == "obsolete.txt"
    assert prepared.action == PermissionAction.WORKSPACE_DELETE
    assert prepared.precondition.kind.value == "expected-state-sha256"
    assert target.read_bytes() == b"\x00binary\xff"
    with pytest.raises(FrozenInstanceError):
        prepared.path = "other.txt"  # type: ignore[misc]


@pytest.mark.parametrize("arguments", [{}, {"path": "a", "extra": "x"}, {"path": 1}])
def test_prepare_rejects_malformed_arguments(tmp_path: Path, arguments: dict[str, object]) -> None:
    call = ToolUse("delete-1", "delete_file", ToolArguments.from_mapping(arguments))
    with pytest.raises(DeleteFilePreparationError, match="input is malformed"):
        DeleteFileTool(tmp_path).prepare(call)


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
    with pytest.raises(DeleteFilePreparationError, match="portable workspace-relative"):
        DeleteFileTool(tmp_path).prepare(request(path))


def test_prepare_enforces_path_bounds(tmp_path: Path) -> None:
    tool = DeleteFileTool(tmp_path)
    invalid = [
        "a" * (MAX_DELETE_PATH_CHARACTERS + 1),
        "é" * (MAX_DELETE_PATH_BYTES // 2 + 1),
        "/".join("a" for _ in range(MAX_DELETE_PATH_COMPONENTS + 1)),
    ]
    for path in invalid:
        with pytest.raises(DeleteFilePreparationError):
            tool.prepare(request(path))
    with pytest.raises(DeleteFilePreparationError, match="component exceeds"):
        tool.prepare(request("é" * (MAX_DELETE_COMPONENT_BYTES // 2 + 1)))


def test_prepare_rejects_missing_directory_and_symlink_targets(tmp_path: Path) -> None:
    tool = DeleteFileTool(tmp_path)
    with pytest.raises(DeleteFilePreparationError, match="does not exist"):
        tool.prepare(request())
    (tmp_path / "obsolete.txt").mkdir()
    with pytest.raises(DeleteFilePreparationError, match="regular file"):
        tool.prepare(request())
    (tmp_path / "obsolete.txt").rmdir()
    (tmp_path / "real.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "obsolete.txt").symlink_to("real.txt")
    with pytest.raises(DeleteFilePreparationError, match="symbolic link"):
        tool.prepare(request())
    assert (tmp_path / "real.txt").read_text(encoding="utf-8") == "keep"


def test_prepare_requires_existing_real_directory_parent(tmp_path: Path) -> None:
    tool = DeleteFileTool(tmp_path)
    with pytest.raises(DeleteFilePreparationError, match="parent directory does not exist"):
        tool.prepare(request("missing/file.txt"))
    (tmp_path / "parent-file").write_text("x", encoding="utf-8")
    with pytest.raises(DeleteFilePreparationError, match="not a directory"):
        tool.prepare(request("parent-file/file.txt"))
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "link").symlink_to("real", target_is_directory=True)
    with pytest.raises(DeleteFilePreparationError, match="symbolic link"):
        tool.prepare(request("link/file.txt"))


def test_execute_deletes_regular_file_and_returns_closed_json(tmp_path: Path) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_bytes(b"x" * 100_000)
    tool = DeleteFileTool(tmp_path)
    result = tool.execute_detailed(tool.prepare(request()))

    assert result.outcome == DeleteFileOutcome.SUCCEEDED
    assert result.result_code == "file_deleted"
    assert result.tool_result.content == '{"operation":"deleted","path":"obsolete.txt"}\n'
    assert not target.exists()


def test_execute_rejects_stale_target_without_deleting_replacement(tmp_path: Path) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("old", encoding="utf-8")
    tool = DeleteFileTool(tmp_path)
    prepared = tool.prepare(request())
    target.unlink()
    target.write_text("replacement", encoding="utf-8")

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteFileOutcome.FAILED
    assert result.result_code == "file_not_deleted"
    assert target.read_text(encoding="utf-8") == "replacement"


def test_execute_rejects_changed_parent(tmp_path: Path) -> None:
    parent = tmp_path / "old"
    parent.mkdir()
    (parent / "obsolete.txt").write_text("old", encoding="utf-8")
    tool = DeleteFileTool(tmp_path)
    prepared = tool.prepare(request("old/obsolete.txt"))
    parent.rename(tmp_path / "moved")
    parent.mkdir()
    replacement = parent / "obsolete.txt"
    replacement.write_text("replacement", encoding="utf-8")

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteFileOutcome.FAILED
    assert replacement.read_text(encoding="utf-8") == "replacement"


def test_unlink_failure_reports_failed_and_keeps_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("x", encoding="utf-8")
    tool = DeleteFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.delete_file.os.unlink",
        lambda *a, **k: (_ for _ in ()).throw(OSError()),
    )

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteFileOutcome.FAILED
    assert result.result_code == "file_not_deleted"
    assert target.exists()


def test_parent_fsync_failure_reports_deleted_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("x", encoding="utf-8")
    tool = DeleteFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.delete_file._fsync_directory",
        lambda _fd: (_ for _ in ()).throw(OSError()),
    )

    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteFileOutcome.PARTIAL
    assert result.result_code == "file_deleted_durability_unknown"
    assert "do not retry automatically" in result.tool_result.content
    assert not target.exists()


def test_execute_rejects_invalid_precondition(tmp_path: Path) -> None:
    (tmp_path / "obsolete.txt").write_text("x", encoding="utf-8")
    tool = DeleteFileTool(tmp_path)
    prepared = replace(tool.prepare(request()), precondition=ActionPrecondition.none())
    result = tool.execute_detailed(prepared)
    assert result.outcome == DeleteFileOutcome.FAILED
    assert result.tool_result.content == "delete_file precondition is invalid"


def test_model_definition_is_closed_and_exact() -> None:
    assert delete_file_model_definition() == {
        "name": "delete_file",
        "description": (
            "Permanently delete one existing workspace-relative regular file. The Host applies "
            "workspace-delete permission and approval policy, rejects symlinks, stale paths, and "
            "directories, and requires the parent directory to already exist. This does not "
            "provide recursive deletion or recovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Portable workspace-relative path of the regular file to delete.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }
