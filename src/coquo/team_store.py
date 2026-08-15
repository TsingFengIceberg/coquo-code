"""Workspace-confined append-only storage for durable Team identities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock
from uuid import UUID, uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from coquo.session_records import SessionRecordError, workspace_fingerprint
from coquo.session_store import SessionStore, SessionStoreError
from coquo.team_records import (
    TEAM_MEMBER_ROLE_CONTRACT,
    TeamMemberDisabled,
    TeamMemberEnabled,
    TeamMemberJoined,
    TeamMemberLeft,
    TeamAssignmentCreated,
    TeamAssignmentChildBound,
    TeamAssignmentObserved,
    TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION,
    TeamAssignmentMailboxBound,
    TeamAssignmentMailboxObserved,
    TeamAssignmentState,
    TeamAssignmentPhase,
    TeamMemberState,
    TeamMemberStatus,
    TeamMessageCancelled,
    TeamMessageRead,
    TeamMessageSent,
    TeamMessageState,
    TeamMessageStatus,
    TeamWorkItemCancelled,
    TeamWorkItemCompleted,
    TeamWorkItemCreated,
    TeamWorkItemReleased,
    TeamWorkItemState,
    TeamWorkStatus,
    TeamClosed,
    TeamHeader,
    TeamRecord,
    TeamRecordError,
    TeamReplayState,
    TeamStatus,
    TeamScheduleCancelRequested,
    TeamScheduleFinished,
    TeamScheduleOutcome,
    TeamScheduleSource,
    TeamScheduleStarted,
    TeamScheduleState,
    canonical_team_schedule_message,
    MAX_TEAM_SCHEDULE_ASSIGNMENTS,
    MAX_TEAM_SCHEDULE_PARALLEL,
    MAX_TEAM_WORK_DEPENDENCIES,
    canonical_team_id,
    canonical_team_name,
    canonical_team_reason,
    canonical_team_assignment_objective,
    team_assignment_objective_sha256,
    team_message_body_sha256,
    decode_team_record,
    encode_team_record,
    replay_team_records,
    utc_now,
)

MAX_TEAM_TRANSCRIPT_BYTES = 1024 * 1024
MAX_TEAM_DIRECTORY_ENTRIES = 10_000
_SCHEDULE_LEASE_HEADER = b"coquo-team-schedule-v1\n"


class TeamStoreError(RuntimeError):
    """Raised when durable Team persistence cannot proceed safely."""


class TeamCreateCommitError(TeamStoreError):
    """Report whether a failed Team create made the final file visible."""

    def __init__(self, message: str, *, team_visible: bool) -> None:
        self.team_visible = team_visible
        super().__init__(message)


class TeamAppendCommitError(TeamStoreError):
    """Report an append whose final durability is uncertain."""

    def __init__(self, message: str, *, record_may_be_visible: bool) -> None:
        self.record_may_be_visible = record_may_be_visible
        super().__init__(message)


@dataclass(frozen=True)
class TeamInfo:
    team_id: str
    path: Path
    workspace: str
    workspace_fingerprint: str
    owner_session_id: str
    name: str
    created_at: str
    status: TeamStatus
    record_count: int
    closed_at: str | None = None
    members: tuple[TeamMemberState, ...] = ()
    assignments: tuple[TeamAssignmentState, ...] = ()
    messages: tuple[TeamMessageState, ...] = ()
    work_items: tuple[TeamWorkItemState, ...] = ()
    schedules: tuple[TeamScheduleState, ...] = ()


_ACTIVE_WRITERS: set[str] = set()
_ACTIVE_WRITERS_GUARD = Lock()
_ACTIVE_SCHEDULE_LEASES: set[str] = set()
_ACTIVE_SCHEDULE_LEASES_GUARD = Lock()


class TeamScheduleLease:
    """Process/lifetime-scoped OS lease for one Team schedule coordinator."""

    def __init__(self, descriptor: int, path: Path, key: str) -> None:
        self._descriptor = descriptor
        self.path = path
        self._key = key
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _unlock_schedule_descriptor(self._descriptor)
        finally:
            with _ACTIVE_SCHEDULE_LEASES_GUARD:
                _ACTIVE_SCHEDULE_LEASES.discard(self._key)
            os.close(self._descriptor)

    def __enter__(self) -> TeamScheduleLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TeamStore:
    """Create and strictly inspect one workspace's durable Team transcripts."""

    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        requested = Path(workspace)
        if requested.is_symlink():
            raise TeamStoreError("workspace must not be a symlink")
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            raise TeamStoreError(
                f"workspace does not exist or is inaccessible: {requested}"
            ) from None
        if not resolved.is_dir():
            raise TeamStoreError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self.workspace_fingerprint = workspace_fingerprint(resolved)
        self.root = resolved / ".coquo" / "teams" / self.workspace_fingerprint
        self._uuid_factory = uuid_factory
        self._clock = clock

    def create(
        self,
        name: str,
        *,
        owner_session: str = "latest",
        team_id: str | None = None,
    ) -> TeamInfo:
        try:
            canonical_name = canonical_team_name(name)
            owner = SessionStore(self.workspace).inspect(owner_session)
        except (TeamRecordError, SessionStoreError, SessionRecordError) as error:
            raise TeamStoreError(f"Team owner or name is invalid: {error}") from None
        try:
            team_id = (
                _factory_team_id(self._uuid_factory)
                if team_id is None
                else canonical_team_id(team_id)
            )
        except TeamRecordError as error:
            raise TeamStoreError(f"Team ID is invalid: {error}") from None
        header = TeamHeader(
            sequence=0,
            team_id=team_id,
            workspace=str(self.workspace),
            workspace_fingerprint=self.workspace_fingerprint,
            owner_session_id=owner.session_id,
            name=canonical_name,
            created_at=self._clock(),
        )
        try:
            payload = encode_team_record(header)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._ensure_root()
        path = self.root / f"{team_id}.jsonl"
        _install_team_transcript(path, payload)
        state = self._replay(path, [header])
        return _team_info(path, state)

    def inspect(self, team_id: str) -> TeamInfo:
        canonical = _store_team_id(team_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        return _team_info(path, self._load_state(path))

    def replay_state(self, team_id: str) -> TeamReplayState:
        canonical = _store_team_id(team_id)
        self._validate_existing_root()
        return self._load_state(self.root / f"{canonical}.jsonl")

    def list(self, *, status: TeamStatus | None = None) -> tuple[TeamInfo, ...]:
        if not self.root.exists() and not self.root.is_symlink():
            return ()
        self._validate_existing_root()
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            raise TeamStoreError("Team directory is inaccessible") from None
        if len(entries) > MAX_TEAM_DIRECTORY_ENTRIES:
            raise TeamStoreError(f"Team directory exceeds {MAX_TEAM_DIRECTORY_ENTRIES} entries")
        teams: list[TeamInfo] = []
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            path = self.root / entry.name
            _store_team_id(path.stem)
            info = _team_info(path, self._load_state(path))
            if status is None or info.status is status:
                teams.append(info)
        return tuple(sorted(teams, key=lambda item: (item.created_at, item.team_id), reverse=True))

    def open(self, team_id: str) -> TeamWriter:
        canonical = _store_team_id(team_id)
        self._validate_existing_root()
        path = self.root / f"{canonical}.jsonl"
        descriptor = _open_team_transcript(path)
        key = str(path)
        claimed = False
        locked = False
        try:
            _claim_writer(key)
            claimed = True
            _lock_descriptor(descriptor)
            locked = True
            state = self._decode_state(path, _read_descriptor(descriptor, path))
            return TeamWriter(self, path, descriptor, state, key)
        except BaseException:
            if locked:
                _unlock_descriptor(descriptor)
            if claimed:
                _release_writer(key)
            os.close(descriptor)
            raise

    def acquire_schedule(self, team_id: str) -> TeamScheduleLease:
        """Acquire a nonblocking, lifetime-scoped Team schedule lease."""
        canonical = _store_team_id(team_id)
        self._ensure_root()
        path = self.workspace / ".coquo" / "teams" / f"{canonical}.schedule-v1.lock"
        if path.is_symlink():
            raise TeamStoreError("Team schedule lease path must not be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError:
            raise TeamStoreError("Team schedule lease is inaccessible") from None
        key = str(path)
        claimed = False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise TeamStoreError("Team schedule lease must be a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise TeamStoreError("Team schedule lease mode must be 0600")
            os.lseek(descriptor, 0, os.SEEK_SET)
            current = os.read(descriptor, len(_SCHEDULE_LEASE_HEADER) + 1)
            if current not in {b"", _SCHEDULE_LEASE_HEADER}:
                raise TeamStoreError("Team schedule lease header is invalid")
            if current == b"":
                os.write(descriptor, _SCHEDULE_LEASE_HEADER)
                os.fsync(descriptor)
            with _ACTIVE_SCHEDULE_LEASES_GUARD:
                if key in _ACTIVE_SCHEDULE_LEASES:
                    raise TeamStoreError("Team already has an active schedule lease")
                _ACTIVE_SCHEDULE_LEASES.add(key)
                claimed = True
            _lock_schedule_descriptor(descriptor)
            return TeamScheduleLease(descriptor, path, key)
        except BaseException:
            if claimed:
                with _ACTIVE_SCHEDULE_LEASES_GUARD:
                    _ACTIVE_SCHEDULE_LEASES.discard(key)
            os.close(descriptor)
            raise

    def close(self, team_id: str) -> TeamInfo:
        with self.open(team_id) as writer:
            writer.close_team()
            return writer.info

    def start_schedule(
        self,
        team_id: str,
        schedule_run_id: str,
        *,
        source: TeamScheduleSource | str = TeamScheduleSource.HOST,
        max_assignments: int = MAX_TEAM_SCHEDULE_ASSIGNMENTS,
        max_parallel: int = MAX_TEAM_SCHEDULE_PARALLEL,
    ) -> TeamScheduleState:
        with self.open(team_id) as writer:
            writer.start_schedule(
                schedule_run_id,
                source=source,
                max_assignments=max_assignments,
                max_parallel=max_parallel,
            )
            return writer.schedule(schedule_run_id)

    def cancel_schedule(
        self,
        team_id: str,
        schedule_run_id: str,
        reason: str,
        *,
        source: TeamScheduleSource | str = TeamScheduleSource.HOST,
    ) -> TeamScheduleState:
        with self.open(team_id) as writer:
            writer.cancel_schedule(schedule_run_id, reason, source=source)
            return writer.schedule(schedule_run_id)

    def finish_schedule(
        self,
        team_id: str,
        schedule_run_id: str,
        *,
        outcome: TeamScheduleOutcome | str,
        assignment_count: int,
        result_code: str,
        message: str,
    ) -> TeamScheduleState:
        with self.open(team_id) as writer:
            writer.finish_schedule(
                schedule_run_id,
                outcome=outcome,
                assignment_count=assignment_count,
                result_code=result_code,
                message=message,
            )
            return writer.schedule(schedule_run_id)

    def add_member(self, team_id: str, name: str) -> TeamMemberState:
        with self.open(team_id) as writer:
            return writer.join_member(name)

    def disable_member(self, team_id: str, member_id: str, reason: str) -> TeamMemberState:
        with self.open(team_id) as writer:
            writer.disable_member(member_id, reason)
            return writer.member(member_id)

    def enable_member(self, team_id: str, member_id: str) -> TeamMemberState:
        with self.open(team_id) as writer:
            writer.enable_member(member_id)
            return writer.member(member_id)

    def leave_member(self, team_id: str, member_id: str, reason: str) -> TeamMemberState:
        with self.open(team_id) as writer:
            writer.leave_member(member_id, reason)
            return writer.member(member_id)

    def create_assignment(
        self,
        team_id: str,
        assignment_id: str,
        member_id: str,
        child_run_id: str,
        objective: str,
        *,
        work_item_id: str | None = None,
        schedule_run_id: str | None = None,
    ) -> TeamAssignmentState:
        with self.open(team_id) as writer:
            return writer.create_assignment(
                assignment_id,
                member_id,
                child_run_id,
                objective,
                work_item_id=work_item_id,
                schedule_run_id=schedule_run_id,
            )

    def bind_assignment(
        self,
        team_id: str,
        assignment_id: str,
        child_run_id: str,
        *,
        child_header_sequence: int = 0,
        child_origin_sequence: int = 1,
    ) -> TeamAssignmentState:
        with self.open(team_id) as writer:
            return writer.bind_assignment(
                assignment_id,
                child_run_id,
                child_header_sequence=child_header_sequence,
                child_origin_sequence=child_origin_sequence,
            )

    def observe_assignment(
        self,
        team_id: str,
        assignment_id: str,
        *,
        child_run_id: str,
        child_session_id: str | None,
        child_outcome: str,
        child_terminal_sequence: int,
        handoff_sha256: str,
    ) -> TeamAssignmentState:
        with self.open(team_id) as writer:
            return writer.observe_assignment(
                assignment_id,
                child_run_id=child_run_id,
                child_session_id=child_session_id,
                child_outcome=child_outcome,
                child_terminal_sequence=child_terminal_sequence,
                handoff_sha256=handoff_sha256,
            )

    def member(self, team_id: str, member_id: str) -> TeamMemberState:
        state = self.replay_state(team_id)
        canonical = _store_team_id(member_id)
        for member in state.members:
            if member.member_id == canonical:
                return member
        raise TeamStoreError("Team member was not found")

    def assignment(self, team_id: str, assignment_id: str) -> TeamAssignmentState:
        state = self.replay_state(team_id)
        canonical = _store_team_id(assignment_id)
        for assignment in state.assignments:
            if assignment.assignment_id == canonical:
                return assignment
        raise TeamStoreError("Team assignment was not found")

    def message(self, team_id: str, message_id: str) -> TeamMessageState:
        state = self.replay_state(team_id)
        canonical = _store_team_id(message_id)
        for message in state.messages:
            if message.message_id == canonical:
                return message
        raise TeamStoreError("Team message was not found")

    def work_item(self, team_id: str, work_item_id: str) -> TeamWorkItemState:
        state = self.replay_state(team_id)
        canonical = _store_team_id(work_item_id)
        for item in state.work_items:
            if item.work_item_id == canonical:
                return item
        raise TeamStoreError("Team work item was not found")

    def send_message(self, team_id: str, member_id: str, body: str) -> TeamMessageState:
        with self.open(team_id) as writer:
            record = writer.send_owner_message(member_id, body)
            return writer.message(record.message_id)

    def send_member_message(
        self,
        team_id: str,
        *,
        message_id: str,
        member_id: str,
        body: str,
        source_assignment_id: str,
        source_child_session_id: str,
        source_turn_record_sequence: int,
        source_handoff_sha256: str,
    ) -> TeamMessageState:
        with self.open(team_id) as writer:
            writer.send_member_message(
                message_id=message_id,
                member_id=member_id,
                body=body,
                source_assignment_id=source_assignment_id,
                source_child_session_id=source_child_session_id,
                source_turn_record_sequence=source_turn_record_sequence,
                source_handoff_sha256=source_handoff_sha256,
            )
            return writer.message(message_id)

    def read_message(self, team_id: str, message_id: str) -> TeamMessageState:
        with self.open(team_id) as writer:
            writer.read_message(message_id)
            return writer.message(message_id)

    def cancel_message(self, team_id: str, message_id: str, reason: str) -> TeamMessageState:
        with self.open(team_id) as writer:
            writer.cancel_message(message_id, reason)
            return writer.message(message_id)

    def create_work_item(
        self,
        team_id: str,
        work_item_id: str,
        title: str,
        objective: str,
        dependency_ids: tuple[str, ...],
    ) -> TeamWorkItemState:
        with self.open(team_id) as writer:
            writer.create_work_item(work_item_id, title, objective, dependency_ids)
            return writer.work_item(work_item_id)

    def cancel_work_item(self, team_id: str, work_item_id: str, reason: str) -> TeamWorkItemState:
        with self.open(team_id) as writer:
            writer.cancel_work_item(work_item_id, reason)
            return writer.work_item(work_item_id)

    def bind_assignment_mailbox(
        self,
        team_id: str,
        assignment_id: str,
        *,
        child_run_id: str,
        member_id: str,
        delivery_id: str,
        inbox_message_ids: tuple[str, ...],
        reply_message_id: str,
    ) -> TeamAssignmentState:
        with self.open(team_id) as writer:
            writer.bind_assignment_mailbox(
                assignment_id,
                child_run_id=child_run_id,
                member_id=member_id,
                delivery_id=delivery_id,
                inbox_message_ids=inbox_message_ids,
                reply_message_id=reply_message_id,
            )
            return writer.assignment(assignment_id)

    def observe_assignment_mailbox(
        self,
        team_id: str,
        assignment_id: str,
        *,
        delivery_id: str,
        child_session_id: str,
        child_turn_record_sequence: int,
        child_user_message_sha256: str,
    ) -> TeamAssignmentState:
        with self.open(team_id) as writer:
            writer.observe_assignment_mailbox(
                assignment_id,
                delivery_id=delivery_id,
                child_session_id=child_session_id,
                child_turn_record_sequence=child_turn_record_sequence,
                child_user_message_sha256=child_user_message_sha256,
            )
            return writer.assignment(assignment_id)

    def _ensure_root(self) -> None:
        _ensure_directory(self.workspace / ".coquo", self.workspace)
        _ensure_directory(self.workspace / ".coquo" / "teams", self.workspace)
        _ensure_directory(self.root, self.workspace)

    def _validate_existing_root(self) -> None:
        if not self.root.exists() and not self.root.is_symlink():
            raise TeamStoreError("Team directory does not exist")
        _validate_directory(self.root, self.workspace)

    def _load_state(self, path: Path) -> TeamReplayState:
        _path_team_id(path)
        return self._decode_state(path, _read_team_transcript(path))

    def _decode_state(self, path: Path, data: bytes) -> TeamReplayState:
        if len(data) > MAX_TEAM_TRANSCRIPT_BYTES:
            raise TeamStoreError("Team transcript is oversized")
        if not data.endswith(b"\n"):
            raise TeamStoreError("Team transcript does not end at a durable record boundary")
        try:
            records = [decode_team_record(line) for line in data.splitlines()]
            return self._replay(path, records)
        except TeamRecordError as error:
            raise TeamStoreError(f"invalid Team transcript {path}: {error}") from None

    def _replay(self, path: Path, records: list[TeamRecord]) -> TeamReplayState:
        try:
            return replay_team_records(
                records,
                expected_workspace=str(self.workspace),
                expected_workspace_fingerprint=self.workspace_fingerprint,
                expected_team_id=_path_team_id(path),
                expected_file_name=path.name,
            )
        except TeamRecordError as error:
            raise TeamStoreError(f"invalid Team transcript {path}: {error}") from None


class TeamWriter:
    """Exclusive append-only writer for one Team transcript."""

    def __init__(
        self, store: TeamStore, path: Path, descriptor: int, state: TeamReplayState, key: str
    ) -> None:
        self._store = store
        self.path = path
        self._descriptor = descriptor
        self._state = state
        self._key = key
        self._closed = False
        self._poisoned = False

    @property
    def state(self) -> TeamReplayState:
        return self._state

    @property
    def info(self) -> TeamInfo:
        return _team_info(self.path, self._state)

    def close_team(self) -> TeamClosed:
        self._ensure_writable()
        if self._state.closed is not None:
            return self._state.closed
        if any(not schedule.status.terminal for schedule in self._state.schedules):
            raise TeamStoreError("Team has a nonterminal schedule")
        if any(
            assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
            for assignment in self._state.assignments
        ):
            raise TeamStoreError("Team has assignments without terminal observation")
        if any(
            item.status not in {TeamWorkStatus.COMPLETED, TeamWorkStatus.CANCELLED}
            for item in self._state.work_items
        ):
            raise TeamStoreError("Team has nonterminal work items")
        if any(
            (message.sender_member_id is None and message.status is TeamMessageStatus.PENDING)
            or (message.sender_member_id is not None and message.status is TeamMessageStatus.UNREAD)
            for message in self._state.messages
        ):
            raise TeamStoreError("Team has pending mailbox messages")
        if any(
            assignment.delivery_id is not None
            and assignment.child_outcome == "completed"
            and assignment.mailbox_observed_at is None
            for assignment in self._state.assignments
        ):
            raise TeamStoreError("Team has unobserved mailbox delivery")
        record = TeamClosed(
            sequence=self._state.next_sequence,
            team_id=self._state.header.team_id,
            closed_at=self._store._clock(),
        )
        self._append(record)
        return record

    def join_member(self, name: str) -> TeamMemberState:
        self._ensure_open_team()
        try:
            canonical_name = canonical_team_name(name)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        member_id = _factory_team_id(self._store._uuid_factory)
        record = TeamMemberJoined(
            sequence=self._state.next_sequence,
            team_id=self._state.header.team_id,
            member_id=member_id,
            name=canonical_name,
            role_contract=TEAM_MEMBER_ROLE_CONTRACT,
            joined_at=self._store._clock(),
        )
        self._append(record)
        return self.member(member_id)

    def disable_member(self, member_id: str, reason: str) -> TeamMemberDisabled:
        self._ensure_open_team()
        member = self.member(member_id)
        if member.status is not TeamMemberStatus.ACTIVE:
            raise TeamStoreError("Team member can be disabled only when active")
        try:
            canonical_member = canonical_team_id(member_id)
            canonical_reason = canonical_team_reason(reason)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        record = TeamMemberDisabled(
            sequence=self._state.next_sequence,
            team_id=self._state.header.team_id,
            member_id=canonical_member,
            reason=canonical_reason,
            disabled_at=self._store._clock(),
        )
        self._append(record)
        return record

    def enable_member(self, member_id: str) -> TeamMemberEnabled:
        self._ensure_open_team()
        member = self.member(member_id)
        if member.status is not TeamMemberStatus.DISABLED:
            raise TeamStoreError("Team member can be enabled only when disabled")
        try:
            canonical_member = canonical_team_id(member_id)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        record = TeamMemberEnabled(
            sequence=self._state.next_sequence,
            team_id=self._state.header.team_id,
            member_id=canonical_member,
            enabled_at=self._store._clock(),
        )
        self._append(record)
        return record

    def leave_member(self, member_id: str, reason: str) -> TeamMemberLeft:
        self._ensure_open_team()
        member = self.member(member_id)
        if member.status is TeamMemberStatus.LEFT:
            raise TeamStoreError("Team member has already left")
        if any(
            message.sender_member_id is None
            and message.recipient_member_id == member.member_id
            and message.status is TeamMessageStatus.PENDING
            for message in self._state.messages
        ):
            raise TeamStoreError("Team member has pending inbound mailbox messages")
        try:
            canonical_member = canonical_team_id(member_id)
            canonical_reason = canonical_team_reason(reason)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        record = TeamMemberLeft(
            sequence=self._state.next_sequence,
            team_id=self._state.header.team_id,
            member_id=canonical_member,
            reason=canonical_reason,
            left_at=self._store._clock(),
        )
        self._append(record)
        return record

    def schedule(self, schedule_run_id: str) -> TeamScheduleState:
        try:
            canonical = canonical_team_id(schedule_run_id)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        for schedule in self._state.schedules:
            if schedule.schedule_run_id == canonical:
                return schedule
        raise TeamStoreError("Team schedule run was not found")

    def start_schedule(
        self,
        schedule_run_id: str,
        *,
        source: TeamScheduleSource | str,
        max_assignments: int,
        max_parallel: int,
    ) -> TeamScheduleStarted:
        self._ensure_open_team()
        try:
            record = TeamScheduleStarted(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                schedule_run_id=canonical_team_id(schedule_run_id),
                source=TeamScheduleSource(source).value,
                max_assignments=max_assignments,
                max_parallel=max_parallel,
                started_at=self._store._clock(),
            )
        except (TeamRecordError, ValueError) as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def cancel_schedule(
        self,
        schedule_run_id: str,
        reason: str,
        *,
        source: TeamScheduleSource | str,
    ) -> TeamScheduleCancelRequested:
        self._ensure_open_team()
        try:
            schedule = self.schedule(schedule_run_id)
            if schedule.status.terminal or schedule.cancel_requested_at is not None:
                raise TeamRecordError("Team schedule cannot be cancelled")
            record = TeamScheduleCancelRequested(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                schedule_run_id=schedule.schedule_run_id,
                reason=canonical_team_reason(reason),
                source=TeamScheduleSource(source).value,
                requested_at=self._store._clock(),
            )
        except (TeamRecordError, ValueError) as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def finish_schedule(
        self,
        schedule_run_id: str,
        *,
        outcome: TeamScheduleOutcome | str,
        assignment_count: int,
        result_code: str,
        message: str,
    ) -> TeamScheduleFinished:
        self._ensure_open_team()
        try:
            schedule = self.schedule(schedule_run_id)
            record = TeamScheduleFinished(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                schedule_run_id=schedule.schedule_run_id,
                outcome=TeamScheduleOutcome(outcome).value,
                assignment_count=assignment_count,
                result_code=canonical_team_reason(result_code),
                message=canonical_team_schedule_message(message),
                finished_at=self._store._clock(),
            )
        except (TeamRecordError, ValueError) as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def create_assignment(
        self,
        assignment_id: str,
        member_id: str,
        child_run_id: str,
        objective: str,
        *,
        work_item_id: str | None = None,
        schedule_run_id: str | None = None,
    ) -> TeamAssignmentState:
        self._ensure_open_team()
        try:
            record = TeamAssignmentCreated(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                assignment_id=canonical_team_id(assignment_id),
                member_id=canonical_team_id(member_id),
                child_run_id=canonical_team_id(child_run_id),
                objective=canonical_team_assignment_objective(objective),
                objective_sha256=team_assignment_objective_sha256(
                    canonical_team_assignment_objective(objective)
                ),
                created_at=self._store._clock(),
                work_item_id=(None if work_item_id is None else canonical_team_id(work_item_id)),
                schedule_run_id=(
                    None if schedule_run_id is None else canonical_team_id(schedule_run_id)
                ),
                schema_version=TEAM_ASSIGNMENT_CREATED_V3_SCHEMA_VERSION,
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return self.assignment(record.assignment_id)

    def release_work_item(
        self, work_item_id: str, assignment_id: str, reason: str
    ) -> TeamWorkItemReleased:
        self._ensure_open_team()
        try:
            item = self.work_item(work_item_id)
            if item.status.value != "review" or item.current_assignment_id != canonical_team_id(
                assignment_id
            ):
                raise TeamRecordError("Team work item is not ready for release")
            record = TeamWorkItemReleased(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                work_item_id=item.work_item_id,
                assignment_id=canonical_team_id(assignment_id),
                reason=canonical_team_reason(reason),
                released_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def complete_work_item(
        self, work_item_id: str, assignment_id: str, handoff_sha256: str, evidence: str
    ) -> TeamWorkItemCompleted:
        self._ensure_open_team()
        try:
            item = self.work_item(work_item_id)
            if item.status.value != "review" or item.current_assignment_id != canonical_team_id(
                assignment_id
            ):
                raise TeamRecordError("Team work item is not ready for completion")
            record = TeamWorkItemCompleted(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                work_item_id=item.work_item_id,
                assignment_id=canonical_team_id(assignment_id),
                handoff_sha256=handoff_sha256,
                evidence=evidence,
                completed_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def send_owner_message(self, member_id: str, body: str) -> TeamMessageSent:
        self._ensure_open_team()
        try:
            member = self.member(member_id)
            if member.status is TeamMemberStatus.LEFT:
                raise TeamRecordError("Team message cannot target a left member")
            canonical_member = canonical_team_id(member_id)
            canonical_body = _canonical_owner_message(body)
            record = TeamMessageSent(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                message_id=_factory_team_id(self._store._uuid_factory),
                sender_member_id=None,
                recipient_member_id=canonical_member,
                body=canonical_body,
                body_sha256=team_message_body_sha256(canonical_body),
                source_assignment_id=None,
                source_child_session_id=None,
                source_turn_record_sequence=None,
                source_handoff_sha256=None,
                sent_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def send_member_message(
        self,
        *,
        message_id: str,
        member_id: str,
        body: str,
        source_assignment_id: str,
        source_child_session_id: str,
        source_turn_record_sequence: int,
        source_handoff_sha256: str,
    ) -> TeamMessageSent:
        self._ensure_open_team()
        try:
            member = self.member(member_id)
            if member.status is TeamMemberStatus.LEFT:
                raise TeamRecordError("Team member has left")
            record = TeamMessageSent(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                message_id=canonical_team_id(message_id),
                sender_member_id=member.member_id,
                recipient_member_id=None,
                body=body,
                body_sha256=team_message_body_sha256(body),
                source_assignment_id=canonical_team_id(source_assignment_id),
                source_child_session_id=canonical_team_id(source_child_session_id),
                source_turn_record_sequence=source_turn_record_sequence,
                source_handoff_sha256=source_handoff_sha256,
                sent_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def create_work_item(
        self, work_item_id: str, title: str, objective: str, dependency_ids: tuple[str, ...]
    ) -> TeamWorkItemCreated:
        self._ensure_open_team()
        try:
            dependencies = tuple(canonical_team_id(item) for item in dependency_ids)
            if len(dependencies) > MAX_TEAM_WORK_DEPENDENCIES:
                raise TeamRecordError("Team work item has too many dependencies")
            record = TeamWorkItemCreated(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                work_item_id=canonical_team_id(work_item_id),
                title=canonical_team_name(title),
                objective=canonical_team_assignment_objective(objective),
                dependency_ids=dependencies,
                created_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def cancel_work_item(self, work_item_id: str, reason: str) -> TeamWorkItemCancelled:
        self._ensure_open_team()
        try:
            item = self.work_item(work_item_id)
            if item.status.value in {"completed", "cancelled", "assigned"}:
                raise TeamRecordError("Team work item cannot be cancelled in its current state")
            record = TeamWorkItemCancelled(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                work_item_id=item.work_item_id,
                reason=canonical_team_reason(reason),
                cancelled_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def read_message(self, message_id: str) -> TeamMessageRead:
        self._ensure_open_team()
        try:
            message = self.message(message_id)
            if message.sender_member_id is None:
                raise TeamRecordError("Owner-to-member message cannot be marked read")
            record = TeamMessageRead(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                message_id=message.message_id,
                read_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def cancel_message(self, message_id: str, reason: str) -> TeamMessageCancelled:
        self._ensure_open_team()
        try:
            message = self.message(message_id)
            if message.sender_member_id is not None:
                raise TeamRecordError("Member-to-owner message cannot be cancelled")
            record = TeamMessageCancelled(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                message_id=message.message_id,
                reason=canonical_team_reason(reason),
                cancelled_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def bind_assignment_mailbox(
        self,
        assignment_id: str,
        *,
        child_run_id: str,
        member_id: str,
        delivery_id: str,
        inbox_message_ids: tuple[str, ...],
        reply_message_id: str,
    ) -> TeamAssignmentMailboxBound:
        self._ensure_open_team()
        try:
            assignment = self.assignment(assignment_id)
            record = TeamAssignmentMailboxBound(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                assignment_id=assignment.assignment_id,
                child_run_id=canonical_team_id(child_run_id),
                member_id=canonical_team_id(member_id),
                delivery_id=canonical_team_id(delivery_id),
                inbox_message_ids=tuple(canonical_team_id(item) for item in inbox_message_ids),
                reply_message_id=canonical_team_id(reply_message_id),
                bound_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def observe_assignment_mailbox(
        self,
        assignment_id: str,
        *,
        delivery_id: str,
        child_session_id: str,
        child_turn_record_sequence: int,
        child_user_message_sha256: str,
    ) -> TeamAssignmentMailboxObserved:
        self._ensure_open_team()
        try:
            assignment = self.assignment(assignment_id)
            if assignment.delivery_id != canonical_team_id(delivery_id):
                raise TeamRecordError("Team mailbox delivery ID does not match assignment")
            record = TeamAssignmentMailboxObserved(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                assignment_id=assignment.assignment_id,
                delivery_id=canonical_team_id(delivery_id),
                child_session_id=canonical_team_id(child_session_id),
                child_turn_record_sequence=child_turn_record_sequence,
                child_user_message_sha256=child_user_message_sha256,
                observed_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return record

    def bind_assignment(
        self,
        assignment_id: str,
        child_run_id: str,
        *,
        child_header_sequence: int = 0,
        child_origin_sequence: int = 1,
    ) -> TeamAssignmentState:
        self._ensure_open_team()
        try:
            assignment = self.assignment(assignment_id)
            record = TeamAssignmentChildBound(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                assignment_id=assignment.assignment_id,
                child_run_id=canonical_team_id(child_run_id),
                child_header_sequence=child_header_sequence,
                child_origin_sequence=child_origin_sequence,
                bound_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return self.assignment(record.assignment_id)

    def observe_assignment(
        self,
        assignment_id: str,
        *,
        child_run_id: str,
        child_session_id: str | None,
        child_outcome: str,
        child_terminal_sequence: int,
        handoff_sha256: str,
    ) -> TeamAssignmentState:
        self._ensure_open_team()
        try:
            assignment = self.assignment(assignment_id)
            record = TeamAssignmentObserved(
                sequence=self._state.next_sequence,
                team_id=self._state.header.team_id,
                assignment_id=assignment.assignment_id,
                child_run_id=canonical_team_id(child_run_id),
                child_session_id=(
                    None if child_session_id is None else canonical_team_id(child_session_id)
                ),
                child_outcome=child_outcome,
                child_terminal_sequence=child_terminal_sequence,
                handoff_sha256=handoff_sha256,
                observed_at=self._store._clock(),
            )
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        self._append(record)
        return self.assignment(record.assignment_id)

    def member(self, member_id: str) -> TeamMemberState:
        try:
            canonical = canonical_team_id(member_id)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        for member in self._state.members:
            if member.member_id == canonical:
                return member
        raise TeamStoreError("Team member was not found")

    def assignment(self, assignment_id: str) -> TeamAssignmentState:
        try:
            canonical = canonical_team_id(assignment_id)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        for assignment in self._state.assignments:
            if assignment.assignment_id == canonical:
                return assignment
        raise TeamStoreError("Team assignment was not found")

    def message(self, message_id: str) -> TeamMessageState:
        try:
            canonical = canonical_team_id(message_id)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        for message in self._state.messages:
            if message.message_id == canonical:
                return message
        raise TeamStoreError("Team message was not found")

    def work_item(self, work_item_id: str) -> TeamWorkItemState:
        try:
            canonical = canonical_team_id(work_item_id)
        except TeamRecordError as error:
            raise TeamStoreError(str(error)) from None
        for item in self._state.work_items:
            if item.work_item_id == canonical:
                return item
        raise TeamStoreError("Team work item was not found")

    def _append(self, record: TeamRecord) -> TeamRecord:
        self._ensure_writable()
        candidate = list(self._state.records) + [record]
        state = self._store._replay(self.path, candidate)
        try:
            _append_record(self._descriptor, self.path, record)
        except TeamAppendCommitError:
            self._poisoned = True
            raise
        self._state = state
        return record

    def _ensure_writable(self) -> None:
        if self._closed:
            raise TeamStoreError("Team writer is closed")
        if self._poisoned:
            raise TeamStoreError("Team writer durability is uncertain")

    def _ensure_open_team(self) -> None:
        self._ensure_writable()
        if self._state.closed is not None:
            raise TeamStoreError("Team is closed")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            _unlock_descriptor(self._descriptor)
        finally:
            _release_writer(self._key)
            os.close(self._descriptor)

    def __enter__(self) -> TeamWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _factory_team_id(factory: Callable[[], UUID | str]) -> str:
    try:
        value = factory()
        if isinstance(value, UUID):
            value = str(value)
        return canonical_team_id(value)
    except (TeamRecordError, ValueError, TypeError) as error:
        raise TeamStoreError(f"Team ID is invalid: {error}") from None


def _store_team_id(value: object) -> str:
    try:
        return canonical_team_id(value)
    except TeamRecordError as error:
        raise TeamStoreError(str(error)) from None


def _canonical_owner_message(value: object) -> str:
    try:
        if not isinstance(value, str) or not value.strip():
            raise TeamRecordError("Team message body must be nonblank text")
        if any(ord(char) < 32 and char not in {"\n", "\t"} for char in value):
            raise TeamRecordError("Team message body contains a control character")
        if len(value) > 4096 or len(value.encode("utf-8")) > 8 * 1024:
            raise TeamRecordError("Owner Team message exceeds its bound")
        return value
    except TeamRecordError:
        raise


def _path_team_id(path: Path) -> str:
    return _store_team_id(path.stem)


def _team_info(path: Path, state: TeamReplayState) -> TeamInfo:
    return TeamInfo(
        team_id=state.header.team_id,
        path=path,
        workspace=state.header.workspace,
        workspace_fingerprint=state.header.workspace_fingerprint,
        owner_session_id=state.header.owner_session_id,
        name=state.header.name,
        created_at=state.header.created_at,
        status=state.status,
        record_count=len(state.records),
        closed_at=state.closed.closed_at if state.closed is not None else None,
        members=state.members,
        assignments=state.assignments,
        messages=state.messages,
        work_items=state.work_items,
        schedules=state.schedules,
    )


def _claim_writer(key: str) -> None:
    with _ACTIVE_WRITERS_GUARD:
        if key in _ACTIVE_WRITERS:
            raise TeamStoreError("Team already has an active writer")
        _ACTIVE_WRITERS.add(key)


def _release_writer(key: str) -> None:
    with _ACTIVE_WRITERS_GUARD:
        _ACTIVE_WRITERS.discard(key)


def _lock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise TeamStoreError("Team already has an active writer") from None


def _unlock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _lock_schedule_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise TeamStoreError("Team schedule lease is already held") from None


def _unlock_schedule_descriptor(descriptor: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _open_team_transcript(path: Path) -> int:
    if path.parent.is_symlink() or path.is_symlink():
        raise TeamStoreError("Team transcript path must not contain a symlink")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise TeamStoreError(f"Team transcript is inaccessible: {path}") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TeamStoreError("Team transcript must be a regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_descriptor(descriptor: int, path: Path) -> bytes:
    try:
        before = os.fstat(descriptor)
        if before.st_size > MAX_TEAM_TRANSCRIPT_BYTES:
            raise TeamStoreError("Team transcript is oversized")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise TeamStoreError("Team transcript ended during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = path.lstat()
        if len(data) != before.st_size or (after.st_dev, after.st_ino, after.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise TeamStoreError("Team transcript changed while it was being read")
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise TeamStoreError("Team transcript path changed while it was being read")
        return data
    except TeamStoreError:
        raise
    except OSError:
        raise TeamStoreError(f"Team transcript is inaccessible: {path}") from None


def _read_team_transcript(path: Path) -> bytes:
    descriptor = _open_team_transcript(path)
    try:
        return _read_descriptor(descriptor, path)
    finally:
        os.close(descriptor)


def _append_record(descriptor: int, path: Path, record: TeamRecord) -> None:
    payload = encode_team_record(record)
    write_started = False
    try:
        info = os.fstat(descriptor)
        pathname = path.lstat()
        if path.is_symlink() or (pathname.st_dev, pathname.st_ino) != (info.st_dev, info.st_ino):
            raise TeamStoreError("Team transcript path no longer matches its writer")
        if info.st_size + len(payload) > MAX_TEAM_TRANSCRIPT_BYTES:
            raise TeamStoreError("Team transcript would exceed its bound")
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(payload)
        while view:
            write_started = True
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Team append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except TeamStoreError:
        raise
    except OSError:
        raise TeamAppendCommitError(
            "could not durably append Team transcript; inspect before retrying",
            record_may_be_visible=write_started,
        ) from None


def _install_team_transcript(path: Path, payload: bytes) -> None:
    temporary: str | None = None
    descriptor: int | None = None
    linked = False
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".team.", suffix=".tmp")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        linked = True
        _fsync_directory(path.parent)
        os.unlink(temporary)
        temporary = None
        _fsync_directory(path.parent)
    except FileExistsError:
        raise TeamStoreError(f"Team ID collision: {path.stem}") from None
    except (OSError, TeamStoreError):
        raise TeamCreateCommitError(
            "could not durably create Team transcript",
            team_visible=linked,
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _ensure_directory(path: Path, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise TeamStoreError("Team storage path escapes the workspace")
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
            _fsync_directory(path.parent)
            return
        except FileExistsError:
            info = path.lstat()
        except OSError:
            raise TeamStoreError(f"could not create Team directory: {path}") from None
    except OSError:
        raise TeamStoreError(f"Team directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TeamStoreError(f"Team storage path must be a real directory: {path}")


def _validate_directory(path: Path, boundary: Path) -> None:
    if path != boundary and boundary not in path.parents:
        raise TeamStoreError("Team storage path escapes the workspace")
    try:
        info = path.lstat()
    except OSError:
        raise TeamStoreError(f"Team directory is inaccessible: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TeamStoreError(f"Team storage path must be a real directory: {path}")


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        os.fsync(descriptor)
    except OSError:
        raise TeamStoreError(f"could not confirm Team directory durability: {path}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
