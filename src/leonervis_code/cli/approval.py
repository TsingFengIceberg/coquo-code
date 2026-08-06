"""Bounded terminal presentation and resolution of informed approvals."""

from __future__ import annotations

from threading import Condition, Lock
from typing import Callable, TextIO
import unicodedata

from leonervis_code.core.action_coordinator import (
    ApprovalResolution,
    HumanApprovalRequest,
)
from leonervis_code.core.approval_preview import ApprovalPreview, ApprovalPreviewKind
from leonervis_code.core.cancellation import TurnCancellation

_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"
_RESET = "\x1b[0m"


def noninteractive_approval(_request: HumanApprovalRequest) -> ApprovalResolution:
    """Cancel policy asks in one-shot/automation mode instead of reading stdin."""
    return ApprovalResolution.CANCEL


def terminal_approval_handler(stdin: TextIO, stdout: TextIO, *, color: bool = False):
    """Build the bounded REPL-only informed confirmation boundary."""

    def handle(request: HumanApprovalRequest) -> ApprovalResolution:
        header = _approval_header(request)
        if request.preview is None:
            prompt = f"{header} [y/N/c]: "
        else:
            stdout.write(f"{header}\n")
            stdout.write(_render_preview(request.preview, color=color))
            prompt = "Approve this exact action? [y/N/c]: "

        for _ in range(3):
            try:
                stdout.write(prompt)
                stdout.flush()
                line = stdin.readline()
            except KeyboardInterrupt:
                stdout.write("\n")
                stdout.flush()
                return ApprovalResolution.CANCEL
            if line == "":
                return ApprovalResolution.CANCEL
            answer = line.strip().lower()
            if answer in {"y", "yes"}:
                return ApprovalResolution.ACCEPT
            if answer in {"", "n", "no"}:
                return ApprovalResolution.REJECT
            if answer in {"c", "cancel"}:
                return ApprovalResolution.CANCEL
            stdout.write("Please answer y, n, or c.\n")
        return ApprovalResolution.CANCEL

    return handle


class TerminalApprovalBroker:
    """Bridge synchronous action approval to one UI-owned input state."""

    def __init__(self, publish: Callable[[int, HumanApprovalRequest], None]) -> None:
        self._publish = publish
        self._lock = Lock()
        self._turn_id: int | None = None
        self._cancellation: TurnCancellation | None = None
        self._pending: _PendingApproval | None = None

    def activate(self, turn_id: int, cancellation: TurnCancellation) -> None:
        with self._lock:
            if self._turn_id is not None or self._pending is not None:
                raise RuntimeError("approval broker is already active")
            self._turn_id = turn_id
            self._cancellation = cancellation

    def deactivate(self, turn_id: int) -> None:
        with self._lock:
            if self._turn_id != turn_id:
                return
            pending = self._pending
            self._turn_id = None
            self._cancellation = None
            self._pending = None
        if pending is not None:
            pending.resolve(ApprovalResolution.CANCEL)

    def __call__(self, request: HumanApprovalRequest) -> ApprovalResolution:
        with self._lock:
            if self._turn_id is None or self._cancellation is None or self._pending is not None:
                return ApprovalResolution.CANCEL
            turn_id = self._turn_id
            cancellation = self._cancellation
            pending = _PendingApproval(request)
            self._pending = pending
        self._publish(turn_id, request)
        resolution = pending.wait(cancellation)
        with self._lock:
            if self._pending is pending:
                self._pending = None
        return resolution

    def resolve(self, resolution: ApprovalResolution) -> bool:
        if type(resolution) is not ApprovalResolution:
            raise ValueError("approval resolution is invalid")
        with self._lock:
            pending = self._pending
        return pending.resolve(resolution) if pending is not None else False

    @property
    def pending_request(self) -> HumanApprovalRequest | None:
        with self._lock:
            return self._pending.request if self._pending is not None else None


class _PendingApproval:
    def __init__(self, request: HumanApprovalRequest) -> None:
        self.request = request
        self._condition = Condition()
        self._resolution: ApprovalResolution | None = None

    def resolve(self, resolution: ApprovalResolution) -> bool:
        with self._condition:
            if self._resolution is not None:
                return False
            self._resolution = resolution
            self._condition.notify_all()
            return True

    def wait(self, cancellation: TurnCancellation) -> ApprovalResolution:
        with self._condition:
            while self._resolution is None:
                if cancellation.requested:
                    self._resolution = ApprovalResolution.CANCEL
                    break
                self._condition.wait(0.1)
            return self._resolution


def render_approval_request(request: HumanApprovalRequest, *, color: bool) -> str:
    """Render one bounded approval request without reading terminal input."""
    header = _approval_header(request)
    if request.preview is None:
        return f"{header}\nApprove this exact action? [y/N/c]"
    return f"{header}\n{_render_preview(request.preview, color=color)}Approve this exact action? [y/N/c]"


def _approval_header(request: HumanApprovalRequest) -> str:
    arguments = request.identity.arguments.as_mapping()
    if request.identity.tool_name == "run_command":
        argv = arguments.get("argv")
        cwd = arguments.get("cwd")
        timeout = arguments.get("timeout_seconds")
        rendered_argv = repr(tuple(argv)) if isinstance(argv, list) else "<unknown>"
        detail = f" argv={rendered_argv} cwd={cwd!r} timeout={timeout!r}s"
    elif request.identity.tool_name == "web_search":
        query = arguments.get("query", "<unknown>")
        max_results = arguments.get("max_results", "<unknown>")
        backend = request.preview.backend if request.preview is not None else "<unknown>"
        detail = f" query={query!r} max_results={max_results!r} backend={backend!r}"
    elif request.identity.tool_name in {"move_file", "copy_file", "move_directory"}:
        source = arguments.get("source", "<unknown>")
        destination = arguments.get("destination", "<unknown>")
        detail = f" source={source!r} destination={destination!r}"
    elif request.identity.tool_name == "web_fetch":
        url = arguments.get("url", "<unknown>")
        output_format = arguments.get("format", "<unknown>")
        detail = f" url={url!r} format={output_format!r}"
    elif request.identity.tool_name == "download_file":
        url = arguments.get("url", "<unknown>")
        path = arguments.get("path", "<unknown>")
        detail = f" url={url!r} path={path!r}"
    elif request.identity.tool_name.startswith("mcp_"):
        detail = " arguments=<redacted>"
    else:
        path = arguments.get("path", "<unknown>")
        content = arguments.get("content")
        byte_count = len(content.encode("utf-8")) if isinstance(content, str) else None
        detail = f" path={path!r}"
        if byte_count is not None:
            detail += f" bytes={byte_count}"
    return (
        f"Approval required: {request.identity.action.value} {request.identity.tool_name}{detail}"
    )


def _render_preview(preview: ApprovalPreview, *, color: bool) -> str:
    byte_count = preview.byte_count
    if preview.kind == ApprovalPreviewKind.FILE_CHANGE:
        assert preview.body is not None
        lines = [f"Prepared candidate ({byte_count} bytes):\n"]
        lines.append(_render_diff(preview.body, color=color))
        if preview.truncated:
            lines.append(
                _style(
                    "[preview truncated; approval still applies the complete candidate]\n",
                    _YELLOW,
                    color=color,
                )
            )
        return "".join(lines)
    if preview.kind == ApprovalPreviewKind.FILE_COPY:
        return f"Prepared copy: {byte_count} bytes; source remains and destination must remain absent.\n"
    if preview.kind == ApprovalPreviewKind.FILE_MOVE:
        return (
            f"Prepared move: {byte_count} bytes; destination must remain absent and partial "
            "effects cannot be rolled back automatically.\n"
        )
    if preview.kind == ApprovalPreviewKind.FILE_DELETE:
        return _style(
            f"Permanent deletion: {byte_count} bytes; no trash, backup, or undo.\n",
            _YELLOW,
            color=color,
        )
    if preview.kind == ApprovalPreviewKind.DIRECTORY_CREATE:
        return "Prepared directory creation: parent must remain unchanged and target absent.\n"
    if preview.kind == ApprovalPreviewKind.DIRECTORY_DELETE:
        return _style(
            "Permanent empty-directory deletion; no recursive delete, trash, backup, or undo.\n",
            _YELLOW,
            color=color,
        )
    if preview.kind == ApprovalPreviewKind.COMMAND:
        return _style(
            "Direct argv execution without shell parsing or rollback; Linux sandbox keeps the "
            "Host filesystem read-only, the workspace writable, and socket creation denied.\n",
            _YELLOW,
            color=color,
        )
    if preview.kind == ApprovalPreviewKind.WEB_SEARCH:
        quota = (
            "consumes one basic-search API credit"
            if preview.backend == "tavily"
            else "may consume API quota"
        )
        return _style(
            f"The exact query will be sent to {preview.backend.title()} Search over HTTPS and {quota}; "
            "returned snippets are untrusted external data.\n",
            _YELLOW,
            color=color,
        )
    if preview.kind == ApprovalPreviewKind.WEB_FETCH:
        return _style(
            "The exact public URL will be fetched with a bounded GET; redirects are revalidated, "
            "response content is untrusted, and no credentials, cookies, or custom headers are sent.\n",
            _YELLOW,
            color=color,
        )
    if preview.kind == ApprovalPreviewKind.DIRECTORY_MOVE:
        return (
            "Prepared directory-tree move: source and destination state must remain unchanged; "
            "destination replacement and cross-filesystem movement are rejected.\n"
        )
    if preview.kind == ApprovalPreviewKind.FILE_DOWNLOAD:
        return _style(
            "The exact public URL will be downloaded and atomically installed at the prepared "
            "workspace path; existing mode is preserved and external bytes are untrusted.\n",
            _YELLOW,
            color=color,
        )
    if preview.kind == ApprovalPreviewKind.MCP_TOOL:
        if preview.transport == "streamable-http":
            message = (
                "The exact arguments will be sent over HTTPS to the selected configured remote "
                "MCP service. The remote service is outside the local command sandbox, returned "
                "content is untrusted, and external side effects cannot be rolled back."
            )
        else:
            message = (
                "The exact arguments will be sent to the selected configured MCP executable. "
                "It runs with a read-only host and workspace, private temporary paths, and no "
                "sockets; returned content is untrusted and external side effects cannot be "
                "rolled back."
            )
        return _style(
            f"{message}\n",
            _YELLOW,
            color=color,
        )
    raise ValueError("approval preview kind is unsupported")


def _render_diff(body: str, *, color: bool) -> str:
    rendered: list[str] = []
    for raw_line in body.splitlines(keepends=True):
        line = _escape_terminal_text(raw_line)
        if line.startswith(("--- ", "+++ ", "@@ ")):
            rendered.append(_style(line, _CYAN, color=color))
        elif line.startswith("+"):
            rendered.append(_style(line, _GREEN, color=color))
        elif line.startswith("-"):
            rendered.append(_style(line, _RED, color=color))
        elif line.startswith("\\"):
            rendered.append(_style(line, _YELLOW, color=color))
        else:
            rendered.append(line)
    return "".join(rendered)


def _escape_terminal_text(text: str) -> str:
    escaped: list[str] = []
    for character in text:
        if character in {"\n", "\t"}:
            escaped.append(character)
            continue
        codepoint = ord(character)
        if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
            continue
        escaped.append(character)
    return "".join(escaped)


def _style(text: str, code: str, *, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text
