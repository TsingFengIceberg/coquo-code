# 0097: Informed Task Admission and Foreground Handoff

## Status

Accepted.

## Context

ADR 0096 introduced model-proposed Task admission and an idempotent cross-Task/Session creation boundary. Its first command surface accepted a proposal immediately, however, so the operator could not inspect the exact Task name, cumulative budget, completion policy, prepared acceptance criteria, or workspace-derived criterion baselines before creation. Acceptance also needed a clearer separation from provider-backed foreground driving.

The remaining boundary is informed admission: a model proposal is untrusted input, user review must cover the exact Host-prepared Task candidate, and a later confirmation must fail if configuration or relevant workspace state changed. Recovery must preserve one sourced Task when provider startup, Session resolution, or Task proposal persistence fails.

## Decision

`/task proposal accept <admission-id> [<config-json>]` is now a read-only preview. The optional strict JSON configuration may select the Task name, completion policy, cumulative budget, and replacement structured acceptance criteria. Missing criteria preserve the model-proposed text as human criteria. The preview creates no Task, writes no Session or Task record, and invokes no provider.

The Host canonicalizes and validates the candidate, prepares workspace-derived acceptance facts such as a `path-unchanged` SHA-256 baseline, and displays the exact name, objective, policy, budget, criteria, configuration SHA-256, confirmation SHA-256, and follow-up command. Acceptance requires `/task proposal accept <admission-id> confirm <sha256> [<config-json>]`. The domain-separated confirmation digest binds the proposal, source Session and Turn, owner Session, canonical configuration, and fully prepared candidate. The Host prepares the candidate again before creation, so stale workspace evidence or changed configuration rejects the old confirmation.

New `task_admission_origin` v1 records include both configuration and confirmation SHA-256 values. The schema remains v1 because the record type has not appeared in a committed release. If Task creation succeeds but the source Session resolution append fails, an exact retry validates both digests against the existing sourced Task and appends only the missing resolution. A different configuration, confirmation, duplicated source, or conflicting resolution fails closed.

Acceptance and execution remain separate. A successful confirmation creates and resolves one durable Task but invokes no provider and executes no Stage. `/task proposal drive <admission-id> [1-16]` resolves only an already accepted current-Session proposal to its exact sourced Task and enters the existing bounded foreground Driver. Pending or rejected proposals cannot drive. The Driver still uses ordinary Task Stage, Turn, PermissionGate, approval, Action Audit, budget, cancellation, checkpoint, and recovery boundaries.

Provider startup failure records one failed Stage with observed usage and preserves the accepted admission. Restart resumes the same owner Session and can drive a new bounded Stage without recreating the Task. A committed Session Turn whose final Task proposal append failed is reconciled through `/task recover` from exact committed ToolUse and successful Host-ledger evidence; recovery never reruns the provider or duplicates the Stage.

The deterministic offline suite advances to `host-baseline-v2`. Its fifth case covers the complete admission lifecycle: ordinary proposal, exact preview and confirmation, foreground planning, explicit plan acceptance, execution completion proposal, human criterion verification, and final completion. Scoring strictly replays three committed Turns and their chronological tool ledgers, the accepted admission and valid source binding, exactly one completed Task with planning and execution Stages, unchanged workspace content, and no Action Audit for proposal-only coordination.

## Compatibility And Versions

The canonical model-visible tool schemas and exposure rules do not change, so system prompt v28 and provider adapter contract v29 remain current. Effective Context representations remain `ctx-v5` and `ctx-v6`, including the existing empty full-context identity. ToolArguments v1, `turn_committed` v8, `task_admission_resolved` v1, Action Audit, compaction, ordinary Turn budgets, and existing Session and Task records remain unchanged.

The terminal command semantics extend ADR 0096: old source transcripts remain readable, but the current `/task proposal accept` command requires preview followed by exact confirmation. Automatic acceptance, automatic driving, background execution, implicit cross-Session mutation, generic automatic retry, and automatic independent-review spending remain out of scope.

## Consequences

- The user sees and confirms the exact durable Task candidate instead of approving only model prose.
- Stale acceptance evidence and configuration drift fail before Task creation.
- Partial cross-store commits remain exactly retryable without duplicate Tasks.
- Acceptance does not silently spend provider tokens or begin execution.
- Startup and proposal-persistence failures retain truthful Stage and Session causality across restart.
- The offline Host Eval now guards the complete admission-to-completion integration path.
