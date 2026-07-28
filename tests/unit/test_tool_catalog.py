from __future__ import annotations

import pytest

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.catalog import (
    TOOL_CATALOG,
    tool_input_from_use,
    tool_use_from_input,
)


def test_catalog_exposes_all_tools_in_canonical_order_with_shared_closed_schema() -> None:
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
        "list_directory",
        "copy_file",
        "read_file_lines",
        "stat_path",
        "list_tree",
        "grep_regex",
        "patch_file",
        "git_status",
        "git_diff",
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


def test_catalog_factory_preserves_optional_assistant_text_without_changing_arguments() -> None:
    request = tool_use_from_input(
        "read-1",
        "read_file",
        {"path": "README.md"},
        assistant_text="I will read the file.",
    )

    assert request == ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will read the file.",
    )
    assert tool_input_from_use(request) == {"path": "README.md"}


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


def test_catalog_validates_closed_copy_file_input() -> None:
    call = tool_use_from_input(
        "copy-1", "copy_file", {"source": "src/a.py", "destination": "src/b.py"}
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
def test_catalog_rejects_malformed_copy_file_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="copy_file"):
        tool_use_from_input("copy-1", "copy_file", tool_input)


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


def test_catalog_validates_closed_list_directory_input() -> None:
    call = tool_use_from_input("list-1", "list_directory", {"path": "."})
    assert call == ToolUse(
        "list-1",
        "list_directory",
        ToolArguments.from_mapping({"path": "."}),
    )
    assert tool_input_from_use(call) == {"path": "."}


@pytest.mark.parametrize("tool_input", [{}, {"path": 1}, {"path": ""}, {"path": ".", "x": 1}])
def test_catalog_rejects_malformed_list_directory_inputs(
    tool_input: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="list_directory"):
        tool_use_from_input("list-1", "list_directory", tool_input)


def test_catalog_validates_new_navigation_regex_and_patch_inputs() -> None:
    assert tool_input_from_use(
        tool_use_from_input(
            "lines-1",
            "read_file_lines",
            {"path": "src/app.py", "start_line": 20, "line_count": 10},
        )
    ) == {"path": "src/app.py", "start_line": 20, "line_count": 10}
    assert tool_input_from_use(
        tool_use_from_input("tree-1", "list_tree", {"path": ".", "max_depth": 4})
    ) == {"path": ".", "max_depth": 4}
    assert tool_input_from_use(
        tool_use_from_input(
            "patch-1",
            "patch_file",
            {
                "path": "src/app.py",
                "edits": [{"old_text": "before", "new_text": "after"}],
            },
        )
    ) == {
        "path": "src/app.py",
        "edits": [{"old_text": "before", "new_text": "after"}],
    }
    tool_use_from_input("stat-1", "stat_path", {"path": "."})
    tool_use_from_input("regex-1", "grep_regex", {"pattern": r"test_\d+", "include": "**/*.py"})
    assert tool_input_from_use(tool_use_from_input("status-1", "git_status", {})) == {}
    assert tool_input_from_use(
        tool_use_from_input("diff-1", "git_diff", {"scope": "staged", "path": "src/app.py"})
    ) == {"scope": "staged", "path": "src/app.py"}


@pytest.mark.parametrize(
    ("name", "tool_input"),
    [
        ("read_file_lines", {"path": "a", "start_line": True, "line_count": 1}),
        ("read_file_lines", {"path": "a", "start_line": 1, "line_count": 0}),
        ("list_tree", {"path": ".", "max_depth": 0}),
        ("grep_regex", {"pattern": "x", "include": 1}),
        ("patch_file", {"path": "a", "edits": []}),
        ("patch_file", {"path": "a", "edits": [{"old_text": "x"}]}),
        ("git_status", {"extra": "x"}),
        ("git_diff", {"scope": "both", "path": "."}),
        ("git_diff", {"scope": "staged"}),
    ],
)
def test_catalog_rejects_malformed_new_tool_inputs(
    name: str,
    tool_input: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=name):
        tool_use_from_input("call-1", name, tool_input)
