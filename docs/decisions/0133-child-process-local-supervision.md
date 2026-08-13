# ADR 0133: Process-Local Child Run Supervision

- Status: Accepted
- Date: 2026-08-13

## Context

A4 proves one detached Child can execute in the foreground, but a parent
Session still cannot continue while several prepared Children run. Scheduling
must not reuse the parent writer, Provider manager, runtime state, or action
identity stream, and it must not imply that work survives the Host process.

## Decision

`ChildRunSupervisor` owns a bounded FIFO queue and at most four daemon worker
threads. It is created lazily by the owning `ProjectSession`, is bound to that
Session's ID, and accepts only `ready` Child Runs belonging to that parent. A
queue may contain at most 32 pending IDs and each live supervisor accepts one
local submission per ID. The durable Child execution lease remains the final
cross-thread/process authority when a worker starts.

Each worker invokes the existing A4 `ChildRunExecutor` seam and owns no parent
resource. Worker exceptions are isolated, converted to bounded local
notifications, and cannot terminate sibling workers. Notifications are
volatile UI hints; the Child Run ledger remains authoritative. A notification
may report that durable status was temporarily unavailable, but it never
fabricates a terminal state.

`ProjectSession.close()` stops new submissions and performs a bounded worker
join. A blocked daemon may outlive that short join and is recovered by the
later A6 interruption protocol; A5 does not claim process survival, automatic
restart, cancellation, wait/join, handoff, or a standalone detached command.

## Compatibility and boundaries

Foreground `child run` remains the one-shot path. `/child start <id>` is a
REPL-only process-local submission command and returns after enqueueing. Parent
prompts continue through the parent writer/runtime while Child workers use
independent Session writers, Provider managers, clients, leases, and runtime
state. The fixed read-only Child ToolSet and all A3/A4 hard bounds remain
unchanged.

## Deferred work

A6 adds cooperative cancellation, bounded wait/join, and restart recovery. A7
adds durable handoff and parent delivery. A8 adds model-visible delegation.
Teams, mailboxes, shared task ownership, worktrees, and process-surviving
workers remain outside this ADR.
