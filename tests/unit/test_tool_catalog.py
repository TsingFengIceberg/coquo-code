from __future__ import annotations

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.catalog import (
    TOOL_CATALOG,
    model_tool_definitions,
    select_tool_definitions,
    tool_input_for_provider_history,
    tool_input_from_use,
    tool_use_from_input,
    tool_use_from_provider_input,
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
        "git_log",
        "git_show",
        "web_search",
        "web_fetch",
        "compare_files",
        "git_blame",
        "git_refs",
        "json_query",
        "checksum_file",
        "archive_list",
        "move_directory",
        "download_file",
        "tool_search",
        "tool_promote",
        "skill_search",
        "skill_load",
        "skill_read_resource",
        "task_propose_plan",
        "task_report_reflection",
        "task_report_blocker",
        "task_propose_completion",
        "task_propose_start",
        "task_accept_admission",
        "task_accept_plan",
        "task_confirm_completion",
        "skill_propose_create",
        "skill_accept_create",
        "child_spawn",
        "child_status",
        "child_wait",
        "child_cancel",
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


def test_catalog_selects_exact_tools_in_global_canonical_order() -> None:
    selected = select_tool_definitions(("git_show", "read_file", "grep"))

    assert tuple(definition.name for definition in selected) == (
        "read_file",
        "grep",
        "git_show",
    )


def test_provider_input_defers_bounded_ordinary_tool_validation_only() -> None:
    deferred = tool_use_from_provider_input(
        "write-1",
        "write_file",
        {"path": "large.txt", "content": "x" * 4097},
    )

    assert deferred.arguments.as_mapping()["content"] == "x" * 4097
    assert tool_input_for_provider_history(deferred)["content"] == "x" * 4097
    with pytest.raises(ValueError, match="exceeds the supported size"):
        tool_input_from_use(deferred)
    with pytest.raises(ValueError):
        tool_use_from_provider_input("task-1", "task_accept_plan", {"task_id": "bad"})
    with pytest.raises(ValueError):
        tool_use_from_provider_input("unknown-1", "unknown", {})


def test_discovery_inputs_are_bounded_and_remote_mcp_arguments_remain_generic() -> None:
    search = tool_use_from_input(
        "search-1",
        "tool_search",
        {"query": "database lookup", "max_results": 4},
    )
    assert tool_input_from_use(search) == {"query": "database lookup", "max_results": 4}

    promote = tool_use_from_input(
        "promote-1",
        "tool_promote",
        {"names": ["mcp_fixture_lookup_1234567890"]},
    )
    assert tool_input_from_use(promote) == {"names": ["mcp_fixture_lookup_1234567890"]}

    remote = tool_use_from_provider_input(
        "remote-1",
        "mcp_fixture_lookup_1234567890",
        {"nested": {"value": 1}},
    )
    assert tool_input_for_provider_history(remote) == {"nested": {"value": 1}}

    with pytest.raises(ValueError, match="max_results"):
        tool_use_from_input("bad-search", "tool_search", {"query": "x", "max_results": 9})
    with pytest.raises(ValueError, match="only MCP"):
        tool_use_from_input("bad-promote", "tool_promote", {"names": ["read_file"]})

    skill_search = tool_use_from_input(
        "skill-search-1",
        "skill_search",
        {"query": "python release", "max_results": 3},
    )
    skill_load = tool_use_from_input(
        "skill-load-1",
        "skill_load",
        {"name": "python-release", "fingerprint": "skill-v1-" + "a" * 64},
    )
    assert tool_input_from_use(skill_search)["max_results"] == 3
    assert tool_input_from_use(skill_load)["name"] == "python-release"
    resource = tool_use_from_input(
        "skill-resource-1",
        "skill_read_resource",
        {
            "name": "python-release",
            "skill_fingerprint": "skill-v1-" + "a" * 64,
            "path": "references/checklist.md",
            "resource_fingerprint": "resource-v1-" + "b" * 64,
        },
    )
    assert tool_input_from_use(resource)["path"] == "references/checklist.md"
    with pytest.raises(ValueError, match="skill_search max_results"):
        tool_use_from_input(
            "bad-skill-search",
            "skill_search",
            {"query": "x", "max_results": 9},
        )
    with pytest.raises(ValueError, match="fingerprint"):
        tool_use_from_input(
            "bad-skill-load",
            "skill_load",
            {"name": "python-release", "fingerprint": "skill-v1-bad"},
        )
    with pytest.raises(ValueError, match="resource fingerprint"):
        tool_use_from_input(
            "bad-skill-resource",
            "skill_read_resource",
            {
                "name": "python-release",
                "skill_fingerprint": "skill-v1-" + "a" * 64,
                "path": "references/checklist.md",
                "resource_fingerprint": "resource-v1-bad",
            },
        )


def test_catalog_validates_closed_task_coordination_inputs() -> None:
    plan = tool_use_from_input(
        "plan-1",
        "task_propose_plan",
        {"steps": ["Inspect", "Verify"]},
    )
    reflection = tool_use_from_input(
        "reflection-1",
        "task_report_reflection",
        {
            "recommendation": "correction",
            "summary": "One bounded correction is needed.",
            "next_objective": "Apply the correction.",
        },
    )
    blocker = tool_use_from_input(
        "blocker-1",
        "task_report_blocker",
        {"category": "human-evidence", "summary": "User evidence is required."},
    )
    completion = tool_use_from_input("completion-1", "task_propose_completion", {})
    admission = tool_use_from_input(
        "admission-1",
        "task_propose_start",
        {
            "objective": "Implement the feature",
            "reason": "Several bounded stages are needed.",
            "acceptance_criteria": ["Tests pass"],
        },
    )
    accept_admission = tool_use_from_input(
        "accept-admission-1",
        "task_accept_admission",
        {"admission_id": "tap-v1-" + "a" * 64},
    )
    accept_plan = tool_use_from_input(
        "accept-plan-1",
        "task_accept_plan",
        {"task_id": "12345678-1234-4234-9234-123456789abc"},
    )
    confirm_completion = tool_use_from_input(
        "confirm-completion-1",
        "task_confirm_completion",
        {"task_id": "12345678-1234-4234-9234-123456789abc"},
    )

    assert tool_input_from_use(plan) == {"steps": ["Inspect", "Verify"]}
    assert tool_input_from_use(reflection)["recommendation"] == "correction"
    assert tool_input_from_use(blocker)["category"] == "human-evidence"
    assert tool_input_from_use(completion) == {}
    assert tool_input_from_use(admission)["acceptance_criteria"] == ["Tests pass"]
    assert tool_input_from_use(accept_admission)["admission_id"].startswith("tap-v1-")
    assert tool_input_from_use(accept_plan) == {"task_id": "12345678-1234-4234-9234-123456789abc"}
    assert tool_input_from_use(confirm_completion) == tool_input_from_use(accept_plan)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("task_propose_plan", {"steps": []}),
        (
            "task_report_reflection",
            {
                "recommendation": "continue",
                "summary": "Continue.",
                "next_objective": None,
            },
        ),
        ("task_report_blocker", {"category": "unknown", "summary": "Blocked."}),
        ("task_propose_completion", {"claim": True}),
        ("task_accept_admission", {"admission_id": "bad"}),
        ("task_accept_plan", {"task_id": "bad"}),
        ("task_confirm_completion", {"task_id": "bad"}),
    ],
)
def test_catalog_rejects_invalid_task_coordination_inputs(name, arguments) -> None:
    with pytest.raises(ValueError):
        tool_use_from_input("task-1", name, arguments)
    assert tuple(item["name"] for item in model_tool_definitions(("git_show", "grep"))) == (
        "grep",
        "git_show",
    )


@pytest.mark.parametrize(
    "names",
    [(), ("read_file", "read_file"), ("unknown",), ["read_file"]],
)
def test_catalog_rejects_invalid_exact_tool_sets(names) -> None:
    with pytest.raises(ValueError, match="enabled tool"):
        select_tool_definitions(names)


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


def test_catalog_validates_closed_web_search_input() -> None:
    call = tool_use_from_input(
        "search-1",
        "web_search",
        {"query": "Python 3.14 documentation", "max_results": 5},
    )

    assert tool_input_from_use(call) == {
        "query": "Python 3.14 documentation",
        "max_results": 5,
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("web_fetch", {"url": "https://example.com/docs", "format": "markdown"}),
        ("compare_files", {"left": "a.txt", "right": "b.txt"}),
        ("git_blame", {"path": "src/app.py", "start_line": 1, "line_count": 20}),
        ("git_refs", {}),
        ("json_query", {"path": "data.json", "pointer": ""}),
        ("checksum_file", {"path": "artifact.bin"}),
        ("archive_list", {"path": "bundle.zip"}),
        ("move_directory", {"source": "old", "destination": "new"}),
        (
            "download_file",
            {"url": "https://example.com/file.bin", "path": "file.bin"},
        ),
    ],
)
def test_catalog_validates_additional_bounded_tool_inputs(
    name: str, arguments: dict[str, object]
) -> None:
    call = tool_use_from_input(f"{name}-1", name, arguments)
    assert tool_input_from_use(call) == arguments


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("web_fetch", {"url": "https://example.com", "format": "html"}),
        ("compare_files", {"left": "a.txt"}),
        ("git_blame", {"path": "a.py", "start_line": 0, "line_count": 1}),
        ("git_refs", {"all": True}),
        ("json_query", {"path": "data.json"}),
        ("checksum_file", {"path": 1}),
        ("archive_list", {"path": ""}),
        ("move_directory", {"source": "old", "destination": ""}),
        ("download_file", {"url": "https://example.com/file.bin"}),
    ],
)
def test_catalog_rejects_malformed_additional_tool_inputs(
    name: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        tool_use_from_input(f"{name}-1", name, arguments)


@pytest.mark.parametrize(
    "tool_input",
    [
        {"query": "", "max_results": 5},
        {"query": "Python", "max_results": 0},
        {"query": "Python", "max_results": 11},
        {"query": "Python"},
        {"query": "Python", "max_results": 5, "url": "https://example.com"},
    ],
)
def test_catalog_rejects_malformed_web_search_inputs(tool_input: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="web_search"):
        tool_use_from_input("search-1", "web_search", tool_input)


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
    assert tool_input_from_use(
        tool_use_from_input("log-1", "git_log", {"limit": 12, "path": "."})
    ) == {"limit": 12, "path": "."}
    commit_id = "a" * 40
    assert tool_input_from_use(
        tool_use_from_input("show-1", "git_show", {"commit_id": commit_id, "path": "src/app.py"})
    ) == {"commit_id": commit_id, "path": "src/app.py"}


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
        ("git_log", {"limit": 0, "path": "."}),
        ("git_log", {"limit": True, "path": "."}),
        ("git_show", {"commit_id": "abc123", "path": "."}),
        ("git_show", {"commit_id": "A" * 40, "path": "."}),
    ],
)
def test_catalog_rejects_malformed_new_tool_inputs(
    name: str,
    tool_input: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=name):
        tool_use_from_input("call-1", name, tool_input)
