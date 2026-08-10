from __future__ import annotations

import json
from pathlib import Path

import pytest

import coquo.tools.grep_regex as grep_regex_module
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.grep_regex import (
    MAX_GREP_REGEX_FILE_BYTES,
    MAX_GREP_REGEX_MATCHES,
    GrepRegexTool,
)


def request(pattern: object, include: object, **extra: object) -> ToolUse:
    return ToolUse(
        "regex-1",
        "grep_regex",
        ToolArguments.from_mapping({"pattern": pattern, "include": include, **extra}),
    )


def records(content: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in content.splitlines()]


@pytest.fixture
def non_timeout_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grep_regex_module, "GREP_REGEX_TIMEOUT_SECONDS", 5.0)


def test_searches_case_sensitive_python_regex_per_logical_line(
    tmp_path: Path, non_timeout_worker: None
) -> None:
    (tmp_path / "b.py").write_text("task_20 = True\nTASK_30 = False", encoding="utf-8")
    (tmp_path / "a.py").write_bytes(b"task_10 = False\r\nother\rfinal_99")

    result = GrepRegexTool(tmp_path).execute(request(r"(?:task|final)_\d{2}\b", "*.py"))

    assert not result.is_error
    assert records(result.content) == [
        {"path": "a.py", "line": 1, "text": "task_10 = False"},
        {"path": "a.py", "line": 3, "text": "final_99"},
        {"path": "b.py", "line": 1, "text": "task_20 = True"},
    ]


def test_empty_match_set_is_success(tmp_path: Path, non_timeout_worker: None) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")

    result = GrepRegexTool(tmp_path).execute(request(r"^beta$", "*.txt"))

    assert not result.is_error
    assert result.content == ""
    assert not result.truncated


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("", "must not be empty"),
        ("a\n", "one line"),
        ("(", "pattern is invalid"),
    ],
)
def test_rejects_invalid_patterns(
    tmp_path: Path, pattern: str, message: str, non_timeout_worker: None
) -> None:
    result = GrepRegexTool(tmp_path).execute(request(pattern, "*.txt"))

    assert result.is_error
    assert message in result.content


@pytest.mark.parametrize(
    "call",
    [
        ToolUse(
            "regex-1",
            "grep_regex",
            ToolArguments.from_mapping({"pattern": "x"}),
        ),
        request(1, "*.txt"),
        request("x", 1),
        request("x", "*.txt", extra=1),
    ],
)
def test_rejects_malformed_input(tmp_path: Path, call: ToolUse) -> None:
    result = GrepRegexTool(tmp_path).execute(call)

    assert result.is_error
    assert result.content == "grep_regex input is malformed"


def test_rejects_symlink_binary_and_large_selected_files(
    tmp_path: Path, non_timeout_worker: None
) -> None:
    outside = tmp_path.parent / "outside-regex.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    tool = GrepRegexTool(tmp_path)

    assert tool.execute(request("secret", "*.txt")).content == ""
    (tmp_path / "binary.txt").write_bytes(b"\xff")
    assert "not valid UTF-8" in tool.execute(request("x", "*.txt")).content
    (tmp_path / "binary.txt").unlink()
    (tmp_path / "large.txt").write_bytes(b"x" * (MAX_GREP_REGEX_FILE_BYTES + 1))
    assert "per-file limit" in tool.execute(request("x", "*.txt")).content


def test_truncates_after_bounded_complete_matches(tmp_path: Path, non_timeout_worker: None) -> None:
    (tmp_path / "many.txt").write_text(
        "\n".join(["match"] * (MAX_GREP_REGEX_MATCHES + 10)),
        encoding="utf-8",
    )

    result = GrepRegexTool(tmp_path).execute(request("match", "*.txt"))

    assert result.truncated
    assert records(result.content)[-1] == {"truncated": True}


def test_catastrophic_backtracking_times_out_without_blocking_host(tmp_path: Path) -> None:
    (tmp_path / "evil.txt").write_text(("a" * 50_000) + "!", encoding="utf-8")

    result = GrepRegexTool(tmp_path).execute(request(r"^(a+)+$", "evil.txt"))

    assert result.is_error
    assert result.content == "grep_regex timed out; use a simpler pattern or narrower include"
