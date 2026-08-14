from __future__ import annotations

from pathlib import Path

import pytest

from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.team_records import TeamRecordError, TeamWorkStatus, encode_team_record
from coquo.team_store import TeamStore
from coquo.team_work import TeamWorkError, TeamWorkService
from coquo.session import ProjectSession


def _team(tmp_path: Path):
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner = writer.session_id
    writer.release()
    store = TeamStore(tmp_path)
    team = store.create("Board", owner_session=owner)
    return store, team


def test_work_dependencies_are_backward_only_and_readiness_is_deterministic(tmp_path: Path) -> None:
    store, team = _team(tmp_path)
    service = TeamWorkService(tmp_path)
    first = service.create(team.team_id, "First", "Do first")
    second = service.create(team.team_id, "Second", "Do second", (first.work_item_id,))
    assert first.status is TeamWorkStatus.READY
    assert second.status is TeamWorkStatus.BLOCKED
    assert second.blocked_dependency_ids == (first.work_item_id,)
    reopened = service.list(team.team_id)
    assert [item.work_item_id for item in reopened.items] == [
        first.work_item_id,
        second.work_item_id,
    ]
    assert store.inspect(team.team_id).work_items[1].status is TeamWorkStatus.BLOCKED


def test_work_cancel_is_durable_and_does_not_touch_parent_session(tmp_path: Path) -> None:
    store, team = _team(tmp_path)
    item = TeamWorkService(tmp_path).create(team.team_id, "Drop", "No longer needed")
    cancelled = TeamWorkService(tmp_path).cancel(team.team_id, item.work_item_id, "superseded")
    assert cancelled.status is TeamWorkStatus.CANCELLED
    assert TeamStore(tmp_path).inspect(team.team_id).work_items[0].terminal_reason == "superseded"


def test_work_rejects_unknown_dependency_and_illegal_second_cancel(tmp_path: Path) -> None:
    _, team = _team(tmp_path)
    service = TeamWorkService(tmp_path)
    with pytest.raises(TeamWorkError, match="dependency"):
        service.create(team.team_id, "Bad", "Bad", ("12345678-1234-4234-8234-123456789abc",))
    item = service.create(team.team_id, "One", "One")
    service.cancel(team.team_id, item.work_item_id, "done")
    with pytest.raises(TeamWorkError, match="current state"):
        service.cancel(team.team_id, item.work_item_id, "again")


def test_work_record_rejects_unknown_fields(tmp_path: Path) -> None:
    _, team = _team(tmp_path)
    state = TeamStore(tmp_path).replay_state(team.team_id)
    record = state.records[0]
    payload = encode_team_record(record).replace(b"}\n", b',"extra":1}\n')
    with pytest.raises(TeamRecordError, match="unknown"):
        from coquo.team_records import decode_team_record

        decode_team_record(payload)


def test_ready_work_uses_one_assignment_and_requires_explicit_completion(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Board Runtime")
    member = session.add_team_member(team.team_id, "Worker")
    item = session.create_team_work(team.team_id, "Inspect", "Inspect the workspace")
    assignment = session.assign_team_work(team.team_id, item.work_item_id, member.member_id)
    assert (
        session.inspect_team_work(team.team_id, item.work_item_id).status is TeamWorkStatus.ASSIGNED
    )
    session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)
    session.run_team_assignment(team.team_id, assignment.assignment.assignment_id)
    reviewed = session.inspect_team_work(team.team_id, item.work_item_id)
    assert reviewed.status is TeamWorkStatus.REVIEW
    completed = session.complete_team_work(
        team.team_id, item.work_item_id, "Host inspected the handoff"
    )
    assert completed.status is TeamWorkStatus.COMPLETED
    session.close()


def test_completed_dependency_unblocks_next_work_item_and_release_reopens_ready(
    tmp_path: Path,
) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Board Chain")
    member = session.add_team_member(team.team_id, "Worker")
    first = session.create_team_work(team.team_id, "First", "Inspect first")
    second = session.create_team_work(
        team.team_id, "Second", "Inspect second", (first.work_item_id,)
    )
    assert (
        session.inspect_team_work(team.team_id, second.work_item_id).status
        is TeamWorkStatus.BLOCKED
    )

    assignment = session.assign_team_work(team.team_id, first.work_item_id, member.member_id)
    session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)
    session.run_team_assignment(team.team_id, assignment.assignment.assignment_id)
    released = session.release_team_work(team.team_id, first.work_item_id, "needs another pass")
    assert released.status is TeamWorkStatus.READY
    retry = session.assign_team_work(team.team_id, first.work_item_id, member.member_id)
    assert retry.assignment.assignment_id != assignment.assignment.assignment_id
    session.prepare_team_assignment(team.team_id, retry.assignment.assignment_id)
    session.run_team_assignment(team.team_id, retry.assignment.assignment_id)
    session.complete_team_work(team.team_id, first.work_item_id, "Host verified first handoff")
    assert (
        session.inspect_team_work(team.team_id, second.work_item_id).status is TeamWorkStatus.READY
    )
    for message in session.inspect_team(team.team_id).messages:
        if message.sender_member_id == member.member_id and message.status.value == "unread":
            session.read_team_message(team.team_id, message.message_id)
    session.close()
