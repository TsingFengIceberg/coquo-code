"""Authenticated loopback HTTP control plane for persistent remote workers."""

from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from coquo.remote_workers import (
    PersistentRemoteTransport,
    RemoteLease,
    RemoteResult,
    RemoteTaskEnvelope,
    RemoteWorkerError,
)


def create_remote_worker_app(workspace: Path, *, secret: bytes) -> FastAPI:
    """Create a private worker service bound to one explicit workspace."""
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("remote worker service secret is required")
    transport = PersistentRemoteTransport(workspace, secret=secret)
    app = FastAPI(title="Coquo Remote Worker", docs_url=None, redoc_url=None)
    app.state.coquo_remote_transport = transport

    @app.exception_handler(RemoteWorkerError)
    async def remote_worker_error(_request: Request, error: RemoteWorkerError):
        return JSONResponse(
            {"error": "remote_worker_rejected", "message": str(error)}, status_code=409
        )

    async def authorized(request: Request) -> bool:
        supplied = request.headers.get("authorization", "")
        try:
            expected = "Bearer " + secret.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            # Binary secrets remain valid for HMAC transport but cannot be
            # represented in an HTTP Bearer header; fail closed.
            return False
        return hmac.compare_digest(supplied, expected)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith("/v1/") and not await authorized(request):
            return JSONResponse({"error": "remote worker authentication required"}, status_code=401)
        return await call_next(request)

    @app.post("/v1/tasks", status_code=202)
    async def submit(payload: Any):
        values = _mapping_payload(payload)
        try:
            transport.submit(RemoteTaskEnvelope(**values))
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote task payload is invalid") from None
        return {"status": "queued"}

    @app.post("/v1/claim")
    async def claim(payload: Any):
        values = _mapping_payload(payload)
        try:
            lease = transport.claim(
                values.get("worker_id", ""),
                lease_seconds=values.get("lease_seconds", 30.0),
                auth_tag=values.get("auth_tag"),
            )
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote claim payload is invalid") from None
        return None if lease is None else _lease_mapping(lease)

    @app.post("/v1/heartbeat")
    async def heartbeat(payload: Any):
        values = _mapping_payload(payload)
        if "lease" not in values:
            raise RemoteWorkerError("remote heartbeat payload is invalid")
        try:
            lease = transport.heartbeat(
                _lease_from_mapping(values["lease"]),
                lease_seconds=values.get("lease_seconds", 30.0),
                auth_tag=values.get("auth_tag"),
            )
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote heartbeat payload is invalid") from None
        return _lease_mapping(lease)

    @app.post("/v1/complete")
    async def complete(payload: Any):
        values = _mapping_payload(payload)
        if "lease" not in values or "result" not in values:
            raise RemoteWorkerError("remote completion payload is invalid")
        lease = _lease_from_mapping(values["lease"])
        try:
            result = RemoteResult(**_mapping_payload(values["result"]))
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote result payload is invalid") from None
        try:
            completed = transport.complete(lease, result, auth_tag=values.get("auth_tag"))
        except (TypeError, ValueError):
            raise RemoteWorkerError("remote completion payload is invalid") from None
        return _result_mapping(completed)

    @app.get("/v1/tasks/{task_id}")
    async def result(task_id: str):
        value = transport.result(task_id)
        return None if value is None else _result_mapping(value)

    @app.post("/v1/recover")
    async def recover():
        return {"recovered_task_ids": list(transport.recover_expired())}

    return app


def _lease_mapping(lease: RemoteLease) -> dict[str, object]:
    return {
        "lease_id": lease.lease_id,
        "task_id": lease.task_id,
        "worker_id": lease.worker_id,
        "expires_at": lease.expires_at,
    }


def _lease_from_mapping(value: object) -> RemoteLease:
    if not isinstance(value, dict):
        raise RemoteWorkerError("remote lease payload is invalid")
    try:
        return RemoteLease(**value)
    except (TypeError, ValueError):
        raise RemoteWorkerError("remote lease payload is invalid") from None


def _mapping_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RemoteWorkerError("remote request payload is invalid")
    return value


def _result_mapping(result: RemoteResult) -> dict[str, object]:
    return {
        "task_id": result.task_id,
        "lease_id": result.lease_id,
        "status": result.status,
        "result_sha256": result.result_sha256,
        "diagnostic": result.diagnostic,
        "unknown": result.unknown,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coquo-remote-worker")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--secret-env", default="COQUO_REMOTE_WORKER_SECRET")
    parser.add_argument(
        "--host",
        choices=("127.0.0.1", "localhost", "::1"),
        default="127.0.0.1",
        help="loopback bind address only",
    )
    parser.add_argument("--port", type=int, default=18752)
    args = parser.parse_args(argv)
    secret_value = os.environ.get(args.secret_env)
    if not secret_value:
        parser.error(f"missing worker secret environment variable: {args.secret_env}")
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        parser.error("workspace must be an existing directory")
    uvicorn.run(
        create_remote_worker_app(workspace, secret=secret_value.encode()),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["create_remote_worker_app", "main"]
