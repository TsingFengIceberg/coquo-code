# 0118: MCP Interoperability and Production Hardening

## Status

Accepted.

## Context

After local and remote transports plus multiple MCP capabilities exist, production failures increasingly come from combinations: JSON versus SSE responses, session header changes, cleanup after failed initialize, resource subscription restoration, notification floods, server errors, and capability drift. Compatibility claims need a deterministic, sanitized inspection surface.

## Decision

Add `mcp doctor` and an immutable conformance report over one bounded live probe. It reports transport, negotiated protocol, known and unknown capability names, normalized tool count, cleanup completeness, and the explicit legacy transport decision. It does not render server instructions, descriptions, schemas, resource or prompt content, credentials, HTTP headers, errors, stderr, process IDs, or session IDs.

Harden Streamable HTTP setup so every failed initialize, initialized notification, tools list, or subscription restore attempts bounded session cleanup. Server JSON-RPC errors count as completed calls; malformed, closed, timeout, cancellation, transport, cleanup, and post-dispatch failures retain existing partial or outcome-uncertain semantics. Remote runtime status distinguishes transport and whether an in-memory MCP session was established without exposing its ID.

The interoperability matrix covers strict JSON and SSE response framing, `202` notifications, protocol and session headers, unchanged session identity, no redirects, environment-owned bearer injection, PKCE/state/token refresh, resource and prompt bounds, Root request handling, reverse-request default denial, process/session retirement, and schema-v1 configuration replay. Unknown server capabilities remain visible by name but inert.

For tool input interoperability, accept only the known Draft 7 `$schema` declaration at the schema root. Preserve the complete server schema in its fingerprint, remove the declaration before Provider projection, and continue to reject unknown dialects, nested declarations, references, patterns, and unsupported validation keywords. This admits SDK-generated metadata without letting a server select validation behavior that the Host does not implement.

Legacy HTTP/SSE is intentionally not implemented. Streamable HTTP is the single remote transport; adding legacy support would duplicate reconnection, endpoint, session, credential, and delivery semantics without a demonstrated required server. A later independent ADR may revisit that decision with concrete interoperability evidence.

## Compatibility And Versions

The canonical system prompt advances to v37 with fingerprint `v37-d7ad600e357ae981d083683cbe35580475da88854a0edbe933ce4106bae11c66`. The empty full-context identity becomes `ctx-v9-febbf229c7b658d6fd2b4f31dc6129cfd7a91487e5f723ef6bf9aafa5969a7b4`. Effective Context remains `ctx-v9`/`ctx-v10`, Provider adapter remains v38, MCP configuration is v2, OAuth storage begins at v1, and other durable schemas remain unchanged.

## Consequences

- Users can distinguish transport compatibility from tool trust and execution success using one redacted command.
- The project has one remote protocol to harden, while legacy compatibility remains an evidence-driven future decision rather than speculative code.
