from __future__ import annotations

import io
import sys
from pathlib import Path

from leonervis_code.cli.main import main
from leonervis_code.mcp.client import McpStdioClient
from leonervis_code.tools.command_sandbox import CommandSandboxLaunch


FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"


class PassthroughSandbox:
    def prepare_launch(self, *, workspace, cwd, argv, environment) -> CommandSandboxLaunch:
        return CommandSandboxLaunch(argv=argv, cwd=cwd, environment=dict(environment))


def client_factory(workspace, *, environment):
    return McpStdioClient(
        workspace,
        environment=environment,
        command_sandbox=PassthroughSandbox(),
    )


def test_mcp_cli_configures_disabled_server_then_enables_probes_and_removes(tmp_path) -> None:
    user_path = tmp_path / "config" / "mcp.json"
    project_path = tmp_path / ".leonervis-code" / "mcp.json"
    common = {
        "cwd": tmp_path,
        "environment": {"HOST_TOKEN": "secret-value"},
        "user_mcp_path": user_path,
        "project_mcp_path": project_path,
        "mcp_client_factory": client_factory,
    }
    output = io.StringIO()
    errors = io.StringIO()

    assert (
        main(
            [
                "mcp",
                "add",
                "fixture",
                "--command",
                sys.executable,
                "--arg",
                str(FIXTURE),
                "--arg",
                "normal",
                "--env",
                "SERVICE_TOKEN=HOST_TOKEN",
            ],
            stdout=output,
            stderr=errors,
            **common,
        )
        == 0
    )
    assert "disabled" in output.getvalue()
    assert "secret-value" not in project_path.read_text(encoding="utf-8")

    output = io.StringIO()
    assert main(["mcp", "list"], stdout=output, stderr=errors, **common) == 0
    assert "fixture: project, disabled, not ready, r1" in output.getvalue()

    output = io.StringIO()
    assert main(["mcp", "show", "fixture"], stdout=output, stderr=errors, **common) == 0
    assert "SERVICE_TOKEN<-HOST_TOKEN" in output.getvalue()
    assert "secret-value" not in output.getvalue()

    probe_errors = io.StringIO()
    assert main(["mcp", "probe", "fixture"], stderr=probe_errors, **common) == 2
    assert "mcp_server_disabled" in probe_errors.getvalue()

    assert main(["mcp", "enable", "fixture"], stderr=errors, **common) == 0
    output = io.StringIO()
    assert main(["mcp", "probe", "fixture"], stdout=output, stderr=errors, **common) == 0
    rendered = output.getvalue()
    assert "MCP probe succeeded: fixture" in rendered
    assert "read_widget" in rendered
    assert "list_widgets" in rendered
    assert "UNTRUSTED" not in rendered
    assert "secret-value" not in rendered
    assert "not added to any model ToolSet" in rendered

    assert main(["mcp", "remove", "fixture", "--if-revision", "2"], **common) == 0
    output = io.StringIO()
    assert main(["mcp", "list"], stdout=output, stderr=errors, **common) == 0
    assert output.getvalue() == "No MCP servers configured.\n"


def test_mcp_cli_rejects_provider_selection_and_stale_revision(tmp_path) -> None:
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_mcp_path": tmp_path / "user.json",
        "project_mcp_path": tmp_path / "project.json",
        "mcp_client_factory": client_factory,
    }
    assert (
        main(
            ["mcp", "add", "fixture", "--command", sys.executable],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            **common,
        )
        == 0
    )
    errors = io.StringIO()
    assert (
        main(
            ["mcp", "enable", "fixture", "--if-revision", "9"],
            stderr=errors,
            **common,
        )
        == 2
    )
    assert "revision conflict" in errors.getvalue()

    errors = io.StringIO()
    assert main(["--model", "fake", "mcp", "list"], stderr=errors, **common) == 2
    assert "cannot be combined with MCP management" in errors.getvalue()
