from __future__ import annotations

import subprocess
import pytest

from coquo.git_worktree import GitWorktreeError, inspect_authority_repository


def _git(path, *args):
    return subprocess.run(
        ("git", *args), cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def test_inspect_authority_requires_attached_safe_repository(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-qm", "base")
    authority = inspect_authority_repository(tmp_path)
    assert authority.root == tmp_path.resolve()
    assert (
        authority.target_ref
        == "refs/heads/" + _git(tmp_path, "branch", "--show-current").stdout.decode().strip()
    )
    assert len(authority.head) == 40


def test_inspect_authority_rejects_linked_worktree_marker(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".git").rename(tmp_path / ".git.real")
    (tmp_path / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    with pytest.raises(GitWorktreeError):
        inspect_authority_repository(tmp_path)


def test_inspect_authority_rejects_detached_head(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "--detach", "-q")
    with pytest.raises(GitWorktreeError):
        inspect_authority_repository(tmp_path)
