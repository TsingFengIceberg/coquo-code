# ADR 0161: Bounded Provider Reliability and Workflow Driver

## Status

Accepted

## Date

2026-09-01

## Context

Provider failures, retry accounting, and multi-stage workflow progression used to
be described separately. A retry can create a second physical request while
remaining one logical invocation, and a workflow can advance several durable
stages without being allowed to imply review, integration, or completion. The
Host needs one explicit boundary for both facts so usage, cancellation, stream
visibility, and recovery remain truthful.

## Decision

`ProviderReliabilityPolicy` is Host-owned and bounded to at most three physical
attempts. The default is one attempt. A retry is allowed only for a retryable
rate-limit, timeout, transport, or provider-unavailable failure that occurred
before any text delta was delivered. Once a delta is visible, the logical
invocation fails without replaying visible text. Backoff, `Retry-After`, total
elapsed time, input/output token ceilings, and known-usage requirements are
checked before another attempt; cancellation interrupts both waiting and the
next attempt.

Every physical attempt contributes usage (or an explicit unknown usage fact) to
the existing Host tracker. Invocation events describe the logical request and
may expose only bounded attempt and stream metrics. Request bodies, response
bodies, credentials, headers, and raw provider diagnostics are never stored in
reliability state.

`WorkflowOrchestrator.drive_until_review()` is a bounded Host driver over the
existing Task, Child, and Team ledgers. It may start the recorded Architect,
Explorer, and Executor stages in order, subject to a four-stage and elapsed-time
limit, then returns a structured stop reason. It never performs review,
integration, acceptance, retry, commit, push, or hidden provider/tool work.
Background mode stops at a durable pending stage; failure, cancellation,
recovery-required, and budget exhaustion are terminal observations that require
explicit Host action.

## Compatibility and Recovery

The provider-neutral request and tool schemas are unchanged. Existing provider,
Session, Task, Child, Team, and queue records remain replayable; attempt counts
and stream metrics are optional Host observations. A failed or interrupted
workflow is not silently resumed or retried. Recovery must re-observe the
authoritative ledger and preserve unknown external effects.

## Verification

Deterministic tests cover retry classification, backoff and cancellation,
pre-delta versus post-delta behavior, usage accounting, provider budget
rejection, foreground/background workflow stops, stage limits, cancellation,
and `/workflow drive` parsing. The offline release gate additionally runs the
full test suite, evals, fake CLI/resume smoke, lint, formatting, lock, and diff
checks. Real Provider acceptance remains a separately authorized manual gate.
