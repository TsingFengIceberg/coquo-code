# 0091: Resume Runtime Binding at the Durable Commit Point

- Status: Accepted
- Date: 2026-07-31

## Context

Session resume deliberately keeps the caller's current runtime instead of recreating the historical provider binding. The schema-v1 `session_resumed` record reopened lifecycle state but left replay's current binding at the last historical header, turn, failure, or runtime-change record. A resumed text-only turn eventually replaced that binding at `turn_committed`, hiding the gap. If the first resumed provider response requested a tool, however, `action_requested` correctly required its pinned current-runtime binding to equal replay state and failed before the turn could commit. This was reproducible when a fake-bound owner Session was resumed with a real profile and a durable Task Stage called `list_directory` first.

## Decision

New `session_resumed` records use record-local schema v2 and include the redacted `BindingSnapshot` selected and context-screened by the caller. Startup resume and live Session switching pass the exact pinned context-transition runtime binding into `PreparedSessionResume.commit`. The candidate replay installs that binding from the same `session_resumed` record before any later action can be admitted.

The resume record remains the single append-and-fsync semantic commit point. The implementation does not append a second `runtime_changed` record after resume, because failure between two records would reopen the Session while leaving the Action Audit baseline stale. `SessionStore.open` remains a low-level compatibility wrapper and uses the replayed binding when no explicit current binding is supplied.

Schema-v1 `session_resumed` records remain readable and preserve their historical behavior: they reopen lifecycle state without changing the replayed binding. Existing transcripts are never rewritten. Schema-v2 records require exactly one valid binding, and schema-v1 records reject a binding field.

## Invariants

- Resume never reconstructs or activates a runtime from transcript provenance.
- Context screening and the durable resume record refer to the same pinned current runtime.
- The first resumed `action_requested` is checked against an already durable current binding.
- `action_requested` binding equality and Action lease validation remain strict.
- `SessionResumed` fsync remains the semantic resume commit point and existing partial latest-pointer outcomes remain truthful.
- Legacy records replay without normalization or prefix rewriting.

## Compatibility

This is a Host-only Session record change. Canonical system prompt v25, provider adapter contract v26, all 21 model-visible tool schemas, ToolArguments v1, Action Audit schema v1, Task records, and Effective Context representations and identities remain unchanged.

## Non-goals

- restoring the historical provider, credential, profile, or output budget;
- weakening action binding checks or allowing tools before durable resume;
- changing Session history projected to the model;
- retrying the failed Task Stage automatically.
