from __future__ import annotations

import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from coquo.mcp.config import (
    McpServerConfiguration,
    McpServerEntry,
    McpTransport,
    McpTrustMode,
)
from coquo.mcp.oauth import McpOAuthError, McpOAuthManager
from coquo.tools.web_transport import WebHttpResponse


class OAuthTransport:
    def __init__(self):
        self.token_responses = [
            {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 120,
                "token_type": "Bearer",
            },
            {"access_token": "access-2", "expires_in": 120, "token_type": "Bearer"},
        ]
        self.requests = []

    def request(self, method, url, *, headers, body, timeout_seconds, max_response_bytes):
        self.requests.append((method, url, dict(headers), body))
        if "oauth-protected-resource" in url:
            value = {"authorization_servers": ["https://auth.example.test/tenant"]}
        elif "oauth-authorization-server" in url:
            value = {
                "issuer": "https://auth.example.test/tenant",
                "authorization_endpoint": "https://auth.example.test/authorize",
                "token_endpoint": "https://auth.example.test/token",
                "code_challenge_methods_supported": ["S256"],
            }
        else:
            value = self.token_responses.pop(0)
        return WebHttpResponse(200, "application/json", "", json.dumps(value).encode(), url, 0)


def entry():
    return McpServerEntry(
        "user",
        McpServerConfiguration(
            name="remote",
            endpoint="https://mcp.example.test/mcp",
            oauth_client_id="client-id",
            oauth_scopes=("resources.read",),
            enabled=True,
            transport=McpTransport.STREAMABLE_HTTP,
            trust=McpTrustMode.REMOTE_HTTPS,
        ),
    )


def test_oauth_pkce_exchange_and_redacted_status(tmp_path):
    transport = OAuthTransport()
    manager = McpOAuthManager(tmp_path / "oauth.json", {}, transport=transport)

    url = manager.begin(entry(), "http://127.0.0.1:8765/callback")
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query

    token = manager.complete(entry(), code="returned-code", state=query["state"][0])
    assert token.revision == 1
    assert manager.access_token(entry()) == "access-1"
    assert manager.status(entry()).authorized is True
    assert os.stat(tmp_path / "oauth.json").st_mode & 0o777 == 0o600


def test_oauth_rejects_wrong_state_without_token_request(tmp_path):
    transport = OAuthTransport()
    manager = McpOAuthManager(tmp_path / "oauth.json", {}, transport=transport)
    manager.begin(entry(), "http://localhost:8765/callback")
    with pytest.raises(McpOAuthError, match="state is stale or invalid"):
        manager.complete(entry(), code="code", state="wrong")
    assert not any(request[1].endswith("/token") for request in transport.requests)


def test_oauth_refreshes_expired_token(tmp_path):
    now = int(time.time())
    transport = OAuthTransport()
    manager = McpOAuthManager(tmp_path / "oauth.json", {}, transport=transport, now=lambda: now)
    url = manager.begin(entry(), "http://127.0.0.1:8765/callback")
    state = parse_qs(urlsplit(url).query)["state"][0]
    manager.complete(entry(), code="code", state=state)

    data = json.loads((tmp_path / "oauth.json").read_text())
    data["tokens"]["user:remote"]["expires_at"] = now
    (tmp_path / "oauth.json").write_text(json.dumps(data))

    assert manager.access_token(entry()) == "access-2"
    assert b"refresh_token=refresh-1" in transport.requests[-1][3]
