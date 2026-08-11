from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from coquo.core.hook_contracts import (
    HookAuditEntry,
    HookAuditLedger,
    HookEffect,
    HookEvent,
)
from coquo.session_records import workspace_fingerprint
from coquo.task_records import (
    MAX_ACCEPTANCE_CRITERIA,
    MAX_TASK_OBJECTIVE_CHARACTERS,
    AcceptanceCriterionKind,
    AcceptancePathType,
    CompletionProposalSource,
    ReflectionRecommendation,
    StageCommitted,
    StageFailed,
    StageFailureReason,
    StageKind,
    StageStarted,
    StageUsage,
    TaskArchived,
    TaskBlockerCategory,
    TaskBlockerRecorded,
    TaskAcceptanceContract,
    TaskAcceptanceCriterion,
    TaskAdmissionOrigin,
    TaskCompletionPolicy,
    TaskCompletionProposed,
    TaskHeader,
    TaskReflectionRecorded,
    TaskPlanAccepted,
    TaskPlanProposed,
    TaskRecordError,
    TaskRenamed,
    TaskStatus,
    TaskTerminalOutcome,
    TaskTerminated,
    canonical_acceptance_criteria,
    canonical_task_id,
    canonical_task_objective,
    decode_task_record,
    encode_task_record,
    replay_task_records,
)

TASK_ID = "12345678-1234-4234-9234-123456789abc"
SESSION_ID = "22345678-1234-4234-9234-123456789abc"
CREATED_AT = "2026-07-31T01:02:03.000004Z"
STAGE_ID = "32345678-1234-4234-9234-123456789abc"
PLAN_ID = "42345678-1234-4234-9234-123456789abc"
OTHER_PLAN_ID = "52345678-1234-4234-9234-123456789abc"


def header(workspace: Path) -> TaskHeader:
    return TaskHeader(
        sequence=0,
        task_id=TASK_ID,
        workspace=str(workspace),
        workspace_fingerprint=workspace_fingerprint(workspace),
        owner_session_id=SESSION_ID,
        objective="Implement durable task state",
        acceptance_criteria=("Task survives process restart", "No provider call is made"),
        created_at=CREATED_AT,
    )


def test_task_header_round_trips_as_closed_canonical_json(tmp_path: Path) -> None:
    record = header(tmp_path)

    payload = encode_task_record(record)

    assert payload.endswith(b"\n")
    assert decode_task_record(payload.removesuffix(b"\n")) == record
    assert json.loads(payload)["scope"] == "workspace"


def test_task_admission_origin_round_trips_and_must_immediately_follow_header(
    tmp_path: Path,
) -> None:
    origin = TaskAdmissionOrigin(
        sequence=1,
        admission_id="tap-v1-" + "a" * 64,
        proposal_sha256="b" * 64,
        configuration_sha256="c" * 64,
        confirmation_sha256="d" * 64,
        source_session_id=SESSION_ID,
        source_turn_record_sequence=1,
        proposal_tool_use_id="admission-1",
        source_context_id="ctx-v5-" + "e" * 64,
        recorded_at="2026-07-31T01:02:04.000000Z",
    )

    assert decode_task_record(encode_task_record(origin).rstrip(b"\n")) == origin
    assert replay(tmp_path, [header(tmp_path), origin]).admission_origin == origin

    with pytest.raises(TaskRecordError, match="source must match the owner Session"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                replace(origin, source_session_id="62345678-1234-4234-9234-123456789abc"),
            ],
        )
    with pytest.raises(TaskRecordError, match="immediately after header"):
        replay(
            tmp_path,
            [header(tmp_path), origin, replace(origin, sequence=2)],
        )


def test_structured_acceptance_contract_round_trips_and_legacy_header_maps_in_memory(
    tmp_path: Path,
) -> None:
    legacy = replay(tmp_path, [header(tmp_path)])
    assert [criterion.kind for criterion in legacy.criteria] == [
        AcceptanceCriterionKind.HUMAN,
        AcceptanceCriterionKind.HUMAN,
    ]
    assert legacy.completion_policy is TaskCompletionPolicy.MANUAL

    contract = TaskAcceptanceContract(
        sequence=1,
        criteria=(
            TaskAcceptanceCriterion(
                AcceptanceCriterionKind.PATH_EXISTS,
                "Task survives process restart",
                path="result.txt",
                path_type=AcceptancePathType.FILE,
            ),
            TaskAcceptanceCriterion(
                AcceptanceCriterionKind.INDEPENDENT_REVIEWER,
                "No provider call is made",
                review_paths=("result.txt",),
            ),
        ),
        completion_policy=TaskCompletionPolicy.AUTO_VERIFIED,
        configured_at="2026-07-31T01:02:04.000000Z",
    )

    assert decode_task_record(encode_task_record(contract).rstrip(b"\n")) == contract
    state = replay(tmp_path, [header(tmp_path), contract])
    assert state.criteria == contract.criteria
    assert state.completion_policy is TaskCompletionPolicy.AUTO_VERIFIED

    malformed = json.loads(encode_task_record(contract))
    malformed["criteria"][0]["extra"] = True
    with pytest.raises(TaskRecordError, match="unknown or missing"):
        decode_task_record(json.dumps(malformed).encode())


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("record_type", "task_created", "unknown task record type"),
        ("schema_version", 2, "unsupported task_header schema version"),
        ("scope", "module", "unsupported task scope"),
        ("sequence", 1, "sequence must be 0"),
    ],
)
def test_task_header_decode_rejects_unknown_contract_values(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    document = json.loads(encode_task_record(header(tmp_path)))
    document[field] = value

    with pytest.raises(TaskRecordError, match=error):
        decode_task_record(json.dumps(document).encode())


def test_task_header_decode_rejects_unknown_and_missing_fields(tmp_path: Path) -> None:
    document = json.loads(encode_task_record(header(tmp_path)))
    document["unexpected"] = True
    with pytest.raises(TaskRecordError, match="unknown or missing"):
        decode_task_record(json.dumps(document).encode())

    document.pop("unexpected")
    document.pop("objective")
    with pytest.raises(TaskRecordError, match="unknown or missing"):
        decode_task_record(json.dumps(document).encode())


def test_task_identity_and_text_bounds_are_strict() -> None:
    assert canonical_task_id(TASK_ID) == TASK_ID
    with pytest.raises(TaskRecordError, match="lowercase UUID4"):
        canonical_task_id(TASK_ID.upper())
    with pytest.raises(TaskRecordError, match="nonblank"):
        canonical_task_objective(" \n")
    with pytest.raises(TaskRecordError, match="exceeds"):
        canonical_task_objective("x" * (MAX_TASK_OBJECTIVE_CHARACTERS + 1))
    with pytest.raises(TaskRecordError, match="duplicates"):
        canonical_acceptance_criteria(("same", "same"))
    with pytest.raises(TaskRecordError, match="item limit"):
        canonical_acceptance_criteria(
            tuple(str(index) for index in range(MAX_ACCEPTANCE_CRITERIA + 1))
        )


def test_replay_derives_ready_state_and_rejects_binding_mismatch(tmp_path: Path) -> None:
    record = header(tmp_path)
    state = replay_task_records(
        [record],
        expected_workspace=str(tmp_path),
        expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
        expected_task_id=TASK_ID,
        expected_file_name=f"{TASK_ID}.jsonl",
    )

    assert state.status is TaskStatus.READY
    assert state.next_sequence == 1

    with pytest.raises(TaskRecordError, match="current workspace"):
        replay_task_records(
            [record],
            expected_workspace=str(tmp_path / "other"),
            expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
            expected_task_id=TASK_ID,
            expected_file_name=f"{TASK_ID}.jsonl",
        )
    with pytest.raises(TaskRecordError, match="does not match its task ID"):
        replay_task_records(
            [record],
            expected_workspace=str(tmp_path),
            expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
            expected_task_id=TASK_ID,
            expected_file_name="wrong.jsonl",
        )


def test_encode_revalidates_immutable_record_values(tmp_path: Path) -> None:
    with pytest.raises(TaskRecordError, match="owner Session ID"):
        encode_task_record(replace(header(tmp_path), owner_session_id="not-a-session"))
    with pytest.raises(TaskRecordError, match="canonical UTC timestamp"):
        encode_task_record(replace(header(tmp_path), created_at="2026-07-31"))
    with pytest.raises(TaskRecordError, match="canonical UTC timestamp"):
        encode_task_record(replace(header(tmp_path), created_at="2026-99-31T01:02:03.000004Z"))
    with pytest.raises(TaskRecordError, match="unsupported task scope"):
        encode_task_record(replace(header(tmp_path), scope="workspace"))  # type: ignore[arg-type]


def started(sequence: int = 1, stage_number: int = 1) -> StageStarted:
    return StageStarted(
        sequence=sequence,
        stage_id=STAGE_ID,
        stage_number=stage_number,
        session_id=SESSION_ID,
        objective="Implement one bounded Stage",
        started_at="2026-07-31T01:03:00.000000Z",
    )


def hook_audit(event: HookEvent) -> HookAuditLedger:
    return HookAuditLedger(
        (
            HookAuditEntry(
                event=event,
                hook_set_id="hooks-v2-" + "a" * 64,
                subject_id=TASK_ID,
                matches=(),
                result=HookEffect.CONTINUE,
            ),
        )
    )


def committed(sequence: int = 2, stage_number: int = 1) -> StageCommitted:
    return StageCommitted(
        sequence=sequence,
        stage_id=STAGE_ID,
        stage_number=stage_number,
        session_id=SESSION_ID,
        turn_number=1,
        turn_record_sequence=1,
        turn_record_sha256="a" * 64,
        committed_at="2026-07-31T01:04:00.000000Z",
    )


def replay(workspace: Path, records) -> object:
    return replay_task_records(
        records,
        expected_workspace=str(workspace),
        expected_workspace_fingerprint=workspace_fingerprint(workspace),
        expected_task_id=TASK_ID,
        expected_file_name=f"{TASK_ID}.jsonl",
    )


def test_stage_records_round_trip_and_derive_interrupted_paused_and_blocked(
    tmp_path: Path,
) -> None:
    start = started()
    commit = committed()
    failure = StageFailed(
        sequence=2,
        stage_id=STAGE_ID,
        stage_number=1,
        reason=StageFailureReason.PROVIDER_ERROR,
        failed_at="2026-07-31T01:04:00.000000Z",
        usage=StageUsage(1, 100, 10, 1, 0, 2, 2, 1, 1, 0),
    )

    assert decode_task_record(encode_task_record(start).rstrip(b"\n")) == start
    assert decode_task_record(encode_task_record(commit).rstrip(b"\n")) == commit
    assert decode_task_record(encode_task_record(failure).rstrip(b"\n")) == failure
    assert replay(tmp_path, [header(tmp_path), start]).status is TaskStatus.INTERRUPTED
    assert replay(tmp_path, [header(tmp_path), start, commit]).status is TaskStatus.PAUSED
    assert replay(tmp_path, [header(tmp_path), start, failure]).status is TaskStatus.BLOCKED


def test_current_stage_records_round_trip_strict_hook_audit_and_legacy_is_empty() -> None:
    start = replace(started(), hook_audit=hook_audit(HookEvent.TASK_STAGE_STARTED))
    commit = replace(committed(), hook_audit=hook_audit(HookEvent.TASK_STAGE_COMMITTED))
    failure = StageFailed(
        sequence=2,
        stage_id=STAGE_ID,
        stage_number=1,
        reason=StageFailureReason.PROVIDER_ERROR,
        failed_at="2026-07-31T01:04:00.000000Z",
        hook_audit=hook_audit(HookEvent.TASK_STAGE_FAILED),
    )

    for record in (start, commit, failure):
        assert decode_task_record(encode_task_record(record).rstrip(b"\n")) == record
    with pytest.raises(TaskRecordError, match="Hook audit event"):
        encode_task_record(replace(start, hook_audit=hook_audit(HookEvent.TASK_STAGE_FAILED)))
    with pytest.raises(TaskRecordError, match="legacy stage_started"):
        encode_task_record(replace(start, schema_version=2))


def test_current_blocker_and_terminal_records_round_trip_strict_hook_audit() -> None:
    blocker = TaskBlockerRecorded(
        sequence=3,
        stage_id=STAGE_ID,
        stage_number=1,
        category=TaskBlockerCategory.INFORMATION,
        summary="Need input",
        proposal_tool_use_id="tool-1",
        recorded_at="2026-07-31T01:05:00.000000Z",
        hook_audit=hook_audit(HookEvent.TASK_BLOCKED),
    )
    terminal = TaskTerminated(
        sequence=4,
        outcome=TaskTerminalOutcome.FAILED,
        reason="Cannot continue",
        terminated_at="2026-07-31T01:06:00.000000Z",
        hook_audit=hook_audit(HookEvent.TASK_TERMINATED),
    )

    assert decode_task_record(encode_task_record(blocker).rstrip(b"\n")) == blocker
    assert decode_task_record(encode_task_record(terminal).rstrip(b"\n")) == terminal
    with pytest.raises(TaskRecordError, match="Hook audit event"):
        encode_task_record(replace(blocker, hook_audit=hook_audit(HookEvent.TASK_TERMINATED)))


def test_legacy_stage_v1_records_replay_without_transcript_rewrite(tmp_path: Path) -> None:
    legacy_start = replace(started(), schema_version=1)
    legacy_commit = replace(committed(), schema_version=1)
    legacy_failure = StageFailed(
        sequence=2,
        stage_id=STAGE_ID,
        stage_number=1,
        reason=StageFailureReason.PROVIDER_ERROR,
        failed_at="2026-07-31T01:04:00.000000Z",
        schema_version=1,
    )

    decoded_start = decode_task_record(encode_task_record(legacy_start).rstrip(b"\n"))
    decoded_commit = decode_task_record(encode_task_record(legacy_commit).rstrip(b"\n"))
    decoded_failure = decode_task_record(encode_task_record(legacy_failure).rstrip(b"\n"))
    state = replay(tmp_path, [header(tmp_path), decoded_start, decoded_commit])

    assert decoded_start.schema_version == 1
    assert decoded_start.prompt_sha256 is None
    assert decoded_commit.schema_version == 1
    assert decoded_commit.usage is None
    assert decoded_failure.schema_version == 1
    assert decoded_failure.usage is None
    assert state.status is TaskStatus.PAUSED


def test_stage_replay_requires_contiguous_alternating_identity(tmp_path: Path) -> None:
    with pytest.raises(TaskRecordError, match="before the active Stage terminates"):
        replay(tmp_path, [header(tmp_path), started(), replace(started(), sequence=2)])
    with pytest.raises(TaskRecordError, match="terminal ID"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                started(),
                replace(committed(), stage_id="42345678-1234-4234-9234-123456789abc"),
            ],
        )
    with pytest.raises(TaskRecordError, match="contiguous"):
        replay(tmp_path, [header(tmp_path), started(stage_number=2)])
    with pytest.raises(TaskRecordError, match="sequence must be 1"):
        replay(tmp_path, [header(tmp_path), started(sequence=2)])


def test_stage_replay_rejects_wrong_owner_invalid_evidence_and_time_regression(
    tmp_path: Path,
) -> None:
    with pytest.raises(TaskRecordError, match="owner Session"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                replace(started(), session_id="42345678-1234-4234-9234-123456789abc"),
            ],
        )
    with pytest.raises(TaskRecordError, match="SHA-256"):
        encode_task_record(replace(committed(), turn_record_sha256="short"))
    with pytest.raises(TaskRecordError, match="nondecreasing"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                replace(started(), started_at="2026-07-31T00:00:00.000000Z"),
            ],
        )


def test_replay_rejects_unimplemented_stage_kind_and_metadata_inside_active_stage(
    tmp_path: Path,
) -> None:
    document = json.loads(encode_task_record(started()))
    document["kind"] = "verification"
    with pytest.raises(TaskRecordError, match="unsupported Stage kind"):
        decode_task_record(json.dumps(document).encode())

    with pytest.raises(TaskRecordError, match="metadata cannot advance"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                started(),
                TaskRenamed(
                    sequence=2,
                    name="Renamed too early",
                    renamed_at="2026-07-31T01:03:30.000000Z",
                ),
            ],
        )
    with pytest.raises(TaskRecordError, match="metadata cannot advance"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                started(),
                TaskArchived(
                    sequence=2,
                    archived=True,
                    changed_at="2026-07-31T01:03:30.000000Z",
                ),
            ],
        )


def test_replay_rejects_overcommitted_duplicate_or_reaccepted_plan(tmp_path: Path) -> None:
    planning_start = replace(started(), kind=StageKind.PLANNING)
    planning_commit = committed()
    overcommitted = TaskPlanProposed(
        sequence=3,
        plan_id=PLAN_ID,
        stage_id=STAGE_ID,
        stage_number=1,
        steps=tuple(f"Step {index}" for index in range(32)),
        proposed_at="2026-07-31T01:05:00.000000Z",
    )
    with pytest.raises(TaskRecordError, match="remaining cumulative Stage budget"):
        replay(tmp_path, [header(tmp_path), planning_start, planning_commit, overcommitted])

    proposal = replace(overcommitted, steps=("One bounded step",))
    duplicate = replace(proposal, sequence=4, plan_id=OTHER_PLAN_ID)
    with pytest.raises(TaskRecordError, match="may propose a plan only once"):
        replay(
            tmp_path,
            [header(tmp_path), planning_start, planning_commit, proposal, duplicate],
        )

    accepted = TaskPlanAccepted(
        sequence=4,
        plan_id=PLAN_ID,
        accepted_at="2026-07-31T01:06:00.000000Z",
    )
    with pytest.raises(TaskRecordError, match="already accepted"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                planning_start,
                planning_commit,
                proposal,
                accepted,
                replace(accepted, sequence=5),
            ],
        )


def test_legacy_plan_proposal_v1_remains_readable_without_revision_fields() -> None:
    record = TaskPlanProposed(
        sequence=3,
        plan_id=PLAN_ID,
        stage_id=STAGE_ID,
        stage_number=1,
        steps=("One legacy step",),
        proposed_at="2026-07-31T01:05:00.000000Z",
        schema_version=1,
    )

    payload = json.loads(encode_task_record(record))

    assert payload["schema_version"] == 1
    assert "predecessor_plan_id" not in payload
    assert decode_task_record(json.dumps(payload).encode()) == record


def test_legacy_task_proposals_remain_readable_without_tool_call_identity() -> None:
    records = (
        TaskPlanProposed(
            sequence=3,
            plan_id=PLAN_ID,
            stage_id=STAGE_ID,
            stage_number=1,
            steps=("One legacy v2 step",),
            proposed_at="2026-07-31T01:05:00.000000Z",
            schema_version=2,
        ),
        TaskCompletionProposed(
            sequence=3,
            stage_id=STAGE_ID,
            stage_number=1,
            source=CompletionProposalSource.MODEL,
            proposed_at="2026-07-31T01:05:00.000000Z",
            schema_version=1,
        ),
        TaskReflectionRecorded(
            sequence=3,
            reflection_id=OTHER_PLAN_ID,
            stage_id=STAGE_ID,
            stage_number=1,
            recommendation=ReflectionRecommendation.NEEDS_HUMAN,
            summary="Legacy reflection",
            next_objective=None,
            recorded_at="2026-07-31T01:05:00.000000Z",
            schema_version=1,
        ),
    )

    for record in records:
        payload = encode_task_record(record)
        decoded = decode_task_record(payload.rstrip(b"\n"))
        assert decoded == record
        assert decoded.proposal_tool_use_id is None
        assert "proposal_tool_use_id" not in json.loads(payload)


def test_replay_rejects_proposal_tool_call_identity_reused_across_stages(
    tmp_path: Path,
) -> None:
    planning_start = replace(started(), kind=StageKind.PLANNING)
    plan = TaskPlanProposed(
        sequence=3,
        plan_id=PLAN_ID,
        stage_id=STAGE_ID,
        stage_number=1,
        steps=("Execute one bounded step",),
        proposed_at="2026-07-31T01:05:00.000000Z",
        proposal_tool_use_id="task-proposal-1",
    )
    execution_stage_id = "62345678-1234-4234-9234-123456789abc"
    execution_start = replace(
        started(sequence=4, stage_number=2),
        stage_id=execution_stage_id,
        started_at="2026-07-31T01:05:30.000000Z",
    )
    execution_commit = replace(
        committed(sequence=5, stage_number=2),
        stage_id=execution_stage_id,
        turn_number=2,
        turn_record_sequence=2,
        turn_record_sha256="b" * 64,
        committed_at="2026-07-31T01:05:45.000000Z",
    )
    completion = TaskCompletionProposed(
        sequence=6,
        stage_id=execution_stage_id,
        stage_number=2,
        source=CompletionProposalSource.MODEL,
        proposed_at="2026-07-31T01:06:00.000000Z",
        proposal_tool_use_id="task-proposal-1",
    )

    with pytest.raises(TaskRecordError, match="tool-use IDs must be unique"):
        replay(
            tmp_path,
            [
                header(tmp_path),
                planning_start,
                committed(),
                plan,
                execution_start,
                execution_commit,
                completion,
            ],
        )
