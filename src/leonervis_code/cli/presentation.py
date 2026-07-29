"""Pure terminal presentation for the Leonervis Code CLI."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    ProviderInvocationPreflighted,
    ProviderInvocationUsageReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestSkipped,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
)
from leonervis_code.core.contracts import ToolRequestOutcome
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.request_context import ContextFitDecision, ContextFitReport
from leonervis_code.session import (
    AutoCompactionCommitted,
    AutoCompactionNotApplied,
    AutoCompactionStarted,
    DurableUsageSnapshot,
    TurnUsageCompleted,
)
from leonervis_code.session_store import MAX_TOOL_LEDGER_QUERY_TURNS
from leonervis_code.providers.usage import RuntimeUsageSnapshot, ProviderUsageTotals

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


HELP_TOPICS = ("session", "tools", "git", "context", "provider", "input")
HELP_TEXT = (
    "Host command groups:\n"
    "  /help session   Session history, browsing, and resume\n"
    "  /help tools     Action Audit and durable tool outcomes\n"
    "  /help git       Read-only Git changes and history\n"
    "  /help context   Context, usage, output budget, and compaction\n"
    "  /help provider  Provider and model selection\n"
    "  /help input     Prompt editing, cancellation, and exit\n"
    "Use /help <group> for commands. Slash commands are Host-only and do not call the model."
)
SESSION_HELP = (
    "Session commands:\n"
    "  /session show\n"
    "  /session list [1-100] [open|closed] [model=<name>]\n"
    "  /session new\n"
    "  /resume <latest|session-id>\n"
    "  /history <count>"
)
TOOLS_HELP = (
    "Tool and audit commands:\n"
    "  /actions [1-100] [status=<status>] [tool=<name>]\n"
    "  /tools [1-20]\n"
    "  /tools details [1-20]\n"
    "  /tool-details [compact|full]\n"
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
    "  /output [tokens|reset]\n"
    "  /model <model>"
)
INPUT_HELP = (
    "Input controls:\n"
    "  Enter submits the current prompt\n"
    "  Alt+Enter inserts a newline; use Esc then Enter if Alt is intercepted\n"
    "  Ctrl-C clears a draft, cancels an active turn, or exits when idle and empty\n"
    "  Ctrl-D deletes ahead of the cursor or exits when the input is empty\n"
    "  /clear clears terminal output\n"
    "  /exit or /quit exits the REPL"
)

HELP_BY_TOPIC = {
    "session": SESSION_HELP,
    "tools": TOOLS_HELP,
    "git": GIT_HELP,
    "context": CONTEXT_HELP,
    "provider": PROVIDER_HELP,
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


class ConversationTurnView(Protocol):
    user: object
    assistant: object


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
) -> str:
    """Render a bounded model and workspace status line below the TTY editor."""
    fields = []
    runtime_label = _toolbar_runtime_label(status)
    if runtime_label is not None:
        fields.append(runtime_label)
    if usage is not None and usage.latest_context is not None:
        fields.append(_toolbar_context_label(usage.latest_context))
    fields.append(_toolbar_workspace_label(cwd))
    text = f"  {' · '.join(fields)}"
    return _ansi(text, BLUE, readline=False) if color else text


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


def render_host_message(text: str, kind: MessageKind, *, color: bool) -> str:
    """Render non-assistant terminal information as an indented secondary block."""
    indented = indent_terminal_block(text)
    if not color or kind == "plain":
        return indented
    if kind == "info":
        return f"{DIM}{indented}{RESET}"
    if kind == "success":
        return f"{DIM}{GREEN}{indented}{RESET}"
    return render_message(indented, kind, color=color)


def render_turn_trace(text: str, kind: MessageKind, *, color: bool) -> str:
    """Render Host-owned execution facts inside one assistant turn."""
    traced = indent_terminal_block(text, "  │ ")
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
    binding = getattr(info, "binding", None)
    model = _safe_inline(getattr(binding, "selected_model", None) or "<none>")
    provider = _safe_inline(getattr(binding, "provider_id", None) or "<unknown>")
    return (
        f"{info.session_id}{marker_text}: {turns}, {state}, created {info.created_at}, "
        f"runtime {provider}/{model}"
    )


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
        return (
            f"[tool {event.call_index}/{event.call_limit}] {event.status.value}{detail}",
            kind,
        )
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
    if isinstance(event, TurnUsageCompleted):
        return render_usage_summary(event.usage, compact=True), "info"

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
    return (
        f"Session: {info.session_id}\n"
        f"Transcript: {info.path}\n"
        f"Turns: {info.turn_count}\n"
        f"Created: {info.created_at}"
    )


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
    lines.extend(
        (
            f"Current profile turns: {_totals_inline(usage.profile_turn_totals)}",
            f"Current profile compaction: {_totals_inline(usage.profile_compaction_totals)}",
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
