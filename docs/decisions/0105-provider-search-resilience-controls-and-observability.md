# 0105: Provider Search Resilience, Controls, and Observability

## Status

Accepted.

## Context

Real DeepSeek Responses search can complete the overall response while retaining a failed `web_search_call`, for example when one Provider-owned `open_page` action is blocked. Compatible Responses relays may also emit optional citation annotations as null, one object, or the nested Chat-compatible `url_citation` shape. Rejecting either condition after streaming valid assistant text leaves the Turn uncommitted even though the Provider invocation itself completed. Provider-native search also lacked process-local force/domain/context controls, content-free terminal progress, durable inspection summaries, and an implemented meaning for ordered independent fallback sources.

## Decision

Treat terminal `web_search_call.status=failed` as a valid Provider-owned item when the enclosing Response is completed. Preserve it unchanged in `turn_committed` v9 and later Responses history. Continue rejecting nonterminal search status in a completed response and noncompleted reasoning or Host function calls. Optional citation metadata accepts null, one mapping, a bounded list, official flat URL citations, and the common nested URL-citation shape. Unsafe or malformed citations are discarded with a content-free warning; they do not invalidate otherwise valid assistant text. Unknown hosted tools, malformed required message content, duplicate item IDs, unsafe URLs, incomplete Responses, and unsupported item states remain fail closed.

Add bounded process-local `NativeSearchRuntimeOptions`: `auto|required`, up to 20 canonical allowed domains, and optional `low|medium|high` search context. OpenAI Responses supports all three and projects required mode as an exact Provider web-search tool choice. Anthropic supports allowed domains, and OpenAI Chat search supports context size; unsupported adapter/option combinations are rejected rather than ignored. `/search mode`, `/search domains`, and `/search context` update only the current runtime and reset on `/search reset` or Provider/model switch.

Provider stream activity and terminal observations remain separate from Host ToolUse. The terminal may display content-free Provider search phases and a bounded summary of call count, failed count, action types, source count, accepted citations, and discarded citations. `/session preview` and `/session turns` derive the same safe call/status/action/source/citation summary from already validated v9 records without showing search queries, URLs, page contents, or Provider reasoning. No Session schema advances.

Ordered sources now define one primary plus explicit model-mediated independent fallbacks. For example, `provider tavily` keeps Provider-native search enabled and exposes the Host `web_search` tool backed by Tavily. The model may call it only after Provider-owned history shows a failed search action or unavailable structured citations. The Host never derives a fallback query, never calls an external backend automatically, and never bypasses `network-read`, PermissionGate, approval, Action Audit, quota disclosure, or tool budgets. There is no fan-out or fusion.

The canonical system prompt advances to v32 and the provider adapter contract to v35. Effective Context representations remain `ctx-v7`/`ctx-v8`, but the prompt change updates the empty full-context identity to `ctx-v7-3ac4ba4e6ffa39c1184cfff6cc4200eb30607553fdf886451c0d967765ff0432`. Profile schema v5/fingerprint v4, `turn_committed` v9, ToolArguments v1, ActionIdentity v1, Action Audit, compaction, and Task schemas remain unchanged.

## Non-goals

- automatically retrying Provider search, scraping blocked pages, or treating Provider failure as Host permission to use another network service;
- guessing a search query from user text, assistant prose, Provider reasoning, or failed Provider action data;
- parallel multi-source search, ranking, result fusion, generic `web_fetch`, or background search;
- persisting runtime search controls or duplicating raw Provider search payloads outside the existing Provider-owned history item;
- claiming that citation presence, a completed search call, or source count proves factual correctness.

## Consequences

- The two observed real-provider compatibility failures become committable, auditable Turns without weakening required response causality.
- Users can force or constrain supported Provider search requests and can see when search completed with degraded evidence.
- Explicit fallbacks remain permissioned ordinary Host actions initiated by the model from observed failure evidence.
- Compatible endpoints with further structural deviations still require a bounded reviewed adapter update rather than arbitrary parsing.
