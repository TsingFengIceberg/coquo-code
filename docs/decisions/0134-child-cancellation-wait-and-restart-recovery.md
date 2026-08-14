# ADR 0134: Child Cancellation, Bounded Wait, and Restart Recovery

- Status: Accepted
- Date: 2026-08-14

## Context

A5 introduced process-local Child workers, but cancellation could only mutate a
queued header and the execution sentinel could not distinguish a live owner from
a file left by a crashed process. Waiting and shutdown therefore had no truthful
durable contract, and a restart could not reconcile abandoned work safely.

## Decision

New Child executions use a persistent regular
`<child-run-id>.execution-v2.lock` file with mode `0600` and a non-blocking OS
lifetime lock. The inode remains after normal release; closing a lease unlocks
and closes it without unlinking. Recovery may append `interrupted` only after it
acquires that same v2 lock. A legacy v1 `<child-run-id>.execution.lock` is
crash-ambiguous: execution and recovery fail closed with
`legacy_lease_ambiguous` and never delete or rewrite it.

The Child ledger remains append-only and authoritative. A running cancellation
first appends and fsyncs one `child_run_cancel_requested` record, then signals a
local `TurnCancellation` token. A blocked Provider leaves durable state
`cancelling`; no thread is force-killed and no terminal cancellation is claimed
before the Provider returns. A committed Child Turn wins a late request;
cooperative cancellation without a committed Turn appends
`child_run_cancelled_terminal`. Provider or adapter errors remain `failed`.

Child ledger writers are held only for short append transactions around
`started`, cancellation requests, and terminal outcomes. The Provider Turn owns
the execution lease, detached Session writer, runtime, and cancellation token,
but never a Child ledger writer. A bounded watcher polls durable state and the
admitted deadline every 100 ms.

`ChildRunSupervisor.wait()` replays durable state first and waits in bounded
100 ms intervals, polling again for cross-process changes. Timeout is
observational and never mutates the run. Supervisor close rejects new work,
cancels queued work, requests cancellation for active work, and joins workers
against one total one-second deadline. It never invents a terminal state for a
still-live worker.

`ChildRunRecoveryService` performs a bounded sorted scan (maximum 100 runs),
isolates corrupt transcripts as diagnostics, validates parent ownership, and
recovers only nonterminal `running`/`cancelling` runs after successful v2 lock
acquisition. It never restarts, retries, changes ready work, or touches a
contended or legacy lease. Project Session startup performs one bounded
parent-owned scan; standalone `child recover` and `/child recover` provide
explicit inspection.

## Compatibility and boundaries

All A1-A5 Child records remain replayable without transcript rewriting. The new
states and records are record-local schema v1 additions. Child authority remains
read-only, one-Turn, depth-one, process-local, and independently durable. The
canonical model prompt, Provider adapter contract, Tool Registry, and Effective
Context identities do not change in A6. Child controls remain Host-only until A8.

## Consequences

Cancellation is cooperative and may remain `cancelling` while an SDK call is
blocked. v2 execution files remain as metadata and are not removed
automatically. A legacy v1 sentinel requires human investigation. A later A7
slice must derive handoff content from exact Child Session evidence rather than
from volatile notifications or terminal output.
