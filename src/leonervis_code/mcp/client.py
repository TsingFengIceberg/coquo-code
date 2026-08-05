"""Confined, bounded MCP stdio initialization and tool-list inspection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

from leonervis_code.mcp.config import McpServerEntry
from leonervis_code.tools.command_sandbox import (
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
MCP_SANDBOX_ACTIVATION_TIMEOUT_SECONDS = 1.0
MCP_PROCESS_EXIT_GRACE_SECONDS = 0.5
MCP_PROCESS_TERMINATE_GRACE_SECONDS = 1.0
MCP_PROCESS_KILL_GRACE_SECONDS = 1.0
MCP_PIPE_DRAIN_GRACE_SECONDS = 1.0
MAX_MCP_MESSAGE_BYTES = 1024 * 1024
MAX_MCP_OUTBOUND_MESSAGE_BYTES = 256 * 1024
MAX_MCP_MESSAGES_PER_PROBE = 1024
MAX_MCP_NOTIFICATIONS_PER_REQUEST = 256
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


class McpClientError(RuntimeError):
    """One sanitized MCP transport, protocol, timeout, or cleanup failure."""

    def __init__(self, code: str, message: str, *, cleanup_complete: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.cleanup_complete = cleanup_complete


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


class _JsonRpcConnection:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdin is None or process.stdout is None:
            raise McpClientError("mcp_spawn_failed", "MCP stdio pipes are unavailable")
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._buffer = bytearray()
        self._messages = 0
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
    ) -> object:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout_seconds
        notifications = 0
        while True:
            message = self._read_message(deadline)
            if "method" in message:
                if "id" in message:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "Server-to-client requests are not supported",
                            },
                        }
                    )
                    raise McpClientError(
                        "mcp_server_request_unsupported",
                        "MCP server sent an unsupported server-to-client request",
                    )
                notifications += 1
                if notifications > MAX_MCP_NOTIFICATIONS_PER_REQUEST:
                    raise McpClientError(
                        "mcp_notification_limit",
                        "MCP server exceeded the notification limit",
                    )
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
                )
            return message["result"]

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

    def _read_message(self, deadline: float) -> dict[str, object]:
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
            events = self._selector.select(remaining)
            if not events:
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


class McpStdioClient:
    """Start one temporary confined stdio process and inspect its tool capability."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        command_sandbox: CommandSandbox | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ValueError("MCP workspace must be an existing directory")
        self._environment = dict(os.environ if environment is None else environment)
        self._command_sandbox = command_sandbox or LinuxBubblewrapCommandSandbox(
            workspace_writable=False
        )
        self._popen_factory = popen_factory

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

    def probe(self, entry: McpServerEntry) -> McpProbeResult:
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
        result: McpProbeResult | None = None
        caught: BaseException | None = None
        try:
            owner.activate()
            connection = _JsonRpcConnection(process)
            initialize = connection.request(
                1,
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "leonervis-code",
                        "version": _client_version(),
                    },
                },
                timeout_seconds=MCP_INITIALIZE_TIMEOUT_SECONDS,
            )
            protocol, server_name, server_version, capabilities = _parse_initialize(initialize)
            connection.notify("notifications/initialized", {})
            tools, pages = _list_tools(connection, capabilities)
            result = McpProbeResult(
                configured_name=configuration.name,
                protocol_version=protocol,
                server_name=server_name,
                server_version=server_version,
                capability_names=tuple(sorted(capabilities)),
                tools=tools,
                pages=pages,
                duration_ms=_elapsed_ms(started),
                stderr_bytes=0,
                stderr_truncated=False,
                cleanup_complete=False,
            )
        except BaseException as error:
            caught = error
        finally:
            if connection is not None:
                connection.close()
            cleanup_complete = owner.close()

        if caught is not None:
            if isinstance(caught, KeyboardInterrupt):
                raise McpClientError(
                    "mcp_cancelled",
                    "MCP probe was cancelled",
                    cleanup_complete=cleanup_complete,
                ) from caught
            if isinstance(caught, McpClientError):
                if not cleanup_complete:
                    raise McpClientError(
                        "mcp_cleanup_incomplete",
                        "MCP probe failed and process cleanup is incomplete",
                        cleanup_complete=False,
                    ) from caught
                raise caught
            raise caught
        if not cleanup_complete:
            raise McpClientError(
                "mcp_cleanup_incomplete",
                "MCP probe completed but process cleanup is incomplete",
                cleanup_complete=False,
            )
        assert result is not None
        return McpProbeResult(
            **{
                **result.__dict__,
                "duration_ms": _elapsed_ms(started),
                "stderr_bytes": owner.stderr.total,
                "stderr_truncated": owner.stderr.total > owner.stderr.limit,
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
        result = connection.request(
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
        return importlib.metadata.version("leonervis-code")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
