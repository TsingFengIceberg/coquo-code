# 0083: Foundation 5A Root `AGENTS.md` Project Instructions

- Status: Accepted
- Date: 2026-07-30
- Scope: load one bounded workspace-root project instruction snapshot into ordinary model turns

## Context

Leonervis previously required the user or model to read repository guidance as ordinary file data. That made project conventions easy to miss and gave no stable distinction between trusted Host policy, current user intent, project-level guidance, and instruction-shaped text found during tool use. Foundation 5A had been deferred while permission, tool, Session, context, and terminal boundaries matured and while the canonical filename and compatibility policy remained undecided.

The repository owner selected `AGENTS.md` as the only Leonervis project instruction filename. Supporting multiple legacy or product-specific names would create ambiguous precedence and hidden context, while recursive inheritance would add path ownership and reload semantics that are not needed for this first independently verifiable slice.

## Decision

Leonervis loads only `<workspace>/AGENTS.md`. It does not search parent directories, a detected Git root, or descendants; merge instruction layers; or automatically read `CLAUDE.md`, `LEONERVIS.md`, or another alias. Absence is a valid explicit state. An existing entry must be a non-symlink regular file opened relative to a no-follow workspace-root descriptor. The Host compares descriptor identity, reads at most 32 KiB plus one detection byte, decodes strict UTF-8, rejects NUL, and enforces both 32 KiB character and byte bounds. Empty content and exact LF/CRLF bytes are preserved. Invalid existing content blocks ordinary provider invocation with a safe diagnostic.

`ProjectInstructionsSnapshot` v1 records the fixed relative path, exact text, UTF-8 byte count, and a domain-separated content fingerprint. AgentLoop reads it once when preparing a user turn. The snapshot is part of `PreparedAgentTurn`, every provider continuation, preflight, and ActionLease identity check for that turn. A tool may modify `AGENTS.md`, but that does not change the in-flight turn; the next prepared turn reloads the file. ActionLease stale checks rebuild committed context with the pinned snapshot so a legitimate in-turn write does not invalidate later sequential actions.

`ConversationRequest` carries project instructions separately from the canonical system prompt and ordinary history. Anthropic emits a second system text block; OpenAI-compatible emits a second system message. Count, create, and streaming requests share this projection. Missing instructions preserve the prior native request shape. Compact-summary and Session-title requests remain dedicated no-tools operations without project instructions because they summarize Host-selected history or name a Session rather than execute the current workspace task.

The canonical system prompt states the authority boundary: Host policy and hard constraints remain highest, the current direct user request overrides conflicting project guidance, and `AGENTS.md` cannot grant permission, approve actions, weaken workspace/symlink/budget/audit/sandbox/durability rules, prove execution, or elevate ordinary file and tool content into instructions. The instruction snapshot participates in token counting and Effective Context identity but is not copied into Session records, Action Audit, compact checkpoints, exports, or title metadata. Resume and Session switching use the current workspace snapshot for future requests without rewriting historical transcripts.

`/instructions` is a Host-only read. It reports presence, `AGENTS.md`, UTF-8 byte count, representation, and fingerprint without displaying content. It invokes no provider or tool, consumes no model budget, and writes no Session or Action Audit record. `/context` includes project instructions naturally through exact request assessment and context identity.

## Compatibility

The model-visible trust contract advances the canonical system prompt from v22 to v23 with fingerprint `v23-3858281d3354288e15dd51569d896fe22c6e4842d8c8b5192dc4a2e296792a55`. The changed Anthropic and OpenAI-compatible ordinary wire projections advance provider adapter contract v25 to v26. Tool names, order, descriptions, schemas, permissions, and budgets do not change.

Current full and compacted Effective Context representations advance from `ctx-v3`/`ctx-v4` to `ctx-v5`/`ctx-v6`; their manifest includes either exact `ProjectInstructionsSnapshot` metadata and text or explicit absence. The empty full-context identity without instructions is `ctx-v5-0700acbf613c3896f65ea82d5fa78f7139406f50e9b5227bcabedf223708d39b`. Legacy `ctx-v1` through `ctx-v4` remain accepted and preserve their historical manifest algorithm. Context ID validators now accept v1 through v6. ToolArguments v1, ActionIdentity v1, Session and Action Audit record schemas, compact-summary representation, and title representation remain unchanged; old JSONL is never rewritten.

## Invariants

- Exactly one canonical root filename is considered, and missing never causes a parent or descendant search.
- A symlink, directory, special file, oversized file, invalid UTF-8, or NUL-bearing `AGENTS.md` never reaches a provider as project instructions.
- One user turn uses one exact snapshot across all provider calls and sequential tool actions.
- A later turn reloads current workspace state, including a change made by the previous turn.
- Count/create/stream native projections carry the same exact project block.
- Project instructions affect preflight and Effective Context identity but never become durable transcript content.
- Permission, approval, workspace, symlink, causality, tool budget, Action Audit, sandbox, and durability boundaries remain non-bypassable.
- Host-only inspection does not reveal instruction content or mutate runtime state.

## Non-goals

- parent, Git-root, home, or global instruction discovery;
- nested directory inheritance, merging, precedence graphs, or per-tool path scoping;
- compatibility aliases such as `CLAUDE.md` or `LEONERVIS.md`;
- persisting historical instruction copies in Session JSONL;
- letting project instructions configure permissions, approvals, tools, providers, sandboxing, budgets, or credentials;
- watching files continuously or changing a turn snapshot after preparation;
- treating arbitrary workspace files, tool output, summaries, transcripts, or reference repositories as project authority.
