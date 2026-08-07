from __future__ import annotations

import io
from uuid import UUID

from leonervis_code.cli.main import main
from leonervis_code.core.contracts import AssistantText, ToolTurnLedger, UserMessage
from leonervis_code.hooks import HookStore
from leonervis_code.core.hook_contracts import (
    HookActionOutcome,
    HookAuditEntry,
    HookAuditLedger,
    HookEffect,
    HookEvent,
)
from leonervis_code.session_records import BindingSnapshot
from leonervis_code.session_store import SessionStore
from leonervis_code.task_store import TaskStore


def test_hooks_cli_adds_disabled_rule_then_enables_inspects_and_removes(tmp_path) -> None:
    user_path = tmp_path / "config" / "hooks.json"
    project_path = tmp_path / ".leonervis-code" / "hooks.json"
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": user_path,
        "project_hooks_path": project_path,
    }
    output = io.StringIO()
    assert (
        main(
            [
                "hooks",
                "add",
                "protect-config",
                "--effect",
                "deny",
                "--tool",
                "write_file",
                "--action",
                "workspace-overwrite",
                "--path-prefix",
                "config",
                "--source",
                "builtin",
                "--message",
                "Configuration requires review.",
            ],
            stdout=output,
            **common,
        )
        == 0
    )
    assert "disabled" in output.getvalue()
    configured = HookStore(user_path, project_path).get_hook("protect-config")
    assert configured.rule.enabled is False

    output = io.StringIO()
    assert main(["hooks", "list"], stdout=output, **common) == 0
    assert (
        "protect-config: project, before_action_authorization, deny, disabled, r1"
        in output.getvalue()
    )
    output = io.StringIO()
    assert main(["hooks", "show", "protect-config"], stdout=output, **common) == 0
    assert "Path prefixes: config" in output.getvalue()
    assert "Message: Configuration requires review." in output.getvalue()

    assert main(["hooks", "enable", "protect-config", "--if-revision", "1"], **common) == 0
    output = io.StringIO()
    assert main(["hooks", "doctor"], stdout=output, **common) == 0
    assert "Hook configuration: valid" in output.getvalue()
    assert "Active: 1" in output.getvalue()
    assert "hooks-v2-" in output.getvalue()

    assert main(["hooks", "remove", "protect-config", "--if-revision", "2"], **common) == 0
    output = io.StringIO()
    assert main(["hooks", "list"], stdout=output, **common) == 0
    assert output.getvalue() == "No Hooks configured.\n"


def test_hooks_cli_rejects_stale_revision_and_provider_selection(tmp_path) -> None:
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": tmp_path / "user.json",
        "project_hooks_path": tmp_path / "project.json",
    }
    assert (
        main(
            ["hooks", "add", "ask-writes", "--effect", "require-ask", "--message", "ask"],
            stdout=io.StringIO(),
            **common,
        )
        == 0
    )
    errors = io.StringIO()
    assert (
        main(
            ["hooks", "enable", "ask-writes", "--if-revision", "9"],
            stderr=errors,
            **common,
        )
        == 2
    )
    assert "revision conflict" in errors.getvalue()
    errors = io.StringIO()
    assert main(["--model", "fake", "hooks", "list"], stderr=errors, **common) == 2
    assert "cannot be combined with Hook management" in errors.getvalue()


def test_hooks_cli_configures_after_action_and_lifecycle_events(tmp_path) -> None:
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": tmp_path / "user.json",
        "project_hooks_path": tmp_path / "project.json",
    }
    assert (
        main(
            [
                "hooks",
                "add",
                "failed-write",
                "--event",
                "after_action",
                "--effect",
                "advisory",
                "--tool",
                "write_file",
                "--outcome",
                "failed",
                "--message",
                "Inspect the failed write.",
            ],
            stdout=io.StringIO(),
            **common,
        )
        == 0
    )
    configured = HookStore(common["user_hooks_path"], common["project_hooks_path"]).get_hook(
        "failed-write"
    )
    assert configured.rule.event is HookEvent.AFTER_ACTION
    assert configured.rule.action_outcomes == (HookActionOutcome.FAILED,)

    output = io.StringIO()
    assert main(["hooks", "show", "failed-write"], stdout=output, **common) == 0
    assert "Event: after_action" in output.getvalue()
    assert "Outcomes: failed" in output.getvalue()

    errors = io.StringIO()
    assert (
        main(
            [
                "hooks",
                "add",
                "invalid-turn",
                "--event",
                "turn_committed",
                "--effect",
                "deny",
                "--message",
                "invalid",
            ],
            stderr=errors,
            **common,
        )
        == 2
    )
    assert "only continue or advisory" in errors.getvalue()


def test_hooks_cli_reads_session_and_task_evaluations_without_provider(tmp_path) -> None:
    session_id = "12345678-1234-4234-9234-123456789abc"
    hook_set_id = "hooks-v2-" + "a" * 64
    session_store = SessionStore(tmp_path, uuid_factory=lambda: UUID(session_id))
    writer = session_store.create(BindingSnapshot.fake())
    turn_audit = HookAuditLedger(
        (
            HookAuditEntry(
                HookEvent.TURN_COMMITTED,
                hook_set_id,
                session_id,
                (),
                HookEffect.CONTINUE,
            ),
        )
    )
    writer.append_turn(
        (UserMessage("hello"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
        hook_audit=turn_audit,
    )
    writer.release()

    task = TaskStore(tmp_path).create("Observe one Task", owner_session=session_id)
    task_writer = TaskStore(tmp_path).open(task.task_id)
    task_writer.start_stage(
        "Start observation",
        hook_audit=HookAuditLedger(
            (
                HookAuditEntry(
                    HookEvent.TASK_STAGE_STARTED,
                    hook_set_id,
                    task.task_id,
                    (),
                    HookEffect.CONTINUE,
                ),
            )
        ),
    )
    task_writer.release()

    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": tmp_path / "user.json",
        "project_hooks_path": tmp_path / "project.json",
    }
    output = io.StringIO()
    assert main(["hooks", "evaluations", session_id], stdout=output, **common) == 0
    assert "turn_committed" in output.getvalue()
    assert "matches=none" in output.getvalue()
    output = io.StringIO()
    assert main(["hooks", "task", task.task_id], stdout=output, **common) == 0
    assert "task_stage_started" in output.getvalue()
