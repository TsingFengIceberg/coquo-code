"""Signed Marketplace index publisher and bounded package hosting service."""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
from pathlib import Path
import io
import zipfile

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import uvicorn

from coquo.marketplace import (
    MAX_MARKETPLACE_INDEX_BYTES,
    MarketplaceError,
    MarketplaceIndex,
    MarketplaceIndexEnvelope,
    _directory_digest,
)
from coquo.tenant import TenantRegistry


MAX_MARKETPLACE_PACKAGE_BYTES = 64 * 1024 * 1024


def create_marketplace_app(
    index_path: Path,
    package_root: Path,
    *,
    admin_token: str | None = None,
    tenant_registry: TenantRegistry | None = None,
    require_https: bool = True,
) -> FastAPI:
    """Serve a signed index and verified package bytes with bounded access.

    Index reads are public because the envelope is independently signed.
    Package downloads require the operator token and, when configured, a
    tenant token.  The server never executes or imports downloaded content.
    """
    index_file = Path(index_path).resolve()
    packages = Path(package_root).resolve()
    if index_file.is_symlink() or packages.is_symlink() or not packages.is_dir():
        raise MarketplaceError("marketplace hosting paths are invalid")
    if admin_token is not None and (not isinstance(admin_token, str) or not admin_token):
        raise MarketplaceError("marketplace admin token is invalid")
    app = FastAPI(title="Coquo Marketplace", docs_url=None, redoc_url=None)
    app.state.coquo_marketplace_index_path = index_file

    @app.exception_handler(MarketplaceError)
    async def marketplace_error(_request: Request, error: MarketplaceError):
        return JSONResponse(
            {"error": "marketplace_rejected", "message": str(error)}, status_code=409
        )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        if require_https and request.url.scheme != "https":
            return JSONResponse({"error": "marketplace HTTPS is required"}, status_code=426)
        if request.url.path.startswith("/v1/packages/"):
            if admin_token is None or not hmac.compare_digest(
                request.headers.get("authorization", ""), "Bearer " + admin_token
            ):
                return JSONResponse(
                    {"error": "marketplace package authentication required"}, status_code=401
                )
            if tenant_registry is not None:
                policy = tenant_registry.resolve_token(
                    request.headers.get("x-coquo-tenant-token", "")
                )
                if policy is None:
                    return JSONResponse(
                        {"error": "marketplace tenant authentication required"}, status_code=401
                    )
                request.state.coquo_tenant = policy.tenant_id
        return await call_next(request)

    @app.get("/v1/index")
    async def index():
        if not index_file.is_file() or index_file.is_symlink():
            return JSONResponse({"error": "marketplace index unavailable"}, status_code=404)
        raw = index_file.read_bytes()
        if len(raw) > MAX_MARKETPLACE_INDEX_BYTES:
            raise MarketplaceError("marketplace index exceeds size limit")
        return Response(raw, media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.put("/v1/index")
    async def publish_index(request: Request):
        if admin_token is None or not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + admin_token
        ):
            return JSONResponse(
                {"error": "marketplace publisher authentication required"}, status_code=401
            )
        raw = await request.body()
        if len(raw) > MAX_MARKETPLACE_INDEX_BYTES:
            raise MarketplaceError("marketplace index exceeds size limit")
        try:
            envelope = MarketplaceIndexEnvelope.from_mapping(json.loads(raw.decode("utf-8")))
            _verify_envelope(envelope, request.headers.get("x-coquo-publisher-key"))
            temporary = index_file.with_suffix(index_file.suffix + ".tmp")
            temporary.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary.write_bytes(raw + (b"" if raw.endswith(b"\n") else b"\n"))
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.replace(index_file)
        except (UnicodeDecodeError, ValueError, OSError) as error:
            raise MarketplaceError("marketplace signed index publish failed") from error
        return {"status": "published", "generated_at": envelope.generated_at}

    @app.get("/v1/packages/{name}/{version}")
    async def package(name: str, version: str, request: Request):
        tenant_id = getattr(request.state, "coquo_tenant", None)
        root = packages if tenant_id is None else packages / "tenants" / tenant_id
        source = root / name / version
        if source.is_symlink() or not source.is_dir():
            return JSONResponse({"error": "marketplace package unavailable"}, status_code=404)
        try:
            index = _load_hosted_index(index_file)
            entry = next(
                item for item in index.entries if item.name == name and item.version == version
            )
            if _directory_digest(source) != entry.package_sha256:
                raise MarketplaceError("marketplace package digest mismatch")
            payload = _zip_directory(source)
        except StopIteration:
            return JSONResponse({"error": "marketplace package is not indexed"}, status_code=404)
        return Response(
            payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}-{version}.zip"'},
        )

    return app


def _load_hosted_index(path: Path) -> MarketplaceIndex:
    """Load either the signed publication envelope or legacy raw index data."""
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_MARKETPLACE_INDEX_BYTES:
            raise MarketplaceError("marketplace index exceeds size limit")
        value = json.loads(raw.decode("utf-8"))
        if isinstance(value, dict) and "index" in value:
            return MarketplaceIndexEnvelope.from_mapping(value).index
        return MarketplaceIndex.from_mapping(value)
    except MarketplaceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketplaceError("marketplace index is unreadable") from error


def _verify_envelope(envelope: MarketplaceIndexEnvelope, public_key_value: str | None) -> None:
    if not public_key_value:
        raise MarketplaceError("marketplace publisher public key is required")
    try:
        public_key = base64.b64decode(public_key_value, validate=True)
        signature = base64.b64decode(envelope.signature, validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, envelope.signed_payload())
    except Exception as error:
        raise MarketplaceError("marketplace index signature verification failed") from error


def _zip_directory(root: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        files = [item for item in root.rglob("*") if item.is_file() and not item.is_symlink()]
        for item in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
            data = item.read_bytes()
            archive.writestr(item.relative_to(root).as_posix(), data)
            if output.tell() > MAX_MARKETPLACE_PACKAGE_BYTES:
                raise MarketplaceError("marketplace package exceeds size limit")
    return output.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coquo-marketplace")
    parser.add_argument("--index", required=True)
    parser.add_argument("--packages", required=True)
    parser.add_argument("--admin-token-env", default="COQUO_MARKETPLACE_ADMIN_TOKEN")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18754)
    parser.add_argument("--allow-http", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get(args.admin_token_env)
    if not token:
        parser.error(
            f"missing marketplace admin token environment variable: {args.admin_token_env}"
        )
    app = create_marketplace_app(
        Path(args.index),
        Path(args.packages),
        admin_token=token,
        require_https=not args.allow_http,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MAX_MARKETPLACE_PACKAGE_BYTES", "create_marketplace_app", "main"]
