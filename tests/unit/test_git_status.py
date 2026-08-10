from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.git_repository import GitObservationError, GitRepository
from coquo.tools.git_status import GitStatusTool


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
    _git(workspace, "config", "user.name", "Coquo Tests")
    (workspace / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-qm", "initial")
    return workspace


def _request() -> ToolUse:
    return ToolUse("status-1", "git_status", ToolArguments.from_mapping({}))


def test_git_status_returns_clean_empty_jsonl(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)

    result = GitStatusTool(workspace).execute(_request())

    assert not result.is_error
    assert result.content == ""
    assert not result.truncated


def test_git_status_returns_sorted_structured_staged_unstaged_and_untracked_entries(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path)
    (workspace / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    (workspace / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (workspace / "z-untracked.txt").write_text("secret\n", encoding="utf-8")
    (workspace / "a-untracked.txt").write_text("hidden payload\n", encoding="utf-8")

    result = GitStatusTool(workspace).execute(_request())
    records = [json.loads(line) for line in result.content.splitlines()]

    assert records == [
        {"path": "a-untracked.txt", "index": "untracked", "worktree": "untracked"},
        {"path": "tracked.txt", "index": "modified", "worktree": "modified"},
        {"path": "z-untracked.txt", "index": "untracked", "worktree": "untracked"},
    ]
    assert "secret" not in result.content
    assert "hidden payload" not in result.content


def test_git_status_parses_rename_origin_without_quoted_paths(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    _git(workspace, "mv", "tracked.txt", "renamed name.txt")

    result = GitStatusTool(workspace).execute(_request())

    assert json.loads(result.content) == {
        "path": "renamed name.txt",
        "index": "renamed",
        "worktree": "clean",
        "original_path": "tracked.txt",
    }


def test_git_status_truncates_with_explicit_jsonl_sentinel(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    for index in range(205):
        (workspace / f"untracked-{index:03}.txt").write_text("x\n", encoding="utf-8")

    result = GitStatusTool(workspace).execute(_request())

    assert not result.is_error
    assert result.truncated
    assert len(result.content.splitlines()) == 201
    assert result.content.endswith('{"truncated":true}\n')


def test_git_status_rejects_invalid_utf8_path_without_partial_claim(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    descriptor = os.open(os.fsencode(workspace) + b"/bad-\xff", os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)

    result = GitStatusTool(workspace).execute(_request())

    assert result.is_error
    assert result.content == "git status encountered a path that is not valid UTF-8"


@pytest.mark.parametrize("kind", ["non-repository", "nested", "git-file"])
def test_git_repository_requires_in_root_worktree_metadata(tmp_path: Path, kind: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if kind == "nested":
        _git(workspace, "init", "-q")
        nested = workspace / "nested"
        nested.mkdir()
        workspace = nested
    elif kind == "git-file":
        (workspace / ".git").write_text("gitdir: /tmp/external\n", encoding="utf-8")

    repository = GitRepository(workspace)
    with pytest.raises(GitObservationError, match=r"Git worktree|\.git directory|top level"):
        repository.status_porcelain()


def test_git_status_disables_configured_fsmonitor(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    marker = workspace / "fsmonitor-ran"
    hook = workspace / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(workspace, "config", "core.fsmonitor", str(hook))

    result = GitStatusTool(workspace).execute(_request())

    assert not result.is_error
    assert not marker.exists()


@pytest.mark.parametrize("metadata", ["include", "alternates"])
def test_git_status_rejects_external_repository_metadata(tmp_path: Path, metadata: str) -> None:
    workspace = _repository(tmp_path)
    if metadata == "include":
        with (workspace / ".git" / "config").open("a", encoding="utf-8") as config:
            config.write("[include]\n\tpath = /tmp/external-git-config\n")
    else:
        alternates = workspace / ".git" / "objects" / "info" / "alternates"
        alternates.write_text("/tmp/external-objects\n", encoding="utf-8")

    result = GitStatusTool(workspace).execute(_request())

    assert result.is_error
    assert "external Git" in result.content


def test_git_status_maps_repository_failures_to_safe_tool_error(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    tool = GitStatusTool(workspace)
    subprocess.run(["git", "config", "--unset", "core.repositoryformatversion"], cwd=workspace)
    (workspace / ".git" / "HEAD").unlink()

    result = tool.execute(_request())

    assert result.is_error
    assert result.content == "workspace Git observation failed"


def test_git_status_observe_raises_safe_failure_for_callers(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    tool = GitStatusTool(workspace)
    (workspace / ".git" / "HEAD").unlink()

    with pytest.raises(GitObservationError, match="observation failed"):
        tool.observe()
