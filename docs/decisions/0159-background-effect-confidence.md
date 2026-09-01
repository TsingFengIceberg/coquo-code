# ADR 0159: Background Effect Confidence and Terminal Idempotency

- Status: Accepted
- Date: 2026-09-01
- Scope: Durable background queue terminal observations and recovery evidence

## Context

The durable Child queue already guarded claims with a worker lease, but a
terminal Child status did not say whether the worker had crossed an external
side-effect boundary before a crash. Repeating terminalization was harmless in
some cases but could hide a conflicting observation.

## Decision

Each background queue item carries an effect state:

- `not-started`: no executor start was observed;
- `in-flight`: a worker claimed the item and execution may be active;
- `confirmed`: the Child terminal record was observed after normal completion;
- `unknown`: recovery or inspection cannot prove whether an external effect
  occurred.

New queue records use schema v2. Schema v1 records remain readable and derive
the compatible state from their event type. Claims become `in-flight`, requeues
become `not-started`, normal terminal observations become `confirmed`, and
orphaned running/cancelling executions become `unknown`. The queue remains an
observation ledger; the Child Run ledger remains execution truth.

Terminalization is idempotent only when the status and effect state match the
existing terminal observation. A conflicting second observation fails closed
instead of overwriting evidence. Worker results carry the same confidence into
the queue, and status/observation rendering exposes it without copying Child
output or claiming rollback, retry, or exactly-once execution.

## Compatibility and recovery

No Child or Session schema changes. Old queue files replay with derived effect
states and new appends use schema v2. Recovery still never automatically
re-executes a claimed Child. An `unknown` result requires explicit human
inspection of the Child ledger and any external side effects before a later
decision.

## Verification

Deterministic tests cover v1 replay, claim/requeue transitions, idempotent
terminalization, conflicting terminal observations, concurrent worker behavior,
and orphan recovery. The release gate remains required.
