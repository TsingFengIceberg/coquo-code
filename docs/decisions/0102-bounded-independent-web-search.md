# 0102: Bounded Independent Web Search

## Status

Accepted.

## Context

Leonervis previously had no first-class public-web observation. A model could only use workspace tools or ask `run_command` to invoke an arbitrary program, but the command sandbox intentionally denies socket creation. Treating search as shell execution would also lose exact query approval, fixed endpoint ownership, structured result bounds, backend provenance, and a truthful distinction between Host-executed search and future provider-native search.

Independent search-service APIs, provider-native search, and MCP search tools have different authorization and causality. This slice needs one narrow Host-owned network path with interchangeable fixed service backends, without prematurely defining provider-native events, general URL fetching, MCP transport, or model-selected endpoints.

## Decision

Add `web_search(query, max_results)` as the twenty-second ordinary model-visible tool and thirtieth canonical tool. It is available in ordinary Prompts and matching Task planning, execution, and correction Stages. The model-visible contract is backend-neutral. The Host supports the fixed Brave endpoint `https://api.search.brave.com/res/v1/web/search` and fixed Tavily endpoint `https://api.tavily.com/search`; the model cannot supply or override either endpoint.

Read credentials only from `BRAVE_SEARCH_API_KEY` and `TAVILY_API_KEY`. Exactly one valid credential selects that backend automatically. When both are valid, require either `LEONERVIS_WEB_SEARCH_BACKEND=brave|tavily` at startup or an explicit runtime `/search use` selection before search execution; an invalid selector or unavailable selected credential rejects before PermissionGate. Credentials never enter ToolArguments, ActionIdentity, tool output, terminal summaries, Session records, or Action Audit. Bind the credential-free backend and endpoint fingerprint into ActionPrecondition so approval cannot silently authorize another backend.

Provide a Host-only, process-local source command boundary: `/search status`, `/search sources`, `/search use <source> [source...]`, and `/search reset`. Ordered activation makes the first source primary. This slice executes only that primary source; additional active sources are retained only as the stable future fan-out interface and are never silently queried, billed, persisted, or presented as merged results. The command calls no model or search provider, writes no Session record, and cannot bypass network permission or approval. When both credentials exist, an explicit runtime `/search use` selection is an alternative to the startup environment selector.

Introduce the explicit `network-read` permission action. It is denied in `read-only` and `workspace-write`; `danger-full-access` applies the existing orthogonal `ask | auto` policy. Ask-mode approval displays the exact query, result limit, selected backend, network disclosure, and provider-specific quota warning before execution. Missing or ambiguous backend configuration, credentials containing whitespace, control, or non-ASCII characters, and malformed arguments reject before PermissionGate and create no Action Audit.

The query is limited to 512 characters and 2 KiB. `max_results` is 1 through 10. Production transport disables redirects, requests identity encoding, waits at most 15 seconds, and retains at most 256 KiB. Brave uses one HTTPS GET with `X-Subscription-Token` and fixed bounded query parameters. Tavily uses one HTTPS POST with Bearer authentication and fixes `topic=general`, `search_depth=basic`, one chunk per source, automatic parameter selection off, and generated answers, raw page content, and images off; Tavily documents basic search as one API credit. Each response must be JSON and may contain at most 100 raw result entries. Both formats normalize to provider-ordered, deduplicated safe HTTP(S) results; URLs containing credentials and malformed or control-bearing values are rejected. Output is at most 32 KiB of deterministic JSON Lines with explicit backend provenance and a truncation sentinel.

Every admitted search receives the normal Action lease, PermissionGate decision, optional exact approval, durable Action Audit start/finish, ToolResult causality, per-Turn tool ledger, Session commit, cancellation checks, and ordinary 8/32/24 budgets. HTTP status and malformed response failures are terminal failed outcomes. Timeout or transport uncertainty is `partial` because delivery or API billing may already have occurred; the ToolResult and system prompt forbid automatic retry.

Use an injectable transport for deterministic tests. No test or release gate uses a real credential, endpoint, network request, or API quota.

## Compatibility And Versions

The canonical system prompt advances to v30 and the provider adapter contract advances to v32. The exact catalog and prompt change update the current empty Effective Context identity to `ctx-v5-468d2b764f1b20902080a07d4a00f027eb531ea5651cc90c74b681956bbc80b9`; representation versions remain `ctx-v5`/`ctx-v6`. ToolArguments remains v1, ActionIdentity remains v1, and Session, Task, Action Audit, compaction, and provider-usage schemas remain unchanged. ActionPrecondition gains the closed `expected-configuration-sha256` kind, and the non-persistent ApprovalPreview contract advances to v2 to carry backend disclosure. Old transcripts are replayed without rewriting.

## Non-goals

- provider-native search, provider citation blocks, or provider-owned search billing;
- arbitrary or custom search endpoints, model-selected backends, fallback after a selected backend fails, or credential persistence;
- `web_fetch`, linked-page reading, HTML extraction, SSRF defense, or private-network access;
- MCP search adapters, Skills, dynamic tool discovery, or runtime tool installation;
- automatic retry, search caching, ranking fusion, or a real-provider benchmark.

## Consequences

- Leonervis can perform exact, approved, independently hosted public-web search without granting arbitrary command networking.
- Search queries and API-quota use are visible at the approval boundary, while routine terminal and audit-list views remain content-redacted.
- Brave and Tavily result text is untrusted data and never becomes project or system authority.
- Future provider-native and MCP search work must preserve their own provenance instead of pretending every search followed this Host ToolUse path.
