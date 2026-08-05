"""Command-line interface for offline policy, named profiles, and persistent sessions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from leonervis_code import ProjectSession, __version__
from leonervis_code.agent.loop import AgentLoop
from leonervis_code.cli.approval import (
    TerminalApprovalBroker,
    noninteractive_approval,
    terminal_approval_handler,
)
from leonervis_code.cli.frontend import ApprovalPending, FrontendEventQueue
from leonervis_code.cli.terminal_app import supports_terminal_application
from leonervis_code.cli.brand import color_enabled
from leonervis_code.cli.event_sink import TerminalEventSink
from leonervis_code.cli.failure_guidance import render_turn_failure
from leonervis_code.cli.markdown_renderer import write_markdown_document
from leonervis_code.cli.presentation import (
    DEFAULT_ACTION_AUDIT_COUNT,
    DEFAULT_SESSION_PREVIEW_TURNS,
    DEFAULT_SESSION_SEARCH_MATCHES,
    DEFAULT_TOOL_LEDGER_COUNT,
    MAX_ACTION_AUDIT_COUNT,
    MAX_SESSION_PREVIEW_TURNS,
    MAX_TOOL_LEDGER_COUNT,
    render_action_audits,
    render_mcp_catalog,
    render_mcp_probe_result,
    render_mcp_server_status,
    render_mcp_server_statuses,
    render_resume_rejection,
    render_session_resume,
    render_session_diagnosis,
    render_session_export,
    render_session_preview,
    render_session_repair,
    render_session_search,
    render_session_summary,
    render_session_turn_range,
    render_session_title_fallback_reason,
    render_task_info,
    render_task_summary,
    render_task_timeline,
    render_tool_ledgers,
)
from leonervis_code.cli.repl import run_repl
from leonervis_code.core.action_coordinator import ActionIdentityChangedError
from leonervis_code.core.approvals import ApprovalGrantError
from leonervis_code.core.contracts import AssistantText, ToolArguments, ToolResult, ToolUse
from leonervis_code.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from leonervis_code.mcp import (
    McpClient,
    McpCatalogService,
    McpCapabilityClient,
    McpClientError,
    McpConfigurationError,
    McpOAuthError,
    McpOAuthManager,
    McpServerConfiguration,
    McpServerStore,
    McpTransport,
    McpTrustMode,
    inspect_mcp_conformance,
    McpToolPolicyError,
    McpToolPolicyRule,
    McpToolPolicyStore,
)
from leonervis_code.mcp.config import parse_environment_bindings
from leonervis_code.evals import (
    EvalError,
    builtin_eval_cases,
    builtin_coding_tasks,
    get_coding_task,
    materialize_coding_task,
    render_coding_task_result_json,
    render_coding_task_result_text,
    render_eval_result_json,
    render_eval_result_text,
    run_coding_task,
    run_eval_suite,
    score_coding_task,
)
from leonervis_code.core.orchestration import (
    GenerationOptions,
    OrchestrationError,
    RouteRequest,
    RouteRequirements,
)
from leonervis_code.providers.definitions import BUILTIN_PROVIDERS, WireProtocol
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.factory import create_provider
from leonervis_code.providers.fake import ScriptedFakeProvider
from leonervis_code.providers.manager import RuntimeProviderManager, RuntimeProviderStateError
from leonervis_code.providers.model_context import ModelContextCapabilityResolver
from leonervis_code.providers.native_search import (
    MAX_NATIVE_SEARCH_MANIFEST_BYTES,
    NativeSearchConfigurationError,
    NativeSearchManifest,
    adapter_option_values,
)
from leonervis_code.providers.profile import (
    MAX_MODEL_OUTPUT_TOKENS,
    NamedProviderProfile,
    ProviderProfileError,
    ProviderProfileSpec,
)
from leonervis_code.providers.profile_store import ProviderProfileStore
from leonervis_code.providers.request_context import ContextPreflightError
from leonervis_code.providers.request_policy import preview_request
from leonervis_code.providers.resolver import (
    RuntimeRouteError,
    resolve_profile_route,
    resolve_runtime_route,
)
from leonervis_code.providers.routing import (
    DEFAULT_ROUTE_REQUEST,
    FAKE_PROVIDER_PROFILES,
    resolve_route,
)
from leonervis_code.session import SessionResumeConflictError, SessionResumeContextError
from leonervis_code.session_store import (
    MAX_SESSION_SEARCH_MATCHES,
    SessionResumeCommitError,
    SessionStore,
    SessionStoreError,
)
from leonervis_code.task_store import TaskStore, TaskStoreError
from leonervis_code.task_records import TaskCompletionPolicy
from leonervis_code.task_records import TaskBudget, TaskStatus
from leonervis_code.tools.delete_directory import DeleteDirectoryTool
from leonervis_code.tools.delete_file import DeleteFileTool
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.mkdir import MkdirTool
from leonervis_code.tools.move_file import MoveFileTool
from leonervis_code.tools.copy_file import CopyFileTool
from leonervis_code.tools.read_file import ReadFileTool
from leonervis_code.tools.run_command import RunCommandTool
from leonervis_code.tools.edit_file import EditFileTool
from leonervis_code.tools.write_file import WriteFileTool
from leonervis_code.tools.web_search import WebSearchTool


def nonblank_prompt(value: str) -> str:
    """Reject prompt values that contain no visible characters."""
    if not value.strip():
        raise argparse.ArgumentTypeError("prompt must not be blank")
    return value


def nonblank_model(value: str) -> str:
    """Reject real model IDs that contain no visible characters."""
    if not value.strip():
        raise argparse.ArgumentTypeError("model must not be blank")
    return value


def output_token_budget(value: str) -> int:
    """Accept one bounded positive ASCII token budget."""
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("max output tokens must be an integer")
    tokens = int(value)
    if not 1 <= tokens <= MAX_MODEL_OUTPUT_TOKENS:
        raise argparse.ArgumentTypeError(
            f"max output tokens must be between 1 and {MAX_MODEL_OUTPUT_TOKENS}"
        )
    return tokens


def action_audit_count(value: str) -> int:
    """Accept one bounded ASCII count for terminal audit rendering."""
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("action audit limit must be an integer")
    count = int(value)
    if not 1 <= count <= MAX_ACTION_AUDIT_COUNT:
        raise argparse.ArgumentTypeError(
            f"action audit limit must be between 1 and {MAX_ACTION_AUDIT_COUNT}"
        )
    return count


def tool_ledger_count(value: str) -> int:
    """Accept one bounded ASCII count for durable tool-ledger rendering."""
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("tool ledger limit must be an integer")
    count = int(value)
    if not 1 <= count <= MAX_TOOL_LEDGER_COUNT:
        raise argparse.ArgumentTypeError(
            f"tool ledger limit must be between 1 and {MAX_TOOL_LEDGER_COUNT}"
        )
    return count


def session_preview_count(value: str) -> int:
    """Accept one bounded ASCII count for read-only Session preview."""
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("session preview limit must be an integer")
    count = int(value)
    if not 1 <= count <= MAX_SESSION_PREVIEW_TURNS:
        raise argparse.ArgumentTypeError(
            f"session preview limit must be between 1 and {MAX_SESSION_PREVIEW_TURNS}"
        )
    return count


def session_search_count(value: str) -> int:
    """Accept one bounded ASCII count for cross-Session search matches."""
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("session search limit must be an integer")
    count = int(value)
    if not 1 <= count <= MAX_SESSION_SEARCH_MATCHES:
        raise argparse.ArgumentTypeError(
            f"session search limit must be between 1 and {MAX_SESSION_SEARCH_MATCHES}"
        )
    return count


def positive_turn_number(value: str) -> int:
    """Accept one positive ASCII 1-based turn number."""
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("turn number must be a positive integer")
    return int(value)


def positive_task_limit(value: str) -> int:
    """Accept one positive ASCII Task budget or list limit."""
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("Task limit must be a positive integer")
    return int(value)


def task_list_limit(value: str) -> int:
    """Accept one bounded ASCII Task listing count."""
    limit = positive_task_limit(value)
    if limit > 100:
        raise argparse.ArgumentTypeError("Task list limit must be between 1 and 100")
    return limit


def build_parser() -> argparse.ArgumentParser:
    """Create the Foundation 3C command, profile, and REPL surface."""
    parser = argparse.ArgumentParser(
        prog="leonervis-code",
        description="Leonervis Code: a learning-first local coding-agent CLI prototype.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-C", "--cwd", dest="workspace", help="workspace directory")
    parser.add_argument("--resume", help="resume latest, a session UUID, or a transcript path")
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        default=PermissionMode.READ_ONLY.value,
        help="Host capability ceiling for model-requested actions (default: read-only)",
    )
    parser.add_argument(
        "--approval",
        choices=[mode.value for mode in ApprovalMode],
        default=ApprovalMode.ASK.value,
        help="whether in-scope controlled actions ask or run automatically (default: ask)",
    )
    profile_selector = parser.add_mutually_exclusive_group()
    profile_selector.add_argument("--profile", help="named endpoint profile for this invocation")
    profile_selector.add_argument(
        "--profile-id", dest="invocation_profile_id", help="profile UUID for this invocation"
    )
    parser.add_argument(
        "--model",
        dest="invocation_model",
        type=nonblank_model,
        help="direct provider/model selector, or model override with --profile",
    )
    parser.add_argument(
        "--max-output-tokens",
        dest="invocation_max_output_tokens",
        type=output_token_budget,
        help="process-local output budget override for prompt or interactive mode",
    )
    parser.add_argument(
        "--provider-protocol",
        dest="invocation_provider_protocol",
        choices=["openai-compatible", "openai-responses"],
        help="explicit custom provider wire protocol",
    )
    parser.add_argument(
        "--base-url", dest="invocation_base_url", help="custom provider API base URL"
    )
    parser.add_argument(
        "--api-key-env",
        dest="invocation_api_key_env",
        help="environment variable holding a custom API key",
    )
    subcommands = parser.add_subparsers(dest="command")
    prompt_parser = subcommands.add_parser("prompt", help="run one prompt turn")
    prompt_parser.add_argument("prompt", type=nonblank_prompt, help="the prompt to send")
    demo_read_parser = subcommands.add_parser(
        "demo-read", help="visibly demonstrate one deterministic read_file tool loop"
    )
    demo_read_parser.add_argument("path", help="relative workspace path for the demonstration")
    route_parser = subcommands.add_parser(
        "route", help="inspect one deterministic offline provider route plan"
    )
    route_parser.add_argument(
        "--model", dest="route_model", help="provider/model selector or unambiguous alias"
    )
    route_parser.add_argument(
        "--fallback-model", action="append", default=[], help="ordered fallback selector"
    )
    route_parser.add_argument("--require-tool-use", action="store_true")
    route_parser.add_argument("--require-streaming", action="store_true")
    route_parser.add_argument("--max-output-tokens", type=int)
    route_parser.add_argument("--temperature", type=float)

    eval_parser = subcommands.add_parser(
        "eval", help="run deterministic Host and actual coding-task evaluations"
    )
    eval_commands = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_commands.add_parser("list", help="list built-in deterministic Eval cases")
    eval_run_parser = eval_commands.add_parser(
        "run", help="run one case or the complete isolated baseline"
    )
    eval_run_parser.add_argument("selector", nargs="?", default="all")
    eval_run_parser.add_argument("--format", choices=["text", "json"], default="text")
    eval_task_parser = eval_commands.add_parser(
        "task", help="prepare, score, or explicitly run an actual coding task"
    )
    eval_task_commands = eval_task_parser.add_subparsers(dest="eval_task_command", required=True)
    eval_task_commands.add_parser("list", help="list built-in actual coding tasks")
    eval_task_prepare = eval_task_commands.add_parser(
        "prepare", help="materialize one task without Host-private tests"
    )
    eval_task_prepare.add_argument("task_id")
    eval_task_prepare.add_argument("output")
    eval_task_score = eval_task_commands.add_parser(
        "score", help="score one existing candidate with visible and Host-private tests"
    )
    eval_task_score.add_argument("task_id")
    eval_task_score.add_argument("candidate_workspace", metavar="WORKSPACE")
    eval_task_score.add_argument("--format", choices=["text", "json"], default="text")
    eval_task_run = eval_task_commands.add_parser(
        "run", help="opt in to one real-provider attempt and deterministic Host scoring"
    )
    eval_task_run.add_argument("task_id")
    eval_task_run.add_argument("--real-provider", action="store_true", required=True)
    eval_task_run.add_argument("--output", help="retain the new isolated task workspace")
    eval_task_run.add_argument("--format", choices=["text", "json"], default="text")

    provider_parser = subcommands.add_parser("provider", help="manage named provider profiles")
    provider_commands = provider_parser.add_subparsers(dest="provider_command", required=True)
    add_parser = provider_commands.add_parser("add", help="add one global provider profile")
    add_parser.add_argument("name")
    add_parser.add_argument("--provider", required=True, choices=[*BUILTIN_PROVIDERS, "custom"])
    add_parser.add_argument("--model", dest="profile_model", required=True, type=nonblank_model)
    add_parser.add_argument(
        "--protocol",
        dest="profile_protocol",
        choices=["openai-compatible", "openai-responses", "anthropic-messages"],
    )
    add_parser.add_argument("--base-url", dest="profile_base_url")
    add_parser.add_argument("--api-key-env", dest="profile_api_key_env")
    add_parser.add_argument("--max-output-tokens", type=int, default=1024)
    add_parser.add_argument("--context-window-tokens", type=int)
    add_parser.add_argument("--model-max-output-tokens", type=int)
    add_parser.add_argument("--temperature", type=float)
    add_parser.add_argument(
        "--native-search-adapter",
        choices=adapter_option_values(),
        help="provider-native search adapter (built-ins default to auto; custom defaults to none)",
    )
    add_parser.add_argument("--native-search-manifest", type=Path)
    add_parser.add_argument("--replace", action="store_true")
    add_parser.add_argument("--if-revision", type=int)
    list_parser = provider_commands.add_parser("list", help="list global provider profiles")
    list_parser.add_argument("--show-ids", action="store_true")
    show_parser = provider_commands.add_parser("show", help="show one redacted provider profile")
    show_selector = show_parser.add_mutually_exclusive_group(required=True)
    show_selector.add_argument("name", nargs="?")
    show_selector.add_argument("--id", dest="profile_id")
    use_parser = provider_commands.add_parser("use", help="activate one provider profile")
    use_selector = use_parser.add_mutually_exclusive_group(required=True)
    use_selector.add_argument("name", nargs="?")
    use_selector.add_argument("--id", dest="profile_id")
    use_parser.add_argument("--scope", choices=["project", "user"], default="project")
    clear_parser = provider_commands.add_parser("clear", help="clear one active profile layer")
    clear_parser.add_argument("--scope", choices=["project", "user"], default="project")
    remove_parser = provider_commands.add_parser("remove", help="remove an inactive profile")
    remove_selector = remove_parser.add_mutually_exclusive_group(required=True)
    remove_selector.add_argument("name", nargs="?")
    remove_selector.add_argument("--id", dest="profile_id")
    remove_parser.add_argument("--if-revision", type=int)
    rename_parser = provider_commands.add_parser("rename", help="rename one provider profile")
    rename_selector = rename_parser.add_mutually_exclusive_group(required=True)
    rename_selector.add_argument("name", nargs="?")
    rename_selector.add_argument("--id", dest="profile_id")
    rename_parser.add_argument("new_name")
    rename_parser.add_argument("--if-revision", type=int)
    replace_parser = provider_commands.add_parser(
        "replace", help="replace one profile configuration"
    )
    replace_parser.add_argument("name")
    replace_parser.add_argument("--provider", required=True, choices=[*BUILTIN_PROVIDERS, "custom"])
    replace_parser.add_argument("--model", dest="profile_model", required=True, type=nonblank_model)
    replace_parser.add_argument(
        "--protocol",
        dest="profile_protocol",
        choices=["openai-compatible", "openai-responses", "anthropic-messages"],
    )
    replace_parser.add_argument("--base-url", dest="profile_base_url")
    replace_parser.add_argument("--api-key-env", dest="profile_api_key_env")
    replace_parser.add_argument("--max-output-tokens", type=int, default=1024)
    replace_parser.add_argument("--context-window-tokens", type=int)
    replace_parser.add_argument("--model-max-output-tokens", type=int)
    replace_parser.add_argument("--temperature", type=float)
    replace_parser.add_argument(
        "--native-search-adapter",
        choices=adapter_option_values(),
        help="provider-native search adapter (built-ins default to auto; custom defaults to none)",
    )
    replace_parser.add_argument("--native-search-manifest", type=Path)
    replace_parser.add_argument("--if-revision", type=int)
    provider_commands.add_parser("migrate", help="upgrade readable profile files to schema v5")

    mcp_parser = subcommands.add_parser("mcp", help="configure and inspect MCP servers")
    mcp_commands = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_add = mcp_commands.add_parser("add", help="add one disabled local stdio server")
    mcp_add.add_argument("name")
    mcp_add.add_argument("--scope", choices=["user", "project"], default="project")
    mcp_add.add_argument("--command", dest="mcp_executable", required=True)
    mcp_add.add_argument("--arg", action="append", default=[])
    mcp_add.add_argument("--server-cwd", default=".")
    mcp_add.add_argument("--env", action="append", default=[])
    mcp_add.add_argument("--enabled", action="store_true")
    mcp_add.add_argument("--expose-workspace-root", action="store_true")
    mcp_add.add_argument("--replace", action="store_true")
    mcp_add.add_argument("--if-revision", type=int)
    mcp_add_http = mcp_commands.add_parser(
        "add-http", help="add one disabled remote Streamable HTTP server"
    )
    mcp_add_http.add_argument("name")
    mcp_add_http.add_argument("--scope", choices=["user", "project"], default="project")
    mcp_add_http.add_argument("--endpoint", required=True)
    mcp_add_http.add_argument("--bearer-token-env")
    mcp_add_http.add_argument("--oauth-client-id")
    mcp_add_http.add_argument("--oauth-client-secret-env")
    mcp_add_http.add_argument("--oauth-scope", action="append", default=[])
    mcp_add_http.add_argument("--enabled", action="store_true")
    mcp_add_http.add_argument("--expose-workspace-root", action="store_true")
    mcp_add_http.add_argument("--replace", action="store_true")
    mcp_add_http.add_argument("--if-revision", type=int)
    mcp_commands.add_parser("list", help="list configured MCP servers")
    mcp_show = mcp_commands.add_parser("show", help="show one redacted MCP server configuration")
    mcp_show.add_argument("name")
    for action in ("enable", "disable", "remove"):
        command = mcp_commands.add_parser(action, help=f"{action} one MCP server")
        command.add_argument("name")
        command.add_argument("--scope", choices=["user", "project"], default="project")
        command.add_argument("--if-revision", type=int)
    mcp_probe = mcp_commands.add_parser("probe", help="temporarily initialize and list tools")
    mcp_probe.add_argument("name")
    mcp_doctor = mcp_commands.add_parser(
        "doctor", help="run bounded transport and capability interoperability checks"
    )
    mcp_doctor.add_argument("name")
    mcp_commands.add_parser("catalog", help="refresh the normalized MCP quarantine catalog")
    mcp_policy = mcp_commands.add_parser("policy", help="manage exact MCP tool trust policy")
    mcp_policy_commands = mcp_policy.add_subparsers(dest="mcp_policy_command", required=True)
    mcp_policy_commands.add_parser("list", help="list local MCP tool policies")
    mcp_policy_show = mcp_policy_commands.add_parser("show", help="show one MCP tool policy")
    mcp_policy_show.add_argument("qualified_name")
    mcp_policy_set = mcp_policy_commands.add_parser(
        "set", help="bind one exact catalog candidate to a permission action"
    )
    mcp_policy_set.add_argument("qualified_name")
    mcp_policy_set.add_argument("--scope", choices=["user", "project"], default="project")
    mcp_policy_set.add_argument("--schema-fingerprint", required=True)
    mcp_policy_set.add_argument("--action", choices=["workspace-read", "dangerous"], required=True)
    mcp_policy_set.add_argument("--replace", action="store_true")
    mcp_policy_set.add_argument("--if-revision", type=int)
    mcp_policy_clear = mcp_policy_commands.add_parser("clear", help="remove one MCP tool policy")
    mcp_policy_clear.add_argument("qualified_name")
    mcp_policy_clear.add_argument("--scope", choices=["user", "project"], default="project")
    mcp_policy_clear.add_argument("--if-revision", type=int)
    mcp_oauth = mcp_commands.add_parser("oauth", help="manage remote MCP OAuth 2.1 credentials")
    mcp_oauth_commands = mcp_oauth.add_subparsers(dest="mcp_oauth_command", required=True)
    mcp_oauth_begin = mcp_oauth_commands.add_parser(
        "begin", help="discover metadata and create one PKCE authorization URL"
    )
    mcp_oauth_begin.add_argument("name")
    mcp_oauth_begin.add_argument("--redirect-uri", default="http://127.0.0.1:8765/callback")
    mcp_oauth_complete = mcp_oauth_commands.add_parser(
        "complete", help="validate state and exchange one authorization code"
    )
    mcp_oauth_complete.add_argument("name")
    mcp_oauth_complete.add_argument("--code", required=True)
    mcp_oauth_complete.add_argument("--state", required=True)
    mcp_oauth_status = mcp_oauth_commands.add_parser("status", help="show redacted OAuth state")
    mcp_oauth_status.add_argument("name")
    mcp_oauth_logout = mcp_oauth_commands.add_parser("logout", help="delete local OAuth state")
    mcp_oauth_logout.add_argument("name")
    mcp_resources = mcp_commands.add_parser("resources", help="inspect bounded MCP resources")
    mcp_resource_commands = mcp_resources.add_subparsers(dest="mcp_resource_command", required=True)
    mcp_resource_list = mcp_resource_commands.add_parser("list", help="list resource metadata")
    mcp_resource_list.add_argument("name")
    mcp_resource_read = mcp_resource_commands.add_parser("read", help="read one bounded resource")
    mcp_resource_read.add_argument("name")
    mcp_resource_read.add_argument("uri")
    for resource_action in ("subscribe", "unsubscribe"):
        resource_parser = mcp_resource_commands.add_parser(
            resource_action, help=f"persistently {resource_action} one resource URI"
        )
        resource_parser.add_argument("name")
        resource_parser.add_argument("uri")
        resource_parser.add_argument("--scope", choices=["user", "project"], default="project")
        resource_parser.add_argument("--if-revision", type=int)
    mcp_prompts = mcp_commands.add_parser("prompts", help="inspect non-authoritative MCP prompts")
    mcp_prompt_commands = mcp_prompts.add_subparsers(dest="mcp_prompt_command", required=True)
    mcp_prompt_list = mcp_prompt_commands.add_parser("list", help="list prompt metadata")
    mcp_prompt_list.add_argument("name")
    mcp_prompt_get = mcp_prompt_commands.add_parser("get", help="render one untrusted prompt")
    mcp_prompt_get.add_argument("name")
    mcp_prompt_get.add_argument("prompt_name")
    mcp_prompt_get.add_argument("--arg", action="append", default=[])

    session_parser = subcommands.add_parser("session", help="inspect durable workspace sessions")
    session_commands = session_parser.add_subparsers(dest="session_command", required=True)
    session_commands.add_parser("list", help="list durable sessions")
    session_show = session_commands.add_parser("show", help="show one durable session")
    session_show.add_argument("selector", nargs="?", default="latest")
    session_preview = session_commands.add_parser(
        "preview", help="preview recent final-text turns without resuming"
    )
    session_preview.add_argument("selector", nargs="?", default="latest")
    session_preview.add_argument(
        "--limit",
        type=session_preview_count,
        default=DEFAULT_SESSION_PREVIEW_TURNS,
        help=f"number of recent complete turns to show (default: {DEFAULT_SESSION_PREVIEW_TURNS})",
    )
    session_turns = session_commands.add_parser(
        "turns", help="show a bounded range of complete final-text turns"
    )
    session_turns.add_argument("selector")
    session_turns.add_argument("start_turn", type=positive_turn_number)
    session_turns.add_argument(
        "--count",
        type=session_preview_count,
        default=DEFAULT_SESSION_PREVIEW_TURNS,
    )
    session_search = session_commands.add_parser(
        "search", help="search final user and assistant text across Sessions"
    )
    session_search.add_argument("query")
    session_search.add_argument(
        "--limit",
        type=session_search_count,
        default=DEFAULT_SESSION_SEARCH_MATCHES,
    )
    session_export = session_commands.add_parser(
        "export", help="write a bounded conversation export to stdout"
    )
    session_export.add_argument("selector", nargs="?", default="latest")
    session_export.add_argument("--format", choices=("markdown", "json"), default="markdown")
    session_fork = session_commands.add_parser(
        "fork", help="create a new Session from complete parent turns"
    )
    session_fork.add_argument("selector")
    session_fork.add_argument("through_turn", type=positive_turn_number)
    session_doctor = session_commands.add_parser(
        "doctor", help="diagnose one transcript without modifying it"
    )
    session_doctor.add_argument("selector", nargs="?", default="latest")
    session_repair = session_commands.add_parser(
        "repair", help="back up and repair only an incomplete final record"
    )
    session_repair.add_argument("selector", nargs="?", default="latest")
    session_actions = session_commands.add_parser(
        "actions", help="show recent redacted action audits for one durable session"
    )
    session_actions.add_argument("selector", nargs="?", default="latest")
    session_actions.add_argument(
        "--limit",
        type=action_audit_count,
        default=DEFAULT_ACTION_AUDIT_COUNT,
        help=f"number of recent actions to show (default: {DEFAULT_ACTION_AUDIT_COUNT})",
    )
    session_tools = session_commands.add_parser(
        "tools", help="show recent durable per-turn tool ledgers"
    )
    session_tools.add_argument("selector", nargs="?", default="latest")
    session_tools.add_argument(
        "--limit",
        type=tool_ledger_count,
        default=DEFAULT_TOOL_LEDGER_COUNT,
        help=f"number of recent committed turns to show (default: {DEFAULT_TOOL_LEDGER_COUNT})",
    )
    session_tools.add_argument(
        "--details",
        action="store_true",
        help="show bounded per-request tool names, outcomes, and safe result codes",
    )
    task_parser = subcommands.add_parser("task", help="create and inspect durable workspace Tasks")
    task_commands = task_parser.add_subparsers(dest="task_command", required=True)
    task_create = task_commands.add_parser("create", help="create one ready Task")
    task_create.add_argument("objective", type=nonblank_prompt)
    task_create.add_argument(
        "--session",
        dest="owner_session",
        default="latest",
        help="owner Session UUID (default: latest)",
    )
    task_create.add_argument(
        "--accept",
        dest="acceptance_criteria",
        action="append",
        default=[],
        help="acceptance criterion; repeat for multiple criteria",
    )
    task_create.add_argument(
        "--criterion",
        dest="structured_criteria",
        action="append",
        default=[],
        metavar="JSON",
        help="structured acceptance criterion JSON object; repeat for multiple criteria",
    )
    task_create.add_argument(
        "--completion-policy",
        choices=[policy.value for policy in TaskCompletionPolicy],
        default=TaskCompletionPolicy.MANUAL.value,
        help="manual or Host auto-completion after verified acceptance",
    )
    task_create.add_argument("--name", type=nonblank_prompt, help="bounded display name")
    task_create.add_argument("--parent", dest="parent_task_id", help="parent Task UUID")
    task_create.add_argument("--max-stages", type=positive_task_limit)
    task_create.add_argument("--max-provider-invocations", type=positive_task_limit)
    task_create.add_argument("--max-tool-requests", type=positive_task_limit)
    task_create.add_argument("--max-input-tokens", type=positive_task_limit)
    task_create.add_argument("--max-output-tokens", type=positive_task_limit)
    task_list = task_commands.add_parser("list", help="list durable Tasks")
    task_list.add_argument("--limit", type=task_list_limit, default=100)
    task_list.add_argument("--status", choices=[status.value for status in TaskStatus])
    task_list.add_argument("--archive", choices=("all", "active", "archived"), default="all")
    task_list.add_argument(
        "--name",
        dest="name_query",
        type=nonblank_prompt,
        help="case-insensitive name substring",
    )
    task_show = task_commands.add_parser("show", help="show one durable Task")
    task_show.add_argument("task_id")
    task_timeline = task_commands.add_parser("timeline", help="show one Task Stage timeline")
    task_timeline.add_argument("task_id")
    return parser


def render_demo_read(workspace: Path, path: str, stdout: TextIO) -> int:
    """Run and visibly report one scripted ``read_file`` tool demonstration."""
    tool_use = ToolUse(
        tool_use_id="demo-read-1",
        name="read_file",
        arguments=ToolArguments.from_mapping({"path": path}),
    )
    provider = ScriptedFakeProvider(
        [tool_use, AssistantText("Demo final response: provider received the read_file result.")]
    )
    demo_loop = AgentLoop(
        provider,
        ReadFileTool(workspace),
        GlobTool(workspace),
        GrepTool(workspace),
        ListDirectoryTool(workspace),
    )
    stdout.write(f"[demo] provider requested read_file: {path}\n")
    response = demo_loop.run(f"Demo read {path}")
    result = provider.received_requests[1].history[-1]
    assert isinstance(result, ToolResult)
    if result.is_error:
        stdout.write(f"[read_file] {path}\n  ✗ {result.content}\n")
    else:
        truncation = " (truncated)" if result.truncated else ""
        preview = result.content.splitlines()[0] if result.content else "<empty file>"
        stdout.write(
            f"[read_file] {path}\n"
            f"  ✓ {len(result.content.encode('utf-8'))} UTF-8 bytes returned{truncation}\n"
            f"  preview: {preview}\n"
        )
    stdout.write(f"{response}\n")
    return 0


def render_route(
    *,
    model: str | None,
    fallback_models: Sequence[str],
    require_tool_use: bool,
    require_streaming: bool,
    max_output_tokens: int | None,
    temperature: float | None,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Render one offline fake-provider request-policy plan."""
    request = RouteRequest(
        primary_selector=model or DEFAULT_ROUTE_REQUEST.primary_selector,
        fallback_selectors=tuple(fallback_models),
        requirements=RouteRequirements(require_tool_use, require_streaming),
        options=GenerationOptions(max_output_tokens, temperature),
    )
    try:
        route = resolve_route(request)
    except OrchestrationError as error:
        print(f"route error: {error}", file=stderr)
        return 2
    profiles = {profile.provider_id: profile for profile in FAKE_PROVIDER_PROFILES}
    for label, plan in (
        ("primary", route.primary),
        *(("fallback", item) for item in route.fallbacks),
    ):
        profile = profiles[plan.provider_id]
        preview = preview_request(plan)
        credential = "configured" if profile.credential_ref is not None else "not configured"
        stdout.write(f"{label}: {plan.provider_id}/{plan.model_id}\n")
        stdout.write(f"  credential: {credential}\n")
        canonical = ", ".join(f"{name}={value}" for name, value in plan.canonical_parameters)
        native = ", ".join(f"{name}={value}" for name, value in preview.native_parameters)
        stdout.write(f"  canonical parameters: {canonical or '<none>'}\n")
        stdout.write(f"  native preview: {native or '<none>'}\n")
        if preview.diagnostics:
            stdout.write("  diagnostics:\n")
            for diagnostic in preview.diagnostics:
                stdout.write(
                    f"    {diagnostic.severity} {diagnostic.code}: "
                    f"{diagnostic.message} ({diagnostic.action})\n"
                )
        else:
            stdout.write("  diagnostics: <none>\n")
    return 0


def render_runtime_route(
    route,
    environment: Mapping[str, str],
    stdout: TextIO,
    *,
    profile_override: int | None = None,
    model_max_output_override: int | None = None,
) -> int:
    """Render one real route without constructing a client or exposing secrets."""
    definition = route.definition
    capability = ModelContextCapabilityResolver().resolve_offline(
        route,
        profile_override=profile_override,
        model_max_output_override=model_max_output_override,
    )
    credential = "not required"
    if definition.credential_env:
        credential = (
            "configured" if environment.get(definition.credential_env, "").strip() else "missing"
        )
    stdout.write(f"provider: {definition.provider_id}\n")
    stdout.write(f"protocol: {definition.protocol}\n")
    stdout.write(f"selected model: {route.selected_model}\n")
    stdout.write(f"wire model: {route.wire_model}\n")
    stdout.write(f"base URL: {route.base_url} ({route.base_url_source})\n")
    stdout.write(f"credential: {credential}\n")
    context = capability.context_window_tokens or "unknown"
    stdout.write(f"context window: {context} ({capability.source.value})\n")
    model_output = capability.model_max_output_tokens or "unknown"
    stdout.write(f"model max output: {model_output} ({capability.model_max_output_source.value})\n")
    stdout.write(f"requested output reserve: {route.max_output_tokens}\n")
    stdout.write(
        f"native search: {'available' if route.native_search.available else 'unavailable'}\n"
    )
    stdout.write(
        "native search adapter: "
        f"{route.native_search.adapter_id.value if route.native_search.adapter_id else '<none>'}\n"
    )
    stdout.write(f"native search source: {route.native_search.source.value}\n")
    if capability.diagnostic:
        stdout.write(f"context diagnostic: {capability.diagnostic}\n")
    return 0


def render_provider_failure(error: ProviderAdapterError, stderr: TextIO) -> int:
    """Render one normalized provider failure without exposing raw SDK data."""
    print(render_turn_failure(error, provider_prefix="provider error"), file=stderr)
    return 2


def render_profile(
    profile: NamedProviderProfile, environment: Mapping[str, str], stdout: TextIO
) -> None:
    """Render one profile's non-secret endpoint configuration."""
    credential = "not required"
    if profile.api_key_env:
        credential = "configured" if environment.get(profile.api_key_env, "").strip() else "missing"
    elif profile.provider_id in BUILTIN_PROVIDERS:
        name = BUILTIN_PROVIDERS[profile.provider_id].credential_env
        if name:
            credential = "configured" if environment.get(name, "").strip() else "missing"
    stdout.write(f"name: {profile.name}\n")
    stdout.write(f"profile ID: {profile.profile_id}\n")
    stdout.write(f"revision: {profile.revision}\n")
    stdout.write(f"provider: {profile.provider_id}\n")
    stdout.write(f"protocol: {profile.protocol.value}\n")
    stdout.write(f"model: {profile.model}\n")
    stdout.write(f"base URL: {profile.base_url or '<provider default>'}\n")
    stdout.write(f"credential: {credential}\n")
    stdout.write(f"max output tokens: {profile.max_output_tokens}\n")
    stdout.write(
        "context window override: "
        f"{profile.context_window_tokens if profile.context_window_tokens is not None else '<none>'}\n"
    )
    stdout.write(
        "model max output override: "
        f"{profile.model_max_output_tokens if profile.model_max_output_tokens is not None else '<none>'}\n"
    )
    stdout.write(
        f"temperature: {profile.temperature if profile.temperature is not None else '<default>'}\n"
    )
    route = resolve_profile_route(profile, environment=environment)
    stdout.write(
        "native search: "
        f"{'available; enabled by default' if route.native_search.available else 'unavailable'}\n"
    )
    stdout.write(
        "native search adapter: "
        f"{route.native_search.adapter_id.value if route.native_search.adapter_id else '<none>'}\n"
    )
    stdout.write(f"native search source: {route.native_search.source.value}\n")
    if route.native_search.manifest is not None:
        stdout.write(f"native search manifest: {route.native_search.manifest.manifest_id}\n")
        stdout.write(
            f"native search manifest digest: sha256:{route.native_search.manifest.fingerprint()}\n"
        )


def _profile_protocol(provider_id: str, option: str | None, model: str) -> WireProtocol:
    if provider_id in BUILTIN_PROVIDERS:
        expected = BUILTIN_PROVIDERS[provider_id].protocol
        if provider_id == "deepseek" and model == "deepseek-v4-flash":
            expected = WireProtocol.OPENAI_RESPONSES
        if option is not None and option != _protocol_option(expected):
            raise ProviderProfileError(
                f"profile protocol does not match built-in provider {provider_id}"
            )
        return expected
    if option == "openai-compatible":
        return WireProtocol.OPENAI_CHAT_COMPLETIONS
    if option == "openai-responses":
        return WireProtocol.OPENAI_RESPONSES
    if option == "anthropic-messages":
        return WireProtocol.ANTHROPIC_MESSAGES
    raise ProviderProfileError(
        "custom profiles require --protocol openai-compatible, openai-responses, or anthropic-messages"
    )


def _protocol_option(protocol: WireProtocol) -> str:
    if protocol == WireProtocol.ANTHROPIC_MESSAGES:
        return "anthropic-messages"
    if protocol == WireProtocol.OPENAI_RESPONSES:
        return "openai-responses"
    return "openai-compatible"


def _store(
    workspace: Path,
    environment: Mapping[str, str],
    user_profile_path: Path | None,
    project_profile_path: Path | None,
) -> ProviderProfileStore:
    return ProviderProfileStore.for_workspace(
        workspace,
        environment=environment,
        user_path=user_profile_path,
        project_path=project_profile_path,
    )


def _profile_spec(arguments: argparse.Namespace) -> ProviderProfileSpec:
    manifest = _load_native_search_manifest(arguments.native_search_manifest)
    selected_adapter = arguments.native_search_adapter
    if arguments.provider == "custom" and selected_adapter is None and manifest is None:
        selected_adapter = "none"
    return ProviderProfileSpec(
        name=arguments.name,
        provider_id=arguments.provider,
        protocol=_profile_protocol(
            arguments.provider,
            arguments.profile_protocol,
            arguments.profile_model,
        ),
        model=arguments.profile_model,
        base_url=arguments.profile_base_url,
        api_key_env=arguments.profile_api_key_env,
        max_output_tokens=arguments.max_output_tokens,
        context_window_tokens=arguments.context_window_tokens,
        model_max_output_tokens=arguments.model_max_output_tokens,
        temperature=arguments.temperature,
        native_search_adapter=selected_adapter,
        native_search_manifest=manifest,
    )


def _load_native_search_manifest(path: Path | None) -> NativeSearchManifest | None:
    if path is None:
        return None
    if path.is_symlink():
        raise ProviderProfileError("native-search manifest path must not be a symlink")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ProviderProfileError(f"could not read native-search manifest: {error}") from None
    if not payload or len(payload) > MAX_NATIVE_SEARCH_MANIFEST_BYTES:
        raise ProviderProfileError("native-search manifest is empty or exceeds its byte limit")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderProfileError("native-search manifest is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise ProviderProfileError("native-search manifest must be a JSON object")
    try:
        return NativeSearchManifest.from_mapping(value)
    except NativeSearchConfigurationError as error:
        raise ProviderProfileError(str(error)) from None


def _selected_profile(
    store: ProviderProfileStore, arguments: argparse.Namespace
) -> NamedProviderProfile:
    profile_id = getattr(arguments, "profile_id", None)
    return store.get_profile_by_id(profile_id) if profile_id else store.get_profile(arguments.name)


def handle_provider_command(
    arguments: argparse.Namespace,
    *,
    workspace: Path,
    environment: Mapping[str, str],
    user_profile_path: Path | None,
    project_profile_path: Path | None,
    provider_factory,
    stdout: TextIO,
) -> int:
    """Execute one profile CRUD or activation command."""
    store = _store(workspace, environment, user_profile_path, project_profile_path)
    command = arguments.provider_command
    if command == "add":
        configured = store.add_profile(
            _profile_spec(arguments),
            replace=arguments.replace,
            expected_revision=arguments.if_revision,
        )
        stdout.write(f"Saved provider profile {configured.name}.\n")
    elif command == "replace":
        current = store.get_profile(arguments.name)
        configured = store.replace_profile(
            current.profile_id,
            _profile_spec(arguments),
            expected_revision=arguments.if_revision,
        )
        stdout.write(
            f"Replaced provider profile {configured.name} at revision {configured.revision}.\n"
        )
    elif command == "list":
        profiles = store.list_profiles()
        active = store.active_selection()
        if not profiles:
            stdout.write("No provider profiles configured.\n")
        for configured in profiles:
            marker = " *" if active and active.profile_id == configured.profile_id else ""
            identity = (
                f" [{configured.profile_id} r{configured.revision}]" if arguments.show_ids else ""
            )
            stdout.write(
                f"{configured.name}{marker}: {configured.provider_id}/{configured.model}{identity}\n"
            )
    elif command == "show":
        render_profile(_selected_profile(store, arguments), environment, stdout)
    elif command == "remove":
        configured = _selected_profile(store, arguments)
        store.remove_profile_by_id(configured.profile_id, expected_revision=arguments.if_revision)
        stdout.write(f"Removed provider profile {configured.name}.\n")
    elif command == "rename":
        configured = _selected_profile(store, arguments)
        renamed = store.rename_profile(
            configured.profile_id,
            arguments.new_name,
            expected_revision=arguments.if_revision,
        )
        stdout.write(f"Renamed provider profile {configured.name} to {renamed.name}.\n")
    elif command == "migrate":
        store.migrate()
        stdout.write("Migrated provider configuration to schema v5.\n")
    elif command == "clear":
        RuntimeProviderManager.prepare_clear(
            store,
            scope=arguments.scope,
            environment=environment,
            provider_factory=provider_factory,
        )
        stdout.write(f"Cleared {arguments.scope} active provider profile.\n")
    elif command == "use":
        configured = _selected_profile(store, arguments)
        status = RuntimeProviderManager.prepare_profile(
            store,
            configured.name,
            scope=arguments.scope,
            environment=environment,
            provider_factory=provider_factory,
        )
        stdout.write(f"Using provider profile {status.profile} at {arguments.scope} scope.\n")
    return 0


def handle_mcp_command(
    arguments: argparse.Namespace,
    *,
    workspace: Path,
    environment: Mapping[str, str],
    user_mcp_path: Path | None,
    project_mcp_path: Path | None,
    user_mcp_policy_path: Path | None,
    project_mcp_policy_path: Path | None,
    mcp_client_factory,
    stdout: TextIO,
) -> int:
    """Execute credential-free MCP configuration or one temporary probe."""
    store = McpServerStore.for_workspace(
        workspace,
        environment=environment,
        user_path=user_mcp_path,
        project_path=project_mcp_path,
    )
    client = (mcp_client_factory or McpClient)(workspace, environment=environment)
    policy_store = McpToolPolicyStore.for_workspace(
        workspace,
        environment=environment,
        user_path=user_mcp_policy_path,
        project_path=project_mcp_policy_path,
    )
    command = arguments.mcp_command
    if command == "add":
        entry = store.add_server(
            McpServerConfiguration(
                name=arguments.name,
                command=arguments.mcp_executable,
                args=tuple(arguments.arg),
                cwd=arguments.server_cwd,
                environment=parse_environment_bindings(arguments.env),
                enabled=arguments.enabled,
                expose_workspace_root=arguments.expose_workspace_root,
            ),
            scope=arguments.scope,
            replace_existing=arguments.replace,
            expected_revision=arguments.if_revision,
        )
        stdout.write(
            f"Saved {entry.scope} MCP server {entry.configuration.name} at revision "
            f"{entry.configuration.revision} ({'enabled' if entry.configuration.enabled else 'disabled'}).\n"
        )
    elif command == "add-http":
        entry = store.add_server(
            McpServerConfiguration(
                name=arguments.name,
                endpoint=arguments.endpoint,
                bearer_token_env=arguments.bearer_token_env,
                oauth_client_id=arguments.oauth_client_id,
                oauth_client_secret_env=arguments.oauth_client_secret_env,
                oauth_scopes=tuple(sorted(set(arguments.oauth_scope))),
                enabled=arguments.enabled,
                expose_workspace_root=arguments.expose_workspace_root,
                transport=McpTransport.STREAMABLE_HTTP,
                trust=McpTrustMode.REMOTE_HTTPS,
            ),
            scope=arguments.scope,
            replace_existing=arguments.replace,
            expected_revision=arguments.if_revision,
        )
        stdout.write(
            f"Saved {entry.scope} MCP server {entry.configuration.name} at revision "
            f"{entry.configuration.revision} "
            f"({'enabled' if entry.configuration.enabled else 'disabled'}).\n"
        )
    elif command == "list":
        statuses = tuple(client.inspect_status(entry) for entry in store.list_servers())
        stdout.write(f"{render_mcp_server_statuses(statuses)}\n")
    elif command == "show":
        stdout.write(
            f"{render_mcp_server_status(client.inspect_status(store.get_server(arguments.name)))}\n"
        )
    elif command in {"enable", "disable"}:
        entry = store.set_enabled(
            arguments.name,
            scope=arguments.scope,
            enabled=command == "enable",
            expected_revision=arguments.if_revision,
        )
        stdout.write(
            f"MCP server {entry.configuration.name} is now "
            f"{'enabled' if entry.configuration.enabled else 'disabled'} at revision "
            f"{entry.configuration.revision}.\n"
        )
    elif command == "remove":
        store.remove_server(
            arguments.name,
            scope=arguments.scope,
            expected_revision=arguments.if_revision,
        )
        stdout.write(f"Removed {arguments.scope} MCP server {arguments.name}.\n")
    elif command == "probe":
        stdout.write(f"{render_mcp_probe_result(client.probe(store.get_server(arguments.name)))}\n")
    elif command == "doctor":
        entry = store.get_server(arguments.name)
        report = inspect_mcp_conformance(entry, client.probe(entry))
        stdout.write(
            f"MCP conformance: {'passed' if report.passed else 'failed'}\n"
            f"Server: {report.configured_name}\n"
            f"Transport/protocol: {report.transport}/{report.protocol_version}\n"
            f"Known capabilities: {', '.join(report.known_capabilities) or 'none'}\n"
            f"Unknown capabilities: {', '.join(report.unknown_capabilities) or 'none'}\n"
            f"Tools: {report.tools}\n"
            f"Cleanup complete: {'yes' if report.cleanup_complete else 'no'}\n"
            "Legacy HTTP/SSE: intentionally unsupported; use Streamable HTTP.\n"
        )
    elif command == "catalog":
        stdout.write(
            f"{render_mcp_catalog(McpCatalogService(store, client, policy_store).snapshot(refresh=True))}\n"
        )
    elif command == "policy":
        policy_command = arguments.mcp_policy_command
        if policy_command == "list":
            rules = policy_store.list_rules()
            if not rules:
                stdout.write("No MCP tool policies configured.\n")
            else:
                for scope, rule in rules:
                    stdout.write(
                        f"{rule.qualified_name}: {scope}, {rule.action.value}, r{rule.revision}, "
                        f"schema {rule.schema_fingerprint}\n"
                    )
        elif policy_command == "show":
            scope, rule = policy_store.get_rule(arguments.qualified_name)
            stdout.write(
                f"MCP tool policy: {rule.qualified_name}\n"
                f"Policy scope: {scope}\n"
                f"Action: {rule.action.value}\n"
                f"Policy revision: {rule.revision}\n"
                f"Server: {rule.server_scope}/{rule.configured_name} "
                f"r{rule.configuration_revision}\n"
                f"Remote tool: {rule.remote_name}\n"
                f"Protocol: {rule.protocol_version}\n"
                f"Schema fingerprint: {rule.schema_fingerprint}\n"
            )
        elif policy_command == "set":
            catalog = McpCatalogService(store, client, policy_store).snapshot(refresh=True)
            candidate = next(
                (
                    item
                    for item in catalog.accepted
                    if item.qualified_name == arguments.qualified_name
                ),
                None,
            )
            if candidate is None:
                raise McpToolPolicyError(
                    "MCP policy candidate is not present in the current accepted catalog"
                )
            if candidate.schema_fingerprint != arguments.schema_fingerprint:
                raise McpToolPolicyError("MCP policy schema fingerprint is stale")
            server = store.get_server(candidate.configured_name)
            if server.configuration.transport is not McpTransport.STDIO:
                raise McpToolPolicyError(
                    "remote MCP tools remain dangerous and cannot receive local read-only policy"
                )
            rule = policy_store.set_rule(
                McpToolPolicyRule(
                    qualified_name=candidate.qualified_name,
                    configured_name=candidate.configured_name,
                    server_scope=candidate.scope,
                    configuration_revision=candidate.configuration_revision,
                    remote_name=candidate.remote_name,
                    protocol_version=candidate.protocol_version,
                    schema_fingerprint=candidate.schema_fingerprint,
                    action=PermissionAction(arguments.action),
                ),
                policy_scope=arguments.scope,
                replace_existing=arguments.replace,
                expected_revision=arguments.if_revision,
            )
            stdout.write(
                f"Saved {arguments.scope} MCP tool policy {rule.qualified_name} at revision "
                f"{rule.revision} ({rule.action.value}).\n"
            )
        elif policy_command == "clear":
            policy_store.clear_rule(
                arguments.qualified_name,
                policy_scope=arguments.scope,
                expected_revision=arguments.if_revision,
            )
            stdout.write(f"Removed {arguments.scope} MCP tool policy {arguments.qualified_name}.\n")
    elif command == "oauth":
        oauth = McpOAuthManager.default(environment)
        entry = store.get_server(arguments.name)
        oauth_command = arguments.mcp_oauth_command
        if oauth_command == "begin":
            authorization_url = oauth.begin(entry, arguments.redirect_uri)
            stdout.write(
                "MCP OAuth authorization is pending. Open this URL in a browser, then run "
                "mcp oauth complete with the returned code and state:\n"
                f"{authorization_url}\n"
            )
        elif oauth_command == "complete":
            token = oauth.complete(entry, code=arguments.code, state=arguments.state)
            stdout.write(
                f"MCP OAuth authorization stored for {entry.configuration.name}; "
                f"token revision {token.revision}.\n"
            )
        elif oauth_command == "status":
            status = oauth.status(entry)
            stdout.write(
                f"MCP OAuth: {'configured' if status.configured else 'not configured'}; "
                f"{'pending' if status.pending else 'not pending'}; "
                f"{'authorized' if status.authorized else 'not authorized'}; "
                f"{'expired' if status.expired else 'not expired'}; "
                f"token revision {status.token_revision or 'none'}.\n"
            )
        elif oauth_command == "logout":
            removed = oauth.logout(entry)
            stdout.write(
                f"MCP OAuth state {'removed' if removed else 'was already absent'} for "
                f"{entry.configuration.name}.\n"
            )
    elif command == "resources":
        resource_command = arguments.mcp_resource_command
        entry = store.get_server(
            arguments.name,
            scope=(arguments.scope if resource_command in {"subscribe", "unsubscribe"} else None),
        )
        if resource_command in {"subscribe", "unsubscribe"}:
            current = set(entry.configuration.resource_subscriptions)
            if resource_command == "subscribe":
                current.add(arguments.uri)
            else:
                current.discard(arguments.uri)
            updated = store.set_resource_subscriptions(
                arguments.name,
                scope=arguments.scope,
                subscriptions=tuple(sorted(current)),
                expected_revision=arguments.if_revision,
            )
            stdout.write(
                f"MCP resource subscriptions updated for {arguments.name} at revision "
                f"{updated.configuration.revision}; {len(updated.configuration.resource_subscriptions)} configured.\n"
            )
        else:
            session = client.connect(entry)
            try:
                capability = McpCapabilityClient(session)
                if resource_command == "list":
                    resources = capability.list_resources()
                    stdout.write(f"MCP resources: {len(resources)}\n")
                    for resource in resources:
                        stdout.write(
                            f"  {resource.uri}: {resource.name}; "
                            f"{resource.mime_type or 'unknown MIME'}; "
                            f"{resource.size if resource.size is not None else 'unknown size'}\n"
                        )
                else:
                    resource = capability.read_resource(arguments.uri)
                    stdout.write(
                        "UNTRUSTED MCP RESOURCE DATA\n"
                        f"URI: {resource.uri}\nBlocks: {resource.blocks}\n"
                        f"Truncated: {'yes' if resource.truncated else 'no'}\n"
                        f"{resource.content}\n"
                    )
            finally:
                if not session.close():
                    raise McpClientError(
                        "mcp_cleanup_incomplete", "MCP capability session cleanup is incomplete"
                    )
    elif command == "prompts":
        entry = store.get_server(arguments.name)
        session = client.connect(entry)
        try:
            capability = McpCapabilityClient(session)
            if arguments.mcp_prompt_command == "list":
                prompts = capability.list_prompts()
                stdout.write(f"MCP prompts: {len(prompts)}\n")
                for prompt in prompts:
                    stdout.write(
                        f"  {prompt.name}: {len(prompt.arguments)} argument(s); "
                        f"description {'present' if prompt.description else 'absent'}\n"
                    )
            else:
                values: dict[str, str] = {}
                for raw in arguments.arg:
                    if raw.count("=") != 1:
                        raise McpConfigurationError("MCP prompt argument must be NAME=VALUE")
                    key, value = raw.split("=", 1)
                    if key in values:
                        raise McpConfigurationError("MCP prompt argument is duplicated")
                    values[key] = value
                prompt = capability.get_prompt(arguments.prompt_name, values)
                stdout.write(
                    "UNTRUSTED MCP PROMPT DATA - NOT HOST OR PROJECT INSTRUCTIONS\n"
                    f"Prompt: {prompt.name}\nMessages: {prompt.messages}\n"
                    f"Truncated: {'yes' if prompt.truncated else 'no'}\n"
                    f"{prompt.content}\n"
                )
        finally:
            if not session.close():
                raise McpClientError(
                    "mcp_cleanup_incomplete", "MCP capability session cleanup is incomplete"
                )
    return 0


def render_session_info(info, stdout: TextIO) -> None:
    """Render durable Session metadata without transcript content."""
    stdout.write(f"session name: {info.name}\n")
    stdout.write(f"name source: {info.name_source.value}\n")
    if info.title_fallback_reason is not None:
        stdout.write(
            f"title fallback: {render_session_title_fallback_reason(info.title_fallback_reason)}\n"
        )
    if info.forked_from_session_id is not None:
        stdout.write(
            f"forked from: {info.forked_from_session_id} through turn {info.forked_from_turn}\n"
        )
    stdout.write(f"archived: {'yes' if info.archived else 'no'}\n")
    stdout.write(f"pinned: {'yes' if info.pinned else 'no'}\n")
    stdout.write(f"session ID: {info.session_id}\n")
    stdout.write(f"workspace: {info.workspace}\n")
    stdout.write(f"transcript: {info.path}\n")
    stdout.write(f"created: {info.created_at}\n")
    stdout.write(f"turns: {info.turn_count}\n")
    stdout.write(f"records: {info.record_count}\n")
    stdout.write(f"closed: {'yes' if info.closed else 'no'}\n")
    stdout.write(f"last provider: {info.binding.provider_id}\n")
    stdout.write(f"last model: {info.binding.selected_model or '<none>'}\n")


def handle_session_command(arguments: argparse.Namespace, workspace: Path, stdout: TextIO) -> int:
    """List or inspect validated Session transcripts without taking a writer lease."""
    store = SessionStore(workspace)
    if arguments.session_command == "show":
        render_session_info(store.inspect(arguments.selector), stdout)
        return 0
    if arguments.session_command == "preview":
        stdout.write(
            f"{render_session_preview(store.preview(arguments.selector, arguments.limit))}\n"
        )
        return 0
    if arguments.session_command == "turns":
        stdout.write(
            f"{render_session_turn_range(store.turn_range(arguments.selector, arguments.start_turn, arguments.count))}\n"
        )
        return 0
    if arguments.session_command == "search":
        stdout.write(f"{render_session_search(store.search(arguments.query, arguments.limit))}\n")
        return 0
    if arguments.session_command == "export":
        stdout.write(
            f"{render_session_export(store.conversation_export(arguments.selector), arguments.format)}\n"
        )
        return 0
    if arguments.session_command == "doctor":
        stdout.write(f"{render_session_diagnosis(store.diagnose(arguments.selector))}\n")
        return 0
    if arguments.session_command == "repair":
        stdout.write(f"{render_session_repair(store.repair(arguments.selector))}\n")
        return 0
    if arguments.session_command == "fork":
        writer = store.fork(arguments.selector, arguments.through_turn)
        writer.close(reason="forked")
        render_session_info(writer.info, stdout)
        return 0
    if arguments.session_command == "actions":
        stdout.write(
            f"{render_action_audits(store.action_audits(arguments.selector), arguments.limit)}\n"
        )
        return 0
    if arguments.session_command == "tools":
        stdout.write(
            f"{render_tool_ledgers(store.tool_ledgers(arguments.selector, arguments.limit), details=arguments.details)}\n"
        )
        return 0
    sessions = store.list()
    if not sessions:
        stdout.write("No durable sessions found.\n")
        return 0
    latest_id = store.show("latest").session_id
    for info in sessions:
        stdout.write(f"{render_session_summary(info, latest_session_id=latest_id)}\n")
    return 0


def handle_task_command(arguments: argparse.Namespace, workspace: Path, stdout: TextIO) -> int:
    """Create or inspect durable Tasks without invoking a provider."""
    store = TaskStore(workspace)
    if arguments.task_command == "create":
        defaults = TaskBudget()
        structured_criteria: list[dict[str, object]] = []
        for raw_criterion in arguments.structured_criteria:
            try:
                value = json.loads(raw_criterion)
            except json.JSONDecodeError as error:
                raise TaskStoreError(
                    f"structured acceptance criterion is not valid JSON: {error.msg}"
                ) from None
            if not isinstance(value, dict):
                raise TaskStoreError("structured acceptance criterion must be a JSON object")
            structured_criteria.append(value)
        info = store.create(
            arguments.objective,
            owner_session=arguments.owner_session,
            acceptance_criteria=tuple(arguments.acceptance_criteria),
            structured_criteria=tuple(structured_criteria),
            completion_policy=TaskCompletionPolicy(arguments.completion_policy),
            name=arguments.name,
            parent_task_id=arguments.parent_task_id,
            budget=TaskBudget(
                max_stages=arguments.max_stages or defaults.max_stages,
                max_provider_invocations=(
                    arguments.max_provider_invocations or defaults.max_provider_invocations
                ),
                max_tool_requests=arguments.max_tool_requests or defaults.max_tool_requests,
                max_input_tokens=arguments.max_input_tokens,
                max_output_tokens=arguments.max_output_tokens,
            ),
        )
        stdout.write(f"{render_task_info(info)}\n")
        return 0
    if arguments.task_command == "show":
        stdout.write(f"{render_task_info(store.inspect(arguments.task_id))}\n")
        return 0
    if arguments.task_command == "timeline":
        stdout.write(f"{render_task_timeline(store.inspect(arguments.task_id))}\n")
        return 0
    tasks = store.list()
    if arguments.status is not None:
        tasks = tuple(task for task in tasks if task.status.value == arguments.status)
    if arguments.archive != "all":
        archived = arguments.archive == "archived"
        tasks = tuple(task for task in tasks if task.archived is archived)
    if arguments.name_query is not None:
        query = arguments.name_query.casefold()
        tasks = tuple(task for task in tasks if query in task.name.casefold())
    tasks = tasks[: arguments.limit]
    if not tasks:
        stdout.write("No durable Tasks found.\n")
        return 0
    for info in tasks:
        stdout.write(f"{render_task_summary(info)}\n")
    return 0


def _eval_path(value: str, invocation_workspace: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else invocation_workspace / path


def _render_coding_task_score(arguments: argparse.Namespace, result, stdout: TextIO) -> int:
    rendered = (
        render_coding_task_result_json(result)
        if arguments.format == "json"
        else render_coding_task_result_text(result)
    )
    stdout.write(f"{rendered}\n")
    return 0 if result.passed else 1


def handle_eval_command(
    arguments: argparse.Namespace,
    *,
    invocation_workspace: Path,
    environment: Mapping[str, str],
    user_profile_path: Path | None,
    project_profile_path: Path | None,
    provider_factory,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run deterministic Host Eval or one explicitly opted-in actual coding task."""
    if arguments.eval_command == "list":
        for case in builtin_eval_cases():
            stdout.write(f"{case.case_id}: {case.summary}\n")
        return 0
    if arguments.eval_command == "task":
        if arguments.eval_task_command == "list":
            for task in builtin_coding_tasks():
                stdout.write(f"{task.task_id}: {task.summary}\n")
            return 0
        task = get_coding_task(arguments.task_id)
        if arguments.eval_task_command == "prepare":
            target = _eval_path(arguments.output, invocation_workspace)
            materialize_coding_task(task, target)
            stdout.write(f"Prepared {task.task_id} at {target}.\n")
            return 0
        if arguments.eval_task_command == "score":
            target = _eval_path(arguments.candidate_workspace, invocation_workspace)
            return _render_coding_task_score(
                arguments,
                score_coding_task(task, target, environment=environment),
                stdout,
            )

        event_sink = TerminalEventSink(
            stderr,
            color=color_enabled(stderr, environment),
            stream_deltas=False,
            render_markdown=stderr.isatty(),
        )
        source_project_profile = (
            project_profile_path
            or _store(
                invocation_workspace,
                environment,
                user_profile_path,
                project_profile_path,
            ).project_path
        )

        def execute(target: Path):
            attempt = run_coding_task(
                task,
                target,
                environment=environment,
                profile=arguments.profile,
                profile_id=arguments.invocation_profile_id,
                model=arguments.invocation_model,
                custom_protocol=arguments.invocation_provider_protocol,
                custom_base_url=arguments.invocation_base_url,
                custom_api_key_env=arguments.invocation_api_key_env,
                max_output_tokens=arguments.invocation_max_output_tokens,
                user_profile_path=user_profile_path,
                provider_project_profile_path=source_project_profile,
                provider_factory=provider_factory,
                event_sink=event_sink,
            )
            if attempt.execution_error is not None:
                stderr.write(
                    f"Coding task provider attempt ended with {attempt.execution_error}.\n"
                )
            return _render_coding_task_score(arguments, attempt.result, stdout)

        if arguments.output is not None:
            target = _eval_path(arguments.output, invocation_workspace)
            exit_code = execute(target)
            stderr.write(f"Retained coding task workspace: {target}\n")
            return exit_code
        with TemporaryDirectory(prefix="leonervis-coding-task-") as temporary:
            return execute(Path(temporary) / task.task_id)

    result = run_eval_suite(arguments.selector)
    rendered = (
        render_eval_result_json(result)
        if arguments.format == "json"
        else render_eval_result_text(result)
    )
    stdout.write(f"{rendered}\n")
    return 0 if result.passed else 1


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    user_profile_path: Path | None = None,
    project_profile_path: Path | None = None,
    user_mcp_path: Path | None = None,
    project_mcp_path: Path | None = None,
    user_mcp_policy_path: Path | None = None,
    project_mcp_policy_path: Path | None = None,
    mcp_client_factory=None,
    provider_factory=None,
) -> int:
    """Run a command or launch one persistent project conversation session."""
    arguments = build_parser().parse_args(argv)
    workspace = (
        Path(arguments.workspace).resolve()
        if arguments.workspace
        else (cwd or Path.cwd()).resolve()
    )
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    env = os.environ if environment is None else environment
    factory = provider_factory or create_provider
    custom_requested = any(
        value is not None
        for value in (
            arguments.invocation_provider_protocol,
            arguments.invocation_base_url,
            arguments.invocation_api_key_env,
        )
    )
    try:
        if arguments.command == "eval":
            if arguments.workspace is not None:
                raise EvalError(
                    "eval uses isolated temporary workspaces and does not accept -C/--cwd"
                )
            coding_task_run = (
                arguments.eval_command == "task" and arguments.eval_task_command == "run"
            )
            if arguments.resume is not None:
                raise EvalError("eval does not accept --resume")
            if (
                arguments.permission_mode != PermissionMode.READ_ONLY.value
                or arguments.approval != ApprovalMode.ASK.value
            ):
                raise EvalError("eval controls its own permission and approval policy")
            provider_selected = any(
                value is not None
                for value in (
                    arguments.profile,
                    arguments.invocation_profile_id,
                    arguments.invocation_model,
                )
            )
            if coding_task_run and not provider_selected:
                raise EvalError(
                    "eval task run requires an explicit --profile, --profile-id, or --model"
                )
            if coding_task_run and custom_requested and arguments.invocation_model is None:
                raise EvalError("custom endpoint options require --model")
            if (
                coding_task_run
                and (arguments.profile is not None or arguments.invocation_profile_id is not None)
                and custom_requested
            ):
                raise EvalError("profile selection cannot be combined with custom endpoint options")
            if not coding_task_run and (
                provider_selected
                or arguments.invocation_max_output_tokens is not None
                or custom_requested
            ):
                raise EvalError(
                    "eval is offline and does not accept runtime or provider selection options"
                )
            return handle_eval_command(
                arguments,
                invocation_workspace=workspace,
                environment=env,
                user_profile_path=user_profile_path,
                project_profile_path=project_profile_path,
                provider_factory=factory,
                stdout=output,
                stderr=errors,
            )
        if arguments.resume is not None and arguments.command not in {None, "prompt"}:
            raise ProviderProfileError("--resume is only valid with prompt or interactive mode")
        if arguments.invocation_max_output_tokens is not None and arguments.command not in {
            None,
            "prompt",
        }:
            raise ProviderProfileError(
                "--max-output-tokens is only valid with prompt or interactive mode"
            )
        if arguments.invocation_profile_id is not None:
            arguments.profile = (
                _store(workspace, env, user_profile_path, project_profile_path)
                .get_profile_by_id(arguments.invocation_profile_id)
                .name
            )
        if arguments.profile is not None and custom_requested:
            raise ProviderProfileError("--profile cannot be combined with custom endpoint options")
        if arguments.command == "provider":
            if (
                arguments.profile is not None
                or arguments.invocation_profile_id is not None
                or arguments.invocation_model is not None
                or custom_requested
            ):
                raise ProviderProfileError(
                    "global provider selection options cannot be combined with provider management"
                )
            return handle_provider_command(
                arguments,
                workspace=workspace,
                environment=env,
                user_profile_path=user_profile_path,
                project_profile_path=project_profile_path,
                provider_factory=factory,
                stdout=output,
            )
        if arguments.command == "mcp":
            if (
                arguments.profile is not None
                or arguments.invocation_profile_id is not None
                or arguments.invocation_model is not None
                or custom_requested
            ):
                raise ProviderProfileError(
                    "provider selection options cannot be combined with MCP management"
                )
            return handle_mcp_command(
                arguments,
                workspace=workspace,
                environment=env,
                user_mcp_path=user_mcp_path,
                project_mcp_path=project_mcp_path,
                user_mcp_policy_path=user_mcp_policy_path,
                project_mcp_policy_path=project_mcp_policy_path,
                mcp_client_factory=mcp_client_factory,
                stdout=output,
            )
        if arguments.command == "session":
            if (
                arguments.profile is not None
                or arguments.invocation_model is not None
                or custom_requested
            ):
                raise ProviderProfileError(
                    "provider selection options cannot be combined with session inspection"
                )
            return handle_session_command(arguments, workspace, output)
        if arguments.command == "task":
            if (
                arguments.profile is not None
                or arguments.invocation_model is not None
                or custom_requested
            ):
                raise ProviderProfileError(
                    "provider selection options cannot be combined with task management"
                )
            return handle_task_command(arguments, workspace, output)
        if arguments.command == "demo-read":
            if (
                arguments.profile is not None
                or arguments.invocation_model is not None
                or custom_requested
            ):
                raise ProviderProfileError("demo-read does not accept provider selection options")
            return render_demo_read(workspace, arguments.path, output)
        if arguments.command == "route":
            if arguments.profile is not None:
                configured = _store(
                    workspace, env, user_profile_path, project_profile_path
                ).get_profile(arguments.profile)
                route = resolve_profile_route(
                    configured, environment=env, model_override=arguments.invocation_model
                )
                return render_runtime_route(
                    route,
                    env,
                    output,
                    profile_override=(
                        configured.context_window_tokens
                        if arguments.invocation_model is None
                        else None
                    ),
                    model_max_output_override=(
                        configured.model_max_output_tokens
                        if arguments.invocation_model is None
                        else None
                    ),
                )
            if arguments.invocation_model is not None:
                route = resolve_runtime_route(
                    arguments.invocation_model,
                    environment=env,
                    custom_protocol=arguments.invocation_provider_protocol,
                    custom_base_url=arguments.invocation_base_url,
                    custom_api_key_env=arguments.invocation_api_key_env,
                )
                return render_runtime_route(route, env, output)
            if custom_requested:
                raise ProviderProfileError("custom endpoint options require --model")
            return render_route(
                model=arguments.route_model,
                fallback_models=arguments.fallback_model,
                require_tool_use=arguments.require_tool_use,
                require_streaming=arguments.require_streaming,
                max_output_tokens=arguments.max_output_tokens,
                temperature=arguments.temperature,
                stdout=output,
                stderr=errors,
            )
        if custom_requested and (
            arguments.command != "prompt" or arguments.invocation_model is None
        ):
            raise ProviderProfileError(
                "custom endpoint options require --model and the prompt command"
            )
        if arguments.command is None:
            input_stream = stdin or sys.stdin
            if not input_stream.isatty() or not output.isatty():
                print(
                    'interactive mode requires a terminal; use leonervis-code prompt "..." instead',
                    file=errors,
                )
                return 2
        input_stream = stdin or sys.stdin
        frontend_queue: FrontendEventQueue | None = None
        approval_broker: TerminalApprovalBroker | None = None
        if arguments.command is None and supports_terminal_application(input_stream, output):
            frontend_queue = FrontendEventQueue()
            approval_broker = TerminalApprovalBroker(
                lambda turn_id, request: frontend_queue.put(ApprovalPending(turn_id, request))
            )
            approval_handler = approval_broker
        elif arguments.command is None:
            approval_handler = terminal_approval_handler(
                input_stream,
                output,
                color=color_enabled(output, env),
            )
        else:
            approval_handler = noninteractive_approval
        session = ProjectSession.open(
            workspace,
            resume=arguments.resume,
            profile=arguments.profile,
            model=arguments.invocation_model,
            custom_protocol=arguments.invocation_provider_protocol,
            custom_base_url=arguments.invocation_base_url,
            custom_api_key_env=arguments.invocation_api_key_env,
            max_output_tokens=arguments.invocation_max_output_tokens,
            environment=env,
            user_profile_path=user_profile_path,
            project_profile_path=project_profile_path,
            user_mcp_path=user_mcp_path,
            project_mcp_path=project_mcp_path,
            user_mcp_policy_path=user_mcp_policy_path,
            project_mcp_policy_path=project_mcp_policy_path,
            provider_factory=factory,
            read_file_factory=ReadFileTool,
            glob_factory=GlobTool,
            grep_factory=GrepTool,
            list_directory_factory=ListDirectoryTool,
            write_file_factory=WriteFileTool,
            edit_file_factory=EditFileTool,
            run_command_factory=RunCommandTool,
            mkdir_factory=MkdirTool,
            move_file_factory=MoveFileTool,
            copy_file_factory=CopyFileTool,
            delete_file_factory=DeleteFileTool,
            delete_directory_factory=DeleteDirectoryTool,
            web_search_factory=WebSearchTool,
            permission_mode=PermissionMode(arguments.permission_mode),
            approval_mode=ApprovalMode(arguments.approval),
            approval_handler=approval_handler,
        )
        try:
            resume_result = session.startup_resume_result
            if resume_result is not None:
                message, _ = render_session_resume(resume_result)
                print(message, file=errors)
            if arguments.command == "prompt":
                event_sink = TerminalEventSink(
                    errors,
                    color=color_enabled(errors, env),
                    stream_deltas=False,
                    render_markdown=errors.isatty(),
                )
                try:
                    response = session.prompt(arguments.prompt, event_sink=event_sink)
                except KeyboardInterrupt:
                    event_sink.abort_stream()
                    print("generation cancelled; no turn was committed", file=errors)
                    return 130
                except BaseException:
                    event_sink.abort_stream()
                    raise
                if output.isatty():
                    write_markdown_document(
                        output,
                        response,
                        color=color_enabled(output, env),
                    )
                else:
                    print(response, file=output)
                return 0
            return run_repl(
                session,
                stdin=input_stream,
                stdout=output,
                version=__version__,
                cwd=workspace,
                color=color_enabled(output, env),
                render_markdown=output.isatty(),
                frontend_queue=frontend_queue,
                approval_broker=approval_broker,
            )
        finally:
            session.close()
    except RuntimeProviderStateError as error:
        print(f"provider runtime state error: {error}", file=errors)
        return 2
    except RuntimeRouteError as error:
        print(f"provider route error: {error}", file=errors)
        return 2
    except ContextPreflightError as error:
        print(f"context preflight error: {error}", file=errors)
        return 2
    except ProviderAdapterError as error:
        return render_provider_failure(error, errors)
    except ProviderProfileError as error:
        print(f"provider profile error: {error}", file=errors)
        return 2
    except EvalError as error:
        print(f"eval error: {error}", file=errors)
        return 2
    except SessionResumeContextError as error:
        print(render_resume_rejection(error.report, startup=True), file=errors)
        return 2
    except SessionResumeConflictError as error:
        print(f"session resume conflict: {error}", file=errors)
        return 2
    except SessionResumeCommitError as error:
        print(f"session resume commit error [{error.stage.value}]: {error}", file=errors)
        return 2
    except (ApprovalGrantError, ActionIdentityChangedError) as error:
        print(f"action authorization error: {error}", file=errors)
        return 2
    except SessionStoreError as error:
        print(f"session error: {error}", file=errors)
        return 2
    except TaskStoreError as error:
        print(f"task error: {error}", file=errors)
        return 2
    except McpConfigurationError as error:
        print(f"MCP configuration error: {error}", file=errors)
        return 2
    except McpToolPolicyError as error:
        print(f"MCP tool policy error: {error}", file=errors)
        return 2
    except McpOAuthError as error:
        print(f"MCP OAuth error: {error}", file=errors)
        return 2
    except McpClientError as error:
        cleanup = " cleanup incomplete" if not error.cleanup_complete else ""
        print(f"MCP client error [{error.code}]{cleanup}: {error}", file=errors)
        return 2
