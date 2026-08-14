from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from coquo.child_handoff import ChildHandoffError, build_child_handoff, publish_child_handoff
from coquo.child_run_records import (
    ChildRunRecordError,
    ChildRunPreparationFailed,
    ChildRunStatus,
    decode_child_run_record,
    encode_child_run_record,
    replay_child_run_records,
)
from coquo.child_run_store import (
    ChildRunAppendCommitError,
    ChildRunStore,
    ChildRunStoreError,
)
from coquo.child_runtime import build_child_runtime_spec_from_binding
from coquo.core.contracts import AssistantText, ToolTurnLedger, UserMessage
from coquo.session_records import BindingSnapshot
from coquo.session import ProjectSession
from coquo.session_store import SessionStore


def parent(tmp_path: Path) -> str:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    value = writer.session_id
    writer.release()
    return value


def ready_child(tmp_path: Path, objective: str = "return a result"):
    sessions = SessionStore(tmp_path)
    parent_writer = sessions.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    binding = parent_writer.state.binding
    parent_writer.release()
    store = ChildRunStore(tmp_path)
    info = store.create(objective, parent_session=parent_id)
    child_session_id = "62345678-1234-4234-9234-123456789abc"
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id=child_session_id,
        objective=info.objective,
        binding=binding,
    )
    store.prepare(info.child_run_id, runtime_spec=spec, session_store=sessions, binding=binding)
    return sessions, store, info, binding, child_session_id


def completed_child(tmp_path: Path, response: str):
    sessions, store, info, binding, child_session_id = ready_child(tmp_path)
    execution_id = "72345678-1234-4234-9234-123456789abc"
    store.start_execution(
        info.child_run_id,
        child_session_id=child_session_id,
        execution_id=execution_id,
    )
    child_writer = sessions.open(child_session_id)
    turn = child_writer.append_turn(
        (UserMessage("child objective"), AssistantText(response)),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    child_writer.release()
    store.finish_completed(
        info.child_run_id,
        execution_id=execution_id,
        session_record_sequence=turn.sequence,
        assistant_text_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
    )
    return sessions, store, info, child_session_id, turn


def test_cancelled_child_publishes_bounded_stable_handoff(tmp_path: Path) -> None:
    session_id = parent(tmp_path)
    store = ChildRunStore(tmp_path)
    info = store.create("do not expose objective", parent_session=session_id)
    store.request_cancel(info.child_run_id, reason="stop")
    handoff = publish_child_handoff(tmp_path, info.child_run_id)
    assert handoff.outcome == ChildRunStatus.CANCELLED
    assert "do not expose objective" not in handoff.body
    assert handoff.child_turn_record_sequence is None
    assert store.inspect(info.child_run_id).handoff is not None
    assert (
        publish_child_handoff(tmp_path, info.child_run_id).handoff_sha256 == handoff.handoff_sha256
    )


def test_nonterminal_child_cannot_publish_handoff(tmp_path: Path) -> None:
    session_id = parent(tmp_path)
    info = ChildRunStore(tmp_path).create("still queued", parent_session=session_id)
    with pytest.raises(ChildHandoffError, match="not terminal"):
        build_child_handoff(tmp_path, info.child_run_id)


def test_completed_handoff_uses_exact_turn_evidence_and_utf8_boundary(tmp_path: Path) -> None:
    response = "结论" * 20000
    sessions, _store, info, child_session_id, turn = completed_child(
        tmp_path,
        response,
    )

    handoff = publish_child_handoff(tmp_path, info.child_run_id)

    evidence = sessions.turn_evidence(child_session_id, turn.sequence)
    assert handoff.truncated is True
    assert handoff.body.endswith("[handoff truncated]")
    assert len(handoff.body.encode("utf-8")) <= 32 * 1024
    assert handoff.child_turn_record_sequence == turn.sequence
    assert handoff.child_turn_record_sha256 == evidence.record_sha256
    assert handoff.source_text_sha256 == hashlib.sha256(response.encode("utf-8")).hexdigest()


def test_handoff_record_round_trip_and_replay_rejects_evidence_mismatch(tmp_path: Path) -> None:
    _sessions, store, info, _child_session_id, _turn = completed_child(tmp_path, "exact result")
    handoff = build_child_handoff(tmp_path, info.child_run_id).record()
    assert decode_child_run_record(encode_child_run_record(handoff)) == handoff
    state = store.replay_state(info.child_run_id)

    invalid = (
        replace(handoff, terminal_record_sequence=handoff.terminal_record_sequence - 1),
        replace(handoff, terminal_record_type="child_run_failed"),
        replace(handoff, outcome="failed"),
        replace(handoff, result_code="failed"),
        replace(handoff, child_turn_record_sequence=handoff.child_turn_record_sequence + 1),
        replace(handoff, child_turn_record_sequence=None, child_turn_record_sha256=None),
    )
    for record in invalid:
        with pytest.raises(ChildRunRecordError):
            replay_child_run_records([*state.records, record])

    with pytest.raises(ChildRunRecordError, match="publication"):
        replay_child_run_records([*state.records, replace(handoff, body="tampered")])
    with pytest.raises(ChildRunRecordError, match="manifest"):
        encode_child_run_record(replace(handoff, handoff_sha256="0" * 64))


def test_published_completed_handoff_revalidates_child_turn_evidence(tmp_path: Path) -> None:
    sessions, _store, info, child_session_id, _turn = completed_child(tmp_path, "exact result")
    publish_child_handoff(tmp_path, info.child_run_id)
    session_path = sessions.inspect(child_session_id).path
    session_path.write_bytes(session_path.read_bytes() + b"not-json\n")

    with pytest.raises(ChildHandoffError, match="evidence is unavailable"):
        build_child_handoff(tmp_path, info.child_run_id)


def test_failed_and_interrupted_handoffs_do_not_leak_untrusted_text(tmp_path: Path) -> None:
    _sessions, failed_store, failed, _binding, child_session_id = ready_child(
        tmp_path, "SECRET objective"
    )
    failed_store.finish_failed(
        failed.child_run_id,
        execution_id=None,
        phase="pre_start",
        result_code="route_failed",
        message="SECRET provider traceback",
    )
    failed_handoff = publish_child_handoff(tmp_path, failed.child_run_id)
    assert failed_handoff.body == "Child Run ended with outcome failed (route_failed)."
    assert "SECRET" not in failed_handoff.body

    execution_id = "72345678-1234-4234-9234-123456789abc"
    second = failed_store.create("ANOTHER SECRET", parent_session=failed.parent_session_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=second.child_run_id,
        parent_session_id=second.parent_session_id,
        child_session_id="82345678-1234-4234-9234-123456789abc",
        objective=second.objective,
        binding=BindingSnapshot.fake(),
    )
    failed_store.prepare(
        second.child_run_id,
        runtime_spec=spec,
        session_store=SessionStore(tmp_path),
        binding=BindingSnapshot.fake(),
    )
    failed_store.start_execution(
        second.child_run_id,
        child_session_id=spec.child_session_id,
        execution_id=execution_id,
    )
    failed_store.finish_interrupted(second.child_run_id)
    interrupted = publish_child_handoff(tmp_path, second.child_run_id)
    assert interrupted.body == ("Child Run ended with outcome interrupted (execution_abandoned).")
    assert child_session_id not in failed_handoff.body


def test_handoff_duplicate_conflict_and_append_uncertainty(monkeypatch, tmp_path: Path) -> None:
    session_id = parent(tmp_path)
    store = ChildRunStore(tmp_path)
    info = store.create("cancel", parent_session=session_id)
    store.request_cancel(info.child_run_id, reason="stop")
    handoff = build_child_handoff(tmp_path, info.child_run_id)
    store.publish_handoff(info.child_run_id, handoff.record())
    store.publish_handoff(info.child_run_id, handoff.record())
    with pytest.raises(ChildRunStoreError, match="already published differently"):
        store.publish_handoff(
            info.child_run_id,
            replace(handoff.record(), published_at="2026-08-14T01:00:00.000000Z"),
        )

    other = store.create("uncertain", parent_session=session_id)
    store.request_cancel(other.child_run_id, reason="stop")
    import coquo.child_run_store as child_run_store

    monkeypatch.setattr(
        child_run_store.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync failure")),
    )
    with pytest.raises(ChildRunAppendCommitError) as error:
        publish_child_handoff(tmp_path, other.child_run_id)
    assert error.value.record_may_be_visible is True
    assert ChildRunStore(tmp_path).inspect(other.child_run_id).handoff is not None


def test_preparation_failure_legacy_terminal_publishes_fixed_summary(tmp_path: Path) -> None:
    session_id = parent(tmp_path)
    store = ChildRunStore(tmp_path)
    info = store.create("SECRET objective", parent_session=session_id)
    with store.open(info.child_run_id) as writer:
        writer._append_transition(
            ChildRunPreparationFailed(
                sequence=1,
                child_run_id=info.child_run_id,
                phase="child_session",
                result_code="session_create_failed",
                message="SECRET raw failure",
                failed_at="2026-08-14T01:00:00.000000Z",
            )
        )

    handoff = publish_child_handoff(tmp_path, info.child_run_id)
    assert handoff.outcome == "failed"
    assert handoff.body == ("Child Run ended with outcome failed (session_create_failed).")
    assert "SECRET" not in handoff.body


def test_project_session_rejects_handoff_owned_by_another_parent(tmp_path: Path) -> None:
    first = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    info = first.create_child_run("owned by first")
    first.cancel_child_run(info.child_run_id, "stop")
    second = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    try:
        with pytest.raises(ChildRunStoreError, match="another parent Session"):
            second.publish_child_handoff(info.child_run_id)
        assert first.publish_child_handoff(info.child_run_id).outcome == "cancelled"
    finally:
        second.close()
        first.close()
