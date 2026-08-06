# 0119: Early Session Title Preparation and Terminal Rendering Safety

- Status: Accepted
- Date: 2026-08-06
- Scope: first-turn title timing, shared invocation accounting, transient TTY identity, and approval rendering

## Context

ADR 0071 made the first successful turn and its automatic title one atomic durable record, but title generation still ran from the commit callback after the complete provider/tool loop. A long MCP turn could therefore remain `New session N` until every tool completed, and a loop that consumed all 24 provider invocations left no title opportunity. The persistent TTY also rendered colored approval text through a later untrusted-text wrapper; that wrapper correctly escaped ANSI controls, but consequently exposed them as literal `\x1b[...]` text. Pre-rendered lines that filled the physical final column could similarly trigger a terminal-owned soft wrap that bypassed Leonervis hanging prefixes.

## Decision

`AgentLoop` accepts one Host-only first-response hook. It runs exactly once after the first ordinary provider response is complete and before any requested tool is dispatched. The hook returns the number of additional provider invocations it consumed; AgentLoop validates that count and includes it in the existing 24-invocation ceiling and later invocation numbering. Ordinary callers that install no hook retain unchanged behavior.

For an unnamed first turn, `ProjectSession` uses that hook to perform the existing bounded no-tools title protocol. Duplicate checks and up to three attempts now happen immediately after the first response. The selected model or Host-fallback title remains only process-local pending state. A content-free `SessionTitlePrepared` event lets the real TTY show that pending name immediately, but a failed or cancelled turn restores the previous toolbar identity. Only the eventual successful `turn_committed` append persists the title, source, fallback reason, conversation, and provider usage together. Commit-time duplicate revalidation still runs under the Session-store boundary; a new collision receives a stable numbered fallback.

Persistent terminal output reserves one physical right-edge column before startup status, Markdown, plain-text, Host-trace, slash, and approval wrapping. Automatically selected content width is additionally capped at 100 display cells. This produces real newlines and continuation prefixes in a wide terminal instead of relying on a narrower IDE pane, transcript viewer, or copied-text viewport to perform a second prefix-free soft wrap. The persistent renderer refreshes that usable width before each visible activity event and final response while retaining any buffered streaming suffix, so a long MCP or Task Turn does not continue wrapping against a stale pre-resize viewport. Startup runtime and Session details use the same secondary Host-block indentation instead of allowing long transcript paths to soft-wrap at the terminal edge. The banner retains its compact horizontal form when it fits and switches to a vertically stacked, hanging-indented form on a narrow terminal. Approval content is rendered as terminal-safe plain text before the outer trace applies semantic warning color, so embedded ANSI bytes are never reinterpreted or escaped into visible `\x1b` sequences. The terminal's existing role and trace prefixes remain authoritative for continuation alignment.

## Compatibility and contracts

No Session record, provider wire, title prompt, system prompt, tool definition, Action Audit, Effective Context, or adapter representation changes. `SessionTitlePrepared` is ephemeral Host UI state and is neither persisted nor sent to a provider. Existing title usage remains `ProviderInvocationKind.TURN`, and the total per-turn ceiling remains 24.

## Explicit non-goals

- persisting a title before the first turn succeeds;
- running title generation in a background thread or against another runtime;
- letting title requests bypass provider budgets or cancellation;
- interpreting ANSI or other terminal controls from approval content;
- changing redirected or non-TTY output into terminal-styled output.

## Verification

Deterministic tests cover hook ordering before tool dispatch, shared final-invocation pressure, early title preparation during a tool turn, atomic title usage persistence, duplicate fallback behavior, transient toolbar rollback on failure, colored approval output without literal ANSI escapes, one-column right-edge reservation, CJK and Markdown wrapping, and Host-trace continuation prefixes. The complete offline release gate remains required.
