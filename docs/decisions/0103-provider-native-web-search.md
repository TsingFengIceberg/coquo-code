# 0103: Provider-native Web Search

## Status

Accepted.

The OpenAI and DeepSeek routing, streaming, and durable history portions are extended by ADR 0104. The Chat Completions adapter described here remains available for compatible custom endpoints.

## Context

ADR 0102 added Host-owned Brave and Tavily search as an ordinary `web_search` ToolUse. Several model providers also expose search inside their own generation request, but their request fields and response events are not one common protocol: Anthropic uses a versioned server tool, OpenAI Chat uses `web_search_options` on supported search models, DashScope uses `extra_body.enable_search`, and OpenRouter adds a provider-owned search tool. Treating these as the Host `web_search` tool would misstate who executed the search, which permission boundary applied, and where billing and citations originated.

Provider identity, message protocol, and native-search dialect also need separate configuration. A custom OpenAI-compatible or Anthropic-messages endpoint may implement a known dialect, no search, or a future vendor extension that Leonervis does not yet know by name. Provider profiles must express that choice without persisting credentials or permitting arbitrary request mutation.

## Decision

Separate provider preset, message protocol, and native-search adapter. Built-in provider definitions own endpoint and credential defaults plus an optional versioned search adapter. Profiles may select `auto`, `none`, one implemented adapter, or one bounded `custom-manifest-v1`. Profile schema advances to v5 and profile fingerprinting advances to v4 so native-search configuration participates in runtime identity. New built-in presets cover Anthropic, OpenAI, xAI, DashScope, OpenRouter, DeepSeek, Zhipu, Moonshot, Ark, Hunyuan, Qianfan, Ollama, and local routes; a custom profile explicitly chooses `openai-compatible` or `anthropic-messages`.

Built-in `auto` resolves only declared capability. Anthropic, DashScope, and OpenRouter currently declare native search. OpenAI declares it only for model names containing `search-preview`. Other presets and custom profiles default to unavailable; custom profiles must explicitly select a known adapter or provide a manifest. A Session starts with Provider-native search enabled when the resolved route declares it available. Brave and Tavily always start disabled even if valid credentials exist. `/search use provider|brave|tavily [...]` changes the process-local ordered selection, `/search reset` restores the Provider-native default or no active source, and the first source remains the only executed primary. Provider or model switching clears independent selections and applies the new route's default.

Provider-native search is part of the provider generation request, not a Leonervis ToolUse. It consumes no ordinary tool request, takes no Action lease, receives no PermissionGate decision, creates no Action Audit record, and has provider-owned search billing. Selecting Provider as primary therefore hides the Host `web_search` definition for that turn. Selecting Brave or Tavily disables Provider-native search and exposes the Host tool; a request for an inactive independent tool is rejected before PermissionGate. Search results and citations from every source remain untrusted content.

Implement fixed request projections for `anthropic-web-search-20250305`, `openai-chat-web-search-options-v1`, `dashscope-enable-search-v1`, and `openrouter-web-search-v1`. Provider-owned server-search events are accepted as provider events rather than Host tool calls. Supported citation structures are normalized into a bounded Markdown `Sources:` section appended to assistant text, so citations persist through ordinary Session history and replay. Native-search requests currently use one buffered non-streaming SDK call and emit the completed assistant text as a single terminal delta; this avoids interpreting provider server-tool stream events as Host tool calls until each native streaming dialect has a tested parser.

The custom manifest is declarative and schema-versioned. It may provide a bounded `extra_body`, one non-function `server_tool`, and one implemented citation format. It cannot override messages, model, tools, token limits, stream controls, temperature, or other protected request fields; inject a client function tool; include credential-shaped fields; exceed 32 KiB, depth eight, or 128 aggregate entries; or contain non-JSON/non-finite values. The CLI reads and validates the file only while creating or replacing a Profile, stores canonical data rather than a path, and displays only its ID and SHA-256 digest during inspection. The manifest never supplies an endpoint, headers, API keys, executable code, or a custom response parser.

## Compatibility And Versions

The canonical system prompt advances to v31 and the provider adapter contract advances to v33. The empty full-context identity becomes `ctx-v5-9ec8e77ded21f83ef65f66cb8c54d0e1c79e64d19bbfaa988e9a7d919b1d1e80`. Profile storage advances to schema v5 and profile fingerprints advance to v4. The store retains best-effort reading for older profile schemas, but native-search configuration is defined only by newly written v5 Profiles; users may remove old local Profiles and create new ones. ToolArguments v1, ActionIdentity v1, Session, Task, Action Audit, compaction, and provider-usage schemas remain unchanged, and existing Session/Task transcripts are not rewritten.

## Non-goals

- pretending Provider-native search used the Host PermissionGate, Action Audit, or Brave/Tavily quota contract;
- automatically activating independent search because an environment credential exists;
- multi-source fan-out, result merging, ranking, fallback, caching, or automatic retry;
- arbitrary request headers, endpoints, credentials, executable adapters, response code, or custom stream parsers in manifests;
- claiming native-search support for every model exposed by a provider preset;
- MCP search, general `web_fetch`, linked-page reading, or Skills.

## Consequences

- Sessions have a predictable default: declared Provider search on, independent search off.
- Vendor-specific wire fields remain adapter-owned while the Session and slash-command layers consume one provider-neutral capability state.
- Citation text is durable and visible, but Provider-native searches intentionally have no Host action-audit record.
- Future native streaming support must add dialect-specific event parsers without changing Provider events into Leonervis ToolUse.
