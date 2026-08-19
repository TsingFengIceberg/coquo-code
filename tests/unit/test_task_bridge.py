from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from coquo.child_run_store import ChildRunStore
from coquo.child_run_records import ChildRunStatus
from coquo.child_runtime import build_child_runtime_spec_from_binding
from coquo.core.contracts import AssistantText, ToolTurnLedger, UserMessage
from coquo.core.permissions import ApprovalMode
from coquo.session_records import BindingSnapshot
from coquo.session import ProjectSession
from coquo.session_store import SessionStore
from coquo.task_bridge import (
    TaskBridgeError,
    TaskBridgeOutcome,
    TaskOrchestrationService,
)
from coquo.task_records import StageExecutionTarget, TaskStatus
from coquo.task_store import TaskStore
from coquo.team_records import TeamScheduleOutcome
from coquo.team_schedule import TeamScheduleError, TeamScheduleService
from coquo.team_store import TeamStore


def _owner(tmp_path: Path) -> tuple[str, BindingSnapshot]:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    binding = writer.state.binding
    writer.release()
    return session_id, binding


def _task(tmp_path: Path, owner_session_id: str):
    return TaskStore(tmp_path).create(
        "Coordinate one bounded external stage", owner_session=owner_session_id
    )


def _complete_child(
    tmp_path: Path,
    *,
    info,
    owner_session_id: str,
    binding: BindingSnapshot,
    response: str = "Child completed",
) -> None:
    child_session_id = "62345678-1234-4234-9234-123456789abc"
    execution_id = "72345678-1234-4234-9234-123456789abc"
    store = ChildRunStore(tmp_path)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=owner_session_id,
        child_session_id=child_session_id,
        objective=info.objective,
        binding=binding,
    )
    store.prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=SessionStore(tmp_path),
        binding=binding,
    )
    store.start_execution(
        info.child_run_id,
        child_session_id=child_session_id,
        execution_id=execution_id,
    )
    child_writer = SessionStore(tmp_path).open(child_session_id)
    turn = child_writer.append_turn(
        (UserMessage(info.objective), AssistantText(response)),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    child_writer.release()
    store.finish_completed(
        info.child_run_id,
        execution_id=execution_id,
        session_record_sequence=turn.sequence,
        assistant_text_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
    )


def test_child_bridge_pending_cancel_commit_and_idempotent_observe(tmp_path: Path) -> None:
    owner_session_id, binding = _owner(tmp_path)
    task = _task(tmp_path, owner_session_id)
    bridge = TaskOrchestrationService(tmp_path)

    admitted = bridge.start_child_stage(task.task_id, "Inspect the workspace")
    assert admitted.target is StageExecutionTarget.CHILD
    assert admitted.outcome == TaskBridgeOutcome.PENDING
    child_id = admitted.child.child_run_id
    pending = bridge.observe_child_stage(task.task_id, child_id)
    assert pending.outcome == TaskBridgeOutcome.PENDING
    assert pending.task.status is TaskStatus.STAGE_IN_PROGRESS

    ChildRunStore(tmp_path).request_cancel(child_id, reason="stop")
    failed = bridge.observe_child_stage(task.task_id, child_id)
    assert failed.outcome == TaskBridgeOutcome.FAILED
    assert failed.result_code == "cancelled_before_start"
    failed_record_count = failed.task.record_count
    repeated_failed = bridge.observe_child_stage(task.task_id, child_id)
    assert repeated_failed.outcome == TaskBridgeOutcome.FAILED
    assert repeated_failed.task.record_count == failed_record_count

    task_two = _task(tmp_path, owner_session_id)
    admitted_two = bridge.start_child_stage(task_two.task_id, "Return exact evidence")
    _complete_child(
        tmp_path,
        info=admitted_two.child,
        owner_session_id=owner_session_id,
        binding=binding,
    )
    committed = bridge.observe_child_stage(task_two.task_id)
    assert committed.outcome == TaskBridgeOutcome.COMMITTED
    assert committed.stage.external_evidence_sha256 == committed.handoffs[0].handoff_sha256
    committed_record_count = committed.task.record_count
    repeated_committed = bridge.observe_child_stage(task_two.task_id)
    assert repeated_committed.outcome == TaskBridgeOutcome.COMMITTED
    assert repeated_committed.task.record_count == committed_record_count


def test_child_bridge_rejects_wrong_identity_and_owner(tmp_path: Path) -> None:
    owner_session_id, _binding = _owner(tmp_path)
    task = _task(tmp_path, owner_session_id)
    bridge = TaskOrchestrationService(tmp_path)
    admitted = bridge.start_child_stage(task.task_id, "Inspect")

    with pytest.raises(TaskBridgeError, match="does not match"):
        bridge.observe_child_stage(task.task_id, "42345678-1234-4234-9234-123456789abc")

    other_session_id, _ = _owner(tmp_path)
    assert other_session_id != owner_session_id
    with pytest.raises(TaskBridgeError, match="does not own the Task"):
        bridge.run_child_stage(
            task.task_id,
            admitted.child.child_run_id,
            SimpleNamespace(_writer=SimpleNamespace(session_id=other_session_id)),
        )
    assert bridge.observe_child_stage(task.task_id).outcome == TaskBridgeOutcome.PENDING


def test_team_assignment_bridge_cancel_and_commit(tmp_path: Path) -> None:
    owner_session_id, binding = _owner(tmp_path)
    task = _task(tmp_path, owner_session_id)
    team = TeamStore(tmp_path).create("Bridge Team", owner_session=owner_session_id)
    member = TeamStore(tmp_path).add_member(team.team_id, "Worker")
    bridge = TaskOrchestrationService(tmp_path)

    admitted = bridge.start_team_assignment_stage(
        task.task_id,
        team.team_id,
        member.member_id,
        objective="Inspect assignment",
    )
    assert admitted.target is StageExecutionTarget.TEAM_ASSIGNMENT
    child_id = admitted.assignment.assignment.child_run_id
    ChildRunStore(tmp_path).request_cancel(child_id, reason="stop")
    failed = bridge.observe_team_assignment_stage(task.task_id)
    assert failed.outcome == TaskBridgeOutcome.FAILED
    resumed_after_cancel = bridge.run_team_assignment_stage(
        task.task_id,
        admitted.assignment.assignment.assignment_id,
        SimpleNamespace(_writer=SimpleNamespace(session_id=owner_session_id)),
    )
    assert resumed_after_cancel.outcome == TaskBridgeOutcome.FAILED

    task_two = _task(tmp_path, owner_session_id)
    admitted_two = bridge.start_team_assignment_stage(
        task_two.task_id,
        team.team_id,
        member.member_id,
        objective="Inspect completed assignment",
    )
    _complete_child(
        tmp_path,
        info=admitted_two.assignment.child,
        owner_session_id=owner_session_id,
        binding=binding,
    )
    committed = bridge.observe_team_assignment_stage(task_two.task_id)
    assert committed.outcome == TaskBridgeOutcome.COMMITTED
    assert committed.assignment.assignment.phase.value == "terminal_observed"


def test_team_schedule_bridge_uses_lazy_roster_and_exact_resume(tmp_path: Path) -> None:
    owner_session_id, _binding = _owner(tmp_path)
    task = _task(tmp_path, owner_session_id)
    team = TeamStore(tmp_path).create("Empty Bridge Team", owner_session=owner_session_id)
    bridge = TaskOrchestrationService(tmp_path)

    admitted = bridge.start_team_schedule_stage(task.task_id, team.team_id)
    assert admitted.target is StageExecutionTarget.TEAM_SCHEDULE
    assert admitted.stage.assignment_ids == ()
    schedule_id = admitted.schedule.schedule_run_id

    session = SimpleNamespace(_writer=SimpleNamespace(session_id=owner_session_id))
    completed = bridge.run_team_schedule_stage(task.task_id, schedule_id, session)
    assert completed.outcome == TaskBridgeOutcome.COMMITTED
    assert completed.schedule.outcome is TeamScheduleOutcome.IDLE
    assert completed.stage.assignment_ids == ()

    state = TeamStore(tmp_path).replay_state(team.team_id)
    starts = [
        record
        for record in state.records
        if record.record_type == "team_schedule_started" and record.schedule_run_id == schedule_id
    ]
    finishes = [
        record
        for record in state.records
        if record.record_type == "team_schedule_finished" and record.schedule_run_id == schedule_id
    ]
    assert len(starts) == 1
    assert len(finishes) == 1


def test_team_schedule_resume_requires_exact_nonterminal_identity(tmp_path: Path) -> None:
    owner_session_id, _binding = _owner(tmp_path)
    team = TeamStore(tmp_path).create("Resume Team", owner_session=owner_session_id)
    service = TeamScheduleService(tmp_path)
    run = service.start(team.team_id, max_assignments=1, max_parallel=1)
    run.close()

    resumed = service.resume(team.team_id, run.schedule_run_id)
    assert resumed.schedule_run_id == run.schedule_run_id
    resumed.finish(outcome=TeamScheduleOutcome.IDLE)

    with pytest.raises(TeamScheduleError, match="already terminal"):
        service.resume(team.team_id, run.schedule_run_id)


def test_task_child_bridge_runs_real_child_and_commits_handoff(tmp_path: Path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        task = TaskStore(tmp_path).create(
            "Run a real Child stage", owner_session=session.session_id
        )
        bridge = TaskOrchestrationService(tmp_path)
        admitted = bridge.start_child_stage(task.task_id, "Inspect through the Child runtime")

        committed = bridge.run_child_stage(
            task.task_id,
            admitted.child.child_run_id,
            session,
            background=False,
        )
        assert committed.outcome == TaskBridgeOutcome.COMMITTED
        assert committed.child.status is ChildRunStatus.COMPLETED
        assert len(committed.handoffs) == 1
        assert committed.stage.external_evidence_sha256 == committed.handoffs[0].handoff_sha256

        repeated = bridge.observe_child_stage(task.task_id, admitted.child.child_run_id)
        assert repeated.outcome == TaskBridgeOutcome.COMMITTED
        assert repeated.task.record_count == committed.task.record_count
    finally:
        session.close()


def test_task_team_assignment_bridge_runs_real_team_child_and_commits_handoff(
    tmp_path: Path,
) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        team = session.create_team("Bridge E2E Team")
        member = session.add_team_member(team.team_id, "Inspector")
        task = TaskStore(tmp_path).create(
            "Run a real Team assignment stage", owner_session=session.session_id
        )
        bridge = TaskOrchestrationService(tmp_path)
        admitted = bridge.start_team_assignment_stage(
            task.task_id,
            team.team_id,
            member.member_id,
            objective="Inspect through the Team Child runtime",
        )
        assignment_id = admitted.assignment.assignment.assignment_id

        committed = bridge.run_team_assignment_stage(
            task.task_id,
            assignment_id,
            session,
            background=False,
        )
        assert committed.outcome == TaskBridgeOutcome.COMMITTED
        assert committed.assignment.assignment.phase.value == "terminal_observed"
        assert committed.assignment.child is not None
        assert committed.assignment.child.status is ChildRunStatus.COMPLETED
        assert len(committed.handoffs) == 1
        assert committed.stage.external_evidence_sha256 == committed.handoffs[0].handoff_sha256

        repeated = bridge.observe_team_assignment_stage(task.task_id, assignment_id)
        assert repeated.outcome == TaskBridgeOutcome.COMMITTED
        assert repeated.task.record_count == committed.task.record_count
    finally:
        session.close()
