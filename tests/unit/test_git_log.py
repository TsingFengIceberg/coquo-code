from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.git_log import GitLogTool, MAX_GIT_LOG_LIMIT


def _git(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "tests@example.invalid")
    _git(workspace, "config", "user.name", "Leonervis Tests")
    (workspace / "first.txt").write_text("one\n", encoding="utf-8")
    _git(workspace, "add", "first.txt")
    _git(workspace, "commit", "-qm", "first commit")
    (workspace / "second.txt").write_text("two\n", encoding="utf-8")
    _git(workspace, "add", "second.txt")
    _git(workspace, "commit", "-qm", "second commit")
    return workspace


def _request(limit: int = 10, path: str = ".") -> ToolUse:
    return ToolUse(
        "log-1",
        "git_log",
        ToolArguments.from_mapping({"limit": limit, "path": path}),
    )


def test_git_log_returns_recent_head_history_as_deterministic_jsonl(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)

    result = GitLogTool(workspace).execute(_request())
    records = [json.loads(line) for line in result.content.splitlines()]

    assert not result.is_error
    assert [record["subject"] for record in records] == ["second commit", "first commit"]
    assert all(len(record["commit_id"]) == 40 for record in records)
    assert records[0]["parent_ids"] == [records[1]["commit_id"]]
    assert records[1]["parent_ids"] == []
    assert all(record["subject_truncated"] is False for record in records)


def test_git_log_limit_and_literal_path_select_current_head_ancestry(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)

    limited = GitLogTool(workspace).execute(_request(1, "."))
    first_only = GitLogTool(workspace).execute(_request(10, "first.txt"))

    assert len(limited.content.splitlines()) == 1
    assert json.loads(limited.content)["subject"] == "second commit"
    assert [json.loads(line)["subject"] for line in first_only.content.splitlines()] == [
        "first commit"
    ]


def test_git_log_root_scope_includes_empty_commits(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    _git(workspace, "commit", "--allow-empty", "-qm", "empty checkpoint")

    result = GitLogTool(workspace).execute(_request(1, "."))

    assert json.loads(result.content)["subject"] == "empty checkpoint"


def test_git_log_marks_one_oversized_subject_without_hiding_other_fields(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    (workspace / "third.txt").write_text("three\n", encoding="utf-8")
    _git(workspace, "add", "third.txt")
    _git(workspace, "commit", "-qm", "界" * 600)

    result = GitLogTool(workspace).execute(_request(1))
    record = json.loads(result.content)

    assert not result.is_error
    assert result.truncated
    assert record["subject_truncated"] is True
    assert len(record["subject"].encode("utf-8")) <= 1024
    record["subject"].encode("utf-8")


@pytest.mark.parametrize(
    "limit,path",
    [
        (0, "."),
        (MAX_GIT_LOG_LIMIT + 1, "."),
        (True, "."),
        (1, "../outside"),
        (1, "/absolute"),
        (1, "src\\file.py"),
    ],
)
def test_git_log_rejects_invalid_limit_and_path(tmp_path: Path, limit: int, path: str) -> None:
    workspace = _repository(tmp_path)

    result = GitLogTool(workspace).execute(_request(limit, path))

    assert result.is_error
    assert result.content.startswith("git_log ")
