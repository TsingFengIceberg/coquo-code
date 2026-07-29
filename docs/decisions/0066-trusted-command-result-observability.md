# 0066: Trusted Command Result Observability

- Status: Accepted
- Date: 2026-07-29
- Scope: content-free command execution observations and bounded terminal result presentation

## Context

`run_command` already returned structured process facts to the model, but the live terminal completion line exposed only the Host outcome and result code. A user could see that a command succeeded or failed without seeing its exit code, elapsed time, captured-output size, truncation, or cleanup state. Parsing these fields back out of model-visible `ToolResult.content` would make terminal truth depend on a serialized payload intended for provider context and would weaken the distinction between trusted Host metadata and untrusted content.

Raw stdout and stderr need a separate policy decision. They may contain source text, credentials, environment details, provider output, or terminal controls. Command-result observability therefore needs a useful content-free boundary before any raw-output display is considered.

## Decision

`RunCommandTool` now produces an immutable typed observation beside its existing `ToolResult`. It records a closed process status, exit code or signal, monotonic elapsed milliseconds when execution reached spawn preparation, stdout/stderr captured and total byte counts, per-stream truncation, and process-group cleanup completeness. A cwd rejected before spawn preparation reports unavailable duration; spawn failure reports the bounded elapsed preparation time. The model-visible JSON is generated from the same observation rather than becoming the source for terminal metadata.

ProjectSession converts this trusted observation into an ephemeral `ToolResultDetails` object only after `ActionCoordinator` has durably accepted the execution finish. AgentLoop carries that object through `ToolDispatchResult` and `ToolRequestFinished` without inspecting result content. Permission denial, approval rejection/cancellation, malformed preparation, and executor exceptions carry no command execution details. If execution-finish audit persistence fails, the existing exception path emits only `outcome-unknown`; a normal completion event and its result details are not exposed.

Compact mode appends exit or lifecycle status, duration, stdout/stderr byte counts, explicit per-stream truncation when present, and incomplete cleanup when relevant to the existing completion line. Full mode renders status, exit code or signal, duration, both stream accounting records, and cleanup completeness on at most six lines and 2 KiB. Both forms are generated from enums, integers, and booleans and pass the terminal-safe event validators. Neither form includes stdout/stderr text or base64, argv, file content, credentials, absolute paths, internal IDs, or raw ToolResult/provider payloads.

The observation and live details are process-local and best-effort presentation. They are not added to Session transcripts, Action Audit schemas, provider history, profile state, or Effective Context. Durable command truth remains the existing Action Audit plus complete model-visible transcript.

## Contracts and compatibility

This is Host-only execution and presentation metadata. The 21 model-visible tools and order, `run_command` input/output schema, permission and approval behavior, canonical system prompt v21, provider adapter contract v24, ToolArguments v1, ActionIdentity v1, `turn_committed` v6, `turn_failed` v2, Action Audit v1, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. Existing Sessions require no migration or rewrite.

## Explicit non-goals

- displaying, streaming, searching, persisting, or replaying raw stdout/stderr content in the terminal;
- changing command timeout, capture limits, environment, process-group cleanup, permission mode, or approval behavior;
- treating process completion as durable action completion before Action Audit finish persistence;
- adding a PTY, retained shell, stdin forwarding, background process monitor, retry, or OS sandbox;
- deriving trusted presentation metadata by parsing model-visible JSON or provider-visible history.

## Verification

Deterministic tests cover zero and nonzero exits, signal termination, timeout, cancellation, cleanup uncertainty, spawn failure, pre-spawn cwd rejection, monotonic duration availability, stdout/stderr byte accounting, truncation, Session-to-AgentLoop propagation, compact/full terminal rendering, denial/rejection omission, raw-output non-disclosure, and unchanged prior contracts. The complete offline release gate remains required; no credential, network request, real provider, or API cost is used.
