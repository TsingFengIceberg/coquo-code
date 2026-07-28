"""Slash-command dispatch independent from terminal streams and ANSI rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leonervis_code.cli.presentation import (
    DEFAULT_ACTION_AUDIT_COUNT,
    DEFAULT_COMPACTION_HISTORY_COUNT,
    DEFAULT_TOOL_LEDGER_COUNT,
    HELP_TEXT,
    MAX_ACTION_AUDIT_COUNT,
    MAX_COMPACTION_HISTORY_COUNT,
    MAX_TOOL_LEDGER_COUNT,
    PROVIDER_HELP,
    SESSION_HELP,
    MessageKind,
    render_compact_result,
    render_action_audits,
    render_context_inspection,
    render_compact_preview,
    render_compaction_history,
    render_provider_adapter_error,
    render_output_budget,
    render_output_budget_rejection,
    render_output_budget_update,
    render_recent_history,
    render_runtime_status,
    render_runtime_switch,
    render_resume_rejection,
    render_session_info,
    render_session_resume,
    render_session_summary,
    render_switch_rejection,
    render_tool_ledgers,
    render_durable_usage_summary,
    render_usage_summary,
)
from leonervis_code.core.compaction import CompactionError
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

TOP_LEVEL_COMMANDS = (
    "/help",
    "/history",
    "/actions",
    "/tools",
    "/exit",
    "/quit",
    "/status",
    "/context",
    "/usage",
    "/output",
    "/compact",
    "/compactions",
    "/provider",
    "/model",
    "/session",
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
    SlashCompletionSpec("/history", "Show recent Session turns", True),
    SlashCompletionSpec("/actions", "Show recent Action Audit", True),
    SlashCompletionSpec("/tools", "Show durable tool ledgers", True),
    SlashCompletionSpec("/status", "Show runtime status", True),
    SlashCompletionSpec("/context", "Inspect Effective Context", True),
    SlashCompletionSpec("/usage", "Show provider token usage", True),
    SlashCompletionSpec("/usage session", "Show durable Session usage"),
    SlashCompletionSpec("/usage turns", "Show recent durable turn usage"),
    SlashCompletionSpec("/output", "Inspect or change output budget", True),
    SlashCompletionSpec("/compact", "Compact earlier complete turns", True),
    SlashCompletionSpec("/compactions", "Show durable compaction history", True),
    SlashCompletionSpec("/provider", "Provider commands", True),
    SlashCompletionSpec("/model", "Override the current model", True),
    SlashCompletionSpec("/session", "Session commands", True),
    SlashCompletionSpec("/resume", "Resume a Session", True),
    SlashCompletionSpec("/clear", "Clear terminal output", True),
    SlashCompletionSpec("/exit", "Exit the REPL", True),
    SlashCompletionSpec("/quit", "Exit the REPL", True),
    SlashCompletionSpec("/provider list", "List provider profiles"),
    SlashCompletionSpec("/provider current", "Show the current provider"),
    SlashCompletionSpec("/provider use", "Use a workspace provider profile"),
    SlashCompletionSpec("/session show", "Show the current Session"),
    SlashCompletionSpec("/session list", "List workspace Sessions"),
    SlashCompletionSpec("/session new", "Start an empty Session"),
    SlashCompletionSpec("/tools details", "Show per-request ledger outcomes"),
    SlashCompletionSpec("/compact preview", "Preview fixed compaction selection"),
)


class ReplSession(Protocol):
    turns: tuple

    def action_audits(self): ...

    def tool_ledgers(self, limit: int): ...

    def status(self): ...

    def inspect_context(self): ...

    def usage(self): ...

    def session_usage(self): ...

    def turn_usage_history(self, limit: int = 10): ...

    def set_output_budget(self, max_output_tokens: int | None): ...

    def compact_context(self): ...

    def preview_compaction(self): ...

    def compaction_history(self, limit: int): ...

    def session_info(self): ...

    def latest_session_info(self): ...

    def list_sessions(self): ...

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


def dispatch_slash(command: str, session: ReplSession) -> SlashResult:
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
        return _usage("Usage: /help")
    if command == "/clear":
        return SlashResult(handled=True, clear_screen=True)
    if command.startswith("/clear "):
        return _usage("Usage: /clear")
    if command == "/session":
        return _info(SESSION_HELP)
    if command == "/provider":
        return _info(PROVIDER_HELP)
    if command == "/status":
        return _call(lambda: render_runtime_status(session.status()), kind="info")
    if command.startswith("/status "):
        return _usage("Usage: /status")
    if command == "/context":
        try:
            message, kind = render_context_inspection(session.inspect_context())
            return SlashResult(handled=True, message=message, kind=kind)
        except Exception as error:
            return _command_error(error, failure_prefix="Context inspection failed")
    if command.startswith("/context "):
        return _usage("Usage: /context")
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
    if command == "/session show" or command.startswith("/session show "):
        if command != "/session show":
            return _usage("Usage: /session show")
        return _call(lambda: render_session_info(session.session_info()), kind="info")
    if command == "/session list" or command.startswith("/session list "):
        if command != "/session list":
            return _usage("Usage: /session list")
        return _session_list(session)
    if command == "/session new" or command.startswith("/session new "):
        if command != "/session new":
            return _usage("Usage: /session new")
        return _new_session(session)
    if command.startswith("/session "):
        subcommand = command.split(maxsplit=2)[1]
        return _usage(f"Unknown session command: {subcommand}\nUsage: /session <show|list|new>")
    if command == "/resume" or command.startswith("/resume "):
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
        return _usage(
            f"Unknown provider command: {subcommand}\nUsage: /provider <list|current|use>"
        )
    if command == "/model" or command.startswith("/model "):
        return _model(command, session)
    return _usage(f"Unknown command: {command}. Type /help for controls.")


def _history(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or int(parts[1]) <= 0:
        return _usage("Usage: /history <positive integer>")
    return _call(lambda: render_recent_history(session.turns, int(parts[1])))


def _actions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        count = DEFAULT_ACTION_AUDIT_COUNT
    elif (
        len(parts) == 2
        and parts[1].isascii()
        and parts[1].isdigit()
        and 1 <= int(parts[1]) <= MAX_ACTION_AUDIT_COUNT
    ):
        count = int(parts[1])
    else:
        return _usage(f"Usage: /actions [1-{MAX_ACTION_AUDIT_COUNT}]")
    return _call(lambda: render_action_audits(session.action_audits(), count), kind="info")


def _tools(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
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
            f"Usage: /tools [1-{MAX_TOOL_LEDGER_COUNT}] | /tools details [1-{MAX_TOOL_LEDGER_COUNT}]"
        )
    return _call(
        lambda: render_tool_ledgers(session.tool_ledgers(count), details=details),
        kind="info",
    )


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


def _session_list(session: ReplSession) -> SlashResult:
    def render() -> str:
        sessions = session.list_sessions()
        if not sessions:
            return "No durable sessions found."
        current_id = session.session_info().session_id
        latest_id = session.latest_session_info().session_id
        return "\n".join(
            render_session_summary(
                info,
                current_session_id=current_id,
                latest_session_id=latest_id,
            )
            for info in sessions
        )

    return _call(render, kind="info")


def _new_session(session: ReplSession) -> SlashResult:
    return _call(
        lambda: (
            f"Started new session {session.new_session().session_id}; runtime provider unchanged."
        ),
        kind="success",
        failure_prefix="Session creation failed",
    )


def _resume(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 2:
        return _usage("Usage: /resume <latest|session-id>")
    try:
        message, kind = render_session_resume(session.switch_session(parts[1]))
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
                f"are unchanged. Retry /resume {parts[1]}."
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
        return SlashResult(
            handled=True,
            message=render_provider_adapter_error(error, prefix=failure_prefix),
            kind="error",
        )
    elif isinstance(
        error,
        (
            CompactionError,
            ProviderProfileError,
            RuntimeProviderStateError,
            RuntimeRouteError,
            SessionStoreError,
        ),
    ):
        message = str(error)
    else:
        message = "unexpected internal error"
    return SlashResult(
        handled=True,
        message=f"{failure_prefix}: {message}",
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
