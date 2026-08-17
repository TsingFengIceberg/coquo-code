from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest

from coquo.git_worktree import GitWorktreeError
from coquo.worktree_integration import WorktreeIntegrationError, WorktreeIntegrationService
from coquo.worktree_service import WorktreeService, WorktreeServiceError
from coquo.worktree_store import WorktreeStoreError


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


def test_integrate_applies_exact_sealed_patch_and_leaves_authority_uncommitted(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    sealed = worktrees.seal(info.worktree_id)
    service = WorktreeIntegrationService(tmp_path)
    prepared = service.prepare(team_id, assignment_id, sealed.patch_sha256)
    result = service.integrate(prepared, action_digest="a" * 64)
    assert result.status == "applied"
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "changed\n"
    assert service.worktrees.store.inspect(info.worktree_id).state == "applied"
    assert service.worktrees.store.inspect(info.worktree_id).integration is not None
    assert (
        subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=tmp_path, check=False).returncode
        == 0
    )


def test_integrate_rejects_wrong_digest_without_mutating_authority(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    worktrees.seal(info.worktree_id)
    with pytest.raises(WorktreeIntegrationError, match="digest"):
        WorktreeIntegrationService(tmp_path).prepare(team_id, assignment_id, "b" * 64)
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "base\n"


def test_integrate_rejects_dirty_authority(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    sealed = worktrees.seal(info.worktree_id)
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeIntegrationError, match="clean"):
        WorktreeIntegrationService(tmp_path).prepare(team_id, assignment_id, sealed.patch_sha256)


def test_prepare_rejects_source_drift_after_sealing(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    sealed = worktrees.seal(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorktreeIntegrationError, match="source worktree changed"):
        WorktreeIntegrationService(tmp_path).prepare(team_id, assignment_id, sealed.patch_sha256)
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "base\n"


def test_prepare_rejects_target_conflict_without_authority_mutation(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("child\n", encoding="utf-8")
    sealed = worktrees.seal(info.worktree_id)
    (tmp_path / "file.txt").write_text("parent\n", encoding="utf-8")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-qm", "advance target")
    with pytest.raises(WorktreeIntegrationError, match="patch|apply"):
        WorktreeIntegrationService(tmp_path).prepare(team_id, assignment_id, sealed.patch_sha256)
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "parent\n"
    assert (
        subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=no"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
        ).stdout
        == b""
    )


def test_provision_unknown_is_recoverable_without_retry(tmp_path, monkeypatch):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    worktree_id = str(uuid4())
    from coquo import worktree_service as worktree_service_module

    original_add = worktree_service_module.add_linked_worktree

    def add_then_lose_process(authority, binding):
        original_add(authority, binding)
        raise GitWorktreeError("injected post-provision process loss")

    monkeypatch.setattr(worktree_service_module, "add_linked_worktree", add_then_lose_process)
    with pytest.raises(WorktreeServiceError, match="process loss"):
        worktrees.provision(
            team_id=team_id,
            assignment_id=assignment_id,
            child_run_id=child_run_id,
            member_id=member_id,
            role_contract="isolated-workspace-writer-v1",
            worktree_id=worktree_id,
        )
    assert worktrees.store.inspect(worktree_id).state == "provision_unknown"
    assert worktrees.recover(worktree_id).state == "provision_unknown"


def test_seal_unknown_is_recoverable_without_retry(tmp_path, monkeypatch):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    original_write_manifest = worktrees.store.write_manifest_artifact

    def write_then_lose_process(worktree_id, manifest):
        original_write_manifest(worktree_id, manifest)
        raise WorktreeStoreError("injected post-seal process loss")

    monkeypatch.setattr(worktrees.store, "write_manifest_artifact", write_then_lose_process)
    with pytest.raises(WorktreeServiceError, match="process loss"):
        worktrees.seal(info.worktree_id)
    assert worktrees.store.inspect(info.worktree_id).state == "seal_unknown"
    assert worktrees.recover(info.worktree_id).state == "seal_unknown"


def test_integration_unknown_is_not_retried_after_apply_effect(tmp_path, monkeypatch):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "file.txt").write_text("changed\n", encoding="utf-8")
    sealed = worktrees.seal(info.worktree_id)
    service = WorktreeIntegrationService(tmp_path)
    prepared = service.prepare(team_id, assignment_id, sealed.patch_sha256)
    from coquo import worktree_integration as integration_module

    original_apply = integration_module.apply_patch

    def apply_then_lose_process(authority, patch):
        original_apply(authority, patch)
        raise GitWorktreeError("injected post-apply process loss")

    monkeypatch.setattr(integration_module, "apply_patch", apply_then_lose_process)
    with pytest.raises(WorktreeIntegrationError, match="process loss"):
        service.integrate(prepared, action_digest="a" * 64)
    assert service.worktrees.store.inspect(info.worktree_id).state == "integration_unknown"
    assert service.worktrees.recover(info.worktree_id).state == "integration_unknown"
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "changed\n"
    with pytest.raises(WorktreeIntegrationError, match="sealed changes"):
        service.prepare(team_id, assignment_id, sealed.patch_sha256)


def test_worktree_diff_recover_and_explicit_retire_are_bounded_and_observable(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    worktrees = WorktreeService(tmp_path)
    info = worktrees.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    binding = worktrees.inspect_binding(info.worktree_id)
    (binding.worktree_root / "new.txt").write_text("new\n", encoding="utf-8")
    diff = worktrees.diff(info.worktree_id, max_bytes=4)
    assert diff.truncated is True
    assert diff.changed_paths == ("new.txt",)
    assert worktrees.recover(info.worktree_id).state == "ready"
    sealed = worktrees.seal(info.worktree_id)
    assert sealed.patch_bytes > 0
    prepared = WorktreeIntegrationService(tmp_path).prepare(
        team_id, assignment_id, sealed.patch_sha256
    )
    WorktreeIntegrationService(tmp_path).integrate(prepared, action_digest="a" * 64)
    retired = worktrees.retire(info.worktree_id)
    assert retired.state == "retired"
    assert not binding.worktree_root.exists()
    assert worktrees.store.artifact_path(info.worktree_id).exists()


def test_worktree_retire_requires_terminal_reviewable_state(tmp_path):
    _repo(tmp_path)
    team_id, assignment_id, child_run_id, member_id = _ids()
    info = WorktreeService(tmp_path).provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id=child_run_id,
        member_id=member_id,
        role_contract="isolated-workspace-writer-v1",
    )
    with pytest.raises(Exception, match="cannot be retired"):
        WorktreeService(tmp_path).retire(info.worktree_id)
