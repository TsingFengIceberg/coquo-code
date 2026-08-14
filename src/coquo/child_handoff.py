"""Evidence-bound, bounded projections from terminal Child Runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from coquo.child_run_records import (
    MAX_CHILD_HANDOFF_BODY_BYTES,
    MAX_CHILD_HANDOFF_BODY_CHARACTERS,
    ChildRunHandoffPublished,
    utc_now,
)
from coquo.child_run_store import ChildRunStore, ChildRunStoreError
from coquo.session_store import SessionStore, SessionStoreError, SessionWriter


class ChildHandoffError(ChildRunStoreError):
    """Raised when terminal Child evidence cannot produce a safe handoff."""


@dataclass(frozen=True)
class ChildHandoff:
    child_run_id: str
    parent_session_id: str
    child_session_id: str | None
    outcome: str
    terminal_record_sequence: int
    terminal_record_type: str
    result_code: str
    source_text_sha256: str
    body: str
    body_sha256: str
    truncated: bool
    child_turn_record_sequence: int | None
    child_turn_record_sha256: str | None
    handoff_sha256: str
    published_at: str

    def record(self) -> ChildRunHandoffPublished:
        return ChildRunHandoffPublished(
            sequence=self.terminal_record_sequence + 1,
            child_run_id=self.child_run_id,
            parent_session_id=self.parent_session_id,
            child_session_id=self.child_session_id,
            outcome=self.outcome,
            terminal_record_sequence=self.terminal_record_sequence,
            terminal_record_type=self.terminal_record_type,
            result_code=self.result_code,
            source_text_sha256=self.source_text_sha256,
            body=self.body,
            body_sha256=self.body_sha256,
            truncated=self.truncated,
            child_turn_record_sequence=self.child_turn_record_sequence,
            child_turn_record_sha256=self.child_turn_record_sha256,
            handoff_sha256=self.handoff_sha256,
            published_at=self.published_at,
        )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_text(text: str) -> tuple[str, bool]:
    if (
        len(text) <= MAX_CHILD_HANDOFF_BODY_CHARACTERS
        and len(text.encode("utf-8")) <= MAX_CHILD_HANDOFF_BODY_BYTES
    ):
        return text, False
    marker = "\n[handoff truncated]"
    budget = MAX_CHILD_HANDOFF_BODY_BYTES - len(marker.encode("utf-8"))
    body = ""
    for character in text:
        candidate = body + character
        if (
            len(candidate.encode("utf-8")) > budget
            or len(candidate) + len(marker) > MAX_CHILD_HANDOFF_BODY_CHARACTERS
        ):
            break
        body = candidate
    return body + marker, True


def _canonical_manifest(values: Mapping[str, object]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_child_handoff(workspace, child_run_id: str) -> ChildHandoff:
    store = ChildRunStore(workspace)
    state = store.replay_state(child_run_id)
    info = store.inspect(child_run_id)
    terminal = (
        state.completed
        or state.failed
        or state.preparation_failed
        or state.cancelled_terminal
        or state.interrupted
        or state.cancelled
    )
    if terminal is None:
        raise ChildHandoffError("Child Run is not terminal")

    outcome = state.status.value
    terminal_type = terminal.record_type
    result_code = (
        "completed"
        if state.completed is not None
        else state.failed.result_code
        if state.failed is not None
        else state.preparation_failed.result_code
        if state.preparation_failed is not None
        else state.cancelled_terminal.result_code
        if state.cancelled_terminal is not None
        else state.interrupted.result_code
        if state.interrupted is not None
        else "cancelled_before_start"
    )
    child_session_id = info.child_session_id
    turn_sequence = turn_digest = None
    if state.completed is not None:
        if child_session_id is None:
            raise ChildHandoffError("completed Child Run has no Child Session")
        try:
            sessions = SessionStore(workspace)
            committed = sessions.committed_turn(
                child_session_id, state.completed.session_record_sequence
            )
            evidence = sessions.turn_evidence(
                child_session_id, state.completed.session_record_sequence
            )
        except (SessionStoreError, OSError) as error:
            raise ChildHandoffError(f"Child Turn evidence is unavailable: {error}") from None
        if _digest(committed.assistant.text) != state.completed.assistant_text_sha256:
            raise ChildHandoffError("Child completed text digest does not match durable evidence")
        source = committed.assistant.text
        source_digest = _digest(source)
        body, truncated = _bounded_text(source)
        turn_sequence = evidence.record_sequence
        turn_digest = evidence.record_sha256
    else:
        source = f"Child Run ended with outcome {outcome} ({result_code})."
        source_digest = _digest(source)
        body, truncated = source, False
    body_digest = _digest(body)
    manifest = {
        "body_sha256": body_digest,
        "child_run_id": info.child_run_id,
        "child_session_id": child_session_id,
        "child_turn_record_sequence": turn_sequence,
        "child_turn_record_sha256": turn_digest,
        "outcome": outcome,
        "parent_session_id": info.parent_session_id,
        "result_code": result_code,
        "source_text_sha256": source_digest,
        "terminal_record_sequence": terminal.sequence,
        "terminal_record_type": terminal_type,
        "truncated": truncated,
    }
    handoff = ChildHandoff(
        info.child_run_id,
        info.parent_session_id,
        child_session_id,
        outcome,
        terminal.sequence,
        terminal_type,
        result_code,
        source_digest,
        body,
        body_digest,
        truncated,
        turn_sequence,
        turn_digest,
        _digest(_canonical_manifest(manifest)),
        state.handoff.published_at if state.handoff is not None else utc_now(),
    )
    if state.handoff is not None and handoff.record() != state.handoff:
        raise ChildHandoffError("published Child handoff does not match durable evidence")
    return handoff


def publish_child_handoff(workspace, child_run_id: str) -> ChildHandoff:
    handoff = build_child_handoff(workspace, child_run_id)
    ChildRunStore(workspace).publish_handoff(child_run_id, handoff.record())
    return build_child_handoff(workspace, child_run_id)


def deliver_child_handoff(
    workspace,
    child_run_id: str,
    *,
    parent_writer: SessionWriter,
    source: str = "host",
    tool_use_id: str | None = None,
) -> ChildHandoff:
    """Commit a content-free parent receipt before returning untrusted handoff content."""
    handoff = publish_child_handoff(workspace, child_run_id)
    if handoff.parent_session_id != parent_writer.session_id:
        raise ChildHandoffError("Child Run belongs to another parent Session")
    parent_writer.child_handoff_delivered(
        child_run_id=handoff.child_run_id,
        child_session_id=handoff.child_session_id,
        outcome=handoff.outcome,
        terminal_record_sequence=handoff.terminal_record_sequence,
        handoff_sha256=handoff.handoff_sha256,
        source=source,
        tool_use_id=tool_use_id,
    )
    return handoff
