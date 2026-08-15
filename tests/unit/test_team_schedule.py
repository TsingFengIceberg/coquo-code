from __future__ import annotations

from pathlib import Path

import pytest

from coquo.session_records import BindingSnapshot, workspace_fingerprint
from coquo.session_store import SessionStore
from coquo.team_records import (
    TeamAssignmentCreated,
    TeamHeader,
    TeamMemberJoined,
    TeamRecordError,
    TeamScheduleCancelRequested,
    TeamScheduleFinished,
    TeamScheduleOutcome,
    TeamScheduleStarted,
    decode_team_record,
    encode_team_record,
    replay_team_records,
    team_assignment_objective_sha256,
)
from coquo.team_schedule import TeamScheduleService, select_member, select_next
from coquo.team_store import TeamStore, TeamStoreError
from coquo.session import ProjectSession


TEAM_ID = "12345678-1234-4234-8234-123456789abc"
SESSION_ID = "22345678-1234-4234-8234-123456789abc"
MEMBER_ID = "32345678-1234-4234-8234-123456789abc"
RUN_ID = "42345678-1234-4234-8234-123456789abc"
ASSIGNMENT_ID = "52345678-1234-4234-8234-123456789abc"
CHILD_ID = "62345678-1234-4234-8234-123456789abc"
STAMP = "2026-08-14T00:00:01.000000Z"


def _header(tmp_path: Path) -> TeamHeader:
    return TeamHeader(
        0,
        TEAM_ID,
        str(tmp_path),
        workspace_fingerprint(tmp_path),
        SESSION_ID,
        "Alpha",
        "2026-08-14T00:00:00.000000Z",
    )


def test_schedule_records_round_trip_and_closed_fields(tmp_path: Path) -> None:
    records = [
        TeamScheduleStarted(1, TEAM_ID, RUN_ID, "host", 2, 1, STAMP),
        TeamScheduleCancelRequested(2, TEAM_ID, RUN_ID, "stop", "host", STAMP),
        TeamScheduleFinished(3, TEAM_ID, RUN_ID, "cancelled", 0, "cancelled", "stopped", STAMP),
    ]
    assert [decode_team_record(encode_team_record(record)) for record in records] == records
    with pytest.raises(TeamRecordError):
        decode_team_record(
            encode_team_record(records[0]).replace(b'"source":"host"', b'"extra":1,"source":"host"')
        )


def test_schedule_replay_requires_cancel_before_cancelled_finish(tmp_path: Path) -> None:
    header = _header(tmp_path)
    started = TeamScheduleStarted(1, TEAM_ID, RUN_ID, "host", 2, 1, STAMP)
    finished = TeamScheduleFinished(
        2, TEAM_ID, RUN_ID, "cancelled", 0, "cancelled", "stopped", STAMP
    )
    with pytest.raises(TeamRecordError, match="cancel request"):
        replay_team_records(
            [header, started, finished],
            expected_workspace=str(tmp_path),
            expected_workspace_fingerprint=header.workspace_fingerprint,
            expected_team_id=TEAM_ID,
            expected_file_name=f"{TEAM_ID}.jsonl",
        )


def test_assignment_v3_replays_schedule_provenance(tmp_path: Path) -> None:
    header = _header(tmp_path)
    member = TeamMemberJoined(1, TEAM_ID, MEMBER_ID, "Worker", "read-only-investigator-v1", STAMP)
    started = TeamScheduleStarted(2, TEAM_ID, RUN_ID, "host", 1, 1, STAMP)
    assignment = TeamAssignmentCreated(
        3,
        TEAM_ID,
        ASSIGNMENT_ID,
        MEMBER_ID,
        CHILD_ID,
        "Inspect",
        team_assignment_objective_sha256("Inspect"),
        STAMP,
        None,
        RUN_ID,
        schema_version=3,
    )
    decoded = decode_team_record(encode_team_record(assignment))
    state = replay_team_records(
        [header, member, started, decoded],
        expected_workspace=str(tmp_path),
        expected_workspace_fingerprint=header.workspace_fingerprint,
        expected_team_id=TEAM_ID,
        expected_file_name=f"{TEAM_ID}.jsonl",
    )
    assert state.assignments[0].schedule_run_id == RUN_ID
    assert state.schedules[0].assignment_ids == (ASSIGNMENT_ID,)


def test_schedule_lease_is_exclusive_and_reacquirable(tmp_path: Path) -> None:
    owner = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner_id = owner.session_id
    owner.release()
    ids = iter([TEAM_ID, MEMBER_ID, RUN_ID])
    store = TeamStore(tmp_path, uuid_factory=lambda: next(ids))
    team = store.create("Alpha", owner_session=owner_id)
    lease = store.acquire_schedule(team.team_id)
    with pytest.raises(TeamStoreError, match="active schedule lease"):
        TeamStore(tmp_path).acquire_schedule(team.team_id)
    lease.close()
    second = TeamStore(tmp_path).acquire_schedule(team.team_id)
    second.close()


def test_schedule_service_selection_is_deterministic(tmp_path: Path) -> None:
    owner = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner_id = owner.session_id
    owner.release()
    ids = iter([TEAM_ID, MEMBER_ID, "42345678-1234-4234-8234-123456789abc", RUN_ID])
    store = TeamStore(tmp_path, uuid_factory=lambda: next(ids))
    team = store.create("Alpha", owner_session=owner_id)
    first = store.add_member(team.team_id, "A")
    second = store.add_member(team.team_id, "B")
    item = TeamStore(tmp_path).create_work_item(
        team.team_id,
        "72345678-1234-4234-8234-123456789abc",
        "Work",
        "Inspect",
        (),
    )
    info = TeamStore(tmp_path).inspect(team.team_id)
    assert select_member(info, item).member_id == first.member_id
    assert select_next(info).member.member_id == first.member_id
    assert second.member_id != first.member_id


def test_schedule_service_start_holds_lease_until_finish(tmp_path: Path) -> None:
    owner = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner_id = owner.session_id
    owner.release()
    ids = iter([TEAM_ID, MEMBER_ID, RUN_ID])
    store = TeamStore(tmp_path, uuid_factory=lambda: next(ids))
    team = store.create("Alpha", owner_session=owner_id)
    store.add_member(team.team_id, "A")
    service = TeamScheduleService(tmp_path, uuid_factory=lambda: RUN_ID)
    run = service.start(team.team_id, max_assignments=1, max_parallel=1)
    with pytest.raises(Exception):
        service.start(team.team_id, max_assignments=1, max_parallel=1, schedule_run_id=RUN_ID)
    run.finish(outcome=TeamScheduleOutcome.IDLE)


def test_project_session_foreground_schedule_reuses_child_supervisor(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Scheduled")
    member = session.add_team_member(team.team_id, "Worker")
    session.create_team_work(team.team_id, "Inspect", "Inspect the workspace")
    state = session.run_team_schedule(team.team_id, max_assignments=1, max_parallel=1)
    assert state.outcome is TeamScheduleOutcome.LIMIT_REACHED
    info = session.inspect_team(team.team_id)
    assert len(info.assignments) == 1
    assert info.assignments[0].schedule_run_id == state.schedule_run_id
    assert info.assignments[0].phase.value == "terminal_observed"
    assert info.members[0].member_id == member.member_id
    assert info.work_items[0].status.value == "review"
    session.close()


def test_project_session_background_schedule_notifies_and_releases_parent(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    team = session.create_team("Background")
    session.add_team_member(team.team_id, "Worker")
    session.create_team_work(team.team_id, "Inspect", "Inspect the workspace")
    started = session.start_team_schedule(team.team_id, max_assignments=1, max_parallel=1)
    notification = session.wait_team_schedule(team.team_id, started.schedule_run_id, 30)
    assert notification is not None and notification.state is not None
    assert notification.state.status.terminal
    assert session.prompt("parent remains usable") == "Fake response: parent remains usable"
    session.close()
