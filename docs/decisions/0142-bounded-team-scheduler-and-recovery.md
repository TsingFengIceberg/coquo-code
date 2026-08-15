# ADR 0142: Bounded Team Scheduler and Recovery

## Status

Accepted for the B5 Team scheduler slices.

## Decision

Automatic Team dispatch is represented by an append-only schedule wave in the existing
workspace-bound Team JSONL transcript. A wave has one stable run ID, a source, bounded
assignment and parallel limits, optional cancellation, and one terminal outcome. The
schedule projection is replay-derived; at most one nonterminal wave may exist for a
Team. `idle` means no currently dispatchable work, not that every work item is complete.

Schedule-owned assignments use assignment schema v3 and carry the exact schedule run
ID. Schema v1 and v2 assignments remain readable without rewriting their original JSONL
bytes. Assignment creation remains the durable work/member claim boundary, so a schedule
cannot forge a duplicate claim or replace an existing identity after a partial failure.

Scheduling chooses the oldest ready work item in Team record order and an active, free
member with the fewest prior schedule attempts, using Team order as the stable tie
breaker. Disabled, left, busy, dependency-blocked, and closed-Team identities are never
selected. The policy is pure and does not invoke a Provider or create a Child.

Each Team has a separate persistent `0600` schedule lock file. The lock is opened
without following symlinks, contains a fixed header, and is held for the lifetime of a
schedule coordinator. It proves current ownership only; replayed records remain the
status authority. A second process or thread fails closed while the lease is held.

## Consequences

- Schedule identity, limits, provenance, cancellation intent, and terminal diagnostics
  survive process loss and can be audited with Team assignment records.
- Existing direct assignments remain compatible while scheduled assignments identify
  their owning wave exactly.
- The lease prevents two coordinators from dispatching the same Team concurrently, but
  it is not mistaken for durable completion or recovery proof.
- Child execution, background supervision, retries, automatic completion, writable
  members, and model-visible Team controls remain separate later slices.

## Rejected alternatives

A mutable queue would make restart ownership and duplicate claims ambiguous. A database,
daemon, or shared service would add lifecycle and deployment authority outside the
single-user CLI harness. Treating a Child result as automatic work completion would
remove the existing Host review boundary. Reusing the Child lease path would conflate
Team schedule ownership with Child execution ownership, so the schedule lease is
Team-specific while Child recovery remains Child-specific.
