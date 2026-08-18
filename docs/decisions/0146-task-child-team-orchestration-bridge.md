# ADR 0146: Task–Child–Team Orchestration Bridge

## Status

Accepted for the Task–Child–Team bridge slice after deterministic cross-ledger,
recovery, compatibility, and offline release-gate evidence.

## Decision

Leonervis keeps Task, Child Run, Team assignment, and Team schedule ledgers as
separate authorities. A Host-only `TaskOrchestrationService` connects them by
binding exact identities at admission, observing the target ledger, validating
terminal evidence, and appending one normalized Task Stage terminal record.
The bridge owns no worker, provider, scheduler, or execution state.

The durable Task Stage lifecycle is:

```text
stage_started
  -> stage_delegated(target + exact identity + inherited limits)
  -> stage_external_committed(evidence digest + target terminal sequence)
   or stage_external_failed(reason + result code)
```

`child`, `team-assignment`, and `team-schedule` are closed delegation targets.
The delegation record carries the Child/Team/schedule identity, objective
binding, parent permission and approval modes, and bounded provider/tool/output
limits. A Team schedule may be admitted with an empty `assignment_ids` tuple:
the scheduler is allowed to create its roster lazily. Before a schedule Stage
can commit, the bridge reads the final roster from the Team schedule ledger and
never invents assignment IDs in the Task ledger.

Admission and observation are ordered fail-closed operations:

1. Read and validate the Task, owner Session, workspace fingerprint, and the
   requested target identity.
2. Append the Task Stage start and create/start the exact external Child,
   assignment, or schedule identity.
3. Append the Task delegation only after the external identity is known; if
   either side becomes durable without the other, report recovery-required and
   do not retry or delete an orphan.
4. Execute only through the existing `ProjectSession`,
   `TeamAssignmentService`, and `TeamScheduleService` seams. The bridge does
   not duplicate model loops, Child supervision, Team scheduling, permission
   checks, worktree handling, or provider adapter behavior.
5. Re-read the external ledger and verify exact owner, workspace, objective,
   Child identity, Team identity, assignment identity, schedule identity, and
   terminal state before committing Task evidence.
6. Append exactly one external Task terminal record. Repeated observation is
   idempotent with respect to Task records, while still requiring the same
   authoritative external evidence to be available.

Child completion requires a published `ChildHandoff`. Team assignment
completion requires `TeamAssignmentService.observe_terminal`, which publishes
and durably observes that assignment's Child handoff. Schedule completion
aggregates every assignment in the exact schedule roster, observes any
terminal assignment not yet observed, requires all assignments to be terminal,
requires every handoff to report completed, and commits a canonical digest over
the schedule outcome, result code, roster, and handoff digests. Cancellation,
failure, interruption, preparation/observation errors, missing handoffs, and
unknown process outcomes become normalized external failure records; they never
become a successful Task Stage.

Permission and approval values are propagated as metadata and parent ceilings:
the bridge may pass a parent permission mode to Child/Team admission, but it
cannot elevate it, grant a Child parent-only controls, or bypass any existing
PermissionGate, approval, sandbox, workspace, timeout, output, conflict,
causality, or audit boundary. Children remain unable to use Task, Team,
delegation, MCP, Skill, or integration controls. Recursive Child or Team
creation is not introduced.

The bridge is Host-only and introduces no model-visible tool, Registry/catalog
entry, system-prompt text, Provider adapter contract, or Effective Context
representation. Existing model-visible Child and Team controls continue to
produce their own ledgers; this bridge is the Host-side convergence seam.

Team schedule resume reopens only the exact nonterminal schedule identity and
its exclusive lease. It does not append a second `TeamScheduleStarted`, choose
new work by itself, or recreate the schedule from Task state. The caller may
then drive it through the existing `run_started` path.

## Compatibility

New Task delegation and external-terminal records use record-local schema v1.
Existing Task, Session, Child, Team, schedule, Action, and Effective Context
records remain readable under their existing compatibility policy; no old
transcript is rewritten. Ordinary foreground Stage records remain distinct
from externally committed records, and external terminal records cannot be
appended without a matching delegation.

## Consequences

Task status, completion eligibility, usage projection, reflection inputs,
checkpoint progress, and Stage inspection can now account for work completed by
an exact Child or Team target without importing that target's transcript into
the parent Session. The parent can audit both the normalized Task fact and the
underlying Child/Team evidence. A cross-ledger crash can leave an explicit
recovery-required condition, but the system will not conceal it with retries,
cleanup, guessed identities, or a success claim.

This slice intentionally does not add background daemon persistence, a general
event bus, arbitrary parallel Task stages, recursive orchestration, automatic
Task completion from external evidence, new model tools, or a distributed
transaction coordinator. Those remain separate design decisions if they are
ever needed.

## Rejected alternatives

Making Task the owner of Child or Team state would duplicate lifecycle facts and
make recovery ambiguous. Making the bridge the scheduler or worker would bypass
the existing permission, supervision, worktree, and Team ledger boundaries.
Copying all Child/Team transcripts into the parent would break bounded context
and audit separation. Treating an empty schedule roster as a failure would
prevent lazy scheduling; generating placeholder assignment IDs would make Task
state claim evidence that the Team ledger never recorded.
