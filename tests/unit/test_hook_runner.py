from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from coquo.core.hook_contracts import (
    HookActionOutcome,
    HookEffect,
    HookEvent,
    HookHandlerSpec,
)
from coquo.hook_runner import (
    HookHandlerEvent,
    HookHandlerPreparationError,
    HookRunner,
)
from coquo.hooks import HookEntry, HookRule
from coquo.tools.command_sandbox import CommandSandboxLaunch
from coquo.tools.run_command import RunCommandTool


class DirectSandbox:
    def prepare_launch(self, *, workspace, cwd, argv, environment) -> CommandSandboxLaunch:
        return CommandSandboxLaunch(argv=argv, cwd=cwd, environment=dict(environment))


def write_handler(tmp_path: Path, body: str, *, name: str = "handler.py") -> tuple[Path, str]:
    path = tmp_path / name
    path.write_text("#!/usr/bin/python3\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def runner(tmp_path: Path) -> HookRunner:
    return HookRunner(
        tmp_path,
        RunCommandTool(tmp_path, environment={"PATH": "/usr/bin"}, command_sandbox=DirectSandbox()),
    )


def entry(executable: str, digest: str, *, event: HookEvent = HookEvent.AFTER_ACTION) -> HookEntry:
    return HookEntry(
        "project",
        HookRule(
            "local-check",
            HookEffect.CONTINUE,
            event=event,
            enabled=True,
            handler=HookHandlerSpec(executable, (), 5, digest),
        ),
    )


def action_event(event: HookEvent = HookEvent.AFTER_ACTION) -> HookHandlerEvent:
    return HookHandlerEvent(
        event=event,
        hook_set_id="hooks-v3-" + "a" * 64,
        subject_id="tool-use-1",
        tool_name="write_file",
        permission_action="workspace-create",
        source="builtin",
        action_outcome=(HookActionOutcome.SUCCEEDED if event is HookEvent.AFTER_ACTION else None),
    )


def test_runner_executes_pinned_direct_argv_and_parses_closed_result(tmp_path: Path) -> None:
    _, digest = write_handler(
        tmp_path,
        """import json, sys
flag, payload = sys.argv[-2:]
event = json.loads(payload)
assert flag == "--coquo-hook-event-v1"
assert set(event) == {"action_outcome", "event", "hook_id", "hook_set_id", "permission_action", "source", "subject_id", "tool_name", "version"}
print(json.dumps({"version": 1, "effect": "advisory", "message": "Review generated output."}))
""",
    )
    hook_runner = runner(tmp_path)
    prepared = hook_runner.prepare(
        entry("handler.py", digest),
        action_event(),
        tool_use_id="handler-1",
    )

    assert prepared.command.argv[0] == str(tmp_path / "handler.py")
    assert prepared.identity_arguments.as_mapping() == {
        "event": "after_action",
        "executable": str(tmp_path / "handler.py"),
        "executable_sha256": digest,
        "hook_id": "local-check",
        "hook_set_id": "hooks-v3-" + "a" * 64,
        "subject_id": "tool-use-1",
        "timeout_seconds": 5,
    }
    assert hook_runner.revalidate(prepared) == prepared.precondition
    executed = hook_runner.execute(prepared)

    assert executed.result_code == "hook_handler_advisory"
    assert executed.handler_result is not None
    assert executed.handler_result.effect is HookEffect.ADVISORY
    assert executed.handler_result.message == "Review generated output."
    assert "Review generated output" not in executed.tool_result.content


def test_runner_rejects_stale_symlink_nonregular_and_unexecutable_handlers(
    tmp_path: Path,
) -> None:
    path, digest = write_handler(
        tmp_path,
        'print("{\\"version\\":1,\\"effect\\":\\"continue\\",\\"message\\":\\"\\"}")\n',
    )
    hook_runner = runner(tmp_path)
    configured = entry("handler.py", digest)

    path.write_text("#!/usr/bin/python3\nprint('changed')\n", encoding="utf-8")
    with pytest.raises(HookHandlerPreparationError, match="fingerprint_mismatch"):
        hook_runner.prepare(configured, action_event(), tool_use_id="handler-1")

    path.chmod(0o600)
    with pytest.raises(HookHandlerPreparationError, match="not_executable"):
        hook_runner.executable_sha256("handler.py")

    path.unlink()
    path.mkdir()
    with pytest.raises(HookHandlerPreparationError, match="not_regular"):
        hook_runner.executable_sha256("handler.py")

    target, _ = write_handler(tmp_path, "print('{}')\n", name="target.py")
    (tmp_path / "linked.py").symlink_to(target)
    with pytest.raises(HookHandlerPreparationError, match="symlink_rejected"):
        hook_runner.executable_sha256("linked.py")


@pytest.mark.parametrize(
    ("body", "result_code"),
    [
        ("print('not-json')\n", "hook_handler_protocol_invalid"),
        (
            'print("{\\"version\\":1,\\"effect\\":\\"deny\\",\\"message\\":\\"no\\"}")\n',
            "hook_handler_protocol_invalid",
        ),
        ("raise SystemExit(7)\n", "hook_handler_command_exited_nonzero"),
    ],
)
def test_runner_rejects_invalid_observation_results_and_nonzero_exit(
    tmp_path: Path,
    body: str,
    result_code: str,
) -> None:
    _, digest = write_handler(tmp_path, body)
    prepared = runner(tmp_path).prepare(
        entry("handler.py", digest),
        action_event(),
        tool_use_id="handler-1",
    )

    executed = runner(tmp_path).execute(prepared)

    assert executed.result_code == result_code
    assert executed.handler_result is None
    assert executed.tool_result.is_error


def test_preauthorization_handler_may_return_deny(tmp_path: Path) -> None:
    _, digest = write_handler(
        tmp_path,
        'print("{\\"version\\":1,\\"effect\\":\\"deny\\",\\"message\\":\\"Blocked by local policy.\\"}")\n',
    )
    hook_runner = runner(tmp_path)
    prepared = hook_runner.prepare(
        entry("handler.py", digest, event=HookEvent.BEFORE_ACTION_AUTHORIZATION),
        action_event(HookEvent.BEFORE_ACTION_AUTHORIZATION),
        tool_use_id="handler-1",
    )

    executed = hook_runner.execute(prepared)

    assert executed.handler_result is not None
    assert executed.handler_result.effect is HookEffect.DENY
    assert executed.handler_result.message == "Blocked by local policy."


def test_handler_timeout_is_bounded_and_has_no_automatic_retry(tmp_path: Path) -> None:
    _, digest = write_handler(tmp_path, "import time\ntime.sleep(2)\n")
    configured = HookEntry(
        "project",
        HookRule(
            "slow-handler",
            HookEffect.CONTINUE,
            event=HookEvent.AFTER_ACTION,
            enabled=True,
            handler=HookHandlerSpec("handler.py", (), 1, digest),
        ),
    )
    hook_runner = runner(tmp_path)
    prepared = hook_runner.prepare(configured, action_event(), tool_use_id="handler-1")

    executed = hook_runner.execute(prepared)

    assert executed.result_code == "hook_handler_command_timed_out"
    assert executed.handler_result is None
    assert executed.tool_result.is_error
