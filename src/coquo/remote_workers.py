"""Explicit remote-worker protocol with leases and fail-closed recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from threading import Lock
from typing import Protocol
from uuid import UUID


class RemoteWorkerError(RuntimeError):
    """Raised when a remote worker envelope or lease is invalid."""


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
    def claim(self, worker_id: str, *, lease_seconds: float = 30.0) -> RemoteLease | None: ...
    def heartbeat(self, lease: RemoteLease, *, lease_seconds: float = 30.0) -> RemoteLease: ...
    def complete(self, lease: RemoteLease, result: RemoteResult) -> RemoteResult: ...
    def recover_expired(self) -> tuple[str, ...]: ...


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

    def claim(self, worker_id: str, *, lease_seconds: float = 30.0) -> RemoteLease | None:
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
    "RemoteLease",
    "RemoteResult",
    "RemoteTaskEnvelope",
    "RemoteWorkerError",
    "RemoteWorkerTransport",
    "make_envelope",
]
