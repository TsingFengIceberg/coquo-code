"""Local Web and IDE bridges over the existing Host Session boundary.

The bridges are transport adapters only.  They do not own Provider clients,
tools, permissions, or durable state; callers provide a prompt handler and an
optional bounded event source.  The Web server binds loopback by default and
requires an exact bearer token when configured.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
from threading import Thread
from typing import Any, Callable, Iterable, Mapping


INTERFACE_PROTOCOL_VERSION = 1
MAX_INTERFACE_BODY_BYTES = 256 * 1024
MAX_INTERFACE_PROMPT_CHARACTERS = 32_768
MAX_INTERFACE_EVENTS = 256


class InterfaceError(RuntimeError):
    """Raised when a local interface request is malformed or not authorized."""


@dataclass(frozen=True)
class InterfaceEvent:
    kind: str
    payload: Mapping[str, Any]
    sequence: int

    def as_mapping(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "payload": dict(self.payload),
            "protocol_version": INTERFACE_PROTOCOL_VERSION,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class InterfaceResponse:
    request_id: str
    outcome: str
    text: str = ""
    events: tuple[InterfaceEvent, ...] = ()
    error: str | None = None

    def as_mapping(self) -> dict[str, object]:
        return {
            "error": self.error,
            "events": [event.as_mapping() for event in self.events],
            "outcome": self.outcome,
            "protocol_version": INTERFACE_PROTOCOL_VERSION,
            "request_id": self.request_id,
            "text": self.text,
        }


def _bounded_request_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(character) < 0x20 for character in value)
    ):
        raise InterfaceError("request_id is invalid")
    return value


def _bounded_prompt(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InterfaceError("prompt must not be blank")
    if len(value) > MAX_INTERFACE_PROMPT_CHARACTERS or "\x00" in value:
        raise InterfaceError("prompt exceeds the interface limit")
    return value


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise InterfaceError("interface response is not JSON serializable") from error


class IDEJsonRpcBridge:
    """Handle one JSON-RPC-like request per input line for IDE integrations."""

    def __init__(
        self,
        prompt_handler: Callable[[str, str], InterfaceResponse],
        *,
        event_source: Callable[[], Iterable[InterfaceEvent]] | None = None,
    ) -> None:
        if not callable(prompt_handler):
            raise ValueError("prompt_handler is required")
        self.prompt_handler = prompt_handler
        self.event_source = event_source

    def handle_line(self, line: str) -> str:
        if not isinstance(line, str) or len(line.encode("utf-8")) > MAX_INTERFACE_BODY_BYTES:
            return self._error(None, "request is oversized")
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise InterfaceError("request must be an object")
            request_id = _bounded_request_id(request.get("id"))
            method = request.get("method")
            params = request.get("params", {})
            if method == "prompt":
                if not isinstance(params, dict) or set(params) != {"prompt"}:
                    raise InterfaceError("prompt params are invalid")
                response = self.prompt_handler(request_id, _bounded_prompt(params["prompt"]))
            elif method == "events":
                if params not in ({}, None):
                    raise InterfaceError("events params are invalid")
                events = tuple(self.event_source() if self.event_source is not None else ())[
                    :MAX_INTERFACE_EVENTS
                ]
                response = InterfaceResponse(request_id, "completed", events=events)
            else:
                raise InterfaceError("unknown interface method")
            if not isinstance(response, InterfaceResponse):
                raise InterfaceError("prompt handler returned an invalid response")
            return json.dumps(response.as_mapping(), ensure_ascii=False, separators=(",", ":"))
        except (InterfaceError, json.JSONDecodeError) as error:
            return self._error(None if "request_id" not in locals() else request_id, str(error))

    @staticmethod
    def _error(request_id: str | None, message: str) -> str:
        return json.dumps(
            {
                "error": message[:512],
                "outcome": "invalid-request",
                "protocol_version": INTERFACE_PROTOCOL_VERSION,
                "request_id": request_id,
                "text": "",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def serve(self, input_stream, output_stream) -> None:
        """Serve newline-delimited requests until EOF; flush every response."""
        for line in input_stream:
            output_stream.write(self.handle_line(line.rstrip("\r\n")) + "\n")
            output_stream.flush()


class _WebHandler(BaseHTTPRequestHandler):
    server_version = "CoquoLocal/1"

    def do_POST(self) -> None:  # noqa: N802
        bridge: "LocalWebBridge" = self.server.bridge  # type: ignore[attr-defined]
        if self.path != "/v1/prompt":
            self._send(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
            return
        if not bridge.authorized(self.headers.get("Authorization")):
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
            return
        length = self.headers.get("Content-Length")
        try:
            size = int(length or "-1")
        except ValueError:
            size = -1
        if size < 0 or size > MAX_INTERFACE_BODY_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body is oversized"})
            return
        try:
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"id", "prompt"}:
                raise InterfaceError("request body fields are invalid")
            response = bridge.prompt_handler(
                _bounded_request_id(payload["id"]), _bounded_prompt(payload["prompt"])
            )
            if not isinstance(response, InterfaceResponse):
                raise InterfaceError("prompt handler returned an invalid response")
            self._send(HTTPStatus.OK, response.as_mapping())
        except (UnicodeDecodeError, json.JSONDecodeError, InterfaceError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})

    def do_GET(self) -> None:  # noqa: N802
        bridge: "LocalWebBridge" = self.server.bridge  # type: ignore[attr-defined]
        if self.path != "/v1/events":
            self._send(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
            return
        if not bridge.authorized(self.headers.get("Authorization")):
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
            return
        events = tuple(bridge.event_source() if bridge.event_source is not None else ())[
            :MAX_INTERFACE_EVENTS
        ]
        self._send(HTTPStatus.OK, {"events": [event.as_mapping() for event in events]})

    def log_message(self, *_args: object) -> None:
        return

    def _send(self, status: HTTPStatus, value: object) -> None:
        payload = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class LocalWebBridge:
    """Loopback HTTP adapter with an explicit lifecycle and bearer token."""

    def __init__(
        self,
        prompt_handler: Callable[[str, str], InterfaceResponse],
        *,
        event_source: Callable[[], Iterable[InterfaceEvent]] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        bearer_token: str | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise InterfaceError("Web bridge must bind loopback")
        if type(port) is not int or not 0 <= port <= 65535:
            raise InterfaceError("Web bridge port is invalid")
        if bearer_token is not None and (
            not isinstance(bearer_token, str) or not 16 <= len(bearer_token) <= 256
        ):
            raise InterfaceError("Web bearer token is invalid")
        self.prompt_handler = prompt_handler
        self.event_source = event_source
        self.host = host
        self.port = port
        self.bearer_token = bearer_token
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def authorized(self, header: str | None) -> bool:
        if self.bearer_token is None:
            return header is None
        expected = f"Bearer {self.bearer_token}"
        return isinstance(header, str) and secrets.compare_digest(header, expected)

    def start(self) -> tuple[str, int]:
        if self._server is not None:
            raise InterfaceError("Web bridge is already running")
        server = ThreadingHTTPServer((self.host, self.port), _WebHandler)
        server.bridge = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = Thread(target=server.serve_forever, name="coquo-web-bridge", daemon=True)
        self._thread.start()
        return server.server_address[0], server.server_address[1]

    def close(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None


__all__ = [
    "IDEJsonRpcBridge",
    "INTERFACE_PROTOCOL_VERSION",
    "InterfaceError",
    "InterfaceEvent",
    "InterfaceResponse",
    "LocalWebBridge",
    "MAX_INTERFACE_BODY_BYTES",
]
