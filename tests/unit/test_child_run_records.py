from __future__ import annotations

import pytest

from coquo.child_run_records import (
    ChildRunAdmitted,
    ChildRunCompleted,
    ChildRunCancelled,
    ChildRunFailed,
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
