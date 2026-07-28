from __future__ import annotations

import pytest

from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    MAX_TOOL_EVENT_SUMMARY_CHARACTERS,
    ToolDispatchResult,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestStarted,
    safe_result_code,
    safe_tool_request_summary,
)
from leonervis_code.core.contracts import ToolArguments, ToolResult, ToolUse


def request(name: str, arguments: dict[str, object]) -> ToolUse:
    return ToolUse("tool-1", name, ToolArguments.from_mapping(arguments))


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("read_file", {"path": "src/app.py"}, "path='src/app.py'"),
        ("glob", {"pattern": "src/**/*.py"}, "pattern='src/**/*.py'"),
        ("grep", {"query": "TOP_SECRET", "include": "*.py"}, "query_bytes=10"),
        ("list_directory", {"path": "."}, "path='.'"),
        (
            "write_file",
            {"path": "note.txt", "content": "TOP_SECRET"},
            "content_bytes=10",
        ),
        (
            "edit_file",
            {"path": "note.txt", "old_text": "TOP_SECRET", "new_text": "NEW_SECRET"},
            "old_bytes=10 new_bytes=10",
        ),
        (
            "run_command",
            {
                "argv": ["/usr/bin/python3", "-c", "print('TOP_SECRET')"],
                "cwd": ".",
                "timeout_seconds": 30,
            },
            "command='python3' args=2 cwd='.' timeout=30s",
        ),
        ("mkdir", {"path": "build"}, "path='build'"),
        (
            "move_file",
            {"source": "old.txt", "destination": "new.txt"},
            "source='old.txt' destination='new.txt'",
        ),
        ("delete_file", {"path": "old.txt"}, "path='old.txt'"),
        ("delete_directory", {"path": "old"}, "path='old'"),
        ("list_directory", {"path": "src"}, "path='src'"),
        (
            "copy_file",
            {"source": "old.txt", "destination": "new.txt"},
            "source='old.txt' destination='new.txt'",
        ),
        (
            "read_file_lines",
            {"path": "app.py", "start_line": 4, "line_count": 8},
            "path='app.py' start_line=4 line_count=8",
        ),
        ("stat_path", {"path": "app.py"}, "path='app.py'"),
        ("list_tree", {"path": "src", "max_depth": 3}, "path='src' max_depth=3"),
        (
            "grep_regex",
            {"pattern": "TOP_SECRET", "include": "*.py"},
            "pattern_bytes=10",
        ),
        (
            "patch_file",
            {
                "path": "app.py",
                "edits": [
                    {"old_text": "TOP_SECRET", "new_text": "NEW_SECRET"},
                    {"old_text": "SECOND_SECRET", "new_text": "OTHER_SECRET"},
                ],
            },
            "path='app.py' edits=2",
        ),
        ("git_status", {}, "repository=."),
        (
            "git_diff",
            {"scope": "staged", "path": "src/app.py"},
            "scope='staged' path='src/app.py'",
        ),
    ],
)
def test_safe_summaries_cover_the_complete_tool_surface_without_content(
    name: str,
    arguments: dict[str, object],
    expected: str,
) -> None:
    summary = safe_tool_request_summary(request(name, arguments))

    assert expected in summary
    assert "TOP_SECRET" not in summary
    assert "NEW_SECRET" not in summary
    assert "SECOND_SECRET" not in summary
    assert "OTHER_SECRET" not in summary
    assert "print(" not in summary
    assert len(summary) <= MAX_TOOL_EVENT_SUMMARY_CHARACTERS


@pytest.mark.parametrize("path", ["/root/private.txt", r"C:\\Users\\private.txt"])
def test_absolute_paths_are_never_rendered(path: str) -> None:
    summary = safe_tool_request_summary(request("read_file", {"path": path}))

    assert summary == "path=<absolute>"
    assert path not in summary


def test_terminal_controls_are_escaped_and_unknown_arguments_are_redacted() -> None:
    summary = safe_tool_request_summary(request("read_file", {"path": "a\n\x1b[31m.txt"}))

    assert "\\n" in summary
    assert "\\x1b" in summary
    assert "\n" not in summary
    assert "\x1b" not in summary
    assert safe_tool_request_summary(request("future_tool", {"secret": "TOP_SECRET"})) == (
        "arguments=<redacted>"
    )
    assert safe_result_code("bad\n\x1b[31m") == r"bad\n\x1b[31m"


def test_event_models_reject_invalid_identity_status_and_controls() -> None:
    with pytest.raises(ValueError, match="call index"):
        ToolRequestStarted("read_file", 0, 6, "path='a.txt'")
    with pytest.raises(ValueError, match="control"):
        ToolRequestStarted("read_file", 1, 6, "bad\nsummary")
    with pytest.raises(ValueError, match="exceed"):
        ToolRequestLimited("read_file", 6, 6, "path='a.txt'")
    with pytest.raises(ValueError, match="status"):
        ToolRequestFinished("read_file", 1, 6, "succeeded")  # type: ignore[arg-type]


def test_assistant_tool_text_event_preserves_exact_bounded_text() -> None:
    assert AssistantToolTextReceived("I will inspect.\n").text == "I will inspect.\n"
    with pytest.raises(ValueError, match="non-empty"):
        AssistantToolTextReceived("")
    with pytest.raises(ValueError, match="NUL"):
        AssistantToolTextReceived("bad\x00text")
    with pytest.raises(ValueError, match="valid UTF-8"):
        AssistantToolTextReceived("\ud800")
    with pytest.raises(ValueError, match="supported size"):
        AssistantToolTextReceived("a" * (32 * 1024 + 1))


def test_dispatch_result_requires_status_to_match_model_visible_error_flag() -> None:
    ToolDispatchResult(ToolResult("tool-1", "ok"), ToolEventStatus.SUCCEEDED, "ok")
    with pytest.raises(ValueError, match="successful"):
        ToolDispatchResult(
            ToolResult("tool-1", "failed", is_error=True),
            ToolEventStatus.SUCCEEDED,
        )
    with pytest.raises(ValueError, match="non-successful"):
        ToolDispatchResult(ToolResult("tool-1", "ok"), ToolEventStatus.FAILED)
