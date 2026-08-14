# ADR 0135: Evidence-Backed Child Handoff and Parent Delivery

- Status: Accepted
- Date: 2026-08-14

## Context

A6 gives every Child Run a durable terminal outcome, but terminal control
metadata is not a safe substitute for the Child's result. Completed text lives
in the detached Child Session, while failure records may contain untrusted
provider diagnostics and the original Child objective. A parent also needs a
future delivery boundary that does not inject detached output directly into its
provider-neutral history.

## Decision

Each terminal Child Run may append exactly one
`child_run_handoff_published` schema-v1 record after its terminal record. The
record is a bounded, untrusted projection rather than a verified factual result.
It binds the parent and Child identities, terminal sequence/type/result code,
source-text digest, rendered-body digest, truncation flag, and one canonical
manifest digest.

For a completed Child, publication loads the exact committed Turn through
`SessionStore.committed_turn()` and its raw record evidence through
`SessionStore.turn_evidence()`. The assistant-text digest must match the durable
Child completion record, and the handoff records the exact Turn sequence and raw
record SHA-256. The body is limited to both 32 KiB UTF-8 and 32 KiB characters;
truncation uses an explicit marker at a valid UTF-8 boundary. Reading an
already-published completed handoff revalidates the detached Session evidence.

Failed, cancelled, interrupted, and preparation-failed Children publish only a
fixed Host summary containing the stable outcome and result code. Objective
text, provider errors, tracebacks, credentials, and other diagnostic content do
not enter the handoff. Child replay validates the record against the exact
terminal lifecycle and rejects altered sequence, type, outcome, result code,
body digest, manifest digest, or completed Turn sequence.

Publication is idempotent only for the exact existing record. A different
publication conflicts. An append/fsync uncertainty poisons that writer and is
reported for inspection rather than blindly retried. Standalone
`child handoff <id>` returns bounded JSON; `/child handoff <id>` renders the
untrusted body with terminal control characters escaped.

Parent delivery appends and fsyncs one content-free schema-v1
`child_handoff_delivered` audit before returning the body for Host rendering.
It contains parent and Child IDs, outcome, terminal sequence, handoff digest,
source `host|model`, optional model ToolUse ID, and time. It contains no handoff
body, objective, Provider error, file text, credential, or prompt. The exact
same receipt is idempotent; a different receipt for that Child conflicts.

`ProjectSession` uses its existing parent writer. Standalone `child deliver`
uses a narrow strict audit writer that adds no `session_resumed`, performs no
tail repair, and does not update `latest`. Receipt commit must succeed before
`child deliver` or `/child deliver` renders the body. A write/fsync uncertainty
poisons the Session writer and returns no body; callers inspect rather than
blindly retry.

Replay projects receipts into a Host-only query tuple. They never enter full or
effective history, context identity, usage, tool ledgers, compaction, export,
or fork. Child workers never own or write the parent Session writer.

## Compatibility and boundaries

The Child addition is record-local schema v1 and requires no transcript
rewrite. A6 and earlier terminal records remain readable and may publish a
handoff when their exact evidence is available. This slice does not change the
Tool Registry, system prompt, Provider adapter contract, Effective Context, or
permission model. Handoff provenance proves source and integrity, not the truth
of the Child's conclusions.
