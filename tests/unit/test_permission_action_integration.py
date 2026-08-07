from __future__ import annotations

from pathlib import Path
import subprocess
from uuid import UUID

import pytest

from leonervis_code.agent.tool_events import (
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
)
from leonervis_code.core.action_coordinator import ApprovalResolution, HumanApprovalRequest
from leonervis_code.core.approvals import ApprovalGrantError, ApprovalGrantRejection
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    ToolArguments,
    ToolResult,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.permissions import ApprovalMode, PermissionMode
from leonervis_code.hooks import HookConfigurationError, HookEffect, HookRule, HookStore
from leonervis_code.providers.request_context import RequestTokenCount, RequestTokenCountMethod
from leonervis_code.session import ProjectSession
from leonervis_code.session_records import (
    ActionAuditStatus,
    ActionExecutionFinished,
    ActionExecutionOutcome,
)
from leonervis_code.session_store import (
    ActionOutcomeAuditError,
    SessionStore,
    SessionStoreError,
)
from leonervis_code.tools import grep_regex as grep_regex_module
from leonervis_code.tools.catalog import MAX_TOOL_REQUESTS_PER_TURN
from leonervis_code.tools.web_search import (
    BRAVE_SEARCH_API_KEY_ENV,
    SearchHttpResponse,
    TAVILY_SEARCH_API_KEY_ENV,
    WebSearchPreparationError,
    WebSearchTool,
)
from leonervis_code.tools.download_file import DownloadFileTool
from leonervis_code.tools.move_directory import MoveDirectoryTool
from leonervis_code.tools.web_fetch import WebFetchTool
from leonervis_code.tools.web_transport import WebHttpResponse

SESSION_ID = "12345678-1234-4234-9234-123456789abc"
NOW = "2026-07-23T12:00:00.000000Z"


class ToolProvider:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.requests = []

    def count_input_tokens(self, _request):
        return RequestTokenCount(100, RequestTokenCountMethod.ESTIMATED)

    def respond(self, request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def write_call(
    path: str = "note.txt", content: str = "hello\n", *, tool_use_id: str = "write-1"
) -> ToolUse:
    return ToolUse(
        tool_use_id,
        "write_file",
        ToolArguments.from_mapping({"path": path, "content": content}),
    )


def session_store_factory(workspace: Path) -> SessionStore:
    return SessionStore(
        workspace,
        uuid_factory=lambda: UUID(SESSION_ID),
        clock=lambda: NOW,
    )


def uuid_factory():
    values = iter(UUID(int=index, version=4) for index in range(1, 129))
    return lambda: next(values)


def open_session(
    workspace: Path,
    provider: ToolProvider,
    *,
    permission_mode: PermissionMode = PermissionMode.READ_ONLY,
    approval_mode: ApprovalMode = ApprovalMode.ASK,
    approval_handler=None,
    environment=None,
    web_search_factory=WebSearchTool,
    **tool_factories,
) -> ProjectSession:
    return ProjectSession.open(
        workspace,
        model="custom/model",
        custom_protocol="openai-compatible",
        custom_base_url="http://127.0.0.1:11434/v1",
        environment={} if environment is None else environment,
        provider_factory=lambda route, *, environment: provider,
        user_profile_path=workspace / "user.json",
        project_profile_path=workspace / "project.json",
        user_hooks_path=workspace / "user-hooks.json",
        project_hooks_path=workspace / ".leonervis-code" / "hooks.json",
        session_store_factory=session_store_factory,
        permission_mode=permission_mode,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        action_uuid_factory=uuid_factory(),
        web_search_factory=web_search_factory,
        **tool_factories,
    )


def hook_store(workspace: Path) -> HookStore:
    return HookStore(
        workspace / "user-hooks.json",
        workspace / ".leonervis-code" / "hooks.json",
    )


def enable_hook(workspace: Path, rule: HookRule) -> None:
    registry = hook_store(workspace)
    registry.add_hook(rule, scope="project")
    registry.set_enabled(rule.hook_id, scope="project", enabled=True, expected_revision=1)


def test_hook_deny_is_model_visible_and_precedes_action_audit(tmp_path: Path) -> None:
    enable_hook(
        tmp_path,
        HookRule(
            "protect-note",
            HookEffect.DENY,
            message="note.txt is protected",
            tool_names=("write_file",),
            path_prefixes=("note.txt",),
        ),
    )
    call = write_call()
    provider = ToolProvider([call, AssistantText("blocked")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("write note") == "blocked"
        assert provider.requests[1].history[-1] == ToolResult(
            "write-1",
            "Hook denied action [protect-note]: note.txt is protected",
            is_error=True,
        )
        assert not (tmp_path / "note.txt").exists()
        assert session.action_audits() == ()
    finally:
        session.close()


def test_hook_require_ask_tightens_auto_but_cannot_override_read_only(tmp_path: Path) -> None:
    enable_hook(
        tmp_path,
        HookRule(
            "ask-writes",
            HookEffect.REQUIRE_ASK,
            message="confirm writes",
            tool_names=("write_file",),
        ),
    )
    approvals = []
    provider = ToolProvider([write_call(), AssistantText("created")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
        approval_handler=lambda request: approvals.append(request) or ApprovalResolution.ACCEPT,
    )
    try:
        assert session.prompt("write") == "created"
        assert len(approvals) == 1
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello\n"
    finally:
        session.close()

    readonly_workspace = tmp_path / "readonly"
    readonly_workspace.mkdir()
    enable_hook(
        readonly_workspace,
        HookRule(
            "ask-writes",
            HookEffect.REQUIRE_ASK,
            message="confirm writes",
            tool_names=("write_file",),
        ),
    )
    denied_approvals = []
    provider = ToolProvider([write_call(tool_use_id="write-2"), AssistantText("denied")])
    session = open_session(
        readonly_workspace,
        provider,
        permission_mode=PermissionMode.READ_ONLY,
        approval_mode=ApprovalMode.AUTO,
        approval_handler=lambda request: denied_approvals.append(request),
    )
    try:
        assert session.prompt("overwrite") == "denied"
        assert denied_approvals == []
        assert provider.requests[1].history[-1].content == (
            "permission denied: denied_read_only_mode"
        )
    finally:
        session.close()


def test_hook_advisory_is_appended_to_normal_model_visible_result(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    enable_hook(
        tmp_path,
        HookRule(
            "read-advice",
            HookEffect.ADVISORY,
            message="Treat this file as generated output.",
            tool_names=("read_file",),
        ),
    )
    call = ToolUse("read-1", "read_file", ToolArguments.from_mapping({"path": "note.txt"}))
    provider = ToolProvider([call, AssistantText("read")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("read note") == "read"
        result = provider.requests[1].history[-1]
        assert result.content == (
            "hello\n\nHook advisory [read-advice]: Treat this file as generated output."
        )
        assert session.action_audits()[0].status is ActionAuditStatus.SUCCEEDED
    finally:
        session.close()


def test_hook_configuration_change_is_frozen_until_next_turn(tmp_path: Path) -> None:
    registry = hook_store(tmp_path)
    registry.add_hook(
        HookRule(
            "future-deny",
            HookEffect.DENY,
            message="later writes are blocked",
            tool_names=("write_file",),
        ),
        scope="project",
    )
    provider = ToolProvider(
        [
            write_call("first.txt", tool_use_id="write-first"),
            AssistantText("first done"),
            write_call("second.txt", tool_use_id="write-second"),
            AssistantText("second blocked"),
        ]
    )

    def approve_and_enable(_request):
        registry.set_enabled("future-deny", scope="project", enabled=True, expected_revision=1)
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve_and_enable,
    )
    try:
        assert session.prompt("first") == "first done"
        assert (tmp_path / "first.txt").exists()
        assert session.prompt("second") == "second blocked"
        assert not (tmp_path / "second.txt").exists()
        assert "Hook denied action [future-deny]" in provider.requests[3].history[-1].content
    finally:
        session.close()


def test_malformed_hook_configuration_fails_turn_preparation_before_provider(
    tmp_path: Path,
) -> None:
    provider = ToolProvider([AssistantText("must not run")])
    session = open_session(tmp_path, provider)
    hook_store(tmp_path).project_path.write_text(
        '{"schema_version":1,"hooks":{},"unexpected":true}',
        encoding="utf-8",
    )
    try:
        with pytest.raises(HookConfigurationError, match="unknown field"):
            session.prompt("do nothing")
        assert provider.requests == []
        assert session.action_audits() == ()
    finally:
        session.close()


class SearchTransport:
    def __init__(self) -> None:
        self.calls = []

    def search(self, **arguments):
        self.calls.append(arguments)
        return SearchHttpResponse(
            200,
            "application/json",
            b'{"results":[{"title":"Python","url":"https://python.org/","content":"Official site"}]}',
        )


def search_call() -> ToolUse:
    return ToolUse(
        "search-1",
        "web_search",
        ToolArguments.from_mapping({"query": "Python official documentation", "max_results": 3}),
    )


class FetchTransport:
    def __init__(self, body: bytes = b"<h1>Docs</h1>") -> None:
        self.body = body
        self.calls: list[str] = []

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int):
        self.calls.append(url)
        return WebHttpResponse(200, "text/html; charset=utf-8", "", self.body, url, 0)


def fetch_call() -> ToolUse:
    return ToolUse(
        "fetch-1",
        "web_fetch",
        ToolArguments.from_mapping({"url": "https://example.com/docs", "format": "markdown"}),
    )


def download_call() -> ToolUse:
    return ToolUse(
        "download-1",
        "download_file",
        ToolArguments.from_mapping({"url": "https://example.com/file.bin", "path": "artifact.bin"}),
    )


def test_web_fetch_requires_network_permission_and_does_not_contact_on_denial(
    tmp_path: Path,
) -> None:
    transport = FetchTransport()
    provider = ToolProvider([fetch_call(), AssistantText("fetch denied")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        web_fetch_factory=lambda: WebFetchTool(transport),
    )
    try:
        assert session.prompt("fetch docs") == "fetch denied"
        assert transport.calls == []
        assert provider.requests[1].history[-1].content == (
            "permission denied: denied_network_access_mode"
        )
        audit = session.action_audits()[0]
        assert audit.identity.action.value == "network-read"
        assert audit.status is ActionAuditStatus.DENIED
    finally:
        session.close()


def test_approved_web_fetch_is_audited_and_returned_to_model(tmp_path: Path) -> None:
    transport = FetchTransport()
    approvals: list[HumanApprovalRequest] = []
    provider = ToolProvider([fetch_call(), AssistantText("fetched")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda request: approvals.append(request) or ApprovalResolution.ACCEPT,
        web_fetch_factory=lambda: WebFetchTool(transport),
    )
    try:
        assert session.prompt("fetch docs") == "fetched"
        assert transport.calls == ["https://example.com/docs"]
        assert approvals[0].preview is not None
        assert approvals[0].preview.kind.value == "web-fetch"
        assert "# Docs" in provider.requests[1].history[-1].content
        audit = session.action_audits()[0]
        assert audit.status is ActionAuditStatus.SUCCEEDED
        assert audit.result_code == "web_fetched"
    finally:
        session.close()


def test_directory_move_and_network_download_use_distinct_permission_classes(
    tmp_path: Path,
) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "item.txt").write_text("x", encoding="utf-8")
    move = ToolUse(
        "move-dir-1",
        "move_directory",
        ToolArguments.from_mapping({"source": "source", "destination": "target"}),
    )
    transport = FetchTransport(b"binary\x00data")
    provider = ToolProvider(
        [move, AssistantText("moved"), download_call(), AssistantText("downloaded")]
    )
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
        move_directory_factory=MoveDirectoryTool,
        download_file_factory=lambda workspace: DownloadFileTool(workspace, transport),
    )
    try:
        assert session.prompt("move") == "moved"
        assert session.prompt("download") == "downloaded"
        assert (tmp_path / "target" / "item.txt").read_text(encoding="utf-8") == "x"
        assert (tmp_path / "artifact.bin").read_bytes() == b"binary\x00data"
        audits = session.action_audits()
        assert [audit.identity.action.value for audit in audits] == [
            "workspace-move",
            "network-write",
        ]
        assert [audit.result_code for audit in audits] == ["directory_moved", "file_created"]
    finally:
        session.close()


def test_default_read_only_denial_is_model_visible_audited_and_committed(tmp_path: Path) -> None:
    call = write_call()
    provider = ToolProvider([call, AssistantText("not written")])
    session = open_session(tmp_path, provider)
    events = []
    try:
        assert session.prompt("write a note", event_sink=events.append) == "not written"

        denied = ToolResult(
            "write-1",
            "permission denied: denied_read_only_mode",
            is_error=True,
        )
        assert provider.requests[1].history[-2:] == (call, denied)
        assert session.history == (
            UserMessage("write a note"),
            call,
            denied,
            AssistantText("not written"),
        )
        assert not (tmp_path / "note.txt").exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.DENIED
        assert audit.execution_outcome is None
        assert (
            ToolRequestFinished(
                "write_file",
                1,
                MAX_TOOL_REQUESTS_PER_TURN,
                ToolEventStatus.DENIED,
                "denied_read_only_mode",
            )
            in events
        )
        assert any(isinstance(event, ToolTurnSummaryCommitted) for event in events)
    finally:
        session.close()


@pytest.mark.parametrize(
    ("permission_mode", "reason"),
    [
        (PermissionMode.READ_ONLY, "denied_network_access_mode"),
        (PermissionMode.WORKSPACE_WRITE, "denied_network_access_mode"),
    ],
)
def test_web_search_requires_danger_full_access_without_contacting_backend(
    tmp_path: Path,
    permission_mode: PermissionMode,
    reason: str,
) -> None:
    transport = SearchTransport()
    call = search_call()
    provider = ToolProvider([call, AssistantText("search denied")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=permission_mode,
        environment={TAVILY_SEARCH_API_KEY_ENV: "secret"},
        web_search_factory=lambda environment: WebSearchTool(environment, transport=transport),
    )
    try:
        session.set_web_search_sources(("tavily",))
        assert session.prompt("search the web") == "search denied"
        assert provider.requests[1].history[-1] == ToolResult(
            "search-1", f"permission denied: {reason}", is_error=True
        )
        assert transport.calls == []
        audit = session.action_audits()[0]
        assert audit.identity.action.value == "network-read"
        assert audit.status is ActionAuditStatus.DENIED
    finally:
        session.close()


def test_approved_web_search_is_exact_audited_and_returned_to_model(tmp_path: Path) -> None:
    transport = SearchTransport()
    call = search_call()
    provider = ToolProvider([call, AssistantText("Python is documented at python.org")])
    approvals: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
        environment={TAVILY_SEARCH_API_KEY_ENV: "secret"},
        web_search_factory=lambda environment: WebSearchTool(environment, transport=transport),
    )
    try:
        session.set_web_search_sources(("tavily",))
        assert session.prompt("find Python docs") == "Python is documented at python.org"
        assert len(approvals) == 1
        assert approvals[0].identity.arguments.as_mapping() == {
            "query": "Python official documentation",
            "max_results": 3,
        }
        assert approvals[0].preview is not None
        assert approvals[0].preview.backend == "tavily"
        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult)
        assert '"backend":"tavily"' in result.content
        assert '"url":"https://python.org/"' in result.content
        assert len(transport.calls) == 1
        assert transport.calls[0]["api_key"] == "secret"
        audit = session.action_audits()[0]
        assert audit.identity.action.value == "network-read"
        assert audit.status is ActionAuditStatus.SUCCEEDED
        assert audit.result_code == "ok"
    finally:
        session.close()


def test_missing_search_credential_rejects_activation_without_action_audit(tmp_path: Path) -> None:
    provider = ToolProvider([])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(WebSearchPreparationError, match="credential environment value"):
            session.set_web_search_sources(("tavily",))
        assert session.history == ()
        assert session.action_audits() == ()
    finally:
        session.close()


def test_multiple_search_credentials_remain_inactive_until_one_is_selected(tmp_path: Path) -> None:
    provider = ToolProvider([])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
        environment={
            BRAVE_SEARCH_API_KEY_ENV: "brave-secret",
            TAVILY_SEARCH_API_KEY_ENV: "tavily-secret",
        },
    )
    try:
        initial = session.inspect_web_search_sources()
        assert initial.ordered_source_names == ()
        assert {source.value for source in initial.available_sources} == {"brave", "tavily"}
        selected = session.set_web_search_sources(("brave",))
        assert selected.ordered_source_names == ("brave",)
        assert session.action_audits() == ()
    finally:
        session.close()


def test_overwriting_agents_keeps_the_current_turn_pinned_and_reloads_next_turn(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("old guidance\n", encoding="utf-8")
    write = write_call("AGENTS.md", "new guidance\n")
    read = ToolUse(
        "read-agents",
        "read_file",
        ToolArguments.from_mapping({"path": "AGENTS.md"}),
    )
    provider = ToolProvider([write, read, AssistantText("updated"), AssistantText("next turn")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("update guidance") == "updated"
        assert session.prompt("continue") == "next turn"

        first, second, third, next_turn = provider.requests
        assert first.project_instructions is second.project_instructions
        assert second.project_instructions is third.project_instructions
        assert first.project_instructions is not None
        assert first.project_instructions.text == "old guidance\n"
        assert next_turn.project_instructions is not None
        assert next_turn.project_instructions.text == "new guidance\n"
        assert third.history[-1] == ToolResult("read-agents", "new guidance\n")
    finally:
        session.close()


def test_list_directory_is_workspace_read_audited_and_committed_without_approval(
    tmp_path: Path,
) -> None:
    (tmp_path / "target" / "empty").mkdir(parents=True)
    call = ToolUse(
        "list-1",
        "list_directory",
        ToolArguments.from_mapping({"path": "target"}),
    )
    provider = ToolProvider([call, AssistantText("listed")])
    approvals = []
    session = open_session(
        tmp_path,
        provider,
        approval_handler=lambda approval: approvals.append(approval),
    )
    try:
        assert session.prompt("inspect target") == "listed"
        result = ToolResult("list-1", '{"path":"target/empty","type":"directory"}\n')
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (
            UserMessage("inspect target"),
            call,
            result,
            AssistantText("listed"),
        )
        assert approvals == []
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "ok"
        assert audit.identity.action.value == "workspace-read"
        assert audit.identity.tool_name == "list_directory"
    finally:
        session.close()


def test_git_observation_tools_are_read_only_audited_and_committed_without_approval(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Leonervis Tests"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    commit_id = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "tracked.txt").write_text("after\n", encoding="utf-8")
    status_call = ToolUse("status-1", "git_status", ToolArguments.from_mapping({}))
    diff_call = ToolUse(
        "diff-1",
        "git_diff",
        ToolArguments.from_mapping({"scope": "unstaged", "path": "tracked.txt"}),
    )
    log_call = ToolUse(
        "log-1",
        "git_log",
        ToolArguments.from_mapping({"limit": 1, "path": "tracked.txt"}),
    )
    show_call = ToolUse(
        "show-1",
        "git_show",
        ToolArguments.from_mapping({"commit_id": commit_id, "path": "tracked.txt"}),
    )
    provider = ToolProvider(
        [
            AssistantToolBatch((status_call, diff_call, log_call, show_call)),
            AssistantText("observed"),
        ]
    )
    approvals = []
    session = open_session(
        tmp_path,
        provider,
        approval_handler=lambda approval: approvals.append(approval),
    )
    try:
        assert session.prompt("inspect Git changes") == "observed"
        continuation = provider.requests[1].history
        assert '"path":"tracked.txt"' in continuation[-4].content
        assert "-before" in continuation[-3].content
        assert "+after" in continuation[-3].content
        assert commit_id in continuation[-2].content
        assert '"message":"initial\\n"' in continuation[-1].content
        assert "+before" in continuation[-1].content
        assert approvals == []
        audits = session.action_audits()
        assert [audit.identity.tool_name for audit in audits] == [
            "git_status",
            "git_diff",
            "git_log",
            "git_show",
        ]
        assert all(audit.identity.action.value == "workspace-read" for audit in audits)
        assert all(audit.status == ActionAuditStatus.SUCCEEDED for audit in audits)
    finally:
        session.close()


def test_hard_rejected_write_returns_tool_error_without_action_audit(
    tmp_path: Path,
) -> None:
    call = write_call(path="nested//note.txt")
    provider = ToolProvider([call, AssistantText("invalid path")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    events = []
    try:
        assert session.prompt("write an invalid path", event_sink=events.append) == "invalid path"

        rejected = ToolResult(
            "write-1",
            "write_file path must be a portable workspace-relative file path",
            is_error=True,
        )
        assert provider.requests[1].history[-2:] == (call, rejected)
        assert session.history == (
            UserMessage("write an invalid path"),
            call,
            rejected,
            AssistantText("invalid path"),
        )
        assert session._writer.state.action_audits == ()
        assert not (tmp_path / "nested").exists()
        assert (
            ToolRequestFinished(
                "write_file",
                1,
                MAX_TOOL_REQUESTS_PER_TURN,
                ToolEventStatus.ERROR,
                "invalid_request",
            )
            in events
        )
        assert any(isinstance(event, ToolTurnSummaryCommitted) for event in events)
    finally:
        session.close()


def test_workspace_write_ask_accept_creates_and_commits_exact_causality(tmp_path: Path) -> None:
    call = write_call(content="approved\n")
    provider = ToolProvider([call, AssistantText("created")])
    approval_requests: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approval_requests.append(request)
        assert not (tmp_path / "note.txt").exists()
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    events = []
    try:
        assert session.prompt("create it", event_sink=events.append) == "created"

        result = ToolResult(
            "write-1",
            '{"bytes_written":9,"operation":"created","path":"note.txt"}\n',
        )
        assert provider.requests[1].history[-2:] == (call, result)
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved\n"
        assert len(approval_requests) == 1
        exact = approval_requests[0].identity
        assert exact.tool_use_id == "write-1"
        assert exact.arguments == call.arguments
        audit = session._writer.state.action_audits[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "created"
        assert (
            ToolRequestFinished(
                "write_file",
                1,
                MAX_TOOL_REQUESTS_PER_TURN,
                ToolEventStatus.SUCCEEDED,
                "created",
            )
            in events
        )
        assert any(isinstance(event, ToolTurnSummaryCommitted) for event in events)
    finally:
        session.close()


@pytest.mark.parametrize(
    ("resolution", "expected_status", "message"),
    [
        (ApprovalResolution.REJECT, ActionAuditStatus.REJECTED, "action approval rejected"),
        (ApprovalResolution.CANCEL, ActionAuditStatus.CANCELLED, "action approval cancelled"),
    ],
)
def test_workspace_write_ask_reject_or_cancel_returns_tool_error_and_commits(
    tmp_path: Path,
    resolution: ApprovalResolution,
    expected_status: ActionAuditStatus,
    message: str,
) -> None:
    provider = ToolProvider([write_call(), AssistantText("stopped")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda _request: resolution,
    )
    events = []
    try:
        assert session.prompt("write", event_sink=events.append) == "stopped"

        result = provider.requests[1].history[-1]
        assert result == ToolResult("write-1", message, is_error=True)
        assert not (tmp_path / "note.txt").exists()
        assert session._writer.state.action_audits[-1].status == expected_status
        expected_event_status = (
            ToolEventStatus.REJECTED
            if resolution == ApprovalResolution.REJECT
            else ToolEventStatus.CANCELLED
        )
        assert (
            ToolRequestFinished(
                "write_file",
                1,
                MAX_TOOL_REQUESTS_PER_TURN,
                expected_event_status,
                "approval_rejected"
                if resolution == ApprovalResolution.REJECT
                else "approval_cancelled",
            )
            in events
        )
        assert any(isinstance(event, ToolTurnSummaryCommitted) for event in events)
    finally:
        session.close()


def test_workspace_write_auto_creates_then_overwrites_using_host_observed_state(
    tmp_path: Path,
) -> None:
    provider = ToolProvider(
        [
            write_call(content="first\n", tool_use_id="write-1"),
            AssistantText("first done"),
            write_call(content="second\n", tool_use_id="write-2"),
            AssistantText("second done"),
        ]
    )
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("first") == "first done"
        assert session.prompt("second") == "second done"

        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "second\n"
        audits = session._writer.state.action_audits
        assert [audit.identity.action.value for audit in audits] == [
            "workspace-create",
            "workspace-overwrite",
        ]
        assert [audit.result_code for audit in audits] == ["created", "overwritten"]
    finally:
        session.close()


def test_accepted_approval_becomes_stale_if_target_changes_while_waiting(tmp_path: Path) -> None:
    provider = ToolProvider([write_call(), AssistantText("must not be reached")])

    def mutate_then_accept(request: HumanApprovalRequest) -> ApprovalResolution:
        assert request.preview is not None
        assert request.preview.action_digest == request.identity.digest
        assert "+hello" in request.preview.body
        (tmp_path / "note.txt").write_text("external\n", encoding="utf-8")
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=mutate_then_accept,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("write")

        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert len(provider.requests) == 1
        assert session.history == ()
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "external\n"
        assert session._writer.state.action_audits[-1].status == ActionAuditStatus.ABANDONED
    finally:
        session.close()


def test_provider_continuation_failure_after_write_preserves_effect_and_audit_without_turn_commit(
    tmp_path: Path,
) -> None:
    provider = ToolProvider([write_call(), RuntimeError("provider continuation failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider continuation failed"):
            session.prompt("write")

        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello\n"
        assert session.history == ()
        audit = session._writer.state.action_audits[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_turn_commit_failure_after_write_preserves_truthful_effect_and_action_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = ToolProvider([write_call(), AssistantText("done")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )

    def fail_commit(*_args, **_kwargs) -> None:
        raise SessionStoreError("injected turn commit failure")

    monkeypatch.setattr(session._writer, "append_turn", fail_commit)
    try:
        with pytest.raises(SessionStoreError, match="injected turn commit failure"):
            session.prompt("write")

        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello\n"
        assert session.history == ()
        assert session._writer.state.action_audits[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def edit_call(
    *,
    old_text: str = "before",
    new_text: str = "after",
    tool_use_id: str = "edit-1",
) -> ToolUse:
    return ToolUse(
        tool_use_id,
        "edit_file",
        ToolArguments.from_mapping(
            {"path": "note.txt", "old_text": old_text, "new_text": new_text}
        ),
    )


def test_model_visible_edit_ask_accept_edits_and_commits_exact_causality(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("before\n", encoding="utf-8")
    call = edit_call()
    provider = ToolProvider([call, AssistantText("edited")])
    approval_requests: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approval_requests.append(request)
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "before\n"
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    try:
        assert session.prompt("change before to after") == "edited"

        result = ToolResult(
            "edit-1",
            '{"bytes_written":6,"operation":"edited","path":"note.txt","replacements":1}\n',
        )
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (
            UserMessage("change before to after"),
            call,
            result,
            AssistantText("edited"),
        )
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "after\n"
        assert len(approval_requests) == 1
        identity = approval_requests[0].identity
        assert identity.tool_name == "edit_file"
        assert identity.arguments == call.arguments
        assert identity.action.value == "workspace-overwrite"
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "edited"
    finally:
        session.close()


def test_model_visible_edit_read_only_denial_is_audited_and_keeps_source(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("before\n", encoding="utf-8")
    call = edit_call()
    provider = ToolProvider([call, AssistantText("not edited")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("edit it") == "not edited"
        denied = ToolResult("edit-1", "permission denied: denied_read_only_mode", is_error=True)
        assert provider.requests[1].history[-2:] == (call, denied)
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "before\n"
        assert session.action_audits()[-1].status == ActionAuditStatus.DENIED
    finally:
        session.close()


def test_model_visible_edit_hard_match_rejection_has_no_action_audit(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("before before\n", encoding="utf-8")
    call = edit_call()
    provider = ToolProvider([call, AssistantText("ambiguous")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("edit one occurrence") == "ambiguous"
        rejected = ToolResult("edit-1", "edit_file old_text matches more than once", is_error=True)
        assert provider.requests[1].history[-2:] == (call, rejected)
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "before before\n"
        assert session.action_audits() == ()
    finally:
        session.close()


def test_model_visible_edit_accepted_approval_rejects_stale_source(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("before\n", encoding="utf-8")
    provider = ToolProvider([edit_call(), AssistantText("must not be reached")])

    def mutate_then_accept(_request: HumanApprovalRequest) -> ApprovalResolution:
        (tmp_path / "note.txt").write_text("external\n", encoding="utf-8")
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=mutate_then_accept,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("edit it")

        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert len(provider.requests) == 1
        assert session.history == ()
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "external\n"
        assert session.action_audits()[-1].status == ActionAuditStatus.ABANDONED
    finally:
        session.close()


def command_call(argv: list[str], *, cwd: str = ".", timeout_seconds: int = 10) -> ToolUse:
    return ToolUse(
        "command-1",
        "run_command",
        ToolArguments.from_mapping({"argv": argv, "cwd": cwd, "timeout_seconds": timeout_seconds}),
    )


def test_model_visible_command_auto_runs_and_commits_exact_causality(tmp_path: Path) -> None:
    import json
    import sys

    call = command_call([sys.executable, "-c", "print('verified')"])
    provider = ToolProvider([call, AssistantText("tests passed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        events: list[object] = []
        assert (
            session.prompt(
                "run verification",
                event_sink=events.append,
                include_tool_details=True,
            )
            == "tests passed"
        )

        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult)
        assert result.tool_use_id == "command-1"
        assert not result.is_error
        assert json.loads(result.content)["stdout"]["text"] == "verified\n"
        assert session.history == (
            UserMessage("run verification"),
            call,
            result,
            AssistantText("tests passed"),
        )
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "command_succeeded"
        assert audit.identity.action.value == "dangerous"
        started = next(event for event in events if isinstance(event, ToolRequestStarted))
        assert started.safe_details[0].startswith("argv: [")
        assert str(sys.executable) in started.safe_details[0]
        assert started.safe_details[1:] == (
            "cwd: '.'",
            "timeout_seconds: 10",
            "execution: direct argv; Host shell parsing disabled",
        )
        finished = next(event for event in events if isinstance(event, ToolRequestFinished))
        assert finished.status == ToolEventStatus.SUCCEEDED
        assert finished.result_details is not None
        assert finished.result_details.compact_summary.startswith("exit=0 duration=")
        assert "stdout=9B stderr=0B" in finished.result_details.compact_summary
        assert finished.result_details.full_details[0:2] == (
            "status: exited",
            "exit_code: 0",
        )
        assert "verified" not in repr(finished.result_details)
    finally:
        session.close()


def test_model_visible_command_workspace_write_denial_never_spawns(tmp_path: Path) -> None:
    import sys

    marker = tmp_path / "must-not-exist.txt"
    call = command_call(
        [sys.executable, "-c", "from pathlib import Path; Path('must-not-exist.txt').touch()"]
    )
    provider = ToolProvider([call, AssistantText("denied")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        events: list[object] = []
        assert session.prompt("run command", event_sink=events.append) == "denied"
        denied = ToolResult(
            "command-1",
            "permission denied: denied_workspace_write_mode",
            is_error=True,
        )
        assert provider.requests[1].history[-2:] == (call, denied)
        assert not marker.exists()
        assert session.action_audits()[-1].status == ActionAuditStatus.DENIED
        started = next(event for event in events if isinstance(event, ToolRequestStarted))
        assert started.safe_details == ()
        finished = next(event for event in events if isinstance(event, ToolRequestFinished))
        assert finished.status == ToolEventStatus.DENIED
        assert finished.result_details is None
    finally:
        session.close()


def test_model_visible_command_ask_accept_binds_exact_request(tmp_path: Path) -> None:
    import sys

    call = command_call([sys.executable, "-c", "print('approved')"], timeout_seconds=7)
    provider = ToolProvider([call, AssistantText("done")])
    approvals: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    try:
        assert session.prompt("run approved command") == "done"
        assert len(approvals) == 1
        assert approvals[0].identity.tool_name == "run_command"
        assert approvals[0].identity.arguments == call.arguments
        assert session.action_audits()[-1].approval_outcome.value == "accepted"
    finally:
        session.close()


def test_model_visible_command_approval_rejection_has_no_execution_metadata(
    tmp_path: Path,
) -> None:
    import sys

    call = command_call([sys.executable, "-c", "print('must not run')"])
    provider = ToolProvider([call, AssistantText("rejected")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda _request: ApprovalResolution.REJECT,
    )
    try:
        events: list[object] = []
        assert session.prompt("run command", event_sink=events.append) == "rejected"

        finished = next(event for event in events if isinstance(event, ToolRequestFinished))
        assert finished.status == ToolEventStatus.REJECTED
        assert finished.result_code == "approval_rejected"
        assert finished.result_details is None
    finally:
        session.close()


def test_model_visible_command_final_audit_failure_emits_only_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sys

    call = command_call([sys.executable, "-c", "print('process completed')"])
    provider = ToolProvider([call, AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
    )
    original_append_audit = session._writer.append_audit

    def fail_final_audit(record):
        if isinstance(record, ActionExecutionFinished):
            raise SessionStoreError("injected final audit failure")
        return original_append_audit(record)

    monkeypatch.setattr(session._writer, "append_audit", fail_final_audit)
    try:
        events: list[object] = []
        with pytest.raises(ActionOutcomeAuditError):
            session.prompt("run command", event_sink=events.append)

        finished = [event for event in events if isinstance(event, ToolRequestFinished)]
        assert len(finished) == 1
        assert finished[0].status == ToolEventStatus.OUTCOME_UNKNOWN
        assert finished[0].result_code is None
        assert finished[0].result_details is None
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.OUTCOME_UNKNOWN
        assert len(provider.requests) == 1
    finally:
        session.close()


def mkdir_call(path: str = "src", *, tool_use_id: str = "mkdir-1") -> ToolUse:
    return ToolUse(
        tool_use_id,
        "mkdir",
        ToolArguments.from_mapping({"path": path}),
    )


def test_model_visible_mkdir_read_only_denial_is_audited_and_does_not_create(
    tmp_path: Path,
) -> None:
    call = mkdir_call()
    provider = ToolProvider([call, AssistantText("denied")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("create src") == "denied"

        denied = ToolResult(
            "mkdir-1",
            "permission denied: denied_read_only_mode",
            is_error=True,
        )
        assert provider.requests[1].history[-2:] == (call, denied)
        assert not (tmp_path / "src").exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.DENIED
        assert audit.identity.action.value == "workspace-create"
    finally:
        session.close()


def test_model_visible_mkdir_auto_creates_and_commits_exact_causality(tmp_path: Path) -> None:
    call = mkdir_call("src")
    provider = ToolProvider([call, AssistantText("created")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("create src") == "created"

        result = ToolResult("mkdir-1", '{"operation":"created","path":"src"}\n')
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (
            UserMessage("create src"),
            call,
            result,
            AssistantText("created"),
        )
        assert (tmp_path / "src").is_dir()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "directory_created"
    finally:
        session.close()


def test_model_visible_mkdir_ask_accept_binds_exact_request(tmp_path: Path) -> None:
    call = mkdir_call("src/pkg")
    (tmp_path / "src").mkdir()
    provider = ToolProvider([call, AssistantText("created")])
    approvals: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        assert not (tmp_path / "src" / "pkg").exists()
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    try:
        assert session.prompt("create package directory") == "created"
        assert (tmp_path / "src" / "pkg").is_dir()
        assert len(approvals) == 1
        assert approvals[0].identity.tool_name == "mkdir"
        assert approvals[0].identity.arguments == call.arguments
        assert session.action_audits()[-1].approval_outcome.value == "accepted"
    finally:
        session.close()


@pytest.mark.parametrize(
    ("resolution", "expected_status", "message"),
    [
        (ApprovalResolution.REJECT, ActionAuditStatus.REJECTED, "action approval rejected"),
        (ApprovalResolution.CANCEL, ActionAuditStatus.CANCELLED, "action approval cancelled"),
    ],
)
def test_model_visible_mkdir_reject_or_cancel_never_creates(
    tmp_path: Path,
    resolution: ApprovalResolution,
    expected_status: ActionAuditStatus,
    message: str,
) -> None:
    provider = ToolProvider([mkdir_call(), AssistantText("stopped")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda _request: resolution,
    )
    try:
        assert session.prompt("create src") == "stopped"
        assert provider.requests[1].history[-1] == ToolResult("mkdir-1", message, is_error=True)
        assert not (tmp_path / "src").exists()
        assert session.action_audits()[-1].status == expected_status
    finally:
        session.close()


def test_model_visible_mkdir_hard_rejection_has_no_action_audit(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    call = mkdir_call("src")
    provider = ToolProvider([call, AssistantText("already exists")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("create src") == "already exists"
        assert provider.requests[1].history[-1] == ToolResult(
            "mkdir-1", "mkdir target already exists", is_error=True
        )
        assert session.action_audits() == ()
    finally:
        session.close()


def test_model_visible_mkdir_accepted_approval_rejects_stale_target(tmp_path: Path) -> None:
    provider = ToolProvider([mkdir_call(), AssistantText("must not be reached")])

    def mutate_then_accept(_request: HumanApprovalRequest) -> ApprovalResolution:
        (tmp_path / "src").mkdir()
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=mutate_then_accept,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("create src")

        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert len(provider.requests) == 1
        assert session.history == ()
        assert (tmp_path / "src").is_dir()
        assert session.action_audits()[-1].status == ActionAuditStatus.ABANDONED
    finally:
        session.close()


def test_provider_continuation_failure_after_mkdir_preserves_effect_and_audit(
    tmp_path: Path,
) -> None:
    provider = ToolProvider([mkdir_call(), RuntimeError("provider continuation failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider continuation failed"):
            session.prompt("create src")

        assert (tmp_path / "src").is_dir()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_turn_commit_failure_after_mkdir_preserves_effect_and_action_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = ToolProvider([mkdir_call(), AssistantText("done")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )

    def fail_commit(*_args, **_kwargs) -> None:
        raise SessionStoreError("injected turn commit failure")

    monkeypatch.setattr(session._writer, "append_turn", fail_commit)
    try:
        with pytest.raises(SessionStoreError, match="injected turn commit failure"):
            session.prompt("create src")

        assert (tmp_path / "src").is_dir()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_model_visible_mkdir_partial_durability_is_audited_truthfully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = ToolProvider([mkdir_call(), AssistantText("inspect before retry")])

    def fail_fsync(_directory: Path) -> None:
        raise OSError("injected")

    monkeypatch.setattr("leonervis_code.tools.mkdir._fsync_directory", fail_fsync)
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("create src") == "inspect before retry"
        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "do not retry automatically" in result.content
        assert (tmp_path / "src").is_dir()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.PARTIAL
        assert audit.execution_outcome == ActionExecutionOutcome.PARTIAL
        assert audit.result_code == "directory_created_durability_unknown"
    finally:
        session.close()


def test_mkdir_execution_start_audit_failure_prevents_directory_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = ToolProvider([mkdir_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )

    def fail_start(*_args, **_kwargs) -> None:
        raise SessionStoreError("injected action_execution_started failure")

    monkeypatch.setattr(session._writer, "action_execution_started", fail_start)
    try:
        with pytest.raises(SessionStoreError, match="action_execution_started"):
            session.prompt("create src")

        assert not (tmp_path / "src").exists()
        assert session.history == ()
        assert len(provider.requests) == 1
    finally:
        session.close()


def move_call(
    source: str = "source.txt",
    destination: str = "destination.txt",
    *,
    tool_use_id: str = "move-1",
) -> ToolUse:
    return ToolUse(
        tool_use_id,
        "move_file",
        ToolArguments.from_mapping({"source": source, "destination": destination}),
    )


def test_model_visible_move_read_only_denial_is_audited_and_keeps_source(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source\n", encoding="utf-8")
    call = move_call()
    provider = ToolProvider([call, AssistantText("denied")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("move it") == "denied"
        denied = ToolResult("move-1", "permission denied: denied_read_only_mode", is_error=True)
        assert provider.requests[1].history[-2:] == (call, denied)
        assert (tmp_path / "source.txt").exists()
        assert not (tmp_path / "destination.txt").exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.DENIED
        assert audit.identity.action.value == "workspace-move"
    finally:
        session.close()


def test_model_visible_move_auto_succeeds_and_commits_exact_causality(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source\n", encoding="utf-8")
    call = move_call()
    provider = ToolProvider([call, AssistantText("moved")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("move it") == "moved"
        result = ToolResult(
            "move-1",
            '{"destination":"destination.txt","operation":"moved","source":"source.txt"}\n',
        )
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (UserMessage("move it"), call, result, AssistantText("moved"))
        assert not (tmp_path / "source.txt").exists()
        assert (tmp_path / "destination.txt").read_text(encoding="utf-8") == "source\n"
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "file_moved"
    finally:
        session.close()


def test_model_visible_move_ask_accept_binds_both_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "src/a.py").write_text("x\n", encoding="utf-8")
    call = move_call("src/a.py", "dst/b.py")
    provider = ToolProvider([call, AssistantText("moved")])
    approvals: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        assert (tmp_path / "src/a.py").exists()
        assert not (tmp_path / "dst/b.py").exists()
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    try:
        assert session.prompt("move source") == "moved"
        assert len(approvals) == 1
        assert approvals[0].identity.tool_name == "move_file"
        assert approvals[0].identity.arguments == call.arguments
        assert session.action_audits()[-1].approval_outcome.value == "accepted"
    finally:
        session.close()


@pytest.mark.parametrize(
    ("resolution", "expected_status", "message"),
    [
        (ApprovalResolution.REJECT, ActionAuditStatus.REJECTED, "action approval rejected"),
        (ApprovalResolution.CANCEL, ActionAuditStatus.CANCELLED, "action approval cancelled"),
    ],
)
def test_model_visible_move_reject_or_cancel_has_no_filesystem_effect(
    tmp_path: Path,
    resolution: ApprovalResolution,
    expected_status: ActionAuditStatus,
    message: str,
) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = ToolProvider([move_call(), AssistantText("stopped")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda _request: resolution,
    )
    try:
        assert session.prompt("move it") == "stopped"
        assert provider.requests[1].history[-1] == ToolResult("move-1", message, is_error=True)
        assert (tmp_path / "source.txt").exists()
        assert not (tmp_path / "destination.txt").exists()
        assert session.action_audits()[-1].status == expected_status
    finally:
        session.close()


def test_model_visible_move_hard_rejection_has_no_action_audit(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    (tmp_path / "destination.txt").write_text("existing", encoding="utf-8")
    provider = ToolProvider([move_call(), AssistantText("unchanged")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("move it") == "unchanged"
        assert provider.requests[1].history[-1] == ToolResult(
            "move-1", "move_file destination already exists", is_error=True
        )
        assert session.action_audits() == ()
        assert (tmp_path / "destination.txt").read_text(encoding="utf-8") == "existing"
    finally:
        session.close()


@pytest.mark.parametrize("stale_part", ["source", "destination"])
def test_model_visible_move_accepted_approval_rejects_stale_state(
    tmp_path: Path, stale_part: str
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    provider = ToolProvider([move_call(), AssistantText("must not be reached")])

    def mutate_then_accept(_request: HumanApprovalRequest) -> ApprovalResolution:
        if stale_part == "source":
            source.write_text("changed", encoding="utf-8")
        else:
            (tmp_path / "destination.txt").write_text("external", encoding="utf-8")
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=mutate_then_accept,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("move it")
        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert len(provider.requests) == 1
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.ABANDONED
    finally:
        session.close()


def test_provider_continuation_failure_after_move_preserves_effect_and_audit(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = ToolProvider([move_call(), RuntimeError("provider continuation failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider continuation failed"):
            session.prompt("move it")
        assert not (tmp_path / "source.txt").exists()
        assert (tmp_path / "destination.txt").exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_turn_commit_failure_after_move_preserves_effect_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = ToolProvider([move_call(), AssistantText("moved")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "append_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected turn commit failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="injected turn commit failure"):
            session.prompt("move it")
        assert not (tmp_path / "source.txt").exists()
        assert (tmp_path / "destination.txt").exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
    finally:
        session.close()


def test_model_visible_move_partial_is_audited_truthfully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = ToolProvider([move_call(), AssistantText("inspect both paths")])
    monkeypatch.setattr(
        "leonervis_code.tools.move_file.os.unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")),
    )
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("move it") == "inspect both paths"
        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult) and result.is_error
        assert "do not retry automatically" in result.content
        assert (tmp_path / "source.txt").exists()
        assert (tmp_path / "destination.txt").exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.PARTIAL
        assert audit.execution_outcome == ActionExecutionOutcome.PARTIAL
        assert audit.result_code == "destination_linked_source_retained"
    finally:
        session.close()


def test_move_execution_start_audit_failure_prevents_destination_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = ToolProvider([move_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "action_execution_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected action_execution_started failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="action_execution_started"):
            session.prompt("move it")
        assert (tmp_path / "source.txt").exists()
        assert not (tmp_path / "destination.txt").exists()
        assert session.history == ()
        assert len(provider.requests) == 1
    finally:
        session.close()


def copy_call(
    source: str = "source.bin",
    destination: str = "destination.bin",
    *,
    tool_use_id: str = "copy-1",
) -> ToolUse:
    return ToolUse(
        tool_use_id,
        "copy_file",
        ToolArguments.from_mapping({"source": source, "destination": destination}),
    )


def test_model_visible_copy_read_only_denial_is_audited_and_has_no_effect(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    call = copy_call()
    provider = ToolProvider([call, AssistantText("denied")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("copy it") == "denied"
        denied = ToolResult("copy-1", "permission denied: denied_read_only_mode", is_error=True)
        assert provider.requests[1].history[-2:] == (call, denied)
        assert (tmp_path / "source.bin").read_bytes() == b"source"
        assert not (tmp_path / "destination.bin").exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.DENIED
        assert audit.identity.action.value == "workspace-create"
    finally:
        session.close()


def test_model_visible_copy_auto_succeeds_and_commits_exact_causality(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    call = copy_call()
    provider = ToolProvider([call, AssistantText("copied")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("copy it") == "copied"
        result = ToolResult(
            "copy-1",
            '{"bytes_copied":6,"destination":"destination.bin",'
            '"operation":"copied","source":"source.bin"}\n',
        )
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (UserMessage("copy it"), call, result, AssistantText("copied"))
        assert (tmp_path / "source.bin").read_bytes() == b"source"
        assert (tmp_path / "destination.bin").read_bytes() == b"source"
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "file_copied"
    finally:
        session.close()


def test_model_visible_copy_ask_accept_binds_both_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "src/a.bin").write_bytes(b"x")
    call = copy_call("src/a.bin", "dst/b.bin")
    provider = ToolProvider([call, AssistantText("copied")])
    approvals: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        assert (tmp_path / "src/a.bin").exists()
        assert not (tmp_path / "dst/b.bin").exists()
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    try:
        assert session.prompt("copy source") == "copied"
        assert len(approvals) == 1
        assert approvals[0].identity.tool_name == "copy_file"
        assert approvals[0].identity.arguments == call.arguments
        assert session.action_audits()[-1].approval_outcome.value == "accepted"
    finally:
        session.close()


@pytest.mark.parametrize("stale_part", ["source", "destination"])
def test_model_visible_copy_accepted_approval_rejects_stale_state(
    tmp_path: Path, stale_part: str
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    provider = ToolProvider([copy_call(), AssistantText("must not be reached")])

    def mutate_then_accept(_request: HumanApprovalRequest) -> ApprovalResolution:
        if stale_part == "source":
            source.write_bytes(b"changed")
        else:
            (tmp_path / "destination.bin").write_bytes(b"external")
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=mutate_then_accept,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("copy it")
        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert len(provider.requests) == 1
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.ABANDONED
    finally:
        session.close()


def test_model_visible_copy_hard_rejection_has_no_action_audit(tmp_path: Path) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    (tmp_path / "destination.bin").write_bytes(b"existing")
    provider = ToolProvider([copy_call(), AssistantText("unchanged")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("copy it") == "unchanged"
        assert provider.requests[1].history[-1] == ToolResult(
            "copy-1", "copy_file destination already exists", is_error=True
        )
        assert session.action_audits() == ()
        assert (tmp_path / "destination.bin").read_bytes() == b"existing"
    finally:
        session.close()


def test_provider_continuation_failure_after_copy_preserves_effect_and_audit(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    provider = ToolProvider([copy_call(), RuntimeError("provider continuation failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider continuation failed"):
            session.prompt("copy it")
        assert (tmp_path / "source.bin").exists()
        assert (tmp_path / "destination.bin").exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_turn_commit_failure_after_copy_preserves_effect_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    provider = ToolProvider([copy_call(), AssistantText("copied")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "append_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected turn commit failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="injected turn commit failure"):
            session.prompt("copy it")
        assert (tmp_path / "source.bin").exists()
        assert (tmp_path / "destination.bin").exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
    finally:
        session.close()


def test_model_visible_copy_partial_is_audited_truthfully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    provider = ToolProvider([copy_call(), AssistantText("inspect destination")])
    real_fsync = __import__("os").fsync
    calls = 0

    def fail_second(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real_fsync(fd)

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr("leonervis_code.tools.copy_file._fsync", fail_second)
    try:
        assert session.prompt("copy it") == "inspect destination"
        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult) and result.is_error
        assert "do not retry automatically" in result.content
        assert (tmp_path / "source.bin").exists()
        assert (tmp_path / "destination.bin").exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.PARTIAL
        assert audit.execution_outcome == ActionExecutionOutcome.PARTIAL
        assert audit.result_code == "file_copied_durability_unknown"
    finally:
        session.close()


def test_copy_execution_start_audit_failure_prevents_destination_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "source.bin").write_bytes(b"source")
    provider = ToolProvider([copy_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "action_execution_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected action_execution_started failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="action_execution_started"):
            session.prompt("copy it")
        assert not (tmp_path / "destination.bin").exists()
        assert session.history == ()
        assert len(provider.requests) == 1
    finally:
        session.close()


def delete_call(path: str = "obsolete.txt", *, tool_use_id: str = "delete-1") -> ToolUse:
    return ToolUse(tool_use_id, "delete_file", ToolArguments.from_mapping({"path": path}))


def test_model_visible_delete_read_only_denial_is_audited_and_keeps_file(tmp_path: Path) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("keep\n", encoding="utf-8")
    call = delete_call()
    provider = ToolProvider([call, AssistantText("denied")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("delete it") == "denied"
        denied = ToolResult("delete-1", "permission denied: denied_read_only_mode", is_error=True)
        assert provider.requests[1].history[-2:] == (call, denied)
        assert target.exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.DENIED
        assert audit.identity.action.value == "workspace-delete"
    finally:
        session.close()


def test_model_visible_delete_auto_succeeds_and_commits_exact_causality(tmp_path: Path) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("remove\n", encoding="utf-8")
    call = delete_call()
    provider = ToolProvider([call, AssistantText("deleted")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("delete it") == "deleted"
        result = ToolResult("delete-1", '{"operation":"deleted","path":"obsolete.txt"}\n')
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (UserMessage("delete it"), call, result, AssistantText("deleted"))
        assert not target.exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "file_deleted"
    finally:
        session.close()


def test_model_visible_delete_ask_reject_keeps_file(tmp_path: Path) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("keep\n", encoding="utf-8")
    approvals: list[HumanApprovalRequest] = []

    def reject(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        return ApprovalResolution.REJECT

    call = delete_call()
    provider = ToolProvider([call, AssistantText("rejected")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=reject,
    )
    try:
        assert session.prompt("delete it") == "rejected"
        assert approvals[0].identity.arguments.as_mapping() == {"path": "obsolete.txt"}
        assert target.exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.REJECTED
    finally:
        session.close()


def test_hard_rejected_delete_has_no_action_audit(tmp_path: Path) -> None:
    call = delete_call("missing.txt")
    provider = ToolProvider([call, AssistantText("missing")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("delete missing") == "missing"
        assert provider.requests[1].history[-1] == ToolResult(
            "delete-1", "delete_file target does not exist", is_error=True
        )
        assert session.action_audits() == ()
    finally:
        session.close()


def test_delete_approval_wait_target_change_fails_stale_and_keeps_replacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("old\n", encoding="utf-8")

    def replace_target(_request: HumanApprovalRequest) -> ApprovalResolution:
        target.unlink()
        target.write_text("replacement\n", encoding="utf-8")
        return ApprovalResolution.ACCEPT

    call = delete_call()
    provider = ToolProvider([call, AssistantText("stale")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=replace_target,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("delete it")
        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert target.read_text(encoding="utf-8") == "replacement\n"
        assert session.history == ()
    finally:
        session.close()


def test_delete_partial_durability_is_audited_and_not_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("remove\n", encoding="utf-8")
    provider = ToolProvider([delete_call(), AssistantText("inspect before retry")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        "leonervis_code.tools.delete_file._fsync_directory",
        lambda _fd: (_ for _ in ()).throw(OSError("injected")),
    )
    try:
        assert session.prompt("delete it") == "inspect before retry"
        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult) and result.is_error
        assert "do not retry automatically" in result.content
        assert not target.exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.PARTIAL
        assert audit.result_code == "file_deleted_durability_unknown"
    finally:
        session.close()


def test_provider_continuation_failure_after_delete_preserves_effect_and_audit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("remove\n", encoding="utf-8")
    provider = ToolProvider([delete_call(), RuntimeError("provider continuation failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider continuation failed"):
            session.prompt("delete it")
        assert not target.exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_turn_commit_failure_after_delete_preserves_effect_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("remove\n", encoding="utf-8")
    provider = ToolProvider([delete_call(), AssistantText("deleted")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "append_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected turn commit failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="injected turn commit failure"):
            session.prompt("delete it")
        assert not target.exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
        assert session._writer.state.records[-1].record_type == "turn_failed"
    finally:
        session.close()


def test_delete_execution_start_audit_failure_prevents_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("keep\n", encoding="utf-8")
    provider = ToolProvider([delete_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "action_execution_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected action_execution_started failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="action_execution_started"):
            session.prompt("delete it")
        assert target.read_text(encoding="utf-8") == "keep\n"
        assert session.history == ()
        assert len(provider.requests) == 1
    finally:
        session.close()


def test_delete_final_audit_failure_reports_known_effect_with_unknown_audit_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "obsolete.txt"
    target.write_text("remove\n", encoding="utf-8")
    provider = ToolProvider([delete_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    original_append_audit = session._writer.append_audit

    def fail_final_audit(record):
        if isinstance(record, ActionExecutionFinished):
            raise SessionStoreError("injected final audit failure")
        return original_append_audit(record)

    monkeypatch.setattr(session._writer, "append_audit", fail_final_audit)
    try:
        with pytest.raises(ActionOutcomeAuditError) as captured:
            session.prompt("delete it")
        assert not target.exists()
        assert captured.value.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert captured.value.result_code == "file_deleted"
        assert session.history == ()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.OUTCOME_UNKNOWN
        assert audit.execution_outcome is None
        assert audit.result_code is None
        assert len(provider.requests) == 1
    finally:
        session.close()


def delete_directory_call(path: str = "empty-dir", *, tool_use_id: str = "rmdir-1") -> ToolUse:
    return ToolUse(
        tool_use_id,
        "delete_directory",
        ToolArguments.from_mapping({"path": path}),
    )


def test_model_visible_delete_directory_read_only_denial_is_audited(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    call = delete_directory_call()
    provider = ToolProvider([call, AssistantText("denied")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("remove empty directory") == "denied"
        denied = ToolResult("rmdir-1", "permission denied: denied_read_only_mode", is_error=True)
        assert provider.requests[1].history[-2:] == (call, denied)
        assert target.is_dir()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.DENIED
        assert audit.identity.action.value == "workspace-delete"
        assert audit.identity.tool_name == "delete_directory"
    finally:
        session.close()


def test_model_visible_delete_directory_auto_succeeds_with_exact_causality(
    tmp_path: Path,
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    call = delete_directory_call()
    provider = ToolProvider([call, AssistantText("deleted")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("remove empty directory") == "deleted"
        result = ToolResult("rmdir-1", '{"operation":"deleted","path":"empty-dir"}\n')
        assert provider.requests[1].history[-2:] == (call, result)
        assert session.history == (
            UserMessage("remove empty directory"),
            call,
            result,
            AssistantText("deleted"),
        )
        assert not target.exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert audit.result_code == "directory_deleted"
    finally:
        session.close()


def test_delete_directory_ask_reject_keeps_directory(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    approvals: list[HumanApprovalRequest] = []

    def reject(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        return ApprovalResolution.REJECT

    provider = ToolProvider([delete_directory_call(), AssistantText("rejected")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=reject,
    )
    try:
        assert session.prompt("remove empty directory") == "rejected"
        assert approvals[0].identity.arguments.as_mapping() == {"path": "empty-dir"}
        assert target.is_dir()
        assert session.action_audits()[-1].status == ActionAuditStatus.REJECTED
    finally:
        session.close()


def test_hard_rejected_non_empty_directory_has_no_action_audit(tmp_path: Path) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    (target / "child.txt").write_text("keep", encoding="utf-8")
    provider = ToolProvider([delete_directory_call(), AssistantText("not empty")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("remove directory") == "not empty"
        assert provider.requests[1].history[-1] == ToolResult(
            "rmdir-1", "delete_directory target must be empty", is_error=True
        )
        assert target.is_dir()
        assert session.action_audits() == ()
    finally:
        session.close()


def test_delete_directory_child_created_during_approval_fails_stale_and_keeps_tree(
    tmp_path: Path,
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()

    def create_child(_request: HumanApprovalRequest) -> ApprovalResolution:
        (target / "child.txt").write_text("keep", encoding="utf-8")
        return ApprovalResolution.ACCEPT

    provider = ToolProvider([delete_directory_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=create_child,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("remove directory")
        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert (target / "child.txt").read_text(encoding="utf-8") == "keep"
        assert session.history == ()
    finally:
        session.close()


def test_delete_directory_execution_start_failure_prevents_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    provider = ToolProvider([delete_directory_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "action_execution_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected action_execution_started failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="action_execution_started"):
            session.prompt("remove directory")
        assert target.is_dir()
        assert session.history == ()
        assert len(provider.requests) == 1
    finally:
        session.close()


def test_delete_directory_partial_durability_is_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    provider = ToolProvider([delete_directory_call(), AssistantText("inspect before retry")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        "leonervis_code.tools.delete_directory._fsync_directory",
        lambda _fd: (_ for _ in ()).throw(OSError("injected")),
    )
    try:
        assert session.prompt("remove directory") == "inspect before retry"
        result = provider.requests[1].history[-1]
        assert isinstance(result, ToolResult) and result.is_error
        assert "do not retry automatically" in result.content
        assert not target.exists()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.PARTIAL
        assert audit.result_code == "directory_deleted_durability_unknown"
    finally:
        session.close()


def test_provider_failure_after_delete_directory_preserves_effect_and_audit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    provider = ToolProvider([delete_directory_call(), RuntimeError("provider failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            session.prompt("remove directory")
        assert not target.exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
    finally:
        session.close()


def test_turn_commit_failure_after_delete_directory_preserves_effect_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    provider = ToolProvider([delete_directory_call(), AssistantText("deleted")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    monkeypatch.setattr(
        session._writer,
        "append_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionStoreError("injected turn commit failure")
        ),
    )
    try:
        with pytest.raises(SessionStoreError, match="injected turn commit failure"):
            session.prompt("remove directory")
        assert not target.exists()
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
    finally:
        session.close()


def test_delete_directory_final_audit_failure_reports_known_effect_with_unknown_audit_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty-dir"
    target.mkdir()
    provider = ToolProvider([delete_directory_call(), AssistantText("must not be reached")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    original_append_audit = session._writer.append_audit

    def fail_final_audit(record):
        if isinstance(record, ActionExecutionFinished):
            raise SessionStoreError("injected final audit failure")
        return original_append_audit(record)

    monkeypatch.setattr(session._writer, "append_audit", fail_final_audit)
    try:
        with pytest.raises(ActionOutcomeAuditError) as captured:
            session.prompt("remove directory")
        assert not target.exists()
        assert captured.value.execution_outcome == ActionExecutionOutcome.SUCCEEDED
        assert captured.value.result_code == "directory_deleted"
        assert session.history == ()
        audit = session.action_audits()[-1]
        assert audit.status == ActionAuditStatus.OUTCOME_UNKNOWN
        assert audit.execution_outcome is None
        assert audit.result_code is None
        assert len(provider.requests) == 1
    finally:
        session.close()


def test_new_read_tools_execute_as_workspace_read_and_share_durable_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(grep_regex_module, "GREP_REGEX_TIMEOUT_SECONDS", 5.0)
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("one\ntask_42 = True\nthree\n", encoding="utf-8")
    calls = [
        ToolUse(
            "lines-1",
            "read_file_lines",
            ToolArguments.from_mapping({"path": "src/app.py", "start_line": 2, "line_count": 1}),
        ),
        ToolUse(
            "stat-1",
            "stat_path",
            ToolArguments.from_mapping({"path": "src/app.py"}),
        ),
        ToolUse(
            "tree-1",
            "list_tree",
            ToolArguments.from_mapping({"path": "src", "max_depth": 1}),
        ),
        ToolUse(
            "regex-1",
            "grep_regex",
            ToolArguments.from_mapping({"pattern": r"task_\d+", "include": "src/*.py"}),
        ),
    ]
    responses = []
    for call in calls:
        responses.extend((call, AssistantText(f"finished {call.name}")))
    provider = ToolProvider(responses)
    approvals: list[HumanApprovalRequest] = []
    session = open_session(
        tmp_path,
        provider,
        approval_handler=lambda request: approvals.append(request),
    )
    try:
        for call in calls:
            assert session.prompt(call.name) == f"finished {call.name}"

        assert approvals == []
        tool_results = [provider.requests[index].history[-1] for index in (1, 3, 5, 7)]
        assert '"line":2,"text":"task_42 = True"' in tool_results[0].content
        assert '"path":"src/app.py"' in tool_results[1].content
        assert '"type":"file"' in tool_results[1].content
        assert tool_results[2].content == ('{"depth":1,"path":"src/app.py","type":"file"}\n')
        assert '"path":"src/app.py","line":2,"text":"task_42 = True"' in (tool_results[3].content)
        audits = session.action_audits()
        assert [audit.identity.tool_name for audit in audits] == [
            "read_file_lines",
            "stat_path",
            "list_tree",
            "grep_regex",
        ]
        assert all(audit.identity.action.value == "workspace-read" for audit in audits)
        assert all(audit.status == ActionAuditStatus.SUCCEEDED for audit in audits)
    finally:
        session.close()


def test_new_tools_share_total_tool_request_budget(tmp_path: Path) -> None:
    calls = [
        ToolUse(
            f"stat-{index}",
            "stat_path",
            ToolArguments.from_mapping({"path": "."}),
        )
        for index in range(1, MAX_TOOL_REQUESTS_PER_TURN + 2)
    ]
    batches = [
        AssistantToolBatch(tuple(calls[start : start + 8]))
        for start in range(0, MAX_TOOL_REQUESTS_PER_TURN, 8)
    ]
    provider = ToolProvider([*batches, calls[-1], AssistantText("stopped")])
    session = open_session(tmp_path, provider)
    try:
        assert session.prompt("inspect repeatedly") == "stopped"

        assert len(session.action_audits()) == MAX_TOOL_REQUESTS_PER_TURN
        final_result = provider.requests[-1].history[-1]
        assert isinstance(final_result, ToolResult)
        assert final_result.tool_use_id == f"stat-{MAX_TOOL_REQUESTS_PER_TURN + 1}"
        assert final_result.is_error is True
        assert "requested=33 admitted=32 dispatched=32" in final_result.content
        assert "rejected_over_budget=1 unused_admission_slots=0" in final_result.content
        assert "tool_requests_closed=true" in final_result.content
    finally:
        session.close()


def patch_call(*, tool_use_id: str = "patch-1") -> ToolUse:
    return ToolUse(
        tool_use_id,
        "patch_file",
        ToolArguments.from_mapping(
            {
                "path": "note.txt",
                "edits": [
                    {"old_text": "alpha", "new_text": "A"},
                    {"old_text": "gamma", "new_text": "G"},
                ],
            }
        ),
    )


def test_model_visible_patch_ask_accept_is_atomic_audited_and_redacted(
    tmp_path: Path,
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")
    call = patch_call()
    provider = ToolProvider([call, AssistantText("patched")])
    approvals: list[HumanApprovalRequest] = []

    def approve(request: HumanApprovalRequest) -> ApprovalResolution:
        approvals.append(request)
        assert target.read_text(encoding="utf-8") == "alpha beta gamma\n"
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=approve,
    )
    try:
        assert session.prompt("patch both anchors") == "patched"

        assert target.read_text(encoding="utf-8") == "A beta G\n"
        assert len(approvals) == 1
        assert approvals[0].identity.arguments == call.arguments
        audit = session.action_audits()[-1]
        assert audit.identity.action.value == "workspace-overwrite"
        assert audit.status == ActionAuditStatus.SUCCEEDED
        assert audit.result_code == "patched"
        rendered = session._session_store.action_audits(SESSION_ID)[-1]
        assert "alpha" not in (rendered.message or "")
        assert "gamma" not in (rendered.message or "")
    finally:
        session.close()


def test_patch_hard_rejection_precedes_permission_and_audit(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("alpha alpha", encoding="utf-8")
    call = patch_call()
    provider = ToolProvider([call, AssistantText("invalid")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("invalid patch") == "invalid"

        result = provider.requests[1].history[-1]
        assert result.is_error
        assert "matches more than once" in result.content
        assert session.action_audits() == ()
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "alpha alpha"
    finally:
        session.close()


def test_accepted_patch_becomes_stale_before_execution(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")
    provider = ToolProvider([patch_call(), AssistantText("must not be reached")])

    def mutate_then_accept(request: HumanApprovalRequest) -> ApprovalResolution:
        assert request.preview is not None
        assert "-alpha beta gamma" in request.preview.body
        assert "+A beta G" in request.preview.body
        target.write_text("external\n", encoding="utf-8")
        return ApprovalResolution.ACCEPT

    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=mutate_then_accept,
    )
    try:
        with pytest.raises(ApprovalGrantError) as caught:
            session.prompt("patch")

        assert caught.value.code == ApprovalGrantRejection.STALE_PRECONDITION
        assert target.read_text(encoding="utf-8") == "external\n"
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.ABANDONED
    finally:
        session.close()


def test_provider_failure_after_patch_preserves_effect_and_audit_without_turn_commit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")
    provider = ToolProvider([patch_call(), RuntimeError("provider continuation failed")])
    session = open_session(
        tmp_path,
        provider,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        with pytest.raises(RuntimeError, match="provider continuation failed"):
            session.prompt("patch")

        assert target.read_text(encoding="utf-8") == "A beta G\n"
        assert session.history == ()
        assert session.action_audits()[-1].status == ActionAuditStatus.SUCCEEDED
    finally:
        session.close()
