from __future__ import annotations

from pathlib import Path

import pytest

from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.child_run_store import ChildRunStoreError
from coquo.team_records import TeamAssignmentPhase
from coquo.team_service import TeamAssignmentError, TeamAssignmentService
from coquo.team_store import TeamStore


TEAM_ID = "12345678-1234-4234-8234-123456789abc"
MEMBER_A = "22345678-1234-4234-8234-123456789abc"
MEMBER_B = "32345678-1234-4234-8234-123456789abc"
ASSIGNMENT_A = "42345678-1234-4234-8234-123456789abc"
CHILD_A = "52345678-1234-4234-8234-123456789abc"


def _session_and_team(tmp_path: Path):
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner = writer.session_id
    writer.release()
    team_ids = iter([TEAM_ID, MEMBER_A, MEMBER_B])
    team_store = TeamStore(tmp_path, uuid_factory=lambda: next(team_ids))
    team = team_store.create("Alpha", owner_session=owner)
    member_a = team_store.add_member(team.team_id, "Worker A")
    member_b = team_store.add_member(team.team_id, "Worker B")
    return owner, team, member_a, member_b


def test_assignment_saga_binds_exact_child_and_reuses_member_identity(tmp_path: Path) -> None:
    owner, team, member_a, member_b = _session_and_team(tmp_path)
    ids = iter(
        [
            ASSIGNMENT_A,
            CHILD_A,
            "62345678-1234-4234-8234-123456789abc",
            "72345678-1234-4234-8234-123456789abc",
        ]
    )
    service = TeamAssignmentService(tmp_path, uuid_factory=lambda: next(ids))

    first = service.create(team.team_id, member_a.member_id, "Inspect files")
    assert first.phase is TeamAssignmentPhase.CHILD_BOUND
    created_record = TeamStore(tmp_path).replay_state(team.team_id).records[3]
    assert created_record.schema_version == 2
    assert created_record.work_item_id is None
    assert first.child is not None and first.child.team_assignment is not None
    assert first.child.parent_session_id == owner
    with pytest.raises(TeamAssignmentError, match="pending assignment"):
        service.create(team.team_id, member_a.member_id, "Second objective")

    second = service.create(team.team_id, member_b.member_id, "Inspect tests")
    assert second.phase is TeamAssignmentPhase.CHILD_BOUND
    assert second.assignment.child_run_id != first.assignment.child_run_id
    assert len(TeamStore(tmp_path).inspect(team.team_id).assignments) == 2


def test_assignment_recovery_completes_pending_child_creation_without_provider(
    tmp_path: Path,
) -> None:
    owner, team, member_a, _ = _session_and_team(tmp_path)
    TeamStore(tmp_path).create_assignment(
        team.team_id, ASSIGNMENT_A, member_a.member_id, CHILD_A, "Recover this"
    )
    result = TeamAssignmentService(tmp_path).recover(team.team_id)
    assert len(result.recovered) == 1
    recovered = result.recovered[0]
    assert recovered.phase is TeamAssignmentPhase.CHILD_BOUND
    assert recovered.child is not None
    assert recovered.child.parent_session_id == owner
    assert result.diagnostics == ()


def test_assignment_saga_exposes_partial_child_failure_for_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    _, team, member_a, _ = _session_and_team(tmp_path)
    service_ids = iter([ASSIGNMENT_A, CHILD_A])
    service = TeamAssignmentService(tmp_path, uuid_factory=lambda: next(service_ids))

    def fail_create(*args, **kwargs):
        raise ChildRunStoreError("injected child install failure")

    monkeypatch.setattr(service.children, "create_for_team", fail_create)
    with pytest.raises(TeamAssignmentError, match="requires recovery") as error:
        service.create(team.team_id, member_a.member_id, "Partial")
    assert error.value.assignment_id is not None
    state = TeamStore(tmp_path).inspect(team.team_id)
    assert state.assignments[0].phase is TeamAssignmentPhase.PENDING_CHILD
