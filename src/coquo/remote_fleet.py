"""Durable, tenant-aware scheduling for a bounded remote Worker fleet.

The fleet coordinator owns admission and lease state.  It never executes a
task and never retries an expired lease: an external effect whose outcome is
not observed remains ``unknown``.  Workers are isolated by exact workspace
fingerprint, tenant allow-list, capability labels, and a permission ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import fcntl
import hmac
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

from coquo.remote_workers import (
    RemoteLease,
    RemoteResult,
    RemoteTaskEnvelope,
    RemoteWorkerError,
    worker_auth_tag,
)
from coquo.tenant import TenantError, TenantRegistry, workspace_fingerprint

MAX_FLEET_LEDGER_BYTES = 32 * 1024 * 1024
MAX_FLEET_LEDGER_EVENTS = 50_000
MAX_FLEET_WORKERS = 256
MAX_FLEET_TASKS = 20_000
MAX_FLEET_CAPABILITIES = 32
MAX_FLEET_TENANTS = 256
MAX_FLEET_TENANT_ID = 128
MAX_FLEET_HTTP_BODY_BYTES = 1024 * 1024


class FleetWorkerStatus(StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    REVOKED = "revoked"


class FleetTaskStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


_PERMISSION_RANK = {"read-only": 0, "workspace-write": 1, "danger-full-access": 2}


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _tenant(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_FLEET_TENANT_ID
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ .:"
            for character in value
        )
        or value != value.strip()
    ):
        raise RemoteWorkerError("fleet tenant ID is invalid")
    return value


def _capabilities(values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(sorted(set(values)))
    if len(result) > MAX_FLEET_CAPABILITIES or any(
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
            for character in value
        )
        for value in result
    ):
        raise RemoteWorkerError("fleet worker capabilities are invalid")
    return result


@dataclass(frozen=True)
class FleetWorker:
    worker_id: str
    endpoint: str
    workspace_fingerprint: str
    capabilities: tuple[str, ...] = ()
    tenants: tuple[str, ...] = ()
    max_permission_mode: str = "read-only"
    max_concurrency: int = 1
    status: FleetWorkerStatus = FleetWorkerStatus.ACTIVE
    last_seen: float = 0.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.worker_id, str)
            or not self.worker_id
            or len(self.worker_id) > 128
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
                for character in self.worker_id
            )
        ):
            raise RemoteWorkerError("fleet worker ID is invalid")
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith("https://"):
            raise RemoteWorkerError("fleet worker endpoint must use HTTPS")
        if not self.workspace_fingerprint or len(self.workspace_fingerprint) > 128:
            raise RemoteWorkerError("fleet workspace fingerprint is invalid")
        _capabilities(self.capabilities)
        if len(self.tenants) > MAX_FLEET_TENANTS:
            raise RemoteWorkerError("fleet worker tenant scope is oversized")
        for tenant in self.tenants:
            _tenant(tenant)
        if self.max_permission_mode not in _PERMISSION_RANK:
            raise RemoteWorkerError("fleet worker permission ceiling is invalid")
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 32:
            raise RemoteWorkerError("fleet worker concurrency is invalid")
        if type(self.status) is not FleetWorkerStatus:
            raise RemoteWorkerError("fleet worker status is invalid")
        if (
            isinstance(self.last_seen, bool)
            or not isinstance(self.last_seen, (int, float))
            or self.last_seen < 0
        ):
            raise RemoteWorkerError("fleet worker heartbeat timestamp is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "endpoint": self.endpoint,
            "workspace_fingerprint": self.workspace_fingerprint,
            "capabilities": list(self.capabilities),
            "tenants": list(self.tenants),
            "max_permission_mode": self.max_permission_mode,
            "max_concurrency": self.max_concurrency,
            "status": self.status.value,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class FleetTask:
    task_id: str
    tenant_id: str
    workspace_fingerprint: str
    objective: str
    permission_mode: str
    required_capabilities: tuple[str, ...]
    status: FleetTaskStatus
    assigned_worker_id: str | None = None
    lease_id: str | None = None
    lease_expires_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.task_id)
        except (ValueError, TypeError, AttributeError):
            raise RemoteWorkerError("fleet task ID is invalid") from None
        _tenant(self.tenant_id)
        if not self.workspace_fingerprint or len(self.workspace_fingerprint) > 128:
            raise RemoteWorkerError("fleet task workspace fingerprint is invalid")
        if (
            not isinstance(self.objective, str)
            or not self.objective
            or len(self.objective.encode()) > 16 * 1024
        ):
            raise RemoteWorkerError("fleet task objective is invalid")
        if self.permission_mode not in _PERMISSION_RANK:
            raise RemoteWorkerError("fleet task permission mode is invalid")
        _capabilities(self.required_capabilities)
        if type(self.status) is not FleetTaskStatus:
            raise RemoteWorkerError("fleet task status is invalid")
        if self.assigned_worker_id is not None and not isinstance(self.assigned_worker_id, str):
            raise RemoteWorkerError("fleet task worker assignment is invalid")
        if self.lease_id is not None:
            try:
                UUID(self.lease_id)
            except (ValueError, TypeError, AttributeError):
                raise RemoteWorkerError("fleet task lease ID is invalid") from None
        if self.lease_expires_at is not None and (
            isinstance(self.lease_expires_at, bool)
            or not isinstance(self.lease_expires_at, (int, float))
            or self.lease_expires_at < 0
        ):
            raise RemoteWorkerError("fleet task lease expiry is invalid")
        if self.status is FleetTaskStatus.ASSIGNED and (
            self.assigned_worker_id is None
            or self.lease_id is None
            or self.lease_expires_at is None
        ):
            raise RemoteWorkerError("assigned fleet task lease is incomplete")

    def as_mapping(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_fingerprint": self.workspace_fingerprint,
            "objective": self.objective,
            "permission_mode": self.permission_mode,
            "required_capabilities": list(self.required_capabilities),
            "status": self.status.value,
            "assigned_worker_id": self.assigned_worker_id,
            "lease_id": self.lease_id,
            "lease_expires_at": self.lease_expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True)
class FleetAssignment:
    task: FleetTask
    lease: RemoteLease


class RemoteFleetClient:
    """Bounded HTTP client for the fleet coordinator control plane.

    Requests carry the service bearer guard and, where applicable, the
    worker-specific HMAC proof.  Redirects are rejected because a coordinator
    endpoint is an authority boundary, and transport failures are surfaced as
    outcome-uncertain rather than retried.
    """

    def __init__(
        self,
        base_url: str,
        *,
        secret: bytes,
        timeout_seconds: float = 30.0,
        tenant_token: str | None = None,
    ) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("remote fleet client secret is required")
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
            raise ValueError("remote fleet URL must be HTTPS or local HTTP")
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("remote fleet client timeout is invalid")
        try:
            bearer = secret.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("remote fleet client secret must be UTF-8") from error
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self._bearer = bearer
        self.timeout_seconds = timeout_seconds
        if tenant_token is not None and (
            not isinstance(tenant_token, str)
            or not tenant_token
            or len(tenant_token.encode("utf-8")) > 4096
        ):
            raise ValueError("remote fleet tenant token is invalid")
        self.tenant_token = tenant_token
        self._opener = build_opener(_NoRedirectHandler())

    def register(self, worker: FleetWorker) -> FleetWorker:
        value = self._request(
            "POST",
            "/v1/workers/register",
            {
                "worker": worker.as_mapping(),
                "auth_tag": worker_auth_tag(worker.worker_id, self.secret),
            },
        )
        return _worker_from_mapping(value)

    def heartbeat(self, worker_id: str) -> FleetWorker:
        value = self._request(
            "POST",
            "/v1/workers/heartbeat",
            {"worker_id": worker_id, "auth_tag": worker_auth_tag(worker_id, self.secret)},
        )
        return _worker_from_mapping(value)

    def submit(
        self,
        envelope: RemoteTaskEnvelope,
        *,
        tenant_id: str,
        required_capabilities: Iterable[str] = (),
    ) -> FleetTask:
        value = self._request(
            "POST",
            "/v1/tasks",
            {
                **_envelope_mapping(envelope),
                "tenant_id": tenant_id,
                "required_capabilities": list(required_capabilities),
            },
        )
        return _task_from_mapping(value)

    def dispatch(
        self,
        worker_id: str,
        *,
        capabilities: Iterable[str] = (),
        lease_seconds: float = 30.0,
    ) -> FleetAssignment | None:
        value = self._request(
            "POST",
            "/v1/dispatch",
            {
                "worker_id": worker_id,
                "auth_tag": worker_auth_tag(worker_id, self.secret),
                "capabilities": list(capabilities),
                "lease_seconds": lease_seconds,
            },
        )
        return None if value is None else _assignment_from_mapping(value)

    def heartbeat_lease(
        self, lease: RemoteLease, *, lease_seconds: float = 30.0
    ) -> FleetAssignment:
        value = self._request(
            "POST",
            "/v1/lease/heartbeat",
            {
                "lease": _lease_mapping(lease),
                "auth_tag": worker_auth_tag(lease.worker_id, self.secret),
                "lease_seconds": lease_seconds,
            },
        )
        return _assignment_from_mapping(value)

    def complete(self, lease: RemoteLease, result: RemoteResult) -> FleetTask:
        value = self._request(
            "POST",
            "/v1/complete",
            {
                "lease": _lease_mapping(lease),
                "result": _result_mapping(result),
                "auth_tag": worker_auth_tag(lease.worker_id, self.secret),
            },
        )
        return _task_from_mapping(value)

    def recover_expired(self) -> tuple[str, ...]:
        value = self._request("POST", "/v1/recover", {})
        if not isinstance(value, dict) or set(value) != {"recovered_task_ids"}:
            raise RemoteWorkerError("remote fleet recovery response is invalid")
        ids = value["recovered_task_ids"]
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise RemoteWorkerError("remote fleet recovery response is invalid")
        return tuple(ids)

    def workers(self) -> tuple[FleetWorker, ...]:
        value = self._request("GET", "/v1/workers")
        if not isinstance(value, dict) or not isinstance(value.get("workers"), list):
            raise RemoteWorkerError("remote fleet worker response is invalid")
        try:
            return tuple(_worker_from_mapping(item) for item in value["workers"])
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote fleet worker response is invalid") from None

    def tasks(self, *, tenant_id: str | None = None) -> tuple[FleetTask, ...]:
        path = (
            "/v1/tasks" if tenant_id is None else "/v1/tasks?" + urlencode({"tenant_id": tenant_id})
        )
        value = self._request("GET", path)
        if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
            raise RemoteWorkerError("remote fleet task response is invalid")
        try:
            return tuple(_task_from_mapping(item) for item in value["tasks"])
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote fleet task response is invalid") from None

    def _request(self, method: str, path: str, payload: object | None = None) -> object:
        body = None
        headers = {"Authorization": "Bearer " + self._bearer}
        if self.tenant_token is not None:
            headers["X-Coquo-Tenant-Token"] = self.tenant_token
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_FLEET_HTTP_BODY_BYTES:
                raise RemoteWorkerError("remote fleet request exceeds size limit")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_FLEET_HTTP_BODY_BYTES + 1)
                if len(raw) > MAX_FLEET_HTTP_BODY_BYTES:
                    raise RemoteWorkerError("remote fleet response exceeds size limit")
        except HTTPError as error:
            raw = error.read(MAX_FLEET_HTTP_BODY_BYTES)
            try:
                detail = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = None
            if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                raise RemoteWorkerError(
                    f"remote fleet HTTP {error.code}: {detail['message']}"
                ) from error
            raise RemoteWorkerError(f"remote fleet HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RemoteWorkerError("remote fleet transport failed; outcome is unknown") from error
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteWorkerError("remote fleet response is invalid") from None


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_301(self, req, fp, code, msg, headers):  # noqa: ANN001, N802
        raise HTTPError(req.full_url, code, msg, headers, fp)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


class RemoteWorkerFleet:
    """Persistent scheduler for a small, explicitly registered Worker fleet."""

    def __init__(
        self,
        workspace: Path,
        *,
        secret: bytes,
        tenant_registry: TenantRegistry | None = None,
    ) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise RemoteWorkerError("fleet secret is required")
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise RemoteWorkerError("fleet workspace is not a directory")
        self.path = root / ".coquo" / "remote-fleet" / "v1" / "events.jsonl"
        self.lock_path = self.path.with_suffix(".lock")
        self.secret = secret
        self.tenant_registry = tenant_registry
        if tenant_registry is not None and tenant_registry.workspace != root:
            raise RemoteWorkerError(
                "fleet tenant registry workspace does not match fleet workspace"
            )
        self.workspace_fingerprint = workspace_fingerprint(root)
        self._guard = RLock()

    def register(self, worker: FleetWorker, *, auth_tag: str) -> FleetWorker:
        self._verify_worker(worker.worker_id, auth_tag)
        with self._guard, self._locked():
            workers, _ = self._state_unlocked()
            if (
                worker.worker_id in workers
                and workers[worker.worker_id].status is FleetWorkerStatus.REVOKED
            ):
                raise RemoteWorkerError("fleet worker is revoked")
            if worker.worker_id not in workers and len(workers) >= MAX_FLEET_WORKERS:
                raise RemoteWorkerError("fleet worker registry is full")
            updated = replace(worker, last_seen=_now(), status=FleetWorkerStatus.ACTIVE)
            self._append_unlocked("worker_registered", {"worker": updated.as_mapping()})
            return updated

    def heartbeat(self, worker_id: str, *, auth_tag: str) -> FleetWorker:
        self._verify_worker(worker_id, auth_tag)
        with self._guard, self._locked():
            workers, _ = self._state_unlocked()
            worker = workers.get(worker_id)
            if worker is None or worker.status is FleetWorkerStatus.REVOKED:
                raise RemoteWorkerError("fleet worker is unknown or revoked")
            updated = replace(worker, last_seen=_now())
            self._append_unlocked("worker_heartbeat", {"worker": updated.as_mapping()})
            return updated

    def revoke(self, worker_id: str) -> None:
        if not isinstance(worker_id, str) or not worker_id:
            raise RemoteWorkerError("fleet worker ID is invalid")
        with self._guard, self._locked():
            workers, tasks = self._state_unlocked()
            worker = workers.get(worker_id)
            if worker is None:
                raise RemoteWorkerError("fleet worker is unknown")
            self._append_unlocked("worker_revoked", {"worker_id": worker_id, "at": _now()})
            for task in tasks.values():
                if task.assigned_worker_id == worker_id and task.lease_id is not None:
                    self._append_expired_unlocked(
                        task,
                        _lease_for_task(task),
                        reason="fleet worker revoked; worker outcome is unknown",
                    )

    def submit(
        self,
        envelope: RemoteTaskEnvelope,
        *,
        tenant_id: str,
        required_capabilities: Iterable[str] = (),
    ) -> FleetTask:
        _tenant(tenant_id)
        capabilities = _capabilities(required_capabilities)
        if not isinstance(envelope, RemoteTaskEnvelope):
            raise RemoteWorkerError("fleet task envelope is invalid")
        with self._guard, self._locked():
            workers, tasks = self._state_unlocked()
            if len(tasks) >= MAX_FLEET_TASKS:
                raise RemoteWorkerError("fleet task ledger is full")
            if envelope.task_id in tasks:
                raise RemoteWorkerError("fleet task is already submitted")
            if self.tenant_registry is not None:
                try:
                    policy = self.tenant_registry.require_workspace(
                        tenant_id, envelope.workspace_fingerprint
                    )
                except TenantError as error:
                    raise RemoteWorkerError(str(error)) from error
                active = sum(
                    item.tenant_id == tenant_id
                    and item.status in {FleetTaskStatus.QUEUED, FleetTaskStatus.ASSIGNED}
                    for item in tasks.values()
                )
                if active >= policy.max_active_tasks:
                    raise RemoteWorkerError("tenant active task quota is exhausted")
            now = _now()
            task = FleetTask(
                task_id=envelope.task_id,
                tenant_id=tenant_id,
                workspace_fingerprint=envelope.workspace_fingerprint,
                objective=envelope.objective,
                permission_mode=envelope.permission_mode,
                required_capabilities=capabilities,
                status=FleetTaskStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._append_unlocked("task_submitted", {"task": task.as_mapping()})
            return task

    def dispatch(
        self,
        worker_id: str,
        *,
        auth_tag: str,
        capabilities: Iterable[str] = (),
        lease_seconds: float = 30.0,
    ) -> FleetAssignment | None:
        self._verify_worker(worker_id, auth_tag)
        worker_capabilities = set(_capabilities(capabilities))
        if not 0 < lease_seconds <= 300:
            raise RemoteWorkerError("fleet lease duration is invalid")
        with self._guard, self._locked():
            workers, tasks = self._state_unlocked()
            worker = workers.get(worker_id)
            if worker is None or worker.status is not FleetWorkerStatus.ACTIVE:
                raise RemoteWorkerError("fleet worker is not active")
            if worker_capabilities and not worker_capabilities <= set(worker.capabilities):
                raise RemoteWorkerError("fleet worker capability proof exceeds registration")
            active = sum(
                item.assigned_worker_id == worker_id and item.status is FleetTaskStatus.ASSIGNED
                for item in tasks.values()
            )
            if active >= worker.max_concurrency:
                return None
            candidates = [
                item
                for item in tasks.values()
                if item.status is FleetTaskStatus.QUEUED
                and item.workspace_fingerprint == worker.workspace_fingerprint
                and _PERMISSION_RANK[item.permission_mode]
                <= _PERMISSION_RANK[worker.max_permission_mode]
                and set(item.required_capabilities) <= set(worker.capabilities)
                and (not worker.tenants or item.tenant_id in worker.tenants)
            ]
            if not candidates:
                return None
            task = min(candidates, key=lambda item: (item.created_at, item.task_id))
            lease = RemoteLease(str(uuid4()), task.task_id, worker_id, _now() + lease_seconds)
            assigned = replace(
                task,
                status=FleetTaskStatus.ASSIGNED,
                assigned_worker_id=worker_id,
                lease_id=lease.lease_id,
                lease_expires_at=lease.expires_at,
                updated_at=_now(),
            )
            self._append_unlocked(
                "task_assigned", {"task": assigned.as_mapping(), "lease": _lease_mapping(lease)}
            )
            return FleetAssignment(assigned, lease)

    def heartbeat_lease(
        self, lease: RemoteLease, *, auth_tag: str, lease_seconds: float = 30.0
    ) -> FleetAssignment:
        self._verify_worker(lease.worker_id, auth_tag)
        if not 0 < lease_seconds <= 300:
            raise RemoteWorkerError("fleet lease duration is invalid")
        with self._guard, self._locked():
            _, tasks = self._state_unlocked()
            task = tasks.get(lease.task_id)
            if (
                task is None
                or task.status is not FleetTaskStatus.ASSIGNED
                or task.lease_id != lease.lease_id
            ):
                raise RemoteWorkerError("fleet lease is stale or unknown")
            if lease.expires_at <= _now():
                self._append_expired_unlocked(task, lease)
                raise RemoteWorkerError("fleet lease expired; outcome is unknown")
            updated_lease = RemoteLease(
                lease.lease_id, lease.task_id, lease.worker_id, _now() + lease_seconds
            )
            updated_task = replace(
                task, lease_expires_at=updated_lease.expires_at, updated_at=_now()
            )
            self._append_unlocked(
                "lease_heartbeat",
                {"task": updated_task.as_mapping(), "lease": _lease_mapping(updated_lease)},
            )
            return FleetAssignment(task, updated_lease)

    def complete(self, lease: RemoteLease, result: RemoteResult, *, auth_tag: str) -> FleetTask:
        self._verify_worker(lease.worker_id, auth_tag)
        if result.task_id != lease.task_id or result.lease_id != lease.lease_id:
            raise RemoteWorkerError("fleet result does not match lease")
        with self._guard, self._locked():
            _, tasks = self._state_unlocked()
            task = tasks.get(lease.task_id)
            if (
                task is None
                or task.status is not FleetTaskStatus.ASSIGNED
                or task.lease_id != lease.lease_id
            ):
                raise RemoteWorkerError("fleet lease is stale or unknown")
            if lease.expires_at <= _now():
                self._append_expired_unlocked(task, lease)
                raise RemoteWorkerError("fleet lease expired; outcome is unknown")
            status = {
                "completed": FleetTaskStatus.COMPLETED,
                "failed": FleetTaskStatus.FAILED,
                "cancelled": FleetTaskStatus.CANCELLED,
                "unknown": FleetTaskStatus.UNKNOWN,
            }.get(result.status)
            if status is None:
                raise RemoteWorkerError("fleet result status is invalid")
            finished = replace(
                task,
                status=status,
                assigned_worker_id=None,
                lease_id=None,
                lease_expires_at=None,
                updated_at=_now(),
                diagnostic=result.diagnostic,
            )
            self._append_unlocked(
                "task_completed", {"task": finished.as_mapping(), "result": _result_mapping(result)}
            )
            return finished

    def recover_expired(self) -> tuple[str, ...]:
        with self._guard, self._locked():
            _, tasks = self._state_unlocked()
            recovered: list[str] = []
            now = _now()
            for task in tasks.values():
                if task.status is not FleetTaskStatus.ASSIGNED or task.lease_id is None:
                    continue
                lease = _lease_for_task(task)
                if lease is not None and lease.expires_at <= now:
                    self._append_expired_unlocked(task, lease)
                    recovered.append(task.task_id)
            return tuple(sorted(recovered))

    def workers(self) -> tuple[FleetWorker, ...]:
        with self._guard, self._locked():
            workers, _ = self._state_unlocked()
            return tuple(sorted(workers.values(), key=lambda item: item.worker_id))

    def tasks(self, *, tenant_id: str | None = None) -> tuple[FleetTask, ...]:
        if tenant_id is not None:
            _tenant(tenant_id)
        with self._guard, self._locked():
            _, tasks = self._state_unlocked()
            result = tuple(
                item for item in tasks.values() if tenant_id is None or item.tenant_id == tenant_id
            )
            return tuple(sorted(result, key=lambda item: (item.created_at, item.task_id)))

    def _verify_worker(self, worker_id: str, auth_tag: str) -> None:
        expected = worker_auth_tag(worker_id, self.secret)
        if not isinstance(auth_tag, str) or not hmac.compare_digest(expected, auth_tag):
            raise RemoteWorkerError("fleet worker authentication failed")

    def _append_expired_unlocked(
        self, task: FleetTask, lease: RemoteLease | None, *, reason: str | None = None
    ) -> None:
        if lease is None:
            raise RemoteWorkerError("fleet task lease is incomplete")
        diagnostic = reason or "fleet lease expired; worker outcome is unknown"
        unknown = replace(
            task,
            status=FleetTaskStatus.UNKNOWN,
            assigned_worker_id=None,
            lease_id=None,
            lease_expires_at=None,
            updated_at=_now(),
            diagnostic=diagnostic,
        )
        result = RemoteResult(
            task.task_id, lease.lease_id, "unknown", diagnostic=unknown.diagnostic, unknown=True
        )
        self._append_unlocked(
            "task_expired", {"task": unknown.as_mapping(), "result": _result_mapping(result)}
        )

    def _state_unlocked(self) -> tuple[dict[str, FleetWorker], dict[str, FleetTask]]:
        workers: dict[str, FleetWorker] = {}
        tasks: dict[str, FleetTask] = {}
        if not self.path.exists():
            return workers, tasks
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_FLEET_LEDGER_BYTES:
                raise ValueError
            for index, line in enumerate(raw.splitlines()):
                if index >= MAX_FLEET_LEDGER_EVENTS:
                    raise ValueError
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict) or set(value) != {
                    "schema_version",
                    "kind",
                    "payload",
                }:
                    raise ValueError
                payload = value["payload"]
                if value["schema_version"] != 1 or not isinstance(payload, dict):
                    raise ValueError
                kind = value["kind"]
                if kind in {"worker_registered", "worker_heartbeat"}:
                    worker = _worker_from_mapping(payload["worker"])
                    workers[worker.worker_id] = worker
                elif kind == "worker_revoked":
                    worker = workers.get(payload["worker_id"])
                    if worker is not None:
                        workers[worker.worker_id] = replace(
                            worker, status=FleetWorkerStatus.REVOKED
                        )
                elif kind in {
                    "task_submitted",
                    "task_assigned",
                    "lease_heartbeat",
                    "task_completed",
                    "task_expired",
                }:
                    task = _task_from_mapping(payload["task"])
                    tasks[task.task_id] = task
                    if "lease" in payload:
                        lease = _lease_from_mapping(payload["lease"])
                        if (
                            task.lease_id != lease.lease_id
                            or task.lease_expires_at != lease.expires_at
                            or task.assigned_worker_id != lease.worker_id
                        ):
                            raise ValueError
                else:
                    raise ValueError
            return workers, tasks
        except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteWorkerError("fleet ledger is invalid") from None

    def _append_unlocked(self, kind: str, payload: dict[str, object]) -> None:
        if kind not in {
            "worker_registered",
            "worker_heartbeat",
            "worker_revoked",
            "task_submitted",
            "task_assigned",
            "lease_heartbeat",
            "task_completed",
            "task_expired",
        }:
            raise RemoteWorkerError("fleet ledger event is invalid")
        current = self.path.stat().st_size if self.path.exists() else 0
        encoded = (
            json.dumps(
                {"schema_version": 1, "kind": kind, "payload": payload},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        if current + len(encoded) > MAX_FLEET_LEDGER_BYTES:
            raise RemoteWorkerError("fleet ledger exceeds size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

    def _locked(self):
        return _FleetFileLock(self.lock_path)


class _FleetFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: object) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


def _worker_from_mapping(value: object) -> FleetWorker:
    if not isinstance(value, dict):
        raise ValueError
    try:
        return FleetWorker(
            worker_id=value["worker_id"],
            endpoint=value["endpoint"],
            workspace_fingerprint=value["workspace_fingerprint"],
            capabilities=tuple(value["capabilities"]),
            tenants=tuple(value["tenants"]),
            max_permission_mode=value["max_permission_mode"],
            max_concurrency=value["max_concurrency"],
            status=FleetWorkerStatus(value["status"]),
            last_seen=value["last_seen"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError from None


def _task_from_mapping(value: object) -> FleetTask:
    if not isinstance(value, dict):
        raise ValueError
    try:
        return FleetTask(
            task_id=value["task_id"],
            tenant_id=value["tenant_id"],
            workspace_fingerprint=value["workspace_fingerprint"],
            objective=value["objective"],
            permission_mode=value["permission_mode"],
            required_capabilities=tuple(value["required_capabilities"]),
            status=FleetTaskStatus(value["status"]),
            assigned_worker_id=value["assigned_worker_id"],
            lease_id=value["lease_id"],
            lease_expires_at=value.get("lease_expires_at"),
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            diagnostic=value["diagnostic"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError from None


def _lease_mapping(lease: RemoteLease) -> dict[str, object]:
    return {
        "lease_id": lease.lease_id,
        "task_id": lease.task_id,
        "worker_id": lease.worker_id,
        "expires_at": lease.expires_at,
    }


def _envelope_mapping(envelope: RemoteTaskEnvelope) -> dict[str, str]:
    return {
        "task_id": envelope.task_id,
        "workspace_fingerprint": envelope.workspace_fingerprint,
        "objective": envelope.objective,
        "permission_mode": envelope.permission_mode,
        "payload_sha256": envelope.payload_sha256,
        "auth_tag": envelope.auth_tag,
    }


def _assignment_from_mapping(value: object) -> FleetAssignment:
    if not isinstance(value, dict) or set(value) != {"task", "lease"}:
        raise RemoteWorkerError("remote fleet assignment response is invalid")
    try:
        return FleetAssignment(
            _task_from_mapping(value["task"]), _lease_from_mapping(value["lease"])
        )
    except (TypeError, ValueError):
        raise RemoteWorkerError("remote fleet assignment response is invalid") from None


def _lease_from_mapping(value: object) -> RemoteLease:
    if not isinstance(value, dict):
        raise ValueError
    return RemoteLease(**value)


def _lease_for_task(task: FleetTask) -> RemoteLease | None:
    if task.assigned_worker_id is None or task.lease_id is None or task.lease_expires_at is None:
        return None
    return RemoteLease(task.lease_id, task.task_id, task.assigned_worker_id, task.lease_expires_at)


def _result_mapping(result: RemoteResult) -> dict[str, object]:
    return {
        "task_id": result.task_id,
        "lease_id": result.lease_id,
        "status": result.status,
        "result_sha256": result.result_sha256,
        "diagnostic": result.diagnostic,
        "unknown": result.unknown,
    }


__all__ = [
    "FleetAssignment",
    "FleetTask",
    "FleetTaskStatus",
    "FleetWorker",
    "FleetWorkerStatus",
    "RemoteFleetClient",
    "RemoteWorkerFleet",
]
