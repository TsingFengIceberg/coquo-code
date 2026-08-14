from __future__ import annotations

from pathlib import Path

import pytest

from coquo.session_records import workspace_fingerprint
from coquo.team_records import (
    TeamAssignmentChildBound,
    TeamAssignmentCreated,
    TeamAssignmentPhase,
    TeamClosed,
    TeamHeader,
    TeamMemberDisabled,
    TeamMemberEnabled,
    TeamMemberJoined,
    TeamMemberLeft,
    TeamRecordError,
    TeamMemberStatus,
    TeamStatus,
    decode_team_record,
    encode_team_record,
    replay_team_records,
    team_assignment_objective_sha256,
)


TEAM_ID = "12345678-1234-4234-8234-123456789abc"
SESSION_ID = "22345678-1234-4234-8234-123456789abc"
STAMP = "2026-08-14T00:00:01.000000Z"


def _header(tmp_path: Path) -> TeamHeader:
    return TeamHeader(
        sequence=0,
        team_id=TEAM_ID,
        workspace=str(tmp_path),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        owner_session_id=SESSION_ID,
        name="Alpha",
        created_at="2026-08-14T00:00:00.000000Z",
    )


def test_team_records_round_trip_and_replay(tmp_path: Path) -> None:
    header = _header(tmp_path)
    closed = TeamClosed(1, TEAM_ID, "2026-08-14T00:00:01.000000Z")
    records = [decode_team_record(encode_team_record(item)) for item in (header, closed)]
    state = replay_team_records(
        records,
        expected_workspace=str(tmp_path),
        expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
        expected_team_id=TEAM_ID,
        expected_file_name=f"{TEAM_ID}.jsonl",
    )
    assert state.status is TeamStatus.CLOSED
    assert state.closed == closed


def test_team_replay_rejects_unknown_fields_and_invalid_lifecycle(tmp_path: Path) -> None:
    header = _header(tmp_path)
    with pytest.raises(TeamRecordError):
        decode_team_record(
            encode_team_record(header).replace(b'"name":"Alpha"', b'"extra":1,"name":"Alpha"')
        )
    with pytest.raises(TeamRecordError, match="after team_closed"):
        replay_team_records(
            [
                header,
                TeamClosed(1, TEAM_ID, "2026-08-14T00:00:01.000000Z"),
                TeamClosed(2, TEAM_ID, "2026-08-14T00:00:02.000000Z"),
            ],
            expected_workspace=str(tmp_path),
            expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
            expected_team_id=TEAM_ID,
            expected_file_name=f"{TEAM_ID}.jsonl",
        )


def test_team_replay_rejects_wrong_workspace_identity(tmp_path: Path) -> None:
    header = _header(tmp_path)
    with pytest.raises(TeamRecordError):
        replay_team_records(
            [header],
            expected_workspace=str(tmp_path),
            expected_workspace_fingerprint="v1-" + "0" * 64,
            expected_team_id=TEAM_ID,
            expected_file_name=f"{TEAM_ID}.jsonl",
        )


def test_team_member_lifecycle_replays_with_fixed_role(tmp_path: Path) -> None:
    header = _header(tmp_path)
    member_id = "32345678-1234-4234-8234-123456789abc"
    records = [
        header,
        TeamMemberJoined(
            1,
            TEAM_ID,
            member_id,
            "Worker",
            "read-only-investigator-v1",
            "2026-08-14T00:00:01.000000Z",
        ),
        TeamMemberDisabled(2, TEAM_ID, member_id, "pause", "2026-08-14T00:00:02.000000Z"),
        TeamMemberEnabled(3, TEAM_ID, member_id, "2026-08-14T00:00:03.000000Z"),
        TeamMemberLeft(4, TEAM_ID, member_id, "done", "2026-08-14T00:00:04.000000Z"),
    ]
    decoded = [decode_team_record(encode_team_record(item)) for item in records]
    state = replay_team_records(
        decoded,
        expected_workspace=str(tmp_path),
        expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
        expected_team_id=TEAM_ID,
        expected_file_name=f"{TEAM_ID}.jsonl",
    )
    assert state.members[0].status is TeamMemberStatus.LEFT
    assert state.members[0].name == "Worker"


def test_team_member_replay_rejects_duplicate_name_and_invalid_transition(tmp_path: Path) -> None:
    header = _header(tmp_path)
    first = TeamMemberJoined(
        1,
        TEAM_ID,
        "32345678-1234-4234-8234-123456789abc",
        "Worker",
        "read-only-investigator-v1",
        "2026-08-14T00:00:01.000000Z",
    )
    second = TeamMemberJoined(
        2,
        TEAM_ID,
        "42345678-1234-4234-8234-123456789abc",
        "worker",
        "read-only-investigator-v1",
        "2026-08-14T00:00:02.000000Z",
    )
    with pytest.raises(TeamRecordError, match="name"):
        replay_team_records(
            [header, first, second],
            expected_workspace=str(tmp_path),
            expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
            expected_team_id=TEAM_ID,
            expected_file_name=f"{TEAM_ID}.jsonl",
        )
    with pytest.raises(TeamRecordError, match="enabled only"):
        replay_team_records(
            [
                header,
                first,
                TeamMemberEnabled(2, TEAM_ID, first.member_id, "2026-08-14T00:00:02.000000Z"),
            ],
            expected_workspace=str(tmp_path),
            expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
            expected_team_id=TEAM_ID,
            expected_file_name=f"{TEAM_ID}.jsonl",
        )


def test_team_assignment_replay_requires_exact_child_binding(tmp_path: Path) -> None:
    header = _header(tmp_path)
    member_id = "32345678-1234-4234-8234-123456789abc"
    assignment_id = "42345678-1234-4234-8234-123456789abc"
    child_id = "52345678-1234-4234-8234-123456789abc"
    joined = TeamMemberJoined(1, TEAM_ID, member_id, "Worker", "read-only-investigator-v1", STAMP)
    created = TeamAssignmentCreated(
        2,
        TEAM_ID,
        assignment_id,
        member_id,
        child_id,
        "Inspect files",
        team_assignment_objective_sha256("Inspect files"),
        "2026-08-14T00:00:02.000000Z",
    )
    bound = TeamAssignmentChildBound(
        3, TEAM_ID, assignment_id, child_id, 0, 1, "2026-08-14T00:00:03.000000Z"
    )
    state = replay_team_records(
        [header, joined, created, bound],
        expected_workspace=str(tmp_path),
        expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
        expected_team_id=TEAM_ID,
        expected_file_name=f"{TEAM_ID}.jsonl",
    )
    assert state.assignments[0].phase is TeamAssignmentPhase.CHILD_BOUND
