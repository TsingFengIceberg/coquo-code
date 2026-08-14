from __future__ import annotations

from threading import Event

import pytest

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunStore
from coquo.child_supervisor import ChildRunSupervisor
from coquo.session import ProjectSession, SessionStoreError
from coquo.session_store import SessionStore
from coquo.team_service import TeamAssignmentError, TeamAssignmentService
from coquo.team_records import TeamAssignmentPhase
from coquo.team_store import TeamStore, TeamStoreError


def _open_session(tmp_path):
    return ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )


def test_one_member_reuses_identity_with_fresh_child_runs_and_sessions(tmp_path) -> None:
    session = _open_session(tmp_path)
    team = session.create_team("Review")
    member = session.add_team_member(team.team_id, "Inspector")

    first = session.create_team_assignment(team.team_id, member.member_id, "Inspect first")
    first = session.prepare_team_assignment(team.team_id, first.assignment.assignment_id)
    first = session.run_team_assignment(team.team_id, first.assignment.assignment_id)
    assert first.phase is TeamAssignmentPhase.TERMINAL_OBSERVED
    assert first.child is not None and first.child.status is ChildRunStatus.COMPLETED
    assert first.child.child_session_id is not None

    second = session.create_team_assignment(team.team_id, member.member_id, "Inspect second")
    assert second.assignment.assignment_id != first.assignment.assignment_id
    second = session.prepare_team_assignment(team.team_id, second.assignment.assignment_id)
    second = session.run_team_assignment(team.team_id, second.assignment.assignment_id)
    assert second.phase is TeamAssignmentPhase.TERMINAL_OBSERVED
    assert second.child is not None and second.child.status is ChildRunStatus.COMPLETED
    assert second.child.child_run_id != first.child.child_run_id
    assert second.child.child_session_id != first.child.child_session_id

    first_session = SessionStore(tmp_path).inspect(first.child.child_session_id)
    second_session = SessionStore(tmp_path).inspect(second.child.child_session_id)
    assert first_session.turn_count == 1
    assert second_session.turn_count == 1
    session.close()


def test_team_close_and_member_leave_require_terminal_observation(tmp_path) -> None:
    session = _open_session(tmp_path)
    team = session.create_team("Review")
    member = session.add_team_member(team.team_id, "Inspector")
    assignment = session.create_team_assignment(team.team_id, member.member_id, "Inspect")

    with pytest.raises(TeamAssignmentError, match="nonterminal"):
        session.close_team(team.team_id)
    with pytest.raises(TeamAssignmentError, match="nonterminal"):
        session.leave_team_member(team.team_id, member.member_id, "done")

    session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)
    session.run_team_assignment(team.team_id, assignment.assignment.assignment_id)
    left = session.leave_team_member(team.team_id, member.member_id, "done")
    assert left.status.value == "left"
    closed = session.close_team(team.team_id)
    assert closed.status.value == "closed"
    session.close()


def test_team_service_rejects_child_without_exact_team_provenance(tmp_path) -> None:
    session = _open_session(tmp_path)
    team = session.create_team("Review")
    member = session.add_team_member(team.team_id, "Inspector")
    child = ChildRunStore(tmp_path).create("Inspect", parent_session=session.session_id)
    assignment_id = "22345678-1234-4234-9234-123456789abc"
    TeamStore(tmp_path).create_assignment(
        team.team_id, assignment_id, member.member_id, child.child_run_id, "Inspect"
    )
    TeamStore(tmp_path).bind_assignment(team.team_id, assignment_id, child.child_run_id)

    info = TeamAssignmentService(tmp_path).inspect(team.team_id, assignment_id)
    assert info.child is None and "provenance" in (info.child_error or "")
    with pytest.raises(TeamStoreError, match="provenance"):
        session.prepare_team_assignment(team.team_id, assignment_id)

    session.close()


def test_session_identity_change_rejects_active_team_assignment_then_retires(tmp_path) -> None:
    session = _open_session(tmp_path)
    team = session.create_team("Review")
    member = session.add_team_member(team.team_id, "Inspector")
    assignment = session.create_team_assignment(team.team_id, member.member_id, "Inspect")
    session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)

    started = Event()
    release = Event()

    class CancellableExecutor:
        def run(self, child_run_id: str, *, cancellation=None):
            started.set()
            while not release.is_set() and not (cancellation and cancellation.requested):
                release.wait(0.01)
            current = ChildRunStore(tmp_path).inspect(child_run_id)
            if current.status is ChildRunStatus.CANCELLING:
                ChildRunStore(tmp_path).finish_cancelled(child_run_id)

    session._child_supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda _child_id: CancellableExecutor(),
        worker_count=1,
        parent_session_id=session.session_id,
    )
    session.start_team_assignment(team.team_id, assignment.assignment.assignment_id)
    assert started.wait(timeout=2)
    old_session_id = session.session_id
    with pytest.raises(SessionStoreError, match="queued or active"):
        session.new_session()
    session.cancel_team_assignment(team.team_id, assignment.assignment.assignment_id, "stop")
    session.wait_team_assignment(team.team_id, assignment.assignment.assignment_id, 2)
    new_info = session.new_session()
    assert new_info.session_id != old_session_id
    release.set()
    session.close()
