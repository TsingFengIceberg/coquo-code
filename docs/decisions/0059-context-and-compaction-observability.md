# 0059: Context and Compaction Observability

- Status: Accepted
- Date: 2026-07-28
- Scope: read-only compaction preview, durable checkpoint inspection, context pressure, and recent compaction usage

## Context

Leonervis already performs exact or explicitly estimated preflight, automatic compaction at the fixed 80% threshold, durable `context_compacted` checkpoints, and process-local provider usage accounting. Users can inspect current Effective Context and manually compact it, but they cannot see the fixed compaction selection before generation, review prior checkpoint triggers, or distinguish the latest compaction generation inside `/usage`.

Observability must not become a second compaction implementation. Preview cannot generate a speculative summary, mutate Session state, or claim an after-count that does not exist. Historical inspection must use strict replayed records and must not expose summary text or reconstruct token measurements that the accepted v2/v3 schema intentionally does not persist.

## Decision

`/compact preview` freezes the current Effective Context, applies the existing `plan_compaction()` policy, and performs the same current-target assessment used by `/context`. It reports eligibility, full/effective turns, summary presence, fixed summarized/retained counts, and current pressure. It does not build or send a compact summary request, acquire a compaction transaction, append a record, or install effective state. Provider-owned exact inspection may still issue a count-only request; this is explicitly disclosed and is not generation usage.

`/compactions [count]` reads only the current writer's already strictly replayed records, defaults to five checkpoints, and is bounded to 20. Presentation includes checkpoint sequence and time, schema, trigger and threshold, derived full/summarized/retained turn counts, and previous checkpoint sequence. It excludes summary text, provider binding, context IDs, prompt content, and credentials. Derivation uses replay-validated whole-turn boundaries; malformed records still fail strict Session loading rather than being skipped.

Historical before/after token counts remain unavailable. Existing schema-v2/v3 records intentionally omit transient count and fit evidence, so this slice neither upgrades `context_compacted` nor performs target-dependent historical recounting. Live preview shows only the current source assessment and never predicts candidate reduction before a real summary exists.

Current pressure is a presentation-only classification over `input + requested output reserve`: below 70% normal, 70%-79% approaching the threshold, 80%-89% auto-compact range, 90%-100% near full, above 100% overflow, and unknown when count or window is unavailable. Model-output overflow is separate. The actual pre-turn policy remains authoritative and includes pending user input, so preview does not promise that the next prompt will or will not compact.

The process-local usage snapshot additionally retains the most recent compaction invocation record. `/usage` shows its sequence and actual input/output pair or unknown. It remains separate from durable checkpoint history and resets with the existing runtime-target generation.

## Contracts and compatibility

This is Host-only behavior. Canonical system prompt v19, provider adapter contract v21, model-visible tools, ToolArguments v1, ActionIdentity v1, `turn_committed` v5, `context_compacted` v2/v3, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. Existing Sessions require no migration or transcript rewrite.

## Failure and privacy boundaries

- preview does not generate, persist, or reveal summary text;
- preview assessment failure is a read-only command failure and cannot change Effective Context;
- checkpoint history is bounded and sourced only from strict replay;
- no historical token count is guessed, estimated, or represented as zero;
- usage remains process-local and is not a billing ledger;
- slash commands never enter model history or consume tool budget.

## Verification

Deterministic tests cover eligible preview without transcript mutation or summary generation, durable manual and resumed checkpoint queries, bounded slash parsing, redacted history rendering, pressure and unknown states, latest compaction usage and reset, and unchanged Session/provider/model-visible contracts. The complete offline release gate remains required; no credential, network, or provider API cost is used without separate authorization.
