# 0063: Bounded Read-only Git Change Observation

- Status: Accepted
- Date: 2026-07-28
- Scope: fixed Git status/diff tools, repository metadata boundary, Host inspection, and model-visible contract integration

## Context

The existing workspace tools could inspect files and run an explicitly dangerous arbitrary command, but the Agent could not safely distinguish staged, unstaged, and untracked changes without requesting `run_command`. Requiring dangerous full access for ordinary change review was too broad, while wrapping user-supplied Git arguments as a new command tool would merely recreate the same risk under another name. The REPL also lacked a provider-free way for the user to inspect the current repository state.

Git observation is not equivalent to ordinary file reading. Git necessarily reads repository metadata, index and object state, may consult config and attributes, and normally supports extension points such as fsmonitor, external diff, text conversion, pager, hooks, submodules, and object alternates. The slice therefore needs its own fixed Host boundary rather than permission-only classification.

## Decision

Append `git_status` and `git_diff` after the existing 17 tools, producing the canonical 19-tool order. Both are `workspace-read`, pass through PermissionGate and durable Action Audit, share the existing 8-calls-per-response, 32-requests-per-turn, and 24-provider-invocations budget, and remain sequential. They never require approval in any permission mode.

`git_status({})` runs fixed porcelain-v2 zero-delimited status at the workspace root, ignores submodule contents, and parses the complete result before returning deterministic UTF-8-sorted JSON Lines. Each record contains a relative path plus normalized index/worktree state and optional rename origin. It never reads or returns untracked file content. Raw status is limited to 1 MiB and 10,000 parsed entries; model output is limited to 200 records and 32 KiB with an explicit `{"truncated":true}` sentinel. Unsafe, malformed, or non-UTF-8 paths fail the whole call.

`git_diff(scope, path)` accepts only `staged | unstaged` and `.` or one portable workspace-relative literal path. Unstaged compares the worktree to the index; staged compares the index to the current commit, while also supporting an unborn repository. It returns only tracked-file patch content, disables rename detection, external diff, text conversion, color, and submodule recursion, and never includes untracked file content. Output is strict UTF-8, limited to 64 KiB, and ends with `[truncated]` when omitted bytes exist.

The dedicated runner uses fixed argv with `shell=False`, closed stdin, a five-second timeout, bounded stdout/stderr capture, a new process group, and bounded TERM-to-KILL cleanup. It disables optional locks, pager, terminal prompts, fsmonitor, untracked cache, hooks, external diff, system/global attributes and config, submodule recursion, and color. It fixes `--git-dir=.git --work-tree=.` and does not invoke aliases or network commands. The runner exposes no raw stderr or absolute path to the model.

V1 requires the workspace itself to be the Git top level with an in-root non-symlink `.git` directory. Linked-worktree pointer files, `commondir`, object alternates, external config includes, configured external filters, unsafe config/object entries, oversized or non-UTF-8 local config, and non-repository workspaces fail safely. This is a bounded Git-process boundary, not an OS sandbox; it reads accepted in-root repository metadata and tracked workspace content but does not claim isolation against every malicious Git implementation or kernel-level race.

The REPL adds `/changes`, `/changes unstaged`, and `/changes staged`. The first renders structured status; the latter two render root-scoped tracked patches after escaping terminal control characters. These Host commands do not call a provider, consume model-tool budget, append Action Audit, modify Session state, or persist their output.

## Contracts and compatibility

Canonical system prompt advances to v20 and its fingerprint becomes `v20-ceb57dd3c3e664b6bb5fb92d04c84ee99a8d79204ba39752f2a3722ec85f9f52`. Provider adapter contract advances to v23 because both Anthropic and OpenAI-compatible ordinary count/create projections now expose the two added schemas; compact-summary requests still expose no tools. The empty full-context identity becomes `ctx-v3-cb7ce2ad36fc600b23c66362f02e4e139beee17e721a06eb490b82a7ae302a9e`.

ToolArguments remains v1 because both new inputs fit its existing immutable canonical JSON representation. ActionIdentity v1, Action Audit v1, `turn_committed` v6, `turn_failed` v2, `context_compacted` v4, and Effective Context representations `ctx-v3`/`ctx-v4` do not advance. Old Session records and checkpoints remain strictly replayable without rewriting; a resumed new turn uses the current 19-tool prompt/catalog snapshot as usual.

## Failure and causality

A Git tool result is appended immediately after its matching tool request. A tool failure is model-visible and causes later calls in the same provider batch to be skipped under the existing batch rule. Provider failure or durable turn-commit failure does not commit the candidate conversation turn, while completed read-only Action Audit evidence remains truthful. The tools do not mutate files or intentionally refresh the index; `GIT_OPTIONAL_LOCKS=0` removes Git's optional index-refresh lock path.

## Explicit non-goals

- arbitrary Git argv, revisions, refs, commits, logs, blame, branches, fetch, push, commit, reset, checkout, or index mutation;
- untracked-file patch content, recursive submodule inspection, rename analysis, external diff, textconv, filters, or pager integration;
- linked worktrees, external object stores, config includes, or repository discovery above the workspace;
- replacing `read_file`, file search, Action Audit, or dangerous `run_command` verification;
- persisting `/changes` output or claiming a stable snapshot across concurrent external workspace edits.

## Verification

Deterministic tests cover clean and mixed status, stable ordering, renames, invalid UTF-8 paths, staged/unstaged separation, literal path filtering, unborn staged diffs, explicit truncation, terminal-control escaping, disabled fsmonitor/external diff/textconv, rejected non-repository/nested/linked/external-metadata layouts, catalog and provider parity, PermissionGate and Action Audit integration, Session causality, slash dispatch, system-prompt fingerprint, Effective Context identity, and the full offline release gate. No credential, network request, real provider, or API cost is used.
