from __future__ import annotations

from pathlib import Path

from coquo.child_recovery import ChildRunRecoveryService
from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunStore
from coquo.child_runtime import build_child_runtime_spec_from_binding
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore


def prepared_run(tmp_path: Path):
    sessions = SessionStore(tmp_path)
    parent_writer = sessions.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = sessions.inspect(parent_id)
    parent_writer.release()
    store = ChildRunStore(tmp_path)
    info = store.create("recover this Child", parent_session=parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    store.prepare(
        info.child_run_id, runtime_spec=spec, session_store=sessions, binding=parent.binding
    )
    return store, info.child_run_id, parent_id


def test_recovery_requires_released_v2_lease_and_is_idempotent(tmp_path: Path) -> None:
    store, child_id, parent_id = prepared_run(tmp_path)
    lease = store.acquire_execution(child_id)
    store.start_execution(
        child_id,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        execution_id="72345678-1234-4234-9234-123456789abc",
    )
    held = ChildRunRecoveryService(tmp_path).recover(parent_session_id=parent_id)
    assert held.recovered == ()
    assert held.diagnostics[0].outcome == "still_owned"
    lease.close()
    recovered = ChildRunRecoveryService(tmp_path).recover(parent_session_id=parent_id)
    assert recovered.recovered[0].status is ChildRunStatus.INTERRUPTED
    again = ChildRunRecoveryService(tmp_path).recover(parent_session_id=parent_id)
    assert again.recovered == ()


def test_recovery_preserves_legacy_lease_ambiguity(tmp_path: Path) -> None:
    store, child_id, parent_id = prepared_run(tmp_path)
    lease = store.acquire_execution(child_id)
    store.start_execution(
        child_id,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        execution_id="72345678-1234-4234-9234-123456789abc",
    )
    lease.close()
    legacy = store.root / f"{child_id}.execution.lock"
    legacy.write_bytes(b"child-execution-lease-v1\n")
    result = ChildRunRecoveryService(tmp_path).recover(parent_session_id=parent_id)
    assert result.recovered == ()
    assert result.diagnostics[0].outcome == "legacy_lease_ambiguous"
    assert store.inspect(child_id).status is ChildRunStatus.RUNNING
