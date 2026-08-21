# 0152: Observation Stream, Diagnosis, and Retention

## Status

Accepted

## Decision

O4–O9 extend the O1–O3 Host-owned observation contract without creating a
second authoritative ledger. `ProjectSession` exposes a bounded,
process-local `ObservationStream`; existing PromptEvents are projected into it
as content-free events with the current volatile trace and turn IDs. The
original terminal sink remains independent, and stream failures cannot change
the model/tool causal outcome.

Provider lifecycle, context preflight, compaction, usage, tool, permission,
Task, Child, Team, and worker activity use the same event shape when a live
event exists. Durable background queue snapshots are projected as
`background_*` Host-observed events and retain only stable IDs, state,
timestamps, and lease/worker references. The Child Run ledger remains the
execution authority.

The read-only `observe timeline` command supports bounded filters for trace,
status, evidence, record type, and an inclusive ISO-8601 time window. The
`observe diagnose` command reports missing parent links, untrusted handoffs,
failed/unknown outcomes, and stale background claims. Diagnosis is advisory:
it never retries, recovers, approves, or mutates a ledger. JSON output is
stable and text output is concise enough for a terminal.

Process-local events are retained by count and optional age only. Retention
never deletes Session, Task, Child, Team, or queue records; after process exit
the durable projections remain the recovery source. Raw prompts, model output,
tool bodies, handoff bodies, headers, credentials, and tokens are excluded.

## Compatibility and non-goals

Existing schemas and model-visible contracts do not change. Older ledgers are
read through the existing strict replay paths. This slice does not add remote
telemetry, cloud export, automatic repair, automatic retry, or a new event bus.

## Verification

Deterministic tests cover bounded live streams, safe content-free projection,
background correlation, filters, diagnosis, CLI JSON/text output, and
byte-for-byte ledger immutability.
