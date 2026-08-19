# ADR 0149: Durable Workflow Stage Observation and Recovery

## Status

Accepted for the Host-owned Task–Child–Team workflow bridge after deterministic
bridge, CLI, concurrency, cancellation, and recovery tests.

## Decision

The workflow ledger stores a bounded identity projection for every external
Explorer or Executor stage. A projection contains the exact stage Task ID,
execution target, Child Run/Team/assignment/schedule identities, status,
handoff observation, result code, diagnostic, and evidence digest. Task, Child,
Team, and schedule ledgers remain authoritative; the workflow never duplicates
worker state or reconstructs an external result from a different ledger.

The workflow root Task owns the Explorer stage. The Executor receives a
separately derived stage Task before admission. This preserves the existing
fail-closed rule for external Task usage accounting: an external stage whose
provider or tool usage is unknown cannot silently consume the root Task budget
or make a later stage appear to be the same execution. The derived Task ID is
persisted and rendered so Host inspection can prove the binding.

Stage admission, execution, observation, and recovery are separate Host-owned
operations. Admission creates one exact external identity and performs no
Provider invocation. Execution reuses the existing Child or Team service;
observation may append one normalized terminal result; recovery only
re-observes the recorded identity. Recovery never retries, creates a
replacement, cleans an orphan, merges files, commits, or pushes.

The CLI exposes provider-independent `workflow start`, `show`, `advance`,
`explore-start`, `execute-start`, and `recover` controls. Output includes the
workflow phase, stage Task ID, external IDs, handoff state, and an explicit
`evidence: untrusted` marker. Provider/profile selection is rejected for these
control-plane commands.

Persistent Child background execution remains bounded by one workspace worker
lease and at most four worker threads. Queue claims, Child execution leases,
heartbeats, terminal events, cancellation, and orphan recovery remain owned by
their existing ledgers. Workflow recovery observes cancellation, interruption,
missing handoff, and unknown outcomes as recovery-required evidence rather than
claiming successful completion.

## Compatibility and recovery

Workflow JSON without `stages` remains readable and decodes to an empty stage
projection. New stage records include `task_id`; legacy projections without it
fall back to the workflow root Task ID for replay only. No existing Task,
Child, Team, Session, queue, or schedule transcript is rewritten. Invalid or
stale identities, mismatched stage Tasks, missing handoffs, lease conflicts,
durability failures, and phase drift fail closed.

## Consequences

The Host can demonstrate which Task, Child, Team assignment, or schedule was
actually admitted and can resume inspection after a process restart without
inventing a new execution. The extra derived Executor Task is intentionally
visible and requires separate accounting. The design remains conservative: no
autonomous scheduler, unbounded recursive tree, implicit Provider use, or
automatic integration is introduced.

## Verification

Deterministic tests cover stage encode/decode compatibility, real Explorer and
derived-Task Executor bridge execution, provider-independent CLI inspection,
provider-selection rejection, bounded two-Child worker concurrency, exclusive
worker leases, cancelled Child recovery, durable identity persistence, and
recovery-required outcomes. Real Providers, network, credentials, commit, and
push are not part of the release gate.
