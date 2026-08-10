from __future__ import annotations

from dataclasses import replace

import pytest

from coquo.core.contracts import ConversationRequest, UserMessage
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.extensions import (
    ExtensionSource,
    ExtensionSourceKind,
    ExtensionToolContract,
    ToolExecutionKind,
    ToolExposure,
    ToolRegistrySnapshot,
)
from coquo.core.permissions import PermissionAction
from coquo.providers.anthropic import (
    AnthropicProviderConfig,
    build_input_projection as build_anthropic_projection,
    build_request as build_anthropic_request,
)
from coquo.providers.openai_compat import (
    build_input_projection as build_openai_projection,
    build_request as build_openai_request,
)
from coquo.providers.openai_responses import (
    build_input_projection as build_responses_projection,
    build_request as build_responses_request,
)
from coquo.providers.resolver import resolve_runtime_route
from coquo.system_prompt import build_system_prompt
from coquo.tools.catalog import TOOL_CATALOG, TOOL_REGISTRY_SNAPSHOT


def _definition(
    name: str, description: str = "A deterministic test tool."
) -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    )


def _contract(
    name: str,
    *,
    source_generation: int = 1,
    exposure: ToolExposure = ToolExposure.DIRECT,
    permission_actions: tuple[PermissionAction, ...] = (PermissionAction.WORKSPACE_READ,),
) -> ExtensionToolContract:
    return ExtensionToolContract(
        definition=_definition(name),
        source=ExtensionSource(
            ExtensionSourceKind.EXTENSION,
            "test-suite",
            source_generation,
        ),
        execution_kind=ToolExecutionKind.HOST_ACTION,
        exposure=exposure,
        permission_actions=permission_actions,
    )


def test_contract_registry_and_tool_set_identities_are_stable_and_content_addressed() -> None:
    contract = _contract("inspect")
    registry = ToolRegistrySnapshot(3, (contract,))
    tool_set = registry.select()

    assert contract.contract_id == _contract("inspect").contract_id
    assert registry.snapshot_id == ToolRegistrySnapshot(3, (contract,)).snapshot_id
    assert tool_set.snapshot_id == registry.select().snapshot_id
    assert contract.contract_id.startswith("tool-v1-")
    assert registry.snapshot_id.startswith("registry-v1-")
    assert tool_set.snapshot_id.startswith("toolset-v1-")

    assert (
        replace(contract, definition=_definition("inspect", "Changed.")).contract_id
        != contract.contract_id
    )
    assert (
        replace(contract, source=replace(contract.source, generation=2)).contract_id
        != contract.contract_id
    )
    assert replace(contract, exposure=ToolExposure.DEFERRED).contract_id != contract.contract_id
    assert (
        replace(
            contract,
            permission_actions=(PermissionAction.WORKSPACE_OVERWRITE,),
        ).contract_id
        != contract.contract_id
    )
    assert ToolRegistrySnapshot(4, (contract,)).snapshot_id != registry.snapshot_id


def test_extension_contract_validation_fails_closed() -> None:
    definition = _definition("inspect")
    source = ExtensionSource(ExtensionSourceKind.EXTENSION, "test-suite", 1)

    with pytest.raises(ValueError, match="execution kind and permission actions disagree"):
        ExtensionToolContract(
            definition,
            source,
            ToolExecutionKind.HOST_ACTION,
            ToolExposure.DIRECT,
            (),
        )
    with pytest.raises(ValueError, match="execution kind and permission actions disagree"):
        ExtensionToolContract(
            definition,
            source,
            ToolExecutionKind.TASK_STAGE_CONTROL,
            ToolExposure.DIRECT,
            (PermissionAction.WORKSPACE_READ,),
        )
    with pytest.raises(ValueError, match="unknown action"):
        replace(_contract("inspect"), permission_actions=(PermissionAction.UNKNOWN,))
    with pytest.raises(ValueError, match="permission actions are invalid"):
        replace(
            _contract("inspect"),
            permission_actions=(
                PermissionAction.WORKSPACE_READ,
                PermissionAction.WORKSPACE_READ,
            ),
        )
    with pytest.raises(ValueError, match="duplicate tool name"):
        ToolRegistrySnapshot(
            1,
            (
                _contract("inspect"),
                replace(
                    _contract("inspect"),
                    permission_actions=(PermissionAction.WORKSPACE_OVERWRITE,),
                ),
            ),
        )


def test_registry_selection_and_deferred_promotion_preserve_canonical_order() -> None:
    inspect = _contract("inspect")
    discoverable = _contract("remote_lookup", exposure=ToolExposure.DEFERRED)
    write = _contract(
        "write",
        permission_actions=(PermissionAction.WORKSPACE_OVERWRITE,),
    )
    registry = ToolRegistrySnapshot(7, (inspect, discoverable, write))

    initial = registry.select()
    selected = registry.select(("write", "inspect"))

    assert initial.names == ("inspect", "write")
    assert selected.names == ("inspect", "write")
    with pytest.raises(ValueError, match="explicit promotion epoch"):
        registry.select(("remote_lookup",))

    promoted = initial.promote(registry, ("remote_lookup",))
    assert promoted.epoch == 1
    assert promoted.names == ("inspect", "remote_lookup", "write")
    assert promoted.snapshot_id != initial.snapshot_id
    assert promoted.promote(registry, ("remote_lookup",)) is promoted

    inspect_only = registry.select(("inspect",))
    with pytest.raises(ValueError, match="only deferred tools"):
        inspect_only.promote(registry, ("write",))
    with pytest.raises(ValueError, match="does not match"):
        initial.promote(replace(registry, generation=8), ("remote_lookup",))


def test_builtin_catalog_has_one_complete_contract_per_tool() -> None:
    assert TOOL_REGISTRY_SNAPSHOT.definitions == TOOL_CATALOG
    assert TOOL_REGISTRY_SNAPSHOT.names == tuple(tool.name for tool in TOOL_CATALOG)
    assert len(set(TOOL_REGISTRY_SNAPSHOT.names)) == len(TOOL_CATALOG)
    assert all(
        contract.source.kind is ExtensionSourceKind.BUILTIN
        and contract.source.name == "coquo"
        and contract.exposure is ToolExposure.DIRECT
        for contract in TOOL_REGISTRY_SNAPSHOT.contracts
    )

    expected_labels = {
        "read_file": "workspace-read",
        "write_file": "workspace-create/overwrite",
        "run_command": "dangerous",
        "move_file": "workspace-move",
        "delete_file": "workspace-delete",
        "web_search": "network-read",
        "download_file": "network-write",
        "task_propose_plan": "task-control",
        "task_propose_start": "task-admission",
        "task_accept_plan": "task-lifecycle",
    }
    for name, label in expected_labels.items():
        assert TOOL_REGISTRY_SNAPSHOT.contract(name).permission_label == label


def test_all_provider_projections_use_the_exact_frozen_tool_definition() -> None:
    registry = ToolRegistrySnapshot(1, (_contract("extension_probe"),))
    tool_set = registry.select()
    request = ConversationRequest(
        system_prompt=build_system_prompt(),
        history=(UserMessage("inspect"),),
        enabled_tool_names=tool_set.names,
        tool_definitions=tool_set.definitions,
        tool_set_id=tool_set.snapshot_id,
    )

    anthropic_config = AnthropicProviderConfig("claude-opus-4-8")
    anthropic_count = build_anthropic_projection(
        anthropic_config,
        request,
        native_search_enabled=False,
    )
    anthropic_create = build_anthropic_request(
        anthropic_config,
        request,
        native_search_enabled=False,
    )
    assert anthropic_count["tools"] == anthropic_create["tools"]
    assert anthropic_count["tools"] == [tool_set.definitions[0].as_mapping()]

    chat_route = resolve_runtime_route("xai/grok-3", environment={})
    chat_count = build_openai_projection(chat_route, request, native_search_enabled=False)
    chat_create = build_openai_request(chat_route, request, native_search_enabled=False)
    assert chat_count["tools"] == chat_create["tools"]
    assert chat_count["tools"][0]["function"]["name"] == "extension_probe"
    assert chat_count["tools"][0]["function"]["description"] == "A deterministic test tool."

    responses_route = resolve_runtime_route("openai/gpt-5", environment={})
    responses_count = build_responses_projection(
        responses_route,
        request,
        native_search_enabled=False,
    )
    responses_create = build_responses_request(
        responses_route,
        request,
        native_search_enabled=False,
    )
    assert responses_count["tools"] == responses_create["tools"]
    assert responses_count["tools"][0]["name"] == "extension_probe"
    assert responses_count["tools"][0]["description"] == "A deterministic test tool."
