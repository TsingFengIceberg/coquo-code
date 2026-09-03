from __future__ import annotations

import asyncio
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient

from coquo.marketplace import (
    MarketplaceEntry,
    MarketplaceIndex,
    MarketplaceIndexPublisher,
)
from coquo.marketplace_server import create_marketplace_app


def _request(app, method: str, path: str, **kwargs):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://marketplace.test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def _index_and_package(tmp_path):
    package_root = tmp_path / "packages"
    package = package_root / "demo" / "1.0"
    package.mkdir(parents=True)
    (package / "skill.md").write_text("# demo", encoding="utf-8")
    from coquo.marketplace import _directory_digest

    entry = MarketplaceEntry(
        "demo",
        "1.0",
        _directory_digest(package),
        "publisher",
        "placeholder",
        "https://market.example.test/v1/packages/demo/1.0",
    )
    signed_entry = MarketplaceEntry(
        entry.name,
        entry.version,
        entry.package_sha256,
        entry.publisher,
        "sha256:placeholder",
        entry.package_url,
    )
    index = MarketplaceIndex.from_mapping({"schema_version": 1, "entries": [signed_entry.__dict__]})
    return package_root, index


def test_marketplace_server_serves_signed_index_and_authenticated_package(tmp_path):
    package_root, index = _index_and_package(tmp_path)
    private = Ed25519PrivateKey.generate()
    signed = MarketplaceIndexPublisher.sign(
        index,
        publisher="publisher",
        public_key_id="key-v1",
        private_key=private.private_bytes_raw(),
    )
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(signed.as_mapping()), encoding="utf-8")
    app = create_marketplace_app(
        index_path,
        package_root,
        admin_token="admin",
        require_https=False,
    )
    response = _request(app, "GET", "/v1/index")
    assert response.status_code == 200
    assert response.json()["public_key_id"] == "key-v1"
    assert _request(app, "GET", "/v1/packages/demo/1.0").status_code == 401
    package = _request(
        app,
        "GET",
        "/v1/packages/demo/1.0",
        headers={"Authorization": "Bearer admin"},
    )
    assert package.status_code == 200
    assert package.headers["content-type"].startswith("application/zip")
    assert package.content.startswith(b"PK")


def test_marketplace_server_rejects_package_digest_drift(tmp_path):
    package_root, index = _index_and_package(tmp_path)
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps({"schema_version": 1, "entries": [index.entries[0].__dict__]}),
        encoding="utf-8",
    )
    (package_root / "demo" / "1.0" / "skill.md").write_text("tampered", encoding="utf-8")
    app = create_marketplace_app(index_path, package_root, admin_token="admin", require_https=False)
    response = _request(
        app,
        "GET",
        "/v1/packages/demo/1.0",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 409
    assert "digest" in response.json()["message"]


def test_marketplace_server_requires_https_for_operator_routes(tmp_path):
    package_root, index = _index_and_package(tmp_path)
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps({"schema_version": 1, "entries": [index.entries[0].__dict__]}),
        encoding="utf-8",
    )
    app = create_marketplace_app(index_path, package_root, admin_token="admin")
    response = _request(app, "GET", "/v1/index")
    assert response.status_code == 426
