# 0123: Skill Authoring, Local Import, Task Audit, and Execution Boundary

- Status: Accepted
- Date: 2026-08-06
- Scope: Skill lifecycle preview, operator authoring, local package import, Task evidence, and script execution

## Context

The initial Skill runtime can discover, load, compose, retain, and inspect bounded packages, but operators still need deterministic authoring and package-maintenance workflows. Compaction preview must explain whether its exact retained-turn selection will deactivate a Skill before any summary call or durable mutation occurs. Durable Tasks also need a content-free way to show which exact committed Stage Turn loaded a Skill. Finally, packages may contain useful scripts, but a dedicated Skill executor would bypass the existing command permission, approval, audit, sandbox, timeout, and output boundaries.

## Decision

Compaction preview projects active Skills from the current Effective Context and again from the exact verbatim retained turns. It reports before/after identities, deactivated names, and the post-compaction action ToolSet without calling the provider or changing Session state. A Skill remains active only when its complete successful `skill_load` pair remains verbatim; summary text never recreates activation.

Standalone `skills init` creates one minimal strict package through exclusive directory and file creation, fsync, and canonical inventory validation. `skills check` validates the complete inventory or reports issues for one named invalid package. `skills search` performs bounded case-insensitive literal all-term matching over name and description, with deterministic score and ordering; `--all` includes shadowed candidates. `skills conflicts` projects source-precedence collisions. REPL `/skills search` and `/skills conflicts` provide the corresponding read-only diagnostics. These inspections make no provider call, create no Session, and write no Action Audit.

`skills import <local-directory>` accepts only an explicit existing local package and never clones, fetches, installs, or resolves dependencies. The canonical no-follow loader validates the source first. The Host then creates an absent destination exclusively, copies only the exact validated `SKILL.md` and indexed resources through bounded no-follow reads, fsyncs files and directories, reloads the destination, and rejects source drift, target replacement, shadowing, or any identity mismatch. A failure removes only the newly created directory when its original inode identity still matches; a concurrently replaced directory is never recursively removed.

Each successful import writes a strict JSON lock outside the scanned `skills/` root for the selected workspace, project, or user scope. Lock v1 records only scope, name, manifest fingerprint, and sorted resource path, byte-count, readability, and fingerprint facts. It stores no source path, URL, credential, timestamp, or instruction/resource content. Lock creation is exclusive and durable. `skills lock show` exposes the bounded portable identity and its domain-separated digest; `skills lock verify` reloads the current package and fails closed on missing, malformed, oversized, symlinked, drifted, or mismatched state. Import is a local operator command, not a model-visible action or package manager.

SessionStore can strictly replay recent committed Turns and project `skill_load` request identities, Host ledger outcomes, and safe loaded source/fingerprint facts without returning instructions. `task skills <task-id>` intersects that projection with the Task's exact committed Stage `turn_record_sequence` values. Reading Task metadata remains independent of Session health; only this explicit cross-transcript audit requires a valid owner Session. The deterministic offline baseline advances to `host-baseline-v3`, and its Task lifecycle case now performs ordinary `skill_search` and `skill_load` calls inside the execution Stage before proposing completion.

Package scripts remain ordinary indexed resources. There is no `skill_run_script`, import hook, executable resource type, implicit subprocess, or permission shortcut. Reading a script uses `skill_read_resource`; running one requires an explicit existing `run_command` request and therefore remains a dangerous Host action governed by PermissionGate, approval, Action Audit, the Linux command sandbox, timeout, cancellation, and output bounds. User-scope packages do not receive a workspace execution path.

## Compatibility and contracts

All changes are Host commands, read-only projections, preview fields, Eval fixtures, or stronger local authoring boundaries. Existing model-visible Skill tools, registry generation 4, system prompt v39, provider adapter v40, Skill inventory v2, Effective Context v13/v14, Session records, Task records, and import-free package discovery remain unchanged. Import locks are new independent local files and are never required to read manually installed legacy packages.

## Explicit non-goals

- network registries, Git clone, dependency installation, semantic version solving, updates, publishing, signing, trust claims, or a marketplace;
- direct Skill script execution, dynamic Python import, executable permissions, implicit commands, or trusted package code;
- automatic Skill selection, summary-based reactivation, Task-owned duplicate Skill state, or instruction content in Task audit output;
- atomic replacement of an existing package, overwrite import, uninstall, lock repair, or background synchronization.

## Verification

Deterministic tests cover compaction removal and retention, authoring success and duplicate rejection, named invalid-package checks, search ordering and shadow diagnostics, local import and lock verification, source drift, target replacement, symlinks, oversized resources, malicious YAML tags, lock tampering, Session/Task audit projection, Task Stage Skill Eval use, and the absence of a direct script executor. The full offline release gate remains required.
