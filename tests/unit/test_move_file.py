from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path

import pytest

from coquo.core.actions import ActionPrecondition
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import PermissionAction
from coquo.tools.move_file import (
    MAX_MOVE_COMPONENT_BYTES,
    MAX_MOVE_PATH_BYTES,
    MAX_MOVE_PATH_CHARACTERS,
    MAX_MOVE_PATH_COMPONENTS,
    MoveFileOutcome,
    MoveFilePreparationError,
    MoveFileTool,
    move_file_model_definition,
)


def request(
    source: object = "source.txt",
    destination: object = "destination.txt",
    *,
    tool_use_id: str = "move-1",
) -> ToolUse:
    return ToolUse(
        tool_use_id,
        "move_file",
        ToolArguments.from_mapping({"source": source, "destination": destination}),
    )


def test_prepare_is_side_effect_free_immutable_and_binds_both_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("hello\n", encoding="utf-8")
    tool = MoveFileTool(tmp_path)

    prepared = tool.prepare(request())

    assert prepared.source_path == "source.txt"
    assert prepared.destination_path == "destination.txt"
    assert prepared.action == PermissionAction.WORKSPACE_MOVE
    assert prepared.precondition.kind.value == "expected-state-sha256"
    assert source.read_text(encoding="utf-8") == "hello\n"
    assert not (tmp_path / "destination.txt").exists()
    with pytest.raises(FrozenInstanceError):
        prepared.source_path = "other.txt"  # type: ignore[misc]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"source": "a", "destination": "b", "extra": "x"},
        {"source": 1, "destination": "b"},
        {"source": "a", "destination": 1},
    ],
)
def test_prepare_rejects_malformed_arguments(tmp_path: Path, arguments: dict[str, object]) -> None:
    call = ToolUse("move-1", "move_file", ToolArguments.from_mapping(arguments))
    with pytest.raises(MoveFilePreparationError, match="input is malformed"):
        MoveFileTool(tmp_path).prepare(call)


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
def test_prepare_rejects_nonportable_source_and_destination_paths(
    tmp_path: Path, path: str
) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    with pytest.raises(MoveFilePreparationError, match="portable workspace-relative"):
        tool.prepare(request(path, "destination.txt"))
    with pytest.raises(MoveFilePreparationError, match="portable workspace-relative"):
        tool.prepare(request("source.txt", path))


def test_prepare_enforces_character_byte_and_component_bounds(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    invalid = [
        "a" * (MAX_MOVE_PATH_CHARACTERS + 1),
        "é" * (MAX_MOVE_PATH_BYTES // 2 + 1),
        "/".join("a" for _ in range(MAX_MOVE_PATH_COMPONENTS + 1)),
    ]
    for path in invalid:
        with pytest.raises(MoveFilePreparationError):
            tool.prepare(request("source.txt", path))
    with pytest.raises(MoveFilePreparationError, match="component exceeds"):
        tool.prepare(request("source.txt", "é" * (MAX_MOVE_COMPONENT_BYTES // 2 + 1)))


def test_prepare_rejects_same_path_missing_nonregular_and_symlink_source(tmp_path: Path) -> None:
    tool = MoveFileTool(tmp_path)
    with pytest.raises(MoveFilePreparationError, match="does not exist"):
        tool.prepare(request())
    (tmp_path / "source.txt").mkdir()
    with pytest.raises(MoveFilePreparationError, match="regular file"):
        tool.prepare(request())
    (tmp_path / "source.txt").rmdir()
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")
    (tmp_path / "source.txt").symlink_to("real.txt")
    with pytest.raises(MoveFilePreparationError, match="symbolic link"):
        tool.prepare(request())
    with pytest.raises(MoveFilePreparationError, match="must differ"):
        tool.prepare(request("real.txt", "real.txt"))


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_prepare_rejects_every_existing_destination(tmp_path: Path, kind: str) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    if kind == "file":
        destination.write_text("existing", encoding="utf-8")
    elif kind == "directory":
        destination.mkdir()
    else:
        destination.symlink_to("missing")
    with pytest.raises(MoveFilePreparationError, match="destination already exists"):
        MoveFileTool(tmp_path).prepare(request())


def test_prepare_requires_existing_real_directory_parents(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    (tmp_path / "plain").write_text("x", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    tool = MoveFileTool(tmp_path)
    with pytest.raises(MoveFilePreparationError, match="does not exist"):
        tool.prepare(request("source.txt", "missing/destination.txt"))
    with pytest.raises(MoveFilePreparationError, match="not a directory"):
        tool.prepare(request("plain/source.txt", "destination.txt"))
    with pytest.raises(MoveFilePreparationError, match="symbolic link"):
        tool.prepare(request("source.txt", "link/destination.txt"))


def test_prepare_rejects_cross_filesystem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    original = tool._observe_source

    def changed_device(path: Path):
        observed = original(path)
        return replace(observed, device=observed.device + 1)

    monkeypatch.setattr(tool, "_observe_source", changed_device)
    with pytest.raises(MoveFilePreparationError, match="share a filesystem"):
        tool.prepare(request())


def test_successfully_moves_same_parent_and_different_parent_without_reading_content(
    tmp_path: Path,
) -> None:
    payload = os.urandom(1024 * 1024)
    (tmp_path / "source.bin").write_bytes(payload)
    (tmp_path / "dst").mkdir()
    tool = MoveFileTool(tmp_path)

    first = tool.execute_detailed(tool.prepare(request("source.bin", "renamed.bin")))
    second = tool.execute_detailed(tool.prepare(request("renamed.bin", "dst/final.bin")))

    assert first.outcome == second.outcome == MoveFileOutcome.SUCCEEDED
    assert first.result_code == second.result_code == "file_moved"
    assert (
        second.tool_result.content
        == '{"destination":"dst/final.bin","operation":"moved","source":"renamed.bin"}\n'
    )
    assert not (tmp_path / "source.bin").exists()
    assert not (tmp_path / "renamed.bin").exists()
    assert (tmp_path / "dst/final.bin").read_bytes() == payload


def test_refresh_and_execute_reject_stale_source_or_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("before", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    stale_source = tool.prepare(request())
    source.write_text("after", encoding="utf-8")
    assert tool.refresh_precondition(stale_source) != stale_source.precondition
    result = tool.execute_detailed(stale_source)
    assert result.outcome == MoveFileOutcome.FAILED
    assert source.read_text(encoding="utf-8") == "after"
    assert not (tmp_path / "destination.txt").exists()

    source.write_text("fresh", encoding="utf-8")
    stale_destination = tool.prepare(request())
    (tmp_path / "destination.txt").write_text("external", encoding="utf-8")
    result = tool.execute_detailed(stale_destination)
    assert result.outcome == MoveFileOutcome.FAILED
    assert source.exists()
    assert (tmp_path / "destination.txt").read_text(encoding="utf-8") == "external"


def test_destination_creation_race_never_overwrites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    prepared = tool.prepare(request())
    real_link = os.link

    def race(*args, **kwargs):
        destination.write_text("external", encoding="utf-8")
        return real_link(*args, **kwargs)

    monkeypatch.setattr("coquo.tools.move_file.os.link", race)
    result = tool.execute_detailed(prepared)
    assert result.outcome == MoveFileOutcome.FAILED
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "external"


def test_link_failure_before_effect_is_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.move_file.os.link",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError()),
    )
    result = tool.execute_detailed(prepared)
    assert result.outcome == MoveFileOutcome.FAILED
    assert result.result_code == "file_not_moved"
    assert (tmp_path / "source.txt").exists()
    assert not (tmp_path / "destination.txt").exists()


def test_destination_fsync_failure_reports_two_names_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.move_file.os.fsync", lambda _fd: (_ for _ in ()).throw(OSError())
    )
    result = tool.execute_detailed(prepared)
    assert result.outcome == MoveFileOutcome.PARTIAL
    assert result.result_code == "destination_linked_source_retained_durability_unknown"
    assert (tmp_path / "source.txt").exists() and (tmp_path / "destination.txt").exists()


def test_unlink_failure_reports_two_names_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.move_file.os.unlink", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    result = tool.execute_detailed(prepared)
    assert result.outcome == MoveFileOutcome.PARTIAL
    assert result.result_code == "destination_linked_source_retained"
    assert (tmp_path / "source.txt").exists() and (tmp_path / "destination.txt").exists()


def test_source_parent_fsync_failure_reports_moved_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    prepared = tool.prepare(request())
    calls = 0
    real_fsync = os.fsync

    def fail_second(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError
        real_fsync(fd)

    monkeypatch.setattr("coquo.tools.move_file.os.fsync", fail_second)
    result = tool.execute_detailed(prepared)
    assert result.outcome == MoveFileOutcome.PARTIAL
    assert result.result_code == "file_moved_durability_unknown"
    assert not (tmp_path / "source.txt").exists()
    assert (tmp_path / "destination.txt").exists()


def test_execute_rejects_invalid_precondition(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("x", encoding="utf-8")
    tool = MoveFileTool(tmp_path)
    prepared = replace(tool.prepare(request()), precondition=ActionPrecondition.none())
    result = tool.execute_detailed(prepared)
    assert result.outcome == MoveFileOutcome.FAILED
    assert result.tool_result.content == "move_file precondition is invalid"


def test_model_definition_is_closed_and_exact() -> None:
    assert move_file_model_definition() == {
        "name": "move_file",
        "description": (
            "Move one existing workspace-relative regular file to one missing workspace-relative "
            "destination. The Host applies workspace-move permission and approval policy, rejects "
            "symlinks, stale paths, directory sources, cross-filesystem moves, and destination "
            "replacement. Destination parents must already exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Portable workspace-relative path of the existing source file.",
                },
                "destination": {
                    "type": "string",
                    "description": "Portable workspace-relative missing destination path.",
                },
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
    }
