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
SKILL_INVENTORY_VERSION = 1
MAX_SKILL_CANDIDATES = 128
MAX_SKILL_FILE_BYTES = 32 * 1024
MAX_SKILL_FRONTMATTER_BYTES = 4 * 1024
MAX_SKILL_DESCRIPTION_CHARS = 512
MAX_SKILL_ALLOWED_TOOLS = 64
_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_FINGERPRINT_DOMAIN = b"leonervis-code-skill-v1\0"
_INVENTORY_DOMAIN = b"leonervis-code-skill-inventory-v1\0"
_FINGERPRINT = re.compile(r"skill-v1-[0-9a-f]{64}\Z")
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
class SkillCandidate:
    """One valid package with source-relative provenance."""

    manifest: SkillManifest
    source: SkillSourceKind
    relative_path: str
    shadowed_by: SkillSourceKind | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SkillManifest) or type(self.source) is not SkillSourceKind:
            raise ValueError("Skill candidate is invalid")
        if self.relative_path != f"{self.manifest.name}/SKILL.md":
            raise ValueError("Skill candidate path is invalid")
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
                    manifest = _load_package(package)
                except SkillCatalogError as error:
                    issues.append(_issue(source, relative, error.code, str(error)))
                    continue
                winner = seen_names.get(manifest.name)
                candidates.append(
                    SkillCandidate(
                        manifest=manifest,
                        source=source,
                        relative_path=relative,
                        shadowed_by=winner,
                    )
                )
                if winner is None:
                    seen_names[manifest.name] = source
        return SkillInventorySnapshot(tuple(candidates), tuple(issues))


class SkillCatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_package(package: Path) -> SkillManifest:
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
    return SkillManifest(
        name=manifest[0],
        description=manifest[1],
        allowed_tools=manifest[2],
        instructions=instructions,
        fingerprint=f"skill-v{SKILL_MANIFEST_VERSION}-{digest}",
    )


def _read_skill_file(package: Path) -> bytes:
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
        try:
            file_fd = os.open("SKILL.md", file_flags, dir_fd=directory_fd)
        except OSError as error:
            raise SkillCatalogError(
                "missing-manifest", "SKILL.md is missing or is not a real file"
            ) from error
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise SkillCatalogError(
                    "missing-manifest", "SKILL.md is missing or is not a real file"
                )
            if before.st_size > MAX_SKILL_FILE_BYTES:
                raise SkillCatalogError("file-limit", "SKILL.md exceeds 32768 bytes")
            chunks: list[bytes] = []
            remaining = MAX_SKILL_FILE_BYTES + 1
            while remaining:
                chunk = os.read(file_fd, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(file_fd)
            if len(raw) > MAX_SKILL_FILE_BYTES:
                raise SkillCatalogError("file-limit", "SKILL.md exceeds 32768 bytes")
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
                raise SkillCatalogError("file-drift", "SKILL.md changed while it was being read")
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


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
