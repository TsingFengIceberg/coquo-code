from __future__ import annotations

import pytest

from coquo.core.permissions import ApprovalMode
from coquo.session import ProjectSession
from coquo.workflow_orchestration import (
    WorkflowError,
    WorkflowOrchestrator,
    WorkflowPhase,
    WorkflowVerdict,
)


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
