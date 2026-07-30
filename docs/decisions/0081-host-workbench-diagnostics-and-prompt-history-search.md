# 0081: Host Workbench Diagnostics and Prompt History Search

- Status: Accepted
- Date: 2026-07-30
- Scope: expose bounded local readiness and navigation helpers without changing model-visible contracts

## Context

The fail-closed command sandbox made `run_command` materially safer, but users could discover a missing dependency only after a model requested a command. Existing `/status` showed runtime routing only, `/tools` already meant durable tool ledgers, Action Audit lacked a one-command latest view, and command approval still contained the obsolete statement that no OS/filesystem/network sandbox existed. The persistent TTY retained current-Session prompt history but did not provide its own visible reverse-search control.

## Decision

`ProjectSession.project_status()` returns one process-local `ProjectStatus` containing the current redacted runtime, Session metadata, latest already-observed context report, permission and approval modes, fixed tool budgets, and dependency-only command-sandbox readiness. `/status` renders this snapshot without provider count/generation, command execution, Session mutation, or Action Audit.

`/sandbox check` calls `RunCommandTool.inspect_sandbox(verify_activation=True)`. The check first validates Linux, fixed `/usr/bin/bwrap`, and seccomp-filter construction, then sends fixed `/usr/bin/true` through the same production spawn, activation-evidence, block-fd release, timeout, and cleanup path as an ordinary command. User argv cannot enter this probe. It calls no model and appends no Session or Action Audit record. A successful probe verifies only current activation readiness; it neither approves a later action nor promises that arbitrary commands will succeed.

`/tools` and `/tools details` retain their durable-ledger meanings. `/tools catalog` separately renders all 21 canonical tools with static permission classes and current permission-mode availability; `run_command` also reports dependency readiness. `/actions last` reuses the existing redacted Action Audit renderer with a one-entry suffix. Command approval now accurately states read-only Host, writable workspace, socket denial, no shell parsing, and no rollback. Trusted `run_command` result codes add bounded next-step guidance without parsing stdout/stderr, automatic retry, or rollback claims.

The persistent prompt-toolkit application attaches a `SearchToolbar` to the existing current-Session history buffer and merges the official Emacs search bindings. Ctrl-R performs case-insensitive reverse search over the same bounded latest-1,000 prompt history already used by navigation. Search-mode Enter accepts the draft but does not submit it; ordinary Enter remains the separate submission step. Session switching replaces the history object as before. `/clear` remains display-only and gains an end-to-end terminal regression test.

## Compatibility

These are Host-only inspection and input-presentation changes. The canonical system prompt remains v22, provider adapter contract remains v25, and model-visible tool name/order/schema, ToolArguments v1, ActionIdentity v1, permission decisions, Action Audit schema, Session records, compaction records, and Effective Context representations and identities remain unchanged. Existing `/tools`, `/actions`, `/provider current`, one-shot output, non-TTY REPL, transcripts, and checkpoints remain compatible.

## Invariants

- `/status` never triggers provider work or an activation probe.
- `/sandbox check` executes only fixed Host-owned argv and never falls back to unsandboxed execution.
- A probe creates no permission grant, Action Audit, tool ledger, Session record, or model history item.
- Tool catalog availability never weakens PermissionGate or any tool hard bound.
- Result guidance is derived only from trusted tool name and result code and never retries automatically.
- Ctrl-R searches only the current Session's bounded committed prompt history and cannot submit during search acceptance.
- `/clear` changes only terminal presentation.

## Non-goals

- package installation or automatic sandbox repair;
- proving future command success, resource isolation, or sandbox security against kernel exploits;
- exposing raw sandbox exceptions, activation payloads, command stdout/stderr, credentials, or absolute workspace paths;
- changing model tool descriptions, permission policy, approval identity, or durable audit content;
- cross-Session prompt search, persistent search queries, fuzzy search, or a full-screen TUI.
