"""Immutable extension-tool contracts and model-visible tool-set snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re

from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.core.permissions import PermissionAction


EXTENSION_TOOL_CONTRACT_VERSION = 1
TOOL_REGISTRY_SNAPSHOT_VERSION = 1
TOOL_SET_SNAPSHOT_VERSION = 1
_SOURCE_NAME = re.compile(r"[a-z][a-z0-9._-]*\Z")
_REGISTRY_ID = re.compile(r"registry-v1-[0-9a-f]{64}\Z")
_TOOL_CONTRACT_ID_DOMAIN = b"leonervis-code-extension-tool-contract-v1\0"
_TOOL_REGISTRY_ID_DOMAIN = b"leonervis-code-tool-registry-snapshot-v1\0"
_TOOL_SET_ID_DOMAIN = b"leonervis-code-tool-set-snapshot-v1\0"


class ExtensionSourceKind(StrEnum):
    """Closed provenance classes accepted by the extension registry."""

    BUILTIN = "builtin"
    MCP = "mcp"
    EXTENSION = "extension"


class ToolExecutionKind(StrEnum):
    """Host-owned execution boundary selected by one tool contract."""

    HOST_ACTION = "host-action"
    TASK_STAGE_CONTROL = "task-control"
    TASK_ADMISSION = "task-admission"
    TASK_LIFECYCLE = "task-lifecycle"
    SKILL_AUTHORING = "skill-authoring"
    SKILL_LIFECYCLE = "skill-lifecycle"
    TOOL_DISCOVERY = "tool-discovery"
    MCP_REMOTE = "mcp-remote"


class ToolExposure(StrEnum):
    """Whether a contract is initially direct or eligible for discovery."""

    DIRECT = "direct"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ExtensionSource:
    """Stable source identity and monotonic source-owned generation."""

    kind: ExtensionSourceKind
    name: str
    generation: int

    def __post_init__(self) -> None:
        if type(self.kind) is not ExtensionSourceKind:
            raise ValueError("extension source kind is invalid")
        if not isinstance(self.name, str) or _SOURCE_NAME.fullmatch(self.name) is None:
            raise ValueError("extension source name is invalid")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("extension source generation must be positive")

    def as_mapping(self) -> dict[str, object]:
        return {
            "generation": self.generation,
            "kind": self.kind.value,
            "name": self.name,
        }


@dataclass(frozen=True)
class ExtensionToolContract:
    """One provider-neutral schema bound to provenance and Host execution policy."""

    definition: CanonicalToolDefinition
    source: ExtensionSource
    execution_kind: ToolExecutionKind
    exposure: ToolExposure
    permission_actions: tuple[PermissionAction, ...]
    version: int = EXTENSION_TOOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != EXTENSION_TOOL_CONTRACT_VERSION:
            raise ValueError("unsupported extension tool contract version")
        if not isinstance(self.definition, CanonicalToolDefinition):
            raise ValueError("extension tool definition is invalid")
        CanonicalToolDefinition.from_mapping(self.definition.as_mapping())
        if not isinstance(self.source, ExtensionSource):
            raise ValueError("extension tool source is invalid")
        if type(self.execution_kind) is not ToolExecutionKind:
            raise ValueError("extension tool execution kind is invalid")
        if type(self.exposure) is not ToolExposure:
            raise ValueError("extension tool exposure is invalid")
        if not isinstance(self.permission_actions, tuple):
            raise ValueError("extension tool permission actions are invalid")
        if any(type(action) is not PermissionAction for action in self.permission_actions):
            raise ValueError("extension tool permission action is invalid")
        if len(set(self.permission_actions)) != len(self.permission_actions):
            raise ValueError("extension tool permission actions are invalid")
        is_action = self.execution_kind in {
            ToolExecutionKind.HOST_ACTION,
            ToolExecutionKind.MCP_REMOTE,
        }
        if is_action != bool(self.permission_actions):
            raise ValueError("extension tool execution kind and permission actions disagree")
        if PermissionAction.UNKNOWN in self.permission_actions:
            raise ValueError("extension tool contract cannot authorize an unknown action")

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def contract_id(self) -> str:
        manifest = {
            "definition": self.definition.as_mapping(),
            "execution_kind": self.execution_kind.value,
            "exposure": self.exposure.value,
            "permission_actions": [action.value for action in self.permission_actions],
            "source": self.source.as_mapping(),
            "version": self.version,
        }
        digest = hashlib.sha256(_TOOL_CONTRACT_ID_DOMAIN + _canonical_json(manifest)).hexdigest()
        return f"tool-v{self.version}-{digest}"

    @property
    def permission_label(self) -> str:
        if self.execution_kind is ToolExecutionKind.MCP_REMOTE:
            return "mcp-remote:" + "/".join(action.value for action in self.permission_actions)
        if self.execution_kind is not ToolExecutionKind.HOST_ACTION:
            return self.execution_kind.value
        if self.permission_actions == (
            PermissionAction.WORKSPACE_CREATE,
            PermissionAction.WORKSPACE_OVERWRITE,
        ):
            return "workspace-create/overwrite"
        return "/".join(action.value for action in self.permission_actions)

    def permits(self, action: PermissionAction) -> bool:
        return type(action) is PermissionAction and action in self.permission_actions


@dataclass(frozen=True)
class ToolRegistrySnapshot:
    """One coherent immutable registry generation from all enabled sources."""

    generation: int
    contracts: tuple[ExtensionToolContract, ...]
    version: int = TOOL_REGISTRY_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != TOOL_REGISTRY_SNAPSHOT_VERSION:
            raise ValueError("unsupported tool registry snapshot version")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("tool registry generation must be positive")
        if not isinstance(self.contracts, tuple) or not self.contracts:
            raise ValueError("tool registry snapshot requires contracts")
        names: set[str] = set()
        identities: set[str] = set()
        for contract in self.contracts:
            if not isinstance(contract, ExtensionToolContract):
                raise ValueError("tool registry contains an invalid contract")
            if contract.name in names:
                raise ValueError("tool registry contains a duplicate tool name")
            if contract.contract_id in identities:
                raise ValueError("tool registry contains a duplicate contract identity")
            names.add(contract.name)
            identities.add(contract.contract_id)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(contract.name for contract in self.contracts)

    @property
    def definitions(self) -> tuple[CanonicalToolDefinition, ...]:
        return tuple(contract.definition for contract in self.contracts)

    @property
    def snapshot_id(self) -> str:
        manifest = {
            "contracts": [contract.contract_id for contract in self.contracts],
            "generation": self.generation,
            "version": self.version,
        }
        digest = hashlib.sha256(_TOOL_REGISTRY_ID_DOMAIN + _canonical_json(manifest)).hexdigest()
        return f"registry-v{self.version}-{digest}"

    def contract(self, name: str) -> ExtensionToolContract:
        for contract in self.contracts:
            if contract.name == name:
                return contract
        raise ValueError(f"unsupported tool: {name}")

    def select(self, names: tuple[str, ...] | None = None) -> ToolSetSnapshot:
        """Freeze one exact policy-selected set in canonical registry order."""
        if names is None:
            selected = tuple(
                contract for contract in self.contracts if contract.exposure is ToolExposure.DIRECT
            )
        else:
            _validate_tool_names(names)
            requested = frozenset(names)
            selected = tuple(contract for contract in self.contracts if contract.name in requested)
            if len(selected) != len(requested):
                raise ValueError("enabled tool names contain an unsupported tool")
            if any(contract.exposure is not ToolExposure.DIRECT for contract in selected):
                raise ValueError("deferred tools require an explicit promotion epoch")
        if not selected:
            raise ValueError("tool registry selection requires a direct tool")
        return ToolSetSnapshot(
            registry_id=self.snapshot_id,
            registry_generation=self.generation,
            epoch=0,
            contracts=selected,
        )


@dataclass(frozen=True)
class ToolSetSnapshot:
    """Exact model-visible contracts for one immutable tool-set epoch."""

    registry_id: str
    registry_generation: int
    epoch: int
    contracts: tuple[ExtensionToolContract, ...]
    version: int = TOOL_SET_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != TOOL_SET_SNAPSHOT_VERSION:
            raise ValueError("unsupported tool set snapshot version")
        if (
            not isinstance(self.registry_id, str)
            or _REGISTRY_ID.fullmatch(self.registry_id) is None
        ):
            raise ValueError("tool set registry identity is invalid")
        if type(self.registry_generation) is not int or self.registry_generation < 1:
            raise ValueError("tool set registry generation must be positive")
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError("tool set epoch must be non-negative")
        if not isinstance(self.contracts, tuple) or not self.contracts:
            raise ValueError("tool set snapshot requires contracts")
        names: set[str] = set()
        for contract in self.contracts:
            if not isinstance(contract, ExtensionToolContract):
                raise ValueError("tool set contains an invalid contract")
            if contract.name in names:
                raise ValueError("tool set contains a duplicate tool name")
            names.add(contract.name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(contract.name for contract in self.contracts)

    @property
    def definitions(self) -> tuple[CanonicalToolDefinition, ...]:
        return tuple(contract.definition for contract in self.contracts)

    @property
    def snapshot_id(self) -> str:
        manifest = {
            "contracts": [contract.contract_id for contract in self.contracts],
            "epoch": self.epoch,
            "registry_generation": self.registry_generation,
            "registry_id": self.registry_id,
            "version": self.version,
        }
        digest = hashlib.sha256(_TOOL_SET_ID_DOMAIN + _canonical_json(manifest)).hexdigest()
        return f"toolset-v{self.version}-{digest}"

    def contract(self, name: str) -> ExtensionToolContract:
        for contract in self.contracts:
            if contract.name == name:
                return contract
        raise ValueError(f"tool is outside the snapshot: {name}")

    def promote(
        self,
        registry: ToolRegistrySnapshot,
        names: tuple[str, ...],
    ) -> ToolSetSnapshot:
        """Create the next epoch by adding exact deferred contracts only."""
        if not isinstance(registry, ToolRegistrySnapshot):
            raise ValueError("promotion registry snapshot is invalid")
        if (
            registry.snapshot_id != self.registry_id
            or registry.generation != self.registry_generation
        ):
            raise ValueError("promotion registry does not match the tool set snapshot")
        _validate_tool_names(names)
        current = frozenset(self.names)
        requested = frozenset(names)
        additions = requested - current
        if not additions:
            return self
        for name in additions:
            if registry.contract(name).exposure is not ToolExposure.DEFERRED:
                raise ValueError("only deferred tools can be promoted")
        selected_names = current | additions
        selected = tuple(
            contract for contract in registry.contracts if contract.name in selected_names
        )
        return ToolSetSnapshot(
            registry_id=self.registry_id,
            registry_generation=self.registry_generation,
            epoch=self.epoch + 1,
            contracts=selected,
        )

    def restrict_actions(self, allowed_names: tuple[str, ...]) -> ToolSetSnapshot:
        """Create the next epoch by intersecting action tools while retaining controls."""
        if not isinstance(allowed_names, tuple) or len(set(allowed_names)) != len(allowed_names):
            raise ValueError("Skill allowed tool names are invalid")
        if any(not isinstance(name, str) or not name for name in allowed_names):
            raise ValueError("Skill allowed tool names are invalid")
        allowed = frozenset(allowed_names)
        selected = tuple(
            contract
            for contract in self.contracts
            if contract.execution_kind
            not in {ToolExecutionKind.HOST_ACTION, ToolExecutionKind.MCP_REMOTE}
            or contract.name in allowed
        )
        if selected == self.contracts:
            return self
        return ToolSetSnapshot(
            registry_id=self.registry_id,
            registry_generation=self.registry_generation,
            epoch=self.epoch + 1,
            contracts=selected,
        )


def _validate_tool_names(names: object) -> None:
    if (
        not isinstance(names, tuple)
        or not names
        or len(set(names)) != len(names)
        or not all(isinstance(name, str) and name and name.isascii() for name in names)
    ):
        raise ValueError("enabled tool names are invalid")


def _canonical_json(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("extension contract is not canonical JSON") from error
    return text.encode("utf-8")
