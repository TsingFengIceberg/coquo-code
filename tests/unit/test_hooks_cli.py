from __future__ import annotations

import io

from leonervis_code.cli.main import main
from leonervis_code.hooks import HookStore


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
    assert "protect-config: project, deny, disabled, r1" in output.getvalue()
    output = io.StringIO()
    assert main(["hooks", "show", "protect-config"], stdout=output, **common) == 0
    assert "Path prefixes: config" in output.getvalue()
    assert "Message: Configuration requires review." in output.getvalue()

    assert main(["hooks", "enable", "protect-config", "--if-revision", "1"], **common) == 0
    output = io.StringIO()
    assert main(["hooks", "doctor"], stdout=output, **common) == 0
    assert "Hook configuration: valid" in output.getvalue()
    assert "Active: 1" in output.getvalue()
    assert "hooks-v1-" in output.getvalue()

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
