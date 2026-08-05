# 0109: MCP Tool Normalization and Quarantine Catalog

## Status

Accepted.

## Context

The confined stdio client can list bounded MCP tool descriptors, but server-reported names, descriptions, schemas, annotations, protocol metadata, and ordering remain untrusted. Copying those descriptors directly into the canonical model catalog would let one server create collisions, rely on provider-incompatible JSON Schema, claim permission through annotations, or change an active tool world without a reviewable identity.

Slice 3 needs an intermediate representation that preserves useful discovery facts while keeping every external tool outside the initial model ToolSet and every execution boundary. Host inspection must expose enough identity to diagnose normalization without rendering server prose, schemas, arguments, errors, stderr, or credentials.

## Decision

Add a version-one `McpQuarantineCatalog`. Enabled configured servers are probed through the existing temporary confined stdio client. Disabled servers contribute nothing. A probe failure becomes one sanitized source issue containing only configured name, scope, configuration revision, and stable error code; one failed source does not silently create candidates.

Each structurally listed tool receives a deterministic qualified name of the form `mcp_<server>_<tool>_<hash>`. Components are normalized to bounded lowercase ASCII and the hash covers the original configured server and remote tool names, so punctuation collisions remain distinct without depending on server order. Candidates bind configured server name, user or project scope, configuration revision, negotiated protocol version, remote name, qualified name, canonical input-schema fingerprint, disposition, and either an exact deferred contract or one sanitized rejection code.

The schema normalizer requires an object root and a closed supported keyword and type subset. It recursively validates properties, required names, items, bounded `anyOf` or `oneOf`, enum size, and boolean `additionalProperties`; unsupported references, keywords, types, composition, or malformed required sets are quarantined. Accepted definitions retain a bounded single-line server description behind an explicit untrusted-data prefix. Output schemas and annotations do not enter the model contract. In particular, `readOnlyHint` and other annotations never grant a `PermissionAction`.

Accepted candidates become `ExtensionToolContract` values with source kind `mcp`, a source name binding scope, configured server, and negotiated protocol, source generation equal to configuration revision, execution kind `mcp-remote`, exposure `deferred`, and no permission actions. Rejected candidates retain no contract. Catalog and schema identities are domain-separated and content-addressed; canonical candidate ordering is by qualified name. The session-local catalog service caches only while the complete credential-free MCP configuration identity is unchanged. Explicit catalog inspection refreshes it.

Add standalone `mcp catalog` and Host-only REPL `/mcp catalog`. Presentation shows only catalog identity, accepted and rejected counts, qualified names, scope, configured server, revision, protocol, schema fingerprints, and sanitized reason codes. Inspection invokes no Provider, writes no Session record or Action Audit, and does not promote or execute a candidate.

## Compatibility And Versions

The MCP quarantine catalog and schema fingerprint begin at v1. The fixed built-in source and Registry advance to generation 2 because Slice 4 adds fixed discovery contracts; a Registry combined with MCP candidates uses generation 3. Extension Contract, Registry, and ToolSet representation schemas remain v1. No Session, Task, Action Audit, ToolArguments, ActionIdentity, ApprovalPreview, Profile, or provider-owned-history schema changes, and legacy Sessions are not rewritten.

## Non-goals

- `tools/call`, MCP execution, permission inference, or approval based on MCP annotations;
- semantic embeddings, remote indexing, fuzzy autonomous activation, or cross-server result merging;
- durable catalog persistence, background refresh, hot reload, or a persistent MCP process manager;
- HTTP/SSE/OAuth transports, resources, prompts, roots, sampling, elicitation, or server requests;
- exposing raw descriptions, schemas, annotations, errors, stderr, arguments, or credential values in Host inspection.

## Consequences

- External schemas become deterministic reviewable candidates before they can affect a Provider request.
- Malformed or unsupported tools remain diagnosable through stable classifications without leaking server prose.
- A server annotation cannot smuggle a tool into workspace-read or another PermissionGate class.
- Slice 4 can search and promote exact deferred contracts from one frozen content-addressed Registry.
