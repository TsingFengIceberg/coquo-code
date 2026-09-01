# ADR 0158: Live Observation Event Output

- Status: Accepted
- Date: 2026-09-01
- Scope: Process-local observation subscription and safe CLI NDJSON output

## Context

The Host already emitted bounded, content-free observation events for terminal
diagnostics, but there was no supported way for an external consumer to receive
those events while a prompt was running. Terminal output alone could not
distinguish provider delivery from presentation buffering, and adding response
text to a diagnostic stream would leak prompt or model content.

## Decision

`ObservationStream` now provides a process-local subscription boundary. A
subscriber receives each event in FIFO order after it is retained; callback
failures are isolated and never change Agent causality. Context switches still
clear the previous Session's volatile events. The subscription is explicitly
unsubscribable and is not a durable ledger or telemetry channel.

The CLI adds `--events ndjson`. It writes one flushed JSON object per Host
observation event to stderr, preserving the existing prompt response on stdout.
Each object carries `schema_version=1` and is safe to parse incrementally.
The output is the existing bounded `ObservationEvent` projection. Stream-delta
events expose only their character and UTF-8 byte counts, timestamps, sequence,
and correlation IDs; response text, prompts, tool arguments, headers,
credentials, reasoning, and tokens never enter this output. The default remains
`--events none`, and REPL output remains human-oriented unless the option is
explicitly selected.

Interactive TTYs additionally use an `immediate_streaming` presentation path:
each received delta is terminal-escaped, written, and flushed without waiting
for a complete Markdown paragraph or fenced code block. This path deliberately
does not reinterpret incomplete Markdown; the Agent loop still validates the
complete response against the received deltas. It removes Host-side Markdown
buffering from the latency diagnosis, but cannot create chunks that the
Provider, SDK, or network has not delivered.

## Compatibility and security

This is a Host-only presentation contract. Provider adapters, Session schemas,
Effective Context identities, permission decisions, and retry behavior do not
change. The event output is best-effort and local; a broken consumer cannot
abort a turn. NDJSON is intentionally content-free and is not a claim of
provider-side flush timing or exactly-once delivery.

## Verification

Deterministic tests cover FIFO subscription, idempotent unsubscribe, subscriber
failure isolation, delta-size redaction, and CLI separation of stdout final
text from stderr NDJSON events. The release gate remains required.
