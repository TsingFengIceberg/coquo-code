from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from coquo.tools import web_search as web_search_module
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import PermissionAction
from coquo.tools.web_search import (
    BRAVE_SEARCH_API_KEY_ENV,
    BRAVE_SEARCH_ENDPOINT,
    MAX_WEB_SEARCH_RESPONSE_BYTES,
    TAVILY_SEARCH_API_KEY_ENV,
    TAVILY_SEARCH_ENDPOINT,
    WEB_SEARCH_BACKEND_ENV,
    WEB_SEARCH_TIMEOUT_SECONDS,
    PreparedWebSearch,
    SearchHttpResponse,
    UrllibWebSearchTransport,
    WebSearchBackend,
    WebSearchOutcome,
    WebSearchPreparationError,
    WebSearchSelectionSource,
    WebSearchTool,
    WebSearchTransportError,
)


class FakeTransport:
    def __init__(self, response: SearchHttpResponse | WebSearchTransportError) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def search(self, **arguments) -> SearchHttpResponse:
        self.calls.append(arguments)
        if isinstance(self.response, WebSearchTransportError):
            raise self.response
        return self.response


def search_call(query: str = "Python release notes", max_results: int = 3) -> ToolUse:
    return ToolUse(
        "search-1",
        "web_search",
        ToolArguments.from_mapping({"query": query, "max_results": max_results}),
    )


def response(
    results: list[object], *, content_type: str = "application/json"
) -> SearchHttpResponse:
    return SearchHttpResponse(
        200,
        content_type,
        json.dumps({"results": results}, ensure_ascii=True).encode("utf-8"),
    )


class FakeNetworkResponse:
    def __init__(self, body: bytes = b"{}") -> None:
        self.body = body
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getcode(self) -> int:
        return 200

    def read(self, _limit: int) -> bytes:
        return self.body


class FakeOpener:
    def __init__(self) -> None:
        self.calls = []

    def open(self, request, *, timeout: int):
        self.calls.append((request, timeout))
        return FakeNetworkResponse()


@pytest.mark.parametrize(
    ("backend", "endpoint", "body", "method", "credential_header", "credential_value"),
    [
        (
            WebSearchBackend.BRAVE,
            f"{BRAVE_SEARCH_ENDPOINT}?q=Python",
            None,
            "GET",
            "X-subscription-token",
            "secret",
        ),
        (
            WebSearchBackend.TAVILY,
            TAVILY_SEARCH_ENDPOINT,
            b'{"query":"Python"}',
            "POST",
            "Authorization",
            "Bearer secret",
        ),
    ],
)
def test_urllib_transport_uses_backend_specific_method_body_and_authentication(
    monkeypatch,
    backend: WebSearchBackend,
    endpoint: str,
    body: bytes | None,
    method: str,
    credential_header: str,
    credential_value: str,
) -> None:
    opener = FakeOpener()
    monkeypatch.setattr(web_search_module, "build_opener", lambda _handler: opener)

    result = UrllibWebSearchTransport().search(
        backend=backend,
        endpoint=endpoint,
        body=body,
        api_key="secret",
        timeout_seconds=WEB_SEARCH_TIMEOUT_SECONDS,
        max_response_bytes=MAX_WEB_SEARCH_RESPONSE_BYTES,
    )

    request, timeout = opener.calls[0]
    assert request.get_method() == method
    assert request.full_url == endpoint
    assert request.data == body
    assert request.get_header(credential_header) == credential_value
    assert timeout == WEB_SEARCH_TIMEOUT_SECONDS
    assert result == SearchHttpResponse(200, "application/json", b"{}")


def test_prepare_binds_fixed_tavily_request_and_network_permission() -> None:
    tool = WebSearchTool(
        {TAVILY_SEARCH_API_KEY_ENV: "secret"}, transport=FakeTransport(response([]))
    )

    prepared = tool.prepare(search_call("Python 3.14 docs", 5))

    assert isinstance(prepared, PreparedWebSearch)
    assert prepared.action is PermissionAction.NETWORK_READ
    assert prepared.backend is WebSearchBackend.TAVILY
    assert prepared.endpoint == TAVILY_SEARCH_ENDPOINT
    assert json.loads(prepared.request_body) == {
        "auto_parameters": False,
        "chunks_per_source": 1,
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
        "include_usage": True,
        "max_results": 5,
        "query": "Python 3.14 docs",
        "search_depth": "basic",
        "topic": "general",
    }
    assert b"secret" not in prepared.request_body


def test_prepare_auto_selects_brave_when_it_is_the_only_available_backend() -> None:
    tool = WebSearchTool(
        {BRAVE_SEARCH_API_KEY_ENV: "secret"}, transport=FakeTransport(response([]))
    )

    prepared = tool.prepare(search_call("Python 3.14 docs", 5))

    assert prepared.backend is WebSearchBackend.BRAVE
    assert prepared.request_body is None
    assert prepared.endpoint.startswith(BRAVE_SEARCH_ENDPOINT + "?")
    query = parse_qs(urlsplit(prepared.endpoint).query)
    assert query["q"] == ["Python 3.14 docs"]
    assert query["count"] == ["5"]
    assert "secret" not in prepared.endpoint


def test_prepare_requires_explicit_backend_when_both_credentials_are_available() -> None:
    environment = {
        BRAVE_SEARCH_API_KEY_ENV: "brave-secret",
        TAVILY_SEARCH_API_KEY_ENV: "tavily-secret",
    }

    with pytest.raises(WebSearchPreparationError, match=WEB_SEARCH_BACKEND_ENV):
        WebSearchTool(environment, transport=FakeTransport(response([]))).prepare(search_call())

    selected = WebSearchTool(
        {**environment, WEB_SEARCH_BACKEND_ENV: "tavily"},
        transport=FakeTransport(response([])),
    ).prepare(search_call())
    assert selected.backend is WebSearchBackend.TAVILY


def test_runtime_source_activation_supports_ordered_multiple_sources_with_primary_only_execution() -> (
    None
):
    environment = {
        BRAVE_SEARCH_API_KEY_ENV: "brave-secret",
        TAVILY_SEARCH_API_KEY_ENV: "tavily-secret",
    }
    tool = WebSearchTool(environment, transport=FakeTransport(response([])))

    unconfigured = tool.source_configuration()
    assert unconfigured.available_sources == (
        WebSearchBackend.BRAVE,
        WebSearchBackend.TAVILY,
    )
    assert unconfigured.active_sources == ()
    assert unconfigured.selection_source is WebSearchSelectionSource.UNCONFIGURED

    configured = tool.configure_sources(("tavily", "brave"))
    assert configured.active_sources == (
        WebSearchBackend.TAVILY,
        WebSearchBackend.BRAVE,
    )
    assert configured.primary_source is WebSearchBackend.TAVILY
    assert configured.execution_sources == (WebSearchBackend.TAVILY,)
    assert configured.execution_mode == "primary-only"
    assert configured.selection_source is WebSearchSelectionSource.RUNTIME
    assert tool.prepare(search_call()).backend is WebSearchBackend.TAVILY

    switched = tool.configure_sources(("brave",))
    assert switched.active_sources == (WebSearchBackend.BRAVE,)
    assert tool.prepare(search_call()).backend is WebSearchBackend.BRAVE

    reset = tool.reset_source_configuration()
    assert reset.active_sources == ()
    assert reset.error is not None
    with pytest.raises(WebSearchPreparationError, match=WEB_SEARCH_BACKEND_ENV):
        tool.prepare(search_call())


def test_runtime_source_activation_rejects_unknown_unavailable_or_duplicate_sources() -> None:
    tool = WebSearchTool(
        {TAVILY_SEARCH_API_KEY_ENV: "tavily-secret"},
        transport=FakeTransport(response([])),
    )
    original = tool.source_configuration()

    for sources in ((), ("other",), ("brave",), ("tavily", "tavily")):
        with pytest.raises(WebSearchPreparationError):
            tool.configure_sources(sources)
        assert tool.source_configuration() == original


def test_prepare_rejects_unknown_or_unavailable_explicit_backend() -> None:
    with pytest.raises(WebSearchPreparationError, match="must be 'brave' or 'tavily'"):
        WebSearchTool(
            {WEB_SEARCH_BACKEND_ENV: "other"}, transport=FakeTransport(response([]))
        ).prepare(search_call())
    with pytest.raises(WebSearchPreparationError, match=BRAVE_SEARCH_API_KEY_ENV):
        WebSearchTool(
            {
                WEB_SEARCH_BACKEND_ENV: "brave",
                TAVILY_SEARCH_API_KEY_ENV: "tavily-secret",
            },
            transport=FakeTransport(response([])),
        ).prepare(search_call())


@pytest.mark.parametrize(
    ("environment", "call"),
    [
        ({}, search_call()),
        ({TAVILY_SEARCH_API_KEY_ENV: ""}, search_call()),
        ({TAVILY_SEARCH_API_KEY_ENV: "secret key"}, search_call()),
        ({TAVILY_SEARCH_API_KEY_ENV: "secret\u2603"}, search_call()),
        ({TAVILY_SEARCH_API_KEY_ENV: "secret"}, search_call(" ")),
        ({TAVILY_SEARCH_API_KEY_ENV: "secret"}, search_call(max_results=0)),
        ({TAVILY_SEARCH_API_KEY_ENV: "secret"}, search_call(max_results=11)),
    ],
)
def test_prepare_rejects_unavailable_or_malformed_search(environment, call) -> None:
    tool = WebSearchTool(environment, transport=FakeTransport(response([])))

    with pytest.raises(WebSearchPreparationError):
        tool.prepare(call)


def test_execute_returns_bounded_json_lines_and_never_exposes_credential() -> None:
    transport = FakeTransport(
        response(
            [
                {
                    "title": "Python 3.14 documentation",
                    "url": "https://docs.python.org/3.14/",
                    "content": "Official documentation.",
                },
                {
                    "title": "Duplicate",
                    "url": "https://docs.python.org/3.14/",
                    "content": "Duplicate result.",
                },
                {
                    "title": "Unsafe credentials",
                    "url": "https://user:pass@example.com/private",
                    "content": "Must be filtered.",
                },
                {
                    "title": "Unsafe control",
                    "url": "https://example.com/line\nbreak",
                    "content": "Must be filtered.",
                },
                {
                    "title": "Malformed host",
                    "url": "https://example.com\uff0funsafe",
                    "content": "Must be filtered without crashing.",
                },
                {
                    "title": "Malformed URL text",
                    "url": "https://example.com/\ud800",
                    "content": "Must be filtered without crashing.",
                },
                {
                    "title": "Release",
                    "url": "https://python.org/downloads/release/python-3140/",
                    "content": "Release page.\u0000 control removed.",
                },
                {
                    "title": "Broken \ud800 title",
                    "url": "https://example.com/surrogate-text",
                    "content": "Broken \ud800 snippet.",
                },
            ]
        )
    )
    tool = WebSearchTool({TAVILY_SEARCH_API_KEY_ENV: "top-secret"}, transport=transport)

    result = tool.execute_detailed(tool.prepare(search_call(max_results=3)))

    assert result.outcome is WebSearchOutcome.SUCCEEDED
    assert result.result_code == "ok_truncated"
    assert result.tool_result.truncated is True
    lines = result.tool_result.content.splitlines()
    assert json.loads(lines[0]) == {
        "backend": "tavily",
        "domain": "docs.python.org",
        "snippet": "Official documentation.",
        "title": "Python 3.14 documentation",
        "url": "https://docs.python.org/3.14/",
    }
    assert json.loads(lines[1])["domain"] == "python.org"
    assert json.loads(lines[2])["title"] == "Broken ? title"
    assert json.loads(lines[2])["snippet"] == "Broken ? snippet."
    assert json.loads(lines[-1]) == {"truncated": True}
    assert "top-secret" not in result.tool_result.content
    assert transport.calls == [
        {
            "backend": WebSearchBackend.TAVILY,
            "endpoint": TAVILY_SEARCH_ENDPOINT,
            "body": tool.prepare(search_call(max_results=3)).request_body,
            "api_key": "top-secret",
            "timeout_seconds": WEB_SEARCH_TIMEOUT_SECONDS,
            "max_response_bytes": MAX_WEB_SEARCH_RESPONSE_BYTES,
        }
    ]


def test_execute_normalizes_brave_results_with_explicit_provenance() -> None:
    transport = FakeTransport(
        SearchHttpResponse(
            200,
            "application/json",
            b'{"web":{"results":[{"title":"Python","url":"https://python.org/","description":"Official site"}]}}',
        )
    )
    tool = WebSearchTool({BRAVE_SEARCH_API_KEY_ENV: "secret"}, transport=transport)

    result = tool.execute_detailed(tool.prepare(search_call()))

    assert result.outcome is WebSearchOutcome.SUCCEEDED
    assert json.loads(result.tool_result.content) == {
        "backend": "brave",
        "domain": "python.org",
        "snippet": "Official site",
        "title": "Python",
        "url": "https://python.org/",
    }
    assert transport.calls[0]["backend"] is WebSearchBackend.BRAVE
    assert transport.calls[0]["body"] is None


@pytest.mark.parametrize(
    "search_response",
    [
        SearchHttpResponse(200, "text/html", b"<html></html>"),
        SearchHttpResponse(200, "application/json", b"not-json"),
        SearchHttpResponse(200, "application/json", b"[]"),
        SearchHttpResponse(503, "application/json", b"{}"),
    ],
)
def test_execute_rejects_invalid_or_unsuccessful_responses(search_response) -> None:
    tool = WebSearchTool(
        {TAVILY_SEARCH_API_KEY_ENV: "secret"}, transport=FakeTransport(search_response)
    )

    result = tool.execute_detailed(tool.prepare(search_call()))

    assert result.outcome is WebSearchOutcome.FAILED
    assert result.tool_result.is_error is True
    assert result.result_code in {"search_http_error", "search_response_invalid"}


def test_transport_uncertainty_is_partial_and_warns_against_retry() -> None:
    transport = FakeTransport(
        WebSearchTransportError(
            "search_timed_out",
            "timed out; do not retry automatically",
            delivery_unknown=True,
        )
    )
    tool = WebSearchTool({TAVILY_SEARCH_API_KEY_ENV: "secret"}, transport=transport)

    result = tool.execute_detailed(tool.prepare(search_call()))

    assert result.outcome is WebSearchOutcome.PARTIAL
    assert result.result_code == "search_timed_out"
    assert result.tool_result.is_error is True
    assert "do not retry automatically" in result.tool_result.content
