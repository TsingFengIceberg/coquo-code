# 0062: Durable Session Provider Usage Audit

- Status: Accepted
- Date: 2026-07-28
- Scope: terminal-operation usage persistence, strict replay compatibility, and Session-level inspection

## Context

ADR 0058 introduced provider-neutral actual token usage, but retained it only in the current process. Restarting or resuming therefore lost earlier turn and compaction totals even though the Session still retained the corresponding successful turns, failed attempts, and checkpoints. Persisting only aggregate counters would erase invocation order, hide unknown provider metadata, and make tool continuations impossible to audit accurately.

Usage is provider-reported operational evidence, not conversation content or a billing ledger. It must never enter Effective Context, influence model identity, turn an unknown count into zero, expose raw provider responses, or recreate the current runtime from historical bindings.

## Decision

Each new terminal operation stores an ordered tuple of provider invocations with operation-local indexes. Every entry contains either a strict bounded input/output pair or an explicit unknown value. Successful ordinary generations are stored atomically in `turn_committed` schema v6, including every tool continuation. Failed ordinary generations are stored in record-local `turn_failed` schema v2. Successful compactions store their summary invocation in `context_compacted` schema v4, while unsuccessful compactions append a Host-only `compaction_failed` schema-v1 audit record with trigger, safe failure classification, binding, and usage.

Old `turn_committed` v1-v5, `turn_failed` v1, and `context_compacted` v2/v3 records remain readable without rewriting. Their missing usage is presented as legacy-unavailable rather than as zero or unknown provider metadata. `compaction_failed` never enters full or effective history and does not imply that a summary checkpoint was installed. A failed compaction may therefore append audit evidence while full history, Effective Context, and checkpoint state remain unchanged.

`/usage` retains its process-local current-runtime meaning. `/usage session` derives totals from strict replay across committed turns, failed turns, committed compactions, and failed compactions. `/usage turns` shows up to ten recent committed or failed turn records with redacted provider/model identity and known, unknown, or legacy-unavailable status. Resume and process restart preserve these Session projections; runtime switching still controls only the process-local counters.

Provider usage is captured from the same pinned runtime that produced the terminal record. Success persistence remains atomic with the successful turn or checkpoint. Failure records persist usage together with the existing safe terminal failure. No token price, cache-token subdivision, reasoning-token subdivision, credential, raw response, partial assistant text, or provider billing claim is stored.

## Contracts and compatibility

Canonical system prompt remains v19, provider adapter contract remains v22, and the 17 model-visible tools and order are unchanged because usage audit is Host-only. ToolArguments v1, ActionIdentity v1, Action Audit v1, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. New records use `turn_committed` v6, `turn_failed` v2, and `context_compacted` v4; existing Session prefixes are never rewritten.

## Explicit non-goals

- estimating missing provider usage or treating unknown as zero;
- calculating price, invoices, quotas, or cross-provider billing equivalence;
- persisting process-local context meters or temporary output-budget overrides;
- exposing usage to the model or including it in compaction summaries;
- retrying failed provider calls or rolling back completed tools;
- promising crash-proof accounting for a provider call whose terminal record could not be durably appended.

## Verification

Deterministic tests cover strict known/unknown encoding, operation-local ordering, legacy replay, successful and failed compaction audit, known turn usage across resume, process-local versus durable command presentation, bounded recent-turn inspection, unchanged model-visible contracts, and the full offline release gate. No credential, network request, real provider, or API cost is used.
