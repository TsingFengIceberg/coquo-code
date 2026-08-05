from __future__ import annotations

import pytest

from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    MAX_TOOL_EVENT_ARGV_LINE_BYTES,
    MAX_TOOL_EVENT_DETAIL_BYTES,
    MAX_TOOL_EVENT_SUMMARY_CHARACTERS,
    MAX_TOOL_RESULT_DETAIL_LINES,
    McpNotificationActivityReceived,
    ToolDispatchResult,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestStarted,
    ToolResultDetails,
    safe_result_code,
    safe_tool_request_details,
    safe_tool_request_summary,
)
from leonervis_code.core.contracts import ToolArguments, ToolResult, ToolUse
from leonervis_code.mcp.client import McpNotificationKind


def request(name: str, arguments: dict[str, object]) -> ToolUse:
    return ToolUse("tool-1", name, ToolArguments.from_mapping(arguments))


def test_mcp_notification_activity_requires_a_closed_content_free_kind() -> None:
    assert (
        McpNotificationActivityReceived(McpNotificationKind.PROGRESS).kind
        is McpNotificationKind.PROGRESS
    )
    with pytest.raises(ValueError, match="kind"):
        McpNotificationActivityReceived("progress")  # type: ignore[arg-type]


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
        ("git_log", {"limit": 10, "path": "src"}, "limit=10 path='src'"),
        (
            "git_show",
            {"commit_id": "a" * 40, "path": "src/app.py"},
            f"commit='{'a' * 40}' path='src/app.py'",
        ),
        (
            "web_search",
            {"query": "TOP_SECRET current docs", "max_results": 5},
            "query_bytes=23 max_results=5",
        ),
        (
            "web_fetch",
            {"url": "https://example.com/?q=TOP_SECRET", "format": "markdown"},
            "url=<redacted> format='markdown'",
        ),
        (
            "compare_files",
            {"left": "before.txt", "right": "after.txt"},
            "left='before.txt' right='after.txt'",
        ),
        (
            "git_blame",
            {"path": "src/app.py", "start_line": 3, "line_count": 4},
            "path='src/app.py' start_line=3 line_count=4",
        ),
        ("git_refs", {}, "repository=."),
        (
            "json_query",
            {"path": "data.json", "pointer": "/TOP_SECRET"},
            "path='data.json' pointer_bytes=11",
        ),
        ("checksum_file", {"path": "artifact.bin"}, "path='artifact.bin'"),
        ("archive_list", {"path": "bundle.zip"}, "path='bundle.zip'"),
        (
            "move_directory",
            {"source": "old", "destination": "new"},
            "source='old' destination='new'",
        ),
        (
            "download_file",
            {"url": "https://example.com/TOP_SECRET", "path": "file.bin"},
            "url=<redacted> path='file.bin'",
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


def test_full_command_details_preserve_structured_argv_and_identify_shell_source() -> None:
    direct = safe_tool_request_details(
        request(
            "run_command",
            {
                "argv": ["uv", "run", "pytest", "tests/unit"],
                "cwd": ".",
                "timeout_seconds": 300,
            },
        )
    )
    shell = safe_tool_request_details(
        request(
            "run_command",
            {
                "argv": ["/bin/bash", "-lc", "printf 'ok\\n' && uv run pytest"],
                "cwd": "src",
                "timeout_seconds": 30,
            },
        )
    )

    assert direct == (
        'argv: ["uv","run","pytest","tests/unit"]',
        "cwd: '.'",
        "timeout_seconds: 300",
        "execution: direct argv; Host shell parsing disabled",
    )
    assert shell[0] == 'argv: ["/bin/bash","-lc","printf \'ok\\\\n\' && uv run pytest"]'
    assert shell[1:3] == ("cwd: 'src'", "timeout_seconds: 30")
    assert shell[3] == "execution: shell interpreter 'bash'; shell source is argv[2]"

    long_option = safe_tool_request_details(
        request(
            "run_command",
            {
                "argv": ["bash", "--norc", "script.sh"],
                "cwd": ".",
                "timeout_seconds": 30,
            },
        )
    )
    assert long_option[3] == "execution: direct argv; Host shell parsing disabled"


def test_full_command_details_escape_controls_and_bound_long_argv() -> None:
    details = safe_tool_request_details(
        request(
            "run_command",
            {
                "argv": ["bash", "-lc", "\u202e\x1b" + "x" * 8000],
                "cwd": ".",
                "timeout_seconds": 30,
            },
        )
    )

    assert "\\u202e" in details[0]
    assert "\\u001b" in details[0]
    assert "\u202e" not in details[0]
    assert "\x1b" not in details[0]
    assert "<truncated; rendered_bytes=" in details[0]
    assert len(details[0].encode("utf-8")) <= MAX_TOOL_EVENT_ARGV_LINE_BYTES
    assert sum(len(line.encode("utf-8")) for line in details) <= MAX_TOOL_EVENT_DETAIL_BYTES
    assert (
        safe_tool_request_details(
            request("write_file", {"path": "note.txt", "content": "TOP_SECRET"})
        )
        == ()
    )


def test_event_models_reject_invalid_identity_status_and_controls() -> None:
    with pytest.raises(ValueError, match="call index"):
        ToolRequestStarted("read_file", 0, 6, "path='a.txt'")
    with pytest.raises(ValueError, match="control"):
        ToolRequestStarted("read_file", 1, 6, "bad\nsummary")
    with pytest.raises(ValueError, match="control"):
        ToolRequestStarted("run_command", 1, 6, "command='bash'", ("argv: bad\u202e",))
    with pytest.raises(ValueError, match="exceed"):
        ToolRequestLimited("read_file", 6, 6, "path='a.txt'")
    with pytest.raises(ValueError, match="status"):
        ToolRequestFinished("read_file", 1, 6, "succeeded")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not be empty"):
        ToolResultDetails("", ())
    with pytest.raises(ValueError, match="control"):
        ToolResultDetails("stdout=0B\u202e", ("stdout: captured=0",))
    with pytest.raises(ValueError, match="control"):
        ToolResultDetails("stdout=0B", ("stdout: secret\nvalue",))
    with pytest.raises(ValueError, match="details"):
        ToolResultDetails(
            "stdout=0B",
            tuple(f"line-{index}" for index in range(MAX_TOOL_RESULT_DETAIL_LINES + 1)),
        )


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
    details = ToolResultDetails(
        "exit=0 duration=1ms stdout=0B stderr=0B",
        (
            "status: exited",
            "exit_code: 0",
            "duration_ms: 1",
            "stdout: captured=0 total=0 truncated=false",
            "stderr: captured=0 total=0 truncated=false",
            "cleanup_complete: true",
        ),
    )
    ToolDispatchResult(
        ToolResult("tool-1", "ok"),
        ToolEventStatus.SUCCEEDED,
        "ok",
        details,
    )
    with pytest.raises(ValueError, match="successful"):
        ToolDispatchResult(
            ToolResult("tool-1", "failed", is_error=True),
            ToolEventStatus.SUCCEEDED,
        )
    with pytest.raises(ValueError, match="non-successful"):
        ToolDispatchResult(ToolResult("tool-1", "ok"), ToolEventStatus.FAILED)
