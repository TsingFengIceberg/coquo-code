from __future__ import annotations

import json
from pathlib import Path

import pytest

from coquo.memory import (
    MemoryError,
    MemoryRecord,
    MemoryRecallMode,
    MemoryScope,
    MemoryStatus,
    MemoryWriteMode,
)
from coquo.memory_config import MemoryConfig, MemoryConfigStore
from coquo.memory_store import MemoryStore, MemoryStoreError


def test_memory_config_defaults_off_and_effective_values_are_gated(tmp_path: Path) -> None:
    store = MemoryConfigStore(tmp_path)
    initial = store.load()
    assert initial == MemoryConfig()
    assert initial.effective_recall is MemoryRecallMode.OFF
    assert initial.effective_write is MemoryWriteMode.OFF
    assert initial.effective_tools is False

    saved = store.update(
        enabled=True,
        recall=MemoryRecallMode.ON,
        write=MemoryWriteMode.PROPOSE,
        tools=True,
    )
    assert saved.effective_recall is MemoryRecallMode.ON
    assert saved.effective_write is MemoryWriteMode.PROPOSE
    assert saved.effective_tools is True

    disabled = store.update(enabled=False)
    assert disabled.recall is MemoryRecallMode.ON
    assert disabled.write is MemoryWriteMode.PROPOSE
    assert disabled.effective_recall is MemoryRecallMode.OFF
    assert disabled.effective_write is MemoryWriteMode.OFF
    assert disabled.effective_tools is False
    assert json.loads(store.path.read_text()) == disabled.to_mapping()


def test_memory_store_candidate_lifecycle_is_append_only_and_replayable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    candidate = store.create_candidate(
        "The project uses strict UTF-8 files.",
        scope=MemoryScope.WORKSPACE,
        scope_id="workspace-one",
        source_session_id="session-one",
        source_turn=1,
    )
    assert candidate.status is MemoryStatus.CANDIDATE
    confirmed = store.confirm(candidate.memory_id)
    assert confirmed.status is MemoryStatus.CONFIRMED
    updated = store.update(candidate.memory_id, confidence=0.9)
    assert updated.confidence == 0.9
    found = store.search("strict UTF-8", scope=MemoryScope.WORKSPACE, scope_id="workspace-one")
    assert [item.memory_id for item in found] == [candidate.memory_id]
    assert store.get(candidate.memory_id).last_recalled_at is not None

    replayed = MemoryStore(tmp_path).get(candidate.memory_id)
    assert replayed == store.get(candidate.memory_id)
    events = (tmp_path / ".coquo" / "memory" / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["event"] for line in events] == [
        "created",
        "confirmed",
        "updated",
        "recalled",
    ]


def test_memory_store_scope_and_terminal_boundaries_are_enforced(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    candidate = store.create_candidate(
        "Keep release checks deterministic.",
        scope=MemoryScope.TASK,
        scope_id="task-one",
    )
    assert store.search("release", scope=MemoryScope.TASK, scope_id="other-task") == ()
    deleted = store.transition(candidate.memory_id, MemoryStatus.DELETED)
    assert deleted.status is MemoryStatus.DELETED
    assert store.search("release") == ()
    with pytest.raises(MemoryStoreError, match="terminal"):
        store.confirm(candidate.memory_id)


def test_memory_store_rejects_corrupt_or_tampered_event_log(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    record = store.create_candidate(
        "A bounded fact.",
        scope=MemoryScope.USER,
        scope_id="user-one",
    )
    path = tmp_path / ".coquo" / "memory" / "events.jsonl"
    path.write_text(path.read_text().replace(record.memory_id, "not-a-uuid"), encoding="utf-8")
    with pytest.raises(MemoryError):
        MemoryStore(tmp_path).list()


def test_memory_contract_rejects_confirmed_record_without_confirmation_timestamp() -> None:
    with pytest.raises(MemoryError, match="confirmed_at"):
        MemoryRecord(
            memory_id="12345678-1234-4234-9234-123456789abc",
            scope=MemoryScope.USER,
            scope_id="user-one",
            content="A fact",
            category="fact",
            confidence=0.5,
            status=MemoryStatus.CONFIRMED,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
