"""Explicit remote-worker protocol with leases and fail-closed recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
from threading import Lock, RLock
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class RemoteWorkerError(RuntimeError):
    """Raised when a remote worker envelope or lease is invalid."""


REMOTE_LEDGER_SCHEMA_VERSION = 1
MAX_REMOTE_LEDGER_EVENTS = 50_000
MAX_REMOTE_LEDGER_BYTES = 16 * 1024 * 1024
REMOTE_WORKER_ID_MAX = 128
MAX_REMOTE_HTTP_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RemoteWorkerIdentity:
    """A worker name plus a proof derived from the shared service secret."""

    worker_id: str
    auth_tag: str

    def __post_init__(self) -> None:
        _validate_worker_id(self.worker_id)
        if (
            not isinstance(self.auth_tag, str)
            or len(self.auth_tag) != 64
            or any(item not in "0123456789abcdef" for item in self.auth_tag)
        ):
            raise ValueError("remote worker authentication tag is invalid")


class RemoteWorkerClient:
    """HTTP client for the authenticated persistent worker service.

    The client deliberately has no retry loop.  A transport failure after a
    request is sent is an outcome-uncertain observation and must be resolved
    by querying the task result or invoking the explicit recovery endpoint.
    """

    def __init__(self, base_url: str, *, secret: bytes, timeout_seconds: float = 30.0) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("remote worker client secret is required")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or (
                parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError("remote worker URL must be HTTPS or local HTTP")
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("remote worker client timeout is invalid")
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.timeout_seconds = timeout_seconds

    def submit(self, envelope: RemoteTaskEnvelope) -> None:
        self._request("POST", "/v1/tasks", _envelope_mapping(envelope))

    def claim(self, worker_id: str, *, lease_seconds: float = 30.0) -> RemoteLease | None:
        payload = {
            "worker_id": worker_id,
            "lease_seconds": lease_seconds,
            "auth_tag": worker_auth_tag(worker_id, self.secret),
        }
        value = self._request("POST", "/v1/claim", payload)
        return None if value is None else _decode_lease(value)

    def heartbeat(self, lease: RemoteLease, *, lease_seconds: float = 30.0) -> RemoteLease:
        value = self._request(
            "POST",
            "/v1/heartbeat",
            {
                "lease": _lease_mapping(lease),
                "lease_seconds": lease_seconds,
                "auth_tag": worker_auth_tag(lease.worker_id, self.secret),
            },
        )
        return _decode_lease(value)

    def complete(self, lease: RemoteLease, result: RemoteResult) -> RemoteResult:
        value = self._request(
            "POST",
            "/v1/complete",
            {
                "lease": _lease_mapping(lease),
                "result": _result_mapping(result),
                "auth_tag": worker_auth_tag(lease.worker_id, self.secret),
            },
        )
        return _decode_result(value)

    def result(self, task_id: str) -> RemoteResult | None:
        value = self._request("GET", f"/v1/tasks/{task_id}")
        return None if value is None else _decode_result(value)

    def recover_expired(self) -> tuple[str, ...]:
        value = self._request("POST", "/v1/recover")
        if not isinstance(value, dict) or set(value) != {"recovered_task_ids"}:
            raise RemoteWorkerError("remote recovery response is invalid")
        result = value["recovered_task_ids"]
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise RemoteWorkerError("remote recovery response is invalid")
        return tuple(result)

    def _request(self, method: str, path: str, payload: object | None = None) -> object:
        body = None
        headers = {"Authorization": "Bearer " + self.secret.decode("utf-8", errors="strict")}
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_REMOTE_HTTP_BODY_BYTES:
                raise RemoteWorkerError("remote worker request exceeds size limit")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_REMOTE_HTTP_BODY_BYTES + 1)
                if len(raw) > MAX_REMOTE_HTTP_BODY_BYTES:
                    raise RemoteWorkerError("remote worker response exceeds size limit")
        except HTTPError as error:
            detail = error.read(MAX_REMOTE_HTTP_BODY_BYTES)
            raise RemoteWorkerError(f"remote worker HTTP {error.code}: {detail[:256]!r}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RemoteWorkerError("remote worker transport failed; outcome is unknown") from error
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteWorkerError("remote worker response is invalid") from None


@dataclass(frozen=True)
class RemoteTaskEnvelope:
    task_id: str
    workspace_fingerprint: str
    objective: str
    permission_mode: str
    payload_sha256: str
    auth_tag: str

    def __post_init__(self) -> None:
        try:
            UUID(self.task_id)
        except (ValueError, AttributeError):
            raise ValueError("remote task ID is invalid") from None
        if not self.workspace_fingerprint or len(self.workspace_fingerprint) > 128:
            raise ValueError("workspace fingerprint is invalid")
        if not self.objective or len(self.objective.encode("utf-8")) > 16 * 1024:
            raise ValueError("remote objective is invalid")
        if self.permission_mode not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError("remote permission mode is invalid")
        if len(self.payload_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.payload_sha256
        ):
            raise ValueError("remote payload digest is invalid")
        if len(self.auth_tag) != 64 or any(c not in "0123456789abcdef" for c in self.auth_tag):
            raise ValueError("remote auth tag is invalid")

    def canonical(self) -> bytes:
        return json.dumps(
            {
                "objective": self.objective,
                "payload_sha256": self.payload_sha256,
                "permission_mode": self.permission_mode,
                "task_id": self.task_id,
                "workspace_fingerprint": self.workspace_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True)
class RemoteLease:
    lease_id: str
    task_id: str
    worker_id: str
    expires_at: float


@dataclass(frozen=True)
class RemoteResult:
    task_id: str
    lease_id: str
    status: str
    result_sha256: str | None = None
    diagnostic: str | None = None
    unknown: bool = False


class RemoteWorkerTransport(Protocol):
    def submit(self, envelope: RemoteTaskEnvelope) -> None: ...
    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
        auth_tag: str | None = None,
    ) -> RemoteLease | None: ...
    def heartbeat(self, lease: RemoteLease, *, lease_seconds: float = 30.0) -> RemoteLease: ...
    def complete(self, lease: RemoteLease, result: RemoteResult) -> RemoteResult: ...
    def recover_expired(self) -> tuple[str, ...]: ...


def worker_auth_tag(worker_id: str, secret: bytes) -> str:
    """Create the stable proof a remote worker sends to the service."""
    _validate_worker_id(worker_id)
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("remote transport secret is required")
    return hmac.new(secret, b"worker\0" + worker_id.encode("utf-8"), hashlib.sha256).hexdigest()


class InMemoryRemoteTransport:
    """Deterministic transport for tests and a local integration harness."""

    def __init__(self, *, secret: bytes) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("remote transport secret is required")
        self.secret = secret
        self._pending: dict[str, RemoteTaskEnvelope] = {}
        self._leases: dict[str, RemoteLease] = {}
        self._results: dict[str, RemoteResult] = {}
        self._guard = Lock()

    def submit(self, envelope: RemoteTaskEnvelope) -> None:
        self._verify(envelope)
        with self._guard:
            if envelope.task_id in self._pending or envelope.task_id in self._leases:
                raise RemoteWorkerError("remote task is already submitted")
            self._pending[envelope.task_id] = envelope

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
        auth_tag: str | None = None,
    ) -> RemoteLease | None:
        del auth_tag
        if not worker_id or not 0 < lease_seconds <= 300:
            raise RemoteWorkerError("remote worker lease request is invalid")
        now = _now()
        with self._guard:
            self.recover_expired()
            if not self._pending:
                return None
            task_id = sorted(self._pending)[0]
            self._pending.pop(task_id)
            lease = RemoteLease(_uuid(), task_id, worker_id, now + lease_seconds)
            self._leases[lease.lease_id] = lease
            return lease

    def heartbeat(self, lease: RemoteLease, *, lease_seconds: float = 30.0) -> RemoteLease:
        self._check_lease(lease)
        if not 0 < lease_seconds <= 300:
            raise RemoteWorkerError("remote lease duration is invalid")
        updated = RemoteLease(
            lease.lease_id, lease.task_id, lease.worker_id, _now() + lease_seconds
        )
        with self._guard:
            self._leases[lease.lease_id] = updated
        return updated

    def complete(self, lease: RemoteLease, result: RemoteResult) -> RemoteResult:
        self._check_lease(lease)
        if result.task_id != lease.task_id or result.lease_id != lease.lease_id:
            raise RemoteWorkerError("remote result does not match lease")
        if result.status not in {"completed", "failed", "cancelled", "unknown"}:
            raise RemoteWorkerError("remote result status is invalid")
        with self._guard:
            prior = self._results.get(result.task_id)
            if prior is not None:
                if prior != result:
                    raise RemoteWorkerError("remote result conflicts with prior observation")
                return prior
            self._results[result.task_id] = result
            self._leases.pop(lease.lease_id, None)
            return result

    def recover_expired(self) -> tuple[str, ...]:
        now = _now()
        recovered: list[str] = []
        for lease_id, lease in tuple(self._leases.items()):
            if lease.expires_at <= now:
                self._leases.pop(lease_id, None)
                recovered.append(lease.task_id)
        return tuple(sorted(recovered))

    def result(self, task_id: str) -> RemoteResult | None:
        return self._results.get(task_id)

    def _verify(self, envelope: RemoteTaskEnvelope) -> None:
        expected = hmac.new(self.secret, envelope.canonical(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, envelope.auth_tag):
            raise RemoteWorkerError("remote envelope authentication failed")

    def _check_lease(self, lease: RemoteLease) -> None:
        if not isinstance(lease, RemoteLease):
            raise RemoteWorkerError("remote lease is invalid")
        with self._guard:
            current = self._leases.get(lease.lease_id)
            if current != lease:
                raise RemoteWorkerError("remote lease is stale or unknown")
            if current.expires_at <= _now():
                self._leases.pop(lease.lease_id, None)
                raise RemoteWorkerError("remote lease expired; outcome is unknown")


class PersistentRemoteTransport:
    """Cross-process remote-worker queue backed by an append-only JSONL ledger.

    This is deliberately a transport/control-plane service, not an execution
    engine.  Workers still run the existing Child/Task runtime on their side;
    the ledger records authenticated admission, lease ownership, heartbeats and
    the Host-observed result.  A process restart replays the ledger and never
    silently requeues a lease whose external outcome is unknown.
    """

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        secret: bytes,
        ledger_path: Path | None = None,
        require_worker_auth: bool = True,
    ) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("remote transport secret is required")
        if workspace is None and ledger_path is None:
            raise ValueError("remote transport workspace or ledger path is required")
        if workspace is not None:
            requested = Path(workspace)
            if requested.is_symlink():
                raise RemoteWorkerError("remote worker workspace must not be a symlink")
            try:
                root = requested.resolve(strict=True)
            except OSError:
                raise RemoteWorkerError("remote worker workspace is inaccessible") from None
            if not root.is_dir():
                raise RemoteWorkerError("remote worker workspace is not a directory")
            resolved_path = root / ".coquo" / "remote-workers" / "v1" / "ledger.jsonl"
        else:
            resolved_path = Path(ledger_path)  # type: ignore[arg-type]
        if resolved_path.is_symlink():
            raise RemoteWorkerError("remote worker ledger must not be a symlink")
        self.path = resolved_path
        self.lock_path = resolved_path.with_suffix(resolved_path.suffix + ".lock")
        self.secret = secret
        if type(require_worker_auth) is not bool:
            raise ValueError("remote worker auth setting is invalid")
        self.require_worker_auth = require_worker_auth
        self._guard = RLock()

    def submit(self, envelope: RemoteTaskEnvelope) -> None:
        self._verify(envelope)
        with self._guard, self._locked():
            state = self._state_unlocked()
            if envelope.task_id in state["pending"] or envelope.task_id in state["leases"]:
                raise RemoteWorkerError("remote task is already submitted")
            if envelope.task_id in state["results"]:
                raise RemoteWorkerError("remote task already has a terminal result")
            self._append_unlocked("submitted", {"envelope": _envelope_mapping(envelope)})

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
        auth_tag: str | None = None,
    ) -> RemoteLease | None:
        _validate_worker_id(worker_id)
        self._verify_worker(worker_id, auth_tag)
        if not 0 < lease_seconds <= 300:
            raise RemoteWorkerError("remote worker lease request is invalid")
        with self._guard, self._locked():
            state = self._state_unlocked()
            self._recover_expired_unlocked(state)
            state = self._state_unlocked()
            if not state["pending"]:
                return None
            task_id = sorted(state["pending"])[0]
            lease = RemoteLease(_uuid(), task_id, worker_id, _now() + lease_seconds)
            self._append_unlocked("claimed", {"lease": _lease_mapping(lease)})
            return lease

    def heartbeat(
        self,
        lease: RemoteLease,
        *,
        lease_seconds: float = 30.0,
        auth_tag: str | None = None,
    ) -> RemoteLease:
        if not 0 < lease_seconds <= 300:
            raise RemoteWorkerError("remote lease duration is invalid")
        self._verify_worker(lease.worker_id, auth_tag)
        with self._guard, self._locked():
            state = self._state_unlocked()
            current = state["leases"].get(lease.lease_id)
            if current != lease:
                raise RemoteWorkerError("remote lease is stale or unknown")
            if lease.expires_at <= _now():
                self._recover_expired_unlocked(state)
                raise RemoteWorkerError("remote lease expired; outcome is unknown")
            updated = RemoteLease(
                lease.lease_id, lease.task_id, lease.worker_id, _now() + lease_seconds
            )
            self._append_unlocked("heartbeat", {"lease": _lease_mapping(updated)})
            return updated

    def complete(
        self,
        lease: RemoteLease,
        result: RemoteResult,
        *,
        auth_tag: str | None = None,
    ) -> RemoteResult:
        self._verify_worker(lease.worker_id, auth_tag)
        if result.task_id != lease.task_id or result.lease_id != lease.lease_id:
            raise RemoteWorkerError("remote result does not match lease")
        if result.status not in {"completed", "failed", "cancelled", "unknown"}:
            raise RemoteWorkerError("remote result status is invalid")
        with self._guard, self._locked():
            state = self._state_unlocked()
            prior = state["results"].get(result.task_id)
            if prior is not None:
                if prior != result:
                    raise RemoteWorkerError("remote result conflicts with prior observation")
                return prior
            current = state["leases"].get(lease.lease_id)
            if current != lease:
                raise RemoteWorkerError("remote lease is stale or unknown")
            if lease.expires_at <= _now():
                self._recover_expired_unlocked(state)
                raise RemoteWorkerError("remote lease expired; outcome is unknown")
            self._append_unlocked("completed", {"result": _result_mapping(result)})
            return result

    def recover_expired(self) -> tuple[str, ...]:
        with self._guard, self._locked():
            state = self._state_unlocked()
            return self._recover_expired_unlocked(state)

    def result(self, task_id: str) -> RemoteResult | None:
        try:
            UUID(task_id)
        except (ValueError, TypeError, AttributeError):
            raise RemoteWorkerError("remote task ID is invalid") from None
        with self._guard, self._locked():
            return self._state_unlocked()["results"].get(task_id)

    def pending(self) -> tuple[RemoteTaskEnvelope, ...]:
        with self._guard, self._locked():
            return tuple(self._state_unlocked()["pending"].values())

    def _recover_expired_unlocked(self, state: dict[str, dict[str, object]]) -> tuple[str, ...]:
        recovered: list[str] = []
        now = _now()
        for lease in tuple(state["leases"].values()):
            if not isinstance(lease, RemoteLease) or lease.expires_at > now:
                continue
            result = RemoteResult(
                lease.task_id,
                lease.lease_id,
                "unknown",
                diagnostic="remote lease expired; worker outcome is unknown",
                unknown=True,
            )
            self._append_unlocked("expired", {"result": _result_mapping(result)})
            recovered.append(lease.task_id)
        return tuple(sorted(recovered))

    def _verify(self, envelope: RemoteTaskEnvelope) -> None:
        expected = hmac.new(self.secret, envelope.canonical(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, envelope.auth_tag):
            raise RemoteWorkerError("remote envelope authentication failed")

    def _verify_worker(self, worker_id: str, auth_tag: str | None) -> None:
        _validate_worker_id(worker_id)
        if not self.require_worker_auth:
            return
        if auth_tag is None or not hmac.compare_digest(
            worker_auth_tag(worker_id, self.secret), auth_tag
        ):
            raise RemoteWorkerError("remote worker authentication failed")

    def _state_unlocked(self) -> dict[str, dict[str, object]]:
        pending: dict[str, RemoteTaskEnvelope] = {}
        leases: dict[str, RemoteLease] = {}
        results: dict[str, RemoteResult] = {}
        for kind, payload in self._events_unlocked():
            if kind == "submitted":
                envelope = _decode_envelope(payload["envelope"])
                pending[envelope.task_id] = envelope
            elif kind == "claimed":
                lease = _decode_lease(payload["lease"])
                pending.pop(lease.task_id, None)
                leases[lease.lease_id] = lease
            elif kind == "heartbeat":
                lease = _decode_lease(payload["lease"])
                if lease.lease_id in leases:
                    leases[lease.lease_id] = lease
            elif kind in {"completed", "expired"}:
                result = _decode_result(payload["result"])
                leases.pop(result.lease_id, None)
                pending.pop(result.task_id, None)
                results[result.task_id] = result
        return {"pending": pending, "leases": leases, "results": results}

    def _events_unlocked(self) -> tuple[tuple[str, dict[str, object]], ...]:
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_REMOTE_LEDGER_BYTES:
                raise ValueError
            result: list[tuple[str, dict[str, object]]] = []
            for line in raw.splitlines():
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict) or set(value) != {
                    "schema_version",
                    "kind",
                    "payload",
                }:
                    raise ValueError
                if value["schema_version"] != REMOTE_LEDGER_SCHEMA_VERSION or not isinstance(
                    value["kind"], str
                ):
                    raise ValueError
                payload = value["payload"]
                if not isinstance(payload, dict):
                    raise ValueError
                result.append((value["kind"], payload))
            if len(result) > MAX_REMOTE_LEDGER_EVENTS:
                raise ValueError
            return tuple(result)
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            raise RemoteWorkerError("remote worker ledger is invalid") from None

    def _append_unlocked(self, kind: str, payload: dict[str, object]) -> None:
        if kind not in {"submitted", "claimed", "heartbeat", "completed", "expired"}:
            raise RemoteWorkerError("remote ledger event is invalid")
        events = self._events_unlocked()
        if len(events) >= MAX_REMOTE_LEDGER_EVENTS:
            raise RemoteWorkerError("remote worker ledger is full")
        encoded = (
            json.dumps(
                {"schema_version": REMOTE_LEDGER_SCHEMA_VERSION, "kind": kind, "payload": payload},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        existing = self.path.stat().st_size if self.path.exists() else 0
        if existing + len(encoded) > MAX_REMOTE_LEDGER_BYTES:
            raise RemoteWorkerError("remote worker ledger exceeds size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

    def _locked(self):
        return _RemoteFileLock(self.lock_path, self.path)


class _RemoteFileLock:
    def __init__(self, lock_path: Path, data_path: Path) -> None:
        self.lock_path = lock_path
        self.data_path = data_path
        self.descriptor: int | None = None

    def __enter__(self) -> "_RemoteFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.name == "nt":
            msvcrt.locking(self.descriptor, msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: object) -> None:
        if self.descriptor is None:
            return
        try:
            if os.name == "nt":
                msvcrt.locking(self.descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def _validate_worker_id(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > REMOTE_WORKER_ID_MAX
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for character in value
        )
    ):
        raise ValueError("remote worker ID is invalid")


def _envelope_mapping(envelope: RemoteTaskEnvelope) -> dict[str, str]:
    return {
        "task_id": envelope.task_id,
        "workspace_fingerprint": envelope.workspace_fingerprint,
        "objective": envelope.objective,
        "permission_mode": envelope.permission_mode,
        "payload_sha256": envelope.payload_sha256,
        "auth_tag": envelope.auth_tag,
    }


def _decode_envelope(value: object) -> RemoteTaskEnvelope:
    if not isinstance(value, dict) or set(value) != {
        "task_id",
        "workspace_fingerprint",
        "objective",
        "permission_mode",
        "payload_sha256",
        "auth_tag",
    }:
        raise RemoteWorkerError("remote envelope ledger record is invalid")
    try:
        return RemoteTaskEnvelope(**value)
    except (TypeError, ValueError):
        raise RemoteWorkerError("remote envelope ledger record is invalid") from None


def _lease_mapping(lease: RemoteLease) -> dict[str, object]:
    return {
        "lease_id": lease.lease_id,
        "task_id": lease.task_id,
        "worker_id": lease.worker_id,
        "expires_at": lease.expires_at,
    }


def _decode_lease(value: object) -> RemoteLease:
    if not isinstance(value, dict) or set(value) != {
        "lease_id",
        "task_id",
        "worker_id",
        "expires_at",
    }:
        raise RemoteWorkerError("remote lease ledger record is invalid")
    try:
        lease = RemoteLease(**value)
        UUID(lease.lease_id)
        UUID(lease.task_id)
        _validate_worker_id(lease.worker_id)
        if not isinstance(lease.expires_at, (int, float)) or isinstance(lease.expires_at, bool):
            raise ValueError
        return lease
    except (TypeError, ValueError):
        raise RemoteWorkerError("remote lease ledger record is invalid") from None


def _result_mapping(result: RemoteResult) -> dict[str, object]:
    return {
        "task_id": result.task_id,
        "lease_id": result.lease_id,
        "status": result.status,
        "result_sha256": result.result_sha256,
        "diagnostic": result.diagnostic,
        "unknown": result.unknown,
    }


def _decode_result(value: object) -> RemoteResult:
    if not isinstance(value, dict) or set(value) != {
        "task_id",
        "lease_id",
        "status",
        "result_sha256",
        "diagnostic",
        "unknown",
    }:
        raise RemoteWorkerError("remote result ledger record is invalid")
    try:
        result = RemoteResult(**value)
        UUID(result.task_id)
        UUID(result.lease_id)
        if result.status not in {"completed", "failed", "cancelled", "unknown"}:
            raise ValueError
        if type(result.unknown) is not bool:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise RemoteWorkerError("remote result ledger record is invalid") from None


def make_envelope(
    *,
    task_id: str,
    workspace_fingerprint: str,
    objective: str,
    permission_mode: str,
    payload: bytes,
    secret: bytes,
) -> RemoteTaskEnvelope:
    provisional = RemoteTaskEnvelope(
        task_id,
        workspace_fingerprint,
        objective,
        permission_mode,
        hashlib.sha256(payload).hexdigest(),
        "0" * 64,
    )
    auth = hmac.new(secret, provisional.canonical(), hashlib.sha256).hexdigest()
    return RemoteTaskEnvelope(
        task_id, workspace_fingerprint, objective, permission_mode, provisional.payload_sha256, auth
    )


def _uuid() -> str:
    from uuid import uuid4

    return str(uuid4())


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


__all__ = [
    "InMemoryRemoteTransport",
    "PersistentRemoteTransport",
    "RemoteWorkerClient",
    "RemoteLease",
    "RemoteResult",
    "RemoteTaskEnvelope",
    "RemoteWorkerIdentity",
    "RemoteWorkerError",
    "RemoteWorkerTransport",
    "make_envelope",
    "worker_auth_tag",
]
