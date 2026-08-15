from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from coquo.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from coquo.core.contracts import ToolArguments
from coquo.core.execution_scope import ExecutionScope
from coquo.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from coquo.session_records import (
    ACTION_REQUESTED_SCHEMA_VERSION,
    ActionRequested,
    BindingSnapshot,
    SessionRecordError,
    workspace_fingerprint,
    replay_records,
)
from coquo.session_store import SessionStore


AUTHORITY_ID = "12345678-1234-4234-9234-123456789abc"
WORKTREE_ID = "22345678-1234-4234-9234-123456789abc"
LEASE_ID = "32345678-1234-4234-9234-123456789abc"
REQUEST_ID = "42345678-1234-4234-9234-123456789abc"
CONTEXT_ID = f"ctx-v1-{'a' * 64}"


def test_execution_scope_defaults_to_one_authority_root(tmp_path: Path) -> None:
    scope = ExecutionScope.authority(tmp_path)

    assert scope.authority_workspace == tmp_path.resolve()
    assert scope.execution_root == scope.authority_workspace
    assert scope.kind == "authority-workspace"
    assert scope.execution_root_fingerprint == workspace_fingerprint(tmp_path)
    assert scope.worktree_id is None


def test_team_scope_pins_a_distinct_root_and_identity(tmp_path: Path) -> None:
    execution = tmp_path / "worktree"
    execution.mkdir()
    scope = ExecutionScope.team_worktree(tmp_path, execution, WORKTREE_ID)

    assert scope.authority_workspace == tmp_path.resolve()
    assert scope.execution_root == execution.resolve()
    assert scope.kind == "team-worktree"
    assert scope.worktree_id == WORKTREE_ID
    assert scope.execution_root_fingerprint == workspace_fingerprint(execution)

    with pytest.raises(ValueError, match="distinct root"):
        ExecutionScope.team_worktree(tmp_path, tmp_path, WORKTREE_ID)
    with pytest.raises(ValueError, match="canonical UUID"):
        ExecutionScope.team_worktree(tmp_path, execution, "not-an-id")


def _identity(tmp_path: Path, scope: ExecutionScope) -> ActionIdentity:
    return ActionIdentity(
        request_id=REQUEST_ID,
        tool_use_id="write-1",
        tool_name="write_file",
        arguments=ToolArguments.from_mapping({"content": "x", "path": "x.txt"}),
        action=PermissionAction.WORKSPACE_CREATE,
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        lease=ActionLease(AUTHORITY_ID, LEASE_ID, 0, CONTEXT_ID),
        precondition=ActionPrecondition.path_absent(),
        version=2,
        execution_scope=scope.kind,
        execution_root_fingerprint=scope.execution_root_fingerprint,
        worktree_id=scope.worktree_id,
    )


def test_v2_action_identity_round_trips_authority_and_team_scope(tmp_path: Path) -> None:
    execution = tmp_path / "worktree"
    execution.mkdir()
    authority = _identity(tmp_path, ExecutionScope.authority(tmp_path))
    team = _identity(tmp_path, ExecutionScope.team_worktree(tmp_path, execution, WORKTREE_ID))

    assert authority.version == 2
    assert ActionIdentity.from_mapping(authority.as_mapping()) == authority
    assert ActionIdentity.from_mapping(team.as_mapping()) == team
    assert authority.digest != team.digest


def test_action_requested_v2_replays_with_authority_execution_root(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, uuid_factory=lambda: AUTHORITY_ID)
    writer = store.create(BindingSnapshot.fake())
    record = writer.action_requested(
        identity=_identity(tmp_path, ExecutionScope.authority(tmp_path)),
        binding=writer.state.binding,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    assert record.schema_version == 2
    assert (
        replay_records(
            [writer.state.records[0], record],
            expected_workspace=str(tmp_path.resolve()),
            expected_workspace_fingerprint=workspace_fingerprint(tmp_path),
            expected_session_id=AUTHORITY_ID,
            expected_file_name=f"{AUTHORITY_ID}.jsonl",
        )
        .action_audits[0]
        .identity.version
        == 2
    )
    writer.release()


def test_v2_action_requested_rejects_a_mismatched_authority_root(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    store = SessionStore(tmp_path, uuid_factory=lambda: AUTHORITY_ID)
    writer = store.create(BindingSnapshot.fake())
    identity = replace(
        _identity(tmp_path, ExecutionScope.authority(tmp_path)),
        execution_root_fingerprint=workspace_fingerprint(other),
    )
    with pytest.raises(SessionRecordError):
        replay_records(
            [
                writer.state.records[0],
                ActionRequested(
                    sequence=1,
                    occurred_at="2026-08-15T00:00:00.000000Z",
                    binding=writer.state.binding,
                    identity=identity,
                    permission_mode=PermissionMode.WORKSPACE_WRITE,
                    approval_mode=ApprovalMode.AUTO,
                    schema_version=ACTION_REQUESTED_SCHEMA_VERSION,
                ),
            ]
        )
    writer.release()
