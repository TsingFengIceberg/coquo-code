"""Confined, bounded MCP stdio initialization and tool-list inspection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import importlib.metadata
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
from threading import Thread
import time
from typing import BinaryIO

from coquo.core.cancellation import TurnCancellation
from coquo.mcp.config import McpServerEntry
from coquo.tools.command_sandbox import (
    CommandSandbox,
    CommandSandboxLaunch,
    CommandSandboxUnavailable,
    LinuxBubblewrapCommandSandbox,
    SANDBOX_STATUS_MAX_BYTES,
    sandbox_activation_succeeded,
)


MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_MCP_PROTOCOL_VERSIONS = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
MCP_INITIALIZE_TIMEOUT_SECONDS = 10.0
MCP_LIST_TOOLS_TIMEOUT_SECONDS = 10.0
MCP_CALL_TOOL_TIMEOUT_SECONDS = 30.0
MCP_SANDBOX_ACTIVATION_TIMEOUT_SECONDS = 1.0
MCP_PROCESS_EXIT_GRACE_SECONDS = 0.5
MCP_PROCESS_TERMINATE_GRACE_SECONDS = 1.0
MCP_PROCESS_KILL_GRACE_SECONDS = 1.0
MCP_PIPE_DRAIN_GRACE_SECONDS = 1.0
MAX_MCP_MESSAGE_BYTES = 1024 * 1024
MAX_MCP_OUTBOUND_MESSAGE_BYTES = 256 * 1024
MAX_MCP_MESSAGES_PER_PROBE = 1024
MAX_MCP_NOTIFICATIONS_PER_REQUEST = 256
MAX_MCP_SERVER_REQUESTS_PER_REQUEST = 8
MAX_MCP_TOOL_PAGES = 16
MAX_MCP_TOOLS = 256
MAX_MCP_TOOL_NAME_CHARACTERS = 256
MAX_MCP_TEXT_CHARACTERS = 8192
MAX_MCP_JSON_DEPTH = 32
MAX_MCP_JSON_NODES = 8192
MAX_MCP_STDERR_BYTES = 32 * 1024
_BASE_ENVIRONMENT_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "PATH",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "UV_CACHE_DIR",
    "VIRTUAL_ENV",
)


class McpNotificationKind(StrEnum):
    """Closed content-free notification classes retained by the Host."""

    PROGRESS = "progress"
    MESSAGE = "message"
    TOOLS_LIST_CHANGED = "tools-list-changed"
    RESOURCES_LIST_CHANGED = "resources-list-changed"
    RESOURCE_UPDATED = "resource-updated"
    PROMPTS_LIST_CHANGED = "prompts-list-changed"


@dataclass(frozen=True)
class McpNotificationSummary:
    """Bounded notification counts without server-provided content."""

    progress_count: int = 0
    message_count: int = 0
    tools_list_changed_count: int = 0
    resources_list_changed_count: int = 0
    resource_updated_count: int = 0
    prompts_list_changed_count: int = 0
    ignored_count: int = 0

    @property
    def total_count(self) -> int:
        return (
            self.progress_count
            + self.message_count
            + self.tools_list_changed_count
            + self.resources_list_changed_count
            + self.resource_updated_count
            + self.prompts_list_changed_count
            + self.ignored_count
        )

    @property
    def catalog_invalidated(self) -> bool:
        return self.tools_list_changed_count > 0


class McpClientError(RuntimeError):
    """One sanitized MCP transport, protocol, timeout, or cleanup failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        cleanup_complete: bool = True,
        outcome_uncertain: bool = False,
        notifications: McpNotificationSummary | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cleanup_complete = cleanup_complete
        self.outcome_uncertain = outcome_uncertain
        self.notifications = notifications or McpNotificationSummary()


@dataclass(frozen=True)
class McpServerStatus:
    """Credential-free readiness facts for one configured server."""

    entry: McpServerEntry
    command_available: bool
    missing_environment: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return (
            self.entry.configuration.enabled
            and self.command_available
            and not self.missing_environment
        )


@dataclass(frozen=True)
class McpListedTool:
    """One bounded server-reported tool descriptor retained without execution."""

    name: str
    title: str | None
    description: str | None
    input_schema_json: str
    output_schema_json: str | None
    annotations_json: str | None


@dataclass(frozen=True)
class McpProbeResult:
    """Successful initialize and tools/list observations for one temporary process."""

    configured_name: str
    protocol_version: str
    server_name: str
    server_version: str | None
    capability_names: tuple[str, ...]
    tools: tuple[McpListedTool, ...]
    pages: int
    duration_ms: int
    stderr_bytes: int
    stderr_truncated: bool
    cleanup_complete: bool


@dataclass(frozen=True)
class McpToolCallResult:
    """One raw bounded tools/call result with content retained only in memory."""

    configured_name: str
    remote_name: str
    protocol_version: str
    result: object
    duration_ms: int
    process_generation: int
    process_reused: bool
    stderr_bytes: int
    stderr_truncated: bool
    notifications: McpNotificationSummary = McpNotificationSummary()


@dataclass(frozen=True)
class McpLiveProcessStatus:
    """Content-free lifecycle facts for one process owned by the current REPL."""

    configured_name: str
    scope: str
    configuration_revision: int
    protocol_version: str
    process_generation: int
    calls_completed: int
    alive: bool
    stderr_bytes: int
    stderr_truncated: bool
    transport: str = "stdio"
    session_bound: bool = False


@dataclass
class _BoundedDrain:
    limit: int
    total: int = 0
    captured: int = 0
    error: bool = False

    def consume(self, chunk: bytes) -> None:
        self.total += len(chunk)
        self.captured += min(len(chunk), max(0, self.limit - self.captured))


class _ProcessOwner:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        launch: CommandSandboxLaunch,
    ) -> None:
        self.process = process
        self.launch = launch
        self.stderr = _BoundedDrain(MAX_MCP_STDERR_BYTES)
        self.stderr_thread: Thread | None = None
        self.activation_pipe: BinaryIO | None = None
        self.activation_thread: Thread | None = None

    def activate(self) -> None:
        self.launch.close_after_spawn()
        assert self.process.stderr is not None
        self.stderr_thread = Thread(
            target=_drain_pipe,
            args=(self.process.stderr, self.stderr),
            daemon=True,
        )
        self.stderr_thread.start()
        if self.launch.activation_read_fd is None:
            return
        capture = bytearray()
        state = {"total": 0, "error": False}
        try:
            pipe = os.fdopen(self.launch.activation_read_fd, "rb", buffering=0)
        except OSError:
            raise McpClientError(
                "mcp_sandbox_unavailable",
                "MCP sandbox activation channel could not be opened",
            ) from None
        reader = Thread(
            target=_drain_activation,
            args=(pipe, capture, state),
            daemon=True,
        )
        self.activation_pipe = pipe
        self.activation_thread = reader
        reader.start()
        reader.join(MCP_SANDBOX_ACTIVATION_TIMEOUT_SECONDS)
        active = (
            not reader.is_alive()
            and state["total"] <= SANDBOX_STATUS_MAX_BYTES
            and sandbox_activation_succeeded(bytes(capture), read_error=state["error"])
            and self.launch.activation_release_fd is not None
        )
        if active:
            try:
                os.write(self.launch.activation_release_fd, b"1")
            except OSError:
                active = False
        if self.launch.activation_release_fd is not None:
            try:
                os.close(self.launch.activation_release_fd)
            except OSError:
                pass
        if not reader.is_alive():
            try:
                pipe.close()
            except OSError:
                pass
            self.activation_pipe = None
            self.activation_thread = None
        if not active:
            raise McpClientError(
                "mcp_sandbox_unavailable",
                "MCP sandbox activation could not be verified",
            )

    def close(self) -> bool:
        process = self.process
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        cleanup_complete = _wait_for_exit(process, MCP_PROCESS_EXIT_GRACE_SECONDS)
        if not cleanup_complete:
            cleanup_complete = _terminate_process_group(process)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        if self.stderr_thread is not None:
            self.stderr_thread.join(MCP_PIPE_DRAIN_GRACE_SECONDS)
            cleanup_complete = cleanup_complete and not self.stderr_thread.is_alive()
        if self.activation_pipe is not None:
            try:
                self.activation_pipe.close()
            except OSError:
                pass
        if self.activation_thread is not None:
            self.activation_thread.join(MCP_PIPE_DRAIN_GRACE_SECONDS)
            cleanup_complete = cleanup_complete and not self.activation_thread.is_alive()
        return cleanup_complete and process.poll() is not None


class _NotificationCollector:
    def __init__(
        self,
        sink: Callable[[McpNotificationKind], None] | None,
    ) -> None:
        self._sink = sink
        self._seen: set[McpNotificationKind] = set()
        self._progress = 0
        self._message = 0
        self._tools_list_changed = 0
        self._resources_list_changed = 0
        self._resource_updated = 0
        self._prompts_list_changed = 0
        self._ignored = 0

    @property
    def summary(self) -> McpNotificationSummary:
        return McpNotificationSummary(
            progress_count=self._progress,
            message_count=self._message,
            tools_list_changed_count=self._tools_list_changed,
            resources_list_changed_count=self._resources_list_changed,
            resource_updated_count=self._resource_updated,
            prompts_list_changed_count=self._prompts_list_changed,
            ignored_count=self._ignored,
        )

    def observe(self, message: dict[str, object]) -> None:
        if self.summary.total_count >= MAX_MCP_NOTIFICATIONS_PER_REQUEST:
            raise McpClientError(
                "mcp_notification_limit",
                "MCP server exceeded the notification limit",
            )
        method = message.get("method")
        if not isinstance(method, str):
            raise McpClientError(
                "mcp_notification_invalid",
                "MCP notification method is invalid",
            )
        if method == "notifications/progress":
            _validate_progress_notification(message)
            self._progress += 1
            self._emit_once(McpNotificationKind.PROGRESS)
        elif method == "notifications/message":
            _validate_message_notification(message)
            self._message += 1
            self._emit_once(McpNotificationKind.MESSAGE)
        elif method == "notifications/tools/list_changed":
            _validate_tools_list_changed_notification(message)
            self._tools_list_changed += 1
            self._emit_once(McpNotificationKind.TOOLS_LIST_CHANGED)
        elif method == "notifications/resources/list_changed":
            _validate_tools_list_changed_notification(message)
            self._resources_list_changed += 1
            self._emit_once(McpNotificationKind.RESOURCES_LIST_CHANGED)
        elif method == "notifications/resources/updated":
            params = _notification_params(message)
            if set(params) != {"uri"}:
                raise McpClientError(
                    "mcp_notification_invalid",
                    "MCP resource-updated notification is invalid",
                )
            _bounded_text(params["uri"], "MCP resource URI")
            self._resource_updated += 1
            self._emit_once(McpNotificationKind.RESOURCE_UPDATED)
        elif method == "notifications/prompts/list_changed":
            _validate_tools_list_changed_notification(message)
            self._prompts_list_changed += 1
            self._emit_once(McpNotificationKind.PROMPTS_LIST_CHANGED)
        else:
            self._ignored += 1

    def _emit_once(self, kind: McpNotificationKind) -> None:
        if self._sink is None or kind in self._seen:
            return
        self._seen.add(kind)
        try:
            self._sink(kind)
        except Exception:
            pass


class _JsonRpcConnection:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        server_request_handler: Callable[[str, dict[str, object]], object] | None = None,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise McpClientError("mcp_spawn_failed", "MCP stdio pipes are unavailable")
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._buffer = bytearray()
        self._messages = 0
        self._server_request_handler = server_request_handler
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._stdout, selectors.EVENT_READ)

    def close(self) -> None:
        self._selector.close()

    def notify(self, method: str, params: dict[str, object]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self,
        request_id: int,
        method: str,
        params: dict[str, object],
        *,
        timeout_seconds: float,
        cancellation: TurnCancellation | None = None,
        outcome_uncertain: bool = False,
        notification_sink: Callable[[McpNotificationKind], None] | None = None,
    ) -> tuple[object, McpNotificationSummary]:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout_seconds
        collector = _NotificationCollector(notification_sink)
        server_requests = 0
        try:
            while True:
                if cancellation is not None and cancellation.requested:
                    try:
                        self.notify(
                            "notifications/cancelled",
                            {"requestId": request_id, "reason": "client turn cancelled"},
                        )
                    except McpClientError:
                        pass
                    raise McpClientError(
                        "mcp_cancelled",
                        "MCP request was cancelled",
                        outcome_uncertain=outcome_uncertain,
                    )
                message = self._read_message(deadline, cancellation=cancellation)
                if "method" in message:
                    if "id" in message:
                        server_requests += 1
                        if server_requests > MAX_MCP_SERVER_REQUESTS_PER_REQUEST:
                            raise McpClientError(
                                "mcp_server_request_limit",
                                "MCP server-to-client request limit was exceeded",
                            )
                        params = message.get("params", {})
                        if not isinstance(message.get("id"), (int, str)) or not isinstance(
                            params, dict
                        ):
                            raise McpClientError(
                                "mcp_server_request_invalid",
                                "MCP server-to-client request is invalid",
                            )
                        if self._server_request_handler is None:
                            self._send(
                                {
                                    "jsonrpc": "2.0",
                                    "id": message["id"],
                                    "error": {
                                        "code": -32601,
                                        "message": "Server-to-client request is not enabled",
                                    },
                                }
                            )
                            raise McpClientError(
                                "mcp_server_request_unsupported",
                                "MCP server sent an unsupported server-to-client request",
                            )
                        try:
                            result = self._server_request_handler(message["method"], params)
                        except McpClientError as handler_error:
                            self._send(
                                {
                                    "jsonrpc": "2.0",
                                    "id": message["id"],
                                    "error": {"code": -32000, "message": str(handler_error)},
                                }
                            )
                            continue
                        _validate_json_bounds(result)
                        self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
                        continue
                    collector.observe(message)
                    continue
                if type(message.get("id")) is not int or message["id"] != request_id:
                    raise McpClientError(
                        "mcp_response_id_mismatch",
                        "MCP response ID does not match the active request",
                    )
                has_result = "result" in message
                has_error = "error" in message
                if has_result == has_error:
                    raise McpClientError(
                        "mcp_response_invalid",
                        "MCP response must contain exactly one result or error",
                    )
                if has_error:
                    _validate_rpc_error(message["error"])
                    raise McpClientError(
                        "mcp_server_error",
                        "MCP server returned a JSON-RPC error",
                        outcome_uncertain=False,
                    )
                return message["result"], collector.summary
        except McpClientError as error:
            raise McpClientError(
                error.code,
                str(error),
                cleanup_complete=error.cleanup_complete,
                outcome_uncertain=error.outcome_uncertain,
                notifications=collector.summary,
            ) from error

    def _send(self, message: dict[str, object]) -> None:
        payload = _canonical_json(message).encode("utf-8") + b"\n"
        if len(payload) > MAX_MCP_OUTBOUND_MESSAGE_BYTES:
            raise McpClientError(
                "mcp_outbound_message_limit",
                "MCP outbound message exceeds the supported size",
            )
        try:
            self._stdin.write(payload)
            self._stdin.flush()
        except (BrokenPipeError, OSError):
            raise McpClientError(
                "mcp_transport_closed",
                "MCP stdio transport closed while sending a message",
            ) from None

    def _read_message(
        self,
        deadline: float,
        *,
        cancellation: TurnCancellation | None = None,
    ) -> dict[str, object]:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                return self._decode_message(raw)
            if len(self._buffer) > MAX_MCP_MESSAGE_BYTES:
                raise McpClientError(
                    "mcp_message_limit",
                    "MCP server message exceeds the supported size",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpClientError("mcp_timeout", "MCP server response timed out")
            events = self._selector.select(
                min(remaining, 0.1) if cancellation is not None else remaining
            )
            if not events:
                if cancellation is not None and cancellation.requested:
                    continue
                if time.monotonic() < deadline:
                    continue
                raise McpClientError("mcp_timeout", "MCP server response timed out")
            try:
                chunk = os.read(self._stdout.fileno(), 64 * 1024)
            except OSError:
                raise McpClientError(
                    "mcp_transport_failed",
                    "MCP stdout could not be read",
                ) from None
            if not chunk:
                if self._buffer:
                    raise McpClientError(
                        "mcp_message_incomplete",
                        "MCP stdout ended with an incomplete message",
                    )
                raise McpClientError(
                    "mcp_transport_closed",
                    "MCP server closed stdout before completing the request",
                )
            self._buffer.extend(chunk)

    def _decode_message(self, raw: bytes) -> dict[str, object]:
        if not raw:
            raise McpClientError("mcp_message_invalid", "MCP server sent an empty message")
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise McpClientError(
                "mcp_message_limit",
                "MCP server message exceeds the supported size",
            )
        try:
            value = json.loads(
                raw,
                object_pairs_hook=_closed_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise McpClientError(
                "mcp_message_invalid",
                "MCP server message is not strict JSON",
            ) from None
        _validate_json_bounds(value)
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise McpClientError(
                "mcp_message_invalid",
                "MCP server message is not a JSON-RPC 2.0 object",
            )
        self._messages += 1
        if self._messages > MAX_MCP_MESSAGES_PER_PROBE:
            raise McpClientError(
                "mcp_message_count_limit",
                "MCP server exceeded the message-count limit",
            )
        return value


class McpStdioSession:
    """One initialized confined process that can serve sequential MCP calls."""

    def __init__(
        self,
        *,
        entry: McpServerEntry,
        owner: _ProcessOwner,
        connection: _JsonRpcConnection,
        protocol_version: str,
        server_name: str,
        server_version: str | None,
        capability_names: tuple[str, ...],
        tools: tuple[McpListedTool, ...],
        pages: int,
        started: float,
    ) -> None:
        self.entry = entry
        self.protocol_version = protocol_version
        self.server_name = server_name
        self.server_version = server_version
        self.capability_names = capability_names
        self.tools = tools
        self.pages = pages
        self._owner = owner
        self._connection = connection
        self._started = started
        self._next_request_id = 2 + pages
        self._calls_completed = 0
        self._closed = False

    @property
    def alive(self) -> bool:
        return self._owner.process.poll() is None

    @property
    def calls_completed(self) -> int:
        return self._calls_completed

    @property
    def stderr_bytes(self) -> int:
        return self._owner.stderr.total

    @property
    def stderr_truncated(self) -> bool:
        return self.stderr_bytes > self._owner.stderr.limit

    def call_tool(
        self,
        remote_name: str,
        arguments: dict[str, object],
        *,
        process_generation: int,
        process_reused: bool,
        cancellation: TurnCancellation | None = None,
        notification_sink: Callable[[McpNotificationKind], None] | None = None,
    ) -> McpToolCallResult:
        if self._closed or not self.alive:
            raise McpClientError("mcp_process_unavailable", "MCP process is not available")
        if not isinstance(remote_name, str) or not remote_name:
            raise ValueError("MCP remote tool name is invalid")
        if not isinstance(arguments, dict):
            raise ValueError("MCP tool arguments must be an object")
        _validate_json_bounds(arguments)
        started = time.monotonic()
        try:
            result, notifications = self.request(
                "tools/call",
                {"arguments": arguments, "name": remote_name},
                cancellation=cancellation,
                outcome_uncertain=True,
                notification_sink=notification_sink,
            )
        except McpClientError as error:
            if error.code == "mcp_server_error":
                self._calls_completed += 1
            uncertain_codes = {
                "mcp_cancelled",
                "mcp_message_count_limit",
                "mcp_message_incomplete",
                "mcp_message_invalid",
                "mcp_message_limit",
                "mcp_notification_invalid",
                "mcp_notification_limit",
                "mcp_response_id_mismatch",
                "mcp_response_invalid",
                "mcp_server_request_unsupported",
                "mcp_timeout",
                "mcp_transport_closed",
                "mcp_transport_failed",
            }
            if error.code in uncertain_codes and not error.outcome_uncertain:
                raise McpClientError(
                    error.code,
                    str(error),
                    cleanup_complete=error.cleanup_complete,
                    outcome_uncertain=True,
                    notifications=error.notifications,
                ) from error
            raise
        self._calls_completed += 1
        return McpToolCallResult(
            configured_name=self.entry.configuration.name,
            remote_name=remote_name,
            protocol_version=self.protocol_version,
            result=result,
            duration_ms=_elapsed_ms(started),
            process_generation=process_generation,
            process_reused=process_reused,
            stderr_bytes=self.stderr_bytes,
            stderr_truncated=self.stderr_truncated,
            notifications=notifications,
        )

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout_seconds: float = MCP_CALL_TOOL_TIMEOUT_SECONDS,
        cancellation: TurnCancellation | None = None,
        outcome_uncertain: bool = False,
        notification_sink: Callable[[McpNotificationKind], None] | None = None,
    ) -> tuple[object, McpNotificationSummary]:
        if self._closed or not self.alive:
            raise McpClientError("mcp_process_unavailable", "MCP process is not available")
        request_id = self._next_request_id
        self._next_request_id += 1
        return self._connection.request(
            request_id,
            method,
            params,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            outcome_uncertain=outcome_uncertain,
            notification_sink=notification_sink,
        )

    def close(self) -> bool:
        if not self._closed:
            self._closed = True
            self._connection.close()
        return self._owner.close()


class McpStdioClient:
    """Start one temporary confined stdio process and inspect its tool capability."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        command_sandbox: CommandSandbox | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        reverse_request_handler: Callable[[McpServerEntry, str, dict[str, object]], object]
        | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ValueError("MCP workspace must be an existing directory")
        self._environment = dict(os.environ if environment is None else environment)
        self._command_sandbox = command_sandbox or LinuxBubblewrapCommandSandbox(
            workspace_writable=False
        )
        self._popen_factory = popen_factory
        self._reverse_request_handler = reverse_request_handler

    def inspect_status(self, entry: McpServerEntry) -> McpServerStatus:
        if not isinstance(entry, McpServerEntry):
            raise ValueError("MCP server entry is invalid")
        configuration = entry.configuration
        command = Path(configuration.command)
        try:
            info = command.stat()
            command_available = stat.S_ISREG(info.st_mode) and os.access(command, os.X_OK)
        except OSError:
            command_available = False
        missing = tuple(
            source
            for _, source in configuration.environment
            if not isinstance(self._environment.get(source), str)
            or not self._environment[source].strip()
        )
        return McpServerStatus(entry, command_available, missing)

    def connect(self, entry: McpServerEntry) -> McpStdioSession:
        """Start, initialize, and enumerate one confined process for later sequential use."""
        status = self.inspect_status(entry)
        configuration = entry.configuration
        if not configuration.enabled:
            raise McpClientError(
                "mcp_server_disabled",
                f"MCP server is disabled: {configuration.name}",
            )
        if not status.command_available:
            raise McpClientError(
                "mcp_command_unavailable",
                f"MCP server command is unavailable: {configuration.name}",
            )
        if status.missing_environment:
            raise McpClientError(
                "mcp_environment_missing",
                "MCP server requires unavailable environment names: "
                + ", ".join(status.missing_environment),
            )

        cwd = _resolve_workspace_cwd(self._workspace, configuration.cwd)
        environment = {
            key: value
            for key in _BASE_ENVIRONMENT_ALLOWLIST
            if isinstance((value := self._environment.get(key)), str)
        }
        for target, source in configuration.environment:
            environment[target] = self._environment[source]
        environment["PWD"] = str(cwd)
        try:
            launch = self._command_sandbox.prepare_launch(
                workspace=self._workspace,
                cwd=cwd,
                argv=(configuration.command, *configuration.args),
                environment=environment,
            )
        except CommandSandboxUnavailable:
            raise McpClientError(
                "mcp_sandbox_unavailable",
                "MCP confined stdio sandbox is unavailable",
            ) from None
        if not isinstance(launch, CommandSandboxLaunch):
            raise McpClientError(
                "mcp_sandbox_unavailable",
                "MCP sandbox returned an invalid launch description",
            )

        started = time.monotonic()
        try:
            process = self._popen_factory(
                launch.argv,
                cwd=launch.cwd,
                env=launch.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                pass_fds=launch.pass_fds,
            )
        except (OSError, ValueError):
            launch.close_without_spawn()
            raise McpClientError(
                "mcp_spawn_failed",
                f"MCP server process could not be started: {configuration.name}",
            ) from None

        owner = _ProcessOwner(process, launch)
        connection: _JsonRpcConnection | None = None
        caught: BaseException | None = None
        try:
            owner.activate()
            root_handler = _server_request_handler(
                self._workspace,
                entry,
                self._reverse_request_handler,
            )
            connection = _JsonRpcConnection(process, root_handler)
            initialize, _ = connection.request(
                1,
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": (
                        {"roots": {"listChanged": False}}
                        if configuration.expose_workspace_root
                        else {}
                    ),
                    "clientInfo": {
                        "name": "coquo",
                        "version": _client_version(),
                    },
                },
                timeout_seconds=MCP_INITIALIZE_TIMEOUT_SECONDS,
            )
            protocol, server_name, server_version, capabilities = _parse_initialize(initialize)
            connection.notify("notifications/initialized", {})
            tools, pages = _list_tools(connection, capabilities)
            session = McpStdioSession(
                entry=entry,
                owner=owner,
                connection=connection,
                protocol_version=protocol,
                server_name=server_name,
                server_version=server_version,
                capability_names=tuple(sorted(capabilities)),
                tools=tools,
                pages=pages,
                started=started,
            )
            _restore_resource_subscriptions(
                session,
                capabilities,
                configuration.resource_subscriptions,
            )
            return session
        except BaseException as error:
            caught = error
        if connection is not None:
            connection.close()
        cleanup_complete = owner.close()
        assert caught is not None
        if isinstance(caught, KeyboardInterrupt):
            raise McpClientError(
                "mcp_cancelled",
                "MCP connection was cancelled",
                cleanup_complete=cleanup_complete,
            ) from caught
        if isinstance(caught, McpClientError):
            if not cleanup_complete:
                raise McpClientError(
                    "mcp_cleanup_incomplete",
                    "MCP connection failed and process cleanup is incomplete",
                    cleanup_complete=False,
                ) from caught
            raise caught
        raise caught

    def probe(self, entry: McpServerEntry) -> McpProbeResult:
        """Start one temporary process, inspect tools, and require complete cleanup."""
        started = time.monotonic()
        session = self.connect(entry)
        result = McpProbeResult(
            configured_name=entry.configuration.name,
            protocol_version=session.protocol_version,
            server_name=session.server_name,
            server_version=session.server_version,
            capability_names=session.capability_names,
            tools=session.tools,
            pages=session.pages,
            duration_ms=_elapsed_ms(started),
            stderr_bytes=session.stderr_bytes,
            stderr_truncated=session.stderr_truncated,
            cleanup_complete=False,
        )
        cleanup_complete = session.close()
        if not cleanup_complete:
            raise McpClientError(
                "mcp_cleanup_incomplete",
                "MCP probe completed but process cleanup is incomplete",
                cleanup_complete=False,
            )
        return McpProbeResult(
            **{
                **result.__dict__,
                "duration_ms": _elapsed_ms(started),
                "stderr_bytes": session.stderr_bytes,
                "stderr_truncated": session.stderr_truncated,
                "cleanup_complete": True,
            }
        )


def _parse_initialize(
    result: object,
) -> tuple[str, str, str | None, dict[str, object]]:
    if not isinstance(result, dict):
        raise McpClientError("mcp_initialize_invalid", "MCP initialize result is invalid")
    protocol = result.get("protocolVersion")
    if protocol not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
        raise McpClientError(
            "mcp_protocol_unsupported",
            "MCP server selected an unsupported protocol version",
        )
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, dict):
        raise McpClientError(
            "mcp_initialize_invalid",
            "MCP initialize capabilities are invalid",
        )
    server_info = result.get("serverInfo")
    if not isinstance(server_info, dict):
        raise McpClientError("mcp_initialize_invalid", "MCP serverInfo is invalid")
    server_name = _bounded_text(server_info.get("name"), "MCP serverInfo name")
    raw_version = server_info.get("version")
    server_version = (
        None if raw_version is None else _bounded_text(raw_version, "MCP serverInfo version")
    )
    instructions = result.get("instructions")
    if instructions is not None:
        _bounded_text(instructions, "MCP server instructions")
    _validate_json_bounds(capabilities)
    return protocol, server_name, server_version, capabilities


def _list_tools(
    connection: _JsonRpcConnection,
    capabilities: dict[str, object],
) -> tuple[tuple[McpListedTool, ...], int]:
    if "tools" not in capabilities:
        return (), 0
    if not isinstance(capabilities["tools"], dict):
        raise McpClientError(
            "mcp_initialize_invalid",
            "MCP tools capability is invalid",
        )
    tools: list[McpListedTool] = []
    names: set[str] = set()
    cursors: set[str] = set()
    cursor: str | None = None
    for page in range(1, MAX_MCP_TOOL_PAGES + 1):
        params: dict[str, object] = {}
        if cursor is not None:
            params["cursor"] = cursor
        result, _ = connection.request(
            1 + page,
            "tools/list",
            params,
            timeout_seconds=MCP_LIST_TOOLS_TIMEOUT_SECONDS,
        )
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise McpClientError("mcp_tools_invalid", "MCP tools/list result is invalid")
        for raw_tool in result["tools"]:
            tool = _parse_tool(raw_tool)
            if tool.name in names:
                raise McpClientError(
                    "mcp_tool_duplicate",
                    "MCP tools/list returned a duplicate tool name",
                )
            names.add(tool.name)
            tools.append(tool)
            if len(tools) > MAX_MCP_TOOLS:
                raise McpClientError(
                    "mcp_tool_limit",
                    f"MCP server exceeds the {MAX_MCP_TOOLS}-tool inspection limit",
                )
        raw_cursor = result.get("nextCursor")
        if raw_cursor is None:
            return tuple(tools), page
        cursor = _bounded_text(raw_cursor, "MCP tools/list cursor")
        if cursor in cursors:
            raise McpClientError(
                "mcp_cursor_repeated",
                "MCP tools/list repeated a pagination cursor",
            )
        cursors.add(cursor)
    raise McpClientError(
        "mcp_page_limit",
        f"MCP tools/list exceeds the {MAX_MCP_TOOL_PAGES}-page inspection limit",
    )


def _restore_resource_subscriptions(
    session,
    capabilities: dict[str, object],
    subscriptions: tuple[str, ...],
) -> None:  # noqa: ANN001
    if not subscriptions:
        return
    resources = capabilities.get("resources")
    if not isinstance(resources, dict) or resources.get("subscribe") is not True:
        raise McpClientError(
            "mcp_resource_subscribe_unsupported",
            "MCP server does not support configured resource subscriptions",
        )
    for uri in subscriptions:
        session.request("resources/subscribe", {"uri": uri})


def _parse_tool(value: object) -> McpListedTool:
    if not isinstance(value, dict):
        raise McpClientError("mcp_tool_invalid", "MCP tool descriptor is invalid")
    name = _bounded_text(value.get("name"), "MCP tool name", MAX_MCP_TOOL_NAME_CHARACTERS)
    if any(character.isspace() or ord(character) < 33 for character in name):
        raise McpClientError("mcp_tool_invalid", "MCP tool name is invalid")
    title = _optional_bounded_text(value.get("title"), "MCP tool title")
    description = _optional_bounded_text(value.get("description"), "MCP tool description")
    input_schema = value.get("inputSchema")
    if not isinstance(input_schema, dict):
        raise McpClientError("mcp_tool_invalid", "MCP tool inputSchema is invalid")
    _validate_json_bounds(value)
    output_schema = value.get("outputSchema")
    if output_schema is not None and not isinstance(output_schema, dict):
        raise McpClientError("mcp_tool_invalid", "MCP tool outputSchema is invalid")
    annotations = value.get("annotations")
    if annotations is not None and not isinstance(annotations, dict):
        raise McpClientError("mcp_tool_invalid", "MCP tool annotations are invalid")
    return McpListedTool(
        name=name,
        title=title,
        description=description,
        input_schema_json=_canonical_json(input_schema),
        output_schema_json=(None if output_schema is None else _canonical_json(output_schema)),
        annotations_json=(None if annotations is None else _canonical_json(annotations)),
    )


def _bounded_text(value: object, label: str, limit: int = MAX_MCP_TEXT_CHARACTERS) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 and character not in {"\n", "\r", "\t"} for character in value)
        or len(value) > limit
        or len(value.encode("utf-8")) > limit * 4
    ):
        raise McpClientError("mcp_text_invalid", f"{label} is invalid")
    return value


def _optional_bounded_text(value: object, label: str) -> str | None:
    return None if value is None else _bounded_text(value, label)


def _notification_params(
    message: dict[str, object],
    *,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if set(message) - {"jsonrpc", "method", "params"}:
        raise McpClientError(
            "mcp_notification_invalid",
            "MCP notification contains unsupported fields",
        )
    params = message.get("params", {})
    if not isinstance(params, dict) or set(params) - allowed or not required <= set(params):
        raise McpClientError(
            "mcp_notification_invalid",
            "MCP notification parameters are invalid",
        )
    return params


def _validate_progress_notification(message: dict[str, object]) -> None:
    params = _notification_params(
        message,
        allowed=frozenset({"message", "progress", "progressToken", "total"}),
        required=frozenset({"progress", "progressToken"}),
    )
    token = params["progressToken"]
    progress = params["progress"]
    total = params.get("total")
    if (
        (not isinstance(token, (str, int)) or isinstance(token, bool) or token == "")
        or not isinstance(progress, (int, float))
        or isinstance(progress, bool)
        or (total is not None and (not isinstance(total, (int, float)) or isinstance(total, bool)))
    ):
        raise McpClientError(
            "mcp_notification_invalid",
            "MCP progress notification is invalid",
        )
    if isinstance(token, str):
        _bounded_text(token, "MCP progress token")
    if "message" in params:
        _bounded_text(params["message"], "MCP progress message")


def _validate_message_notification(message: dict[str, object]) -> None:
    params = _notification_params(
        message,
        allowed=frozenset({"data", "level", "logger"}),
        required=frozenset({"data", "level"}),
    )
    if params["level"] not in {
        "debug",
        "info",
        "notice",
        "warning",
        "error",
        "critical",
        "alert",
        "emergency",
    }:
        raise McpClientError(
            "mcp_notification_invalid",
            "MCP message notification level is invalid",
        )
    if "logger" in params:
        _bounded_text(params["logger"], "MCP message logger")


def _validate_tools_list_changed_notification(message: dict[str, object]) -> None:
    _notification_params(message, allowed=frozenset())


def _validate_rpc_error(value: object) -> None:
    if (
        not isinstance(value, dict)
        or type(value.get("code")) is not int
        or not isinstance(value.get("message"), str)
    ):
        raise McpClientError("mcp_response_invalid", "MCP JSON-RPC error is invalid")


def _validate_json_bounds(value: object) -> None:
    nodes = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_MCP_JSON_NODES or depth > MAX_MCP_JSON_DEPTH:
            raise McpClientError(
                "mcp_json_limit",
                "MCP JSON exceeds the supported structure bounds",
            )
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            raise McpClientError("mcp_json_invalid", "MCP JSON contains an invalid value")


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError):
        raise McpClientError("mcp_json_invalid", "MCP JSON is not canonicalizable") from None


def _resolve_workspace_cwd(workspace: Path, relative: str) -> Path:
    candidate = workspace if relative == "." else workspace / relative
    current = workspace
    try:
        workspace_info = workspace.lstat()
        if not stat.S_ISDIR(workspace_info.st_mode) or stat.S_ISLNK(workspace_info.st_mode):
            raise OSError
        for component in Path(relative).parts:
            if component == ".":
                continue
            current = current / component
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise OSError
        info = candidate.lstat()
    except OSError:
        raise McpClientError(
            "mcp_cwd_invalid",
            "MCP server cwd is unavailable or crosses a symlink",
        ) from None
    if not stat.S_ISDIR(info.st_mode):
        raise McpClientError("mcp_cwd_invalid", "MCP server cwd is not a directory")
    return candidate


def _workspace_root_handler(
    workspace: Path,
) -> Callable[[str, dict[str, object]], object]:
    root = workspace.resolve()

    def handle(method: str, params: dict[str, object]) -> object:
        if method != "roots/list" or params:
            raise McpClientError(
                "mcp_server_request_unsupported",
                "MCP server-to-client request is not enabled",
            )
        return {"roots": [{"name": root.name or "workspace", "uri": root.as_uri()}]}

    return handle


def _server_request_handler(
    workspace: Path,
    entry: McpServerEntry,
    reverse: Callable[[McpServerEntry, str, dict[str, object]], object] | None,
) -> Callable[[str, dict[str, object]], object] | None:
    roots = (
        _workspace_root_handler(workspace) if entry.configuration.expose_workspace_root else None
    )
    if roots is None and reverse is None:
        return None

    def handle(method: str, params: dict[str, object]) -> object:
        if method == "roots/list" and roots is not None:
            return roots(method, params)
        if reverse is not None:
            return reverse(entry, method, params)
        raise McpClientError(
            "mcp_server_request_unsupported",
            "MCP server-to-client request is not enabled",
        )

    return handle


def _drain_pipe(pipe: BinaryIO, observation: _BoundedDrain) -> None:
    try:
        while chunk := pipe.read(64 * 1024):
            observation.consume(chunk)
    except (OSError, ValueError):
        observation.error = True


def _drain_activation(
    pipe: BinaryIO,
    capture: bytearray,
    state: dict[str, int | bool],
) -> None:
    try:
        while chunk := pipe.read(4096):
            state["total"] = int(state["total"]) + len(chunk)
            remaining = SANDBOX_STATUS_MAX_BYTES - len(capture)
            if remaining > 0:
                capture.extend(chunk[:remaining])
    except (OSError, ValueError):
        state["error"] = True


def _wait_for_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    if process.poll() is not None:
        return True
    try:
        process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return process.poll() is not None


def _terminate_process_group(process: subprocess.Popen[bytes]) -> bool:
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return process.poll() is not None
    except OSError:
        return False
    if _wait_for_exit(process, MCP_PROCESS_TERMINATE_GRACE_SECONDS):
        return True
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return process.poll() is not None
    except OSError:
        return False
    return _wait_for_exit(process, MCP_PROCESS_KILL_GRACE_SECONDS)


def _client_version() -> str:
    try:
        return importlib.metadata.version("coquo")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
