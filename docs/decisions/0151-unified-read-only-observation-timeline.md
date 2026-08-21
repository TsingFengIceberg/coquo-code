# ADR 0151: Unified Read-only Observation Timeline

## Status

Accepted for the O1–O3 observability slice after deterministic projection,
correlation, CLI, and runtime-context tests.

## Decision

Coquo exposes one Host-only `ObservationEvent` contract and a read-only
`observe timeline` CLI projection over the existing Session, Task, Child Run,
and Team durable ledgers. The projection is computed at inspection time; it is
not a second append-only log and does not rewrite or migrate any existing
transcript.

Each event retains only bounded metadata: source and durable source identity,
sequence, timestamp, record type, normalized phase/status, evidence level,
stable event ID, parent event ID, and selected related durable IDs. Conversation
text, tool arguments/results, objectives, Team message bodies, and Child
handoff bodies are deliberately excluded. Child handoff records are marked
`untrusted`; strict replay of Session, Task, and Team records is Host-verified,
while Child lifecycle records are Host-observed.

The CLI supports text and JSONL output for one source or the workspace-wide
timeline. Workspace-wide merging keeps each ledger's causal order and links
ledger roots through already durable relationships: Task admission, delegated
Task stages, Child parent delegation, Team control, and Team assignment. It
does not infer missing relationships from timestamps or prose.

One ordinary in-process Agent Turn carries a volatile `ObservationContext`
with `trace_id`, `turn_id`, and `session_id`; an in-process derived context may
retain that trace while adding its own durable IDs. Detached Child workers do
not receive an unpersisted parent trace across a restart; they establish their
own volatile Turn context and are correlated through durable parent/session,
tool, stage, and assignment IDs. This is correlation metadata only. Existing
Session/Task/Child/Team record schemas, provider contracts, system prompt,
Effective Context identity, and model-visible tools are unchanged, so old
transcripts remain readable and historical timelines are reconstructed from
their durable identities rather than pretending an old trace was persisted.

## Compatibility and recovery

Observation inspection is strictly read-only and fails closed on invalid or
unreadable ledgers. Missing cross-ledger records produce an unparented event,
never a guessed parent. A malformed query or an out-of-range limit is rejected
before any store is opened. No network, credential, Provider call, retry,
background work, telemetry, commit, or push is part of this slice.

## Consequences

Operators can inspect Session, Task, Child, and Team progress in one bounded
timeline, follow durable parent/child identities, distinguish verified facts
from Host observations and untrusted handoffs, and consume deterministic JSONL
without exposing private content. Because no observation log is persisted,
crash recovery remains based on the authoritative ledgers and a later event
cannot claim a trace ID that was never durably recorded.

## Verification

Deterministic tests cover context validation and cleanup, stable event IDs,
phase/evidence mapping, body exclusion, cross-ledger parent linking, JSONL
projection, CLI query/limit rejection, and byte-for-byte read-only behavior.
The full project release gate remains required; real Providers, network,
credentials, API cost, and mutation operations are excluded.
