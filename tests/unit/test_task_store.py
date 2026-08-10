from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from coquo.core.contracts import (
    AssistantText,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from coquo.core.task_admission import (
    TASK_PROPOSE_START_TOOL_NAME,
    TaskAdmissionProposal,
    task_admission_receipt,
)
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.task_records import (
    ReflectionRecommendation,
    StageFailureReason,
    StageKind,
    TaskBlockerCategory,
    TaskStatus,
)
from coquo.task_store import (
    MAX_TASK_TRANSCRIPT_BYTES,
    TaskCreateCommitError,
    TaskAppendCommitError,
    TaskStore,
    TaskStoreError,
)

SESSION_ONE = "12345678-1234-4234-9234-123456789abc"
SESSION_TWO = "22345678-1234-4234-9234-123456789abc"
TASK_ONE = "32345678-1234-4234-9234-123456789abc"
TASK_TWO = "42345678-1234-4234-9234-123456789abc"
STAGE_ONE = "52345678-1234-4234-9234-123456789abc"
STAGE_TWO = "62345678-1234-4234-9234-123456789abc"


def create_session(workspace: Path, session_id: str) -> None:
    writer = SessionStore(workspace, uuid_factory=lambda: session_id).create(BindingSnapshot.fake())
    writer.release()


def task_store(
    workspace: Path, task_id: str = TASK_ONE, created_at: str = "2026-07-31T01:02:03.000004Z"
) -> TaskStore:
    stage_ids = iter((STAGE_ONE, STAGE_TWO))
    return TaskStore(
        workspace,
        uuid_factory=lambda: task_id,
        stage_uuid_factory=lambda: next(stage_ids),
        clock=lambda: created_at,
    )


def committed_task_writer(workspace: Path, kind: StageKind):
    workspace.mkdir()
    session_times = iter(("2026-07-31T01:02:00.000000Z", "2026-07-31T01:02:03.000000Z"))
    session_writer = SessionStore(
        workspace,
        uuid_factory=lambda: SESSION_ONE,
        clock=lambda: next(session_times),
    ).create(BindingSnapshot.fake())
    task_times = iter(f"2026-07-31T01:02:{second:02}.000000Z" for second in range(1, 20))
    store = TaskStore(
        workspace,
        uuid_factory=lambda: TASK_ONE,
        stage_uuid_factory=lambda: STAGE_ONE,
        clock=lambda: next(task_times),
    )
    task = store.create("Test proposal idempotence")
    writer = store.open(task.task_id)
    writer.start_stage("Commit one proposal", kind=kind)
    turn = session_writer.append_turn(
        (UserMessage("advance"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
    )
    session_writer.release()
    writer.commit_stage(turn.sequence)
    return writer


def test_empty_list_is_read_only_and_does_not_create_state(tmp_path: Path) -> None:
    store = task_store(tmp_path)

    assert store.list() == ()
    assert not (tmp_path / ".coquo").exists()


def test_create_links_existing_owner_and_round_trips_strictly(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)

    created = store.create(
        "Implement durable Task state",
        acceptance_criteria=("Survives restart", "No model invocation"),
    )

    assert created.task_id == TASK_ONE
    assert created.owner_session_id == SESSION_ONE
    assert created.status is TaskStatus.READY
    assert created.record_count == 1
    assert created.acceptance_criteria == ("Survives restart", "No model invocation")
    assert store.inspect(TASK_ONE) == created
    assert store.list() == (created,)
    assert created.path.read_bytes().count(b"\n") == 1
    assert created.path.stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o777 == 0o700


def test_create_from_admission_persists_origin_and_duplicate_lookup_fails_closed(
    tmp_path: Path,
) -> None:
    request = ToolUse(
        "admission-1",
        TASK_PROPOSE_START_TOOL_NAME,
        ToolArguments.from_mapping(
            {
                "objective": "Implement a sourced Task",
                "reason": "The work needs multiple stages.",
                "acceptance_criteria": ["The result exists"],
            }
        ),
    )
    proposal = TaskAdmissionProposal.from_request(request, "ctx-v5-" + "a" * 64)
    writer = SessionStore(tmp_path, uuid_factory=lambda: SESSION_ONE).create(BindingSnapshot.fake())
    source_turn = writer.append_turn(
        (
            UserMessage("Propose one durable Task"),
            request,
            ToolResult(request.tool_use_id, task_admission_receipt(proposal)),
            AssistantText("Proposal recorded."),
        ),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(
            (
                ToolOutcomeEntry(
                    request.tool_use_id,
                    request.name,
                    1,
                    ToolRequestOutcome.SUCCEEDED,
                ),
            )
        ),
    )
    writer.release()
    first_store = task_store(tmp_path, TASK_ONE)
    preview = first_store.prepare_admission_acceptance(
        proposal,
        owner_session=SESSION_ONE,
        source_turn_record_sequence=source_turn.sequence,
    )

    with pytest.raises(TaskStoreError, match="does not match its source Session Turn"):
        first_store.create_from_admission(
            proposal,
            owner_session=SESSION_ONE,
            source_turn_record_sequence=source_turn.sequence + 1,
            confirmation_sha256=preview.confirmation_sha256,
        )

    created = first_store.create_from_admission(
        proposal,
        owner_session=SESSION_ONE,
        source_turn_record_sequence=source_turn.sequence,
        confirmation_sha256=preview.confirmation_sha256,
    )

    assert created.admission_origin is not None
    assert created.admission_origin.admission_id == proposal.admission_id
    assert created.admission_origin.proposal_sha256 == proposal.proposal_sha256
    assert created.admission_origin.source_session_id == SESSION_ONE
    assert first_store.find_by_admission(proposal.admission_id) == created

    task_store(tmp_path, TASK_TWO).create_from_admission(
        proposal,
        owner_session=SESSION_ONE,
        source_turn_record_sequence=source_turn.sequence,
        confirmation_sha256=preview.confirmation_sha256,
    )
    with pytest.raises(TaskStoreError, match="provenance is duplicated"):
        first_store.find_by_admission(proposal.admission_id)


def test_create_accepts_an_explicit_nonlatest_owner_session(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    create_session(tmp_path, SESSION_TWO)
    store = task_store(tmp_path)

    created = store.create("Task for the first Session", owner_session=SESSION_ONE)

    assert created.owner_session_id == SESSION_ONE


def test_invalid_owner_and_invalid_objective_leave_task_storage_absent(tmp_path: Path) -> None:
    store = task_store(tmp_path)
    with pytest.raises(TaskStoreError, match="owner Session"):
        store.create("No owner exists")
    assert not store.root.exists()

    create_session(tmp_path, SESSION_ONE)
    with pytest.raises(TaskStoreError, match="nonblank"):
        store.create(" \n")
    assert not store.root.exists()


def test_task_id_collision_does_not_replace_existing_transcript(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    first = store.create("First objective")
    before = first.path.read_bytes()

    with pytest.raises(TaskStoreError, match="task ID collision"):
        store.create("Second objective")

    assert first.path.read_bytes() == before
    assert store.inspect(TASK_ONE).objective == "First objective"


def test_list_is_newest_first_and_ignores_internal_temporary_names(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    first = task_store(tmp_path, TASK_ONE, "2026-07-31T01:00:00.000000Z").create("First")
    second_store = task_store(tmp_path, TASK_TWO, "2026-07-31T02:00:00.000000Z")
    second = second_store.create("Second")
    (second_store.root / ".task.abandoned.tmp").write_text("temporary", encoding="utf-8")

    assert second_store.list() == (second, first)


def test_strict_inspection_rejects_corruption_incomplete_tail_and_symlink(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    created = store.create("Inspect strictly")
    original = created.path.read_bytes()

    document = json.loads(original)
    document["unknown"] = True
    created.path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    with pytest.raises(TaskStoreError, match="unknown or missing"):
        store.inspect(TASK_ONE)

    created.path.write_bytes(original + b'{"record_type":')
    with pytest.raises(TaskStoreError, match="durable record boundary"):
        store.inspect(TASK_ONE)

    created.path.unlink()
    created.path.symlink_to(tmp_path / "outside.jsonl")
    with pytest.raises(TaskStoreError, match="symlink"):
        store.inspect(TASK_ONE)


def test_inspection_rejects_oversized_and_nonregular_transcripts(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    created = store.create("Bounded transcript")
    created.path.write_bytes(b"x" * (MAX_TASK_TRANSCRIPT_BYTES + 1))
    with pytest.raises(TaskStoreError, match="exceeds"):
        store.inspect(TASK_ONE)

    created.path.unlink()
    created.path.mkdir()
    with pytest.raises(TaskStoreError, match="regular file"):
        store.inspect(TASK_ONE)


def test_task_storage_rejects_symlinked_state_directory(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    state = tmp_path / ".coquo"
    sessions = state / "sessions"
    moved = tmp_path / "real-state"
    state.rename(moved)
    state.symlink_to(moved, target_is_directory=True)

    with pytest.raises(TaskStoreError, match="real directory"):
        task_store(tmp_path).create("Unsafe state path")
    assert (moved / "sessions").samefile(sessions)


def test_post_install_durability_failure_reports_visible_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    store._ensure_root()
    import coquo.task_store as task_store_module

    def fail_directory_fsync(_path: Path) -> None:
        raise TaskStoreError("injected durability failure")

    monkeypatch.setattr(task_store_module, "_fsync_directory", fail_directory_fsync)

    with pytest.raises(TaskCreateCommitError) as captured:
        store.create("Visible but durability unknown")

    assert captured.value.task_visible is True
    assert (store.root / f"{TASK_ONE}.jsonl").is_file()


def test_workspace_symlink_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = tmp_path / "link"
    link.symlink_to(workspace, target_is_directory=True)

    with pytest.raises(TaskStoreError, match="workspace must not be a symlink"):
        TaskStore(link)


def test_task_transcript_mode_is_not_affected_by_umask(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    old_umask = os.umask(0)
    try:
        created = task_store(tmp_path).create("Private task")
    finally:
        os.umask(old_umask)

    assert created.path.stat().st_mode & 0o777 == 0o600


def test_stage_start_is_active_in_writer_and_interrupted_after_release(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    task = store.create("Long task")

    writer = store.open(task.task_id)
    started = writer.start_stage("Advance one bounded step")

    assert started.stage_number == 1
    assert writer.info.status is TaskStatus.STAGE_IN_PROGRESS
    assert writer.info.stages[0].outcome == "stage-in-progress"
    assert store.inspect(task.task_id).status is TaskStatus.STAGE_IN_PROGRESS
    writer.release()

    inspected = store.inspect(task.task_id)
    assert inspected.status is TaskStatus.INTERRUPTED
    assert inspected.stages[0].outcome == "interrupted"


def test_task_writer_is_exclusive_and_failed_stage_allows_next_attempt(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    task = store.create("Retry safely")
    first = store.open(task.task_id)

    with pytest.raises(TaskStoreError, match="active writer"):
        store.open(task.task_id)

    first.start_stage("First attempt")
    failed = first.fail_stage(StageFailureReason.PROVIDER_ERROR)
    assert failed.reason is StageFailureReason.PROVIDER_ERROR
    second = first.start_stage("Second attempt")
    assert second.stage_number == 2
    first.fail_stage(StageFailureReason.CANCELLED)
    first.release()
    assert store.inspect(task.task_id).status is TaskStatus.BLOCKED
    assert len(store.inspect(task.task_id).stages) == 2


def test_task_proposal_writes_are_exactly_idempotent_and_reject_conflicts(
    tmp_path: Path,
) -> None:
    plan_writer = committed_task_writer(tmp_path / "plan", StageKind.PLANNING)
    plan = plan_writer.propose_plan(("Implement one step",), proposal_tool_use_id="plan-1")
    assert plan_writer.propose_plan(("Implement one step",), proposal_tool_use_id="plan-1") == plan
    with pytest.raises(TaskStoreError, match="does not match its tool call"):
        plan_writer.propose_plan(("Different step",), proposal_tool_use_id="plan-1")
    with pytest.raises(TaskStoreError, match="already has a plan proposal"):
        plan_writer.propose_plan(("Implement one step",), proposal_tool_use_id="plan-2")
    plan_writer.release()

    reflection_writer = committed_task_writer(tmp_path / "reflection", StageKind.REFLECTION)
    reflection = reflection_writer.record_reflection(
        ReflectionRecommendation.NEEDS_HUMAN,
        "Need user evidence",
        None,
        proposal_tool_use_id="reflection-1",
    )
    assert (
        reflection_writer.record_reflection(
            ReflectionRecommendation.NEEDS_HUMAN,
            "Need user evidence",
            None,
            proposal_tool_use_id="reflection-1",
        )
        == reflection
    )
    with pytest.raises(TaskStoreError, match="does not match its tool call"):
        reflection_writer.record_reflection(
            ReflectionRecommendation.FAIL,
            "Different reflection",
            None,
            proposal_tool_use_id="reflection-1",
        )
    with pytest.raises(TaskStoreError, match="already recorded"):
        reflection_writer.record_reflection(
            ReflectionRecommendation.NEEDS_HUMAN,
            "Need user evidence",
            None,
            proposal_tool_use_id="reflection-2",
        )
    reflection_writer.release()

    completion_writer = committed_task_writer(tmp_path / "completion", StageKind.EXECUTION)
    completion = completion_writer.propose_completion(proposal_tool_use_id="completion-1")
    assert completion_writer.propose_completion(proposal_tool_use_id="completion-1") == completion
    with pytest.raises(TaskStoreError, match="already proposed completion"):
        completion_writer.propose_completion(proposal_tool_use_id="completion-2")
    completion_writer.release()

    blocker_writer = committed_task_writer(tmp_path / "blocker", StageKind.EXECUTION)
    blocker = blocker_writer.record_blocker(
        TaskBlockerCategory.PERMISSION,
        "Need write approval",
        proposal_tool_use_id="blocker-1",
    )
    assert (
        blocker_writer.record_blocker(
            TaskBlockerCategory.PERMISSION,
            "Need write approval",
            proposal_tool_use_id="blocker-1",
        )
        == blocker
    )
    with pytest.raises(TaskStoreError, match="does not match its tool call"):
        blocker_writer.record_blocker(
            TaskBlockerCategory.INFORMATION,
            "Different blocker",
            proposal_tool_use_id="blocker-1",
        )
    with pytest.raises(TaskStoreError, match="already has a blocker"):
        blocker_writer.record_blocker(
            TaskBlockerCategory.PERMISSION,
            "Need write approval",
            proposal_tool_use_id="blocker-2",
        )
    blocker_writer.release()


def test_task_writer_lock_is_visible_to_another_process(tmp_path: Path) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    task = store.create("Cross-process lease")
    writer = store.open(task.task_id)
    writer.start_stage("Hold the durable Stage lease")

    script = """
import sys
from pathlib import Path
from coquo.task_store import TaskStore, TaskStoreError

store = TaskStore(Path(sys.argv[1]))
assert store.inspect(sys.argv[2]).status.value == "stage-in-progress"
try:
    store.open(sys.argv[2])
except TaskStoreError as error:
    assert "active writer" in str(error)
else:
    raise AssertionError("second process acquired the active Task")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), task.task_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    writer.release()
    assert result.returncode == 0, result.stderr
    assert store.inspect(task.task_id).status is TaskStatus.INTERRUPTED


def test_committed_stage_links_exact_session_turn_record(tmp_path: Path) -> None:
    writer = SessionStore(
        tmp_path,
        uuid_factory=lambda: SESSION_ONE,
        clock=lambda: "2026-07-31T01:02:03.000004Z",
    ).create(BindingSnapshot.fake())
    turn = writer.append_turn(
        (UserMessage("advance"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
    )
    session_path = writer.path
    writer.release()
    store = task_store(tmp_path)
    task = store.create("Link evidence", owner_session=SESSION_ONE)

    with store.open(task.task_id) as task_writer:
        task_writer.start_stage("Commit one ordinary Turn")
        committed = task_writer.commit_stage(turn.sequence)

    line = session_path.read_bytes().splitlines(keepends=True)[turn.sequence]
    assert committed.turn_number == 1
    assert committed.turn_record_sequence == turn.sequence
    assert committed.turn_record_sha256 == hashlib.sha256(line).hexdigest()
    inspected = store.inspect(task.task_id)
    assert inspected.status is TaskStatus.PAUSED
    assert inspected.stages[0].turn_record_sha256 == committed.turn_record_sha256


def test_stage_commit_rejects_a_committed_turn_that_predates_stage_start(tmp_path: Path) -> None:
    session_writer = SessionStore(
        tmp_path,
        uuid_factory=lambda: SESSION_ONE,
        clock=lambda: "2026-07-31T00:00:00.000000Z",
    ).create(BindingSnapshot.fake())
    old_turn = session_writer.append_turn(
        (UserMessage("old"), AssistantText("old")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
    )
    session_writer.release()
    store = task_store(tmp_path)
    task = store.create("Reject historical evidence", owner_session=SESSION_ONE)

    with store.open(task.task_id) as writer:
        writer.start_stage("Must produce a new Turn")
        with pytest.raises(TaskStoreError, match="predates the active Stage"):
            writer.commit_stage(old_turn.sequence)
        assert writer.state.active_stage is not None


def test_stage_commit_rejects_nonturn_session_record_without_terminal_fact(tmp_path: Path) -> None:
    session_writer = SessionStore(tmp_path, uuid_factory=lambda: SESSION_ONE).create(
        BindingSnapshot.fake()
    )
    nonturn = session_writer.runtime_changed(BindingSnapshot.fake(), reason="test")
    session_writer.release()
    store = task_store(tmp_path)
    task = store.create("Reject false evidence")

    with store.open(task.task_id) as writer:
        writer.start_stage("No committed Turn exists")
        with pytest.raises(TaskStoreError, match="not a committed Turn"):
            writer.commit_stage(nonturn.sequence)
        assert writer.state.active_stage is not None


def test_uncertain_stage_append_poison_writer_and_requires_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_session(tmp_path, SESSION_ONE)
    store = task_store(tmp_path)
    task = store.create("Durability uncertainty")
    writer = store.open(task.task_id)
    import coquo.task_store as task_store_module

    def fail_fsync(_descriptor: int) -> None:
        raise OSError

    monkeypatch.setattr(task_store_module.os, "fsync", fail_fsync)

    with pytest.raises(TaskAppendCommitError) as captured:
        writer.start_stage("Possibly visible Stage")
    assert captured.value.record_may_be_visible is True
    with pytest.raises(TaskStoreError, match="durability is uncertain"):
        writer.start_stage("Must not retry")
    writer.release()
    assert store.inspect(task.task_id).status is TaskStatus.INTERRUPTED
