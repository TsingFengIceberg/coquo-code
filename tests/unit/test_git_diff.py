from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.git_diff import GitDiffTool


def _git(workspace: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _repository(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "tests@example.invalid")
    _git(workspace, "config", "user.name", "Leonervis Tests")
    (workspace / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-qm", "initial")
    return workspace


def _request(scope: str = "unstaged", path: str = ".") -> ToolUse:
    return ToolUse(
        "diff-1",
        "git_diff",
        ToolArguments.from_mapping({"scope": scope, "path": path}),
    )


def test_git_diff_separates_staged_and_unstaged_tracked_changes(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    (workspace / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    (workspace / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (workspace / "untracked.txt").write_text("UNTRACKED_SECRET\n", encoding="utf-8")
    tool = GitDiffTool(workspace)

    staged = tool.execute(_request("staged"))
    unstaged = tool.execute(_request("unstaged"))

    assert "-before" in staged.content and "+staged" in staged.content
    assert "unstaged" not in staged.content
    assert "-staged" in unstaged.content and "+unstaged" in unstaged.content
    assert "UNTRACKED_SECRET" not in staged.content + unstaged.content


def test_git_diff_literal_path_limits_patch(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    (workspace / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (workspace / "other.txt").write_text("one\n", encoding="utf-8")
    _git(workspace, "add", "other.txt")
    _git(workspace, "commit", "-qm", "other")
    (workspace / "other.txt").write_text("two\n", encoding="utf-8")

    result = GitDiffTool(workspace).execute(_request(path="tracked.txt"))

    assert "tracked.txt" in result.content
    assert "other.txt" not in result.content


@pytest.mark.parametrize(
    "scope,path",
    [
        ("both", "."),
        ("unstaged", ""),
        ("unstaged", "../outside"),
        ("staged", "/absolute"),
        ("staged", "src\\file.py"),
        ("staged", "src//file.py"),
    ],
)
def test_git_diff_rejects_noncanonical_scope_and_path(
    tmp_path: Path, scope: str, path: str
) -> None:
    workspace = _repository(tmp_path)

    result = GitDiffTool(workspace).execute(_request(scope, path))

    assert result.is_error
    assert result.content.startswith("git_diff ")


def test_git_diff_truncates_with_explicit_marker_at_utf8_boundary(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    content = "before\n" + "界" * 40_000 + "\n"
    (workspace / "tracked.txt").write_text(content, encoding="utf-8")

    result = GitDiffTool(workspace).execute(_request())

    assert not result.is_error
    assert result.truncated
    assert result.content.endswith("\n[truncated]\n")
    result.content.encode("utf-8")


def test_git_diff_disables_external_diff_and_textconv(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    marker = workspace / "external-ran"
    helper = workspace / "external.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    helper.chmod(0o755)
    (workspace / ".gitattributes").write_text("tracked.txt diff=unsafe\n", encoding="utf-8")
    _git(workspace, "add", ".gitattributes")
    _git(workspace, "commit", "-qm", "attributes")
    _git(workspace, "config", "diff.unsafe.textconv", str(helper))
    _git(workspace, "config", "diff.external", str(helper))
    (workspace / "tracked.txt").write_text("changed\n", encoding="utf-8")

    result = GitDiffTool(workspace).execute(_request())

    assert not result.is_error
    assert not marker.exists()


def test_git_diff_rejects_external_clean_filter_without_execution(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    marker = workspace / "filter-ran"
    helper = workspace / "filter.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    helper.chmod(0o755)
    (workspace / ".gitattributes").write_text("tracked.txt filter=unsafe\n", encoding="utf-8")
    _git(workspace, "add", ".gitattributes")
    _git(workspace, "commit", "-qm", "attributes")
    _git(workspace, "config", "filter.unsafe.clean", str(helper))
    (workspace / "tracked.txt").write_text("changed\n", encoding="utf-8")

    result = GitDiffTool(workspace).execute(_request())

    assert result.is_error
    assert result.content == "external Git filters are not supported"
    assert not marker.exists()


def test_git_diff_supports_staged_changes_before_first_commit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    (workspace / "first.txt").write_text("first\n", encoding="utf-8")
    _git(workspace, "add", "first.txt")

    result = GitDiffTool(workspace).execute(_request("staged"))

    assert not result.is_error
    assert "new file mode" in result.content
    assert "+first" in result.content
