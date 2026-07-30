"""Slash-command dispatch independent from terminal streams and ANSI rendering."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Protocol

from leonervis_code.cli.presentation import (
    DEFAULT_ACTION_AUDIT_COUNT,
    DEFAULT_COMPACTION_HISTORY_COUNT,
    DEFAULT_SESSION_LIST_COUNT,
    DEFAULT_SESSION_PREVIEW_TURNS,
    DEFAULT_SESSION_SEARCH_MATCHES,
    DEFAULT_TOOL_LEDGER_COUNT,
    HELP_TEXT,
    HELP_BY_TOPIC,
    HELP_TOPICS,
    MAX_ACTION_AUDIT_COUNT,
    MAX_COMPACTION_HISTORY_COUNT,
    MAX_SESSION_LIST_COUNT,
    MAX_SESSION_PREVIEW_TURNS,
    MAX_TOOL_LEDGER_COUNT,
    PROVIDER_HELP,
    SESSION_HELP,
    TASK_HELP,
    MessageKind,
    ToolDetailMode,
    render_compact_result,
    render_action_audits,
    render_context_inspection,
    render_compact_preview,
    render_compaction_history,
    render_command_sandbox_inspection,
    render_git_diff,
    render_git_log,
    render_git_show,
    render_git_status,
    render_provider_adapter_error,
    render_project_status,
    render_project_instructions_inspection,
    render_permission_matrix,
    render_output_budget,
    render_output_budget_rejection,
    render_output_budget_update,
    render_recent_history,
    render_runtime_status,
    render_runtime_switch,
    render_resume_rejection,
    render_session_info,
    render_session_diagnosis,
    render_session_export,
    render_session_preview,
    render_session_repair,
    render_session_search,
    render_session_resume,
    render_session_summary,
    render_session_turn_range,
    render_task_info,
    render_task_summary,
    render_switch_rejection,
    render_tool_ledgers,
    render_tool_catalog,
    render_durable_usage_summary,
    render_usage_summary,
)
from leonervis_code.core.compaction import CompactionError
from leonervis_code.core.permissions import ApprovalMode, PermissionMode
from leonervis_code.cli.failure_guidance import command_failure_guidance
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.manager import (
    RuntimeProviderStateError,
    RuntimeSwitchAuditError,
    RuntimeSwitchContextError,
)
from leonervis_code.providers.profile import ProviderProfileError
from leonervis_code.providers.profile import MAX_MODEL_OUTPUT_TOKENS
from leonervis_code.providers.resolver import RuntimeRouteError
from leonervis_code.session import SessionResumeConflictError, SessionResumeContextError
from leonervis_code.session_store import SessionResumeCommitError, SessionStoreError
from leonervis_code.session_records import (
    ActionAuditStatus,
    SessionRecordError,
    canonical_session_id,
)
from leonervis_code.task_records import TaskRecordError, canonical_task_id
from leonervis_code.tools.git_repository import GitObservationError
from leonervis_code.tools.git_log import DEFAULT_GIT_LOG_LIMIT, MAX_GIT_LOG_LIMIT
from leonervis_code.tools.catalog import TOOL_CATALOG

TOP_LEVEL_COMMANDS = (
    "/help",
    "/history",
    "/actions",
    "/tools",
    "/tool-details",
    "/changes",
    "/commit",
    "/commits",
    "/exit",
    "/quit",
    "/status",
    "/permissions",
    "/sandbox",
    "/context",
    "/instructions",
    "/usage",
    "/output",
    "/compact",
    "/compactions",
    "/provider",
    "/model",
    "/session",
    "/task",
    "/resume",
    "/clear",
)


@dataclass(frozen=True)
class SlashCompletionSpec:
    """One static Host command completion and its terminal-only description."""

    text: str
    description: str
    top_level: bool = False


SLASH_COMPLETIONS = (
    SlashCompletionSpec("/help", "Show Host commands", True),
    SlashCompletionSpec("/help session", "Session history, browsing, and resume"),
    SlashCompletionSpec("/help task", "Durable Task identity and inspection"),
    SlashCompletionSpec("/help tools", "Action Audit and tool outcomes"),
    SlashCompletionSpec("/help git", "Read-only Git observation"),
    SlashCompletionSpec("/help context", "Context, usage, and compaction"),
    SlashCompletionSpec("/help provider", "Provider and model selection"),
    SlashCompletionSpec("/help policy", "Permission, approval, and command sandbox"),
    SlashCompletionSpec("/help input", "Prompt editor controls"),
    SlashCompletionSpec("/history", "Show recent Session turns", True),
    SlashCompletionSpec("/actions", "Show recent Action Audit", True),
    SlashCompletionSpec("/tools", "Show durable tool ledgers", True),
    SlashCompletionSpec("/changes", "Show Git working changes", True),
    SlashCompletionSpec("/changes unstaged", "Show unstaged tracked patch"),
    SlashCompletionSpec("/changes staged", "Show staged tracked patch"),
    SlashCompletionSpec("/commit", "Show one reachable Git commit", True),
    SlashCompletionSpec("/commits", "Show recent reachable Git commits", True),
    SlashCompletionSpec("/status", "Show runtime status", True),
    SlashCompletionSpec("/permissions", "Inspect or preview permission policy", True),
    SlashCompletionSpec("/permissions read-only", "Preview read-only policy"),
    SlashCompletionSpec("/permissions read-only ask", "Preview read-only with approvals"),
    SlashCompletionSpec("/permissions read-only auto", "Preview read-only with auto approval"),
    SlashCompletionSpec("/permissions workspace-write", "Preview workspace-write policy"),
    SlashCompletionSpec(
        "/permissions workspace-write ask", "Preview workspace-write with approvals"
    ),
    SlashCompletionSpec(
        "/permissions workspace-write auto", "Preview workspace-write with auto approval"
    ),
    SlashCompletionSpec("/permissions danger-full-access", "Preview danger-full-access policy"),
    SlashCompletionSpec(
        "/permissions danger-full-access ask", "Preview danger-full-access with approvals"
    ),
    SlashCompletionSpec(
        "/permissions danger-full-access auto", "Preview danger-full-access with auto approval"
    ),
    SlashCompletionSpec("/sandbox", "Command sandbox diagnostics", True),
    SlashCompletionSpec("/sandbox check", "Verify command sandbox activation"),
    SlashCompletionSpec("/context", "Inspect Effective Context", True),
    SlashCompletionSpec("/instructions", "Inspect root AGENTS.md metadata", True),
    SlashCompletionSpec("/usage", "Show provider token usage", True),
    SlashCompletionSpec("/usage session", "Show durable Session usage"),
    SlashCompletionSpec("/usage turns", "Show recent durable turn usage"),
    SlashCompletionSpec("/output", "Inspect or change output budget", True),
    SlashCompletionSpec("/compact", "Compact earlier complete turns", True),
    SlashCompletionSpec("/compactions", "Show durable compaction history", True),
    SlashCompletionSpec("/provider", "Provider commands", True),
    SlashCompletionSpec("/model", "Override the current model", True),
    SlashCompletionSpec("/session", "Session commands", True),
    SlashCompletionSpec("/task", "Task commands", True),
    SlashCompletionSpec("/resume", "Resume a Session", True),
    SlashCompletionSpec("/clear", "Clear terminal output", True),
    SlashCompletionSpec("/exit", "Exit the REPL", True),
    SlashCompletionSpec("/quit", "Exit the REPL", True),
    SlashCompletionSpec("/provider list", "List provider profiles"),
    SlashCompletionSpec("/provider current", "Show the current provider"),
    SlashCompletionSpec("/provider use", "Use a workspace provider profile"),
    SlashCompletionSpec("/session show", "Show current or selected Session metadata"),
    SlashCompletionSpec("/session preview", "Preview recent turns without switching"),
    SlashCompletionSpec("/session turns", "Show a specific complete-turn range"),
    SlashCompletionSpec("/session search", "Search final dialogue across Sessions"),
    SlashCompletionSpec("/session export", "Export final dialogue as Markdown or JSON"),
    SlashCompletionSpec("/session fork", "Fork complete turns into a new Session"),
    SlashCompletionSpec("/session doctor", "Diagnose one Session transcript"),
    SlashCompletionSpec("/session repair", "Repair one incomplete transcript tail"),
    SlashCompletionSpec("/session list", "Browse and filter workspace Sessions"),
    SlashCompletionSpec("/session new", "Start an empty Session"),
    SlashCompletionSpec("/session rename", "Rename the current Session"),
    SlashCompletionSpec("/session archive", "Archive the current Session"),
    SlashCompletionSpec("/session unarchive", "Unarchive the current Session"),
    SlashCompletionSpec("/session pin", "Pin the current Session"),
    SlashCompletionSpec("/session unpin", "Unpin the current Session"),
    SlashCompletionSpec("/session switch", "Build or use a recent Session picker"),
    SlashCompletionSpec("/session switch list", "Refresh the Session picker with filters"),
    SlashCompletionSpec("/task start", "Create a Task owned by the current Session"),
    SlashCompletionSpec("/task list", "List workspace Tasks"),
    SlashCompletionSpec("/task show", "Show one workspace Task"),
    SlashCompletionSpec("/tools details", "Show per-request ledger outcomes"),
    SlashCompletionSpec("/tools catalog", "Show tool permissions and availability"),
    SlashCompletionSpec("/actions last", "Show the most recent Action Audit"),
    SlashCompletionSpec("/tool-details", "Show live tool detail mode", True),
    SlashCompletionSpec("/tool-details compact", "Use compact live tool lines"),
    SlashCompletionSpec("/tool-details full", "Show bounded structured tool details"),
    SlashCompletionSpec("/compact preview", "Preview fixed compaction selection"),
    *tuple(
        SlashCompletionSpec(
            f"/actions status={status.value}",
            f"Filter actions by {status.value}",
        )
        for status in ActionAuditStatus
    ),
    *tuple(
        SlashCompletionSpec(
            f"/actions tool={tool.name}",
            f"Filter actions for {tool.name}",
        )
        for tool in TOOL_CATALOG
    ),
    *tuple(
        SlashCompletionSpec(f"/tools catalog {tool.name}", f"Inspect {tool.name}")
        for tool in TOOL_CATALOG
    ),
)


class ReplSession(Protocol):
    turns: tuple

    def action_audits(self): ...

    def tool_ledgers(self, limit: int): ...

    def git_status(self): ...

    def git_diff(self, scope: str): ...

    def git_log(self, limit: int, path: str): ...

    def git_show(self, commit_id: str, path: str): ...

    def status(self): ...

    def project_status(self): ...

    def inspect_command_sandbox(self): ...

    def inspect_context(self): ...

    def inspect_project_instructions(self): ...

    def usage(self): ...

    def session_usage(self): ...

    def turn_usage_history(self, limit: int = 10): ...

    def set_output_budget(self, max_output_tokens: int | None): ...

    def compact_context(self): ...

    def preview_compaction(self): ...

    def compaction_history(self, limit: int): ...

    def session_info(self): ...

    def latest_session_info(self): ...

    def inspect_session(self, selector: str): ...

    def preview_session(self, selector: str, limit: int): ...

    def session_turn_range(self, selector: str, start_turn: int, count: int): ...

    def search_sessions(self, query: str, limit: int): ...

    def export_session(self, selector: str): ...

    def fork_session(self, selector: str, through_turn: int): ...

    def diagnose_session(self, selector: str): ...

    def repair_session(self, selector: str): ...

    def list_sessions(self): ...

    def create_task(self, objective: str, acceptance_criteria: tuple[str, ...] = ()): ...

    def list_tasks(self): ...

    def inspect_task(self, task_id: str): ...

    def rename_session(self, name: str | None = None): ...

    def set_session_archived(self, archived: bool): ...

    def set_session_pinned(self, pinned: bool): ...

    def new_session(self): ...

    def switch_session(self, selector: str): ...

    def list_profiles(self): ...

    def use_profile(self, name: str, *, scope: str): ...

    def set_model(self, model: str): ...


@dataclass(frozen=True)
class SlashResult:
    """One stream-independent result from slash-command dispatch."""

    handled: bool
    exit: bool = False
    message: str | None = None
    kind: MessageKind = "plain"
    clear_screen: bool = False


_NOT_HANDLED = SlashResult(handled=False)
DEFAULT_SESSION_SWITCH_COUNT = 10
MAX_SESSION_SWITCH_COUNT = 20


@dataclass
class ToolDetailSettings:
    """Mutable process-local REPL presentation state."""

    mode: ToolDetailMode = ToolDetailMode.COMPACT


@dataclass
class SessionSwitchCatalog:
    """Process-local mapping from displayed picker numbers to exact Session IDs."""

    session_ids: tuple[str, ...] = ()

    def replace(self, session_ids: tuple[str, ...]) -> None:
        self.session_ids = session_ids

    def clear(self) -> None:
        self.session_ids = ()

    def consume(self, number: int) -> str | None:
        session_ids = self.session_ids
        self.clear()
        if not 1 <= number <= len(session_ids):
            return None
        return session_ids[number - 1]


def dispatch_slash(
    command: str,
    session: ReplSession,
    *,
    tool_details: ToolDetailSettings | None = None,
    session_switch: SessionSwitchCatalog | None = None,
) -> SlashResult:
    """Dispatch one exact slash command without writing terminal output."""
    if not command.startswith("/") or "\n" in command or "\r" in command:
        return _NOT_HANDLED
    if command in {"/exit", "/quit"}:
        return SlashResult(handled=True, exit=True)
    if command.startswith("/exit ") or command.startswith("/quit "):
        name = command.split(maxsplit=1)[0]
        return _usage(f"Usage: {name}")
    if command == "/help":
        return _info(HELP_TEXT)
    if command.startswith("/help "):
        topic = command.removeprefix("/help ")
        if topic in HELP_BY_TOPIC:
            return _info(HELP_BY_TOPIC[topic])
        return _usage(f"Usage: /help [{'|'.join(HELP_TOPICS)}]")
    if command == "/clear":
        return SlashResult(handled=True, clear_screen=True)
    if command.startswith("/clear "):
        return _usage("Usage: /clear")
    if command == "/session":
        return _info(SESSION_HELP)
    if command == "/provider":
        return _info(PROVIDER_HELP)
    if command == "/status":
        return _call(lambda: render_project_status(session.project_status()), kind="info")
    if command.startswith("/status "):
        return _usage("Usage: /status")
    if command == "/permissions" or command.startswith("/permissions "):
        return _permissions(command, session)
    if command == "/sandbox" or command == "/sandbox check":
        return _call(
            lambda: render_command_sandbox_inspection(session.inspect_command_sandbox()),
            kind="info",
            failure_prefix="Sandbox check failed",
        )
    if command.startswith("/sandbox "):
        return _usage("Usage: /sandbox check")
    if command == "/context":
        try:
            message, kind = render_context_inspection(session.inspect_context())
            return SlashResult(handled=True, message=message, kind=kind)
        except Exception as error:
            return _command_error(error, failure_prefix="Context inspection failed")
    if command.startswith("/context "):
        return _usage("Usage: /context")
    if command == "/instructions":
        return _call(
            lambda: render_project_instructions_inspection(session.inspect_project_instructions()),
            kind="info",
            failure_prefix="Project instruction inspection failed",
        )
    if command.startswith("/instructions "):
        return _usage("Usage: /instructions")
    if command == "/usage":
        return _call(lambda: render_usage_summary(session.usage()), kind="info")
    if command == "/usage session":
        return _call(
            lambda: render_durable_usage_summary(session.session_usage()),
            kind="info",
        )
    if command == "/usage turns":
        return _call(
            lambda: render_durable_usage_summary(
                session.turn_usage_history(),
                turns=True,
            ),
            kind="info",
        )
    if command.startswith("/usage "):
        return _usage("Usage: /usage [session|turns]")
    if command == "/tool-details" or command.startswith("/tool-details "):
        return _tool_details(command, tool_details)
    if command == "/changes":
        return _call(lambda: render_git_status(session.git_status()), kind="info")
    if command in {"/changes unstaged", "/changes staged"}:
        scope = command.split()[1]
        return _call(lambda: render_git_diff(session.git_diff(scope)), kind="plain")
    if command.startswith("/changes "):
        return _usage("Usage: /changes [unstaged|staged]")
    if command == "/commits" or command.startswith("/commits "):
        return _commits(command, session)
    if command == "/commit" or command.startswith("/commit "):
        return _commit(command, session)
    if command == "/output" or command.startswith("/output "):
        return _output(command, session)
    if command == "/compact preview":
        try:
            message, kind = render_compact_preview(session.preview_compaction())
            return SlashResult(handled=True, message=message, kind=kind)
        except Exception as error:
            return _command_error(error, failure_prefix="Compaction preview failed")
    if command == "/compact":
        try:
            return SlashResult(
                handled=True,
                message=render_compact_result(session.compact_context()),
                kind="success",
            )
        except Exception as error:
            result = _command_error(error, failure_prefix="Compaction failed")
            suffix = " Full history and effective context are unchanged."
            return SlashResult(
                handled=True,
                message=f"{result.message}{suffix}",
                kind=result.kind,
            )
    if command.startswith("/compact "):
        return _usage("Usage: /compact | /compact preview")
    if command == "/compactions" or command.startswith("/compactions "):
        return _compactions(command, session)
    if command == "/history" or command.startswith("/history "):
        return _history(command, session)
    if command == "/actions" or command.startswith("/actions "):
        return _actions(command, session)
    if command == "/tools" or command.startswith("/tools "):
        return _tools(command, session)
    if command == "/task":
        return SlashResult(handled=True, message=TASK_HELP, kind="info")
    if command == "/task start" or command.startswith("/task start "):
        return _task_start(command, session)
    if command == "/task list" or command.startswith("/task list "):
        return _task_list(command, session)
    if command == "/task show" or command.startswith("/task show "):
        return _task_show(command, session)
    if command.startswith("/task "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(subcommand, ("start", "list", "show"))
        return _usage(
            "Unknown task command: "
            f"{subcommand}{_suggestion_line(suggestion)}\nUsage: /task <start|list|show>"
        )
    if command == "/session show" or command.startswith("/session show "):
        return _session_show(command, session)
    if command == "/session preview" or command.startswith("/session preview "):
        return _session_preview(command, session)
    if command == "/session turns" or command.startswith("/session turns "):
        return _session_turns(command, session)
    if command == "/session search" or command.startswith("/session search "):
        return _session_search(command, session)
    if command == "/session export" or command.startswith("/session export "):
        return _session_export(command, session)
    if command == "/session doctor" or command.startswith("/session doctor "):
        return _session_doctor(command, session)
    if command == "/session repair" or command.startswith("/session repair "):
        if session_switch is not None:
            session_switch.clear()
        return _session_repair(command, session)
    if command == "/session fork" or command.startswith("/session fork "):
        if session_switch is not None:
            session_switch.clear()
        return _session_fork(command, session)
    if command == "/session list" or command.startswith("/session list "):
        return _session_list(command, session)
    if command == "/session switch" or command.startswith("/session switch "):
        return _session_switch(command, session, session_switch)
    if command == "/session new" or command.startswith("/session new "):
        if command != "/session new":
            return _usage("Usage: /session new")
        if session_switch is not None:
            session_switch.clear()
        return _new_session(session)
    if command == "/session rename" or command.startswith("/session rename "):
        if session_switch is not None:
            session_switch.clear()
        return _rename_session(command, session)
    if command in {"/session archive", "/session unarchive"}:
        if session_switch is not None:
            session_switch.clear()
        return _archive_session(session, archived=command == "/session archive")
    if command.startswith("/session archive ") or command.startswith("/session unarchive "):
        return _usage("Usage: /session archive | /session unarchive")
    if command in {"/session pin", "/session unpin"}:
        if session_switch is not None:
            session_switch.clear()
        return _pin_session(session, pinned=command == "/session pin")
    if command.startswith("/session pin ") or command.startswith("/session unpin "):
        return _usage("Usage: /session pin | /session unpin")
    if command.startswith("/session "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand,
            (
                "show",
                "preview",
                "turns",
                "search",
                "export",
                "fork",
                "doctor",
                "repair",
                "list",
                "new",
                "rename",
                "archive",
                "unarchive",
                "pin",
                "unpin",
                "switch",
            ),
        )
        return _usage(
            "Unknown session command: "
            f"{subcommand}{_suggestion_line(suggestion)}\nUsage: "
            "/session <show|preview|turns|search|export|fork|doctor|repair|list|new|rename|archive|unarchive|pin|unpin|switch>"
        )
    if command == "/resume" or command.startswith("/resume "):
        if session_switch is not None:
            session_switch.clear()
        return _resume(command, session)
    if command == "/provider list" or command.startswith("/provider list "):
        if command != "/provider list":
            return _usage("Usage: /provider list")
        return _provider_list(session)
    if command == "/provider current" or command.startswith("/provider current "):
        if command != "/provider current":
            return _usage("Usage: /provider current")
        return _call(lambda: render_runtime_status(session.status()), kind="info")
    if command == "/provider use" or command.startswith("/provider use "):
        return _provider_use(command, session)
    if command.startswith("/provider "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(subcommand, ("list", "current", "use"))
        return _usage(
            f"Unknown provider command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Usage: /provider <list|current|use>"
        )
    if command == "/model" or command.startswith("/model "):
        return _model(command, session)
    token = command.split(maxsplit=1)[0]
    suggestion = _suggest_token(token, TOP_LEVEL_COMMANDS)
    if suggestion is not None:
        return _usage(
            f"Unknown command: {command}.\nDid you mean {suggestion}?\nType /help for controls."
        )
    return _usage(f"Unknown command: {command}. Type /help for controls.")


def _permissions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    permission_mode: PermissionMode | None = None
    approval_mode: ApprovalMode | None = None
    if len(parts) in {2, 3}:
        try:
            permission_mode = PermissionMode(parts[1])
            if len(parts) == 3:
                approval_mode = ApprovalMode(parts[2])
        except ValueError:
            return _permissions_usage()
    elif len(parts) != 1:
        return _permissions_usage()
    return _call(
        lambda: render_permission_matrix(
            session.project_status(),
            permission_mode=permission_mode,
            approval_mode=approval_mode,
        ),
        kind="info",
    )


def _permissions_usage() -> SlashResult:
    return _usage(
        "Usage: /permissions | /permissions "
        "<read-only|workspace-write|danger-full-access> [ask|auto]"
    )


def _suggest_token(token: str, candidates: tuple[str, ...]) -> str | None:
    if not token or len(token) > 64 or any(character.isspace() for character in token):
        return None
    matches = get_close_matches(token, candidates, n=1, cutoff=0.7)
    return matches[0] if matches else None


def _suggestion_line(suggestion: str | None) -> str:
    return f"\nDid you mean {suggestion}?" if suggestion is not None else ""


def _history(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or int(parts[1]) <= 0:
        return _usage("Usage: /history <positive integer>")
    return _call(lambda: render_recent_history(session.turns, int(parts[1])))


def _commits(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=2)
    if len(parts) == 1:
        limit = DEFAULT_GIT_LOG_LIMIT
        path = "."
    elif len(parts) >= 2 and parts[1].isascii() and parts[1].isdigit():
        limit = int(parts[1])
        path = parts[2] if len(parts) == 3 else "."
    else:
        return _usage(f"Usage: /commits [1-{MAX_GIT_LOG_LIMIT}] [path]")
    if not 1 <= limit <= MAX_GIT_LOG_LIMIT or not path:
        return _usage(f"Usage: /commits [1-{MAX_GIT_LOG_LIMIT}] [path]")
    return _call(lambda: render_git_log(session.git_log(limit, path)), kind="info")


def _commit(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=2)
    if len(parts) not in {2, 3}:
        return _usage("Usage: /commit <full-commit-id> [path]")
    commit_id = parts[1]
    path = parts[2] if len(parts) == 3 else "."
    if not path:
        return _usage("Usage: /commit <full-commit-id> [path]")
    return _call(lambda: render_git_show(session.git_show(commit_id, path)), kind="plain")


def _actions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if parts == ["/actions", "last"]:
        return _call(lambda: render_action_audits(session.action_audits(), 1), kind="info")
    count = DEFAULT_ACTION_AUDIT_COUNT
    status_filter: str | None = None
    tool_filter: str | None = None
    count_seen = False
    valid_statuses = {status.value for status in ActionAuditStatus}
    for argument in parts[1:]:
        if argument.isascii() and argument.isdigit() and not count_seen:
            count = int(argument)
            count_seen = True
        elif argument.startswith("status=") and status_filter is None:
            status_filter = argument.removeprefix("status=")
        elif argument.startswith("tool=") and tool_filter is None:
            tool_filter = argument.removeprefix("tool=")
        else:
            return _actions_usage()
    if not 1 <= count <= MAX_ACTION_AUDIT_COUNT:
        return _actions_usage()
    if status_filter is not None and status_filter not in valid_statuses:
        return _actions_usage()
    if tool_filter is not None and (not tool_filter or len(tool_filter) > 64):
        return _actions_usage()

    def render() -> str:
        audits = session.action_audits()
        if status_filter is not None:
            audits = tuple(audit for audit in audits if audit.status.value == status_filter)
        if tool_filter is not None:
            audits = tuple(audit for audit in audits if audit.identity.tool_name == tool_filter)
        if not audits and (status_filter is not None or tool_filter is not None):
            return "No action audits match the selected filters."
        return render_action_audits(audits, count)

    return _call(render, kind="info")


def _actions_usage() -> SlashResult:
    return _usage(
        f"Usage: /actions last | /actions [1-{MAX_ACTION_AUDIT_COUNT}] "
        "[status=<status>] [tool=<name>]"
    )


def _tools(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if parts == ["/tools", "catalog"]:
        return _call(lambda: render_tool_catalog(session.project_status()), kind="info")
    if len(parts) == 3 and parts[:2] == ["/tools", "catalog"]:
        tool_name = parts[2]
        if tool_name not in {definition.name for definition in TOOL_CATALOG}:
            suggestion = _suggest_token(tool_name, tuple(tool.name for tool in TOOL_CATALOG))
            return _usage(
                f"Unknown model-visible tool: {tool_name}{_suggestion_line(suggestion)}\n"
                "Usage: /tools catalog [tool-name]"
            )
        return _call(
            lambda: render_tool_catalog(session.project_status(), tool_name),
            kind="info",
        )
    details = False
    if len(parts) == 1:
        count = DEFAULT_TOOL_LEDGER_COUNT
    elif len(parts) == 2 and parts[1] == "details":
        count = DEFAULT_TOOL_LEDGER_COUNT
        details = True
    elif (
        len(parts) == 2
        and parts[1].isascii()
        and parts[1].isdigit()
        and 1 <= int(parts[1]) <= MAX_TOOL_LEDGER_COUNT
    ):
        count = int(parts[1])
    elif (
        len(parts) == 3
        and parts[1] == "details"
        and parts[2].isascii()
        and parts[2].isdigit()
        and 1 <= int(parts[2]) <= MAX_TOOL_LEDGER_COUNT
    ):
        count = int(parts[2])
        details = True
    else:
        return _usage(
            f"Usage: /tools catalog [tool-name] | /tools [1-{MAX_TOOL_LEDGER_COUNT}] | "
            f"/tools details [1-{MAX_TOOL_LEDGER_COUNT}]"
        )
    return _call(
        lambda: render_tool_ledgers(session.tool_ledgers(count), details=details),
        kind="info",
    )


def _tool_details(command: str, settings: ToolDetailSettings | None) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        if settings is None:
            return _usage("Usage: /tool-details <compact|full>")
        return _info(f"Live tool details: {settings.mode.value} (process-local).")
    if len(parts) != 2 or parts[1] not in {"compact", "full"}:
        return _usage("Usage: /tool-details <compact|full>")
    if settings is None:
        return _usage("Usage: /tool-details <compact|full>")
    settings.mode = ToolDetailMode(parts[1])
    if settings.mode == ToolDetailMode.FULL:
        return SlashResult(
            handled=True,
            message=(
                "Live tool details: full (process-local). Future command starts show bounded "
                "structured argv, which may contain sensitive values; file, edit, patch, and "
                "search contents remain redacted."
            ),
            kind="warning",
        )
    return _info("Live tool details: compact (process-local).")


def _compactions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        count = DEFAULT_COMPACTION_HISTORY_COUNT
    elif (
        len(parts) == 2
        and parts[1].isascii()
        and parts[1].isdigit()
        and 1 <= int(parts[1]) <= MAX_COMPACTION_HISTORY_COUNT
    ):
        count = int(parts[1])
    else:
        return _usage(f"Usage: /compactions [1-{MAX_COMPACTION_HISTORY_COUNT}]")
    return _call(
        lambda: render_compaction_history(session.compaction_history(count)),
        kind="info",
    )


def _output(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        message, kind = render_output_budget(session.status())
        return SlashResult(handled=True, message=message, kind=kind)
    if len(parts) != 2:
        return _usage("Usage: /output [1-100000000|reset]")
    if parts[1] == "reset":
        value = None
    elif (
        parts[1].isascii() and parts[1].isdigit() and 1 <= int(parts[1]) <= MAX_MODEL_OUTPUT_TOKENS
    ):
        value = int(parts[1])
    else:
        return _usage("Usage: /output [1-100000000|reset]")
    try:
        message, kind = render_output_budget_update(session.set_output_budget(value))
        return SlashResult(handled=True, message=message, kind=kind)
    except RuntimeSwitchContextError as error:
        return SlashResult(
            handled=True,
            message=render_output_budget_rejection(error.report),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Output budget change failed")


@dataclass(frozen=True)
class _SessionFilters:
    count: int
    state: str | None = None
    archive: str | None = None
    pin: str | None = None
    model: str | None = None
    name: str | None = None


def _parse_session_filters(
    arguments: list[str],
    *,
    default_count: int,
    maximum_count: int,
) -> _SessionFilters | None:
    count = default_count
    state: str | None = None
    archive: str | None = None
    pin: str | None = None
    model: str | None = None
    name: str | None = None
    count_seen = False
    for argument in arguments:
        if argument.isascii() and argument.isdigit() and not count_seen:
            count = int(argument)
            count_seen = True
        elif argument in {"open", "closed"} and state is None:
            state = argument
        elif argument in {"active", "archived"} and archive is None:
            archive = argument
        elif argument in {"pinned", "unpinned"} and pin is None:
            pin = argument
        elif argument.startswith("model=") and model is None:
            model = argument.removeprefix("model=")
        elif argument.startswith("name=") and name is None:
            name = argument.removeprefix("name=")
        else:
            return None
    if not 1 <= count <= maximum_count:
        return None
    if model is not None and (not model or len(model) > 256):
        return None
    if name is not None and not _valid_session_name_filter(name):
        return None
    return _SessionFilters(count, state, archive, pin, model, name)


def _apply_session_filters(sessions, filters: _SessionFilters):
    if filters.state is not None:
        closed = filters.state == "closed"
        sessions = tuple(info for info in sessions if info.closed is closed)
    if filters.archive is not None:
        archived = filters.archive == "archived"
        sessions = tuple(info for info in sessions if info.archived is archived)
    if filters.pin is not None:
        pinned = filters.pin == "pinned"
        sessions = tuple(info for info in sessions if info.pinned is pinned)
    if filters.model is not None:
        sessions = tuple(
            info
            for info in sessions
            if getattr(info.binding, "selected_model", None) == filters.model
        )
    if filters.name is not None:
        needle = filters.name.casefold()
        sessions = tuple(info for info in sessions if needle in info.name.casefold())
    return sessions


def _session_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 2:
        return _call(lambda: render_session_info(session.session_info()), kind="info")
    if len(parts) != 3 or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session show [latest|session-id]")
    return _call(
        lambda: render_session_info(session.inspect_session(parts[2])),
        kind="info",
        failure_prefix="Session inspection failed",
    )


def _session_preview(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4} or not _valid_session_read_selector(parts[2]):
        return _session_preview_usage()
    limit = DEFAULT_SESSION_PREVIEW_TURNS
    if len(parts) == 4:
        if not parts[3].isascii() or not parts[3].isdigit():
            return _session_preview_usage()
        limit = int(parts[3])
    if not 1 <= limit <= MAX_SESSION_PREVIEW_TURNS:
        return _session_preview_usage()
    return _call(
        lambda: render_session_preview(session.preview_session(parts[2], limit)),
        kind="info",
        failure_prefix="Session preview failed",
    )


def _session_preview_usage() -> SlashResult:
    return _usage(f"Usage: /session preview <latest|session-id> [1-{MAX_SESSION_PREVIEW_TURNS}]")


def _session_turns(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if (
        len(parts) not in {4, 5}
        or not _valid_session_read_selector(parts[2])
        or not _positive_ascii_integer(parts[3])
    ):
        return _session_turns_usage()
    count = DEFAULT_SESSION_PREVIEW_TURNS
    if len(parts) == 5:
        if not _positive_ascii_integer(parts[4]):
            return _session_turns_usage()
        count = int(parts[4])
    if count > MAX_SESSION_PREVIEW_TURNS:
        return _session_turns_usage()
    return _call(
        lambda: render_session_turn_range(
            session.session_turn_range(parts[2], int(parts[3]), count)
        ),
        kind="info",
        failure_prefix="Session turn inspection failed",
    )


def _session_turns_usage() -> SlashResult:
    return _usage(
        f"Usage: /session turns <latest|session-id> <start> [1-{MAX_SESSION_PREVIEW_TURNS}]"
    )


def _session_search(command: str, session: ReplSession) -> SlashResult:
    query = command.removeprefix("/session search").strip()
    if not query:
        return _usage("Usage: /session search <literal text>")
    return _call(
        lambda: render_session_search(
            session.search_sessions(query, DEFAULT_SESSION_SEARCH_MATCHES)
        ),
        kind="info",
        failure_prefix="Session search failed",
    )


def _session_export(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4} or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session export <latest|session-id> [markdown|json]")
    format_name = parts[3] if len(parts) == 4 else "markdown"
    if format_name not in {"markdown", "json"}:
        return _usage("Usage: /session export <latest|session-id> [markdown|json]")
    return _call(
        lambda: render_session_export(session.export_session(parts[2]), format_name),
        kind="info",
        failure_prefix="Session export failed",
    )


def _session_doctor(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3 or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session doctor <latest|session-id>")
    return _call(
        lambda: render_session_diagnosis(session.diagnose_session(parts[2])),
        kind="info",
        failure_prefix="Session diagnosis failed",
    )


def _session_repair(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3 or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session repair <latest|session-id>")
    return _call(
        lambda: render_session_repair(session.repair_session(parts[2])),
        kind="success",
        failure_prefix="Session repair failed",
    )


def _session_fork(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if (
        len(parts) != 4
        or not _valid_session_read_selector(parts[2])
        or not _positive_ascii_integer(parts[3])
    ):
        return _usage("Usage: /session fork <latest|session-id> <through-turn>")

    def fork() -> str:
        info = session.fork_session(parts[2], int(parts[3]))
        return (
            f"Forked and selected Session: {info.name}\n"
            f"Session ID: {info.session_id}\n"
            f"Source: {info.forked_from_session_id} through turn #{info.forked_from_turn}\n"
            "Parent transcript, Action Audit, compaction checkpoints, and provider usage were "
            "not modified."
        )

    return _call(fork, kind="success", failure_prefix="Session fork failed")


def _positive_ascii_integer(value: str) -> bool:
    return value.isascii() and value.isdigit() and int(value) >= 1


def _valid_session_read_selector(value: str) -> bool:
    if value == "latest":
        return True
    try:
        canonical_session_id(value)
    except SessionRecordError:
        return False
    return True


def _task_start(command: str, session: ReplSession) -> SlashResult:
    objective = command.removeprefix("/task start").strip()
    if not objective:
        return _usage("Usage: /task start <objective>")
    return _call(
        lambda: "Created durable Task:\n" + render_task_info(session.create_task(objective)),
        kind="success",
        failure_prefix="Task creation failed",
    )


def _task_list(command: str, session: ReplSession) -> SlashResult:
    if command != "/task list":
        return _usage("Usage: /task list")

    def render() -> str:
        tasks = session.list_tasks()
        if not tasks:
            return "No durable Tasks found."
        return "\n".join(render_task_summary(info) for info in tasks)

    return _call(render, kind="info", failure_prefix="Task listing failed")


def _task_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /task show <task-id>")
    try:
        task_id = canonical_task_id(parts[2])
    except TaskRecordError:
        return _usage("Usage: /task show <task-id>")
    return _call(
        lambda: render_task_info(session.inspect_task(task_id)),
        kind="info",
        failure_prefix="Task inspection failed",
    )


def _session_list(command: str, session: ReplSession) -> SlashResult:
    filters = _parse_session_filters(
        command.split()[2:],
        default_count=DEFAULT_SESSION_LIST_COUNT,
        maximum_count=MAX_SESSION_LIST_COUNT,
    )
    if filters is None:
        return _session_list_usage()

    def render() -> str:
        sessions = session.list_sessions()
        if not sessions:
            return "No durable sessions found."
        sessions = _apply_session_filters(sessions, filters)
        total = len(sessions)
        sessions = sessions[: filters.count]
        if not sessions:
            return "No durable sessions match the selected filters."
        current_id = session.session_info().session_id
        latest_id = session.latest_session_info().session_id
        body = "\n".join(
            render_session_summary(
                info,
                current_session_id=current_id,
                latest_session_id=latest_id,
            )
            for info in sessions
        )
        if len(sessions) < total:
            return f"Showing {len(sessions)} most recent of {total} matching Sessions.\n{body}"
        return body

    return _call(render, kind="info")


def _session_list_usage() -> SlashResult:
    return _usage(
        f"Usage: /session list [1-{MAX_SESSION_LIST_COUNT}] [open|closed] "
        "[active|archived] [pinned|unpinned] [model=<name>] [name=<text>]"
    )


def _new_session(session: ReplSession) -> SlashResult:
    return _call(
        lambda: f"Started {session.new_session().name!r}; runtime provider unchanged.",
        kind="success",
        failure_prefix="Session creation failed",
    )


def _rename_session(command: str, session: ReplSession) -> SlashResult:
    argument = command.removeprefix("/session rename").strip()
    if not argument:
        return _usage("Usage: /session rename <name> | /session rename --auto")

    def rename() -> str:
        info = session.rename_session(None if argument == "--auto" else argument)
        return f"Session name: {info.name} ({info.name_source.value})"

    return _call(rename, kind="success", failure_prefix="Session rename failed")


def _archive_session(session: ReplSession, *, archived: bool) -> SlashResult:
    def change() -> str:
        before = session.session_info()
        info = session.set_session_archived(archived)
        state = "archived" if archived else "active"
        if before.archived == archived:
            return f"Session is already {state}: {info.name}"
        return (
            f"Session marked {state}: {info.name}. History, runtime, latest, and resume "
            "identity are unchanged."
        )

    operation = "archive" if archived else "unarchive"
    return _call(
        change,
        kind="success",
        failure_prefix=f"Session {operation} failed",
    )


def _pin_session(session: ReplSession, *, pinned: bool) -> SlashResult:
    def change() -> str:
        before = session.session_info()
        info = session.set_session_pinned(pinned)
        state = "pinned" if pinned else "unpinned"
        if before.pinned == pinned:
            return f"Session is already {state}: {info.name}"
        return (
            f"Session marked {state}: {info.name}. History, runtime, latest, and resume "
            "identity are unchanged."
        )

    operation = "pin" if pinned else "unpin"
    return _call(
        change,
        kind="success",
        failure_prefix=f"Session {operation} failed",
    )


def _session_switch(
    command: str,
    session: ReplSession,
    catalog: SessionSwitchCatalog | None,
) -> SlashResult:
    if catalog is None:
        return _command_error(
            SessionStoreError("Session switch catalog is unavailable"),
            failure_prefix="Session switch failed",
        )
    parts = command.split()
    if len(parts) == 3 and parts[2].isascii() and parts[2].isdigit():
        number = int(parts[2])
        selector = catalog.consume(number)
        if selector is None:
            return SlashResult(
                handled=True,
                message=(
                    "Session picker entry is unavailable. Run /session switch to build a fresh "
                    "numbered snapshot."
                ),
                kind="warning",
            )
        return _resume_selector(
            selector,
            session,
            retry_command="Run /session switch again before retrying a numbered selection.",
        )
    if len(parts) == 2:
        arguments: list[str] = []
    elif len(parts) >= 3 and parts[2] == "list":
        arguments = parts[3:]
    else:
        return _session_switch_usage()
    catalog.clear()
    filters = _parse_session_filters(
        arguments,
        default_count=DEFAULT_SESSION_SWITCH_COUNT,
        maximum_count=MAX_SESSION_SWITCH_COUNT,
    )
    if filters is None:
        return _session_switch_usage()

    def build() -> str:
        current_id = session.session_info().session_id
        sessions = tuple(
            info
            for info in _apply_session_filters(session.list_sessions(), filters)
            if info.session_id != current_id
        )
        total = len(sessions)
        sessions = sessions[: filters.count]
        if not sessions:
            catalog.clear()
            return "No other durable Sessions match the picker filters."
        catalog.replace(tuple(info.session_id for info in sessions))
        latest_id = session.latest_session_info().session_id
        body = "\n".join(
            f"{index}. "
            + render_session_summary(
                info,
                current_session_id=current_id,
                latest_session_id=latest_id,
            )
            for index, info in enumerate(sessions, start=1)
        )
        prefix = (
            f"Session picker snapshot: {len(sessions)} of {total} matching Sessions.\n"
            if len(sessions) < total
            else f"Session picker snapshot: {len(sessions)} matching Sessions.\n"
        )
        return f"{prefix}{body}\nSelect once with /session switch <number>."

    return _call(build, kind="info", failure_prefix="Session switch listing failed")


def _session_switch_usage() -> SlashResult:
    return _usage(
        f"Usage: /session switch | /session switch <number> | /session switch list "
        f"[1-{MAX_SESSION_SWITCH_COUNT}] [open|closed] [active|archived] "
        "[pinned|unpinned] [model=<name>] [name=<text>]"
    )


def _valid_session_name_filter(value: str) -> bool:
    if not value or len(value) > 80 or len(value.encode("utf-8")) > 256:
        return False
    return all(character.isprintable() and not character.isspace() for character in value)


def _resume(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 2:
        return _usage("Usage: /resume <latest|session-id>")
    return _resume_selector(
        parts[1],
        session,
        retry_command=f"Retry /resume {parts[1]}.",
    )


def _resume_selector(
    selector: str,
    session: ReplSession,
    *,
    retry_command: str,
) -> SlashResult:
    try:
        message, kind = render_session_resume(session.switch_session(selector))
        return SlashResult(handled=True, message=message, kind=kind)
    except SessionResumeContextError as error:
        return SlashResult(
            handled=True,
            message=render_resume_rejection(error.report),
            kind="error",
        )
    except SessionResumeConflictError as error:
        return SlashResult(
            handled=True,
            message=(
                f"Session resume was not committed: {error}. Current Session and runtime "
                f"are unchanged. {retry_command}"
            ),
            kind="warning",
        )
    except SessionResumeCommitError as error:
        return SlashResult(
            handled=True,
            message=(
                f"Session resume commit failed at {error.stage.value}: {error}. "
                "Inspect the target transcript before retrying."
            ),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Session resume failed")


def _provider_list(session: ReplSession) -> SlashResult:
    def render() -> str:
        profiles = session.list_profiles()
        if not profiles:
            return "No provider profiles configured."
        return "\n".join(
            f"{profile.name}: {profile.provider_id}/{profile.model}" for profile in profiles
        )

    return _call(render, kind="info")


def _provider_use(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /provider use <name>")
    try:
        result = session.use_profile(parts[2], scope="project")
        message, kind = render_runtime_switch(
            f"Using provider profile {result.status.profile} for this workspace",
            result.fit_report,
            suffix="active workspace selection updated",
        )
        return SlashResult(handled=True, message=message, kind=kind)
    except RuntimeSwitchContextError as error:
        return SlashResult(
            handled=True,
            message=render_switch_rejection(error.report),
            kind="error",
        )
    except RuntimeSwitchAuditError as error:
        return SlashResult(
            handled=True,
            message=(
                "Runtime changed, but Session audit persistence failed. "
                f"Effective profile: {error.result.status.profile or '<direct>'}."
            ),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Provider switch failed")


def _model(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return _usage("Usage: /model <model>")
    model = parts[1].strip()
    try:
        result = session.set_model(model)
        message, kind = render_runtime_switch(
            f"Runtime model changed to {result.status.selected_model}",
            result.fit_report,
            suffix="profile was not modified",
        )
        return SlashResult(handled=True, message=message, kind=kind)
    except RuntimeSwitchContextError as error:
        return SlashResult(
            handled=True,
            message=render_switch_rejection(error.report),
            kind="error",
        )
    except RuntimeSwitchAuditError as error:
        return SlashResult(
            handled=True,
            message=(
                "Runtime changed, but Session audit persistence failed. "
                f"Effective model: {error.result.status.selected_model}."
            ),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Model switch failed")


def _command_error(error: Exception, *, failure_prefix: str) -> SlashResult:
    if isinstance(error, ProviderAdapterError):
        message = render_provider_adapter_error(error, prefix=failure_prefix)
    elif isinstance(
        error,
        (
            CompactionError,
            GitObservationError,
            ProviderProfileError,
            RuntimeProviderStateError,
            RuntimeRouteError,
            SessionStoreError,
        ),
    ):
        message = f"{failure_prefix}: {error}"
    else:
        message = f"{failure_prefix}: unexpected internal error"
    guidance = command_failure_guidance(error)
    if guidance is not None:
        message = f"{message}\n{guidance}"
    return SlashResult(
        handled=True,
        message=message,
        kind="error",
    )


def _call(
    operation,
    *,
    kind: MessageKind = "plain",
    failure_prefix: str = "Command failed",
) -> SlashResult:
    try:
        return SlashResult(handled=True, message=operation(), kind=kind)
    except Exception as error:
        return _command_error(error, failure_prefix=failure_prefix)


def _usage(message: str) -> SlashResult:
    return SlashResult(handled=True, message=message, kind="warning")


def _info(message: str) -> SlashResult:
    return SlashResult(handled=True, message=message, kind="info")
