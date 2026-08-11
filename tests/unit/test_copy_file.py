from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat

import pytest

from coquo.core.actions import ActionPrecondition
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import PermissionAction
from coquo.tools.copy_file import (
    MAX_COPY_SOURCE_BYTES,
    CopyFileOutcome,
    CopyFilePreparationError,
    CopyFileTool,
    copy_file_model_definition,
)


def request(
    source: str = "source.bin",
    destination: str = "destination.bin",
    *,
    tool_use_id: str = "copy-1",
) -> ToolUse:
    return ToolUse(
        tool_use_id,
        "copy_file",
        ToolArguments.from_mapping({"source": source, "destination": destination}),
    )


def test_copy_binary_file_without_changing_source_and_preserves_mode(tmp_path: Path) -> None:
    content = b"\x00binary\xff\n"
    source = tmp_path / "source.bin"
    source.write_bytes(content)
    source.chmod(0o4751)
    tool = CopyFileTool(tmp_path)

    prepared = tool.prepare(request())
    result = tool.execute_detailed(prepared)

    assert prepared.action == PermissionAction.WORKSPACE_CREATE
    assert prepared.content == content
    assert result.outcome == CopyFileOutcome.SUCCEEDED
    assert result.result_code == "file_copied"
    assert result.tool_result.content == (
        '{"bytes_copied":9,"destination":"destination.bin",'
        '"operation":"copied","source":"source.bin"}\n'
    )
    assert source.read_bytes() == content
    assert (tmp_path / "destination.bin").read_bytes() == content
    assert stat.S_IMODE((tmp_path / "destination.bin").stat().st_mode) == 0o751


def test_copy_empty_file(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"")
    result = CopyFileTool(tmp_path).execute_detailed(CopyFileTool(tmp_path).prepare(request()))
    assert result.outcome == CopyFileOutcome.SUCCEEDED
    assert (tmp_path / "destination.bin").read_bytes() == b""


def test_copy_accepts_exact_source_size_limit(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"x" * MAX_COPY_SOURCE_BYTES)
    tool = CopyFileTool(tmp_path)
    assert tool.execute_detailed(tool.prepare(request())).outcome == CopyFileOutcome.SUCCEEDED


def test_copy_rejects_source_over_size_limit(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"x" * (MAX_COPY_SOURCE_BYTES + 1))
    with pytest.raises(CopyFilePreparationError, match="source exceeds"):
        CopyFileTool(tmp_path).prepare(request())
    assert not (tmp_path / "destination.bin").exists()


@pytest.mark.parametrize(
    "source,destination",
    [
        ("", "destination.bin"),
        ("/etc/passwd", "destination.bin"),
        ("../source.bin", "destination.bin"),
        ("source.bin", "../destination.bin"),
        ("source.bin", "C:/destination.bin"),
        ("source.bin", "dir\\destination.bin"),
        ("source.bin", "."),
    ],
)
def test_copy_rejects_nonportable_paths(tmp_path: Path, source: str, destination: str) -> None:
    (tmp_path / "source.bin").write_bytes(b"x")
    with pytest.raises(CopyFilePreparationError, match="portable workspace-relative"):
        CopyFileTool(tmp_path).prepare(request(source, destination))


def test_copy_rejects_same_path(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"x")
    with pytest.raises(CopyFilePreparationError, match="must differ"):
        CopyFileTool(tmp_path).prepare(request("source.bin", "source.bin"))


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_copy_rejects_nonregular_source(tmp_path: Path, kind: str) -> None:
    if kind == "directory":
        (tmp_path / "source.bin").mkdir()
    else:
        (tmp_path / "real.bin").write_bytes(b"x")
        (tmp_path / "source.bin").symlink_to("real.bin")
    with pytest.raises(CopyFilePreparationError, match="regular file|symbolic link"):
        CopyFileTool(tmp_path).prepare(request())


@pytest.mark.parametrize("side", ["source", "destination"])
def test_copy_rejects_symlinked_parent(tmp_path: Path, side: str) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real/source.bin").write_bytes(b"x")
    (tmp_path / "linked").symlink_to("real", target_is_directory=True)
    source = "linked/source.bin" if side == "source" else "real/source.bin"
    destination = "linked/destination.bin" if side == "destination" else "real/destination.bin"
    with pytest.raises(CopyFilePreparationError, match="contains a symbolic link"):
        CopyFileTool(tmp_path).prepare(request(source, destination))


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_copy_rejects_existing_destination(tmp_path: Path, kind: str) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    destination = tmp_path / "destination.bin"
    if kind == "file":
        destination.write_bytes(b"existing")
    elif kind == "directory":
        destination.mkdir()
    else:
        destination.symlink_to("source.bin")
    with pytest.raises(CopyFilePreparationError, match="destination already exists"):
        CopyFileTool(tmp_path).prepare(request())


@pytest.mark.parametrize(
    "changed", ["source", "destination", "source_parent", "destination_parent"]
)
def test_copy_detects_stale_state_before_execution(tmp_path: Path, changed: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    source = tmp_path / "src/source.bin"
    source.write_bytes(b"before")
    tool = CopyFileTool(tmp_path)
    prepared = tool.prepare(request("src/source.bin", "dst/destination.bin"))
    if changed == "source":
        source.write_bytes(b"after")
    elif changed == "destination":
        (tmp_path / "dst/destination.bin").write_bytes(b"external")
    elif changed == "source_parent":
        (tmp_path / "src").rename(tmp_path / "old-src")
        (tmp_path / "src").mkdir()
        (tmp_path / "src/source.bin").write_bytes(b"before")
    else:
        (tmp_path / "dst").rename(tmp_path / "old-dst")
        (tmp_path / "dst").mkdir()

    result = tool.execute_detailed(prepared)

    assert result.outcome == CopyFileOutcome.FAILED
    assert result.result_code == "file_not_copied"
    assert "conflict" in result.tool_result.content


def test_link_destination_race_does_not_overwrite(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    tool = CopyFileTool(tmp_path)
    prepared = tool.prepare(request())
    real_link = os.link

    def race_link(*args, **kwargs):
        (tmp_path / "destination.bin").write_bytes(b"external")
        return real_link(*args, **kwargs)

    monkeypatch.setattr("coquo.tools.copy_file.os.link", race_link)
    result = tool.execute_detailed(prepared)
    assert result.outcome == CopyFileOutcome.FAILED
    assert (tmp_path / "destination.bin").read_bytes() == b"external"
    assert not tuple(tmp_path.glob(".*.coquo-*.tmp"))


def test_temporary_file_fsync_failure_has_no_destination(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    tool = CopyFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.copy_file._fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("injected")),
    )
    result = tool.execute_detailed(prepared)
    assert result.outcome == CopyFileOutcome.FAILED
    assert not (tmp_path / "destination.bin").exists()
    assert not tuple(tmp_path.glob(".*.coquo-*.tmp"))


def test_preinstall_failure_with_cleanup_failure_reports_partial(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    tool = CopyFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.copy_file._fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("injected fsync")),
    )
    monkeypatch.setattr(
        "coquo.tools.copy_file.os.unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected cleanup")),
    )
    result = tool.execute_detailed(prepared)
    assert result.outcome == CopyFileOutcome.PARTIAL
    assert result.result_code == "temporary_cleanup_failed_destination_absent"
    assert not (tmp_path / "destination.bin").exists()
    assert tuple(tmp_path.glob(".*.coquo-*.tmp"))


def test_directory_fsync_failure_reports_copied_partial(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    tool = CopyFileTool(tmp_path)
    prepared = tool.prepare(request())
    calls = 0
    real_fsync = os.fsync

    def fail_second(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real_fsync(fd)

    monkeypatch.setattr("coquo.tools.copy_file._fsync", fail_second)
    result = tool.execute_detailed(prepared)
    assert result.outcome == CopyFileOutcome.PARTIAL
    assert result.result_code == "file_copied_durability_unknown"
    assert (tmp_path / "source.bin").exists()
    assert (tmp_path / "destination.bin").read_bytes() == b"source"


def test_temporary_cleanup_failure_reports_durable_partial(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    tool = CopyFileTool(tmp_path)
    prepared = tool.prepare(request())
    monkeypatch.setattr(
        "coquo.tools.copy_file.os.unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")),
    )
    result = tool.execute_detailed(prepared)
    assert result.outcome == CopyFileOutcome.PARTIAL
    assert result.result_code == "copied_with_temporary_cleanup_failure"
    assert (tmp_path / "destination.bin").read_bytes() == b"source"
    assert tuple(tmp_path.glob(".*.coquo-*.tmp"))


def test_execute_rejects_invalid_precondition(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"x")
    tool = CopyFileTool(tmp_path)
    prepared = replace(tool.prepare(request()), precondition=ActionPrecondition.none())
    result = tool.execute_detailed(prepared)
    assert result.outcome == CopyFileOutcome.FAILED
    assert result.tool_result.content == "copy_file precondition is invalid"


def test_model_definition_is_closed_and_exact() -> None:
    definition = copy_file_model_definition()
    assert definition["name"] == "copy_file"
    assert definition["input_schema"] == {
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
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"source": "source.bin"},
        {"source": "source.bin", "destination": "destination.bin", "extra": "x"},
        {"source": 1, "destination": "destination.bin"},
    ],
)
def test_prepare_rejects_malformed_input(tmp_path: Path, arguments: dict[str, object]) -> None:
    malformed = ToolUse("copy-1", "copy_file", ToolArguments.from_mapping(arguments))
    with pytest.raises(CopyFilePreparationError, match="input is malformed"):
        CopyFileTool(tmp_path).prepare(malformed)
