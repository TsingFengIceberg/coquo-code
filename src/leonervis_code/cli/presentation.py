"""Pure terminal presentation for the Leonervis Code CLI."""

from __future__ import annotations

from enum import StrEnum
import json
from pathlib import Path
from typing import Literal, Protocol

from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    ProviderInvocationPreflighted,
    ProviderInvocationUsageReceived,
    TaskAdmissionProposed,
    TaskLifecycleCommitted,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestSkipped,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
)
from leonervis_code.core.contracts import ToolRequestOutcome
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.core.permissions import (
    ApprovalMode,
    PermissionAction,
    PermissionGate,
    PermissionMode,
    PermissionRequest,
)
from leonervis_code.core.project_instructions import ProjectInstructionsSnapshot
from leonervis_code.cli.failure_guidance import tool_result_guidance
from leonervis_code.cli.markdown_renderer import render_plain_document
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.request_context import ContextFitDecision, ContextFitReport
from leonervis_code.session import (
    AutoCompactionCommitted,
    AutoCompactionNotApplied,
    AutoCompactionStarted,
    DurableUsageSnapshot,
    SessionTitleFallbackApplied,
    TurnUsageCompleted,
)
from leonervis_code.task_runtime import TaskNextAction, TaskRunStopped
from leonervis_code.session_records import SessionTitleFallbackReason
from leonervis_code.session_store import MAX_SESSION_PREVIEW_TURNS, MAX_TOOL_LEDGER_QUERY_TURNS
from leonervis_code.providers.usage import RuntimeUsageSnapshot, ProviderUsageTotals
from leonervis_code.tools.catalog import TOOL_CATALOG

RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
DIM = "\x1b[2m"
_READLINE_START = "\001"
_READLINE_END = "\002"
_TOOLBAR_MODEL_WIDTH = 36
_TOOLBAR_WORKSPACE_WIDTH = 64
DEFAULT_ACTION_AUDIT_COUNT = 20
MAX_ACTION_AUDIT_COUNT = 100
DEFAULT_SESSION_LIST_COUNT = 20
MAX_SESSION_LIST_COUNT = 100
DEFAULT_SESSION_PREVIEW_TURNS = 3
MAX_SESSION_PREVIEW_RENDER_BYTES = 32 * 1024
DEFAULT_SESSION_SEARCH_MATCHES = 20
MAX_SESSION_QUERY_RENDER_BYTES = 32 * 1024
MAX_SESSION_EXPORT_RENDER_BYTES = 2 * 1024 * 1024
DEFAULT_TOOL_LEDGER_COUNT = 5
MAX_TOOL_LEDGER_COUNT = MAX_TOOL_LEDGER_QUERY_TURNS
MAX_TOOL_LEDGER_RENDER_BYTES = 32 * 1024
DEFAULT_COMPACTION_HISTORY_COUNT = 5
MAX_COMPACTION_HISTORY_COUNT = 20
CLEAR_SCREEN = "\x1b[2J\x1b[H"
MessageKind = Literal["plain", "info", "success", "warning", "error"]


class ToolDetailMode(StrEnum):
    """Process-local terminal detail level for ephemeral tool start events."""

    COMPACT = "compact"
    FULL = "full"


HELP_TOPICS = ("session", "task", "tools", "git", "context", "provider", "policy", "input")
HELP_TEXT = (
    "Host command groups:\n"
    "  /help session   Session history, browsing, and resume\n"
    "  /help task      Durable Task execution and lifecycle\n"
    "  /help tools     Action Audit and durable tool outcomes\n"
    "  /help git       Read-only Git changes and history\n"
    "  /help context   Context, usage, output budget, and compaction\n"
    "  /help provider  Provider and model selection\n"
    "  /help policy    Permission, approval, and command sandbox\n"
    "  /help input     Prompt editing, cancellation, and exit\n"
    "Use /help <group> for commands. Slash commands are Host-parsed; only explicit Task Stage "
    "execution commands call the provider."
)
SESSION_HELP = (
    "Session commands:\n"
    "  /session show [latest|session-id]\n"
    f"  /session preview <latest|session-id> [1-{MAX_SESSION_PREVIEW_TURNS}]\n"
    f"  /session turns <latest|session-id> <start> [1-{MAX_SESSION_PREVIEW_TURNS}]\n"
    "  /session search <literal text>\n"
    "  /session export <latest|session-id> [markdown|json]\n"
    "  /session fork <latest|session-id> <through-turn>\n"
    "  /session doctor <latest|session-id>\n"
    "  /session repair <latest|session-id>\n"
    "  /session list [1-100] [open|closed] [active|archived] [pinned|unpinned] "
    "[model=<name>] [name=<text>]\n"
    "  /session switch | /session switch <number> | /session switch list [filters]\n"
    "  /session new\n"
    "  /session rename <name> | /session rename --auto\n"
    "  /session archive | /session unarchive\n"
    "  /session pin | /session unpin\n"
    "  /resume <latest|session-id>\n"
    "  /history <count>"
)
TASK_HELP = (
    "Task commands:\n"
    "  /task start <objective>\n"
    "  /task proposals [pending|accepted|rejected|all]\n"
    "  /task proposal show <admission-id>\n"
    "  /task proposal accept <admission-id> [<config-json>]\n"
    "  /task proposal accept <admission-id> confirm <sha256> [<config-json>]\n"
    "  /task proposal reject <admission-id> [reason]\n"
    "  /task proposal drive <admission-id> [1-16]\n"
    "  /task list [1-100] [status=<status>] [active|archived] [name=<text>]\n"
    "  /task show <task-id> | /task timeline <task-id>\n"
    "  /task continue <task-id> <stage-objective> | /task recover <task-id>\n"
    "  /task plan <task-id> | /task plan accept <task-id> | /task run <task-id> [1-16]\n"
    "  /task reflect <task-id> | /task correct <task-id> [objective] | /task revise <task-id>\n"
    "  /task drive <task-id> [1-16] | /task next <task-id> | /task checkpoint <task-id>\n"
    "  /task pause <task-id> [reason] | /task resume <task-id>\n"
    "  /task verify <task-id> <criterion-number> <evidence>\n"
    "  /task verify host <task-id> | /task review <task-id> | /task complete <task-id>\n"
    "  /task cancel <task-id> <reason> | /task fail <task-id> <reason>\n"
    "  /task rename <task-id> <name> | /task archive <task-id> | /task unarchive <task-id>\n"
    "  /task derive <parent-task-id> <objective>\n"
    "Each Stage is one ordinary foreground Turn with normal budgets, permissions, approvals, "
    "sandboxing, Action Audit, cancellation, and Session durability. Model completion is only a "
    "proposal; /task complete requires explicit acceptance evidence."
)
TOOLS_HELP = (
    "Tool and audit commands:\n"
    "  /actions last | /actions [1-100] [status=<status>] [tool=<name>]\n"
    "  /tools catalog [tool-name]\n"
    "  /tools [1-20]\n"
    "  /tools details [1-20]\n"
    "  /tool-details [compact|full]\n"
    "  /permissions [permission-mode [approval-mode]]\n"
    "Action status values: requested, awaiting-approval, authorized, approved, executing, "
    "succeeded, failed, partial, denied, rejected, cancelled, abandoned, outcome-unknown"
)
GIT_HELP = (
    "Read-only Git commands:\n"
    "  /changes [unstaged|staged]\n"
    "  /commits [1-50] [path]\n"
    "  /commit <full-commit-id> [path]"
)
CONTEXT_HELP = (
    "Context commands:\n"
    "  /context\n"
    "  /instructions\n"
    "  /usage [session|turns]\n"
    "  /output [tokens|reset]\n"
    "  /compact | /compact preview\n"
    "  /compactions [1-20]"
)
PROVIDER_HELP = (
    "Provider commands:\n"
    "  /provider list\n"
    "  /provider current\n"
    "  /provider use <name>\n"
    "  /status\n"
    "  /permissions [permission-mode [approval-mode]]\n"
    "  /sandbox check\n"
    "  /output [tokens|reset]\n"
    "  /model <model>"
)
POLICY_HELP = (
    "Policy commands:\n"
    "  /status\n"
    "  /permissions [read-only|workspace-write|danger-full-access] [ask|auto]\n"
    "  /sandbox check\n"
    "Permission mode is the capability ceiling; approval mode controls whether an in-scope "
    "action asks first. Auto never bypasses tool hard validation or the run_command sandbox."
)
INPUT_HELP = (
    "Input controls:\n"
    "  Enter submits the current prompt\n"
    "  Alt+Enter inserts a newline; use Esc then Enter if Alt is intercepted\n"
    "  Ctrl-C clears a draft, cancels an active turn, or exits when idle and empty\n"
    "  Ctrl-D deletes ahead of the cursor or exits when the input is empty\n"
    "  Up/Down recalls accepted prompts and slash commands from this process\n"
    "  Ctrl-R searches the same process-local history\n"
    "  /clear clears terminal output\n"
    "  /exit or /quit exits the REPL"
)

HELP_BY_TOPIC = {
    "session": SESSION_HELP,
    "task": TASK_HELP,
    "tools": TOOLS_HELP,
    "git": GIT_HELP,
    "context": CONTEXT_HELP,
    "provider": PROVIDER_HELP,
    "policy": POLICY_HELP,
    "input": INPUT_HELP,
}


def render_provider_adapter_error(
    error: ProviderAdapterError,
    *,
    prefix: str,
) -> str:
    """Render normalized failure evidence without provider payload contents."""
    failure = error.failure
    trace = f" [request {failure.request_id}]" if failure.request_id else ""
    lines = [f"{prefix} [{failure.kind}]{trace}: {failure.message}"]
    if failure.kind == ProviderFailureKind.OUTPUT_LIMIT:
        requested = error.requested_output_tokens
        usage = error.usage
        if usage is None:
            actual = "provider actual usage unavailable"
        else:
            actual = (
                f"provider reported {usage.output_tokens} output tokens and "
                f"{usage.input_tokens} input tokens"
            )
        lines.append(f"Output limit: requested {requested} tokens; {actual}.")
        observed = " with partial content" if error.partial_response_observed else ""
        lines.append(f"The provider response was incomplete{observed} and was rejected.")
    return "\n".join(lines)


class RuntimeStatusView(Protocol):
    mode: str
    profile: str | None
    selection_source: str
    provider_id: str
    protocol: str | None
    selected_model: str | None
    base_url: str | None
    base_url_source: str | None
    credential_required: bool
    credential_present: bool
    context_window_tokens: int | None
    context_window_source: str
    context_window_diagnostic: str | None
    model_max_output_tokens: int | None
    model_max_output_source: str
    model_max_output_diagnostic: str | None
    max_output_tokens: int | None
    default_max_output_tokens: int | None
    max_output_tokens_source: str


class CommandSandboxDependenciesView(Protocol):
    platform: str
    platform_supported: bool
    bubblewrap_path: str
    bubblewrap_available: bool
    seccomp_available: bool
    ready: bool


class CommandSandboxInspectionView(Protocol):
    dependencies: CommandSandboxDependenciesView
    activation_verified: bool | None
    result_code: str | None
    available: bool


class ProjectStatusView(Protocol):
    runtime: RuntimeStatusView
    session: SessionInfoView
    usage: RuntimeUsageSnapshot
    permission_mode: object
    approval_mode: object
    sandbox: CommandSandboxInspectionView
    tool_count: int
    calls_per_response: int
    requests_per_turn: int
    provider_invocations_per_turn: int


class EffectiveContextInspectionView(Protocol):
    source: str
    context_id: str
    full_turn_count: int
    full_item_count: int
    effective_turn_count: int
    summary_present: bool
    retained_turn_count: int
    latest_checkpoint_sequence: int | None
    latest_checkpoint_trigger: object | None
    fit_report: ContextFitReport | None
    fit_decision: ContextFitDecision
    remaining_capacity: int | None
    target_assessment: object


class SessionInfoView(Protocol):
    session_id: str
    path: object
    turn_count: int
    created_at: str
    closed: bool
    binding: object
    name: str
    name_source: object
    archived: bool
    pinned: bool
    title_fallback_reason: object | None
    forked_from_session_id: str | None
    forked_from_turn: int | None


class TaskInfoView(Protocol):
    task_id: str
    path: object
    workspace: str
    workspace_fingerprint: str
    owner_session_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    created_at: str
    scope: object
    status: object
    record_count: int
    stages: tuple[TaskStageInfoView, ...]
    name: str
    archived: bool
    parent_task_id: str | None
    budget: object
    usage: object
    budget_exhausted: tuple[str, ...]
    latest_plan: object | None
    acceptance_verifications: tuple[object, ...]
    criteria: tuple[object, ...]
    completion_policy: object
    acceptance_checks: tuple[object, ...]
    terminal_outcome: object | None
    terminal_reason: str | None


class TaskStageInfoView(Protocol):
    stage_id: str
    stage_number: int
    objective: str
    started_at: str
    outcome: str
    terminal_at: str | None
    turn_number: int | None
    turn_record_sequence: int | None
    turn_record_sha256: str | None
    failure_reason: object | None
    kind: object
    usage: object | None


class ConversationTurnView(Protocol):
    user: object
    assistant: object


class SessionPreviewView(Protocol):
    info: SessionInfoView
    total_turns: int
    turns: tuple[ConversationTurnView, ...]


class SessionTurnRangeView(Protocol):
    info: SessionInfoView
    total_turns: int
    start_turn: int
    turns: tuple[ConversationTurnView, ...]


class ActionAuditView(Protocol):
    identity: object
    permission_result: object | None
    approval_outcome: object | None
    status: object
    result_code: str | None
    requested_sequence: int


class TurnToolLedgerView(Protocol):
    turn_number: int
    record_sequence: int
    committed_at: str
    schema_version: int
    ledger: object | None


class GitStatusEntryView(Protocol):
    path: str
    index: str
    worktree: str
    original_path: str | None


class GitStatusSnapshotView(Protocol):
    entries: tuple[GitStatusEntryView, ...]
    truncated: bool


class GitDiffSnapshotView(Protocol):
    scope: object
    content: str
    truncated: bool


class GitLogEntryView(Protocol):
    commit_id: str
    parent_ids: tuple[str, ...]
    committed_at: str
    subject: str
    subject_truncated: bool


class GitLogSnapshotView(Protocol):
    entries: tuple[GitLogEntryView, ...]
    path: str
    truncated: bool


class GitShowSnapshotView(Protocol):
    commit_id: str
    parent_ids: tuple[str, ...]
    committed_at: str
    path: str
    message: str
    message_truncated: bool
    patch: str
    patch_truncated: bool


class ToolLedgerQueryResultView(Protocol):
    total_turns: int
    turns: tuple[TurnToolLedgerView, ...]


class CompactContextPreviewView(Protocol):
    source_context_id: str
    full_turn_count: int
    effective_turn_count: int
    summary_present: bool
    eligible: bool
    reason: str | None
    summarized_turn_count: int
    retained_turn_count: int
    fit_report: ContextFitReport | None


class CompactionHistoryResultView(Protocol):
    total_checkpoints: int
    checkpoints: tuple


def render_prompt(
    status: RuntimeStatusView | None,
    session: SessionInfoView | None,
    *,
    color: bool,
    readline: bool = False,
) -> str:
    """Render the minimal input marker; runtime identity lives in the toolbar."""
    del status, session
    return f"{_ansi('›', GREEN, readline=readline)} " if color else "› "


def render_assistant_prefix(*, color: bool) -> str:
    """Render the stable assistant role marker and body separator."""
    return f"{_ansi('•', BLUE, readline=False)} " if color else "• "


def render_message_separator(width: int, *, color: bool) -> str:
    """Render a short secondary rule between complete conversation blocks."""
    if type(width) is not int or width < 1:
        raise ValueError("terminal message separator width is invalid")
    rule = f"  {'─' * max(8, min(24, width // 3))}"
    return f"{DIM}{rule}{RESET}" if color else rule


def render_prompt_toolbar(
    status: RuntimeStatusView | None,
    cwd: Path,
    *,
    color: bool,
    usage: RuntimeUsageSnapshot | None = None,
    session: SessionInfoView | None = None,
) -> str:
    """Render a bounded model and workspace status line below the TTY editor."""
    fields = []
    runtime_label = _toolbar_runtime_label(status)
    if runtime_label is not None:
        fields.append(runtime_label)
    if session is not None:
        fields.append(_toolbar_session_label(session))
    if usage is not None and usage.latest_context is not None:
        fields.append(_toolbar_context_label(usage.latest_context))
    fields.append(_toolbar_workspace_label(cwd))
    text = f"  {' · '.join(fields)}"
    return _ansi(text, BLUE, readline=False) if color else text


def render_activity_line(status: str, *, color: bool) -> str:
    """Render one bounded ephemeral activity label above the prompt editor."""
    if not isinstance(status, str) or not status.strip():
        raise ValueError("terminal activity status is invalid")
    label = _truncate(_safe_toolbar_text(status.strip()), 72)
    suffix = "" if label.endswith((".", "!", "?", "…")) else "..."
    text = f"  {label}{suffix}"
    return f"{DIM}{BLUE}{text}{RESET}" if color else text


def render_message(text: str, kind: MessageKind, *, color: bool) -> str:
    """Apply a semantic terminal style without changing message text."""
    if not color or kind == "plain":
        return text
    code = {
        "info": BLUE,
        "success": GREEN,
        "warning": YELLOW,
        "error": RED,
    }[kind]
    return f"{code}{text}{RESET}"


def indent_terminal_block(text: str, indent: str = "  ") -> str:
    """Indent every visible logical line while preserving existing ANSI styling."""
    if not text:
        return indent
    return "".join(f"{indent}{line}" for line in text.splitlines(keepends=True))


def render_host_message(
    text: str,
    kind: MessageKind,
    *,
    color: bool,
    width: int | None = None,
) -> str:
    """Render non-assistant terminal information as an indented secondary block."""
    indented = (
        indent_terminal_block(text)
        if width is None
        else render_plain_document(
            text,
            width=width,
            first_prefix="  ",
            continuation_prefix="  ",
            prefix_width=2,
        ).removesuffix("\n")
    )
    if not color or kind == "plain":
        return indented
    if kind == "info":
        return f"{DIM}{indented}{RESET}"
    if kind == "success":
        return f"{DIM}{GREEN}{indented}{RESET}"
    return render_message(indented, kind, color=color)


def render_turn_trace(
    text: str,
    kind: MessageKind,
    *,
    color: bool,
    width: int | None = None,
) -> str:
    """Render Host-owned execution facts inside one assistant turn."""
    traced = (
        indent_terminal_block(text, "  │ ")
        if width is None
        else render_plain_document(
            text,
            width=width,
            first_prefix="  │ ",
            continuation_prefix="  │ ",
            prefix_width=4,
        ).removesuffix("\n")
    )
    if not color or kind == "plain":
        return traced
    if kind == "info":
        return f"{DIM}{traced}{RESET}"
    if kind == "success":
        return f"{DIM}{GREEN}{traced}{RESET}"
    return render_message(traced, kind, color=color)


def render_recent_history(turns: tuple[ConversationTurnView, ...], count: int) -> str:
    """Render the most recent complete conversation turns in chronological order."""
    recent_turns = turns[-count:]
    if not recent_turns:
        return "No conversation turns yet."
    return "\n\n".join(
        f"User: {turn.user.text}\nAssistant: {turn.assistant.text}" for turn in recent_turns
    )


def render_session_preview(preview: SessionPreviewView) -> str:
    """Render bounded final user/assistant text from one non-current Session."""
    selected_count = len(preview.turns)
    first_turn_number = preview.total_turns - selected_count + 1
    lines = [
        f"Session preview: {_safe_inline(preview.info.name)}",
        f"Session ID: {preview.info.session_id}",
        f"Showing latest {selected_count} of {preview.total_turns} complete turns (read-only).",
    ]
    if not preview.turns:
        lines.append("No conversation turns yet.")
    for offset, turn in enumerate(preview.turns):
        lines.extend(
            (
                "",
                f"Turn #{first_turn_number + offset}",
                "User:",
                indent_terminal_block(_escape_terminal_text(turn.user.text), "  "),
                "Assistant:",
                indent_terminal_block(_escape_terminal_text(turn.assistant.text), "  "),
            )
        )
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) <= MAX_SESSION_PREVIEW_RENDER_BYTES:
        return rendered
    marker = "\n[Session preview truncated at 32768 UTF-8 bytes.]"
    available = MAX_SESSION_PREVIEW_RENDER_BYTES - len(marker.encode("utf-8"))
    prefix = rendered.encode("utf-8")[:available].decode("utf-8", errors="ignore")
    return prefix + marker


def render_session_turn_range(result: SessionTurnRangeView) -> str:
    """Render one explicit complete-turn range with stable 1-based numbering."""
    lines = [
        f"Session turns: {_safe_inline(result.info.name)}",
        f"Session ID: {result.info.session_id}",
        f"Showing {len(result.turns)} turns from #{result.start_turn} of {result.total_turns} "
        "complete turns (read-only).",
    ]
    if not result.turns:
        lines.append("No conversation turns yet.")
    for offset, turn in enumerate(result.turns):
        lines.extend(
            (
                "",
                f"Turn #{result.start_turn + offset}",
                "User:",
                indent_terminal_block(_escape_terminal_text(turn.user.text), "  "),
                "Assistant:",
                indent_terminal_block(_escape_terminal_text(turn.assistant.text), "  "),
            )
        )
    return _cap_rendered_text(
        "\n".join(lines),
        MAX_SESSION_PREVIEW_RENDER_BYTES,
        "[Session turn range truncated at 32768 UTF-8 bytes.]",
    )


def render_session_search(result) -> str:
    """Render bounded cross-Session matches and explicit completeness facts."""
    lines = [
        f"Session search: {_safe_inline(result.query)}",
        f"Scanned {result.scanned_sessions} of {result.candidate_sessions} candidate Sessions "
        f"and {result.scanned_transcript_bytes} transcript bytes.",
    ]
    if not result.matches:
        lines.append("No matches in the bounded scanned set.")
    for match in result.matches:
        lines.extend(
            (
                "",
                f"{_safe_inline(match.info.name)} ({match.info.session_id})",
                f"Turn #{match.turn_number} {match.role} line {match.line_number}",
                f"  {_safe_inline(match.excerpt)}",
            )
        )
    if result.truncated:
        lines.append("Search was truncated; omitted Sessions or matches may still contain results.")
    else:
        lines.append("Search completed within all configured bounds.")
    return _cap_rendered_text(
        "\n".join(lines),
        MAX_SESSION_QUERY_RENDER_BYTES,
        "[Session search rendering truncated at 32768 UTF-8 bytes.]",
    )


def render_session_export(result, format_name: str) -> str:
    """Serialize one bounded final-text conversation as Markdown or JSON."""
    if format_name == "json":
        payload = {
            "schema_version": 1,
            "session": {
                "id": result.info.session_id,
                "name": result.info.name,
                "created_at": result.info.created_at,
                "turn_count": len(result.turns),
            },
            "turns": [
                {
                    "turn": index,
                    "user": _escape_terminal_text(turn.user.text),
                    "assistant": _escape_terminal_text(turn.assistant.text),
                }
                for index, turn in enumerate(result.turns, start=1)
            ],
        }
        rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
    elif format_name == "markdown":
        lines = [
            f"# Session export: {_safe_inline(result.info.name)}",
            "",
            f"- Session ID: `{result.info.session_id}`",
            f"- Created: `{result.info.created_at}`",
            f"- Complete turns: {len(result.turns)}",
        ]
        for index, turn in enumerate(result.turns, start=1):
            lines.extend(
                (
                    "",
                    f"## Turn {index}",
                    "",
                    "### User",
                    "",
                    indent_terminal_block(_escape_terminal_text(turn.user.text), "    "),
                    "",
                    "### Assistant",
                    "",
                    indent_terminal_block(_escape_terminal_text(turn.assistant.text), "    "),
                )
            )
        rendered = "\n".join(lines)
    else:
        raise ValueError("Session export format must be markdown or json")
    if len(rendered.encode("utf-8")) > MAX_SESSION_EXPORT_RENDER_BYTES:
        raise ValueError(
            f"rendered Session export exceeds {MAX_SESSION_EXPORT_RENDER_BYTES} UTF-8 bytes"
        )
    return rendered


def render_session_diagnosis(diagnosis) -> str:
    """Render bounded transcript health without raw record content."""
    lines = [
        f"Session doctor: {diagnosis.session_id}",
        f"Status: {diagnosis.status.value}",
        f"Code: {diagnosis.code}",
        f"Transcript bytes: {diagnosis.transcript_bytes}",
    ]
    if diagnosis.record_count is not None:
        lines.append(f"Validated records: {diagnosis.record_count}")
    if diagnosis.turn_count is not None:
        lines.append(f"Complete turns: {diagnosis.turn_count}")
    if diagnosis.recoverable_tail_bytes is not None:
        lines.append(f"Recoverable incomplete tail bytes: {diagnosis.recoverable_tail_bytes}")
        lines.append("Run explicit Session repair only after reviewing this diagnosis.")
    return "\n".join(lines)


def render_session_repair(result) -> str:
    """Render a truthful completed tail repair and retained backup identity."""
    return "\n".join(
        (
            f"Session repaired: {result.info.session_id}",
            f"Truncated incomplete tail bytes: {result.truncated_bytes}",
            f"Backup: {result.backup_path.name}",
            f"Validated records after repair: {result.info.record_count}",
            "Current Session, runtime, and latest pointer were not changed by repair.",
        )
    )


def render_git_status(snapshot: GitStatusSnapshotView) -> str:
    """Render bounded repository states without exposing file contents."""
    if not snapshot.entries:
        return "Git status: clean."
    qualifier = " (truncated)" if snapshot.truncated else ""
    lines = [f"Git status: {len(snapshot.entries)} visible changes{qualifier}."]
    for entry in snapshot.entries:
        path = _safe_inline(entry.path)
        origin = ""
        if entry.original_path is not None:
            origin = f" <- {_safe_inline(entry.original_path)}"
        lines.append(f"  index={entry.index} worktree={entry.worktree} path={path}{origin}")
    if snapshot.truncated:
        lines.append("More changed paths were omitted by the bounded status view.")
    return "\n".join(lines)


def render_git_diff(snapshot: GitDiffSnapshotView) -> str:
    """Render one bounded patch while neutralizing terminal control characters."""
    scope = getattr(snapshot.scope, "value", str(snapshot.scope))
    if not snapshot.content:
        return f"Git diff ({scope}): no tracked changes."
    suffix = " · truncated" if snapshot.truncated else ""
    return f"Git diff ({scope}{suffix}):\n{_escape_terminal_text(snapshot.content)}"


def render_git_log(snapshot: GitLogSnapshotView) -> str:
    """Render bounded current-HEAD history with copyable complete object IDs."""
    path = _safe_inline(snapshot.path)
    if not snapshot.entries:
        return f"Git history: no commits matched path={path}."
    qualifier = " (truncated)" if snapshot.truncated else ""
    lines = [f"Git history: {len(snapshot.entries)} visible commits for path={path}{qualifier}."]
    for entry in snapshot.entries:
        subject = _safe_inline(entry.subject)
        subject_suffix = " [subject truncated]" if entry.subject_truncated else ""
        lines.append(f"  {entry.commit_id} {entry.committed_at} {subject}{subject_suffix}")
    return "\n".join(lines)


def render_git_show(snapshot: GitShowSnapshotView) -> str:
    """Render one reachable commit while neutralizing terminal control characters."""
    parents = " ".join(snapshot.parent_ids) if snapshot.parent_ids else "<root>"
    message_suffix = " (truncated)" if snapshot.message_truncated else ""
    patch_suffix = " (truncated)" if snapshot.patch_truncated else ""
    patch = (
        _escape_terminal_text(snapshot.patch)
        if snapshot.patch
        else "No tracked changes for the selected path."
    )
    return "\n".join(
        (
            f"Git commit: {snapshot.commit_id}",
            f"Parents: {parents}",
            f"Committed: {snapshot.committed_at}",
            f"Path: {_safe_inline(snapshot.path)}",
            f"Message{message_suffix}:",
            _escape_terminal_text(snapshot.message),
            f"Patch{patch_suffix}:",
            patch,
        )
    )


def render_action_audits(audits: tuple[ActionAuditView, ...], count: int) -> str:
    """Render recent Host action lifecycles without sensitive identity material."""
    recent = audits[-count:]
    if not recent:
        return "No action audits yet."

    entries = []
    for audit in recent:
        identity = audit.identity
        arguments = identity.arguments.as_mapping()
        path = arguments.get("path")
        path_line = f"\n  path: {path!r}" if isinstance(path, str) else ""
        move_line = ""
        if identity.tool_name in {"move_file", "copy_file"}:
            source = arguments.get("source")
            destination = arguments.get("destination")
            if isinstance(source, str) and isinstance(destination, str):
                move_line = f"\n  source: {source!r}\n  destination: {destination!r}"
        command_line = ""
        if identity.tool_name == "run_command":
            argv = arguments.get("argv")
            executable = argv[0] if isinstance(argv, list) and argv else "<unknown>"
            argument_count = len(argv) - 1 if isinstance(argv, list) and argv else 0
            cwd = arguments.get("cwd", "<unknown>")
            timeout = arguments.get("timeout_seconds", "<unknown>")
            command_line = (
                f"\n  command: {_safe_inline(str(executable))!r} (+{argument_count} args)"
                f"\n  cwd: {_safe_inline(str(cwd))!r}"
                f"\n  timeout: {timeout}s"
            )

        permission = audit.permission_result
        if permission is None:
            permission_line = "pending" if audit.status.value == "requested" else "not recorded"
            approval_line = "not reached"
        else:
            permission_line = f"{permission.decision.value} ({permission.reason.value})"
            if permission.decision.value == "ask":
                if audit.approval_outcome is not None:
                    approval_line = audit.approval_outcome.value
                elif audit.status.value == "awaiting-approval":
                    approval_line = "pending"
                else:
                    approval_line = "not recorded"
            elif permission.decision.value == "deny":
                approval_line = "not requested"
            else:
                approval_line = "not required"

        result_line = audit.status.value
        if audit.result_code is not None:
            result_line += f" ({_safe_inline(audit.result_code)})"
        entries.append(
            f"Action #{audit.requested_sequence}: {identity.tool_name}\n"
            f"  class: {identity.action.value}{path_line}{move_line}{command_line}\n"
            f"  permission: {permission_line}\n"
            f"  approval: {approval_line}\n"
            f"  result: {result_line}"
        )

    prefix = ""
    if len(recent) < len(audits):
        prefix = f"Showing {len(recent)} most recent of {len(audits)} action audits.\n\n"
    return prefix + "\n\n".join(entries)


def render_tool_ledgers(result: ToolLedgerQueryResultView, *, details: bool) -> str:
    """Render bounded replayed ledger facts without IDs, arguments, or result prose."""
    if not result.turns:
        return "No committed turns yet."

    summaries = []
    for turn in result.turns:
        header = (
            f"Turn #{turn.turn_number} (record #{turn.record_sequence}, "
            f"committed {turn.committed_at})"
        )
        ledger = turn.ledger
        if ledger is None:
            summaries.append(
                f"{header}: tool ledger unavailable (legacy turn_committed v{turn.schema_version})"
            )
            continue
        summaries.append(f"{header}: {' '.join(_tool_ledger_fields(ledger))}")

    prefix = ""
    if len(result.turns) < result.total_turns:
        prefix = (
            f"Showing {len(result.turns)} most recent of {result.total_turns} committed turns.\n"
        )
    rendered = prefix + "\n".join(summaries)
    if not details:
        return rendered

    detailed_turns = tuple(
        turn
        for turn in result.turns
        if turn.ledger is not None and getattr(turn.ledger, "entries", ())
    )
    if not detailed_turns:
        return f"{rendered}\n\nNo persisted tool request details in selected turns."

    lines = [rendered, "", "Details:"]
    for turn in detailed_turns:
        ledger = turn.ledger
        entries = getattr(ledger, "entries", ())
        lines.append(f"Turn #{turn.turn_number} requests:")
        for entry in entries:
            code = f" ({_safe_inline(entry.result_code)})" if entry.result_code is not None else ""
            line = (
                f"  #{entry.request_index} {_safe_inline(entry.tool_name)}: "
                f"{entry.outcome.value}{code}"
            )
            candidate = "\n".join((*lines, line))
            sentinel = "  [truncated: additional ledger entries omitted]"
            if len(candidate.encode("utf-8")) > MAX_TOOL_LEDGER_RENDER_BYTES:
                if len("\n".join((*lines, sentinel)).encode("utf-8")) <= (
                    MAX_TOOL_LEDGER_RENDER_BYTES
                ):
                    lines.append(sentinel)
                return "\n".join(lines)
            lines.append(line)
    return "\n".join(lines)


def render_session_summary(
    info: SessionInfoView,
    *,
    current_session_id: str | None = None,
    latest_session_id: str | None = None,
) -> str:
    """Render compact Session metadata with explicit pointer markers."""
    markers = []
    if info.session_id == current_session_id:
        markers.append("[current]")
    if info.session_id == latest_session_id:
        markers.append("[latest]")
    marker_text = f" {' '.join(markers)}" if markers else ""
    turns = f"{info.turn_count} {'turn' if info.turn_count == 1 else 'turns'}"
    state = "closed" if info.closed else "open"
    if info.archived:
        state = f"{state}, archived"
    if getattr(info, "pinned", False):
        state = f"{state}, pinned"
    binding = getattr(info, "binding", None)
    model = _safe_inline(getattr(binding, "selected_model", None) or "<none>")
    provider = _safe_inline(getattr(binding, "provider_id", None) or "<unknown>")
    name = _safe_inline(getattr(info, "name", "New session"))
    return (
        f"{name!r}{marker_text} ({info.session_id}): {turns}, {state}, created {info.created_at}, "
        f"runtime {provider}/{model}"
    )


def render_task_summary(info: TaskInfoView) -> str:
    """Render compact durable Task metadata without terminal control injection."""
    name = _safe_inline(getattr(info, "name", info.objective))
    archived = ", archived" if getattr(info, "archived", False) else ""
    plan = getattr(info, "latest_plan", None)
    progress = (
        f", plan {plan.completed_steps}/{len(plan.steps)}"
        if plan is not None and plan.accepted
        else ""
    )
    policy = getattr(getattr(info, "completion_policy", None), "value", "manual")
    return (
        f"{name!r} ({info.task_id}): {info.status.value}{archived}, {len(info.stages)} stages"
        f"{progress}, completion {policy}, owner {info.owner_session_id}, created {info.created_at}"
    )


def render_task_admission_summary(info) -> str:
    """Render one compact committed admission state without exposing hidden content."""
    proposal = info.proposal
    suffix = f", Task {info.task_id}" if info.task_id is not None else ""
    return (
        f"{proposal.admission_id}: {info.status}{suffix}, "
        f"objective {_safe_inline(proposal.objective)!r}, Session turn #{info.turn_number}"
    )


def render_task_admission_info(info) -> str:
    """Render exact user-reviewable proposal fields and durable source provenance."""
    proposal = info.proposal
    lines = [
        f"Admission: {proposal.admission_id}",
        f"Status: {info.status}",
        f"Objective: {_safe_inline(proposal.objective)}",
        f"Reason: {_safe_inline(proposal.reason)}",
        f"Acceptance criteria: {len(proposal.acceptance_criteria)}",
    ]
    lines.extend(
        f"  {index}. {_safe_inline(criterion)}"
        for index, criterion in enumerate(proposal.acceptance_criteria, start=1)
    )
    lines.extend(
        (
            f"Source Session: {_safe_inline(info.session_name)} ({info.session_id})",
            f"Source Turn: #{info.turn_number}, record #{info.turn_record_sequence}",
            f"Source context: {proposal.context_id}",
            f"Proposal tool-use ID: {_safe_inline(proposal.tool_use_id)}",
            f"Committed: {info.committed_at}",
        )
    )
    if info.task_id is not None:
        lines.append(f"Created Task: {info.task_id}")
    if info.rejection_reason is not None:
        lines.append(f"Rejection reason: {_safe_inline(info.rejection_reason)}")
    if info.resolved_at is not None:
        lines.append(f"Resolved: {info.resolved_at}")
    return "\n".join(lines)


def render_task_admission_acceptance_preview(preview, confirmation_command: str) -> str:
    """Render one exact no-write acceptance candidate and its confirmation command."""
    budget = preview.budget
    lines = [
        "Task admission acceptance preview (no Task created):",
        f"Admission: {preview.proposal.admission_id}",
        f"Task name: {_safe_inline(preview.name)}",
        f"Objective: {_safe_inline(preview.proposal.objective)}",
        f"Completion policy: {preview.completion_policy.value}",
        (
            "Budget: "
            f"stages={budget.max_stages}, provider-invocations={budget.max_provider_invocations}, "
            f"tool-requests={budget.max_tool_requests}, input-tokens={budget.max_input_tokens}, "
            f"output-tokens={budget.max_output_tokens}"
        ),
        f"Acceptance criteria: {len(preview.criteria)}",
    ]
    lines.extend(
        f"  {index}. {_safe_inline(criterion.description)} [{criterion.kind.value}]"
        for index, criterion in enumerate(preview.criteria, start=1)
    )
    lines.extend(
        (
            f"Configuration SHA-256: {preview.configuration_sha256}",
            f"Confirmation SHA-256: {preview.confirmation_sha256}",
            "Confirm this exact candidate with:",
            confirmation_command,
        )
    )
    return "\n".join(lines)


def render_runtime_status(status: RuntimeStatusView) -> str:
    """Render redacted runtime status without credential names or values."""
    if status.mode == "fake":
        return "Mode: fake/offline\nProfile: <none>\nProvider: fake\nModel: <none>"
    credential = "not required"
    if status.credential_required:
        credential = "configured" if status.credential_present else "missing"
    context = (
        f"{status.context_window_tokens} tokens ({status.context_window_source})"
        if status.context_window_tokens is not None
        else "unknown"
    )
    model_output = (
        f"{status.model_max_output_tokens} tokens ({status.model_max_output_source})"
        if status.model_max_output_tokens is not None
        else "unknown"
    )
    output_reserve = (
        f"{status.max_output_tokens} tokens" if status.max_output_tokens is not None else "unknown"
    )
    diagnostic = (
        f"\nContext diagnostic: {status.context_window_diagnostic}"
        if status.context_window_diagnostic
        else ""
    )
    output_diagnostic = (
        f"\nModel output diagnostic: {status.model_max_output_diagnostic}"
        if status.model_max_output_diagnostic
        else ""
    )
    return (
        f"Mode: real\n"
        f"Profile: {status.profile or '<direct>'} ({status.selection_source})\n"
        f"Provider: {status.provider_id} ({status.protocol})\n"
        f"Model: {status.selected_model}\n"
        f"Base URL: {status.base_url} ({status.base_url_source})\n"
        f"Credential: {credential}\n"
        f"Context window: {context}{diagnostic}\n"
        f"Model max output: {model_output}{output_diagnostic}\n"
        f"Requested output reserve: {output_reserve} ({status.max_output_tokens_source})"
    )


def render_project_status(status: ProjectStatusView) -> str:
    """Render one local workbench snapshot without triggering provider inspection."""
    session = status.session
    context = status.usage.latest_context
    if context is None:
        context_line = "Context pressure: not measured for the current runtime"
    elif context.context_window_limit is None:
        context_line = (
            f"Context pressure: input={context.input_count.input_tokens} + "
            f"reserve={context.requested_output_tokens}; window unknown "
            f"({context.input_count.method.value})"
        )
    else:
        used = context.input_count.input_tokens + context.requested_output_tokens
        percent = min(100, (used * 100) // context.context_window_limit)
        context_line = (
            f"Context pressure: {used}/{context.context_window_limit} tokens ({percent}%, "
            f"{context.input_count.method.value}, {context.decision.value})"
        )
    sandbox = _sandbox_summary(status.sandbox, activation_required=False)
    policy = getattr(status.permission_mode, "value", str(status.permission_mode))
    approval = getattr(status.approval_mode, "value", str(status.approval_mode))
    return (
        f"Session: {_safe_inline(session.name)} ({session.session_id}, {session.turn_count} turns)\n"
        f"Permission mode: {policy}\n"
        f"Approval mode: {approval}\n"
        f"{context_line}\n"
        f"Command sandbox: {sandbox}\n"
        f"Tool surface: {status.tool_count} tools; {status.calls_per_response}/response, "
        f"{status.requests_per_turn}/turn, {status.provider_invocations_per_turn} provider invocations/turn\n\n"
        f"{render_runtime_status(status.runtime)}"
    )


def render_command_sandbox_inspection(inspection: CommandSandboxInspectionView) -> str:
    """Render dependency and activation facts without raw setup errors."""
    dependencies = inspection.dependencies
    activation = (
        "verified"
        if inspection.activation_verified is True
        else "failed"
        if inspection.activation_verified is False
        else "not run"
    )
    lines = [
        f"Command sandbox: {_sandbox_summary(inspection, activation_required=True)}",
        f"Platform: {dependencies.platform} ({'supported' if dependencies.platform_supported else 'unsupported'})",
        f"Bubblewrap: {dependencies.bubblewrap_path} ({'ready' if dependencies.bubblewrap_available else 'unavailable'})",
        f"Seccomp filter: {'ready' if dependencies.seccomp_available else 'unavailable'}",
        f"Activation probe: {activation}",
        "Probe command: fixed /usr/bin/true; no model call, user argv, Session write, or Action Audit entry.",
    ]
    if inspection.result_code is not None:
        lines.append(f"Probe result: {_safe_inline(inspection.result_code)}")
    if not inspection.available:
        lines.append(
            "Next: install or repair Linux /usr/bin/bwrap and libseccomp.so.2, then run /sandbox check again."
        )
    return "\n".join(lines)


_TOOL_POLICY_LABELS = {
    "read_file": "workspace-read",
    "glob": "workspace-read",
    "grep": "workspace-read",
    "write_file": "workspace-create/overwrite",
    "edit_file": "workspace-overwrite",
    "run_command": "dangerous",
    "mkdir": "workspace-create",
    "move_file": "workspace-move",
    "delete_file": "workspace-delete",
    "delete_directory": "workspace-delete",
    "list_directory": "workspace-read",
    "copy_file": "workspace-create",
    "read_file_lines": "workspace-read",
    "stat_path": "workspace-read",
    "list_tree": "workspace-read",
    "grep_regex": "workspace-read",
    "patch_file": "workspace-overwrite",
    "git_status": "workspace-read",
    "git_diff": "workspace-read",
    "git_log": "workspace-read",
    "git_show": "workspace-read",
    "task_propose_plan": "task-control",
    "task_report_reflection": "task-control",
    "task_report_blocker": "task-control",
    "task_propose_completion": "task-control",
    "task_propose_start": "task-admission",
    "task_accept_admission": "task-lifecycle",
    "task_accept_plan": "task-lifecycle",
    "task_confirm_completion": "task-lifecycle",
}

_TOOL_HARD_BOUND_SUMMARIES = {
    "read_file": "Existing UTF-8 regular file; no symlink traversal; output retained up to 32 KiB.",
    "glob": "Regular files only; no symlink traversal; bounded scan, 200 paths, and 32 KiB output.",
    "grep": "Case-sensitive literal UTF-8 line search; 1 MiB/file, 1,000 files, 200 matches, and 32 KiB output.",
    "write_file": "Full UTF-8 content up to 4,096 characters/bytes; no parent creation or symlinks; atomic conflict-checked install.",
    "edit_file": "Existing UTF-8 file up to 1 MiB; exactly one literal match; digest recheck and atomic replacement.",
    "run_command": "Direct argv only; 64 arguments and 8 KiB argv; 1-300 seconds; 32 KiB per stream; Linux sandbox required.",
    "mkdir": "Creates one missing directory only; existing parent required; no recursion or symlink traversal.",
    "move_file": "Moves one regular file to a missing same-filesystem destination; no overwrite or symlink traversal.",
    "delete_file": "Permanently deletes one existing non-symlink regular file; no trash, backup, or undo.",
    "delete_directory": "Permanently removes one existing empty non-symlink directory; never recursive.",
    "list_directory": "Direct children only; 10,000-entry scan bound, 200 results, and 32 KiB output; no symlink following.",
    "copy_file": "Copies one regular file up to 1 MiB to a missing destination; no overwrite or symlink traversal.",
    "read_file_lines": "Existing UTF-8 file up to 1 MiB; at most 200 logical lines and 32 KiB output.",
    "stat_path": "No-follow metadata for one workspace path; reports symlinks without following them.",
    "list_tree": "Depth 1-16; no symlink following; 10,000-entry scan, 500 results, and 32 KiB output.",
    "grep_regex": "Case-sensitive per-line Python regex in a bounded worker; 1-second Host wait and 32 KiB output.",
    "patch_file": "One existing UTF-8 file up to 1 MiB; 1-16 unique non-overlapping exact edits; atomic replacement.",
    "git_status": "Read-only current-worktree observation; 10,000-entry parse bound, 200 results, and 32 KiB output.",
    "git_diff": "Current staged or unstaged tracked patch only; literal path, no external diff, and 64 KiB output.",
    "git_log": "Current-HEAD-reachable history only; literal path, at most 50 commits, and 32 KiB output.",
    "git_show": "One full current-HEAD-reachable commit ID; bounded metadata/message and 64 KiB tracked patch.",
    "task_propose_plan": "Planning Stage only; 1-32 bounded objectives; proposal does not accept or execute the plan.",
    "task_report_reflection": "Reflection Stage only; bounded recommendation and summary; no ordinary execution tools are exposed.",
    "task_report_blocker": "Matching Task Stage only; bounded category and summary; never grants permission or completes the Task.",
    "task_propose_completion": "Execution or correction Stage only; proposal requires later Host acceptance evidence.",
    "task_propose_start": "Ordinary Prompt only; creates a pending proposal, not a Task, permission grant, or execution.",
    "task_accept_admission": "Ordinary Prompt only; exact current-Session pending admission and post-Turn stale revalidation required.",
    "task_accept_plan": "Ordinary Prompt only; exact owner-Session latest unaccepted plan and post-Turn stale revalidation required.",
    "task_confirm_completion": "Ordinary Prompt only; current completion proposal and all non-human criteria already verified.",
}


def render_tool_catalog(status: ProjectStatusView, tool_name: str | None = None) -> str:
    """Render canonical tools with current policy availability, not argument schemas."""
    mode = getattr(status.permission_mode, "value", str(status.permission_mode))
    approval = getattr(status.approval_mode, "value", str(status.approval_mode))
    if tool_name is not None:
        definition = next(
            (candidate for candidate in TOOL_CATALOG if candidate.name == tool_name),
            None,
        )
        if definition is None:
            raise ValueError(f"unknown model-visible tool: {tool_name}")
        index = TOOL_CATALOG.index(definition) + 1
        policy = _TOOL_POLICY_LABELS[definition.name]
        return "\n".join(
            (
                f"Tool {index}/{len(TOOL_CATALOG)}: {definition.name}",
                f"Permission class: {policy}",
                f"Current policy: {_tool_policy_availability(policy, status)}",
                "Arguments:",
                *_render_tool_arguments(definition.as_mapping()),
                f"Hard boundaries: {_TOOL_HARD_BOUND_SUMMARIES[definition.name]}",
                "Permission and approval never bypass workspace, symlink, size, conflict, timeout, output, or durability checks.",
            )
        )
    lines = [
        f"Model-visible tools: {len(TOOL_CATALOG)} in canonical order",
        f"Current policy: permission={mode}, approval={approval}",
        "Availability below is policy-level; every request still passes tool hard validation.",
    ]
    for index, definition in enumerate(TOOL_CATALOG, start=1):
        policy = _TOOL_POLICY_LABELS[definition.name]
        availability = _tool_policy_availability(policy, status)
        lines.append(f"{index:>2}. {definition.name}: {policy}; {availability}")
    lines.append(
        "Use /tools for durable per-turn tool ledgers and /tools details for request outcomes."
    )
    return "\n".join(lines)


def render_permission_matrix(
    status: ProjectStatusView,
    *,
    permission_mode: PermissionMode | None = None,
    approval_mode: ApprovalMode | None = None,
) -> str:
    """Render current or hypothetical pure PermissionGate decisions without mutation."""
    current_permission = PermissionMode(
        getattr(status.permission_mode, "value", status.permission_mode)
    )
    current_approval = ApprovalMode(getattr(status.approval_mode, "value", status.approval_mode))
    selected_permission = permission_mode or current_permission
    selected_approval = approval_mode or current_approval
    scope = (
        "Current policy"
        if (selected_permission, selected_approval) == (current_permission, current_approval)
        else "Policy preview (not applied)"
    )
    lines = [
        f"{scope}: permission={selected_permission.value}, approval={selected_approval.value}",
        "Capability and interaction are independent; auto never bypasses hard tool validation.",
        f"Command sandbox: {_sandbox_summary(status.sandbox, activation_required=False)}",
        "Permission decisions describe policy only; sandbox readiness and tool preparation can still reject execution.",
    ]
    gate = PermissionGate()
    for action in (
        PermissionAction.WORKSPACE_READ,
        PermissionAction.WORKSPACE_CREATE,
        PermissionAction.WORKSPACE_OVERWRITE,
        PermissionAction.WORKSPACE_MOVE,
        PermissionAction.WORKSPACE_DELETE,
        PermissionAction.DANGEROUS,
    ):
        result = gate.evaluate(PermissionRequest(selected_permission, selected_approval, action))
        lines.append(f"{action.value}: {result.decision.value} ({result.reason.value})")
    lines.extend(
        (
            "read-only: workspace reads only.",
            "workspace-write: reads and bounded workspace mutations; dangerous commands remain denied.",
            "danger-full-access: includes dangerous actions, while run_command still requires its Linux sandbox.",
            "Change startup policy with --permission-mode and --approval; this command never mutates it.",
        )
    )
    return "\n".join(lines)


def _tool_policy_availability(policy: str, status: ProjectStatusView) -> str:
    mode = getattr(status.permission_mode, "value", str(status.permission_mode))
    approval = getattr(status.approval_mode, "value", str(status.approval_mode))
    if policy == "workspace-read":
        return "available"
    if policy == "task-control":
        return "available only in a matching Task Stage"
    if policy == "task-admission":
        return "available only in an ordinary Prompt; explicit user acceptance required"
    if policy == "task-lifecycle":
        return "available only in an ordinary Prompt for the current user's explicit decision"
    if policy == "dangerous":
        if mode != "danger-full-access":
            return "denied by current permission mode"
        if not status.sandbox.dependencies.ready:
            return "sandbox dependencies unavailable"
        return f"available ({approval}; sandbox required)"
    if mode == "read-only":
        return "denied by current permission mode"
    return f"available ({approval})"


def _render_tool_arguments(definition: dict[str, object]) -> tuple[str, ...]:
    schema = definition.get("input_schema")
    if not isinstance(schema, dict):
        return ("  (schema unavailable)",)
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return ("  (schema unavailable)",)
    if not properties:
        return ("  (none)",)
    required_names = {name for name in required if isinstance(name, str)}
    lines: list[str] = []
    for name, raw_property in properties.items():
        if not isinstance(name, str) or not isinstance(raw_property, dict):
            continue
        shape = _schema_shape(raw_property)
        presence = "required" if name in required_names else "optional"
        lines.append(f"  {name}: {shape}; {presence}")
    return tuple(lines) or ("  (schema unavailable)",)


def _schema_shape(schema: dict[str, object]) -> str:
    kind = schema.get("type")
    shape = kind if isinstance(kind, str) else "value"
    if shape == "array" and isinstance(schema.get("items"), dict):
        item_type = schema["items"].get("type")
        if isinstance(item_type, str):
            shape = f"array<{item_type}>"
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(item, str) for item in enum):
        shape += f" {{{'|'.join(enum)}}}"
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if type(minimum) is int or type(maximum) is int:
        shape += f" [{minimum if type(minimum) is int else '?'}..{maximum if type(maximum) is int else '?'}]"
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if type(min_items) is int or type(max_items) is int:
        shape += f" [{min_items if type(min_items) is int else '?'}..{max_items if type(max_items) is int else '?'} items]"
    return shape


def _sandbox_summary(
    inspection: CommandSandboxInspectionView,
    *,
    activation_required: bool,
) -> str:
    dependencies = inspection.dependencies
    if not dependencies.platform_supported:
        return "unavailable (Linux required)"
    if not dependencies.bubblewrap_available:
        return "unavailable (/usr/bin/bwrap missing or unusable)"
    if not dependencies.seccomp_available:
        return "unavailable (libseccomp filter unavailable)"
    if inspection.activation_verified is False:
        code = inspection.result_code or "activation_failed"
        return f"unavailable ({_safe_inline(code)})"
    if inspection.activation_verified is True:
        return "ready; activation verified"
    return "dependencies ready; run /sandbox check" if activation_required else "dependencies ready"


def render_output_budget(status: RuntimeStatusView) -> tuple[str, MessageKind]:
    """Render effective and configured output limits without mutating runtime state."""
    if status.mode != "real" or status.max_output_tokens is None:
        return "Output budget is unavailable without a real provider runtime.", "warning"
    configured = (
        f"{status.default_max_output_tokens} tokens"
        if status.default_max_output_tokens is not None
        else "unknown"
    )
    model_limit = (
        f"{status.model_max_output_tokens} tokens"
        if status.model_max_output_tokens is not None
        else "unknown"
    )
    return (
        f"Effective output budget: {status.max_output_tokens} tokens "
        f"({status.max_output_tokens_source})\n"
        f"Configured default: {configured}\n"
        f"Model maximum: {model_limit}\n"
        "Scope: current process only; provider profile and resumed Session selection are unchanged.",
        "info",
    )


def render_output_budget_update(result) -> tuple[str, MessageKind]:
    """Render one applied process-local budget update with fit evidence."""
    status = result.status
    current = status.max_output_tokens
    if not result.changed:
        return f"Output budget unchanged at {current} tokens.", "info"
    if result.previous_output_tokens == current and status.max_output_tokens_source == "profile":
        first_line = f"Output budget reset to configured default: {current} tokens (profile)."
    else:
        first_line = (
            f"Output budget changed: {result.previous_output_tokens} -> {current} tokens "
            f"({status.max_output_tokens_source})."
        )
    lines = [first_line, "Provider profile and Session history were not modified."]
    report = result.fit_report
    if report is None:
        return "\n".join(lines), "success"
    if report.decision == ContextFitDecision.FITS:
        lines.append(
            f"Committed context fits: input={report.input_count.input_tokens} "
            f"({report.input_count.method.value}) + reserve={report.requested_output_tokens} "
            f"<= window={report.context_window_limit}."
        )
        return "\n".join(lines), "success"
    diagnostic = report.input_count.diagnostic or "required context facts are unknown"
    lines.append(
        f"Compatibility is not confirmed: {diagnostic}. The next provider invocation "
        "will run full preflight."
    )
    return "\n".join(lines), "warning"


def render_output_budget_rejection(report: ContextFitReport) -> str:
    """Render a known unsafe budget update without implying any state change."""
    if report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED:
        detail = (
            f"reserve={report.requested_output_tokens} > model max output="
            f"{report.model_output_limit}"
        )
    else:
        detail = (
            f"input={report.input_count.input_tokens} ({report.input_count.method.value}) + "
            f"reserve={report.requested_output_tokens} > window={report.context_window_limit}"
        )
    return f"Output budget change rejected: {detail}. Current output budget is unchanged."


def render_context_inspection(
    inspection: EffectiveContextInspectionView,
) -> tuple[str, MessageKind]:
    """Render approved context metadata without exposing model-visible content."""
    report = inspection.fit_report
    source = inspection.source.replace("_", " ")
    lines = [
        f"Source: {source}",
        f"Context ID: {inspection.context_id}",
        f"Full history: {_count_label(inspection.full_turn_count, 'turn')}, "
        f"{_count_label(inspection.full_item_count, 'item')}",
        f"Effective history: {_count_label(inspection.effective_turn_count, 'turn')}, "
        f"{_count_label(inspection.effective_item_count, 'item')}",
        f"Compact summary: {'present' if inspection.summary_present else 'absent'}",
    ]
    if inspection.summary_present:
        lines.append(
            f"Retained real history: {_count_label(inspection.retained_turn_count, 'turn')}"
        )
        if inspection.latest_checkpoint_sequence is not None:
            lines.append(f"Checkpoint sequence: {inspection.latest_checkpoint_sequence}")
        if inspection.latest_checkpoint_trigger is not None:
            lines.append(
                f"Checkpoint trigger: {inspection.latest_checkpoint_trigger.value.replace('_', ' ')}"
            )
    diagnostic = None
    if report is None:
        lines.extend(
            (
                "Input: unavailable",
                "Output reserve: unavailable",
                "Context window: unknown",
                "Model max output: unknown",
                "Fit: unknown",
            )
        )
        diagnostic = getattr(
            inspection.target_assessment,
            "unavailable_diagnostic",
            None,
        )
        kind: MessageKind = "warning"
    else:
        count = report.input_count
        if count.input_tokens is None:
            lines.append("Input: unknown")
        else:
            lines.append(f"Input: {count.input_tokens} tokens ({count.method.value})")
        lines.append(f"Output reserve: {report.requested_output_tokens} tokens")
        lines.append(
            f"Context window: {report.context_window_limit} tokens"
            if report.context_window_limit is not None
            else "Context window: unknown"
        )
        lines.append(
            f"Model max output: {report.model_output_limit} tokens"
            if report.model_output_limit is not None
            else "Model max output: unknown"
        )
        lines.append(f"Fit: {report.decision.value}")
        if inspection.remaining_capacity is not None:
            lines.append(f"Remaining capacity: {inspection.remaining_capacity} tokens")
        diagnostic = count.diagnostic
        if report.decision == ContextFitDecision.FITS:
            kind = "info"
        elif report.decision == ContextFitDecision.UNKNOWN:
            kind = "warning"
        else:
            kind = "error"
    pressure, pressure_kind = _context_pressure(report)
    lines.append(f"Pressure: {pressure}")
    if pressure_kind == "error":
        kind = "error"
    elif pressure_kind == "warning" and kind == "info":
        kind = "warning"
    if diagnostic:
        lines.append(f"Diagnostic: {diagnostic}")
    return "\n".join(lines), kind


def render_project_instructions_inspection(
    snapshot: ProjectInstructionsSnapshot | None,
) -> str:
    """Render current project-instruction metadata without exposing file content."""
    if snapshot is None:
        return "Project instructions: absent\nPath: AGENTS.md"
    return "\n".join(
        (
            "Project instructions: loaded",
            f"Path: {snapshot.path}",
            f"UTF-8 bytes: {snapshot.byte_count}",
            f"Representation: pi-v{snapshot.version}",
            f"Fingerprint: {snapshot.fingerprint}",
        )
    )


def render_session_resume(result: object) -> tuple[str, MessageKind]:
    """Render target-aware resume evidence and any applied pointer warning."""
    if result.effect.value == "already_current":
        return (
            f"Session {result.session_id} is already current; no resume record was written.",
            "info",
        )
    report = result.fit_report
    prefix = f"Resumed session {result.session_id}; current runtime unchanged."
    if report is None:
        message = (
            f"{prefix} Compatibility screening is unavailable for fake runtime, "
            "and no provider request was made."
        )
        kind: MessageKind = "warning"
    elif report.decision == ContextFitDecision.FITS:
        message = (
            f"{prefix} Committed context fits: input={report.input_count.input_tokens} "
            f"({report.input_count.method.value}) + reserve={report.requested_output_tokens} "
            f"<= window={report.context_window_limit}. The next provider invocation "
            "still runs full preflight."
        )
        kind = "success"
    else:
        diagnostic = report.input_count.diagnostic or "required context facts are unknown"
        message = (
            f"{prefix} Compatibility was not confirmed: {diagnostic}. The resume was "
            "applied, no history was deleted, and the next provider invocation will "
            "run full preflight."
        )
        kind = "warning"
    if result.effect.value == "applied_latest_failed":
        message += " The resume audit is durable, but latest pointer update failed."
        kind = "error"
    elif result.effect.value == "applied_latest_durability_unknown":
        message += " The latest pointer was replaced, but crash durability is unconfirmed."
        kind = "warning"
    if result.recovery_applied:
        message += " An incomplete crash tail was recovered during commit."
    return message, kind


def render_resume_rejection(report: ContextFitReport, *, startup: bool = False) -> str:
    """Render a known-incompatible resume with truthful unchanged state."""
    if report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED:
        detail = (
            f"reserve={report.requested_output_tokens} > model max output="
            f"{report.model_output_limit}"
        )
    else:
        detail = (
            f"input={report.input_count.input_tokens} ({report.input_count.method.value}) + "
            f"reserve={report.requested_output_tokens} > window={report.context_window_limit}"
        )
    state = (
        "No Session was resumed; the latest pointer and runtime selection are unchanged."
        if startup
        else "Current Session, latest pointer, target transcript, and runtime are unchanged."
    )
    return f"Session resume rejected: {detail}. {state}"


def render_prompt_event(
    event: object,
    *,
    tool_detail_mode: ToolDetailMode = ToolDetailMode.COMPACT,
) -> tuple[str, MessageKind]:
    """Render one safe ephemeral prompt lifecycle event."""
    if type(tool_detail_mode) is not ToolDetailMode:
        raise ValueError("tool detail mode is invalid")
    if isinstance(event, AssistantToolTextReceived):
        return event.text, "plain"
    if isinstance(event, ToolRequestStarted):
        if tool_detail_mode == ToolDetailMode.FULL:
            details = event.safe_details or ((event.safe_summary,) if event.safe_summary else ())
            suffix = "" if not details else "\n" + "\n".join(f"  {detail}" for detail in details)
            return (
                f"[tool {event.call_index}/{event.call_limit}] {event.tool_name}{suffix}",
                "info",
            )
        detail = f" {event.safe_summary}" if event.safe_summary else ""
        return (
            f"[tool {event.call_index}/{event.call_limit}] {event.tool_name}{detail}",
            "info",
        )
    if isinstance(event, ToolRequestFinished):
        detail = f" code={event.result_code}" if event.result_code is not None else ""
        if event.truncated:
            detail += " truncated=true"
        if event.result_details is not None:
            if tool_detail_mode == ToolDetailMode.FULL:
                detail += "\n" + "\n".join(
                    f"  {line}" for line in event.result_details.full_details
                )
            else:
                detail += f" {event.result_details.compact_summary}"
        kind: MessageKind
        if event.status == ToolEventStatus.SUCCEEDED:
            kind = "success"
        elif event.status in {
            ToolEventStatus.DENIED,
            ToolEventStatus.REJECTED,
            ToolEventStatus.CANCELLED,
            ToolEventStatus.PARTIAL,
        }:
            kind = "warning"
        else:
            kind = "error"
        message = f"[tool {event.call_index}/{event.call_limit}] {event.status.value}{detail}"
        guidance = tool_result_guidance(event.tool_name, event.result_code)
        if guidance is not None:
            message = f"{message}\n{guidance}"
        return message, kind
    if isinstance(event, ToolRequestLimited):
        return (
            f"[tool {event.call_index}/{event.call_limit}] {event.tool_name} not executed: "
            "tool-call limit reached",
            "warning",
        )
    if isinstance(event, ToolRequestSkipped):
        return (
            f"[tool {event.call_index}/{event.call_limit}] {event.tool_name} skipped: "
            f"{event.reason_code}",
            "warning",
        )
    if isinstance(event, ToolTurnSummaryCommitted):
        return f"Tool summary: {' '.join(_tool_ledger_fields(event.ledger))}", "info"
    if isinstance(event, ProviderInvocationPreflighted):
        return render_context_meter(
            event.report,
            invocation_index=event.invocation_index,
            invocation_limit=event.invocation_limit,
        ), "info"
    if isinstance(event, ProviderInvocationUsageReceived):
        if event.usage is None:
            detail = "unknown (provider did not return usable metadata)"
            kind: MessageKind = "warning"
        else:
            detail = (
                f"{_format_tokens(event.usage.input_tokens)} in / "
                f"{_format_tokens(event.usage.output_tokens)} out"
            )
            kind = "info"
        return (
            f"Token usage [{event.invocation_index}/{event.invocation_limit}]: {detail}",
            kind,
        )
    if isinstance(event, TaskAdmissionProposed):
        return (
            "Task admission proposal committed:\n"
            f"Admission: {event.admission_id}\n"
            f"Objective: {event.objective_summary}\n"
            f"Acceptance criteria: {event.acceptance_criteria_count}\n"
            "Reply naturally when you want to accept it; no /task command is required.",
            "info",
        )
    if isinstance(event, TaskLifecycleCommitted):
        if event.operation == "accept-admission":
            message = "Task admission accepted and committed. Continuing in the foreground."
        elif event.operation == "accept-plan":
            message = "Task plan accepted and committed. Continuing in the foreground."
        else:
            message = "Task completion confirmation committed. The durable Task is complete."
        return f"{message}\nTask: {event.task_id}", "success"
    if isinstance(event, TurnUsageCompleted):
        return render_usage_summary(event.usage, compact=True), "info"
    if isinstance(event, TaskRunStopped):
        message = (
            f"Task run stopped: reason={event.reason}, completed_stages={event.completed_stages}"
        )
        if event.reason == "independent-review-required":
            message += (
                "\nIndependent review was not started automatically. /task review uses the "
                "current provider with tools disabled and may consume tokens or API cost."
            )
        return message, "info"
    if isinstance(event, SessionTitleFallbackApplied):
        return (
            "Session naming used a Host fallback: "
            f"{render_session_title_fallback_reason(event.reason)}.",
            "warning",
        )

    if not isinstance(
        event,
        (AutoCompactionStarted, AutoCompactionCommitted, AutoCompactionNotApplied),
    ):
        raise ValueError("unsupported prompt event")
    trigger = event.trigger.value.replace("_", " ")
    if isinstance(event, AutoCompactionStarted):
        threshold = (
            f" at the {event.high_water_percent}% high-water mark"
            if event.high_water_percent is not None
            else " after known context overflow"
        )
        return (
            f"Automatic compact started{threshold}: input={event.input_tokens} "
            f"({event.input_method}) + reserve={event.requested_output_tokens}, "
            f"window={event.context_window_tokens}; trigger={trigger}.",
            "info",
        )
    if isinstance(event, AutoCompactionCommitted):
        result = event.result
        return (
            f"Automatic compact committed ({trigger}): summarized "
            f"{result.summarized_turn_count} complete turns, retained "
            f"{result.retained_turn_count}; input {result.before_input_tokens} -> "
            f"{result.after_input_tokens} ({result.input_method}); checkpoint "
            f"{result.checkpoint_sequence}. Full transcript and /history were preserved.",
            "success",
        )
    continuation = (
        "the original prompt will continue"
        if event.prompt_continues
        else ("the original prompt will not be sent")
    )
    return (
        f"Automatic compact was not applied ({trigger}): {event.reason}; {continuation}.",
        "warning" if event.prompt_continues else "error",
    )


def render_compact_result(result: object) -> str:
    """Render one committed checkpoint without exposing summary contents."""
    return (
        f"Compacted {result.summarized_turn_count} complete turns; retained "
        f"{result.retained_turn_count} turns.\n"
        f"Context ID: {result.source_context_id} -> {result.result_context_id}\n"
        f"Input: {result.before_input_tokens} -> {result.after_input_tokens} tokens "
        f"({result.input_method}); fit: {result.fit_decision.value}.\n"
        f"Checkpoint: sequence {result.checkpoint_sequence} in session "
        f"{result.session_id}. Full transcript and /history were preserved."
    )


def render_compact_preview(preview: CompactContextPreviewView) -> tuple[str, MessageKind]:
    """Render read-only fixed-policy selection without implying a summary exists."""
    report = preview.fit_report
    pressure, pressure_kind = _context_pressure(report)
    lines = [
        f"Eligible: {'yes' if preview.eligible else 'no'}",
        f"Context ID: {preview.source_context_id}",
        f"History: {preview.full_turn_count} full turns; "
        f"{preview.effective_turn_count} effective turns; summary "
        f"{'present' if preview.summary_present else 'absent'}.",
    ]
    if preview.eligible:
        lines.append(
            f"Selection: summarize {preview.summarized_turn_count} complete effective turns; "
            f"retain the latest {preview.retained_turn_count} turns verbatim."
        )
    elif preview.reason is not None:
        lines.append(f"Reason: {preview.reason}")
    lines.append(f"Pressure: {pressure}")
    lines.append(
        "Next prompt: the Host reassesses the exact request including pending user input; "
        "80% may trigger proactive compaction and known overflow requires compaction."
    )
    lines.append(
        "Preview did not generate a summary or modify the Session. Exact Anthropic "
        "inspection may use a count-only API request."
    )
    kind: MessageKind = "info" if preview.eligible else "warning"
    if pressure_kind == "error":
        kind = "error"
    elif pressure_kind == "warning" and kind == "info":
        kind = "warning"
    return "\n".join(lines), kind


def render_compaction_history(result: CompactionHistoryResultView) -> str:
    """Render durable checkpoint metadata without summary text or binding details."""
    if not result.checkpoints:
        return "No durable compaction checkpoints yet."
    lines = []
    if len(result.checkpoints) < result.total_checkpoints:
        lines.append(
            f"Showing {len(result.checkpoints)} most recent of "
            f"{result.total_checkpoints} compaction checkpoints."
        )
    for checkpoint in result.checkpoints:
        threshold = (
            f" at {checkpoint.high_water_percent}%"
            if checkpoint.high_water_percent is not None
            else ""
        )
        previous = (
            str(checkpoint.previous_checkpoint_sequence)
            if checkpoint.previous_checkpoint_sequence is not None
            else "none"
        )
        lines.append(
            f"Checkpoint #{checkpoint.sequence} ({checkpoint.occurred_at}): "
            f"{checkpoint.trigger.value.replace('_', ' ')}{threshold}; summarized "
            f"{checkpoint.summarized_turn_count}, retained {checkpoint.retained_turn_count}, "
            f"full transcript {checkpoint.full_turn_count} turns; previous {previous}; "
            f"schema v{checkpoint.schema_version}."
        )
    lines.append(
        "Historical before/after token counts are unavailable because checkpoints do not "
        "persist token measurements."
    )
    return "\n".join(lines)


def render_runtime_switch(
    destination: str,
    report: ContextFitReport | None,
    *,
    suffix: str,
) -> tuple[str, MessageKind]:
    """Render committed switch evidence without claiming more than the probe proved."""
    if report is None:
        return f"{destination}; {suffix}", "success"
    if report.decision == ContextFitDecision.FITS:
        return (
            f"{destination}; committed context fits: input="
            f"{report.input_count.input_tokens} ({report.input_count.method.value}) + "
            f"reserve={report.requested_output_tokens} <= window="
            f"{report.context_window_limit}. The next provider invocation still runs "
            f"full preflight; {suffix}",
            "success",
        )
    diagnostic = report.input_count.diagnostic or "required context facts are unknown"
    return (
        f"{destination}; compatibility not confirmed: {diagnostic}. "
        "The switch was applied, no history was deleted, and the next provider "
        f"invocation will run full preflight; {suffix}",
        "warning",
    )


def render_switch_rejection(report: ContextFitReport) -> str:
    """Render a safe known-overflow rejection with explicit unchanged state."""
    if report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED:
        detail = (
            f"reserve={report.requested_output_tokens} > model max output="
            f"{report.model_output_limit}"
        )
    else:
        detail = (
            f"input={report.input_count.input_tokens} "
            f"({report.input_count.method.value}) + reserve="
            f"{report.requested_output_tokens} > window={report.context_window_limit}"
        )
    return (
        f"Runtime switch rejected: {detail}. Current runtime and profile selection "
        "are unchanged. Keep the current runtime or use /session new before switching."
    )


def render_session_info(info: SessionInfoView) -> str:
    """Render one durable Session without exposing transcript contents."""
    lines = [f"Session: {info.name}", f"Name source: {info.name_source.value}"]
    if info.title_fallback_reason is not None:
        lines.append(
            f"Title fallback: {render_session_title_fallback_reason(info.title_fallback_reason)}"
        )
    if getattr(info, "forked_from_session_id", None) is not None:
        lines.append(
            f"Forked from: {info.forked_from_session_id} through turn #{info.forked_from_turn}"
        )
    lines.extend(
        (
            f"Archived: {'yes' if info.archived else 'no'}",
            f"Pinned: {'yes' if getattr(info, 'pinned', False) else 'no'}",
            f"Session ID: {info.session_id}",
            f"Transcript: {info.path}",
            f"Turns: {info.turn_count}",
            f"Created: {info.created_at}",
        )
    )
    return "\n".join(lines)


def render_task_info(info: TaskInfoView) -> str:
    """Render one durable Task and its bounded acceptance contract."""
    lines = [
        f"Task name: {_safe_inline(getattr(info, 'name', info.objective))}",
        f"Task: {_safe_inline(info.objective)}",
        f"Status: {info.status.value}",
        f"Archived: {'yes' if getattr(info, 'archived', False) else 'no'}",
        f"Driver paused: {'yes' if getattr(info, 'driver_paused', False) else 'no'}",
        f"Task ID: {info.task_id}",
        f"Owner Session: {info.owner_session_id}",
        f"Scope: {info.scope.value}",
        f"Transcript: {info.path}",
        f"Created: {info.created_at}",
        f"Records: {info.record_count}",
        f"Stages: {len(info.stages)}",
        f"Acceptance criteria: {len(info.acceptance_criteria)}",
        "Completion policy: "
        f"{getattr(getattr(info, 'completion_policy', None), 'value', 'manual')}",
    ]
    if getattr(info, "parent_task_id", None) is not None:
        lines.append(f"Derived from: {info.parent_task_id}")
    structured = getattr(info, "criteria", ())
    if len(structured) == len(info.acceptance_criteria):
        for index, criterion in enumerate(structured, start=1):
            kind = getattr(getattr(criterion, "kind", None), "value", "human")
            source = (
                "user"
                if kind == "human"
                else "independent-reviewer"
                if kind == "independent-reviewer"
                else "host-check"
            )
            lines.append(
                f"  {index}. {_safe_inline(criterion.description)} [{kind}; verify={source}]"
            )
    else:
        lines.extend(
            f"  {index}. {_safe_inline(criterion)} [human; verify=user]"
            for index, criterion in enumerate(info.acceptance_criteria, start=1)
        )
    if info.stages:
        latest = info.stages[-1]
        lines.extend(
            (
                f"Latest Stage: #{latest.stage_number} {latest.outcome}",
                f"Stage kind: {getattr(getattr(latest, 'kind', None), 'value', 'execution')}",
                f"Stage ID: {latest.stage_id}",
                f"Stage objective: {_safe_inline(latest.objective)}",
                f"Stage started: {latest.started_at}",
            )
        )
        if latest.turn_number is not None:
            lines.append(
                f"Turn evidence: Session turn #{latest.turn_number}, "
                f"record #{latest.turn_record_sequence}, SHA-256 {latest.turn_record_sha256}"
            )
        elif latest.failure_reason is not None:
            lines.append(f"Stage failure: {latest.failure_reason.value}")
        elif latest.outcome == "interrupted":
            lines.append("Recovery: run /task recover before any new Stage.")
        stage_usage = getattr(latest, "usage", None)
        if stage_usage is not None:
            lines.append(
                "Stage usage: "
                f"{stage_usage.provider_invocations} provider calls, "
                f"{stage_usage.tool_requests} tool requests, "
                f"{stage_usage.input_tokens} input and "
                f"{stage_usage.output_tokens} output tokens"
            )
    budget = getattr(info, "budget", None)
    usage = getattr(info, "usage", None)
    if budget is not None and usage is not None:
        lines.extend(
            (
                "Budget: "
                f"stages {len(info.stages)}/{budget.max_stages}, provider calls "
                f"{usage.provider_invocations}/{budget.max_provider_invocations}, tool requests "
                f"{usage.tool_requests}/{budget.max_tool_requests}",
                "Task usage: "
                f"{usage.input_tokens} input tokens, {usage.output_tokens} output tokens, "
                f"{usage.known_token_invocations} known and {usage.unknown_token_invocations} unknown "
                f"provider calls, {usage.unavailable_stages} legacy/unavailable stages",
                "Token ceilings: input "
                f"{budget.max_input_tokens if budget.max_input_tokens is not None else 'unbounded'}, "
                "output "
                f"{budget.max_output_tokens if budget.max_output_tokens is not None else 'unbounded'}",
            )
        )
    if getattr(info, "budget_exhausted", ()):
        lines.append("Budget blockers: " + ", ".join(info.budget_exhausted))
    plan = getattr(info, "latest_plan", None)
    if plan is not None:
        lines.append(
            f"Latest plan: {plan.plan_id}, {'accepted' if plan.accepted else 'proposed'}, "
            f"progress {plan.completed_steps}/{len(plan.steps)}"
        )
        if getattr(plan, "predecessor_plan_id", None) is not None:
            lines.append(
                f"Plan revision: replaces {plan.predecessor_plan_id}; reason "
                f"{_safe_inline(plan.revision_reason)}"
            )
    reflection = getattr(info, "latest_reflection", None)
    if reflection is not None:
        lines.append(
            f"Latest reflection: {reflection.recommendation.value} - "
            f"{_safe_inline(reflection.summary)}"
        )
        if reflection.next_objective is not None:
            lines.append(f"Reflection next objective: {_safe_inline(reflection.next_objective)}")
    blocker = getattr(info, "latest_blocker", None)
    if blocker is not None:
        lines.append(f"Latest blocker: {blocker.category.value} - {_safe_inline(blocker.summary)}")
        lines.append(f"Blocker proposal tool: {_safe_inline(blocker.proposal_tool_use_id)}")
    checkpoint = getattr(info, "latest_checkpoint", None)
    if checkpoint is not None:
        lines.append(
            f"Task checkpoint: {checkpoint.checkpoint_id}, source record "
            f"#{checkpoint.source_sequence}, unresolved criteria "
            f"{len(checkpoint.unresolved_criterion_indices)}"
        )
    verifications = getattr(info, "acceptance_verifications", ())
    verified = {item.criterion_index for item in verifications}
    if info.acceptance_criteria:
        lines.append(f"Acceptance verified: {len(verified)}/{len(info.acceptance_criteria)}")
    for verification in verifications:
        source = getattr(getattr(verification, "source", None), "value", "user")
        lines.append(
            f"  Criterion {verification.criterion_index}: verified by {source} - "
            f"{_safe_inline(verification.evidence)}"
        )
    for check in getattr(info, "acceptance_checks", ()):
        lines.append(
            f"  Check {check.criterion_index}: {check.outcome.value} by {check.source.value} - "
            f"{_safe_inline(check.evidence)}"
        )
    if getattr(info, "terminal_outcome", None) is not None:
        lines.append(f"Terminal outcome: {info.terminal_outcome.value}")
        if getattr(info, "terminal_reason", None) is not None:
            lines.append(f"Terminal reason: {_safe_inline(info.terminal_reason)}")
    return "\n".join(lines)


def render_task_verification_result(result) -> str:
    """Render one bounded Host/reviewer check operation and resulting Task state."""
    lines = ["Acceptance check results:"]
    if not result.checks:
        lines.append("  No eligible unverified criteria were checked.")
    for check in result.checks:
        lines.append(
            f"  {check.criterion_index}. {check.outcome.value} by {check.source.value}: "
            f"{_safe_inline(check.evidence)}"
        )
    lines.append(f"Auto-completed: {'yes' if result.auto_completed else 'no'}")
    lines.append(render_task_info(result.task))
    return "\n".join(lines)


def render_task_next_action(action: TaskNextAction) -> str:
    """Render one read-only foreground-driver decision preview."""
    lines = [
        f"Next Task decision: {action.reason.value}",
        f"Action: {_safe_inline(action.description)}",
        f"Would mutate Task/workspace: {'yes' if action.mutates else 'no'}",
        f"Would call provider: {'yes' if action.provider_call else 'no'}",
    ]
    if action.reviewer_paths:
        lines.append(
            f"Independent review paths: {action.reviewer_paths}; tools are disabled, but provider tokens/API cost may apply."
        )
    return "\n".join(lines)


def render_task_timeline(info: TaskInfoView) -> str:
    """Render all bounded Stage outcomes without Session dialogue or tool bodies."""
    lines = [
        f"Task timeline: {_safe_inline(getattr(info, 'name', info.objective))}",
        f"Task ID: {info.task_id}",
        f"Status: {info.status.value}",
    ]
    if not info.stages:
        lines.append("No Stages recorded.")
    for stage in info.stages:
        line = (
            f"#{stage.stage_number} "
            f"[{getattr(getattr(stage, 'kind', None), 'value', 'execution')}] {stage.outcome}: "
            f"{_safe_inline(stage.objective)}"
        )
        if stage.turn_number is not None:
            line += f" -> Session turn #{stage.turn_number}"
        elif stage.failure_reason is not None:
            line += f" -> {stage.failure_reason.value}"
        lines.append(line)
        usage = getattr(stage, "usage", None)
        if usage is not None:
            lines.append(
                "  Usage: "
                f"{usage.provider_invocations} provider, {usage.tool_requests} tools, "
                f"{usage.input_tokens} input, {usage.output_tokens} output tokens"
            )
    plan = getattr(info, "latest_plan", None)
    if plan is not None:
        lines.append(
            f"Plan {plan.plan_id}: {'accepted' if plan.accepted else 'proposed'}, "
            f"{plan.completed_steps}/{len(plan.steps)} steps committed"
        )
        lines.extend(
            f"  {index}. {_safe_inline(step)}" for index, step in enumerate(plan.steps, start=1)
        )
    reflection = getattr(info, "latest_reflection", None)
    if reflection is not None:
        lines.append(
            f"Reflection {reflection.reflection_id}: {reflection.recommendation.value} - "
            f"{_safe_inline(reflection.summary)}"
        )
    blocker = getattr(info, "latest_blocker", None)
    if blocker is not None:
        lines.append(
            f"Blocker for Stage #{blocker.stage_number}: {blocker.category.value} - "
            f"{_safe_inline(blocker.summary)}"
        )
    checkpoint = getattr(info, "latest_checkpoint", None)
    if checkpoint is not None:
        lines.append(
            f"Checkpoint {checkpoint.checkpoint_id}: source record #{checkpoint.source_sequence}, "
            f"unresolved {len(checkpoint.unresolved_criterion_indices)}"
        )
    for verification in getattr(info, "acceptance_verifications", ()):
        lines.append(
            f"Acceptance #{verification.criterion_index} for Stage "
            f"{getattr(verification, 'completion_stage_id', '<legacy>')}: "
            f"verified at {verification.verified_at}"
        )
    if getattr(info, "budget_exhausted", ()):
        lines.append("Budget blockers: " + ", ".join(info.budget_exhausted))
    if getattr(info, "terminal_outcome", None) is not None:
        lines.append(f"Terminal outcome: {info.terminal_outcome.value}")
        if getattr(info, "terminal_reason", None) is not None:
            lines.append(f"Terminal reason: {_safe_inline(info.terminal_reason)}")
    return "\n".join(lines)


def render_context_meter(
    report: ContextFitReport,
    *,
    invocation_index: int,
    invocation_limit: int,
) -> str:
    """Render one bounded input/reserve/remaining context meter."""
    input_tokens = report.input_count.input_tokens
    window = report.context_window_limit
    prefix = f"[context {invocation_index}/{invocation_limit}]"
    if input_tokens is None or window is None:
        input_text = _format_tokens(input_tokens) if input_tokens is not None else "unknown"
        window_text = _format_tokens(window) if window is not None else "unknown"
        return (
            f"{prefix} input {input_text} + reserve "
            f"{_format_tokens(report.requested_output_tokens)} / {window_text} · "
            f"{report.input_count.method.value}"
        )
    bar = _context_bar(input_tokens, report.requested_output_tokens, window)
    return (
        f"{prefix} [{bar}] input {_format_tokens(input_tokens)} + reserve "
        f"{_format_tokens(report.requested_output_tokens)} / {_format_tokens(window)} · "
        f"{report.input_count.method.value}"
    )


def render_usage_summary(usage: RuntimeUsageSnapshot, *, compact: bool = False) -> str:
    """Render process-local actual usage without converting unknown calls to zero."""
    latest = usage.latest_invocation
    if latest is None:
        return "No provider generation usage recorded for the current runtime."
    if compact:
        return (
            f"Turn usage: {_totals_inline(usage.turn_totals)}\n"
            f"Profile usage: {_totals_inline(usage.profile_turn_totals)}"
            f" · compaction {_totals_inline(usage.profile_compaction_totals)}"
            f" · review {_totals_inline(usage.profile_review_totals)}"
        )
    latest_text = (
        "unknown"
        if latest.usage is None
        else (
            f"{_format_tokens(latest.usage.input_tokens)} in / "
            f"{_format_tokens(latest.usage.output_tokens)} out"
        )
    )
    lines = [
        f"Latest invocation: #{latest.sequence} ({latest.kind.value}) {latest_text}",
        f"Latest turn: {_totals_inline(usage.turn_totals)}",
    ]
    for index, record in enumerate(usage.latest_turn, start=1):
        detail = (
            "unknown"
            if record.usage is None
            else (
                f"{_format_tokens(record.usage.input_tokens)} in / "
                f"{_format_tokens(record.usage.output_tokens)} out"
            )
        )
        lines.append(f"  invocation {index}: {detail}")
    latest_compaction = usage.latest_compaction
    if latest_compaction is None:
        lines.append("Latest compaction invocation: none in current runtime")
    else:
        detail = (
            "unknown"
            if latest_compaction.usage is None
            else (
                f"{_format_tokens(latest_compaction.usage.input_tokens)} in / "
                f"{_format_tokens(latest_compaction.usage.output_tokens)} out"
            )
        )
        lines.append(f"Latest compaction invocation: #{latest_compaction.sequence} {detail}")
    latest_review = usage.latest_review
    if latest_review is None:
        lines.append("Latest review invocation: none in current runtime")
    else:
        detail = (
            "unknown"
            if latest_review.usage is None
            else (
                f"{_format_tokens(latest_review.usage.input_tokens)} in / "
                f"{_format_tokens(latest_review.usage.output_tokens)} out"
            )
        )
        lines.append(f"Latest review invocation: #{latest_review.sequence} {detail}")
    lines.extend(
        (
            f"Current profile turns: {_totals_inline(usage.profile_turn_totals)}",
            f"Current profile compaction: {_totals_inline(usage.profile_compaction_totals)}",
            f"Current profile review: {_totals_inline(usage.profile_review_totals)}",
            "Scope: current process and runtime target; /provider use or /model resets totals.",
        )
    )
    return "\n".join(lines)


def render_durable_usage_summary(
    usage: DurableUsageSnapshot,
    *,
    turns: bool = False,
) -> str:
    """Render replayed Session usage while preserving legacy-unavailable evidence."""
    if turns:
        if not usage.operations:
            return "No committed or failed turn usage is available in this Session."
        lines = ["Recent Session turn usage:"]
        for operation in usage.operations:
            target = operation.provider_id
            if operation.model is not None:
                target += f"/{operation.model}"
            totals = operation.totals
            detail = "legacy usage unavailable" if totals is None else _totals_inline(totals)
            lines.append(
                f"  record #{operation.record_sequence} {operation.outcome} · {target} · {detail}"
            )
        return "\n".join(lines)

    turn_totals = ProviderUsageTotals()
    compaction_totals = ProviderUsageTotals()
    for operation in usage.operations:
        totals = operation.totals
        if totals is None:
            continue
        destination = turn_totals if operation.operation == "turn" else compaction_totals
        for invocation in operation.invocations or ():
            destination = destination.add(invocation.usage)
        if operation.operation == "turn":
            turn_totals = destination
        else:
            compaction_totals = destination
    return "\n".join(
        (
            f"Session usage: {_totals_inline(usage.totals)}",
            f"Turns: {_totals_inline(turn_totals)}",
            f"Compaction: {_totals_inline(compaction_totals)}",
            f"Operations: {len(usage.operations)} recorded · "
            f"legacy usage unavailable={usage.unavailable_operations}",
            "Scope: durable current Session audit; resume and process restart preserve it.",
        )
    )


def _safe_inline(value: str) -> str:
    """Escape control characters before rendering persisted text in a terminal."""
    rendered = repr(value)
    return rendered[1:-1]


def _cap_rendered_text(text: str, limit: int, marker: str) -> str:
    payload = text.encode("utf-8")
    if len(payload) <= limit:
        return text
    suffix = "\n" + marker
    available = limit - len(suffix.encode("utf-8"))
    prefix = payload[:available].decode("utf-8", errors="ignore")
    return prefix + suffix


def _escape_terminal_text(value: str) -> str:
    rendered: list[str] = []
    for character in value:
        code = ord(character)
        if character in {"\n", "\t"} or (code >= 0x20 and code != 0x7F):
            rendered.append(character)
        else:
            rendered.append(f"\\x{code:02x}")
    return "".join(rendered)


def _tool_ledger_fields(ledger: object) -> list[str]:
    """Render the same derived aggregate fields for live and durable views."""
    fields = [
        f"requested={ledger.requested}",
        f"admitted={ledger.admitted}",
        f"dispatched={ledger.dispatched}",
        f"succeeded={ledger.count(ToolRequestOutcome.SUCCEEDED)}",
    ]
    labels = (
        (ToolRequestOutcome.ERROR, "error"),
        (ToolRequestOutcome.DENIED, "denied"),
        (ToolRequestOutcome.REJECTED, "rejected"),
        (ToolRequestOutcome.CANCELLED, "cancelled"),
        (ToolRequestOutcome.FAILED, "failed"),
        (ToolRequestOutcome.PARTIAL, "partial"),
        (ToolRequestOutcome.OUTCOME_UNKNOWN, "unknown"),
        (ToolRequestOutcome.SKIPPED_AFTER_FAILURE, "skipped"),
        (ToolRequestOutcome.REJECTED_OVER_BUDGET, "over-budget"),
    )
    for outcome, label in labels:
        count = ledger.count(outcome)
        if count:
            fields.append(f"{label}={count}")
    return fields


def _count_label(value: int, label: str) -> str:
    suffix = label if value == 1 else f"{label}s"
    return f"{value} {suffix}"


def _toolbar_runtime_label(status: RuntimeStatusView | None) -> str | None:
    if status is None:
        return None
    if status.mode == "fake":
        raw = "fake"
    else:
        raw = status.selected_model or status.profile or status.provider_id or "unknown"
    return _truncate(_safe_toolbar_text(raw), _TOOLBAR_MODEL_WIDTH)


def _toolbar_session_label(session: SessionInfoView) -> str:
    markers = []
    if getattr(session, "pinned", False):
        markers.append("pinned")
    if getattr(session, "archived", False):
        markers.append("archived")
    suffix = f" [{' '.join(markers)}]" if markers else ""
    return _truncate(_safe_toolbar_text(f"{session.name}{suffix}"), 32)


def render_session_title_fallback_reason(reason: SessionTitleFallbackReason) -> str:
    return {
        SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT: "provider output limit",
        SessionTitleFallbackReason.PROVIDER_FAILURE: "provider failure",
        SessionTitleFallbackReason.INVALID_CANDIDATE: "invalid model title",
        SessionTitleFallbackReason.DUPLICATE_TITLE: "duplicate model titles",
        SessionTitleFallbackReason.INVOCATION_BUDGET: "provider invocation budget exhausted",
    }[reason]


def _toolbar_context_label(report: ContextFitReport) -> str:
    input_tokens = report.input_count.input_tokens
    window = report.context_window_limit
    if input_tokens is None or window is None:
        return "ctx unknown"
    percent = min(999, round((input_tokens + report.requested_output_tokens) * 100 / window))
    return f"ctx {_context_bar(input_tokens, report.requested_output_tokens, window)} {percent}%"


def _context_bar(input_tokens: int, reserve_tokens: int, window_tokens: int) -> str:
    cells = 10
    input_cells = min(cells, (input_tokens * cells + window_tokens - 1) // window_tokens)
    reserve_cells = min(
        cells - input_cells,
        (reserve_tokens * cells + window_tokens - 1) // window_tokens,
    )
    return "█" * input_cells + "▒" * reserve_cells + "░" * (cells - input_cells - reserve_cells)


def _totals_inline(totals: ProviderUsageTotals) -> str:
    return (
        f"{_format_tokens(totals.input_tokens)} in / "
        f"{_format_tokens(totals.output_tokens)} out · "
        f"known={totals.known_invocations} unknown={totals.unknown_invocations}"
    )


def _format_tokens(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _context_pressure(report: ContextFitReport | None) -> tuple[str, MessageKind]:
    if report is None:
        return "unknown (current runtime cannot assess this context)", "warning"
    if report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED:
        return "output reserve exceeds the model limit", "error"
    input_tokens = report.input_count.input_tokens
    window = report.context_window_limit
    if input_tokens is None or window is None:
        return "unknown (input count or context window is unavailable)", "warning"
    used = input_tokens + report.requested_output_tokens
    percent = min(999, round(used * 100 / window))
    if used > window:
        return f"overflow ({percent}% of window)", "error"
    if used * 100 >= window * 90:
        return f"near full ({percent}%); next prompt may auto-compact", "warning"
    if used * 100 >= window * 80:
        return f"auto-compact range ({percent}%); threshold is 80%", "warning"
    if used * 100 >= window * 70:
        return f"approaching 80% threshold ({percent}%)", "warning"
    return f"normal ({percent}% of window)", "info"


def _toolbar_workspace_label(cwd: Path) -> str:
    try:
        relative = cwd.relative_to(Path.home())
    except ValueError:
        raw = cwd.as_posix()
    else:
        raw = "~" if relative == Path(".") else f"~/{relative.as_posix()}"
    return _truncate(_safe_toolbar_text(raw), _TOOLBAR_WORKSPACE_WIDTH)


def _safe_toolbar_text(value: object) -> str:
    projected = "".join(character if character.isprintable() else "?" for character in str(value))
    return projected or "unknown"


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return f"{value[: width - 3]}..."


def _ansi(text: str, code: str, *, readline: bool) -> str:
    if not readline:
        return f"{code}{text}{RESET}"
    return f"{_READLINE_START}{code}{_READLINE_END}{text}{_READLINE_START}{RESET}{_READLINE_END}"
