from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.git_show import GitShowTool


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
    _git(workspace, "config", "user.name", "Coquo Tests")
    (workspace / "first.txt").write_text("before\n", encoding="utf-8")
    (workspace / "other.txt").write_text("other\n", encoding="utf-8")
    _git(workspace, "add", "first.txt", "other.txt")
    _git(workspace, "commit", "-qm", "initial")
    (workspace / "first.txt").write_text("after\n", encoding="utf-8")
    (workspace / "other.txt").write_text("changed\n", encoding="utf-8")
    _git(workspace, "add", "first.txt", "other.txt")
    _git(workspace, "commit", "-qm", "change both")
    return workspace


def _request(commit_id: str, path: str = ".") -> ToolUse:
    return ToolUse(
        "show-1",
        "git_show",
        ToolArguments.from_mapping({"commit_id": commit_id, "path": path}),
    )


def test_git_show_returns_reachable_metadata_message_and_literal_path_patch(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    commit_id = _git(workspace, "rev-parse", "HEAD")

    result = GitShowTool(workspace).execute(_request(commit_id, "first.txt"))
    header, patch = result.content.split("\n", 1)
    metadata = json.loads(header)

    assert not result.is_error
    assert metadata["commit_id"] == commit_id
    assert metadata["message"] == "change both\n"
    assert metadata["message_truncated"] is False
    assert "first.txt" in patch
    assert "other.txt" not in patch
    assert "-before" in patch and "+after" in patch


def test_git_show_rejects_abbreviated_and_unreachable_commit_ids(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    current_branch = _git(workspace, "branch", "--show-current")
    _git(workspace, "checkout", "-qb", "side", "HEAD~1")
    (workspace / "side.txt").write_text("side\n", encoding="utf-8")
    _git(workspace, "add", "side.txt")
    _git(workspace, "commit", "-qm", "side commit")
    side_commit = _git(workspace, "rev-parse", "HEAD")
    _git(workspace, "checkout", "-q", current_branch)

    abbreviated = GitShowTool(workspace).execute(_request(side_commit[:12]))
    unreachable = GitShowTool(workspace).execute(_request(side_commit))

    assert abbreviated.is_error
    assert "complete lowercase" in abbreviated.content
    assert unreachable.is_error
    assert unreachable.content == "git_show commit_id is not reachable from current HEAD"


def test_git_show_truncates_large_message_and_patch_at_utf8_boundaries(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    (workspace / "large.txt").write_text("before\n", encoding="utf-8")
    _git(workspace, "add", "large.txt")
    _git(workspace, "commit", "-qm", "base large")
    (workspace / "large.txt").write_text("界" * 40_000 + "\n", encoding="utf-8")
    _git(workspace, "add", "large.txt")
    _git(workspace, "commit", "-qm", "界" * 4000)
    commit_id = _git(workspace, "rev-parse", "HEAD")

    result = GitShowTool(workspace).execute(_request(commit_id))
    header, patch = result.content.split("\n", 1)
    metadata = json.loads(header)

    assert not result.is_error
    assert result.truncated
    assert metadata["message_truncated"] is True
    assert patch.endswith("\n[truncated]\n")
    result.content.encode("utf-8")
    assert len(result.content.encode("utf-8")) <= 64 * 1024


def test_git_show_disables_external_diff_and_textconv(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    marker = workspace / "external-ran"
    helper = workspace / "external.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    helper.chmod(0o755)
    (workspace / ".gitattributes").write_text("first.txt diff=unsafe\n", encoding="utf-8")
    _git(workspace, "add", ".gitattributes")
    _git(workspace, "config", "diff.unsafe.textconv", str(helper))
    _git(workspace, "config", "diff.external", str(helper))
    _git(workspace, "commit", "-qm", "attributes")
    commit_id = _git(workspace, "rev-parse", "HEAD")

    result = GitShowTool(workspace).execute(_request(commit_id))

    assert not result.is_error
    assert not marker.exists()


@pytest.mark.parametrize("path", ["", "../outside", "/absolute", "src\\file.py"])
def test_git_show_rejects_noncanonical_path(tmp_path: Path, path: str) -> None:
    workspace = _repository(tmp_path)
    commit_id = _git(workspace, "rev-parse", "HEAD")

    result = GitShowTool(workspace).execute(_request(commit_id, path))

    assert result.is_error
    assert result.content.startswith("git_show path ")
