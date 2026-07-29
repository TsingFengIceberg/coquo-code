# 0072: Session Archive, Search, and Title Fallback Diagnostics

- Status: Accepted
- Date: 2026-07-30
- Scope: reversible Session organization metadata, bounded list filtering, and durable automatic-title fallback reasons

## Context

Durable Session names make a workspace history recognizable, but a growing list still lacks a reversible way to hide inactive conversations or find a Session by name. Closing a Session represents runtime lifecycle rather than organization, and physical deletion would destroy append-only audit history. Archive therefore needs its own meaning rather than overloading close, resume, or the `latest` selector.

Automatic first-turn naming also previously exposed only `source=fallback`. That proves the model title was not used but does not distinguish a provider output limit from a provider failure, malformed candidate, repeated collision, or exhausted shared invocation budget. Operators need a durable safe diagnosis without persisting provider exceptions, raw responses, or title prompt content.

## Decision

Add `session_archive_changed` schema v1 as an append-only record containing `occurred_at` and a boolean `archived`. Latest valid record wins. Archive is reversible organization metadata only: it does not close, delete, switch, summarize, or rename a Session and does not alter history, runtime binding, Effective Context, the workspace `latest` pointer, or the UUID resume identity. Archived Sessions remain valid current Sessions and remain resumable by complete UUID or `latest`. Repeating the already-current state is idempotent and appends no record.

The REPL adds `/session archive` and `/session unarchive`. Session detail output reports `Archived: yes|no`, list summaries append `archived` to lifecycle state, and the TTY toolbar adds a bounded `[archived]` suffix. Archiving does not automatically create a replacement Session because changing two transcripts plus `latest` would not be one atomic Session operation.

`/session list` accepts at most one argument from each independent filter dimension: result count `1-100`, `open|closed`, `active|archived`, exact `model=<name>`, and `name=<text>`. Name matching is a case-insensitive literal substring over validated display metadata. The value is one non-whitespace token bounded to 80 characters and 256 UTF-8 bytes. Filters compose over strictly replayed workspace Sessions in existing newest-first order. Empty results remain explicit.

Advance new `turn_committed` records from schema v7 to v8 with nullable `session_title_fallback_reason`. A first-turn title whose source is `fallback` must carry exactly one closed reason: `provider_output_limit`, `provider_failure`, `invalid_candidate`, `duplicate_title`, or `invocation_budget`. A model title, unnamed ordinary turn, or v7 record must not carry a reason. The title, reason, first-turn conversation, and provider usage remain one durable append; failed commit leaves all uncommitted.

Title generation classifies structured output-limit failures separately from other provider failures. Candidate parsing and collision retries retain the latest bounded reason, while no remaining title invocation starts with `invocation_budget`. A typed content-free Host event reports that fallback was applied. Terminal and standalone Session rendering map only the enum to safe prose and never expose provider output, exception text, stack traces, first-user content, or rejected titles.

## Compatibility and contracts

`turn_committed` v1-v8 replay remains closed and fail-fast. v7 first-turn names decode without a reason and are not rewritten; v1-v6 behavior is unchanged. `session_archive_changed` v1 participates in strict replay and cannot appear while an Action Audit lifecycle is incomplete. Resume, close/release, tail recovery, and mixed legacy-prefix append behavior retain their existing rules.

This slice changes Session metadata, Host slash commands, and terminal presentation only. Canonical system prompt remains v21, provider adapter contract remains v25, the 21 model-visible tools and their order remain unchanged, and ToolArguments v1, ActionIdentity v1, Action Audit v1, `turn_failed` v2, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. Archive state, list filters, and fallback reasons enter neither provider history nor compaction or Effective Context identity.

## Explicit non-goals

- physical Session deletion, transcript rewriting, trash, retention policy, or automatic cleanup;
- automatically starting a new Session after archive or excluding archived Sessions from `latest` and exact resume;
- name-based resume, fuzzy search, regular expressions, multi-word quoted filter parsing, tags, folders, pinning, or sort controls;
- retrying title generation beyond the existing bounded attempts or exposing raw provider failure details;
- changing model-visible tools, prompts, provider wire formats, permissions, approvals, Action Audit, or compaction.

## Verification

Deterministic tests cover `turn_committed` v8 reason/source pairing and closed enums, complete v7 replay, archive record round trips and validation, archive/unarchive idempotency, unchanged history/runtime/latest identity, release/resume state, combined name/archive/lifecycle/model filtering, help/completion/usage errors, Session detail and list rendering, toolbar suffixes, safe fallback event text, and structured provider output-limit classification. The complete offline release gate remains required; no credential, network request, provider endpoint, or API cost is used.
