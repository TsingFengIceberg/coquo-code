from __future__ import annotations

from pathlib import Path

import pytest

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import (
    ChildRunAppendCommitError,
    ChildRunExecutionLeaseError,
    ChildRunStore,
    ChildRunStoreError,
)
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionCreationRequest, SessionStore
from coquo.child_runtime import build_child_runtime_spec_from_binding


def test_child_run_create_inspect_cancel_and_restart(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    store = ChildRunStore(tmp_path)
    info = store.create("Inspect the workspace", parent_session=session_id)
    assert info.status is ChildRunStatus.QUEUED
    assert store.list()[0] == info
    with store.open(info.child_run_id) as child:
        child.cancel("superseded")
    reopened = ChildRunStore(tmp_path).inspect(info.child_run_id)
    assert reopened.status is ChildRunStatus.CANCELLED
    assert reopened.cancellation_reason == "superseded"
    assert reopened.record_count == 2


def test_child_run_empty_list_does_not_create_storage(tmp_path: Path) -> None:
    assert ChildRunStore(tmp_path).list() == ()
    assert not (tmp_path / ".coquo").exists()


def test_child_run_requires_existing_parent_session(tmp_path: Path) -> None:
    with pytest.raises(ChildRunStoreError, match="session|Session"):
        ChildRunStore(tmp_path).create("No owner")


def test_child_run_team_origin_is_atomic_and_idempotent(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    parent_id = writer.session_id
    writer.release()
    child_id = "42345678-1234-4234-9234-123456789abc"
    team_id = "52345678-1234-4234-9234-123456789abc"
    member_id = "62345678-1234-4234-9234-123456789abc"
    assignment_id = "72345678-1234-4234-9234-123456789abc"
    store = ChildRunStore(tmp_path, uuid_factory=lambda: child_id)
    first = store.create_for_team(
        "Inspect files",
        child_run_id=child_id,
        parent_session=parent_id,
        team_id=team_id,
        member_id=member_id,
        assignment_id=assignment_id,
        assigned_at="2026-08-14T10:00:00.000000Z",
    )
    second = ChildRunStore(tmp_path).create_for_team(
        "Inspect files",
        child_run_id=child_id,
        parent_session=parent_id,
        team_id=team_id,
        member_id=member_id,
        assignment_id=assignment_id,
        assigned_at="2026-08-14T10:00:00.000000Z",
    )
    assert first == second
    assert first.team_assignment is not None
    assert first.delegated is None
    with pytest.raises(ChildRunStoreError, match="conflicts"):
        ChildRunStore(tmp_path).create_for_team(
            "Other objective",
            child_run_id=child_id,
            parent_session=parent_id,
            team_id=team_id,
            member_id=member_id,
            assignment_id=assignment_id,
            assigned_at="2026-08-14T10:00:00.000000Z",
        )


def test_child_run_append_uncertainty_poisoned_writer(monkeypatch, tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    store = ChildRunStore(tmp_path)
    info = store.create("Inspect the workspace", parent_session=session_id)

    import coquo.child_run_store as child_run_store

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected append fsync failure")

    with store.open(info.child_run_id) as child:
        monkeypatch.setattr(child_run_store.os, "fsync", fail_fsync)
        with pytest.raises(ChildRunAppendCommitError) as error:
            child.cancel("uncertain")
        assert error.value.record_may_be_visible is True
        with pytest.raises(ChildRunStoreError, match="durability is uncertain"):
            child.cancel("retry")
    # The failed append is inspectable, but the writer never treats it as a
    # confirmed in-memory transition. The caller must reconcile before retrying.
    assert ChildRunStore(tmp_path).inspect(info.child_run_id).status in {
        ChildRunStatus.QUEUED,
        ChildRunStatus.CANCELLED,
    }


def test_child_run_prepare_binds_detached_session_without_latest_change(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    latest_before = (session_store.root / "latest.json").read_bytes()
    parent_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    parent = session_store.inspect(parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="62345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    prepared = child_store.prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=session_store,
        binding=parent.binding,
    )
    assert prepared.status is ChildRunStatus.READY
    assert prepared.child_session_id == spec.child_session_id
    assert (session_store.root / "latest.json").read_bytes() == latest_before
    child = session_store.inspect(spec.child_session_id)
    assert child.binding == parent.binding
    assert child.session_id != parent_id
    assert child_store.inspect(info.child_run_id).status is ChildRunStatus.READY


def test_child_run_prepare_is_idempotent_after_session_binding(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = session_store.inspect(parent_id)
    parent_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="72345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    first = child_store.prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=session_store,
        binding=parent.binding,
    )
    second = child_store.prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=session_store,
        binding=parent.binding,
    )
    assert second == first
    assert second.record_count == 3


def test_child_run_prepare_rejects_mismatched_existing_session(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = session_store.inspect(parent_id)
    parent_writer.release()
    child_id = "82345678-1234-4234-9234-123456789abc"
    child_writer = session_store.create(
        BindingSnapshot.fake(source="other"),
        creation=SessionCreationRequest(
            session_id=child_id,
            publish_latest=False,
            name="Existing",
        ),
    )
    child_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id=child_id,
        objective=info.objective,
        binding=parent.binding,
    )
    with pytest.raises(ChildRunStoreError, match="binding"):
        child_store.prepare(
            info.child_run_id,
            runtime_spec=spec,
            session_store=session_store,
            binding=parent.binding,
        )
    assert child_store.inspect(info.child_run_id).status is ChildRunStatus.FAILED


def test_child_run_execution_lease_is_exclusive_and_released(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = session_store.inspect(parent_id)
    parent_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="92345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    child_store.prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=session_store,
        binding=parent.binding,
    )
    first = child_store.acquire_execution(info.child_run_id)
    with pytest.raises(ChildRunExecutionLeaseError, match="active execution lease"):
        child_store.acquire_execution(info.child_run_id)
    first.close()
    assert first.path.name.endswith(".execution-v2.lock")
    assert first.path.exists()
    second = child_store.acquire_execution(info.child_run_id)
    second.close()


def test_child_run_legacy_execution_lease_fails_closed(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = session_store.inspect(parent_id)
    parent_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="92345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    child_store.prepare(
        info.child_run_id, runtime_spec=spec, session_store=session_store, binding=parent.binding
    )
    legacy = child_store.root / f"{info.child_run_id}.execution.lock"
    legacy.write_bytes(b"child-execution-lease-v1\n")
    with pytest.raises(ChildRunExecutionLeaseError, match="legacy_lease_ambiguous"):
        child_store.acquire_execution(info.child_run_id)
    assert legacy.exists()


def test_child_run_cancel_request_is_durable_and_idempotent(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    parent = session_store.inspect(parent_id)
    parent_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="92345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    child_store.prepare(
        info.child_run_id, runtime_spec=spec, session_store=session_store, binding=parent.binding
    )
    child_store.start_execution(
        info.child_run_id,
        child_session_id=spec.child_session_id,
        execution_id="82345678-1234-4234-9234-123456789abc",
    )
    cancelling = child_store.request_cancel(info.child_run_id, reason="stop", source="host")
    assert cancelling.status is ChildRunStatus.CANCELLING
    assert child_store.request_cancel(info.child_run_id, reason="stop", source="host") == cancelling
    with pytest.raises(ChildRunStoreError, match="conflicts"):
        child_store.request_cancel(info.child_run_id, reason="other", source="host")
    cancelled = child_store.finish_cancelled(info.child_run_id)
    assert cancelled.status is ChildRunStatus.CANCELLED
