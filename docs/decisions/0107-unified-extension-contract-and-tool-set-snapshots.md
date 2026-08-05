# 0107: Unified Extension Contract and ToolSet Snapshots

## Status

Accepted.

## Context

Leonervis already had a canonical tool catalog, provider projections, Task coordination dispatch, PermissionGate classifications, and purpose-specific executors. Those facts were distributed across catalog tuples, adapter helpers, terminal labels, and execution branches. Adding MCP directly on top of those separate representations would create multiple sources of truth for schema, provenance, exposure, execution ownership, and permission class. It would also leave a Turn vulnerable to registry drift if an external source changed while the model-tool loop was in progress.

The pre-MCP boundary must unify metadata without pretending that every capability has the same executor. It must preserve the existing Host action and Task coordination boundaries, provider-native capabilities must remain outside Leonervis ToolUse, and no external process, credential, transport, discovery call, or mutable plugin registry should be introduced in this slice.

## Decision

Define one immutable `ExtensionToolContract` for every model-visible Leonervis tool. A contract binds the exact provider-neutral `CanonicalToolDefinition` to a source kind and name, source generation, execution kind, exposure class, and the complete set of permitted `PermissionAction` classifications. Source kinds are closed to `builtin`, future `mcp`, and future local `extension`. Execution kinds distinguish `host-action`, Task Stage control, Task admission, and Task lifecycle dispatch. Exposure is either initially `direct` or `deferred` for an explicit later discovery epoch.

Each contract has a domain-separated content identity covering all of those fields. `ToolRegistrySnapshot` freezes one ordered registry generation and rejects duplicate names or contract identities. Its identity covers generation and ordered contract IDs. `ToolSetSnapshot` freezes the exact contracts visible in one epoch and binds them to the registry identity and generation. Epoch zero may select only direct contracts and always preserves registry order. `promote()` can add only deferred contracts from the same exact registry snapshot, creates a monotonically later epoch, preserves canonical order, and is idempotent for already-visible names.

Migrate all current built-ins into one immutable generation-one registry. Current tools remain direct, so the model-visible catalog and order do not change. Permission labels shown by `/tools catalog` are now derived from contracts instead of a second handwritten table; detailed inspection also shows registry, contract, source generation, and exposure identities.

`AgentLoop.prepare_turn()` obtains one registry snapshot, selects one exact ToolSet, installs its definitions into the Effective Context, and pins that ToolSet for every continuation. A Provider response naming a tool outside the pinned ToolSet fails before dispatch and commits no Turn. Compaction rebase preserves the same ToolSet. A pure `advance_tool_set()` boundary can install a compatible later epoch before an ActionLease exists, but this slice exposes no discovery tool and does not invoke it automatically.

`ConversationRequest` may carry the exact frozen definitions and ToolSet identity. Anthropic Messages, OpenAI-compatible Chat Completions, and OpenAI Responses use those definitions for both count and create projections; compatibility callers without a frozen snapshot retain the fixed built-in fallback. Provider-native tools such as server-owned web search remain adapter-owned capabilities and are not added to the Host ToolSet.

`ProjectSession` retains the active ToolSet beside the ActionLease. Before PermissionGate or execution, the requested contract must be present, use the Host action boundary, and permit the executor-derived `PermissionAction`. This validation does not replace executor preparation, workspace containment, stale-state checks, approval, sandboxing, ActionIdentity, Action Audit, or any tool-specific hard bound. Task coordination tools keep their dedicated dispatchers and cannot be routed through Host action execution merely because they share the unified metadata contract.

## Compatibility And Versions

Effective Context representations advance from `ctx-v7`/`ctx-v8` to `ctx-v9`/`ctx-v10` because the exact ToolSet identity now participates in context identity. The current empty no-instructions full-context identity becomes `ctx-v9-6e8bb3a51d3138760bdb6e8ea9db1ab94927599529048ba7bee2d7e792fe2b0e`. Legacy `ctx-v7`/`ctx-v8` values remain valid without a ToolSet ID, and existing transcripts or compaction records are not rewritten. The provider adapter contract advances from v36 to v37 because native Host-tool projection now accepts exact frozen definitions.

The canonical system prompt remains v33 because no model-visible tool name, description, schema, permission behavior, or output convention changes. ToolArguments, ActionIdentity, ApprovalPreview, Session, Task, Action Audit, provider-owned history, Profile, and usage schemas remain unchanged. The Tool Contract, Registry Snapshot, and ToolSet Snapshot each begin at representation v1.

## Non-goals

- MCP server configuration, process startup, transport, authentication, handshake, capability negotiation, reconnect, cancellation, or resource/prompt support;
- a model-visible discovery tool, semantic search over tools, automatic ToolSet promotion, or dynamic mutation during a leased Turn;
- plugin installation, package loading, executable extension code, marketplace behavior, hot reload, or durable extension configuration;
- merging Provider-native server tools into Host ToolUse, PermissionGate, or Action Audit;
- replacing dedicated Task dispatch, tool executors, hard workspace/network constraints, or sandbox boundaries with metadata;
- rewriting legacy Sessions or persisting registry snapshots as a new durable record type.

## Consequences

- Built-ins and future MCP or local extensions can enter one reviewable contract surface without erasing their distinct execution owners.
- One Turn sees one content-addressed tool world; registry changes can affect only a separately prepared Turn or an explicit compatible epoch transition.
- Provider count/create projection, terminal inspection, and Host permission classification derive from the same frozen definitions and contract metadata.
- MCP work can now focus on source loading, trust, transport, lifecycle, and discovery rather than first inventing parallel schema and permission systems.
