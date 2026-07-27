"""Pure terminal presentation for the Leonervis Code CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestStarted,
)
from leonervis_code.providers.request_context import ContextFitDecision, ContextFitReport
from leonervis_code.session import (
    AutoCompactionCommitted,
    AutoCompactionNotApplied,
    AutoCompactionStarted,
)

RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
_READLINE_START = "\001"
_READLINE_END = "\002"
_TOOLBAR_MODEL_WIDTH = 36
_TOOLBAR_WORKSPACE_WIDTH = 64
DEFAULT_ACTION_AUDIT_COUNT = 20
MAX_ACTION_AUDIT_COUNT = 100
CLEAR_SCREEN = "\x1b[2J\x1b[H"
MessageKind = Literal["plain", "info", "success", "warning", "error"]

HELP_TEXT = (
    "Commands: /help, /history <count>, /actions [count], /session, /provider, /status, "
    "/context, /compact, "
    "/model <model>, /resume <latest|id>, /clear, /exit, /quit. Enter submits; "
    "Alt+Enter inserts a newline (press Esc then Enter if Alt is intercepted). Ctrl-C "
    "cancels a draft or exits when empty; Ctrl-D exits when empty."
)
SESSION_HELP = (
    "Session commands:\n"
    "  /session show\n"
    "  /session list\n"
    "  /session new\n"
    "  /resume <latest|session-id>"
)
PROVIDER_HELP = (
    "Provider commands:\n"
    "  /provider list\n"
    "  /provider current\n"
    "  /provider use <name>\n"
    "  /status\n"
    "  /model <model>"
)


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


def render_prompt_toolbar(
    status: RuntimeStatusView | None,
    cwd: Path,
    *,
    color: bool,
) -> str:
    """Render a bounded model and workspace status line below the TTY editor."""
    fields = []
    runtime_label = _toolbar_runtime_label(status)
    if runtime_label is not None:
        fields.append(runtime_label)
    fields.append(_toolbar_workspace_label(cwd))
    text = f"  {' · '.join(fields)}"
    return _ansi(text, BLUE, readline=False) if color else text


def render_message(text: str, kind: MessageKind, *, color: bool) -> str:
    """Apply a traditional semantic color without changing message text."""
    if not color or kind == "plain":
        return text
    code = {
        "info": BLUE,
        "success": GREEN,
        "warning": YELLOW,
        "error": RED,
    }[kind]
    return f"{code}{text}{RESET}"


def render_recent_history(turns: tuple[ConversationTurnView, ...], count: int) -> str:
    """Render the most recent complete conversation turns in chronological order."""
    recent_turns = turns[-count:]
    if not recent_turns:
        return "No conversation turns yet."
    return "\n\n".join(
        f"User: {turn.user.text}\nAssistant: {turn.assistant.text}" for turn in recent_turns
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
    return f"{info.session_id}{marker_text}: {turns}, {state}, created {info.created_at}"


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
        f"Requested output reserve: {output_reserve}"
    )


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


def render_prompt_event(event: object) -> tuple[str, MessageKind]:
    """Render one safe ephemeral prompt lifecycle event."""
    if isinstance(event, AssistantToolTextReceived):
        return event.text, "plain"
    if isinstance(event, ToolRequestStarted):
        detail = f" {event.safe_summary}" if event.safe_summary else ""
        return (
            f"[tool {event.call_index}/{event.call_limit}] {event.tool_name}{detail}",
            "info",
        )
    if isinstance(event, ToolRequestFinished):
        detail = f" code={event.result_code}" if event.result_code is not None else ""
        if event.truncated:
            detail += " truncated=true"
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


def _safe_inline(value: str) -> str:
    """Escape control characters before rendering persisted text in a terminal."""
    rendered = repr(value)
    return rendered[1:-1]


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
