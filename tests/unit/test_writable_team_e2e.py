from __future__ import annotations

import subprocess
from threading import Event
from pathlib import Path

import pytest

from coquo.child_runtime import ChildRunExecutor
from coquo.child_run_store import ChildRunStore
from coquo.child_supervisor import ChildRunSupervisor
from coquo.core.contracts import AssistantText, ToolArguments, ToolUse
from coquo.core.permissions import ApprovalMode, PermissionMode
from coquo.providers.fake import ScriptedFakeProvider
from coquo.session import ProjectSession
from coquo.team_service import TeamAssignmentError
from coquo.worktree_integration import WorktreeIntegrationError, WorktreeIntegrationService
from coquo.worktree_service import WorktreeService


def _git_workspace(path: Path) -> None:
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.email", "test@example.com"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "Coquo Test"), check=True)
    (path / ".gitignore").write_text(".coquo/\nuser.json\nproject.json\n", encoding="utf-8")
    (path / "README.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-qm", "base"), check=True)


def _provider_for(child_run_id: str) -> ScriptedFakeProvider:
    return ScriptedFakeProvider(
        (
            ToolUse(
                f"write-{child_run_id[:8]}",
                "write_file",
                ToolArguments.from_mapping(
                    {
                        "path": "result.txt",
                        "content": f"written by {child_run_id}\n",
                    }
                ),
            ),
            AssistantText(f"sealed result from {child_run_id}"),
        )
    )


def _open(path: Path) -> ProjectSession:
    return ProjectSession.open(
        path,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
        environment={},
        user_profile_path=path / "user.json",
        project_profile_path=path / "project.json",
    )


def test_two_writable_children_write_isolated_roots_and_parent_applies_one_patch(
    tmp_path: Path,
) -> None:
    _git_workspace(tmp_path)
    session = _open(tmp_path)
    try:
        team = session.create_team("Writable E2E")
        first_member = session.add_team_member(
            team.team_id, "First", role_contract="isolated-workspace-writer-v1"
        )
        second_member = session.add_team_member(
            team.team_id, "Second", role_contract="isolated-workspace-writer-v1"
        )
        first = session.create_team_assignment(team.team_id, first_member.member_id, "Write first")
        second = session.create_team_assignment(
            team.team_id, second_member.member_id, "Write second"
        )
        session.prepare_team_assignment(team.team_id, first.assignment.assignment_id)
        session.prepare_team_assignment(team.team_id, second.assignment.assignment_id)
        session._child_supervisor = ChildRunSupervisor(
            tmp_path,
            worker_count=2,
            parent_session_id=session.session_id,
            executor_factory=lambda child_id: ChildRunExecutor(
                tmp_path,
                environment={},
                fake_provider_factory=lambda: _provider_for(child_id),
            ),
        )

        session.start_team_assignment(team.team_id, first.assignment.assignment_id)
        session.start_team_assignment(team.team_id, second.assignment.assignment_id)
        first_done = session.wait_team_assignment(team.team_id, first.assignment.assignment_id, 10)
        second_done = session.wait_team_assignment(
            team.team_id, second.assignment.assignment_id, 10
        )

        assert first_done.assignment.worktree_id != second_done.assignment.worktree_id
        assert first_done.assignment.phase.value == "terminal_observed"
        assert second_done.assignment.phase.value == "terminal_observed"
        first_info = WorktreeService(tmp_path).store.inspect(first_done.assignment.worktree_id)
        second_info = WorktreeService(tmp_path).store.inspect(second_done.assignment.worktree_id)
        assert first_info.state == "sealed_changes"
        assert second_info.state == "sealed_changes"
        first_root = WorktreeService(tmp_path).inspect_binding(first_info.worktree_id).worktree_root
        second_root = (
            WorktreeService(tmp_path).inspect_binding(second_info.worktree_id).worktree_root
        )
        assert (first_root / "result.txt").read_text(encoding="utf-8").startswith("written by ")
        assert (second_root / "result.txt").read_text(encoding="utf-8").startswith("written by ")
        assert not (tmp_path / "result.txt").exists()
        assert (
            subprocess.run(
                ("git", "-C", str(tmp_path), "status", "--porcelain"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            == ""
        )

        prepared = WorktreeIntegrationService(tmp_path).prepare(
            team.team_id,
            first_done.assignment.assignment_id,
            first_info.sealed.patch_sha256,
        )
        applied = WorktreeIntegrationService(tmp_path).integrate(prepared, action_digest="a" * 64)
        assert applied.status == "applied"
        assert (tmp_path / "result.txt").read_text(encoding="utf-8").startswith("written by ")
        assert not (second_root / "result.txt").resolve() == (tmp_path / "result.txt").resolve()
        assert WorktreeService(tmp_path).store.inspect(first_info.worktree_id).state == "applied"
        assert (
            WorktreeService(tmp_path).store.inspect(second_info.worktree_id).state
            == "sealed_changes"
        )

    finally:
        session.close()


def test_writable_worktree_artifact_tamper_is_rejected_without_retry(tmp_path: Path) -> None:
    _git_workspace(tmp_path)
    service = WorktreeService(tmp_path)
    team_id = "11111111-1111-4111-8111-111111111111"
    assignment_id = "22222222-2222-4222-8222-222222222222"
    info = service.provision(
        team_id=team_id,
        assignment_id=assignment_id,
        child_run_id="33333333-3333-4333-8333-333333333333",
        member_id="44444444-4444-4444-8444-444444444444",
        role_contract="isolated-workspace-writer-v1",
    )
    binding = service.inspect_binding(info.worktree_id)
    (binding.worktree_root / "result.txt").write_text("safe\n", encoding="utf-8")
    sealed = service.seal(info.worktree_id)
    artifact = service.store.artifact_path(info.worktree_id)
    artifact.write_bytes(artifact.read_bytes() + b"tampered")
    with pytest.raises(WorktreeIntegrationError, match="artifact|digest"):
        WorktreeIntegrationService(tmp_path).prepare(team_id, assignment_id, sealed.patch_sha256)
    assert service.store.inspect(info.worktree_id).state == "sealed_changes"
    assert (tmp_path / "result.txt").exists() is False


def test_team_close_does_not_retire_writable_worktree_and_recovery_is_provider_free(
    tmp_path: Path,
) -> None:
    _git_workspace(tmp_path)
    session = _open(tmp_path)
    try:
        team = session.create_team("Retained")
        member = session.add_team_member(
            team.team_id, "Writer", role_contract="isolated-workspace-writer-v1"
        )
        assignment = session.create_team_assignment(team.team_id, member.member_id, "Write")
        session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)
        session._child_supervisor = ChildRunSupervisor(
            tmp_path,
            parent_session_id=session.session_id,
            executor_factory=lambda child_id: ChildRunExecutor(
                tmp_path,
                environment={},
                fake_provider_factory=lambda: _provider_for(child_id),
            ),
        )
        session.start_team_assignment(team.team_id, assignment.assignment.assignment_id)
        completed = session.wait_team_assignment(
            team.team_id, assignment.assignment.assignment_id, 10
        )
        worktree_id = completed.assignment.worktree_id
        assert worktree_id is not None
        info = session.inspect_team_worktree(worktree_id)
        assert info.state == "sealed_changes"
        assert session.recover_team_worktree(worktree_id).state == "sealed_changes"
        with pytest.raises(TeamAssignmentError):
            session.close_team(team.team_id)
        assert session.inspect_team_worktree(worktree_id).state == "sealed_changes"
        assert WorktreeService(tmp_path).inspect_binding(worktree_id).worktree_root.exists()
    finally:
        session.close()


def test_writable_assignment_cancellation_preserves_worktree_for_host_recovery(
    tmp_path: Path,
) -> None:
    _git_workspace(tmp_path)
    session = _open(tmp_path)
    started = Event()
    try:
        team = session.create_team("Cancel writable")
        member = session.add_team_member(
            team.team_id, "Writer", role_contract="isolated-workspace-writer-v1"
        )
        assignment = session.create_team_assignment(team.team_id, member.member_id, "Cancel")
        session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)

        class CancellableWriter:
            def run(self, child_run_id: str, *, cancellation=None) -> None:
                started.set()
                while cancellation is None or not cancellation.requested:
                    cancellation.wait(0.01)
                current = ChildRunStore(tmp_path).inspect(child_run_id)
                if current.status.value == "cancelling":
                    ChildRunStore(tmp_path).finish_cancelled(child_run_id)

        session._child_supervisor = ChildRunSupervisor(
            tmp_path,
            parent_session_id=session.session_id,
            executor_factory=lambda _child_id: CancellableWriter(),
            worker_count=1,
        )
        session.start_team_assignment(team.team_id, assignment.assignment.assignment_id)
        assert started.wait(timeout=2)
        session.cancel_team_assignment(team.team_id, assignment.assignment.assignment_id, "stop")
        cancelled = session.wait_team_assignment(
            team.team_id, assignment.assignment.assignment_id, 2
        )
        assert cancelled.child is not None
        assert cancelled.child.status.value == "cancelled"
        assert cancelled.assignment.phase.value == "terminal_observed"
        assert (
            WorktreeService(tmp_path).store.inspect(cancelled.assignment.worktree_id).state
            == "ready"
        )
    finally:
        session.close()
