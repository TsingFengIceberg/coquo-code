"""Strict, credential-free Skill/Plugin marketplace index and lifecycle.

The marketplace is deliberately a metadata and package-verification layer.
It never imports extension code and never activates a downloaded package without
an explicit Host approval.  A publisher is trusted only when configured by the
Host and the package digest matches the signed index entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
from threading import RLock
from typing import Mapping
from datetime import datetime, timezone


class MarketplaceError(RuntimeError):
    """Raised for malformed indexes or unsafe package lifecycle operations."""


MARKETPLACE_LEDGER_SCHEMA_VERSION = 1
MAX_MARKETPLACE_LEDGER_EVENTS = 20_000
MAX_MARKETPLACE_LEDGER_BYTES = 8 * 1024 * 1024
SIGNATURE_ED25519 = "ed25519"
SIGNATURE_LEGACY_SHA256 = "legacy-sha256"


class MarketplaceTrustStore:
    """Persistent publisher-key trust with explicit revoke/rotation."""

    def __init__(self, workspace: Path) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise MarketplaceError("marketplace trust workspace is not a directory")
        self.path = root / ".coquo" / "marketplace" / "v1" / "trusted-keys.json"
        self.lock_path = self.path.with_suffix(".lock")
        self._guard = RLock()

    def add(self, key_id: str, publisher: str, public_key: bytes | str) -> None:
        _validate_key_id(key_id)
        if not isinstance(publisher, str) or not publisher or len(publisher) > 256:
            raise MarketplaceError("marketplace key publisher is invalid")
        try:
            key = (
                base64.b64decode(public_key, validate=True)
                if isinstance(public_key, str)
                else public_key
            )
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            Ed25519PublicKey.from_public_bytes(key)
        except Exception as error:
            raise MarketplaceError("marketplace public key is invalid") from error
        with self._guard, _marketplace_file_lock(self.lock_path):
            values = self._read_unlocked()
            current = values.get(key_id)
            if current is not None and current["status"] == "active":
                raise MarketplaceError("marketplace key ID is already active")
            values[key_id] = {
                "publisher": publisher,
                "public_key": base64.b64encode(key).decode("ascii"),
                "status": "active",
                "updated_at": _marketplace_now(),
            }
            self._write_unlocked(values)

    def revoke(self, key_id: str) -> None:
        _validate_key_id(key_id)
        with self._guard, _marketplace_file_lock(self.lock_path):
            values = self._read_unlocked()
            if key_id not in values:
                raise MarketplaceError("marketplace key ID was not found")
            values[key_id]["status"] = "revoked"
            values[key_id]["updated_at"] = _marketplace_now()
            self._write_unlocked(values)

    def resolve(self, key_id: str) -> tuple[str, bytes] | None:
        _validate_key_id(key_id)
        with self._guard, _marketplace_file_lock(self.lock_path):
            value = self._read_unlocked().get(key_id)
        if value is None or value.get("status") != "active":
            return None
        try:
            return value["publisher"], base64.b64decode(value["public_key"], validate=True)
        except (KeyError, ValueError, TypeError):
            raise MarketplaceError("marketplace trust store is invalid") from None

    def list(self) -> tuple[dict[str, str], ...]:
        with self._guard, _marketplace_file_lock(self.lock_path):
            values = self._read_unlocked()
        return tuple({"key_id": key_id, **dict(item)} for key_id, item in sorted(values.items()))

    def _read_unlocked(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not all(
                isinstance(key, str) and isinstance(item, dict) for key, item in value.items()
            ):
                raise ValueError
            for item in value.values():
                if set(item) != {"publisher", "public_key", "status", "updated_at"}:
                    raise ValueError
                if item["status"] not in {"active", "revoked"}:
                    raise ValueError
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise MarketplaceError("marketplace trust store is invalid") from None

    def _write_unlocked(self, values: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(values, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(self.path)


class MarketplaceStatus(StrEnum):
    QUARANTINED = "quarantined"
    APPROVED = "approved"
    INSTALLED = "installed"
    REVOKED = "revoked"
    ROLLED_BACK = "rolled-back"


@dataclass(frozen=True)
class MarketplaceEntry:
    name: str
    version: str
    package_sha256: str
    publisher: str
    signature: str
    package_url: str
    signature_algorithm: str = SIGNATURE_LEGACY_SHA256
    public_key_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.name
            or len(self.name) > 128
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in self.name)
        ):
            raise ValueError("marketplace name is invalid")
        if (
            not self.version
            or len(self.version) > 32
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-+"
                for c in self.version
            )
        ):
            raise ValueError("marketplace version is invalid")
        if len(self.package_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.package_sha256
        ):
            raise ValueError("marketplace package digest is invalid")
        if (
            not self.publisher
            or len(self.publisher) > 256
            or "\n" in self.publisher
            or "\r" in self.publisher
        ):
            raise ValueError("marketplace publisher is invalid")
        if not self.signature or len(self.signature) > 1024:
            raise ValueError("marketplace signature is invalid")
        if not self.package_url.startswith("https://") or len(self.package_url) > 2048:
            raise ValueError("marketplace package URL must be HTTPS")
        if self.signature_algorithm not in {SIGNATURE_LEGACY_SHA256, SIGNATURE_ED25519}:
            raise ValueError("marketplace signature algorithm is invalid")
        if self.signature_algorithm == SIGNATURE_ED25519:
            if (
                not isinstance(self.public_key_id, str)
                or not self.public_key_id
                or len(self.public_key_id) > 128
                or any(
                    character
                    not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
                    for character in self.public_key_id
                )
            ):
                raise ValueError("marketplace public key ID is required")
        elif self.public_key_id is not None:
            raise ValueError("legacy marketplace signatures cannot declare a public key ID")

    def signed_payload(self) -> bytes:
        payload = {
            "name": self.name,
            "package_sha256": self.package_sha256,
            "package_url": self.package_url,
            "publisher": self.publisher,
            "version": self.version,
        }
        if self.signature_algorithm == SIGNATURE_ED25519:
            payload["public_key_id"] = self.public_key_id
            payload["signature_algorithm"] = self.signature_algorithm
        return _canonical_json(payload)

    def signed_payload_b64(self) -> str:
        return base64.b64encode(self.signed_payload()).decode("ascii")


@dataclass(frozen=True)
class MarketplaceIndex:
    entries: tuple[MarketplaceEntry, ...]
    index_sha256: str
    schema_version: int = 1

    @classmethod
    def from_mapping(cls, value: object) -> "MarketplaceIndex":
        if not isinstance(value, dict) or set(value) != {"schema_version", "entries"}:
            raise MarketplaceError("marketplace index fields are invalid")
        if value.get("schema_version") != 1 or not isinstance(value.get("entries"), list):
            raise MarketplaceError("unsupported marketplace index")
        entries: list[MarketplaceEntry] = []
        try:
            for item in value["entries"]:
                if not isinstance(item, dict) or not set(item) <= {
                    "name",
                    "version",
                    "package_sha256",
                    "publisher",
                    "signature",
                    "package_url",
                    "signature_algorithm",
                    "public_key_id",
                }:
                    raise MarketplaceError("marketplace entry fields are invalid")
                entries.append(MarketplaceEntry(**item))
        except (TypeError, ValueError) as error:
            raise MarketplaceError("marketplace entry is invalid") from error
        if len(entries) > 1024 or len({(e.name, e.version) for e in entries}) != len(entries):
            raise MarketplaceError("marketplace entries are duplicated or oversized")
        raw = _canonical_json(value)
        return cls(
            tuple(sorted(entries, key=lambda e: (e.name, e.version))),
            hashlib.sha256(raw).hexdigest(),
        )

    @classmethod
    def load(cls, path: Path) -> "MarketplaceIndex":
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 2 * 1024 * 1024:
                raise MarketplaceError("marketplace index exceeds size limit")
            return cls.from_mapping(json.loads(raw.decode("utf-8")))
        except MarketplaceError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MarketplaceError("marketplace index is unreadable") from error


@dataclass(frozen=True)
class MarketplacePackage:
    entry: MarketplaceEntry
    root: Path
    status: MarketplaceStatus
    previous_version: str | None = None


class MarketplaceCatalog:
    """Host-controlled package catalog with explicit trust and rollback."""

    def __init__(
        self,
        workspace: Path,
        *,
        trusted_publishers: frozenset[str] = frozenset(),
        trusted_keys: Mapping[str, bytes | str] | None = None,
        trust_store: MarketplaceTrustStore | None = None,
        allow_legacy_signatures: bool = True,
    ) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise MarketplaceError("workspace is not a directory")
        self.workspace = root
        self.root = root / ".coquo" / "marketplace" / "v1"
        self.trusted_publishers = frozenset(trusted_publishers)
        self.trusted_keys = dict(trusted_keys or {})
        self.trust_store = trust_store
        self.allow_legacy_signatures = bool(allow_legacy_signatures)
        self._packages: dict[str, MarketplacePackage] = {}
        self._ledger = self.root / "events.jsonl"
        self._ledger_lock = self._ledger.with_suffix(".lock")
        self._guard = RLock()
        self._load_ledger()

    def verify(self, entry: MarketplaceEntry, package: Path) -> None:
        if entry.publisher not in self.trusted_publishers:
            raise MarketplaceError("publisher is not trusted")
        package = Path(package).resolve(strict=True)
        if package.is_symlink() or not package.is_dir():
            raise MarketplaceError("marketplace package must be a real directory")
        digest = _directory_digest(package)
        if digest != entry.package_sha256:
            raise MarketplaceError("marketplace package digest mismatch")
        if entry.signature_algorithm == SIGNATURE_LEGACY_SHA256:
            if not self.allow_legacy_signatures:
                raise MarketplaceError("legacy marketplace signatures are disabled")
            expected = "sha256:" + hashlib.sha256(entry.signed_payload()).hexdigest()
            if entry.signature != expected:
                raise MarketplaceError("marketplace signature verification failed")
            return
        key = self.trusted_keys.get(entry.public_key_id)
        key_publisher = entry.publisher
        if self.trust_store is not None:
            trusted = self.trust_store.resolve(entry.public_key_id or "")
            if trusted is not None:
                key_publisher, key = trusted
        if key_publisher != entry.publisher:
            raise MarketplaceError("marketplace signing key publisher mismatch")
        if key is None:
            raise MarketplaceError("marketplace signing key is not trusted")
        try:
            if isinstance(key, str):
                key = base64.b64decode(key, validate=True)
            if not isinstance(key, bytes):
                raise ValueError
            signature = base64.b64decode(entry.signature, validate=True)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            Ed25519PublicKey.from_public_bytes(key).verify(signature, entry.signed_payload())
        except Exception as error:
            raise MarketplaceError("marketplace Ed25519 signature verification failed") from error

    def quarantine(self, entry: MarketplaceEntry, package: Path) -> MarketplacePackage:
        self.verify(entry, package)
        destination = self.root / "quarantine" / entry.name / entry.version
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise MarketplaceError("marketplace package version already quarantined")
        shutil.copytree(Path(package).resolve(), destination, symlinks=False)
        info = MarketplacePackage(entry, destination, MarketplaceStatus.QUARANTINED)
        self._packages[f"{entry.name}@{entry.version}"] = info
        self._append_event("quarantined", info)
        return info

    def approve(self, name: str, version: str) -> MarketplacePackage:
        key = f"{name}@{version}"
        info = self._packages.get(key)
        if info is None or info.status is not MarketplaceStatus.QUARANTINED:
            raise MarketplaceError("package is not quarantined")
        info = MarketplacePackage(info.entry, info.root, MarketplaceStatus.APPROVED)
        self._packages[key] = info
        self._append_event("approved", info)
        return info

    def install(self, name: str, version: str) -> MarketplacePackage:
        key = f"{name}@{version}"
        info = self._packages.get(key)
        if info is None or info.status is not MarketplaceStatus.APPROVED:
            raise MarketplaceError("package requires explicit approval")
        destination = self.root / "installed" / name / version
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise MarketplaceError("package version is already installed")
        shutil.copytree(info.root, destination, symlinks=False)
        info = MarketplacePackage(info.entry, destination, MarketplaceStatus.INSTALLED)
        self._packages[key] = info
        self._append_event("installed", info)
        return info

    def rollback(self, name: str, version: str) -> MarketplacePackage:
        key = f"{name}@{version}"
        info = self._packages.get(key)
        if info is None or info.status is not MarketplaceStatus.INSTALLED:
            raise MarketplaceError("package is not installed")
        info.root.rename(info.root.with_name(info.root.name + ".revoked"))
        info = MarketplacePackage(info.entry, info.root, MarketplaceStatus.ROLLED_BACK)
        self._packages[key] = info
        self._append_event("rolled-back", info)
        return info

    def revoke(self, name: str, version: str) -> MarketplacePackage:
        key = f"{name}@{version}"
        info = self._packages.get(key)
        if info is None or info.status not in {
            MarketplaceStatus.QUARANTINED,
            MarketplaceStatus.APPROVED,
            MarketplaceStatus.INSTALLED,
        }:
            raise MarketplaceError("package cannot be revoked from its current state")
        info = MarketplacePackage(info.entry, info.root, MarketplaceStatus.REVOKED)
        self._packages[key] = info
        self._append_event("revoked", info)
        return info

    def list(self) -> tuple[MarketplacePackage, ...]:
        return tuple(
            sorted(self._packages.values(), key=lambda item: (item.entry.name, item.entry.version))
        )

    def _append_event(self, kind: str, info: MarketplacePackage) -> None:
        event = {
            "schema_version": MARKETPLACE_LEDGER_SCHEMA_VERSION,
            "kind": kind,
            "package": {
                "entry": _entry_mapping(info.entry),
                "root": str(info.root),
                "status": info.status.value,
                "previous_version": info.previous_version,
            },
        }
        encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self._guard, _marketplace_file_lock(self._ledger_lock):
            existing = self._ledger.stat().st_size if self._ledger.exists() else 0
            if existing + len(encoded) > MAX_MARKETPLACE_LEDGER_BYTES:
                raise MarketplaceError("marketplace lifecycle ledger exceeds size limit")
            self._ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._ledger.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    def _load_ledger(self) -> None:
        if not self._ledger.exists():
            return
        try:
            raw = self._ledger.read_bytes()
            if len(raw) > MAX_MARKETPLACE_LEDGER_BYTES:
                raise ValueError
            events = raw.splitlines()
            if len(events) > MAX_MARKETPLACE_LEDGER_EVENTS:
                raise ValueError
            for line in events:
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict) or set(value) != {
                    "schema_version",
                    "kind",
                    "package",
                }:
                    raise ValueError
                if value["schema_version"] != MARKETPLACE_LEDGER_SCHEMA_VERSION:
                    raise ValueError
                package = value["package"]
                if not isinstance(package, dict) or set(package) != {
                    "entry",
                    "root",
                    "status",
                    "previous_version",
                }:
                    raise ValueError
                entry = _entry_from_mapping(package["entry"])
                info = MarketplacePackage(
                    entry,
                    Path(package["root"]),
                    MarketplaceStatus(package["status"]),
                    package["previous_version"],
                )
                self._packages[f"{entry.name}@{entry.version}"] = info
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            raise MarketplaceError("marketplace lifecycle ledger is invalid") from None


class _marketplace_file_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            import fcntl

            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        except ImportError:
            pass
        return self

    def __exit__(self, *_):
        if self.descriptor is None:
            return
        try:
            try:
                import fcntl

                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            except ImportError:
                pass
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def _entry_mapping(entry: MarketplaceEntry) -> dict[str, object]:
    return {
        "name": entry.name,
        "version": entry.version,
        "package_sha256": entry.package_sha256,
        "publisher": entry.publisher,
        "signature": entry.signature,
        "package_url": entry.package_url,
        "signature_algorithm": entry.signature_algorithm,
        "public_key_id": entry.public_key_id,
    }


def _entry_from_mapping(value: object) -> MarketplaceEntry:
    if not isinstance(value, dict):
        raise ValueError
    allowed = {
        "name",
        "version",
        "package_sha256",
        "publisher",
        "signature",
        "package_url",
        "signature_algorithm",
        "public_key_id",
    }
    if not set(value) <= allowed:
        raise ValueError
    return MarketplaceEntry(**value)


def _validate_key_id(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
            for character in value
        )
    ):
        raise MarketplaceError("marketplace key ID is invalid")


def _marketplace_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = [item for item in root.rglob("*") if item.is_file() and not item.is_symlink()]
    if len(files) > 4096:
        raise MarketplaceError("marketplace package contains too many files")
    for item in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
        relative = item.relative_to(root).as_posix().encode("utf-8")
        data = item.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


__all__ = [
    "MarketplaceCatalog",
    "MarketplaceEntry",
    "MarketplaceError",
    "MarketplaceIndex",
    "MarketplacePackage",
    "MarketplaceStatus",
    "MarketplaceTrustStore",
    "SIGNATURE_ED25519",
    "SIGNATURE_LEGACY_SHA256",
]
