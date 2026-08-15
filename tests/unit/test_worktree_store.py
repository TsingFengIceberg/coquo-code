from __future__ import annotations

from uuid import uuid4

import pytest

from coquo.worktree_records import (
    WorktreeOperation,
    WorktreeOperationFinished,
    WorktreeOperationStarted,
    WorktreeOutcome,
)
from coquo.worktree_store import WorktreeLeaseError, WorktreeStore, WorktreeStoreError


def _declare(tmp_path):
    store = WorktreeStore(tmp_path)
    team, assignment, child, member, worktree = (str(uuid4()) for _ in range(5))
    info = store.declare(
        worktree_id=worktree,
        team_id=team,
        assignment_id=assignment,
        child_run_id=child,
        member_id=member,
        role_contract="isolated-workspace-writer-v1",
        target_ref="refs/heads/main",
        base_commit="a" * 64,
        branch=f"coquo/team/{team}/{assignment}",
        relative_path=f".coquo/worktrees/v1-test/{worktree}",
    )
    return store, info


def test_store_append_replay_and_lease(tmp_path) -> None:
    store, info = _declare(tmp_path)
    operation_id = str(uuid4())
    store.append(
        info.worktree_id,
        WorktreeOperationStarted(
            1,
            operation_id,
            info.worktree_id,
            WorktreeOperation.PROVISION,
            "2026-08-15T00:00:00.000001Z",
        ),
    )
    result = store.append(
        info.worktree_id,
        WorktreeOperationFinished(
            2,
            operation_id,
            info.worktree_id,
            WorktreeOperation.PROVISION,
            WorktreeOutcome.SUCCEEDED,
            "ready",
            "ok",
            "2026-08-15T00:00:00.000002Z",
        ),
    )
    assert result.state == "ready"
    with store.acquire_lease(info.worktree_id):
        with pytest.raises(WorktreeLeaseError):
            store.acquire_lease(info.worktree_id)


def test_store_rejects_stale_append_and_tampered_artifact(tmp_path) -> None:
    store, info = _declare(tmp_path)
    operation_id = str(uuid4())
    record = WorktreeOperationStarted(
        1,
        operation_id,
        info.worktree_id,
        WorktreeOperation.PROVISION,
        "2026-08-15T00:00:00.000001Z",
    )
    store.append(info.worktree_id, record)
    with pytest.raises(WorktreeStoreError):
        store.append(info.worktree_id, record)
    artifact = store.write_patch_artifact(info.worktree_id, b"patch")
    assert artifact.read_bytes() == b"patch"
    with pytest.raises(WorktreeStoreError):
        store.write_patch_artifact(info.worktree_id, b"different")
