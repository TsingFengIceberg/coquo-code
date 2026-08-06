"""Explicit bounded authoring helpers for declarative Skill packages."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping

from leonervis_code.skills.catalog import (
    MAX_SKILL_DESCRIPTION_CHARS,
    SkillCandidate,
    SkillCatalogError,
    SkillInventoryLoader,
    SkillManifest,
    SkillResource,
    SkillSourceKind,
    canonical_skill_name,
    load_skill_package,
    read_skill_package_file,
)


SKILL_AUTHORING_TEMPLATE_VERSION = 1
SKILL_IMPORT_LOCK_VERSION = 1
_SCOPE_TO_SOURCE = {
    "workspace": SkillSourceKind.WORKSPACE_LOCAL,
    "project": SkillSourceKind.PROJECT_SHARED,
    "user": SkillSourceKind.USER,
}


@dataclass(frozen=True)
class SkillAuthoringResult:
    """One newly created package after canonical inventory validation."""

    candidate: SkillCandidate
    root: Path
    template_version: int = SKILL_AUTHORING_TEMPLATE_VERSION


@dataclass(frozen=True)
class SkillImportLock:
    """Portable exact identity for one copied package without source path disclosure."""

    name: str
    scope: str
    fingerprint: str
    resources: tuple[SkillResource, ...]
    version: int = SKILL_IMPORT_LOCK_VERSION

    def __post_init__(self) -> None:
        canonical_skill_name(self.name)
        if self.scope not in _SCOPE_TO_SOURCE:
            raise SkillCatalogError("invalid-lock", "Skill import lock scope is invalid")
        if not self.fingerprint.startswith("skill-v1-") or len(self.fingerprint) != 73:
            raise SkillCatalogError("invalid-lock", "Skill import lock fingerprint is invalid")
        if not isinstance(self.resources, tuple) or any(
            not isinstance(resource, SkillResource) for resource in self.resources
        ):
            raise SkillCatalogError("invalid-lock", "Skill import lock resources are invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "lock-version": self.version,
            "name": self.name,
            "resources": [
                {
                    "bytes": resource.byte_count,
                    "fingerprint": resource.fingerprint,
                    "path": resource.path,
                    "text-readable": resource.text_readable,
                }
                for resource in self.resources
            ],
            "scope": self.scope,
        }

    @property
    def digest(self) -> str:
        raw = json.dumps(
            self.as_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(b"leonervis-skill-import-lock-v1\0" + raw).hexdigest()


@dataclass(frozen=True)
class SkillImportResult:
    """One package copied from an explicit local directory with a durable exact lock."""

    candidate: SkillCandidate
    lock: SkillImportLock
    lock_path: Path


@dataclass(frozen=True)
class SkillLockVerification:
    """Current package identity compared with one exact import lock."""

    lock: SkillImportLock
    lock_path: Path
    current_fingerprint: str | None
    current_resources: tuple[SkillResource, ...]
    valid: bool
    reason: str


@dataclass(frozen=True)
class _CreatedFile:
    path: Path
    identity: tuple[int, int]


def skill_root(
    workspace: Path,
    scope: str,
    environment: Mapping[str, str] | None = None,
) -> tuple[SkillSourceKind, Path]:
    """Resolve one exact supported authoring scope without compatibility scanning."""
    source = _SCOPE_TO_SOURCE.get(scope)
    if source is None:
        raise SkillCatalogError("invalid-scope", "Skill scope must be workspace, project, or user")
    loader = SkillInventoryLoader(workspace, environment)
    return next(item for item in loader.roots if item[0] is source)


def initialize_skill(
    workspace: Path,
    *,
    name: str,
    description: str,
    scope: str = "project",
    environment: Mapping[str, str] | None = None,
) -> SkillAuthoringResult:
    """Create one absent minimal package and validate it through the canonical inventory."""
    canonical_skill_name(name)
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > MAX_SKILL_DESCRIPTION_CHARS
        or "\x00" in description
        or "\r" in description
    ):
        raise SkillCatalogError(
            "invalid-description", "Skill description must contain 1 to 512 safe characters"
        )
    source, root = skill_root(workspace, scope, environment)
    _ensure_directory_chain(root)
    package = root / name
    try:
        package.mkdir(mode=0o700)
    except FileExistsError:
        raise SkillCatalogError("package-exists", f"Skill package already exists: {name}") from None
    except OSError as error:
        raise SkillCatalogError(
            "create-failed", "Skill package directory could not be created"
        ) from error
    package_info = package.lstat()
    package_identity = (package_info.st_dev, package_info.st_ino)
    manifest = (
        "---\n"
        "manifest-version: 1\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n"
        "Describe the workflow, required evidence, execution steps, and verification criteria.\n"
    ).encode("utf-8")
    target = package / "SKILL.md"
    created_file: _CreatedFile | None = None
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            target_info = os.fstat(descriptor)
            created_file = _CreatedFile(target, (target_info.st_dev, target_info.st_ino))
            _write_all(descriptor, manifest)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(package)
        _fsync_directory(root)
        loader = SkillInventoryLoader(workspace, environment)
        inventory = loader.load()
        candidate = inventory.get(name)
        if candidate.source is not source:
            raise SkillCatalogError(
                "package-shadowed",
                "New Skill package is shadowed by a higher-priority package with the same name",
            )
        final_info = package.lstat()
        if (final_info.st_dev, final_info.st_ino) != package_identity:
            raise SkillCatalogError("create-drift", "Skill package changed before commit")
        return SkillAuthoringResult(candidate, root)
    except BaseException:
        _remove_new_package(
            package,
            package_identity,
            () if created_file is None else (created_file,),
        )
        raise


def import_skill(
    workspace: Path,
    source_package: Path,
    *,
    scope: str = "project",
    environment: Mapping[str, str] | None = None,
) -> SkillImportResult:
    """Copy one explicit local package and commit an exact path-free fingerprint lock."""
    source_package = Path(source_package)
    if source_package.is_symlink():
        raise SkillCatalogError("invalid-source", "Skill import source must not be a symlink")
    try:
        source_package = source_package.resolve(strict=True)
    except OSError:
        raise SkillCatalogError("invalid-source", "Skill import source does not exist") from None
    if not source_package.is_dir():
        raise SkillCatalogError("invalid-source", "Skill import source is not a directory")
    manifest, resources = load_skill_package(source_package)
    source, root = skill_root(workspace, scope, environment)
    _ensure_directory_chain(root)
    lock_root = skill_lock_root(workspace, scope, environment)
    _ensure_directory_chain(lock_root)
    package = root / manifest.name
    lock_path = lock_root / f"{manifest.name}.json"
    if lock_path.exists() or lock_path.is_symlink():
        raise SkillCatalogError("lock-exists", f"Skill import lock already exists: {manifest.name}")
    try:
        package.mkdir(mode=0o700)
    except FileExistsError:
        raise SkillCatalogError(
            "package-exists", f"Skill package already exists: {manifest.name}"
        ) from None
    except OSError as error:
        raise SkillCatalogError(
            "create-failed", "Skill package directory could not be created"
        ) from error
    package_info = package.lstat()
    package_identity = (package_info.st_dev, package_info.st_ino)
    lock = SkillImportLock(manifest.name, scope, manifest.fingerprint, resources)
    created_lock: _CreatedFile | None = None
    created_files: list[_CreatedFile] = []
    try:
        created_files.append(_copy_package_file(source_package, package, "SKILL.md"))
        for resource in resources:
            created_files.append(_copy_package_file(source_package, package, resource.path))
        _fsync_package_directories(package)
        imported_manifest, imported_resources = load_skill_package(package)
        _require_lock_match(lock, imported_manifest, imported_resources)
        current_info = package.lstat()
        if (current_info.st_dev, current_info.st_ino) != package_identity:
            raise SkillCatalogError("copy-drift", "Skill import target changed during copy")
        created_lock = _write_exclusive_json(lock_path, lock.as_mapping())
        _fsync_directory(lock_root)
        inventory = SkillInventoryLoader(workspace, environment).load()
        candidate = inventory.get(manifest.name)
        if candidate.source is not source:
            raise SkillCatalogError(
                "package-shadowed",
                "Imported Skill package is shadowed by a higher-priority package",
            )
        final_info = package.lstat()
        if (final_info.st_dev, final_info.st_ino) != package_identity:
            raise SkillCatalogError("copy-drift", "Skill import target changed before commit")
        return SkillImportResult(candidate, lock, lock_path)
    except BaseException:
        if created_lock is not None:
            _unlink_created_file(created_lock)
        _remove_new_package(package, package_identity, tuple(created_files))
        raise


def skill_lock_root(
    workspace: Path,
    scope: str,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the lock directory paired with a scope but outside its scanned skills root."""
    _, root = skill_root(workspace, scope, environment)
    return root.parent / "skill-locks"


def load_skill_lock(
    workspace: Path,
    name: str,
    *,
    scope: str = "project",
    environment: Mapping[str, str] | None = None,
) -> tuple[SkillImportLock, Path]:
    """Read and strictly decode one bounded import lock without mutating it."""
    canonical_skill_name(name)
    path = skill_lock_root(workspace, scope, environment) / f"{name}.json"
    raw = _read_lock_file(path)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise SkillCatalogError(
            "invalid-lock", "Skill import lock is not valid UTF-8 JSON"
        ) from None
    lock = _lock_from_mapping(value)
    if lock.name != name or lock.scope != scope:
        raise SkillCatalogError("invalid-lock", "Skill import lock identity does not match")
    return lock, path


def verify_skill_lock(
    workspace: Path,
    name: str,
    *,
    scope: str = "project",
    environment: Mapping[str, str] | None = None,
) -> SkillLockVerification:
    """Compare one current package with its exact durable import lock."""
    lock, lock_path = load_skill_lock(workspace, name, scope=scope, environment=environment)
    _, root = skill_root(workspace, scope, environment)
    package = root / name
    try:
        manifest, resources = load_skill_package(package)
    except SkillCatalogError as error:
        return SkillLockVerification(lock, lock_path, None, (), False, error.code)
    valid = lock.fingerprint == manifest.fingerprint and lock.resources == resources
    return SkillLockVerification(
        lock,
        lock_path,
        manifest.fingerprint,
        resources,
        valid,
        "match" if valid else "fingerprint-mismatch",
    )


def _ensure_directory_chain(target: Path) -> None:
    missing: list[Path] = []
    current = target
    while not current.exists():
        if current.is_symlink():
            raise SkillCatalogError("invalid-root", "Skill authoring root contains a symlink")
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise SkillCatalogError("invalid-root", "Skill authoring root is not a real directory")
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if directory.is_symlink() or not directory.is_dir():
                raise SkillCatalogError(
                    "invalid-root", "Skill authoring root changed while it was created"
                ) from None
    for directory in (target, target.parent):
        if directory.is_symlink() or not directory.is_dir():
            raise SkillCatalogError("invalid-root", "Skill authoring root is not a real directory")


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("Skill template write made no progress")
        offset += written


def _copy_package_file(source: Path, target: Path, relative: str) -> _CreatedFile:
    raw = read_skill_package_file(source, relative)
    segments = relative.split("/")
    parent = target.joinpath(*segments[:-1])
    if parent != target:
        _ensure_directory_chain(parent)
    path = parent / segments[-1]
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        raise SkillCatalogError("copy-failed", "Skill package copy target changed") from error
    try:
        info = os.fstat(descriptor)
        created = _CreatedFile(path, (info.st_dev, info.st_ino))
        _write_all(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return created


def _fsync_package_directories(package: Path) -> None:
    directories = [package]
    for root, child_directories, _ in os.walk(package, topdown=True, followlinks=False):
        current = Path(root)
        for name in child_directories:
            child = current / name
            if child.is_symlink():
                raise SkillCatalogError("copy-failed", "Copied Skill package contains a symlink")
            directories.append(child)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(package.parent)


def _require_lock_match(
    lock: SkillImportLock,
    manifest: SkillManifest,
    resources: tuple[SkillResource, ...],
) -> None:
    if lock.name != manifest.name or lock.fingerprint != manifest.fingerprint:
        raise SkillCatalogError("copy-drift", "Skill manifest changed during import")
    if lock.resources != resources:
        raise SkillCatalogError("copy-drift", "Skill resources changed during import")


def _write_exclusive_json(path: Path, value: Mapping[str, object]) -> _CreatedFile:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        raise SkillCatalogError(
            "lock-create-failed", "Skill import lock could not be created"
        ) from error
    info = os.fstat(descriptor)
    created = _CreatedFile(path, (info.st_dev, info.st_ino))
    try:
        _write_all(descriptor, raw)
        os.fsync(descriptor)
    except BaseException:
        _unlink_created_file(created)
        raise
    finally:
        os.close(descriptor)
    return created


def _read_lock_file(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SkillCatalogError("missing-lock", "Skill import lock does not exist") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SkillCatalogError("invalid-lock", "Skill import lock is not a regular file")
        if before.st_size > 64 * 1024:
            raise SkillCatalogError("invalid-lock", "Skill import lock exceeds 65536 bytes")
        chunks: list[bytes] = []
        remaining = 64 * 1024 + 1
        while remaining:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(raw) > 64 * 1024 or len(raw) != after.st_size:
            raise SkillCatalogError("invalid-lock", "Skill import lock is oversized or changed")
        if (before.st_dev, before.st_ino, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
        ):
            raise SkillCatalogError("invalid-lock", "Skill import lock changed while reading")
        return raw
    finally:
        os.close(descriptor)


def _lock_from_mapping(value: object) -> SkillImportLock:
    if not isinstance(value, dict) or set(value) != {
        "fingerprint",
        "lock-version",
        "name",
        "resources",
        "scope",
    }:
        raise SkillCatalogError("invalid-lock", "Skill import lock fields are invalid")
    if value["lock-version"] != SKILL_IMPORT_LOCK_VERSION:
        raise SkillCatalogError("invalid-lock", "Skill import lock version is unsupported")
    raw_resources = value["resources"]
    if not isinstance(raw_resources, list):
        raise SkillCatalogError("invalid-lock", "Skill import lock resources are invalid")
    resources: list[SkillResource] = []
    for item in raw_resources:
        if not isinstance(item, dict) or set(item) != {
            "bytes",
            "fingerprint",
            "path",
            "text-readable",
        }:
            raise SkillCatalogError("invalid-lock", "Skill import lock resource is invalid")
        try:
            resources.append(
                SkillResource(
                    item["path"],
                    item["bytes"],
                    item["fingerprint"],
                    item["text-readable"],
                )
            )
        except (TypeError, ValueError, SkillCatalogError):
            raise SkillCatalogError(
                "invalid-lock", "Skill import lock resource is invalid"
            ) from None
    try:
        return SkillImportLock(
            value["name"],
            value["scope"],
            value["fingerprint"],
            tuple(resources),
            value["lock-version"],
        )
    except (TypeError, ValueError, SkillCatalogError):
        raise SkillCatalogError("invalid-lock", "Skill import lock identity is invalid") from None


def _remove_new_package(
    package: Path,
    identity: tuple[int, int],
    created_files: tuple[_CreatedFile, ...],
) -> None:
    if not package.exists() or package.is_symlink():
        return
    try:
        current = package.lstat()
        if (current.st_dev, current.st_ino) != identity:
            return
        for created in reversed(created_files):
            _unlink_created_file(created)
        directories = {
            parent
            for created in created_files
            for parent in created.path.parents
            if parent != package and package in parent.parents
        }
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        package.rmdir()
    except OSError:
        pass


def _unlink_created_file(created: _CreatedFile) -> None:
    try:
        info = created.path.lstat()
        if (info.st_dev, info.st_ino) == created.identity:
            created.path.unlink()
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
