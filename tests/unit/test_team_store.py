from __future__ import annotations

from pathlib import Path

import pytest

from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.team_records import TeamMemberStatus, TeamStatus
from coquo.team_store import TeamStore, TeamStoreError


def owner_session(tmp_path: Path) -> str:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    return session_id


def test_team_store_creates_lists_reopens_and_closes(tmp_path: Path) -> None:
    session_id = owner_session(tmp_path)
    store = TeamStore(tmp_path)
    created = store.create("Alpha", owner_session=session_id)
    assert created.status is TeamStatus.OPEN
    assert created.owner_session_id == session_id
    assert store.list() == (created,)
    assert TeamStore(tmp_path).inspect(created.team_id) == created
    closed = store.close(created.team_id)
    assert closed.status is TeamStatus.CLOSED
    assert TeamStore(tmp_path).inspect(created.team_id).closed_at is not None
    assert store.close(created.team_id) == closed


def test_team_store_rejects_unknown_owner_and_wrong_team(tmp_path: Path) -> None:
    store = TeamStore(tmp_path)
    with pytest.raises(TeamStoreError, match="owner"):
        store.create("Alpha", owner_session="12345678-1234-4234-8234-123456789abc")
    session_id = owner_session(tmp_path)
    store.create("Alpha", owner_session=session_id)
    with pytest.raises(TeamStoreError, match="inaccessible"):
        store.inspect("12345678-1234-4234-8234-123456789abc")


def test_team_store_host_operations_do_not_change_session(tmp_path: Path) -> None:
    session_id = owner_session(tmp_path)
    before = SessionStore(tmp_path).inspect(session_id).path.read_bytes()
    team = TeamStore(tmp_path).create("Host only", owner_session=session_id)
    TeamStore(tmp_path).close(team.team_id)
    assert SessionStore(tmp_path).inspect(session_id).path.read_bytes() == before


def test_team_store_rejects_writer_reuse(tmp_path: Path) -> None:
    session_id = owner_session(tmp_path)
    store = TeamStore(tmp_path)
    team = store.create("Alpha", owner_session=session_id)
    writer = store.open(team.team_id)
    try:
        with pytest.raises(TeamStoreError, match="active writer"):
            store.open(team.team_id)
    finally:
        writer.close()


def test_team_store_member_lifecycle_is_durable_and_name_is_reserved(tmp_path: Path) -> None:
    session_id = owner_session(tmp_path)
    store = TeamStore(tmp_path)
    team = store.create("Alpha", owner_session=session_id)
    member = store.add_member(team.team_id, "Worker")
    assert member.status is TeamMemberStatus.ACTIVE
    disabled = store.disable_member(team.team_id, member.member_id, "pause")
    assert disabled.status is TeamMemberStatus.DISABLED
    enabled = store.enable_member(team.team_id, member.member_id)
    assert enabled.status is TeamMemberStatus.ACTIVE
    left = store.leave_member(team.team_id, member.member_id, "done")
    assert left.status is TeamMemberStatus.LEFT
    assert TeamStore(tmp_path).inspect(team.team_id).members[0] == left
    with pytest.raises(TeamStoreError, match="duplicated"):
        TeamStore(tmp_path).add_member(team.team_id, "worker")


def test_team_store_member_operations_reject_invalid_states(tmp_path: Path) -> None:
    session_id = owner_session(tmp_path)
    store = TeamStore(tmp_path)
    team = store.create("Alpha", owner_session=session_id)
    member = store.add_member(team.team_id, "Worker")
    with pytest.raises(TeamStoreError, match="enabled only"):
        store.enable_member(team.team_id, member.member_id)
    store.disable_member(team.team_id, member.member_id, "pause")
    with pytest.raises(TeamStoreError, match="active"):
        store.disable_member(team.team_id, member.member_id, "again")
    store.close(team.team_id)
    with pytest.raises(TeamStoreError, match="closed"):
        store.enable_member(team.team_id, member.member_id)
