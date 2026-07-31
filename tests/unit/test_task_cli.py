from __future__ import annotations

import io
from pathlib import Path

from leonervis_code.cli.main import main
from leonervis_code.session_records import BindingSnapshot
from leonervis_code.session_store import SessionStore
from leonervis_code.task_store import TaskStore
from leonervis_code.task_records import StageFailureReason


def invoke(tmp_path: Path, arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = main(
        arguments,
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    return status, stdout.getvalue(), stderr.getvalue()


def test_task_cli_create_list_and_show_without_session_mutation(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner_session_id = writer.session_id
    session_path = writer.path
    writer.release()
    before = session_path.read_bytes()

    status, created, errors = invoke(
        tmp_path,
        [
            "task",
            "create",
            "Implement durable stages",
            "--accept",
            "Survives restart",
            "--accept",
            "No provider call",
        ],
    )

    assert status == 0
    assert errors == ""
    assert "Task: Implement durable stages" in created
    assert f"Owner Session: {owner_session_id}" in created
    assert "1. Survives restart" in created
    task = TaskStore(tmp_path).list()[0]
    with TaskStore(tmp_path).open(task.task_id) as writer:
        writer.start_stage("Attempt one bounded step")
        writer.fail_stage(StageFailureReason.PROVIDER_ERROR)

    status, listed, errors = invoke(tmp_path, ["task", "list"])
    assert status == 0
    assert errors == ""
    assert task.task_id in listed
    assert "Implement durable stages" in listed
    assert "blocked, 1 stages" in listed

    status, shown, errors = invoke(tmp_path, ["task", "show", task.task_id])
    assert status == 0
    assert errors == ""
    assert "Latest Stage: #1 failed" in shown
    assert "Stage objective: Attempt one bounded step" in shown
    assert "Stage failure: provider-error" in shown
    assert "Stage usage: 0 provider calls, 0 tool requests" in shown
    assert session_path.read_bytes() == before


def test_task_cli_create_requires_an_existing_owner_session(tmp_path: Path) -> None:
    status, output, errors = invoke(tmp_path, ["task", "create", "Unowned task"])

    assert status == 2
    assert output == ""
    assert "task error: owner Session is invalid or unavailable" in errors
    assert not (tmp_path / ".leonervis-code").exists()


def test_task_cli_list_is_read_only_when_no_task_storage_exists(tmp_path: Path) -> None:
    status, output, errors = invoke(tmp_path, ["task", "list"])

    assert status == 0
    assert output == "No durable Tasks found.\n"
    assert errors == ""
    assert not (tmp_path / ".leonervis-code").exists()


def test_task_cli_exposes_configuration_filters_parent_and_timeline(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    writer.release()

    status, parent_output, errors = invoke(
        tmp_path,
        ["task", "create", "Parent objective", "--name", "Parent Task"],
    )
    assert status == 0 and errors == ""
    parent = TaskStore(tmp_path).list()[0]
    assert "Task name: Parent Task" in parent_output

    status, child_output, errors = invoke(
        tmp_path,
        [
            "task",
            "create",
            "Child objective",
            "--name",
            "Release Child",
            "--parent",
            parent.task_id,
            "--accept",
            "Tests pass",
            "--max-stages",
            "4",
            "--max-provider-invocations",
            "40",
            "--max-tool-requests",
            "64",
            "--max-input-tokens",
            "10000",
            "--max-output-tokens",
            "2000",
        ],
    )
    assert status == 0 and errors == ""
    child = TaskStore(tmp_path).list()[0]
    assert f"Derived from: {parent.task_id}" in child_output
    assert "Budget: stages 0/4, provider calls 0/40, tool requests 0/64" in child_output
    assert child.parent_task_id == parent.task_id
    assert child.budget.max_input_tokens == 10_000
    assert child.budget.max_output_tokens == 2_000

    status, listed, errors = invoke(
        tmp_path,
        [
            "task",
            "list",
            "--limit",
            "1",
            "--status",
            "ready",
            "--archive",
            "active",
            "--name",
            "release",
        ],
    )
    assert status == 0 and errors == ""
    assert child.task_id in listed
    assert parent.task_id not in listed

    with TaskStore(tmp_path).open(child.task_id) as task_writer:
        task_writer.start_stage("Interrupted Stage")
        task_writer.fail_stage(StageFailureReason.INTERRUPTED)
    status, timeline, errors = invoke(tmp_path, ["task", "timeline", child.task_id])
    assert status == 0 and errors == ""
    assert "Task timeline: Release Child" in timeline
    assert "#1 [execution] failed: Interrupted Stage -> interrupted" in timeline
