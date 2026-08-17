# ADR 0144: Host-Owned Linked Worktree Lifecycle and Bounded Change Sealing

## Status

Accepted for B6.1-B6.7 after deterministic writable-Team isolation, lifecycle,
integration, recovery, compatibility, and release-gate evidence.

## Decision

Writable Team execution is provisioned only by the Host in a generated Git linked
worktree below `.coquo/worktrees/<authority-fingerprint>/<worktree-id>`, on a generated
local branch `coquo/team/<team-id>/<assignment-id>`. The authority repository must be a
clean, attached, non-symlink Git top level with in-root metadata and no unsupported
external Git state. User or model text never supplies a worktree path, branch, Git
arguments, target ref, or base commit.

The worktree ledger is append-only JSONL with closed records for its header, lifecycle
operation start/finish, and sealed result. A `0600` no-follow lifecycle lease protects
one worktree's local side effects; it is not a substitute for durable ledger truth.
Provision, sealing, and retirement record an operation start before a filesystem or Git
side effect. Known failure, partial effect, and outcome unknown remain distinct, and an
outcome-unknown operation is never retried automatically.

Child changes are sealed into one bounded immutable local patch artifact and a digest-
only manifest. Sealing includes tracked and untracked regular files, rejects unsafe
paths, metadata, symlinks, submodules, HEAD drift, and bound overflow, and records an
explicit empty result. The authority workspace remains unchanged until a later,
explicit parent-only `team_worktree_integrate` Action is approved and executed.

Integration revalidates the exact assignment, target ref and descendant HEAD, sealed
base, source worktree digest, manifest and patch artifacts, and clean target state
immediately before a fixed `git apply --check` followed by one uncommitted `git apply`.
It records Action and worktree evidence in the authority Session and worktree ledger.
A conflict, source/target drift, dirty target, or process-loss outcome fails closed or
remains `outcome_unknown`; it never retries, resets, merges, commits, or asks a model
to resolve the conflict. Applying a patch is not Team work completion: the parent must
separately review the matching handoff and integration evidence. Empty sealed work may
be completed only with explicit no-change evidence.

The ordinary `GitRepository` observation boundary remains strict and continues to
reject arbitrary linked worktree roots. Only a Host-attested B6 binding may inspect its
linked-worktree identity, and that binding must resolve exactly to the authority Git
admin directory, generated branch, base commit, and generated relative path.

## Compatibility

The lifecycle ledger and patch artifacts remain Host-only local runtime state, but
B6.6 adds bounded Host commands for worktree status, diff, recovery observation, and
explicit retirement, plus fixed writable Team roles. B6.5-B6.6 expose exactly one
integration Action to ordinary parent Sessions; restricted Child, Task, compact, and
other Team ToolSets do not receive it. Registry generation 9, catalog 62, system
prompt/provider contract 48, and Effective Context v27/v28 migrate atomically while
legacy replay remains supported. Artifacts are never copied into model history or
treated as a security sandbox boundary.

## Consequences

Parallel writable Children can work without sharing authority writes, while review and
integration retain a complete bounded artifact and crash-recovery evidence. Retained
worktrees and artifacts require explicit Host inspection and retirement; Team close,
schedule completion, process exit, and elapsed time never imply cleanup. Integration
leaves the authority workspace dirty and uncommitted by design, so a later integration
must wait for an explicit user commit or restoration to return the target to a clean
descendant state.

## Rejected alternatives

Shared authority writes and file-level leases do not provide repository identity or
crash isolation. Automatic force cleanup, branch reset, merge/rebase/cherry-pick, and
LLM conflict resolution would make uncertain effects untruthful and are therefore
outside this boundary.
