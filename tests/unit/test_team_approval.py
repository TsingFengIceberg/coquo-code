from __future__ import annotations

import pytest

from coquo.core.permissions import ApprovalMode
from coquo.core.team_approval import (
    TeamControlApprovalIdentity,
    TeamControlApprovalPreview,
    TeamControlApprovalRequest,
    canonical_team_arguments_sha256,
    team_control_decision_sha256,
)
from coquo.tools.team_control import parse_team_control
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.session_records import (
    ApprovalAuditOutcome,
    BindingSnapshot,
    TeamControlDecided,
    TeamMessageDeliveredToParent,
    decode_record,
    encode_record,
    replay_records,
)
from coquo.session_store import SessionStore, SessionStoreError


PARENT = "12345678-1234-4234-9234-123456789abc"
TEAM = "22345678-1234-4234-9234-123456789abc"
RUN = "32345678-1234-4234-9234-123456789abc"
MESSAGE = "42345678-1234-4234-9234-123456789abc"
ASSIGNMENT = "52345678-1234-4234-9234-123456789abc"
CHILD = "62345678-1234-4234-9234-123456789abc"


def schedule_request() -> object:
    return parse_team_control(
        ToolUse(
            "team-schedule-1",
            "team_schedule_start",
            ToolArguments.from_mapping({"team_id": TEAM, "max_assignments": 4, "max_parallel": 2}),
        )
    )


def identity(
    *, route: str = "route-v1-a", arguments: str | None = None
) -> TeamControlApprovalIdentity:
    request = schedule_request()
    return TeamControlApprovalIdentity(
        parent_session_id=PARENT,
        context_id="ctx-v25-" + "a" * 64,
        tool_use_id="team-schedule-1",
        control_name=request.name,
        canonical_arguments_sha256=arguments or canonical_team_arguments_sha256(request),
        target_or_preallocated_team_id=TEAM,
        approval_mode=ApprovalMode.ASK,
        schedule_run_id=RUN,
        route_fingerprint=route,
        child_tool_set_id="toolset-v1-" + "b" * 64,
        max_assignments=4,
        max_parallel=2,
        per_child_provider_invocations=2,
        per_child_tool_requests=3,
        per_child_output_tokens=2048,
        per_child_deadline_seconds=30,
    )


def test_team_approval_identity_binds_every_schedule_cost_coordinate() -> None:
    first = identity()
    assert first.digest != identity(route="route-v1-b").digest
    assert first.digest != identity(arguments="c" * 64).digest
    assert team_control_decision_sha256(first, "accepted") != team_control_decision_sha256(
        first, "rejected"
    )
    with pytest.raises(ValueError):
        team_control_decision_sha256(first, "unknown")


def test_team_approval_preview_must_match_identity_without_audit_body() -> None:
    approval = TeamControlApprovalRequest(
        identity(),
        TeamControlApprovalPreview(
            control_name="team_schedule_start",
            team_id=TEAM,
            summary="Start one bounded schedule",
            provider_id="fake",
            model="fake-model",
            child_tool_names=("read_file", "glob", "grep"),
            max_assignments=4,
            max_parallel=2,
            per_child_provider_invocations=2,
            per_child_tool_requests=3,
            per_child_output_tokens=2048,
            per_child_deadline_seconds=30,
        ),
    )
    assert approval.identity.target_or_preallocated_team_id == approval.preview.team_id
    assert "secret" not in repr(approval.identity)
    with pytest.raises(ValueError):
        TeamControlApprovalRequest(
            identity(),
            TeamControlApprovalPreview(
                control_name="team_close", team_id=TEAM, summary="wrong control"
            ),
        )


def test_team_audit_records_round_trip_replay_and_remain_content_free(tmp_path) -> None:
    store = SessionStore(tmp_path)
    writer = store.create(BindingSnapshot.fake())
    decision = TeamControlDecided(
        sequence=1,
        occurred_at=writer.now(),
        parent_session_id=writer.session_id,
        context_id="ctx-v25-" + "a" * 64,
        tool_use_id="team-create-1",
        control_name="team_create",
        target_team_id=TEAM,
        team_control_identity_sha256="a" * 64,
        canonical_arguments_sha256="b" * 64,
        approval_mode=ApprovalMode.ASK,
        outcome=ApprovalAuditOutcome.ACCEPTED,
        decision_sha256="c" * 64,
    )
    delivery = TeamMessageDeliveredToParent(
        sequence=2,
        occurred_at=writer.now(),
        parent_session_id=writer.session_id,
        context_id=decision.context_id,
        tool_use_id="team-message-show-1",
        team_id=TEAM,
        message_id=MESSAGE,
        body_sha256="d" * 64,
        source_assignment_id=ASSIGNMENT,
        source_child_session_id=CHILD,
        source_child_turn_sequence=4,
        source_handoff_sha256="e" * 64,
    )
    assert decode_record(encode_record(decision)) == decision
    assert decode_record(encode_record(delivery)) == delivery
    state = replay_records([writer.state.header, decision, delivery])
    assert state.team_control_decisions == (decision,)
    assert state.team_message_deliveries == (delivery,)
    assert b"objective" not in encode_record(decision)
    assert b'"body":' not in encode_record(delivery)
    writer.team_control_decided(decision)
    writer.team_message_delivered_to_parent(
        context_id=delivery.context_id,
        tool_use_id=delivery.tool_use_id,
        team_id=delivery.team_id,
        message_id=delivery.message_id,
        body_sha256=delivery.body_sha256,
        source_assignment_id=delivery.source_assignment_id,
        source_child_session_id=delivery.source_child_session_id,
        source_child_turn_sequence=delivery.source_child_turn_sequence,
        source_handoff_sha256=delivery.source_handoff_sha256,
    )
    writer.release()
    assert len(store.team_control_decisions(writer.session_id)) == 1
    assert len(store.team_message_deliveries(writer.session_id)) == 1
    conflict_writer = store.open_for_audit(writer.session_id)
    try:
        with pytest.raises(SessionStoreError, match="already decided differently"):
            conflict_writer.team_control_decided(
                TeamControlDecided(
                    **{
                        **decision.__dict__,
                        "sequence": conflict_writer.state.next_sequence,
                        "decision_sha256": "f" * 64,
                    }
                )
            )
    finally:
        conflict_writer.release()
