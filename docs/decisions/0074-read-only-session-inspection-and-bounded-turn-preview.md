# 0074: Read-only Session Inspection and Bounded Turn Preview

- Status: Accepted
- Date: 2026-07-30
- Scope: target Session metadata inspection and bounded final-turn preview without resume or durable mutation

## Context

Session naming, filtering, pinning, and numbered switching make stored work discoverable, but checking whether a candidate contains the desired conversation still requires a state-changing resume. Resume intentionally screens context, appends `session_resumed`, transfers the writer, and updates `latest`; those effects are unnecessary when the user only wants to inspect metadata or recent final dialogue.

The transcript may contain large untrusted user and provider text, tool companion text, tool results, Action Audit records, usage, and compaction checkpoints. A terminal preview must be bounded and control-safe, and it must not imply that a simplified user/final-assistant view is the complete durable audit.

## Decision

Extend the REPL with `/session show [latest|session-id]`. No argument retains the existing current-Session behavior. An explicit target accepts only `latest` or a canonical lowercase UUID4 and strictly replays that Session's metadata without resuming it. Names, picker numbers, filesystem paths, and abbreviated identifiers are not accepted as REPL inspection identity.

Add `/session preview <latest|session-id> [1-10]`, with a default of three turns, and standalone `session preview [selector] --limit N`, whose selector defaults to `latest`. `SessionStore.preview` validates the 1-to-10 limit, performs existing-only target selection, and calls strict replay with tail repair disabled. It returns validated `SessionInfo`, the total committed-turn count, and only the selected recent `ConversationTurn` values in chronological order.

Preview renders each selected turn's final `UserMessage` and final `AssistantText`. It does not include assistant tool companion text, `ToolUse`, `AssistantToolBatch`, `ToolResult`, tool ledgers, Action Audit, provider usage, failures, or compaction summaries. Existing `/tools`, `/actions`, `/history` for the current Session, standalone audit commands, and the append-only transcript remain the authoritative paths for those facts.

All persisted text is treated as untrusted terminal data. Control characters are escaped before display. The complete rendered preview is capped at 32 KiB of UTF-8 and ends with an explicit truncation marker when necessary. The turn-count bound limits selection but does not replace the byte bound.

Inspection and preview take no writer lease, perform no tail recovery, append no record, invoke no provider, consume no model tool or provider budget, and leave the current Session, `latest`, runtime, history, Effective Context, and process-local switch snapshot unchanged. Existing-only validation also ensures that inspection of an empty workspace does not create `.leonervis-code` state.

## Compatibility and contracts

This slice adds a Host-only read projection and terminal commands. It changes no transcript record or schema, replay representation, Session identity, provider history, compaction checkpoint, Action Audit, tool ledger, or resume commit point. Existing Sessions require no rewrite and are previewed through the same strict current decoder.

Canonical system prompt remains v21, provider adapter contract remains v25, the 21 model-visible tools and their order remain unchanged, and ToolArguments v1, ActionIdentity v1, `turn_committed` v8, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged.

## Explicit non-goals

- full-text cross-Session search, indexing, fuzzy matching, or name-based identity;
- rendering intermediate tool dialogue, raw provider payloads, reasoning, or full Action Audit inline;
- preview by transient picker number or mutation of the current picker snapshot;
- Session resume, fork, branch, merge, export, deletion, retention, or transcript rewriting;
- Markdown execution, terminal escape passthrough, unbounded output, or provider-generated summaries.

## Verification

Deterministic tests cover bounded strict projection, recent-turn ordering, empty-workspace no-create behavior, invalid limits, canonical REPL selectors, selected metadata, terminal-control escaping, the 32 KiB output cap, standalone CLI parity, and unchanged current Session, `latest`, runtime, history, transcript bytes, writer state, and prompt history. The complete offline release gate remains required; no credential, network request, provider endpoint, or API cost is used.
