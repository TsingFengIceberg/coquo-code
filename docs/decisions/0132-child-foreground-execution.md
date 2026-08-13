# ADR 0132: One-Shot Child Foreground Execution

- Status: Accepted
- Date: 2026-08-13

## Context

A3 can prepare a detached Child Session but cannot prove that a Child uses the
same causal runtime safely. Reusing the parent's Provider manager or writer
would serialize or corrupt concurrent work, while copying `AgentLoop` would
create a second unverified orchestration path.

## Decision

`ChildRunExecutor` acquires a lifetime execution lease, reconstructs the
admitted redacted Provider route, opens the detached Session with
`publish_latest=False`, and runs exactly one turn through
`ProjectSession -> AgentRuntimeFactory -> AgentRuntime -> AgentLoop`. The
Child gets a fresh RuntimeProviderManager/provider client, Session writer,
usage state, action lease sequence, and volatile turn state. Its Host mode
disables parent-only Task/action/tool-promotion dispatchers and exposes only
the fixed A3 read-only ToolSet.

The control ledger appends `child_run_started` only after route validation and
resource ownership succeed. A durable Child Session `turn_committed` record is
the prerequisite for `child_run_completed`, which stores only the execution
ID, exact Session record sequence, and assistant-text digest. Construction or
turn failures append a bounded `child_run_failed` record and never claim
completion. Execution leases are distinct from short append writers and reject
duplicate foreground runners.

## Compatibility and boundaries

Legacy queued/cancelled Child transcripts remain readable. Detached execution
never changes the parent Session, current runtime, or `latest` pointer. Child
permission remains read-only and approval remains auto only for its fixed read
actions; writes, commands, network, MCP, Task/Skill controls, delegation, and
background scheduling are excluded.

## Deferred work

Parallel workers, live cancellation, wait/join, restart recovery, handoff,
parent delivery, and model-visible delegation remain A5-A8.
