from pathlib import Path
import subprocess

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.git_blame import GIT_BLAME_TOOL_NAME, GitBlameTool, _parse_blame
from coquo.tools.git_repository import GitObservationError


def git(workspace: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=workspace, check=True, capture_output=True)


def request() -> ToolUse:
    return ToolUse(
        "toolu_blame",
        GIT_BLAME_TOOL_NAME,
        ToolArguments.from_mapping({"path": "notes.txt", "start_line": 2, "line_count": 1}),
    )


def test_git_blame_returns_current_head_line_attribution(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    git(tmp_path, "add", "notes.txt")
    git(tmp_path, "commit", "-qm", "initial")

    result = GitBlameTool(tmp_path).execute(request())

    assert not result.is_error
    assert '"author":"Test User"' in result.content
    assert '"line":2' in result.content
    assert '"text":"two"' in result.content


def test_git_blame_rejects_non_ascii_author_timezone_as_structured_error() -> None:
    data = (
        b"0" * 40
        + b" 1 1 1\n"
        + b"author Test User\n"
        + b"author-time 0\n"
        + b"author-tz +0\xff00\n"
        + b"\tline\n"
    )

    with pytest.raises(GitObservationError, match="non-ASCII author timezone"):
        _parse_blame(data)
