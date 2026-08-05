from __future__ import annotations

from dataclasses import replace
import json

from leonervis_code.core.extensions import (
    ExtensionSourceKind,
    ToolExecutionKind,
    ToolExposure,
)
from leonervis_code.mcp.catalog import (
    McpCandidateDisposition,
    build_mcp_quarantine_catalog,
)
from leonervis_code.mcp.client import McpListedTool, McpProbeResult
from leonervis_code.mcp.config import McpServerConfiguration, McpServerEntry
from leonervis_code.core.permissions import PermissionAction
from leonervis_code.mcp.policy import (
    McpPolicyDisposition,
    McpToolPolicyRule,
    McpToolPolicyStore,
)


def _entry(name: str = "fixture", *, revision: int = 3) -> McpServerEntry:
    return McpServerEntry(
        "project",
        McpServerConfiguration(
            name=name,
            command="/usr/bin/fixture-mcp",
            enabled=True,
            revision=revision,
        ),
    )


def _tool(name: str, schema: dict[str, object], description: str = "Find widgets") -> McpListedTool:
    return McpListedTool(
        name=name,
        title=None,
        description=description,
        input_schema_json=json.dumps(schema, separators=(",", ":"), sort_keys=True),
        output_schema_json=None,
        annotations_json='{"readOnlyHint":true}',
    )


class CatalogClient:
    def __init__(self, tools: tuple[McpListedTool, ...]) -> None:
        self.tools = tools

    def probe(self, entry: McpServerEntry) -> McpProbeResult:
        return McpProbeResult(
            configured_name=entry.configuration.name,
            protocol_version="2025-06-18",
            server_name="untrusted-server-name",
            server_version="1",
            capability_names=("tools",),
            tools=self.tools,
            pages=1,
            duration_ms=1,
            stderr_bytes=0,
            stderr_truncated=False,
            cleanup_complete=True,
        )


def test_catalog_normalizes_candidates_with_stable_content_id_and_no_annotation_authority() -> None:
    tools = (
        _tool(
            "read-widget",
            {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
        ),
        _tool("read_widget", {"type": "object", "properties": {}}),
    )
    first = build_mcp_quarantine_catalog((_entry(),), CatalogClient(tools))
    second = build_mcp_quarantine_catalog((_entry(),), CatalogClient(tuple(reversed(tools))))

    assert first.catalog_id == second.catalog_id
    assert len(first.accepted) == 2
    assert len({candidate.qualified_name for candidate in first.accepted}) == 2
    assert all(
        candidate.qualified_name.startswith("mcp_fixture_read_widget_")
        for candidate in first.accepted
    )
    candidate = first.accepted[0]
    assert candidate.schema_fingerprint.startswith("mcp-schema-v1-")
    assert candidate.configuration_revision == 3
    assert candidate.protocol_version == "2025-06-18"
    assert candidate.scope == "project"
    assert candidate.contract is not None
    assert candidate.contract.source.kind is ExtensionSourceKind.MCP
    assert candidate.contract.source.generation == 3
    assert candidate.contract.exposure is ToolExposure.DEFERRED
    assert candidate.contract.execution_kind is ToolExecutionKind.MCP_REMOTE
    assert candidate.contract.permission_actions == (PermissionAction.DANGEROUS,)
    assert "readOnlyHint" not in candidate.contract.definition.canonical_json

    changed = build_mcp_quarantine_catalog(
        (replace(_entry(), configuration=replace(_entry().configuration, revision=4)),),
        CatalogClient(tools),
    )
    assert changed.catalog_id != first.catalog_id
    assert changed.accepted[0].contract is not None
    assert changed.accepted[0].contract.contract_id != candidate.contract.contract_id


def test_catalog_quarantines_unsupported_schemas_with_only_sanitized_reason_codes() -> None:
    tools = (
        _tool("non_object", {"type": "string"}, "SECRET_DESCRIPTION"),
        _tool("ref_schema", {"type": "object", "$ref": "SECRET_REFERENCE"}),
        _tool(
            "bad_required",
            {"type": "object", "properties": {}, "required": ["missing"]},
        ),
    )
    catalog = build_mcp_quarantine_catalog((_entry(),), CatalogClient(tools))

    assert not catalog.accepted
    assert [candidate.reason_code for candidate in catalog.rejected] == [
        "mcp_schema_required_invalid",
        "mcp_schema_root_not_object",
        "mcp_schema_keyword_unsupported",
    ]
    assert all(
        candidate.disposition is McpCandidateDisposition.REJECTED for candidate in catalog.rejected
    )
    assert all(candidate.contract is None for candidate in catalog.rejected)
    assert "SECRET" not in catalog.catalog_id


def test_catalog_quarantines_pattern_instead_of_executing_an_untrusted_regex() -> None:
    catalog = build_mcp_quarantine_catalog(
        (_entry(),),
        CatalogClient(
            (
                _tool(
                    "unsafe_pattern",
                    {
                        "type": "object",
                        "properties": {"value": {"type": "string", "pattern": "^(a+)+$"}},
                    },
                ),
            )
        ),
    )

    assert not catalog.accepted
    assert catalog.rejected[0].reason_code == "mcp_schema_keyword_unsupported"


def test_catalog_registry_keeps_candidates_deferred_and_content_addressed() -> None:
    catalog = build_mcp_quarantine_catalog(
        (_entry(),),
        CatalogClient((_tool("find_widgets", {"type": "object", "properties": {}}),)),
    )
    registry = catalog.registry_snapshot()
    initial = registry.select()
    candidate = catalog.accepted[0]

    assert candidate.qualified_name not in initial.names
    assert registry.snapshot_id == catalog.registry_snapshot().snapshot_id
    promoted = initial.promote(registry, (candidate.qualified_name,))
    assert promoted.epoch == 1
    assert promoted.names[-1] == candidate.qualified_name


def test_catalog_applies_only_an_exact_local_policy_and_binds_identity(tmp_path) -> None:
    tools = (_tool("read_widget", {"type": "object", "properties": {}}),)
    baseline = build_mcp_quarantine_catalog((_entry(),), CatalogClient(tools))
    candidate = baseline.accepted[0]
    policy_store = McpToolPolicyStore(tmp_path / "user.json", tmp_path / "project.json")
    policy_store.set_rule(
        McpToolPolicyRule(
            qualified_name=candidate.qualified_name,
            configured_name=candidate.configured_name,
            server_scope=candidate.scope,
            configuration_revision=candidate.configuration_revision,
            remote_name=candidate.remote_name,
            protocol_version=candidate.protocol_version,
            schema_fingerprint=candidate.schema_fingerprint,
            action=PermissionAction.WORKSPACE_READ,
        ),
        policy_scope="project",
    )

    applied = build_mcp_quarantine_catalog(
        (_entry(),), CatalogClient(tools), policy_store=policy_store
    )
    trusted = applied.accepted[0]
    assert trusted.policy_disposition is McpPolicyDisposition.APPLIED
    assert trusted.permission_action is PermissionAction.WORKSPACE_READ
    assert trusted.policy_revision == 1
    assert trusted.contract is not None
    assert trusted.contract.permission_actions == (PermissionAction.WORKSPACE_READ,)
    assert trusted.contract.contract_id != candidate.contract.contract_id
    assert applied.catalog_id != baseline.catalog_id

    stale = build_mcp_quarantine_catalog(
        (replace(_entry(), configuration=replace(_entry().configuration, revision=4)),),
        CatalogClient(tools),
        policy_store=policy_store,
    ).accepted[0]
    assert stale.policy_disposition is McpPolicyDisposition.STALE
    assert stale.permission_action is PermissionAction.DANGEROUS
    assert stale.contract is not None
    assert stale.contract.permission_actions == (PermissionAction.DANGEROUS,)
