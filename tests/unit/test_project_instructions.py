from __future__ import annotations

import os
from pathlib import Path

import pytest

from leonervis_code.core.project_instructions import (
    MAX_PROJECT_INSTRUCTIONS_BYTES,
    PROJECT_INSTRUCTIONS_FILENAME,
    PROJECT_INSTRUCTIONS_VERSION,
    ProjectInstructionsError,
    ProjectInstructionsLoader,
    render_project_instructions,
)


def test_missing_and_nested_agents_are_not_loaded(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / PROJECT_INSTRUCTIONS_FILENAME).write_text("nested only", encoding="utf-8")

    assert ProjectInstructionsLoader(tmp_path).load() is None


def test_exact_empty_and_crlf_content_is_frozen_with_metadata(tmp_path: Path) -> None:
    target = tmp_path / PROJECT_INSTRUCTIONS_FILENAME
    target.write_bytes(b"")
    empty = ProjectInstructionsLoader(tmp_path).load()
    assert empty is not None
    assert empty.version == PROJECT_INSTRUCTIONS_VERSION
    assert empty.path == PROJECT_INSTRUCTIONS_FILENAME
    assert empty.text == ""
    assert empty.byte_count == 0
    assert empty.fingerprint.startswith("pi-v1-")

    target.write_bytes(b"first\r\nsecond\r\n")
    snapshot = ProjectInstructionsLoader(tmp_path).load()
    assert snapshot is not None
    assert snapshot.text == "first\r\nsecond\r\n"
    assert snapshot.byte_count == 15
    assert render_project_instructions(snapshot).endswith(snapshot.text)


def test_exact_byte_limit_is_accepted_and_one_more_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / PROJECT_INSTRUCTIONS_FILENAME
    target.write_bytes(b"a" * MAX_PROJECT_INSTRUCTIONS_BYTES)
    snapshot = ProjectInstructionsLoader(tmp_path).load()
    assert snapshot is not None
    assert snapshot.byte_count == MAX_PROJECT_INSTRUCTIONS_BYTES

    target.write_bytes(b"a" * (MAX_PROJECT_INSTRUCTIONS_BYTES + 1))
    with pytest.raises(ProjectInstructionsError, match="exceeds 32768"):
        ProjectInstructionsLoader(tmp_path).load()


@pytest.mark.parametrize(
    "content, message",
    [
        (b"\xff", "not valid UTF-8"),
        (b"before\x00after", "must not contain NUL"),
    ],
)
def test_invalid_content_is_rejected(tmp_path: Path, content: bytes, message: str) -> None:
    (tmp_path / PROJECT_INSTRUCTIONS_FILENAME).write_bytes(content)

    with pytest.raises(ProjectInstructionsError, match=message):
        ProjectInstructionsLoader(tmp_path).load()


def test_symlink_directory_and_fifo_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / PROJECT_INSTRUCTIONS_FILENAME
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    target.symlink_to(source)
    with pytest.raises(ProjectInstructionsError, match="symbolic link"):
        ProjectInstructionsLoader(tmp_path).load()

    target.unlink()
    target.mkdir()
    with pytest.raises(ProjectInstructionsError, match="regular file"):
        ProjectInstructionsLoader(tmp_path).load()

    target.rmdir()
    os.mkfifo(target)
    with pytest.raises(ProjectInstructionsError, match="regular file"):
        ProjectInstructionsLoader(tmp_path).load()


def test_in_place_change_during_read_is_rejected(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / PROJECT_INSTRUCTIONS_FILENAME
    target.write_text("before\n", encoding="utf-8")
    original = ProjectInstructionsLoader._read_bounded

    def changing_read(file_fd: int) -> bytes:
        content = original(file_fd)
        target.write_text("after with a different size\n", encoding="utf-8")
        return content

    monkeypatch.setattr(
        ProjectInstructionsLoader,
        "_read_bounded",
        staticmethod(changing_read),
    )
    with pytest.raises(ProjectInstructionsError, match="changed while being read"):
        ProjectInstructionsLoader(tmp_path).load()
