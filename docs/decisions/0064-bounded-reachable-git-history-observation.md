# 0064: Bounded Reachable Git History Observation

- Status: Accepted
- Date: 2026-07-29
- Scope: current-HEAD commit listing, reachable-commit inspection, Host commands, and model-visible contract integration

## Context

ADR 0063 made staged, unstaged, and untracked state observable without granting arbitrary command execution, but the Agent still needed `danger-full-access` to understand recent commit history or inspect why an existing change was introduced. A general `git log` or `git show` wrapper accepting revisions and flags would reopen Git's object, ref, pathspec, formatting, pager, external-diff, and extension surfaces. History observation therefore needs reviewed fixed commands and a narrower identity contract.

## Decision

Append `git_log` and `git_show` after the existing 19 tools, producing the canonical 21-tool order. Both are `workspace-read`, pass through PermissionGate and durable Action Audit, require no approval, share the existing 8/32/24 budgets, and execute sequentially.

`git_log(limit, path)` accepts an integer from 1 through 50 and either `.` or one portable literal workspace-relative path. It walks only commits reachable from current `HEAD`, newest first in topological order, and returns deterministic JSON Lines containing the complete commit ID, complete parent IDs, committer ISO timestamp, bounded subject, and explicit subject-truncation state. Raw output is limited to 1 MiB; model output is limited to 32 KiB with an explicit truncation sentinel. It does not enumerate all refs, decorations, signatures, notes, authors, email addresses, or arbitrary revisions.

`git_show(commit_id, path)` accepts only a complete lowercase 40- or 64-hex object ID and the same literal path contract. Before reading metadata or patch content, the Host uses fixed `merge-base --is-ancestor` behavior to require that the object is a commit reachable from current `HEAD`. It then returns one JSON metadata line with bounded commit message followed by a tracked-file patch. Message content is capped at 8 KiB; total output is capped at 64 KiB with explicit message and patch truncation. External diff, text conversion, rename detection, signatures, color, and submodule recursion remain disabled.

The shared runner additionally disables log/show pagers and replacement objects. Existing repository validation, fixed `.git` and worktree roots, closed stdin, `shell=False`, five-second per-command timeout, bounded capture, process-group cleanup, config/filter rejection, and no-network/no-write claims remain unchanged. This is a bounded Git process boundary, not an OS sandbox.

The REPL adds `/commits [count] [path]` and `/commit <full-commit-id> [path]`. They expose complete IDs for copy/paste and escape terminal controls in subject, message, and patch text. Like `/changes`, these Host commands invoke no provider, consume no model tool budget, append no Action Audit, and mutate no Session.

## Contracts and compatibility

Canonical system prompt advances to v21 with fingerprint `v21-c5cc71da1e01c230a50bd6d29a7cf087e86ba3517e3ef00123cc1f2c44543707`. Provider adapter contract advances to v24 because ordinary Anthropic and OpenAI-compatible count/create projections expose the 21-tool catalog; compact-summary requests still expose no tools. The empty full-context identity becomes `ctx-v3-bf336060a8cf9fb75df3766f81b6dae9ef175e8b6e0929f0a0ef10ebab387dd7`.

ToolArguments remains v1. ActionIdentity v1, Action Audit v1, `turn_committed` v6, `turn_failed` v2, `context_compacted` v4, and Effective Context representations `ctx-v3`/`ctx-v4` do not advance. Existing Sessions and checkpoints replay without rewriting; a resumed new turn uses the current prompt and 21-tool catalog.

## Failure and causality

Invalid limits, noncanonical paths, abbreviated or uppercase IDs, non-commit objects, missing objects, unborn `HEAD`, unreachable commits, malformed or non-UTF-8 Git output, timeout, and repository-boundary failures produce safe tool errors. A failed request causes later calls in the same provider batch to be skipped under the existing batch rule. Completed read-only Action Audit remains truthful if provider continuation or turn commit later fails.

## Explicit non-goals

- arbitrary revisions, abbreviated IDs, branch or tag names, `--all`, reflogs, unreachable objects, revision ranges, or user-supplied Git flags;
- blame, branch, tag, fetch, push, add, reset, checkout, commit, or any index/ref/worktree mutation;
- signatures, notes, mailmap projection, external diff, textconv, configured filters, submodule history, rename analysis, or linked worktrees;
- snapshot stability across concurrent external ref/object/worktree changes;
- project-instruction loading or Foundation 5A.

## Verification

Deterministic tests cover current-HEAD ordering, path filtering, root and parent metadata, complete-ID validation, reachability rejection, subject/message/patch UTF-8 truncation, external-diff suppression, catalog and provider parity, AgentLoop dispatch, PermissionGate and Action Audit integration, slash parsing, terminal-control escaping, system-prompt fingerprint, Effective Context identity, and the complete offline release gate. No credential, network request, real provider, or API cost is used.
