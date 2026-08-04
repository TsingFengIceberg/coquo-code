"""Bounded independent web search through fixed Brave and Tavily APIs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import socket
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from leonervis_code.core.actions import ActionPrecondition
from leonervis_code.core.contracts import ToolResult, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.core.permissions import PermissionAction

WEB_SEARCH_TOOL_NAME = "web_search"
WEB_SEARCH_BACKEND_ENV = "LEONERVIS_WEB_SEARCH_BACKEND"
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_SEARCH_API_KEY_ENV = "BRAVE_SEARCH_API_KEY"
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
TAVILY_SEARCH_API_KEY_ENV = "TAVILY_API_KEY"
MIN_WEB_SEARCH_RESULTS = 1
MAX_WEB_SEARCH_RESULTS = 10
MAX_WEB_SEARCH_QUERY_CHARACTERS = 512
MAX_WEB_SEARCH_QUERY_BYTES = 2048
MAX_WEB_SEARCH_RESPONSE_BYTES = 256 * 1024
MAX_WEB_SEARCH_OUTPUT_BYTES = 32 * 1024
MAX_WEB_SEARCH_TITLE_BYTES = 1024
MAX_WEB_SEARCH_URL_BYTES = 4096
MAX_WEB_SEARCH_SNIPPET_BYTES = 4096
WEB_SEARCH_TIMEOUT_SECONDS = 15
WEB_SEARCH_MAX_PARSED_RESULTS = 100
WEB_SEARCH_TRUNCATION_SENTINEL = '{"truncated":true}\n'


class WebSearchBackend(StrEnum):
    BRAVE = "brave"
    TAVILY = "tavily"


class WebSearchSelectionSource(StrEnum):
    AUTOMATIC = "automatic"
    ENVIRONMENT = "environment"
    RUNTIME = "runtime"
    UNCONFIGURED = "unconfigured"


class WebSearchPreparationError(ValueError):
    """Reject an unavailable or malformed search before permission evaluation."""


class WebSearchTransportError(RuntimeError):
    """A bounded transport failure with a stable result code."""

    def __init__(self, result_code: str, message: str, *, delivery_unknown: bool) -> None:
        super().__init__(message)
        self.result_code = result_code
        self.delivery_unknown = delivery_unknown


@dataclass(frozen=True)
class SearchHttpResponse:
    status_code: int
    content_type: str
    body: bytes


class WebSearchTransport(Protocol):
    def search(
        self,
        *,
        backend: WebSearchBackend,
        endpoint: str,
        body: bytes | None,
        api_key: str,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> SearchHttpResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibWebSearchTransport:
    """Perform one fixed-endpoint Brave or Tavily request without redirects."""

    def search(
        self,
        *,
        backend: WebSearchBackend,
        endpoint: str,
        body: bytes | None,
        api_key: str,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> SearchHttpResponse:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "leonervis-code/0.1",
        }
        if backend is WebSearchBackend.BRAVE:
            headers["X-Subscription-Token"] = api_key
            method = "GET"
        elif backend is WebSearchBackend.TAVILY:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["Content-Type"] = "application/json"
            method = "POST"
        else:
            raise ValueError("web search backend is invalid")
        request = Request(endpoint, data=body, headers=headers, method=method)
        backend_label = _backend_label(backend)
        opener = build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                status_code = response.getcode()
                content_type = response.headers.get("Content-Type", "")
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > max_response_bytes:
                            raise WebSearchTransportError(
                                "search_response_too_large",
                                f"{backend_label} response exceeds the Host byte limit",
                                delivery_unknown=False,
                            )
                    except ValueError:
                        pass
                body = response.read(max_response_bytes + 1)
        except HTTPError as error:
            raise WebSearchTransportError(
                "search_http_error",
                f"{backend_label} returned HTTP {error.code}",
                delivery_unknown=False,
            ) from None
        except (TimeoutError, socket.timeout):
            raise WebSearchTransportError(
                "search_timed_out",
                f"{backend_label} timed out; delivery or billing may be unknown, so do not retry automatically",
                delivery_unknown=True,
            ) from None
        except URLError:
            raise WebSearchTransportError(
                "search_transport_error",
                f"{backend_label} transport failed; delivery or billing may be unknown, so do not retry automatically",
                delivery_unknown=True,
            ) from None
        except OSError:
            raise WebSearchTransportError(
                "search_transport_error",
                f"{backend_label} transport failed; delivery or billing may be unknown, so do not retry automatically",
                delivery_unknown=True,
            ) from None
        if len(body) > max_response_bytes:
            raise WebSearchTransportError(
                "search_response_too_large",
                f"{backend_label} response exceeds the Host byte limit",
                delivery_unknown=False,
            )
        return SearchHttpResponse(status_code, content_type, body)


@dataclass(frozen=True)
class PreparedWebSearch:
    request: ToolUse
    query: str
    max_results: int
    backend: WebSearchBackend
    endpoint: str
    request_body: bytes | None
    action: PermissionAction
    precondition: ActionPrecondition


@dataclass(frozen=True)
class WebSearchSourceConfiguration:
    """Process-local source activation with one executable primary backend."""

    available_sources: tuple[WebSearchBackend, ...]
    active_sources: tuple[WebSearchBackend, ...]
    primary_source: WebSearchBackend | None
    selection_source: WebSearchSelectionSource
    error: str | None = None
    ordered_sources: tuple[str, ...] = ()
    provider_available: bool = False
    provider_active: bool = False
    provider_adapter: str | None = None
    provider_mode: str = "auto"
    provider_allowed_domains: tuple[str, ...] = ()
    provider_context_size: str | None = None

    def __post_init__(self) -> None:
        supported = tuple(WebSearchBackend)
        if any(type(source) is not WebSearchBackend for source in self.available_sources):
            raise ValueError("available web search sources are invalid")
        if any(type(source) is not WebSearchBackend for source in self.active_sources):
            raise ValueError("active web search sources are invalid")
        if len(set(self.available_sources)) != len(self.available_sources) or any(
            source not in supported for source in self.available_sources
        ):
            raise ValueError("available web search sources are invalid")
        if len(set(self.active_sources)) != len(self.active_sources) or any(
            source not in self.available_sources for source in self.active_sources
        ):
            raise ValueError("active web search sources are invalid")
        expected_primary = self.active_sources[0] if self.active_sources else None
        if self.primary_source is not expected_primary:
            raise ValueError("primary web search source must be the first active source")
        if type(self.selection_source) is not WebSearchSelectionSource:
            raise ValueError("web search selection source is invalid")
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise ValueError("web search configuration error is invalid")
        if self.ordered_sources:
            supported_names = {"provider", *(source.value for source in WebSearchBackend)}
            if len(set(self.ordered_sources)) != len(self.ordered_sources) or any(
                source not in supported_names for source in self.ordered_sources
            ):
                raise ValueError("ordered web search sources are invalid")
            if self.provider_active != ("provider" in self.ordered_sources):
                raise ValueError("provider web search activation is inconsistent")
            external_names = tuple(
                source for source in self.ordered_sources if source != "provider"
            )
            if external_names != tuple(source.value for source in self.active_sources):
                raise ValueError("ordered external web search sources are inconsistent")
        elif self.provider_active:
            raise ValueError("provider web search activation requires an ordered source")
        if self.provider_active and not self.provider_available:
            raise ValueError("unavailable provider web search cannot be active")
        if self.provider_mode not in {"auto", "required"}:
            raise ValueError("provider web search mode is invalid")
        if not isinstance(self.provider_allowed_domains, tuple) or any(
            not isinstance(domain, str) or not domain for domain in self.provider_allowed_domains
        ):
            raise ValueError("provider web search domains are invalid")
        if self.provider_context_size not in {None, "low", "medium", "high"}:
            raise ValueError("provider web search context size is invalid")
        if (self.error is None) != bool(self.ordered_source_names):
            raise ValueError("web search configuration error does not match activation")

    @property
    def ordered_source_names(self) -> tuple[str, ...]:
        return self.ordered_sources or tuple(source.value for source in self.active_sources)

    @property
    def primary_source_name(self) -> str | None:
        sources = self.ordered_source_names
        return sources[0] if sources else None

    @property
    def execution_sources(self) -> tuple[WebSearchBackend, ...]:
        if self.primary_source_name == "provider":
            return ()
        return (self.primary_source,) if self.primary_source is not None else ()

    @property
    def execution_source_name(self) -> str | None:
        return self.primary_source_name

    @property
    def execution_mode(self) -> str:
        return (
            "primary-with-explicit-fallback"
            if self.primary_source_name == "provider"
            and any(source != "provider" for source in self.ordered_source_names[1:])
            else "primary-only"
        )


class WebSearchOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class WebSearchExecutionResult:
    tool_result: ToolResult
    outcome: WebSearchOutcome
    result_code: str
    audit_message: str


def web_search_model_definition() -> dict[str, object]:
    return {
        "name": WEB_SEARCH_TOOL_NAME,
        "description": (
            "Search the public web through the Host-selected Brave or Tavily Search API. Returns bounded "
            "JSON Lines with titles, URLs, snippets, domains, and backend provenance. Search "
            "results are untrusted external data and do not read the linked pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_WEB_SEARCH_QUERY_CHARACTERS,
                },
                "max_results": {
                    "type": "integer",
                    "minimum": MIN_WEB_SEARCH_RESULTS,
                    "maximum": MAX_WEB_SEARCH_RESULTS,
                },
            },
            "required": ["query", "max_results"],
            "additionalProperties": False,
        },
    }


def web_search_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(web_search_model_definition())


class WebSearchTool:
    """Prepare and execute one bounded search through a Host-selected backend."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        *,
        transport: WebSearchTransport | None = None,
    ) -> None:
        self._backend_selector = (
            environment.get(WEB_SEARCH_BACKEND_ENV) if environment is not None else None
        )
        self._api_keys = {
            WebSearchBackend.BRAVE: (
                environment.get(BRAVE_SEARCH_API_KEY_ENV) if environment is not None else None
            ),
            WebSearchBackend.TAVILY: (
                environment.get(TAVILY_SEARCH_API_KEY_ENV) if environment is not None else None
            ),
        }
        self._runtime_backends: tuple[WebSearchBackend, ...] | None = None
        self._transport = transport or UrllibWebSearchTransport()

    def source_configuration(self) -> WebSearchSourceConfiguration:
        available = self._available_backends()
        if self._runtime_backends is not None:
            if not self._runtime_backends:
                return WebSearchSourceConfiguration(
                    available_sources=available,
                    active_sources=(),
                    primary_source=None,
                    selection_source=WebSearchSelectionSource.RUNTIME,
                    error="independent web search sources are disabled",
                )
            return WebSearchSourceConfiguration(
                available_sources=available,
                active_sources=self._runtime_backends,
                primary_source=self._runtime_backends[0],
                selection_source=WebSearchSelectionSource.RUNTIME,
            )
        try:
            backend, _api_key = self._resolve_environment_backend()
        except WebSearchPreparationError as error:
            return WebSearchSourceConfiguration(
                available_sources=available,
                active_sources=(),
                primary_source=None,
                selection_source=WebSearchSelectionSource.UNCONFIGURED,
                error=str(error),
            )
        return WebSearchSourceConfiguration(
            available_sources=available,
            active_sources=(backend,),
            primary_source=backend,
            selection_source=(
                WebSearchSelectionSource.ENVIRONMENT
                if self._backend_selector is not None
                else WebSearchSelectionSource.AUTOMATIC
            ),
        )

    def configure_sources(self, sources: tuple[str, ...]) -> WebSearchSourceConfiguration:
        if not isinstance(sources, tuple) or not sources or len(sources) > len(WebSearchBackend):
            raise WebSearchPreparationError("one or more web search sources are required")
        try:
            backends = tuple(WebSearchBackend(source) for source in sources)
        except (TypeError, ValueError):
            raise WebSearchPreparationError(
                "web search sources must be selected from: brave, tavily"
            ) from None
        if len(set(backends)) != len(backends):
            raise WebSearchPreparationError("web search sources must not contain duplicates")
        for backend in backends:
            if not _valid_api_key(self._api_keys[backend]):
                raise WebSearchPreparationError(
                    f"web search source '{backend.value}' requires a valid "
                    f"{_backend_credential_env(backend)} environment value"
                )
        self._runtime_backends = backends
        return self.source_configuration()

    def reset_source_configuration(self) -> WebSearchSourceConfiguration:
        """Restore the standalone source selection derived from the environment."""
        self._runtime_backends = None
        return self.source_configuration()

    def disable_sources(self) -> WebSearchSourceConfiguration:
        """Explicitly disable independent sources while retaining credential availability."""
        self._runtime_backends = ()
        return self.source_configuration()

    def prepare(self, request: ToolUse) -> PreparedWebSearch:
        try:
            arguments = request.arguments.as_mapping()
        except AttributeError:
            raise WebSearchPreparationError("web_search input is malformed") from None
        if request.name != WEB_SEARCH_TOOL_NAME or set(arguments) != {"query", "max_results"}:
            raise WebSearchPreparationError("web_search input is malformed")
        query = arguments["query"]
        max_results = arguments["max_results"]
        try:
            query_bytes = len(query.encode("utf-8")) if isinstance(query, str) else 0
        except UnicodeEncodeError:
            query_bytes = MAX_WEB_SEARCH_QUERY_BYTES + 1
        if (
            not isinstance(query, str)
            or not query.strip()
            or "\x00" in query
            or len(query) > MAX_WEB_SEARCH_QUERY_CHARACTERS
            or query_bytes > MAX_WEB_SEARCH_QUERY_BYTES
        ):
            raise WebSearchPreparationError("web_search query is invalid or exceeds its limit")
        if (
            type(max_results) is not int
            or not MIN_WEB_SEARCH_RESULTS <= max_results <= MAX_WEB_SEARCH_RESULTS
        ):
            raise WebSearchPreparationError("web_search max_results is invalid")
        backend, _api_key = self._resolve_backend()
        endpoint, request_body = _prepare_backend_request(backend, query, max_results)
        return PreparedWebSearch(
            request=request,
            query=query,
            max_results=max_results,
            backend=backend,
            endpoint=endpoint,
            request_body=request_body,
            action=PermissionAction.NETWORK_READ,
            precondition=ActionPrecondition.expected_configuration(
                _backend_configuration_fingerprint(backend)
            ),
        )

    def revalidate(self, prepared: PreparedWebSearch) -> ActionPrecondition:
        if not isinstance(prepared, PreparedWebSearch):
            raise WebSearchPreparationError("prepared web_search request is invalid")
        backend, _api_key = self._resolve_backend()
        if backend is not prepared.backend:
            raise WebSearchPreparationError(
                "web_search backend selection changed after preparation"
            )
        return ActionPrecondition.expected_configuration(
            _backend_configuration_fingerprint(backend)
        )

    def execute_detailed(self, prepared: PreparedWebSearch) -> WebSearchExecutionResult:
        try:
            backend, api_key = self._resolve_backend()
        except WebSearchPreparationError as error:
            return _failed_result(
                prepared.request,
                "search_backend_unavailable",
                str(error),
            )
        if backend is not prepared.backend:
            return _failed_result(
                prepared.request,
                "search_backend_changed",
                "web_search backend selection changed after preparation",
            )
        try:
            response = self._transport.search(
                backend=backend,
                endpoint=prepared.endpoint,
                body=prepared.request_body,
                api_key=api_key,
                timeout_seconds=WEB_SEARCH_TIMEOUT_SECONDS,
                max_response_bytes=MAX_WEB_SEARCH_RESPONSE_BYTES,
            )
        except WebSearchTransportError as error:
            outcome = (
                WebSearchOutcome.PARTIAL if error.delivery_unknown else WebSearchOutcome.FAILED
            )
            return WebSearchExecutionResult(
                ToolResult(prepared.request.tool_use_id, str(error), is_error=True),
                outcome,
                error.result_code,
                str(error),
            )
        if response.status_code != 200:
            return _failed_result(
                prepared.request,
                "search_http_error",
                f"{_backend_label(backend)} returned HTTP {response.status_code}",
            )
        if "application/json" not in response.content_type.lower():
            return _failed_result(
                prepared.request,
                "search_response_invalid",
                f"{_backend_label(backend)} returned an unsupported content type",
            )
        try:
            content, truncated = _parse_search_response(
                response.body, prepared.max_results, backend
            )
        except ValueError as error:
            return _failed_result(prepared.request, "search_response_invalid", str(error))
        return WebSearchExecutionResult(
            ToolResult(prepared.request.tool_use_id, content, truncated=truncated),
            WebSearchOutcome.SUCCEEDED,
            "ok_truncated" if truncated else "ok",
            f"{_backend_label(backend)} returned {'truncated' if truncated else 'bounded'} results",
        )

    def _resolve_backend(self) -> tuple[WebSearchBackend, str]:
        if self._runtime_backends is not None:
            if not self._runtime_backends:
                raise WebSearchPreparationError(
                    "independent web search is disabled; use /search use brave or tavily"
                )
            backend = self._runtime_backends[0]
            api_key = self._api_keys[backend]
            if not _valid_api_key(api_key):
                raise WebSearchPreparationError(
                    f"web search source '{backend.value}' requires a valid "
                    f"{_backend_credential_env(backend)} environment value"
                )
            return backend, api_key
        return self._resolve_environment_backend()

    def _resolve_environment_backend(self) -> tuple[WebSearchBackend, str]:
        if self._backend_selector is not None:
            try:
                backend = WebSearchBackend(self._backend_selector)
            except ValueError:
                raise WebSearchPreparationError(
                    f"{WEB_SEARCH_BACKEND_ENV} must be 'brave' or 'tavily'"
                ) from None
            api_key = self._api_keys[backend]
            if not _valid_api_key(api_key):
                raise WebSearchPreparationError(
                    f"web_search backend '{backend.value}' requires a valid "
                    f"{_backend_credential_env(backend)} environment value"
                )
            return backend, api_key

        available = self._available_backends()
        if len(available) == 1:
            backend = available[0]
            api_key = self._api_keys[backend]
            assert isinstance(api_key, str)
            return backend, api_key
        if not available:
            raise WebSearchPreparationError(
                f"web_search requires a valid {BRAVE_SEARCH_API_KEY_ENV} or "
                f"{TAVILY_SEARCH_API_KEY_ENV} environment value"
            )
        raise WebSearchPreparationError(
            f"both search credentials are available; set {WEB_SEARCH_BACKEND_ENV} to "
            "'brave' or 'tavily', or use /search use <source> [source...] in the REPL"
        )

    def _available_backends(self) -> tuple[WebSearchBackend, ...]:
        return tuple(
            backend for backend, api_key in self._api_keys.items() if _valid_api_key(api_key)
        )


def _prepare_backend_request(
    backend: WebSearchBackend, query: str, max_results: int
) -> tuple[str, bytes | None]:
    if backend is WebSearchBackend.BRAVE:
        query_string = urlencode(
            (
                ("q", query),
                ("count", str(max_results)),
                ("safesearch", "moderate"),
                ("spellcheck", "true"),
                ("text_decorations", "false"),
                ("result_filter", "web"),
            )
        )
        return f"{BRAVE_SEARCH_ENDPOINT}?{query_string}", None
    if backend is not WebSearchBackend.TAVILY:
        raise ValueError("web search backend is invalid")
    body = json.dumps(
        {
            "auto_parameters": False,
            "chunks_per_source": 1,
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False,
            "include_usage": True,
            "max_results": max_results,
            "query": query,
            "search_depth": "basic",
            "topic": "general",
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return TAVILY_SEARCH_ENDPOINT, body


def _valid_api_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 4096
        and all("!" <= character <= "~" for character in value)
    )


def _backend_label(backend: WebSearchBackend) -> str:
    if backend is WebSearchBackend.BRAVE:
        return "Brave Search"
    if backend is WebSearchBackend.TAVILY:
        return "Tavily Search"
    raise ValueError("web search backend is invalid")


def _backend_credential_env(backend: WebSearchBackend) -> str:
    if backend is WebSearchBackend.BRAVE:
        return BRAVE_SEARCH_API_KEY_ENV
    if backend is WebSearchBackend.TAVILY:
        return TAVILY_SEARCH_API_KEY_ENV
    raise ValueError("web search backend is invalid")


def _backend_configuration_fingerprint(backend: WebSearchBackend) -> str:
    endpoint = (
        BRAVE_SEARCH_ENDPOINT
        if backend is WebSearchBackend.BRAVE
        else TAVILY_SEARCH_ENDPOINT
        if backend is WebSearchBackend.TAVILY
        else None
    )
    if endpoint is None:
        raise ValueError("web search backend is invalid")
    payload = f"web-search-backend-v1\0{backend.value}\0{endpoint}".encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _parse_search_response(
    body: bytes, max_results: int, backend: WebSearchBackend
) -> tuple[str, bool]:
    backend_label = _backend_label(backend)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError(f"{backend_label} returned malformed JSON") from None
    if not isinstance(payload, dict):
        raise ValueError(f"{backend_label} returned an invalid response object")
    if backend is WebSearchBackend.BRAVE:
        web = payload.get("web")
        if web is None:
            results = []
        elif not isinstance(web, dict) or not isinstance(web.get("results", []), list):
            raise ValueError("Brave Search returned an invalid web result collection")
        else:
            results = web.get("results", [])
        snippet_field = "description"
    elif backend is WebSearchBackend.TAVILY:
        results = payload.get("results")
        snippet_field = "content"
    else:
        raise ValueError("web search backend is invalid")
    if not isinstance(results, list):
        raise ValueError(f"{backend_label} returned an invalid result collection")
    if len(results) > WEB_SEARCH_MAX_PARSED_RESULTS:
        raise ValueError(f"{backend_label} returned too many result entries")

    records: list[str] = []
    output_bytes = 0
    seen_urls: set[str] = set()
    truncated = len(results) > max_results
    for item in results:
        if len(records) >= max_results:
            break
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        snippet = item.get(snippet_field, "")
        if not isinstance(title, str) or not isinstance(url, str) or not isinstance(snippet, str):
            continue
        if not _safe_result_url(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        record = {
            "backend": backend.value,
            "domain": urlsplit(url).hostname or "",
            "snippet": _bounded_text(snippet, MAX_WEB_SEARCH_SNIPPET_BYTES),
            "title": _bounded_text(title, MAX_WEB_SEARCH_TITLE_BYTES),
            "url": url,
        }
        encoded = (
            json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        record_bytes = len(encoded.encode("utf-8"))
        if (
            output_bytes + record_bytes + len(WEB_SEARCH_TRUNCATION_SENTINEL)
            > MAX_WEB_SEARCH_OUTPUT_BYTES
        ):
            truncated = True
            break
        records.append(encoded)
        output_bytes += record_bytes

    if not records:
        records.append(f'{{"backend":"{backend.value}","results":[]}}\n')
    if truncated:
        records.append(WEB_SEARCH_TRUNCATION_SENTINEL)
    return "".join(records), truncated


def _safe_result_url(value: str) -> bool:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if (
        not value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or len(encoded) > MAX_WEB_SEARCH_URL_BYTES
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _bounded_text(value: str, max_bytes: int) -> str:
    sanitized = "".join(
        character if character >= " " or character in "\t\n" else " " for character in value
    ).strip()
    encoded = sanitized.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _failed_result(request: ToolUse, result_code: str, message: str) -> WebSearchExecutionResult:
    return WebSearchExecutionResult(
        ToolResult(request.tool_use_id, message, is_error=True),
        WebSearchOutcome.FAILED,
        result_code,
        message,
    )
