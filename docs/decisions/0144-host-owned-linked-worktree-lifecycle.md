# ADR 0144: Host-Owned Linked Worktree Lifecycle and Bounded Change Sealing

## Status

Proposed for B6.1 and B6.2. This decision is not an implementation authority until
the complete writable-Team slices and recovery evidence are accepted.

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
explicit integration Action is approved and executed.

The ordinary `GitRepository` observation boundary remains strict and continues to
reject arbitrary linked worktree roots. Only a Host-attested B6 binding may inspect its
linked-worktree identity, and that binding must resolve exactly to the authority Git
admin directory, generated branch, base commit, and generated relative path.

## Compatibility

This is Host-only lifecycle state. It changes no ordinary Session, provider, prompt,
ToolSet, or public CLI contract. Existing authority repositories and read-only Child
runs retain their behavior. Ledger and patch artifacts are local runtime data and are
not copied into Session transcripts.

## Consequences

Parallel writable Children can work without sharing authority writes, while review and
integration retain a complete bounded artifact and crash-recovery evidence. Retained
worktrees and artifacts require explicit Host inspection and retirement; Team close,
schedule completion, process exit, and elapsed time never imply cleanup.

## Rejected alternatives

Shared authority writes and file-level leases do not provide repository identity or
crash isolation. Automatic force cleanup, branch reset, merge/rebase/cherry-pick, and
LLM conflict resolution would make uncertain effects untruthful and are therefore
outside this boundary.
