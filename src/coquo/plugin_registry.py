"""Local plugin manifests and Host-gated invocation contracts.

Plugins are discovered as immutable manifest packages.  Coquo never imports a
plugin into the agent process: a Host integration supplies a sandboxed direct-
argv executor and an approval callback.  This keeps external extensions from
changing ToolSet, permissions, provider routing, or model authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Callable


PLUGIN_MANIFEST_VERSION = 1
MAX_PLUGIN_MANIFEST_BYTES = 32 * 1024
MAX_PLUGIN_NAME_CHARACTERS = 64
MAX_PLUGIN_VERSION_CHARACTERS = 32
MAX_PLUGIN_ENTRYPOINT_ARGUMENTS = 32
MAX_PLUGIN_ARGUMENT_CHARACTERS = 512
MAX_PLUGINS = 128
_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9.-]+)?\Z")
_ALLOWED_CAPABILITIES = frozenset(
    {"workspace-read", "workspace-write", "command", "network-read", "network-write"}
)
_DANGEROUS_CAPABILITIES = frozenset({"workspace-write", "command", "network-read", "network-write"})


class PluginRegistryError(RuntimeError):
    """Raised when a plugin manifest or lifecycle operation is invalid."""


class PluginStatus(StrEnum):
    DISCOVERED = "discovered"
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str
    entrypoint: tuple[str, ...]
    capabilities: frozenset[str]
    digest: str
    manifest_version: int = PLUGIN_MANIFEST_VERSION

    def __post_init__(self) -> None:
        if self.manifest_version != PLUGIN_MANIFEST_VERSION or _NAME.fullmatch(self.name) is None:
            raise ValueError("plugin manifest identity is invalid")
        if _VERSION.fullmatch(self.version) is None:
            raise ValueError("plugin version is invalid")
        if not self.description or len(self.description) > 512:
            raise ValueError("plugin description is invalid")
        if (
            not isinstance(self.entrypoint, tuple)
            or not 1 <= len(self.entrypoint) <= MAX_PLUGIN_ENTRYPOINT_ARGUMENTS
        ):
            raise ValueError("plugin entrypoint is invalid")
        if any(
            not isinstance(argument, str)
            or not argument
            or len(argument) > MAX_PLUGIN_ARGUMENT_CHARACTERS
            or "\x00" in argument
            or "\n" in argument
            or "\r" in argument
            for argument in self.entrypoint
        ):
            raise ValueError("plugin entrypoint argument is invalid")
        if not self.capabilities or not self.capabilities <= _ALLOWED_CAPABILITIES:
            raise ValueError("plugin capabilities are invalid")
        if not re.fullmatch(r"[0-9a-f]{64}\Z", self.digest):
            raise ValueError("plugin digest is invalid")

    @classmethod
    def from_mapping(cls, value: object, *, digest: str) -> "PluginManifest":
        if not isinstance(value, dict):
            raise PluginRegistryError("plugin manifest must be an object")
        if set(value) != {
            "manifest_version",
            "name",
            "version",
            "description",
            "entrypoint",
            "capabilities",
        }:
            raise PluginRegistryError("plugin manifest fields are invalid")
        try:
            manifest = cls(
                name=value["name"],
                version=value["version"],
                description=value["description"],
                entrypoint=tuple(value["entrypoint"]),
                capabilities=frozenset(value["capabilities"]),
                digest=digest,
                manifest_version=value["manifest_version"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PluginRegistryError("plugin manifest is invalid") from error
        return manifest

    def as_mapping(self) -> dict[str, object]:
        return {
            "capabilities": sorted(self.capabilities),
            "description": self.description,
            "entrypoint": list(self.entrypoint),
            "manifest_version": self.manifest_version,
            "name": self.name,
            "version": self.version,
        }


@dataclass(frozen=True)
class PluginInfo:
    manifest: PluginManifest
    root: Path
    status: PluginStatus


@dataclass(frozen=True)
class PluginInvocation:
    plugin: PluginManifest
    argv: tuple[str, ...]
    timeout_seconds: float
    approved: bool


@dataclass(frozen=True)
class PluginExecutionResult:
    plugin_name: str
    outcome: str
    output: str = ""
    error: str | None = None


class PluginRegistry:
    """Discover and explicitly install local manifest-only plugins."""

    def __init__(self, workspace: Path, *, user_root: Path | None = None) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise PluginRegistryError("workspace is not a directory")
        self.workspace = root
        self.root = root / ".coquo" / "plugins" / "v1"
        self.user_root = None if user_root is None else Path(user_root).resolve()

    def install(self, source: Path) -> PluginInfo:
        package = Path(source).resolve(strict=True)
        if package.is_symlink() or not package.is_dir():
            raise PluginRegistryError("plugin source must be a real directory")
        manifest_path = package / "plugin.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PluginRegistryError("plugin.json is missing")
        raw = manifest_path.read_bytes()
        if len(raw) > MAX_PLUGIN_MANIFEST_BYTES:
            raise PluginRegistryError("plugin manifest exceeds the byte limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PluginRegistryError("plugin manifest is not UTF-8 JSON") from error
        digest = hashlib.sha256(raw).hexdigest()
        manifest = PluginManifest.from_mapping(value, digest=digest)
        destination = self.root / manifest.name
        if destination.exists():
            raise PluginRegistryError("plugin is already installed")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(package, destination, symlinks=False)
        return PluginInfo(manifest, destination, PluginStatus.DISCOVERED)

    def list(self) -> tuple[PluginInfo, ...]:
        roots = [self.root]
        if self.user_root is not None:
            roots.append(self.user_root)
        infos: list[PluginInfo] = []
        for root in roots:
            if not root.exists():
                continue
            for package in sorted(root.iterdir(), key=lambda item: item.name):
                if len(infos) >= MAX_PLUGINS:
                    return tuple(infos)
                try:
                    info = self._read(package)
                except PluginRegistryError:
                    continue
                if not any(item.manifest.name == info.manifest.name for item in infos):
                    infos.append(info)
        return tuple(infos)

    def _read(self, package: Path) -> PluginInfo:
        if package.is_symlink() or not package.is_dir():
            raise PluginRegistryError("plugin package is invalid")
        path = package / "plugin.json"
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw.decode("utf-8"))
        return PluginInfo(
            PluginManifest.from_mapping(value, digest=digest), package, PluginStatus.DISCOVERED
        )


class PluginRunner:
    """Prepare and run one plugin only through Host-supplied process controls."""

    def __init__(
        self,
        *,
        executor: Callable[[tuple[str, ...], float], object],
        approve: Callable[[PluginManifest], bool] | None = None,
    ) -> None:
        if not callable(executor):
            raise ValueError("plugin executor callback is required")
        self.executor = executor
        self.approve = approve

    def invoke(
        self, plugin: PluginManifest, *, timeout_seconds: float = 30.0
    ) -> PluginExecutionResult:
        if not isinstance(plugin, PluginManifest):
            raise PluginRegistryError("plugin manifest is invalid")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 300
        ):
            raise PluginRegistryError("plugin timeout must be between 0 and 300 seconds")
        dangerous = bool(plugin.capabilities & _DANGEROUS_CAPABILITIES)
        if dangerous and (self.approve is None or not self.approve(plugin)):
            return PluginExecutionResult(plugin.name, "denied", error="host approval required")
        try:
            result = self.executor(plugin.entrypoint, float(timeout_seconds))
        except Exception as error:
            return PluginExecutionResult(plugin.name, "failed", error=type(error).__name__)
        output = getattr(result, "output", result if isinstance(result, str) else "")
        return PluginExecutionResult(plugin.name, "completed", output=str(output))


__all__ = [
    "PLUGIN_MANIFEST_VERSION",
    "PluginExecutionResult",
    "PluginInfo",
    "PluginInvocation",
    "PluginManifest",
    "PluginRegistry",
    "PluginRegistryError",
    "PluginRunner",
    "PluginStatus",
]
