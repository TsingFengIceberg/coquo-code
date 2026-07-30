from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import stat
from threading import Barrier, Thread
from uuid import UUID

import pytest

from leonervis_code.core.contracts import (
    ToolArguments,
    AssistantText,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from leonervis_code.session_records import (
    SESSION_HEADER_SCHEMA_VERSION,
    TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
    TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
    TURN_COMMITTED_BATCH_SCHEMA_VERSION,
    TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
    TURN_COMMITTED_SCHEMA_VERSION,
    BindingSnapshot,
    SessionHeader,
    SessionNameSource,
    SessionTitleFallbackReason,
    TurnCommitted,
    encode_record,
    replay_records,
    workspace_fingerprint,
)
from leonervis_code.session_store import (
    AtomicJsonWriteError,
    MAX_SESSION_PREVIEW_TURNS,
    SessionLockedError,
    SessionNameConflictError,
    SessionResumeStaleError,
    SessionStore,
    SessionStoreError,
    query_tool_ledgers,
)

SESSION_ONE = "12345678-1234-4234-9234-123456789abc"
SESSION_TWO = "22345678-1234-4234-9234-123456789abc"
NOW = "2026-07-17T12:00:00.000000Z"


def store(workspace: Path, session_id: str = SESSION_ONE) -> SessionStore:
    return SessionStore(
        workspace,
        uuid_factory=lambda: UUID(session_id),
        clock=lambda: NOW,
    )


def committed_items(tool_id: str = "tool-1", *, assistant_text: str | None = None):
    return (
        UserMessage("read"),
        ToolUse(
            tool_id,
            "read_file",
            ToolArguments.from_mapping({"path": "README.md"}),
            assistant_text=assistant_text,
        ),
        ToolResult(tool_id, "content"),
        AssistantText("done"),
    )


def committed_ledger(tool_id: str = "tool-1") -> ToolTurnLedger:
    return ToolTurnLedger(
        (
            ToolOutcomeEntry(
                tool_id,
                "read_file",
                1,
                ToolRequestOutcome.SUCCEEDED,
            ),
        )
    )


def test_create_append_release_open_latest_round_trip_and_list(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    binding = BindingSnapshot.fake()
    writer = session_store.create(binding)

    assert writer.path == session_store.root / f"{SESSION_ONE}.jsonl"
    assert writer.path.parent == (
        tmp_path / ".leonervis-code" / "sessions" / session_store.workspace_fingerprint
    )
    writer.append_turn(committed_items(), binding=binding, tool_ledger=committed_ledger())
    persisted_turn = writer.path.read_text(encoding="utf-8").splitlines()[-1]
    assert '"record_type":"turn_committed"' in persisted_turn
    assert f'"schema_version":{TURN_COMMITTED_SCHEMA_VERSION}' in persisted_turn
    assert '"arguments":{"path":"README.md"}' in persisted_turn
    assert '"assistant_text":null' in persisted_turn
    writer.turn_failed(binding=binding, failure_kind="cancelled", message="user cancelled")
    assert len(writer.state.history) == 4
    assert len(writer.state.turns) == 1
    writer.release()

    reopened = session_store.open("latest")
    assert reopened.session_id == SESSION_ONE
    assert reopened.state.records[-1].record_type == "session_resumed"
    assert reopened.state.history == committed_items()
    reopened.close(reason="done")

    info = session_store.show(SESSION_ONE)
    assert info.closed is True
    assert info.turn_count == 1
    assert session_store.list() == (info,)

    resumed_after_clean_close = session_store.open(SESSION_ONE)
    assert resumed_after_clean_close.state.closed is False
    assert resumed_after_clean_close.state.history == committed_items()
    resumed_after_clean_close.release()


def test_preview_replays_bounded_recent_turns_without_mutation(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    binding = BindingSnapshot.fake()
    writer = session_store.create(binding)
    writer.append_turn(
        (UserMessage("first"), AssistantText("answer one")),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    writer.append_turn(
        (UserMessage("second"), AssistantText("answer two")),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    transcript_before = writer.path.read_bytes()
    latest_path = session_store.root / "latest.json"
    latest_before = latest_path.read_bytes()
    state_before = writer.state

    preview = session_store.preview(SESSION_ONE, 1)

    assert preview.info.session_id == SESSION_ONE
    assert preview.total_turns == 2
    assert len(preview.turns) == 1
    assert preview.turns[0].user.text == "second"
    assert preview.turns[0].assistant.text == "answer two"
    assert writer.path.read_bytes() == transcript_before
    assert latest_path.read_bytes() == latest_before
    assert writer.state == state_before
    with pytest.raises(SessionStoreError, match="preview limit"):
        session_store.preview(SESSION_ONE, 0)
    with pytest.raises(SessionStoreError, match="preview limit"):
        session_store.preview(SESSION_ONE, MAX_SESSION_PREVIEW_TURNS + 1)
    with pytest.raises(SessionStoreError, match="preview limit"):
        session_store.preview(SESSION_ONE, True)
    writer.release()


def test_show_and_preview_do_not_create_state_in_empty_workspace(tmp_path: Path) -> None:
    session_store = store(tmp_path)

    with pytest.raises(SessionStoreError, match="does not exist"):
        session_store.inspect("latest")
    with pytest.raises(SessionStoreError, match="does not exist"):
        session_store.preview("latest", 1)

    assert not (tmp_path / ".leonervis-code").exists()


def test_new_sessions_receive_monotonic_default_names_under_directory_lock(
    tmp_path: Path,
) -> None:
    identifiers = iter((UUID(SESSION_ONE), UUID(SESSION_TWO)))
    session_store = SessionStore(
        tmp_path,
        uuid_factory=lambda: next(identifiers),
        clock=lambda: NOW,
    )

    first = session_store.create(BindingSnapshot.fake())
    assert first.info.name == "New session 1"
    assert first.info.name_source == SessionNameSource.DEFAULT
    assert first.state.header.schema_version == SESSION_HEADER_SCHEMA_VERSION
    first.release()

    second = session_store.create(BindingSnapshot.fake())
    assert second.info.name == "New session 2"
    assert second.info.name_source == SessionNameSource.DEFAULT
    second.release()


def test_first_committed_prompt_auto_names_but_failed_turn_does_not(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    binding = BindingSnapshot.fake()
    writer = session_store.create(binding)

    writer.turn_failed(binding=binding, failure_kind="provider", message="failed")
    assert writer.info.name == "New session 1"
    assert writer.info.name_source == SessionNameSource.DEFAULT

    writer.append_turn(
        (
            UserMessage("  Review the provider adapter\nwith a second line"),
            AssistantText("done"),
        ),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )

    assert writer.info.name == "Review the provider adapter"
    assert writer.info.name_source == SessionNameSource.AUTO
    writer.release()

    assert session_store.show(SESSION_ONE).name == "Review the provider adapter"


def test_first_turn_model_name_conflict_is_checked_atomically_case_insensitively(
    tmp_path: Path,
) -> None:
    identifiers = iter((UUID(SESSION_ONE), UUID(SESSION_TWO)))
    session_store = SessionStore(
        tmp_path,
        uuid_factory=lambda: next(identifiers),
        clock=lambda: NOW,
    )
    first = session_store.create(BindingSnapshot.fake())
    first.append_turn(
        (UserMessage("first"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
        session_name="Adapter Review",
        session_name_source=SessionNameSource.MODEL,
    )
    first.release()
    second = session_store.create(BindingSnapshot.fake())

    with pytest.raises(SessionNameConflictError, match="adapter review"):
        second.append_turn(
            (UserMessage("second"), AssistantText("done")),
            binding=BindingSnapshot.fake(),
            tool_ledger=ToolTurnLedger(),
            session_name="adapter review",
            session_name_source=SessionNameSource.MODEL,
        )

    assert second.info.turn_count == 0
    assert second.info.name == "New session 2"
    second.release()


def test_manual_rename_is_append_only_and_auto_restore_keeps_history_unchanged(
    tmp_path: Path,
) -> None:
    session_store = store(tmp_path)
    binding = BindingSnapshot.fake()
    writer = session_store.create(binding)
    writer.append_turn(
        (UserMessage("Automatic title source"), AssistantText("done")),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    original_history = writer.state.history

    manual = writer.rename("  Release   review  ")
    assert manual.name == "Release review"
    assert manual.name_source == SessionNameSource.MANUAL
    assert writer.state.history == original_history

    writer.append_turn(
        (UserMessage("Later prompt"), AssistantText("later reply")),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    assert writer.info.name == "Release review"
    assert writer.info.name_source == SessionNameSource.MANUAL
    renamed_history = writer.state.history

    automatic = writer.rename()
    assert automatic.name == "Automatic title source"
    assert automatic.name_source == SessionNameSource.AUTO
    assert writer.state.history == renamed_history
    writer.release()

    reopened = session_store.open(SESSION_ONE)
    assert reopened.info.name == "Automatic title source"
    assert reopened.info.name_source == SessionNameSource.AUTO
    assert reopened.state.history == renamed_history
    reopened.release()


def test_archive_toggle_is_idempotent_append_only_and_survives_resume(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    initial_records = writer.info.record_count

    archived = writer.set_archived(True)
    assert archived.archived is True
    assert archived.record_count == initial_records + 1
    assert writer.set_archived(True).record_count == archived.record_count

    writer.release()
    resumed = session_store.open(SESSION_ONE)
    assert resumed.info.archived is True
    assert resumed.state.history == ()

    active = resumed.set_archived(False)
    assert active.archived is False
    assert active.record_count == archived.record_count + 2
    assert resumed.set_archived(False).record_count == active.record_count

    pinned = resumed.set_pinned(True)
    assert pinned.pinned is True
    assert pinned.record_count == active.record_count + 1
    assert resumed.set_pinned(True).record_count == pinned.record_count
    resumed.release()

    reopened = session_store.open(SESSION_ONE)
    assert reopened.info.pinned is True
    unpinned = reopened.set_pinned(False)
    assert unpinned.pinned is False
    assert reopened.set_pinned(False).record_count == unpinned.record_count
    reopened.release()


def test_fallback_title_reason_is_exposed_without_changing_name_identity(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.append_turn(
        (UserMessage("first"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
        session_name="Fallback title",
        session_name_source=SessionNameSource.FALLBACK,
        session_title_fallback_reason=SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT,
    )

    info = writer.info
    assert info.name == "Fallback title"
    assert info.name_source == SessionNameSource.FALLBACK
    assert info.title_fallback_reason == SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT
    writer.release()


def test_legacy_empty_session_uses_stable_short_id_fallback_without_rewrite(
    tmp_path: Path,
) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()
    legacy_header = SessionHeader(
        sequence=0,
        session_id=SESSION_ONE,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
    )
    legacy_bytes = encode_record(legacy_header)
    writer.path.write_bytes(legacy_bytes)

    info = session_store.show(SESSION_ONE)

    assert info.name == "New session 12345678"
    assert info.name_source == SessionNameSource.DEFAULT
    assert writer.path.read_bytes() == legacy_bytes


def test_tool_ledger_query_is_recent_bounded_and_distinguishes_empty_v5(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    binding = BindingSnapshot.fake()
    writer = session_store.create(binding)
    writer.append_turn(committed_items("tool-1"), binding=binding, tool_ledger=committed_ledger())
    writer.append_turn(
        (UserMessage("plain"), AssistantText("done")),
        binding=binding,
        tool_ledger=ToolTurnLedger(),
    )
    writer.release()

    result = session_store.tool_ledgers("latest", 1)

    assert result.total_turns == 2
    assert len(result.turns) == 1
    assert result.turns[0].turn_number == 2
    assert result.turns[0].schema_version == TURN_COMMITTED_SCHEMA_VERSION
    assert result.turns[0].ledger == ToolTurnLedger()


def test_tool_ledger_query_marks_legacy_turn_as_unavailable(tmp_path: Path) -> None:
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ONE,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=binding,
    )
    legacy = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=binding,
        items=committed_items(),
        schema_version=TURN_COMMITTED_BATCH_SCHEMA_VERSION,
    )
    state = replay_records((header, legacy), expected_workspace=str(tmp_path.resolve()))

    result = query_tool_ledgers(state, 20)

    assert result.total_turns == 1
    assert result.turns[0].schema_version == TURN_COMMITTED_BATCH_SCHEMA_VERSION
    assert result.turns[0].ledger is None


@pytest.mark.parametrize("limit", [0, 21, True, "1"])
def test_tool_ledger_query_rejects_invalid_limits(tmp_path: Path, limit) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()

    with pytest.raises(SessionStoreError, match="tool ledger limit must be between 1 and 20"):
        session_store.tool_ledgers("latest", limit)


@pytest.mark.parametrize(
    "legacy_schema",
    [
        TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
        TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
        TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
        TURN_COMMITTED_BATCH_SCHEMA_VERSION,
    ],
)
def test_resume_appends_current_turn_without_rewriting_legacy_prefix(
    tmp_path: Path, legacy_schema: int
) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    legacy = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=committed_items("legacy-tool"),
        schema_version=legacy_schema,
    )
    writer.path.write_bytes(writer.path.read_bytes() + encode_record(legacy))
    writer.release()
    prefix = writer.path.read_bytes()

    resumed = session_store.open(SESSION_ONE)
    assert resumed.state.history == committed_items("legacy-tool")
    resumed.append_turn(
        committed_items("current-tool"),
        binding=BindingSnapshot.fake(),
        tool_ledger=committed_ledger("current-tool"),
    )
    after = resumed.path.read_bytes()
    resumed.release()

    assert after.startswith(prefix)
    appended = after[len(prefix) :]
    assert b'"record_type":"session_resumed"' in appended
    assert b'"record_type":"turn_committed"' in appended
    assert f'"schema_version":{TURN_COMMITTED_SCHEMA_VERSION}'.encode() in appended
    assert b'"provider_usage":[]' in appended
    assert b'"tool_ledger":{"entries":[' in appended
    assert b'"arguments":{"path":"README.md"}' in appended
    assert b'"assistant_text":null' in appended


def test_store_reopens_v3_assistant_tool_text_exactly(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    mixed = committed_items(assistant_text="  I will read it.\n")

    legacy = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=mixed,
        schema_version=TURN_COMMITTED_ASSISTANT_TEXT_SCHEMA_VERSION,
    )
    writer.path.write_bytes(writer.path.read_bytes() + encode_record(legacy))
    transcript = writer.path.read_bytes()
    writer.release()

    reopened = session_store.open(SESSION_ONE)

    assert reopened.state.history == mixed
    assert b'"assistant_text":"  I will read it.\\n"' in transcript
    reopened.release()


def test_prepare_resume_is_read_only_and_abort_releases_target_lock(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.append_turn(
        committed_items(), binding=BindingSnapshot.fake(), tool_ledger=committed_ledger()
    )
    writer.release()
    transcript_before = writer.path.read_bytes()
    latest = session_store.root / "latest.json"
    latest_before = latest.read_bytes()

    prepared = session_store.prepare_resume("latest")

    assert prepared.state.history == committed_items()
    assert writer.path.read_bytes() == transcript_before
    assert latest.read_bytes() == latest_before
    with pytest.raises(SessionLockedError):
        session_store.prepare_resume(SESSION_ONE)

    prepared.abort()
    reopened = session_store.prepare_resume(SESSION_ONE)
    reopened.abort()


def test_prepare_resume_defers_tail_recovery_until_commit(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.append_turn(
        committed_items(), binding=BindingSnapshot.fake(), tool_ledger=committed_ledger()
    )
    writer.release()
    partial = b'{"record_type":"turn_comm'
    writer.path.write_bytes(writer.path.read_bytes() + partial)
    before = writer.path.read_bytes()

    prepared = session_store.prepare_resume(SESSION_ONE)

    assert prepared.pending_recovery is not None
    assert writer.path.read_bytes() == before
    committed = prepared.commit()
    assert [record.record_type for record in committed.writer.state.records[-2:]] == [
        "recovery",
        "session_resumed",
    ]
    committed.writer.release()


def test_prepare_resume_detects_exact_transcript_staleness(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()
    prepared = session_store.prepare_resume(SESSION_ONE)
    original = writer.path.read_bytes()
    changed = bytearray(original)
    changed[-2] = ord(" ") if changed[-2] != ord(" ") else ord("x")
    writer.path.write_bytes(changed)

    with pytest.raises(SessionResumeStaleError):
        prepared.commit()
    prepared.abort()


def test_latest_resume_uses_exact_pointer_cas_but_explicit_id_does_not(
    tmp_path: Path,
) -> None:
    session_store = store(tmp_path)
    first = session_store.create(BindingSnapshot.fake())
    first.release()
    prepared_latest = session_store.prepare_resume("latest")
    latest = session_store.root / "latest.json"
    latest_before = latest.read_bytes()
    latest.write_bytes(latest_before.replace(SESSION_ONE.encode(), SESSION_TWO.encode()))

    with pytest.raises(SessionResumeStaleError, match="latest Session changed"):
        prepared_latest.commit()
    prepared_latest.abort()

    latest.write_bytes(latest_before)
    prepared_explicit = session_store.prepare_resume(SESSION_ONE)
    latest.write_bytes(latest_before.replace(SESSION_ONE.encode(), SESSION_TWO.encode()))
    committed = prepared_explicit.commit()

    assert committed.writer.session_id == SESSION_ONE
    assert session_store.show("latest").session_id == SESSION_ONE
    committed.writer.release()


def test_prepare_resume_detects_lock_path_replacement(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()
    prepared = session_store.prepare_resume(SESSION_ONE)
    replacement = prepared.lock_path.with_suffix(".replacement")
    replacement.write_bytes(b"")
    os.replace(replacement, prepared.lock_path)

    with pytest.raises(SessionResumeStaleError, match="lock changed"):
        prepared.commit()
    prepared.abort()


def test_create_keeps_transcript_if_latest_was_replaced_before_fsync_failure(
    monkeypatch, tmp_path: Path
) -> None:
    session_store = store(tmp_path)

    def fail_after_replace(path, data):
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": SESSION_ONE,
                    "transcript": f"{SESSION_ONE}.jsonl",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise AtomicJsonWriteError("directory fsync failed", replaced=True)

    monkeypatch.setattr(
        session_store,
        "_write_latest",
        lambda session_id: fail_after_replace(session_store.root / "latest.json", session_id),
    )

    with pytest.raises(AtomicJsonWriteError) as caught:
        session_store.create(BindingSnapshot.fake())

    assert caught.value.replaced is True
    assert (session_store.root / f"{SESSION_ONE}.jsonl").is_file()
    assert (session_store.root / f"{SESSION_ONE}.lock").is_file()
    assert session_store.show("latest").session_id == SESSION_ONE


def test_create_is_collision_safe_and_latest_does_not_fallback(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()
    with pytest.raises(SessionStoreError, match="collision"):
        session_store.create(BindingSnapshot.fake())

    latest = session_store.root / "latest.json"
    latest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": SESSION_TWO,
                "transcript": f"{SESSION_TWO}.jsonl",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SessionStoreError, match="does not exist"):
        session_store.show("latest")


def test_selectors_reject_noncanonical_ids_and_paths_outside_root(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()

    with pytest.raises(SessionStoreError, match="canonical UUID4"):
        session_store.show(SESSION_ONE.upper())
    with pytest.raises(SessionStoreError, match="directly inside"):
        session_store.show(tmp_path / f"{SESSION_ONE}.jsonl")
    with pytest.raises(SessionStoreError, match="directly inside"):
        session_store.show(session_store.root / "subdir" / f"{SESSION_ONE}.jsonl")
    assert session_store.show(session_store.root / f"{SESSION_ONE}.jsonl").session_id == SESSION_ONE


def test_open_repairs_only_incomplete_final_tail_and_appends_recovery(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.append_turn(
        committed_items(), binding=BindingSnapshot.fake(), tool_ledger=committed_ledger()
    )
    writer.release()
    original = writer.path.read_bytes()
    writer.path.write_bytes(original + b'{"record_type":"turn_comm')

    reopened = session_store.open(SESSION_ONE)

    assert [record.record_type for record in reopened.state.records[-2:]] == [
        "recovery",
        "session_resumed",
    ]
    recovery = reopened.state.records[-2]
    assert recovery.truncated_bytes == len(b'{"record_type":"turn_comm')
    assert reopened.state.history == committed_items()
    assert reopened.path.read_bytes().endswith(b"\n")
    reopened.release()


def test_complete_json_without_newline_is_not_repaired(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()
    writer.path.write_bytes(writer.path.read_bytes() + b"{}")

    before = writer.path.read_bytes()
    with pytest.raises(SessionStoreError, match="complete JSON record without a newline"):
        session_store.open(SESSION_ONE)
    assert writer.path.read_bytes() == before


@pytest.mark.parametrize(
    "corruption",
    [
        b"not-json\n",
        json.dumps({"record_type": "unknown", "schema_version": 1, "sequence": 1}).encode() + b"\n",
        json.dumps(
            {
                "record_type": "session_resumed",
                "schema_version": 2,
                "sequence": 1,
                "occurred_at": NOW,
            }
        ).encode()
        + b"\n",
        json.dumps(
            {
                "record_type": "session_resumed",
                "schema_version": 1,
                "sequence": 9,
                "occurred_at": NOW,
            }
        ).encode()
        + b"\n",
    ],
)
def test_newline_terminated_corruption_fails_closed_without_repair(
    tmp_path: Path, corruption: bytes
) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.release()
    writer.path.write_bytes(writer.path.read_bytes() + corruption)
    before = writer.path.read_bytes()

    with pytest.raises(SessionStoreError):
        session_store.open(SESSION_ONE)
    assert writer.path.read_bytes() == before


def test_middle_corruption_is_never_repaired(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    writer.append_turn(
        committed_items(), binding=BindingSnapshot.fake(), tool_ledger=committed_ledger()
    )
    writer.release()
    lines = writer.path.read_bytes().splitlines(keepends=True)
    writer.path.write_bytes(lines[0] + b"broken\n" + lines[1] + b"partial")
    before = writer.path.read_bytes()

    with pytest.raises(SessionStoreError, match="record 2"):
        session_store.open(SESSION_ONE)
    assert writer.path.read_bytes() == before


def test_workspace_mismatch_filename_mismatch_symlink_and_nonregular_are_rejected(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_store = store(first)
    writer = first_store.create(BindingSnapshot.fake())
    writer.release()

    second_store = store(second, SESSION_TWO)
    second_store.root.mkdir(parents=True)
    stolen = second_store.root / f"{SESSION_ONE}.jsonl"
    stolen.write_bytes(writer.path.read_bytes())
    with pytest.raises(SessionStoreError, match="workspace does not match"):
        second_store.show(stolen)

    renamed = first_store.root / f"{SESSION_TWO}.jsonl"
    renamed.write_bytes(writer.path.read_bytes())
    with pytest.raises(SessionStoreError, match="session ID does not match"):
        first_store.show(renamed)

    target = tmp_path / "target.jsonl"
    target.write_bytes(writer.path.read_bytes())
    symlink = first_store.root / "32345678-1234-4234-9234-123456789abc.jsonl"
    symlink.symlink_to(target)
    with pytest.raises(SessionStoreError, match="symlink"):
        first_store.show(symlink)

    nonregular = first_store.root / "42345678-1234-4234-9234-123456789abc.jsonl"
    nonregular.mkdir()
    with pytest.raises(SessionStoreError, match="regular file"):
        first_store.show(nonregular)


def test_session_directory_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    control = tmp_path / ".leonervis-code"
    control.mkdir()
    (control / "sessions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionStoreError, match="symlink"):
        store(tmp_path).create(BindingSnapshot.fake())


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode assertions")
def test_storage_permissions_are_private(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())

    assert mode(tmp_path / ".leonervis-code") == 0o700
    assert mode(tmp_path / ".leonervis-code" / "sessions") == 0o700
    assert mode(session_store.root) == 0o700
    assert mode(writer.path) == 0o600
    assert mode(writer.lock_path) == 0o600
    assert mode(session_store.root / "latest.json") == 0o600
    assert mode(session_store.root / ".directory.lock") == 0o600
    writer.release()


def test_lifetime_lock_is_nonblocking_in_threads_and_other_sessions_can_open(
    tmp_path: Path,
) -> None:
    first_store = store(tmp_path, SESSION_ONE)
    first = first_store.create(BindingSnapshot.fake())
    second_store = store(tmp_path, SESSION_TWO)
    second = second_store.create(BindingSnapshot.fake())
    barrier = Barrier(2)
    errors: list[Exception] = []

    def contend() -> None:
        barrier.wait()
        try:
            first_store.open(SESSION_ONE)
        except Exception as error:
            errors.append(error)

    thread = Thread(target=contend)
    thread.start()
    barrier.wait()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], SessionLockedError)
    second.append_turn(
        committed_items("tool-2"),
        binding=BindingSnapshot.fake(),
        tool_ledger=committed_ledger("tool-2"),
    )
    first.release()
    second.release()


@pytest.mark.skipif(os.name == "nt", reason="process flock test uses fork")
def test_lifetime_lock_is_nonblocking_across_processes(tmp_path: Path) -> None:
    session_store = store(tmp_path)
    writer = session_store.create(BindingSnapshot.fake())
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_try_open_process, args=(tmp_path, queue))
    process.start()
    process.join(timeout=5)

    assert process.exitcode == 0
    assert queue.get(timeout=1) == "locked"
    writer.release()


def _try_open_process(workspace: Path, queue) -> None:
    try:
        store(workspace).open(SESSION_ONE)
    except SessionLockedError:
        queue.put("locked")
    except Exception as error:
        queue.put(type(error).__name__)
    else:
        queue.put("opened")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)
