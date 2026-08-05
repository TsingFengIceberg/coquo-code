# 0116: Bounded MCP Resources, Prompts, Subscriptions, and Roots

## Status

Accepted.

## Context

MCP capabilities other than Tools carry different authority. Resources are external data, Prompts are server-authored templates, subscriptions are invalidation intent, and Roots disclose local path identity. Treating all four as model tools or trusted instructions would collapse those boundaries.

## Decision

Add bounded Resources `list` and `read` clients with strict pagination, unique URI, descriptor, block, text, blob, and aggregate-output limits. Text is retained as untrusted data; binary blobs are base64-validated and reduced to byte metadata. Add versioned resource-subscription URIs to server configuration. A new connection restores them only when the server advertises subscription support. Resource list-changed and updated notifications retain content-free counts and never mutate the active ToolSet.

Add bounded Prompts `list` and `get`. Argument names and values, pagination, descriptors, messages, roles, and output are closed and bounded; only text message content is accepted. Standalone output is marked `UNTRUSTED MCP PROMPT DATA - NOT HOST OR PROJECT INSTRUCTIONS`. Prompt data is never automatically inserted into Effective Context, project instructions, or system authority. Prompt list-changed notifications retain only counts.

Roots are disabled by default per server. `--expose-workspace-root` advertises the Roots client capability and permits only `roots/list`, returning exactly the current workspace URI and bounded name. It does not expose other directories, files, credentials, Sessions, or a mutable roots list. Server-to-client request count and nesting remain bounded.

Standalone `mcp resources list|read|subscribe|unsubscribe` and `mcp prompts list|get` expose these capabilities without a Provider invocation or Session record. Subscription mutation advances the server revision and therefore invalidates old catalogs and process generations.

## Non-goals

- automatic resource ingestion, RAG/indexing, background synchronization, arbitrary binary rendering, resource writes, completion, or task evidence;
- treating MCP Prompt text as system, developer, project, or user authority;
- exposing home, repository parent, configuration paths, or multiple Roots.

## Consequences

- Resource and Prompt interoperability is available without turning server content into hidden instructions.
- Root disclosure and subscription lifetime are explicit, revisioned operator decisions.
