# ADR 0130: Durable Child Run Identity and State

- Status: Accepted
- Date: 2026-08-13

## Context

Future Subagent execution needs a durable identity and truthful crash-safe
state before a thread or Provider call can start. A queue record alone must
not imply that work ran.

## Decision

Coquo stores Child Runs independently under
`.coquo/child-runs/<workspace-fingerprint>/` as closed v1 JSONL transcripts.
Creation durably records a bounded objective and existing parent Session as
`queued`. The only later transition in this slice is an append-only,
fsynced cancellation, deriving `cancelled`. Strict replay rejects every other
record or status.

Standalone `child create/list/show/cancel` and REPL `/child` commands are
Host-only, provider-free, Session-neutral, workspace-bound, bounded, and
terminal-safe. Atomic private-temp installation, no-follow regular-file
checks, inode verification, exclusive writers, directory fsync, and poisoned
writers after uncertain append durability prevent false state claims.

## Deferred Execution Contract

No thread, process, Provider call, Child Session, `running`, `completed`,
`failed`, `interrupted`, handoff, wait/join, message, or Team state exists
yet. A later execution slice must durably freeze permission ceiling,
approval, ToolSet, Provider route, budgets, role/delegation provenance, and
Child Session identity before starting work.
