# ADR 0157: Real Provider Acceptance and Stream Diagnostics

## Status

Accepted

## Context

Coquo has deterministic adapter and AgentLoop coverage, but a real endpoint can
still fail because of credentials, endpoint compatibility, provider buffering,
or terminal delivery. These causes must not be conflated, and real requests
must never become an accidental test-suite side effect.

## Decision

The repository provides `scripts/real_provider_acceptance.py` as an explicit
operator-run acceptance harness. It creates a temporary workspace by default
and exercises a final response, one bounded read-only tool call, and confirmed
long-term-memory recall. A caller-provided workspace is accepted only when it
is a real directory and does not already contain the exclusive fixture name;
the fixture is created with exclusive-create semantics and removed when the
run ends. The harness never prints credential values, writes a report file, or
runs with `shell=True`.

The harness requires all of `COQUO_REAL_PROVIDER_ACCEPT=1`,
`--allow-network`, `--allow-credentials`, and `--allow-cost`. Missing any one
of these gates fails before a Provider process is created. Each subprocess
has a bounded timeout and bounded stdout/stderr snippets. The harness is not
run by CI and does not replace deterministic tests.

Every AgentLoop Provider invocation records content-free stream metrics on its
existing `ProviderInvocationFinished` event: elapsed duration, number of text
deltas, time to the first delta, and the maximum interval between deltas.
Metrics are Host-measured and optional; they contain no text, request body,
headers, credentials, or token data. TTY presentation shows them only when
text deltas were observed. A large first-delta duration indicates upstream
generation/transport latency; a large inter-delta gap indicates provider
buffering or delivery gaps; a low-gap stream followed by delayed terminal
rendering remains a Host/UI issue. These facts are diagnostic and never alter
retry, commit, or failure semantics.

## Compatibility and security

The model-visible tool catalog, Provider wire projections, Session schemas,
permission rules, and Action Audit contracts are unchanged. Existing event
constructors remain valid because the new metrics are optional and excluded
from event identity comparisons. Real acceptance remains opt-in and outside
the offline release gate. Caller workspaces are never overwritten by the
harness fixture.

## Verification

Deterministic tests cover all authorization gates, profile-name validation,
exclusive fixture collision and cleanup, bounded scenario reporting, stream
metric calculation and validation, and safe TTY rendering. The offline CI
gate runs the full test suite, lint/format/lock checks, CLI fake-provider smoke,
and explicit Session/Child/Memory recovery tests. A real-provider run is a
separate manual release activity with its endpoint, credential, and cost
approval recorded by the operator.
