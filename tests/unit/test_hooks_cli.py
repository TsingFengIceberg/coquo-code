from __future__ import annotations

import hashlib
import io
import json
from uuid import UUID

from coquo.cli.main import main
from coquo.core.contracts import AssistantText, ToolTurnLedger, UserMessage
from coquo.hooks import HookStore
from coquo.core.hook_contracts import (
    HookActionOutcome,
    HookAuditEntry,
    HookAuditLedger,
    HookEffect,
    HookEvent,
)
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.task_store import TaskStore


def test_hooks_cli_adds_disabled_rule_then_enables_inspects_and_removes(tmp_path) -> None:
    user_path = tmp_path / "config" / "hooks.json"
    project_path = tmp_path / ".coquo" / "hooks.json"
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
    assert "hooks-v3-" in output.getvalue()

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


def test_hooks_cli_fingerprints_configures_and_checks_local_handler(tmp_path) -> None:
    executable = tmp_path / "handler.py"
    executable.write_text(
        '#!/usr/bin/python3\nprint("{\\"version\\":1,\\"effect\\":\\"continue\\",\\"message\\":\\"\\"}")\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": tmp_path / "user.json",
        "project_hooks_path": tmp_path / "project.json",
    }

    output = io.StringIO()
    assert main(["hooks", "fingerprint", "handler.py"], stdout=output, **common) == 0
    assert output.getvalue() == f"{digest}\n"
    assert (
        main(
            [
                "hooks",
                "add",
                "local-observer",
                "--event",
                "after_action",
                "--effect",
                "continue",
                "--handler-executable",
                "handler.py",
                "--handler-timeout",
                "5",
                "--handler-sha256",
                digest,
            ],
            stdout=io.StringIO(),
            **common,
        )
        == 0
    )
    configured = HookStore(common["user_hooks_path"], common["project_hooks_path"]).get_hook(
        "local-observer"
    )
    assert configured.rule.handler is not None
    assert configured.rule.enabled is False
    assert main(["hooks", "enable", "local-observer"], stdout=io.StringIO(), **common) == 0

    output = io.StringIO()
    assert main(["hooks", "doctor"], stdout=output, **common) == 0
    assert "Executable handlers: 1" in output.getvalue()
    assert "Handlers ready: 1" in output.getvalue()
    assert "Handlers stale: 0" in output.getvalue()

    executable.write_text("#!/usr/bin/python3\nprint('changed')\n", encoding="utf-8")
    output = io.StringIO()
    assert main(["hooks", "doctor"], stdout=output, **common) == 0
    assert "Handlers stale: 1" in output.getvalue()


def test_hooks_cli_import_is_closed_workspace_local_and_disabled(tmp_path) -> None:
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": tmp_path / "user.json",
        "project_hooks_path": tmp_path / "project.json",
    }
    output = io.StringIO()
    assert main(["hooks", "template", "local-handler"], stdout=output, **common) == 0
    value = json.loads(output.getvalue())
    value["hook"]["hook_id"] = "imported-handler"
    candidate = tmp_path / "hook-import.json"
    candidate.write_text(json.dumps(value), encoding="utf-8")

    output = io.StringIO()
    assert main(["hooks", "import", "hook-import.json"], stdout=output, **common) == 0
    assert "(disabled)" in output.getvalue()
    imported = HookStore(common["user_hooks_path"], common["project_hooks_path"]).get_hook(
        "imported-handler"
    )
    assert imported.rule.enabled is False
    assert imported.rule.revision == 1

    errors = io.StringIO()
    assert main(["hooks", "import", "../hook-import.json"], stderr=errors, **common) == 2
    assert "workspace-relative" in errors.getvalue()


def test_hooks_cli_rejects_incomplete_or_invalid_handler_configuration(tmp_path) -> None:
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_hooks_path": tmp_path / "user.json",
        "project_hooks_path": tmp_path / "project.json",
    }
    errors = io.StringIO()
    assert (
        main(
            [
                "hooks",
                "add",
                "bad-handler",
                "--effect",
                "continue",
                "--handler-executable",
                "handler.py",
            ],
            stderr=errors,
            **common,
        )
        == 2
    )
    assert "requires --handler-executable" in errors.getvalue()

    errors = io.StringIO()
    assert main(["hooks", "fingerprint", "missing.py"], stderr=errors, **common) == 2
    assert "handler_executable_unavailable" in errors.getvalue()
