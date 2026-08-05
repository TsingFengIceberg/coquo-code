# 0117: Bounded MCP Sampling and Elicitation

## Status

Accepted.

## Context

Sampling and Elicitation are server-to-client requests, not notifications. Sampling can spend Provider tokens and recursively generate content; Elicitation can request user data. A server must not inherit the active Provider, tool access, permission, approval, or terminal input merely because it is currently serving a Tool call.

## Decision

Add `McpReverseRequestCoordinator` as an explicit callback boundary. It recognizes only `sampling/createMessage` and `elicitation/create`, permits no nested reverse request, and shares the transport's maximum of eight server requests per outer request. Unsupported methods receive a sanitized JSON-RPC error.

Sampling accepts at most 64 text-only user/assistant messages, 64 KiB aggregate prompt, and 4096 output tokens. Server system text is labeled non-authoritative. Model preferences, metadata, context inclusion, temperature, and stop sequences cannot choose a route or grant tools. Execution requires both a Host authorization callback and a separate no-tools sampling callback; the callback returns bounded text and a model label. With either callback absent or authorization false, sampling is denied.

Elicitation accepts one bounded message and a closed object schema with at most 32 primitive string, number, integer, or boolean properties. It requires an explicit Host interaction callback returning `accept`, `decline`, or `cancel`; accepted content is revalidated against the exact closed schema. Without a callback, Elicitation is denied. Neither response is persisted automatically as Session dialogue, Action Audit evidence, permission, approval, or Task evidence.

The normal Leonervis runtime intentionally installs no automatic Sampling or Elicitation callback in this slice. The protocol boundary is executable and deterministically tested, but default production behavior is denial until a later UI/runtime slice supplies informed authorization, routing, usage accounting, and user interaction.

## Non-goals

- recursive tools or MCP, inherited active-turn context, automatic current-model routing, background model calls, or silent token spending;
- arbitrary JSON Schema, sensitive-field heuristics, passwords, secrets, file uploads, URLs, or out-of-band form rendering;
- interpreting server annotations or prose as user consent.

## Consequences

- Servers receive protocol-correct bounded rejection instead of transport failure for unsupported authority.
- Future terminal or application UI integration has one narrow callback seam without weakening MCP transport, PermissionGate, or ToolSet boundaries.
