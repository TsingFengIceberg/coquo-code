# 0101: turn_committed v5 Inherited Assistant Content Replay

## Status

Accepted.

## Context

`turn_committed` v3 added optional assistant companion text to ordinary `tool_use` items. Version 4 added atomic `assistant_tool_batch`, and version 5 added the Host tool ledger. Historical v5 writers therefore persisted the inherited v3/v4 fields together with the new ledger, including required `assistant_text: null` on ordinary tool uses without companion text.

The current item codec enumerated supported versions manually and accidentally skipped v5 in both the general item-version set and the inherited assistant-content capability sets. Strict replay consequently rejected a semantically valid historical v5 record as `tool_use contains unknown field: assistant_text`. First-turn automatic Session naming could surface the defect after model output because duplicate-title checking strictly lists every workspace Session before the new turn commit.

## Decision

Admit v5 in the item codec and express inherited item capabilities by their introduction boundary: every supported schema at v3 or later accepts and emits ordinary `tool_use.assistant_text`, while every supported schema at v4 or later accepts and emits `assistant_tool_batch`. Version validation remains closed to the eight known `turn_committed` schemas, so the monotonic capability checks do not admit unknown future versions.

Keep v1/v2 strict: they still reject `assistant_text`, and schemas before v4 still reject batches. Keep v5 ledger validation unchanged. Add deterministic coverage for v5 batches, null and non-null ordinary companion text, strict Session-store listing, and exact replay of the previously failing historical record shape.

## Compatibility And Versions

No transcript is rewritten or normalized. Canonical system prompt remains v29, provider adapter contract remains v31, ToolArguments remains v1, Effective Context remains `ctx-v5`/`ctx-v6`, and all current Session, Task, Action Audit, compaction, permission, tool, and budget versions remain unchanged.

## Consequences

- Valid v5 Sessions remain listable, resumable, searchable, and eligible for automatic title conflict checks.
- Existing v5 bytes retain their original schema and field shape.
- A malformed v5 ledger, assistant companion text, batch, or causality chain still fails strict replay.
- Live model output can no longer be followed by this specific compatibility failure during first-turn Session naming.
