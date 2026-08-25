"""Workspace-local switches for long-term semantic memory.

The configuration is deliberately separate from provider profiles: memory is a
Host capability, not an endpoint or model property.  This slice keeps the
master switch disabled by default and does not make a model call.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import stat
from threading import RLock
from typing import Any

from coquo.memory import MemoryError, MemoryRecallMode, MemoryRetrievalMode, MemoryWriteMode

MEMORY_CONFIG_SCHEMA_VERSION = 2
MEMORY_CONFIG_LEGACY_SCHEMA_VERSION = 1
MEMORY_CONFIG_MAX_BYTES = 32 * 1024


@dataclass(frozen=True)
class MemoryConfig:
    """Configured and validated Host memory policy."""

    enabled: bool = False
    recall: MemoryRecallMode = MemoryRecallMode.OFF
    write: MemoryWriteMode = MemoryWriteMode.OFF
    retrieval: MemoryRetrievalMode = MemoryRetrievalMode.TEXT
    tools: bool = False
    provider: str = "local"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.tools) is not bool:
            raise MemoryError("memory enabled/tools must be boolean")
        if not isinstance(self.recall, MemoryRecallMode):
            raise MemoryError("memory recall mode is invalid")
        if not isinstance(self.write, MemoryWriteMode):
            raise MemoryError("memory write mode is invalid")
        if not isinstance(self.retrieval, MemoryRetrievalMode):
            raise MemoryError("memory retrieval mode is invalid")
        if self.provider != "local":
            raise MemoryError("only the local memory provider is available in this slice")

    @property
    def effective_recall(self) -> MemoryRecallMode:
        return self.recall if self.enabled else MemoryRecallMode.OFF

    @property
    def effective_write(self) -> MemoryWriteMode:
        return self.write if self.enabled else MemoryWriteMode.OFF

    @property
    def effective_tools(self) -> bool:
        return self.tools if self.enabled else False

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": MEMORY_CONFIG_SCHEMA_VERSION,
            "enabled": self.enabled,
            "recall": self.recall.value,
            "write": self.write.value,
            "retrieval": self.retrieval.value,
            "tools": self.tools,
            "provider": self.provider,
        }

    @classmethod
    def from_mapping(cls, value: Any) -> "MemoryConfig":
        if not isinstance(value, dict):
            raise MemoryError("memory configuration must be an object")
        common = {
            "schema_version",
            "enabled",
            "recall",
            "write",
            "tools",
            "provider",
        }
        version = value.get("schema_version")
        if version == MEMORY_CONFIG_LEGACY_SCHEMA_VERSION and set(value) in (
            common,
            common | {"retrieval"},
        ):
            try:
                retrieval = MemoryRetrievalMode(value.get("retrieval", "text"))
            except (ValueError, TypeError):
                raise MemoryError("memory recall, write, or retrieval mode is invalid") from None
        elif version == MEMORY_CONFIG_SCHEMA_VERSION and set(value) == common | {"retrieval"}:
            try:
                retrieval = MemoryRetrievalMode(value["retrieval"])
            except (ValueError, TypeError):
                raise MemoryError("memory recall, write, or retrieval mode is invalid") from None
        else:
            raise MemoryError("memory configuration schema is invalid")
        try:
            recall = MemoryRecallMode(value["recall"])
            write = MemoryWriteMode(value["write"])
        except (ValueError, TypeError):
            raise MemoryError("memory recall, write, or retrieval mode is invalid") from None
        return cls(
            enabled=value["enabled"],
            recall=recall,
            write=write,
            retrieval=retrieval,
            tools=value["tools"],
            provider=value["provider"],
        )


class MemoryConfigStore:
    """Atomically read or update one workspace-local memory configuration."""

    def __init__(self, workspace: Path) -> None:
        resolved = Path(workspace).resolve(strict=True)
        if not resolved.is_dir() or Path(workspace).is_symlink():
            raise MemoryError("workspace must be an existing non-symlink directory")
        self.workspace = resolved
        self.root = resolved / ".coquo" / "memory"
        self.path = self.root / "config.json"
        self.lock_path = self.root / ".memory-config.lock"
        self._lock = RLock()

    def load(self) -> MemoryConfig:
        with self._lock:
            if not self.path.exists():
                return MemoryConfig()
            if self.path.is_symlink() or not self.path.is_file():
                raise MemoryError("memory configuration path must be a regular file")
            try:
                raw = self.path.read_bytes()
            except OSError:
                raise MemoryError("memory configuration is not readable") from None
            if len(raw) > MEMORY_CONFIG_MAX_BYTES:
                raise MemoryError("memory configuration exceeds its size limit")
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise MemoryError("memory configuration is invalid JSON") from None
            return MemoryConfig.from_mapping(value)

    def save(self, config: MemoryConfig) -> MemoryConfig:
        if not isinstance(config, MemoryConfig):
            raise MemoryError("memory configuration is invalid")
        raw = (
            json.dumps(
                config.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
        )
        if len(raw) > MEMORY_CONFIG_MAX_BYTES:
            raise MemoryError("memory configuration exceeds its size limit")
        with self._transaction():
            self._save_locked(raw)
        return config

    def _save_locked(self, raw: bytes) -> None:
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if os.write(descriptor, raw) != len(raw):
                    raise OSError("memory configuration write was incomplete")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.path)
            _fsync_directory(self.root)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise MemoryError("memory configuration could not be written") from None

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._ensure_root()
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except OSError:
                raise MemoryError("memory configuration lock could not be opened") from None
            stream = None
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise MemoryError("memory configuration lock must be a regular file")
                os.fchmod(descriptor, 0o600)
                stream = os.fdopen(descriptor, "a+b")
                descriptor = -1
                _lock_stream(stream)
                yield
            finally:
                if stream is not None:
                    _unlock_stream(stream)
                    stream.close()
                if descriptor != -1:
                    os.close(descriptor)

    def update(
        self,
        *,
        enabled: bool | None = None,
        recall: MemoryRecallMode | None = None,
        write: MemoryWriteMode | None = None,
        retrieval: MemoryRetrievalMode | None = None,
        tools: bool | None = None,
    ) -> MemoryConfig:
        with self._transaction():
            current = self.load()
            updated = replace(
                current,
                enabled=current.enabled if enabled is None else enabled,
                recall=current.recall if recall is None else recall,
                write=current.write if write is None else write,
                retrieval=current.retrieval if retrieval is None else retrieval,
                tools=current.tools if tools is None else tools,
            )
            raw = (
                json.dumps(
                    updated.to_mapping(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            if len(raw) > MEMORY_CONFIG_MAX_BYTES:
                raise MemoryError("memory configuration exceeds its size limit")
            self._save_locked(raw)
            return updated

    def _ensure_root(self) -> None:
        parent = self.root.parent
        try:
            parent.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise MemoryError(".coquo could not be created") from None
        if parent.is_symlink() or not parent.is_dir():
            raise MemoryError(".coquo must be a regular directory")
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise MemoryError("memory directory must be a regular directory")
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise MemoryError("memory directory could not be created") from None
        if self.root.is_symlink() or not self.root.is_dir():
            raise MemoryError("memory directory must be a regular directory")
        try:
            os.chmod(parent, stat.S_IRWXU)
            os.chmod(self.root, stat.S_IRWXU)
        except OSError:
            raise MemoryError("memory directory permissions could not be secured") from None


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise MemoryError("memory directory could not be opened") from None
    try:
        os.fsync(descriptor)
    except OSError:
        raise MemoryError("memory directory could not be synchronized") from None
    finally:
        os.close(descriptor)


def _lock_stream(stream) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except OSError:
        raise MemoryError("memory configuration lock could not be acquired") from None


def _unlock_stream(stream) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        raise MemoryError("memory configuration lock could not be released") from None
