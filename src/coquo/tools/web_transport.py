"""Pinned-public-address HTTP GET transport shared by bounded web tools."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

MAX_WEB_URL_CHARACTERS = 4096
MAX_WEB_URL_BYTES = 4096
MAX_WEB_REDIRECTS = 5
WEB_USER_AGENT = "coquo/0.1"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class WebTransportError(RuntimeError):
    def __init__(self, result_code: str, message: str, *, delivery_unknown: bool) -> None:
        super().__init__(message)
        self.result_code = result_code
        self.delivery_unknown = delivery_unknown


@dataclass(frozen=True)
class WebHttpResponse:
    status_code: int
    content_type: str
    content_encoding: str
    body: bytes
    final_url: str
    redirects: int
    headers: tuple[tuple[str, str], ...] = ()


class WebGetTransport(Protocol):
    def fetch(
        self,
        url: str,
        *,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> WebHttpResponse: ...


class PinnedWebGetTransport:
    """Resolve publicly, pin one IP, and revalidate every redirect target."""

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self._ssl_context = ssl_context or ssl.create_default_context()

    def fetch(
        self,
        url: str,
        *,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> WebHttpResponse:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("web timeout is invalid")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("web response byte limit is invalid")
        current = canonical_public_web_url(url)
        for redirect_count in range(MAX_WEB_REDIRECTS + 1):
            response = self._request_once(
                current,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
            if response.status_code not in _REDIRECT_STATUSES:
                return WebHttpResponse(
                    response.status_code,
                    response.content_type,
                    response.content_encoding,
                    response.body,
                    current,
                    redirect_count,
                    response.headers,
                )
            location = response.location
            if not location:
                raise WebTransportError(
                    "web_redirect_invalid",
                    "web response redirect is missing a Location header",
                    delivery_unknown=False,
                )
            if redirect_count == MAX_WEB_REDIRECTS:
                raise WebTransportError(
                    "web_redirect_limit",
                    f"web response exceeds {MAX_WEB_REDIRECTS} redirects",
                    delivery_unknown=False,
                )
            current = canonical_public_web_url(urljoin(current, location))
        raise AssertionError("unreachable redirect loop")

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> WebHttpResponse:
        """Send one pinned public-address request without following redirects."""
        if method not in {"GET", "POST", "DELETE"}:
            raise ValueError("web request method is unsupported")
        if type(headers) is not dict or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or any(character in key + value for character in "\r\n")
            for key, value in headers.items()
        ):
            raise ValueError("web request headers are invalid")
        if body is not None and not isinstance(body, bytes):
            raise ValueError("web request body is invalid")
        canonical = canonical_public_web_url(url)
        response = self._request_once(
            canonical,
            method=method,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        return WebHttpResponse(
            response.status_code,
            response.content_type,
            response.content_encoding,
            response.body,
            canonical,
            0,
            response.headers,
        )

    def _request_once(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> _OneResponse:
        parsed = urlsplit(url)
        host = parsed.hostname
        assert host is not None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = resolve_public_addresses(host, port)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        last_error: BaseException | None = None
        for address in addresses:
            connection: http.client.HTTPConnection
            if parsed.scheme == "https":
                connection = _PinnedHTTPSConnection(
                    host,
                    port,
                    address,
                    timeout=timeout_seconds,
                    context=self._ssl_context,
                )
            else:
                connection = _PinnedHTTPConnection(
                    host,
                    port,
                    address,
                    timeout=timeout_seconds,
                )
            try:
                request_headers = {
                    "Accept": "text/html, text/plain, application/json, application/xml;q=0.9, */*;q=0.1",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": WEB_USER_AGENT,
                }
                if headers is not None:
                    request_headers.update(headers)
                connection.request(method, target, body=body, headers=request_headers)
                response = connection.getresponse()
                content_type = response.getheader("Content-Type", "")
                encoding = response.getheader("Content-Encoding", "").strip().lower()
                location = response.getheader("Location")
                response_headers = tuple(
                    (key.lower(), value) for key, value in response.getheaders()
                )
                declared = response.getheader("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) > max_response_bytes:
                            raise WebTransportError(
                                "web_response_too_large",
                                "web response exceeds the Host byte limit",
                                delivery_unknown=False,
                            )
                    except ValueError:
                        pass
                if encoding not in {"", "identity"}:
                    raise WebTransportError(
                        "web_content_encoding_unsupported",
                        "web response uses an unsupported content encoding",
                        delivery_unknown=False,
                    )
                body = _read_bounded(response, max_response_bytes)
                return _OneResponse(
                    response.status,
                    content_type,
                    encoding,
                    body,
                    location,
                    response_headers,
                )
            except WebTransportError:
                raise
            except (TimeoutError, socket.timeout) as error:
                last_error = error
            except (ssl.SSLError, OSError, http.client.HTTPException) as error:
                last_error = error
            finally:
                connection.close()
        if isinstance(last_error, (TimeoutError, socket.timeout)):
            raise WebTransportError(
                "web_timed_out",
                "web request timed out; remote delivery is unknown, so do not retry automatically",
                delivery_unknown=True,
            ) from None
        raise WebTransportError(
            "web_transport_error",
            "web transport failed; remote delivery is unknown, so do not retry automatically",
            delivery_unknown=True,
        ) from None


@dataclass(frozen=True)
class _OneResponse:
    status_code: int
    content_type: str
    content_encoding: str
    body: bytes
    location: str | None
    headers: tuple[tuple[str, str], ...]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, **kwargs) -> None:  # noqa: ANN003
        super().__init__(host, port, **kwargs)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._address, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(
        self,
        host: str,
        port: int,
        address: str,
        *,
        context: ssl.SSLContext,
        **kwargs,  # noqa: ANN003
    ) -> None:
        super().__init__(host, port, address, **kwargs)
        self._context = context

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def canonical_public_web_url(value: str) -> str:
    """Validate syntax and literal-IP safety; DNS safety is checked before every request."""
    if not isinstance(value, str):
        raise WebTransportError("web_url_invalid", "web URL must be text", delivery_unknown=False)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise WebTransportError(
            "web_url_invalid", "web URL must be valid UTF-8", delivery_unknown=False
        ) from None
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_WEB_URL_CHARACTERS
        or len(encoded) > MAX_WEB_URL_BYTES
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise WebTransportError(
            "web_url_invalid", "web URL is invalid or too long", delivery_unknown=False
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise WebTransportError(
            "web_url_invalid", "web URL is malformed", delivery_unknown=False
        ) from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebTransportError(
            "web_url_invalid", "web URL must use HTTP or HTTPS", delivery_unknown=False
        )
    if parsed.username is not None or parsed.password is not None:
        raise WebTransportError(
            "web_url_credentials", "web URL must not contain credentials", delivery_unknown=False
        )
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise WebTransportError(
            "web_url_port",
            "web URL must use the standard port for its scheme",
            delivery_unknown=False,
        )
    host = _canonical_host(parsed.hostname)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise WebTransportError(
            "web_address_not_public",
            "web URL resolves to a non-public address",
            delivery_unknown=False,
        )
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(SplitResult(parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))


def resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise WebTransportError(
            "web_dns_failed",
            "web URL hostname could not be resolved",
            delivery_unknown=False,
        ) from None
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in infos:
        try:
            addresses.add(ipaddress.ip_address(info[4][0]))
        except ValueError:
            raise WebTransportError(
                "web_dns_invalid",
                "web URL hostname resolved to an invalid address",
                delivery_unknown=False,
            ) from None
    if not addresses or any(not address.is_global for address in addresses):
        raise WebTransportError(
            "web_address_not_public",
            "web URL hostname must resolve only to public addresses",
            delivery_unknown=False,
        )
    return tuple(
        str(address) for address in sorted(addresses, key=lambda item: (item.version, item.packed))
    )


def _canonical_host(value: str) -> str:
    host = value.rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise WebTransportError(
            "web_url_invalid", "web URL hostname is invalid", delivery_unknown=False
        ) from None
    if (
        not encoded
        or len(encoded) > 253
        or any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in encoded.split(".")
        )
    ):
        raise WebTransportError(
            "web_url_invalid", "web URL hostname is invalid", delivery_unknown=False
        )
    return encoded


def _read_bounded(response: http.client.HTTPResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise WebTransportError(
                "web_response_too_large",
                "web response exceeds the Host byte limit",
                delivery_unknown=False,
            )
    return b"".join(chunks)
