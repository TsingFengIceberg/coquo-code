from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from coquo.core.actions import ActionPreconditionKind
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import PermissionAction
from coquo.tools.patch_file import (
    MAX_PATCH_FILE_EDITS,
    MAX_PATCH_FILE_RESULT_BYTES,
    PatchFileOutcome,
    PatchFilePreparationError,
    PatchFileTool,
)


def request(path: object, edits: object, **extra: object) -> ToolUse:
    return ToolUse(
        "patch-1",
        "patch_file",
        ToolArguments.from_mapping({"path": path, "edits": edits, **extra}),
    )


def edit(old_text: object, new_text: object, **extra: object) -> dict[str, object]:
    return {"old_text": old_text, "new_text": new_text, **extra}


def temporary_files(workspace: Path) -> list[Path]:
    return list(workspace.rglob("*.coquo-*.tmp"))


def test_prepare_builds_all_edits_from_one_snapshot_without_side_effects(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")
    tool = PatchFileTool(tmp_path)

    prepared = tool.prepare(request("note.txt", [edit("gamma", "G"), edit("alpha", "A")]))

    assert target.read_text(encoding="utf-8") == "alpha beta gamma\n"
    assert prepared.content == b"A beta G\n"
    assert prepared.replacements == 2
    assert prepared.action == PermissionAction.WORKSPACE_OVERWRITE
    assert prepared.precondition.kind == ActionPreconditionKind.EXPECTED_STATE_SHA256
    assert temporary_files(tmp_path) == []


def test_execute_atomically_patches_and_preserves_mode(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("one two three", encoding="utf-8")
    target.chmod(0o640)
    tool = PatchFileTool(tmp_path)

    result = tool.execute_detailed(
        tool.prepare(request("note.txt", [edit("one", "1"), edit("three", "3")]))
    )

    assert result.outcome == PatchFileOutcome.SUCCEEDED
    assert result.result_code == "patched"
    assert result.tool_result.content == (
        '{"bytes_written":7,"operation":"patched","path":"note.txt","replacements":2}\n'
    )
    assert target.read_text(encoding="utf-8") == "1 two 3"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert temporary_files(tmp_path) == []


@pytest.mark.parametrize(
    "call",
    [
        ToolUse(
            "patch-1",
            "patch_file",
            ToolArguments.from_mapping({"path": "a"}),
        ),
        request(1, [edit("a", "b")]),
        request("a", "not-list"),
        request("a", [edit("a", "b")], extra=1),
    ],
)
def test_rejects_malformed_top_level_input(tmp_path: Path, call: ToolUse) -> None:
    with pytest.raises(PatchFilePreparationError, match="input is malformed"):
        PatchFileTool(tmp_path).prepare(call)


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ([], "must contain"),
        ([edit("a", "b")] * (MAX_PATCH_FILE_EDITS + 1), "must contain"),
        ([{"old_text": "a"}], r"edits\[0\] is malformed"),
        ([edit(1, "b")], r"edits\[0\] is malformed"),
        ([edit("", "b")], "old_text must not be empty"),
        ([edit("a", "a")], "must change"),
        ([edit("a", "b", extra=1)], r"edits\[0\] is malformed"),
    ],
)
def test_rejects_invalid_edit_collection(
    tmp_path: Path,
    edits: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(PatchFilePreparationError, match=message):
        PatchFileTool(tmp_path).prepare(request("note.txt", edits))


@pytest.mark.parametrize("path", ["", ".", "../x", "a/../x", "a//b", "a\\b", "/x"])
def test_rejects_nonportable_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(PatchFilePreparationError, match="portable workspace-relative"):
        PatchFileTool(tmp_path).prepare(request(path, [edit("a", "b")]))


def test_requires_existing_utf8_regular_nonsymlink_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-patch.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside)
    (tmp_path / "directory").mkdir()
    (tmp_path / "binary").write_bytes(b"\xff")
    tool = PatchFileTool(tmp_path)

    with pytest.raises(PatchFilePreparationError, match="must already exist"):
        tool.prepare(request("missing", [edit("a", "b")]))
    with pytest.raises(PatchFilePreparationError, match="symbolic link"):
        tool.prepare(request("link", [edit("a", "b")]))
    with pytest.raises(PatchFilePreparationError, match="regular file"):
        tool.prepare(request("directory", [edit("a", "b")]))
    with pytest.raises(PatchFilePreparationError, match="valid UTF-8"):
        tool.prepare(request("binary", [edit("a", "b")]))


def test_requires_each_old_text_to_be_unique_in_original_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    tool = PatchFileTool(tmp_path)

    target.write_text("alpha", encoding="utf-8")
    with pytest.raises(PatchFilePreparationError, match="was not found"):
        tool.prepare(request("note.txt", [edit("beta", "x")]))

    target.write_text("alpha alpha", encoding="utf-8")
    with pytest.raises(PatchFilePreparationError, match="matches more than once"):
        tool.prepare(request("note.txt", [edit("alpha", "x")]))


def test_rejects_overlapping_edits_even_when_each_anchor_is_unique(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("abcde", encoding="utf-8")

    with pytest.raises(PatchFilePreparationError, match="exact edits overlap"):
        PatchFileTool(tmp_path).prepare(request("note.txt", [edit("abcd", "x"), edit("cde", "y")]))


def test_rejects_candidate_over_result_bound(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_bytes(b"b" + (b"a" * (MAX_PATCH_FILE_RESULT_BYTES - 1)))

    with pytest.raises(PatchFilePreparationError, match="result exceeds"):
        PatchFileTool(tmp_path).prepare(request("large.txt", [edit("b", "c" * 4096)]))


def test_stale_source_fails_without_losing_external_change(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("before old", encoding="utf-8")
    tool = PatchFileTool(tmp_path)
    prepared = tool.prepare(request("note.txt", [edit("old", "new")]))
    target.write_text("external", encoding="utf-8")

    result = tool.execute_detailed(prepared)

    assert result.outcome == PatchFileOutcome.FAILED
    assert result.result_code == "patch_not_applied"
    assert "conflict" in result.tool_result.content
    assert target.read_text(encoding="utf-8") == "external"


def test_refresh_precondition_observes_change_and_deletion(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    tool = PatchFileTool(tmp_path)
    prepared = tool.prepare(request("note.txt", [edit("before", "after")]))
    target.write_text("external", encoding="utf-8")

    assert tool.refresh_precondition(prepared) != prepared.precondition
    os.unlink(target)
    assert tool.refresh_precondition(prepared).kind == ActionPreconditionKind.PATH_ABSENT


def test_directory_fsync_failure_reports_visible_partial_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    tool = PatchFileTool(tmp_path)
    prepared = tool.prepare(request("note.txt", [edit("before", "after")]))

    monkeypatch.setattr(
        "coquo.tools.write_file._fsync_directory",
        lambda _directory: (_ for _ in ()).throw(OSError("injected")),
    )
    result = tool.execute_detailed(prepared)

    assert result.outcome == PatchFileOutcome.PARTIAL
    assert result.result_code == "patched_durability_unknown"
    assert target.read_text(encoding="utf-8") == "after"
    assert "do not retry automatically" in result.tool_result.content
