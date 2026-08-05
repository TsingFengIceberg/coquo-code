from __future__ import annotations

import json

import pytest

from leonervis_code.mcp.client import McpClientError
from leonervis_code.mcp.config import (
    McpServerConfiguration,
    McpServerEntry,
    McpTransport,
    McpTrustMode,
)
from leonervis_code.mcp.http_client import McpStreamableHttpClient
from leonervis_code.tools.web_transport import WebHttpResponse


class FakeHttpTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers, body, timeout_seconds, max_response_bytes):
        self.requests.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def response(value=None, *, status=200, content_type="application/json", headers=()):
    body = b"" if value is None else json.dumps(value).encode()
    return WebHttpResponse(
        status, content_type, "", body, "https://mcp.example.test/mcp", 0, headers
    )


def entry(**overrides):
    values = {
        "name": "remote",
        "endpoint": "https://mcp.example.test/mcp",
        "enabled": True,
        "transport": McpTransport.STREAMABLE_HTTP,
        "trust": McpTrustMode.REMOTE_HTTPS,
    }
    values.update(overrides)
    return McpServerEntry("project", McpServerConfiguration(**values))


def initialize_result(capabilities=None):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": capabilities or {"tools": {}},
            "serverInfo": {"name": "remote-test", "version": "1"},
        },
    }


def test_streamable_http_initializes_calls_with_sse_and_closes():
    sse = (
        b"event: message\n"
        b'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"x","progress":1}}\n\n'
        b'event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"ok"}]}}\n\n'
    )
    transport = FakeHttpTransport(
        [
            response(initialize_result(), headers=(("mcp-session-id", "session-1"),)),
            response(status=202),
            response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
            WebHttpResponse(200, "text/event-stream", "", sse, "https://mcp.example.test/mcp", 0),
            response(status=204),
        ]
    )
    client = McpStreamableHttpClient("/tmp", {}, transport=transport)
    session = client.connect(entry())

    result = session.call_tool("echo", {}, process_generation=1, process_reused=False)

    assert result.result["content"][0]["text"] == "ok"
    assert result.notifications.progress_count == 1
    assert session.close() is True
    assert [request[0] for request in transport.requests] == [
        "POST",
        "POST",
        "POST",
        "POST",
        "DELETE",
    ]
    assert transport.requests[2][2]["MCP-Session-Id"] == "session-1"
    assert transport.requests[2][2]["MCP-Protocol-Version"] == "2025-06-18"


def test_streamable_http_uses_only_environment_named_bearer_token():
    transport = FakeHttpTransport(
        [
            response(initialize_result(), headers=(("mcp-session-id", "s"),)),
            response(status=202),
            response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
            response(status=204),
        ]
    )
    client = McpStreamableHttpClient(
        "/tmp",
        {"REMOTE_TOKEN": "secret-value"},
        transport=transport,
    )
    session = client.connect(entry(bearer_token_env="REMOTE_TOKEN"))
    assert transport.requests[0][2]["Authorization"] == "Bearer secret-value"
    assert session.close() is True


def test_streamable_http_rejects_redirect_without_following():
    transport = FakeHttpTransport([response(status=307)])
    client = McpStreamableHttpClient("/tmp", {}, transport=transport)
    with pytest.raises(McpClientError, match="redirects are not followed"):
        client.connect(entry())
    assert len(transport.requests) == 1


def test_streamable_http_rejects_changed_session_id():
    transport = FakeHttpTransport(
        [
            response(initialize_result(), headers=(("mcp-session-id", "one"),)),
            response(status=202, headers=(("mcp-session-id", "two"),)),
            response(status=204),
        ]
    )
    client = McpStreamableHttpClient("/tmp", {}, transport=transport)
    with pytest.raises(McpClientError, match="session ID changed"):
        client.connect(entry())
