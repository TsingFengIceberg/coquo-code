"""Strict, bounded loading for declarative SKILL.md packages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping

import yaml


SKILL_MANIFEST_VERSION = 1
SKILL_INVENTORY_VERSION = 2
MAX_SKILL_CANDIDATES = 128
MAX_SKILL_FILE_BYTES = 32 * 1024
MAX_SKILL_FRONTMATTER_BYTES = 4 * 1024
MAX_SKILL_DESCRIPTION_CHARS = 512
MAX_SKILL_ALLOWED_TOOLS = 64
MAX_SKILL_RESOURCES = 64
MAX_SKILL_RESOURCE_BYTES = 64 * 1024
MAX_SKILL_RESOURCE_TOTAL_BYTES = 256 * 1024
MAX_SKILL_RESOURCE_PATH_CHARACTERS = 256
MAX_SKILL_RESOURCE_DIRECTORIES = 128
MAX_SKILL_SEARCH_QUERY_CHARACTERS = 256
MAX_SKILL_SEARCH_RESULTS = 32
_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_FINGERPRINT_DOMAIN = b"leonervis-code-skill-v1\0"
_INVENTORY_DOMAIN = b"leonervis-code-skill-inventory-v2\0"
_RESOURCE_DOMAIN = b"leonervis-code-skill-resource-v1\0"
_FINGERPRINT = re.compile(r"skill-v1-[0-9a-f]{64}\Z")
_RESOURCE_FINGERPRINT = re.compile(r"resource-v1-[0-9a-f]{64}\Z")
_RESOURCE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ALLOWED_FIELDS = frozenset({"manifest-version", "name", "description", "allowed-tools"})


class SkillSourceKind(StrEnum):
    """Closed Skill source precedence, highest first."""

    WORKSPACE_LOCAL = "workspace-local"
    PROJECT_SHARED = "project-shared"
    USER = "user"


@dataclass(frozen=True)
class SkillManifest:
    """Validated declarative metadata and complete bounded instructions."""

    name: str
    description: str
    allowed_tools: tuple[str, ...] | None
    instructions: str
    fingerprint: str
    version: int = SKILL_MANIFEST_VERSION

    def __post_init__(self) -> None:
        if self.version != SKILL_MANIFEST_VERSION or _NAME.fullmatch(self.name) is None:
            raise ValueError("Skill manifest identity is invalid")
        if not self.description or len(self.description) > MAX_SKILL_DESCRIPTION_CHARS:
            raise ValueError("Skill manifest description is invalid")
        if (
            not self.instructions.strip()
            or len(self.instructions.encode("utf-8")) > MAX_SKILL_FILE_BYTES
        ):
            raise ValueError("Skill manifest instructions are invalid")
        if _FINGERPRINT.fullmatch(self.fingerprint) is None:
            raise ValueError("Skill manifest fingerprint is invalid")
        if self.allowed_tools is not None:
            if (
                not isinstance(self.allowed_tools, tuple)
                or len(self.allowed_tools) > MAX_SKILL_ALLOWED_TOOLS
                or len(set(self.allowed_tools)) != len(self.allowed_tools)
                or any(_TOOL_NAME.fullmatch(name) is None for name in self.allowed_tools)
            ):
                raise ValueError("Skill manifest allowed tools are invalid")


@dataclass(frozen=True)
class SkillResource:
    """One bounded package-relative regular file identity."""

    path: str
    byte_count: int
    fingerprint: str
    text_readable: bool

    def __post_init__(self) -> None:
        _validate_resource_path(self.path)
        if type(self.byte_count) is not int or not 0 <= self.byte_count <= MAX_SKILL_RESOURCE_BYTES:
            raise ValueError("Skill resource byte count is invalid")
        if _RESOURCE_FINGERPRINT.fullmatch(self.fingerprint) is None:
            raise ValueError("Skill resource fingerprint is invalid")
        if type(self.text_readable) is not bool:
            raise ValueError("Skill resource text flag is invalid")


@dataclass(frozen=True)
class SkillCandidate:
    """One valid package with source-relative provenance."""

    manifest: SkillManifest
    source: SkillSourceKind
    relative_path: str
    resources: tuple[SkillResource, ...] = ()
    shadowed_by: SkillSourceKind | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SkillManifest) or type(self.source) is not SkillSourceKind:
            raise ValueError("Skill candidate is invalid")
        if self.relative_path != f"{self.manifest.name}/SKILL.md":
            raise ValueError("Skill candidate path is invalid")
        if (
            not isinstance(self.resources, tuple)
            or len(self.resources) > MAX_SKILL_RESOURCES
            or any(not isinstance(resource, SkillResource) for resource in self.resources)
            or len({resource.path for resource in self.resources}) != len(self.resources)
        ):
            raise ValueError("Skill candidate resources are invalid")
        if self.shadowed_by is not None and type(self.shadowed_by) is not SkillSourceKind:
            raise ValueError("Skill candidate shadow source is invalid")

    @property
    def active(self) -> bool:
        return self.shadowed_by is None


@dataclass(frozen=True)
class SkillCatalogIssue:
    """One bounded, non-authoritative catalog diagnostic."""

    source: SkillSourceKind
    relative_path: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if type(self.source) is not SkillSourceKind:
            raise ValueError("Skill catalog issue source is invalid")
        if not self.relative_path or not self.code or not self.code.isascii() or not self.message:
            raise ValueError("Skill catalog issue is invalid")


@dataclass(frozen=True)
class SkillSearchMatch:
    """One deterministic metadata match with bounded explanation facts."""

    candidate: SkillCandidate
    score: int
    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, SkillCandidate):
            raise ValueError("Skill search candidate is invalid")
        if type(self.score) is not int or self.score < 0:
            raise ValueError("Skill search score is invalid")
        if not isinstance(self.terms, tuple) or any(
            not isinstance(term, str) or not term for term in self.terms
        ):
            raise ValueError("Skill search terms are invalid")


@dataclass(frozen=True)
class SkillInventorySnapshot:
    """One immutable view over all valid, invalid, active, and shadowed packages."""

    candidates: tuple[SkillCandidate, ...]
    issues: tuple[SkillCatalogIssue, ...]
    version: int = SKILL_INVENTORY_VERSION

    def __post_init__(self) -> None:
        if self.version != SKILL_INVENTORY_VERSION:
            raise ValueError("unsupported Skill inventory version")
        if not isinstance(self.candidates, tuple) or len(self.candidates) > MAX_SKILL_CANDIDATES:
            raise ValueError("Skill inventory candidates are invalid")
        if not isinstance(self.issues, tuple):
            raise ValueError("Skill inventory issues are invalid")
        if any(not isinstance(candidate, SkillCandidate) for candidate in self.candidates):
            raise ValueError("Skill inventory contains an invalid candidate")
        if any(not isinstance(issue, SkillCatalogIssue) for issue in self.issues):
            raise ValueError("Skill inventory contains an invalid issue")
        active_names = [
            candidate.manifest.name for candidate in self.candidates if candidate.active
        ]
        if len(active_names) != len(set(active_names)):
            raise ValueError("Skill inventory contains duplicate active names")

    @property
    def active(self) -> tuple[SkillCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.active)

    @property
    def snapshot_id(self) -> str:
        payload = {
            "candidates": [
                {
                    "fingerprint": item.manifest.fingerprint,
                    "name": item.manifest.name,
                    "path": item.relative_path,
                    "resources": [
                        {
                            "bytes": resource.byte_count,
                            "fingerprint": resource.fingerprint,
                            "path": resource.path,
                            "text_readable": resource.text_readable,
                        }
                        for resource in item.resources
                    ],
                    "shadowed_by": (None if item.shadowed_by is None else item.shadowed_by.value),
                    "source": item.source.value,
                }
                for item in self.candidates
            ],
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.relative_path,
                    "source": issue.source.value,
                }
                for issue in self.issues
            ],
            "version": self.version,
        }
        digest = hashlib.sha256(_INVENTORY_DOMAIN + _canonical_json(payload)).hexdigest()
        return f"skills-v{self.version}-{digest}"

    def get(self, name: str) -> SkillCandidate:
        for candidate in self.active:
            if candidate.manifest.name == name:
                return candidate
        raise SkillCatalogError("unknown-skill", f"unknown Skill: {name}")

    def resource(self, name: str, path: str) -> SkillResource:
        candidate = self.get(name)
        for resource in candidate.resources:
            if resource.path == path:
                return resource
        raise SkillCatalogError("unknown-resource", f"unknown Skill resource: {path}")

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        active_only: bool = True,
    ) -> tuple[SkillSearchMatch, ...]:
        """Search deterministic literal metadata and retain score/source diagnostics."""
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_SKILL_SEARCH_QUERY_CHARACTERS
        ):
            raise SkillCatalogError(
                "invalid-search", "Skill search query must contain 1 to 256 characters"
            )
        if type(limit) is not int or not 1 <= limit <= MAX_SKILL_SEARCH_RESULTS:
            raise SkillCatalogError(
                "invalid-search-limit",
                f"Skill search limit must be between 1 and {MAX_SKILL_SEARCH_RESULTS}",
            )
        if type(active_only) is not bool:
            raise ValueError("Skill search active-only flag is invalid")
        terms = tuple(part for part in query.casefold().split() if part)
        candidates = self.active if active_only else self.candidates
        matches: list[SkillSearchMatch] = []
        for candidate in candidates:
            manifest = candidate.manifest
            haystack = f"{manifest.name} {manifest.description}".casefold()
            if not all(term in haystack for term in terms):
                continue
            matches.append(
                SkillSearchMatch(
                    candidate=candidate,
                    score=sum(haystack.count(term) for term in terms),
                    terms=terms,
                )
            )
        return tuple(
            sorted(
                matches,
                key=lambda match: (
                    -match.score,
                    match.candidate.manifest.name,
                    match.candidate.source.value,
                ),
            )[:limit]
        )


class SkillInventoryLoader:
    """Load exactly three roots without ancestor or compatibility scanning."""

    def __init__(self, workspace: Path, environment: Mapping[str, str] | None = None) -> None:
        self._workspace = workspace.resolve(strict=True)
        env = os.environ if environment is None else environment
        config_home = Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        self._roots = (
            (SkillSourceKind.WORKSPACE_LOCAL, self._workspace / ".leonervis-code" / "skills"),
            (SkillSourceKind.PROJECT_SHARED, self._workspace / ".agents" / "skills"),
            (SkillSourceKind.USER, config_home / "leonervis-code" / "skills"),
        )

    @property
    def roots(self) -> tuple[tuple[SkillSourceKind, Path], ...]:
        return self._roots

    def load(self) -> SkillInventorySnapshot:
        candidates: list[SkillCandidate] = []
        issues: list[SkillCatalogIssue] = []
        seen_names: dict[str, SkillSourceKind] = {}
        total = 0
        for source, root in self._roots:
            if not root.exists():
                continue
            if root.is_symlink() or not root.is_dir():
                issues.append(
                    _issue(source, ".", "invalid-root", "Skill root is not a real directory")
                )
                continue
            remaining = MAX_SKILL_CANDIDATES - total
            try:
                entries = heapq.nsmallest(
                    remaining + 1,
                    root.iterdir(),
                    key=lambda path: path.name,
                )
            except OSError:
                issues.append(_issue(source, ".", "read-failed", "Skill root could not be listed"))
                continue
            for package in entries:
                total += 1
                relative = f"{package.name}/SKILL.md"
                if total > MAX_SKILL_CANDIDATES:
                    issues.append(
                        _issue(
                            source,
                            relative,
                            "inventory-limit",
                            "Skill inventory exceeds 128 candidates",
                        )
                    )
                    return SkillInventorySnapshot(tuple(candidates), tuple(issues))
                try:
                    manifest, resources = _load_package(package)
                except SkillCatalogError as error:
                    issues.append(_issue(source, relative, error.code, str(error)))
                    continue
                winner = seen_names.get(manifest.name)
                candidates.append(
                    SkillCandidate(
                        manifest=manifest,
                        source=source,
                        relative_path=relative,
                        resources=resources,
                        shadowed_by=winner,
                    )
                )
                if winner is None:
                    seen_names[manifest.name] = source
        return SkillInventorySnapshot(tuple(candidates), tuple(issues))

    def read_resource(
        self,
        *,
        inventory_id: str,
        name: str,
        skill_fingerprint: str,
        path: str,
        resource_fingerprint: str,
    ) -> str:
        """Read one exact text resource after reloading and matching the inventory."""
        inventory = self.load()
        if inventory.snapshot_id != inventory_id:
            raise SkillCatalogError(
                "stale-inventory", "Skill inventory changed before resource reading"
            )
        candidate = inventory.get(name)
        if candidate.manifest.fingerprint != skill_fingerprint:
            raise SkillCatalogError(
                "stale-skill", "Skill fingerprint changed before resource reading"
            )
        resource = inventory.resource(name, path)
        if resource.fingerprint != resource_fingerprint:
            raise SkillCatalogError(
                "stale-resource", "Skill resource fingerprint changed before reading"
            )
        if not resource.text_readable:
            raise SkillCatalogError("binary-resource", "Skill resource is not UTF-8 text")
        root = next(root for source, root in self._roots if source is candidate.source)
        raw = _read_package_file(
            root / candidate.manifest.name,
            path.split("/"),
            max_bytes=MAX_SKILL_RESOURCE_BYTES,
            label="Skill resource",
        )
        actual = _resource_fingerprint(path, raw)
        if actual != resource.fingerprint or len(raw) != resource.byte_count:
            raise SkillCatalogError("file-drift", "Skill resource changed while it was being read")
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SkillCatalogError(
                "binary-resource", "Skill resource is not UTF-8 text"
            ) from error


class SkillCatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_package(package: Path) -> tuple[SkillManifest, tuple[SkillResource, ...]]:
    if package.is_symlink() or not package.is_dir():
        raise SkillCatalogError("invalid-package", "Skill package is not a real directory")
    if _NAME.fullmatch(package.name) is None:
        raise SkillCatalogError("invalid-name", "Skill directory name is invalid")
    raw = _read_skill_file(package)
    if b"\r" in raw:
        raise SkillCatalogError("invalid-newline", "SKILL.md must use LF newlines")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SkillCatalogError("invalid-utf8", "SKILL.md must be strict UTF-8") from error
    metadata, instructions = _split_frontmatter(text)
    manifest = _parse_metadata(metadata, package.name)
    identity = {
        "allowed_tools": manifest[2],
        "description": manifest[1],
        "instructions": instructions,
        "name": manifest[0],
        "version": SKILL_MANIFEST_VERSION,
    }
    digest = hashlib.sha256(_FINGERPRINT_DOMAIN + _canonical_json(identity)).hexdigest()
    manifest = SkillManifest(
        name=manifest[0],
        description=manifest[1],
        allowed_tools=manifest[2],
        instructions=instructions,
        fingerprint=f"skill-v{SKILL_MANIFEST_VERSION}-{digest}",
    )
    return manifest, _load_resources(package)


def load_skill_package(package: Path) -> tuple[SkillManifest, tuple[SkillResource, ...]]:
    """Validate one explicit package directory using the canonical bounded loader."""
    return _load_package(Path(package))


def read_skill_package_file(package: Path, path: str) -> bytes:
    """Read one canonical package file without following any package-internal symlink."""
    package = Path(package)
    if path == "SKILL.md":
        return _read_skill_file(package)
    _validate_resource_path(path)
    return _read_package_file(
        package,
        tuple(path.split("/")),
        max_bytes=MAX_SKILL_RESOURCE_BYTES,
        label=f"Skill resource {path}",
    )


def canonical_skill_name(value: str) -> str:
    """Validate one portable Skill package name without rewriting it."""
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise SkillCatalogError("invalid-name", "Skill name is invalid")
    return value


def _read_skill_file(package: Path) -> bytes:
    return _read_package_file(
        package,
        ("SKILL.md",),
        max_bytes=MAX_SKILL_FILE_BYTES,
        label="SKILL.md",
    )


def _read_package_file(
    package: Path,
    segments: tuple[str, ...] | list[str],
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    if not segments or any(not isinstance(segment, str) for segment in segments):
        raise SkillCatalogError("invalid-resource-path", f"{label} path is invalid")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(package, directory_flags)
    except OSError as error:
        raise SkillCatalogError(
            "invalid-package", "Skill package could not be opened safely"
        ) from error
    try:
        for segment in segments[:-1]:
            try:
                next_fd = os.open(segment, directory_flags, dir_fd=directory_fd)
            except OSError as error:
                raise SkillCatalogError(
                    "invalid-resource-path", f"{label} parent is not a real directory"
                ) from error
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            file_fd = os.open(segments[-1], file_flags, dir_fd=directory_fd)
        except OSError as error:
            code = "missing-manifest" if label == "SKILL.md" else "missing-resource"
            raise SkillCatalogError(code, f"{label} is missing or is not a real file") from error
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                code = "missing-manifest" if label == "SKILL.md" else "invalid-resource"
                raise SkillCatalogError(code, f"{label} is not a real file")
            if before.st_size > max_bytes:
                raise SkillCatalogError("file-limit", f"{label} exceeds {max_bytes} bytes")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(file_fd, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(file_fd)
            if len(raw) > max_bytes:
                raise SkillCatalogError("file-limit", f"{label} exceeds {max_bytes} bytes")
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or len(raw) != after.st_size:
                raise SkillCatalogError("file-drift", f"{label} changed while it was being read")
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


def _load_resources(package: Path) -> tuple[SkillResource, ...]:
    resources: list[SkillResource] = []
    pending: list[tuple[str, ...]] = [()]
    directory_count = 0
    total_bytes = 0
    while pending:
        prefix = pending.pop()
        directory_count += 1
        if directory_count > MAX_SKILL_RESOURCE_DIRECTORIES:
            raise SkillCatalogError(
                "resource-directory-limit",
                f"Skill package exceeds {MAX_SKILL_RESOURCE_DIRECTORIES} directories",
            )
        for name, metadata in _list_package_directory(package, prefix):
            segments = prefix + (name,)
            relative = "/".join(segments)
            _validate_resource_path(relative)
            if stat.S_ISLNK(metadata.st_mode):
                raise SkillCatalogError(
                    "resource-symlink", "Skill package resources must not contain symlinks"
                )
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(segments)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise SkillCatalogError(
                    "invalid-resource", "Skill package resources must be regular files"
                )
            if not prefix and name == "SKILL.md":
                continue
            if len(resources) >= MAX_SKILL_RESOURCES:
                raise SkillCatalogError(
                    "resource-count-limit",
                    f"Skill package exceeds {MAX_SKILL_RESOURCES} resources",
                )
            raw = _read_package_file(
                package,
                segments,
                max_bytes=MAX_SKILL_RESOURCE_BYTES,
                label=f"Skill resource {relative}",
            )
            total_bytes += len(raw)
            if total_bytes > MAX_SKILL_RESOURCE_TOTAL_BYTES:
                raise SkillCatalogError(
                    "resource-total-limit",
                    f"Skill package resources exceed {MAX_SKILL_RESOURCE_TOTAL_BYTES} bytes",
                )
            try:
                raw.decode("utf-8", errors="strict")
                text_readable = True
            except UnicodeDecodeError:
                text_readable = False
            resources.append(
                SkillResource(
                    path=relative,
                    byte_count=len(raw),
                    fingerprint=_resource_fingerprint(relative, raw),
                    text_readable=text_readable,
                )
            )
    return tuple(sorted(resources, key=lambda resource: resource.path))


def _list_package_directory(
    package: Path, segments: tuple[str, ...]
) -> tuple[tuple[str, os.stat_result], ...]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(package, directory_flags)
    except OSError as error:
        raise SkillCatalogError(
            "invalid-package", "Skill package could not be opened safely"
        ) from error
    try:
        for segment in segments:
            try:
                next_fd = os.open(segment, directory_flags, dir_fd=directory_fd)
            except OSError as error:
                raise SkillCatalogError(
                    "resource-read-failed", "Skill resource directory changed while listing"
                ) from error
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            names = sorted(os.listdir(directory_fd), reverse=True)
            return tuple(
                (
                    name,
                    os.stat(name, dir_fd=directory_fd, follow_symlinks=False),
                )
                for name in names
            )
        except OSError as error:
            raise SkillCatalogError(
                "resource-read-failed", "Skill package resources could not be listed"
            ) from error
    finally:
        os.close(directory_fd)


def _validate_resource_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > MAX_SKILL_RESOURCE_PATH_CHARACTERS
        or path.startswith("/")
        or "\\" in path
    ):
        raise SkillCatalogError("invalid-resource-path", "Skill resource path is invalid")
    segments = path.split("/")
    if any(
        segment in {"", ".", ".."} or _RESOURCE_SEGMENT.fullmatch(segment) is None
        for segment in segments
    ):
        raise SkillCatalogError("invalid-resource-path", "Skill resource path is invalid")


def _resource_fingerprint(path: str, raw: bytes) -> str:
    _validate_resource_path(path)
    digest = hashlib.sha256(_RESOURCE_DOMAIN + path.encode("utf-8") + b"\0" + raw).hexdigest()
    return f"resource-v1-{digest}"


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise SkillCatalogError("missing-frontmatter", "SKILL.md requires YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise SkillCatalogError("invalid-frontmatter", "SKILL.md frontmatter is not closed")
    metadata = text[4:end]
    if len(metadata.encode("utf-8")) > MAX_SKILL_FRONTMATTER_BYTES:
        raise SkillCatalogError("frontmatter-limit", "Skill frontmatter exceeds 4096 bytes")
    instructions = text[end + 5 :]
    if not instructions.strip():
        raise SkillCatalogError("empty-instructions", "Skill instructions must not be blank")
    return metadata, instructions


def _parse_metadata(metadata: str, directory_name: str) -> tuple[str, str, tuple[str, ...] | None]:
    try:
        value = yaml.safe_load(metadata)
    except yaml.YAMLError as error:
        raise SkillCatalogError("invalid-yaml", "Skill frontmatter is invalid YAML") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SkillCatalogError("invalid-frontmatter", "Skill frontmatter must be a mapping")
    unknown = set(value) - _ALLOWED_FIELDS
    if unknown:
        raise SkillCatalogError("unknown-field", "Skill frontmatter contains unknown fields")
    if value.get("manifest-version") != SKILL_MANIFEST_VERSION:
        raise SkillCatalogError("unsupported-version", "Skill manifest-version must be 1")
    name = value.get("name")
    if not isinstance(name, str) or _NAME.fullmatch(name) is None or name != directory_name:
        raise SkillCatalogError("invalid-name", "Skill name must match its package directory")
    description = value.get("description")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > MAX_SKILL_DESCRIPTION_CHARS
    ):
        raise SkillCatalogError(
            "invalid-description", "Skill description must contain 1 to 512 characters"
        )
    raw_tools = value.get("allowed-tools", None)
    if raw_tools is None:
        allowed_tools = None
    elif not isinstance(raw_tools, list) or len(raw_tools) > MAX_SKILL_ALLOWED_TOOLS:
        raise SkillCatalogError(
            "invalid-allowed-tools", "allowed-tools must be a list of at most 64 tool names"
        )
    else:
        if any(
            not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None for name in raw_tools
        ):
            raise SkillCatalogError(
                "invalid-allowed-tools", "allowed-tools contains an invalid tool name"
            )
        if len(set(raw_tools)) != len(raw_tools):
            raise SkillCatalogError("invalid-allowed-tools", "allowed-tools contains duplicates")
        allowed_tools = tuple(raw_tools)
    return name, description, allowed_tools


def _issue(source: SkillSourceKind, path: str, code: str, message: str) -> SkillCatalogIssue:
    return SkillCatalogIssue(source, path, code, message)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
