from __future__ import annotations

import pytest

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.catalog import (
    TOOL_CATALOG,
    tool_input_from_use,
    tool_use_from_input,
)


def test_catalog_exposes_move_file_last_with_shared_closed_schema() -> None:
    assert [definition.name for definition in TOOL_CATALOG] == [
        "read_file",
        "glob",
        "grep",
        "write_file",
        "edit_file",
        "run_command",
        "mkdir",
        "move_file",
        "delete_file",
        "delete_directory",
    ]
    request = tool_use_from_input(
        "edit-1",
        "edit_file",
        {"path": "note.txt", "old_text": " \n", "new_text": ""},
    )
    assert request == ToolUse(
        "edit-1",
        "edit_file",
        ToolArguments.from_mapping({"path": "note.txt", "old_text": " \n", "new_text": ""}),
    )
    assert tool_input_from_use(request) == {
        "path": "note.txt",
        "old_text": " \n",
        "new_text": "",
    }


@pytest.mark.parametrize(
    "tool_input",
    [
        {"path": "note.txt", "old_text": "", "new_text": "after"},
        {"path": "note.txt", "old_text": "before"},
        {"path": "note.txt", "old_text": "before", "new_text": "after", "extra": "x"},
        {"path": "note.txt", "old_text": "before", "new_text": 1},
    ],
)
def test_catalog_rejects_malformed_edit_file_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="edit_file"):
        tool_use_from_input("edit-1", "edit_file", tool_input)


def test_catalog_validates_closed_run_command_array_and_integer_input() -> None:
    command = tool_use_from_input(
        "command-1",
        "run_command",
        {"argv": ["uv", "run", "pytest", ""], "cwd": ".", "timeout_seconds": 60},
    )
    assert command == ToolUse(
        "command-1",
        "run_command",
        ToolArguments.from_mapping(
            {"argv": ["uv", "run", "pytest", ""], "cwd": ".", "timeout_seconds": 60}
        ),
    )
    assert tool_input_from_use(command) == {
        "argv": ["uv", "run", "pytest", ""],
        "cwd": ".",
        "timeout_seconds": 60,
    }


@pytest.mark.parametrize(
    "tool_input",
    [
        {"argv": [], "cwd": ".", "timeout_seconds": 60},
        {"argv": ["uv", 1], "cwd": ".", "timeout_seconds": 60},
        {"argv": ["uv"], "cwd": ".", "timeout_seconds": True},
        {"argv": ["uv"], "cwd": ".", "timeout_seconds": 301},
        {"argv": ["uv"], "cwd": "."},
    ],
)
def test_catalog_rejects_malformed_run_command_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="run_command"):
        tool_use_from_input("command-1", "run_command", tool_input)


def test_catalog_validates_closed_mkdir_input() -> None:
    call = tool_use_from_input("mkdir-1", "mkdir", {"path": "src/pkg"})
    assert call == ToolUse(
        "mkdir-1",
        "mkdir",
        ToolArguments.from_mapping({"path": "src/pkg"}),
    )
    assert tool_input_from_use(call) == {"path": "src/pkg"}


@pytest.mark.parametrize(
    "tool_input",
    [
        {},
        {"path": "src", "extra": "x"},
        {"path": 1},
        {"path": ""},
    ],
)
def test_catalog_rejects_malformed_mkdir_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="mkdir"):
        tool_use_from_input("mkdir-1", "mkdir", tool_input)


def test_catalog_validates_closed_move_file_input() -> None:
    call = tool_use_from_input(
        "move-1", "move_file", {"source": "src/a.py", "destination": "src/b.py"}
    )
    assert tool_input_from_use(call) == {"source": "src/a.py", "destination": "src/b.py"}


@pytest.mark.parametrize(
    "tool_input",
    [
        {},
        {"source": "a"},
        {"source": "a", "destination": "b", "extra": "x"},
        {"source": 1, "destination": "b"},
        {"source": "a", "destination": ""},
    ],
)
def test_catalog_rejects_malformed_move_file_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="move_file"):
        tool_use_from_input("move-1", "move_file", tool_input)


def test_catalog_validates_closed_delete_file_input() -> None:
    call = tool_use_from_input("delete-1", "delete_file", {"path": "src/obsolete.py"})
    assert call == ToolUse(
        "delete-1",
        "delete_file",
        ToolArguments.from_mapping({"path": "src/obsolete.py"}),
    )
    assert tool_input_from_use(call) == {"path": "src/obsolete.py"}


@pytest.mark.parametrize("tool_input", [{}, {"path": 1}, {"path": ""}, {"path": "a", "extra": "x"}])
def test_catalog_rejects_malformed_delete_file_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="delete_file"):
        tool_use_from_input("delete-1", "delete_file", tool_input)


def test_catalog_validates_closed_delete_directory_input() -> None:
    call = tool_use_from_input("rmdir-1", "delete_directory", {"path": "build/empty"})
    assert call == ToolUse(
        "rmdir-1",
        "delete_directory",
        ToolArguments.from_mapping({"path": "build/empty"}),
    )
    assert tool_input_from_use(call) == {"path": "build/empty"}


@pytest.mark.parametrize("tool_input", [{}, {"path": 1}, {"path": ""}, {"path": "a", "extra": "x"}])
def test_catalog_rejects_malformed_delete_directory_inputs(
    tool_input: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="delete_directory"):
        tool_use_from_input("rmdir-1", "delete_directory", tool_input)
