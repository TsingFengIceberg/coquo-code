"""Transport-neutral MCP client routing over configured server identity."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from leonervis_code.mcp.client import (
    McpClientError,
    McpProbeResult,
    McpServerStatus,
    McpStdioClient,
)
from leonervis_code.mcp.config import McpServerEntry, McpTransport
from leonervis_code.mcp.http_client import (
    McpHttpTransport,
    McpStreamableHttpClient,
)
from leonervis_code.tools.command_sandbox import CommandSandbox


class McpClient:
    """Dispatch MCP inspection and connections to the configured closed transport set."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        command_sandbox: CommandSandbox | None = None,
        http_transport: McpHttpTransport | None = None,
        access_token_resolver: Callable[[McpServerEntry], str | None] | None = None,
        reverse_request_handler: Callable[[McpServerEntry, str, dict[str, object]], object]
        | None = None,
    ) -> None:
        if access_token_resolver is None:
            from leonervis_code.mcp.oauth import McpOAuthManager

            access_token_resolver = McpOAuthManager.default(environment).access_token
        if reverse_request_handler is None:
            from leonervis_code.mcp.reverse import McpReverseRequestCoordinator

            reverse_request_handler = McpReverseRequestCoordinator().handle
        self._stdio = McpStdioClient(
            workspace,
            environment,
            command_sandbox=command_sandbox,
            reverse_request_handler=reverse_request_handler,
        )
        self._http = McpStreamableHttpClient(
            workspace,
            environment,
            transport=http_transport,
            access_token_resolver=access_token_resolver,
            reverse_request_handler=reverse_request_handler,
        )

    def inspect_status(self, entry: McpServerEntry) -> McpServerStatus:
        return self._client(entry).inspect_status(entry)

    def connect(self, entry: McpServerEntry):  # noqa: ANN201
        return self._client(entry).connect(entry)

    def probe(self, entry: McpServerEntry) -> McpProbeResult:
        return self._client(entry).probe(entry)

    def _client(self, entry: McpServerEntry):  # noqa: ANN202
        transport = entry.configuration.transport
        if transport is McpTransport.STDIO:
            return self._stdio
        if transport is McpTransport.STREAMABLE_HTTP:
            return self._http
        raise McpClientError("mcp_transport_unsupported", "MCP transport is unsupported")
