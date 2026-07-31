from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from leonervis_code.core.contracts import AssistantText, ToolTurnLedger, UserMessage
from leonervis_code.session_records import BindingSnapshot
from leonervis_code.session_store import SessionStore
from leonervis_code.task_records import (
    AcceptanceCheckOutcome,
    AcceptanceCriterionKind,
    TaskCompletionPolicy,
)
from leonervis_code.task_store import TaskInfo, TaskStore
from leonervis_code.task_verification import (
    TaskVerificationError,
    build_task_review_request,
    parse_task_review_response,
    run_host_acceptance_checks,
)
from leonervis_code.tools.run_command import (
    RunCommandExecutionStatus,
    RunCommandOutcome,
)


class SuccessfulCommandTool:
    def prepare(self, request):
        return request

    def execute_detailed(self, _prepared):
        return SimpleNamespace(
            outcome=RunCommandOutcome.SUCCEEDED,
            observation=SimpleNamespace(
                exit_code=0,
                status=RunCommandExecutionStatus.EXITED,
                cleanup_complete=True,
            ),
        )


def proposed_task(
    workspace: Path,
    criteria: tuple[dict[str, object], ...],
    *,
    completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL,
) -> tuple[TaskStore, TaskInfo]:
    session = SessionStore(workspace).create(BindingSnapshot.fake())
    store = TaskStore(workspace)
    task = store.create(
        "Verify bounded acceptance",
        structured_criteria=criteria,
        completion_policy=completion_policy,
    )
    with store.open(task.task_id) as writer:
        writer.start_stage("Produce completion evidence")
        turn = session.append_turn(
            (UserMessage("stage"), AssistantText("done")),
            binding=BindingSnapshot.fake(),
            tool_ledger=ToolTurnLedger(),
        )
        writer.commit_stage(turn.sequence)
        writer.propose_completion()
    session.release()
    return store, store.inspect(task.task_id)


def test_host_checks_cover_paths_digest_command_and_session_action_certainty(
    tmp_path: Path,
) -> None:
    (tmp_path / "artifact.txt").write_text("stable\n", encoding="utf-8")
    store, task = proposed_task(
        tmp_path,
        (
            {
                "kind": "path-exists",
                "description": "Artifact exists",
                "path": "artifact.txt",
                "path_type": "file",
            },
            {
                "kind": "path-unchanged",
                "description": "Artifact remains unchanged",
                "path": "artifact.txt",
            },
            {
                "kind": "command-succeeds",
                "description": "Verification command succeeds",
                "argv": ["/usr/bin/true"],
                "cwd": ".",
                "timeout_seconds": 5,
            },
            {
                "kind": "action-audit-certain",
                "description": "No uncertain Action remains",
            },
        ),
    )

    passed = run_host_acceptance_checks(
        tmp_path,
        task,
        command_tool_factory=lambda _workspace: SuccessfulCommandTool(),
    )

    assert [result.outcome for result in passed] == [AcceptanceCheckOutcome.PASSED] * 4
    assert "command=true status=exited exit=0 cleanup=complete" in passed[2].evidence

    (tmp_path / "artifact.txt").write_text("changed\n", encoding="utf-8")
    changed = run_host_acceptance_checks(
        tmp_path,
        store.inspect(task.task_id),
        command_tool_factory=lambda _workspace: SuccessfulCommandTool(),
    )
    assert changed[0].outcome is AcceptanceCheckOutcome.PASSED
    assert changed[1].outcome is AcceptanceCheckOutcome.FAILED


def test_reviewer_request_is_no_tools_independent_and_includes_only_selected_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "first.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second\n", encoding="utf-8")
    _store, task = proposed_task(
        tmp_path,
        (
            {
                "kind": "independent-reviewer",
                "description": "First file is correct",
                "paths": ["first.txt"],
            },
            {
                "kind": "independent-reviewer",
                "description": "Second file is correct",
                "paths": ["second.txt"],
            },
        ),
    )

    request = build_task_review_request(task, tmp_path, reviewer_indices=(2,))

    assert request.allow_tools is False
    assert len(request.history) == 1
    assert isinstance(request.history[0], UserMessage)
    payload = json.loads(request.history[0].text)
    assert payload["reviewer_criterion_indices"] == [2]
    assert set(payload["files"]) == {"second.txt"}
    assert payload["files"]["second.txt"]["content"] == "second\n"


@pytest.mark.parametrize("path", (".env", "nested/.env.local", ".git/config"))
def test_reviewer_rejects_private_or_credential_paths(tmp_path: Path, path: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret\n", encoding="utf-8")
    _store, task = proposed_task(
        tmp_path,
        (
            {
                "kind": "independent-reviewer",
                "description": "Unsafe review",
                "paths": [path],
            },
        ),
    )

    with pytest.raises(TaskVerificationError, match="private or credential"):
        build_task_review_request(task, tmp_path)


def test_reviewer_response_parser_requires_exact_complete_json_verdicts() -> None:
    parsed = parse_task_review_response(
        AssistantText(
            '{"verdicts":[{"criterion_index":2,"verdict":"passed",'
            '"evidence":"The explicit file snapshot satisfies the criterion."}]}'
        ),
        expected_indices=(2,),
    )

    assert parsed[0].criterion_index == 2
    assert parsed[0].outcome is AcceptanceCheckOutcome.PASSED

    for invalid in (
        AssistantText("not-json"),
        AssistantText('{"verdicts":[]}'),
        AssistantText('{"verdicts":[{"criterion_index":2,"verdict":"error","evidence":"bad"}]}'),
    ):
        with pytest.raises(TaskVerificationError):
            parse_task_review_response(invalid, expected_indices=(2,))


def test_human_criteria_remain_distinct_from_host_and_reviewer_criteria(
    tmp_path: Path,
) -> None:
    session = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session.release()
    task = TaskStore(tmp_path).create(
        "Mixed acceptance",
        acceptance_criteria=("Human approves release",),
        structured_criteria=(
            {
                "kind": "path-exists",
                "description": "Artifact exists",
                "path": "artifact.txt",
                "path_type": "file",
            },
        ),
    )

    assert [criterion.kind for criterion in task.criteria] == [
        AcceptanceCriterionKind.HUMAN,
        AcceptanceCriterionKind.PATH_EXISTS,
    ]
