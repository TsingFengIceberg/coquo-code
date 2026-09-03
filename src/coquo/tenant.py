"""Small durable tenant registry shared by network-facing control planes.

The local CLI remains the default single-user product.  This module only
supplies the identity, workspace ownership, and bounded admission facts needed
when an operator explicitly exposes an A2A, marketplace, or worker endpoint.
It never authenticates a user by itself and it never stores bearer values.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import RLock


MAX_TENANTS = 256
MAX_TENANT_ID_BYTES = 128
MAX_TENANT_ACTIVE_TASKS = 128
MAX_TENANT_MARKETPLACE_PACKAGES = 512
MAX_TENANT_TOKEN_BYTES = 4096


class TenantError(ValueError):
    """Raised when tenant scope, quota, or persistence is invalid."""


def validate_tenant_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > MAX_TENANT_ID_BYTES
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
            for character in value
        )
    ):
        raise TenantError("tenant ID is invalid")
    return value


def workspace_fingerprint(workspace: Path) -> str:
    requested = Path(workspace)
    if requested.is_symlink():
        raise TenantError("tenant workspace must not be a symlink")
    try:
        resolved = requested.resolve(strict=True)
    except OSError:
        raise TenantError("tenant workspace is inaccessible") from None
    if not resolved.is_dir():
        raise TenantError("tenant workspace is not a directory")
    return "workspace-v1-" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TenantPolicy:
    tenant_id: str
    workspace_fingerprint: str
    max_active_tasks: int = 8
    max_marketplace_packages: int = 64

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        if not isinstance(
            self.workspace_fingerprint, str
        ) or not self.workspace_fingerprint.startswith("workspace-v1-"):
            raise TenantError("tenant workspace fingerprint is invalid")
        if (
            type(self.max_active_tasks) is not int
            or not 1 <= self.max_active_tasks <= MAX_TENANT_ACTIVE_TASKS
        ):
            raise TenantError("tenant active task quota is invalid")
        if (
            type(self.max_marketplace_packages) is not int
            or not 1 <= self.max_marketplace_packages <= MAX_TENANT_MARKETPLACE_PACKAGES
        ):
            raise TenantError("tenant marketplace quota is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_fingerprint": self.workspace_fingerprint,
            "max_active_tasks": self.max_active_tasks,
            "max_marketplace_packages": self.max_marketplace_packages,
        }


class TenantRegistry:
    """Host-managed tenant ownership and quota configuration.

    The registry has no implicit tenant.  An exposed endpoint must either pass
    a configured tenant or deliberately choose the explicit local default.
    """

    def __init__(self, workspace: Path) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise TenantError("tenant registry workspace is not a directory")
        self.workspace = root
        self.path = root / ".coquo" / "tenants" / "v1" / "policies.json"
        self.tokens_path = root / ".coquo" / "tenants" / "v1" / "token-digests.json"
        self.lock_path = self.path.with_suffix(".lock")
        self.tokens_lock_path = self.tokens_path.with_suffix(".lock")
        self._guard = RLock()

    def configure(self, policy: TenantPolicy) -> TenantPolicy:
        if not isinstance(policy, TenantPolicy):
            raise TenantError("tenant policy is invalid")
        with self._guard, _tenant_file_lock(self.lock_path):
            values = self._read_unlocked()
            if policy.tenant_id not in values and len(values) >= MAX_TENANTS:
                raise TenantError("tenant registry is full")
            values[policy.tenant_id] = policy.as_mapping()
            self._write_unlocked(values)
        return policy

    def resolve(self, tenant_id: str) -> TenantPolicy | None:
        validate_tenant_id(tenant_id)
        with self._guard, _tenant_file_lock(self.lock_path):
            value = self._read_unlocked().get(tenant_id)
        return None if value is None else _policy_from_mapping(value)

    def require_workspace(self, tenant_id: str, fingerprint: str) -> TenantPolicy:
        policy = self.resolve(tenant_id)
        if policy is None:
            raise TenantError("tenant is not configured")
        if policy.workspace_fingerprint != fingerprint:
            raise TenantError("tenant does not own this workspace")
        return policy

    def bind_token(self, tenant_id: str, token: str) -> None:
        """Bind a caller token by digest; the bearer value is never persisted."""
        validate_tenant_id(tenant_id)
        if not isinstance(token, str) or not token or len(token.encode()) > MAX_TENANT_TOKEN_BYTES:
            raise TenantError("tenant token is invalid")
        if self.resolve(tenant_id) is None:
            raise TenantError("tenant is not configured")
        digest = _token_digest(token)
        with self._guard, _tenant_file_lock(self.tokens_lock_path):
            values = self._read_tokens_unlocked()
            existing = values.get(digest)
            if existing is not None and existing != tenant_id:
                raise TenantError("tenant token is already bound")
            values[digest] = tenant_id
            self._write_tokens_unlocked(values)

    def resolve_token(self, token: str) -> TenantPolicy | None:
        if not isinstance(token, str) or not token or len(token.encode()) > MAX_TENANT_TOKEN_BYTES:
            return None
        with self._guard, _tenant_file_lock(self.tokens_lock_path):
            tenant_id = self._read_tokens_unlocked().get(_token_digest(token))
        return None if tenant_id is None else self.resolve(tenant_id)

    def list(self) -> tuple[TenantPolicy, ...]:
        with self._guard, _tenant_file_lock(self.lock_path):
            values = self._read_unlocked()
        return tuple(_policy_from_mapping(values[key]) for key in sorted(values))

    def _read_unlocked(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or len(value) > MAX_TENANTS:
                raise ValueError
            result: dict[str, dict[str, object]] = {}
            for key, item in value.items():
                validate_tenant_id(key)
                policy = _policy_from_mapping(item)
                if policy.tenant_id != key:
                    raise ValueError
                result[key] = policy.as_mapping()
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError, TenantError):
            raise TenantError("tenant registry is invalid") from None

    def _write_unlocked(self, values: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(values, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def _read_tokens_unlocked(self) -> dict[str, str]:
        if not self.tokens_path.exists():
            return {}
        try:
            value = json.loads(self.tokens_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or len(value) > MAX_TENANTS:
                raise ValueError
            if any(
                not isinstance(key, str)
                or len(key) != 64
                or any(character not in "0123456789abcdef" for character in key)
                or not isinstance(tenant, str)
                for key, tenant in value.items()
            ):
                raise ValueError
            for tenant in value.values():
                validate_tenant_id(tenant)
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError, TenantError):
            raise TenantError("tenant token registry is invalid") from None

    def _write_tokens_unlocked(self, values: dict[str, str]) -> None:
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.tokens_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(values, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(self.tokens_path)


class _tenant_file_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> "_tenant_file_lock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.name != "nt":
            import fcntl

            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: object) -> None:
        if self.descriptor is None:
            return
        try:
            if os.name != "nt":
                import fcntl

                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def _policy_from_mapping(value: object) -> TenantPolicy:
    if not isinstance(value, dict) or set(value) != {
        "tenant_id",
        "workspace_fingerprint",
        "max_active_tasks",
        "max_marketplace_packages",
    }:
        raise ValueError
    return TenantPolicy(
        tenant_id=value["tenant_id"],
        workspace_fingerprint=value["workspace_fingerprint"],
        max_active_tasks=value["max_active_tasks"],
        max_marketplace_packages=value["max_marketplace_packages"],
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(b"coquo-tenant-token-v1\0" + token.encode("utf-8")).hexdigest()


__all__ = [
    "TenantError",
    "TenantPolicy",
    "TenantRegistry",
    "validate_tenant_id",
    "workspace_fingerprint",
]
