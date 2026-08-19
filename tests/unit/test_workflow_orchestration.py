from __future__ import annotations

import pytest

from coquo.core.permissions import ApprovalMode
from coquo.child_run_store import ChildRunStore
from coquo.session import ProjectSession
from coquo.workflow_orchestration import (
    WorkflowError,
    WorkflowOrchestrator,
    WorkflowPhase,
    WorkflowRole,
    WorkflowVerdict,
)
from coquo.task_records import StageExecutionTarget


def test_host_owned_workflow_advances_and_persists_untrusted_evidence(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "inspect and verify the fixture",
            owner_session=session.session_id,
            acceptance_criteria=("a bounded review is recorded",),
        )
        assert state.phase is WorkflowPhase.ARCHITECTURE
        assert state.packet.permission_profile.startswith("read-only")

        assert orchestrator.advance(state.workflow_id).phase is WorkflowPhase.EXPLORATION
        state = orchestrator.record_exploration(
            state.workflow_id,
            source_id="child-run-1",
            status="completed",
            summary="bounded read-only evidence",
        )
        assert state.phase is WorkflowPhase.EXECUTION
        assert state.evidence[-1].untrusted is True

        state = orchestrator.record_execution(
            state.workflow_id,
            source_id="task-stage-1",
            status="committed",
            summary="executor stage converged through the Task bridge",
        )
        assert state.phase is WorkflowPhase.REVIEW
        state = orchestrator.record_review(
            state.workflow_id,
            source_id="review-child-1",
            verdict=WorkflowVerdict.PASSED,
            summary="reviewer evidence is sufficient for Host decision",
        )
        assert state.phase is WorkflowPhase.INTEGRATION
        state = orchestrator.accept(state.workflow_id)
        assert state.phase is WorkflowPhase.COMPLETED
        assert orchestrator.inspect(state.workflow_id).revision == state.revision
    finally:
        session.close()


def test_workflow_review_unknown_fails_closed_and_never_auto_integrates(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "review a fixture",
            owner_session=session.session_id,
            acceptance_criteria=("review exists",),
        )
        orchestrator.advance(state.workflow_id)
        orchestrator.record_exploration(
            state.workflow_id,
            source_id="child-run",
            status="completed",
            summary="read-only result",
        )
        orchestrator.record_execution(
            state.workflow_id,
            source_id="executor",
            status="committed",
            summary="task evidence",
        )
        state = orchestrator.record_review(
            state.workflow_id,
            source_id="reviewer",
            verdict=WorkflowVerdict.UNKNOWN,
            summary="review result was lost",
        )
        assert state.phase is WorkflowPhase.RECOVERY_REQUIRED
        with pytest.raises(WorkflowError):
            orchestrator.accept(state.workflow_id)
    finally:
        session.close()


def test_workflow_rejects_phase_skips_and_non_untrusted_evidence(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "phase discipline",
            owner_session=session.session_id,
            acceptance_criteria=("phases are ordered",),
        )
        with pytest.raises(WorkflowError):
            orchestrator.record_execution(
                state.workflow_id,
                source_id="executor",
                status="committed",
                summary="skip explorer",
            )
        with pytest.raises(WorkflowError):
            orchestrator.record_exploration(
                state.workflow_id,
                source_id="child",
                status="completed",
                summary="not yet",
            )
    finally:
        session.close()


def test_workflow_bridge_runs_real_explorer_and_executor_with_durable_identities(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "bridge a bounded workflow",
            owner_session=session.session_id,
            acceptance_criteria=("Explorer and Executor handoffs are observed",),
        )
        orchestrator.advance(state.workflow_id)
        admitted = orchestrator.start_exploration_stage(state.workflow_id)
        assert admitted.target is StageExecutionTarget.CHILD
        assert admitted.child is not None

        explored = orchestrator.run_exploration_stage(state.workflow_id, session, background=False)
        assert explored.outcome == "committed"
        after_explore = orchestrator.inspect(state.workflow_id)
        assert after_explore.phase is WorkflowPhase.EXECUTION
        assert after_explore.stages[-1].child_run_id == admitted.child.child_run_id
        assert after_explore.stages[-1].handoff_received is True
        assert after_explore.stages[-1].untrusted is True

        execution = orchestrator.start_execution_stage(state.workflow_id)
        assert execution.child is not None
        completed = orchestrator.run_execution_stage(state.workflow_id, session, background=False)
        assert completed.outcome == "committed"
        after_execution = orchestrator.inspect(state.workflow_id)
        assert after_execution.phase is WorkflowPhase.REVIEW
        assert len(after_execution.stages) == 2
        assert after_execution.stages[-1].child_run_id == execution.child.child_run_id

        reloaded = WorkflowOrchestrator(tmp_path).inspect(state.workflow_id)
        assert reloaded.revision == after_execution.revision
        assert reloaded.stages == after_execution.stages
    finally:
        session.close()


def test_workflow_bridge_fails_closed_when_external_handoff_is_not_ready(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "observe a background Child",
            owner_session=session.session_id,
            acceptance_criteria=("background identity remains visible",),
        )
        orchestrator.advance(state.workflow_id)
        admitted = orchestrator.start_exploration_stage(state.workflow_id)
        pending = orchestrator.run_exploration_stage(state.workflow_id, session, background=True)
        assert pending.outcome == "pending"
        observed = orchestrator.recover_stage(state.workflow_id, WorkflowRole.EXPLORER)
        assert observed.outcome in {"pending", "committed", "failed"}
        current = orchestrator.inspect(state.workflow_id)
        if observed.outcome == "pending":
            assert current.phase is WorkflowPhase.RECOVERY_REQUIRED
            assert current.stages[-1].child_run_id == admitted.child.child_run_id
    finally:
        session.close()


def test_workflow_recovery_observes_cancelled_child_without_retrying(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "cancel one exact workflow stage",
            owner_session=session.session_id,
            acceptance_criteria=("cancel remains terminal",),
        )
        orchestrator.advance(state.workflow_id)
        admitted = orchestrator.start_exploration_stage(state.workflow_id)
        assert admitted.child is not None
        cancelled = ChildRunStore(tmp_path).request_cancel(
            admitted.child.child_run_id, reason="Host stopped the stage"
        )
        assert cancelled.status.value == "cancelled"

        observed = orchestrator.recover_stage(state.workflow_id, WorkflowRole.EXPLORER)
        assert observed.outcome == "failed"
        current = orchestrator.inspect(state.workflow_id)
        assert current.phase is WorkflowPhase.RECOVERY_REQUIRED
        assert current.stages[-1].child_run_id == admitted.child.child_run_id
        assert current.stages[-1].status == "failed"
        assert current.stages[-1].result_code == "cancelled_before_start"
        assert len(ChildRunStore(tmp_path).list()) == 1
    finally:
        session.close()
