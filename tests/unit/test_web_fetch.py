from dataclasses import dataclass

import pytest

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.tools.web_fetch import WEB_FETCH_TOOL_NAME, WebFetchOutcome, WebFetchTool
from leonervis_code.tools.web_transport import (
    WebHttpResponse,
    WebTransportError,
    canonical_public_web_url,
    resolve_public_addresses,
)


@dataclass
class FakeTransport:
    response: WebHttpResponse | None = None
    error: WebTransportError | None = None

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> WebHttpResponse:
        assert url == "https://example.com/docs"
        assert timeout_seconds == 20
        assert max_response_bytes == 512 * 1024
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def request(format: str = "markdown", url: str = "https://example.com/docs") -> ToolUse:
    return ToolUse(
        "toolu_fetch",
        WEB_FETCH_TOOL_NAME,
        ToolArguments.from_mapping({"format": format, "url": url}),
    )


def test_web_fetch_extracts_bounded_markdown_and_metadata() -> None:
    transport = FakeTransport(
        WebHttpResponse(
            200,
            "text/html; charset=utf-8",
            "",
            b"<html><body><script>ignore()</script><h1>Docs</h1><p>Read <code>x</code>.</p></body></html>",
            "https://example.com/docs",
            0,
        )
    )
    tool = WebFetchTool(transport)

    result = tool.execute_detailed(tool.prepare(request()))

    assert result.outcome is WebFetchOutcome.SUCCEEDED
    assert '"content_type":"text/html"' in result.tool_result.content
    assert "# Docs" in result.tool_result.content
    assert "`x`" in result.tool_result.content
    assert "ignore" not in result.tool_result.content


def test_web_fetch_rejects_mime_and_preserves_transport_uncertainty() -> None:
    unsupported = WebFetchTool(
        FakeTransport(WebHttpResponse(200, "image/png", "", b"png", "https://example.com/docs", 0))
    )
    result = unsupported.execute_detailed(unsupported.prepare(request()))
    assert result.outcome is WebFetchOutcome.FAILED
    assert result.result_code == "web_mime_unsupported"

    failed = WebFetchTool(
        FakeTransport(error=WebTransportError("web_timed_out", "timed out", delivery_unknown=True))
    )
    result = failed.execute_detailed(failed.prepare(request()))
    assert result.outcome is WebFetchOutcome.PARTIAL
    assert result.result_code == "web_timed_out"


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1/",
        "http://[::1]/",
        "https://user:password@example.com/",
        "https://example.com:444/",
        "file:///etc/passwd",
    ),
)
def test_web_url_rejects_private_credentials_ports_and_schemes(url: str) -> None:
    with pytest.raises(WebTransportError):
        canonical_public_web_url(url)


def test_dns_rejects_mixed_public_and_private_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(WebTransportError, match="only to public"):
        resolve_public_addresses("example.com", 443)
