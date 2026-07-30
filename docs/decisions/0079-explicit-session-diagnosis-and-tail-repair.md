# 0079: Read-only Session Diagnosis and Explicit Backed-up Tail Repair

- Status: Accepted
- Date: 2026-07-30
- Scope: classify transcript integrity and repair only a provably incomplete final record

## Context

Strict inspection correctly rejects corruption, while resume may recover an incomplete final write as part of its prepared transaction. Users also need a diagnostic path that never mutates and a separate repair command that can restore a Session without resuming it. Broad JSON repair would violate append-only audit and causality guarantees.

## Decision

Add standalone `session doctor [selector]` and REPL `/session doctor <latest|complete-UUID>`. Doctor opens the transcript no-follow and returns only `valid`, `repairable_tail`, or `invalid` plus a bounded code, sizes, and validated counts. It takes no writer lease, writes nothing, and never repairs.

Only a replay-valid newline-terminated prefix followed by an invalid UTF-8 or invalid-JSON final fragment is `repairable_tail`. Empty transcripts, corrupt prefixes or middle records, newline-terminated invalid records, and complete JSON records missing only their final newline are invalid and are never repaired automatically.

Add standalone `session repair [selector]` and REPL `/session repair <latest|complete-UUID>`. Repair acquires the target writer lease and existing directory lock, re-reads and revalidates the exact pathname/descriptor identity, and rejects an active or changed target. Before truncation it durably creates a private sibling backup named by the complete source SHA-256. It then truncates only the incomplete fragment, appends and fsyncs the existing `recovery` v1 record, and strictly replays the result. The backup remains local under `.leonervis-code`.

Repair does not append `session_resumed`, update `latest`, select the target, rebuild runtime, invoke a provider, or alter the current Session. A transcript that is already valid or not in the one repairable shape is rejected unchanged.

## Compatibility

The existing `recovery` v1 record is reused, so no transcript schema advances. System prompt v21, adapter contract v25, the 21-tool catalog, Action Audit, compaction, and Effective Context identities remain unchanged.

## Non-goals

- middle-record repair, JSON normalization, sequence rewriting, or causality reconstruction;
- repairing an actively written Session, deleting backups, or automatic retry after uncertain durability;
- remote recovery, import, merge, or general filesystem repair.
