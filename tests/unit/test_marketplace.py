from __future__ import annotations

import hashlib

import pytest

from coquo.marketplace import (
    MarketplaceCatalog,
    MarketplaceEntry,
    MarketplaceError,
    MarketplaceIndex,
    MarketplaceStatus,
)


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
