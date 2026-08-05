"""Deterministic MCP interoperability report over one bounded probe."""

from __future__ import annotations

from dataclasses import dataclass

from leonervis_code.mcp.client import McpProbeResult
from leonervis_code.mcp.config import McpServerEntry


SUPPORTED_SERVER_CAPABILITIES = frozenset({"logging", "prompts", "resources", "tools"})


@dataclass(frozen=True)
class McpConformanceReport:
    configured_name: str
    transport: str
    protocol_version: str
    known_capabilities: tuple[str, ...]
    unknown_capabilities: tuple[str, ...]
    tools: int
    cleanup_complete: bool
    legacy_http_sse_supported: bool = False

    @property
    def passed(self) -> bool:
        return self.cleanup_complete


def inspect_mcp_conformance(entry: McpServerEntry, probe: McpProbeResult) -> McpConformanceReport:
    capabilities = set(probe.capability_names)
    return McpConformanceReport(
        configured_name=entry.configuration.name,
        transport=entry.configuration.transport.value,
        protocol_version=probe.protocol_version,
        known_capabilities=tuple(sorted(capabilities & SUPPORTED_SERVER_CAPABILITIES)),
        unknown_capabilities=tuple(sorted(capabilities - SUPPORTED_SERVER_CAPABILITIES)),
        tools=len(probe.tools),
        cleanup_complete=probe.cleanup_complete,
    )
