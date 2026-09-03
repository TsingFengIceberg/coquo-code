"""Authenticated HTTPS control plane for a tenant-aware Worker fleet."""

from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from coquo.remote_fleet import (
    FleetAssignment,
    FleetWorker,
    RemoteWorkerFleet,
)
from coquo.remote_workers import (
    RemoteLease,
    RemoteResult,
    RemoteTaskEnvelope,
    RemoteWorkerError,
)
from coquo.tenant import TenantRegistry


def create_remote_fleet_app(
    workspace: Path,
    *,
    secret: bytes,
    require_https: bool = True,
    tenant_registry: TenantRegistry | None = None,
) -> FastAPI:
    """Create the fleet coordinator API.

    The API is deliberately a scheduler only.  Worker processes execute their
    own bounded runtime and report a result; this service never runs arbitrary
    task code.  Authentication is a bearer transport guard in addition to the
    per-worker HMAC proofs carried by the Fleet API.
    """
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("fleet service secret is required")
    if type(require_https) is not bool:
        raise ValueError("fleet HTTPS setting is invalid")
    fleet = RemoteWorkerFleet(workspace, secret=secret, tenant_registry=tenant_registry)
    app = FastAPI(title="Coquo Remote Worker Fleet", docs_url=None, redoc_url=None)
    app.state.coquo_remote_fleet = fleet

    @app.exception_handler(RemoteWorkerError)
    async def fleet_error(_request: Request, error: RemoteWorkerError):
        return JSONResponse(
            {"error": "remote_fleet_rejected", "message": str(error)}, status_code=409
        )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith("/v1/"):
            if require_https and request.url.scheme != "https":
                return JSONResponse({"error": "fleet HTTPS is required"}, status_code=426)
            supplied = request.headers.get("authorization", "")
            try:
                expected = "Bearer " + secret.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return JSONResponse(
                    {"error": "fleet authentication configuration invalid"}, status_code=500
                )
            if not hmac.compare_digest(supplied, expected):
                return JSONResponse({"error": "fleet authentication required"}, status_code=401)
            if tenant_registry is not None and request.url.path in {"/v1/tasks"}:
                policy = tenant_registry.resolve_token(
                    request.headers.get("x-coquo-tenant-token", "")
                )
                if policy is None:
                    return JSONResponse(
                        {"error": "fleet tenant authentication required"}, status_code=401
                    )
                request.state.coquo_tenant = policy.tenant_id
        return await call_next(request)

    @app.post("/v1/workers/register")
    async def register(payload: Any):
        values = _mapping_payload(payload)
        worker_value = values.get("worker")
        auth_tag = values.get("auth_tag")
        if not isinstance(worker_value, dict) or not isinstance(auth_tag, str):
            raise RemoteWorkerError("fleet worker registration payload is invalid")
        try:
            worker = FleetWorker(
                worker_id=worker_value["worker_id"],
                endpoint=worker_value["endpoint"],
                workspace_fingerprint=worker_value["workspace_fingerprint"],
                capabilities=tuple(worker_value.get("capabilities", ())),
                tenants=tuple(worker_value.get("tenants", ())),
                max_permission_mode=worker_value.get("max_permission_mode", "read-only"),
                max_concurrency=worker_value.get("max_concurrency", 1),
            )
        except (KeyError, TypeError, ValueError):
            raise RemoteWorkerError("fleet worker registration payload is invalid") from None
        return fleet.register(worker, auth_tag=auth_tag).as_mapping()

    @app.post("/v1/workers/heartbeat")
    async def worker_heartbeat(payload: Any):
        values = _mapping_payload(payload)
        worker_id = values.get("worker_id")
        auth_tag = values.get("auth_tag")
        if not isinstance(worker_id, str) or not isinstance(auth_tag, str):
            raise RemoteWorkerError("fleet worker heartbeat payload is invalid")
        return fleet.heartbeat(worker_id, auth_tag=auth_tag).as_mapping()

    @app.post("/v1/tasks")
    async def submit(payload: Any, request: Request):
        values = _mapping_payload(payload)
        try:
            envelope = RemoteTaskEnvelope(
                task_id=values["task_id"],
                workspace_fingerprint=values["workspace_fingerprint"],
                objective=values["objective"],
                permission_mode=values["permission_mode"],
                payload_sha256=values["payload_sha256"],
                auth_tag=values["auth_tag"],
            )
            scoped_tenant = getattr(request.state, "coquo_tenant", None)
            if scoped_tenant is None:
                scoped_tenant = values.get("tenant_id")
            if not isinstance(scoped_tenant, str):
                raise RemoteWorkerError("fleet tenant scope is required")
            task = fleet.submit(
                envelope,
                tenant_id=scoped_tenant,
                required_capabilities=tuple(values.get("required_capabilities", ())),
            )
        except (KeyError, TypeError, ValueError):
            raise RemoteWorkerError("fleet task submission payload is invalid") from None
        return task.as_mapping()

    @app.post("/v1/dispatch")
    async def dispatch(payload: Any):
        values = _mapping_payload(payload)
        if not isinstance(values.get("worker_id"), str) or not isinstance(
            values.get("auth_tag"), str
        ):
            raise RemoteWorkerError("fleet dispatch payload is invalid")
        assignment = fleet.dispatch(
            values["worker_id"],
            auth_tag=values["auth_tag"],
            capabilities=tuple(values.get("capabilities", ())),
            lease_seconds=values.get("lease_seconds", 30.0),
        )
        return None if assignment is None else _assignment_mapping(assignment)

    @app.post("/v1/lease/heartbeat")
    async def lease_heartbeat(payload: Any):
        values = _mapping_payload(payload)
        lease = _lease_from_mapping(values.get("lease"))
        auth_tag = values.get("auth_tag")
        if not isinstance(auth_tag, str):
            raise RemoteWorkerError("fleet lease heartbeat payload is invalid")
        return _assignment_mapping(
            fleet.heartbeat_lease(
                lease,
                auth_tag=auth_tag,
                lease_seconds=values.get("lease_seconds", 30.0),
            )
        )

    @app.post("/v1/complete")
    async def complete(payload: Any):
        values = _mapping_payload(payload)
        lease = _lease_from_mapping(values.get("lease"))
        result_value = values.get("result")
        if not isinstance(result_value, dict) or not isinstance(values.get("auth_tag"), str):
            raise RemoteWorkerError("fleet completion payload is invalid")
        try:
            result = RemoteResult(**result_value)
        except (TypeError, ValueError):
            raise RemoteWorkerError("fleet completion payload is invalid") from None
        return fleet.complete(lease, result, auth_tag=values["auth_tag"]).as_mapping()

    @app.post("/v1/recover")
    async def recover():
        return {"recovered_task_ids": list(fleet.recover_expired())}

    @app.get("/v1/workers")
    async def workers():
        return {"workers": [worker.as_mapping() for worker in fleet.workers()]}

    @app.get("/v1/tasks")
    async def tasks(request: Request, tenant_id: str | None = None):
        scoped_tenant = getattr(request.state, "coquo_tenant", None)
        if (
            tenant_registry is not None
            and scoped_tenant is not None
            and tenant_id
            not in {
                None,
                scoped_tenant,
            }
        ):
            return JSONResponse({"error": "fleet tenant scope mismatch"}, status_code=403)
        return {
            "tasks": [
                task.as_mapping() for task in fleet.tasks(tenant_id=scoped_tenant or tenant_id)
            ]
        }

    return app


def _mapping_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RemoteWorkerError("fleet request payload is invalid")
    return value


def _lease_from_mapping(value: object) -> RemoteLease:
    if not isinstance(value, dict):
        raise RemoteWorkerError("fleet lease payload is invalid")
    try:
        return RemoteLease(**value)
    except (TypeError, ValueError):
        raise RemoteWorkerError("fleet lease payload is invalid") from None


def _assignment_mapping(assignment: FleetAssignment) -> dict[str, object]:
    return {"task": assignment.task.as_mapping(), "lease": _lease_mapping(assignment.lease)}


def _lease_mapping(lease: RemoteLease) -> dict[str, object]:
    return {
        "lease_id": lease.lease_id,
        "task_id": lease.task_id,
        "worker_id": lease.worker_id,
        "expires_at": lease.expires_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coquo-remote-fleet")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--secret-env", default="COQUO_REMOTE_FLEET_SECRET")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18753)
    parser.add_argument("--certfile", required=True)
    parser.add_argument("--keyfile", required=True)
    parser.add_argument("--client-ca", required=True)
    parser.add_argument(
        "--enforce-tenants",
        action="store_true",
        help="require each submitted task to match a configured tenant policy",
    )
    args = parser.parse_args(argv)
    secret_value = os.environ.get(args.secret_env)
    if not secret_value:
        parser.error(f"missing fleet secret environment variable: {args.secret_env}")
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        parser.error("workspace must be an existing directory")
    for label, value in (
        ("certfile", args.certfile),
        ("keyfile", args.keyfile),
        ("client-ca", args.client_ca),
    ):
        if not Path(value).is_file():
            parser.error(f"{label} must be an existing file")
    uvicorn.run(
        create_remote_fleet_app(
            workspace,
            secret=secret_value.encode(),
            require_https=True,
            tenant_registry=TenantRegistry(workspace) if args.enforce_tenants else None,
        ),
        host=args.host,
        port=args.port,
        ssl_certfile=args.certfile,
        ssl_keyfile=args.keyfile,
        ssl_ca_certs=args.client_ca,
        ssl_cert_reqs=2,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["create_remote_fleet_app", "main"]
