from __future__ import annotations

import json

from coquo.child_run_store import ChildRunStore
from coquo.core.action_coordinator import ApprovalResolution
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import ApprovalMode
from coquo.session import ProjectSession
from coquo.session_records import ApprovalAuditOutcome
from coquo.session_store import SessionStore


CONTEXT_ID = "ctx-v21-" + "a" * 64


def spawn_request(tool_use_id: str = "child-spawn-1") -> ToolUse:
    return ToolUse(
        tool_use_id,
        "child_spawn",
        ToolArguments.from_mapping({"objective": "Inspect the failing tests"}),
    )


def test_rejected_delegation_records_decision_but_creates_no_child(tmp_path) -> None:
    with ProjectSession.open(
        tmp_path,
        environment={},
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda _request: ApprovalResolution.REJECT,
    ) as session:
        result = session._dispatch_child_control(spawn_request(), CONTEXT_ID)
        assert result.dispatch.tool_result.is_error
        assert ChildRunStore(tmp_path).list() == ()
        decisions = SessionStore(tmp_path).child_delegation_decisions(session._writer.session_id)
        assert len(decisions) == 1
        assert decisions[0].outcome is ApprovalAuditOutcome.REJECTED
        assert "Inspect the failing tests" not in decisions[0].__repr__()


def test_auto_delegation_persists_parent_and_child_provenance_before_admission(tmp_path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        result = session._dispatch_child_control(spawn_request(), CONTEXT_ID)
        assert not result.dispatch.tool_result.is_error
        payload = json.loads(result.dispatch.tool_result.content)
        child_id = payload["child_run_id"]
        state = ChildRunStore(tmp_path).replay_state(child_id)
        decision = SessionStore(tmp_path).child_delegation_decisions(session._writer.session_id)[0]
        assert decision.outcome is ApprovalAuditOutcome.ACCEPTED
        assert state.delegated is not None
        assert state.delegated.parent_context_id == CONTEXT_ID
        assert state.delegated.parent_tool_use_id == "child-spawn-1"
        assert state.delegated.decision_record_sequence == decision.sequence
        assert state.delegated.decision_sha256 == decision.decision_sha256
        assert state.admitted is not None
    finally:
        session.close()


def test_child_wait_budget_is_reserved_before_observation(tmp_path) -> None:
    with ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO) as session:
        state = session._runtime.turn_state.child_control_state
        state.reserve_wait(30)
        state.reserve_wait(30)
        try:
            state.reserve_wait(1)
        except ValueError as error:
            assert "60" in str(error)
        else:
            raise AssertionError("wait budget should reject the 61st requested second")
