from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from leonervis_code.core.compaction import (
    CompactionTrigger,
    EffectiveContextSummary,
    build_compact_prompt,
)
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from leonervis_code.session_records import (
    BindingSnapshot,
    CompactionFailed,
    ContextCompacted,
    Recovery,
    RuntimeChanged,
    SessionArchiveChanged,
    SessionClosed,
    SessionHeader,
    SESSION_HEADER_SCHEMA_VERSION,
    SessionNamed,
    SessionNameSource,
    SessionTitleFallbackReason,
    SessionRecordError,
    SessionResumed,
    TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
    TURN_COMMITTED_BATCH_SCHEMA_VERSION,
    TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
    TURN_COMMITTED_NAMING_SCHEMA_VERSION,
    TURN_COMMITTED_SCHEMA_VERSION,
    TURN_COMMITTED_USAGE_SCHEMA_VERSION,
    TURN_FAILED_LEGACY_SCHEMA_VERSION,
    TURN_FAILED_SCHEMA_VERSION,
    TurnCommitted,
    TurnFailed,
    canonical_session_name,
    decode_record,
    encode_record,
    replay_records,
    workspace_fingerprint,
)
from leonervis_code.providers.usage import (
    ProviderInvocationKind,
    ProviderInvocationUsage,
    ProviderTokenUsage,
)

SESSION_ID = "12345678-1234-4234-9234-123456789abc"
NOW = "2026-07-17T12:00:00.000000Z"


def successful_ledger(*requests: ToolUse) -> ToolTurnLedger:
    return ToolTurnLedger(
        tuple(
            ToolOutcomeEntry(
                request.tool_use_id,
                request.name,
                index,
                ToolRequestOutcome.SUCCEEDED,
            )
            for index, request in enumerate(requests, start=1)
        )
    )


def test_record_codec_round_trip_and_replay_excludes_audit(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    first_binding = BindingSnapshot.fake()
    second_binding = BindingSnapshot.fake(generation=1, source="runtime")
    records = [
        SessionHeader(
            sequence=0,
            session_id=SESSION_ID,
            workspace=str(workspace),
            workspace_fingerprint=workspace_fingerprint(workspace),
            created_at=NOW,
            binding=first_binding,
        ),
        RuntimeChanged(
            sequence=1,
            occurred_at=NOW,
            binding=second_binding,
            reason="model override",
        ),
        TurnCommitted(
            sequence=2,
            committed_at=NOW,
            binding=second_binding,
            items=(
                UserMessage("read it"),
                ToolUse("tool-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
                ToolResult("tool-1", "contents"),
                AssistantText("done"),
            ),
            tool_ledger=successful_ledger(
                ToolUse(
                    "tool-1",
                    "read_file",
                    ToolArguments.from_mapping({"path": "README.md"}),
                )
            ),
        ),
    ]

    decoded = [decode_record(encode_record(record)) for record in records]
    state = replay_records(
        decoded,
        expected_workspace=str(workspace),
        expected_workspace_fingerprint=workspace_fingerprint(workspace),
        expected_session_id=SESSION_ID,
        expected_file_name=f"{SESSION_ID}.jsonl",
    )

    assert decoded == records
    assert state.history == records[-1].items
    assert state.turns[0].user.text == "read it"
    assert state.binding == second_binding
    assert state.next_sequence == 3


def test_session_header_v2_and_session_named_round_trip_with_latest_name(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(workspace),
        workspace_fingerprint=workspace_fingerprint(workspace),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
        name="New session 1",
        schema_version=SESSION_HEADER_SCHEMA_VERSION,
    )
    first = SessionNamed(1, NOW, "Provider review", SessionNameSource.MANUAL)
    latest = SessionNamed(2, NOW, "Automatic title", SessionNameSource.AUTO)

    decoded = [decode_record(encode_record(record)) for record in (header, first, latest)]
    state = replay_records(decoded)

    assert decoded == [header, first, latest]
    assert state.header.name == "New session 1"
    assert state.latest_name == latest
    assert state.history == ()


def test_legacy_session_header_v1_round_trip_remains_byte_identical(tmp_path: Path) -> None:
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
    )
    encoded = encode_record(header)

    decoded = decode_record(encoded)

    assert decoded == header
    assert decoded.name is None
    assert encode_record(decoded) == encoded
    assert b'"name"' not in encoded


@pytest.mark.parametrize(
    "name,match",
    [
        ("", "empty"),
        ("line\nbreak", "control or format"),
        ("hidden\u200bvalue", "control or format"),
        ("x" * 81, "80 characters"),
        ("\U0001f600" * 70, "256 UTF-8 bytes"),
    ],
)
def test_session_name_validation_fails_closed(name: str, match: str) -> None:
    with pytest.raises(SessionRecordError, match=match):
        canonical_session_name(name)


def test_session_name_normalizes_bounded_visible_whitespace() -> None:
    assert canonical_session_name("  Review   provider\u00a0adapters  ") == (
        "Review provider adapters"
    )


def test_current_terminal_records_round_trip_strict_provider_usage(tmp_path: Path) -> None:
    binding = BindingSnapshot.fake()
    turn_usage = (
        ProviderInvocationUsage(
            1,
            ProviderInvocationKind.TURN,
            ProviderTokenUsage(120, 30),
        ),
        ProviderInvocationUsage(2, ProviderInvocationKind.TURN, None),
    )
    compact_usage = (
        ProviderInvocationUsage(
            1,
            ProviderInvocationKind.COMPACTION,
            ProviderTokenUsage(80, 12),
        ),
    )
    failed = TurnFailed(1, NOW, binding, "ProviderError", "failed", turn_usage)
    compact_failed = CompactionFailed(
        2,
        NOW,
        binding,
        CompactionTrigger.MANUAL,
        "CompactionCandidateError",
        "not smaller",
        compact_usage,
    )

    assert decode_record(encode_record(failed)) == failed
    assert decode_record(encode_record(compact_failed)) == compact_failed
    assert b'"input_tokens":120' in encode_record(failed)
    assert b'"input_tokens":null' in encode_record(failed)

    legacy = replace(
        failed,
        schema_version=TURN_FAILED_LEGACY_SCHEMA_VERSION,
        provider_usage=(),
    )
    decoded_legacy = decode_record(encode_record(legacy))
    assert decoded_legacy.schema_version == TURN_FAILED_LEGACY_SCHEMA_VERSION
    assert decoded_legacy.provider_usage == ()
    assert TURN_FAILED_SCHEMA_VERSION == 2

    with pytest.raises(SessionRecordError, match="contiguous"):
        encode_record(
            replace(
                failed,
                provider_usage=(
                    ProviderInvocationUsage(
                        2,
                        ProviderInvocationKind.TURN,
                        ProviderTokenUsage(1, 1),
                    ),
                ),
            )
        )


def test_turn_v8_round_trips_first_turn_model_name_and_v6_remains_readable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path.resolve()
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(workspace),
        workspace_fingerprint=workspace_fingerprint(workspace),
        created_at=NOW,
        binding=binding,
        name="New session 1",
        schema_version=SESSION_HEADER_SCHEMA_VERSION,
    )
    current = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=binding,
        items=(UserMessage("review adapters"), AssistantText("done")),
        session_name="Provider adapter review",
        session_name_source=SessionNameSource.MODEL,
    )

    decoded = decode_record(encode_record(current))
    state = replay_records((header, decoded))

    assert decoded == current
    assert state.turns[0].user.text == "review adapters"
    assert b'"session_name":"Provider adapter review"' in encode_record(current)
    assert b'"session_name_source":"model"' in encode_record(current)
    assert b'"session_title_fallback_reason":null' in encode_record(current)

    legacy = replace(
        current,
        schema_version=TURN_COMMITTED_USAGE_SCHEMA_VERSION,
        session_name=None,
        session_name_source=None,
    )
    legacy_encoded = encode_record(legacy)
    assert decode_record(legacy_encoded) == legacy
    assert b'"schema_version":6' in legacy_encoded
    assert b'"session_name"' not in legacy_encoded

    legacy_named = replace(
        current,
        schema_version=TURN_COMMITTED_NAMING_SCHEMA_VERSION,
    )
    legacy_named_encoded = encode_record(legacy_named)
    assert decode_record(legacy_named_encoded) == legacy_named
    assert b'"schema_version":7' in legacy_named_encoded
    assert b'"session_title_fallback_reason"' not in legacy_named_encoded


def test_turn_v8_rejects_partial_invalid_or_late_session_name(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(workspace),
        workspace_fingerprint=workspace_fingerprint(workspace),
        created_at=NOW,
        binding=binding,
    )
    first = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=binding,
        items=(UserMessage("first"), AssistantText("done")),
    )
    named_late = TurnCommitted(
        sequence=2,
        committed_at=NOW,
        binding=binding,
        items=(UserMessage("second"), AssistantText("done")),
        session_name="Late title",
        session_name_source=SessionNameSource.MODEL,
    )

    with pytest.raises(SessionRecordError, match="both be null or present"):
        encode_record(replace(first, session_name="Partial"))
    with pytest.raises(SessionRecordError, match="source is invalid"):
        encode_record(
            replace(
                first,
                session_name="Manual title",
                session_name_source=SessionNameSource.MANUAL,
            )
        )
    with pytest.raises(SessionRecordError, match="unnamed first turn"):
        replay_records((header, first, named_late))


def test_turn_v8_requires_bounded_reason_only_for_fallback_title(tmp_path: Path) -> None:
    workspace = tmp_path.resolve()
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(workspace),
        workspace_fingerprint=workspace_fingerprint(workspace),
        created_at=NOW,
        binding=binding,
    )
    fallback = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=binding,
        items=(UserMessage("first"), AssistantText("done")),
        session_name="Fallback title",
        session_name_source=SessionNameSource.FALLBACK,
        session_title_fallback_reason=SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT,
    )

    decoded = decode_record(encode_record(fallback))
    assert decoded == fallback
    assert replay_records((header, decoded)).turns[0].user.text == "first"

    with pytest.raises(SessionRecordError, match="requires a bounded reason"):
        encode_record(replace(fallback, session_title_fallback_reason=None))
    with pytest.raises(SessionRecordError, match="model Session name"):
        encode_record(
            replace(
                fallback,
                session_name_source=SessionNameSource.MODEL,
            )
        )


def test_session_archive_records_are_reversible_and_do_not_change_history(tmp_path: Path) -> None:
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=binding,
    )
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=binding,
        items=(UserMessage("first"), AssistantText("done")),
    )
    archived = SessionArchiveChanged(2, NOW, True)
    active = SessionArchiveChanged(3, NOW, False)

    decoded = tuple(
        decode_record(encode_record(record)) for record in (header, turn, archived, active)
    )
    state = replay_records(decoded)

    assert decoded == (header, turn, archived, active)
    assert state.archived is False
    assert state.history == turn.items

    with pytest.raises(SessionRecordError, match="archived must be boolean"):
        decode_record(encode_record(archived).replace(b'"archived":true', b'"archived":1'))


def test_turn_schema_v3_round_trips_structured_arguments_with_null_companion_text() -> None:
    glob = ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "src/**/*.py"}))
    grep = ToolUse(
        "grep-1",
        "grep",
        ToolArguments.from_mapping({"query": "ToolUse(", "include": "src/**/*.py"}),
    )
    read = ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "src/app.py"}))
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("find and read"),
            glob,
            ToolResult("glob-1", "src/app.py\n", truncated=True),
            grep,
            ToolResult("grep-1", '{"path":"src/app.py","line":1,"text":"ToolUse("}\n'),
            read,
            ToolResult("read-1", "contents"),
            AssistantText("done"),
        ),
        tool_ledger=successful_ledger(glob, grep, read),
    )

    encoded = encode_record(turn)
    decoded = decode_record(encoded)

    assert decoded == turn
    assert f'"schema_version":{TURN_COMMITTED_SCHEMA_VERSION}'.encode() in encoded
    assert b'"arguments":{"pattern":"src/**/*.py"},"arguments_version":1' in encoded
    assert b'"arguments":{"include":"src/**/*.py","query":"ToolUse("}' in encoded
    assert encoded.count(b'"assistant_text":null') == 3


def test_turn_schema_v3_round_trips_assistant_tool_text_without_normalizing_it() -> None:
    request = ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="  I will read it.\n",
    )
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("read"),
            request,
            ToolResult("read-1", "notes"),
            AssistantText("done"),
        ),
        tool_ledger=successful_ledger(request),
    )

    encoded = encode_record(turn)
    decoded = decode_record(encoded)

    assert decoded == turn
    assert b'"assistant_text":"  I will read it.\\n"' in encoded


def test_turn_schema_v5_round_trips_one_atomic_tool_batch_and_ledger() -> None:
    batch = AssistantToolBatch(
        (
            ToolUse("mkdir-src", "mkdir", ToolArguments.from_mapping({"path": "src"})),
            ToolUse("mkdir-tests", "mkdir", ToolArguments.from_mapping({"path": "tests"})),
        ),
        "Creating directories.",
    )
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("create"),
            batch,
            ToolResult("mkdir-src", "directory_created"),
            ToolResult("mkdir-tests", "directory_created"),
            AssistantText("done"),
        ),
        tool_ledger=successful_ledger(*batch.tool_uses),
    )

    encoded = encode_record(turn)

    assert decode_record(encoded) == turn
    assert b'"item_type":"assistant_tool_batch"' in encoded
    assert encoded.count(b'"tool_use_id"') == 6
    assert b'"tool_ledger":{"entries":[' in encoded

    legacy_v4 = replace(
        turn,
        schema_version=TURN_COMMITTED_BATCH_SCHEMA_VERSION,
        tool_ledger=ToolTurnLedger(),
    )
    legacy_encoded = encode_record(legacy_v4)
    assert decode_record(legacy_encoded) == legacy_v4
    assert b'"schema_version":4' in legacy_encoded
    assert b'"tool_ledger"' not in legacy_encoded


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda value: value.pop("tool_ledger"), "missing required field"),
        (
            lambda value: value["tool_ledger"]["entries"][0].update(request_index=2),
            "continuous",
        ),
        (
            lambda value: value["tool_ledger"]["entries"][0].update(tool_name="read_file"),
            "identity",
        ),
        (
            lambda value: value["tool_ledger"]["entries"][0].update(outcome="failed"),
            "contradicts",
        ),
    ],
)
def test_turn_schema_v5_rejects_missing_or_inconsistent_tool_ledger(mutate, match: str) -> None:
    request = ToolUse("mkdir-1", "mkdir", ToolArguments.from_mapping({"path": "src"}))
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("create"),
            request,
            ToolResult("mkdir-1", "directory_created"),
            AssistantText("done"),
        ),
        tool_ledger=successful_ledger(request),
    )
    value = json.loads(encode_record(turn))
    mutate(value)

    with pytest.raises(SessionRecordError, match=match):
        decode_record(json.dumps(value).encode())


def test_turn_schema_v2_remains_readable_and_cannot_claim_assistant_tool_text() -> None:
    pure = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("read"),
            ToolUse(
                "read-1",
                "read_file",
                ToolArguments.from_mapping({"path": "README.md"}),
            ),
            ToolResult("read-1", "notes"),
            AssistantText("done"),
        ),
        schema_version=TURN_COMMITTED_ARGUMENTS_SCHEMA_VERSION,
    )

    encoded = encode_record(pure)

    assert decode_record(encoded) == pure
    assert b'"schema_version":2' in encoded
    assert b'"arguments":{"path":"README.md"}' in encoded
    assert b'"assistant_text":' not in encoded

    value = json.loads(encoded)
    value["items"][1]["assistant_text"] = None
    with pytest.raises(SessionRecordError, match="unknown field"):
        decode_record(json.dumps(value).encode())

    mixed = replace(
        pure,
        items=(
            pure.items[0],
            replace(pure.items[1], assistant_text="I will read it."),
            *pure.items[2:],
        ),
    )
    with pytest.raises(SessionRecordError, match="newer turn_committed schema"):
        encode_record(mixed)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda item: item.pop("assistant_text"), "missing required field"),
        (lambda item: item.update(assistant_text=1), "text or null"),
        (lambda item: item.update(assistant_text=""), "non-empty text or null"),
        (lambda item: item.update(assistant_text="\ud800"), "valid UTF-8"),
        (lambda item: item.update(assistant_text="contains\x00nul"), "must not contain NUL"),
        (lambda item: item.update(assistant_text="x" * (32 * 1024 + 1)), "supported size"),
    ],
)
def test_turn_schema_v3_rejects_malformed_assistant_tool_text(mutate, match: str) -> None:
    request = ToolUse(
        "read-1",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will read it.",
    )
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("read"),
            request,
            ToolResult("read-1", "notes"),
            AssistantText("done"),
        ),
        tool_ledger=successful_ledger(request),
    )
    value = json.loads(encode_record(turn))
    mutate(value["items"][1])

    with pytest.raises(SessionRecordError, match=match):
        decode_record(json.dumps(value).encode())


def test_turn_schema_v1_decodes_to_generic_arguments_without_rewriting_shape() -> None:
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage("find and read"),
            ToolUse("glob-1", "glob", ToolArguments.from_mapping({"pattern": "src/**/*.py"})),
            ToolResult("glob-1", "src/app.py\n"),
            ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "src/app.py"})),
            ToolResult("read-1", "contents"),
            AssistantText("done"),
        ),
        schema_version=TURN_COMMITTED_LEGACY_SCHEMA_VERSION,
    )

    encoded = encode_record(turn)
    decoded = decode_record(encoded)

    assert decoded == turn
    assert b'"schema_version":1' in encoded
    assert b'"name":"glob","path":"src/**/*.py"' in encoded
    assert b'"arguments"' not in encoded


def test_tool_arguments_are_canonical_immutable_and_bounded() -> None:
    arguments = ToolArguments.from_mapping({"query": "x", "include": "**/*.py"})
    first = arguments.as_mapping()
    first["query"] = "changed"

    assert arguments.as_mapping() == {"include": "**/*.py", "query": "x"}
    assert arguments.canonical_json == '{"include":"**/*.py","query":"x"}'
    with pytest.raises(ValueError, match="version"):
        ToolArguments.from_mapping({"path": "x"}, version=2)
    with pytest.raises(ValueError, match="bytes"):
        ToolArguments.from_mapping({"path": "x" * (16 * 1024)})


def test_codec_restores_conversation_payloads_larger_than_metadata_limit() -> None:
    long_user = "用" * 5000
    long_assistant = "答" * 6000
    long_result = "结果" * 3000
    request = ToolUse("tool-long", "read_file", ToolArguments.from_mapping({"path": "README.md"}))
    turn = TurnCommitted(
        sequence=1,
        committed_at=NOW,
        binding=BindingSnapshot.fake(),
        items=(
            UserMessage(long_user),
            request,
            ToolResult("tool-long", long_result),
            AssistantText(long_assistant),
        ),
        tool_ledger=successful_ledger(request),
    )

    decoded = decode_record(encode_record(turn))

    assert decoded == turn


def test_canonical_codec_is_compact_sorted_and_contains_no_secret_value(tmp_path: Path) -> None:
    binding = BindingSnapshot(
        profile_id="profile-id",
        profile_revision=3,
        profile_name="work",
        profile_fingerprint="a" * 64,
        provider_id="custom",
        protocol="openai_chat_completions",
        selected_model="vendor/model",
        wire_model="vendor/model",
        base_url="https://example.test/v1",
        base_url_source="profile",
        source="profile",
        credential_env="API_TOKEN",
        max_output_tokens=4096,
        temperature=0.2,
        generation=7,
        adapter_version="openai-compat-v1",
        route_fingerprint="b" * 64,
    )
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=binding,
    )

    line = encode_record(header)

    assert line.endswith(b"\n")
    assert b" " not in line
    assert b"API_TOKEN" in line
    assert b"credential_value" not in line
    assert line == encode_record(decode_record(line))


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda value: value.update(secret="x"), "unknown field"),
        (lambda value: value.update(schema_version=3), "unsupported"),
        (lambda value: value.update(sequence=True), "sequence"),
        (lambda value: value["binding"].update(secret="x"), "unknown field"),
    ],
)
def test_decode_fails_closed_on_unknown_version_and_field_types(
    tmp_path: Path, mutate, match: str
) -> None:
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
    )
    value = json.loads(encode_record(header))
    mutate(value)

    with pytest.raises(SessionRecordError, match=match):
        decode_record(json.dumps(value).encode())


def test_replay_rejects_sequence_workspace_filename_and_records_after_close(tmp_path: Path) -> None:
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
    )
    skipped = TurnCommitted(
        sequence=2,
        committed_at=NOW,
        binding=header.binding,
        items=(UserMessage("u"), AssistantText("a")),
    )

    with pytest.raises(SessionRecordError, match="sequence mismatch"):
        replay_records([header, skipped])
    with pytest.raises(SessionRecordError, match="workspace does not match"):
        replay_records([header], expected_workspace="/different")
    with pytest.raises(SessionRecordError, match="file name"):
        replay_records([header], expected_file_name="wrong.jsonl")


def test_recovery_after_close_preserves_closed_state_until_resumed(tmp_path: Path) -> None:
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
    )
    closed = SessionClosed(sequence=1, occurred_at=NOW, reason="closed")
    recovery = Recovery(sequence=2, occurred_at=NOW, truncated_bytes=12)

    recovered = replay_records([header, closed, recovery])

    assert recovered.closed is True
    with pytest.raises(SessionRecordError, match="requires session_resumed"):
        replay_records(
            [
                header,
                closed,
                recovery,
                TurnCommitted(
                    sequence=3,
                    committed_at=NOW,
                    binding=header.binding,
                    items=(UserMessage("u"), AssistantText("a")),
                ),
            ]
        )
    resumed = replay_records(
        [
            header,
            closed,
            recovery,
            SessionResumed(sequence=3, occurred_at=NOW),
        ]
    )
    assert resumed.closed is False


def test_replay_requires_closed_turns_and_strict_tool_causality(tmp_path: Path) -> None:
    header = SessionHeader(
        sequence=0,
        session_id=SESSION_ID,
        workspace=str(tmp_path.resolve()),
        workspace_fingerprint=workspace_fingerprint(tmp_path),
        created_at=NOW,
        binding=BindingSnapshot.fake(),
    )

    cases = [
        (
            UserMessage("u"),
            ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "x"})),
            AssistantText("a"),
        ),
        (UserMessage("u"), ToolResult("one", "x"), AssistantText("a")),
        (
            UserMessage("u"),
            ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "x"})),
            ToolResult("one", "x"),
            ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "y"})),
            ToolResult("one", "y"),
            AssistantText("a"),
        ),
        (
            UserMessage("u"),
            ToolUse("one", "read_file", ToolArguments.from_mapping({"path": "x"})),
            ToolUse("two", "read_file", ToolArguments.from_mapping({"path": "y"})),
            ToolResult("two", "y"),
            ToolResult("one", "x"),
            AssistantText("a"),
        ),
        (UserMessage("u"), AssistantText("middle"), AssistantText("a")),
    ]
    for items in cases:
        turn = TurnCommitted(
            sequence=1,
            committed_at=NOW,
            binding=header.binding,
            items=items,
        )
        with pytest.raises(SessionRecordError):
            replay_records([header, turn])


def test_turn_v3_and_checkpoint_v2_replay_preserve_mixed_retained_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path.resolve()
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        0,
        SESSION_ID,
        str(workspace),
        workspace_fingerprint(workspace),
        NOW,
        binding,
    )
    turns = [
        TurnCommitted(
            sequence=index,
            committed_at=NOW,
            binding=binding,
            items=(UserMessage(f"u{index}"), AssistantText(f"a{index}")),
        )
        for index in range(1, 4)
    ]
    mixed_request = ToolUse(
        "mixed-4",
        "read_file",
        ToolArguments.from_mapping({"path": "README.md"}),
        assistant_text="I will inspect first.",
    )
    turns.append(
        TurnCommitted(
            sequence=4,
            committed_at=NOW,
            binding=binding,
            items=(
                UserMessage("u4"),
                mixed_request,
                ToolResult("mixed-4", "notes"),
                AssistantText("a4"),
            ),
            tool_ledger=successful_ledger(mixed_request),
        )
    )
    prompt = build_compact_prompt()
    summary = EffectiveContextSummary("u1 and u2 were resolved")
    checkpoint = ContextCompacted(
        sequence=5,
        occurred_at=NOW,
        binding=binding,
        source_context_id="ctx-v1-" + "a" * 64,
        result_context_id="ctx-v2-" + "b" * 64,
        source_full_turn_count=4,
        source_effective_turn_count=4,
        retained_from_full_turn=2,
        previous_checkpoint_sequence=None,
        summary=summary.text,
        compact_prompt_version=prompt.version,
        compact_prompt_fingerprint=prompt.fingerprint,
        continuation_version=summary.continuation_version,
        continuation_fingerprint=summary.continuation_fingerprint,
        effective_context_representation_version=2,
        schema_version=2,
    )

    encoded_prefix = b"".join(encode_record(record) for record in [header, *turns])
    decoded = [decode_record(encode_record(record)) for record in [header, *turns, checkpoint]]
    state = replay_records(decoded)

    assert b"".join(encode_record(record) for record in decoded[:5]) == encoded_prefix
    assert len(state.turns) == 4
    assert state.history == tuple(item for turn in turns for item in turn.items)
    assert state.effective_history == turns[2].items + turns[3].items
    assert state.effective_summary == summary
    assert state.latest_checkpoint == checkpoint
    assert state.effective_source == "compact_checkpoint"

    value = json.loads(encode_record(checkpoint))
    value["schema_version"] = 1
    with pytest.raises(SessionRecordError, match="unsupported"):
        decode_record(json.dumps(value).encode())


def test_context_compacted_v3_persists_trigger_and_validates_combinations(
    tmp_path: Path,
) -> None:
    workspace = tmp_path.resolve()
    binding = BindingSnapshot.fake()
    header = SessionHeader(
        0,
        SESSION_ID,
        str(workspace),
        workspace_fingerprint(workspace),
        NOW,
        binding,
    )
    turns = [
        TurnCommitted(
            sequence=index,
            committed_at=NOW,
            binding=binding,
            items=(UserMessage(f"u{index}"), AssistantText(f"a{index}")),
        )
        for index in range(1, 5)
    ]
    prompt = build_compact_prompt()
    checkpoint = ContextCompacted(
        sequence=5,
        occurred_at=NOW,
        binding=binding,
        source_context_id="ctx-v1-" + "a" * 64,
        result_context_id="ctx-v2-" + "b" * 64,
        source_full_turn_count=4,
        source_effective_turn_count=4,
        retained_from_full_turn=2,
        previous_checkpoint_sequence=None,
        summary="summary",
        compact_prompt_version=prompt.version,
        compact_prompt_fingerprint=prompt.fingerprint,
        continuation_version=EffectiveContextSummary("summary").continuation_version,
        continuation_fingerprint=EffectiveContextSummary("summary").continuation_fingerprint,
        effective_context_representation_version=2,
        trigger=CompactionTrigger.HIGH_WATER,
        high_water_percent=80,
    )

    decoded = decode_record(encode_record(checkpoint))
    assert decoded == checkpoint
    state = replay_records([header, *turns, decoded])
    assert state.latest_checkpoint.trigger == CompactionTrigger.HIGH_WATER

    with pytest.raises(SessionRecordError, match="threshold"):
        encode_record(replace(checkpoint, high_water_percent=70))
    with pytest.raises(SessionRecordError, match="threshold"):
        encode_record(
            replace(
                checkpoint,
                trigger=CompactionTrigger.OVERFLOW,
                high_water_percent=80,
            )
        )

    binding = BindingSnapshot.fake()
    with pytest.raises(SessionRecordError, match="credential-free"):
        replace(binding, base_url="https://user:secret@example.test/v1")
    with pytest.raises(SessionRecordError, match="SHA-256"):
        replace(binding, route_fingerprint="short")
