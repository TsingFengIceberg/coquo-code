from __future__ import annotations

from pathlib import Path

import pytest

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import (
    ChildRunAppendCommitError,
    ChildRunStore,
    ChildRunStoreError,
)
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore


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
    assert not (tmp_path / ".coquo").exists()


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
