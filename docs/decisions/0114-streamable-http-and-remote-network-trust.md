# 0114: Streamable HTTP and Remote Network Trust

## Status

Accepted.

## Context

Confined stdio proves local process ownership but cannot connect to hosted MCP services. A remote transport adds DNS rebinding, redirects, TLS identity, credential handling, session reuse, HTTP/SSE framing, and delivery uncertainty. It must not bypass the existing quarantine catalog, frozen ToolSet, PermissionGate, approval, ActionIdentity, or Action Audit.

## Decision

MCP configuration advances to schema v2 while schema-v1 stdio files remain readable. A `streamable-http` entry contains one canonical credential-free HTTPS endpoint, optional bearer-token environment name or OAuth client metadata, explicit Root exposure, and revisioned resource subscriptions. It uses `remote-https` trust; remote tools are always `dangerous` and cannot receive the local stdio `workspace-read` policy.

The transport resolves only public addresses, pins the selected IP, verifies the original TLS hostname, accepts only standard HTTPS, sends identity encoding, follows no redirect, and bounds request, response, JSON, SSE line, SSE event, notification, timeout, and session-ID sizes. It sends `MCP-Protocol-Version`, retains a valid `MCP-Session-Id` only in memory, rejects an ID change, accepts JSON or SSE responses, treats notification POST `202` as success, and attempts `DELETE` on a session-bound close.

Static bearer values are read only from the configured environment name when constructing a request header. Values never enter configuration, model arguments, terminal output, Session, Action Audit, catalog identity, or documentation. Transport failure after POST dispatch remains outcome-uncertain and is not automatically retried. The same catalog candidate, policy, ToolSet epoch, permission, normalization, and durable audit path used by stdio tools owns remote execution.

## Compatibility And Versions

MCP configuration advances from v1 to v2 with legacy-v1 read compatibility. Provider adapter remains v38 and existing Session, Task, Action Audit, Extension, Registry, ToolSet, ToolArguments, ActionIdentity, ApprovalPreview, and compaction schemas remain unchanged.

## Non-goals

- HTTP, private-address endpoints, custom ports, redirects, proxies, custom certificate authorities, mutual TLS, or user-supplied arbitrary headers;
- legacy HTTP/SSE transport, WebSocket transport, automatic retry, fan-out, load balancing, or durable remote session IDs;
- trusting a remote annotation or endpoint identity as permission or completion evidence.

## Consequences

- Hosted MCP servers can participate in the same progressively discovered and audited tool path as local servers.
- Remote credentials and network authority remain Host-owned, while uncertainty remains visible instead of being flattened into a retryable failure.
