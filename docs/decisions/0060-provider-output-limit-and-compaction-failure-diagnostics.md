# 0060: Provider Output-limit and Compaction Failure Diagnostics

- Status: Accepted
- Date: 2026-07-28
- Scope: normalized output-limit failures, failed-generation usage accounting, terminal state guidance, and non-reducing compaction evidence

## Context

Real DeepSeek-compatible testing reached a configured 4096-token output reserve while asking for long structured answers. The adapter correctly rejected the truncated response, but exposed it only as a generic `response_invalid` error. The terminal did not show the requested limit or usable provider-reported usage, and Runtime accounting marked the failed invocation unknown even when the completed response metadata contained exact counts. A separate manual compaction test generated a summary but rejected it because the candidate was not smaller; the failure omitted the already comparable source and candidate input counts.

These failures must remain atomic with respect to conversation state. A truncated response cannot become final assistant text, an incomplete tool call cannot execute, and a non-reducing summary cannot become an Effective Context checkpoint. At the same time, truthful failure handling must not erase provider usage or imply that tool side effects completed earlier in the attempted turn were rolled back.

## Decision

`ProviderFailureKind` adds the provider-neutral `output_limit` classification with diagnostic code `output_token_limit`. `ProviderAdapterError` carries bounded Host-only observations: the positive requested output-token limit, an optional strict `ProviderTokenUsage`, and a boolean indicating whether incomplete response content was observed. It never retains raw provider payloads or partial text. OpenAI-compatible and Anthropic non-streaming and streaming adapters use the same classification for ordinary generation and compact-summary truncation.

Adapters parse usable actual usage before raising the output-limit error. OpenAI-compatible streaming accepts the bounded usage-only tail after the finish reason; Anthropic streaming combines validated `message_start` input and `message_delta` output counts. Runtime records that known usage on the failed turn or compaction invocation; malformed or absent metadata remains unknown. The error is still non-retryable because the Host does not know whether a larger limit is supported or whether replaying a tool-bearing attempt would duplicate side effects.

One-shot and REPL presentation report the normalized failure, requested limit, known actual input/output counts or explicit unavailability, and rejection of the incomplete response. They state that no turn was committed and that any already completed tool side effects remain recorded in Action Audit. Streamed partial text remains ephemeral and is explicitly marked uncommitted. The failed prompt may still append the existing safe `turn_failed` audit record, but full/effective conversation history does not gain a turn.

`CompactionCandidateError` adds optional complete source/candidate input evidence. The non-reducing path reports `before -> after` with the shared count method. Manual and automatic compaction continue to install no summary or checkpoint when the candidate is not smaller; full history and Effective Context remain unchanged, while the compaction generation's process-local usage remains visible through `/usage` when known.

## Contracts and compatibility

The provider adapter contract advances from v21 to v22 because normalized failure transport and failed-invocation usage accounting changed. Native request bodies, successful response shapes, tool projection, and provider history projection do not change. Canonical system prompt v19, the 17 model-visible tools and their order, ToolArguments v1, ActionIdentity v1, `turn_committed` schema v5, Action Audit schema v1, `context_compacted` v2/v3, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. Existing Sessions require no migration or rewrite; historical provider bindings remain audit provenance only.

## Explicit non-goals

- automatically retrying or continuing a truncated response;
- committing or persisting partial assistant text as a successful turn;
- increasing a profile's output reserve or adding a runtime `/output` command;
- rolling back completed tools or claiming an attempted turn was transactionally reversible;
- persisting token usage as a billing ledger;
- predicting candidate compaction size in `/compact preview` before a summary exists.

## Verification

Deterministic tests cover both adapters in non-streaming and streaming output-limit paths, requested limits, partial-response flags, known usage transport, Runtime turn and compaction accounting, one-shot and REPL guidance, non-reducing compaction evidence, unchanged transcript/effective state, absent checkpoints, adapter contract v22, and existing compatibility boundaries. The complete offline release gate remains required; no credential, network, or provider API cost is used without separate authorization.
