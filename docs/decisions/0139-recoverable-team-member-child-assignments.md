# ADR 0139: Recoverable Team Member Child Assignments

- Status: Accepted
- Date: 2026-08-14

## Context

ADR 0138 established durable Team and member identity, but identity alone did not
answer how a Host assignment becomes executable work. Creating a Team record and a
Child Run independently could leave an orphaned objective, a Child with the wrong
owner, or a false claim that execution had started. The implementation also needed
to reuse the proven Child runtime and bounded supervisor without turning a member
into a long-lived thread or Session.

## Decision

Each assignment has an independent UUID, one active member, one bounded objective,
and a preallocated fresh Child Run UUID. The Team ledger advances through
`pending_child -> child_bound -> terminal_observed`. Creation is a deterministic
two-ledger saga:

1. append and fsync `TeamAssignmentCreated`;
2. atomically install the Child header and `ChildRunTeamAssignment` origin;
3. append and fsync `TeamAssignmentChildBound`;
4. only then admit, run, start, or wait for the Child;
5. after exact terminal handoff evidence is published, append a content-free
   `TeamAssignmentObserved` record.

The two JSONL files are never presented as one atomic transaction. Exact IDs and
provenance make partial states inspectable and recoverable. Recovery retries only
the missing metadata step, uses the existing v2 lease-proven Child recovery for
abandoned `running`/`cancelling` work, never auto-starts `ready` work, and never
creates replacement IDs after uncertain durability.

Execution delegates to the existing Child APIs. Foreground `prepare` and `run`,
REPL-only background `start`, bounded `wait`, cooperative `cancel`, and terminal
`handoff` all validate Team owner, member, assignment, Child origin, objective,
and parent Session before acting. The Child ledger remains the execution authority;
Team observations store only terminal outcome, exact terminal sequence, Child
Session identity, and handoff digest. Handoff bodies, Provider errors, prompts,
tool results, and objective text are not copied into Team records.

Every assignment creates a new detached Child Session. Reusing a member means only
reusing its durable identity and audit lineage. Different active members may run in
parallel through the existing four-worker/32-queue process-local supervisor. A
member may have only one pending or nonterminal assignment. Team close and member
leave first reconcile exact terminal evidence and otherwise fail closed; they never
cancel work implicitly.

The parent `ProjectSession` remains usable while Child workers run. New/fork/switch
Session identity changes reject queued or active local Child submissions without
mutating the old Session. Once the supervisor is quiescent it is retired before the
writer/runtime swap; a new selected Session creates a newly bound supervisor lazily.
No process-surviving worker, mailbox, shared task board, scheduler, writable member,
recursive delegation, Team model tool, or Team data in model context is introduced.

## Compatibility and invariants

- Existing ordinary, model-delegated, and legacy Child transcripts replay unchanged;
  the Team origin is an additive record-local schema-v1 origin.
- Child admission, fixed read-only ToolSet, one-Turn/depth-one contract,
  permission/approval envelope, cancellation, lease, handoff, and supervisor bounds
  remain the existing A-series contracts.
- Tool Registry generation 7, system prompt v45, Provider adapter contract v46,
  Effective Context v23/v24, ordinary/Child tool schemas, and Session transcript
  schemas remain unchanged because Team controls are Host-only.
- Team and Child task text is untrusted audit data. It never becomes system
  authority, project instructions, Provider context, parent history, compaction,
  usage, Action Audit, Task, Skill, or MCP state.
- A terminal handoff proves durable provenance and integrity, not factual truth.
  Parent delivery receipts are deliberately absent from Team assignment execution.

## Alternatives rejected

- A second Team-specific runtime loop: duplicates Agent runtime assembly and risks
  diverging from Child permission, lease, and one-Turn guarantees.
- Reusing one Child Session for a member: retains context across objectives and
  makes identity indistinguishable from conversation state.
- A cross-file database transaction: expands the learning slice and hides rather
  than exposes append-only partial durability; the deterministic saga is sufficient.
- A persistent worker or scheduler: exceeds the local process scope and would make
  restart behavior impossible to prove without a new service lifecycle.
- Mailboxes, shared tasks, or model-visible Team tools: those are future coordination
  contracts requiring their own trust, permission, and context decisions.

## Verification

Deterministic tests cover strict Team/Child provenance, partial assignment recovery,
terminal observation and handoff idempotence, sequential member reuse with distinct
Child/Session IDs, two-member supervisor overlap while the parent prompts, bounded
wait/cancel behavior, close/leave gates, Session identity-change rejection, REPL
execution commands, and unchanged model-visible contract goldens. The complete
offline release gate remains the required final check.
