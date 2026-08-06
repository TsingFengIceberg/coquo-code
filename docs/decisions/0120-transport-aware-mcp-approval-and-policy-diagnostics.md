# 0120: Transport-aware MCP Approval and Policy Diagnostics

- Status: Accepted
- Date: 2026-08-06
- Scope: MCP approval truthfulness, result guidance, quarantine explanations, and read-only policy diagnostics

## Context

The original MCP approval preview was introduced for confined stdio execution and always described a configured executable running without sockets. Streamable HTTP later reused that preview, so approval for a remote service incorrectly described a local process boundary. Operators could see quarantine and stale-policy codes, but had no bounded explanation command and no safe way to distinguish a proven obsolete rule from a rule whose server could not currently be probed.

## Decision

ApprovalPreview v5 adds one required `transport` value for MCP previews. A prepared MCP action resolves the configured server by exact scope and name before approval construction. The stdio rendering retains the confined executable facts. The Streamable HTTP rendering instead states that exact arguments are sent over HTTPS to the selected remote service, that the service is outside the local command sandbox, and that external side effects cannot be rolled back. Neither rendering reveals arguments, endpoints, headers, credential values, or server content.

Known MCP ToolResult codes receive Host-owned conservative `Next:` guidance. Result-limit outcomes recommend narrower or paginated calls; stale contract outcomes require a refreshed catalog and a new Turn; authorization outcomes point to redacted OAuth/server inspection and `mcp doctor`; cleanup and transport uncertainty point to runtime and Action Audit inspection without automatic replay; bounded server errors recommend revising arguments. Unknown codes produce no invented recommendation.

`mcp catalog explain <reason-code>` accepts only the closed set of catalog-owned quarantine codes and renders static meaning and operator action. It never interpolates server schema, prose, errors, stderr, arguments, endpoints, or credentials.

`mcp policy stale` refreshes the quarantine catalog and compares each stored rule with the current exact candidate identity. A missing, rejected, or identity-changed candidate is `stale` only when current discovery completed sufficiently to prove that fact. A matching source probe failure or an incomplete candidate-limited catalog is `unresolved`. Exact active rules are omitted. `mcp policy prune --dry-run` is deliberately non-mutating: it emits existing `mcp policy clear` commands with the exact policy scope and revision only for confirmed stale rules, excludes every unresolved rule, and states that no files changed.

## Compatibility and contracts

ApprovalPreview advances from v4 to v5. It is process-local and non-durable, so no Session or Action Audit migration is required. The canonical system prompt was reviewed and remains v37 because the changes are Host presentation and standalone diagnostics, not model-visible tool behavior. Provider adapter v38, MCP configuration and policy schemas, Extension Contract, Registry, ToolSet, Effective Context, ToolArguments, ActionIdentity, Session, Task, Action Audit, Profile, and compaction representations remain unchanged.

## Explicit non-goals

- automatically deleting stale policies;
- treating a probe failure as evidence that a policy is obsolete;
- showing untrusted server content while explaining a reason code;
- retrying an MCP call automatically from `Next:` guidance;
- changing remote MCP trust, sandboxing, permission classification, or rollback guarantees.

## Verification

Deterministic tests cover both approval transports and redaction, ApprovalPreview transport validation, MCP result-code guidance, the closed quarantine explanation table, CLI rejection of unknown reason codes, exact active-policy omission, stale identity detection, unresolved probe handling, revision-bound dry-run commands, and proof that dry-run leaves policy bytes unchanged. The complete offline release gate remains required.
