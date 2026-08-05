"""Typed assistant/tool events for one sequential model-requested action loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import PurePosixPath, PureWindowsPath
import unicodedata

from leonervis_code.core.contracts import (
    MAX_ASSISTANT_TOOL_TEXT_BYTES,
    MAX_ASSISTANT_TOOL_TEXT_CHARACTERS,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
)
from leonervis_code.core.task_admission import TaskAdmissionProposal
from leonervis_code.mcp.client import McpNotificationKind
from leonervis_code.providers.streaming import (
    ProviderSearchObservation,
    ProviderSearchPhase,
    ProviderTextDelta,
)
from leonervis_code.providers.request_context import ContextFitReport
from leonervis_code.providers.usage import ProviderTokenUsage

MAX_TOOL_EVENT_SUMMARY_CHARACTERS = 512
MAX_TOOL_EVENT_VALUE_CHARACTERS = 160
MAX_TOOL_EVENT_DETAIL_LINES = 4
MAX_TOOL_EVENT_DETAIL_BYTES = 8 * 1024
MAX_TOOL_EVENT_ARGV_LINE_BYTES = 7 * 1024
MAX_TOOL_RESULT_DETAIL_LINES = 6
MAX_TOOL_RESULT_DETAIL_BYTES = 2 * 1024


class ToolEventStatus(StrEnum):
    """Host-observed terminal status for one normally dispatched tool request."""

    SUCCEEDED = "succeeded"
    ERROR = "error"
    DENIED = "denied"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PARTIAL = "partial"
    OUTCOME_UNKNOWN = "outcome-unknown"


@dataclass(frozen=True)
class AssistantToolTextReceived:
    """Exact bounded assistant text atomically accompanying one tool request."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("assistant tool text event must contain non-empty text")
        try:
            encoded = self.text.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("assistant tool text event must be valid UTF-8") from None
        if "\x00" in self.text:
            raise ValueError("assistant tool text event must not contain NUL")
        if (
            len(self.text) > MAX_ASSISTANT_TOOL_TEXT_CHARACTERS
            or len(encoded) > MAX_ASSISTANT_TOOL_TEXT_BYTES
        ):
            raise ValueError("assistant tool text event exceeds the supported size")


@dataclass(frozen=True)
class AssistantResponseTextDeltaReceived:
    """Exact ephemeral text from one incomplete provider response stream."""

    text: str

    def __post_init__(self) -> None:
        ProviderTextDelta(self.text)


@dataclass(frozen=True)
class ProviderSearchActivityReceived:
    """Content-free Provider-owned search progress, never a Host ToolUse."""

    phase: ProviderSearchPhase

    def __post_init__(self) -> None:
        if type(self.phase) is not ProviderSearchPhase:
            raise ValueError("provider search activity phase is invalid")


@dataclass(frozen=True)
class ProviderSearchSummaryReceived:
    """One terminal content-free Provider search observation."""

    observation: ProviderSearchObservation

    def __post_init__(self) -> None:
        if type(self.observation) is not ProviderSearchObservation:
            raise ValueError("provider search summary is invalid")


@dataclass(frozen=True)
class McpNotificationActivityReceived:
    """One content-free MCP notification class, emitted at most once per request."""

    kind: McpNotificationKind

    def __post_init__(self) -> None:
        if type(self.kind) is not McpNotificationKind:
            raise ValueError("MCP notification activity kind is invalid")


@dataclass(frozen=True)
class AssistantToolTextStreamCompleted:
    """Resolve preceding deltas as companion text for one complete tool request."""

    text: str

    def __post_init__(self) -> None:
        AssistantToolTextReceived(self.text)


@dataclass(frozen=True)
class AssistantFinalTextStreamCommitted:
    """Confirm that preceding final-text deltas were durably committed."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("committed assistant stream text must be non-empty")
        ProviderTextDelta(self.text)


@dataclass(frozen=True)
class ToolRequestStarted:
    tool_name: str
    call_index: int
    call_limit: int
    safe_summary: str
    safe_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_event_identity(self.tool_name, self.call_index, self.call_limit)
        _validate_safe_text(self.safe_summary, "tool event summary")
        _validate_safe_details(self.safe_details)


@dataclass(frozen=True)
class ToolResultDetails:
    """Bounded content-free Host metadata for one completed tool execution."""

    compact_summary: str
    full_details: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.compact_summary or not self.full_details:
            raise ValueError("tool result details must not be empty")
        _validate_safe_text(self.compact_summary, "tool result summary")
        _validate_safe_details(
            self.full_details,
            max_lines=MAX_TOOL_RESULT_DETAIL_LINES,
            max_bytes=MAX_TOOL_RESULT_DETAIL_BYTES,
        )


@dataclass(frozen=True)
class ToolRequestFinished:
    tool_name: str
    call_index: int
    call_limit: int
    status: ToolEventStatus
    result_code: str | None = None
    truncated: bool = False
    result_details: ToolResultDetails | None = None

    def __post_init__(self) -> None:
        _validate_event_identity(self.tool_name, self.call_index, self.call_limit)
        if type(self.status) is not ToolEventStatus:
            raise ValueError("tool event status is invalid")
        if self.result_code is not None:
            _validate_safe_text(self.result_code, "tool event result code")
        if type(self.truncated) is not bool:
            raise ValueError("tool event truncated flag is invalid")
        if self.result_details is not None and type(self.result_details) is not ToolResultDetails:
            raise ValueError("tool event result details are invalid")


@dataclass(frozen=True)
class ToolRequestLimited:
    tool_name: str
    call_index: int
    call_limit: int
    safe_summary: str

    def __post_init__(self) -> None:
        _validate_event_identity(self.tool_name, self.call_index, self.call_limit)
        if self.call_index <= self.call_limit:
            raise ValueError("limited tool event must exceed the call limit")
        _validate_safe_text(self.safe_summary, "tool event summary")


@dataclass(frozen=True)
class ToolRequestSkipped:
    tool_name: str
    call_index: int
    call_limit: int
    reason_code: str

    def __post_init__(self) -> None:
        _validate_event_identity(self.tool_name, self.call_index, self.call_limit)
        _validate_safe_text(self.reason_code, "tool skip reason")


@dataclass(frozen=True)
class ToolTurnSummaryCommitted:
    """Expose Host accounting only after the containing turn is durable."""

    ledger: ToolTurnLedger

    def __post_init__(self) -> None:
        if type(self.ledger) is not ToolTurnLedger or not self.ledger.entries:
            raise ValueError("tool turn summary requires a non-empty ledger")


@dataclass(frozen=True)
class ProviderInvocationPreflighted:
    invocation_index: int
    invocation_limit: int
    report: ContextFitReport

    def __post_init__(self) -> None:
        _validate_invocation_identity(self.invocation_index, self.invocation_limit)
        if type(self.report) is not ContextFitReport:
            raise ValueError("provider invocation context report is invalid")


@dataclass(frozen=True)
class ProviderInvocationUsageReceived:
    invocation_index: int
    invocation_limit: int
    usage: ProviderTokenUsage | None

    def __post_init__(self) -> None:
        _validate_invocation_identity(self.invocation_index, self.invocation_limit)
        if self.usage is not None and type(self.usage) is not ProviderTokenUsage:
            raise ValueError("provider invocation token usage is invalid")


@dataclass(frozen=True)
class TaskAdmissionProposed:
    """Announce one proposal only after its containing Session Turn commits."""

    admission_id: str
    objective_summary: str
    acceptance_criteria_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.admission_id, str) or not self.admission_id.startswith("tap-v1-"):
            raise ValueError("Task admission event ID is invalid")
        _validate_safe_text(self.objective_summary, "Task admission objective summary")
        if type(self.acceptance_criteria_count) is not int or not (
            1 <= self.acceptance_criteria_count <= 16
        ):
            raise ValueError("Task admission criterion count is invalid")

    @classmethod
    def from_proposal(cls, proposal: TaskAdmissionProposal) -> TaskAdmissionProposed:
        if type(proposal) is not TaskAdmissionProposal:
            raise ValueError("Task admission proposal event source is invalid")
        return cls(
            proposal.admission_id,
            _safe_inline(proposal.objective),
            len(proposal.acceptance_criteria),
        )


@dataclass(frozen=True)
class TaskLifecycleCommitted:
    """Announce one lifecycle mutation only after both durable commit boundaries."""

    operation: str
    task_id: str
    foreground_max_stages: int | None = None

    def __post_init__(self) -> None:
        if self.operation not in {
            "accept-admission",
            "accept-plan",
            "confirm-completion",
        }:
            raise ValueError("Task lifecycle event operation is invalid")
        try:
            from uuid import UUID

            parsed = UUID(self.task_id)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Task lifecycle event Task ID is invalid") from None
        if parsed.version != 4 or str(parsed) != self.task_id:
            raise ValueError("Task lifecycle event Task ID is invalid")
        if self.foreground_max_stages is not None and (
            type(self.foreground_max_stages) is not int or not 1 <= self.foreground_max_stages <= 16
        ):
            raise ValueError("Task lifecycle event foreground Stage limit is invalid")
        if self.operation == "confirm-completion" and self.foreground_max_stages is not None:
            raise ValueError("completed Task lifecycle event cannot request a foreground handoff")


ToolPromptEvent = (
    ToolRequestStarted
    | ToolRequestFinished
    | ToolRequestLimited
    | ToolRequestSkipped
    | ToolTurnSummaryCommitted
)
AssistantStreamEvent = (
    AssistantResponseTextDeltaReceived
    | AssistantToolTextStreamCompleted
    | AssistantFinalTextStreamCommitted
)
AgentPromptEvent = (
    AssistantToolTextReceived
    | AssistantStreamEvent
    | ToolPromptEvent
    | ProviderInvocationPreflighted
    | ProviderInvocationUsageReceived
    | ProviderSearchActivityReceived
    | ProviderSearchSummaryReceived
    | McpNotificationActivityReceived
    | TaskAdmissionProposed
    | TaskLifecycleCommitted
)


def _validate_invocation_identity(index: int, limit: int) -> None:
    if type(index) is not int or type(limit) is not int or not 1 <= index <= limit:
        raise ValueError("provider invocation event identity is invalid")


@dataclass(frozen=True)
class ToolDispatchResult:
    """Keep Host-only outcome metadata beside the exact model-visible ToolResult."""

    tool_result: ToolResult
    status: ToolEventStatus
    result_code: str | None = None
    result_details: ToolResultDetails | None = None

    def __post_init__(self) -> None:
        if type(self.tool_result) is not ToolResult:
            raise ValueError("tool dispatch result is invalid")
        if type(self.status) is not ToolEventStatus:
            raise ValueError("tool dispatch status is invalid")
        if self.status == ToolEventStatus.SUCCEEDED and self.tool_result.is_error:
            raise ValueError("successful tool dispatch cannot carry an error result")
        if self.status != ToolEventStatus.SUCCEEDED and not self.tool_result.is_error:
            raise ValueError("non-successful tool dispatch must carry an error result")
        if self.result_code is not None and (
            not isinstance(self.result_code, str) or not self.result_code
        ):
            raise ValueError("tool dispatch result code is invalid")
        if self.result_details is not None and type(self.result_details) is not ToolResultDetails:
            raise ValueError("tool dispatch result details are invalid")


def infer_tool_dispatch_result(result: ToolResult) -> ToolDispatchResult:
    """Describe legacy/direct dispatch results without parsing their untrusted content."""
    return ToolDispatchResult(
        tool_result=result,
        status=ToolEventStatus.ERROR if result.is_error else ToolEventStatus.SUCCEEDED,
    )


def safe_tool_request_summary(request: ToolUse) -> str:
    """Return a bounded terminal-safe summary that excludes content-bearing arguments."""
    try:
        arguments = request.arguments.as_mapping()
    except Exception:
        return "arguments=<redacted>"

    name = request.name
    if name in {
        "read_file",
        "stat_path",
        "list_directory",
        "delete_file",
        "delete_directory",
        "mkdir",
    }:
        summary = f"path={_safe_path(arguments.get('path'))}"
    elif name == "glob":
        summary = f"pattern={_safe_path(arguments.get('pattern'))}"
    elif name == "grep":
        summary = (
            f"include={_safe_path(arguments.get('include'))} "
            f"query_bytes={_utf8_size(arguments.get('query'))}"
        )
    elif name == "write_file":
        summary = (
            f"path={_safe_path(arguments.get('path'))} "
            f"content_bytes={_utf8_size(arguments.get('content'))}"
        )
    elif name == "edit_file":
        summary = (
            f"path={_safe_path(arguments.get('path'))} "
            f"old_bytes={_utf8_size(arguments.get('old_text'))} "
            f"new_bytes={_utf8_size(arguments.get('new_text'))}"
        )
    elif name == "run_command":
        argv = arguments.get("argv")
        executable = argv[0] if isinstance(argv, list) and argv else None
        argument_count = len(argv) - 1 if isinstance(argv, list) and argv else "<invalid>"
        summary = (
            f"command={_safe_executable(executable)} args={argument_count} "
            f"cwd={_safe_path(arguments.get('cwd'))} "
            f"timeout={_safe_number(arguments.get('timeout_seconds'))}s"
        )
    elif name in {"move_file", "copy_file", "move_directory"}:
        summary = (
            f"source={_safe_path(arguments.get('source'))} "
            f"destination={_safe_path(arguments.get('destination'))}"
        )
    elif name == "read_file_lines":
        summary = (
            f"path={_safe_path(arguments.get('path'))} "
            f"start_line={_safe_number(arguments.get('start_line'))} "
            f"line_count={_safe_number(arguments.get('line_count'))}"
        )
    elif name == "list_tree":
        summary = (
            f"path={_safe_path(arguments.get('path'))} "
            f"max_depth={_safe_number(arguments.get('max_depth'))}"
        )
    elif name == "grep_regex":
        summary = (
            f"include={_safe_path(arguments.get('include'))} "
            f"pattern_bytes={_utf8_size(arguments.get('pattern'))}"
        )
    elif name == "patch_file":
        edits = arguments.get("edits")
        edit_count = len(edits) if isinstance(edits, list) else "<invalid>"
        summary = f"path={_safe_path(arguments.get('path'))} edits={edit_count}"
    elif name == "git_status":
        summary = "repository=."
    elif name == "git_diff":
        summary = (
            f"scope={_safe_argument(arguments.get('scope'))} "
            f"path={_safe_path(arguments.get('path'))}"
        )
    elif name == "git_log":
        summary = (
            f"limit={_safe_number(arguments.get('limit'))} path={_safe_path(arguments.get('path'))}"
        )
    elif name == "git_show":
        summary = (
            f"commit={_safe_argument(arguments.get('commit_id'))} "
            f"path={_safe_path(arguments.get('path'))}"
        )
    elif name == "web_search":
        summary = (
            f"query_bytes={_utf8_size(arguments.get('query'))} "
            f"max_results={_safe_number(arguments.get('max_results'))}"
        )
    elif name == "web_fetch":
        summary = f"url=<redacted> format={_safe_argument(arguments.get('format'))}"
    elif name == "compare_files":
        summary = (
            f"left={_safe_path(arguments.get('left'))} right={_safe_path(arguments.get('right'))}"
        )
    elif name == "git_blame":
        summary = (
            f"path={_safe_path(arguments.get('path'))} "
            f"start_line={_safe_number(arguments.get('start_line'))} "
            f"line_count={_safe_number(arguments.get('line_count'))}"
        )
    elif name == "git_refs":
        summary = "repository=."
    elif name == "json_query":
        summary = (
            f"path={_safe_path(arguments.get('path'))} "
            f"pointer_bytes={_utf8_size(arguments.get('pointer'))}"
        )
    elif name in {"checksum_file", "archive_list"}:
        summary = f"path={_safe_path(arguments.get('path'))}"
    elif name == "download_file":
        summary = f"url=<redacted> path={_safe_path(arguments.get('path'))}"
    elif name == "task_propose_plan":
        steps = arguments.get("steps")
        summary = f"steps={len(steps) if isinstance(steps, list) else '<invalid>'}"
    elif name == "task_report_reflection":
        summary = (
            f"recommendation={_safe_argument(arguments.get('recommendation'))} "
            f"summary_bytes={_utf8_size(arguments.get('summary'))}"
        )
    elif name == "task_report_blocker":
        summary = (
            f"category={_safe_argument(arguments.get('category'))} "
            f"summary_bytes={_utf8_size(arguments.get('summary'))}"
        )
    elif name == "task_propose_completion":
        summary = "current_stage=true"
    else:
        summary = "arguments=<redacted>"
    return summary[:MAX_TOOL_EVENT_SUMMARY_CHARACTERS]


def safe_tool_request_details(request: ToolUse) -> tuple[str, ...]:
    """Return opt-in bounded details without exposing file or search contents."""
    if request.name != "run_command":
        return ()
    try:
        arguments = request.arguments.as_mapping()
    except Exception:
        return ("argv: <invalid>",)

    argv = arguments.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        argv_line = "argv: <invalid>"
        execution = "execution: unavailable"
    else:
        rendered_argv = json.dumps(
            argv,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        rendered_argv = _escape_terminal_controls(rendered_argv)
        argv_line = _bounded_detail_line("argv: ", rendered_argv, MAX_TOOL_EVENT_ARGV_LINE_BYTES)
        executable = _safe_executable(argv[0])
        shell_source_index = _shell_source_index(argv)
        if shell_source_index is None:
            execution = "execution: direct argv; Host shell parsing disabled"
        else:
            execution = (
                f"execution: shell interpreter {executable}; "
                f"shell source is argv[{shell_source_index}]"
            )
    cwd = f"cwd: {_safe_path(arguments.get('cwd'))}"
    timeout = f"timeout_seconds: {_safe_number(arguments.get('timeout_seconds'))}"
    return (argv_line, cwd, timeout, execution)


def safe_result_code(value: str | None) -> str | None:
    """Escape one stable Host result code before placing it in a terminal event."""
    if value is None:
        return None
    rendered = _safe_inline(value)
    return rendered or None


def _safe_argument(value: object) -> str:
    if not isinstance(value, str):
        return "<invalid>"
    rendered = repr(value)
    if len(rendered) <= MAX_TOOL_EVENT_VALUE_CHARACTERS:
        return rendered
    prefix = value[: max(1, MAX_TOOL_EVENT_VALUE_CHARACTERS // 2)]
    return f"{prefix!r}..."


def _safe_path(value: object) -> str:
    if not isinstance(value, str):
        return "<invalid>"
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return "<absolute>"
    return _safe_argument(value)


def _safe_executable(value: object) -> str:
    if not isinstance(value, str):
        return "<invalid>"
    name = PureWindowsPath(value).name if "\\" in value else PurePosixPath(value).name
    return _safe_argument(name)


def _shell_source_index(argv: list[str]) -> int | None:
    executable = PureWindowsPath(argv[0]).name if "\\" in argv[0] else PurePosixPath(argv[0]).name
    if executable not in {"bash", "dash", "ksh", "sh", "zsh"}:
        return None
    for index, argument in enumerate(argv[1:], start=1):
        if not argument.startswith("-") or argument == "-":
            break
        if argument == "--":
            break
        if argument.startswith("--"):
            continue
        if "c" in argument[1:] and index + 1 < len(argv):
            return index + 1
    return None


def _bounded_detail_line(prefix: str, value: str, limit: int) -> str:
    rendered = f"{prefix}{value}"
    encoded = rendered.encode("utf-8")
    if len(encoded) <= limit:
        return rendered
    suffix = f"... <truncated; rendered_bytes={len(encoded)}>"
    available = limit - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    clipped = value.encode("utf-8")[: max(0, available)].decode("utf-8", errors="ignore")
    return f"{prefix}{clipped}{suffix}"


def _escape_terminal_controls(value: str) -> str:
    rendered: list[str] = []
    for character in value:
        if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
            codepoint = ord(character)
            rendered.append(f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _safe_inline(value: str) -> str:
    rendered = repr(value)[1:-1]
    return rendered[:MAX_TOOL_EVENT_VALUE_CHARACTERS]


def _utf8_size(value: object) -> int | str:
    if not isinstance(value, str):
        return "<invalid>"
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return "<invalid>"


def _safe_number(value: object) -> int | str:
    return value if type(value) is int else "<invalid>"


def _validate_event_identity(tool_name: str, call_index: int, call_limit: int) -> None:
    if not isinstance(tool_name, str) or not tool_name or not tool_name.isascii():
        raise ValueError("tool event name is invalid")
    if type(call_index) is not int or call_index <= 0:
        raise ValueError("tool event call index is invalid")
    if type(call_limit) is not int or call_limit <= 0:
        raise ValueError("tool event call limit is invalid")


def _validate_safe_text(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) > MAX_TOOL_EVENT_SUMMARY_CHARACTERS:
        raise ValueError(f"{label} is invalid")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in value):
        raise ValueError(f"{label} contains terminal control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{label} is not valid UTF-8") from None


def _validate_safe_details(
    details: tuple[str, ...],
    *,
    max_lines: int = MAX_TOOL_EVENT_DETAIL_LINES,
    max_bytes: int = MAX_TOOL_EVENT_DETAIL_BYTES,
) -> None:
    if type(details) is not tuple or len(details) > max_lines:
        raise ValueError("tool event details are invalid")
    total_bytes = 0
    for detail in details:
        if not isinstance(detail, str) or not detail:
            raise ValueError("tool event detail is invalid")
        if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in detail):
            raise ValueError("tool event detail contains terminal control characters")
        try:
            total_bytes += len(detail.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("tool event detail is not valid UTF-8") from None
    if total_bytes > max_bytes:
        raise ValueError("tool event details exceed the supported size")
