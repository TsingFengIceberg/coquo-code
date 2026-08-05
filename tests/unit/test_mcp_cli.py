from __future__ import annotations

import io
import sys
from pathlib import Path

from leonervis_code.cli.main import main
from leonervis_code.mcp.client import McpStdioClient
from leonervis_code.mcp.catalog import McpCatalogService
from leonervis_code.mcp.config import McpServerStore
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
    assert "use mcp catalog to inspect normalized quarantine candidates" in rendered

    output = io.StringIO()
    assert main(["mcp", "doctor", "fixture"], stdout=output, stderr=errors, **common) == 0
    assert "MCP conformance: passed" in output.getvalue()
    assert "Legacy HTTP/SSE: intentionally unsupported" in output.getvalue()

    output = io.StringIO()
    assert main(["mcp", "catalog"], stdout=output, stderr=errors, **common) == 0
    catalog = output.getvalue()
    assert "MCP quarantine catalog: mcp-catalog-v1-" in catalog
    assert "Candidates: 2 accepted, 0 rejected" in catalog
    assert "UNTRUSTED" not in catalog
    assert "secret-value" not in catalog

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


def test_mcp_cli_adds_remote_server_without_persisting_bearer_value(tmp_path) -> None:
    user_path = tmp_path / "user.json"
    project_path = tmp_path / "project.json"
    common = {
        "cwd": tmp_path,
        "environment": {"REMOTE_TOKEN": "secret-value"},
        "user_mcp_path": user_path,
        "project_mcp_path": project_path,
        "mcp_client_factory": client_factory,
    }
    output = io.StringIO()
    assert (
        main(
            [
                "mcp",
                "add-http",
                "remote",
                "--endpoint",
                "https://mcp.example.test/mcp",
                "--bearer-token-env",
                "REMOTE_TOKEN",
                "--expose-workspace-root",
            ],
            stdout=output,
            **common,
        )
        == 0
    )
    configured = McpServerStore(user_path, project_path).get_server("remote").configuration
    assert configured.endpoint == "https://mcp.example.test/mcp"
    assert configured.bearer_token_env == "REMOTE_TOKEN"
    assert configured.expose_workspace_root is True
    assert "secret-value" not in project_path.read_text(encoding="utf-8")


def test_mcp_cli_sets_inspects_and_clears_exact_tool_policy(tmp_path) -> None:
    user_path = tmp_path / "config" / "mcp.json"
    project_path = tmp_path / ".leonervis-code" / "mcp.json"
    user_policy_path = tmp_path / "config" / "mcp-policy.json"
    project_policy_path = tmp_path / ".leonervis-code" / "mcp-policy.json"
    common = {
        "cwd": tmp_path,
        "environment": {},
        "user_mcp_path": user_path,
        "project_mcp_path": project_path,
        "user_mcp_policy_path": user_policy_path,
        "project_mcp_policy_path": project_policy_path,
        "mcp_client_factory": client_factory,
    }
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
                "--enabled",
            ],
            **common,
        )
        == 0
    )
    store = McpServerStore(user_path, project_path)
    candidate = (
        McpCatalogService(store, client_factory(tmp_path, environment={}))
        .snapshot(refresh=True)
        .accepted[0]
    )

    output = io.StringIO()
    assert (
        main(
            [
                "mcp",
                "policy",
                "set",
                candidate.qualified_name,
                "--schema-fingerprint",
                candidate.schema_fingerprint,
                "--action",
                "workspace-read",
            ],
            stdout=output,
            **common,
        )
        == 0
    )
    assert "workspace-read" in output.getvalue()

    output = io.StringIO()
    assert main(["mcp", "policy", "list"], stdout=output, **common) == 0
    assert candidate.qualified_name in output.getvalue()
    assert candidate.schema_fingerprint in output.getvalue()
    output = io.StringIO()
    assert main(["mcp", "policy", "show", candidate.qualified_name], stdout=output, **common) == 0
    assert "Policy revision: 1" in output.getvalue()

    errors = io.StringIO()
    assert (
        main(
            [
                "mcp",
                "policy",
                "set",
                candidate.qualified_name,
                "--schema-fingerprint",
                "mcp-schema-v1-" + "0" * 64,
                "--action",
                "dangerous",
                "--replace",
            ],
            stderr=errors,
            **common,
        )
        == 2
    )
    assert "fingerprint is stale" in errors.getvalue()

    assert (
        main(
            [
                "mcp",
                "policy",
                "clear",
                candidate.qualified_name,
                "--if-revision",
                "1",
            ],
            **common,
        )
        == 0
    )
