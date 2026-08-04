# 0104: OpenAI Responses Protocol and Provider-owned History

## Status

Accepted.

## Context

ADR 0103 added Provider-native search adapters, but the OpenAI-compatible Chat Completions representation cannot express the official OpenAI Responses item stream. DeepSeek exposes native web search for `deepseek-v4-flash` only through its stateless Responses endpoint. That endpoint returns ordered `reasoning`, `message`, `function_call`, and `web_search_call` output items and requires `web_search_call` to be sent back unchanged in later full-history requests. Treating a Provider `web_search_call` as a Leonervis ToolUse would incorrectly dispatch it through PermissionGate and Action Audit; dropping it would break multi-turn search context.

## Decision

Add `openai_responses` as a first-class WireProtocol beside Anthropic Messages and OpenAI Chat Completions. Built-in OpenAI routes use Responses. DeepSeek selects protocol by model: `deepseek-v4-flash` uses Responses and declares `openai-responses-web-search-v1`, while other DeepSeek models retain Chat Completions with no inferred native search. New custom Profiles may explicitly select `openai-responses`. Existing DeepSeek V4 Flash Profiles that explicitly stored Chat Completions remain readable and continue using Chat without silently gaining native search.

The adapter sends stateless full history with separate `instructions` and `input`, `store=false`, bounded `max_output_tokens`, canonical Host function tools, and an optional Provider `{"type":"web_search"}` tool. Host ToolUse projects as Responses `function_call`; its matching ToolResult projects as `function_call_output` using the same `call_id`. Multiple function calls remain one bounded AssistantToolBatch and retain existing sequential Host dispatch, permission, budget, and audit behavior.

Introduce immutable `ProviderOwnedItem` for only `reasoning` and `web_search_call`. Each item binds protocol, type, ID, and at most 256 KiB of canonical complete JSON. One response may retain at most 32 such items. `ProviderResponseEnvelope` carries these items before one ordinary final text or Host tool response. AgentLoop appends them to pending causal history but never dispatches them. Unknown hosted-tool output types, duplicate IDs, incomplete items, malformed JSON, and unsupported mixed shapes fail closed.

Responses streaming consumes semantic events, requires a terminal `response.completed`, `response.incomplete`, or `response.failed`, uses the terminal response object as authoritative truth, emits exact `response.output_text.delta` content, and appends normalized citation text at completion. Output-limit failures retain usage and partial-observation metadata but commit no Turn. Standalone Session-title and compaction calls disable all tools; they may parse and discard Provider reasoning from that independent invocation, but reject Provider tool calls.

Persist ProviderOwnedItem in new `turn_committed` schema v9. Readers retain v1-v8 without rewriting, and old schemas cannot encode the new item. Effective Context representations advance from ctx-v5/v6 to ctx-v7/v8 because provider-owned replay content now participates in context identity. The current empty full-context ID is `ctx-v7-a9178c934e67352a98ba3641b927acc250d800c1af8d9d1de1bfaa2f2028a6e7`. ActionLease and Task context validators accept versioned future context IDs rather than a fixed v1-v6 range. The provider adapter contract advances to v34; canonical system prompt v31, Profile schema v5/fingerprint v4, ToolArguments v1, ActionIdentity v1, and all Task and Action Audit schemas remain unchanged.

## Non-goals

- using `previous_response_id`, Provider conversations, or remote response storage;
- treating Provider web search as a Host tool, permission decision, or Action Audit entry;
- exposing arbitrary hosted tools such as code interpreter, computer use, file search, or MCP;
- persisting raw stream events instead of validated terminal output items;
- migrating or rewriting old Profile or Session files;
- claiming Responses support for every model offered by a compatible provider.

## Consequences

- OpenAI and DeepSeek V4 Flash can use one protocol that preserves official native-search causality across turns.
- Provider-owned work remains visible in durable model history without consuming Host tool budgets or bypassing Host action policy.
- Chat Completions remains available for providers and relays that do not implement Responses.
- Adding another Responses hosted tool requires an explicit neutral representation, replay policy, terminal parser, and Session compatibility decision.
