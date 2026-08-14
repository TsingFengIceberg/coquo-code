from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from coquo.child_handoff import deliver_child_handoff, publish_child_handoff
from coquo.child_run_store import ChildRunStore
from coquo.core.contracts import AssistantText, ToolTurnLedger, UserMessage
from coquo.session import ProjectSession
from coquo.session_records import (
    BindingSnapshot,
    ChildHandoffDelivered,
    SessionRecordError,
    decode_record,
    encode_record,
    replay_records,
)
from coquo.session_store import (
    SessionAppendCommitError,
    SessionStore,
    SessionStoreError,
)


STAMP = "2026-08-14T02:00:00.000000Z"
CHILD_ID = "42345678-1234-4234-9234-123456789abc"
CHILD_SESSION_ID = "52345678-1234-4234-9234-123456789abc"


def receipt(parent_session_id: str, sequence: int = 1) -> ChildHandoffDelivered:
    return ChildHandoffDelivered(
        sequence=sequence,
        occurred_at=STAMP,
        parent_session_id=parent_session_id,
        child_run_id=CHILD_ID,
        child_session_id=CHILD_SESSION_ID,
        outcome="completed",
        terminal_record_sequence=4,
        handoff_sha256="a" * 64,
        source="host",
    )


def test_delivery_receipt_round_trip_and_strict_validation(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    record = receipt(writer.session_id)
    assert decode_record(encode_record(record)) == record
    state = replay_records([writer.state.header, record])
    assert state.child_handoff_deliveries == (record,)
    assert state.history == ()
    assert state.effective_history == ()

    invalid = (
        replace(record, parent_session_id=CHILD_ID),
        replace(record, outcome="running"),
        replace(record, terminal_record_sequence=0),
        replace(record, handoff_sha256="A" * 64),
        replace(record, source="worker"),
        replace(record, source="model", tool_use_id=None),
        replace(record, source="host", tool_use_id="tool-1"),
    )
    for candidate in invalid:
        with pytest.raises(SessionRecordError):
            replay_records([writer.state.header, candidate])
    with pytest.raises(SessionRecordError, match="already delivered"):
        replay_records([writer.state.header, record, replace(record, sequence=2)])
    writer.release()


def test_audit_writer_delivery_is_idempotent_content_free_and_has_no_resume(tmp_path: Path) -> None:
    sessions = SessionStore(tmp_path, clock=lambda: STAMP)
    parent = sessions.create(BindingSnapshot.fake())
    parent_id = parent.session_id
    parent.release()
    before = sessions.inspect(parent_id).path.read_bytes()

    writer = sessions.open_for_audit(parent_id)
    first = writer.child_handoff_delivered(
        child_run_id=CHILD_ID,
        child_session_id=CHILD_SESSION_ID,
        outcome="completed",
        terminal_record_sequence=4,
        handoff_sha256="a" * 64,
        source="host",
    )
    assert (
        writer.child_handoff_delivered(
            child_run_id=CHILD_ID,
            child_session_id=CHILD_SESSION_ID,
            outcome="completed",
            terminal_record_sequence=4,
            handoff_sha256="a" * 64,
            source="host",
        )
        == first
    )
    with pytest.raises(SessionStoreError, match="delivered differently"):
        writer.child_handoff_delivered(
            child_run_id=CHILD_ID,
            child_session_id=CHILD_SESSION_ID,
            outcome="completed",
            terminal_record_sequence=4,
            handoff_sha256="b" * 64,
            source="host",
        )
    writer.release()

    appended = sessions.inspect(parent_id).path.read_bytes()[len(before) :]
    payload = json.loads(appended)
    assert payload["record_type"] == "child_handoff_delivered"
    assert "body" not in payload and "objective" not in payload and "message" not in payload
    assert b"session_resumed" not in appended
    assert sessions.child_handoff_deliveries(parent_id) == (first,)


def test_delivery_receipt_is_ignored_by_export_and_fork(tmp_path: Path) -> None:
    identifiers = iter(
        (
            "12345678-1234-4234-9234-123456789abc",
            "22345678-1234-4234-9234-123456789abc",
        )
    )
    sessions = SessionStore(tmp_path, uuid_factory=lambda: next(identifiers), clock=lambda: STAMP)
    parent = sessions.create(BindingSnapshot.fake())
    parent.append_turn(
        (UserMessage("user"), AssistantText("assistant")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
    )
    parent.child_handoff_delivered(
        child_run_id=CHILD_ID,
        child_session_id=CHILD_SESSION_ID,
        outcome="completed",
        terminal_record_sequence=4,
        handoff_sha256="a" * 64,
        source="host",
    )
    parent.release()

    exported = sessions.conversation_export("12345678-1234-4234-9234-123456789abc")
    assert len(exported.turns) == 1
    assert "handoff" not in exported.turns[0].assistant.text
    fork = sessions.fork("12345678-1234-4234-9234-123456789abc", 1)
    assert fork.state.history == (UserMessage("user"), AssistantText("assistant"))
    assert fork.state.child_handoff_deliveries == ()
    assert all(record.record_type != "child_handoff_delivered" for record in fork.state.records)
    fork.release()


def test_delivery_append_uncertainty_poisons_session_writer(monkeypatch, tmp_path: Path) -> None:
    sessions = SessionStore(tmp_path, clock=lambda: STAMP)
    writer = sessions.create(BindingSnapshot.fake())
    import coquo.session_store as session_store

    monkeypatch.setattr(
        session_store.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync failure")),
    )
    with pytest.raises(SessionAppendCommitError) as error:
        writer.child_handoff_delivered(
            child_run_id=CHILD_ID,
            child_session_id=CHILD_SESSION_ID,
            outcome="completed",
            terminal_record_sequence=4,
            handoff_sha256="a" * 64,
            source="host",
        )
    assert error.value.record_may_be_visible is True
    with pytest.raises(SessionStoreError, match="durability is uncertain"):
        writer.append_turn((), binding=BindingSnapshot.fake(), tool_ledger=ToolTurnLedger())
    writer.release()


def test_delivery_service_does_not_return_body_before_receipt_commit(
    monkeypatch, tmp_path: Path
) -> None:
    sessions = SessionStore(tmp_path)
    parent = sessions.create(BindingSnapshot.fake())
    parent_id = parent.session_id
    parent.release()
    child = ChildRunStore(tmp_path).create("SECRET objective", parent_session=parent_id)
    ChildRunStore(tmp_path).request_cancel(child.child_run_id, reason="stop")
    publish_child_handoff(tmp_path, child.child_run_id)
    writer = sessions.open_for_audit(parent_id)
    import coquo.session_store as session_store

    monkeypatch.setattr(
        session_store.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync failure")),
    )
    result = None
    with pytest.raises(SessionAppendCommitError):
        result = deliver_child_handoff(
            tmp_path,
            child.child_run_id,
            parent_writer=writer,
        )
    assert result is None
    writer.release()


def test_project_delivery_does_not_change_parent_model_state(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    assert session.prompt("parent turn") == "Fake response: parent turn"
    child = session.create_child_run("SECRET objective")
    session.cancel_child_run(child.child_run_id, "stop")
    before_history = session.history
    before_effective = session.effective_history
    before_context = session._loop.effective_context_snapshot().context_id
    before_ledgers = session.tool_ledgers(20)
    before_usage = session.session_usage()
    before_binding = session._writer.state.binding

    handoff = session.deliver_child_handoff(child.child_run_id)

    assert handoff.outcome == "cancelled"
    assert session.history == before_history
    assert session.effective_history == before_effective
    assert session._loop.effective_context_snapshot().context_id == before_context
    assert session.tool_ledgers(20) == before_ledgers
    assert session.session_usage() == before_usage
    assert session._writer.state.binding == before_binding
    assert session._writer.state.child_handoff_deliveries[0].handoff_sha256 == (
        handoff.handoff_sha256
    )
    parent_id = session.session_id
    session.close()

    reopened = SessionStore(tmp_path).open(parent_id)
    assert reopened.state.history == before_history
    assert reopened.state.effective_history == before_effective
    assert len(reopened.state.child_handoff_deliveries) == 1
    reopened.release()
