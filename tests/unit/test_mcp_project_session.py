from __future__ import annotations

import sys
from pathlib import Path

from coquo.core.action_coordinator import ApprovalResolution
from coquo.core.contracts import AssistantText, ToolArguments, ToolUse
from coquo.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from coquo.agent.tool_events import (
    McpNotificationActivityReceived,
    ToolRequestFinished,
)
from coquo.mcp.catalog import McpCatalogService
from coquo.mcp.client import McpStdioClient
from coquo.mcp.config import McpServerConfiguration, McpServerStore
from coquo.mcp.policy import McpToolPolicyRule, McpToolPolicyStore
from coquo.providers.fake import ScriptedFakeProvider
from coquo.session import ProjectSession
from coquo.session_records import ActionAuditStatus
from coquo.tools.command_sandbox import CommandSandboxLaunch


FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"


class PassthroughSandbox:
    def prepare_launch(self, *, workspace, cwd, argv, environment) -> CommandSandboxLaunch:
        return CommandSandboxLaunch(argv=argv, cwd=cwd, environment=dict(environment))


def _client_factory(workspace, *, environment):
    return McpStdioClient(
        workspace,
        environment=environment,
        command_sandbox=PassthroughSandbox(),
    )


def _configured(tmp_path):
    user_path = tmp_path / "config" / "mcp.json"
    project_path = tmp_path / ".coquo" / "mcp-servers-test.json"
    store = McpServerStore(user_path, project_path)
    store.add_server(
        McpServerConfiguration(
            name="fixture",
            command=sys.executable,
            args=(str(FIXTURE), "normal"),
            enabled=True,
        ),
        scope="project",
    )
    client = _client_factory(tmp_path, environment={})
    catalog = McpCatalogService(store, client).snapshot(refresh=True)
    candidate = next(item for item in catalog.accepted if item.remote_name == "read_widget")
    return user_path, project_path, candidate


def _provider(candidate_name):
    return ScriptedFakeProvider(
        [
            ToolUse(
                "search-mcp",
                "tool_search",
                ToolArguments.from_mapping({"query": "widget", "max_results": 2}),
            ),
            ToolUse(
                "promote-mcp",
                "tool_promote",
                ToolArguments.from_mapping({"names": [candidate_name]}),
            ),
            ToolUse(
                "call-mcp",
                candidate_name,
                ToolArguments.from_mapping({"widget": "blue"}),
            ),
            AssistantText("MCP call handled"),
        ]
    )


def test_project_session_executes_promoted_mcp_through_permission_and_audit(tmp_path) -> None:
    user_path, project_path, candidate = _configured(tmp_path)
    provider = _provider(candidate.qualified_name)
    approvals = []

    def approve(request):
        approvals.append(request)
        return ApprovalResolution.ACCEPT

    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_mcp_path=user_path,
        project_mcp_path=project_path,
        fake_provider_factory=lambda: provider,
        mcp_client_factory=_client_factory,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )

    assert session.prompt("Find and call the widget tool") == "MCP call handled"
    assert len(approvals) == 1
    assert approvals[0].identity.action is PermissionAction.DANGEROUS
    assert approvals[0].identity.tool_name == candidate.qualified_name
    assert approvals[0].preview.kind.value == "mcp-tool"
    audit = session.action_audits()[0]
    assert audit.status is ActionAuditStatus.SUCCEEDED
    assert audit.result_code == "mcp_tool_succeeded"
    runtime = session.inspect_mcp_runtime()
    assert len(runtime) == 1
    assert runtime[0].calls_completed == 1
    session.close()


def test_project_session_denies_mcp_without_starting_runtime_process(tmp_path) -> None:
    user_path, project_path, candidate = _configured(tmp_path)
    provider = _provider(candidate.qualified_name)
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_mcp_path=user_path,
        project_mcp_path=project_path,
        fake_provider_factory=lambda: provider,
        mcp_client_factory=_client_factory,
        permission_mode=PermissionMode.READ_ONLY,
        approval_mode=ApprovalMode.AUTO,
    )

    assert session.prompt("Find and call the widget tool") == "MCP call handled"
    audit = session.action_audits()[0]
    assert audit.status is ActionAuditStatus.DENIED
    assert session.inspect_mcp_runtime() == ()
    session.close()


def test_exact_workspace_read_policy_allows_confined_mcp_in_read_only_mode(tmp_path) -> None:
    user_path, project_path, candidate = _configured(tmp_path)
    user_policy_path = tmp_path / "config" / "mcp-policy.json"
    project_policy_path = tmp_path / ".coquo" / "mcp-policy-test.json"
    policies = McpToolPolicyStore(user_policy_path, project_policy_path)
    policies.set_rule(
        McpToolPolicyRule(
            qualified_name=candidate.qualified_name,
            configured_name=candidate.configured_name,
            server_scope=candidate.scope,
            configuration_revision=candidate.configuration_revision,
            remote_name=candidate.remote_name,
            protocol_version=candidate.protocol_version,
            schema_fingerprint=candidate.schema_fingerprint,
            action=PermissionAction.WORKSPACE_READ,
        ),
        policy_scope="project",
    )
    provider = _provider(candidate.qualified_name)
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_mcp_path=user_path,
        project_mcp_path=project_path,
        user_mcp_policy_path=user_policy_path,
        project_mcp_policy_path=project_policy_path,
        fake_provider_factory=lambda: provider,
        mcp_client_factory=_client_factory,
        permission_mode=PermissionMode.READ_ONLY,
        approval_mode=ApprovalMode.AUTO,
    )

    assert session.prompt("Read the widget") == "MCP call handled"
    audit = session.action_audits()[0]
    assert audit.status is ActionAuditStatus.SUCCEEDED
    assert audit.identity.action is PermissionAction.WORKSPACE_READ
    session.close()


def test_project_session_surfaces_content_free_notifications_and_invalidates_catalog(
    tmp_path,
) -> None:
    user_path = tmp_path / "config" / "mcp.json"
    project_path = tmp_path / ".coquo" / "mcp-servers-test.json"
    store = McpServerStore(user_path, project_path)
    store.add_server(
        McpServerConfiguration(
            name="fixture",
            command=sys.executable,
            args=(str(FIXTURE), "call-notifications"),
            enabled=True,
        ),
        scope="project",
    )
    client = _client_factory(tmp_path, environment={})
    catalog = McpCatalogService(store, client).snapshot(refresh=True)
    candidate = next(item for item in catalog.accepted if item.remote_name == "read_widget")
    events = []
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_mcp_path=user_path,
        project_mcp_path=project_path,
        fake_provider_factory=lambda: _provider(candidate.qualified_name),
        mcp_client_factory=_client_factory,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
    )

    assert session.prompt("Read the widget", event_sink=events.append) == "MCP call handled"
    activities = [event for event in events if isinstance(event, McpNotificationActivityReceived)]
    assert [event.kind.value for event in activities] == [
        "progress",
        "message",
        "tools-list-changed",
    ]
    finished = [event for event in events if isinstance(event, ToolRequestFinished)][-1]
    assert finished.result_details is not None
    assert "catalog-invalidated=true" in finished.result_details.compact_summary
    assert "SECRET" not in repr(events)
    assert session._mcp_catalog_service._cached is None
    assert session.inspect_mcp_runtime() == ()
    session.close()
