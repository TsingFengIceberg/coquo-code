# 0065: Opt-in Bounded Live Tool Details

- Status: Accepted
- Date: 2026-07-29
- Scope: process-local REPL display mode, structured command argv presentation, and terminal-safety bounds

## Context

ADR 0041 deliberately limited live tool lines to redacted summaries. That default keeps one-shot stderr stable and avoids printing content-bearing arguments or potentially sensitive command values, but it also makes an automatically approved command look less transparent than the long command presentations familiar from other coding agents. The model already requests structured `run_command(argv, cwd, timeout_seconds)` arguments; the missing capability is an explicit terminal observation mode, not broader execution authority or an interactive terminal.

The display boundary must not reconstruct a shell command that Leonervis never executed. `run_command` uses direct argv with `shell=False`; an argv that explicitly starts a shell interpreter may still ask that executable to interpret a source argument. The UI therefore needs to display the structured argv and distinguish these two cases without claiming that every argv is shell source.

## Decision

The REPL adds process-local `/tool-details`, `/tool-details compact`, and `/tool-details full`. Every new process starts in `compact`; the setting is not stored in profiles, Sessions, transcripts, Action Audit, provider bindings, or Effective Context. One-shot `prompt` remains compact and gains no new flag.

`compact` preserves the existing single-line redacted event. In `full`, a started event uses multiple lines. Non-command tools expand only the existing safe summary, so file, edit, patch, grep query, and regex content remain hidden. `run_command` additionally exposes structured JSON argv, relative cwd, timeout, and an execution annotation. Direct argv is labeled as Host shell parsing disabled. For the supported common shell executable names, a `-c`-style option is labeled as an explicitly requested shell interpreter and identifies which argv element contains source.

The argv line is bounded to 7 KiB and reports rendered-byte truncation; all detail lines together are bounded to 8 KiB and four lines. C0/C1 controls, Unicode format controls, and line/paragraph separators are escaped before rendering. The full-mode activation message warns that argv may contain sensitive values. This is an intentional opt-in exception to ADR 0041's original no-complete-argv presentation rule; compact remains the privacy-preserving default.

`ToolRequestStarted` carries optional Host-generated safe details beside its existing summary. Only an explicit full-mode request makes AgentLoop derive them from immutable ToolArguments before dispatch; compact and one-shot events carry no argv details. TerminalEventSink then renders the selected form. Events remain best-effort and ephemeral: sink failure cannot change permission, approval, execution, audit, causality, turn commit, or provider failure behavior.

## Contracts and compatibility

This is Host-only presentation. The 21 model-visible tools and order, tool schemas, PermissionGate, approval previews, command executor, canonical system prompt v21, provider adapter contract v24, ToolArguments v1, ActionIdentity v1, `turn_committed` v6, `turn_failed` v2, Action Audit v1, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. Existing Sessions require no migration or rewrite.

## Explicit non-goals

- an interactive PTY, retained shell session, terminal stdin forwarding, or shell-source command API;
- changing `danger-full-access`, ask/auto approval, timeout, output, environment, or process cleanup behavior;
- displaying file/edit/patch/search contents, raw ToolResult, provider payloads, credentials, internal IDs, digests, or approval grants;
- persisting the display mode or live details, replaying terminal animation, or adding a TUI/event bus;
- claiming that a display annotation is a security sandbox or complete executable-behavior analysis.

## Verification

Deterministic tests cover compact-default stability, structured direct argv, explicit `bash -lc` recognition, control escaping, rendered-byte truncation, AgentLoop event propagation, TerminalEventSink rendering, slash parsing/completion, process-local REPL switching, and unchanged model-visible contracts. The complete offline release gate remains required; no credential, network request, real provider, or API cost is used.
