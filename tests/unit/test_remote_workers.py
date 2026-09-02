from __future__ import annotations

import pytest

from coquo.remote_workers import (
    InMemoryRemoteTransport,
    RemoteResult,
    RemoteWorkerError,
    make_envelope,
)


def test_remote_transport_requires_auth_and_supports_lease_completion(tmp_path):
    import uuid

    secret = b"test-secret"
    transport = InMemoryRemoteTransport(secret=secret)
    task_id = str(uuid.uuid4())
    envelope = make_envelope(
        task_id=task_id,
        workspace_fingerprint="ws",
        objective="inspect",
        permission_mode="read-only",
        payload=b"x",
        secret=secret,
    )
    transport.submit(envelope)
    lease = transport.claim("worker-a", lease_seconds=30)
    assert lease is not None
    result = transport.complete(lease, RemoteResult(task_id, lease.lease_id, "completed", "a" * 64))
    assert result.status == "completed"
    assert transport.result(task_id) == result


def test_remote_transport_rejects_bad_auth_and_stale_completion(tmp_path):
    import uuid

    transport = InMemoryRemoteTransport(secret=b"secret")
    task_id = str(uuid.uuid4())
    bad = make_envelope(
        task_id=task_id,
        workspace_fingerprint="ws",
        objective="inspect",
        permission_mode="read-only",
        payload=b"x",
        secret=b"other",
    )
    with pytest.raises(RemoteWorkerError, match="authentication"):
        transport.submit(bad)
