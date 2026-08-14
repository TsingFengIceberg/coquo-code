from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.team_messaging import TeamMessageError, TeamMessagingService
from coquo.team_service import TeamAssignmentError
from coquo.team_records import (
    TeamMessageRead,
    TeamMessageSent,
    TeamRecordError,
    encode_team_record,
    replay_team_records,
    team_message_body_sha256,
    utc_now,
)
from coquo.team_store import TeamStore
from coquo.session import ProjectSession


def _team(tmp_path: Path):
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    store = TeamStore(tmp_path)
    team = store.create("Mailbox", owner_session=session_id)
    member = store.add_member(team.team_id, "Worker")
    return store, team, member, session_id


def test_owner_message_lifecycle_survives_reopen_without_consuming_body(tmp_path: Path) -> None:
    store, team, member, _ = _team(tmp_path)
    service = TeamMessagingService(tmp_path)
    message = service.send_owner(team.team_id, member.member_id, "inspect the config")
    assert message.status.value == "pending"
    assert service.show(team.team_id, message.message_id).body == "inspect the config"
    listed = service.list(team.team_id)
    assert [item.message_id for item in listed.messages] == [message.message_id]
    cancelled = service.cancel(team.team_id, message.message_id, "superseded")
    assert cancelled.status.value == "cancelled"
    assert store.inspect(team.team_id).messages[0].body == "inspect the config"
    with pytest.raises(TeamMessageError, match="no longer pending"):
        service.cancel(team.team_id, message.message_id, "again")


def test_owner_message_rejects_left_member_and_member_read(tmp_path: Path) -> None:
    store, team, member, _ = _team(tmp_path)
    store.leave_member(team.team_id, member.member_id, "retired")
    service = TeamMessagingService(tmp_path)
    with pytest.raises(TeamMessageError, match="left"):
        service.send_owner(team.team_id, member.member_id, "hello")


def test_message_record_round_trip_and_digest_rejection(tmp_path: Path) -> None:
    _, team, member, _ = _team(tmp_path)
    record = TeamMessageSent(
        sequence=2,
        team_id=team.team_id,
        message_id=str(uuid4()),
        sender_member_id=None,
        recipient_member_id=member.member_id,
        body="hello",
        body_sha256=team_message_body_sha256("hello"),
        source_assignment_id=None,
        source_child_session_id=None,
        source_turn_record_sequence=None,
        source_handoff_sha256=None,
        sent_at=utc_now(),
    )
    from coquo.team_records import decode_team_record

    assert decode_team_record(encode_team_record(record)) == record
    with pytest.raises(TeamRecordError, match="digest"):
        encode_team_record(replace(record, body_sha256="0" * 64))


def test_replay_rejects_read_before_send_and_owner_read(tmp_path: Path) -> None:
    store, team, member, _ = _team(tmp_path)
    state = store.replay_state(team.team_id)
    message_id = str(uuid4())
    read = TeamMessageRead(
        sequence=len(state.records), team_id=team.team_id, message_id=message_id, read_at=utc_now()
    )
    with pytest.raises(TeamRecordError, match="Only member messages"):
        replay_team_records(
            [*state.records, read],
            expected_workspace=str(tmp_path.resolve()),
            expected_workspace_fingerprint=state.header.workspace_fingerprint,
            expected_team_id=team.team_id,
            expected_file_name=f"{team.team_id}.jsonl",
        )


def test_replay_rejects_forged_member_provenance(tmp_path: Path) -> None:
    store, team, member, _ = _team(tmp_path)
    state = store.replay_state(team.team_id)
    forged = TeamMessageSent(
        sequence=len(state.records),
        team_id=team.team_id,
        message_id=str(uuid4()),
        sender_member_id=member.member_id,
        recipient_member_id=None,
        body="forged",
        body_sha256=team_message_body_sha256("forged"),
        source_assignment_id=str(uuid4()),
        source_child_session_id=str(uuid4()),
        source_turn_record_sequence=3,
        source_handoff_sha256="a" * 64,
        sent_at=utc_now(),
    )
    with pytest.raises(TeamRecordError, match="provenance"):
        replay_team_records(
            [*state.records, forged],
            expected_workspace=str(tmp_path.resolve()),
            expected_workspace_fingerprint=state.header.workspace_fingerprint,
            expected_team_id=team.team_id,
            expected_file_name=f"{team.team_id}.jsonl",
        )


def test_message_operations_do_not_append_parent_session_turn(tmp_path: Path) -> None:
    store, team, member, session_id = _team(tmp_path)
    before = SessionStore(tmp_path).inspect(session_id).path.read_bytes()
    service = TeamMessagingService(tmp_path)
    service.send_owner(team.team_id, member.member_id, "no provider")
    after = SessionStore(tmp_path).inspect(session_id).path.read_bytes()
    assert after == before


def test_team_assignment_freezes_inbox_and_publishes_one_reply(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Mailbox Runtime")
    member = session.add_team_member(team.team_id, "Worker")
    first = session.send_team_message(team.team_id, member.member_id, "check README")
    assignment = session.create_team_assignment(
        team.team_id, member.member_id, "Inspect the workspace"
    )
    prepared = session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)
    assert prepared.assignment.inbox_message_ids == (first.message_id,)
    assert prepared.assignment.reply_message_id is not None
    session.run_team_assignment(team.team_id, assignment.assignment.assignment_id)
    info = session.inspect_team_assignment(team.team_id, assignment.assignment.assignment_id)
    assert info.phase.value == "terminal_observed"
    state = session.inspect_team(team.team_id)
    replies = [item for item in state.messages if item.sender_member_id == member.member_id]
    assert len(replies) == 1
    assert replies[0].source_assignment_id == assignment.assignment.assignment_id
    assert state.messages[0].status.value == "delivered"
    session.close()


def test_team_close_requires_message_cancellation_and_reply_read(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Mailbox Gates")
    member = session.add_team_member(team.team_id, "Worker")
    pending = session.send_team_message(team.team_id, member.member_id, "later")
    with pytest.raises(TeamAssignmentError, match="coordination items"):
        session.close_team(team.team_id)
    session.cancel_team_message(team.team_id, pending.message_id, "no longer needed")
    assignment = session.create_team_assignment(team.team_id, member.member_id, "Inspect")
    session.prepare_team_assignment(team.team_id, assignment.assignment.assignment_id)
    session.run_team_assignment(team.team_id, assignment.assignment.assignment_id)
    with pytest.raises(TeamAssignmentError, match="unread"):
        session.close_team(team.team_id)
    reply = next(
        message
        for message in session.inspect_team(team.team_id).messages
        if message.sender_member_id == member.member_id
    )
    session.read_team_message(team.team_id, reply.message_id)
    session.close_team(team.team_id)


def test_failed_assignment_does_not_consume_pending_inbox(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Mailbox Retry")
    member = session.add_team_member(team.team_id, "Worker")
    message = session.send_team_message(team.team_id, member.member_id, "retry me")
    first = session.create_team_assignment(team.team_id, member.member_id, "First attempt")
    session.prepare_team_assignment(team.team_id, first.assignment.assignment_id)
    session.cancel_team_assignment(team.team_id, first.assignment.assignment_id, "stop")
    second = session.create_team_assignment(team.team_id, member.member_id, "Second attempt")
    prepared = session.prepare_team_assignment(team.team_id, second.assignment.assignment_id)
    assert prepared.assignment.inbox_message_ids == (message.message_id,)
    session.cancel_team_assignment(team.team_id, second.assignment.assignment_id, "stop")
    session.close()
