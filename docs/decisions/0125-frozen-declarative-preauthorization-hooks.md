# ADR 0125: Frozen Declarative Preauthorization Hooks

- Status: Accepted
- Date: 2026-08-07

## Context

Leonervis already has hard Tool validation, a central PermissionGate, informed approval, Action Audit, frozen ToolSet snapshots, and durable Session causality. Users still need a small project or user policy layer for rules such as protecting selected paths, requiring confirmation for a class of writes, or attaching a bounded warning to a ToolResult. Duplicating those rules inside every Tool would mix local policy with execution constraints, while arbitrary shell, HTTP, or model-driven Hook handlers would create a second execution system that could bypass the existing authorization and audit boundaries.

Reference systems expose broader Hook surfaces. MewCode-style event/action automation is flexible but can turn lifecycle callbacks into direct command or HTTP execution. Claw-Code-style structured pre/post Tool protocols improve observability, but a callback that can invoke `sh -lc` or override a permission decision still expands authority outside the normal Tool path. Leonervis therefore begins with a deliberately smaller policy-only slice integrated into its existing frozen context and authorization flow.

## Decision

Define strict Hook schema v1 with one event, `before_action_authorization`, and four declarative effects: `continue`, `deny`, `require_ask`, and `advisory`. A rule may match exact canonical Tool names, PermissionAction values, workspace-relative path prefixes, and source kind (`builtin` or `mcp`). Matching is deterministic and bounded: there is no regex, expression language, executable content, argument mutation, credential access, or side effect. `deny` wins, then `require_ask`; advisories are appended only when no denial wins, while `continue` changes nothing.

Store user and project rules in strict revisioned JSON. New rules are disabled by default. IDs are unique across both scopes, writes use private mode-`0600` atomic replacement and revision CAS, and symlinked or malformed configuration fails closed. Standalone `hooks add|list|show|doctor|enable|disable|remove` commands manage configuration without a provider or Session. REPL `/hooks`, `/hooks active|list|show|doctor` commands are read-only inspections.

Freeze the complete configured Hook set, including disabled rules, into one immutable `HookSetSnapshot` at Turn preparation. Its `hooks-v1` identity enters Effective Context and ActionLease validation. Configuration changes during a Turn cannot alter that Turn and become visible only to a later prepared Turn. Full and compacted Effective Context representations advance to v17/v18; v15/v16 remain strict legacy Skill-v3 representations.

Evaluate the frozen snapshot only after Tool hard preparation, PermissionAction classification, extension-contract validation, and ActionIdentity construction, but before `ActionCoordinator`, `action_requested`, approval, or execution. A Hook denial returns a bounded model-visible ToolResult and creates no Action Audit because the action never enters authorization. `require_ask` can tighten `auto` to `ask`; no Hook can turn a PermissionGate denial into an allow, promote an MCP policy, bypass a Tool hard boundary, disable sandboxing, or grant blanket approval. Advisory text does not change the underlying outcome or audit status.

The canonical system prompt advances to v41 and the provider adapter contract to v42 because the model-visible policy description changes. The Tool Registry remains generation 5 because Hooks add no model-visible Tool schema. Session, Task, Action Audit, ToolSet, Skill inventory, and Hook configuration schemas otherwise remain unchanged.

## Consequences

- Local policy is reusable across built-in and MCP Tools without becoming another executor.
- Every action in one Turn sees one exact Hook policy identity, preserving deterministic continuation, compaction, resume, and stale-action checks.
- Existing PermissionGate, approval, audit, sandbox, and Tool hard constraints remain authoritative and non-bypassable.
- Malformed configuration blocks Turn preparation before provider invocation instead of silently disabling policy.
- Post-action handlers, shell or HTTP callbacks, provider or subagent calls, background execution, argument/result mutation, arbitrary expressions, automatic retries, and executable plugin Hooks remain out of scope.
