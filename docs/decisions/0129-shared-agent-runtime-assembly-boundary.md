# ADR 0129: Shared Agent Runtime Assembly Boundary

- Status: Accepted
- Date: 2026-08-13

## Context

`ProjectSession` previously assembled the only `AgentLoop` directly and also
owned the full per-turn orchestration. That made a future Child Run likely to
copy parent behavior or accidentally inherit Session-only state.

## Decision

Coquo now assembles every parent runtime through
`AgentRuntimeFactory -> AgentRuntime -> AgentLoop`. `AgentRuntime` owns one
loop and one volatile active-turn state, including provider preflight,
automatic-compaction ordering, action-lease issuance, provider execution,
usage completion, failure handling, and cleanup. `ProjectSession` remains the
Host owner of Session writers, permissions, Action Audit, Task effects, hooks,
titles, and durable callbacks.

Session writer/runtime pairs are installed together during construction,
resume, switch, new, and fork operations. The extraction does not add worker
threads or concurrent provider use; parallel runtime safety is deferred to a
later execution slice.

## Compatibility And Boundaries

The existing `AgentLoop` causal behavior, tool contracts, provider adapter,
system prompt, Effective Context identities, Session schemas, permissions, and
Task Stage evidence are unchanged. Active runtime state is rejected on
re-entry and cleared after success, cancellation, provider failure, commit
failure, or `BaseException`.

## Non-goals

This ADR does not introduce Child Sessions, background execution, handoff,
waiting, messaging, Teams, or a second loop implementation.
