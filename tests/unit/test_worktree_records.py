from __future__ import annotations

import json
from uuid import uuid4

import pytest

from coquo.worktree_records import (
    WorktreeHeader,
    WorktreeOperation,
    WorktreeOperationFinished,
    WorktreeOperationStarted,
    WorktreeOutcome,
    WorktreeRecordError,
    WorktreeSealed,
    decode_worktree_record,
    encode_worktree_record,
    replay_worktree_records,
)


def _header() -> WorktreeHeader:
    ids = [str(uuid4()) for _ in range(4)]
    return WorktreeHeader(
        sequence=0,
        worktree_id=ids[0],
        authority_workspace="/tmp/authority",
        authority_workspace_fingerprint="v1-" + "a" * 64,
        team_id=ids[1],
        assignment_id=ids[2],
        child_run_id=ids[3],
        member_id=str(uuid4()),
        role_contract="isolated-workspace-writer-v1",
        target_ref="refs/heads/main",
        base_commit="b" * 64,
        branch=f"coquo/team/{ids[1]}/{ids[2]}",
        relative_path=".coquo/worktrees/v1-a/" + ids[0],
        created_at="2026-08-15T00:00:00.000000Z",
    )


def test_worktree_record_roundtrip_and_replay() -> None:
    header = _header()
    records = [
        header,
        WorktreeOperationStarted(
            1,
            str(uuid4()),
            header.worktree_id,
            WorktreeOperation.PROVISION,
            "2026-08-15T00:00:00.000001Z",
        ),
        WorktreeOperationFinished(
            2,
            "",
            header.worktree_id,
            WorktreeOperation.PROVISION,
            WorktreeOutcome.SUCCEEDED,
            "ready",
            "ok",
            "2026-08-15T00:00:00.000002Z",
        ),
    ]
    records[2] = WorktreeOperationFinished(
        2,
        records[1].operation_id,
        header.worktree_id,
        WorktreeOperation.PROVISION,
        WorktreeOutcome.SUCCEEDED,
        "ready",
        "ok",
        "2026-08-15T00:00:00.000002Z",
    )
    state = replay_worktree_records(records)
    assert state.state.value == "ready"
    for record in records:
        encoded = encode_worktree_record(record)
        assert decode_worktree_record(encoded) == record
        assert json.loads(encoded) == json.loads(encode_worktree_record(record))


def test_seal_requires_matching_started_operation_and_preserves_state() -> None:
    header = _header()
    operation_id = str(uuid4())
    records = [
        header,
        WorktreeOperationStarted(
            1,
            operation_id,
            header.worktree_id,
            WorktreeOperation.PROVISION,
            "2026-08-15T00:00:00.000001Z",
        ),
        WorktreeOperationFinished(
            2,
            operation_id,
            header.worktree_id,
            WorktreeOperation.PROVISION,
            WorktreeOutcome.SUCCEEDED,
            "ready",
            "ok",
            "2026-08-15T00:00:00.000002Z",
        ),
        WorktreeOperationStarted(
            3,
            operation_id,
            header.worktree_id,
            WorktreeOperation.SEAL,
            "2026-08-15T00:00:00.000003Z",
        ),
        WorktreeSealed(
            4,
            operation_id,
            header.worktree_id,
            "c" * 64,
            0,
            0,
            "d" * 64,
            "2026-08-15T00:00:00.000004Z",
        ),
        WorktreeOperationFinished(
            5,
            operation_id,
            header.worktree_id,
            WorktreeOperation.SEAL,
            WorktreeOutcome.SUCCEEDED,
            "sealed",
            "ok",
            "2026-08-15T00:00:00.000005Z",
        ),
    ]
    state = replay_worktree_records(records)
    assert state.state.value == "sealed_empty"
    assert state.sealed is not None


def test_replay_rejects_sequence_and_unknown_fields() -> None:
    header = _header()
    with pytest.raises(WorktreeRecordError):
        replay_worktree_records(
            [
                header,
                WorktreeOperationStarted(
                    2,
                    str(uuid4()),
                    header.worktree_id,
                    WorktreeOperation.PROVISION,
                    "2026-08-15T00:00:00.000001Z",
                ),
            ]
        )
    mapping = json.loads(encode_worktree_record(header))
    mapping["extra"] = True
    with pytest.raises(WorktreeRecordError):
        decode_worktree_record(
            json.dumps(mapping, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        )
