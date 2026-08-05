# 0111: Audited MCP Execution and Process Lifecycle

## Status

Accepted.

## Context

The quarantine catalog and progressive ToolSet epochs let a model discover and promote an exact MCP contract, but Slice 4 deliberately stopped before `tools/call`. Executing a promoted contract introduces a new action boundary: a configured external executable receives model-selected arguments, may retain process-local state, and can return untrusted structured or binary-bearing content. Server annotations cannot safely classify that authority, and a timeout or transport failure after request delivery cannot prove that the remote tool had no effect.

Slices 5 through 7 need one complete path through permission, approval, durable Action Audit, strict protocol handling, result normalization, cancellation, and process cleanup. Reuse must improve stateful-server compatibility without allowing a process, approval, schema, or ToolSet identity to survive configuration and catalog changes.

## Decision

Every accepted `mcp-remote` contract receives the Host-assigned `dangerous` permission action. This classification is independent of `readOnlyHint`, `destructiveHint`, titles, descriptions, output schemas, or any other server data. A promoted MCP request therefore requires `danger-full-access` and follows the existing ask or auto policy. Ask approval names the exact qualified tool but redacts its arguments; the preview explains the confined process boundary and lack of rollback. ApprovalPreview advances to v4 with an MCP-specific metadata kind.

Before entering ActionCoordinator, the Host resolves the exact accepted candidate from the current catalog, verifies its contract identity, validates canonical arguments against the supported frozen JSON Schema subset, and binds candidate plus catalog identity into an expected-configuration precondition. The executable subset rejects `pattern`, references, and other unsupported keywords instead of evaluating server-supplied regular expressions or schema code inside the Host. ActionIdentity already binds the ToolSet through its non-recreatable context lease. Revalidation checks the current credential-free configuration and candidate identity before durable execution start. Permission denial or approval rejection starts no reusable process.

`McpProcessManager` lazily owns initialized confined stdio processes. A process key binds user/project scope, configured server name, configuration revision, negotiated protocol, and catalog ID. It reuses one healthy process only for sequential calls, permits at most eight active processes and 128 completed calls per process, and uses deterministic least-recently-used retirement at capacity. Configuration or catalog change, process exit, protocol failure, cancellation, call limit, ProjectSession close, or incompatible live tool/schema identity retires the process. Status inspection reconciles live generations with current enabled configuration, while an explicit catalog refresh retires generations from another catalog. A failed cleanup remains manager-owned for a later bounded cleanup attempt rather than disappearing from lifecycle tracking. There is no retry after a `tools/call` request has been sent. One-shot sessions receive the same manager but close it when the command exits.

Live connection setup repeats initialize, initialized notification, and bounded `tools/list`. Before the first call, the live protocol, remote tool name, and input-schema fingerprint must match the promoted candidate. `tools/call` uses exact canonical arguments, a 30-second timeout, bounded JSON-RPC framing, and cooperative cancellation. Cancellation sends `notifications/cancelled` when possible and then retires the process. `/mcp status` exposes only scope, server, revision, protocol, process generation, completed-call count, alive state, and stderr byte metadata; it exposes no PID, arguments, result content, credential, or stderr body.

Result normalization accepts a closed `CallToolResult` shape with up to 64 content blocks plus optional structured content. Text is retained under field and aggregate limits. Image, audio, and blob payloads are validated as base64 but represented only by type, MIME metadata, and decoded byte count. Resource links and embedded resources receive bounded structural validation. `_meta` and annotations are discarded. Model-visible output is at most 64 KiB. Oversized output is a partial error with bounded retained content; unsupported or malformed content after dispatch is also partial and retires the process.

A normal bounded result is succeeded, `isError: true` is a known failed action, and a JSON-RPC error is a known server-reported failure. Timeout, cancellation, transport/protocol loss after dispatch, malformed result, truncation, or incomplete cleanup is partial or outcome-uncertain. These outcomes are persisted through the existing Action Audit lifecycle and returned through ordinary ToolResult causality. Terminal tool details contain only process generation, reuse, duration, result-block count, and cleanup completeness.

## Compatibility And Versions

The canonical system prompt advances from v34 to v35 with fingerprint `v35-8537a2ef36ba8aa29068cc93f9b09231c0ea4e51a534fdb473e591408a7b5dca`. The current empty full-context identity becomes `ctx-v9-8e257b8889c2794ab1deef575bf96a22a9394cdac71e54234cb769adeaafadc7`; Effective Context representations remain `ctx-v9` and `ctx-v10`. ApprovalPreview advances from v3 to v4. The Provider adapter contract remains v38 because provider tool projection and parsing do not change. Extension Contract, Registry, ToolSet, ToolArguments, ActionIdentity, Session, Task, Action Audit, Profile, and compaction representation versions remain unchanged; existing transcripts replay without rewriting.

## Non-goals

- HTTP, Streamable HTTP, SSE, OAuth, remote credentials, or network-enabled MCP processes;
- concurrent calls to one server, cross-Session process sharing, background startup, automatic restart after uncertain delivery, or durable process state;
- trusting server annotations for permission, approval, retry, or task-completion policy;
- exposing raw image/audio/blob payloads, server `_meta`, result annotations, stderr, credentials, or routine call arguments in terminal diagnostics;
- MCP resources, prompts, roots, sampling, elicitation, server-to-client requests, or persistent notification history;
- rollback, hostile-concurrency isolation, CPU/memory/disk quotas, or a claim that a successful tool result completes the user's task.

## Consequences

- Natural-language turns can now discover, promote, authorize, execute, and consume one exact MCP tool through the same permission and audit architecture as built-in actions.
- Conservative `dangerous` classification makes first-version authority explicit while preserving room for a future local trusted policy declaration.
- Stateful local servers can retain process-local state across calls without making process identity durable or reusable across catalog generations.
- Partial and uncertain outcomes remain visible and non-retryable instead of being flattened into generic tool errors.
