"""Strict, credential-free Skill/Plugin marketplace index and lifecycle.

The marketplace is deliberately a metadata and package-verification layer.
It never imports extension code and never activates a downloaded package without
an explicit Host approval.  A publisher is trusted only when configured by the
Host and the package digest matches the signed index entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import shutil


class MarketplaceError(RuntimeError):
    """Raised for malformed indexes or unsafe package lifecycle operations."""


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

    def signed_payload(self) -> bytes:
        return _canonical_json(
            {
                "name": self.name,
                "package_sha256": self.package_sha256,
                "package_url": self.package_url,
                "publisher": self.publisher,
                "version": self.version,
            }
        )


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
                if not isinstance(item, dict) or set(item) != {
                    "name",
                    "version",
                    "package_sha256",
                    "publisher",
                    "signature",
                    "package_url",
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
        self, workspace: Path, *, trusted_publishers: frozenset[str] = frozenset()
    ) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise MarketplaceError("workspace is not a directory")
        self.workspace = root
        self.root = root / ".coquo" / "marketplace" / "v1"
        self.trusted_publishers = frozenset(trusted_publishers)
        self._packages: dict[str, MarketplacePackage] = {}

    def verify(self, entry: MarketplaceEntry, package: Path) -> None:
        if entry.publisher not in self.trusted_publishers:
            raise MarketplaceError("publisher is not trusted")
        package = Path(package).resolve(strict=True)
        if package.is_symlink() or not package.is_dir():
            raise MarketplaceError("marketplace package must be a real directory")
        digest = _directory_digest(package)
        if digest != entry.package_sha256:
            raise MarketplaceError("marketplace package digest mismatch")
        expected = "sha256:" + hashlib.sha256(entry.signed_payload()).hexdigest()
        if entry.signature != expected:
            raise MarketplaceError("marketplace signature verification failed")

    def quarantine(self, entry: MarketplaceEntry, package: Path) -> MarketplacePackage:
        self.verify(entry, package)
        destination = self.root / "quarantine" / entry.name / entry.version
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise MarketplaceError("marketplace package version already quarantined")
        shutil.copytree(Path(package).resolve(), destination, symlinks=False)
        info = MarketplacePackage(entry, destination, MarketplaceStatus.QUARANTINED)
        self._packages[f"{entry.name}@{entry.version}"] = info
        return info

    def approve(self, name: str, version: str) -> MarketplacePackage:
        key = f"{name}@{version}"
        info = self._packages.get(key)
        if info is None or info.status is not MarketplaceStatus.QUARANTINED:
            raise MarketplaceError("package is not quarantined")
        info = MarketplacePackage(info.entry, info.root, MarketplaceStatus.APPROVED)
        self._packages[key] = info
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
        return info

    def rollback(self, name: str, version: str) -> MarketplacePackage:
        key = f"{name}@{version}"
        info = self._packages.get(key)
        if info is None or info.status is not MarketplaceStatus.INSTALLED:
            raise MarketplaceError("package is not installed")
        info.root.rename(info.root.with_name(info.root.name + ".revoked"))
        info = MarketplacePackage(info.entry, info.root, MarketplaceStatus.ROLLED_BACK)
        self._packages[key] = info
        return info

    def list(self) -> tuple[MarketplacePackage, ...]:
        return tuple(
            sorted(self._packages.values(), key=lambda item: (item.entry.name, item.entry.version))
        )


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
]
