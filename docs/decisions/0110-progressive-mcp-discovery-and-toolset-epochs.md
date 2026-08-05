# 0110: Progressive MCP Discovery and ToolSet Epochs

## Status

Accepted.

## Context

Sending every enabled MCP schema in every Provider request would increase context cost, amplify untrusted descriptions, and make the model tool surface change whenever a server catalog changed. The existing `ToolSetSnapshot.promote()` primitive can construct a later epoch, but a normal ProjectSession Turn already holds an ActionLease bound to the old Effective Context and ToolSet identity. Mutating that snapshot in place would let stale approvals or ActionIdentity values survive a different model-visible tool world.

Slice 4 must provide useful model-driven discovery without implementing MCP execution. It must define exactly when a later Provider invocation sees promoted schemas, preserve tool-use/result causality, and fail closed if registry, runtime, Session, or lease state has changed.

## Decision

Add two fixed direct built-in contracts: `tool_search(query, max_results)` and `tool_promote(names)`. They are classified as `tool-discovery`, carry no permission actions, and remain in the initial ToolSet. `tool_search` performs bounded case-insensitive literal-term matching only over MCP contracts marked `deferred` in the exact Registry frozen during Turn preparation. It returns at most eight deterministically ranked candidates as bounded JSON Lines. It does not search built-ins, arbitrary extension contracts, live servers, files, Sessions, or the web.

`tool_promote` accepts at most eight exact MCP qualified names. Every name must have appeared in a successful `tool_search` result earlier in the same Turn. Both discovery tools must be the only call in their assistant response, which keeps discovery result causality and ToolSet transitions outside multi-action batch ambiguity. Promotion is idempotent for already visible names and otherwise uses the existing Registry-bound canonical `promote()` operation to create exactly the next ToolSet epoch.

Turn preparation now retains the complete immutable Registry beside the epoch-zero ToolSet. When a leased ProjectSession Turn promotes a candidate, the Host first validates the active Session, runtime generation, old context, old ToolSet, and current MCP Registry identity. It then retires the old non-recreatable ActionLease, creates the later Effective Context with the new exact ToolSet identity, issues a fresh lease bound to that context, and installs all three together before the next Provider invocation. The pending user, tool-use, and tool-result sequence remains unchanged. Provider count and create paths receive the same new frozen definitions.

No old lease, approval, or ActionIdentity is translated to the new epoch. A configuration or Registry mismatch rejects the transition and commits no candidate Turn. Compaction cannot rebase a leased transition. Host-only catalog inspection never changes an active Turn. Every later continuation uses the promoted snapshot consistently until the Turn ends.

Promoted MCP contracts still have execution kind `mcp-remote`. Because Slice 5 has not implemented `tools/call`, a model request for one returns the explicit `mcp_execution_unavailable` error result inside normal tool causality. It never routes to a built-in executor, PermissionGate, approval handler, Action Audit, or MCP process. This proves discovery and exposure independently from remote execution.

## Compatibility And Versions

The canonical system prompt advances from v33 to v34 and the provider adapter contract from v37 to v38 because the fixed model-visible surface and accepted provider tool names change. Effective Context representations remain `ctx-v9` and `ctx-v10`; the current empty full-context identity becomes `ctx-v9-2f737163e792a16fbae49a629f54afc5cf43d49b75f1afe47b12ff5ed4e60d3e`. ToolSet epoch transition state remains in-memory and is evidenced by ordinary committed tool-use/result pairs rather than a new Session record. Existing durable schemas and legacy replay remain unchanged.

## Non-goals

- MCP `tools/call`, process reuse, cancellation forwarding, progress notifications, or result normalization;
- automatic promotion based only on a user prompt, server recommendation, annotation, or model guess;
- sending the full quarantine catalog to the Provider initially;
- carrying promoted tools across Turns without a newly prepared Registry and ToolSet;
- treating a promoted contract as permission, approval, execution success, or task completion evidence.

## Consequences

- Initial Provider requests pay for two small discovery schemas rather than every enabled MCP schema.
- The model can select relevant external capabilities while the Host retains an exact, auditable epoch boundary.
- Lease replacement makes stale approval and dispatch reuse structurally invalid after promotion.
- Slice 5 can add a dedicated MCP executor without changing discovery identity or weakening current PermissionGate boundaries.
