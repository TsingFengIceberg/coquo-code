# 0112: Bounded MCP Notifications and Catalog Invalidation

## Status

Accepted.

## Context

Persistent MCP processes can emit JSON-RPC notifications while a request is pending. Earlier slices counted and ignored all notifications, which bounded floods but could neither expose safe progress nor react to the standard tools-list-changed signal. Retaining server logging or progress text would leak untrusted data into terminal or durable state, while replacing an active Turn's ToolSet would violate the frozen snapshot and ActionLease boundary.

## Decision

Recognize `notifications/progress`, `notifications/message`, and `notifications/tools/list_changed` under the existing per-request message and notification limits. Validate each recognized shape strictly. Retain only four counts: progress, message, tools-list-changed, and ignored unknown notifications. Never retain notification parameters, logging data, progress messages, tokens, or server prose. A prompt event may expose the first content-free activity fact of each recognized class; sink failures remain isolated and repeated notifications do not flood the terminal.

A tools-list-changed notification never mutates the active `ToolSetSnapshot`, does not discard an otherwise valid current result, and never retries a dispatched call. After the call terminates, the Host retires that process generation and invalidates the cached quarantine catalog. The next catalog-dependent operation refreshes server tools. Existing contract, Registry, ToolSet, ActionLease, and ActionIdentity checks then reject stale execution if identity changed. Incomplete retirement makes the execution partial. Notification counts and catalog-invalidated state remain available on successful, known-failed, partial, and outcome-uncertain paths without including server content.

## Compatibility And Versions

The notification summary is in-memory Host observation, not a Session or Action Audit schema. Provider adapter, MCP configuration, Extension Contract, Registry, ToolSet, ActionIdentity, and transcript versions do not change. The canonical system-prompt version change is recorded with ADR 0113 because both slices share one model-visible contract update.

## Non-goals

- persistent notification history, server log rendering, progress percentages, subscriptions, or resource/prompt invalidation;
- server-to-client requests, sampling, roots, elicitation, or automatic catalog refresh inside the active ToolSet;
- automatic retry, concurrent refresh, or treating a notification as execution or completion evidence.

## Consequences

- Users can observe that an MCP server is progressing, logging, or changing tools without receiving untrusted notification bodies.
- Dynamic tool changes take effect only through a later immutable catalog and ToolSet identity.
- Notification floods and malformed recognized messages fail closed after dispatch and preserve uncertain-outcome semantics.
