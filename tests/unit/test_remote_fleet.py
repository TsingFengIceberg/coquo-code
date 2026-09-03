from __future__ import annotations

import time
from uuid import uuid4

import pytest

from coquo.remote_fleet import (
    FleetTaskStatus,
    FleetWorker,
    FleetWorkerStatus,
    RemoteFleetClient,
    RemoteWorkerFleet,
)
from coquo.remote_workers import RemoteResult, RemoteWorkerError, make_envelope, worker_auth_tag
from coquo.tenant import TenantPolicy, TenantRegistry


def _envelope(secret: bytes, workspace: str = "workspace-v1-test"):
    return make_envelope(
        task_id=str(uuid4()),
        workspace_fingerprint=workspace,
        objective="inspect workspace",
        permission_mode="read-only",
        payload=b"objective",
        secret=secret,
    )


def _worker(secret: bytes, worker_id: str = "worker-a") -> FleetWorker:
    return FleetWorker(
        worker_id=worker_id,
        endpoint="https://worker.example.test/v1",
        workspace_fingerprint="workspace-v1-test",
        capabilities=("read",),
        tenants=("tenant-a",),
        max_permission_mode="read-only",
    )


def test_fleet_dispatch_replays_lease_and_expiry_as_unknown(tmp_path):
    secret = b"fleet-secret"
    fleet = RemoteWorkerFleet(tmp_path, secret=secret)
    worker = _worker(secret)
    fleet.register(worker, auth_tag=worker_auth_tag(worker.worker_id, secret))
    submitted = fleet.submit(
        _envelope(secret), tenant_id="tenant-a", required_capabilities=("read",)
    )
    assignment = fleet.dispatch(
        worker.worker_id,
        auth_tag=worker_auth_tag(worker.worker_id, secret),
        capabilities=("read",),
        lease_seconds=0.01,
    )
    assert assignment is not None
    restarted = RemoteWorkerFleet(tmp_path, secret=secret)
    assert restarted.tasks()[0].lease_expires_at == assignment.lease.expires_at
    time.sleep(0.03)
    assert restarted.recover_expired() == (submitted.task_id,)
    assert restarted.tasks()[0].status is FleetTaskStatus.UNKNOWN


def test_fleet_enforces_tenant_capability_and_permission_boundaries(tmp_path):
    secret = b"fleet-secret"
    fleet = RemoteWorkerFleet(tmp_path, secret=secret)
    worker = _worker(secret)
    fleet.register(worker, auth_tag=worker_auth_tag(worker.worker_id, secret))
    fleet.submit(_envelope(secret), tenant_id="tenant-b")
    assert (
        fleet.dispatch(worker.worker_id, auth_tag=worker_auth_tag(worker.worker_id, secret)) is None
    )
    with pytest.raises(RemoteWorkerError, match="authentication"):
        fleet.dispatch(worker.worker_id, auth_tag="0" * 64)


def test_fleet_completion_is_lease_bound_and_idempotent(tmp_path):
    secret = b"fleet-secret"
    fleet = RemoteWorkerFleet(tmp_path, secret=secret)
    worker = _worker(secret)
    auth = worker_auth_tag(worker.worker_id, secret)
    fleet.register(worker, auth_tag=auth)
    envelope = _envelope(secret)
    fleet.submit(envelope, tenant_id="tenant-a")
    assignment = fleet.dispatch(worker.worker_id, auth_tag=auth)
    assert assignment is not None
    result = RemoteResult(envelope.task_id, assignment.lease.lease_id, "completed", "a" * 64)
    assert (
        fleet.complete(assignment.lease, result, auth_tag=auth).status is FleetTaskStatus.COMPLETED
    )
    with pytest.raises(RemoteWorkerError, match="stale"):
        fleet.complete(assignment.lease, result, auth_tag=auth)


def test_fleet_uses_configured_tenant_workspace_and_active_task_quota(tmp_path):
    secret = b"fleet-secret"
    registry = TenantRegistry(tmp_path)
    registry.configure(TenantPolicy("tenant-a", "workspace-v1-task", max_active_tasks=1))
    fleet = RemoteWorkerFleet(tmp_path, secret=secret, tenant_registry=registry)
    fleet.submit(_envelope(secret, "workspace-v1-task"), tenant_id="tenant-a")
    with pytest.raises(RemoteWorkerError, match="quota"):
        fleet.submit(_envelope(secret, "workspace-v1-task"), tenant_id="tenant-a")
    with pytest.raises(RemoteWorkerError, match="does not own"):
        fleet.submit(_envelope(secret, "workspace-v1-wrong"), tenant_id="tenant-a")


def test_fleet_revocation_marks_active_lease_unknown(tmp_path):
    secret = b"fleet-secret"
    fleet = RemoteWorkerFleet(tmp_path, secret=secret)
    worker = _worker(secret)
    auth = worker_auth_tag(worker.worker_id, secret)
    fleet.register(worker, auth_tag=auth)
    envelope = _envelope(secret)
    fleet.submit(envelope, tenant_id="tenant-a")
    assignment = fleet.dispatch(worker.worker_id, auth_tag=auth)
    assert assignment is not None
    fleet.revoke(worker.worker_id)
    task = fleet.tasks()[0]
    assert task.status is FleetTaskStatus.UNKNOWN
    assert "revoked" in (task.diagnostic or "")
    assert fleet.workers()[0].status is FleetWorkerStatus.REVOKED
    with pytest.raises(RemoteWorkerError, match="stale"):
        fleet.complete(
            assignment.lease,
            RemoteResult(envelope.task_id, assignment.lease.lease_id, "completed"),
            auth_tag=auth,
        )


def test_fleet_enforces_worker_registry_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("coquo.remote_fleet.MAX_FLEET_WORKERS", 1)
    secret = b"fleet-secret"
    fleet = RemoteWorkerFleet(tmp_path, secret=secret)
    first = _worker(secret, "worker-a")
    fleet.register(first, auth_tag=worker_auth_tag(first.worker_id, secret))
    second = _worker(secret, "worker-b")
    with pytest.raises(RemoteWorkerError, match="registry is full"):
        fleet.register(second, auth_tag=worker_auth_tag(second.worker_id, secret))


def test_remote_fleet_client_rejects_redirect_and_decodes_control_plane(monkeypatch):
    client = RemoteFleetClient("http://127.0.0.1:18753", secret=b"fleet-secret")
    worker = _worker(b"fleet-secret")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: worker.as_mapping())
    assert client.register(worker) == worker
    with pytest.raises(ValueError, match="HTTPS or local HTTP"):
        RemoteFleetClient("http://fleet.example.test", secret=b"fleet-secret")


def test_remote_fleet_client_validates_and_sends_tenant_token(monkeypatch):
    client = RemoteFleetClient(
        "http://127.0.0.1:18753", secret=b"fleet-secret", tenant_token="tenant-token"
    )
    seen = {}

    class FakeRequest:
        def __init__(self, url, data=None, headers=None, method=None):
            seen.update(url=url, headers=headers, method=method)

    monkeypatch.setattr("coquo.remote_fleet.Request", FakeRequest)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return b'{"tasks": []}'

    monkeypatch.setattr(client._opener, "open", lambda *_args, **_kwargs: Response())
    assert client.tasks() == ()
    assert seen["headers"]["X-Coquo-Tenant-Token"] == "tenant-token"
    with pytest.raises(ValueError, match="tenant token"):
        RemoteFleetClient("http://127.0.0.1:18753", secret=b"fleet-secret", tenant_token="")
