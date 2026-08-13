"""Secure append-only storage for versioned Coquo sessions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock
from typing import BinaryIO
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from coquo.core.actions import ActionIdentity
from coquo.core.contracts import (
    AssistantText,
    AssistantToolBatch,
    CommittedTurn,
    ConversationItem,
    ConversationTurn,
    ProviderOwnedItem,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from coquo.core.hook_contracts import (
    HookAuditLedger,
    HookAuditObservation,
    bounded_hook_audit_limit,
)
from coquo.core.permissions import (
    ApprovalMode,
    PermissionMode,
    PermissionResult,
)
from coquo.core.task_admission import (
    TaskAdmissionOutcome,
    TaskAdmissionProposal,
    canonical_task_admission_id,
)
from coquo.core.compaction import CompactionTrigger
from coquo.providers.usage import ProviderInvocationUsage
from coquo.session_records import (
    ActionAuditState,
    ActionAuthorization,
    ActionExecutionFinished,
    ActionExecutionOutcome,
    ActionExecutionStarted,
    ActionRequested,
    ApprovalAuditOutcome,
    ApprovalResolved,
    AuditRecord,
    BindingSnapshot,
    CompactionFailed,
    ContextCompacted,
    MAX_RECORD_BYTES,
    MAX_RECORDS,
    PermissionDecided,
    Recovery,
    ReplayState,
    RuntimeChanged,
    SessionArchiveChanged,
    SessionClosed,
    SessionForked,
    SessionHeader,
    SESSION_HEADER_SCHEMA_VERSION,
    SessionNamed,
    SessionNameSource,
    SessionPinChanged,
    SessionTitleFallbackReason,
    TaskAdmissionResolved,
    SessionRecord,
    SessionRecordError,
    SessionResumed,
    TurnCommitted,
    TURN_COMMITTED_LEDGER_SCHEMA_VERSION,
    TurnFailed,
    canonical_session_name,
    canonical_session_id,
    decode_record,
    encode_record,
    replay_records,
    workspace_fingerprint,
)

MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_TOOL_LEDGER_QUERY_TURNS = 20
MAX_SESSION_PREVIEW_TURNS = 10
MAX_SESSION_SEARCH_QUERY_CHARACTERS = 256
MAX_SESSION_SEARCH_QUERY_BYTES = 1024
MAX_SESSION_SEARCH_DIRECTORY_ENTRIES = 10_000
MAX_SESSION_SEARCH_CANDIDATES = 100
MAX_SESSION_SEARCH_TRANSCRIPT_BYTES = 16 * 1024 * 1024
MAX_SESSION_SEARCH_MATCHES = 100
MAX_SESSION_SEARCH_EXCERPT_CHARACTERS = 320
MAX_SESSION_SEARCH_EXCERPT_BYTES = 2048
MAX_SESSION_EXPORT_TURNS = 1000
MAX_SESSION_EXPORT_TEXT_BYTES = 1024 * 1024
LATEST_SCHEMA_VERSION = 1
_DIRECTORY_LOCK_NAME = ".directory.lock"
_LATEST_NAME = "latest.json"


class SessionStoreError(RuntimeError):
    """Raised when session persistence cannot proceed safely."""


def _preview_turns(state: ReplayState) -> tuple[SessionPreviewTurn, ...]:
    records = tuple(record for record in state.records if isinstance(record, TurnCommitted))
    if len(records) != len(state.turns):
        raise SessionRecordError("Session turn projection is inconsistent")
    projected: list[SessionPreviewTurn] = []
    for record, turn in zip(records, state.turns, strict=True):
        calls = 0
        failed = 0
        actions: list[str] = []
        sources = 0
        for item in record.items:
            if not isinstance(item, ProviderOwnedItem) or item.item_type != "web_search_call":
                continue
            calls += 1
            mapping = item.as_mapping()
            failed += int(mapping.get("status") == "failed")
            action = mapping.get("action")
            action_type = action.get("type") if isinstance(action, dict) else None
            safe_action = (
                action_type if action_type in {"search", "open_page", "find_in_page"} else "unknown"
            )
            if safe_action not in actions and len(actions) < 8:
                actions.append(safe_action)
            raw_sources = action.get("sources") if isinstance(action, dict) else None
            if isinstance(raw_sources, list):
                sources = min(1000, sources + len(raw_sources[:100]))
        citation_count = min(
            20,
            turn.assistant.text.count("\n- [") if "\n\nSources:\n" in turn.assistant.text else 0,
        )
        summary = (
            ProviderSearchTurnSummary(
                calls,
                failed,
                tuple(actions),
                sources,
                citation_count,
            )
            if calls
            else None
        )
        projected.append(SessionPreviewTurn(turn.user, turn.assistant, summary))
    return tuple(projected)


@dataclass(frozen=True)
class TurnToolLedger:
    """One committed turn's replay-validated tool-ledger availability and data."""

    turn_number: int
    record_sequence: int
    committed_at: str
    schema_version: int
    ledger: ToolTurnLedger | None


@dataclass(frozen=True)
class ToolLedgerQueryResult:
    """One bounded recent-turn projection from a strictly replayed Session."""

    total_turns: int
    turns: tuple[TurnToolLedger, ...]


@dataclass(frozen=True)
class SkillLoadAudit:
    """One replay-derived Skill load attempt without exposing instruction content."""

    tool_use_id: str
    name: str | None
    requested_fingerprint: str | None
    outcome: ToolRequestOutcome
    result_code: str | None
    loaded_source: str | None
    loaded_fingerprint: str | None


@dataclass(frozen=True)
class TurnSkillAudit:
    """Bounded Skill load attempts in one committed Session Turn."""

    turn_number: int
    record_sequence: int
    committed_at: str
    loads: tuple[SkillLoadAudit, ...]


@dataclass(frozen=True)
class SkillAuditQueryResult:
    """Read-only Skill load projection over recent committed Turns."""

    total_turns: int
    turns: tuple[TurnSkillAudit, ...]


@dataclass(frozen=True)
class ProviderSearchTurnSummary:
    call_count: int
    failed_count: int
    action_types: tuple[str, ...]
    source_count: int
    citation_count: int


@dataclass(frozen=True)
class SessionPreviewTurn:
    user: UserMessage
    assistant: AssistantText
    provider_search: ProviderSearchTurnSummary | None


@dataclass(frozen=True)
class SessionPreview:
    """Bounded final-text projection from one strictly replayed Session."""

    info: SessionInfo
    total_turns: int
    turns: tuple[SessionPreviewTurn, ...]


@dataclass(frozen=True)
class SessionTurnRange:
    """One bounded chronological range of complete conversation turns."""

    info: SessionInfo
    total_turns: int
    start_turn: int
    turns: tuple[SessionPreviewTurn, ...]


@dataclass(frozen=True)
class SessionTurnEvidence:
    """Content-free identity and bounded accounting for one committed Turn record."""

    session_id: str
    turn_number: int
    record_sequence: int
    record_sha256: str
    committed_at: str
    user_message_sha256: str
    provider_usage_available: bool
    provider_invocations: int
    input_tokens: int
    output_tokens: int
    known_token_invocations: int
    unknown_token_invocations: int
    tool_usage_available: bool
    tool_requests: int
    tool_admitted: int
    tool_dispatched: int
    tool_succeeded: int
    tool_unsuccessful: int


@dataclass(frozen=True)
class SessionSearchMatch:
    """One bounded literal match from final user or assistant text."""

    info: SessionInfo
    turn_number: int
    role: str
    line_number: int
    excerpt: str


@dataclass(frozen=True)
class SessionSearchResult:
    """Bounded cross-Session literal search result and completeness facts."""

    query: str
    candidate_sessions: int
    scanned_sessions: int
    scanned_transcript_bytes: int
    matches: tuple[SessionSearchMatch, ...]
    truncated: bool


@dataclass(frozen=True)
class SessionConversationExport:
    """Complete bounded final-text conversation projection for export."""

    info: SessionInfo
    turns: tuple[ConversationTurn, ...]


class SessionDiagnosisStatus(StrEnum):
    """Closed read-only diagnosis outcomes."""

    VALID = "valid"
    REPAIRABLE_TAIL = "repairable_tail"
    INVALID = "invalid"


@dataclass(frozen=True)
class SessionDiagnosis:
    """Bounded diagnosis without transcript mutation or repair."""

    session_id: str
    status: SessionDiagnosisStatus
    code: str
    transcript_bytes: int
    record_count: int | None
    turn_count: int | None
    recoverable_tail_bytes: int | None = None


@dataclass(frozen=True)
class SessionRepairResult:
    """Durable explicit incomplete-tail repair with a retained backup."""

    info: SessionInfo
    truncated_bytes: int
    backup_path: Path


def query_tool_ledgers(state: ReplayState, limit: int) -> ToolLedgerQueryResult:
    """Project the most recent committed turns without exposing conversation content."""
    if type(state) is not ReplayState:
        raise SessionStoreError("tool ledger query requires replayed Session state")
    if type(limit) is not int or not 1 <= limit <= MAX_TOOL_LEDGER_QUERY_TURNS:
        raise SessionStoreError(
            f"tool ledger limit must be between 1 and {MAX_TOOL_LEDGER_QUERY_TURNS}"
        )
    committed = tuple(record for record in state.records if isinstance(record, TurnCommitted))
    first_turn_number = max(1, len(committed) - limit + 1)
    selected = committed[-limit:]
    turns = tuple(
        TurnToolLedger(
            turn_number=first_turn_number + offset,
            record_sequence=record.sequence,
            committed_at=record.committed_at,
            schema_version=record.schema_version,
            ledger=(
                record.tool_ledger
                if record.schema_version >= TURN_COMMITTED_LEDGER_SCHEMA_VERSION
                else None
            ),
        )
        for offset, record in enumerate(selected)
    )
    return ToolLedgerQueryResult(len(committed), turns)


def _query_skill_load_audits(state: ReplayState, limit: int) -> SkillAuditQueryResult:
    committed = tuple(record for record in state.records if isinstance(record, TurnCommitted))
    first_turn_number = max(1, len(committed) - limit + 1)
    turns: list[TurnSkillAudit] = []
    for offset, record in enumerate(committed[-limit:]):
        loads = _skill_loads_for_record(record)
        if loads:
            turns.append(
                TurnSkillAudit(
                    turn_number=first_turn_number + offset,
                    record_sequence=record.sequence,
                    committed_at=record.committed_at,
                    loads=loads,
                )
            )
    return SkillAuditQueryResult(len(committed), tuple(turns))


def _skill_loads_for_record(record: TurnCommitted) -> tuple[SkillLoadAudit, ...]:
    requests: list[ToolUse] = []
    results: dict[str, ToolResult] = {}
    for item in record.items:
        if isinstance(item, ToolUse):
            requests.append(item)
        elif isinstance(item, AssistantToolBatch):
            requests.extend(item.tool_uses)
        elif isinstance(item, ToolResult):
            results[item.tool_use_id] = item
    outcomes = {entry.tool_use_id: entry for entry in _copied_tool_ledger(record).entries}
    loads: list[SkillLoadAudit] = []
    for request in requests:
        if request.name != "skill_load":
            continue
        arguments = request.arguments.as_mapping()
        name = arguments.get("name")
        fingerprint = arguments.get("fingerprint")
        entry = outcomes[request.tool_use_id]
        loaded_source: str | None = None
        loaded_fingerprint: str | None = None
        result = results.get(request.tool_use_id)
        if result is not None and not result.is_error:
            try:
                payload = json.loads(result.content)
            except (TypeError, json.JSONDecodeError):
                payload = None
            if (
                entry.outcome is ToolRequestOutcome.SUCCEEDED
                and isinstance(name, str)
                and isinstance(fingerprint, str)
                and isinstance(payload, dict)
                and payload.get("kind") == "skill_loaded"
                and payload.get("name") == name
                and payload.get("fingerprint") == fingerprint
            ):
                source = payload.get("source")
                loaded = payload.get("fingerprint")
                loaded_source = (
                    source if source in {"workspace-local", "project-shared", "user"} else None
                )
                loaded_fingerprint = loaded if isinstance(loaded, str) else None
        loads.append(
            SkillLoadAudit(
                tool_use_id=request.tool_use_id,
                name=name if isinstance(name, str) else None,
                requested_fingerprint=(fingerprint if isinstance(fingerprint, str) else None),
                outcome=entry.outcome,
                result_code=entry.result_code,
                loaded_source=loaded_source,
                loaded_fingerprint=loaded_fingerprint,
            )
        )
    return tuple(loads)


class SessionLockedError(SessionStoreError):
    """Raised when another writer already owns a session."""


class SessionNameConflictError(SessionStoreError):
    """Raised when an automatic title conflicts with another workspace Session."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Session name already exists: {name}")


class AtomicJsonWriteError(SessionStoreError):
    """Report whether an atomic JSON write reached pathname replacement."""

    def __init__(self, message: str, *, replaced: bool) -> None:
        self.replaced = replaced
        super().__init__(message)


class SessionResumeStaleError(SessionStoreError):
    """Raised when a prepared target changed before its first durable write."""


class ResumeDurableStage(StrEnum):
    NONE = "none"
    RECOVERY_DURABLE = "recovery_durable"
    SESSION_RESUMED_DURABLE = "session_resumed_durable"
    DURABILITY_UNKNOWN = "durability_unknown"


class LatestUpdateStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    UPDATED = "updated"
    FAILED_UNCHANGED = "failed_unchanged"
    REPLACED_DURABILITY_UNKNOWN = "replaced_durability_unknown"


class SessionResumeCommitError(SessionStoreError):
    """Describe the durable resume stage reached before a storage failure."""

    def __init__(
        self,
        message: str,
        *,
        stage: ResumeDurableStage,
        recovery_applied: bool = False,
        session_resumed_applied: bool = False,
    ) -> None:
        self.stage = stage
        self.recovery_applied = recovery_applied
        self.session_resumed_applied = session_resumed_applied
        super().__init__(message)


class ActionOutcomeAuditError(SessionStoreError):
    """Report a known executor outcome whose final audit append did not commit."""

    def __init__(
        self,
        message: str,
        *,
        action_request_id: str,
        action_digest: str,
        execution_outcome: ActionExecutionOutcome,
        result_code: str,
    ) -> None:
        self.action_request_id = action_request_id
        self.action_digest = action_digest
        self.execution_outcome = execution_outcome
        self.result_code = result_code
        super().__init__(message)


@dataclass(frozen=True)
class SessionInfo:
    """Validated, redacted metadata for one stored session."""

    session_id: str
    path: Path
    workspace: str
    workspace_fingerprint: str
    created_at: str
    record_count: int
    turn_count: int
    closed: bool
    binding: BindingSnapshot
    name: str = "New session"
    name_source: SessionNameSource = SessionNameSource.DEFAULT
    archived: bool = False
    pinned: bool = False
    title_fallback_reason: SessionTitleFallbackReason | None = None
    forked_from_session_id: str | None = None
    forked_from_turn: int | None = None


@dataclass(frozen=True)
class SessionCreationRequest:
    """Optional creation controls for detached, workspace-bound Sessions."""

    session_id: str | None = None
    publish_latest: bool = True
    name: str | None = None

    def __post_init__(self) -> None:
        if self.session_id is not None:
            try:
                canonical_session_id(self.session_id)
            except SessionRecordError as error:
                raise ValueError(f"session creation ID is invalid: {error}") from None
        if type(self.publish_latest) is not bool:
            raise ValueError("session creation latest flag is invalid")
        if self.name is not None:
            try:
                if canonical_session_name(self.name) != self.name:
                    raise ValueError("session creation name is not canonical")
            except SessionRecordError as error:
                raise ValueError(f"session creation name is invalid: {error}") from None


@dataclass(frozen=True)
class TaskAdmissionInfo:
    """One committed Task admission proposal and its optional durable resolution."""

    proposal: TaskAdmissionProposal
    session_id: str
    session_name: str
    turn_record_sequence: int
    turn_number: int
    committed_at: str
    outcome: TaskAdmissionOutcome | None = None
    task_id: str | None = None
    rejection_reason: str | None = None
    resolved_at: str | None = None

    @property
    def status(self) -> str:
        return self.outcome.value if self.outcome is not None else "pending"


@dataclass(frozen=True)
class TranscriptStaleToken:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class LatestStaleToken:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str
    data: bytes


@dataclass(frozen=True)
class PendingTailRecovery:
    truncate_offset: int
    truncated_bytes: int


@dataclass(frozen=True)
class CommittedSessionResume:
    writer: SessionWriter
    recovery_applied: bool
    latest_status: LatestUpdateStatus
    latest_diagnostic: str | None = None


_ACTIVE_WRITERS: set[str] = set()
_ACTIVE_WRITERS_GUARD = Lock()


def utc_now() -> str:
    """Return a canonical UTC timestamp suitable for a transcript record."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SessionStore:
    """Create, select, validate, and exclusively open workspace-bound sessions."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise SessionStoreError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise SessionStoreError(
                f"workspace does not exist or is inaccessible: {requested}"
            ) from None
        if not resolved.is_dir():
            raise SessionStoreError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".coquo" / "sessions" / self.workspace_fingerprint
        self._uuid_factory = uuid_factory
        self._clock = clock

    def create(
        self,
        binding: BindingSnapshot,
        *,
        creation: SessionCreationRequest | None = None,
    ) -> SessionWriter:
        """Create a collision-safe transcript, update latest, and keep its writer lock."""
        if creation is not None and type(creation) is not SessionCreationRequest:
            raise SessionStoreError("session creation request is invalid")
        self._ensure_root()
        with self._directory_lock():
            session_id = (
                _factory_session_id(self._uuid_factory)
                if creation is None or creation.session_id is None
                else creation.session_id
            )
            transcript_path = self.root / f"{session_id}.jsonl"
            lock_path = self.root / f"{session_id}.lock"
            if transcript_path.exists() or transcript_path.is_symlink():
                raise SessionStoreError(f"session ID collision: {session_id}")
            lock_stream = self._acquire_writer_lock(lock_path, create_exclusive=True)
            try:
                header = SessionHeader(
                    sequence=0,
                    session_id=session_id,
                    workspace=str(self.workspace),
                    workspace_fingerprint=self.workspace_fingerprint,
                    created_at=self._clock(),
                    binding=binding,
                    name=(
                        _next_default_session_name(self.root)
                        if creation is None or creation.name is None
                        else creation.name
                    ),
                    schema_version=SESSION_HEADER_SCHEMA_VERSION,
                )
                _create_transcript(transcript_path, encode_record(header))
                if creation is None or creation.publish_latest:
                    self._write_latest(session_id)
            except AtomicJsonWriteError as error:
                lock_stream.close()
                _release_active_writer(lock_path)
                if not error.replaced:
                    _remove_created_session_files(transcript_path, lock_path)
                raise
            except Exception:
                lock_stream.close()
                _release_active_writer(lock_path)
                _remove_created_session_files(transcript_path, lock_path)
                raise
        state = replay_records(
            [header],
            expected_workspace=str(self.workspace),
            expected_workspace_fingerprint=self.workspace_fingerprint,
            expected_session_id=session_id,
            expected_file_name=transcript_path.name,
        )
        transcript_descriptor = _open_existing_transcript(transcript_path, writable=True)
        return SessionWriter(
            self,
            transcript_path,
            lock_path,
            lock_stream,
            transcript_descriptor,
            state,
        )

    def prepare_resume(self, selector: str | Path) -> PreparedSessionResume:
        """Lock and replay one resume target without durable mutation."""
        _validate_existing_session_root(self.root, self.workspace)
        selector_was_latest = selector == "latest"
        latest_token: LatestStaleToken | None = None
        if selector_was_latest:
            with self._directory_lock(existing_only=True):
                latest_path = self.root / _LATEST_NAME
                latest_data, latest_info = _read_regular_file_descriptor(latest_path)
                latest_token = _latest_token(latest_data, latest_info)
                path = self._decode_latest_data(latest_data)
        else:
            path = self._select_path_readonly(selector)
        session_id = _session_id_from_path(path)
        lock_path = self.root / f"{session_id}.lock"
        lock_stream = self._acquire_writer_lock(
            lock_path,
            create_exclusive=False,
            existing_only=True,
        )
        lock_info = os.fstat(lock_stream.fileno())
        transcript_descriptor: int | None = None
        try:
            transcript_descriptor = _open_existing_transcript(path)
            data, info = _read_descriptor_bytes(transcript_descriptor, path)
            state, pending_recovery = self._prepare_replay(path, data)
            token = _transcript_token(data, info)
            prepared = PreparedSessionResume(
                self,
                path,
                lock_path,
                lock_stream,
                (lock_info.st_dev, lock_info.st_ino),
                transcript_descriptor,
                state,
                data,
                token,
                pending_recovery,
                selector_was_latest=selector_was_latest,
                latest_token=latest_token,
            )
            transcript_descriptor = None
            return prepared
        except BaseException:
            if transcript_descriptor is not None:
                os.close(transcript_descriptor)
            _release_writer_lease(lock_path, lock_stream)
            raise

    def open(
        self,
        selector: str | Path,
        *,
        binding: BindingSnapshot | None = None,
    ) -> SessionWriter:
        """Compatibility wrapper over the prepared resume transaction."""
        prepared = self.prepare_resume(selector)
        try:
            return prepared.commit(binding=binding).writer
        except BaseException:
            prepared.abort()
            raise

    def show(self, selector: str | Path) -> SessionInfo:
        """Strictly validate and describe a session without repairing or updating it."""
        self._ensure_root()
        path = self._select_path(selector)
        return _info(path, self._load_state(path, allow_repair=False))

    def inspect(self, selector: str | Path) -> SessionInfo:
        """Describe an existing Session without creating storage or repairing state."""
        _validate_existing_session_root(self.root, self.workspace)
        path = self._read_latest() if selector == "latest" else self._select_path_readonly(selector)
        return _info(path, self._load_state(path, allow_repair=False))

    def preview(self, selector: str | Path, limit: int) -> SessionPreview:
        """Strictly replay bounded recent final-text turns without durable mutation."""
        if type(limit) is not int or not 1 <= limit <= MAX_SESSION_PREVIEW_TURNS:
            raise SessionStoreError(
                f"session preview limit must be between 1 and {MAX_SESSION_PREVIEW_TURNS}"
            )
        _validate_existing_session_root(self.root, self.workspace)
        path = self._read_latest() if selector == "latest" else self._select_path_readonly(selector)
        state = self._load_state(path, allow_repair=False)
        turns = _preview_turns(state)
        return SessionPreview(
            info=_info(path, state),
            total_turns=len(turns),
            turns=turns[-limit:],
        )

    def turn_range(
        self,
        selector: str | Path,
        start_turn: int,
        count: int,
    ) -> SessionTurnRange:
        """Strictly replay one bounded 1-based range of complete turns."""
        if type(start_turn) is not int or start_turn < 1:
            raise SessionStoreError("session turn start must be a positive integer")
        if type(count) is not int or not 1 <= count <= MAX_SESSION_PREVIEW_TURNS:
            raise SessionStoreError(
                f"session turn count must be between 1 and {MAX_SESSION_PREVIEW_TURNS}"
            )
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        turns = _preview_turns(state)
        total = len(turns)
        if total and start_turn > total:
            raise SessionStoreError(f"session turn start exceeds the {total} committed turns")
        if not total and start_turn != 1:
            raise SessionStoreError("empty Session only accepts turn start 1")
        return SessionTurnRange(
            info=_info(path, state),
            total_turns=total,
            start_turn=start_turn,
            turns=turns[start_turn - 1 : start_turn - 1 + count],
        )

    def turn_evidence(
        self,
        selector: str | Path,
        record_sequence: int,
    ) -> SessionTurnEvidence:
        """Return exact raw-record identity for one strictly replayed committed Turn."""
        if type(record_sequence) is not int or record_sequence < 1:
            raise SessionStoreError("Session Turn record sequence must be a positive integer")
        path = self._resolve_existing_path(selector)
        data, state = self._strict_snapshot(path)
        if record_sequence >= len(state.records):
            raise SessionStoreError(
                f"Session Turn record sequence exceeds the {len(state.records) - 1} records"
            )
        if not isinstance(state.records[record_sequence], TurnCommitted):
            raise SessionStoreError("selected Session record is not a committed Turn")
        return _session_turn_evidence(data, state, record_sequence)

    def committed_turn(self, selector: str | Path, record_sequence: int) -> CommittedTurn:
        """Return one strictly replayed complete Turn including tool causality and ledger."""
        if type(record_sequence) is not int or record_sequence < 1:
            raise SessionStoreError("Session Turn record sequence must be a positive integer")
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        if record_sequence >= len(state.records):
            raise SessionStoreError(
                f"Session Turn record sequence exceeds the {len(state.records) - 1} records"
            )
        record = state.records[record_sequence]
        if not isinstance(record, TurnCommitted):
            raise SessionStoreError("selected Session record is not a committed Turn")
        user = record.items[0]
        assistant = record.items[-1]
        if not isinstance(user, UserMessage) or not isinstance(assistant, AssistantText):
            raise SessionStoreError("committed Session Turn boundary is invalid")
        return CommittedTurn(
            record.items,
            user,
            assistant,
            record.tool_ledger,
            record.hook_audit,
        )

    def hook_evaluations(
        self,
        selector: str | Path,
        limit: int = 20,
    ) -> tuple[HookAuditObservation, ...]:
        """Project recent content-free Hook evaluations from one strict Session replay."""
        try:
            bounded_hook_audit_limit(limit)
        except ValueError as error:
            raise SessionStoreError(str(error)) from None
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        observations = tuple(
            HookAuditObservation(record.record_type, record.sequence, entry)
            for record in state.records
            if isinstance(record, (TurnCommitted, TurnFailed))
            for entry in record.hook_audit.entries
        )
        return observations[-limit:]

    def find_turn_evidence(
        self,
        selector: str | Path,
        *,
        after_record_sequence: int,
        user_message_sha256: str,
    ) -> tuple[SessionTurnEvidence, ...]:
        """Find exact committed Turns after a durable baseline by pending-user digest."""
        if type(after_record_sequence) is not int or after_record_sequence < 0:
            raise SessionStoreError("Session record baseline must be a nonnegative integer")
        if (
            not isinstance(user_message_sha256, str)
            or len(user_message_sha256) != 64
            or any(character not in "0123456789abcdef" for character in user_message_sha256)
        ):
            raise SessionStoreError("Session user-message SHA-256 is invalid")
        path = self._resolve_existing_path(selector)
        data, state = self._strict_snapshot(path)
        matches: list[SessionTurnEvidence] = []
        for sequence, record in enumerate(state.records):
            if sequence <= after_record_sequence or not isinstance(record, TurnCommitted):
                continue
            user = next((item for item in record.items if isinstance(item, UserMessage)), None)
            if user is None:
                raise SessionStoreError("committed Turn has no canonical user message")
            digest = hashlib.sha256(user.text.encode("utf-8")).hexdigest()
            if digest == user_message_sha256:
                matches.append(_session_turn_evidence(data, state, sequence))
        return tuple(matches)

    def conversation_export(self, selector: str | Path) -> SessionConversationExport:
        """Return a complete bounded final-text projection suitable for stdout export."""
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        if len(state.turns) > MAX_SESSION_EXPORT_TURNS:
            raise SessionStoreError(
                f"session export exceeds {MAX_SESSION_EXPORT_TURNS} complete turns"
            )
        text_bytes = sum(
            len(turn.user.text.encode("utf-8")) + len(turn.assistant.text.encode("utf-8"))
            for turn in state.turns
        )
        if text_bytes > MAX_SESSION_EXPORT_TEXT_BYTES:
            raise SessionStoreError(
                f"session export text exceeds {MAX_SESSION_EXPORT_TEXT_BYTES} UTF-8 bytes"
            )
        return SessionConversationExport(_info(path, state), state.turns)

    def search(self, query: str, limit: int) -> SessionSearchResult:
        """Search final dialogue text across a bounded set of strictly replayed Sessions."""
        canonical = _canonical_session_search_query(query)
        if type(limit) is not int or not 1 <= limit <= MAX_SESSION_SEARCH_MATCHES:
            raise SessionStoreError(
                f"session search limit must be between 1 and {MAX_SESSION_SEARCH_MATCHES}"
            )
        _validate_existing_session_root(self.root, self.workspace)
        paths = _bounded_session_paths(self.root)
        candidate_count = len(paths)
        truncated = candidate_count > MAX_SESSION_SEARCH_CANDIDATES
        selected = paths[:MAX_SESSION_SEARCH_CANDIDATES]
        loaded: list[tuple[SessionInfo, ReplayState]] = []
        scanned_bytes = 0
        for path in selected:
            try:
                size = path.lstat().st_size
            except OSError:
                raise SessionStoreError(f"session transcript is inaccessible: {path}") from None
            if scanned_bytes + size > MAX_SESSION_SEARCH_TRANSCRIPT_BYTES:
                truncated = True
                break
            state = self._load_state(path, allow_repair=False)
            scanned_bytes += size
            loaded.append((_info(path, state), state))
        loaded.sort(key=lambda item: (item[0].created_at, item[0].session_id), reverse=True)
        matches: list[SessionSearchMatch] = []
        for info, state in loaded:
            for turn_number, turn in enumerate(state.turns, start=1):
                for role, text in (("user", turn.user.text), ("assistant", turn.assistant.text)):
                    for line_number, line in enumerate(text.splitlines() or ("",), start=1):
                        if canonical not in line:
                            continue
                        matches.append(
                            SessionSearchMatch(
                                info=info,
                                turn_number=turn_number,
                                role=role,
                                line_number=line_number,
                                excerpt=_bounded_search_excerpt(line, canonical),
                            )
                        )
                        if len(matches) == limit:
                            return SessionSearchResult(
                                canonical,
                                candidate_count,
                                len(loaded),
                                scanned_bytes,
                                tuple(matches),
                                True,
                            )
        return SessionSearchResult(
            canonical,
            candidate_count,
            len(loaded),
            scanned_bytes,
            tuple(matches),
            truncated,
        )

    def fork(
        self,
        selector: str | Path,
        through_turn: int,
        *,
        binding: BindingSnapshot | None = None,
    ) -> SessionWriter:
        """Materialize complete parent turns into a new provenance-linked Session."""
        if type(through_turn) is not int or through_turn < 1:
            raise SessionStoreError("session fork turn must be a positive integer")
        source_path = self._resolve_existing_path(selector)
        source_data, source_state = self._strict_snapshot(source_path)
        if through_turn > len(source_state.turns):
            raise SessionStoreError(
                f"session fork turn exceeds the {len(source_state.turns)} committed turns"
            )
        fork_binding = binding or source_state.binding
        fork_binding.__post_init__()
        source_info = _info(source_path, source_state)
        committed = tuple(
            record for record in source_state.records if isinstance(record, TurnCommitted)
        )[:through_turn]
        self._ensure_root()
        with self._directory_lock():
            session_id = _factory_session_id(self._uuid_factory)
            transcript_path = self.root / f"{session_id}.jsonl"
            lock_path = self.root / f"{session_id}.lock"
            if transcript_path.exists() or transcript_path.is_symlink():
                raise SessionStoreError(f"session ID collision: {session_id}")
            lock_stream = self._acquire_writer_lock(lock_path, create_exclusive=True)
            try:
                fork_name = _fork_session_name(source_info.name, session_id)
                header = SessionHeader(
                    sequence=0,
                    session_id=session_id,
                    workspace=str(self.workspace),
                    workspace_fingerprint=self.workspace_fingerprint,
                    created_at=self._clock(),
                    binding=fork_binding,
                    name=fork_name,
                    schema_version=SESSION_HEADER_SCHEMA_VERSION,
                )
                forked = SessionForked(
                    sequence=1,
                    occurred_at=self._clock(),
                    source_session_id=source_info.session_id,
                    source_turn_count=through_turn,
                    source_transcript_sha256=hashlib.sha256(source_data).hexdigest(),
                )
                records: list[SessionRecord] = [header, forked]
                for source_record in committed:
                    records.append(
                        TurnCommitted(
                            sequence=len(records),
                            committed_at=source_record.committed_at,
                            binding=source_record.binding,
                            items=source_record.items,
                            tool_ledger=_copied_tool_ledger(source_record),
                            hook_audit=source_record.hook_audit,
                            provider_usage=(),
                            session_name=None,
                            session_name_source=None,
                            session_title_fallback_reason=None,
                        )
                    )
                records.append(
                    RuntimeChanged(
                        sequence=len(records),
                        occurred_at=self._clock(),
                        binding=fork_binding,
                        reason="session_forked_current_runtime",
                    )
                )
                records.append(
                    SessionNamed(
                        sequence=len(records),
                        occurred_at=self._clock(),
                        name=fork_name,
                        source=SessionNameSource.AUTO,
                    )
                )
                state = replay_records(
                    records,
                    expected_workspace=str(self.workspace),
                    expected_workspace_fingerprint=self.workspace_fingerprint,
                    expected_session_id=session_id,
                    expected_file_name=transcript_path.name,
                )
                payload = b"".join(encode_record(record) for record in records)
                if len(payload) > MAX_TRANSCRIPT_BYTES:
                    raise SessionStoreError(
                        f"forked session transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes"
                    )
                _create_transcript(transcript_path, payload)
                self._write_latest(session_id)
            except AtomicJsonWriteError as error:
                lock_stream.close()
                _release_active_writer(lock_path)
                if not error.replaced:
                    _remove_created_session_files(transcript_path, lock_path)
                raise
            except Exception:
                lock_stream.close()
                _release_active_writer(lock_path)
                _remove_created_session_files(transcript_path, lock_path)
                raise
        descriptor = _open_existing_transcript(transcript_path, writable=True)
        return SessionWriter(
            self,
            transcript_path,
            lock_path,
            lock_stream,
            descriptor,
            state,
        )

    def diagnose(self, selector: str | Path) -> SessionDiagnosis:
        """Classify transcript integrity without mutation, repair, or writer lease."""
        path = self._resolve_existing_path(selector)
        data, _ = _read_path_snapshot(path)
        session_id = _session_id_from_path(path)
        if not data:
            return SessionDiagnosis(
                session_id, SessionDiagnosisStatus.INVALID, "empty", 0, None, None
            )
        if data.endswith(b"\n"):
            try:
                state = self._replay(path, _decode_lines(data))
            except SessionStoreError:
                return SessionDiagnosis(
                    session_id,
                    SessionDiagnosisStatus.INVALID,
                    "invalid_complete_transcript",
                    len(data),
                    None,
                    None,
                )
            return SessionDiagnosis(
                session_id,
                SessionDiagnosisStatus.VALID,
                "ok",
                len(data),
                len(state.records),
                len(state.turns),
            )
        tail_start = data.rfind(b"\n") + 1
        tail = data[tail_start:]
        try:
            json.loads(tail.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            if tail_start == 0:
                return SessionDiagnosis(
                    session_id,
                    SessionDiagnosisStatus.INVALID,
                    "incomplete_header",
                    len(data),
                    None,
                    None,
                )
            try:
                state = self._replay(path, _decode_lines(data[:tail_start]))
            except SessionStoreError:
                return SessionDiagnosis(
                    session_id,
                    SessionDiagnosisStatus.INVALID,
                    "invalid_prefix_before_tail",
                    len(data),
                    None,
                    None,
                )
            return SessionDiagnosis(
                session_id,
                SessionDiagnosisStatus.REPAIRABLE_TAIL,
                "incomplete_final_record",
                len(data),
                len(state.records),
                len(state.turns),
                len(tail),
            )
        return SessionDiagnosis(
            session_id,
            SessionDiagnosisStatus.INVALID,
            "complete_record_missing_newline",
            len(data),
            None,
            None,
        )

    def repair(self, selector: str | Path) -> SessionRepairResult:
        """Back up and repair only a replay-valid prefix plus incomplete final tail."""
        path = self._resolve_existing_path(selector)
        session_id = _session_id_from_path(path)
        lock_path = self.root / f"{session_id}.lock"
        lock_stream = self._acquire_writer_lock(
            lock_path,
            create_exclusive=False,
            existing_only=True,
        )
        descriptor: int | None = None
        try:
            descriptor = _open_existing_transcript(path, writable=True)
            with self._directory_lock(existing_only=True):
                data, info = _read_descriptor_bytes(descriptor, path)
                path_info = path.lstat()
                if path.is_symlink() or (path_info.st_dev, path_info.st_ino) != (
                    info.st_dev,
                    info.st_ino,
                ):
                    raise SessionResumeStaleError("Session changed before explicit repair")
                state, pending = self._prepare_replay(path, data)
                if pending is None:
                    raise SessionStoreError("session transcript does not need tail repair")
                backup_path = _create_repair_backup(path, data)
                recovery = Recovery(
                    sequence=state.next_sequence,
                    occurred_at=self._clock(),
                    truncated_bytes=pending.truncated_bytes,
                )
                _truncate_and_append_recovery_descriptor(
                    descriptor,
                    path,
                    pending.truncate_offset,
                    recovery,
                )
                repaired_data, _ = _read_descriptor_bytes(descriptor, path)
                repaired_state = self._replay(path, _decode_lines(repaired_data))
                return SessionRepairResult(
                    _info(path, repaired_state),
                    pending.truncated_bytes,
                    backup_path,
                )
        finally:
            if descriptor is not None:
                os.close(descriptor)
            _release_writer_lease(lock_path, lock_stream)

    def _resolve_existing_path(self, selector: str | Path) -> Path:
        _validate_existing_session_root(self.root, self.workspace)
        return self._read_latest() if selector == "latest" else self._select_path_readonly(selector)

    def _strict_snapshot(self, path: Path) -> tuple[bytes, ReplayState]:
        data, _ = _read_path_snapshot(path)
        if not data.endswith(b"\n"):
            raise SessionStoreError("session fork source must be a complete strict transcript")
        return data, self._replay(path, _decode_lines(data))

    def action_audits(self, selector: str | Path) -> tuple[ActionAuditState, ...]:
        """Strictly replay and return one session's Host-only action lifecycles."""
        _validate_existing_session_root(self.root, self.workspace)
        path = self._read_latest() if selector == "latest" else self._select_path_readonly(selector)
        return self._load_state(path, allow_repair=False).action_audits

    def tool_ledgers(self, selector: str | Path, limit: int) -> ToolLedgerQueryResult:
        """Strictly replay and return bounded recent per-turn tool ledgers."""
        _validate_existing_session_root(self.root, self.workspace)
        path = self._read_latest() if selector == "latest" else self._select_path_readonly(selector)
        return query_tool_ledgers(self._load_state(path, allow_repair=False), limit)

    def skill_load_audits(self, selector: str | Path, limit: int = 100) -> SkillAuditQueryResult:
        """Strictly replay recent committed Turns and project only Skill load identities."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise SessionStoreError("Skill audit limit must be between 1 and 100")
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        return _query_skill_load_audits(state, limit)

    def skill_load_audit(self, selector: str | Path, record_sequence: int) -> TurnSkillAudit:
        """Project Skill loads from one exact replay-validated committed Turn."""
        return self.skill_load_audits_for_records(selector, (record_sequence,))[0]

    def skill_load_audits_for_records(
        self, selector: str | Path, record_sequences: tuple[int, ...]
    ) -> tuple[TurnSkillAudit, ...]:
        """Project exact committed Turn Skill loads through one strict Session replay."""
        if (
            not isinstance(record_sequences, tuple)
            or len(record_sequences) > 100
            or len(set(record_sequences)) != len(record_sequences)
            or any(type(sequence) is not int or sequence < 1 for sequence in record_sequences)
        ):
            raise SessionStoreError(
                "Skill audit record sequences must be up to 100 unique positive integers"
            )
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        audits: list[TurnSkillAudit] = []
        for record_sequence in record_sequences:
            if record_sequence >= len(state.records):
                raise SessionStoreError(
                    f"Session Turn record sequence exceeds the {len(state.records) - 1} records"
                )
            record = state.records[record_sequence]
            if not isinstance(record, TurnCommitted):
                raise SessionStoreError("selected Session record is not a committed Turn")
            audits.append(
                TurnSkillAudit(
                    turn_number=sum(
                        isinstance(candidate, TurnCommitted)
                        for candidate in state.records[: record_sequence + 1]
                    ),
                    record_sequence=record_sequence,
                    committed_at=record.committed_at,
                    loads=_skill_loads_for_record(record),
                )
            )
        return tuple(audits)

    def task_admissions(self, selector: str | Path) -> tuple[TaskAdmissionInfo, ...]:
        """Strictly derive committed Task admission state without mutation or provider work."""
        path = self._resolve_existing_path(selector)
        state = self._load_state(path, allow_repair=False)
        return _task_admission_infos(_info(path, state), state)

    def list(self) -> tuple[SessionInfo, ...]:
        """Return all strictly validated transcripts, newest first."""
        if not self.root.exists() and not self.root.is_symlink():
            return ()
        self._ensure_root()
        infos: list[SessionInfo] = []
        try:
            entries = tuple(self.root.iterdir())
        except OSError:
            raise SessionStoreError(f"could not list session directory: {self.root}") from None
        for path in entries:
            if path.name.endswith(".jsonl"):
                _session_id_from_path(path)
                infos.append(_info(path, self._load_state(path, allow_repair=False)))
        return tuple(
            sorted(infos, key=lambda item: (item.created_at, item.session_id), reverse=True)
        )

    def _prepare_replay(
        self,
        path: Path,
        data: bytes,
    ) -> tuple[ReplayState, PendingTailRecovery | None]:
        if not data:
            raise SessionStoreError("session transcript is empty")
        if len(data) > MAX_TRANSCRIPT_BYTES:
            raise SessionStoreError(f"session transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes")
        if data.endswith(b"\n"):
            return self._replay(path, _decode_lines(data)), None
        tail_start = data.rfind(b"\n") + 1
        tail = data[tail_start:]
        try:
            json.loads(tail.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            if tail_start == 0:
                raise SessionStoreError(
                    "session transcript has an incomplete final record"
                ) from None
            prefix = data[:tail_start]
            return (
                self._replay(path, _decode_lines(prefix)),
                PendingTailRecovery(tail_start, len(tail)),
            )
        raise SessionStoreError(
            "session transcript ends with a complete JSON record without a newline"
        )

    def _select_path_readonly(self, selector: str | Path) -> Path:
        if isinstance(selector, Path):
            return _validated_selected_path_readonly(selector, self.root)
        if not isinstance(selector, str):
            raise SessionStoreError("session selector must be latest, a UUID, or a path")
        if selector == "latest":
            raise SessionStoreError("latest selector must be resolved through prepare_resume")
        if "/" in selector or "\\" in selector or selector.endswith(".jsonl"):
            return _validated_selected_path_readonly(Path(selector), self.root)
        try:
            session_id = canonical_session_id(selector)
        except SessionRecordError as error:
            raise SessionStoreError(str(error)) from None
        return _validated_selected_path_readonly(self.root / f"{session_id}.jsonl", self.root)

    def _decode_latest_data(self, data: bytes) -> Path:
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise SessionStoreError("latest session metadata is unreadable or invalid") from None
        if not isinstance(value, dict):
            raise SessionStoreError("latest session metadata must be a JSON object")
        expected = {"schema_version", "session_id", "transcript"}
        if set(value) != expected or value.get("schema_version") != LATEST_SCHEMA_VERSION:
            raise SessionStoreError("latest session metadata has an unsupported schema")
        try:
            session_id = canonical_session_id(value.get("session_id"))
        except SessionRecordError as error:
            raise SessionStoreError(f"invalid latest session target: {error}") from None
        if value.get("transcript") != f"{session_id}.jsonl":
            raise SessionStoreError("latest session target does not match its session ID")
        return _validated_selected_path_readonly(
            self.root / f"{session_id}.jsonl",
            self.root,
        )

    def _load_state(self, path: Path, *, allow_repair: bool) -> ReplayState:
        _ensure_contained_file(path, self.root, suffix=".jsonl")
        try:
            size = path.stat().st_size
        except OSError:
            raise SessionStoreError(f"session transcript is inaccessible: {path}") from None
        if size > MAX_TRANSCRIPT_BYTES:
            raise SessionStoreError(
                f"session transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes: {path}"
            )
        try:
            data = path.read_bytes()
        except OSError:
            raise SessionStoreError(f"could not read session transcript: {path}") from None
        if len(data) != size:
            raise SessionStoreError("session transcript changed while it was being read")

        repaired: Recovery | None = None
        if data and not data.endswith(b"\n"):
            tail_start = data.rfind(b"\n") + 1
            tail = data[tail_start:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                if not allow_repair or tail_start == 0:
                    raise SessionStoreError(
                        "session transcript has an incomplete final record"
                    ) from None
                prefix = data[:tail_start]
                preliminary = _decode_lines(prefix)
                preliminary_state = self._replay(path, preliminary)
                repaired = Recovery(
                    sequence=preliminary_state.next_sequence,
                    occurred_at=self._clock(),
                    truncated_bytes=len(tail),
                )
                _truncate_and_append_recovery(path, tail_start, repaired)
                data = prefix + encode_record(repaired)
            else:
                raise SessionStoreError(
                    "session transcript ends with a complete JSON record without a newline"
                )

        records = _decode_lines(data)
        if repaired is not None and (not records or records[-1] != repaired):
            raise SessionStoreError("session recovery record was not persisted correctly")
        return self._replay(path, records)

    def _replay(self, path: Path, records: list[SessionRecord]) -> ReplayState:
        try:
            return replay_records(
                records,
                expected_workspace=str(self.workspace),
                expected_workspace_fingerprint=self.workspace_fingerprint,
                expected_session_id=_session_id_from_path(path),
                expected_file_name=path.name,
            )
        except SessionRecordError as error:
            raise SessionStoreError(f"invalid session transcript {path}: {error}") from None

    def _select_path(self, selector: str | Path) -> Path:
        if isinstance(selector, Path):
            return _validated_selected_path(selector, self.root)
        if not isinstance(selector, str):
            raise SessionStoreError("session selector must be latest, a UUID, or a path")
        if selector == "latest":
            return self._read_latest()
        if "/" in selector or "\\" in selector or selector.endswith(".jsonl"):
            return _validated_selected_path(Path(selector), self.root)
        try:
            session_id = canonical_session_id(selector)
        except SessionRecordError as error:
            raise SessionStoreError(str(error)) from None
        return _validated_selected_path(self.root / f"{session_id}.jsonl", self.root)

    def _read_latest(self) -> Path:
        path = self.root / _LATEST_NAME
        _ensure_contained_file(path, self.root, suffix=".json")
        try:
            if path.stat().st_size > MAX_RECORD_BYTES:
                raise SessionStoreError("latest session metadata is oversized")
            value = json.loads(path.read_text(encoding="utf-8"))
        except SessionStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SessionStoreError("latest session metadata is unreadable or invalid") from None
        if not isinstance(value, dict):
            raise SessionStoreError("latest session metadata must be a JSON object")
        expected = {"schema_version", "session_id", "transcript"}
        if set(value) != expected or value.get("schema_version") != LATEST_SCHEMA_VERSION:
            raise SessionStoreError("latest session metadata has an unsupported schema")
        session_id_value = value.get("session_id")
        transcript = value.get("transcript")
        try:
            session_id = canonical_session_id(session_id_value)
        except SessionRecordError as error:
            raise SessionStoreError(f"invalid latest session target: {error}") from None
        if transcript != f"{session_id}.jsonl":
            raise SessionStoreError("latest session target does not match its session ID")
        return _validated_selected_path(self.root / transcript, self.root)

    def _write_latest(self, session_id: str) -> None:
        canonical_session_id(session_id)
        data = {
            "schema_version": LATEST_SCHEMA_VERSION,
            "session_id": session_id,
            "transcript": f"{session_id}.jsonl",
        }
        _atomic_json_write(self.root / _LATEST_NAME, data)

    def _ensure_root(self) -> None:
        _ensure_directory(self.workspace / ".coquo", boundary=self.workspace)
        _ensure_directory(self.workspace / ".coquo" / "sessions", boundary=self.workspace)
        _ensure_directory(self.root, boundary=self.workspace)

    @contextmanager
    def _directory_lock(self, *, existing_only: bool = False) -> Iterator[None]:
        path = self.root / _DIRECTORY_LOCK_NAME
        stream = _open_lock(
            path,
            exclusive_create=False,
            existing_only=existing_only,
        )
        try:
            _lock_stream(stream, nonblocking=False)
            yield
        finally:
            _unlock_stream(stream)
            stream.close()

    def _acquire_writer_lock(
        self,
        path: Path,
        *,
        create_exclusive: bool,
        existing_only: bool = False,
    ) -> BinaryIO:
        key = str(path)
        with _ACTIVE_WRITERS_GUARD:
            if key in _ACTIVE_WRITERS:
                raise SessionLockedError(f"session already has an active writer: {path.stem}")
            _ACTIVE_WRITERS.add(key)
        try:
            stream = _open_lock(
                path,
                exclusive_create=create_exclusive,
                existing_only=existing_only,
            )
            _lock_stream(stream, nonblocking=True)
            return stream
        except SessionLockedError:
            _release_active_writer(path)
            raise
        except Exception:
            _release_active_writer(path)
            raise


class PreparedSessionResume:
    """Single-use, mutation-free replay lease promoted only by commit."""

    def __init__(
        self,
        store: SessionStore,
        path: Path,
        lock_path: Path,
        lock_stream: BinaryIO,
        lock_identity: tuple[int, int],
        transcript_descriptor: int,
        state: ReplayState,
        original_data: bytes,
        transcript_token: TranscriptStaleToken,
        pending_recovery: PendingTailRecovery | None,
        *,
        selector_was_latest: bool,
        latest_token: LatestStaleToken | None,
    ) -> None:
        self._store = store
        self.path = path
        self.lock_path = lock_path
        self._lock_stream = lock_stream
        self._lock_identity = lock_identity
        self._transcript_descriptor = transcript_descriptor
        self.state = state
        self.original_data = original_data
        self.transcript_token = transcript_token
        self.pending_recovery = pending_recovery
        self.selector_was_latest = selector_was_latest
        self.latest_token = latest_token
        self._live = True

    @property
    def session_id(self) -> str:
        return self.state.header.session_id

    @property
    def info(self) -> SessionInfo:
        return _info(self.path, self.state)

    def commit(
        self,
        *,
        binding: BindingSnapshot | None = None,
        publish_latest: bool = True,
    ) -> CommittedSessionResume:
        """Revalidate, durably resume, update latest, and transfer ownership."""
        if not self._live:
            raise SessionResumeStaleError("prepared resume is no longer active")
        if type(publish_latest) is not bool:
            raise ValueError("publish latest flag is invalid")
        current_binding = self.state.binding if binding is None else binding
        if type(current_binding) is not BindingSnapshot:
            raise ValueError("resume runtime binding is invalid")
        recovery_applied = False
        with self._store._directory_lock(existing_only=True):
            self._revalidate()
            records = list(self.state.records)
            recovery: Recovery | None = None
            if self.pending_recovery is not None:
                recovery = Recovery(
                    sequence=len(records),
                    occurred_at=self._store._clock(),
                    truncated_bytes=self.pending_recovery.truncated_bytes,
                )
                records.append(recovery)
            resumed = SessionResumed(
                sequence=len(records),
                occurred_at=self._store._clock(),
                binding=current_binding,
            )
            records.append(resumed)
            candidate = self._store._replay(self.path, records)
            try:
                if recovery is not None:
                    _truncate_and_append_recovery_descriptor(
                        self._transcript_descriptor,
                        self.path,
                        self.pending_recovery.truncate_offset,
                        recovery,
                    )
                    recovery_applied = True
                _append_record_descriptor(self._transcript_descriptor, self.path, resumed)
            except BaseException as error:
                stage = (
                    ResumeDurableStage.RECOVERY_DURABLE
                    if recovery_applied
                    else ResumeDurableStage.DURABILITY_UNKNOWN
                )
                self.abort()
                raise SessionResumeCommitError(
                    "could not durably append the resume audit",
                    stage=stage,
                    recovery_applied=recovery_applied,
                    session_resumed_applied=False,
                ) from error
            latest_status = LatestUpdateStatus.NOT_REQUESTED
            latest_diagnostic = None
            if publish_latest:
                latest_status = LatestUpdateStatus.UPDATED
                try:
                    self._store._write_latest(self.session_id)
                except AtomicJsonWriteError as error:
                    latest_status = (
                        LatestUpdateStatus.REPLACED_DURABILITY_UNKNOWN
                        if error.replaced
                        else LatestUpdateStatus.FAILED_UNCHANGED
                    )
                    latest_diagnostic = str(error)
            writer = SessionWriter(
                self._store,
                self.path,
                self.lock_path,
                self._lock_stream,
                self._transcript_descriptor,
                candidate,
            )
            self._live = False
            return CommittedSessionResume(
                writer,
                recovery_applied,
                latest_status,
                latest_diagnostic,
            )

    def abort(self) -> None:
        if not self._live:
            return
        self._live = False
        try:
            try:
                os.close(self._transcript_descriptor)
            finally:
                _unlock_stream(self._lock_stream)
        finally:
            try:
                self._lock_stream.close()
            finally:
                _release_active_writer(self.lock_path)

    def _revalidate(self) -> None:
        try:
            lock_path_info = self.lock_path.lstat()
            lock_descriptor_info = os.fstat(self._lock_stream.fileno())
        except OSError:
            raise SessionResumeStaleError(
                "target Session lock changed during resume preparation"
            ) from None
        if (
            self.lock_path.is_symlink()
            or not stat.S_ISREG(lock_path_info.st_mode)
            or (lock_path_info.st_dev, lock_path_info.st_ino) != self._lock_identity
            or (lock_descriptor_info.st_dev, lock_descriptor_info.st_ino) != self._lock_identity
        ):
            raise SessionResumeStaleError("target Session lock changed during resume preparation")
        data, info = _read_descriptor_bytes(self._transcript_descriptor, self.path)
        if _transcript_token(data, info) != self.transcript_token:
            raise SessionResumeStaleError("target Session changed during resume preparation")
        pathname = self.path.lstat()
        if (pathname.st_dev, pathname.st_ino) != (
            self.transcript_token.device,
            self.transcript_token.inode,
        ):
            raise SessionResumeStaleError("target Session path changed during resume preparation")
        if self.selector_was_latest:
            assert self.latest_token is not None
            latest_data, latest_info = _read_regular_file_descriptor(
                self._store.root / _LATEST_NAME
            )
            if _latest_token(latest_data, latest_info) != self.latest_token:
                raise SessionResumeStaleError(
                    "workspace latest Session changed during resume preparation"
                )

    def __enter__(self) -> PreparedSessionResume:
        return self

    def __exit__(self, *_: object) -> None:
        self.abort()


class SessionWriter:
    """Lifetime-exclusive append handle for one validated session transcript."""

    def __init__(
        self,
        store: SessionStore,
        path: Path,
        lock_path: Path,
        lock_stream: BinaryIO,
        transcript_descriptor: int,
        state: ReplayState,
    ) -> None:
        self._store = store
        self.path = path
        self.lock_path = lock_path
        self._lock_stream = lock_stream
        self._transcript_descriptor = transcript_descriptor
        self._state = state
        self._released = False

    @property
    def session_id(self) -> str:
        return self._state.header.session_id

    @property
    def state(self) -> ReplayState:
        return self._state

    def now(self) -> str:
        """Return the store-owned canonical time for a new durable record."""
        return self._store._clock()

    @property
    def info(self) -> SessionInfo:
        return _info(self.path, self._state)

    def append_turn(
        self,
        items: Iterable[ConversationItem],
        *,
        binding: BindingSnapshot,
        tool_ledger: ToolTurnLedger,
        hook_audit: HookAuditLedger = HookAuditLedger(),
        provider_usage: tuple[ProviderInvocationUsage, ...] = (),
        session_name: str | None = None,
        session_name_source: SessionNameSource | None = None,
        session_title_fallback_reason: SessionTitleFallbackReason | None = None,
        committed_at: str | None = None,
    ) -> TurnCommitted:
        """Durably commit one complete turn as exactly one JSONL record."""
        self._ensure_writable()
        record = TurnCommitted(
            sequence=self._state.next_sequence,
            committed_at=committed_at or self._store._clock(),
            binding=binding,
            items=tuple(items),
            tool_ledger=tool_ledger,
            hook_audit=hook_audit,
            provider_usage=provider_usage,
            session_name=session_name,
            session_name_source=session_name_source,
            session_title_fallback_reason=session_title_fallback_reason,
        )
        if session_name is None:
            self._append(record)
        else:
            with self._store._directory_lock(existing_only=True):
                if _session_name_exists(
                    self._store,
                    session_name,
                    exclude_session_id=self.session_id,
                ):
                    raise SessionNameConflictError(session_name)
                self._append(record)
        return record

    def append_context_compacted(self, record: ContextCompacted) -> ContextCompacted:
        """Durably append one prevalidated effective-context checkpoint."""
        self._ensure_writable()
        if record.sequence != self._state.next_sequence:
            raise SessionStoreError(
                f"checkpoint sequence must be {self._state.next_sequence}, got {record.sequence}"
            )
        self._append(record)
        return record

    def append_audit(self, record: AuditRecord) -> AuditRecord:
        """Append one typed audit event; audit events never enter replay history."""
        self._ensure_writable()
        if isinstance(record, (Recovery, SessionResumed)):
            raise SessionStoreError(
                "recovery and session_resumed records are reserved for prepared resume"
            )
        if record.sequence != self._state.next_sequence:
            raise SessionStoreError(
                f"audit sequence must be {self._state.next_sequence}, got {record.sequence}"
            )
        if isinstance(record, SessionClosed):
            raise SessionStoreError("use close() to append session_closed and release the lock")
        self._append(record)
        return record

    def resolve_task_admission(
        self,
        admission_id: str,
        outcome: TaskAdmissionOutcome,
        *,
        task_id: str | None = None,
        reason: str | None = None,
    ) -> TaskAdmissionResolved:
        """Durably resolve one committed proposal, returning an exact idempotent replay."""
        self._ensure_writable()
        try:
            canonical = canonical_task_admission_id(admission_id)
        except ValueError as error:
            raise SessionStoreError(str(error)) from None
        if type(outcome) is not TaskAdmissionOutcome:
            raise SessionStoreError("Task admission outcome is invalid")
        if not any(
            source.proposal.admission_id == canonical for source in self._state.task_admissions
        ):
            raise SessionStoreError("Task admission proposal was not committed in this Session")
        existing = next(
            (
                record
                for record in self._state.task_admission_resolutions
                if record.admission_id == canonical
            ),
            None,
        )
        if existing is not None:
            if (
                existing.outcome is outcome
                and existing.task_id == task_id
                and existing.reason == reason
            ):
                return existing
            raise SessionStoreError("Task admission proposal is already resolved differently")
        record = TaskAdmissionResolved(
            sequence=self._state.next_sequence,
            occurred_at=self._store._clock(),
            admission_id=canonical,
            outcome=outcome,
            task_id=task_id,
            reason=reason,
        )
        self.append_audit(record)
        return record

    def runtime_changed(
        self, binding: BindingSnapshot, *, reason: str, occurred_at: str | None = None
    ) -> RuntimeChanged:
        """Convenience API for a typed runtime_changed audit event."""
        record = RuntimeChanged(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            binding=binding,
            reason=reason,
        )
        self.append_audit(record)
        return record

    def rename(self, name: str | None = None) -> SessionInfo:
        """Durably set a manual name or restore the deterministic automatic name."""
        self._ensure_writable()
        if name is None:
            resolved, source = _automatic_session_identity(self._state)
        else:
            resolved = canonical_session_name(name)
            source = SessionNameSource.MANUAL
        self.append_audit(
            SessionNamed(
                sequence=self._state.next_sequence,
                occurred_at=self._store._clock(),
                name=resolved,
                source=source,
            )
        )
        return self.info

    def set_archived(self, archived: bool) -> SessionInfo:
        """Durably change reversible Session archive metadata when needed."""
        self._ensure_writable()
        if type(archived) is not bool:
            raise SessionStoreError("Session archived state must be boolean")
        if self._state.archived == archived:
            return self.info
        self.append_audit(
            SessionArchiveChanged(
                sequence=self._state.next_sequence,
                occurred_at=self._store._clock(),
                archived=archived,
            )
        )
        return self.info

    def set_pinned(self, pinned: bool) -> SessionInfo:
        """Durably change reversible Session pin metadata when needed."""
        self._ensure_writable()
        if type(pinned) is not bool:
            raise SessionStoreError("Session pinned state must be boolean")
        if self._state.pinned == pinned:
            return self.info
        self.append_audit(
            SessionPinChanged(
                sequence=self._state.next_sequence,
                occurred_at=self._store._clock(),
                pinned=pinned,
            )
        )
        return self.info

    def turn_failed(
        self,
        *,
        binding: BindingSnapshot,
        failure_kind: str,
        message: str,
        provider_usage: tuple[ProviderInvocationUsage, ...] = (),
        hook_audit: HookAuditLedger = HookAuditLedger(),
        occurred_at: str | None = None,
    ) -> TurnFailed:
        """Convenience API for a typed turn_failed audit event."""
        record = TurnFailed(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            binding=binding,
            failure_kind=failure_kind,
            message=message,
            provider_usage=provider_usage,
            hook_audit=hook_audit,
        )
        self.append_audit(record)
        return record

    def compaction_failed(
        self,
        *,
        binding: BindingSnapshot,
        trigger: CompactionTrigger,
        failure_kind: str,
        message: str,
        provider_usage: tuple[ProviderInvocationUsage, ...],
        occurred_at: str | None = None,
    ) -> CompactionFailed:
        """Persist one terminal compaction failure and its provider usage."""
        record = CompactionFailed(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            binding=binding,
            trigger=trigger,
            failure_kind=failure_kind,
            message=message,
            provider_usage=provider_usage,
        )
        self.append_audit(record)
        return record

    def action_requested(
        self,
        *,
        identity: ActionIdentity,
        binding: BindingSnapshot,
        permission_mode: PermissionMode,
        approval_mode: ApprovalMode,
        occurred_at: str | None = None,
    ) -> ActionRequested:
        """Durably begin the lifecycle for one exact Host action."""
        record = ActionRequested(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            binding=binding,
            identity=identity,
            permission_mode=permission_mode,
            approval_mode=approval_mode,
        )
        self.append_audit(record)
        return record

    def permission_decided(
        self,
        *,
        identity: ActionIdentity,
        result: PermissionResult,
        occurred_at: str | None = None,
    ) -> PermissionDecided:
        """Durably record the deterministic permission result for an action."""
        record = PermissionDecided(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            action_request_id=identity.request_id,
            action_digest=identity.digest,
            decision=result.decision,
            reason=result.reason,
        )
        self.append_audit(record)
        return record

    def approval_resolved(
        self,
        *,
        identity: ActionIdentity,
        outcome: ApprovalAuditOutcome,
        grant_id: str | None,
        occurred_at: str | None = None,
    ) -> ApprovalResolved:
        """Durably record one accepted, rejected, or cancelled approval."""
        record = ApprovalResolved(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            action_request_id=identity.request_id,
            action_digest=identity.digest,
            outcome=outcome,
            grant_id=grant_id,
        )
        self.append_audit(record)
        return record

    def action_execution_started(
        self,
        *,
        identity: ActionIdentity,
        authorization: ActionAuthorization,
        grant_id: str | None,
        occurred_at: str | None = None,
    ) -> ActionExecutionStarted:
        """Commit the durable start barrier before any external effect begins."""
        record = ActionExecutionStarted(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            action_request_id=identity.request_id,
            action_digest=identity.digest,
            authorization=authorization,
            grant_id=grant_id,
        )
        self.append_audit(record)
        return record

    def action_execution_finished(
        self,
        *,
        identity: ActionIdentity,
        outcome: ActionExecutionOutcome,
        result_code: str,
        message: str,
        occurred_at: str | None = None,
    ) -> ActionExecutionFinished:
        """Record a known outcome or raise with truthful partial-outcome evidence."""
        record = ActionExecutionFinished(
            sequence=self._state.next_sequence,
            occurred_at=occurred_at or self._store._clock(),
            action_request_id=identity.request_id,
            action_digest=identity.digest,
            outcome=outcome,
            result_code=result_code,
            message=message,
        )
        try:
            self.append_audit(record)
        except Exception as error:
            raise ActionOutcomeAuditError(
                "action outcome is known but its final audit record was not durably committed",
                action_request_id=identity.request_id,
                action_digest=identity.digest,
                execution_outcome=outcome,
                result_code=result_code,
            ) from error
        return record

    def close(self, *, reason: str = "closed", occurred_at: str | None = None) -> None:
        """Append session_closed once, fsync it, then release the writer lock."""
        if self._released:
            return
        try:
            if not self._state.closed:
                record = SessionClosed(
                    sequence=self._state.next_sequence,
                    occurred_at=occurred_at or self._store._clock(),
                    reason=reason,
                )
                self._append(record)
        finally:
            self._release()

    def release(self) -> None:
        """Release the writer without closing the durable session (for process handoff)."""
        self._release()

    def __enter__(self) -> SessionWriter:
        self._ensure_writable()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(reason="error" if exc_type is not None else "closed")

    def _append(self, record: SessionRecord) -> None:
        candidate = replay_records(
            [*self._state.records, record],
            expected_workspace=str(self._store.workspace),
            expected_workspace_fingerprint=self._store.workspace_fingerprint,
            expected_session_id=self.session_id,
            expected_file_name=self.path.name,
        )
        _append_record_descriptor(self._transcript_descriptor, self.path, record)
        self._state = candidate

    def _ensure_writable(self) -> None:
        if self._released:
            raise SessionStoreError("session writer is released")
        if self._state.closed:
            raise SessionStoreError("session is closed")

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            try:
                os.close(self._transcript_descriptor)
            finally:
                _unlock_stream(self._lock_stream)
        finally:
            try:
                self._lock_stream.close()
            finally:
                _release_active_writer(self.lock_path)


def _open_existing_transcript(path: Path, *, writable: bool = True) -> int:
    flags = os.O_RDWR if writable else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SessionStoreError(f"session transcript is not a regular file: {path}")
        return descriptor
    except SessionStoreError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError:
        raise SessionStoreError(f"session transcript is inaccessible: {path}") from None


def _read_descriptor_bytes(descriptor: int, path: Path) -> tuple[bytes, os.stat_result]:
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SessionStoreError(f"session transcript is not a regular file: {path}")
        if info.st_size > MAX_TRANSCRIPT_BYTES:
            raise SessionStoreError(
                f"session transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes: {path}"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        final = os.fstat(descriptor)
        if len(data) != info.st_size or (final.st_dev, final.st_ino, final.st_size) != (
            info.st_dev,
            info.st_ino,
            info.st_size,
        ):
            raise SessionStoreError("session transcript changed while it was being read")
        return data, final
    except SessionStoreError:
        raise
    except OSError:
        raise SessionStoreError(f"could not read session transcript: {path}") from None


def _read_regular_file_descriptor(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = _open_existing_transcript(path, writable=False)
    try:
        data, info = _read_descriptor_bytes(descriptor, path)
        if len(data) > MAX_RECORD_BYTES:
            raise SessionStoreError("latest session metadata is oversized")
        return data, info
    finally:
        os.close(descriptor)


def _transcript_token(data: bytes, info: os.stat_result) -> TranscriptStaleToken:
    return TranscriptStaleToken(
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        hashlib.sha256(data).hexdigest(),
    )


def _latest_token(data: bytes, info: os.stat_result) -> LatestStaleToken:
    return LatestStaleToken(
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        hashlib.sha256(data).hexdigest(),
        data,
    )


def _append_record_descriptor(descriptor: int, path: Path, record: SessionRecord) -> None:
    payload = encode_record(record)
    try:
        path_info = path.lstat()
        info = os.fstat(descriptor)
        if path.is_symlink() or (path_info.st_dev, path_info.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise SessionResumeStaleError("session transcript path no longer matches its writer")
        if info.st_size + len(payload) > MAX_TRANSCRIPT_BYTES:
            raise SessionStoreError(
                f"session transcript would exceed {MAX_TRANSCRIPT_BYTES} bytes: {path}"
            )
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    except SessionStoreError:
        raise
    except OSError:
        raise SessionStoreError(f"could not append session transcript: {path}") from None


def _truncate_and_append_recovery_descriptor(
    descriptor: int,
    path: Path,
    offset: int,
    record: Recovery,
) -> None:
    try:
        os.ftruncate(descriptor, offset)
        _append_record_descriptor(descriptor, path, record)
    except SessionStoreError:
        raise
    except OSError:
        raise SessionStoreError(f"could not repair session transcript: {path}") from None


def _validate_existing_session_root(root: Path, workspace: Path) -> None:
    if root.is_symlink() or workspace not in root.parents:
        raise SessionStoreError("session root is unsafe")
    try:
        info = root.lstat()
    except OSError:
        raise SessionStoreError(
            f"session directory does not exist or is inaccessible: {root}"
        ) from None
    if not stat.S_ISDIR(info.st_mode):
        raise SessionStoreError(f"session root is not a directory: {root}")
    for name in (_DIRECTORY_LOCK_NAME, _LATEST_NAME):
        path = root / name
        if path.is_symlink():
            raise SessionStoreError(f"session path must not be a symlink: {path}")
        try:
            child = path.lstat()
        except OSError:
            raise SessionStoreError(
                f"session file does not exist or is inaccessible: {path}"
            ) from None
        if not stat.S_ISREG(child.st_mode):
            raise SessionStoreError(f"session path is not a regular file: {path}")


def _validated_selected_path_readonly(path: Path, root: Path) -> Path:
    candidate = path if path.is_absolute() else Path.cwd() / path
    absolute = candidate.absolute()
    if absolute.parent != root.absolute():
        raise SessionStoreError("session path must be directly inside the current session root")
    _session_id_from_path(absolute)
    if absolute.is_symlink():
        raise SessionStoreError(f"session path must not be a symlink: {absolute}")
    try:
        info = absolute.lstat()
    except OSError:
        raise SessionStoreError(
            f"session file does not exist or is inaccessible: {absolute}"
        ) from None
    if not stat.S_ISREG(info.st_mode):
        raise SessionStoreError(f"session path is not a regular file: {absolute}")
    return absolute


def _release_writer_lease(path: Path, stream: BinaryIO) -> None:
    try:
        _unlock_stream(stream)
    finally:
        try:
            stream.close()
        finally:
            _release_active_writer(path)


def _read_path_snapshot(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = _open_existing_transcript(path, writable=False)
    try:
        data, info = _read_descriptor_bytes(descriptor, path)
        path_info = path.lstat()
        if path.is_symlink() or (path_info.st_dev, path_info.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise SessionStoreError("session transcript changed while it was being read")
        return data, info
    finally:
        os.close(descriptor)


def _remove_created_session_files(transcript_path: Path, lock_path: Path) -> None:
    failures: list[str] = []
    removed = False
    for path in (transcript_path, lock_path):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError:
            failures.append(path.name)
    if removed:
        try:
            _fsync_directory(transcript_path.parent)
        except SessionStoreError:
            failures.append("session directory durability")
    if failures:
        details = ", ".join(failures)
        raise SessionStoreError(f"failed Session creation cleanup was incomplete: {details}")


def _create_repair_backup(path: Path, data: bytes) -> Path:
    digest = hashlib.sha256(data).hexdigest()
    backup = path.parent / f"{path.stem}.repair-backup-{digest}.bak"
    if backup.exists() or backup.is_symlink():
        if backup.is_symlink() or not backup.is_file():
            raise SessionStoreError("Session repair backup path is unsafe")
        try:
            existing = backup.read_bytes()
        except OSError:
            raise SessionStoreError("could not verify existing Session repair backup") from None
        if existing != data:
            raise SessionStoreError("existing Session repair backup does not match source")
        return backup
    _create_transcript(backup, data)
    return backup


def _factory_session_id(factory: Callable[[], UUID | str]) -> str:
    value = factory()
    candidate = str(value) if isinstance(value, UUID) else value
    try:
        return canonical_session_id(candidate)
    except SessionRecordError as error:
        raise SessionStoreError(f"UUID factory returned an invalid session ID: {error}") from None


def _decode_lines(data: bytes) -> list[SessionRecord]:
    if not data:
        raise SessionStoreError("session transcript is empty")
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise SessionStoreError(f"session transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes")
    lines = data.splitlines(keepends=True)
    if len(lines) > MAX_RECORDS:
        raise SessionStoreError(f"session transcript exceeds {MAX_RECORDS} records")
    records: list[SessionRecord] = []
    for number, line in enumerate(lines, start=1):
        if not line.endswith(b"\n"):
            raise SessionStoreError(f"session record {number} is missing its newline")
        try:
            records.append(decode_record(line))
        except SessionRecordError as error:
            raise SessionStoreError(f"invalid session record {number}: {error}") from None
    return records


def _create_transcript(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(path.parent)
    except FileExistsError:
        raise SessionStoreError(f"session transcript already exists: {path.name}") from None
    except OSError:
        raise SessionStoreError(f"could not create session transcript: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _append_record(path: Path, record: SessionRecord) -> None:
    payload = encode_record(record)
    flags = os.O_WRONLY | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SessionStoreError(f"session transcript is not a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        if info.st_size + len(payload) > MAX_TRANSCRIPT_BYTES:
            raise SessionStoreError(
                f"session transcript would exceed {MAX_TRANSCRIPT_BYTES} bytes: {path}"
            )
        with os.fdopen(descriptor, "ab") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except SessionStoreError:
        raise
    except OSError:
        raise SessionStoreError(f"could not append session transcript: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _truncate_and_append_recovery(path: Path, offset: int, record: Recovery) -> None:
    payload = encode_record(record)
    flags = os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SessionStoreError(f"session transcript is not a regular file: {path}")
        os.ftruncate(descriptor, offset)
        os.lseek(descriptor, 0, os.SEEK_END)
        with os.fdopen(descriptor, "ab") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except SessionStoreError:
        raise
    except OSError:
        raise SessionStoreError(f"could not repair session transcript: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_json_write(path: Path, data: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        _ensure_contained_file(path, path.parent, suffix=".json")
    payload = (
        json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )
    temporary: str | None = None
    descriptor: int | None = None
    replaced = False
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".latest.", suffix=".tmp")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        replaced = True
        temporary = None
        _fsync_directory(path.parent)
    except OSError:
        raise AtomicJsonWriteError(
            f"could not update latest session metadata: {path}",
            replaced=replaced,
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _validated_selected_path(path: Path, root: Path) -> Path:
    candidate = path if path.is_absolute() else Path.cwd() / path
    absolute = candidate.absolute()
    root_absolute = root.absolute()
    if absolute.parent != root_absolute:
        raise SessionStoreError("session path must be directly inside the current session root")
    _session_id_from_path(absolute)
    _ensure_contained_file(absolute, root_absolute, suffix=".jsonl")
    return absolute


def _session_id_from_path(path: Path) -> str:
    if path.suffix != ".jsonl":
        raise SessionStoreError("session transcript file name must end in .jsonl")
    try:
        return canonical_session_id(path.stem)
    except SessionRecordError as error:
        raise SessionStoreError(f"invalid session transcript file name: {error}") from None


def _ensure_contained_file(path: Path, root: Path, *, suffix: str) -> None:
    if path.parent.absolute() != root.absolute() or path.suffix != suffix:
        raise SessionStoreError("session path escapes the current session root")
    if path.is_symlink():
        raise SessionStoreError(f"session path must not be a symlink: {path}")
    try:
        info = path.lstat()
    except OSError:
        raise SessionStoreError(f"session file does not exist or is inaccessible: {path}") from None
    if not stat.S_ISREG(info.st_mode):
        raise SessionStoreError(f"session path is not a regular file: {path}")
    try:
        os.chmod(path, 0o600)
    except OSError:
        raise SessionStoreError(f"could not secure session file: {path}") from None


def _ensure_directory(path: Path, *, boundary: Path) -> None:
    if boundary not in path.parents and path != boundary:
        raise SessionStoreError("session directory escapes the workspace")
    relative = path.relative_to(boundary)
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SessionStoreError(f"session directory must not be a symlink: {current}")
        if current.exists():
            try:
                info = current.lstat()
            except OSError:
                raise SessionStoreError(f"session directory is inaccessible: {current}") from None
            if not stat.S_ISDIR(info.st_mode):
                raise SessionStoreError(f"session directory path is not a directory: {current}")
        else:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                if current.is_symlink() or not current.is_dir():
                    raise SessionStoreError(f"session directory is unsafe: {current}") from None
            except OSError:
                raise SessionStoreError(f"could not create session directory: {current}") from None
        try:
            os.chmod(current, 0o700)
        except OSError:
            raise SessionStoreError(f"could not secure session directory: {current}") from None


def _open_lock(
    path: Path,
    *,
    exclusive_create: bool,
    existing_only: bool = False,
) -> BinaryIO:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise SessionStoreError(f"session lock directory is unsafe: {path.parent}")
    if path.is_symlink():
        raise SessionStoreError(f"session lock must not be a symlink: {path}")
    flags = os.O_RDWR
    if not existing_only:
        flags |= os.O_CREAT
    if exclusive_create:
        flags |= os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SessionStoreError(f"session lock is not a regular file: {path}")
        if not existing_only:
            os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "a+b")
        descriptor = None
        return stream
    except FileExistsError:
        raise SessionStoreError(f"session ID collision: {path.stem}") from None
    except SessionStoreError:
        raise
    except OSError:
        raise SessionStoreError(f"could not open session lock: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _lock_stream(stream: BinaryIO, *, nonblocking: bool) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            mode = msvcrt.LK_NBLCK if nonblocking else msvcrt.LK_LOCK
            msvcrt.locking(stream.fileno(), mode, 1)
        else:
            operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
            fcntl.flock(stream.fileno(), operation)
    except (OSError, BlockingIOError):
        if nonblocking:
            raise SessionLockedError("session already has an active writer") from None
        raise SessionStoreError("could not lock session directory") from None


def _unlock_stream(stream: BinaryIO) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _release_active_writer(path: Path) -> None:
    with _ACTIVE_WRITERS_GUARD:
        _ACTIVE_WRITERS.discard(str(path))


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        raise SessionStoreError(f"could not fsync session directory: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _info(path: Path, state: ReplayState) -> SessionInfo:
    name, name_source = _session_name(state)
    return SessionInfo(
        session_id=state.header.session_id,
        path=path,
        workspace=state.header.workspace,
        workspace_fingerprint=state.header.workspace_fingerprint,
        created_at=state.header.created_at,
        record_count=len(state.records),
        turn_count=len(state.turns),
        closed=state.closed,
        binding=state.binding,
        name=name,
        name_source=name_source,
        archived=state.archived,
        pinned=state.pinned,
        title_fallback_reason=_committed_title_fallback_reason(state),
        forked_from_session_id=(
            state.forked_from.source_session_id if state.forked_from is not None else None
        ),
        forked_from_turn=(
            state.forked_from.source_turn_count if state.forked_from is not None else None
        ),
    )


def _task_admission_infos(
    info: SessionInfo,
    state: ReplayState,
) -> tuple[TaskAdmissionInfo, ...]:
    resolutions = {record.admission_id: record for record in state.task_admission_resolutions}
    values: list[TaskAdmissionInfo] = []
    for source in state.task_admissions:
        resolution = resolutions.get(source.proposal.admission_id)
        values.append(
            TaskAdmissionInfo(
                proposal=source.proposal,
                session_id=info.session_id,
                session_name=info.name,
                turn_record_sequence=source.turn_record_sequence,
                turn_number=source.turn_number,
                committed_at=source.committed_at,
                outcome=resolution.outcome if resolution is not None else None,
                task_id=resolution.task_id if resolution is not None else None,
                rejection_reason=resolution.reason if resolution is not None else None,
                resolved_at=resolution.occurred_at if resolution is not None else None,
            )
        )
    return tuple(values)


def _session_name(state: ReplayState) -> tuple[str, SessionNameSource]:
    if state.latest_name is not None:
        return state.latest_name.name, state.latest_name.source
    committed = _committed_session_name(state)
    if committed is not None:
        return committed
    if state.turns:
        return _automatic_session_name(state), SessionNameSource.AUTO
    if state.header.name is not None:
        return state.header.name, SessionNameSource.DEFAULT
    return f"New session {state.header.session_id[:8]}", SessionNameSource.DEFAULT


def _automatic_session_name(state: ReplayState) -> str:
    if not state.turns:
        if state.header.name is not None:
            return state.header.name
        return f"New session {state.header.session_id[:8]}"
    text = state.turns[0].user.text
    candidate = next((line for line in text.splitlines() if line.strip()), text)
    candidate = " ".join(
        "".join(character if character.isprintable() else " " for character in candidate).split()
    )
    if not candidate:
        return "Untitled session"
    return _truncate_session_name(candidate, 48, 160)


def _automatic_session_identity(state: ReplayState) -> tuple[str, SessionNameSource]:
    committed = _committed_session_name(state)
    if committed is not None:
        return committed
    return _automatic_session_name(state), SessionNameSource.AUTO


def _committed_session_name(
    state: ReplayState,
) -> tuple[str, SessionNameSource] | None:
    for record in state.records:
        if isinstance(record, TurnCommitted):
            if record.session_name is None or record.session_name_source is None:
                return None
            return record.session_name, record.session_name_source
    return None


def _committed_title_fallback_reason(
    state: ReplayState,
) -> SessionTitleFallbackReason | None:
    for record in state.records:
        if isinstance(record, TurnCommitted) and record.session_name is not None:
            return record.session_title_fallback_reason
    return None


def _truncate_session_name(value: str, max_characters: int, max_bytes: int) -> str:
    if len(value) <= max_characters and len(value.encode("utf-8")) <= max_bytes:
        return canonical_session_name(value)
    suffix = "..."
    kept: list[str] = []
    for character in value:
        candidate = "".join(kept) + character + suffix
        if len(candidate) > max_characters or len(candidate.encode("utf-8")) > max_bytes:
            break
        kept.append(character)
    return canonical_session_name("".join(kept).rstrip() + suffix)


def _next_default_session_name(root: Path) -> str:
    try:
        count = sum(1 for path in root.iterdir() if path.name.endswith(".jsonl"))
    except OSError:
        raise SessionStoreError(f"could not allocate session name in: {root}") from None
    return f"New session {count + 1}"


def _fork_session_name(source_name: str, session_id: str) -> str:
    suffix = f" [{session_id[:8]}]"
    base = _truncate_session_name(
        f"Fork of {source_name}",
        80 - len(suffix),
        256 - len(suffix.encode("utf-8")),
    )
    return canonical_session_name(base + suffix)


def _session_turn_evidence(
    data: bytes,
    state: ReplayState,
    record_sequence: int,
) -> SessionTurnEvidence:
    record = state.records[record_sequence]
    if not isinstance(record, TurnCommitted):
        raise SessionStoreError("selected Session record is not a committed Turn")
    lines = data.splitlines(keepends=True)
    if len(lines) != len(state.records):
        raise SessionStoreError("Session transcript record boundaries are inconsistent")
    user = next((item for item in record.items if isinstance(item, UserMessage)), None)
    if user is None:
        raise SessionStoreError("committed Turn has no canonical user message")
    provider_usage = record.provider_usage
    provider_available = provider_usage is not None
    provider_invocations = len(provider_usage) if provider_usage is not None else 0
    input_tokens = 0
    output_tokens = 0
    known = 0
    unknown = 0
    for invocation in provider_usage or ():
        if invocation.usage is None:
            unknown += 1
        else:
            known += 1
            input_tokens += invocation.usage.input_tokens
            output_tokens += invocation.usage.output_tokens
    ledger_available = record.schema_version >= TURN_COMMITTED_LEDGER_SCHEMA_VERSION
    ledger = record.tool_ledger
    tool_requests = ledger.requested if ledger_available else 0
    tool_admitted = ledger.admitted if ledger_available else 0
    tool_dispatched = ledger.dispatched if ledger_available else 0
    tool_succeeded = ledger.count(ToolRequestOutcome.SUCCEEDED) if ledger_available else 0
    return SessionTurnEvidence(
        session_id=state.header.session_id,
        turn_number=sum(
            isinstance(candidate, TurnCommitted)
            for candidate in state.records[: record_sequence + 1]
        ),
        record_sequence=record_sequence,
        record_sha256=hashlib.sha256(lines[record_sequence]).hexdigest(),
        committed_at=record.committed_at,
        user_message_sha256=hashlib.sha256(user.text.encode("utf-8")).hexdigest(),
        provider_usage_available=provider_available,
        provider_invocations=provider_invocations,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        known_token_invocations=known,
        unknown_token_invocations=unknown,
        tool_usage_available=ledger_available,
        tool_requests=tool_requests,
        tool_admitted=tool_admitted,
        tool_dispatched=tool_dispatched,
        tool_succeeded=tool_succeeded,
        tool_unsuccessful=tool_dispatched - tool_succeeded,
    )


def _copied_tool_ledger(record: TurnCommitted) -> ToolTurnLedger:
    if record.schema_version >= TURN_COMMITTED_LEDGER_SCHEMA_VERSION:
        return record.tool_ledger
    requests: list[ToolUse] = []
    results: dict[str, ToolResult] = {}
    for item in record.items:
        if isinstance(item, ToolUse):
            requests.append(item)
        elif isinstance(item, AssistantToolBatch):
            requests.extend(item.tool_uses)
        elif isinstance(item, ToolResult):
            results[item.tool_use_id] = item
    return ToolTurnLedger(
        tuple(
            ToolOutcomeEntry(
                request.tool_use_id,
                request.name,
                index,
                (
                    ToolRequestOutcome.ERROR
                    if results[request.tool_use_id].is_error
                    else ToolRequestOutcome.SUCCEEDED
                ),
            )
            for index, request in enumerate(requests, start=1)
        )
    )


def _canonical_session_search_query(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SessionStoreError("session search query must be non-empty text")
    if (
        len(value) > MAX_SESSION_SEARCH_QUERY_CHARACTERS
        or len(value.encode("utf-8")) > MAX_SESSION_SEARCH_QUERY_BYTES
    ):
        raise SessionStoreError("session search query exceeds its character or UTF-8 byte limit")
    if any(not character.isprintable() for character in value):
        raise SessionStoreError("session search query must be printable single-line text")
    return value


def _bounded_session_paths(root: Path) -> tuple[Path, ...]:
    try:
        entries = tuple(root.iterdir())
    except OSError:
        raise SessionStoreError(f"could not list session directory: {root}") from None
    if len(entries) > MAX_SESSION_SEARCH_DIRECTORY_ENTRIES:
        raise SessionStoreError(
            f"session directory exceeds {MAX_SESSION_SEARCH_DIRECTORY_ENTRIES} entries"
        )
    paths = []
    for path in entries:
        if not path.name.endswith(".jsonl"):
            continue
        _session_id_from_path(path)
        paths.append(path)
    return tuple(sorted(paths, key=lambda path: path.name))


def _bounded_search_excerpt(line: str, query: str) -> str:
    if (
        len(line) <= MAX_SESSION_SEARCH_EXCERPT_CHARACTERS
        and len(line.encode("utf-8")) <= MAX_SESSION_SEARCH_EXCERPT_BYTES
    ):
        return line
    index = line.find(query)
    start = max(0, index - 40)
    end = min(len(line), start + MAX_SESSION_SEARCH_EXCERPT_CHARACTERS - 6)
    if end < index + len(query):
        end = min(len(line), index + len(query) + 40)
        start = max(0, end - (MAX_SESSION_SEARCH_EXCERPT_CHARACTERS - 6))
    prefix = "..." if start else ""
    suffix = "..." if end < len(line) else ""
    candidate = prefix + line[start:end] + suffix
    while len(candidate.encode("utf-8")) > MAX_SESSION_SEARCH_EXCERPT_BYTES:
        end -= 1
        suffix = "..." if end < len(line) else ""
        candidate = prefix + line[start:end] + suffix
    return candidate


def _session_name_exists(
    store: SessionStore,
    name: str,
    *,
    exclude_session_id: str,
) -> bool:
    key = name.casefold()
    return any(
        info.session_id != exclude_session_id and info.name.casefold() == key
        for info in store.list()
    )
