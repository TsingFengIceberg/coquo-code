from __future__ import annotations

from types import SimpleNamespace

import pytest

from coquo.core.permissions import ApprovalMode
from coquo.child_run_store import ChildRunStore
from coquo.core.permissions import PermissionMode
from coquo.session import ProjectSession
from coquo.worktree_integration import (
    IntegrationPreflight,
    IntegrationResult,
    WorktreeIntegrationError,
)
from coquo.worktree_records import WorktreeState
from coquo.workflow_orchestration import (
    WorkflowError,
    WorkflowDrivePolicy,
    WorkflowDriveStopReason,
    WorkflowOrchestrator,
    WorkflowPhase,
    WorkflowRole,
    WorkflowVerdict,
    WorkflowStage,
)
from coquo.core.cancellation import TurnCancellation
from coquo.task_records import StageExecutionTarget


class _FakeTeamAssignments:
    def inspect(self, team_id: str, assignment_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            assignment=SimpleNamespace(
                team_id=team_id,
                assignment_id=assignment_id,
                worktree_id="worktree-1",
            )
        )


class _FakeBridge:
    def __init__(self) -> None:
        self.team_assignments = _FakeTeamAssignments()


class _FakeWorktreeStore:
    def __init__(self, state: WorktreeState = WorktreeState.SEALED_CHANGES) -> None:
        self.state = state

    def inspect(self, worktree_id: str) -> SimpleNamespace:
        assert worktree_id == "worktree-1"
        return SimpleNamespace(state=self.state)


class _FakeIntegrationService:
    def __init__(self, *, unknown_on_apply: bool = False) -> None:
        self.store = _FakeWorktreeStore()
        self.worktrees = SimpleNamespace(store=self.store)
        self.prepare_calls = 0
        self.apply_calls = 0
        self.unknown_on_apply = unknown_on_apply

    def prepare(
        self, team_id: str, assignment_id: str, expected_patch_sha256: str
    ) -> IntegrationPreflight:
        self.prepare_calls += 1
        assert (team_id, assignment_id) == ("team-1", "assignment-1")
        return IntegrationPreflight(
            team_id=team_id,
            assignment_id=assignment_id,
            worktree_id="worktree-1",
            patch_sha256=expected_patch_sha256,
            manifest_sha256="b" * 64,
            target_ref="refs/heads/main",
            target_head="c" * 40,
            base_commit="d" * 40,
            changed_paths=1,
            patch_bytes=128,
            precondition_sha256="e" * 64,
        )

    def integrate(self, prepared: IntegrationPreflight, *, action_digest: str) -> IntegrationResult:
        self.apply_calls += 1
        assert prepared.worktree_id == "worktree-1"
        assert action_digest == "f" * 64
        if self.unknown_on_apply:
            self.store.state = WorktreeState.INTEGRATION_UNKNOWN
            raise WorktreeIntegrationError("apply outcome was interrupted")
        self.store.state = WorktreeState.APPLIED
        return IntegrationResult(
            status="applied",
            result_code="applied",
            message="sealed patch applied; authority changes remain uncommitted",
            worktree_id=prepared.worktree_id,
            team_id=prepared.team_id,
            assignment_id=prepared.assignment_id,
            patch_sha256=prepared.patch_sha256,
            manifest_sha256=prepared.manifest_sha256,
            target_ref=prepared.target_ref,
            target_head="1" * 40,
            changed_paths=prepared.changed_paths,
        )


def _writable_workflow(tmp_path, *, integration_service=None):
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    bridge = _FakeBridge()
    integrations = integration_service or _FakeIntegrationService()
    orchestrator = WorkflowOrchestrator(
        tmp_path,
        bridge_service=bridge,
        integration_service=integrations,
    )
    state = orchestrator.start(
        "integrate a reviewed Team patch",
        owner_session=session.session_id,
        acceptance_criteria=("the Host explicitly accepts the applied patch",),
    )
    state = orchestrator.advance(state.workflow_id)
    state = orchestrator.record_exploration(
        state.workflow_id,
        source_id="explorer",
        status="completed",
        summary="bounded exploration evidence",
    )
    executor = WorkflowStage(
        role=WorkflowRole.EXECUTOR,
        task_id=state.task_id,
        target=StageExecutionTarget.TEAM_ASSIGNMENT,
        source_id="assignment-1",
        status="committed",
        team_id="team-1",
        assignment_id="assignment-1",
        handoff_received=True,
    )
    state = orchestrator._replace(state, stages=(executor,))
    state = orchestrator.record_execution(
        state.workflow_id,
        source_id="assignment-1",
        status="committed",
        summary="Team assignment produced a sealed patch",
    )
    state = orchestrator.record_review(
        state.workflow_id,
        source_id="reviewer",
        verdict=WorkflowVerdict.PASSED,
        summary="sealed patch satisfies the review contract",
    )
    return session, orchestrator, integrations, state


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


def test_bounded_workflow_driver_runs_explore_and_execute_then_stops_at_review(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "drive a bounded workflow",
            owner_session=session.session_id,
            acceptance_criteria=("review is still explicit",),
        )
        driven = orchestrator.drive_until_review(
            state.workflow_id,
            session,
            policy=WorkflowDrivePolicy(background=False),
        )
        assert driven.stop_reason is WorkflowDriveStopReason.REVIEW_READY
        assert driven.stages_started == (WorkflowRole.EXPLORER, WorkflowRole.EXECUTOR)
        assert driven.state.phase is WorkflowPhase.REVIEW
        assert driven.state.integration is None
    finally:
        session.close()


def test_bounded_workflow_driver_stops_for_pending_background_stage(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "observe a bounded background workflow",
            owner_session=session.session_id,
            acceptance_criteria=("pending remains visible",),
        )
        driven = orchestrator.drive_until_review(
            state.workflow_id,
            session,
            policy=WorkflowDrivePolicy(background=True),
        )
        assert driven.stop_reason is WorkflowDriveStopReason.PENDING_STAGE
        assert driven.stages_started == (WorkflowRole.EXPLORER,)
        assert driven.state.phase is WorkflowPhase.EXPLORATION
    finally:
        session.close()


def test_bounded_workflow_driver_enforces_stage_limit_and_cancellation(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        orchestrator = WorkflowOrchestrator(tmp_path)
        state = orchestrator.start(
            "stop before executor",
            owner_session=session.session_id,
            acceptance_criteria=("stage limit is honored",),
        )
        limited = orchestrator.drive_until_review(
            state.workflow_id,
            session,
            policy=WorkflowDrivePolicy(max_stages=1, background=False),
        )
        assert limited.stop_reason is WorkflowDriveStopReason.STAGE_LIMIT
        assert limited.state.phase is WorkflowPhase.EXECUTION

        cancelled_state = orchestrator.start(
            "cancel before driving",
            owner_session=session.session_id,
            acceptance_criteria=("cancel is visible",),
        )
        cancellation = TurnCancellation()
        cancellation.request()
        cancelled = orchestrator.drive_until_review(
            cancelled_state.workflow_id,
            session,
            cancellation=cancellation,
        )
        assert cancelled.stop_reason is WorkflowDriveStopReason.CANCELLED
        assert cancelled.state.phase is WorkflowPhase.ARCHITECTURE
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


def test_workflow_team_integration_requires_preflight_and_host_acceptance(tmp_path) -> None:
    session, orchestrator, integrations, state = _writable_workflow(tmp_path)
    try:
        with pytest.raises(WorkflowError, match="explicit integration preflight"):
            orchestrator.accept(state.workflow_id)

        prepared = orchestrator.prepare_integration(
            state.workflow_id,
            expected_patch_sha256="a" * 64,
            permission_mode=PermissionMode.WORKSPACE_WRITE,
            approval_mode=ApprovalMode.AUTO,
        )
        assert prepared is not None
        assert integrations.prepare_calls == 1
        assert orchestrator.inspect(state.workflow_id).integration.status == "preflighted"

        result = orchestrator.integrate(
            state.workflow_id,
            action_digest="f" * 64,
            permission_mode=PermissionMode.WORKSPACE_WRITE,
            approval_mode=ApprovalMode.AUTO,
        )
        assert result is not None and result.status == "applied"
        assert integrations.apply_calls == 1

        completed = orchestrator.accept(state.workflow_id)
        assert completed.phase is WorkflowPhase.COMPLETED
        assert completed.integration is not None
        assert completed.integration.host_accepted is True

        reloaded = WorkflowOrchestrator(
            tmp_path,
            bridge_service=_FakeBridge(),
            integration_service=integrations,
        ).inspect(state.workflow_id)
        assert reloaded.integration == completed.integration
        assert reloaded.integration.host_accepted_at is not None
    finally:
        session.close()


def test_workflow_team_integration_permission_boundary_is_fail_closed(tmp_path) -> None:
    session, orchestrator, integrations, state = _writable_workflow(tmp_path)
    try:
        with pytest.raises(WorkflowError, match="permission denied"):
            orchestrator.prepare_integration(
                state.workflow_id,
                expected_patch_sha256="a" * 64,
                permission_mode=PermissionMode.READ_ONLY,
                approval_mode=ApprovalMode.AUTO,
            )
        with pytest.raises(WorkflowError, match="approval required"):
            orchestrator.prepare_integration(
                state.workflow_id,
                expected_patch_sha256="a" * 64,
                permission_mode=PermissionMode.WORKSPACE_WRITE,
                approval_mode=ApprovalMode.ASK,
            )
        assert integrations.prepare_calls == 0
    finally:
        session.close()


def test_workflow_unknown_integration_enters_recovery_without_second_apply(tmp_path) -> None:
    integrations = _FakeIntegrationService(unknown_on_apply=True)
    session, orchestrator, integrations, state = _writable_workflow(
        tmp_path, integration_service=integrations
    )
    try:
        orchestrator.prepare_integration(
            state.workflow_id,
            expected_patch_sha256="a" * 64,
            permission_mode=PermissionMode.WORKSPACE_WRITE,
            approval_mode=ApprovalMode.AUTO,
        )
        with pytest.raises(WorkflowError, match="outcome is unknown"):
            orchestrator.integrate(
                state.workflow_id,
                action_digest="f" * 64,
                permission_mode=PermissionMode.WORKSPACE_WRITE,
                approval_mode=ApprovalMode.AUTO,
            )
        assert integrations.apply_calls == 1
        current = orchestrator.inspect(state.workflow_id)
        assert current.phase is WorkflowPhase.RECOVERY_REQUIRED
        assert current.integration.status == "recovery-required"

        recovered = orchestrator.recover_integration(state.workflow_id)
        assert recovered.status == "recovery-required"
        assert recovered.result_code == "integration_unknown"
        assert integrations.apply_calls == 1
    finally:
        session.close()
