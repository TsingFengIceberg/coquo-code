from __future__ import annotations

import sys
from pathlib import Path

import pytest

from leonervis_code.mcp.client import (
    McpClientError,
    McpNotificationKind,
    McpStdioClient,
)
from leonervis_code.mcp.config import McpServerConfiguration, McpServerEntry
from leonervis_code.tools.command_sandbox import CommandSandboxLaunch


FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"


class PassthroughSandbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def prepare_launch(self, *, workspace, cwd, argv, environment) -> CommandSandboxLaunch:
        self.calls.append(
            {
                "workspace": workspace,
                "cwd": cwd,
                "argv": argv,
                "environment": dict(environment),
            }
        )
        return CommandSandboxLaunch(argv=argv, cwd=cwd, environment=dict(environment))


def entry(mode: str = "normal", **changes) -> McpServerEntry:
    values = {
        "name": "fixture",
        "command": sys.executable,
        "args": (str(FIXTURE), mode),
        "enabled": True,
    }
    values.update(changes)
    return McpServerEntry("project", McpServerConfiguration(**values))


def client(tmp_path, *, environment=None, sandbox=None) -> McpStdioClient:
    return McpStdioClient(
        tmp_path,
        environment={} if environment is None else environment,
        command_sandbox=sandbox or PassthroughSandbox(),
    )


def test_probe_initializes_lists_paginated_tools_and_cleans_up(tmp_path) -> None:
    sandbox = PassthroughSandbox()
    probe = client(tmp_path, environment={"HOST_TOKEN": "secret-value"}, sandbox=sandbox)
    configured = entry(environment=(("SERVICE_TOKEN", "HOST_TOKEN"),))

    result = probe.probe(configured)

    assert result.protocol_version == "2025-06-18"
    assert result.server_name == "fixture-server"
    assert result.server_version == "1.2.3"
    assert result.capability_names == ("tools",)
    assert [tool.name for tool in result.tools] == ["read_widget", "list_widgets"]
    assert result.pages == 2
    assert result.cleanup_complete is True
    assert sandbox.calls[0]["environment"] == {
        "PWD": str(tmp_path),
        "SERVICE_TOKEN": "secret-value",
    }


def test_probe_without_tools_capability_does_not_send_tools_list(tmp_path) -> None:
    result = client(tmp_path).probe(entry("no-tools"))

    assert result.capability_names == ()
    assert result.tools == ()
    assert result.pages == 0


@pytest.mark.parametrize(
    "mode, code",
    [
        ("malformed-json", "mcp_message_invalid"),
        ("duplicate-key", "mcp_message_invalid"),
        ("wrong-id", "mcp_response_id_mismatch"),
        ("unsupported-version", "mcp_protocol_unsupported"),
        ("server-request", "mcp_server_request_unsupported"),
        ("repeated-cursor", "mcp_cursor_repeated"),
        ("duplicate-tool", "mcp_tool_duplicate"),
    ],
)
def test_probe_rejects_protocol_violations_with_sanitized_errors(tmp_path, mode, code) -> None:
    with pytest.raises(McpClientError) as raised:
        client(tmp_path).probe(entry(mode))

    assert raised.value.code == code
    assert raised.value.cleanup_complete is True
    assert "UNTRUSTED" not in str(raised.value)
    assert "SECRET" not in str(raised.value)


def test_probe_hides_json_rpc_error_and_stderr_content(tmp_path) -> None:
    with pytest.raises(McpClientError) as raised:
        client(tmp_path).probe(entry("rpc-error"))
    assert raised.value.code == "mcp_server_error"
    assert "SECRET_SERVER_ERROR" not in str(raised.value)

    result = client(tmp_path).probe(entry("stderr"))
    assert result.stderr_bytes > 0
    assert result.stderr_truncated is True
    assert "SECRET_STDERR" not in repr(result)


def test_probe_timeout_terminates_temporary_process(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("leonervis_code.mcp.client.MCP_INITIALIZE_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(McpClientError) as raised:
        client(tmp_path).probe(entry("timeout"))

    assert raised.value.code == "mcp_timeout"
    assert raised.value.cleanup_complete is True


def test_probe_requires_enabled_available_command_and_named_environment(tmp_path) -> None:
    probe = client(tmp_path)
    disabled = entry(enabled=False)
    unavailable = entry(command="/missing/mcp-server")
    missing_environment = entry(environment=(("TOKEN", "HOST_SECRET_NAME"),))

    assert probe.inspect_status(disabled).ready is False
    with pytest.raises(McpClientError, match="disabled"):
        probe.probe(disabled)
    with pytest.raises(McpClientError, match="unavailable"):
        probe.probe(unavailable)
    with pytest.raises(McpClientError, match="HOST_SECRET_NAME") as raised:
        probe.probe(missing_environment)
    assert "secret-value" not in str(raised.value)


def test_default_client_uses_read_only_workspace_sandbox(tmp_path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    class RecordingSandbox:
        def __init__(self, *, workspace_writable: bool) -> None:
            observed["workspace_writable"] = workspace_writable

    monkeypatch.setattr("leonervis_code.mcp.client.LinuxBubblewrapCommandSandbox", RecordingSandbox)

    McpStdioClient(tmp_path, environment={})

    assert observed == {"workspace_writable": False}


def test_tool_call_retains_only_bounded_notification_kinds_and_counts(tmp_path) -> None:
    session = client(tmp_path).connect(entry("call-notifications"))
    activities = []

    call = session.call_tool(
        "read_widget",
        {"widget": "blue"},
        process_generation=1,
        process_reused=False,
        notification_sink=activities.append,
    )

    assert activities == [
        McpNotificationKind.PROGRESS,
        McpNotificationKind.MESSAGE,
        McpNotificationKind.TOOLS_LIST_CHANGED,
    ]
    assert call.notifications.progress_count == 1
    assert call.notifications.message_count == 1
    assert call.notifications.tools_list_changed_count == 1
    assert call.notifications.ignored_count == 1
    assert "SECRET" not in repr(call.notifications)
    assert session.close() is True


@pytest.mark.parametrize(
    "mode,code",
    [
        ("call-malformed-notification", "mcp_notification_invalid"),
        ("call-notification-flood", "mcp_notification_limit"),
    ],
)
def test_tool_call_rejects_malformed_or_flooded_notifications(tmp_path, mode, code) -> None:
    session = client(tmp_path).connect(entry(mode))

    with pytest.raises(McpClientError) as raised:
        session.call_tool(
            "read_widget",
            {"widget": "blue"},
            process_generation=1,
            process_reused=False,
        )

    assert raised.value.code == code
    assert raised.value.outcome_uncertain is True
    assert "SECRET" not in str(raised.value)
    assert session.close() is True
