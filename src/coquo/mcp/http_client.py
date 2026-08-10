"""Bounded MCP Streamable HTTP transport with pinned public-network requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
import re
import time
from typing import Protocol
from pathlib import Path

from coquo.core.cancellation import TurnCancellation
from coquo.mcp.client import (
    MAX_MCP_MESSAGE_BYTES,
    MAX_MCP_SERVER_REQUESTS_PER_REQUEST,
    MCP_CALL_TOOL_TIMEOUT_SECONDS,
    MCP_INITIALIZE_TIMEOUT_SECONDS,
    MCP_PROTOCOL_VERSION,
    SUPPORTED_MCP_PROTOCOL_VERSIONS,
    McpClientError,
    McpListedTool,
    McpNotificationKind,
    McpNotificationSummary,
    McpProbeResult,
    McpServerStatus,
    McpToolCallResult,
    _NotificationCollector,
    _canonical_json,
    _closed_object,
    _elapsed_ms,
    _list_tools,
    _parse_initialize,
    _reject_json_constant,
    _validate_json_bounds,
    _validate_rpc_error,
    _server_request_handler,
    _restore_resource_subscriptions,
)
from coquo.mcp.config import McpServerEntry, McpTransport
from coquo.tools.web_transport import (
    PinnedWebGetTransport,
    WebHttpResponse,
    WebTransportError,
)


MAX_MCP_HTTP_RESPONSE_BYTES = 1024 * 1024
MAX_MCP_HTTP_SESSION_ID_CHARACTERS = 512
MAX_MCP_HTTP_SSE_EVENTS = 1024
MAX_MCP_HTTP_SSE_LINE_BYTES = MAX_MCP_MESSAGE_BYTES + len(b"data: ")
MAX_MCP_HTTP_PARAMETER_HEADERS = 32
MAX_MCP_HTTP_PARAMETER_VALUE_CHARACTERS = 1024
_MCP_PARAMETER_HEADER_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class McpHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> WebHttpResponse: ...


@dataclass(frozen=True)
class McpRemoteSessionStatus:
    configured_name: str
    scope: str
    configuration_revision: int
    protocol_version: str
    process_generation: int
    calls_completed: int
    alive: bool
    stderr_bytes: int = 0
    stderr_truncated: bool = False


class _HttpJsonRpcConnection:
    def __init__(
        self,
        entry: McpServerEntry,
        transport: McpHttpTransport,
        environment: Mapping[str, str],
        *,
        access_token_resolver: Callable[[McpServerEntry], str | None] | None = None,
        server_request_handler: Callable[[str, dict[str, object]], object] | None = None,
    ) -> None:
        configuration = entry.configuration
        if configuration.endpoint is None:
            raise McpClientError("mcp_endpoint_invalid", "remote MCP endpoint is unavailable")
        self.entry = entry
        self.endpoint = configuration.endpoint
        self._transport = transport
        self._environment = environment
        self._access_token_resolver = access_token_resolver
        self._server_request_handler = server_request_handler
        self.session_id: str | None = None
        self.protocol_version = MCP_PROTOCOL_VERSION
        self.closed = False

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
        parameter_headers: Mapping[str, str] | None = None,
    ) -> tuple[object, McpNotificationSummary]:
        if self.closed:
            raise McpClientError("mcp_transport_closed", "MCP HTTP session is closed")
        if cancellation is not None and cancellation.requested:
            raise McpClientError(
                "mcp_cancelled",
                "MCP request was cancelled before dispatch",
                outcome_uncertain=False,
            )
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        response = self._send(
            "POST",
            _canonical_json(message).encode("utf-8"),
            timeout_seconds=max(1, int(timeout_seconds)),
            outcome_uncertain=outcome_uncertain,
            parameter_headers=parameter_headers,
        )
        if cancellation is not None and cancellation.requested:
            raise McpClientError(
                "mcp_cancelled",
                "MCP request was cancelled after dispatch",
                outcome_uncertain=outcome_uncertain,
            )
        collector = _NotificationCollector(notification_sink)
        server_requests = 0
        try:
            for item in _response_messages(response):
                if "method" in item:
                    if "id" in item:
                        server_requests += 1
                        if server_requests > MAX_MCP_SERVER_REQUESTS_PER_REQUEST:
                            raise McpClientError(
                                "mcp_server_request_limit",
                                "MCP server-to-client request limit was exceeded",
                                outcome_uncertain=outcome_uncertain,
                            )
                        params_value = item.get("params", {})
                        if (
                            self._server_request_handler is None
                            or not isinstance(params_value, dict)
                            or type(item.get("id")) not in {int, str}
                        ):
                            raise McpClientError(
                                "mcp_server_request_unsupported",
                                "MCP server sent an unsupported server-to-client request",
                                outcome_uncertain=outcome_uncertain,
                            )
                        try:
                            server_result = self._server_request_handler(
                                item["method"], params_value
                            )
                            outbound = {
                                "jsonrpc": "2.0",
                                "id": item["id"],
                                "result": server_result,
                            }
                        except McpClientError as handler_error:
                            outbound = {
                                "jsonrpc": "2.0",
                                "id": item["id"],
                                "error": {"code": -32000, "message": str(handler_error)},
                            }
                        self._send(
                            "POST",
                            _canonical_json(outbound).encode("utf-8"),
                            timeout_seconds=max(1, int(timeout_seconds)),
                            outcome_uncertain=False,
                        )
                        continue
                    collector.observe(item)
                    continue
                if type(item.get("id")) is not int or item["id"] != request_id:
                    raise McpClientError(
                        "mcp_response_id_mismatch",
                        "MCP response ID does not match the active request",
                        outcome_uncertain=outcome_uncertain,
                    )
                has_result = "result" in item
                has_error = "error" in item
                if has_result == has_error:
                    raise McpClientError(
                        "mcp_response_invalid",
                        "MCP response must contain exactly one result or error",
                        outcome_uncertain=outcome_uncertain,
                    )
                if has_error:
                    _validate_rpc_error(item["error"])
                    raise McpClientError(
                        "mcp_server_error",
                        "MCP server returned a JSON-RPC error",
                        outcome_uncertain=False,
                        notifications=collector.summary,
                    )
                return item["result"], collector.summary
        except McpClientError as error:
            raise McpClientError(
                error.code,
                str(error),
                cleanup_complete=error.cleanup_complete,
                outcome_uncertain=error.outcome_uncertain,
                notifications=collector.summary,
            ) from error
        raise McpClientError(
            "mcp_response_incomplete",
            "MCP HTTP response did not contain the active request result",
            outcome_uncertain=outcome_uncertain,
            notifications=collector.summary,
        )

    def notify(self, method: str, params: dict[str, object]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        response = self._send(
            "POST",
            _canonical_json(message).encode("utf-8"),
            timeout_seconds=int(MCP_INITIALIZE_TIMEOUT_SECONDS),
            outcome_uncertain=False,
        )
        if response.status_code == 202:
            return
        if response.body:
            tuple(_response_messages(response))

    def close(self) -> bool:
        if self.closed:
            return True
        self.closed = True
        if self.session_id is None:
            return True
        try:
            response = self._send(
                "DELETE",
                None,
                timeout_seconds=int(MCP_INITIALIZE_TIMEOUT_SECONDS),
                outcome_uncertain=False,
                permit_closed=True,
            )
        except McpClientError:
            return False
        return response.status_code in {200, 202, 204, 404, 405}

    def _send(
        self,
        method: str,
        body: bytes | None,
        *,
        timeout_seconds: int,
        outcome_uncertain: bool,
        permit_closed: bool = False,
        parameter_headers: Mapping[str, str] | None = None,
    ) -> WebHttpResponse:
        if self.closed and not permit_closed:
            raise McpClientError("mcp_transport_closed", "MCP HTTP session is closed")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.session_id is not None:
            headers["MCP-Session-Id"] = self.session_id
        token = self._resolve_access_token()
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if parameter_headers is not None:
            if len(parameter_headers) > MAX_MCP_HTTP_PARAMETER_HEADERS:
                raise McpClientError(
                    "mcp_parameter_header_limit",
                    "MCP parameter header count exceeds the limit",
                )
            for key, value in parameter_headers.items():
                suffix = key.removeprefix("MCP-Param-")
                if (
                    suffix == key
                    or _MCP_PARAMETER_HEADER_NAME.fullmatch(suffix) is None
                    or not isinstance(value, str)
                    or not value
                    or len(value) > MAX_MCP_HTTP_PARAMETER_VALUE_CHARACTERS
                    or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
                ):
                    raise McpClientError(
                        "mcp_parameter_header_invalid",
                        "MCP parameter header is invalid",
                    )
                headers[key] = value
        try:
            response = self._transport.request(
                method,
                self.endpoint,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
                max_response_bytes=MAX_MCP_HTTP_RESPONSE_BYTES,
            )
        except WebTransportError as error:
            raise McpClientError(
                "mcp_http_transport_failed",
                "MCP HTTP transport failed",
                outcome_uncertain=outcome_uncertain and error.delivery_unknown,
            ) from None
        if response.status_code in {301, 302, 303, 307, 308}:
            raise McpClientError("mcp_http_redirect", "MCP HTTP redirects are not followed")
        if response.status_code == 401:
            raise McpClientError("mcp_auth_required", "MCP remote server requires authorization")
        if not 200 <= response.status_code < 300:
            raise McpClientError(
                "mcp_http_status",
                f"MCP HTTP server returned status {response.status_code}",
                outcome_uncertain=outcome_uncertain,
            )
        session_values = [value for key, value in response.headers if key == "mcp-session-id"]
        if len(session_values) > 1:
            raise McpClientError("mcp_session_invalid", "MCP HTTP session ID is ambiguous")
        if session_values:
            candidate = session_values[0]
            if (
                not candidate
                or len(candidate) > MAX_MCP_HTTP_SESSION_ID_CHARACTERS
                or any(ord(character) < 0x21 or ord(character) > 0x7E for character in candidate)
            ):
                raise McpClientError("mcp_session_invalid", "MCP HTTP session ID is invalid")
            if self.session_id is not None and candidate != self.session_id:
                raise McpClientError("mcp_session_changed", "MCP HTTP session ID changed")
            self.session_id = candidate
        return response

    def _resolve_access_token(self) -> str | None:
        configuration = self.entry.configuration
        value: str | None = None
        if configuration.bearer_token_env is not None:
            value = self._environment.get(configuration.bearer_token_env)
        elif self._access_token_resolver is not None:
            value = self._access_token_resolver(self.entry)
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 16 * 1024
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
        ):
            raise McpClientError("mcp_credential_invalid", "MCP credential is invalid")
        return value


class McpHttpSession:
    """One reusable MCP Streamable HTTP session."""

    def __init__(
        self,
        *,
        entry: McpServerEntry,
        connection: _HttpJsonRpcConnection,
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
        self._connection = connection
        self._started = started
        self._next_request_id = 2 + pages
        self._calls_completed = 0
        self._closed = False

    @property
    def alive(self) -> bool:
        return not self._closed and not self._connection.closed

    @property
    def calls_completed(self) -> int:
        return self._calls_completed

    @property
    def stderr_bytes(self) -> int:
        return 0

    @property
    def stderr_truncated(self) -> bool:
        return False

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout_seconds: float = MCP_CALL_TOOL_TIMEOUT_SECONDS,
        cancellation: TurnCancellation | None = None,
        outcome_uncertain: bool = False,
        notification_sink: Callable[[McpNotificationKind], None] | None = None,
        parameter_headers: Mapping[str, str] | None = None,
    ) -> tuple[object, McpNotificationSummary]:
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
            parameter_headers=parameter_headers,
        )

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
        started = time.monotonic()
        parameter_headers = _tool_parameter_headers(self.tools, remote_name, arguments)
        try:
            result, notifications = self.request(
                "tools/call",
                {"arguments": arguments, "name": remote_name},
                cancellation=cancellation,
                outcome_uncertain=True,
                notification_sink=notification_sink,
                parameter_headers=parameter_headers,
            )
        except McpClientError as error:
            if error.code == "mcp_server_error":
                self._calls_completed += 1
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
            stderr_bytes=0,
            stderr_truncated=False,
            notifications=notifications,
        )

    def close(self) -> bool:
        if self._closed:
            return True
        self._closed = True
        return self._connection.close()


def _tool_parameter_headers(
    tools: tuple[McpListedTool, ...],
    remote_name: str,
    arguments: Mapping[str, object],
) -> dict[str, str]:
    tool = next((item for item in tools if item.name == remote_name), None)
    if tool is None:
        raise McpClientError("mcp_live_tool_missing", "MCP live tool is unavailable")
    try:
        schema = json.loads(
            tool.input_schema_json,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise McpClientError("mcp_schema_invalid", "MCP live tool schema is invalid") from None
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        raise McpClientError("mcp_schema_invalid", "MCP live tool schema is invalid")
    headers: dict[str, str] = {}
    for argument_name, property_schema in properties.items():
        if not isinstance(property_schema, dict):
            continue
        hint = property_schema.get("x-mcp-header")
        if hint is None or argument_name not in arguments:
            continue
        if (
            not isinstance(hint, str)
            or _MCP_PARAMETER_HEADER_NAME.fullmatch(hint) is None
            or property_schema.get("type") != "string"
        ):
            raise McpClientError(
                "mcp_parameter_header_invalid",
                "MCP parameter header mapping is invalid",
            )
        value = arguments[argument_name]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_MCP_HTTP_PARAMETER_VALUE_CHARACTERS
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
        ):
            raise McpClientError(
                "mcp_parameter_header_invalid",
                "MCP parameter header value is invalid",
            )
        header_name = f"MCP-Param-{hint}"
        if header_name in headers:
            raise McpClientError(
                "mcp_parameter_header_invalid",
                "MCP parameter header mapping is duplicated",
            )
        headers[header_name] = value
    return headers


class McpStreamableHttpClient:
    """Initialize and reuse public HTTPS Streamable HTTP MCP sessions."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        transport: McpHttpTransport | None = None,
        access_token_resolver: Callable[[McpServerEntry], str | None] | None = None,
        reverse_request_handler: Callable[[McpServerEntry, str, dict[str, object]], object]
        | None = None,
    ) -> None:
        self._environment = dict(os.environ if environment is None else environment)
        self._workspace = Path(workspace).resolve()
        self._transport = transport or PinnedWebGetTransport()
        self._access_token_resolver = access_token_resolver
        self._reverse_request_handler = reverse_request_handler

    def inspect_status(self, entry: McpServerEntry) -> McpServerStatus:
        configuration = entry.configuration
        missing: tuple[str, ...] = ()
        if (
            configuration.bearer_token_env is not None
            and not self._environment.get(configuration.bearer_token_env, "").strip()
        ):
            missing = (configuration.bearer_token_env,)
        if (
            configuration.oauth_client_secret_env is not None
            and not self._environment.get(configuration.oauth_client_secret_env, "").strip()
        ):
            missing += (configuration.oauth_client_secret_env,)
        return McpServerStatus(entry, configuration.endpoint is not None, missing)

    def connect(self, entry: McpServerEntry) -> McpHttpSession:
        configuration = entry.configuration
        if configuration.transport is not McpTransport.STREAMABLE_HTTP:
            raise McpClientError("mcp_transport_mismatch", "MCP server is not Streamable HTTP")
        status = self.inspect_status(entry)
        if not configuration.enabled:
            raise McpClientError("mcp_server_disabled", "MCP server is disabled")
        if not status.command_available:
            raise McpClientError("mcp_endpoint_invalid", "MCP remote endpoint is unavailable")
        if status.missing_environment:
            raise McpClientError(
                "mcp_environment_missing",
                "MCP server requires unavailable environment names: "
                + ", ".join(status.missing_environment),
            )
        started = time.monotonic()
        connection = _HttpJsonRpcConnection(
            entry,
            self._transport,
            self._environment,
            access_token_resolver=self._access_token_resolver,
            server_request_handler=_server_request_handler(
                self._workspace,
                entry,
                self._reverse_request_handler,
            ),
        )
        try:
            initialize, _ = connection.request(
                1,
                "initialize",
                {
                    "capabilities": (
                        {"roots": {"listChanged": False}}
                        if configuration.expose_workspace_root
                        else {}
                    ),
                    "clientInfo": {"name": "coquo", "version": "0.1.0"},
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                },
                timeout_seconds=MCP_INITIALIZE_TIMEOUT_SECONDS,
            )
            protocol, server_name, server_version, capabilities = _parse_initialize(initialize)
            if protocol not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
                raise McpClientError(
                    "mcp_protocol_unsupported", "MCP protocol version is unsupported"
                )
            connection.protocol_version = protocol
            connection.notify("notifications/initialized", {})
            tools: tuple[McpListedTool, ...] = ()
            pages = 0
            if "tools" in capabilities:
                tools, pages = _list_tools(connection, capabilities)
            session = McpHttpSession(
                entry=entry,
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
            cleanup = connection.close()
            if isinstance(error, McpClientError):
                if cleanup:
                    raise
                raise McpClientError(
                    "mcp_cleanup_incomplete",
                    "MCP HTTP connection failed and cleanup is incomplete",
                    cleanup_complete=False,
                    outcome_uncertain=error.outcome_uncertain,
                ) from error
            raise

    def probe(self, entry: McpServerEntry) -> McpProbeResult:
        started = time.monotonic()
        session = self.connect(entry)
        cleanup = session.close()
        return McpProbeResult(
            configured_name=entry.configuration.name,
            protocol_version=session.protocol_version,
            server_name=session.server_name,
            server_version=session.server_version,
            capability_names=session.capability_names,
            tools=session.tools,
            pages=session.pages,
            duration_ms=_elapsed_ms(started),
            stderr_bytes=0,
            stderr_truncated=False,
            cleanup_complete=cleanup,
        )


def _response_messages(response: WebHttpResponse) -> tuple[dict[str, object], ...]:
    content_type = response.content_type.partition(";")[0].strip().lower()
    if content_type == "application/json":
        return (_decode_message(response.body),)
    if content_type != "text/event-stream":
        raise McpClientError(
            "mcp_http_content_type",
            "MCP HTTP response content type is unsupported",
        )
    messages: list[dict[str, object]] = []
    data_lines: list[bytes] = []
    for line in response.body.splitlines():
        if len(line) > MAX_MCP_HTTP_SSE_LINE_BYTES:
            raise McpClientError("mcp_sse_line_limit", "MCP SSE line exceeds the limit")
        if not line:
            if data_lines:
                messages.append(_decode_message(b"\n".join(data_lines)))
                data_lines = []
                if len(messages) > MAX_MCP_HTTP_SSE_EVENTS:
                    raise McpClientError("mcp_sse_event_limit", "MCP SSE event limit exceeded")
            continue
        if line.startswith(b"data:"):
            data_lines.append(line[5:].lstrip(b" "))
    if data_lines:
        messages.append(_decode_message(b"\n".join(data_lines)))
    if not messages:
        raise McpClientError("mcp_sse_empty", "MCP SSE response contained no JSON-RPC event")
    return tuple(messages)


def _decode_message(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > MAX_MCP_MESSAGE_BYTES:
        raise McpClientError("mcp_message_limit", "MCP message is empty or too large")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise McpClientError("mcp_message_invalid", "MCP message is not strict JSON") from None
    _validate_json_bounds(value)
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise McpClientError("mcp_message_invalid", "MCP message is not JSON-RPC 2.0")
    return value
