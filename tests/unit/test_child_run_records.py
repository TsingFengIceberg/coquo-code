from __future__ import annotations

import pytest

from coquo.child_run_records import (
    ChildRunCancelled,
    ChildRunHeader,
    ChildRunRecordError,
    ChildRunStatus,
    decode_child_run_record,
    encode_child_run_record,
    replay_child_run_records,
)


RUN_ID = "42345678-1234-4234-9234-123456789abc"
SESSION_ID = "52345678-1234-4234-9234-123456789abc"
STAMP = "2026-08-13T10:00:00.000000Z"


def header() -> ChildRunHeader:
    return ChildRunHeader(
        sequence=0,
        child_run_id=RUN_ID,
        workspace="/tmp/workspace",
        workspace_fingerprint="v1-" + "a" * 64,
        parent_session_id=SESSION_ID,
        objective="Inspect the workspace",
        created_at=STAMP,
    )


def test_child_run_header_and_cancel_round_trip() -> None:
    first = header()
    cancelled = ChildRunCancelled(1, RUN_ID, "no longer needed", STAMP)
    assert decode_child_run_record(encode_child_run_record(first)) == first
    assert decode_child_run_record(encode_child_run_record(cancelled)) == cancelled
    assert replay_child_run_records([first]).status is ChildRunStatus.QUEUED
    assert replay_child_run_records([first, cancelled]).status is ChildRunStatus.CANCELLED


@pytest.mark.parametrize(
    "records",
    [
        [ChildRunCancelled(0, RUN_ID, "bad", STAMP)],
        [header(), ChildRunCancelled(2, RUN_ID, "bad", STAMP)],
        [header(), ChildRunCancelled(1, SESSION_ID, "bad", STAMP)],
        [header(), ChildRunCancelled(1, RUN_ID, "bad", "2026-08-12T10:00:00.000000Z")],
        [header(), ChildRunCancelled(1, RUN_ID, "bad", STAMP), ChildRunCancelled(2, RUN_ID, "third", STAMP)],
    ],
)
def test_child_run_replay_rejects_invalid_lifecycle(records) -> None:
    with pytest.raises(ChildRunRecordError):
        replay_child_run_records(records)


def test_child_run_record_has_closed_fields() -> None:
    raw = encode_child_run_record(header()).replace(b'"objective":"Inspect the workspace"', b'"extra":1,"objective":"Inspect the workspace"')
    with pytest.raises(ChildRunRecordError):
        decode_child_run_record(raw)
