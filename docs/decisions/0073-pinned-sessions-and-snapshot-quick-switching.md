# 0073: Pinned Sessions and Snapshot-based Quick Switching

- Status: Accepted
- Date: 2026-07-30
- Scope: reversible Session pins, composable pin filtering, and process-local numbered navigation over exact resume identities

## Context

Archive separates inactive Sessions from active work, and name filtering can locate a known conversation. Users still need a lightweight way to retain important Sessions and switch among recent matches without copying a complete UUID. A display name is not globally unique, while a bare list index can silently refer to another Session if the directory changes between listing and selection.

The existing resume path already owns context screening, transcript staleness, durable `session_resumed`, writer transfer, and `latest` update. Quick navigation must reuse that transaction rather than creating a second resume implementation or treating terminal selection as proof that a switch committed.

## Decision

Add append-only `session_pin_changed` schema v1 with `occurred_at` and boolean `pinned`. Latest valid record wins. Pin is reversible organization metadata only and does not rename, archive, close, resume, summarize, or otherwise change a Session. It preserves complete history, runtime binding, Effective Context, `latest`, and UUID identity. Repeating the current state is idempotent and appends no record. Legacy transcripts have no pin record and replay as unpinned without rewriting.

The REPL adds `/session pin` and `/session unpin`. Session detail output reports `Pinned: yes|no`, list summaries append `pinned` to the metadata state, and the bounded TTY toolbar adds a pin marker. `/session list` accepts one independent `pinned|unpinned` filter and composes it with its existing count, lifecycle, archive, exact-model, and literal-name filters. Default list ordering remains newest-first rather than moving pinned Sessions implicitly.

Add one mutable `SessionSwitchCatalog` owned by each REPL frontend. It stores only an ordered tuple of complete Session UUIDs and is never persisted. `/session switch` strictly replays the workspace list, excludes the current Session, and creates a newest-first snapshot of at most ten entries. `/session switch list` accepts the same filters as `/session list` with a maximum of 20 entries. Preview rows reuse validated Session metadata and include number, display name, complete UUID, turn count, lifecycle/archive/pin state, creation time, and durable provider/model provenance. They do not read conversation content.

`/session switch <number>` consumes the entire current snapshot before resolving the selected number. An absent, invalid, or already-consumed snapshot cannot fall back to a fresh list and instead instructs the user to rebuild it. Every new picker build clears the previous snapshot before reading or filtering, so a failed refresh cannot leave old numbers active. Ordinary model prompts, new Session creation, rename, archive/unarchive, pin/unpin, and direct `/resume` also clear it. Number is therefore transient terminal navigation, never a Session selector accepted by storage.

A valid number yields the stored complete UUID and calls the same `ProjectSession.switch_session` used by `/resume`. Target-aware prepare, current-runtime screening, transcript and `latest` stale/CAS validation, durable resume commit, writer transfer, and partial latest-pointer outcomes remain unchanged. Known context rejection, precommit failure, stale conflict, and invalid target preserve the current Session and runtime. After the semantic resume commit point, any pointer durability uncertainty remains a truthful existing partial result rather than rollback.

## Compatibility and contracts

`session_pin_changed` v1 is a closed ordinary Session metadata record and cannot cross an incomplete Action Audit lifecycle. Existing records, `turn_committed` v1-v8, resume, close/release, tail recovery, and mixed legacy-prefix append rules remain unchanged. Pin and picker data enter neither full/effective provider history nor summaries, checkpoints, or Effective Context identity.

This slice is Host-only. Canonical system prompt remains v21, provider adapter contract remains v25, the 21 model-visible tools and order remain unchanged, and ToolArguments v1, ActionIdentity v1, Action Audit v1, `turn_failed` v2, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged.

## Explicit non-goals

- name-based or number-based persistent resume identity;
- fuzzy search, tags, folders, automatic pin ranking, drag ordering, or cross-workspace favorites;
- full-screen TUI selection, mouse interaction, queued prompts, or a second input-ownership system;
- physical deletion, retention policy, transcript rewriting, automatic archive, or automatic cleanup;
- Session fork/branch, history copying, merging, export, or remote Sessions.

## Verification

Deterministic tests cover pin record round trips and strict boolean validation, idempotent append behavior, legacy unpinned defaults, release/resume persistence, unchanged history/runtime/latest identity, combined pinned filtering, Session detail/list/toolbar presentation, help and completion, bounded picker filtering, exclusion of the current Session, exact UUID mapping, one-use consumption, invalid and unavailable snapshot rejection, failed-refresh invalidation, and reuse of existing resume result rendering. The complete offline release gate remains required; no credential, network request, provider endpoint, or API cost is used.
