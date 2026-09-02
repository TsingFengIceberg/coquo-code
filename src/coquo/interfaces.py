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
from pathlib import Path
import secrets
from threading import Event, RLock, Thread
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from coquo.core.cancellation import TurnCancellation, TurnCancelled
from coquo.observability import ObservationBatch


INTERFACE_PROTOCOL_VERSION = 1
MAX_INTERFACE_BODY_BYTES = 256 * 1024
MAX_INTERFACE_PROMPT_CHARACTERS = 32_768
MAX_INTERFACE_EVENTS = 256
MAX_INTERFACE_SESSIONS = 16
MAX_INTERFACE_WAIT_SECONDS = 30
MAX_INTERFACE_COMPLETED_TURNS = 128


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
    session_id: str | None = None
    turn_id: str | None = None
    next_sequence: int | None = None
    events_truncated: bool = False
    events_gap: bool = False
    events_oldest_sequence: int | None = None
    events_latest_sequence: int | None = None
    events_dropped_count: int = 0
    stream_epoch: int | None = None

    def as_mapping(self) -> dict[str, object]:
        return {
            "error": self.error,
            "events": [event.as_mapping() for event in self.events],
            "outcome": self.outcome,
            "protocol_version": INTERFACE_PROTOCOL_VERSION,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "text": self.text,
            "turn_id": self.turn_id,
            "next_sequence": self.next_sequence,
            "events_truncated": self.events_truncated,
            "events_gap": self.events_gap,
            "events_oldest_sequence": self.events_oldest_sequence,
            "events_latest_sequence": self.events_latest_sequence,
            "events_dropped_count": self.events_dropped_count,
            "stream_epoch": self.stream_epoch,
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


@dataclass
class _ManagedTurn:
    turn_id: str
    request_id: str
    session_id: str
    cancellation: TurnCancellation
    done: Event
    outcome: str = "running"
    text: str = ""
    error: str | None = None


class ProjectSessionManager:
    """Host-owned registry that gives local transports real ProjectSession access.

    The manager owns lifecycle and routing only. Provider clients, tools,
    permissions, durable transcripts, and ObservationStream remain owned by
    each ProjectSession. Prompt work runs in one background thread per Session
    so Web/IDE clients can observe the same live stream while a request runs.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        session_factory: Callable[..., object] | None = None,
        max_sessions: int = MAX_INTERFACE_SESSIONS,
    ) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise InterfaceError("Session manager workspace is not a directory")
        if type(max_sessions) is not int or not 1 <= max_sessions <= MAX_INTERFACE_SESSIONS:
            raise InterfaceError("Session manager session limit is invalid")
        if session_factory is not None and not callable(session_factory):
            raise InterfaceError("Session manager factory is invalid")
        self.workspace = root
        self.session_factory = session_factory or self._default_factory
        self.max_sessions = max_sessions
        self._sessions: dict[str, object] = {}
        self._active: dict[str, _ManagedTurn] = {}
        self._turns: dict[str, _ManagedTurn] = {}
        self._current: str | None = None
        self._lock = RLock()

    @staticmethod
    def _default_factory(workspace: Path, **kwargs: object) -> object:
        from coquo.session import ProjectSession

        return ProjectSession.open(workspace, **kwargs)

    def register(self, session: object, *, make_current: bool = True) -> str:
        """Register one already-open ProjectSession for transport access."""
        session_id = getattr(session, "session_id", None)
        stream = getattr(session, "observation_stream", None)
        if not isinstance(session_id, str) or not session_id:
            raise InterfaceError("Session does not expose a valid session_id")
        if stream is None or not callable(getattr(stream, "snapshot", None)):
            raise InterfaceError("Session does not expose an ObservationStream")
        session_workspace = getattr(session, "workspace", self.workspace)
        if Path(session_workspace).resolve() != self.workspace:
            raise InterfaceError("Session workspace does not match manager workspace")
        with self._lock:
            if session_id not in self._sessions and len(self._sessions) >= self.max_sessions:
                raise InterfaceError("Session manager session limit reached")
            self._sessions[session_id] = session
            if make_current:
                self._current = session_id
        return session_id

    def create(self, **open_kwargs: object) -> InterfaceResponse:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise InterfaceError("Session manager session limit reached")
        session = self.session_factory(self.workspace, **open_kwargs)
        session_id = self.register(session)
        return InterfaceResponse(
            request_id="session-create",
            outcome="completed",
            session_id=session_id,
        )

    def session_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._sessions))

    def current_id(self) -> str | None:
        with self._lock:
            return self._current

    def get(self, session_id: str | None = None) -> tuple[str, object]:
        with self._lock:
            selected = session_id or self._current
            if selected is None or selected not in self._sessions:
                raise InterfaceError("Session is not registered")
            return selected, self._sessions[selected]

    def close(self, session_id: str | None = None) -> InterfaceResponse:
        selected, session = self.get(session_id)
        with self._lock:
            if selected in self._active:
                raise InterfaceError("Session has an active turn")
            self._sessions.pop(selected, None)
            if self._current == selected:
                self._current = next(iter(sorted(self._sessions)), None)
        close = getattr(session, "close", None)
        if callable(close):
            close()
        return InterfaceResponse("session-close", "completed", session_id=selected)

    def start_prompt(
        self, request_id: str, prompt: str, *, session_id: str | None = None
    ) -> InterfaceResponse:
        request_id = _bounded_request_id(request_id)
        prompt = _bounded_prompt(prompt)
        selected, session = self.get(session_id)
        with self._lock:
            if selected in self._active:
                return InterfaceResponse(
                    request_id,
                    "busy",
                    error="Session already has an active turn",
                    session_id=selected,
                )
            turn = _ManagedTurn(str(uuid4()), request_id, selected, TurnCancellation(), Event())
            self._active[selected] = turn
            self._turns[turn.turn_id] = turn
        worker = Thread(
            target=self._run_prompt,
            args=(session, prompt, turn),
            name=f"coquo-interface-turn-{turn.turn_id}",
            daemon=False,
        )
        worker.start()
        return InterfaceResponse(request_id, "started", session_id=selected, turn_id=turn.turn_id)

    def _run_prompt(self, session: object, prompt: str, turn: _ManagedTurn) -> None:
        try:
            turn.text = str(
                session.prompt(
                    prompt,
                    cancellation=turn.cancellation,
                )
            )
            turn.outcome = "completed"
        except TurnCancelled:
            turn.outcome = "cancelled"
            turn.error = "turn cancelled before durable commit"
        except BaseException as error:
            turn.outcome = "failed"
            turn.error = f"turn failed: {type(error).__name__}"
        finally:
            turn.done.set()
            with self._lock:
                if self._active.get(turn.session_id) is turn:
                    self._active.pop(turn.session_id, None)

    def wait(self, request_id: str, turn_id: str, timeout: float = 30.0) -> InterfaceResponse:
        request_id = _bounded_request_id(request_id)
        if not isinstance(turn_id, str) or not turn_id:
            raise InterfaceError("turn_id is invalid")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 <= timeout <= MAX_INTERFACE_WAIT_SECONDS
        ):
            raise InterfaceError("wait timeout exceeds interface limit")
        with self._lock:
            turn = self._turns.get(turn_id)
        if turn is None:
            raise InterfaceError("unknown turn")
        if not turn.done.wait(float(timeout)):
            return InterfaceResponse(
                request_id, "running", session_id=turn.session_id, turn_id=turn_id
            )
        return InterfaceResponse(
            request_id,
            turn.outcome,
            text=turn.text,
            error=turn.error,
            session_id=turn.session_id,
            turn_id=turn_id,
        )

    def prompt(
        self, request_id: str, prompt: str, *, session_id: str | None = None
    ) -> InterfaceResponse:
        started = self.start_prompt(request_id, prompt, session_id=session_id)
        if started.outcome == "busy":
            return started
        assert started.turn_id is not None
        return self.wait(request_id, started.turn_id, MAX_INTERFACE_WAIT_SECONDS)

    def cancel(
        self, request_id: str, *, session_id: str | None = None, turn_id: str | None = None
    ) -> InterfaceResponse:
        request_id = _bounded_request_id(request_id)
        with self._lock:
            turn = (
                self._turns.get(turn_id)
                if turn_id is not None
                else self._active.get(session_id or self._current or "")
            )
        if turn is None:
            return InterfaceResponse(
                request_id, "completed", error="no active turn", session_id=session_id
            )
        changed = turn.cancellation.request()
        return InterfaceResponse(
            request_id,
            "cancellation-requested" if changed else "already-cancelled",
            session_id=turn.session_id,
            turn_id=turn.turn_id,
        )

    def events(
        self, *, session_id: str | None = None, after: int = -1, limit: int = MAX_INTERFACE_EVENTS
    ) -> tuple[InterfaceEvent, ...]:
        if type(after) is not int or after < -1:
            raise InterfaceError("event cursor is invalid")
        if type(limit) is not int or not 1 <= limit <= MAX_INTERFACE_EVENTS:
            raise InterfaceError("event limit is invalid")
        return self.event_batch(session_id=session_id, after=after, limit=limit).events

    def event_batch(
        self,
        *,
        session_id: str | None = None,
        after: int = -1,
        limit: int = MAX_INTERFACE_EVENTS,
    ) -> ObservationBatch:
        if type(after) is not int or after < -1:
            raise InterfaceError("event cursor is invalid")
        if type(limit) is not int or not 1 <= limit <= MAX_INTERFACE_EVENTS:
            raise InterfaceError("event limit is invalid")
        _, session = self.get(session_id)
        stream = session.observation_stream
        read = getattr(stream, "read", None)
        if callable(read):
            batch = read(after=after, limit=limit)
        else:
            values = tuple(stream.snapshot())
            selected = tuple(event for event in values if event.sequence > after)[:limit]
            batch = ObservationBatch(
                selected,
                selected[-1].sequence + 1 if selected else after + 1,
                values[0].sequence if values else None,
                values[-1].sequence if values else None,
                int(getattr(stream, "stream_epoch", 0)),
                bool(values and after + 1 < values[0].sequence),
            )
        return ObservationBatch(
            tuple(_interface_event(event) for event in batch.events),
            batch.next_sequence,
            batch.oldest_sequence,
            batch.latest_sequence,
            batch.stream_epoch,
            batch.gap,
            batch.dropped_count,
        )


def _interface_event(event: object) -> InterfaceEvent:
    """Project ObservationEvent without exposing prompt, arguments, or content."""
    sequence = getattr(event, "sequence", None)
    if type(sequence) is not int or sequence < 0:
        raise InterfaceError("Session emitted an invalid observation sequence")
    payload = {
        "evidence": str(getattr(getattr(event, "evidence", None), "value", "host-observed")),
        "phase": str(getattr(getattr(event, "phase", None), "value", "observed")),
        "record_type": str(getattr(event, "record_type", "unknown")),
        "session_id": str(getattr(event, "source_id", "")),
        "status": str(getattr(event, "status", "unknown")),
        "summary": str(getattr(event, "summary", ""))[:512],
        "trace_id": str(getattr(event, "trace_id", "")),
    }
    kind = f"observation.{payload['record_type']}"
    return InterfaceEvent(kind, payload, sequence)


class IDEJsonRpcBridge:
    """Handle one JSON-RPC-like request per input line for IDE integrations."""

    def __init__(
        self,
        prompt_handler: Callable[[str, str], InterfaceResponse] | None = None,
        *,
        event_source: Callable[[], Iterable[InterfaceEvent]] | None = None,
        session_manager: ProjectSessionManager | None = None,
    ) -> None:
        if prompt_handler is not None and not callable(prompt_handler):
            raise ValueError("prompt_handler is required")
        if prompt_handler is None and session_manager is None:
            raise ValueError("prompt_handler or session_manager is required")
        if session_manager is not None and not isinstance(session_manager, ProjectSessionManager):
            raise ValueError("session_manager is invalid")
        self.prompt_handler = prompt_handler
        self.event_source = event_source
        self.session_manager = session_manager

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
                if not isinstance(params, dict) or set(params) - {"prompt", "session_id", "wait"}:
                    raise InterfaceError("prompt params are invalid")
                if "prompt" not in params:
                    raise InterfaceError("prompt params are invalid")
                prompt = _bounded_prompt(params["prompt"])
                session_id = params.get("session_id")
                if session_id is not None and not isinstance(session_id, str):
                    raise InterfaceError("session_id is invalid")
                wait = params.get("wait", True)
                if type(wait) is not bool:
                    raise InterfaceError("wait flag is invalid")
                if self.session_manager is not None:
                    response = (
                        self.session_manager.prompt(request_id, prompt, session_id=session_id)
                        if wait
                        else self.session_manager.start_prompt(
                            request_id, prompt, session_id=session_id
                        )
                    )
                else:
                    if self.prompt_handler is None or set(params) != {"prompt"}:
                        raise InterfaceError("prompt params are invalid")
                    response = self.prompt_handler(request_id, prompt)
            elif method == "events":
                if self.session_manager is not None:
                    if not isinstance(params, dict) or set(params) - {
                        "session_id",
                        "after",
                        "limit",
                    }:
                        raise InterfaceError("events params are invalid")
                    session_id = params.get("session_id")
                    after = params.get("after", -1)
                    limit = params.get("limit", MAX_INTERFACE_EVENTS)
                    batch = self.session_manager.event_batch(
                        session_id=session_id, after=after, limit=limit
                    )
                    response = InterfaceResponse(
                        request_id,
                        "completed",
                        events=batch.events,
                        session_id=session_id or self.session_manager.current_id(),
                        next_sequence=batch.next_sequence,
                        events_gap=batch.gap,
                        events_oldest_sequence=batch.oldest_sequence,
                        events_latest_sequence=batch.latest_sequence,
                        events_dropped_count=batch.dropped_count,
                        stream_epoch=batch.stream_epoch,
                    )
                elif params not in ({}, None):
                    raise InterfaceError("events params are invalid")
                else:
                    events = tuple(self.event_source() if self.event_source is not None else ())[
                        :MAX_INTERFACE_EVENTS
                    ]
                    response = InterfaceResponse(request_id, "completed", events=events)
            elif method == "session_list" and self.session_manager is not None:
                if params not in ({}, None):
                    raise InterfaceError("session_list params are invalid")
                response = InterfaceResponse(
                    request_id,
                    "completed",
                    session_id=self.session_manager.current_id(),
                    text=json.dumps(
                        {"sessions": self.session_manager.session_ids()},
                        separators=(",", ":"),
                    ),
                )
            elif method == "session_create" and self.session_manager is not None:
                if params not in ({}, None):
                    raise InterfaceError("session_create params are invalid")
                response = self.session_manager.create()
                response = InterfaceResponse(
                    request_id,
                    response.outcome,
                    session_id=response.session_id,
                )
            elif method == "session_cancel" and self.session_manager is not None:
                if not isinstance(params, dict) or set(params) - {"session_id", "turn_id"}:
                    raise InterfaceError("session_cancel params are invalid")
                response = self.session_manager.cancel(
                    request_id,
                    session_id=params.get("session_id"),
                    turn_id=params.get("turn_id"),
                )
            elif method == "session_wait" and self.session_manager is not None:
                if not isinstance(params, dict) or set(params) - {"turn_id", "timeout"}:
                    raise InterfaceError("session_wait params are invalid")
                response = self.session_manager.wait(
                    request_id,
                    params.get("turn_id"),
                    params.get("timeout", MAX_INTERFACE_WAIT_SECONDS),
                )
            elif method == "session_close" and self.session_manager is not None:
                if not isinstance(params, dict) or set(params) - {"session_id"}:
                    raise InterfaceError("session_close params are invalid")
                response = self.session_manager.close(params.get("session_id"))
                response = InterfaceResponse(
                    request_id, response.outcome, session_id=response.session_id
                )
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

    def _read_json(self) -> object | None:
        length = self.headers.get("Content-Length")
        try:
            size = int(length or "-1")
        except ValueError:
            size = -1
        if size < 0 or size > MAX_INTERFACE_BODY_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body is oversized"})
            return None
        try:
            return json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})
            return None

    def do_POST(self) -> None:  # noqa: N802
        bridge: "LocalWebBridge" = self.server.bridge  # type: ignore[attr-defined]
        route = urlsplit(self.path).path
        if route == "/v1/sessions" and bridge.session_manager is not None:
            if not bridge.authorized(self.headers.get("Authorization")):
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
                return
            payload = self._read_json()
            if payload is None:
                return
            if payload not in ({}, None):
                self._send(HTTPStatus.BAD_REQUEST, {"error": "session create body is invalid"})
                return
            try:
                created = bridge.session_manager.create()
                self._send(
                    HTTPStatus.CREATED,
                    InterfaceResponse(
                        str(uuid4()), "completed", session_id=created.session_id
                    ).as_mapping(),
                )
            except InterfaceError as error:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})
            return
        if (
            route.startswith("/v1/sessions/")
            and route.endswith("/cancel")
            and bridge.session_manager is not None
        ):
            session_id = route[len("/v1/sessions/") : -len("/cancel")].strip("/")
            if not bridge.authorized(self.headers.get("Authorization")):
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
                return
            try:
                response = bridge.session_manager.cancel(str(uuid4()), session_id=session_id)
                self._send(HTTPStatus.OK, response.as_mapping())
            except InterfaceError as error:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})
            return
        if route != "/v1/prompt":
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
            if (
                not isinstance(payload, dict)
                or set(payload)
                - {
                    "id",
                    "prompt",
                    "session_id",
                    "wait",
                }
                or "id" not in payload
                or "prompt" not in payload
            ):
                raise InterfaceError("request body fields are invalid")
            request_id = _bounded_request_id(payload["id"])
            prompt = _bounded_prompt(payload["prompt"])
            session_id = payload.get("session_id")
            if session_id is not None and not isinstance(session_id, str):
                raise InterfaceError("session_id is invalid")
            wait = payload.get("wait", True)
            if type(wait) is not bool:
                raise InterfaceError("wait flag is invalid")
            if bridge.session_manager is not None:
                response = (
                    bridge.session_manager.prompt(request_id, prompt, session_id=session_id)
                    if wait
                    else bridge.session_manager.start_prompt(
                        request_id, prompt, session_id=session_id
                    )
                )
            else:
                if bridge.prompt_handler is None or set(payload) != {"id", "prompt"}:
                    raise InterfaceError("request body fields are invalid")
                response = bridge.prompt_handler(request_id, prompt)
            if not isinstance(response, InterfaceResponse):
                raise InterfaceError("prompt handler returned an invalid response")
            self._send(HTTPStatus.OK, response.as_mapping())
        except (UnicodeDecodeError, json.JSONDecodeError, InterfaceError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})

    def do_GET(self) -> None:  # noqa: N802
        bridge: "LocalWebBridge" = self.server.bridge  # type: ignore[attr-defined]
        parsed = urlsplit(self.path)
        if parsed.path == "/v1/sessions" and bridge.session_manager is not None:
            if not bridge.authorized(self.headers.get("Authorization")):
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
                return
            self._send(
                HTTPStatus.OK,
                {
                    "sessions": bridge.session_manager.session_ids(),
                    "current": bridge.session_manager.current_id(),
                },
            )
            return
        if (
            parsed.path.startswith("/v1/sessions/")
            and parsed.path.endswith("/events")
            and bridge.session_manager is not None
        ):
            if not bridge.authorized(self.headers.get("Authorization")):
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
                return
            session_id = parsed.path[len("/v1/sessions/") : -len("/events")].strip("/")
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                after = int(query.get("after", ["-1"])[0])
                limit = int(query.get("limit", [str(MAX_INTERFACE_EVENTS)])[0])
                batch = bridge.session_manager.event_batch(
                    session_id=session_id,
                    after=after,
                    limit=limit,
                )
                self._send(
                    HTTPStatus.OK,
                    {
                        "events": [event.as_mapping() for event in batch.events],
                        "next_sequence": batch.next_sequence,
                        "events_gap": batch.gap,
                        "events_oldest_sequence": batch.oldest_sequence,
                        "events_latest_sequence": batch.latest_sequence,
                        "events_dropped_count": batch.dropped_count,
                        "stream_epoch": batch.stream_epoch,
                    },
                )
            except (ValueError, InterfaceError) as error:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})
            return
        if parsed.path != "/v1/events":
            self._send(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
            return
        if not bridge.authorized(self.headers.get("Authorization")):
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "authorization required"})
            return
        if bridge.session_manager is not None:
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                batch = bridge.session_manager.event_batch(
                    session_id=query.get("session_id", [None])[0],
                    after=int(query.get("after", ["-1"])[0]),
                    limit=int(query.get("limit", [str(MAX_INTERFACE_EVENTS)])[0]),
                )
                self._send(
                    HTTPStatus.OK,
                    {
                        "events": [event.as_mapping() for event in batch.events],
                        "next_sequence": batch.next_sequence,
                        "events_gap": batch.gap,
                        "events_oldest_sequence": batch.oldest_sequence,
                        "events_latest_sequence": batch.latest_sequence,
                        "events_dropped_count": batch.dropped_count,
                        "stream_epoch": batch.stream_epoch,
                    },
                )
                return
            except (ValueError, InterfaceError) as error:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)[:512]})
                return
        else:
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
        prompt_handler: Callable[[str, str], InterfaceResponse] | None = None,
        *,
        event_source: Callable[[], Iterable[InterfaceEvent]] | None = None,
        session_manager: ProjectSessionManager | None = None,
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
        if prompt_handler is not None and not callable(prompt_handler):
            raise ValueError("prompt_handler is required")
        if prompt_handler is None and session_manager is None:
            raise ValueError("prompt_handler or session_manager is required")
        if session_manager is not None and not isinstance(session_manager, ProjectSessionManager):
            raise ValueError("session_manager is invalid")
        self.prompt_handler = prompt_handler
        self.event_source = event_source
        self.session_manager = session_manager
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
    "MAX_INTERFACE_EVENTS",
    "MAX_INTERFACE_SESSIONS",
    "MAX_INTERFACE_WAIT_SECONDS",
    "ProjectSessionManager",
]
