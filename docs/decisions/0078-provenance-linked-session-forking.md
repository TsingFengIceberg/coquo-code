# 0078: Provenance-linked Session Forking at Complete-turn Boundaries

- Status: Accepted
- Date: 2026-07-30
- Scope: create a new Session from a strict parent snapshot through one complete turn

## Context

Exploring an alternative after an earlier point must not rewrite or truncate the parent. Copying only final text would lose model-visible tool causality, while copying the parent's Action Audit or provider usage would falsely claim those Host actions or invocations occurred in the child.

## Decision

Add standalone `session fork <selector> <through-turn>` and REPL `/session fork <latest|complete-UUID> <through-turn>`. The source is strictly replayed without repair and the boundary must be a positive committed complete turn. The child receives a new UUID and name, becomes `latest`, and REPL also selects it while preserving the current runtime.

Introduce append-only `session_forked` schema v1 immediately after the child header. It stores the parent UUID, copied turn count, and SHA-256 of the exact strict source transcript snapshot. Replay exposes the source UUID and turn through Session metadata. Existing transcripts without this record continue to replay with no fork provenance and are never rewritten.

Each selected `turn_committed` is materialized with its complete provider-neutral items so ToolUse and ToolResult remain paired. Current tool ledgers are copied; pre-ledger legacy turns derive a minimal replay-consistent ledger from their requests and results. Copied provider usage is empty because no provider invocation occurred while materializing the child. Session names embedded in a parent first turn, Action Audit, failures, runtime events, archive/pin state, compaction checkpoints, and summaries are not copied. A final runtime record installs the invoking runtime binding, and the child starts from full copied history rather than a parent compacted context.

The parent bytes and metadata remain unchanged. Child records are fully replay-validated before exclusive transcript creation and `latest` update. Invalid boundaries, incomplete sources, UUID collisions, record overflow, or creation failures do not install a ProjectSession child in memory. Failures before `latest` replacement durably remove the newly created transcript and lock file; cleanup failure is reported rather than hidden. If `latest` replacement occurred but its directory durability confirmation failed, the child is retained because `latest` may already reference it and the error truthfully remains a durability-unknown partial outcome. If ProjectSession cannot construct the child AgentLoop after durable creation, it releases the child writer lease and keeps the current in-memory Session unchanged rather than leaking an active writer.

## Compatibility

This slice adds only the record-local `session_forked` v1 type and optional replay metadata. Session header remains v2, `turn_committed` remains v8, Action Audit and compaction schemas remain unchanged, and old transcripts require no migration. System prompt remains v21, adapter contract remains v25, the 21-tool catalog remains unchanged, and Effective Context representation versions remain `ctx-v3`/`ctx-v4`.

## Non-goals

- parent mutation, branch merge, rebase, or automatic conflict resolution;
- copying parent Action Audit, provider usage, failures, compact summaries, or organization metadata;
- partial-turn, raw-record, or unresolved-action boundaries.
