# 0108: Confined stdio MCP Configuration and Inspection

## Status

Accepted.

## Context

The unified extension contract and frozen ToolSet boundary make it possible to add external tool sources without letting them mutate an active Turn. The first MCP slice still needs a narrower trust boundary before any server-reported tool can become a Leonervis tool: configuration must not store credentials, starting a local server must not grant ambient host or workspace authority, protocol negotiation must be bounded and fail closed, and inspection must not silently become model exposure or execution.

An MCP stdio server is executable code selected by the user. Its tool descriptions, schemas, annotations, instructions, JSON-RPC errors, stdout, and stderr are untrusted data. A configured executable therefore cannot be treated as safe merely because it speaks MCP, and an annotation such as `readOnlyHint` cannot grant PermissionGate authority.

## Decision

Add version-one MCP configuration with two explicit scopes. User configuration lives at `${XDG_CONFIG_HOME:-~/.config}/leonervis-code/mcp-servers.json`; project configuration lives at `.leonervis-code/mcp-servers.json`. A server name may exist in only one scope. Entries are revisioned, atomically replaced, written with mode `0600`, protected by scope locks, and rejected across symlinked configuration paths. Add, replace, enable, disable, and remove support optional revision compare-and-swap. New entries default to disabled.

The current transport set contains only `stdio`; the current trust set contains only `confined-stdio`. The executable must be an absolute POSIX path. Arguments and a workspace-relative server cwd are bounded. Environment configuration stores only explicit `TARGET=SOURCE_ENVIRONMENT_NAME` mappings. The Host reads values at probe time, passes only a small non-credential base allowlist plus mapped values, reports missing source names without values, and never persists credential values.

Add a temporary `McpStdioClient` probe. Every probe starts one process through `LinuxBubblewrapCommandSandbox(workspace_writable=False)`: the host root and workspace are read-only, temporary/home/config locations are private, known sensitive home paths are masked, capabilities are dropped, and socket syscalls are denied. Sandbox activation must be verified before protocol exchange. The process owns a new process group and is closed, then terminated and killed if necessary, on success, protocol failure, timeout, cancellation, or unexpected failure. Incomplete cleanup is a distinct error.

The client sends `initialize` using protocol `2025-06-18`, accepts a closed compatibility set, sends `notifications/initialized`, and follows bounded `tools/list` pagination only when the server declares the tools capability. Newline-delimited JSON-RPC is strict: duplicate keys, non-finite values, wrong IDs, malformed result/error pairs, server-to-client requests, repeated cursors, duplicate tool names, unsupported versions, oversized messages, excessive messages, deep or broad JSON, excessive pages, and excessive tools all fail closed. Server error text and stderr content are never rendered; only sanitized classifications and stderr byte/truncation facts survive.

Expose standalone `mcp add|list|show|enable|disable|remove|probe` commands. The REPL exposes only Host-side `/mcp list|status|show|probe`; mutations remain standalone. Presentation does not show argument contents, credential values, server instructions, descriptions, schemas, annotations, JSON-RPC errors, or stderr. Probe output contains only bounded identities, capability names, tool names, input-schema byte counts, pagination, duration, stderr byte counts, and cleanup status.

No discovered tool enters `ExtensionToolContract`, `ToolRegistrySnapshot`, `ToolSetSnapshot`, provider projection, PermissionGate, Action Audit, or Session history in this slice. The probe starts no Provider call, writes no Session record, and executes no `tools/call`.

## Compatibility And Versions

MCP configuration begins at schema v1. The canonical model system prompt remains v33, provider adapter contract remains v37, Effective Context remains `ctx-v9`/`ctx-v10`, and Session, Task, Action Audit, ToolArguments, ActionIdentity, ApprovalPreview, and provider-owned history schemas remain unchanged. Existing profiles and Sessions require no migration.

## Non-goals

- `tools/call`, MCP tool import, discovery epochs, model-visible discovery, ToolSet promotion, or PermissionGate mapping;
- a persistent process manager, pooling, reconnect, restart, health supervision, background servers, or cross-Turn connections;
- Streamable HTTP, legacy HTTP/SSE, remote servers, OAuth, bearer-token configuration, or network MCP;
- MCP resources, prompts, roots, sampling, elicitation, logging, subscriptions, or server-to-client requests;
- trusting server annotations, instructions, names, schemas, or stderr as policy, approval, execution, or completion evidence.

## Consequences

- Users can define, inspect, enable, and protocol-test local MCP servers without granting workspace writes, network access, ambient credentials, or model visibility.
- MCP interoperability failures become deterministic sanitized Host diagnostics rather than malformed model history or leaked server output.
- The next slice can normalize reviewed server tools into deferred extension contracts while preserving the existing frozen ToolSet and PermissionGate boundaries.
