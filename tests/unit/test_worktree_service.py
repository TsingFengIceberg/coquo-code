from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest

from coquo.worktree_service import WorktreeService, WorktreeServiceError


def _git(path, *args):
    return subprocess.run(
        ("git", *args), cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def _repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    (path / "file.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "file.txt")
    _git(path, "commit", "-qm", "base")


def _ids():
    return [str(uuid4()) for _ in range(4)]


def test_provision_and_seal_empty_worktree(tmp_path):
    _repo(tmp_path)
    ids = _ids()
    service = WorktreeService(tmp_path)
    info = service.provision(
        team_id=ids[0],
        assignment_id=ids[1],
        child_run_id=ids[2],
        member_id=ids[3],
        role_contract="isolated-workspace-writer-v1",
    )
    assert info.state == "ready"
    sealed = service.seal(info.worktree_id)
    assert sealed.patch_bytes == 0
    assert service.store.inspect(info.worktree_id).state == "sealed_empty"


def test_provision_and_seal_tracked_and_untracked_changes(tmp_path):
    _repo(tmp_path)
    ids = _ids()
    service = WorktreeService(tmp_path)
    info = service.provision(
        team_id=ids[0],
        assignment_id=ids[1],
        child_run_id=ids[2],
        member_id=ids[3],
        role_contract="isolated-workspace-writer-v1",
    )
    binding = service.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    (binding.worktree_root / "new.txt").write_text("new\n", encoding="utf-8")
    sealed = service.seal(info.worktree_id)
    assert sealed.patch_bytes > 0
    assert "new.txt" in sealed.changed_paths
    assert service.store.artifact_path(info.worktree_id).read_bytes()


def test_provision_rejects_dirty_authority(tmp_path):
    _repo(tmp_path)
    (tmp_path / "file.txt").write_text("dirty\n", encoding="utf-8")
    service = WorktreeService(tmp_path)
    ids = _ids()
    with pytest.raises(WorktreeServiceError, match="clean"):
        service.provision(
            team_id=ids[0],
            assignment_id=ids[1],
            child_run_id=ids[2],
            member_id=ids[3],
            role_contract="isolated-workspace-writer-v1",
        )
