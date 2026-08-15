from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coquo.child_runtime import child_role_descriptor, role_allowed_by_parent
from coquo.core.permissions import PermissionMode
from coquo.session import ProjectSession
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.team_schedule import TeamScheduleService
from coquo.team_service import TeamAssignmentError
from coquo.team_store import TeamStore
from coquo.worktree_service import WorktreeService


def _git_workspace(path: Path) -> None:
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.email", "test@example.com"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "Coquo Test"), check=True)
    (path / ".gitignore").write_text(".coquo/\nuser.json\nproject.json\n", encoding="utf-8")
    (path / "README.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-qm", "base"), check=True)


def _open(path: Path, mode: PermissionMode) -> ProjectSession:
    return ProjectSession.open(
        path,
        permission_mode=mode,
        environment={},
        user_profile_path=path / "user.json",
        project_profile_path=path / "project.json",
    )


def test_role_catalog_and_parent_ceiling_are_closed() -> None:
    assert len(child_role_descriptor("read-only-investigator-v1").tool_names) == 18
    assert len(child_role_descriptor("isolated-workspace-writer-v1").tool_names) == 27
    coder = child_role_descriptor("isolated-coder-v1")
    assert len(coder.tool_names) == 28
    assert "run_command" in coder.tool_names
    assert role_allowed_by_parent("isolated-workspace-writer-v1", "workspace-write")
    assert not role_allowed_by_parent("isolated-coder-v1", "workspace-write")
    assert role_allowed_by_parent("isolated-coder-v1", "danger-full-access")
    with pytest.raises(ValueError):
        child_role_descriptor("unknown-role")


def test_writable_team_member_runs_in_linked_worktree_and_seals_empty_result(
    tmp_path: Path,
) -> None:
    _git_workspace(tmp_path)
    session = _open(tmp_path, PermissionMode.WORKSPACE_WRITE)
    try:
        team = session.create_team("Writable")
        member = session.add_team_member(
            team.team_id,
            "Writer",
            role_contract="isolated-workspace-writer-v1",
        )
        assignment = session.create_team_assignment(team.team_id, member.member_id, "Inspect root")
        assert assignment.assignment.worktree_id is not None
        assert assignment.child is not None
        assert assignment.child.execution_scope == "team-worktree"

        prepared = session.prepare_team_assignment(
            team.team_id, assignment.assignment.assignment_id
        )
        assert prepared.child is not None
        assert prepared.child.status.value == "ready"
        completed = session.run_team_assignment(team.team_id, assignment.assignment.assignment_id)
        assert completed.assignment.phase.value == "terminal_observed"
        worktree = WorktreeService(tmp_path).store.inspect(assignment.assignment.worktree_id)
        assert worktree.state == "sealed_empty"
        assert TeamStore(tmp_path).inspect(team.team_id).assignments[0].member_role_contract == (
            "isolated-workspace-writer-v1"
        )
    finally:
        session.close()


def test_parent_workspace_write_rejects_coder_assignment(tmp_path: Path) -> None:
    _git_workspace(tmp_path)
    session = _open(tmp_path, PermissionMode.WORKSPACE_WRITE)
    try:
        team = session.create_team("Ceiling")
        member = session.add_team_member(
            team.team_id,
            "Coder",
            role_contract="isolated-coder-v1",
        )
        with pytest.raises(TeamAssignmentError, match="permission ceiling"):
            session.create_team_assignment(team.team_id, member.member_id, "Run tests")
    finally:
        session.close()


def test_two_writable_members_use_distinct_roots_in_one_schedule(tmp_path: Path) -> None:
    _git_workspace(tmp_path)
    session = _open(tmp_path, PermissionMode.WORKSPACE_WRITE)
    try:
        team = session.create_team("Parallel writable")
        first_member = session.add_team_member(
            team.team_id,
            "First",
            role_contract="isolated-workspace-writer-v1",
        )
        second_member = session.add_team_member(
            team.team_id,
            "Second",
            role_contract="isolated-workspace-writer-v1",
        )
        first = session.create_team_assignment(team.team_id, first_member.member_id, "Inspect one")
        second = session.create_team_assignment(
            team.team_id, second_member.member_id, "Inspect two"
        )
        session.prepare_team_assignment(team.team_id, first.assignment.assignment_id)
        session.prepare_team_assignment(team.team_id, second.assignment.assignment_id)
        session.start_team_assignment(team.team_id, first.assignment.assignment_id)
        session.start_team_assignment(team.team_id, second.assignment.assignment_id)
        first_done = session.wait_team_assignment(team.team_id, first.assignment.assignment_id, 10)
        second_done = session.wait_team_assignment(
            team.team_id, second.assignment.assignment_id, 10
        )
        assert first_done.assignment.worktree_id != second_done.assignment.worktree_id
        assert first_done.child is not None and first_done.child.status.value == "completed"
        assert second_done.child is not None and second_done.child.status.value == "completed"
        status = subprocess.run(
            ("git", "-C", str(tmp_path), "status", "--porcelain"),
            check=True,
            capture_output=True,
            text=True,
        )
        assert status.stdout == ""
    finally:
        session.close()


def test_writable_schedule_pins_capability_snapshot(tmp_path: Path) -> None:
    _git_workspace(tmp_path)
    owner_writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner = owner_writer.session_id
    owner_writer.release()
    team_store = TeamStore(tmp_path)
    team = team_store.create("Scheduled", owner_session=owner)
    member = team_store.add_member(
        team.team_id,
        "Writer",
        role_contract="isolated-workspace-writer-v1",
    )
    run = TeamScheduleService(tmp_path).start(
        team.team_id,
        parent_permission_mode="workspace-write",
    )
    try:
        state = team_store.inspect(team.team_id).schedules[-1]
        assert state.capability_snapshot_sha256 is not None
        assert state.eligible_members[0]["member_id"] == member.member_id
        assert state.eligible_members[0]["role_contract"] == "isolated-workspace-writer-v1"
        assert state.parent_permission_mode == "workspace-write"
    finally:
        run.cancel("test cleanup")
        run.close()
