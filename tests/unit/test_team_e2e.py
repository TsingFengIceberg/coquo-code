from __future__ import annotations

from threading import Barrier, Event, Lock

from coquo.child_supervisor import ChildRunSupervisor
from coquo.cli.slash import dispatch_slash
from coquo.session import ProjectSession


def _open_session(tmp_path) -> ProjectSession:
    return ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )


def test_two_members_overlap_on_existing_supervisor_while_parent_works(tmp_path) -> None:
    session = _open_session(tmp_path)
    team = session.create_team("Parallel")
    first_member = session.add_team_member(team.team_id, "One")
    second_member = session.add_team_member(team.team_id, "Two")
    first = session.create_team_assignment(team.team_id, first_member.member_id, "Inspect one")
    second = session.create_team_assignment(team.team_id, second_member.member_id, "Inspect two")
    session.prepare_team_assignment(team.team_id, first.assignment.assignment_id)
    session.prepare_team_assignment(team.team_id, second.assignment.assignment_id)

    barrier = Barrier(3)
    release = Event()
    calls: list[str] = []
    calls_lock = Lock()

    class BlockingExecutor:
        def run(self, child_run_id: str):
            with calls_lock:
                calls.append(child_run_id)
            barrier.wait(timeout=2)
            release.wait(timeout=2)

    supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda _child_id: BlockingExecutor(),
        worker_count=2,
        parent_session_id=session.session_id,
    )
    session._child_supervisor = supervisor
    session.start_team_assignment(team.team_id, first.assignment.assignment_id)
    session.start_team_assignment(team.team_id, second.assignment.assignment_id)
    barrier.wait(timeout=2)
    assert set(calls) == {
        first.assignment.child_run_id,
        second.assignment.child_run_id,
    }
    assert session.prompt("parent remains usable") == "Fake response: parent remains usable"
    release.set()
    session.close()


def test_repl_team_assignment_execution_commands_use_project_session_wrappers(tmp_path) -> None:
    session = _open_session(tmp_path)
    team = session.create_team("Commands")
    member = session.add_team_member(team.team_id, "Inspector")
    assignment = session.create_team_assignment(team.team_id, member.member_id, "Inspect")
    assignment_id = assignment.assignment.assignment_id

    prepared = dispatch_slash(f"/team assignment prepare {team.team_id} {assignment_id}", session)
    assert (
        prepared.handled and prepared.kind == "success" and "Phase: child_bound" in prepared.message
    )
    ran = dispatch_slash(f"/team assignment run {team.team_id} {assignment_id}", session)
    assert ran.handled and ran.kind == "success" and "terminal_observed" in ran.message
    handoff = dispatch_slash(f"/team assignment handoff {team.team_id} {assignment_id}", session)
    assert handoff.handled and handoff.kind == "info" and handoff.message

    second = session.create_team_assignment(team.team_id, member.member_id, "Inspect again")
    second_id = second.assignment.assignment_id
    session.prepare_team_assignment(team.team_id, second_id)
    started = dispatch_slash(f"/team assignment start {team.team_id} {second_id}", session)
    assert started.handled and started.kind == "success"
    waited = dispatch_slash(f"/team assignment wait {team.team_id} {second_id} 30", session)
    assert waited.handled and waited.kind == "info" and "terminal_observed" in waited.message
    session.close()
