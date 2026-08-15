# ADR 0145: Authority/Execution Scope and Restricted Child Actions

## Status

Proposed for B6.3. Public writable Team roles and model-visible integration remain
unavailable until the later B6 slices accept this boundary together.

## Decision

`ProjectSession.workspace` is the authority root. It owns provider/profile
configuration, Session/Team/Child/worktree ledgers, approvals, leases, hooks, and
action audit. `ProjectSession.execution_workspace` is the only root passed to file and
command tools. Ordinary Sessions use the same directory for both roots; a writable Team
Child receives a Host-attested distinct linked worktree through an immutable
`ExecutionScope`.

New Action identities use v2 and bind the authority workspace fingerprint, execution
scope (`authority-workspace` or `team-worktree`), execution-root fingerprint, and (for a
Team worktree) the canonical worktree ID. `ActionRequested` is record-local schema v2;
legacy v1 identities and v1 records remain readable without rewriting old transcripts.
Replay checks the authority root and rejects an authority Action whose execution root
does not match the Session header. All Action effects continue through the existing
PermissionGate and ActionCoordinator, so the split changes identity and tool ownership,
not the causal or approval ordering.

Child runtime services are least privilege: Team/Child/Task controls, MCP, Skills, and
hooks are not installed for a restricted Child. A writable Child can receive an
explicit Host-injected built-in Action whitelist; the dispatcher checks the active
ToolSet and rejects names outside that immutable whitelist before invoking the shared
Action path. Read-only Children retain their existing no-Action-dispatcher behavior.

On Linux, a linked-worktree command sandbox binds only the exact execution root
writable. The linked-worktree `.git` pointer is over-mounted read-only after the root
bind, while the host and common Git metadata remain read-only and network syscalls stay
blocked. Missing or invalid sandbox capability fails before the command starts.

## Compatibility

Legacy Action v1 golden digests, Session replay, ordinary read-only tools, and existing
provider contracts remain supported. New ordinary Actions are v2 with equal authority
and execution-root fingerprints; no public writable role or new model-visible ToolSet is
introduced by this ADR alone.

## Consequences

Action audit remains durable in the authority Session even when tools mutate a distinct
execution root. Root escape, stale scope, stale ToolSet, MCP/Skill/Hook access, and
authority metadata writes fail closed before execution. The later role and integration
slices must carry the same scope and capability snapshot through Team admission and
review evidence.

## Rejected alternatives

Passing the authority directory to Child tools would allow cross-writing between
parallel members. Giving a Child the parent dispatcher or broad danger permission would
make the parent capability ceiling unenforceable. Reusing ordinary linked-worktree
observation without Host attestation would permit arbitrary Git metadata and branch
identity injection.
