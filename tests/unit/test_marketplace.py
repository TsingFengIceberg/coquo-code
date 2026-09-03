from __future__ import annotations

import hashlib
import base64
import json

import pytest

from coquo.marketplace import (
    MarketplaceCatalog,
    MarketplaceEntry,
    MarketplaceError,
    MarketplaceIndex,
    MarketplaceIndexFetcher,
    MarketplaceIndexPublisher,
    MarketplaceStatus,
    MarketplaceTrustStore,
)
from coquo.tenant import TenantPolicy, TenantRegistry, workspace_fingerprint
from coquo.tools.web_transport import WebHttpResponse


def package_digest(root):
    digest = hashlib.sha256()
    for item in sorted(
        (p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()
    ):
        rel = item.relative_to(root).as_posix().encode()
        data = item.read_bytes()
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def test_marketplace_requires_trusted_publisher_signature_and_explicit_lifecycle(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "plugin.json").write_text("{}")
    digest = package_digest(package)
    base = MarketplaceEntry(
        "demo", "1.0.0", digest, "acme", "0" * 64, "https://example.test/demo.zip"
    )
    signed = MarketplaceEntry(
        base.name,
        base.version,
        base.package_sha256,
        base.publisher,
        "sha256:" + hashlib.sha256(base.signed_payload()).hexdigest(),
        base.package_url,
    )
    catalog = MarketplaceCatalog(tmp_path, trusted_publishers=frozenset({"acme"}))
    info = catalog.quarantine(signed, package)
    assert info.status is MarketplaceStatus.QUARANTINED
    with pytest.raises(MarketplaceError, match="approval"):
        catalog.install("demo", "1.0.0")
    catalog.approve("demo", "1.0.0")
    installed = catalog.install("demo", "1.0.0")
    assert installed.status is MarketplaceStatus.INSTALLED
    assert catalog.rollback("demo", "1.0.0").status is MarketplaceStatus.ROLLED_BACK


def test_marketplace_index_is_strict_and_sorted():
    entry = {
        "name": "z",
        "version": "1",
        "package_sha256": "a" * 64,
        "publisher": "p",
        "signature": "s",
        "package_url": "https://x.test/p",
    }
    index = MarketplaceIndex.from_mapping({"schema_version": 1, "entries": [entry]})
    assert index.entries[0].name == "z"
    with pytest.raises(MarketplaceError):
        MarketplaceIndex.from_mapping({"schema_version": 2, "entries": []})


def test_marketplace_ed25519_signature_and_lifecycle_survive_restart(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    package = tmp_path / "signed-pkg"
    package.mkdir()
    (package / "skill.md").write_text("# signed")
    digest = package_digest(package)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    unsigned = MarketplaceEntry(
        "signed",
        "1.0",
        digest,
        "publisher",
        "placeholder",
        "https://example.test/p",
        "ed25519",
        "publisher-key-v1",
    )
    signature = base64.b64encode(private.sign(unsigned.signed_payload())).decode("ascii")
    signed = MarketplaceEntry(
        unsigned.name,
        unsigned.version,
        unsigned.package_sha256,
        unsigned.publisher,
        signature,
        unsigned.package_url,
        unsigned.signature_algorithm,
        unsigned.public_key_id,
    )
    catalog = MarketplaceCatalog(
        tmp_path,
        trusted_publishers=frozenset({"publisher"}),
        trusted_keys={"publisher-key-v1": public},
        allow_legacy_signatures=False,
    )
    catalog.quarantine(signed, package)
    catalog.approve("signed", "1.0")
    catalog.install("signed", "1.0")
    restarted = MarketplaceCatalog(
        tmp_path,
        trusted_publishers=frozenset({"publisher"}),
        trusted_keys={"publisher-key-v1": public},
        allow_legacy_signatures=False,
    )
    assert restarted.list()[0].status is MarketplaceStatus.INSTALLED


def test_marketplace_trust_store_supports_key_rotation_and_revocation(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    store = MarketplaceTrustStore(tmp_path)
    store.add("key-v1", "publisher", public)
    assert store.resolve("key-v1")[0] == "publisher"
    store.revoke("key-v1")
    assert store.resolve("key-v1") is None
    assert MarketplaceTrustStore(tmp_path).list()[0]["status"] == "revoked"


def test_signed_marketplace_index_can_be_written_and_fetched_without_redirects(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    entry = {
        "name": "demo",
        "version": "1",
        "package_sha256": "a" * 64,
        "publisher": "publisher",
        "signature": "s",
        "package_url": "https://example.test/demo",
    }
    index = MarketplaceIndex.from_mapping({"schema_version": 1, "entries": [entry]})
    signed = MarketplaceIndexPublisher.sign(
        index,
        publisher="publisher",
        public_key_id="index-key",
        private_key=private.private_bytes_raw(),
        generated_at="2026-01-01T00:00:00Z",
    )
    path = tmp_path / "index.json"
    MarketplaceIndexPublisher.write(signed, path)
    assert json.loads(path.read_text())["schema_version"] == 1

    class Transport:
        def request(self, method, url, *, headers, body, timeout_seconds, max_response_bytes):
            assert method == "GET"
            return WebHttpResponse(200, "application/json", "", path.read_bytes(), url, 0)

    store = MarketplaceTrustStore(tmp_path)
    store.add("index-key", "publisher", public)
    fetched = MarketplaceIndexFetcher(transport=Transport()).fetch(
        "https://index.example.test/catalog.json", trust_store=store
    )
    assert fetched.index.entries[0].name == "demo"
    with pytest.raises(MarketplaceError, match="query"):
        MarketplaceIndexFetcher(transport=Transport()).fetch(
            "https://index.example.test/catalog.json?x=1", trust_store=store
        )


def test_marketplace_tenant_package_quota_is_enforced(tmp_path):
    registry = TenantRegistry(tmp_path)
    registry.configure(
        TenantPolicy("tenant-a", workspace_fingerprint(tmp_path), max_marketplace_packages=1)
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "skill.md").write_text("# package")
    digest = package_digest(package)
    base = MarketplaceEntry(
        "demo", "1", digest, "publisher", "placeholder", "https://example.test/demo"
    )
    signed = MarketplaceEntry(
        base.name,
        base.version,
        base.package_sha256,
        base.publisher,
        "sha256:" + hashlib.sha256(base.signed_payload()).hexdigest(),
        base.package_url,
    )
    catalog = MarketplaceCatalog(
        tmp_path,
        trusted_publishers=frozenset({"publisher"}),
        tenant_registry=registry,
        tenant_id="tenant-a",
    )
    catalog.quarantine(signed, package)
    second = MarketplaceEntry(
        "demo-two", "1", digest, "publisher", "placeholder", "https://example.test/demo-two"
    )
    second = MarketplaceEntry(
        second.name,
        second.version,
        second.package_sha256,
        second.publisher,
        "sha256:" + hashlib.sha256(second.signed_payload()).hexdigest(),
        second.package_url,
    )
    with pytest.raises(MarketplaceError, match="quota"):
        catalog.quarantine(second, package)


def test_marketplace_tenant_packages_are_partitioned_across_restart(tmp_path):
    registry = TenantRegistry(tmp_path)
    fingerprint = workspace_fingerprint(tmp_path)
    registry.configure(TenantPolicy("tenant-a", fingerprint, max_marketplace_packages=1))
    registry.configure(TenantPolicy("tenant-b", fingerprint, max_marketplace_packages=1))
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "skill.md").write_text("# package")
    digest = package_digest(package)

    def signed_entry(name):
        base = MarketplaceEntry(
            name, "1", digest, "publisher", "placeholder", "https://example.test/" + name
        )
        return MarketplaceEntry(
            base.name,
            base.version,
            base.package_sha256,
            base.publisher,
            "sha256:" + hashlib.sha256(base.signed_payload()).hexdigest(),
            base.package_url,
        )

    tenant_a = MarketplaceCatalog(
        tmp_path,
        trusted_publishers=frozenset({"publisher"}),
        tenant_registry=registry,
        tenant_id="tenant-a",
    )
    tenant_b = MarketplaceCatalog(
        tmp_path,
        trusted_publishers=frozenset({"publisher"}),
        tenant_registry=registry,
        tenant_id="tenant-b",
    )
    a = tenant_a.quarantine(signed_entry("same"), package)
    b = tenant_b.quarantine(signed_entry("same"), package)
    assert a.tenant_id == "tenant-a"
    assert b.tenant_id == "tenant-b"
    assert a.root != b.root
    assert [item.tenant_id for item in tenant_a.list()] == ["tenant-a"]
    assert [item.tenant_id for item in tenant_b.list()] == ["tenant-b"]
    restarted = MarketplaceCatalog(
        tmp_path,
        trusted_publishers=frozenset({"publisher"}),
        tenant_registry=registry,
        tenant_id="tenant-a",
    )
    assert [item.tenant_id for item in restarted.list()] == ["tenant-a"]
