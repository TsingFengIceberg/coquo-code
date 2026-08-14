from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from coquo.child_run_records import (
    ChildRunAdmitted,
    ChildRunCompleted,
    ChildRunCancelled,
    ChildRunDelegated,
    ChildRunTeamAssignment,
    ChildRunFailed,
    ChildRunCancelRequested,
    ChildRunCancelledTerminal,
    ChildRunInterrupted,
    ChildRunHeader,
    ChildRunStarted,
    ChildSessionBound,
    ChildRunRecordError,
    ChildRunStatus,
    decode_child_run_record,
    encode_child_run_record,
    replay_child_run_records,
)
from coquo.child_runtime import child_role_prompt_fingerprint


RUN_ID = "42345678-1234-4234-9234-123456789abc"
SESSION_ID = "52345678-1234-4234-9234-123456789abc"
STAMP = "2026-08-13T10:00:00.000000Z"


def header() -> ChildRunHeader:
    return ChildRunHeader(
        sequence=0,
        child_run_id=RUN_ID,
        workspace="/tmp/workspace",
        workspace_fingerprint="v1-" + "a" * 64,
        parent_session_id=SESSION_ID,
        objective="Inspect the workspace",
        created_at=STAMP,
    )


def test_child_run_header_and_cancel_round_trip() -> None:
    first = header()
    cancelled = ChildRunCancelled(1, RUN_ID, "no longer needed", STAMP)
    assert decode_child_run_record(encode_child_run_record(first)) == first
    assert decode_child_run_record(encode_child_run_record(cancelled)) == cancelled
    assert replay_child_run_records([first]).status is ChildRunStatus.QUEUED
    assert replay_child_run_records([first, cancelled]).status is ChildRunStatus.CANCELLED


def test_model_delegation_prefix_round_trip_and_legacy_admission_compatibility() -> None:
    delegated = ChildRunDelegated(
        sequence=1,
        child_run_id=RUN_ID,
        parent_session_id=SESSION_ID,
        parent_context_id="ctx-v21-" + "a" * 64,
        parent_tool_use_id="child-tool-1",
        decision_record_sequence=1,
        decision_sha256="b" * 64,
        depth=1,
        source="model",
        delegated_at=STAMP,
    )
    state = replay_child_run_records([header(), delegated])
    assert state.delegated == delegated
    assert state.status is ChildRunStatus.QUEUED
    assert decode_child_run_record(encode_child_run_record(delegated)) == delegated
    cancelled = ChildRunCancelled(2, RUN_ID, "stop", STAMP)
    assert replay_child_run_records([header(), delegated, cancelled]).status is (
        ChildRunStatus.CANCELLED
    )


def test_team_assignment_prefix_round_trip_and_digest_binding() -> None:
    origin = ChildRunTeamAssignment(
        sequence=1,
        child_run_id=RUN_ID,
        parent_session_id=SESSION_ID,
        team_id="62345678-1234-4234-9234-123456789abc",
        member_id="72345678-1234-4234-9234-123456789abc",
        assignment_id="82345678-1234-4234-9234-123456789abc",
        objective_sha256=hashlib.sha256(header().objective.encode("utf-8")).hexdigest(),
        assigned_at=STAMP,
    )
    state = replay_child_run_records([header(), origin])
    assert state.team_assignment == origin
    assert state.delegated is None
    assert decode_child_run_record(encode_child_run_record(origin)) == origin
    with pytest.raises(ChildRunRecordError, match="objective"):
        replay_child_run_records([header(), replace(origin, objective_sha256="a" * 64)])


def test_child_run_delegation_must_be_exactly_between_header_and_admission() -> None:
    delegated = ChildRunDelegated(
        1,
        RUN_ID,
        SESSION_ID,
        "ctx-v21-" + "a" * 64,
        "child-tool-1",
        1,
        "b" * 64,
        1,
        "model",
        STAMP,
    )
    with pytest.raises(ChildRunRecordError, match="owner"):
        replay_child_run_records([header(), replace(delegated, parent_session_id=RUN_ID)])


@pytest.mark.parametrize(
    "records",
    [
        [ChildRunCancelled(0, RUN_ID, "bad", STAMP)],
        [header(), ChildRunCancelled(2, RUN_ID, "bad", STAMP)],
        [header(), ChildRunCancelled(1, SESSION_ID, "bad", STAMP)],
        [header(), ChildRunCancelled(1, RUN_ID, "bad", "2026-08-12T10:00:00.000000Z")],
        [
            header(),
            ChildRunCancelled(1, RUN_ID, "bad", STAMP),
            ChildRunCancelled(2, RUN_ID, "third", STAMP),
        ],
    ],
)
def test_child_run_replay_rejects_invalid_lifecycle(records) -> None:
    with pytest.raises(ChildRunRecordError):
        replay_child_run_records(records)


def test_child_run_record_has_closed_fields() -> None:
    raw = encode_child_run_record(header()).replace(
        b'"objective":"Inspect the workspace"', b'"extra":1,"objective":"Inspect the workspace"'
    )
    with pytest.raises(ChildRunRecordError):
        decode_child_run_record(raw)


def test_child_run_admission_and_session_binding_replay() -> None:
    admitted = ChildRunAdmitted(
        sequence=1,
        child_run_id=RUN_ID,
        parent_session_id=SESSION_ID,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        permission_mode="read-only",
        approval_mode="auto",
        provider_binding={"mode": "fake", "route_fingerprint": "a" * 64},
        tool_registry_id="registry-v1-" + "b" * 64,
        tool_registry_generation=6,
        tool_set_id="toolset-v1-" + "c" * 64,
        tool_names=("read_file",),
        role_contract_version=1,
        role_prompt_fingerprint=child_role_prompt_fingerprint(),
        max_provider_invocations=24,
        max_tool_requests=32,
        max_output_tokens=1024,
        deadline_seconds=300,
        admitted_at=STAMP,
    )
    bound = ChildSessionBound(
        sequence=2,
        child_run_id=RUN_ID,
        child_session_id=admitted.child_session_id,
        session_header_sequence=0,
        session_path="/tmp/workspace/.coquo/sessions/child.jsonl",
        bound_at=STAMP,
    )
    state = replay_child_run_records([header(), admitted, bound])
    assert state.status is ChildRunStatus.READY
    assert decode_child_run_record(encode_child_run_record(admitted)) == admitted
    assert decode_child_run_record(encode_child_run_record(bound)) == bound


def test_child_run_started_completed_replay_and_round_trip() -> None:
    admitted = ChildRunAdmitted(
        sequence=1,
        child_run_id=RUN_ID,
        parent_session_id=SESSION_ID,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        permission_mode="read-only",
        approval_mode="auto",
        provider_binding={"mode": "fake", "route_fingerprint": "a" * 64},
        tool_registry_id="registry-v1-" + "b" * 64,
        tool_registry_generation=6,
        tool_set_id="toolset-v1-" + "c" * 64,
        tool_names=("read_file",),
        role_contract_version=1,
        role_prompt_fingerprint=child_role_prompt_fingerprint(),
        max_provider_invocations=24,
        max_tool_requests=32,
        max_output_tokens=1024,
        deadline_seconds=300,
        admitted_at=STAMP,
    )
    bound = ChildSessionBound(
        sequence=2,
        child_run_id=RUN_ID,
        child_session_id=admitted.child_session_id,
        session_header_sequence=0,
        session_path="/tmp/workspace/.coquo/sessions/child.jsonl",
        bound_at=STAMP,
    )
    started = ChildRunStarted(
        sequence=3,
        child_run_id=RUN_ID,
        child_session_id=admitted.child_session_id,
        execution_id="72345678-1234-4234-9234-123456789abc",
        started_at=STAMP,
    )
    completed = ChildRunCompleted(
        sequence=4,
        child_run_id=RUN_ID,
        execution_id=started.execution_id,
        session_record_sequence=2,
        assistant_text_sha256="d" * 64,
        completed_at=STAMP,
    )
    state = replay_child_run_records([header(), admitted, bound, started, completed])
    assert state.status is ChildRunStatus.COMPLETED
    assert decode_child_run_record(encode_child_run_record(started)) == started
    assert decode_child_run_record(encode_child_run_record(completed)) == completed


def test_child_run_failed_before_and_after_start_replay() -> None:
    admitted = ChildRunAdmitted(
        sequence=1,
        child_run_id=RUN_ID,
        parent_session_id=SESSION_ID,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        permission_mode="read-only",
        approval_mode="auto",
        provider_binding={"mode": "fake", "route_fingerprint": "a" * 64},
        tool_registry_id="registry-v1-" + "b" * 64,
        tool_registry_generation=6,
        tool_set_id="toolset-v1-" + "c" * 64,
        tool_names=("read_file",),
        role_contract_version=1,
        role_prompt_fingerprint=child_role_prompt_fingerprint(),
        max_provider_invocations=24,
        max_tool_requests=32,
        max_output_tokens=1024,
        deadline_seconds=300,
        admitted_at=STAMP,
    )
    failed = ChildRunFailed(
        sequence=2,
        child_run_id=RUN_ID,
        execution_id=None,
        phase="pre_start",
        result_code="child_execution_failed",
        message="route changed",
        failed_at=STAMP,
    )
    assert replay_child_run_records([header(), admitted, failed]).status is ChildRunStatus.FAILED


def test_child_run_cancellation_request_and_terminal_replay() -> None:
    admitted = ChildRunAdmitted(
        sequence=1,
        child_run_id=RUN_ID,
        parent_session_id=SESSION_ID,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        permission_mode="read-only",
        approval_mode="auto",
        provider_binding={"mode": "fake", "route_fingerprint": "a" * 64},
        tool_registry_id="registry-v1-" + "b" * 64,
        tool_registry_generation=6,
        tool_set_id="toolset-v1-" + "c" * 64,
        tool_names=("read_file",),
        role_contract_version=1,
        role_prompt_fingerprint=child_role_prompt_fingerprint(),
        max_provider_invocations=24,
        max_tool_requests=32,
        max_output_tokens=1024,
        deadline_seconds=300,
        admitted_at=STAMP,
    )
    bound = ChildSessionBound(2, RUN_ID, admitted.child_session_id, 0, "/tmp/child.jsonl", STAMP)
    request = ChildRunCancelRequested(
        3,
        RUN_ID,
        None,
        "no longer needed",
        "host",
        STAMP,
    )
    terminal = ChildRunCancelledTerminal(4, RUN_ID, None, 3, "cancelled", STAMP)
    state = replay_child_run_records([header(), admitted, bound, request, terminal])
    assert state.status is ChildRunStatus.CANCELLED
    assert decode_child_run_record(encode_child_run_record(request)) == request
    assert decode_child_run_record(encode_child_run_record(terminal)) == terminal


def test_child_run_cancelling_and_interrupted_replay() -> None:
    started = ChildRunStarted(
        3,
        RUN_ID,
        "62345678-1234-4234-9234-123456789abc",
        "72345678-1234-4234-9234-123456789abc",
        STAMP,
    )
    request = ChildRunCancelRequested(4, RUN_ID, started.execution_id, "stop", "host", STAMP)
    state = replay_child_run_records(
        [
            header(),
            ChildRunAdmitted(
                1,
                RUN_ID,
                SESSION_ID,
                started.child_session_id,
                "read-only",
                "auto",
                {"mode": "fake", "route_fingerprint": "a" * 64},
                "registry-v1-" + "b" * 64,
                6,
                "toolset-v1-" + "c" * 64,
                ("read_file",),
                1,
                child_role_prompt_fingerprint(),
                24,
                32,
                1024,
                300,
                STAMP,
            ),
            ChildSessionBound(2, RUN_ID, started.child_session_id, 0, "/tmp/child.jsonl", STAMP),
            started,
            request,
        ]
    )
    assert state.status is ChildRunStatus.CANCELLING
    interrupted = ChildRunInterrupted(
        5, RUN_ID, started.execution_id, "cancelling", "v2", "execution_abandoned", STAMP
    )
    assert (
        replay_child_run_records([*state.records, interrupted]).status is ChildRunStatus.INTERRUPTED
    )
