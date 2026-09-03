from __future__ import annotations

import asyncio

import pytest

from coquo.remote_worker_server import create_remote_worker_app, main
from coquo.remote_workers import RemoteWorkerError
from coquo.remote_fleet_server import create_remote_fleet_app


def test_remote_worker_service_requires_bearer_authentication(tmp_path):
    app = create_remote_worker_app(tmp_path, secret=b"worker-secret")
    assert app.state.coquo_remote_transport.path.parent.name == "v1"
    routes = {route.path for route in app.routes}
    assert {"/v1/tasks", "/v1/claim", "/v1/heartbeat", "/v1/complete", "/v1/recover"} <= routes
    middleware = [item.cls.__name__ for item in app.user_middleware]
    assert "BaseHTTPMiddleware" in middleware


def test_remote_worker_cli_rejects_non_loopback_host(tmp_path, monkeypatch):
    monkeypatch.setenv("COQUO_REMOTE_WORKER_SECRET", "worker-secret")
    with pytest.raises(SystemExit) as caught:
        main(["--workspace", str(tmp_path), "--host", "0.0.0.0"])
    assert caught.value.code == 2


def test_remote_worker_http_payload_errors_are_structured(tmp_path):
    app = create_remote_worker_app(tmp_path, secret=b"worker-secret")
    claim = next(route for route in app.routes if route.path == "/v1/claim")
    with pytest.raises(RemoteWorkerError, match="claim payload is invalid"):
        asyncio.run(claim.endpoint({"lease_seconds": "bad"}))


def test_remote_fleet_service_exposes_mtls_control_plane_and_tenant_scoped_routes(tmp_path):
    app = create_remote_fleet_app(tmp_path, secret=b"fleet-secret")
    routes = {(route.path, tuple(route.methods or ())) for route in app.routes}
    assert ("/v1/workers/register", ("POST",)) in routes
    assert ("/v1/tasks", ("POST",)) in routes
    assert ("/v1/tasks", ("GET",)) in routes
    assert app.state.coquo_remote_fleet.path.parent.name == "v1"
    assert any(item.cls.__name__ == "BaseHTTPMiddleware" for item in app.user_middleware)
