from __future__ import annotations

import sys
from pathlib import Path

from leonervis_code.core.action_coordinator import ApprovalResolution
from leonervis_code.core.contracts import AssistantText, ToolArguments, ToolUse
from leonervis_code.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from leonervis_code.mcp.catalog import McpCatalogService
from leonervis_code.mcp.client import McpStdioClient
from leonervis_code.mcp.config import McpServerConfiguration, McpServerStore
from leonervis_code.providers.fake import ScriptedFakeProvider
from leonervis_code.session import ProjectSession
from leonervis_code.session_records import ActionAuditStatus
from leonervis_code.tools.command_sandbox import CommandSandboxLaunch


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
    project_path = tmp_path / ".leonervis-code" / "mcp-servers-test.json"
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
